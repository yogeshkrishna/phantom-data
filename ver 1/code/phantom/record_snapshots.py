"""record_snapshots.py – evidence adapter for regular JSON record snapshots."""

import copy
import dataclasses

from .contracts import EvidenceFact, EvidenceRef, EntityKey, parse_instant

COLLECTION_CATEGORIES: dict[tuple[str, str], str] = {
    ("account_service", "customer_profiles"): "USER_PROFILE",
    ("analytics_platform", "customer_analytics_reports"): "ANALYTICS_PROFILE",
    ("backup_service", "backup_catalog"): "BACKUP_ARCHIVE",
    ("export_service", "customer_export_jobs"): "USER_EXPORT",
    ("identity_service", "deleted_customer_references"): "ACCOUNT_TOMBSTONE",
    ("integration_service", "connector_configurations"): "DATA_FLOW",
    ("legal_service", "retained_order_records"): "LEGAL_HOLD_RECORD",
    ("search_service", "customer_search_documents"): "SEARCH_INDEX_ENTRY",
    ("security_service", "security_audit_events"): "SECURITY_AUDIT_LOG",
    ("support_platform", "support_conversations"): "SUPPORT_MESSAGE",
    ("support_platform", "ticket_attachments"): "SUPPORT_ATTACHMENT",
}

_OPERATIONAL_HISTORY = "operational_record_history"


def extract_record_snapshot(
    snapshot: dict[str, object],
    source: EvidenceRef,
) -> tuple[EvidenceFact, ...]:
    """Convert a decoded regular JSON snapshot dict into EvidenceFact objects.

    Each record produces one PRESENT EvidenceFact. Operational history
    snapshots are explicitly rejected. No file I/O or model calls are made.
    Raises ValueError for invalid inputs without exposing personal-data values.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a dict")
    if not isinstance(source, EvidenceRef):
        raise ValueError("source must be an EvidenceRef")
    if not isinstance(source.locator, str) or not (
        source.locator == "" or source.locator.startswith("/")
    ):
        raise ValueError("source.locator must be '' or start with '/'")

    for field in ("captured_at", "data_store", "collection"):
        val = snapshot.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"snapshot field {field!r} must be a nonblank string")

    captured_at: str = snapshot["captured_at"]
    data_store: str = snapshot["data_store"]
    collection: str = snapshot["collection"]

    try:
        parse_instant(captured_at)
        parse_instant(source.available_at)
    except ValueError:
        raise ValueError("invalid observation or availability timestamp") from None

    if collection == _OPERATIONAL_HISTORY:
        raise ValueError(
            "operational_record_history requires a dedicated adapter; "
            "cannot be processed as a regular record snapshot"
        )
    if (data_store, collection) not in COLLECTION_CATEGORIES:
        raise ValueError("unsupported (data_store, collection) schema")

    category = COLLECTION_CATEGORIES[(data_store, collection)]

    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ValueError("snapshot['records'] must be a list")

    seen_ids: set[str] = set()
    facts: list[EvidenceFact] = []

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"record at index {idx} must be a dict")
        if not all(isinstance(k, str) for k in record):
            raise ValueError(f"record at index {idx} must have string keys")
        rid = record.get("resource_id")
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError(f"record at index {idx} missing nonblank string resource_id")
        if rid in seen_ids:
            raise ValueError("duplicate resource_id within snapshot")
        seen_ids.add(rid)

        record_source = dataclasses.replace(
            source, locator=f"{source.locator}/records/{idx}"
        )
        facts.append(EvidenceFact(
            entity=EntityKey(system_id=data_store, store_id=data_store, resource_id=rid),
            category=category,
            observed_at=captured_at,
            kind="state",
            state="PRESENT",
            stored_fields=copy.deepcopy(record),
            query_identifiers={},
            field_coverage=tuple(sorted(record.keys())),
            references=(),
            metadata={"collection": collection},
            source=record_source,
        ))

    return tuple(facts)
