"""Semantic SQL tests do not assume successful loading means valid audit proof."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef, is_available
from phantom.sql_evidence import normalize_sql
from phantom.sql_reader import SqlDump, SqlTable

CAPTURE = "2026-02-01T00:00:00Z"
OLD = "2026-01-01T00:00:00Z"
SOURCE = EvidenceRef("file", "snapshot.sql", "a" * 64, "", "2026-03-01T00:00:00Z")
HEADER = f"-- Captured at: {CAPTURE}\n"
GENERIC = ("resource_id", "origin_system", "collection_name", "record_json", "updated_at")


def generic(name, fields, resource="r"):
    return SqlTable(name, GENERIC, ((resource, "account_service", "customer_profiles",
                                   json.dumps(fields), OLD),))


class SqlEvidenceTests(unittest.TestCase):
    def test_replicas_remain_distinct_and_capture_is_not_update_time(self):
        batch = normalize_sql(SqlDump(HEADER, (generic("primary", {"email": None}),
                                              generic("replica", {"email": "pii@example.test"}))), SOURCE)
        self.assertFalse(batch.diagnostics)
        self.assertEqual({fact.entity.store_id for fact in batch.facts}, {"primary", "replica"})
        self.assertEqual({fact.observed_at for fact in batch.facts}, {CAPTURE})
        self.assertFalse(is_available(batch.facts[0], "2026-02-15T00:00:00Z"))
        self.assertEqual(batch.facts[1].metadata["row_updated_at"], OLD)

    def test_scope_timestamp_does_not_imply_exhaustiveness(self):
        scope = SqlTable("snapshot_scope", ("origin_system", "captured_at"), (("account_service", CAPTURE),))
        empty = SqlTable("customer_profiles", ("resource_id", "email", "updated_at"), ())
        batch = normalize_sql(SqlDump("", (scope, empty)), SOURCE)
        self.assertFalse(batch.diagnostics)
        self.assertEqual(batch.records_seen, batch.records_normalized)
        self.assertFalse(batch.scopes[0].complete)
        self.assertFalse(batch.facts)

    def test_explicit_complete_scope_is_exact_table_and_collection(self):
        scope = SqlTable("snapshot_scope", ("origin_system", "captured_at", "complete"),
                         (("account_service", CAPTURE, 1),))
        empty = SqlTable("customer_profiles", ("resource_id",), ())
        batch = normalize_sql(SqlDump("", (scope, empty)), SOURCE)
        self.assertTrue(batch.scopes[0].complete)
        self.assertEqual(batch.scopes[0].store_id, "customer_profiles")
        self.assertEqual(batch.scopes[0].collection, "customer_profiles")

    def test_absent_capture_does_not_backdate_using_updated_at(self):
        batch = normalize_sql(SqlDump("", (generic("primary", {"email": None}),)), SOURCE)
        self.assertFalse(batch.facts)
        self.assertEqual(batch.diagnostics[0].code, "INVALID_SQL_RECORD")

    def test_snapshot_footer_dates_unscoped_empty_table_without_proving_absence(self):
        empty = SqlTable("customer_exports", ("resource_id",), ())
        batch = normalize_sql(SqlDump(f"-- snapshot_completed_at={CAPTURE}\n", (empty,)), SOURCE)
        self.assertFalse(batch.diagnostics)
        self.assertFalse(batch.facts)
        self.assertEqual(batch.scopes[0].observed_at, CAPTURE)
        self.assertFalse(batch.scopes[0].complete)

    def test_bad_embedded_json_and_conflicting_resource_id(self):
        for text in ('{"email":1,"email":2}', '[1]', '{"email":NaN}', '{"x":1e999}',
                     '{"resource_id":"another"}'):
            with self.subTest(text=text):
                table = SqlTable("primary", GENERIC, (("r", "account_service", "customer_profiles", text, OLD),))
                batch = normalize_sql(SqlDump(HEADER, (table,)), SOURCE)
                self.assertFalse(batch.facts)
                self.assertTrue(batch.diagnostics)

    def test_failed_row_cannot_leave_a_complete_scope(self):
        scope = SqlTable("snapshot_scope", ("origin_system", "captured_at", "complete"),
                         (("account_service", CAPTURE, 1),))
        table = SqlTable("primary", GENERIC, (("r", "account_service", "customer_profiles", '{}', OLD),
                                              ("bad", "account_service", "customer_profiles", 'invalid', OLD)))
        batch = normalize_sql(SqlDump(HEADER, (scope, table)), SOURCE)
        self.assertFalse(batch.scopes[0].complete)
        self.assertTrue(batch.diagnostics)

    def test_explicit_reference_not_numeric_similarity(self):
        account = SqlTable("customer_profiles_primary", ("resource_id", "email"), (("opaque", None),))
        search = SqlTable("search_documents_primary", ("resource_id", "source_resource_id"), (("unrelated-number", "opaque"),))
        batch = normalize_sql(SqlDump(HEADER, (account, search)), SOURCE)
        self.assertEqual(batch.facts[1].references, (batch.facts[0].entity,))

    def test_tombstone_and_retained_fields_remain_raw(self):
        table = SqlTable("generic", GENERIC, (("odd-id", "identity_service", "deleted_customer_references",
                         '{"subject_reference":"subject","email":null}', OLD),))
        batch = normalize_sql(SqlDump(HEADER, (table,)), SOURCE)
        self.assertEqual(batch.facts[0].category, "ACCOUNT_TOMBSTONE")
        self.assertEqual(batch.facts[0].state, "PRESENT")
        self.assertIsNone(batch.facts[0].stored_fields["email"])
        self.assertNotIn("phone", batch.facts[0].field_coverage)

    def test_connector_and_archive_flags_not_treatment_claims(self):
        tables = (SqlTable("connector_subscriptions", ("resource_id", "enabled"), (("a", 0),)),
                  SqlTable("backup_restore_entries", ("resource_id", "restore_allowed"), (("b", 0),)))
        batch = normalize_sql(SqlDump(HEADER, tables), SOURCE)
        self.assertEqual([fact.state for fact in batch.facts], ["PRESENT", "PRESENT"])
        self.assertEqual(batch.facts[0].stored_fields["enabled"], 0)

    def test_unknown_tables_explicitly_diagnosed(self):
        batch = normalize_sql(SqlDump(HEADER, (SqlTable("unknown", ("x",), ((1,),)),)), SOURCE)
        self.assertEqual(batch.records_seen, 1)
        self.assertEqual(batch.records_normalized, 0)
        self.assertEqual(batch.diagnostics[0].code, "UNSUPPORTED_SQL_TABLE")


if __name__ == "__main__":
    unittest.main()
