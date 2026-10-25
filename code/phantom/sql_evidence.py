"""SQL snapshot observations with explicit store and capture-time boundaries."""

import copy
import json
import re
from collections import defaultdict
from dataclasses import replace

from .contracts import EntityKey, EvidenceFact, EvidenceRef, SnapshotScope, parse_instant
from .corpus import ManifestEntry
from .json_evidence import EvidenceBatch, EvidenceDiagnostic
from .json_reader import walk_json
from .record_snapshots import COLLECTION_CATEGORIES
from .sql_reader import SqlDump, read_sql_entry


# Declared SQL schemas, not resource-name prefixes or participant answer tables.
DOMAIN_TABLES = {
    "customer_profiles": ("account_service", "customer_profiles"),
    "customer_profiles_primary": ("account_service", "customer_profiles"),
    "customer_profiles_failover_replica": ("account_service", "customer_profiles"),
    "customer_search_documents": ("search_service", "customer_search_documents"),
    "search_documents_primary": ("search_service", "customer_search_documents"),
    "customer_analytics_profiles": ("analytics_platform", "customer_analytics_reports"),
    "support_records": ("support_platform", "support_conversations"),
    "customer_exports": ("export_service", "customer_export_jobs"),
    "connector_subscriptions": ("integration_service", "connector_configurations"),
    "backup_restore_entries": ("backup_service", "backup_catalog"),
    "security_audit_records": ("security_service", "security_audit_events"),
}
GENERIC_COLUMNS = {"resource_id", "origin_system", "collection_name", "record_json", "updated_at"}
BOOLEAN_COLUMNS = {
    "connector_subscriptions": frozenset({"enabled"}),
    "backup_restore_entries": frozenset({"restore_allowed"}),
}


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required string missing")
    return value


