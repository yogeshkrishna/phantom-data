"""CSV-governed obligations; uncertainty is distinct from supported noncompliance."""

import re
from dataclasses import dataclass

from .contracts import EntityKey, EvidenceFact, EvidenceRef, parse_instant
from .identities import FIELD_ALIASES, normalize_identifier
from .state import ResourceState, StateView, STABLE_FIELDS, leaves


@dataclass(frozen=True, slots=True)
class PolicyRule:
    rule_id: str
    category: str
    condition: str
    action: str


@dataclass(frozen=True, slots=True)
class Obligation:
    entity: EntityKey
    rule_id: str
    treatment: str
    compliant: bool | None
    finding: str | None
    remediation: str | None
    verification: str
    proofs: tuple[EvidenceRef, ...]
    reasons: tuple[str, ...]


CONDITIONS = {
    "Always", "Conversation contains shared customer, agent, or order context that must be retained",
    "Attachment belongs only to the deletion subject", "Record is still attributable to a user",
    "Log contains a user email address", "Record is within the supplied retention period",
    "A supplied legal hold is active", "Restore access can be blocked",
    "Account is deleted and only a stable internal user_id is needed to preserve references to retained records",
}


def _field_values(resource):
    for field in resource.fields:
        for path, value in leaves(field.value, (field.field,)):
            yield path, value, field.fact


def exposed_fields(resource: ResourceState, view: StateView, *, allow_stable=False, email_only=False):
    """Return current prohibited values with provenance, including free text and hashes.

    A stable reference exception accepts only an exact resolved internal user ID.
    Redaction markers/null are not by themselves a proof that an omitted field cleared.
    """
    found = []
    markers = {"", "[redacted]", "redacted", "[removed]", "anonymous", "anonymized"}
    aliases = [(field, normalize_identifier(field, value)) for field, values in view.identity.identifiers.items()
               for value in values if value and field != "name"]
    for path, value, fact in _field_values(resource):
        if not isinstance(value, str) or value.strip().casefold() in markers:
            continue
        key = path[-1]
        stable = key in STABLE_FIELDS and value in view.identity.user_ids
        if allow_stable and stable:
            continue
        typed = FIELD_ALIASES.get(key)
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[\w-]+", value)
        exposed = bool(emails) or (typed == "email" and bool(value.strip()))
        if not email_only:
            exposed = exposed or typed is not None or key in STABLE_FIELDS
            for field, alias in aliases:
                text = normalize_identifier(field, value)
                if field in {"email", "email_hash", "user_id"} and re.search(
                        r"(?<![\w@.+-])" + re.escape(alias) + r"(?![\w@.+-])", text):
                    exposed = True
                if field == "phone" and text == alias:
                    exposed = True
            for name in view.identity.identifiers.get("name", ()):
                if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", value, re.I):
                    exposed = True
        if exposed:
            found.append((path, fact))
    return tuple(found)


def _values(resource, key):
    return [value for path, value, _ in _field_values(resource) if path[-1] == key]


def _proofs(facts):
    return tuple(dict.fromkeys(fact.source for fact in facts if fact.source.evidence_id))


