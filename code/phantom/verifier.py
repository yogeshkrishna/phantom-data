"""Independent output checker. Never calls the planner or trusts its obligations."""

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from .contracts import AuditResult, parse_instant, to_csv_row
from .identities import FIELD_ALIASES, normalize_identifier
from .intent import classify_intent
from .state import STABLE_FIELDS, leaves


@dataclass(frozen=True, slots=True)
class VerificationReport:
    valid: bool
    issues: tuple[str, ...]


@lru_cache(maxsize=4096)
def _boundary_pattern(alias, name=False):
    boundary = r"\w" if name else r"\w@.+-"
    return re.compile(r"(?<![" + boundary + r"])" + re.escape(alias) + r"(?![" + boundary + r"])", re.I)


def _unsafe(resource, identity, allow_stable=False, email_only=False):
    facts = [fact for fact in resource.latest if fact.metadata.get("pii_detected") is True or
             fact.metadata.get("readback_result") == "IDENTIFIABLE"]
    for field in resource.fields:
        for path, value in leaves(field.value, (field.field,)):
            if not isinstance(value, str) or value.strip().casefold() in {
                    "", "[redacted]", "redacted", "[removed]", "anonymous", "anonymized"}:
                continue
            key = path[-1]
            if allow_stable and key in STABLE_FIELDS and value in identity.user_ids:
                continue
            unsafe = bool(re.search(r"[\w.+-]+@[\w.-]+\.[\w-]+", value)) or FIELD_ALIASES.get(key) == "email"
            if not email_only:
                unsafe |= key in FIELD_ALIASES or key in STABLE_FIELDS
                for kind, aliases in identity.identifiers.items():
                    for alias in aliases:
                        if kind == "phone":
                            number = normalize_identifier(kind, alias)
                            unsafe |= normalize_identifier(kind, value) == number
                            if sum(c.isdigit() for c in number) >= 7:
                                unsafe |= any(normalize_identifier(kind, m[0]) == number for m in
                                              re.finditer(r"(?<![\w+])\+?\d[\d ().-]{5,}\d(?!\w)", value))
                        else:
                            unsafe |= bool(_boundary_pattern(alias, kind == "name").search(value))
            if unsafe:
                facts.append(field.fact)
    return facts


def _requirements(resource, view, rule):
    """Independently derive (check, compliance, finding, remediation, citations)."""
    latest_ids = {f.source.evidence_id for f in resource.latest if f.source.evidence_id}
    fields = defaultdict(list)
    for field in resource.fields:
        for path, value in leaves(field.value, (field.field,)):
            fields[path[-1]].append(value)
    exists = resource.state in {"PRESENT", "REDACTED", "ANONYMIZED", "ACTIVE", "DISABLED", "BLOCKED"}
    if resource.category == "DATA_FLOW" or resource.category == "BACKUP_ARCHIVE":
        flow = resource.category == "DATA_FLOW"
        key = "enabled" if flow else "restore_allowed"
        values = fields[key]
        active = any(v is True for v in values) or resource.state == "ACTIVE"
        safe = bool(values and all(v is False for v in values)) or resource.state == ("DISABLED" if flow else "BLOCKED")
        proof = {f.fact.source.evidence_id for f in resource.fields if f.field == key} or latest_ids
        compliance = None if resource.issues or active == safe else bool(safe)
        risk = ("VERIFY_DATA_FLOW_DISABLED" if flow else "VERIFY_RESTORE_BLOCKED", compliance,
                ("REPORT_ACTIVE_DATA_FLOW" if flow else "REPORT_RESTORE_RISK") if active else None,
                ("DISABLE_DATA_FLOW" if flow else "BLOCK_RESTORE") if active else None, proof)
        if flow:
            return [risk]
    if rule is None:
        return [("VERIFY_RECORD_PRESENT", None, None, None, set())]
    action = rule["required_action"]
    if action == "DELETE":
        shared = resource.category == "SUPPORT_ATTACHMENT" and any(v is True for v in fields["shared"])
        compliance = None if resource.issues or shared else resource.state == "ABSENT" if exists or resource.state == "ABSENT" else None
        return [("VERIFY_RECORD_ABSENT", compliance, "REPORT_REMAINING_RECORD" if exists and not shared else None,
                 "DELETE_RECORD" if exists and not shared else None, latest_ids)]
    if action in {"REDACT", "ANONYMIZE"}:
        unsafe = _unsafe(resource, view.identity, resource.category in {"ACCOUNT_TOMBSTONE", "SUPPORT_MESSAGE"},
                         resource.category == "EMAIL_DELIVERY_LOG")
        target = "REDACTED" if action == "REDACT" else "ANONYMIZED"
        required = {"name", "email", "phone", "email_hash"}
        if resource.category == "SUPPORT_MESSAGE":
            required = {"name", "email", "phone"}
        elif action == "ANONYMIZE":
            required = {"email", "phone", "email_hash"}
        coverage = (resource.state == target or required <=
                    {FIELD_ALIASES.get(k) for k in fields} or
                    bool(resource.latest) and all(f.metadata.get("full_record") is True for f in resource.latest))
        invalid_tombstone = resource.category == "ACCOUNT_TOMBSTONE" and any(str(v).upper() != "DELETED" for v in fields["account_status"])
        good = None if resource.issues or invalid_tombstone or not exists else False if unsafe else True if coverage else None
        return [("VERIFY_RECORD_" + target, good, "REPORT_EXPOSED_PII" if unsafe else None,
                 action + "_RECORD" if unsafe else None,
                 {f.source.evidence_id for f in unsafe} if unsafe else latest_ids)]
    if action != "RETAIN":
        return [("VERIFY_RECORD_PRESENT", None, None, None, set())]
    unsafe = [] if resource.category == "BACKUP_ARCHIVE" else _unsafe(resource, view.identity, True)
    if resource.category == "BACKUP_ARCHIVE" and resource.state in {"BLOCKED", "ACTIVE"}:
        archive_fields = [field for field in resource.fields if field.field in {"archive_key", "archive_path"}
                          and isinstance(field.value, str) and bool(field.value)]
        exists = bool(archive_fields) or any(v is True for v in fields["archive_present"])
        latest_ids = {field.fact.source.evidence_id for field in archive_fields} or latest_ids
    condition = True
    if resource.category in {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}:
        key = "retention_until" if resource.category == "SECURITY_AUDIT_LOG" else "retained_until"
        try:
            condition = bool(fields[key]) and all(parse_instant(v) >= parse_instant(view.audit_at) for v in fields[key])
        except (ValueError, TypeError):
            condition = False
        if not fields[key] and resource.category == "LEGAL_HOLD_RECORD":
            condition = bool(fields["legal_hold_active"]) and all(v is True for v in fields["legal_hold_active"])
    good = None if resource.issues or not exists or not condition else not bool(unsafe)
    proof = {f.source.evidence_id for f in unsafe} if unsafe else latest_ids
    requirements = [("VERIFY_RECORD_PRESENT", good, "REPORT_EXPOSED_PII" if unsafe else None,
                     "REDACT_RECORD" if unsafe else None, proof)]
    if unsafe:
        requirements.append(("VERIFY_RECORD_REDACTED", False, "REPORT_EXPOSED_PII", "REDACT_RECORD", proof))
    if resource.category == "BACKUP_ARCHIVE":
        check, compliance, finding, remediation, citations = risk
        return [(check, compliance if good is True else None, finding, remediation, citations)]
    return requirements


