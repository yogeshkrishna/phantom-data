"""Typed log events and file-local, explicitly scoped request/readback traces."""

import hashlib
import json
import re
import shlex
from dataclasses import replace

from .contracts import EntityKey, EvidenceFact, EvidenceRef, parse_instant
from .json_evidence import EvidenceBatch, EvidenceDiagnostic, READBACK_STATES, RECORD_CATEGORIES

NOISE = {
    "export_started", "export_completed", "cache_lookup", "connection_acquired", "dependency_retry",
    "dependency_recovered", "consumer_lag", "cache_refresh", "lease_extended", "delivery_abandoned",
    "flag_snapshot_loaded", "slow_call_rate", "certificate_checked", "disk_latency", "timer_tick",
    "connection_pool_pressure", "pool_sample", "properties_reloaded", "batch_started", "checkpoint_saved",
    "thumbnail_failed", "deletion_marker_check", "lease_renewed", "telemetry_flushed",
}
EVENT_CATEGORIES = {
    "profile_lookup_completed": "USER_PROFILE", "index_query_completed": "SEARCH_INDEX_ENTRY",
    "message_pii_scan": "SUPPORT_MESSAGE", "message_read": "SUPPORT_MESSAGE",
    "support_ticket_read_completed": "SUPPORT_MESSAGE", "attachment_pii_scan": "SUPPORT_ATTACHMENT",
    "analytics_identity_scan": "ANALYTICS_PROFILE", "analytics_profile_read": "ANALYTICS_PROFILE",
    "analytics_profile_read_completed": "ANALYTICS_PROFILE", "contact_lookup_completed": "SECONDARY_IDENTIFIER",
    "contact_query_completed": "SECONDARY_IDENTIFIER", "audit_record_read_completed": "SECURITY_AUDIT_LOG",
    "export_download_check_completed": "USER_EXPORT", "export_metadata_read_completed": "USER_EXPORT",
    "connector_heartbeat": "DATA_FLOW", "connector_config_loaded": "DATA_FLOW",
    "connector_config_read_completed": "DATA_FLOW", "restore_candidate_evaluated": "BACKUP_ARCHIVE",
    "restore_authorization_completed": "BACKUP_ARCHIVE", "restore_policy_read_completed": "BACKUP_ARCHIVE",
    "retained_order_loaded": "LEGAL_HOLD_RECORD",
}
TRACE_COMPLETIONS = {
    "profile_lookup_started": {"profile_lookup_completed"}, "index_query_started": {"index_query_completed"},
    "message_scan_started": {"message_pii_scan", "message_read"},
    "attachment_scan_started": {"attachment_pii_scan"},
    "analytics_profile_scan_started": {"analytics_identity_scan", "analytics_profile_read"},
    "analytics_profile_read_started": {"analytics_profile_read_completed"},
    "audit_record_query_started": {"audit_record_read_completed"},
    "audit_record_read_started": {"audit_record_read_completed"},
    "connector_config_read_started": {"connector_heartbeat", "connector_config_loaded", "connector_config_read_completed"},
    "restore_policy_check_started": {"restore_candidate_evaluated", "restore_authorization_completed"},
    "restore_policy_read_started": {"restore_policy_read_completed"},
    "export_download_check_started": {"export_download_check_completed"},
    "export_metadata_read_started": {"export_metadata_read_completed"},
    "support_ticket_read_started": {"support_ticket_read_completed"},
    "contact_query_started": {"contact_query_completed"},
    "retained_order_query_started": {"retained_order_loaded"},
}
CLAIM_EVENTS = {
    "delete_record_completed": "DELETE_RECORD", "sync_record_completed": "SYNC_RECORD",
    "restore_completed": "RESTORE_BACKUP", "redact_record_completed": "REDACT_RECORD",
    "anonymize_record_completed": "ANONYMIZE_RECORD",
}
QUERY_KEYS = {"lookup_key", "match_key", "subject_key", "scan_key", "subject_ref", "owner_key", "correlation_key"}
CONTROL = {"severity", "logger", "level", "service", "instance", "pod", "region", "trace_id", "event",
           "resource_id", "system", "record_type", "result", "seq", "attempt", "rows", "http_status",
           "duration_ms", "latency_ms", "took_ms", "shard", "index", "total_hits", "detector", "direct_identifiers"}
SERVICE_ALIASES = {"profile_service": "account_service", "support_replica": "support_platform"}


def _pairs(tokens):
    result = {}
    for token in shlex.split(tokens, posix=True):
        if "=" in token:
            key, value = token.split("=", 1)
            if key in result:
                raise ValueError("duplicate log key")
            result[key] = value
    return result


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON log key")
        result[key] = value
    return result


