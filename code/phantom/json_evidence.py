"""Normalize supported JSON evidence; never infer global absence from inventory."""

import copy
from dataclasses import dataclass, replace

from .contracts import EntityKey, EvidenceFact, EvidenceRef, SnapshotScope, parse_instant
from .corpus import ManifestEntry
from .json_reader import load_json_entry
from .record_snapshots import COLLECTION_CATEGORIES, extract_record_snapshot


@dataclass(frozen=True, slots=True)
class EvidenceDiagnostic:
    code: str
    source: EvidenceRef
    detail: str


@dataclass(frozen=True, slots=True)
class EvidenceBatch:
    layout: str
    facts: tuple[EvidenceFact, ...]
    scopes: tuple[SnapshotScope, ...]
    diagnostics: tuple[EvidenceDiagnostic, ...]
    records_seen: int
    records_normalized: int


SERVICE_COLLECTIONS = {
    ("account_service", "profile_lookups"): ("customer_profiles", "USER_PROFILE"),
    ("analytics_platform", "profiles"): ("customer_analytics_reports", "ANALYTICS_PROFILE"),
    ("backup_service", "restore_entries"): ("backup_catalog", "BACKUP_ARCHIVE"),
    ("export_service", "exports"): ("customer_export_jobs", "USER_EXPORT"),
    ("integration_service", "connectors"): ("connector_configurations", "DATA_FLOW"),
    ("search_service", "index_queries"): ("customer_search_documents", "SEARCH_INDEX_ENTRY"),
    ("security_service", "audit_records"): ("security_audit_events", "SECURITY_AUDIT_LOG"),
    ("support_platform", "tickets"): ("support_conversations", "SUPPORT_MESSAGE"),
}
STANDALONE_SCHEMAS = {
    ("analytics_identity_store", "customer_analytics_reports"): "analytics_platform",
    ("user_export_store", "customer_export_jobs"): "export_service",
}
READBACK_STATES = {
    "FOUND": "PRESENT", "NOT_FOUND": "ABSENT", "IDENTIFIABLE": "PRESENT",
    "ANONYMIZED": "ANONYMIZED", "REDACTED": "REDACTED",
    "ACTIVE": "ACTIVE", "DISABLED": "DISABLED", "BLOCKED": "BLOCKED",
}
RECORD_CATEGORIES = frozenset(COLLECTION_CATEGORIES.values()) | {
    "SECONDARY_IDENTIFIER", "CACHE_ENTRY", "PRODUCT_TELEMETRY",
    "MARKETING_PROFILE", "EMAIL_DELIVERY_LOG",
}


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required nonblank string")
    return value


def _pointer(*parts: object) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


