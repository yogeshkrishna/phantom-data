"""Static contract tables and repository paths."""

from __future__ import annotations

import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DEFAULT_DATA_DIR = Path(os.environ.get("GHOST_AUDIT_DATA_DIR", REPO_ROOT / "dataset"))
DEFAULT_OUTPUT = REPO_ROOT / "output.csv"
EVIDENCE_CACHE_DIR = Path(os.environ.get("GHOST_AUDIT_CACHE_DIR", REPO_ROOT / ".cache" / "ghost_audit"))

PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

# Operation -> (phase, state). Order of this table is the grader's operation order.
OPERATIONS = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
OPERATION_RANK = {op: i for i, op in enumerate(OPERATIONS)}
PHASE_RANK = {p: i for i, p in enumerate(PHASES)}

STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
OUTPUT_HEADER = ("case_id", "deletion_status", "steps_json")
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")

# Resource-id prefix -> owning system, used when evidence names a resource without its system.
PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "marketing": "marketing_platform",
    "legal_hold": "legal_service",
    "object": "object_storage",
}

# Final-state treatment -> (finding op, remediation op, verification op)
TREATMENT_OPS = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "DISABLE_FLOW": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCK_RESTORE": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "RETAIN": (None, None, "VERIFY_RECORD_PRESENT"),
}
