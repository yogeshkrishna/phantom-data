"""Map a resource's data category to the required treatment from deletion_rules.csv."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .loaders import parse_ts

# Categories observed in evidence that the rules file names differently.
CATEGORY_ALIASES = {
    "SUPPORT_CONTACT_COPY": "SECONDARY_IDENTIFIER",  # legacy contact-index copy of identifiers
    "DATA_FLOW": "DATA_FLOW",
}

# Required action -> internal treatment. BACKUP_ARCHIVE is retained but its restore path is blocked.
ACTION_TREATMENT = {"DELETE": "DELETE", "REDACT": "REDACT", "ANONYMIZE": "ANONYMIZE", "RETAIN": "RETAIN"}


@dataclass
class Decision:
    category: str
    rule_id: str
    action: str
    treatment: str  # DELETE / REDACT / ANONYMIZE / RETAIN / BLOCK_RESTORE / DISABLE_FLOW
    note: str = ""


class Policy:
    def __init__(self, rules: list[dict]):
        self.rules = {r["data_category"]: r for r in rules}

    def decide(self, category: str, flags: dict, audit_at: datetime) -> Decision:
        category = CATEGORY_ALIASES.get(category, category)
        if category == "DATA_FLOW":
            # Not a stored record: an active sync/import path must be disabled so it cannot
            # recreate deleted data (problem statement: no active sync, import, or restore path).
            return Decision(category, "-", "DISABLE", "DISABLE_FLOW", "active data flow")
        rule = self.rules.get(category)
        if rule is None:
            return Decision(category, "-", "DELETE", "DELETE", "no explicit rule; personal data defaults to delete")
        action = rule["required_action"]
        treatment = ACTION_TREATMENT.get(action, "DELETE")
        note = rule["condition"]
        if category == "BACKUP_ARCHIVE":
            treatment = "BLOCK_RESTORE"
        elif category == "SECURITY_AUDIT_LOG":
            until = parse_ts(flags.get("retention_until") or "")
            if until is not None and until < audit_at:
                return Decision(category, rule["rule_id"], "DELETE", "DELETE", "retention period expired")
        elif category == "LEGAL_HOLD_RECORD":
            until = parse_ts(flags.get("retained_until") or "")
            if until is not None and until < audit_at:
                return Decision(category, rule["rule_id"], "DELETE", "DELETE", "legal hold expired")
        return Decision(category, rule["rule_id"], action, treatment, note)
