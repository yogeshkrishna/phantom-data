Ghost Data Still Exists — evaluation report, 25 October 2026

This report describes the selected Version 3 implementation. The 32 public samples were repeatedly inspected during development; they are not a held-out test set. No canonical labels were supplied for the 518 submission requests, so their prediction accuracy is unknown. No higher hidden score or rank is promised.

**Architecture and evidence**

The deterministic workflow resolves exact current/historical identifiers, discovers structured and document records, reconstructs state at audit_at, applies deletion_rules.csv, and emits ordered findings, remediation and verification. The global activity stream is a linkage/operational clue rather than automatic proof that an operation succeeded. Policy-required records and permitted account-tombstone references are preserved. Parser provenance and confidence diagnostics are retained outside the submitted CSV.

The verified external participant corpus has 94 evidence files: 26 logs, 28 JSON files, one plain-text file, 11 screenshots, 24 SQL snapshots and four PDFs. All 94 were processed successfully, producing 2,893 structured observations, 458 scopes and 15 extracted documents. SQL hardening independently preserved all 891 SQL observations and 258 SQL scopes. Successful parsing is coverage evidence, not proof of complete discovery or interpretation.

**Measured predictions**

| Metric | Version 2 baseline | Version 3 selected |
|---|---:|---:|
| Status accuracy | 32/32 = 100% | 32/32 = 100% |
| Full ordered-case accuracy | 28/32 = 87.5% | 28/32 = 87.5% |
| Exact sequence accuracy | 28/32 = 87.5% | 28/32 = 87.5% |
| Step precision | 100.0000% | 100.0000% |
| Step recall | 97.9275% | 97.9275% |
| Step F1 | 98.9529% | 98.9529% |
| Positional field agreement | 95.7809% | 95.7809% |
| Mean sequence LCS | 97.4330% | 97.4330% |
| Matched / predicted / reference steps | 189 / 189 / 193 | 189 / 189 / 193 |
| Submission rows / steps | 518 / 3,127 | 518 / 3,127 |
| Full rows / statuses / citations changed | — | 0 / 0 / 0 |
| Full statuses | 355 INCOMPLETE;153 COMPLETE;10 NOT_APPLICABLE | Identical |

Selected output.csv SHA-256: `dd65f8801c9ab5dddc6a7c40a58d87138a503d77e166b95ad81e379d611c889c`. Full source fresh/warm runs and baseline outputs are byte-identical. Baseline sample SHA-256: `3806fe511456105f8247f38fb82edf64226f3de0b27de408a02f729ccc104e0b`; the selected sample matches it too. Main outputs pass independent structural, input-coverage, operation-order and citation-ID/time validation.

**Metric integrity**

Invalid predictions are rejected before scoring: duplicate, missing or extra case IDs; malformed CSV/JSON; illegal operation/state/phase pairs; missing action/check counterparts; wrong ordering and step numbering. Exact-case equality compares status and all seven ordered step fields. Multiset step precision/recall/F1 compare the other six fields; sequence accuracy and LCS compare ordered phase/operation/system/resource tuples. Positional field agreement compares seven fields across max(predicted length, reference length) positions. Counts and denominators accompany metrics. Object-key order and JSON whitespace do not affect semantic equality.

The old scorer could award exact accuracy to step_no=999 and extra rows, overwrite duplicate IDs, and hide duplicate multiplicities in diagnostics. These defects are repaired. Correcting the scorer does not change the well-formed baseline sample metrics and is not counted as a prediction improvement. Known blank COMPLETE citations in public references are accepted only when loading references, not as permission for missing prediction evidence.

**Implementation checks**

The final source test run passed 139 tests with zero skips, including the two external-corpus regressions. There are 14 inherited hand-authored filesystem evaluation cases across three suites, all exact, and 40 newly added semantic counterexample/control tests. Fault-injection tests cover output serialization, contract failure, fsync/replace failure, absent reports, failed ZIP construction, forbidden output destinations, corrupted/stale/empty OCR caches, unsafe SQL and execution-budget exhaustion.

