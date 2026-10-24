# Verified implementation handoff

Dataset: pass an explicit path; the local read-only reference is
`../research/october26/dataset` from the project root. No corpus is committed.
Only participant-facing data may be used. Sample answers are evaluation-only.

## Locked shared contracts

`phantom.contracts` is authoritative. All dataclasses are frozen with slots.

```python
IdentifierMap = dict[str, tuple[str, ...]]
FactKind = Literal['state', 'claim', 'identity', 'context']
EntityKey(system_id: str, store_id: str, resource_id: str)
EvidenceRef(evidence_id: str, source_path: str, source_sha256: str,
            locator: str, available_at: str)
EvidenceFact(entity: EntityKey, category: str, observed_at: str, kind: FactKind,
             state: str, stored_fields: dict[str, object],
             query_identifiers: IdentifierMap, field_coverage: tuple[str, ...],
             references: tuple[EntityKey, ...], metadata: dict[str, object],
             source: EvidenceRef)
SnapshotScope(system_id: str, store_id: str, collection: str, category: str,
              observed_at: str, complete: bool, resource_ids: tuple[str, ...],
              query_identifiers: IdentifierMap, source: EvidenceRef)
AuditStep(step_no: int, phase: str, system_id: str, resource_id: str,
          operation: str, state: str, evidence_id: str)
AuditResult(case_id: str, deletion_status: str, steps: tuple[AuditStep, ...],
            issues: tuple[str, ...] = ())
parse_instant(value: str) -> datetime
is_available(item: EvidenceFact | SnapshotScope, audit_at: str) -> bool
validate_row(row: dict[str, str]) -> None
to_csv_row(result: AuditResult) -> dict[str, str]
write_output(path: Path, results: Sequence[AuditResult],
             expected_case_ids: Sequence[str]) -> None
```

Output constants and operation order remain exactly those in contracts.py.
`is_available` requires observation AND source availability by the cutoff.
COMPLETE requires cited verification only; INCOMPLETE future checks/actions have
empty evidence IDs; NOT_APPLICABLE has no steps. Internal issues are not CSV fields.

## Implemented input and evaluation APIs

```python
# phantom.corpus
ManifestEntry(file_id: str, file_path: str, origin: str, captured_at: str,
              path: Path, sha256: str)  # frozen, slots
read_csv_table(path: Path, columns: tuple[str, ...],
               id_column: str) -> list[dict[str, str]]
read_manifest(dataset_dir: Path) -> tuple[ManifestEntry, ...]
# phantom.tabular
load_tables(dataset_dir: Path) -> dict[str, tuple[dict[str, str], ...]]
# phantom.json_reader
load_json_entry(entry: ManifestEntry) -> object
walk_json(value: object) -> Iterator[tuple[str, object]]
# phantom.sql_reader
SqlTable(name: str, columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...])
SqlDump(text: str, tables: tuple[SqlTable, ...])
read_sql_entry(entry: ManifestEntry) -> SqlDump
# phantom.record_snapshots
extract_record_snapshot(snapshot: dict[str, object],
                        source: EvidenceRef) -> tuple[EvidenceFact, ...]
# phantom.metrics
compare_rows(expected_rows: Sequence[dict[str, str]],
             predicted_rows: Sequence[dict[str, str]]) -> dict[str, object]
```

SQL requires SQLite >=3.37. Sources execute in memory with an aggregate budget.
Only enabling foreign_keys is an allowed source PRAGMA. WITHOUT ROWID and
tables shadowing every rowid alias fail explicitly. No source files are modified.
JSON rejects duplicate keys, nonfinite numbers and mismatched hashes.

Regular snapshot coverage is top-level observed field names, not a complete
schema or a declaration that missing fields are null. Snapshot facts are PRESENT
even for sanitized tombstones/inactive configuration rows. Internal DATA_FLOW
is not a new category in the policy CSV. Operational history is a separate
adapter; an empty record list is not absence proof.

## Verification and remaining stages

Before stage 01: 169 tests passed, 113 regular snapshots -> 361 facts; all 28 JSON
and 24 SQL files passed their raw-reader integrations. These are NOT prediction
accuracy results. Stage 01 adds locator/timestamp privacy regressions.

Command: `python -B -m unittest discover -s code/tests -q`.
Remaining: JSON and SQL semantic adapters; intent/history resolution; temporal
state; policy/planning/verifier; logs/text/media; compiler/cache/CLI; evaluation;
final measured run and clean packaging. No full audit CLI exists yet.

Stage 01 verified: 171 tests passed, zero skips; real integration again covered 113 regular snapshots and 361 facts with unchanged source hashes. Locator validation and timestamp error privacy repaired.
