# Exact-match investigation: all 25 original mismatches

Verified starting point:333 tests pass; exact7/32,status28/32,stepF1=0.861111; full457 supported,61 blocked. Current exact8/32; case_06 resolved. All remaining membership differences persist. No independent ordering/formatting defect was found: positional step_no/field differences cascade from inserted/missing steps. Categories overlap.

## Ranked categories after source re-audit

| Category | Samples affected | IDs |
|---|---|---|
| MISSING_PRIMARY_LINK_AND_SCOPE | 14 | case_01, case_02, case_04, case_07, case_08, case_10, case_18, case_20, case_22, case_23, case_29, case_30, case_31, case_32 |
| ADDITIONAL_SUPPORTED_RECORD | 7 | case_07, case_08, case_12, case_17, case_21, case_24, case_29 |
| MISSING_SUBJECT_LINK | 7 | case_03, case_04, case_21, case_25, case_26, case_27, case_28 |
| POST_CUTOFF_EVIDENCE | 5 | case_09, case_23, case_24, case_29, case_30 |
| BLOCKED_STATUS | 4 | case_01, case_02, case_04, case_31 |
| NO_SOURCE_RECORD | 2 | case_18, case_20 |
| BLOCKED_PLAN_CASCADE | 1 | case_31 |
| CITATION_TIE_BREAK | 1 | case_09 |
| CROSS_FILE_REFERENCE_MISS_RESOLVED | 1 | case_06 |
| RETENTION_CONDITION_UNPROVEN | 1 | case_31 |

## Every original non-exact sample

The table identifies differing fields and source-level causes. BLOCKED means no supported row, not an invented deletion_status. Case_06 is now exact.

| Sample | Differing fields | Root cause categories | Exact differences |
|---|---|---|---|
| case_01 | deletion_status/no row, steps_json membership | BLOCKED_STATUS, MISSING_PRIMARY_LINK_AND_SCOPE | account_001: no visible state readback |
| case_02 | deletion_status/no row, steps_json membership | BLOCKED_STATUS, MISSING_PRIMARY_LINK_AND_SCOPE | account_002: no visible state readback |
| case_03 | steps_json membership | MISSING_SUBJECT_LINK | analytics_9406: visible readback without admissible owner edge |
| case_04 | deletion_status/no row, steps_json membership | BLOCKED_STATUS, MISSING_PRIMARY_LINK_AND_SCOPE, MISSING_SUBJECT_LINK | account_004: no visible state readback; analytics_901: visible readback without admissible owner edge |
| case_06 | steps_json membership | CROSS_FILE_REFERENCE_MISS_RESOLVED | account_006: explicit source_resource_id in evidence_57 was dropped across files; now resolved |
| case_07 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD, MISSING_PRIMARY_LINK_AND_SCOPE | account_007: no visible state readback; object_19: additional supported step |
| case_08 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD, MISSING_PRIMARY_LINK_AND_SCOPE | account_008: no visible state readback; ORD-20264118: additional supported step |
| case_09 | steps_json membership, evidence_id | CITATION_TIE_BREAK, POST_CUTOFF_EVIDENCE | account_3960: readback unavailable or observed after cutoff; audit_record_4902: readback unavailable or observed after cutoff; backup_4978: differs in evidence_id |
| case_10 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE | account_010: no visible state readback |
| case_12 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD | ORD-20264207: additional supported step |
| case_17 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD | account_6523: additional supported step; tombstone_17: additional supported step; ORD-20264319: additional supported step |
| case_18 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE, NO_SOURCE_RECORD | account_5521: no visible state readback; audit_record_6645: no visible state readback |
| case_20 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE, NO_SOURCE_RECORD | account_5037: no visible state readback; audit_record_5569: no visible state readback |
| case_21 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD, MISSING_SUBJECT_LINK | account_6440: visible readback without admissible owner edge; analytics_5335: visible readback without admissible owner edge; ORD-20264402: additional supported step |
| case_22 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE | account_4990: no visible state readback |
| case_23 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE, POST_CUTOFF_EVIDENCE | account_410: no visible state readback; audit_record_4128: readback unavailable or observed after cutoff |
| case_24 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD, POST_CUTOFF_EVIDENCE | audit_record_7095: readback unavailable or observed after cutoff; ORD-20264511: additional supported step |
| case_25 | steps_json membership | MISSING_SUBJECT_LINK | account_3136: visible readback without admissible owner edge; analytics_1895: visible readback without admissible owner edge |
| case_26 | steps_json membership | MISSING_SUBJECT_LINK | account_5684: visible readback without admissible owner edge; analytics_4566: visible readback without admissible owner edge |
| case_27 | steps_json membership | MISSING_SUBJECT_LINK | account_3866: visible readback without admissible owner edge; analytics_4574: visible readback without admissible owner edge |
| case_28 | steps_json membership | MISSING_SUBJECT_LINK | account_5482: visible readback without admissible owner edge; analytics_6077: visible readback without admissible owner edge |
| case_29 | steps_json membership | ADDITIONAL_SUPPORTED_RECORD, MISSING_PRIMARY_LINK_AND_SCOPE, POST_CUTOFF_EVIDENCE | account_7012: no visible state readback; audit_record_3954: readback unavailable or observed after cutoff; ORD-20264600: additional supported step |
| case_30 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE, POST_CUTOFF_EVIDENCE | account_4163: no visible state readback; audit_record_1707: readback unavailable or observed after cutoff |
| case_31 | deletion_status/no row, steps_json membership | BLOCKED_PLAN_CASCADE, BLOCKED_STATUS, MISSING_PRIMARY_LINK_AND_SCOPE, RETENTION_CONDITION_UNPROVEN | account_4857: no visible state readback; tombstone_440: linked; plan blocked by another obligation; audit_record_6909: linked; plan blocked by another obligation |
| case_32 | steps_json membership | MISSING_PRIMARY_LINK_AND_SCOPE | account_8858: no visible state readback |

## Source checks and limits

Literal identifiers were searched across all94 manifest files, including all11 OCR span sets and all4 PDFs; CSV activity details and request texts were searched separately. Eleven OCR cache hits, zero new inference calls. Every screenshot was visually rechecked. account_006 occurs in an explicit search source pointer, which supplied the actual extraction fix. The remaining14 primary cases have no recovered subject-to-target edge. Their worker statements do not contain subject identifiers; request text does not name those resource keys. No suffix arithmetic, event-window assignment, row-position matching, or answer-derived edges were used.

Some expected security records are present in files captured after audit_at, rather than missing from the corpus. The brief does not expressly resolve retrospective knowledge semantics. Existing locked availability behavior and regression tests were retained; the weaker experimental mode is labelled UNAPPROVED in blocked_case_audit.md. Two expected security IDs have no occurrence in the participant evidence. No global retention duration was inferred from other records.

Additional sources include the object listing plus its exclusive-owner catalog statement, six retained finance orders in evidence_27 page2, and direct account/tombstone observations for case_17. These were not discarded to fit canonical omissions. Explicit external-to-internal alias evidence or organizer reconciliation is needed before treating distinct keys as the same record.