class PolicyEngine:
    def __init__(self, rows):
        rules = {}
        for row in rows:
            rule = PolicyRule(row["rule_id"], row["data_category"], row["condition"], row["required_action"])
            if rule.category in rules or rule.action not in {"DELETE", "REDACT", "ANONYMIZE", "RETAIN"}:
                raise ValueError("duplicate category or unsupported policy action")
            if rule.condition not in CONDITIONS:
                raise ValueError("unsupported policy condition; review required")
            rules[rule.category] = rule
        if not rules:
            raise ValueError("policy must not be empty")
        self.rules = rules

    def evaluate(self, view: StateView) -> tuple[Obligation, ...]:
        result = []
        for resource in view.resources:
            result.extend(self.for_resource(resource, view))
        return tuple(result)

    def for_resource(self, resource: ResourceState, view: StateView) -> tuple[Obligation, ...]:
        if resource.category == "DATA_FLOW":
            return (self._risk(resource, "FLOW"),)
        rule = self.rules.get(resource.category)
        if rule is None:
            return (Obligation(resource.entity, "", "UNKNOWN", None, None, None,
                               "VERIFY_RECORD_PRESENT", (), ("UNKNOWN_POLICY",)),)
        base = dict(entity=resource.entity, rule_id=rule.rule_id, treatment=rule.action)
        reasons = list(resource.issues)
        present = resource.state in {"PRESENT", "REDACTED", "ANONYMIZED", "ACTIVE", "BLOCKED", "DISABLED"}
        absent = resource.state == "ABSENT"
        proofs = _proofs(resource.latest)
        if rule.action == "DELETE":
            conditional = rule.condition == "Attachment belongs only to the deletion subject"
            # A linked attachment is not assumed exclusively owned when explicitly shared.
            shared = any(value is True for value in _values(resource, "shared"))
            if conditional and shared:
                return (Obligation(**base, compliant=None, finding=None, remediation=None,
                                   verification="VERIFY_RECORD_ABSENT", proofs=(), reasons=("SHARED_ATTACHMENT_POLICY_UNSPECIFIED",)),)
            compliant = True if absent else False if present else None
            return (Obligation(**base, compliant=compliant if not reasons else None,
                               finding="REPORT_REMAINING_RECORD" if present else None,
                               remediation="DELETE_RECORD" if present else None,
                               verification="VERIFY_RECORD_ABSENT", proofs=proofs, reasons=tuple(reasons)),)
        if rule.action in {"REDACT", "ANONYMIZE"}:
            allow_stable = resource.category == "ACCOUNT_TOMBSTONE"
            if allow_stable and any(str(value).upper() != "DELETED" for value in _values(resource, "account_status")):
                reasons.append("TOMBSTONE_CONDITION_UNPROVEN")
            pii = exposed_fields(resource, view, allow_stable=allow_stable,
                                 email_only=resource.category == "EMAIL_DELIVERY_LOG")
            target = "REDACTED" if rule.action == "REDACT" else "ANONYMIZED"
            known_treatment = resource.state == target
            # Explicit readback of treatment is evidence; an empty generic object is not.
            covered_identifiers = {FIELD_ALIASES.get(path[-1]) for path, _, _ in _field_values(resource)}
            coverage = known_treatment or {"name", "email", "phone", "email_hash"} <= covered_identifiers or (
                bool(resource.latest) and all(f.metadata.get("full_record") is True for f in resource.latest))
            compliant = False if pii else True if present and coverage else None
            if absent:
                reasons.append("REQUIRED_SHARED_RECORD_MISSING" if rule.action == "REDACT" else "ANONYMIZED_RECORD_NOT_AVAILABLE")
            if reasons:
                compliant = None
            return (Obligation(**base, compliant=compliant,
                               finding="REPORT_EXPOSED_PII" if pii else None,
                               remediation=rule.action + "_RECORD" if pii else None,
                               verification="VERIFY_RECORD_" + target,
                               proofs=_proofs([fact for _, fact in pii]) if pii else proofs,
                               reasons=tuple(reasons)),)
        # RETAIN: never infer deletion authority from expiry or a missing condition.
        if resource.category in {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}:
            key = "retention_until" if resource.category == "SECURITY_AUDIT_LOG" else "retained_until"
            dates = _values(resource, key)
            active_flags = _values(resource, "legal_hold_active") if resource.category == "LEGAL_HOLD_RECORD" else []
            if dates:
                try:
                    if not all(parse_instant(value) >= parse_instant(view.audit_at) for value in dates):
                        reasons.append("RETENTION_EXPIRED_NO_FALLBACK_RULE")
                except (ValueError, TypeError):
                    reasons.append("INVALID_RETENTION_BOUNDARY")
            elif not (active_flags and all(value is True for value in active_flags)):
                reasons.append("RETENTION_CONDITION_UNPROVEN")
        if absent:
            reasons.append("REQUIRED_RETAINED_RECORD_MISSING")
        pii = exposed_fields(resource, view, allow_stable=True) if resource.category != "BACKUP_ARCHIVE" else ()
        if pii:
            reasons.append("RETAINED_RECORD_EXPOSES_DIRECT_PII")
        # For security/legal direct PII requires redaction while retaining the record.
        retain = Obligation(**base, compliant=(False if pii else True) if present and not
                            any(reason != "RETAINED_RECORD_EXPOSES_DIRECT_PII" for reason in reasons) else None,
                            finding="REPORT_EXPOSED_PII" if pii else None,
                            remediation="REDACT_RECORD" if pii else None,
                            verification="VERIFY_RECORD_PRESENT", proofs=_proofs([fact for _, fact in pii]) if pii else proofs,
                            reasons=tuple(reasons))
        result = [retain]
        if pii:
            result.append(Obligation(resource.entity, rule.rule_id, "REDACT", False,
                                     "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED",
                                     _proofs([fact for _, fact in pii]), ("PRESERVE_RETAINED_RECORD",)))
        if resource.category == "BACKUP_ARCHIVE":
            result.append(self._risk(resource, "RESTORE", rule.rule_id))
        return tuple(result)

    @staticmethod
    def _risk(resource, kind, rule_id=""):
        flow = kind == "FLOW"
        field = "enabled" if flow else "restore_allowed"
        states = {"DISABLED"} if flow else {"BLOCKED"}
        values = _values(resource, field)
        active = any(value is True for value in values) or resource.state == "ACTIVE"
        safe = bool(values and all(value is False for value in values)) or resource.state in states
        conflict = active and safe
        compliant = None if conflict or resource.issues else False if active else True if safe else None
        return Obligation(resource.entity, rule_id, kind, compliant,
                          ("REPORT_ACTIVE_DATA_FLOW" if flow else "REPORT_RESTORE_RISK") if active else None,
                          ("DISABLE_DATA_FLOW" if flow else "BLOCK_RESTORE") if active else None,
                          "VERIFY_DATA_FLOW_DISABLED" if flow else "VERIFY_RESTORE_BLOCKED",
                          _proofs([f.fact for f in resource.fields if f.field == field]) or _proofs(resource.latest),
                          ("CONFLICTING_RISK_STATE",) if conflict else resource.issues if resource.issues else
                          ("RISK_STATE_UNPROVEN",) if compliant is None else ())
