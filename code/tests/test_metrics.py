"""test_metrics.py – unit tests for phantom.metrics.compare_rows."""

import copy, json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.metrics import compare_rows

def _step(no, phase, sys_id, res_id, op, state, eid):
    return {"step_no": no, "phase": phase, "system_id": sys_id,
            "resource_id": res_id, "operation": op, "state": state,
            "evidence_id": eid}

def _row(case_id, status, steps):
    return {"case_id": case_id, "deletion_status": status,
            "steps_json": json.dumps(steps)}

_S1 = _step(1, "VERIFICATION", "SYS", "U1", "VERIFY_RECORD_ABSENT", "ABSENT", "ev1")
_ROW_A = _row("case-A", "COMPLETE", [_S1])
_BOOL_STEP = {"step_no": True, "phase": "p", "system_id": "s",
              "resource_id": "r", "operation": "o", "state": "t", "evidence_id": "e"}


class TestCompareRows(unittest.TestCase):
    # 1. Exact match (different JSON whitespace / object-key order)
    def test_exact_match(self):
        exp = [_ROW_A]
        pred_row = {"case_id": "case-A", "deletion_status": "COMPLETE",
                    "steps_json": json.dumps([_S1], indent=2)}
        m = compare_rows(exp, [pred_row])
        self.assertEqual(m["expected_cases"], 1)
        self.assertEqual(m["predicted_cases"], 1)
        self.assertEqual(m["missing_case_ids"], [])
        self.assertEqual(m["extra_case_ids"], [])
        self.assertTrue(m["coverage_exact"])
        for key in ("status_accuracy", "sequence_accuracy", "case_exact_accuracy", "step_f1"):
            self.assertAlmostEqual(m[key], 1.0, msg=key)
        self.assertEqual(m["matched_steps"], 1)
        for v in m["field_accuracy"].values():
            self.assertAlmostEqual(v, 1.0)

    # 2. Input row order does not matter
    def test_row_order_irrelevant(self):
        s_b = _step(1, "VERIFICATION", "SYS", "U2", "VERIFY_RECORD_ABSENT", "ABSENT", "ev2")
        exp = [_row("case-B", "COMPLETE", [s_b]), _ROW_A]
        pred = [_ROW_A, _row("case-B", "COMPLETE", [s_b])]
        m = compare_rows(exp, pred)
        self.assertAlmostEqual(m["status_accuracy"], 1.0)
        self.assertAlmostEqual(m["step_f1"], 1.0)

    # 3. Correct steps reordered and renumbered: F1 stays 1; sequence/exact fail
    def test_reordered_steps(self):
        s2 = _step(1, "FINDING", "SYS", "U1", "REPORT_REMAINING_RECORD", "PRESENT", "ev2")
        exp = [_row("case-A", "COMPLETE", [_S1, s2])]
        pred = [_row("case-A", "COMPLETE", [
            _step(1, "FINDING", "SYS", "U1", "REPORT_REMAINING_RECORD", "PRESENT", "ev2"),
            _step(2, "VERIFICATION", "SYS", "U1", "VERIFY_RECORD_ABSENT", "ABSENT", "ev1"),
        ])]
        m = compare_rows(exp, pred)
        self.assertAlmostEqual(m["step_f1"], 1.0)
        self.assertAlmostEqual(m["sequence_accuracy"], 0.0)   # array order differs
        self.assertAlmostEqual(m["case_exact_accuracy"], 0.0)

    # 4. Wrong status with correct steps: status/exact fail; sequence correct
    def test_wrong_status_correct_steps(self):
        exp = [_ROW_A]
        pred = [_row("case-A", "INCOMPLETE", [_S1])]
        m = compare_rows(exp, pred)
        self.assertAlmostEqual(m["status_accuracy"], 0.0)
        self.assertAlmostEqual(m["sequence_accuracy"], 1.0)   # steps equal
        self.assertAlmostEqual(m["case_exact_accuracy"], 0.0)
        self.assertAlmostEqual(m["step_f1"], 1.0)

    # 5. One wrong evidence_id: only that field drops
    def test_wrong_evidence_id(self):
        exp = [_ROW_A]
        pred = [_row("case-A", "COMPLETE", [
            _step(1, "VERIFICATION", "SYS", "U1", "VERIFY_RECORD_ABSENT", "ABSENT", "WRONG")
        ])]
        m = compare_rows(exp, pred)
        self.assertAlmostEqual(m["status_accuracy"], 1.0)
        self.assertAlmostEqual(m["step_f1"], 0.0)    # signature differs
        fa = m["field_accuracy"]
        self.assertAlmostEqual(fa["evidence_id"], 0.0)
        for col in ("step_no", "phase", "system_id", "resource_id", "operation", "state"):
            self.assertAlmostEqual(fa[col], 1.0, msg=f"field {col}")

    # 6. Duplicate predicted step: precision decreases, recall stays 1
    def test_duplicate_predicted_step(self):
        exp = [_ROW_A]
        pred = [_row("case-A", "COMPLETE", [
            _step(1, "VERIFICATION", "SYS", "U1", "VERIFY_RECORD_ABSENT", "ABSENT", "ev1"),
            _step(2, "VERIFICATION", "SYS", "U1", "VERIFY_RECORD_ABSENT", "ABSENT", "ev1"),
        ])]
        m = compare_rows(exp, pred)
        self.assertEqual(m["expected_steps"], 1)
        self.assertEqual(m["predicted_steps"], 2)
        self.assertEqual(m["matched_steps"], 1)
        self.assertAlmostEqual(m["step_precision"], 0.5)
    # 7. Missing and extra case IDs (extra case has steps)
    def test_missing_and_extra_cases(self):
        s_b = _step(1, "VERIFICATION", "SYS", "U2", "VERIFY_RECORD_ABSENT", "ABSENT", "ev2")
        exp = [_ROW_A, _row("case-B", "COMPLETE", [s_b])]
        pred = [_ROW_A, _row("case-X", "COMPLETE", [s_b])]
        m = compare_rows(exp, pred)
        self.assertEqual(m["missing_case_ids"], ["case-B"])
        self.assertEqual(m["extra_case_ids"], ["case-X"])
        self.assertFalse(m["coverage_exact"])
        # U = {case-A, case-B, case-X} = 3; only case-A in both
        self.assertAlmostEqual(m["sequence_accuracy"], 1 / 3)
        self.assertAlmostEqual(m["case_exact_accuracy"], 1 / 3)
        # status_accuracy: case-A correct, case-B missing → 1/2
        self.assertAlmostEqual(m["status_accuracy"], 0.5)

    # 8. Empty predicted input
    def test_empty_predicted(self):
        exp = [_ROW_A]
        m = compare_rows(exp, [])
        self.assertEqual(m["predicted_cases"], 0)
        self.assertEqual(m["missing_case_ids"], ["case-A"])
        self.assertEqual(m["extra_case_ids"], [])
        self.assertAlmostEqual(m["status_accuracy"], 0.0)
        self.assertAlmostEqual(m["sequence_accuracy"], 0.0)
        self.assertAlmostEqual(m["step_recall"], 0.0)
        self.assertAlmostEqual(m["step_f1"], 0.0)

    # 9. Both sides NOT_APPLICABLE with empty steps: zero-step conventions
    def test_not_applicable_empty_steps(self):
        na = _row("case-NA", "NOT_APPLICABLE", [])
        m = compare_rows([na], [na])
        self.assertEqual(m["expected_steps"], 0)
        self.assertEqual(m["predicted_steps"], 0)
        self.assertEqual(m["matched_steps"], 0)
        for key in ("step_precision", "step_recall", "step_f1"):
            self.assertAlmostEqual(m[key], 1.0, msg=key)
        for v in m["field_accuracy"].values():
            self.assertAlmostEqual(v, 1.0)

    # 10. Invalid inputs raise ValueError
    def test_invalid_inputs_raise(self):
        g = [_ROW_A]
        cases = [
            ([], g, "empty expected"),
            ([_row("c1","S",[_S1]), _row("c1","S",[_S1])], g, "dup exp id"),
            (g, [_row("c1","S",[_S1]), _row("c1","S",[_S1])], "dup pred id"),
            ([{"case_id": "x", "deletion_status": "S"}], g, "wrong keys"),
            ([_row("  ", "S", [])], g, "blank case_id"),
            ([_row("x", "S", [_S1, _BOOL_STEP])], g, "bool step_no"),
            ([_row("x", "S", "notjson")], g, "bad json"),
            ([_row("x", "S", [{"wrong": "keys"}])], g, "bad step keys"),
            ([_row("x", "S", [{**_S1, "step_no": "1"}])], g, "str step_no"),
        ]
        for exp_arg, pred_arg, desc in cases:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    compare_rows(exp_arg, pred_arg)

    # 11. Inputs remain unchanged after call
    def test_inputs_unchanged(self):
        exp = [copy.deepcopy(_ROW_A)]
        pred = [copy.deepcopy(_ROW_A)]
        before = (copy.deepcopy(exp), copy.deepcopy(pred))
        compare_rows(exp, pred)
        self.assertEqual(exp, before[0])
        self.assertEqual(pred, before[1])


if __name__ == "__main__":
    unittest.main()
