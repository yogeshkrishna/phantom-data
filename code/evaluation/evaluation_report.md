# Deletion audit evaluation

Measured on the supplied visible development samples. These are not held-out or hidden-test scores; the HackerRank aggregate formula is unknown.

Predictions were attempted through the production pipeline before reading sample_output.csv. Blocked requests count as missing predictions and reduce coverage/accuracy.

| Metric | Value |
|---|---|
| expected_cases | 32 |
| predicted_cases | 28 |
| status_accuracy | 0.875000 |
| case_exact_accuracy | 0.218750 |
| sequence_accuracy | 0.218750 |
| expected_steps | 193 |
| predicted_steps | 167 |
| matched_steps | 155 |
| step_precision | 0.928144 |
| step_recall | 0.803109 |
| step_f1 | 0.861111 |

Positional field accuracy (unmatched/missing/extra positions are penalized):

| Field | Accuracy |
|---|---|
| step_no | 0.809045 |
| phase | 0.793970 |
| system_id | 0.572864 |
| resource_id | 0.572864 |
| operation | 0.628141 |
| state | 0.638191 |
| evidence_id | 0.788945 |

Missing cases: case_01, case_02, case_04, case_31. Extra cases: none.

Evidence: 94/94 files interpreted, 0 diagnostics. Preparation 13.039s; prepared sample attempts 0.259s; compiler cache hit=False.

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
| case_02 | COMPLETE / BLOCKED | 1 / 0 | NO_RELEVANT_RESOURCE_PROOF |
| case_03 | COMPLETE / COMPLETE | 3 / 2 | STEP_OR_CITATION_MISMATCH |
| case_04 | COMPLETE / BLOCKED | 2 / 0 | NO_RELEVANT_RESOURCE_PROOF |
| case_06 | INCOMPLETE / INCOMPLETE | 4 / 3 | STEP_OR_CITATION_MISMATCH |
| case_07 | INCOMPLETE / INCOMPLETE | 4 / 6 | STEP_OR_CITATION_MISMATCH |
| case_08 | INCOMPLETE / INCOMPLETE | 4 / 4 | STEP_OR_CITATION_MISMATCH |
| case_09 | INCOMPLETE / INCOMPLETE | 5 / 3 | STEP_OR_CITATION_MISMATCH |
| case_10 | INCOMPLETE / INCOMPLETE | 4 / 3 | STEP_OR_CITATION_MISMATCH |
| case_12 | INCOMPLETE / INCOMPLETE | 5 / 6 | STEP_OR_CITATION_MISMATCH |
| case_17 | INCOMPLETE / INCOMPLETE | 5 / 8 | STEP_OR_CITATION_MISMATCH |
| case_18 | INCOMPLETE / INCOMPLETE | 14 / 12 | STEP_OR_CITATION_MISMATCH |
| case_20 | INCOMPLETE / INCOMPLETE | 8 / 6 | STEP_OR_CITATION_MISMATCH |
| case_21 | COMPLETE / COMPLETE | 3 / 2 | STEP_OR_CITATION_MISMATCH |
| case_22 | INCOMPLETE / INCOMPLETE | 8 / 7 | STEP_OR_CITATION_MISMATCH |
| case_23 | INCOMPLETE / INCOMPLETE | 8 / 6 | STEP_OR_CITATION_MISMATCH |
| case_24 | INCOMPLETE / INCOMPLETE | 7 / 7 | STEP_OR_CITATION_MISMATCH |
| case_25 | COMPLETE / COMPLETE | 3 / 1 | STEP_OR_CITATION_MISMATCH |
| case_26 | COMPLETE / COMPLETE | 6 / 4 | STEP_OR_CITATION_MISMATCH |
| case_27 | COMPLETE / COMPLETE | 6 / 4 | STEP_OR_CITATION_MISMATCH |
| case_28 | COMPLETE / COMPLETE | 6 / 4 | STEP_OR_CITATION_MISMATCH |
| case_29 | INCOMPLETE / INCOMPLETE | 14 / 13 | STEP_OR_CITATION_MISMATCH |
| case_30 | INCOMPLETE / INCOMPLETE | 14 / 12 | STEP_OR_CITATION_MISMATCH |
| case_31 | COMPLETE / BLOCKED | 3 / 0 | UNPROVEN_COMPLETION_WITHOUT_SUPPORTED_FINDING |
| case_32 | INCOMPLETE / INCOMPLETE | 5 / 4 | STEP_OR_CITATION_MISMATCH |

