"""metrics.py – transparent comparison metrics for deletion-audit row evaluation."""

import json
from collections import Counter
from collections.abc import Sequence
from .contracts import OUTPUT_COLUMNS, STEP_COLUMNS

_OC = OUTPUT_COLUMNS
_SC = STEP_COLUMNS


def _parse_steps(raw: list, label: str) -> list[dict]:
    """Validate and return parsed steps; raise ValueError on any structural fault."""
    steps: list[dict] = []
    for i, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise ValueError(f"{label} step {i}: not a dict")
        if set(s.keys()) != set(_SC):
            raise ValueError(f"{label} step {i}: wrong keys")
        sno = s["step_no"]
        if isinstance(sno, bool) or not isinstance(sno, int):
            raise ValueError(f"{label} step {i}: step_no must be int, not bool")
        for col in _SC:
            if col != "step_no" and not isinstance(s[col], str):
                raise ValueError(f"{label} step {i}: {col} must be str")
        steps.append(s)
    return steps


def _parse_inputs(
    rows: Sequence[dict[str, str]],
    label: str,
    require_nonempty: bool,
) -> dict[str, tuple[str, list[dict]]]:
    """Validate and index rows by case_id; return {case_id: (status, steps)}."""
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError(f"{label}: must be a sequence of row dictionaries")
    if require_nonempty and not rows:
        raise ValueError(f"{label}: must contain at least one case")
    result: dict[str, tuple[str, list[dict]]] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict) or set(row.keys()) != set(_OC):
            raise ValueError(f"{label}[{idx}]: wrong type or keys")
        for v in row.values():
            if not isinstance(v, str):
                raise ValueError(f"{label}[{idx}]: all values must be strings")
        cid = row["case_id"]
        if not cid.strip():
            raise ValueError(f"{label}[{idx}]: blank case_id")
        if cid in result:
            raise ValueError(f"{label}: duplicate case_id")
        try:
            raw = json.loads(row["steps_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}[{idx}]: invalid steps_json") from exc
        if not isinstance(raw, list):
            raise ValueError(f"{label}[{idx}]: steps_json must be an array")
        result[cid] = (row["deletion_status"], _parse_steps(raw, f"{label}[{idx}]"))
    return result


def _sig(case_id: str, step: dict) -> tuple:
    """Step signature excluding step_no; used for order-independent matching."""
    return (case_id, step["phase"], step["system_id"], step["resource_id"],
            step["operation"], step["state"], step["evidence_id"])


def compare_rows(
    expected_rows: Sequence[dict[str, str]],
    predicted_rows: Sequence[dict[str, str]],
) -> dict[str, object]:
    """Compare predicted rows against expected; return transparent metric dict."""
    exp = _parse_inputs(expected_rows, "expected_rows", require_nonempty=True)
    pred = _parse_inputs(predicted_rows, "predicted_rows", require_nonempty=False)

    E, P = set(exp), set(pred)
    U, both = E | P, E & P
    missing, extra = sorted(E - P), sorted(P - E)

    status_ok = sum(1 for c in E if c in pred and pred[c][0] == exp[c][0])
    status_accuracy = status_ok / len(E)

    seq_ok = sum(1 for c in both if pred[c][1] == exp[c][1])
    case_ok = sum(1 for c in both if pred[c][0] == exp[c][0] and pred[c][1] == exp[c][1])
    denom_u = len(U)
    sequence_accuracy = seq_ok / denom_u if denom_u else 1.0
    case_exact_accuracy = case_ok / denom_u if denom_u else 1.0

    exp_sigs = [_sig(c, s) for c, (_, ss) in exp.items() for s in ss]
    pred_sigs = [_sig(c, s) for c, (_, ss) in pred.items() for s in ss]
    exp_n, pred_n = len(exp_sigs), len(pred_sigs)
    matched = sum((Counter(exp_sigs) & Counter(pred_sigs)).values())
    if exp_n == 0 and pred_n == 0:
        sp, sr, sf = 1.0, 1.0, 1.0
    else:
        sp = matched / pred_n if pred_n else 0.0
        sr = matched / exp_n if exp_n else 0.0
        sf = 2 * matched / (exp_n + pred_n) if (exp_n + pred_n) else 0.0

    field_hits = {col: 0 for col in _SC}
    positions = 0
    for cid in U:
        es = exp[cid][1] if cid in exp else []
        ps = pred[cid][1] if cid in pred else []
        n = max(len(es), len(ps))
        positions += n
        for i in range(n):
            if i < len(es) and i < len(ps):
                for col in _SC:
                    if es[i][col] == ps[i][col]:
                        field_hits[col] += 1
    field_acc = (
        {col: 1.0 for col in _SC} if positions == 0
        else {col: field_hits[col] / positions for col in _SC}
    )

    return {
        "expected_cases": len(E),
        "predicted_cases": len(P),
        "missing_case_ids": missing,
        "extra_case_ids": extra,
        "coverage_exact": E == P,
        "status_accuracy": status_accuracy,
        "case_exact_accuracy": case_exact_accuracy,
        "sequence_accuracy": sequence_accuracy,
        "expected_steps": exp_n,
        "predicted_steps": pred_n,
        "matched_steps": matched,
        "step_precision": sp,
        "step_recall": sr,
        "step_f1": sf,
        "field_accuracy": field_acc,
    }
