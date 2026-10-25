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


def load(path: Path) -> dict:
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def step_key(s: dict) -> tuple:
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])


def seq_key(s: dict) -> tuple:
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"])


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
    n = len(gold)
    status_ok = exact = seq_ok = 0
    tp = fp = fn = 0
    field_hits = field_total = 0
    lcs_sum = 0.0
    per_case = {}
    for cid, (gstatus, gsteps) in gold.items():
        pstatus, psteps = pred.get(cid, ("<missing>", []))
        status_ok += pstatus == gstatus
        exact += pstatus == gstatus and [step_key(s) for s in psteps] == [step_key(s) for s in gsteps]
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
        per_case[cid] = {"status": (pstatus, gstatus), "missing": sorted(set(gc) - set(pc)),
                         "extra": sorted(set(pc) - set(gc))}
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": n,
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
        result = score(load(out), load(gold_path))
    if verbose:
        diag = {r.case_id: r for r in run.results}
        for cid, info in result["per_case"].items():
            if info["missing"] or info["extra"] or info["status"][0] != info["status"][1]:
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
    """The fixtures use the challenge's own deletion_rules.csv, which code.zip does not ship."""
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

    repo = CODE_DIR.parent
    results = {}
    if args.pred and args.gold:
        results["files"] = score(load(Path(args.pred)), load(Path(args.gold)))
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
    raise SystemExit(main())
