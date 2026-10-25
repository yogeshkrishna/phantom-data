"""Whole SQL rows prove stored field coverage, never exhaustive collections."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef
from phantom.policy import PolicyEngine
from phantom.sql_evidence import normalize_sql, GENERIC_COLUMNS
from phantom.sql_reader import SqlDump, SqlTable
from phantom.state import StateIndex
from test_policy import rules
from test_state import A, B, C, fact, subject


COLUMNS = ("resource_id", "origin_system", "collection_name", "record_json", "updated_at")


def batch(payload, extra_columns=(), extra_values=()):
    table = SqlTable("primary", COLUMNS + extra_columns, (("opaque", "support_platform", "support_conversations",
                     json.dumps(payload), A, *extra_values),))
    return normalize_sql(SqlDump(f"-- Captured at: {B}\n", (table,)),
                         EvidenceRef("sql", "snapshot.sql", "a"*64, "", B))


class SqlRowCoverageTests(unittest.TestCase):
    def test_complete_stored_document_has_no_unrendered_contact_fields(self):
        result = batch(dict(subject_reference="u", message_text="[REDACTED]"))
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.facts[0].metadata["full_record"])
        view = StateIndex(result.facts).resolve(subject(), C)
        self.assertTrue(PolicyEngine(rules()).evaluate(view)[0].compliant)
        self.assertFalse(result.scopes[0].complete)

    def test_full_sql_row_replaces_old_omitted_fields_in_same_physical_store(self):
        old = fact({"subject_reference": "u", "email": "a@example.test"}, category="SUPPORT_MESSAGE",
                   entity=batch({}).facts[0].entity)
        current = batch(dict(subject_reference="u", message_text="[REDACTED]"))
        view = StateIndex((old, *current.facts)).resolve(subject(), C)
        self.assertNotIn("email", {field.field for field in view.resources[0].fields})
        self.assertTrue(PolicyEngine(rules()).evaluate(view)[0].compliant)

    def test_partial_projection_and_another_replica_cannot_clear_fields(self):
        old = fact({"user_id": "u", "email": "a@example.test"}, category="SUPPORT_MESSAGE")
        partial = fact({"message_text": "[REDACTED]"}, B, category="SUPPORT_MESSAGE")
        self.assertFalse(PolicyEngine(rules()).evaluate(StateIndex((old, partial)).resolve(subject(), C))[0].compliant)
        clean = batch(dict(subject_reference="u", message_text="[REDACTED]"))
        from dataclasses import replace
        replica = replace(old, entity=replace(clean.facts[0].entity, store_id="replica"))
        obligations = PolicyEngine(rules()).evaluate(StateIndex((*clean.facts, replica)).resolve(subject(), C))
        self.assertIn(False, [o.compliant for o in obligations])

    def test_extra_native_columns_cannot_hide_pii_or_overwrite_document_values(self):
        result = batch(dict(subject_reference="u", email=None, message_text="[REDACTED]"),
                       ("email",), ("a@example.test",))
        fields = result.facts[0].stored_fields
        self.assertIsNone(fields["document"]["email"])
        self.assertEqual(fields["sql_columns"]["email"], "a@example.test")
        view = StateIndex(result.facts).resolve(subject(), C)
        self.assertFalse(PolicyEngine(rules()).evaluate(view)[0].compliant)

    def test_whole_row_does_not_invent_retention_or_absent_resources(self):
        table = SqlTable("security_audit_records", ("resource_id", "subject_reference"), (("audit", "u"),))
        normalized = normalize_sql(SqlDump(f"-- Captured at: {B}\n", (table,)),
                                   EvidenceRef("sql", "snapshot.sql", "a"*64, "", B))
        view = StateIndex(normalized.facts).resolve(subject(), C)
        self.assertIsNone(PolicyEngine(rules()).evaluate(view)[0].compliant)
        self.assertFalse(normalized.scopes[0].complete)
        self.assertEqual({f.state for f in normalized.facts}, {"PRESENT"})


if __name__ == "__main__":
    unittest.main()
