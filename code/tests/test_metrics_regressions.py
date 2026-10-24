"""Additional independent checks for metric validation and denominators."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.metrics import compare_rows


def row(case_id="case", steps=None):
    return {"case_id": case_id, "deletion_status": "COMPLETE",
            "steps_json": json.dumps(steps or [])}


def step(number=1):
    return {"step_no": number, "phase": "VERIFICATION", "system_id": "system",
            "resource_id": "resource", "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT", "evidence_id": "evidence"}


class MetricRegressions(unittest.TestCase):
    def test_invalid_outer_containers_raise_value_error_on_either_side(self):
        for bad in (None, 1, {}, set(), "", b"", bytearray(), iter([])):
            for position in (0, 1):
                arguments = [[row()], [row()]]
                arguments[position] = bad
                with self.subTest(kind=type(bad).__name__, position=position):
                    with self.assertRaises(ValueError):
                        compare_rows(*arguments)

    def test_tuple_sequences_supported(self):
        self.assertEqual(compare_rows((row(),), (row(),))["case_exact_accuracy"], 1.0)

    def test_malformed_json_text_rejected(self):
        bad = row()
        bad["steps_json"] = "[{"
        with self.assertRaises(ValueError):
            compare_rows([row()], [bad])

    def test_json_object_key_order_irrelevant(self):
        expected = row(steps=[step()])
        predicted = row(steps=[dict(reversed(list(step().items())))])
        self.assertNotEqual(expected["steps_json"], predicted["steps_json"])
        self.assertEqual(compare_rows([expected], [predicted])["case_exact_accuracy"], 1.0)

    def test_extra_empty_case_penalizes_case_metrics(self):
        metrics = compare_rows([row()], [row(), row("extra")])
        self.assertEqual(metrics["status_accuracy"], 1.0)
        self.assertEqual(metrics["sequence_accuracy"], 0.5)
        self.assertEqual(metrics["case_exact_accuracy"], 0.5)
        self.assertEqual(metrics["step_f1"], 1.0)
        self.assertFalse(metrics["coverage_exact"])

    def test_duplicate_signature_and_missing_position_penalties(self):
        metrics = compare_rows([row(steps=[step()])], [row(steps=[step(), step(2)])])
        self.assertEqual(metrics["matched_steps"], 1)
        self.assertEqual(metrics["step_precision"], 0.5)
        self.assertEqual(metrics["step_recall"], 1.0)
        self.assertAlmostEqual(metrics["step_f1"], 2 / 3)
        self.assertEqual(set(metrics["field_accuracy"].values()), {0.5})


if __name__ == "__main__":
    unittest.main()