class _Builder:
    def __init__(self, source: EvidenceRef):
        self.source = source
        self.facts = []
        self.scopes = []
        self.diagnostics = []
        self.seen = self.normalized = 0

    def error(self, pointer: str, code="INVALID_RECORD"):
        self.diagnostics.append(EvidenceDiagnostic(code, replace(self.source, locator=self.source.locator + pointer),
                                                  "Unsupported or inconsistent source structure/semantics"))

    def fact(self, system, resource, category, observed, pointer, fields=None,
             state="PRESENT", queries=None, metadata=None, store=None, kind="state"):
        _text(system)
        _text(resource)
        _text(category)
        parse_instant(observed)
        values = copy.deepcopy(fields) if fields is not None else {}
        fact = EvidenceFact(EntityKey(system, store or system, resource), category, observed,
                            kind, state, values, copy.deepcopy(queries or {}),
                            tuple(sorted(values)), (), copy.deepcopy(metadata or {}),
                            replace(self.source, locator=self.source.locator + pointer))
        self.facts.append(fact)

    def rows(self, values, pointer, handler):
        if not isinstance(values, list):
            self.error(pointer, "INVALID_COLLECTION")
            return
        for index, value in enumerate(values):
            self.seen += 1
            location = pointer + f"/{index}"
            before = (len(self.facts), len(self.scopes))
            try:
                if not isinstance(value, dict):
                    raise ValueError("record must be an object")
                handler(value, location)
                self.normalized += 1
            except (ValueError, TypeError, KeyError):
                # A malformed node cannot leave a half-emitted observation behind.
                del self.facts[before[0]:]
                del self.scopes[before[1]:]
                self.error(location)

    def regular(self, snapshot, pointer, store=None):
        records = snapshot.get("records")
        count = len(records) if isinstance(records, list) else 0
        self.seen += count
        try:
            facts = extract_record_snapshot(snapshot, replace(self.source, locator=self.source.locator + pointer))
            if store:
                facts = tuple(replace(fact, entity=replace(fact.entity, store_id=store)) for fact in facts)
            self.facts.extend(facts)
            self.normalized += len(facts)
        except (ValueError, TypeError, KeyError):
            self.error(pointer, "INVALID_SNAPSHOT")

    def history(self, row, pointer):
        system, category = _text(row.get("system")), _text(row.get("record_type"))
        if category not in RECORD_CATEGORIES:
            raise ValueError("unsupported category")
        result = row.get("result")
        queries = {key: (row[key],) for key in ("lookup_value", "subject_reference")
                   if isinstance(row.get(key), str) and row[key]}
        metadata = {"collection": "operational_record_history", "readback_result": result}
        if row.get("operation") or row.get("event_type"):
            metadata.update(operation=row.get("operation"), event_type=row.get("event_type"))
            kind, state = "claim", _text(result)
        else:
            kind, state = "state", READBACK_STATES[result]
            if state == "DISABLED" and category != "DATA_FLOW":
                raise ValueError("flow state on unrelated record")
            if state == "ACTIVE" and category not in {"DATA_FLOW", "BACKUP_ARCHIVE"}:
                raise ValueError("active risk on unrelated record")
            if state == "BLOCKED" and category != "BACKUP_ARCHIVE":
                raise ValueError("restore state on unrelated record")
        fields = {}
        if kind == "state" and category == "DATA_FLOW" and state in {"ACTIVE", "DISABLED"}:
            fields["enabled"] = state == "ACTIVE"
        if kind == "state" and category == "BACKUP_ARCHIVE" and state in {"ACTIVE", "BLOCKED"}:
            fields["restore_allowed"] = state == "ACTIVE"
        self.fact(system, row.get("resource_id"), category, row.get("recorded_at"),
                  pointer, fields=fields, state=state, queries=queries, metadata=metadata, kind=kind,
                  store=row.get("store_id"))

    def service(self, system, group, row, pointer, region):
        collection, category = SERVICE_COLLECTIONS[(system, group)]
        observed, resource = row.get("observed_at"), _text(row.get("resource_id"))
        metadata = {"collection": collection, "region": region, "readback": True}
        if group == "profile_lookups":
            status, profile = row.get("status_code"), row.get("profile")
            if type(status) is not int or status not in (200, 404):
                raise ValueError("unsupported lookup status")
            if (status == 404 and profile is not None) or (status == 200 and not isinstance(profile, dict)):
                raise ValueError("contradictory lookup payload")
            self.fact(system, resource, category, observed, pointer,
                      fields=profile, state="ABSENT" if status == 404 else "PRESENT",
                      metadata={**metadata, "status_code": status})
        elif group == "index_queries":
            hits, query = row.get("hits"), _text(row.get("query"))
            if not isinstance(hits, dict):
                raise ValueError("missing search response")
            total, documents = hits.get("total"), hits.get("documents")
            if type(total) is not int or total < 0 or not isinstance(documents, list) or total < len(documents):
                raise ValueError("inconsistent hit count")
            identifiers = {"query": (query,)}
            if total == 0:
                self.fact(system, resource, category, observed, pointer, state="ABSENT",
                          queries=identifiers, metadata=metadata)
            for index, document in enumerate(documents):
                if not isinstance(document, dict):
                    raise ValueError("invalid hit")
                self.fact(system, document.get("resource_id"), category, observed,
                          pointer + f"/hits/documents/{index}", fields=document,
                          queries=identifiers, metadata=metadata)
            parse_instant(observed)
            self.scopes.append(SnapshotScope(system, system, collection, category, observed,
                               total == len(documents), tuple(doc["resource_id"] for doc in documents),
                               identifiers, replace(self.source, locator=self.source.locator + pointer)))
            if total != len(documents):
                self.error(pointer, "PARTIAL_QUERY_RESPONSE")
        else:
            fields = {key: value for key, value in row.items() if key != "observed_at"}
            if group == "connectors" and type(row.get("enabled")) is not bool:
                raise ValueError("invalid connector flag")
            if group == "restore_entries" and type(row.get("restore_allowed")) is not bool:
                raise ValueError("invalid restore flag")
            self.fact(system, resource, category, observed, pointer, fields=fields, metadata=metadata)

    def batch(self, layout):
        return EvidenceBatch(layout, tuple(self.facts), tuple(self.scopes), tuple(self.diagnostics),
                             self.seen, self.normalized)


