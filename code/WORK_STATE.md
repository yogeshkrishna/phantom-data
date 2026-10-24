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

## Stage 02: JSON evidence

`phantom.json_evidence` adds these immutable result types and APIs:

```python
EvidenceDiagnostic(code: str, source: EvidenceRef, detail: str)
EvidenceBatch(layout: str, facts: tuple[EvidenceFact, ...],
              scopes: tuple[SnapshotScope, ...],
              diagnostics: tuple[EvidenceDiagnostic, ...],
              records_seen: int, records_normalized: int)
normalize_json(document: object, source: EvidenceRef) -> EvidenceBatch
read_json_evidence(entry: ManifestEntry) -> EvidenceBatch
```

All four supplied root layouts are supported. Real integration: 28 files,
890 source records normalized to 890 facts and 58 query-scoped search responses;
zero diagnostics, unchanged hashes. No regular empty inventory becomes absence.
Readback query values stay in `query_identifiers` under their original field names
(`lookup_value`, `subject_reference`, `query`); they never become stored PII.
Workflow rows containing `operation`/`event_type` are claims. Treatment readbacks
retain their assertion in metadata with no invented stored fields/coverage.
ACTIVE can denote a connector or archive restore risk; category disambiguates it.
Search scopes cover only their explicit query, never the global collection.
Per-record failures roll back partial facts and produce source-located diagnostics.
Optional real tests use `PHANTOM_DATASET` or the local reference checkout;
they never open sample outputs. Next: semantic SQL and historical identities.

## Stage 03: SQL evidence

```python
# phantom.sql_evidence; shares EvidenceBatch/EvidenceDiagnostic with JSON adapter
normalize_sql(dump: SqlDump, source: EvidenceRef) -> EvidenceBatch
read_sql_evidence(entry: ManifestEntry) -> EvidenceBatch
```

Verified all 24 dumps: all 969 rows have an explicit role (891 record facts and
78 scope declarations), producing 268 per-table/collection scopes; zero diagnostics
and unchanged input hashes. All 196 tests passed with no skips.
Store IDs are exact physical SQL table names, not filenames or logical services.
Capture timestamps come from declared system scope, capture header or explicit
snapshot-completed footer, never from a stale row update/index timestamp.
Embedded JSON rejects duplicate keys/nonfinite values and conflicting IDs.
Only explicit unambiguous `source_resource_id` links produce references.

Completeness convention: the supplied scope table has system and time, not an
exhaustiveness flag. Its scopes, and point-in-time header-only captures, remain
partial. Empty tables therefore do not silently prove global/person-wide absence.
The adapter supports a future explicit `complete=1` declaration for exact domain
scopes, with malformed rows downgrading completeness. This is exercised only by
independent synthetic fixtures; no supplied scope was upgraded heuristically.
Next: request authorization and source-grounded historical identity reconstruction.

## Stage 04: authorization and historical identifiers

```python
# phantom.intent
IntentDecision(authorization: Literal['REQUESTED', 'NOT_REQUESTED', 'AMBIGUOUS'],
               reason: str, language: str | None = None)
classify_intent(request_text: str) -> IntentDecision
# phantom.identities
IdentitySupport(evidence_id: str, source_path: str, locator: str, available_at: str)
IdentityVersion(user_id: str, field: str, value: str, effective_from: str | None,
                effective_until: str | None, source: IdentitySupport,
                observation_only: bool = False)
IdentityResolution(status: str, user_ids: tuple[str, ...], identifiers: IdentifierMap,
                   current: dict[str, str], versions: tuple[IdentityVersion, ...],
                   supports: tuple[IdentitySupport, ...], issues: tuple[str, ...])
normalize_identifier(field: str, value: str) -> str
IdentityIndex(identities: Sequence[dict[str, str]], audits: Sequence[dict[str, str]],
              facts: Sequence[EvidenceFact] = ())
IdentityIndex.resolve(email: str, audit_at: str, *,
                      requested_at: str | None = None) -> IdentityResolution
```

