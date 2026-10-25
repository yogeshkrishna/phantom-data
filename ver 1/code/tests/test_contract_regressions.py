"""Regressions found during independent foundation review."""
import csv
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phantom.contracts import (
    AuditResult, AuditStep, EntityKey, EvidenceFact, EvidenceRef,
    is_available, to_csv_row, validate_row, write_output,
)


class ContractRegressions(unittest.TestCase):
    def result(self):
        return AuditResult("fixture_a", "NOT_APPLICABLE", ())

    def test_all_top_level_values_must_be_strings(self):
        row = to_csv_row(self.result())
        for key in row:
            for value in (7, True, None, [], b"[]"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        validate_row({**row, key: value})

    def test_bad_row_containers_raise_value_error(self):
        for row in (None, [], "not a row", {"case_id": "only_one"}):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    validate_row(row)

    def test_blank_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_row({**to_csv_row(self.result()), "case_id": " \t "})

    def test_invalid_availability_is_not_hidden_by_future_observation(self):
        source = EvidenceRef("fixture_e", "source", "hash", "row:1", "invalid")
        fact = EvidenceFact(
            EntityKey("account", "primary", "fixture_r"), "USER_PROFILE",
            "2030-01-01T00:00:00Z", "state", "PRESENT", {}, {}, (), (), {}, source,
        )
        with self.assertRaises(ValueError):
            is_available(fact, "2026-01-01T00:00:00Z")

    def test_invalid_expected_ids_preserve_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "output.csv"
            path.write_bytes(b"original")
            for expected in ([""], [" "], [7], [None], [[]]):
                with self.subTest(expected=expected):
                    with self.assertRaises(ValueError):
                        write_output(path, [self.result()], expected)
                    self.assertEqual(path.read_bytes(), b"original")

    def test_existing_temporary_sibling_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.csv"
            sibling = path.with_suffix(".tmp")
            sibling.write_bytes(b"unrelated content")
            write_output(path, [self.result()], ["fixture_a"])
            self.assertEqual(sibling.read_bytes(), b"unrelated content")
            self.assertEqual(set(Path(folder).iterdir()), {path, sibling})

    def test_destination_with_tmp_suffix_works(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "output.tmp"
            path.write_bytes(b"old")
            write_output(path, [self.result()], ["fixture_a"])
            self.assertTrue(path.read_text(encoding="utf-8").startswith("case_id,"))

    def test_write_failure_preserves_destination_and_cleans_only_owned_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.csv"
            sibling = path.with_suffix(".tmp")
            path.write_bytes(b"original")
            sibling.write_bytes(b"unrelated")
            with patch("phantom.contracts.csv.DictWriter.writerows", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    write_output(path, [self.result()], ["fixture_a"])
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(sibling.read_bytes(), b"unrelated")
            self.assertEqual(set(Path(folder).iterdir()), {path, sibling})

    def test_unicode_quotes_survive_inside_step_json_and_csv(self):
        step = AuditStep(1, "VERIFICATION", "système", 'record,"quoted"',
                         "VERIFY_RECORD_ABSENT", "ABSENT", 'evidence,"référence"')
        result = replace(self.result(), deletion_status="COMPLETE", steps=(step,))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "output.csv"
            write_output(path, [result], [result.case_id])
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row, to_csv_row(result))


if __name__ == "__main__":
    unittest.main()
