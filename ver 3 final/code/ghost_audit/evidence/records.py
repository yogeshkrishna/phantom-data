"""Turn heterogeneous evidence records into normalized observation attributes."""

from __future__ import annotations

from ..candidates import enabled
from .model import (
    LOOKUP_FIELDS, PII_FIELDS, REFERENCE_FIELDS, Observation, is_blank, keys_from_value, system_for_resource,
)

TRUE = {"true", "1", "yes", "y", "allow", "allowed"}
FALSE = {"false", "0", "no", "n", "deny", "denied"}

# Extra lookup-style fields that only identify the subject of a query.
EXTRA_LOOKUP_FIELDS = {"lookup_value", "owner_key", "subject_selector", "uploaded_by"}

PRESENT_STATUSES = {"FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "BLOCKED", "DISABLED", "HIT",
                    "PRESENT", "RETAINED"}
ABSENT_STATUSES = {"NOT_FOUND", "MISS", "ABSENT", "DELETED_RECORD"}

COLLECTION_TYPES = {
    "customer_profiles": "USER_PROFILE",
    "customer_profiles_primary": "USER_PROFILE",
    "customer_profiles_failover_replica": "USER_PROFILE",
    "profile_lookups": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "search_documents_primary": "SEARCH_INDEX_ENTRY",
    "index_queries": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "profiles": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "tickets": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "customer_exports": "USER_EXPORT",
    "exports": "USER_EXPORT",
    "connector_configurations": "DATA_FLOW",
    "connector_subscriptions": "DATA_FLOW",
    "connectors": "DATA_FLOW",
    "backup_catalog": "BACKUP_ARCHIVE",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "restore_entries": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "audit_records": "SECURITY_AUDIT_LOG",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}

SYSTEM_TYPES = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "export_service": "USER_EXPORT",
    "integration_service": "DATA_FLOW",
    "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "marketing_platform": "MARKETING_PROFILE",
    "object_storage": "SUPPORT_ATTACHMENT",
}


def as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    return None


def flatten(record: dict) -> dict:
    out: dict = {}
    for key, value in record.items():
        if isinstance(value, dict) and key in ("document", "profile"):
            for k, v in value.items():
                out.setdefault(k, v)
        else:
            out[key] = value
    return out


def build_observation(record: dict, *, file_id, captured_at, obs_time, system=None, store="", replica=False,
                      record_type=None, kind="", position=-1, collection="", extra_keys=None) -> Observation | None:
    """Interpret one structured record (row, JSON object or parsed log line)."""
    fields = flatten(record)
    resource_id = str(fields.get("resource_id") or "").strip()
    if not resource_id:
        return None
    system = system_for_resource(resource_id) or fields.get("system") or system
    if not system:
        return None

    pii: dict = {}
    refs: dict = {}
    link_keys: set = set(extra_keys or ())
    for key, value in fields.items():
        if key in PII_FIELDS or (enabled("reference_fields") and key == "message_excerpt"):
            if key in ("message_text", "message_excerpt"):
                found = keys_from_value(value)
                if found:
                    pii[key] = value
                    link_keys |= found
                continue
            if not is_blank(value):
                pii[key] = value
                link_keys |= keys_from_value(value, key)
        elif key in REFERENCE_FIELDS:
            if not is_blank(value):
                refs[key] = value
                link_keys |= keys_from_value(value, key)
        elif key in LOOKUP_FIELDS or key in EXTRA_LOOKUP_FIELDS:
            link_keys |= keys_from_value(value, key)
    if fields.get("uploaded_by") and not is_blank(fields.get("uploaded_by")):
        pii["uploaded_by"] = fields["uploaded_by"]

    flags: dict = {}
    if enabled("projection_completeness"):
        # Keep source coverage separate from nonblank PII: blank returned values
        # prove something different from fields omitted by a projection.
        flags["observed_fields"] = sorted(fields)
        for key in ("projection_complete", "all_fields_returned", "content_complete"):
            if key in fields:
                flags[key] = as_bool(fields[key])
        projected = fields.get("projected_fields") or fields.get("selected_fields")
        if isinstance(projected, list):
            flags["projected_fields"] = [str(value) for value in projected]
    for key in ("enabled", "restore_allowed", "download_available", "available", "marker_found", "eligible",
                "deletion_block", "archive_present", "scheduler_registered", "email_opt_in"):
        if key in fields:
            b = as_bool(fields[key])
            if b is not None:
                flags[key] = b
    for key in ("retention_until", "retained_until", "account_status", "decision", "last_delivery_status",
                "last_run", "mode", "access", "direct_identifiers", "file", "file_name", "event_code",
                "event_name", "connector_name", "updated_at", "storage_class"):
        if key in fields and not is_blank(fields[key]):
            flags[key] = str(fields[key])

    status = None
    raw_status = fields.get("result")
    if raw_status is not None and not is_blank(raw_status):
        status = str(raw_status).strip().upper()
    present: bool | None = True
    if status in ABSENT_STATUSES:
        present = False
    elif status in PRESENT_STATUSES:
        present = True
    for key in ("rows", "total_hits"):
        if key in fields and str(fields[key]).strip().isdigit():
            present = int(fields[key]) > 0
    for key in ("http_status", "status_code"):
        if key in fields and str(fields[key]).strip() == "404":
            present = False
    if "profile" in record and record.get("profile") is None and str(fields.get("status_code")) == "404":
        present = False
    if str(flags.get("direct_identifiers", "")) == "1" and not pii:
        pii["direct_identifiers"] = "1"

    rtype = record_type or fields.get("record_type")
    if not rtype:
        rtype = COLLECTION_TYPES.get(store) or SYSTEM_TYPES.get(system)
    if fields.get("storage_class") == "ticket_attachment":
        rtype = "SUPPORT_ATTACHMENT"

    return Observation(
        file_id=file_id, captured_at=captured_at, obs_time=obs_time, system=system, resource_id=resource_id,
        present=present, store=store, replica=replica, pii=pii, refs=refs, link_keys=link_keys,
        record_type=rtype, status=status, flags=flags, kind=kind, position=position, collection=collection,
    )