def verify_result(result: AuditResult, request, identity_index, state_index, policy_rows,
                  *, coverage_issues=()) -> VerificationReport:
    errors = set()
    try:
        to_csv_row(result)
    except (ValueError, TypeError):
        return VerificationReport(False, ("OUTPUT_CONTRACT_VIOLATION",))
    if result.case_id != request["case_id"]:
        errors.add("CASE_ID_MISMATCH")
    intent = classify_intent(request["request_text"])
    if intent.authorization == "NOT_REQUESTED":
        if result.deletion_status != "NOT_APPLICABLE":
            errors.add("UNAUTHORIZED_DELETION_PLAN")
        return VerificationReport(not errors, tuple(sorted(errors)))
    if intent.authorization != "REQUESTED" or result.deletion_status == "NOT_APPLICABLE":
        return VerificationReport(False, ("AUTHORIZATION_MISMATCH",))
    identity = identity_index.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
    if identity.status != "RESOLVED":
        return VerificationReport(False, ("UNRESOLVED_IDENTITY",))
    view = state_index.resolve(identity, request["audit_at"])
    if "RESOURCE_OWNERSHIP_CONFLICT" in view.issues:
        return VerificationReport(False, ("RESOURCE_OWNERSHIP_CONFLICT",))
    rules = {r["data_category"]: r for r in policy_rows}
    required = defaultdict(list)
    for resource in view.resources:
        for check, good, finding, remediation, citations in _requirements(resource, view, rules.get(resource.category)):
            key = resource.entity.system_id, resource.entity.resource_id
            required["VERIFICATION", *key, check].append((good, citations))
            if finding and remediation:
                required["FINDING", *key, finding].append((False, citations))
                required["REMEDIATION", *key, remediation].append((False, set()))
    complete = result.deletion_status == "COMPLETE"
    if complete and set(identity.issues) & {"AUDIT_CHAIN_GAP", "CURRENT_AUDIT_GAP", "CONFLICTING_IDENTITY_OBSERVATIONS"}:
        errors.add("IDENTITY_HISTORY_UNCERTAIN")
    if not required:
        errors.add("NO_RELEVANT_RESOURCE_PROOF")
    if complete and (coverage_issues or any(good is not True for key, values in required.items()
                                           if key[0] == "VERIFICATION" for good, _ in values)):
        errors.add("UNPROVEN_OR_NONCOMPLIANT_COMPLETION")
    wanted = {key for key in required if not complete or key[0] == "VERIFICATION"}
    actual = {(s.phase, s.system_id, s.resource_id, s.operation): s for s in result.steps}
    if set(actual) != wanted:
        errors.add("MISSING_OR_UNSUPPORTED_STEPS")
    for key, step in actual.items():
        groups = required.get(key, ())
        if step.phase == "FINDING":
            allowed = set().union(*(ids for _, ids in groups)) if groups else set()
            if step.evidence_id not in allowed:
                errors.add("UNSUPPORTED_FINDING_CITATION")
        if complete:
            if not groups or any(step.evidence_id not in ids for _, ids in groups):
                errors.add("INCOMPLETE_PHYSICAL_STORE_PROOF")
    return VerificationReport(not errors, tuple(sorted(errors)))
