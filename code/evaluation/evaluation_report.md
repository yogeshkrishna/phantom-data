# Deletion audit evaluation

Measured on the supplied visible development samples. These are not held-out or hidden-test scores; the HackerRank aggregate formula is unknown.

Predictions were attempted through the production pipeline before reading sample_output.csv. Blocked requests count as missing predictions and reduce coverage/accuracy.

| Metric | Value |
|---|---|
| expected_cases | 32 |
| predicted_cases | 20 |
| status_accuracy | 0.406250 |
| case_exact_accuracy | 0.125000 |
| sequence_accuracy | 0.125000 |
| expected_steps | 193 |
| predicted_steps | 96 |
| matched_steps | 81 |
| step_precision | 0.843750 |
| step_recall | 0.419689 |
| step_f1 | 0.560554 |

Positional field accuracy (unmatched/missing/extra positions are penalized):

| Field | Accuracy |
|---|---|
| step_no | 0.467005 |
| phase | 0.441624 |
| system_id | 0.350254 |
| resource_id | 0.350254 |
| operation | 0.375635 |
| state | 0.385787 |
| evidence_id | 0.416244 |

Missing cases: case_01, case_02, case_04, case_12, case_15, case_17, case_21, case_24, case_28, case_30, case_31, case_32. Extra cases: none.

Evidence: 94/94 files interpreted, 0 diagnostics. Preparation 2.321s; prepared sample attempts 0.067s; compiler cache hit=True.

## Independent filesystem cases

| Case | Expected status | Exact result | Citation mutation rejected |
|---|---|---|---|
| deletion_then_sync_recreation | INCOMPLETE | PASS | True |
| clean_primary_dirty_replica | INCOMPLETE | PASS | True |
| retained_clean_tombstone | COMPLETE | PASS | True |
| shared_support_personal_attachment | INCOMPLETE | PASS | True |
| future_source_unavailable | INCOMPLETE | PASS | True |
| non_deletion_authorization | NOT_APPLICABLE | PASS | True |
| retention_and_blocked_restore | COMPLETE | PASS | True |

Fixtures construct their own CSV/JSON/SQL sources and explicit expected plans; no production planner generates their labels. Full source/expectations are in fixtures.py.

- deletion_then_sync_recreation: An absence readback is followed by a new profile and enabled connector.
- clean_primary_dirty_replica: The primary is absent; an independently captured SQL replica remains present.
- retained_clean_tombstone: Deleted tombstone keeps only the internal reference; all direct fields are explicitly null.
- shared_support_personal_attachment: Shared conversation is redacted; a personal attachment is removed.
- future_source_unavailable: A clean observation is captured after cutoff and cannot erase the visible profile.
- non_deletion_authorization: An explicit request against deletion authorizes no destructive plan.
- retention_and_blocked_restore: Retained security and archive remain present, with restore disabled.

## Error inventory

| Case | Expected / actual status | Steps expected / actual | Diagnosis |
|---|---|---|---|
| case_01 | COMPLETE / BLOCKED | 1 / 0 | NO_RELEVANT_RESOURCE_PROOF |
| case_02 | COMPLETE / BLOCKED | 1 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_03 | COMPLETE / COMPLETE | 3 / 2 | STEP_OR_CITATION_MISMATCH |
| case_04 | COMPLETE / BLOCKED | 2 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_06 | INCOMPLETE / NOT_APPLICABLE | 4 / 0 | STEP_OR_CITATION_MISMATCH |
| case_07 | INCOMPLETE / INCOMPLETE | 4 / 6 | STEP_OR_CITATION_MISMATCH |
| case_08 | INCOMPLETE / NOT_APPLICABLE | 4 / 0 | STEP_OR_CITATION_MISMATCH |
| case_09 | INCOMPLETE / INCOMPLETE | 5 / 4 | STEP_OR_CITATION_MISMATCH |
| case_10 | INCOMPLETE / NOT_APPLICABLE | 4 / 0 | STEP_OR_CITATION_MISMATCH |
| case_11 | INCOMPLETE / INCOMPLETE | 14 / 14 | STEP_OR_CITATION_MISMATCH |
| case_12 | INCOMPLETE / BLOCKED | 5 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_14 | INCOMPLETE / INCOMPLETE | 5 / 6 | STEP_OR_CITATION_MISMATCH |
| case_15 | INCOMPLETE / BLOCKED | 5 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_17 | INCOMPLETE / BLOCKED | 5 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_18 | INCOMPLETE / INCOMPLETE | 14 / 12 | STEP_OR_CITATION_MISMATCH |
| case_20 | INCOMPLETE / INCOMPLETE | 8 / 6 | STEP_OR_CITATION_MISMATCH |
| case_21 | COMPLETE / BLOCKED | 3 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_22 | INCOMPLETE / INCOMPLETE | 8 / 7 | STEP_OR_CITATION_MISMATCH |
| case_23 | INCOMPLETE / INCOMPLETE | 8 / 6 | STEP_OR_CITATION_MISMATCH |
| case_24 | INCOMPLETE / BLOCKED | 7 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_25 | COMPLETE / NOT_APPLICABLE | 3 / 0 | STEP_OR_CITATION_MISMATCH |
| case_26 | COMPLETE / NOT_APPLICABLE | 6 / 0 | STEP_OR_CITATION_MISMATCH |
| case_27 | COMPLETE / INCOMPLETE | 6 / 7 | STEP_OR_CITATION_MISMATCH |
| case_28 | COMPLETE / BLOCKED | 6 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_29 | INCOMPLETE / NOT_APPLICABLE | 14 / 0 | STEP_OR_CITATION_MISMATCH |
| case_30 | INCOMPLETE / BLOCKED | 14 / 0 | AMBIGUOUS_AUTHORIZATION |
| case_31 | COMPLETE / BLOCKED | 3 / 0 | UNPROVEN_COMPLETION_WITHOUT_SUPPORTED_FINDING |
| case_32 | INCOMPLETE / BLOCKED | 5 / 0 | AMBIGUOUS_AUTHORIZATION |

## Boundaries and improvements

Local OCR/model-derived evidence uses the same cutoff, scope and policy verifier as structured evidence. Raw extraction/cache data and model weights remain outside the submitted source. No paid API is used.

Regression checks cover added PII, enabled connectors, unavailable future captures, missing retained records and forged citations. Earlier fixes include per-store state, partial-field preservation, query/payload separation and concurrent Windows cache writes.

Remaining mismatches require source-level error analysis in stage12. No predictions were edited to match labels. A complete 518-row submission has not yet been demonstrated.
