"""Format-specific evidence parsers. Each returns (observations, scopes, docs, notes)."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..loaders import parse_ts
from .documents import image_text, pdf_text
from .log_lines import parse_line
from .model import EvidenceDoc, Scope, keys_from_value, system_for_resource
from .records import build_observation

TYPED_TABLE_SYSTEMS = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}


class ParseResult:
    def __init__(self):
        self.observations = []
        self.scopes = []
        self.docs = []
        self.notes: list[str] = []
        self.method = ""


# --------------------------------------------------------------------------- SQL
def parse_sql(path: Path, file_id: str, captured_at) -> ParseResult:
    res = ParseResult()
    res.method = "sqlite3"
    text = path.read_text(encoding="utf-8")
    header_ts = None
    m = re.search(r"Captured at:\s*(\S+)", text)
    if m:
        header_ts = parse_ts(m.group(1))
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
    except sqlite3.Error as exc:  # pragma: no cover - reported in coverage
        res.notes.append(f"sqlite error: {exc}")
        con.close()
        return res
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    scope_times = {}
    if "snapshot_scope" in tables:
        for row in con.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
            scope_times[row["origin_system"]] = parse_ts(row["captured_at"])
    snap_time = header_ts or captured_at
    file_systems: set[str] = set()
    for table in tables:
        if table == "snapshot_scope":
            continue
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        replica = "replica" in table
        listed_by_system: dict[str, set] = {}
        rows = list(con.execute(f'SELECT rowid AS _pos, * FROM "{table}" ORDER BY rowid'))
        for pos, row in enumerate(rows):
            rec = {c: row[c] for c in cols}
            store = table
            system = TYPED_TABLE_SYSTEMS.get(table)
            if "record_json" in rec:
                try:
                    inner = json.loads(rec["record_json"])
                except json.JSONDecodeError:
                    res.notes.append(f"bad record_json in {table}")
                    continue
                system = rec.get("origin_system") or system
                store = f"{table}:{rec.get('collection_name')}"
                rtype_store = rec.get("collection_name")
                inner.setdefault("resource_id", rec.get("resource_id"))
                record = inner
            else:
                rtype_store = table
                record = rec
            system = system_for_resource(str(record.get("resource_id"))) or system
            obs_time = scope_times.get(system) or snap_time
            obs = build_observation(record, file_id=file_id, captured_at=captured_at, obs_time=obs_time,
                                    system=system, store=rtype_store, replica=replica, kind=f"sql:{table}",
                                    position=pos, collection=f"{file_id}:{store}")
            if obs is None:
                continue
            obs.flags["table"] = table
            if rec.get("source_resource_id"):
                obs.flags["source_resource_id"] = rec["source_resource_id"]
            res.observations.append(obs)
            listed_by_system.setdefault(obs.system, set())
            if obs.present:
                listed_by_system[obs.system].add(obs.resource_id)
            file_systems.add(obs.system)
        sys_for_scope = set(listed_by_system)
        if table in TYPED_TABLE_SYSTEMS:
            sys_for_scope.add(TYPED_TABLE_SYSTEMS[table])
        if "record_json" in cols:
            # a full customer-data snapshot also covers the account store: no row means no account
            sys_for_scope.add("account_service")
        for system in sys_for_scope:
            if scope_times and system not in scope_times:
                continue
            res.scopes.append(Scope(file_id, captured_at, scope_times.get(system) or snap_time, system, table,
                                    listed_by_system.get(system, set())))
    con.close()
    return res


# --------------------------------------------------------------------------- JSON
def _ts(record: dict, *keys, default=None):
    for k in keys:
        if record.get(k):
            t = parse_ts(record[k])
            if t:
                return t
    return default


def parse_json(path: Path, file_id: str, captured_at) -> ParseResult:
    res = ParseResult()
    res.method = "json"
    data = json.loads(path.read_text(encoding="utf-8"))
    top_time = _ts(data, "captured_at", "exported_at", default=captured_at)

    if "service_exports" in data:
        res.method = "json:service_export"
        for system, colls in data["service_exports"].items():
            for coll, rows in colls.items():
                listed = set()
                for pos, row in enumerate(rows):
                    record = dict(row)
                    hits = record.pop("hits", None)
                    if isinstance(hits, dict):
                        docs = [d for d in hits.get("documents") or [] if d.get("resource_id") == record.get("resource_id")]
                        record["total_hits"] = len(docs) if docs or hits.get("total") == 0 else hits.get("total")
                        if docs:
                            record.update({k: v for k, v in docs[0].items() if k != "resource_id"})
                    obs = build_observation(record, file_id=file_id, captured_at=captured_at,
                                            obs_time=_ts(row, "observed_at", default=top_time), system=system,
                                            store=coll, kind=f"export:{coll}", position=pos,
                                            collection=f"{file_id}:{system}:{coll}")
                    if obs:
                        res.observations.append(obs)
                        if obs.present:
                            listed.add(obs.resource_id)
                res.scopes.append(Scope(file_id, captured_at, top_time, system, coll, listed, kind="query"))
        return res

    if "snapshots" in data:
        res.method = "json:snapshot_bundle"
        for snap in data["snapshots"]:
            store = snap.get("collection", "")
            system = snap.get("data_store", "")
            snap_time = _ts(snap, "captured_at", default=top_time)
            history = store == "operational_record_history"
            listed = set()
            for pos, row in enumerate(snap.get("records") or []):
                obs_time = _ts(row, "recorded_at", default=snap_time) if history else snap_time
                obs = build_observation(row, file_id=file_id, captured_at=captured_at, obs_time=obs_time,
                                        system=row.get("system") or system, store=store,
                                        kind=f"bundle:{store}", position=pos,
                                        collection=f"{file_id}:{system}:{store}:{snap.get('captured_at')}")
                if obs:
                    res.observations.append(obs)
                    if obs.present:
                        listed.add(obs.resource_id)
            if not history:
                res.scopes.append(Scope(file_id, captured_at, snap_time, system, store, listed))
        return res

    records = data.get("records")
    if isinstance(records, list):
        manifest_style = any("record_type" in r for r in records)
        res.method = "json:record_manifest" if manifest_style else "json:collection"
        store = data.get("collection", "")
        listed = set()
        systems = set()
        for pos, row in enumerate(records):
            obs = build_observation(row, file_id=file_id, captured_at=captured_at,
                                    obs_time=_ts(row, "recorded_at", default=top_time),
                                    system=row.get("system"), store=store,
                                    kind="manifest" if manifest_style else f"collection:{store}",
                                    position=pos, collection=f"{file_id}:{store}")
            if obs:
                res.observations.append(obs)
                if obs.present:
                    listed.add(obs.resource_id)
                systems.add(obs.system)
        if not manifest_style:
            for system in systems:
                res.scopes.append(Scope(file_id, captured_at, top_time, system, store, listed))
        return res

    res.notes.append("unrecognized JSON layout")
    return res


# --------------------------------------------------------------------------- logs
LINK_FIELDS = ("lookup_key", "correlation_key", "subject_key", "match_key", "scan_key", "subject_ref",
               "owner_key", "subject_reference", "matched_value")


def parse_log(path: Path, file_id: str, captured_at) -> ParseResult:
    res = ParseResult()
    res.method = "log"
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rec = parse_line(raw)
        if rec is None:
            res.notes.append(f"unparsed line: {raw[:80]}")
            continue
        lines.append(rec)
    trace_keys: dict[str, set] = {}
    for rec in lines:
        if rec["trace_id"]:
            keys = trace_keys.setdefault(rec["trace_id"], set())
            for f in LINK_FIELDS:
                if f in rec["attrs"]:
                    keys |= keys_from_value(rec["attrs"][f], f)
    formats = sorted({r["format"] for r in lines})
    res.method = "log:" + "+".join(formats)
    for pos, rec in enumerate(lines):
        attrs = dict(rec["attrs"])
        rid = attrs.get("resource_id")
        if not rid:
            continue
        event = rec["event"] or ""
        if event.endswith("_started") or event in ("thumbnail_failed",):
            continue  # query start lines only carry lookup keys
        extra = set(trace_keys.get(rec["trace_id"], set())) if rec["trace_id"] else set()
        for f in LINK_FIELDS:
            if f in attrs:
                extra |= keys_from_value(attrs[f], f)
        system = attrs.get("system") or rec["service"]
        record_type = attrs.get("record_type")
        if event == "contact_lookup_completed":
            record_type = "SECONDARY_IDENTIFIER"
        elif event == "attachment_pii_scan":
            record_type = "SUPPORT_ATTACHMENT"
        elif event in ("message_pii_scan", "message_read", "support_ticket_read_completed"):
            record_type = "SUPPORT_MESSAGE"
        elif event in ("contact_query_completed", "deletion_marker_check"):
            record_type = "SUPPORT_CONTACT_COPY"
        if event in ("message_pii_scan", "attachment_pii_scan", "analytics_identity_scan") and attrs.get("matched_value"):
            attrs["matched_value"] = attrs["matched_value"]
        else:
            attrs.pop("matched_value", None)
        if event == "index_query_completed" and attrs.get("result"):
            attrs["result"] = {"hit": "FOUND", "miss": "NOT_FOUND"}.get(attrs["result"].lower(), attrs["result"])
        if event == "deletion_marker_check":
            attrs.setdefault("rows", "1")
        obs = build_observation(attrs, file_id=file_id, captured_at=captured_at, obs_time=parse_ts(rec["ts"]),
                                system=system, store=rec["service"] or "", record_type=record_type,
                                kind=f"log:{event}", position=pos, collection=f"{file_id}:log",
                                extra_keys=extra)
        if obs:
            obs.flags["trace_id"] = rec["trace_id"] or ""
            res.observations.append(obs)
    return res


# --------------------------------------------------------------------------- text
HANDOVER_RE = re.compile(r"^\s*-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*$")


def parse_text(path: Path, file_id: str, captured_at) -> ParseResult:
    res = ParseResult()
    res.method = "text:pipe_rows"
    text = path.read_text(encoding="utf-8")
    for pos, line in enumerate(text.splitlines()):
        m = HANDOVER_RE.match(line)
        if not m:
            continue
        ts, system, rid, rtype, result, subject = m.groups()
        record = {"resource_id": rid, "system": system, "record_type": rtype, "result": result,
                  "lookup_value": subject}
        obs = build_observation(record, file_id=file_id, captured_at=captured_at,
                                obs_time=parse_ts(ts) or captured_at, system=system, kind="handover",
                                position=pos, collection=f"{file_id}:handover")
        if obs:
            res.observations.append(obs)
    if not res.observations:
        res.docs.append(EvidenceDoc(file_id, captured_at, text))
    return res


# --------------------------------------------------------------------------- documents
def parse_document(path: Path, file_id: str, captured_at) -> ParseResult:
    res = ParseResult()
    if path.suffix.lower() == ".pdf":
        text, method = pdf_text(path)
    else:
        text, method = image_text(path, file_id)
    res.method = method
    if not text.strip():
        res.notes.append(f"no text extracted ({method})")
    res.docs.append(EvidenceDoc(file_id, captured_at, text))
    return res


def parse_file(path: Path, file_id: str, captured_at) -> ParseResult:
    suffix = path.suffix.lower()
    if suffix == ".sql":
        return parse_sql(path, file_id, captured_at)
    if suffix == ".json":
        return parse_json(path, file_id, captured_at)
    if suffix == ".log":
        return parse_log(path, file_id, captured_at)
    if suffix == ".txt":
        return parse_text(path, file_id, captured_at)
    if suffix in (".pdf", ".png", ".jpg", ".jpeg"):
        return parse_document(path, file_id, captured_at)
    res = ParseResult()
    res.method = "unsupported"
    res.notes.append(f"unsupported file type {suffix}")
    return res