| Reproduced Version 2 failure | Version 3 result |
|---|---|
| Serialization truncated an existing CSV | Complete serialization, independent validation, staged readback and atomic replacement preserve prior bytes on tested failures |
| Missing report left a partial archive | Required files preflight before staging; ZIP members/readback verified; publication atomic |
| Synthetic policy excluded from archive | Independent policy shipped and fixture generator can rebuild without corpus |
| SQL ATTACH wrote outside memory; recursive SQL unbounded | Restricted authorizer/extensions and source/page/step/time budgets; failures discard partial observations |
| Empty or altered OCR cache trusted | Full source/engine/model/config identity plus text checksum; invalid entries regenerate; writes atomic |
| Missing OCR still exported successfully | Default release blocks missing/partial/failed evidence and preserves prior CSV; exploratory bypass explicitly labelled |
| Caller-selected paths could overwrite corpus/source | Prediction/telemetry/cache/ZIP paths guarded; unsafe paths rejected in tests |
| Nonexistent resource passed structural validation | Separate advisory evidence checker catches mutated resource/citation/state/owner/retention claims |

The independent checker shares extraction but imports none of the planner, state evaluator or policy decision code. Its separate checks inspect latest cited facts, source membership, direct ownership, temporal eligibility and policy-operation compatibility. On the selected output it reports 0 detected errors and 1071 review notes. The notes cover weak ownership, incomplete field projections, scope exhaustiveness and retrospective capture assumptions; they are not certified proof. Checks remain advisory rather than silently changing the CSV. Deliberate mutations and correct controls demonstrate bounded detection capability, not a complete independent proof of deletion.

**Semantic candidates and promotion**

Candidates were evaluated individually, then the selected combination was evaluated. Seven were promoted because their counterexamples and neighboring controls pass and their combined current sample/full outputs are identical: marker_content, reference_fields, temporal_categories, citation_continuity, intent_scope, completion_proof, fractional_timestamps. They fix visible-content precedence, reference synonyms/message excerpts, future category leakage, stale citations, scoped authorization, vacuous completion and fractional cutoffs. Contradictory zero-count/stored-content observations now remain unknown until later appropriate proof; selectors remain separate. Hypothetical/marketing-only requests do not become authorized merely by containing deletion vocabulary, and actual deletion can coexist with retention or explanation side requests.

Five candidates remain experimental, require a separate CLI output path and are excluded from the selected default. The table reports measured effects, not improvements:

| Candidate | Sample exact cases / F1 | Full rows changed | Status changes | Decision |
|---|---|---:|---:|---|
| projection_completeness | 21/32 / 86.5435% | 125 | 85 | Deferred |
| independent_copies | Not scored: invalid empty proof row | 110 | 33 | Deferred |
| snapshot_coverage | Not scored: invalid empty proof row | 68 | 30 | Deferred |
| temporal_identity | 21/32 / 95.4667% | 126 | 0 | Deferred |
| document_rows | 27/32 / 97.6253% | 3 | 1 | Deferred |

Verified clean-environment archive rehearsal: a fresh Python environment installed the extracted pinned lock file; pip check reported no broken requirements. The extracted engine processed all 94 evidence files in both fresh- and warm-cache runs, reproduced all 518 rows byte-for-byte, passed all 139 tests with zero skips, passed all 14 custom cases exactly, and reproduced the sample bytes/metrics. Extracted full-run wall times were 51.371 seconds cold and 3.311 seconds warm. The cold run made 11 local OCR invocations; the warm run used 11 validated cache hits and no inference. README and Markdown reports were finalized after that rehearsal; runtime source bytes are checked against the rehearsed archive.

Projection completeness lacks explicit field-coverage metadata; physical-copy aggregation lacks compatible copy identities and common proof; snapshot omission lacks exhaustiveness declarations; temporal ownership loses known weak retrospective links; row-local document parsing changes a visible case and introduces errors. No rejected behavior was silently made default. Their per-case before/after steps, sources, decision traces and diagnostic errors are retained in the local review artifacts outside code.zip. The selected default has no changed full rows to justify individually.

**Independent custom cases and controls**

The expected behavior is specified from synthetic facts and policy, not generated from production predictions. These cases do not use participant answers or identifier-number shortcuts.

