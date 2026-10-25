"""Structured handover observations, with explicit coverage for unparsed text."""

import hashlib
from dataclasses import replace

from .contracts import EvidenceRef, parse_instant
from .json_evidence import EvidenceBatch, EvidenceDiagnostic
from .log_evidence import event_fact


def normalize_text(text: str, source: EvidenceRef) -> EvidenceBatch:
    parse_instant(source.available_at)
    facts, diagnostics = [], []
    seen = normalized = 0
    headers = {"CUSTOMER PLATFORM - SHIFT HANDOVER", "Account and downstream checks completed during the shift:"}
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        ref = replace(source, locator=f"/lines/{line_number}")
        seen += 1
        if line in headers:
            normalized += 1
            continue
        if line.startswith("Snapshot: "):
            try:
                parse_instant(line.removeprefix("Snapshot: "))
                normalized += 1
                continue
            except ValueError:
                pass
        try:
            if not line.startswith("- "):
                raise ValueError("unknown handover line")
            values = [part.strip() for part in line[2:].split("|")]
            if len(values) != 6:
                raise ValueError("invalid handover record")
            timestamp, system, resource, category, result, identifier = values
            parse_instant(timestamp)
            data = dict(event="record_lookup", service=system, system=system, resource_id=resource,
                        record_type=category, result=result, subject_reference=identifier)
            facts.append(event_fact(timestamp, data, ref))
            normalized += 1
        except (ValueError, TypeError, KeyError):
            diagnostics.append(EvidenceDiagnostic("UNKNOWN_TEXT_RECORD", ref, "Unparsed handover content"))
    return EvidenceBatch("handover-text", tuple(facts), (), tuple(diagnostics), seen, normalized)


def read_text_evidence(entry):
    raw = entry.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError("text changed after manifest hashing")
    return normalize_text(raw.decode("utf-8-sig"), EvidenceRef(
        entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at))
