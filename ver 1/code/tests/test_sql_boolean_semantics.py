"""Native SQLite booleans are not arbitrary truthy values or JSON numbers."""
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef
from phantom.identities import IdentityIndex
from phantom.pipeline import PreparedAudit, audit_request
from phantom.planner import AuditBlocked
from phantom.policy import PolicyEngine
from phantom.sql_evidence import normalize_sql
from phantom.sql_reader import SqlDump, SqlTable
from phantom.state import StateIndex
from test_policy import rules
from test_state import A, B, C


def context(value):
    tables = (SqlTable("connector_subscriptions", ("resource_id", "customer_reference", "enabled"),
                       (("opaque-flow", "u", value),)),
              SqlTable("backup_restore_entries", ("resource_id", "customer_reference", "archive_key", "restore_allowed"),
                       (("opaque-archive", "u", "archive/opaque.zst", value),)))
    source = EvidenceRef("sql-proof", "snapshot.sql", "a"*64, "", B)
    batch = normalize_sql(SqlDump(f"-- Captured at: {B}\n", tables), source)
    identities = IdentityIndex([dict(user_id="u", name="Person", email="a@example.test", phone="123",
                                     email_hash="hash", updated_at=A)], [], batch.facts)
    return PreparedAudit({"deletion_rules": rules()}, identities, StateIndex(batch.facts),
                         PolicyEngine(rules()), batch.diagnostics, 1, 1)


REQUEST = dict(case_id="independent", email="a@example.test", requested_at=A, audit_at=C,
               request_text="Delete my personal data")


class SqlBooleanTests(unittest.TestCase):
    def test_native_zero_and_one_drive_both_policy_and_independent_verifier(self):
        for value, status in ((0, "COMPLETE"), (1, "INCOMPLETE")):
            with self.subTest(value=value):
                prepared = context(value)
                result = audit_request(prepared, REQUEST)
                self.assertEqual(result.deletion_status, status)
                flags = [f.stored_fields.get("enabled", f.stored_fields.get("restore_allowed"))
                         for f in prepared.states.facts]
                self.assertTrue(all(type(flag) is bool for flag in flags))
                if value:
                    self.assertEqual({s.operation for s in result.steps if s.phase == "FINDING"},
                                     {"REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"})

    def test_unknown_native_values_never_become_safe_false(self):
        for value in (None, "false", "0", 2, -1, 0.0):
            with self.subTest(value=value):
                with self.assertRaises(AuditBlocked):
                    audit_request(context(value), REQUEST)

    def test_json_numeric_flag_not_coerced_and_raw_source_unchanged(self):
        columns = ("resource_id", "origin_system", "collection_name", "record_json", "updated_at")
        payload = json.dumps(dict(customer_reference="u", enabled=0))
        table = SqlTable("documents", columns, (("flow", "integration_service", "connector_configurations", payload, A),))
        batch = normalize_sql(SqlDump(f"-- Captured at: {B}\n", (table,)),
                              EvidenceRef("source", "snapshot.sql", "a"*64, "", B))
        self.assertIs(type(batch.facts[0].stored_fields["enabled"]), int)
        self.assertEqual(table.rows[0][3], payload)

    def test_nonboolean_numeric_columns_are_preserved(self):
        table = SqlTable("customer_profiles", ("resource_id", "user_id", "score"), (("profile", "u", 0),))
        batch = normalize_sql(SqlDump(f"-- Captured at: {B}\n", (table,)),
                              EvidenceRef("source", "snapshot.sql", "a"*64, "", B))
        self.assertIs(type(batch.facts[0].stored_fields["score"]), int)

    def test_opaque_resource_and_case_renaming_does_not_change_semantics(self):
        for value in (0, 1):
            original = context(value)
            old = audit_request(original, REQUEST)
            renamed_facts = tuple(replace(f, entity=replace(f.entity, resource_id="renamed-"+f.entity.resource_id))
                                  for f in reversed(original.states.facts))
            renamed = replace(original, states=StateIndex(renamed_facts))
            new = audit_request(renamed, dict(REQUEST, case_id="arbitrary-unseen-request"))
            self.assertEqual(old.deletion_status, new.deletion_status)
            self.assertEqual([(s.phase, s.operation, s.state) for s in old.steps],
                             [(s.phase, s.operation, s.state) for s in new.steps])


if __name__ == "__main__":
    unittest.main()
