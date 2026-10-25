"""Portable corpus compiler: facts, provenance and layout/error inventory, never labels."""
import hashlib
import importlib.metadata
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .activity_evidence import normalize_activity
from .cache import JsonCache, canonical
from .contracts import EntityKey, EvidenceFact, EvidenceRef, SnapshotScope, parse_instant
from .corpus import read_manifest
from .json_evidence import EvidenceDiagnostic, read_json_evidence
from .local_model import LocalOCR
from .references import bind_resource_references
from .log_evidence import read_log_evidence
from .media_evidence import read_media_evidence
from .sql_evidence import read_sql_evidence
from .tabular import TABLE_SPECS, load_tables
from .text_evidence import read_text_evidence


@dataclass(frozen=True, slots=True)
class CompiledCorpus:
    tables: dict
    facts: tuple[EvidenceFact, ...]
    scopes: tuple[SnapshotScope, ...]
    diagnostics: tuple[EvidenceDiagnostic, ...]
    inventory: tuple[dict, ...]
    model_usage: dict | None
    cache_key: str
    cache_hit: bool


def _ref(value):
    ref = EvidenceRef(**value)
    if not all(isinstance(v, str) for v in asdict(ref).values()):
        raise ValueError("invalid cached source")
    parse_instant(ref.available_at)
    return ref


def decode(payload):
    """Reconstitute typed values through explicit constructors, with semantic shape checks."""
    facts = []
    for raw in payload["facts"]:
        item = dict(raw)
        if not isinstance(item["field_coverage"], list) or not all(isinstance(v, str) for v in item["field_coverage"]):
            raise ValueError("invalid cached coverage")
        if not all(isinstance(values, list) for values in item["query_identifiers"].values()):
            raise ValueError("invalid cached selectors")
        item["entity"] = EntityKey(**item["entity"])
        item["source"] = _ref(item["source"])
        item["references"] = tuple(EntityKey(**ref) for ref in item["references"])
        item["field_coverage"] = tuple(item["field_coverage"])
        item["query_identifiers"] = {key: tuple(values) for key, values in item["query_identifiers"].items()}
        fact = EvidenceFact(**item)
        parse_instant(fact.observed_at)
        if fact.kind not in {"state", "claim", "identity", "context"} or not isinstance(fact.stored_fields, dict) or not isinstance(fact.metadata, dict):
            raise ValueError("invalid cached fact")
        if not all(isinstance(v, str) and v for v in asdict(fact.entity).values()):
            raise ValueError("invalid cached entity")
        if not all(isinstance(k, str) and all(isinstance(v, str) for v in vs) for k, vs in fact.query_identifiers.items()):
            raise ValueError("invalid cached selector")
        facts.append(fact)
    scopes = []
    for raw in payload["scopes"]:
        item = dict(raw)
        item["source"] = _ref(item["source"])
        item["resource_ids"] = tuple(item["resource_ids"])
        item["query_identifiers"] = {key: tuple(values) for key, values in item["query_identifiers"].items()}
        scope = SnapshotScope(**item)
        parse_instant(scope.observed_at)
        if type(scope.complete) is not bool:
            raise ValueError("invalid cached completeness")
        scopes.append(scope)
    diagnostics = tuple(EvidenceDiagnostic(d["code"], _ref(d["source"]), d["detail"]) for d in payload["diagnostics"])
    return tuple(facts), tuple(scopes), diagnostics


