"""Portable preparation, audit, coverage and evaluation commands."""

import argparse
import json
import sys
import time
from pathlib import Path

from phantom.contracts import write_output
from phantom.pipeline import audit_request, prepare
from phantom.planner import AuditBlocked


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("--") and argv[0] != "--help":
        argv.insert(0, "audit")
    parser = argparse.ArgumentParser(description="Local read-only deletion audit")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "audit", "check", "evaluate"):
        child = commands.add_parser(name)
        child.add_argument("--dataset", type=Path, required=True)
        child.add_argument("--cache", type=Path, default=Path(".cache/phantom"))
        if name == "audit":
            child.add_argument("--case-id", action="append")
            child.add_argument("--output", type=Path, default=Path("output.csv"))
        if name == "evaluate":
            child.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            from evaluation.main import evaluate
            outcome=evaluate(args.dataset,args.cache,args.report)
            print(json.dumps(outcome['metrics'],indent=2))
            return 0 if all(case['passed'] and case['mutation_rejected'] is not False for case in outcome['custom']) else 2
        started = time.perf_counter()
        prepared = prepare(args.dataset, cache_dir=args.cache)
        if args.command in {"prepare", "check"}:
            print(json.dumps(dict(files_attempted=prepared.files_attempted, files_supported=prepared.files_supported,
                facts=len(prepared.states.facts), diagnostics=[dict(code=d.code, file_id=d.source.evidence_id, locator=d.source.locator)
                for d in prepared.diagnostics], inventory=prepared.inventory, cache_hit=prepared.cache_hit,
                preparation_seconds=time.perf_counter()-started, model_usage=prepared.model_usage), indent=2))
            return 2 if prepared.diagnostics else 0
        requests = prepared.tables["deletion_requests"]
        if args.case_id:
            selected = set(args.case_id)
            if not selected <= {r["case_id"] for r in requests}:
                raise ValueError("Unknown case ID")
            requests = tuple(r for r in requests if r["case_id"] in selected)
        results, blocked = [], []
        for request in requests:
            try:
                results.append(audit_request(prepared, request))
            except AuditBlocked as exc:
                blocked.append((request["case_id"], str(exc)))
        if blocked:
            print(json.dumps(dict(blocked_cases=blocked, output_written=False)), file=sys.stderr)
            return 2
        write_output(args.output, results, [r["case_id"] for r in requests])
    except (OSError, ValueError, RuntimeError) as exc:
        print("Audit failed: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(f"Wrote {len(results)} verified rows; interpreted {prepared.files_supported}/{prepared.files_attempted} evidence files; compiled cache hit={prepared.cache_hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
