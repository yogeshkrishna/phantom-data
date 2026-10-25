"""Semantic fixtures separate workflow claims, queries, scopes and stored PII."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef, is_available
from phantom.json_evidence import normalize_json

TIME = "2026-01-01T00:00:00Z"
SOURCE = EvidenceRef("file", "evidence.json", "a" * 64, "", "2026-02-01T00:00:00Z")


def history(**extra):
    row = dict(recorded_at=TIME, system="account_service", resource_id="opaque-key",
               record_type="USER_PROFILE", result="FOUND", lookup_value="person@example.test")
    row.update(extra)
    return dict(exported_at=SOURCE.available_at, origin="exporter", record_count=1, records=[row])


def service(system, group, rows):
    return {"exported_at": SOURCE.available_at, "region": "global",
            "service_exports": {system: {group: rows}}}


class JsonEvidenceTests(unittest.TestCase):
    def test_query_identifier_is_not_stored_pii(self):
        batch = normalize_json(history(result="NOT_FOUND"), SOURCE)
        self.assertFalse(batch.diagnostics)
        fact = batch.facts[0]
        self.assertEqual(fact.state, "ABSENT")
        self.assertEqual(fact.stored_fields, {})
        self.assertEqual(fact.query_identifiers, {"lookup_value": ("person@example.test",)})
        self.assertEqual(fact.source.locator, "/records/0")
        self.assertFalse(is_available(fact, "2026-01-15T00:00:00Z"))

    def test_operation_success_is_claim_and_unknown_result_diagnostic(self):
        claim = normalize_json(history(result="SUCCESS", operation="DELETE"), SOURCE)
        self.assertEqual(claim.facts[0].kind, "claim")
        self.assertEqual(claim.facts[0].state, "SUCCESS")
        unknown = normalize_json(history(result="MAGIC"), SOURCE)
        self.assertFalse(unknown.facts)
        self.assertEqual(unknown.records_seen, 1)
        self.assertEqual(unknown.records_normalized, 0)
        self.assertTrue(unknown.diagnostics)

    def test_explicit_treatment_and_risk_readbacks(self):
        for category, system, state in (("DATA_FLOW", "integration_service", "DISABLED"),
                                        ("BACKUP_ARCHIVE", "backup_service", "ACTIVE"),
                                        ("BACKUP_ARCHIVE", "backup_service", "BLOCKED"),
                                        ("ANALYTICS_PROFILE", "analytics_platform", "ANONYMIZED"),
                                        ("SUPPORT_MESSAGE", "support_platform", "REDACTED")):
            with self.subTest(state=state):
                batch = normalize_json(history(record_type=category, system=system, result=state), SOURCE)
                self.assertEqual(batch.facts[0].state, state)
                expected = ("enabled",) if category == "DATA_FLOW" else ("restore_allowed",) if category == "BACKUP_ARCHIVE" else ()
                self.assertEqual(batch.facts[0].field_coverage, expected)

    def test_bundle_history_and_regular_records_separate(self):
        document = {"exported_at": SOURCE.available_at, "snapshots": [
            {"captured_at": TIME, "data_store": "account_service", "collection": "customer_profiles",
             "records": [{"resource_id": "r", "email": None}]},
            {"collection": "operational_record_history", "records": history()["records"]}]}
        batch = normalize_json(document, SOURCE)
        self.assertEqual((batch.records_seen, batch.records_normalized), (2, 2))
        self.assertEqual(batch.facts[0].stored_fields["email"], None)
        self.assertEqual(batch.facts[1].stored_fields, {})
        self.assertEqual(batch.facts[1].source.locator, "/snapshots/1/records/0")

    def test_empty_inventory_not_global_absence(self):
        document = {"captured_at": TIME, "source": "analytics_identity_store",
                    "collection": "customer_analytics_reports", "records": []}
        batch = normalize_json(document, SOURCE)
        self.assertFalse(batch.facts)
        self.assertFalse(batch.scopes)
        self.assertFalse(batch.diagnostics)

    def test_standalone_system_is_not_exporter_store(self):
        batch = normalize_json({"captured_at": TIME, "source": "user_export_store",
                               "collection": "customer_export_jobs", "records": [{"resource_id": "r"}]}, SOURCE)
        self.assertEqual(batch.facts[0].entity.system_id, "export_service")
        self.assertEqual(batch.facts[0].entity.store_id, "user_export_store")

    def test_profile_readback_and_partial_field_coverage(self):
        row = dict(observed_at=TIME, resource_id="r", profile={"email": None}, status_code=200)
        batch = normalize_json(service("account_service", "profile_lookups", [row]), SOURCE)
        self.assertEqual(batch.facts[0].stored_fields, {"email": None})
        self.assertNotIn("phone", batch.facts[0].field_coverage)
        row.update(profile=None, status_code=404)
        batch = normalize_json(service("account_service", "profile_lookups", [row]), SOURCE)
        self.assertEqual(batch.facts[0].state, "ABSENT")
        row.update(profile={"email": "contradiction"})
        self.assertTrue(normalize_json(service("account_service", "profile_lookups", [row]), SOURCE).diagnostics)

    def test_search_empty_complete_selector_and_partial_page(self):
        row = dict(observed_at=TIME, resource_id="r", query="person@example.test",
                   hits={"total": 0, "documents": []})
        batch = normalize_json(service("search_service", "index_queries", [row]), SOURCE)
        self.assertEqual(batch.facts[0].state, "ABSENT")
        self.assertTrue(batch.scopes[0].complete)
        self.assertTrue(batch.scopes[0].query_identifiers)
        row["hits"] = {"total": 3, "documents": [{"resource_id": "r", "email": None}]}
        batch = normalize_json(service("search_service", "index_queries", [row]), SOURCE)
        self.assertEqual(batch.facts[0].stored_fields, {"resource_id": "r", "email": None})
        self.assertFalse(batch.scopes[0].complete)
        self.assertEqual(batch.diagnostics[0].code, "PARTIAL_QUERY_RESPONSE")

    def test_flags_remain_stored_fields_and_not_automatic_verdicts(self):
        for system, group, flag in (("integration_service", "connectors", "enabled"),
                                    ("backup_service", "restore_entries", "restore_allowed")):
            batch = normalize_json(service(system, group, [dict(observed_at=TIME, resource_id="r", **{flag: False})]), SOURCE)
            self.assertEqual(batch.facts[0].state, "PRESENT")
            self.assertIs(batch.facts[0].stored_fields[flag], False)

    def test_bad_node_cannot_emit_partial_facts(self):
        row = dict(observed_at=TIME, resource_id="r", query="q", hits={"total": 2,
                   "documents": [{"resource_id": "ok"}, {"email": "missing id"}]})
        batch = normalize_json(service("search_service", "index_queries", [row]), SOURCE)
        self.assertFalse(batch.facts)
        self.assertTrue(batch.diagnostics)

    def test_unknown_layout_and_collections_not_silently_ignored(self):
        for value in ([], {"other": []}, {"service_exports": {"unknown": {"unknown": [{}]}}},
                      {"captured_at": TIME, "source": {}, "collection": "unknown", "records": [{}]}):
            self.assertTrue(normalize_json(value, SOURCE).diagnostics)

    def test_inputs_mutation_isolated(self):
        document = service("support_platform", "tickets", [dict(observed_at=TIME, resource_id="r", nested={"x": []})])
        before = copy.deepcopy(document)
        batch = normalize_json(document, SOURCE)
        batch.facts[0].stored_fields["nested"]["x"].append(1)
        self.assertEqual(document, before)


if __name__ == "__main__":
    unittest.main()
