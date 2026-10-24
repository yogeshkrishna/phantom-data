# Structured-evidence checkpoint (stage 07)

This is an intermediate audit, not the final evaluation or submission run.
Preparation interpreted all 28 JSON and 24 SQL files (52/94 manifest entries).
Logs, text, media and activity adaptation were still pending at this checkpoint.
Visible unsupported evidence prevents a supported COMPLETE verdict.

Selection: iterate sample requests in source order and select the first whose
supported obligations allow a verified INCOMPLETE plan. Expected answers were
opened only after generating the result. This selected `case_05`.

Generated status: INCOMPLETE. Generated steps: remaining account finding,
DELETE_RECORD, VERIFY_RECORD_ABSENT. The published sample has the same status
and all three steps, including citation and sequence (one exact development row).
This is not an accuracy estimate for the other samples or hidden requests.

The finding is a physical failover copy, independent of a clean primary:
`evidence_54`, `/tables/customer_profiles_failover_replica/rows/0`, observed and
available at `2026-09-02T23:00:00Z`, logical resource `account_005` in
`account_service`. No numeric-suffix identity or category join was used.

CLI smoke: the first supported main request selected by the same evidence-driven
procedure is `case_54` (INCOMPLETE, 13 steps). The CLI successfully writes a
validated single-row CSV. A separate request lacking relevant structured proof
reports NO_RELEVANT_RESOURCE_PROOF and does not fabricate output.

Independent synthetic end-to-end tests exercise all three statuses. Mutations
remove steps, replace citations/resources, reorder steps, forge compliance flags,
move cutoff/source availability, add a dirty replica and remove source coverage.
The verifier rejects those mutations without calling the planner or PolicyEngine.

Output has no physical-store column. For COMPLETE, a collapsed logical-resource
verification requires a single citation supporting all physical-store obligations.
If no such citation exists, generation blocks. For INCOMPLETE, logical treatment
and future verification apply to all linked stores; internal obligations retain
each store and its provenance. No obligation is dropped to fit output syntax.
