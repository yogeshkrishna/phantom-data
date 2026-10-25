"""Scoring must reject malformed submissions before reporting accuracy."""

from __future__ import annotations

import copy
import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
SPEC = importlib.util.spec_from_file_location("evaluation_scorer", CODE / "evaluation" / "main.py")
scorer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scorer)


def complete(evidence="proof_synthetic"):
    return {"synthetic_case": ("COMPLETE", [{
        "step_no": 1, "phase": "VERIFICATION", "system_id": "search_service",
        "resource_id": "synthetic_search", "operation": "VERIFY_RECORD_ABSENT",
        "state": "ABSENT", "evidence_id": evidence,
    }])}


class EvaluationIntegrityTests(unittest.TestCase):
    def test_valid_control_has_explicit_metric_denominators(self):
        result = scorer.score(complete(), complete())
        self.assertEqual((result["exact_cases"], result["cases"]), (1, 1))
        self.assertEqual((result["matched_steps"], result["predicted_steps"], result["reference_steps"]), (1, 1, 1))
        self.assertEqual((result["field_hits"], result["field_total"]), (7, 7))

    def test_missing_extra_and_empty_case_maps_are_not_scored(self):
        for predicted in ({}, {**complete(), "unexpected": ("NOT_APPLICABLE", [])},
                          {"different": complete()["synthetic_case"]}):
            with self.subTest(predicted_ids=list(predicted)), self.assertRaises(scorer.EvaluationError):
                scorer.score(predicted, complete())

    def test_duplicate_csv_rows_are_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.csv"
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["case_id", "deletion_status", "steps_json"])
                writer.writerows([("same", "NOT_APPLICABLE", "[]")] * 2)
            with self.assertRaisesRegex(scorer.EvaluationError, "duplicate case_id"):
                scorer.load(path)

    def test_step_number_and_bool_are_invalid_even_when_content_matches(self):
        for number in (999, True, "1", 1.0):
            predicted = complete()
            predicted["synthetic_case"][1][0]["step_no"] = number
            with self.subTest(number=number), self.assertRaisesRegex(scorer.EvaluationError, "step_no"):
                scorer.score(predicted, complete())

    def test_operation_state_fields_and_shape_are_validated(self):
        for field, value in (("phase", "REMEDIATION"), ("state", "PRESENT"), ("operation", "MAGIC_DELETE"),
                             ("system_id", ""), ("resource_id", None), ("evidence_id", [])):
            predicted = complete()
            predicted["synthetic_case"][1][0][field] = value
            with self.subTest(field=field), self.assertRaises(scorer.EvaluationError):
                scorer.score(predicted, complete())
        predicted = complete()
        predicted["synthetic_case"][1][0]["unexpected"] = "field"
        with self.assertRaises(scorer.EvaluationError):
            scorer.score(predicted, complete())

    def test_gold_blank_complete_citation_is_a_reference_limitation(self):
        result = scorer.score(complete(), complete(""))
        self.assertEqual(result["status_accuracy"], 1)
        self.assertEqual(result["exact_case_accuracy"], 0)
        self.assertEqual(result["sequence_accuracy"], 1)
        self.assertEqual(result["step_recall"], 0)
        with self.assertRaisesRegex(scorer.EvaluationError, "lacks evidence"):
            scorer.score(complete(""), complete(""))

    def test_exact_uses_every_ordered_field_and_ignores_object_key_order(self):
        base = complete()
        changed = copy.deepcopy(base)
        step = changed["synthetic_case"][1][0]
        changed["synthetic_case"][1][0] = dict(reversed(list(step.items())))
        self.assertEqual(scorer.score(changed, base)["exact_case_accuracy"], 1)
        for field, value in (("resource_id", "another_resource"), ("system_id", "another_service"),
                             ("evidence_id", "other_proof")):
            changed = copy.deepcopy(base)
            changed["synthetic_case"][1][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(scorer.score(changed, base)["exact_case_accuracy"], 0)
        self.assertNotEqual(scorer.full_step_key(step), scorer.full_step_key({**step, "step_no": 2}))

    def test_duplicate_operations_are_rejected_and_counter_deltas_keep_multiplicity(self):
        predicted = complete()
        predicted["synthetic_case"][1].append({**predicted["synthetic_case"][1][0], "step_no": 2})
        with self.assertRaisesRegex(scorer.EvaluationError, "duplicate"):
            scorer.score(predicted, complete())
        key = scorer.step_key(complete()["synthetic_case"][1][0])
        delta = scorer.step_differences(Counter({key: 3}), Counter({key: 1}))
        self.assertEqual(delta, {"missing": [], "extra": [key, key]})
        self.assertEqual(scorer.step_differences(Counter({key: 1}), Counter({key: 3}))["missing"], [key, key])

    def test_wrong_order_and_inapplicable_steps_are_invalid(self):
        predicted = complete()
        first = predicted["synthetic_case"][1][0]
        predicted["synthetic_case"][1].append({**first, "step_no": 2, "resource_id": "aaa_first"})
        with self.assertRaisesRegex(scorer.EvaluationError, "ordering"):
            scorer.score(predicted, complete())
        with self.assertRaisesRegex(scorer.EvaluationError, "NOT_APPLICABLE"):
            scorer.score({"synthetic_case": ("NOT_APPLICABLE", [first])}, complete())

    def test_csv_header_and_extra_columns_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.csv"
            for rows in ((["case_id", "deletion_status", "steps_json", "extra"],),
                         (["case_id", "deletion_status", "steps_json"], ["a", "NOT_APPLICABLE", "[]", "x"])):
                with path.open("w", encoding="utf-8", newline="") as fh:
                    csv.writer(fh).writerows(rows)
                with self.assertRaises(scorer.EvaluationError):
                    scorer.load(path)

    def test_duplicate_json_fields_and_incomplete_action_pairs_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate-field.csv"
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["case_id", "deletion_status", "steps_json"])
                writer.writerow(["synthetic_case", "COMPLETE", '[{"step_no":1,"step_no":999}]'])
            with self.assertRaisesRegex(scorer.EvaluationError, "duplicate object field"):
                scorer.load(path)
        finding = {**complete()["synthetic_case"][1][0], "phase": "FINDING", "state": "PRESENT",
                   "operation": "REPORT_REMAINING_RECORD"}
        with self.assertRaisesRegex(scorer.EvaluationError, "lacks matching"):
            scorer.score({"synthetic_case": ("INCOMPLETE", [finding])}, complete())


if __name__ == "__main__":
    unittest.main()
