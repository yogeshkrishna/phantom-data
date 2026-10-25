"""Independent contract validator for output.csv.

Shares no code with the prediction pipeline: it re-reads the raw dataset files and checks the
submission against the published contract. Exit code 0 = valid, 1 = violations found.

    python code/validate_output.py                 # validates ./output.csv against dataset/
    python code/validate_output.py path/to/out.csv --data-dir dataset --requests deletion_requests.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HEADER = ["case_id", "deletion_status", "steps_json"]
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
OPS = [  # (operation, phase, state) in grader order
    ("REPORT_REMAINING_RECORD", "FINDING", "PRESENT"),
    ("REPORT_EXPOSED_PII", "FINDING", "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", "FINDING", "ACTIVE"),
    ("REPORT_RESTORE_RISK", "FINDING", "ACTIVE"),
    ("DISABLE_DATA_FLOW", "REMEDIATION", "DISABLED"),
    ("BLOCK_RESTORE", "REMEDIATION", "BLOCKED"),
    ("DELETE_RECORD", "REMEDIATION", "ABSENT"),
    ("REDACT_RECORD", "REMEDIATION", "REDACTED"),
    ("ANONYMIZE_RECORD", "REMEDIATION", "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", "VERIFICATION", "ABSENT"),
    ("VERIFY_RECORD_PRESENT", "VERIFICATION", "PRESENT"),
    ("VERIFY_RECORD_REDACTED", "VERIFICATION", "REDACTED"),
    ("VERIFY_RECORD_ANONYMIZED", "VERIFICATION", "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", "VERIFICATION", "DISABLED"),
    ("VERIFY_RESTORE_BLOCKED", "VERIFICATION", "BLOCKED"),
]
OP_INFO = {op: (phase, state, i) for i, (op, phase, state) in enumerate(OPS)}
PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
REMEDIATION_FOR_FINDING = {"REPORT_REMAINING_RECORD": "DELETE_RECORD",
                           "REPORT_ACTIVE_DATA_FLOW": "DISABLE_DATA_FLOW",
                           "REPORT_RESTORE_RISK": "BLOCK_RESTORE"}
VERIFY_FOR_REMEDIATION = {"DELETE_RECORD": "VERIFY_RECORD_ABSENT", "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
                          "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
                          "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED", "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED"}


def ts(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def natural(case_id: str):
    m = re.match(r"(.*?)(\d+)$", case_id)
    return (m.group(1), int(m.group(2))) if m else (case_id, 0)


def read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def validate(out_path: Path, data_dir: Path, requests_file: str, warnings: list | None = None) -> list[str]:
    errors: list[str] = []
    requests = {r["case_id"]: r for r in read(data_dir / requests_file)}
    evidence_time = {r["file_id"]: ts(r["captured_at"]) for r in read(data_dir / "evidence_files.csv")}
    evidence_time.update({r["activity_id"]: ts(r["event_time"]) for r in read(data_dir / "system_activity.csv")})
    evidence_time.update({r["audit_id"]: ts(r["event_time"]) for r in read(data_dir / "user_audit_log.csv")})

    with open(out_path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = list(reader)
    if header != HEADER:
        errors.append(f"header {header} != {HEADER}")
    ids = [r[0] for r in rows if r]
    if len(rows) != len(requests):
        errors.append(f"{len(rows)} data rows, expected {len(requests)}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case_id rows")
    if set(ids) != set(requests):
        errors.append(f"case ids differ: missing={sorted(set(requests) - set(ids))[:5]} "
                      f"extra={sorted(set(ids) - set(requests))[:5]}")
    if ids != sorted(ids, key=natural):
        errors.append("rows are not sorted by case_id")

    for row in rows:
        if len(row) != 3:
            errors.append(f"row with {len(row)} columns: {row[:1]}")
            continue
        cid, status, raw = row
        where = f"{cid}:"
        if status not in STATUSES:
            errors.append(f"{where} bad status {status}")
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{where} steps_json is not JSON ({exc})")
            continue
        if not isinstance(steps, list):
            errors.append(f"{where} steps_json is not an array")
            continue
        audit_at = ts(requests[cid]["audit_at"]) if cid in requests else None
        if status == "NOT_APPLICABLE" and steps:
            errors.append(f"{where} NOT_APPLICABLE must have []")
        if status in ("COMPLETE", "INCOMPLETE") and not steps:
            errors.append(f"{where} {status} with no steps")
        prev = None
        seen = set()
        for i, s in enumerate(steps, start=1):
            if not isinstance(s, dict) or set(s) != FIELDS:
                errors.append(f"{where} step {i} fields {sorted(s) if isinstance(s, dict) else type(s)}")
                continue
            if s["step_no"] != i or not isinstance(s["step_no"], int):
                errors.append(f"{where} step_no {s['step_no']} at position {i}")
            info = OP_INFO.get(s["operation"])
            if info is None:
                errors.append(f"{where} unknown operation {s['operation']}")
                continue
            phase, state, rank = info
            if s["phase"] != phase or s["state"] != state:
                errors.append(f"{where} {s['operation']} needs phase={phase} state={state}")
            for f in ("system_id", "resource_id", "evidence_id"):
                if not isinstance(s[f], str):
                    errors.append(f"{where} {f} must be a string")
            if not s["system_id"] or not s["resource_id"]:
                errors.append(f"{where} empty system_id/resource_id")
            key = (PHASE_ORDER[phase], rank, s["system_id"], s["resource_id"])
            if prev is not None and key < prev:
                errors.append(f"{where} ordering violation at step {i}")
            if key in seen:
                errors.append(f"{where} duplicate step {s['operation']} {s['resource_id']}")
            seen.add(key)
            prev = key
            ev = s["evidence_id"]
            if status == "COMPLETE":
                if phase != "VERIFICATION":
                    errors.append(f"{where} COMPLETE may only contain VERIFICATION steps")
                if not ev:
                    errors.append(f"{where} COMPLETE verification step {i} lacks evidence")
            if status == "INCOMPLETE":
                if phase == "FINDING" and not ev:
                    errors.append(f"{where} finding step {i} lacks evidence")
                if phase != "FINDING" and ev:
                    errors.append(f"{where} future step {i} must have empty evidence_id")
            if ev:
                if ev not in evidence_time:
                    errors.append(f"{where} unknown evidence id {ev}")
                elif audit_at and evidence_time[ev] > audit_at:
                    errors.append(f"{where} evidence {ev} is later than audit_at")
        if status == "INCOMPLETE":
            ops = {(s["operation"], s["resource_id"]) for s in steps if isinstance(s, dict)}
            if not any(op.startswith("REPORT_") for op, _ in ops):
                # The schema has no UNKNOWN status; a case whose completion cannot be established
                # (no evidenced violation) may carry only the checks that must pass.
                if any(op in VERIFY_FOR_REMEDIATION for op, _ in ops):
                    errors.append(f"{where} INCOMPLETE remediation without any finding")
                elif warnings is not None:
                    warnings.append(f"{where} INCOMPLETE without findings (verification-only)")
            for op, rid in ops:
                if op in REMEDIATION_FOR_FINDING and (REMEDIATION_FOR_FINDING[op], rid) not in ops:
                    errors.append(f"{where} finding {op} {rid} has no matching remediation")
                if op in VERIFY_FOR_REMEDIATION and (VERIFY_FOR_REMEDIATION[op], rid) not in ops:
                    errors.append(f"{where} remediation {op} {rid} has no verification")
    return errors


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?", default=str(repo / "output.csv"))
    ap.add_argument("--data-dir", default=str(repo / "dataset"))
    ap.add_argument("--requests", default="deletion_requests.csv")
    args = ap.parse_args(argv)
    warnings: list = []
    errors = validate(Path(args.output), Path(args.data_dir), args.requests, warnings)
    for w in warnings[:20]:
        print("  warning:", w)
    if errors:
        print(f"INVALID: {len(errors)} problem(s)")
        for e in errors[:50]:
            print("  -", e)
        return 1
    print(f"VALID: {args.output} satisfies the output contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
