"""test_tabular.py – tests for phantom.tabular TABLE_SPECS and load_tables."""

import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.tabular import TABLE_SPECS, load_tables

# Independent expected values — NOT derived from TABLE_SPECS so they can
# catch a wrong constant.
_EXPECTED_NAMES = (
    "deletion_requests",
    "user_identities",
    "user_audit_log",
    "system_activity",
    "deletion_rules",
)

_EXPECTED_HEADERS = {
    "deletion_requests": (
        "case_id", "email", "requested_at", "reported_status",
        "reported_at", "audit_at", "request_text",
    ),
    "user_identities": (
        "user_id", "name", "email", "phone", "email_hash", "updated_at",
    ),
    "user_audit_log": (
        "audit_id", "user_id", "event_time",
        "field_name", "old_value", "new_value",
    ),
    "system_activity": (
        "activity_id", "event_time", "event_type", "source_system",
        "target_system", "resource_id", "reported_result", "details",
    ),
    "deletion_rules": (
        "rule_id", "data_category", "condition", "required_action",
    ),
}


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[tuple]) -> bytes:
    """Write a CSV file and return its exact bytes."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for row in rows:
        w.writerow(row)
    raw = buf.getvalue().encode("utf-8")
    path.write_bytes(raw)
    return raw


# One representative fixture row per table (values stated independently).
_FIXTURE_ROWS: dict[str, list[tuple]] = {
    "deletion_requests": [
        ("DR001", "a@example.com", "2024-01-01T00:00:00Z",
         "COMPLETE", "2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z", "Delete me"),
    ],
    "user_identities": [
        ("U001", "Alice Smith", "a@example.com", "+1-555-0100", "hashAAAA", "2024-01-01T00:00:00Z"),
    ],
    "user_audit_log": [
        ("AUD001", "U001", "2024-01-01T10:00:00Z", "email", "old@x.com", "a@x.com"),
    ],
    "system_activity": [
        ("ACT001", "2024-01-01T11:00:00Z", "DELETE", "CRM", "DB", "U001", "SUCCESS", "{}"),
    ],
    "deletion_rules": [
        ("RULE001", "personal_data", "always", "delete"),
    ],
}


def _make_all_tables(dataset_dir: Path) -> None:
    """Write the five required CSV files using independent fixture data."""
    for name, cols, _ in TABLE_SPECS:
        _write_csv(dataset_dir / (name + ".csv"), cols, _FIXTURE_ROWS[name])


class TestTableSpecs(unittest.TestCase):
    """Verify the TABLE_SPECS constant against independently stated values."""

    def test_names_in_order(self) -> None:
        self.assertEqual(tuple(n for n, _, _ in TABLE_SPECS), _EXPECTED_NAMES)

    def test_headers(self) -> None:
        for name, cols, _ in TABLE_SPECS:
            with self.subTest(table=name):
                self.assertEqual(cols, _EXPECTED_HEADERS[name])

    def test_id_columns(self) -> None:
        expected = {
            "deletion_requests": "case_id", "user_identities": "user_id",
            "user_audit_log": "audit_id", "system_activity": "activity_id",
            "deletion_rules": "rule_id",
        }
        for name, _, id_col in TABLE_SPECS:
            with self.subTest(table=name):
                self.assertEqual(id_col, expected[name])


class TestLoadTables(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_five_keys_in_order_with_tuple_values(self) -> None:
        _make_all_tables(self.d)
        tables = load_tables(self.d)
        self.assertEqual(list(tables.keys()), list(_EXPECTED_NAMES))
        for name in _EXPECTED_NAMES:
            self.assertIsInstance(tables[name], tuple)

    def test_row_dicts_have_expected_string_fields(self) -> None:
        _make_all_tables(self.d)
        tables = load_tables(self.d)
        for name, expected_cols in _EXPECTED_HEADERS.items():
            with self.subTest(table=name):
                for row in tables[name]:
                    self.assertEqual(set(row.keys()), set(expected_cols))
                    for val in row.values():
                        self.assertIsInstance(val, str)

    def test_fixture_values_preserved_verbatim(self) -> None:
        _make_all_tables(self.d)
        tables = load_tables(self.d)
        dr = tables["deletion_requests"]
        self.assertEqual(len(dr), 1)
        self.assertEqual(dr[0]["case_id"], "DR001")
        self.assertEqual(dr[0]["email"], "a@example.com")
        self.assertEqual(dr[0]["request_text"], "Delete me")
        ui = tables["user_identities"]
        self.assertEqual(ui[0]["user_id"], "U001")
        self.assertEqual(ui[0]["name"], "Alice Smith")

    def test_multiple_rows_preserve_source_order(self) -> None:
        for name, cols, _ in TABLE_SPECS:
            rows = _FIXTURE_ROWS[name] if name != "deletion_rules" else []
            _write_csv(self.d / (name + ".csv"), cols, rows)
        dr_cols = _EXPECTED_HEADERS["deletion_rules"]
        ordered_rows = [
            ("RULE_C", "cat_c", "cond_c", "action_c"),
            ("RULE_A", "cat_a", "cond_a", "action_a"),
            ("RULE_B", "cat_b", "cond_b", "action_b"),
        ]
        _write_csv(self.d / "deletion_rules.csv", dr_cols, ordered_rows)
        tables = load_tables(self.d)
        self.assertEqual(
            [r["rule_id"] for r in tables["deletion_rules"]],
            ["RULE_C", "RULE_A", "RULE_B"],
        )

    def test_unicode_spaces_quotes_comma_embedded_newline_preserved(self) -> None:
        for name, cols, _ in TABLE_SPECS:
            rows = _FIXTURE_ROWS[name] if name != "deletion_rules" else []
            _write_csv(self.d / (name + ".csv"), cols, rows)
        dr_cols = _EXPECTED_HEADERS["deletion_rules"]
        _write_csv(self.d / "deletion_rules.csv", dr_cols,
                   [("R1", " café ", 'say "hi"', "a,b\nc")])
        row = load_tables(self.d)["deletion_rules"][0]
        self.assertEqual(row["data_category"], " café ")
        self.assertEqual(row["condition"], 'say "hi"')
        self.assertEqual(row["required_action"], "a,b\nc")

    def test_header_only_table_returns_empty_tuple(self) -> None:
        _make_all_tables(self.d)
        _write_csv(self.d / "deletion_rules.csv",
                   _EXPECTED_HEADERS["deletion_rules"], [])
        self.assertEqual(load_tables(self.d)["deletion_rules"], ())

    def test_wrong_or_reordered_header_raises_value_error(self) -> None:
        _make_all_tables(self.d)
        for bad_cols, desc in [
            (("rule_id", "data_category", "condition", "WRONG"), "wrong column"),
            (("rule_id", "condition", "data_category", "required_action"), "reordered"),
        ]:
            with self.subTest(desc):
                _write_csv(self.d / "deletion_rules.csv", bad_cols,
                           [("R1", "c", "d", "a")])
                with self.assertRaises(ValueError):
                    load_tables(self.d)

    def test_duplicate_id_raises_value_error(self) -> None:
        _make_all_tables(self.d)
        cols = _EXPECTED_HEADERS["deletion_rules"]
        _write_csv(self.d / "deletion_rules.csv", cols,
                   [("R1", "c", "d", "a"), ("R1", "x", "y", "z")])
        with self.assertRaises(ValueError):
            load_tables(self.d)

    def test_missing_table_raises_file_not_found(self) -> None:
        _make_all_tables(self.d)
        (self.d / "deletion_rules.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            load_tables(self.d)

    def test_source_file_bytes_unchanged(self) -> None:
        _make_all_tables(self.d)
        originals = {n: (self.d / (n + ".csv")).read_bytes() for n in _EXPECTED_NAMES}
        load_tables(self.d)
        for name in _EXPECTED_NAMES:
            with self.subTest(table=name):
                self.assertEqual((self.d / (name + ".csv")).read_bytes(), originals[name])

    def test_repeated_calls_observe_changed_file_and_return_independent_results(self) -> None:
        _make_all_tables(self.d)
        tables1 = load_tables(self.d)
        cols = _EXPECTED_HEADERS["deletion_rules"]
        _write_csv(self.d / "deletion_rules.csv", cols, [("NEW_RULE", "x", "y", "z")])
        tables2 = load_tables(self.d)
        # First result unchanged by second call.
        self.assertEqual(tables1["deletion_rules"][0]["rule_id"], "RULE001")
        # Second result reflects updated file.
        self.assertEqual(tables2["deletion_rules"][0]["rule_id"], "NEW_RULE")

    def test_mutating_returned_row_does_not_affect_subsequent_call(self) -> None:
        _make_all_tables(self.d)
        tables1 = load_tables(self.d)
        tables1["deletion_rules"][0]["rule_id"] = "MUTATED"
        tables2 = load_tables(self.d)
        self.assertEqual(tables2["deletion_rules"][0]["rule_id"], "RULE001")


if __name__ == "__main__":
    unittest.main()