### Visible source traces for resource-set disagreements

These evaluation-only lookups use expected resource IDs solely to diagnose missing/extra output after prediction. They never create production identity edges. A visible readback with no subject selector or supported history cannot be assigned by numeric suffix or event proximity.

| Case | Resource | Difference | Latest visible sources |
|---|---|---|---|
| case_01 | account_service/account_001 | missing expected subject link | activity_3342:/rows/3341 (claim/SUCCESS) |
| case_02 | account_service/account_002 | missing expected subject link | activity_3376:/rows/3375 (claim/PARTIAL) |
| case_03 | analytics_platform/analytics_9406 | missing expected subject link | evidence_94:/lines/42../lines/43 (state/PRESENT); activity_3852:/rows/3851 (claim/SUCCESS) |
| case_04 | account_service/account_004 | missing expected subject link | No visible normalized record for this resource |
| case_04 | analytics_platform/analytics_901 | missing expected subject link | evidence_56:/records/0 (state/PRESENT); activity_3463:/rows/3462 (claim/SUCCESS) |
| case_06 | account_service/account_006 | missing expected subject link | activity_3545:/rows/3544 (claim/SUCCESS) |
| case_07 | account_service/account_007 | missing expected subject link | activity_3590:/rows/3589 (claim/SUCCESS) |
| case_07 | object_storage/object_19 | additional supported resource | activity_4007:/rows/4006 (identity/LINKED); evidence_46:/image/region/table/row/1 (state/PRESENT) |
| case_08 | account_service/account_008 | missing expected subject link | activity_3554:/rows/3553 (claim/SUCCESS) |
| case_08 | legal_service/ORD-20264118 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_09 | account_service/account_3960 | missing expected subject link | activity_3703:/rows/3702 (claim/FAILED) |
| case_09 | security_service/audit_record_4902 | missing expected subject link | No visible normalized record for this resource |
| case_10 | account_service/account_010 | missing expected subject link | activity_3595:/rows/3594 (claim/SUCCESS) |
| case_12 | legal_service/ORD-20264207 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_17 | account_service/account_6523 | additional supported resource | activity_3850:/rows/3849 (claim/SUCCESS); evidence_67:/lines/2../lines/3 (state/ABSENT) |
| case_17 | identity_service/tombstone_17 | additional supported resource | activity_4011:/rows/4010 (identity/LINKED); evidence_42:/image/region/reference-header (state/PRESENT) |
| case_17 | legal_service/ORD-20264319 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_18 | account_service/account_5521 | missing expected subject link | activity_3732:/rows/3731 (claim/SUCCESS) |
| case_18 | security_service/audit_record_6645 | missing expected subject link | No visible normalized record for this resource |
| case_20 | account_service/account_5037 | missing expected subject link | activity_3734:/rows/3733 (claim/FAILED) |
| case_20 | security_service/audit_record_5569 | missing expected subject link | No visible normalized record for this resource |
| case_21 | account_service/account_6440 | missing expected subject link | evidence_92:/service_exports/account_service/profile_lookups/5 (state/ABSENT); activity_3661:/rows/3660 (claim/SUCCESS) |
| case_21 | analytics_platform/analytics_5335 | missing expected subject link | evidence_92:/service_exports/analytics_platform/profiles/5 (state/PRESENT); activity_3665:/rows/3664 (claim/SUCCESS) |
| case_21 | legal_service/ORD-20264402 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_22 | account_service/account_4990 | missing expected subject link | activity_3573:/rows/3572 (claim/SUCCESS) |
| case_23 | account_service/account_410 | missing expected subject link | activity_3662:/rows/3661 (claim/FAILED) |
| case_23 | security_service/audit_record_4128 | missing expected subject link | No visible normalized record for this resource |
| case_24 | legal_service/ORD-20264511 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_24 | security_service/audit_record_7095 | missing expected subject link | No visible normalized record for this resource |
| case_25 | account_service/account_3136 | missing expected subject link | evidence_92:/service_exports/account_service/profile_lookups/0 (state/ABSENT); activity_3597:/rows/3596 (claim/FAILED) |
| case_25 | analytics_platform/analytics_1895 | missing expected subject link | evidence_92:/service_exports/analytics_platform/profiles/0 (state/PRESENT); activity_3602:/rows/3601 (claim/SUCCESS) |
| case_26 | account_service/account_5684 | missing expected subject link | evidence_92:/service_exports/account_service/profile_lookups/4 (state/ABSENT); activity_3600:/rows/3599 (claim/FAILED) |
| case_26 | analytics_platform/analytics_4566 | missing expected subject link | evidence_92:/service_exports/analytics_platform/profiles/2 (state/PRESENT); activity_3603:/rows/3602 (claim/SUCCESS) |
| case_27 | account_service/account_3866 | missing expected subject link | evidence_92:/service_exports/account_service/profile_lookups/1 (state/ABSENT); activity_3530:/rows/3529 (claim/SUCCESS) |
| case_27 | analytics_platform/analytics_4574 | missing expected subject link | evidence_92:/service_exports/analytics_platform/profiles/3 (state/PRESENT); activity_3533:/rows/3532 (claim/SUCCESS) |
| case_28 | account_service/account_5482 | missing expected subject link | evidence_92:/service_exports/account_service/profile_lookups/3 (state/ABSENT); activity_3529:/rows/3528 (claim/FAILED) |
| case_28 | analytics_platform/analytics_6077 | missing expected subject link | evidence_92:/service_exports/analytics_platform/profiles/6 (state/PRESENT); activity_3534:/rows/3533 (claim/SUCCESS) |
| case_29 | account_service/account_7012 | missing expected subject link | activity_3532:/rows/3531 (claim/FAILED) |
| case_29 | legal_service/ORD-20264600 | additional supported resource | evidence_27:/pages/2 (state/PRESENT) |
| case_29 | security_service/audit_record_3954 | missing expected subject link | No visible normalized record for this resource |
| case_30 | account_service/account_4163 | missing expected subject link | activity_3465:/rows/3464 (claim/SUCCESS) |
| case_30 | security_service/audit_record_1707 | missing expected subject link | No visible normalized record for this resource |
| case_31 | account_service/account_4857 | missing expected subject link | activity_3682:/rows/3681 (claim/SUCCESS) |
| case_31 | identity_service/tombstone_440 | missing expected subject link | evidence_51:/snapshots/1/records/0 (state/PRESENT); activity_3687:/rows/3686 (claim/SUCCESS) |
| case_31 | security_service/audit_record_6909 | missing expected subject link | evidence_51:/snapshots/2/records/0 (state/PRESENT) |
| case_32 | account_service/account_8858 | missing expected subject link | activity_3685:/rows/3684 (claim/PARTIAL) |

