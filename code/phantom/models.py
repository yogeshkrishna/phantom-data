"""Small shared evidence contract; no predictions or sample labels live here."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def timestamp(value: str) -> datetime:
    """Compare instants, never filenames or lexicographic event identifiers."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class Observation:
    evidence_id: str
    captured_at: str
    observed_at: str
    system_id: str
    resource_id: str
    category: str = ""
    state: str = ""
    identifiers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    source_location: str = ""
    source_sha256: str = ""
    proof_kind: str = "state"
    trace_id: str = ""
    parent_resource_id: str = ""

    def available_at(self, cutoff: str) -> bool:
        end = timestamp(cutoff)
        return timestamp(self.captured_at) <= end and timestamp(self.observed_at) <= end

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Subject:
    user_id: str
    identifiers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    audit_ids: tuple[str, ...] = ()


PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
OPERATIONS = {
    "FINDING": ("REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"),
    "REMEDIATION": ("DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"),
    "VERIFICATION": ("VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"),
}
OPERATION_STATES = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")
