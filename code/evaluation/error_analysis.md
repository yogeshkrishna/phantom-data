# Source-level error analysis, stage 12

This analysis concerns the visible development samples. It does not assert that
all remaining errors are unfixable or predict the hidden score. The generated
evaluation_report.md includes the current per-case error inventory, exact source
locators for missing/extra resources, measured metrics and ablations.

## Changes justified by source evidence

1. **Authorization and continuation.** Earlier intent handling recognized simple
   deletion commands but missed first-person deletion in progress, follow-up
   requests to finish deletion, coordinated redaction, and personal audits of
   earlier deletion. Language-level clauses and cross-clause target references
   now cover these without matching case IDs or whole request templates.
   Counterexamples preserve quoted/withdrawn requests, information-only requests,
   and newsletter removal. Full-dataset comparison caught an information request
   containing “not a request to erase it”; explicit negation now governs that
   dereference. No deletion is authorized for that request.

2. **Latest risk readbacks.** Explicit ACTIVE/DISABLED/BLOCKED diagnostic results
   describe a narrow boolean facet. They now normalize enabled/restore_allowed
   values, so an old activity flag does not override a newer readback. A concrete
   example is the integration readback in evidence_03's handover lines versus
   older activity_3817: current proof should cite the readback rather than choose
   an activity ID merely because it sorts earlier. The same correction applies
   to JSON operational history and log diagnostic events.

3. **Redaction versus anonymization.** In evidence_92,
   `/service_exports/support_platform/tickets/0` through `/tickets/2`, the direct
   requester fields are explicitly null and an internal customer reference
   preserves shared support context. REDACT permits that necessary stable
   context; ANONYMIZE still requires unlinking stable IDs and hashes. The older
   policy confused those treatments. Category-specific coverage is now required;
   a partial readback cannot clear previously observed omitted PII.

4. **Retained archives.** VERIFY_RESTORE_BLOCKED checks both archive availability
   and restore permission. The planner emits that single check rather than an
   additional redundant presence step, while policy and the independent verifier
   each enforce the conjunction. The independent retained-archive fixture was
   updated to this documented operation semantics; a mutation removing the
   retained copy still fails. Retained security/legal presence checks remain.

Regression fixtures for each change are in tests/test_error_regressions.py and
the corresponding intent/adapter tests. Shared contracts and allowed operations
were preserved. No resource-ID arithmetic, answer caches or case-specific paths
were introduced.

An additional timestamp regression found while inspecting the media facts was
fixed: a later catalog statement repeating an already visible exact resource ID
must not delay that self-identified observation. External-to-internal alias
binding still carries the later catalog availability. For example evidence_04's
subject header already contains its actual account key and subject reference;
the later catalog statement is redundant for that key.

## Remaining evidence/label disagreements

- **Unbound absence observations.** A 404 with `profile: null`, resource ID and
  timestamp provides no subject selector. For example, evidence_92
  `/service_exports/account_service/profile_lookups/0` has no email/user ID.
  If earlier source evidence also lacks an owner edge, that resource cannot be
  assigned to a request just because surrounding rows belong to that request.
  The source-trace inventory distinguishes actual state rows from operation
  claims, which do not by themselves establish ownership or absence.
- **Anonymous row ownership.** evidence_56 `/records/0` contains null user_id,
  email, phone and email_hash. Its metrics and resource ID do not establish which
  subject it previously belonged to. Likewise anonymous profiles in evidence_92
  have no subject link unless another visible source supplies one. The agent
  preserves legitimate historical links but does not invent missing ones.
- **Empty partial SQL scopes.** evidence_54's primary table contains other users,
  but the export has no explicit completeness declaration and no subject-to-key
  map for absent records. It cannot prove named absent resource IDs by matching
  user/case numeric suffixes. This blocks some expected COMPLETE samples.
- **Unproven retention conditions.** evidence_51
  `/snapshots/2/records/0` and `/snapshots/2/records/1` contain signed security
  events and stable references, but no retention boundary. The CSV condition
  requires a supplied retention period. The agent preserves these records but
  does not invent a period to certify completion. Its empty primary snapshot
  also lacks an explicit exhaustive scope and subject-to-resource key mapping.
- **Additional supported records.** evidence_46's object listing and the explicit
  attachment-catalog identity statement bind a personal object independently of
  the support attachment in another PDF. The agent retains the additional
  deletion obligation even when it is absent from a published sample row.
  evidence_27 page 2 also supplies retained order entries associated with stable
  subject IDs; dropping those solely to improve exact matching would lose source
  evidence. Their external order IDs are not fabricated internal legal-hold IDs.

These explain observed mismatches, not a claim that the canonical answers are
universally wrong. Additional participant-visible identity or scope evidence
could resolve them. Full coverage remains an explicit release gate.

## Measured improvements and ablations

The committed stage11 baseline had status accuracy 0.40625, exact-row accuracy
0.125 and step F1 0.560554. Stage12's measured report records the improved result
without claiming held-out performance. Historical identities, replica evidence
and neural image facts are removed independently on the same 32 requests.
Unsafe/removal variants are confined to evaluation, with no production flags.
Raw model inference is outside these prepared ablation timings.
