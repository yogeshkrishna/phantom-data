"""test_record_snapshots.py – tests for phantom.record_snapshots."""

import copy, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.contracts import EntityKey, EvidenceFact, EvidenceRef
from phantom.record_snapshots import COLLECTION_CATEGORIES, extract_record_snapshot


def _src(locator: str = "/snapshots/0") -> EvidenceRef:
    return EvidenceRef(
        evidence_id="ev1", source_path="evidence/t.json",
        source_sha256="a" * 64, locator=locator,
        available_at="2026-02-03T12:00:00Z",
    )


def _snap(ds: str = "search_service", coll: str = "customer_search_documents",
          records: list | None = None) -> dict:
    return {
        "captured_at": "2026-02-01T10:00:00Z",
        "data_store": ds,
        "collection": coll,
        "records": records if records is not None else [
            {"resource_id": "r1", "email": None, "ref": "user-A"},
        ],
    }


class TestExtractRecordSnapshot(unittest.TestCase):

    # 1. Correct complete EvidenceFact fields and source locator
    def test_complete_fields(self):
        facts = extract_record_snapshot(_snap(), _src("/snapshots/2"))
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertIsInstance(f, EvidenceFact)
        self.assertEqual(f.entity, EntityKey("search_service", "search_service", "r1"))
        self.assertEqual(f.category, "SEARCH_INDEX_ENTRY")
        self.assertEqual(f.observed_at, "2026-02-01T10:00:00Z")
        self.assertEqual(f.kind, "state")
        self.assertEqual(f.state, "PRESENT")
        self.assertEqual(f.query_identifiers, {})
        self.assertEqual(f.references, ())
        self.assertEqual(f.metadata, {"collection": "customer_search_documents"})
        self.assertEqual(f.source.locator, "/snapshots/2/records/0")
        self.assertEqual(f.source.evidence_id, "ev1")
        self.assertEqual(f.source.available_at, "2026-02-03T12:00:00Z")
        self.assertEqual(f.field_coverage, ("email", "ref", "resource_id"))

    # 2. Nested null/False/zero preserved; missing field stays missing
    def test_field_values_preserved(self):
        rec = {"resource_id": "r1", "active": False, "count": 0,
               "ref": None, "nested": {"x": [1, None]}}
        f = extract_record_snapshot(_snap(records=[rec]), _src())[0]
        self.assertIs(f.stored_fields["active"], False)
        self.assertEqual(f.stored_fields["count"], 0)
        self.assertIsNone(f.stored_fields["ref"])
        self.assertEqual(f.stored_fields["nested"], {"x": [1, None]})
        self.assertNotIn("absent_field", f.stored_fields)

    # 3. Source available_at and observed_at are independent and both preserved
    def test_times_are_independent(self):
        snap = {**_snap(), "captured_at": "2026-01-15T08:00:00Z"}
        src = EvidenceRef("e", "p", "b" * 64, "", "2026-03-01T00:00:00Z")
        f = extract_record_snapshot(snap, src)[0]
        self.assertEqual(f.observed_at, "2026-01-15T08:00:00Z")
        self.assertEqual(f.source.available_at, "2026-03-01T00:00:00Z")
        self.assertEqual(f.source.locator, "/records/0")  # empty base locator

    # 4. Multiple records retain order; locators are indexed
    def test_multiple_records_order(self):
        recs = [{"resource_id": f"r{i}", "v": i} for i in range(3)]
        facts = extract_record_snapshot(_snap(records=recs), _src())
        self.assertEqual(len(facts), 3)
        for i, f in enumerate(facts):
            self.assertEqual(f.entity.resource_id, f"r{i}")
            self.assertEqual(f.source.locator, f"/snapshots/0/records/{i}")

    # 5. Empty records list returns () with no absence claims
    def test_empty_records(self):
        self.assertEqual(extract_record_snapshot(_snap(records=[]), _src()), ())

    # 6. Category comes from schema, not resource_id prefix
    def test_category_from_schema_not_prefix(self):
        rec = {"resource_id": "order-99999"}  # deliberately misleading prefix
        f = extract_record_snapshot(_snap(records=[rec]), _src())[0]
        self.assertEqual(f.category, "SEARCH_INDEX_ENTRY")

    # 7. enabled=False connector config → PRESENT DATA_FLOW (no policy verdict)
    def test_connector_disabled_is_present(self):
        rec = {"resource_id": "conn-1", "enabled": False, "target": "crm"}
        f = extract_record_snapshot(
            _snap("integration_service", "connector_configurations", [rec]), _src()
        )[0]
        self.assertEqual(f.state, "PRESENT")
        self.assertEqual(f.category, "DATA_FLOW")
        self.assertIs(f.stored_fields["enabled"], False)

    # 8. Tombstone and analytics records: PRESENT, correct category, no verdict
    def test_tombstone_and_analytics_no_verdict(self):
        for ds, coll, cat in [
            ("identity_service", "deleted_customer_references", "ACCOUNT_TOMBSTONE"),
            ("analytics_platform", "customer_analytics_reports", "ANALYTICS_PROFILE"),
        ]:
            with self.subTest(ds):
                f = extract_record_snapshot(_snap(ds, coll, [{"resource_id": "x"}]), _src())[0]
                self.assertEqual(f.state, "PRESENT")
                self.assertEqual(f.category, cat)

    # 9. Mutation isolation in both directions
    def test_mutation_isolation(self):
        rec = {"resource_id": "r1", "data": {"k": "original"}}
        snap = _snap(records=[rec])
        facts = extract_record_snapshot(snap, _src())
        # Mutating returned stored_fields must not affect the input snapshot
        facts[0].stored_fields["data"]["k"] = "mutated_fact"
        self.assertEqual(snap["records"][0]["data"]["k"], "original")
        # Mutating input after call must not affect the already-returned fact
        snap["records"][0]["data"]["k"] = "mutated_input"
        self.assertEqual(facts[0].stored_fields["data"]["k"], "mutated_fact")

    # 10. Invalid inputs raise ValueError
    def test_invalid_inputs_raise(self):
        g = _src()
        bad_src = EvidenceRef("e", "p", "s" * 64, "bad_locator", "2026-01-01T00:00:00Z")
        cases = [
            ([], g, "snapshot not dict"),
            (_snap(), "not_eref", "source wrong type"),
            (_snap(), bad_src, "bad locator"),
            ({**_snap(), "captured_at": "not-a-date"}, g, "bad captured_at"),
            ({**_snap(), "captured_at": ""}, g, "blank captured_at"),
            ({**_snap(), "data_store": 99}, g, "non-str data_store"),
            ({**_snap(), "records": "oops"}, g, "records not list"),
            (_snap(records=[{"resource_id": "r1"}, {"resource_id": "r1"}]), g, "dup resource_id"),
            (_snap(records=[{"resource_id": ""}]), g, "blank resource_id"),
            (_snap(records=[{"resource_id": None}]), g, "None resource_id"),
            (_snap(records=[{1: "v", "resource_id": "r1"}]), g, "int key"),
            ({"captured_at": "2026-01-01T00:00:00Z", "data_store": "x",
              "collection": "y", "records": []}, g, "unknown schema"),
        ]
        for snap_arg, src_arg, desc in cases:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    extract_record_snapshot(snap_arg, src_arg)

    # 11. Operational history is explicitly rejected
    def test_operational_history_rejected(self):
        snap = {
            "captured_at": "2026-01-01T00:00:00Z",
            "data_store": "account_service",
            "collection": "operational_record_history",
            "records": [],
        }
        with self.assertRaises(ValueError):
            extract_record_snapshot(snap, _src())

    # 12. Original input snapshot is unchanged after successful extraction
    def test_input_unchanged(self):
        snap = _snap(records=[{"resource_id": "r1", "v": {"nested": True}}])
        before = copy.deepcopy(snap)
        extract_record_snapshot(snap, _src())
        self.assertEqual(snap, before)


if __name__ == "__main__":
    unittest.main()
