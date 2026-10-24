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

## Stage 07: planner, independent verifier and first audit

`plan(case_id, intent: IntentDecision, obligations: tuple[Obligation, ...], *, issues=()) -> AuditResult`; `AuditBlocked(ValueError)` marks unrepresentable/unproven results rather than inventing a status. `verify_result(result, request, identity_index, state_index, policy_rows, *, coverage_issues=()) -> VerificationReport(valid, issues)` independently re-resolves identity/state and derives required treatment/risk checks without planner/PolicyEngine compliance flags. It checks exact required steps, citations, cutoff, relevant resources, retention and physical-store coverage.

`prepare(dataset_dir: Path) -> PreparedAudit(tables, identities, states, policy, diagnostics, files_attempted, files_supported)`; `audit_request(prepared, request) -> AuditResult` invokes verifier before returning. `python code/main.py --dataset PATH --case-id ID --output PATH` supports repeated case IDs. Missing proof fails before output replacement. COMPLETE requires shared citation support across collapsed physical-store checks.

263 tests pass. Three synthetic end-to-end statuses and adversarial mutations pass. First evidence-supported sample, selected before opening expected labels, was case_05: all three steps/status/citation exactly match its development answer. Details and limits in evaluation/first_audit.md. Real CLI smoke case_54 writes 13 verified steps. case_33 correctly blocks without relevant structured proof. Preparation 0.451 seconds, 52/94 files interpreted; this is not full audit accuracy or final runtime. Logs/text/activity next, media later.

## Stage 08: logs, handover text and activity integration

`normalize_log(text: str, source: EvidenceRef) -> EvidenceBatch`; `read_log_evidence(entry) -> EvidenceBatch`; `parse_log_line(line) -> tuple[str, dict]`. All six supplied envelope families plus diagnostic records and multiline JSON have explicit handling. Trace association is file-local and scoped to service/instance/pod/region/trace, event family, time direction and any explicit resource ID. Repeated starts remain ambiguous. Unknown or malformed records have diagnostics and physical line/range locators. Query echoes are not stored PII. Positive IDENTIFIABLE/detector readbacks support exposure without manufacturing a payload.

`normalize_text(text: str, source: EvidenceRef) -> EvidenceBatch`; `read_text_evidence(entry) -> EvidenceBatch`. The authored handover rows preserve timestamps, category, explicit result and lookup separately; unknown text is diagnostic data, never instructions.

`normalize_activity(rows, source_sha256: str, existing_facts=()) -> EvidenceBatch`. All 4,011 rows preserve activity IDs, event timestamps, locators, source hash and event details. There are 3,882 claims, 117 explicit risk checker readbacks and 12 catalog identity statements. Catalog alternate document references and exclusive attachment ownership survive in metadata. Existing category inference is cutoff-gated; no numeric ID joins or backdating from future evidence. Identity statements add observation-only aliases, never canonical identity transitions.

Preparation now dispatches all supported text formats and activity. Same logical resource IDs connect physical-store obligations without merging their fields. Store-unspecified operation claims are attached as logical context, not invented extra copies; writes at/after an old readback require fresh proof. An indexed reference graph replaces whole-corpus scans per subject. Resource reassignment blocks destructive planning. Archive presence must be independently supported alongside restore blocking. The verifier independently enforces these boundaries and uncertain identity histories.

291 tests pass (zero skips). All 26 logs plus one text file: 2,255 events accounted for, 1,111 facts, zero diagnostics. All 79 currently supported manifest files parse; 15 unsupported files remain (11 PNG, four PDF). All 94 input hashes unchanged. Final interim all-518 attempt: 376 verified INCOMPLETE, 10 NOT_APPLICABLE, two COMPLETE, 130 blocked with UNPROVEN_COMPLETION_WITHOUT_SUPPORTED_FINDING. These are execution counts, not accuracy. No 518-row final output was manufactured. Coverage details are in evaluation/evidence_coverage.md.

Measured prepared execution was 1.124 seconds after 1.309 seconds of preparation; this excludes pending media/model preparation and is not final-pipeline runtime. Real CLI subprocess smoke succeeded with exit 0, unknown-case failure returned exit 2 and preserved existing output. Production has no sample-answer lookup. No paid APIs/model inference were added. Native session turn_context confirms current coding model gpt-6.1-sol. Next prerequisite is stage 09 local media/model extraction, followed by broader CLI/caching, evaluation and submission stages.

## Stage 09: actual local neural OCR and PDFs

`LocalOCR(cache_dir: Path | None=None).read(image) -> tuple[TextSpan, ...]`, `identity()`, `usage()`; TextSpan(text, confidence, box, page=1). OCR cache keys contain image bytes/shape, actual model hashes, runtime/package/config and adapter version. `read_media_evidence(entry, ocr, context=()) -> EvidenceBatch`; labelled screen regions and PDF text-layer records feed existing facts. Explicit catalog aliases preserve binding evidence and later availability. No filename/case answer mapping. `prepare(dataset_dir, *, media_cache=None)` now interprets media; PreparedAudit adds optional model_usage.

Hardware verified: 16 GB RAM, GTX1650 4 GB, approximately23 GB free disk. Installed pinned RapidOCR3.10.0, ONNXRuntime1.31.0, pypdf6.21.2 and PDFium5.14.0. CPU OCR uses bundled PP-OCRv6 detector/recognizer and mobile orientation model; exact model SHA256s are reported by usage(). Official install/model/license references recorded in prompts/media_extraction.md. First actual preparation made11 neural OCR calls, measured52.9248s inference; repeated preparation reused11 source/model-addressed entries and made0 new calls. Tokens are not applicable; no paid API calls.

All11 screenshots and all7 pages of4 PDFs visually inspected against extraction. All15 media files attempted,40 grounded media facts, zero layout diagnostics. Low-confidence unrendered values remain omitted with region provenance; absence of displayed fields is not sanitization proof. All94 manifest hashes unchanged. 298 tests pass. Some audits remain unproven; full evaluation is stage11, not inferred from media coverage. Next: stage10 compiler/CLI/cache.

## Stage 10: compiler, CLI and safe cache

`compile_corpus(dataset_dir, *, cache_dir=None, media_cache=None) -> CompiledCorpus(tables, facts, scopes, diagnostics, inventory, model_usage, cache_key, cache_hit)`; `decode(payload)` reconstructs explicit typed values. All files retain layout/coverage/error entries; parser failures remain diagnostics through cache reuse. Keys contain all source hashes, manifest IDs/paths/origins/availability, core CSV bytes, every production module hash, model hashes/config, Python/SQLite/PDF runtime versions. No labels or verdicts are cached. `JsonCache(directory).load(key)/store(key,data)` uses bounded validated JSON envelopes, content checksum, exclusive sibling temps, fsync and atomic replacement. Windows concurrent destination locks have bounded retries after a regression test exposed the race.

`prepare(..., cache_dir=None)` builds indexes from compiled facts; PreparedAudit adds inventory/cache_key/cache_hit. CLI prepare/check/audit commands and original stage07 invocation work; evaluate explicitly returns3 until stage11. README contains setup/run commands. Failed audit never overwrites output.

305 tests pass. Synthetic filesystem fixtures prove source-byte/availability invalidation, corrupt/stale/partial cache rebuild, concurrent writes, source-error inventory preservation and operation without sample answers. Real prepare:94 files,0 diagnostics,3.484s with warm OCR/cold compiler. Real check:compiled hit,1.609s. Real selected audit returns0 and verified CSV. These exclude cold neural OCR preparation. Next: stage11 evaluation.