def parse_log_line(line):
    """Return (authored ISO timestamp, event dict); reject ambiguous/naive time."""
    timestamp, body = line.split(" ", 1)
    parse_instant(timestamp)
    if body.startswith("{"):
        data = json.loads(body, object_pairs_hook=_json_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite log JSON")))
        if not isinstance(data, dict):
            raise ValueError("log event must be an object")
    elif body.startswith("|"):
        parts = body.split("|", 5)
        if len(parts) != 6:
            raise ValueError("invalid pipe log")
        data = _pairs(parts[5])
        data.update(service=parts[2].strip(), trace_id=parts[3].strip(), event=parts[4].strip())
    elif body.startswith("["):
        match = re.fullmatch(r"\[\w+\] \[([^]]+)\] \[([^]]+)\] (\w+):\s*(.*)", body, re.S)
        if not match:
            raise ValueError("invalid application log")
        data = _pairs(match[4])
        data.update(service=match[1], trace_id=match[2], event=match[3])
    elif body.startswith("<"):
        match = re.fullmatch(r"<\d+>1 \S+ (\S+) (\S+) - - (.*)", body, re.S)
        if not match:
            raise ValueError("invalid syslog")
        data = _pairs(match[3])
        data.update(service=match[1], trace_id=match[2])
    else:
        data = _pairs(body)
        if "message" in data and "function" in data:
            nested = _pairs(data["message"])
            nested.update(service=data["function"], trace_id=data.get("requestId", ""))
            data = nested
    if not isinstance(data.get("event"), str) or not isinstance(data.get("service"), str):
        raise ValueError("missing log event/service")
    return timestamp, data


def selector(value):
    if not isinstance(value, str) or not value or value == "null":
        return {}
    field = "email" if "@" in value else "email_hash" if re.fullmatch(r"[a-fA-F0-9]{64}", value) else (
        "phone" if re.fullmatch(r"\+?[\d ()-]{7,}", value) else "user_id")
    return {field: (value,)}


def _typed(key, value):
    if value == "null":
        return None
    if value in ("true", "false") and key in {
            "enabled", "restore_allowed", "archive_present", "available", "eligible", "deletion_block", "scheduler_registered"}:
        return value == "true"
    return value


def event_fact(timestamp, data, source, queries=None):
    event = data["event"]
    if event in CLAIM_EVENTS:
        system = data["service"]
        resource = data.get("resource_id")
        if not resource:
            raise ValueError("operation claim lacks resource")
        identifiers = dict(queries or {})
        for key in QUERY_KEYS:
            identifiers.update(selector(data.get(key)))
        return EvidenceFact(EntityKey(system, data.get("store_id", system), resource),
                            data.get("record_type", "UNKNOWN"), timestamp, "claim", data.get("result", "UNKNOWN"),
                            {}, identifiers, (), (), {"event_type": CLAIM_EVENTS[event],
                            "reported_result": data.get("result", "UNKNOWN")}, source)
    category = data.get("record_type") if event == "record_lookup" else EVENT_CATEGORIES.get(event)
    if category not in RECORD_CATEGORIES:
        raise ValueError("unsupported readback category")
    raw_system = data.get("system", data["service"])
    system = SERVICE_ALIASES.get(raw_system, raw_system)
    resource = data.get("resource_id")
    if not isinstance(resource, str) or not resource:
        raise ValueError("readback lacks resource")
    store = data.get("store_id", "support_replica" if raw_system == "support_replica" else system)
    identifiers = dict(queries or {})
    for key in QUERY_KEYS:
        identifiers.update(selector(data.get(key)))
    if event == "record_lookup":
        identifiers.update(selector(data.get("subject_reference")))
    fields = {key: _typed(key, value) for key, value in data.items()
              if key not in CONTROL | QUERY_KEYS and not (event == "record_lookup" and key == "subject_reference")}
    state, metadata = "PRESENT", {"event": event, "readback": True}
    if event == "record_lookup":
        state = READBACK_STATES[data["result"]]
        metadata["readback_result"] = data["result"]
    if event == "profile_lookup_completed":
        if str(data.get("rows")) not in {"0", "1"} or str(data.get("http_status")) not in {"200", "404"}:
            raise ValueError("unsupported profile response")
        if (str(data["rows"]) == "0") != (str(data["http_status"]) == "404"):
            raise ValueError("contradictory profile response")
        state = "ABSENT" if str(data["rows"]) == "0" else "PRESENT"
    elif event == "index_query_completed":
        if data.get("result") not in {"hit", "miss"}:
            raise ValueError("unknown index result")
        state = "ABSENT" if data["result"] == "miss" else "PRESENT"
    elif event.endswith("pii_scan") or event == "analytics_identity_scan":
        metadata["pii_detected"] = True
        value = data.get("matched_value")
        if value:
            field = next(iter(selector(value)), "detected_identifier")
            fields[field] = value
    elif event in {"contact_lookup_completed", "contact_query_completed", "audit_record_read_completed"}:
        if str(data.get("rows")) not in {"0", "1"}:
            raise ValueError("unsupported row count")
        state = "ABSENT" if str(data["rows"]) == "0" else "PRESENT"
    elif event == "restore_candidate_evaluated":
        if data.get("eligible") not in {"true", "false", True, False}:
            raise ValueError("unknown restore eligibility")
        fields["restore_allowed"] = data["eligible"] in {"true", True}
    elif event == "restore_authorization_completed":
        if data.get("decision") not in {"allow", "deny"}:
            raise ValueError("unknown restore authorization")
        state = "BLOCKED" if data["decision"] == "deny" else "ACTIVE"
        fields["restore_allowed"] = data["decision"] == "allow"
    elif event in {"export_download_check_completed", "export_metadata_read_completed"}:
        if data.get("available") not in {"true", "false", True, False}:
            raise ValueError("unknown export availability")
        # Download unavailability does not establish that the stored object is absent.
        metadata["download_available"] = data["available"] in {"true", True}
    return EvidenceFact(EntityKey(system, store, resource), category, timestamp, "state", state,
                        fields, identifiers, tuple(sorted(fields)), (), metadata, source)


def normalize_log(text: str, source: EvidenceRef) -> EvidenceBatch:
    parse_instant(source.available_at)
    facts, diagnostics, parsed, pending = [], [], [], {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        start = index + 1
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        # Pretty-printed JSON is bounded by the next timestamp, with physical range provenance.
        if re.match(r"\S+ \{", line):
            while index < len(lines) and not re.match(r"\d{4}-\d\d-\d\dT", lines[index]):
                line += "\n" + lines[index]
                index += 1
        ref = replace(source, locator=f"/lines/{start}" + (f"-{index}" if index != start else ""))
        try:
            timestamp, data = parse_log_line(line)
            parsed.append((timestamp, data, ref))
        except (ValueError, TypeError, KeyError):
            diagnostics.append(EvidenceDiagnostic("INVALID_LOG_EVENT", ref, "Unsupported format or timestamp"))
    normalized = 0
    for timestamp, data, ref in parsed:
        event = data["event"]
        trace = data.get("trace_id")
        key = tuple(data.get(k, "") for k in ("service", "instance", "pod", "region", "trace_id"))
        if event.endswith("_started") and event not in NOISE:
            if event not in TRACE_COMPLETIONS:
                diagnostics.append(EvidenceDiagnostic("UNKNOWN_LOG_START", ref, "No reviewed trace pairing"))
                continue
            selectors = {}
            for field in QUERY_KEYS:
                selectors.update(selector(data.get(field)))
            if trace:
                if key in pending:
                    pending[key] = None
                    diagnostics.append(EvidenceDiagnostic("AMBIGUOUS_LOG_TRACE", ref, "Repeated start before trace completion"))
                else:
                    pending[key] = (timestamp, selectors, data.get("resource_id"), ref, event)
            normalized += 1
            continue
        if event in NOISE:
            if event == "export_completed" and str(data.get("dropped", "0")) != "0":
                diagnostics.append(EvidenceDiagnostic("INCOMPLETE_LOG_EXPORT", ref, "Readback export reports dropped records"))
            normalized += 1
            continue
        if event not in EVENT_CATEGORIES and event not in CLAIM_EVENTS and event != "record_lookup":
            diagnostics.append(EvidenceDiagnostic("UNKNOWN_LOG_EVENT", ref, "Event has no reviewed semantics"))
            continue
        try:
            queries = {}
            start = pending.pop(key, None) if trace else None
            if start and parse_instant(start[0]) <= parse_instant(timestamp) and (
                    not start[2] or start[2] == data.get("resource_id")) and event in TRACE_COMPLETIONS[start[4]]:
                queries = start[1]
                ref = replace(ref, locator=start[3].locator + ".." + ref.locator)
            fact = event_fact(timestamp, data, ref, queries)
            facts.append(fact)
            normalized += 1
        except (ValueError, TypeError, KeyError):
            diagnostics.append(EvidenceDiagnostic("INVALID_LOG_READBACK", ref, "Incomplete or inconsistent readback"))
    return EvidenceBatch("log-events", tuple(facts), (), tuple(diagnostics), len(parsed) +
                         sum(d.code == "INVALID_LOG_EVENT" for d in diagnostics), normalized)


def read_log_evidence(entry):
    raw = entry.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError("log changed after manifest hashing")
    source = EvidenceRef(entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at)
    return normalize_log(raw.decode("utf-8-sig"), source)
