"""test_sql_reader.py – unit tests for phantom.sql_reader."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.corpus import ManifestEntry
from phantom.sql_reader import SqlDump, SqlTable, read_sql_entry


def _entry(content: bytes, tmp: Path, name: str = "snap.sql") -> ManifestEntry:
    """Write bytes to tmp/name and return a ManifestEntry with matching sha256."""
    p = tmp / name
    p.write_bytes(content)
    return ManifestEntry(
        file_id="f1", file_path=name, origin="test",
        captured_at="2026-01-01T00:00:00Z", path=p,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _sql(text: str, tmp: Path, name: str = "snap.sql") -> ManifestEntry:
    return _entry(text.encode("utf-8"), tmp, name)


class TestSqlReader(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_basic_dump_structure_text_and_bom(self) -> None:
        # BOM stripped, comments and CRLF preserved, types correct.
        sql = "-- hdr\r\nCREATE TABLE t (id INTEGER PRIMARY KEY);\r\n"
        raw = ("\ufeff" + sql).encode("utf-8")
        dump = read_sql_entry(_entry(raw, self.d))
        self.assertIsInstance(dump, SqlDump)
        self.assertIsInstance(dump.tables[0], SqlTable)
        self.assertFalse(dump.text.startswith("\ufeff"))
        self.assertIn("-- hdr", dump.text)
        self.assertIn("\r\n", dump.text)
        # Internal sqlite_ tables excluded.
        for t in dump.tables:
            self.assertFalse(t.name.startswith("sqlite_"))

    def test_primary_and_replica_tables_distinct(self) -> None:
        sql = (
            "CREATE TABLE primary_store (id TEXT PRIMARY KEY, val TEXT);\n"
            "CREATE TABLE replica_store (id TEXT PRIMARY KEY, val TEXT);\n"
            "INSERT INTO primary_store VALUES('r1','primary_value');\n"
            "INSERT INTO replica_store VALUES('r1','replica_value');\n"
        )
        dump = read_sql_entry(_sql(sql, self.d))
        by_name = {t.name: t for t in dump.tables}
        self.assertIn("primary_store", by_name)
        self.assertIn("replica_store", by_name)
        self.assertEqual(by_name["primary_store"].rows[0][1], "primary_value")
        self.assertEqual(by_name["replica_store"].rows[0][1], "replica_value")

    def test_empty_table_sort_column_order_rowid_order(self) -> None:
        sql = (
            "CREATE TABLE beta (b TEXT PRIMARY KEY, a TEXT);\n"
            "CREATE TABLE alpha (x TEXT PRIMARY KEY, y TEXT);\n"
            "INSERT INTO beta VALUES('b2','second');\n"
            "INSERT INTO beta VALUES('b1','first');\n"
        )
        dump = read_sql_entry(_sql(sql, self.d))
        # Sorted lexicographically.
        self.assertEqual(dump.tables[0].name, "alpha")
        self.assertEqual(dump.tables[1].name, "beta")
        # Column order preserved.
        self.assertEqual(dump.tables[0].columns, ("x", "y"))
        self.assertEqual(dump.tables[1].columns, ("b", "a"))
        # Empty table retained.
        self.assertEqual(dump.tables[0].rows, ())
        # Rowid (insertion) order for beta.
        vals = [r[0] for r in dump.tables[1].rows]
        self.assertEqual(vals, ["b2", "b1"])

    def test_unicode_null_integer_real_apostrophe_json_unchanged(self) -> None:
        sql = (
            "CREATE TABLE t (id INTEGER PRIMARY KEY, s TEXT, n REAL, j TEXT);\n"
            "INSERT INTO t VALUES(1, 'café \"Ω\"', 3.14, '{\"k\":\"v\"}');\n"
            "INSERT INTO t VALUES(2, NULL, 0.0, '{}');\n"
            "CREATE TABLE apo (id TEXT PRIMARY KEY, v TEXT);\n"
            "INSERT INTO apo VALUES('r1', 'O''Brien');\n"
        )
        dump = read_sql_entry(_sql(sql, self.d))
        by_name = {t.name: t for t in dump.tables}
        rows = by_name["t"].rows
        self.assertEqual(rows[0][1], 'café "Ω"')
        self.assertAlmostEqual(rows[0][2], 3.14)
        self.assertEqual(rows[0][3], '{"k":"v"}')
        self.assertIsNone(rows[1][1])
        self.assertEqual(by_name["apo"].rows[0][1], "O'Brien")

    def test_quoted_identifier_and_json_valid_and_transaction(self) -> None:
        sql = (
            'CREATE TABLE "my""table" (id TEXT PRIMARY KEY, j TEXT CHECK(json_valid(j)));\n'
            "BEGIN TRANSACTION;\n"
            'INSERT INTO "my""table" VALUES(\'r1\', \'{"a":1}\');\n'
            "COMMIT;\n"
        )
        dump = read_sql_entry(_sql(sql, self.d))
        self.assertEqual(dump.tables[0].name, 'my"table')
        self.assertEqual(len(dump.tables[0].rows), 1)

    def test_hash_mismatch_and_malformed_sql_raise(self) -> None:
        with self.subTest("hash mismatch"):
            p = self.d / "bad.sql"
            p.write_bytes(b"CREATE TABLE t (id TEXT PRIMARY KEY);\n")
            entry = ManifestEntry(
                file_id="f2", file_path="bad.sql", origin="test",
                captured_at="2026-01-01T00:00:00Z", path=p, sha256="0" * 64,
            )
            with self.assertRaises(ValueError):
                read_sql_entry(entry)
        with self.subTest("malformed SQL"):
            with self.assertRaises(ValueError):
                read_sql_entry(_sql("NOT VALID SQL;;;", self.d))

    def test_missing_file_propagates(self) -> None:
        entry = ManifestEntry(
            file_id="f3", file_path="gone.sql", origin="test",
            captured_at="2026-01-01T00:00:00Z",
            path=self.d / "gone.sql", sha256="0" * 64,
        )
        with self.assertRaises((FileNotFoundError, OSError)):
            read_sql_entry(entry)

    def test_rejected_operations_raise(self) -> None:
        base = "CREATE TABLE t (id TEXT PRIMARY KEY);\n"
        cases = [
            ("ATTACH DATABASE ':memory:' AS aux;", "ATTACH"),
            ("PRAGMA journal_mode=WAL;", "PRAGMA"),
            ("CREATE TRIGGER trg AFTER INSERT ON t BEGIN SELECT 1; END;", "TRIGGER"),
            ("CREATE VIEW v AS SELECT 1;", "VIEW"),
            ("INSERT INTO t VALUES(hex('secret'));", "unauthorized function"),
        ]
        for extra, desc in cases:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    read_sql_entry(_sql(base + extra, self.d))

    def test_attach_does_not_create_file(self) -> None:
        target = self.d / "should_not_exist.db"
        with self.assertRaises(ValueError):
            read_sql_entry(_sql(f"ATTACH DATABASE '{target}' AS aux;\n", self.d))
        self.assertFalse(target.exists())

    def test_without_rowid_rejected(self) -> None:
        with self.assertRaises(ValueError):
            read_sql_entry(_sql(
                "CREATE TABLE t (id TEXT PRIMARY KEY) WITHOUT ROWID;\n", self.d
            ))

    def test_budget_exceeded_raises(self) -> None:
        sql = (
            "CREATE TABLE t (n INTEGER PRIMARY KEY);\n"
            "WITH RECURSIVE r(n) AS "
            "(SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n<999999999) "
            "INSERT INTO t SELECT n FROM r LIMIT 10;\n"
        )
        with patch("phantom.sql_reader.MAX_SQL_STEPS", 10):
            with self.assertRaises(ValueError):
                read_sql_entry(_sql(sql, self.d))

    def test_source_bytes_unchanged(self) -> None:
        original = b"CREATE TABLE t (id TEXT PRIMARY KEY);\n"
        entry = _entry(original, self.d)
        read_sql_entry(entry)
        self.assertEqual((self.d / "snap.sql").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