def normalize_json(document: object, source: EvidenceRef) -> EvidenceBatch:
    """Normalize four supported layouts; surface unsupported nodes explicitly.

    No cutoff is applied here. Empty inventories have no completeness implication.
    Search response scopes are complete only within their explicit query selector.
    """
    if not isinstance(source, EvidenceRef):
        raise ValueError("source must be an EvidenceRef")
    if not isinstance(source.locator, str) or (source.locator and not source.locator.startswith("/")):
        raise ValueError("invalid source locator")
    try:
        parse_instant(source.available_at)
    except ValueError:
        raise ValueError("invalid source availability") from None
    builder = _Builder(source)
    if not isinstance(document, dict):
        builder.error("", "UNSUPPORTED_LAYOUT")
        return builder.batch("unsupported")
    if "snapshots" in document:
        layout = "snapshot_bundle"
        snapshots = document["snapshots"]
        if not isinstance(snapshots, list):
            builder.error("/snapshots", "INVALID_COLLECTION")
        else:
            for index, snapshot in enumerate(snapshots):
                pointer = f"/snapshots/{index}"
                if not isinstance(snapshot, dict):
                    builder.error(pointer, "INVALID_SNAPSHOT")
                elif snapshot.get("collection") == "operational_record_history":
                    builder.rows(snapshot.get("records"), pointer + "/records", builder.history)
                else:
                    builder.regular(snapshot, pointer)
    elif "service_exports" in document:
        layout = "service_exports"
        exports = document["service_exports"]
        if not isinstance(exports, dict):
            builder.error("/service_exports", "INVALID_COLLECTION")
        else:
            for system, groups in exports.items():
                if not isinstance(groups, dict):
                    builder.error(_pointer("service_exports", system), "INVALID_COLLECTION")
                    continue
                for group, rows in groups.items():
                    location = _pointer("service_exports", system, group)
                    if (system, group) not in SERVICE_COLLECTIONS:
                        builder.seen += len(rows) if isinstance(rows, list) else 0
                        builder.error(location, "UNSUPPORTED_COLLECTION")
                        continue
                    builder.rows(rows, location, lambda row, p, s=system, g=group:
                                 builder.service(s, g, row, p, document.get("region")))
    elif "records" in document and "collection" in document:
        layout = "standalone_records"
        pair = (document.get("source"), document.get("collection"))
        system = STANDALONE_SCHEMAS.get(pair) if all(isinstance(value, str) for value in pair) else None
        if system is None:
            builder.seen += len(document["records"]) if isinstance(document["records"], list) else 0
            builder.error("", "UNSUPPORTED_COLLECTION")
        else:
            snapshot = dict(captured_at=document.get("captured_at"), data_store=system,
                            collection=document["collection"], records=document["records"])
            builder.regular(snapshot, "", store=document["source"])
    elif "records" in document and "exported_at" in document:
        layout = "readback_history"
        builder.rows(document["records"], "/records", builder.history)
        count = document.get("record_count")
        if type(count) is not int or not isinstance(document["records"], list) or count != len(document["records"]):
            builder.error("/record_count", "RECORD_COUNT_MISMATCH")
    else:
        layout = "unsupported"
        builder.error("", "UNSUPPORTED_LAYOUT")
    return builder.batch(layout)


def read_json_evidence(entry: ManifestEntry) -> EvidenceBatch:
    """Verify source bytes through the reviewed reader, then normalize evidence."""
    source = EvidenceRef(entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at)
    return normalize_json(load_json_entry(entry), source)
