"""Regression coverage for corpus compatibility and lossless SQL loading."""

import hashlib
import sqlite3
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom import sql_reader
from phantom.corpus import ManifestEntry


class SqlReaderRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "snapshot.sql"

    def read(self, script):
        raw = script.encode("utf-8")
        self.path.write_bytes(raw)
        entry = ManifestEntry("synthetic", "snapshot.sql", "test",
                              "2026-01-01T00:00:00Z", self.path,
                              hashlib.sha256(raw).hexdigest())
        return sql_reader.read_sql_entry(entry)

    def test_foreign_keys_enable_and_ordinary_indexes_supported(self):
        dump = self.read("""
            PRAGMA foreign_keys=ON;
            BEGIN;
            CREATE TABLE parent(id TEXT PRIMARY KEY);
            CREATE TABLE child(id TEXT REFERENCES parent(id));
            CREATE INDEX child_id ON child(id);
            INSERT INTO parent VALUES('p');
            INSERT INTO child VALUES('p');
            COMMIT;
        """)
        self.assertEqual([table.name for table in dump.tables], ["child", "parent"])
        self.assertEqual(dump.tables[0].rows, (("p",),))

    def test_foreign_keys_actually_enforced(self):
        with self.assertRaises(ValueError):
            self.read("""PRAGMA foreign_keys=ON;
                CREATE TABLE p(id TEXT PRIMARY KEY);
                CREATE TABLE c(id TEXT REFERENCES p(id));
                INSERT INTO c VALUES('missing');""")

    def test_unrelated_operations_remain_rejected(self):
        base = "CREATE TABLE t(x); INSERT INTO t VALUES(1);"
        for suffix in ("UPDATE t SET x=2;", "DELETE FROM t;", "ANALYZE;",
                       "PRAGMA foreign_keys=OFF;", "PRAGMA foreign_keys;",
                       "PRAGMA writable_schema=ON;", "DROP TABLE t;"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.read(base + suffix)

    def test_sqlite_prefix_filter_is_literal(self):
        dump = self.read("CREATE TABLE sqliteXrecords(x); INSERT INTO sqliteXrecords VALUES(7);")
        self.assertEqual([table.name for table in dump.tables], ["sqliteXrecords"])
        self.assertEqual(dump.tables[0].rows, ((7,),))

    def test_shadowed_rowid_uses_physical_order(self):
        for column in ("rowid", "ROWID", "_rowid_", "oid"):
            with self.subTest(column=column):
                dump = self.read(f"""CREATE TABLE t({column} TEXT, value TEXT);
                    INSERT INTO t VALUES('z','first');
                    INSERT INTO t VALUES('a','second');""")
                self.assertEqual(dump.tables[0].rows,
                                 (("z", "first"), ("a", "second")))

    def test_without_rowid_rejected_even_with_rowid_named_column(self):
        with self.assertRaisesRegex(ValueError, "WITHOUT ROWID"):
            self.read("CREATE TABLE t(rowid TEXT PRIMARY KEY) WITHOUT ROWID;")

    def test_all_rowid_aliases_shadowed_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "aliases"):
            self.read("CREATE TABLE t(rowid, _rowid_, oid);")

    def test_generated_columns_align_with_returned_rows(self):
        dump = self.read("""CREATE TABLE t(j TEXT, valid INTEGER AS (json_valid(j)));
                            INSERT INTO t(j) VALUES('{}');""")
        self.assertEqual(dump.tables[0].columns, ("j", "valid"))
        self.assertEqual(dump.tables[0].rows, (("{}", 1),))

    def test_errors_do_not_leak_literal_or_sqlite_traceback(self):
        try:
            self.read("""CREATE TABLE t(x CHECK(x != 'private@example.test'));
                         INSERT INTO t VALUES('private@example.test');""")
        except ValueError as exc:
            self.assertNotIn("private@example.test", str(exc))
            formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.assertNotIn("CHECK constraint failed", formatted)
            self.assertTrue(exc.__suppress_context__)
        else:
            self.fail("Expected rejected CHECK constraint")

    def test_many_short_statements_share_one_budget(self):
        with patch.object(sql_reader, "MAX_SQL_STEPS", 100):
            with self.assertRaisesRegex(ValueError, "budget"):
                self.read("SELECT 1;" * 1000)

    def test_budget_remains_active_during_inspection(self):
        class InspectionBudgetConnection(sqlite3.Connection):
            def set_authorizer(self, callback):
                super().set_authorizer(callback)
                if callback is None:
                    sql_reader.MAX_SQL_STEPS = 1

        connection = sqlite3.connect(":memory:", factory=InspectionBudgetConnection)
        with patch.object(sql_reader, "MAX_SQL_STEPS", 5_000_000):
            with patch.object(sql_reader.sqlite3, "connect", return_value=connection):
                with self.assertRaisesRegex(ValueError, "budget"):
                    self.read("CREATE TABLE t(x);")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