def compile_corpus(dataset_dir: Path, *, cache_dir: Path | None = None,
                   media_cache: Path | None = None) -> CompiledCorpus:
    dataset_dir = dataset_dir.resolve()
    table_hashes = {name: hashlib.sha256((dataset_dir / (name+".csv")).read_bytes()).hexdigest()
                    for name, _, _ in TABLE_SPECS}
    tables = load_tables(dataset_dir)
    if any(hashlib.sha256((dataset_dir / (name+".csv")).read_bytes()).hexdigest() != digest
           for name, digest in table_hashes.items()):
        raise ValueError("CSV changed during loading")
    entries = read_manifest(dataset_dir)
    ocr = LocalOCR(media_cache if media_cache is not None else (cache_dir / "ocr" if cache_dir else Path(".cache/ocr")))
    model_identity = ocr.identity() if any(e.path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"} for e in entries) else None
    source_versions = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}
    inputs = dict(tables=table_hashes, manifest=[{k: getattr(e, k) for k in
                  ("file_id", "file_path", "origin", "captured_at", "sha256")} for e in entries],
                  code=source_versions, model=model_identity, schema="compiler.v1",
                  runtime=dict(python=sys.version, sqlite=sqlite3.sqlite_version,
                               pdfium=importlib.metadata.version("pypdfium2") if model_identity else None))
    key = hashlib.sha256(canonical(inputs)).hexdigest()
    cache = JsonCache(cache_dir / "compiled") if cache_dir else None
    cached = cache.load(key) if cache else None
    if cached is not None:
        try:
            facts, scopes, diagnostics = decode(cached)
            usage = dict(cached["model_usage"] or {}) or None
            if usage:
                usage = dict(usage, preparation_provenance=usage, calls=0, cache_hits=0, inference_seconds=0.0,
                             media_wall_seconds=0.0, workers_used=0, compiled_cache_hit=True)
            return CompiledCorpus(tables, facts, scopes, diagnostics, tuple(cached["inventory"]), usage, key, True)
        except (ValueError, TypeError, KeyError):
            pass
    facts, scopes, diagnostics, inventory, media = [], [], [], [], []
    readers = {".json": read_json_evidence, ".sql": read_sql_evidence,
               ".log": read_log_evidence, ".txt": read_text_evidence}
    def accept(entry, batch):
        facts.extend(batch.facts)
        scopes.extend(batch.scopes)
        diagnostics.extend(batch.diagnostics)
        inventory.append(dict(file_id=entry.file_id, path=entry.file_path, layout=batch.layout,
                              records_seen=batch.records_seen, records_normalized=batch.records_normalized,
                              facts=len(batch.facts), scopes=len(batch.scopes), diagnostics=[d.code for d in batch.diagnostics],
                              status="DIAGNOSTIC" if batch.diagnostics else "SUPPORTED"))
    def fail(entry):
        ref = EvidenceRef(entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at)
        diagnostics.append(EvidenceDiagnostic("SOURCE_PARSE_ERROR", ref, "Source failed reviewed adapter"))
        inventory.append(dict(file_id=entry.file_id, path=entry.file_path, status="ERROR", layout="unknown",
                              facts=0, scopes=0, records_seen=0, records_normalized=0, diagnostics=["SOURCE_PARSE_ERROR"]))
    for entry in entries:
        suffix = entry.path.suffix.lower()
        if suffix in {".pdf", ".png", ".jpg", ".jpeg"}:
            media.append(entry)
            continue
        try:
            if suffix not in readers:
                raise ValueError("unsupported evidence type")
            accept(entry, readers[suffix](entry))
        except (OSError, ValueError):
            fail(entry)
    activity = normalize_activity(tables["system_activity"], table_hashes["system_activity"], facts)
    facts.extend(activity.facts)
    diagnostics.extend(activity.diagnostics)
    facts = list(bind_resource_references(facts))
    # Each worker owns its OCR engine. Catalog identity context is already
    # complete; media facts never supply catalog bindings for subsequent files.
    # Consume map results in manifest order to keep provenance/replays stable.
    batches, usage = _media_batches(media, ocr, tuple(facts)) if media else ([], None)
    for entry, batch in zip(media, batches):
        fail(entry) if batch is None else accept(entry, batch)
    payload = dict(facts=[asdict(f) for f in facts], scopes=[asdict(s) for s in scopes],
                   diagnostics=[asdict(d) for d in diagnostics], inventory=inventory,
                   model_usage=usage)
    if cache:
        cache.store(key, payload)
    return CompiledCorpus(tables, tuple(facts), tuple(scopes), tuple(diagnostics), tuple(inventory), payload["model_usage"], key, False)


def _media_batches(entries, ocr, context):
    local, engines = threading.local(), []
    lock = threading.Lock()

    def read(entry):
        if not hasattr(local, "ocr"):
            local.ocr = LocalOCR(ocr.cache_dir)
            with lock:
                engines.append(local.ocr)
        try:
            return read_media_evidence(entry, local.ocr, context)
        except (OSError, ValueError):
            return None

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="media") as pool:
        batches = list(pool.map(read, entries))
    usage = ocr.usage()
    usage.update(calls=sum(engine.calls for engine in engines),
                 cache_hits=sum(engine.cache_hits for engine in engines),
                 inference_seconds=sum(engine.seconds for engine in engines),
                 media_wall_seconds=time.perf_counter() - started, workers_used=len(engines))
    return batches, usage
