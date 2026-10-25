"""Write independent advisory checks; --strict returns nonzero for detected errors."""

import argparse
import csv
import json
from pathlib import Path

from ghost_audit.atomic import atomic_write_text
from ghost_audit.verification import check_predictions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--requests", default="deletion_requests.csv")
    parser.add_argument("--report", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    from ghost_audit.release import check_output_paths
    check_output_paths(Path(args.data_dir), [Path(args.report)])
    if Path(args.output).resolve() == Path(args.report).resolve():
        parser.error("report path must differ from the prediction input")
    from validate_output import validate

    errors = validate(Path(args.output), Path(args.data_dir), args.requests)
    if errors:
        print("Invalid output contract:", errors[:8])
        return 2
    with Path(args.output).open(encoding="utf-8", newline="") as stream:
        rows = [(r["case_id"], r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(stream)]
    report = check_predictions(Path(args.data_dir), rows, args.requests)
    atomic_write_text(Path(args.report), json.dumps(report, indent=2))
    print(f"Advisory evidence checks: {report['errors']} errors, {report['reviews']} review notes")
    return int(args.strict and report["errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
