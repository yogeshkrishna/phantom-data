"""Independent, advisory checks over source facts and emitted conclusions.

This module never imports the planner, linker, policy engine, or state evaluator.
Extraction is shared; inference is not. Unknown ownership, partial projections and
snapshot coverage are reported for review instead of being converted to proof.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _time(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _rows(path):
    import csv

    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _identifiers(row):
    out = set()
    for key, value in row.items():
        if value is None:
            continue
        value = str(value).strip().lower()
        if key in {"email", "old_value", "new_value"} and "@" in value:
            out.add("email:" + value)
        if key == "user_id" and value:
            out.add("user:" + value)
        if key == "email_hash" and value:
            out.add("hash:" + value)
        if key == "phone" and value:
            out.add("phone:" + re.sub(r"\D", "", value))
    return out


def _fact_verdict(obs, operation):
    """Independent local support check; None explicitly means insufficient fields."""
    present = obs.present
    status = (obs.status or "").upper()
    flags = obs.flags
    if operation in {"REPORT_REMAINING_RECORD", "VERIFY_RECORD_PRESENT"}:
        return present
    if operation == "VERIFY_RECORD_ABSENT":
        return None if present is None else not present
    pii = any(v is not None and str(v).strip().lower() not in {
        "", "null", "none", "[redacted]", "redacted", "-", "—", "no direct identifier"
    } for v in obs.pii.values())
    if operation == "REPORT_EXPOSED_PII":
        return True if pii else None
    if operation in {"VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED"}:
        if present is False:
            return True
        if pii:
            return False
        if operation == "VERIFY_RECORD_ANONYMIZED" and any(obs.refs.values()):
            return False
        return None  # no visible PII is not proof that the projection is complete
    if operation in {"REPORT_ACTIVE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"}:
        active = status == "ACTIVE" or flags.get("enabled") is True or flags.get("scheduler_registered") is True
        disabled = status == "DISABLED" or flags.get("enabled") is False
        if active:
            return operation == "REPORT_ACTIVE_DATA_FLOW"
        if disabled:
            return operation == "VERIFY_DATA_FLOW_DISABLED"
    if operation in {"REPORT_RESTORE_RISK", "VERIFY_RESTORE_BLOCKED"}:
        active = status == "ACTIVE" or flags.get("restore_allowed") is True or str(flags.get("decision", "")).lower() == "allow"
        blocked = status == "BLOCKED" or flags.get("restore_allowed") is False or flags.get("deletion_block") is True or str(flags.get("decision", "")).lower() == "deny"
        if active:
            return operation == "REPORT_RESTORE_RISK"
        if blocked:
            return operation == "VERIFY_RESTORE_BLOCKED"
    return None


def _latest_copies(facts):
    groups = defaultdict(list)
    for fact in facts:
        groups[(fact.system, getattr(fact, "store", ""), getattr(fact, "replica", False))].append(fact)
    return [fact for group in groups.values() for fact in group if fact.obs_time == max(f.obs_time for f in group)]


def _policy_operations(fact, rules, cutoff):
    category = fact.record_type
    if category == "DATA_FLOW":
        return {"REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"}
    rule = rules.get(category)
    if rule is None:
        return None
    if category == "BACKUP_ARCHIVE":
        return {"REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", "VERIFY_RECORD_PRESENT"}
    action = rule["required_action"]
    if category in {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}:
        until = fact.flags.get("retention_until" if category == "SECURITY_AUDIT_LOG" else "retained_until")
        if not until:
            return None
        try:
            if _time(until) < cutoff:
                action = "DELETE"
        except ValueError:
            return None
    return {
        "DELETE": {"REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"},
        "REDACT": {"REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ABSENT"},
        "ANONYMIZE": {"REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", "VERIFY_RECORD_ABSENT"},
        "RETAIN": {"VERIFY_RECORD_PRESENT"},
    }.get(action)


def check_predictions(data_dir: Path, rows, requests_file="deletion_requests.csv", *, evidence=None):
    """Return errors/review notes without silently changing or approving predictions."""
    data_dir = Path(data_dir)
    requests = {r["case_id"]: r for r in _rows(data_dir / requests_file)}
    manifest = _rows(data_dir / "evidence_files.csv")
    if evidence is None:
        from .evidence.registry import EvidenceIndex

        evidence = EvidenceIndex(data_dir, manifest)
    activity = _rows(data_dir / "system_activity.csv")
    audits = _rows(data_dir / "user_audit_log.csv")
    identities = _rows(data_dir / "user_identities.csv")
    rules = {r["data_category"]: r for r in _rows(data_dir / "deletion_rules.csv")}
    issues = []

    def issue(cid, step, code, detail, level="REVIEW"):
        issues.append({"case_id": cid, "step_no": step.get("step_no"), "resource_id": step.get("resource_id"),
                       "code": code, "level": level, "detail": detail})

    by_resource = defaultdict(list)
    for fact in evidence.observations:
        by_resource[fact.resource_id].append(fact)
    known = set(by_resource)
    systems = defaultdict(set)
    for fact in evidence.observations:
        systems[fact.resource_id].add(fact.system)
    source_rows = {r["activity_id"]: r for r in activity}
    source_rows.update({r["audit_id"]: r for r in audits})
    for item in activity:
        rid = item["resource_id"]
        known.add(rid)
    # Document mentions are discovery evidence, not automatic existence or ownership proof.
    doc_text = {doc.file_id: doc.text for doc in evidence.docs}
    for doc in evidence.docs:
        known.update(doc.resources)
    captures = {r["file_id"]: _time(r["captured_at"]) for r in manifest}
    scopes = defaultdict(list)
    for scope in evidence.scopes:
        scopes[scope.file_id].append(scope)
    user_keys = {r["user_id"]: _identifiers(r) for r in identities}
    for row in audits:
        user_keys.setdefault(row["user_id"], set()).update(_identifiers(row))
    for cid, status, steps in rows:
        request = requests.get(cid)
        if request is None:
            issue(cid, {}, "unknown_case", "case is absent from request input", "ERROR")
            continue
        cutoff = _time(request["audit_at"])
        owners = [u for u, keys in user_keys.items() if "email:" + request["email"].lower() in keys]
        subject = owners[0] if len(owners) == 1 else None
        if status != "NOT_APPLICABLE" and subject is None:
            issue(cid, {}, "ambiguous_subject", "request identifier has zero or multiple indexed candidate owners")
        if status == "COMPLETE" and not steps:
            issue(cid, {}, "empty_completion", "no checks establish completion", "ERROR")
        for step in steps:
            rid, system, ev, op = (step[k] for k in ("resource_id", "system_id", "evidence_id", "operation"))
            if rid not in known:
                issue(cid, step, "unknown_resource", "no source observation/activity/document names this resource", "ERROR")
            if systems[rid] and system not in systems[rid]:
                issue(cid, step, "system_conflict", "source systems disagree with emitted system", "ERROR")
            eligible = [f for f in by_resource[rid] if f.obs_time <= cutoff and f.system == system
                        and captures.get(f.file_id, cutoff) <= cutoff]
            policy_sets = [_policy_operations(f, rules, cutoff) for f in _latest_copies(eligible)]
            known_policy = [allowed for allowed in policy_sets if allowed is not None]
            if known_policy and all(op not in allowed for allowed in known_policy) and all(p is not None for p in policy_sets):
                late_context = [f for f in by_resource[rid] if f.obs_time <= cutoff and f.system == system
                                and captures.get(f.file_id, cutoff) > cutoff]
                alternative = any(op in (_policy_operations(f, rules, cutoff) or set()) for f in late_context)
                if alternative:
                    issue(cid, step, "policy_temporal_assumption", "policy depends on pre-cutoff statement in later-captured context")
                else:
                    issue(cid, step, "policy_conflict", "eligible category/retention facts forbid this operation", "ERROR")
            elif not known_policy:
                issue(cid, step, "policy_review", "category or conditional retention lacks independent policy proof")
            if not ev:
                continue
            if ev in captures and captures[ev] > cutoff:
                issue(cid, step, "late_citation", "manifest capture is after the audit cutoff", "ERROR")
                continue
            if ev in source_rows:
                source = source_rows[ev]
                if _time(source["event_time"]) > cutoff:
                    issue(cid, step, "late_citation", "cited event is after audit cutoff", "ERROR")
                if source.get("resource_id") != rid:
                    issue(cid, step, "citation_resource", "cited event does not identify the resource", "ERROR")
                else:
                    issue(cid, step, "activity_claim", "operation result alone does not prove final record state")
                continue
            facts = [f for f in by_resource[rid] if f.file_id == ev and f.obs_time <= cutoff and f.system == system]
            if not facts:
                text = doc_text.get(ev, "")
                mentioned = bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(rid) + r"(?![A-Za-z0-9_])", text))
                display_mapping = False
                for catalog in activity:
                    if catalog["event_type"] != "LINK_RESOURCE_IDENTITY" or catalog["resource_id"] != rid or _time(catalog["event_time"]) > cutoff:
                        continue
                    label = re.search(r"(?:linked|confirmed)\s+" + re.escape(rid) + r"\s+\(([^)]+)\)", catalog["details"], re.I)
                    if label and label.group(1).lower() in text.lower():
                        display_mapping = True
                matching_scopes = [s for s in scopes[ev] if s.system == system and s.obs_time <= cutoff]
                if mentioned or display_mapping:
                    issue(cid, step, "document_review", "document names resource; row-local state/ownership needs inspection")
                elif matching_scopes and op == "VERIFY_RECORD_ABSENT":
                    issue(cid, step, "scope_coverage", "absence by omission needs independent store/selector completeness proof")
                else:
                    issue(cid, step, "citation_resource", "cited source has no eligible matching resource observation", "ERROR")
                continue
            facts = _latest_copies(facts)
            answers = [_fact_verdict(f, op) for f in facts]
            if (op.startswith("VERIFY_") and any(v is False for v in answers)) or all(v is False for v in answers):
                issue(cid, step, "contradicted_state", "cited contents contradict the emitted assertion", "ERROR")
            elif not any(v is True for v in answers):
                issue(cid, step, "insufficient_fields", "citation does not independently prove required treatment")
            if op.startswith("VERIFY_") and facts:
                last_cited = max(f.obs_time for f in facts)
                if any(f.obs_time > last_cited and _fact_verdict(f, op) is False for f in eligible):
                    issue(cid, step, "stale_citation", "later eligible evidence contradicts the cited state", "ERROR")
            fact_keys = set().union(*(f.link_keys for f in facts))
            fact_owners = {u for u, keys in user_keys.items() if fact_keys & keys}
            if subject and fact_owners and subject not in fact_owners:
                issue(cid, step, "ownership_conflict", "cited identifiers belong only to a different indexed subject", "ERROR")
            elif not fact_owners:
                issue(cid, step, "ownership_review", "source has no independent direct subject identifier")
            elif len(fact_owners) > 1:
                issue(cid, step, "shared_ownership", "source identifiers match multiple candidate owners")
            for fact in facts:
                rule = rules.get(fact.record_type)
                if not rule:
                    issue(cid, step, "policy_unknown", "no exact category rule independently established")
                elif rule["required_action"] == "RETAIN" and op == "REPORT_REMAINING_RECORD":
                    issue(cid, step, "retention_review", "retained category needs expiry/hold and preservation review")
    counts = Counter(i["level"] for i in issues)
    return {"mode": "advisory", "independence": "shared extraction; separate source/ownership/claim checks; no planner decisions reused",
            "errors": counts["ERROR"], "reviews": counts["REVIEW"], "issues": issues,
            "limitations": ["Partial snapshots, missing fields, document rows and weak ownership require review.",
                            "Historic identity aggregation is not a proof of temporal ownership.",
                            "Passing this checker does not prove complete discovery or hidden canonical accuracy."]}
