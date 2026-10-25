"""Score predictions against reference answers (public samples or custom cases).

Usage (from the repository root):
    python code/evaluation/main.py                         # run pipeline on the 32 public samples and score
    python code/evaluation/main.py --pred p.csv --gold g.csv   # score two existing files
    python code/evaluation/main.py --custom                # run and score the custom evaluation cases
    python code/evaluation/main.py --verbose               # print per-case mismatches

Metrics (JSON object-key order is ignored; array order is respected where stated):
  status_accuracy      share of cases whose deletion_status matches
  exact_case_accuracy  share of cases whose status and full ordered steps match exactly
  step_precision/recall/f1   multiset match on (phase, system_id, resource_id, operation, state, evidence_id)
  field_accuracy       per-position field agreement over max(len(pred), len(gold)) positions x 7 fields
  sequence_accuracy    share of cases whose ordered (phase, operation, system_id, resource_id) list matches
  sequence_lcs         mean LCS(pred, gold) / max(len) over the same ordered keys
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")

# Keep evaluation independent of the planner. This validator supplies only the
# published vocabulary; no corpus answers or prediction functions are imported.
from validate_output import (OP_INFO, PHASE_ORDER, STATUSES, REMEDIATION_FOR_FINDING,
                             VERIFY_FOR_REMEDIATION, natural)  # noqa: E402


class EvaluationError(ValueError):
    """Invalid predictions/references are not a scored evaluation."""


def _validate_cases(cases: dict, *, reference: bool = False) -> None:
    if not isinstance(cases, dict) or not cases:
        raise EvaluationError("evaluation needs a nonempty mapping of case IDs")
    for cid, row in cases.items():
        if not isinstance(cid, str) or not cid.strip():
            raise EvaluationError("case_id must be a nonempty string")
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise EvaluationError(f"{cid}: expected (deletion_status, steps)")
        status, steps = row
        if not isinstance(status, str) or status not in STATUSES:
            raise EvaluationError(f"{cid}: unknown deletion_status")
        if not isinstance(steps, list):
            raise EvaluationError(f"{cid}: steps_json must be an array")
        if status == "NOT_APPLICABLE" and steps:
            raise EvaluationError(f"{cid}: NOT_APPLICABLE must have []")
        if status != "NOT_APPLICABLE" and not steps:
            raise EvaluationError(f"{cid}: {status} needs verification steps")
        previous = None
        seen = set()
        for position, step in enumerate(steps, 1):
            prefix = f"{cid}: step {position}"
            if not isinstance(step, dict) or set(step) != set(FIELDS):
                raise EvaluationError(f"{prefix}: incorrect fields")
            if type(step["step_no"]) is not int or step["step_no"] != position:
                raise EvaluationError(f"{prefix}: step_no must equal its 1-based position")
            if any(not isinstance(step[field], str) for field in FIELDS if field != "step_no"):
                raise EvaluationError(f"{prefix}: step fields other than step_no must be strings")
            if not step["system_id"].strip() or not step["resource_id"].strip():
                raise EvaluationError(f"{prefix}: empty system_id/resource_id")
            info = OP_INFO.get(step["operation"])
            if info is None or (step["phase"], step["state"]) != info[:2]:
                raise EvaluationError(f"{prefix}: invalid operation/phase/state")
            key = (PHASE_ORDER[step["phase"]], info[2], step["system_id"], step["resource_id"])
            if previous is not None and key < previous:
                raise EvaluationError(f"{prefix}: operation ordering violation")
            if key in seen:
                raise EvaluationError(f"{prefix}: duplicate operation/system/resource")
            previous = key
            seen.add(key)
            if status == "COMPLETE":
                if step["phase"] != "VERIFICATION":
                    raise EvaluationError(f"{prefix}: COMPLETE may only contain verification")
                # Public references contain known blank COMPLETE citations. Treat
                # those as reference limitations, not permission for predictions
                # to omit proof. Corpus citation existence is a separate check.
                if not reference and not step["evidence_id"]:
                    raise EvaluationError(f"{prefix}: COMPLETE prediction lacks evidence")
            if status == "INCOMPLETE":
                if step["phase"] == "FINDING" and not step["evidence_id"]:
                    raise EvaluationError(f"{prefix}: finding lacks evidence")
                if step["phase"] != "FINDING" and step["evidence_id"]:
                    raise EvaluationError(f"{prefix}: future step must not cite evidence")
        if status == "INCOMPLETE":
            ops = {(step["operation"], step["system_id"], step["resource_id"]) for step in steps}
            if any(operation in VERIFY_FOR_REMEDIATION for operation, _, _ in ops) and not any(
                    operation.startswith("REPORT_") for operation, _, _ in ops):
                raise EvaluationError(f"{cid}: remediation without a finding")
            for operation, system, resource in ops:
                for counterpart in (REMEDIATION_FOR_FINDING.get(operation), VERIFY_FOR_REMEDIATION.get(operation)):
                    if counterpart and (counterpart, system, resource) not in ops:
                        raise EvaluationError(f"{cid}: {operation} lacks matching {counterpart}")
                if operation == "REPORT_EXPOSED_PII" and not any(
                        (action, system, resource) in ops for action in ("REDACT_RECORD", "ANONYMIZE_RECORD")):
                    raise EvaluationError(f"{cid}: exposed PII lacks redact/anonymize remediation")


def _json_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"steps_json has duplicate object field {key}")
        result[key] = value
    return result


def load(path: Path, *, reference: bool = False) -> dict:
    cases = {}
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        if next(reader, None) != ["case_id", "deletion_status", "steps_json"]:
            raise EvaluationError(f"{path}: incorrect CSV header")
        for line, row in enumerate(reader, 2):
            if len(row) != 3:
                raise EvaluationError(f"{path}: line {line} must have exactly three columns")
            cid, status, raw = row
            if cid in cases:
                raise EvaluationError(f"{path}: duplicate case_id {cid}")
            try:
                steps = json.loads(raw, object_pairs_hook=_json_object)
            except json.JSONDecodeError as exc:
                raise EvaluationError(f"{path}: line {line} invalid steps_json") from exc
            cases[cid] = (status, steps)
    if list(cases) != sorted(cases, key=natural):
        raise EvaluationError(f"{path}: rows must be sorted by case_id")
    _validate_cases(cases, reference=reference)
    return cases


def step_key(s: dict) -> tuple:
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])


def seq_key(s: dict) -> tuple:
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"])


def full_step_key(s: dict) -> tuple:
    return tuple(s[field] for field in FIELDS)


def step_differences(predicted: Counter, reference: Counter) -> dict:
    """Expand multiset deltas, preserving multiplicity for diagnostics."""
    return {"missing": sorted((reference - predicted).elements()),
            "extra": sorted((predicted - reference).elements())}


def lcs(a: list, b: list) -> int:
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev + 1 if x == y else max(dp[j], dp[j - 1])
            prev = cur
    return dp[-1]


def score(pred: dict, gold: dict) -> dict:
    _validate_cases(pred)
    _validate_cases(gold, reference=True)
    if set(pred) != set(gold):
        raise EvaluationError(f"case IDs differ: missing={sorted(set(gold) - set(pred))} "
                              f"extra={sorted(set(pred) - set(gold))}")
    n = len(gold)
    status_ok = exact = seq_ok = 0
    tp = fp = fn = 0
    field_hits = field_total = 0
    lcs_sum = 0.0
    per_case = {}
    for cid, (gstatus, gsteps) in gold.items():
        pstatus, psteps = pred[cid]
        status_ok += pstatus == gstatus
        exact += pstatus == gstatus and [full_step_key(s) for s in psteps] == [full_step_key(s) for s in gsteps]
        pc, gc = Counter(map(step_key, psteps)), Counter(map(step_key, gsteps))
        inter = sum((pc & gc).values())
        tp += inter
        fp += sum(pc.values()) - inter
        fn += sum(gc.values()) - inter
        width = max(len(psteps), len(gsteps))
        for i in range(width):
            for f in FIELDS:
                field_total += 1
                if i < len(psteps) and i < len(gsteps) and psteps[i].get(f) == gsteps[i].get(f):
                    field_hits += 1
        ps, gs = [seq_key(s) for s in psteps], [seq_key(s) for s in gsteps]
        seq_ok += ps == gs
        lcs_sum += 1.0 if width == 0 else lcs(ps, gs) / width
        per_case[cid] = {"status": (pstatus, gstatus), **step_differences(pc, gc),
                         "exact": pstatus == gstatus and
                         [full_step_key(s) for s in psteps] == [full_step_key(s) for s in gsteps]}
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": n,
        "status_matches": status_ok,
        "exact_cases": exact,
        "matched_steps": tp,
        "predicted_steps": tp + fp,
        "reference_steps": tp + fn,
        "field_hits": field_hits,
        "field_total": field_total,
        "exact_sequences": seq_ok,
        "status_accuracy": status_ok / n,
        "exact_case_accuracy": exact / n,
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "field_accuracy": field_hits / field_total if field_total else 1.0,
        "sequence_accuracy": seq_ok / n,
        "sequence_lcs": lcs_sum / n,
        "per_case": per_case,
    }


def run_and_score(data_dir: Path, requests: str, gold_path: Path, verbose: bool, link_policy: str | None = None,
                  strict_capture_cutoff: bool = False) -> dict:
    from ghost_audit.output import write_output
    from ghost_audit.pipeline import run_pipeline

    from ghost_audit.pipeline import DEFAULT_LINK_POLICY

    run = run_pipeline(data_dir, requests, link_policy=link_policy or DEFAULT_LINK_POLICY,
                       strict_capture_cutoff=strict_capture_cutoff)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "pred.csv"
        write_output(out, [(r.case_id, r.status, r.steps) for r in run.results])
        result = score(load(out), load(gold_path, reference=True))
    if verbose:
        diag = {r.case_id: r for r in run.results}
        for cid, info in result["per_case"].items():
            if not info["exact"]:
                print(f"--- {cid} status pred={info['status'][0]} gold={info['status'][1]}")
                for m in info["missing"]:
                    print("    missing", m)
                for e in info["extra"]:
                    print("    extra  ", e)
                for res in diag[cid].resources:
                    st = res["state_at_audit"]
                    print(f"    res {res['resource_id']:<20} {res['link']['confidence']:<10} {res['link']['method']:<40} "
                          f"{res['policy']['treatment']:<13} compliant={st['compliant']} cite={st['citation']}")
    return result


def _ensure_fixture_rules(root: Path) -> None:
    """Restore any missing policy fixture from the shipped synthetic generator."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_custom_cases", root / "build_custom_cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_rules()


