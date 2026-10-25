"""Checks required before replacing a final prediction artifact."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path


def validate_dataset(data_dir: Path, requests_file: str) -> None:
    required = {
        requests_file: ("case_id", "email", "requested_at", "reported_at", "audit_at", "request_text", "reported_status"),
        "user_identities.csv": ("user_id", "name", "email"),
        "user_audit_log.csv": ("audit_id", "user_id", "field_name", "old_value", "new_value", "event_time"),
        "system_activity.csv": ("activity_id", "event_time", "resource_id", "event_type", "source_system", "target_system", "reported_result", "details"),
        "deletion_rules.csv": ("rule_id", "data_category", "required_action", "condition"),
        "evidence_files.csv": ("file_id", "file_path", "captured_at"),
    }
    from datetime import datetime

    for filename, fields in required.items():
        with (data_dir / filename).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not set(fields).issubset(reader.fieldnames):
                raise ValueError(f"{filename}: missing required input columns")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(f"{filename}: duplicate input columns")
            seen = set()
            for row in reader:
                key = row.get(fields[0])
                if not key or key in seen or None in row or any(row.get(f) is None for f in fields):
                    raise ValueError(f"{filename}: malformed or duplicate input row")
                seen.add(key)
                for field in ("event_time", "captured_at", "requested_at", "reported_at", "audit_at"):
                    if field in row:
                        datetime.fromisoformat(row[field].replace("Z", "+00:00"))
                if filename == "deletion_rules.csv" and row["required_action"] not in {"DELETE", "REDACT", "ANONYMIZE", "RETAIN"}:
                    raise ValueError("unrecognized policy action; refusing implicit destructive fallback")
                if filename == "evidence_files.csv":
                    candidate = (data_dir / row["file_path"]).resolve()
                    if not candidate.is_relative_to(data_dir.resolve()):
                        raise ValueError("evidence manifest path leaves the input corpus")


def validate_for_dataset(data: bytes, data_dir: Path, requests_file: str) -> None:
    """Run the independent validator against staged bytes, never the destination."""
    from validate_output import validate

    with tempfile.TemporaryDirectory(prefix="ghost-output-check-") as temporary:
        staged = Path(temporary) / "output.csv"
        staged.write_bytes(data)
        errors = validate(staged, data_dir, requests_file)
    if errors:
        raise ValueError("dataset/output contract failed: " + "; ".join(errors[:8]))


def check_output_paths(data_dir: Path, paths: list[Path]) -> None:
    resolved = [p.resolve() for p in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("output and telemetry paths must be distinct")
    if any(p.is_relative_to(data_dir.resolve()) for p in resolved):
        raise ValueError("output paths must be outside the read-only corpus")
