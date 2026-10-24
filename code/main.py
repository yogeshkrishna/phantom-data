"""Minimal audited CLI; unsupported proof fails before replacing the output."""

import argparse
import sys
from pathlib import Path

from phantom.contracts import write_output
from phantom.pipeline import audit_request, prepare
from phantom.planner import AuditBlocked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit supplied deletion evidence without modifying it")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--case-id", action="append", help="Select a main request; repeatable")
    parser.add_argument("--output", type=Path, default=Path("output.csv"))
    args = parser.parse_args(argv)
    try:
        prepared = prepare(args.dataset)
        requests = prepared.tables["deletion_requests"]
        if args.case_id:
            selected = set(args.case_id)
            if not selected <= {r["case_id"] for r in requests}:
                raise ValueError("Unknown requested case ID")
            requests = tuple(r for r in requests if r["case_id"] in selected)
        results = [audit_request(prepared, request) for request in requests]
        write_output(args.output, results, [r["case_id"] for r in requests])
    except (OSError, ValueError) as exc:
        # Adapters sanitize source failures. Do not print arbitrary raw record values.
        code = str(exc) if isinstance(exc, AuditBlocked) else type(exc).__name__
        print("Audit blocked: " + code, file=sys.stderr)
        return 2
    print(f"Wrote {len(results)} verified rows; interpreted {prepared.files_supported}/{prepared.files_attempted} evidence files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