Intent uses language-level explicit consent patterns for EN/FR/ES/PT/JA/KO,
quote/negation/withdrawal/information checks, and AMBIGUOUS for unclear consent.
No status claim or participant ID is used. The later model stage may assist
ambiguous requests; no runtime model calls were added in these four stages.

Identity resolution keeps exact original aliases, source-supported transition
intervals, unknown interval starts and current fields separately. Future audit,
current-row and capture evidence is excluded at each cutoff. Current CSV row
supports have empty evidence IDs rather than fabricated output citations.
Visible audit IDs and file IDs are retained. Explicit user IDs and direct fields
in the same stored object can establish edges; names, query-only values, adjacent
records and numeric suffixes cannot. Current values are not backdated before
their observation. Inconsistent audit chains and same-time field conflicts are
diagnosed; ambiguous owners are never unioned. Ownership windows can resolve
explicit email reuse at requested_at using only evidence visible by audit_at.
Secondary-copy values are observation-only aliases: they cannot overwrite the
canonical CSV/audit-derived current identity or establish continuous ownership.
Conflicts between copies are diagnosed separately from canonical identity conflicts.

Real structured-source integration covers all 518 request identity lookups.
Intent distribution: 508 explicit erasure requests (446 EN, 14 PT, 12 each
FR/ES/JA/KO), 10 non-deletion requests, zero ambiguous texts in this supplied set.
All 518 obtain a unique supported subject link; 100 use a historical email alias.
One same-time email field conflict across SQL stores remains explicitly flagged,
without replacing the canonical current email. This is coverage, NOT verdict accuracy.
Index construction plus all 518 identity resolutions measured 0.256 seconds on
this machine; it is NOT full audit runtime or a promised benchmark elsewhere.

All 221 tests pass, including optional real integrations. No sample answer files
were loaded. Next is Prompt 05, temporal resource linking/reconstruction; policy,
planner, verifier, CLI and complete log/media interpretation remain unimplemented.

## Stage 05: per-store temporal state

`StateIndex(facts=(), scopes=()).resolve(identity: IdentityResolution, audit_at: str) -> StateView` indexes once; `StateView(audit_at, identity, resources, issues)` contains `ResourceState(entity, category, state, latest, fields, history, claims, issues)` and provenance-bearing `FieldObservation(field, value, fact)`. Physical stores stay distinct; both timestamps gate evidence; explicit references and historical identity preserve links. Claims cannot override readbacks. Partial fields retain earlier observations without manufacturing a merged record. Explicit full-record metadata and complete exact scopes can supersede prior fields/absence. Conflicting current captures remain unresolved.

237 tests pass. All 518 structured request views reconstructed (1,009 relevant physical resources) in 1.483 seconds excluding preparation; zero current state conflicts. This is adapter/state coverage, not verdict accuracy or full runtime. Policy/planner/logs/media remain pending.

## Stage 06: policy and recreation obligations

`PolicyEngine(rows).evaluate(view: StateView) -> tuple[Obligation, ...]`; `.for_resource(resource, view)` is a focused primitive. `PolicyRule(rule_id, category, condition, action)` comes from the CSV. `Obligation(entity, rule_id, treatment, compliant: bool | None, finding, remediation, verification, proofs, reasons)` separates supported failures from uncertainty. `exposed_fields(resource, view, *, allow_stable=False, email_only=False)` retains prohibited-value provenance.

All 15 CSV categories/conditions are supported; unknown conditions fail explicitly. Retention expiry supplies no invented deletion authority. Security/legal retention requires cutoff-valid support; direct PII gets redaction plus presence checks. Stable tombstone references must actually be an internal subject ID. Analytics cannot retain linkable stable IDs/hashes. Restore and flow obligations are independent. Anonymous generic objects do not prove removed omitted fields. State selector handling now covers schema-declared lookup_value, subject_selector and uploaded_by without treating query echoes as stored PII.

252 tests pass. Real structured integration evaluated 1,126 obligations: 619 supported failures, 189 supported compliant, 318 unknown. Reasons include 272 missing retention-condition supports and 34 unproven risk states (remaining unknowns are insufficient field coverage). These are obligation counts, not case verdict accuracy; logs and media are still pending.