def _json_record(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    value = json.loads(_text(text), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("object required")
    for _ in walk_json(value):
        pass  # Rejects nonfinite literals/overflow at every depth.
    return value


def _location(table, suffix):
    escaped = table.replace("~", "~0").replace("/", "~1")
    return f"/tables/{escaped}/{suffix}"


def normalize_sql(dump: SqlDump, source: EvidenceRef) -> EvidenceBatch:
    """Normalize all ordinary rows plus scope declarations without reading labels.

    `snapshot_scope(origin_system,captured_at)` declares where/when, not that a
    person-filtered export is exhaustive. Supplied scopes are therefore partial.
    Only an explicit complete=1 declaration upgrades that exact domain scope.
    A point-in-time header alone is not a statement of collection completeness.
    """
    if not isinstance(dump, SqlDump) or not isinstance(source, EvidenceRef):
        raise ValueError("expected SQL dump and source reference")
    if not isinstance(source.locator, str):
        raise ValueError("invalid source locator")
    try:
        parse_instant(source.available_at)
    except ValueError:
        raise ValueError("invalid source availability") from None
    facts, scopes, diagnostics = [], [], []
    seen = normalized = 0

    def reference(location):
        return replace(source, locator=source.locator + location)

    def diagnostic(location, code):
        diagnostics.append(EvidenceDiagnostic(code, reference(location),
                           "Unsupported, inconsistent or insufficient SQL evidence metadata"))

    captures = re.findall(r"^--\s*Captured at:\s*(\S+)\s*$", dump.text, re.M)
    if not captures:
        captures = re.findall(r"^--\s*snapshot_completed_at=(\S+)\s*$", dump.text, re.M)
    header_time = captures[0] if len(set(captures)) == 1 else None
    if header_time:
        try:
            parse_instant(header_time)
        except ValueError:
            header_time = None
            diagnostic("/sql-header", "INVALID_CAPTURE_TIME")
    elif captures:
        diagnostic("/sql-header", "CONFLICTING_CAPTURE_TIME")
    declared = {}
    invalid_systems = set()
    for table in dump.tables:
        if table.name != "snapshot_scope":
            continue
        for index, row in enumerate(table.rows):
            seen += 1
            location = _location(table.name, f"rows/{index}")
            try:
                if len(row) != len(table.columns):
                    raise ValueError("invalid scope row")
                values = dict(zip(table.columns, row))
                system, observed = _text(values.get("origin_system")), _text(values.get("captured_at"))
                parse_instant(observed)
                complete = values.get("complete", 0)
                if type(complete) not in (int, bool) or complete not in (0, 1):
                    raise ValueError("invalid completeness declaration")
                if system in declared:
                    invalid_systems.add(system)
                    raise ValueError("duplicate scope declaration")
                declared[system] = (observed, bool(complete), reference(location))
                normalized += 1
            except (ValueError, TypeError):
                diagnostic(location, "INVALID_SCOPE_DECLARATION")
    for system in invalid_systems:
        declared.pop(system, None)

    for table in dump.tables:
        if table.name == "snapshot_scope":
            continue
        generic = GENERIC_COLUMNS <= set(table.columns)
        domain = DOMAIN_TABLES.get(table.name)
        if not generic and domain is None:
            seen += len(table.rows)
            diagnostic(_location(table.name, ""), "UNSUPPORTED_SQL_TABLE")
            continue
        # Each physical table has its own collection-specific scope groups.
        groups = defaultdict(list)
        for index, row in enumerate(table.rows):
            seen += 1
            location = _location(table.name, f"rows/{index}")
            try:
                if len(row) != len(table.columns):
                    raise ValueError("invalid row")
                values = dict(zip(table.columns, row))
                if generic:
                    system, collection = _text(values.get("origin_system")), _text(values.get("collection_name"))
                    fields = _json_record(values.get("record_json"))
                    extra_columns = {key: value for key, value in values.items() if key not in GENERIC_COLUMNS}
                    if extra_columns:
                        # Preserve all physical payload columns without allowing
                        # them to overwrite equally named JSON document fields.
                        fields = {"document": fields, "sql_columns": extra_columns}
                else:
                    system, collection = domain
                    fields = {key: value for key, value in values.items()
                              if key not in {"updated_at", "indexed_at"}}
                    # SQLite BOOLEAN values use INTEGER storage. Interpret only
                    # reviewed boolean columns; NULL/text/nonbinary numbers stay
                    # unknown, and JSON document numbers remain JSON numbers.
                    for key in BOOLEAN_COLUMNS.get(table.name, ()):
                        if type(fields.get(key)) is int and fields[key] in (0, 1):
                            fields[key] = bool(fields[key])
                category = COLLECTION_CATEGORIES[(system, collection)]
                resource = _text(values.get("resource_id"))
                document = fields["document"] if generic and extra_columns else fields
                if "resource_id" in document and document["resource_id"] != resource:
                    raise ValueError("conflicting resource identity")
                observed = declared.get(system, (header_time,))[0]
                if not observed or system in invalid_systems:
                    raise ValueError("missing capture time")
                parse_instant(observed)
                metadata = {"collection": collection, "sql_table": table.name,
                            # SELECT * over the supplied physical table yields a
                            # complete stored row, including the whole JSON blob.
                            # This says nothing about collection completeness.
                            "full_record": True,
                            "row_updated_at": values.get("updated_at"),
                            "row_indexed_at": values.get("indexed_at"),
                            "scope_basis": "explicit-complete" if declared.get(system, (None, False))[1]
                            else "declared-time-only" if system in declared else "capture-header-only"}
                fact = EvidenceFact(EntityKey(system, table.name, resource), category, observed,
                                    "state", "PRESENT", copy.deepcopy(fields), {}, tuple(sorted(fields)),
                                    (), metadata, reference(location))
                facts.append(fact)
                groups[(system, collection, category, observed)].append(resource)
                normalized += 1
            except (ValueError, TypeError, KeyError):
                diagnostic(location, "INVALID_SQL_RECORD")
        if domain and not table.rows:
            system, collection = domain
            observed = declared.get(system, (header_time,))[0]
            if observed and system not in invalid_systems:
                groups[(system, collection, COLLECTION_CATEGORIES[domain], observed)] = []
            else:
                diagnostic(_location(table.name, "scope"), "MISSING_SCOPE_TIME")
        for (system, collection, category, observed), resources in groups.items():
            complete = declared.get(system, (None, False))[1]
            # A failed row invalidates collection completeness; it cannot disappear.
            complete = complete and not any(d.source.locator.startswith(
                source.locator + _location(table.name, "")) for d in diagnostics)
            scopes.append(SnapshotScope(system, table.name, collection, category, observed,
                          complete, tuple(resources), {}, reference(_location(table.name, "scope"))))
        if generic and not table.rows:
            if header_time:
                scopes.append(SnapshotScope("*", table.name, "*", "*", header_time, False, (), {},
                              reference(_location(table.name, "scope"))))
            else:
                diagnostic(_location(table.name, "scope"), "MISSING_SCOPE_TIME")

    # Only actual explicit source_resource_id links are resolved, never suffixes.
    targets = defaultdict(list)
    for fact in facts:
        targets[fact.entity.resource_id].append(fact.entity)
    resolved = []
    for fact in facts:
        resource = fact.stored_fields.get("source_resource_id")
        candidates = targets.get(resource, []) if isinstance(resource, str) else []
        if len(candidates) == 1 and candidates[0] != fact.entity:
            fact = replace(fact, references=(candidates[0],))
        resolved.append(fact)
    return EvidenceBatch("sql_snapshot", tuple(resolved), tuple(scopes), tuple(diagnostics), seen, normalized)


def read_sql_evidence(entry: ManifestEntry) -> EvidenceBatch:
    """Use the verified, sandboxed raw reader and then interpret the captured data."""
    return normalize_sql(read_sql_entry(entry), EvidenceRef(entry.file_id, entry.file_path,
                         entry.sha256, "", entry.captured_at))
