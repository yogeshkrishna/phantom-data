"""Deterministic step ordering and CSV/JSON serialization."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .config import OPERATION_RANK, OUTPUT_HEADER, PHASE_RANK, STEP_FIELDS
from .atomic import atomic_write_bytes


def order_steps(steps: list[dict]) -> list[dict]:
    """Phase, then operation-table order, then system_id, then resource_id; renumber from 1."""
    unique = {}
    for s in steps:
        unique.setdefault((s["phase"], s["operation"], s["system_id"], s["resource_id"]), s)
    ordered = sorted(unique.values(), key=lambda s: (PHASE_RANK[s["phase"]], OPERATION_RANK[s["operation"]],
                                                      s["system_id"], s["resource_id"]))
    out = []
    for i, s in enumerate(ordered, start=1):
        out.append({k: (i if k == "step_no" else s[k]) for k in STEP_FIELDS})
    return out


def steps_to_json(steps: list[dict]) -> str:
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"))


def case_sort_key(case_id: str):
    match = re.match(r"(.*?)(\d+)$", case_id)
    return (match.group(1), int(match.group(2))) if match else (case_id, 0)


def serialize_output(rows: list[tuple[str, str, list]]) -> bytes:
    """Serialize completely before the destination can be opened or replaced."""
    rows = sorted(rows, key=lambda r: case_sort_key(r[0]))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(OUTPUT_HEADER)
    for case_id, status, steps in rows:
        writer.writerow([case_id, status, steps_to_json(steps)])
    return stream.getvalue().encode("utf-8")


def write_output(path: Path, rows: list[tuple[str, str, list]], *, validator=None) -> None:
    """Publish only serialized, structurally valid rows and optional external checks."""
    data = serialize_output(rows)
    # Separate from planner decisions; also used by evaluation and the standalone CLI.
    from validate_output import validate_serialized

    errors = validate_serialized(data)
    if errors:
        raise ValueError("output contract failed: " + "; ".join(errors[:8]))
    if validator is not None:
        validator(data)
    atomic_write_bytes(path, data)