1. Historical identifier and decoy: a subject changed email; an export carries the old exact email while a similar address belongs to another person. Expected: include the subject export, exclude the decoy, and output finding→delete→absence verification. The inherited historical fixture passes exactly; identifier-renaming controls preserve this relation.
2. Independent replica: a primary read is absent while a separately named replica remains present. Expected: deletion remains incomplete and the replica requires treatment; later proven cleanup of that same copy resolves it. The old same-time filesystem fixture passes; newly staggered-copy controls pass under the deferred independent-copies candidate. This does not justify promoting a candidate that fails corpus gates.
3. Mixed intent: 'Please delete my account, but do not delete invoices that policy requires you to retain' authorizes deletion within policy; unsubscribe-only, access-only and hypothetical requests do not. Deletion plus an explanation request remains authorized. New intent tests pass under the selected default, while the Spanish non-deletion filesystem fixture remains NOT_APPLICABLE with [].
4. Claimed anonymization versus contents: an ANONYMIZED record still contains an email or stable attribution. Expected: prohibited contents prevent clean proof. A contradictory zero-row response with stored PII is unknown; an actual empty query with only a lookup selector remains absent. A permitted tombstone user reference is retained while direct PII is removed.
5. Ordered process remediation: a tombstone contains PII, a connector remains active and a backup is restorable. Expected: findings first, then disable connector/block restore before record treatment, then matching checks. The inherited synthetic ordered-plan case passes exactly.
6. Genuine tie and missing proof: two unattributed accounts share a request window. Expected: do not invent ownership; known noncompliant tie candidates prevent false COMPLETE. The two ambiguity and two guard cases pass exactly. A wholly unsupported authorized case remains internally unknown/INCOMPLETE; the exporter refuses an unrepresentable empty plan instead of fabricating resources.
7. Temporal boundaries and evidence continuity: a future attachment category must not change earlier support-message policy; an old clean citation cannot cross a later exposure; subsecond evidence before/at/after cutoff is distinct. New tests cover these and timezone equivalence. Fractional precision is limited to Python datetime microseconds.
8. Document row independence: an unrelated person's email cannot expose a clean target row, message prose containing 'not found' is not an absence result, and two resources sharing a line must not share owners/results. These controls pass within the deferred document candidate; its measured sample regression keeps it out of the default.

Other metamorphic controls reorder inputs/rows, consistently rename synthetic identifiers, shift all fixture timestamps equally, add unrelated people and add future-only evidence. Conditional retention/hold expiration, flow re-enable, restore contradictions and missing retained records have explicit controls. Tests reject unsupported transformations rather than claiming invariance when evidence genuinely changes.

**Remaining limits and visible mismatches**

Four public samples each omit one expected verification: case_04 account_004; case_18 audit_record_6645; case_20 audit_record_5569; case_23 account_410. The first three exact resource IDs were not found in inspected participant structured evidence, activity, PDF extraction or fresh OCR text. The fourth has an unresolved ownership tie; activity results do not prove absence. Some expected COMPLETE steps also have blank evidence. No IDs, labels or fabricated citations were inserted to reproduce these answers. An upstream participant issue reports related inconsistencies; that is not organizer confirmation of hidden truth.

The current implementation retains documented assumptions: 215 output steps on weak links across 203 requests; 140 states based on omission plus one omission override; 178 resource states depending only on exports captured after the audit cutoff, including resources in 11 COMPLETE cases. These are review counts, not known error counts. Strict whole-file cutoff and removing weak links reduced visible accuracy, so broad switches were not promoted. Unknown policy categories still inherit the legacy delete fallback; preserved input policies and release checks reject unknown action vocabulary, but arbitrary unseen categories remain a limitation. Missing retained records have no allowed recovery operation in the published output schema; they cannot be repaired by inventing an operation or deleting required data. The checker cannot certify complete discovery or temporal ownership. Linux and live optional paid-model execution were not tested.

**Reproducibility and artifacts**

Run the README commands with the external corpus and pinned dependencies. Set a fresh external GHOST_AUDIT_CACHE_DIR for cold OCR; repeat for warm validation. Use the standalone output validator, full tests with GHOST_AUDIT_TEST_DATA_DIR and custom evaluator. Build code.zip, extract it into a clean directory, install its pinned lock file into a fresh environment, then reproduce output and rerun tests/custom cases. The local implementation report records extraction results, archive/source/output hashes and preservation checks. Required Markdown reports are included under code/evaluation and mirrored under evaluation for compatibility. Corpus, caches, environments, generated runs, planning notes, transcript and nested archives are excluded.

The final transcript is a separate authentic development artifact. Historical missing metadata or prompt placeholders remain explicitly disclosed rather than invented. No submission, push or upload was performed.