def print_summary(title: str, result: dict) -> None:
    print(f"== {title} ({result['cases']} cases)")
    for k in ("status_accuracy", "exact_case_accuracy", "step_precision", "step_recall", "step_f1",
              "field_accuracy", "sequence_accuracy", "sequence_lcs"):
        print(f"   {k:20s} {result[k]:.4f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred")
    from ghost_audit.config import DEFAULT_DATA_DIR
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="external participant dataset directory")
    ap.add_argument("--gold")
    ap.add_argument("--custom", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", help="write metrics JSON here")
    ap.add_argument("--link-policy", default=None, choices=["no-guess", "full", "corroborated"],
                    help="link policy for pipeline runs (default: the production default)")
    ap.add_argument("--strict-capture-cutoff", action="store_true",
                    help="ignore files exported after audit_at even for linking/state (comparison mode)")
    args = ap.parse_args(argv)
    if bool(args.pred) != bool(args.gold):
        ap.error("--pred and --gold must be provided together")

    repo = CODE_DIR.parent
    results = {}
    if args.pred and args.gold:
        results["files"] = score(load(Path(args.pred)), load(Path(args.gold), reference=True))
        print_summary("files", results["files"])
    elif args.custom:
        root = Path(__file__).resolve().parent / "custom_cases"
        _ensure_fixture_rules(root)
        for case_dir in sorted(p for p in root.iterdir() if (p / "data").is_dir()):
            policy_file = case_dir / "link_policy.txt"
            from ghost_audit.pipeline import DEFAULT_LINK_POLICY

            policy = (policy_file.read_text(encoding="utf-8").strip() if policy_file.exists()
                      else args.link_policy or DEFAULT_LINK_POLICY)
            res = run_and_score(case_dir / "data", "deletion_requests.csv", case_dir / "expected_output.csv",
                                args.verbose, policy, args.strict_capture_cutoff)
            results[case_dir.name] = res
            print_summary(f"{case_dir.name} (link policy {policy})", res)
    else:
        res = run_and_score(Path(args.data_dir), "sample_deletion_requests.csv", Path(args.data_dir) / "sample_output.csv",
                            args.verbose, args.link_policy, args.strict_capture_cutoff)
        results["public_sample"] = res
        print_summary("public sample", res)
    if args.json:
        slim = {k: {m: v for m, v in r.items() if m != "per_case"} for k, r in results.items()}
        Path(args.json).write_text(json.dumps(slim, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvaluationError as error:
        print(f"INVALID EVALUATION: {error}", file=sys.stderr)
        raise SystemExit(2)
