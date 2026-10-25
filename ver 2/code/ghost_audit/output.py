"""Deterministic step ordering and CSV/JSON serialization."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import OPERATION_RANK, OUTPUT_HEADER, PHASE_RANK, STEP_FIELDS


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
    head, _, tail = case_id.rpartition("_")
    return (head, int(tail)) if tail.isdigit() else (case_id, 0)


def write_output(path: Path, rows: list[tuple[str, str, list]]) -> None:
    rows = sorted(rows, key=lambda r: case_sort_key(r[0]))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(OUTPUT_HEADER)
        for case_id, status, steps in rows:
            writer.writerow([case_id, status, steps_to_json(steps)])