## Boundaries and improvements

Local OCR/model-derived evidence uses the same cutoff, scope and policy verifier as structured evidence. Raw extraction/cache data and model weights remain outside the submitted source. No paid API is used.

Regression checks cover added PII, enabled connectors, unavailable future captures, missing retained records and forged citations. Earlier fixes include per-store state, partial-field preservation, query/payload separation and concurrent Windows cache writes.

Stage12 source analysis and remaining ambiguities are recorded in error_analysis.md. No predictions were edited to match labels. A complete 518-row submission has not yet been demonstrated.

## Measured ablations

Same visible32 requests, same expected rows and independent verifier. Deliberately weakened baselines exist only in evaluation/ablation.py. Times include index rebuilding and audits, exclude shared source/OCR preparation.

| Variant | Status accuracy | Exact row | Step F1 | Predictions | Blocked | Seconds |
|---|---|---|---|---|---|---|
| full | 0.875000 | 0.218750 | 0.861111 | 28 | 4 | 2.940 |
| without_historical_identities | 0.750000 | 0.218750 | 0.754601 | 24 | 8 | 1.821 |
| without_replicas | 0.812500 | 0.156250 | 0.831909 | 26 | 6 | 3.305 |
| without_neural_images | 0.875000 | 0.218750 | 0.865922 | 28 | 4 | 3.778 |

The image removal isolates neural screenshot facts; PDF text layers remain. Better or worse sample metrics under a removal do not establish hidden-test benefit. Additional supported records absent from a canonical row can reduce exact-match scores.
