# Accuracy investigation: result and wall

The requested20/32 target was not reached. Three general production fixes were
implemented and tested; exact matches rose from7 to8. No submission or packaging
was performed. The pre-existing code.zip was left unchanged and is stale.

## A. Verified before/after

| Metric | Before | After |
|---|---:|---:|
| Visible sample exact | 7/32 (21.875%) | 8/32 (25%) |
| Visible status accuracy | 28/32 (87.5%) | 28/32 (87.5%) |
| Visible step F1 | 0.861111 | 0.864266 |
| Supported full-input requests | 457/518 | 462/518 |
| Blocked full-input requests | 61 | 56 |

Final verification: 355 tests pass, zero skips. All original333 tests remain passing. New tests cover native boolean storage,
full-row versus partial-scope coverage, extra stored payload columns, exact
cross-file references, future/ambiguous witnesses, opaque renaming, row order,
diagnostic step alignment, and independent raw-key inspection. The generated
evaluation_report.md records the eight independent filesystem cases and existing
history/replica/image ablations. No hidden labels are available.

## B. Implemented fixes and measured deltas

| Fix | General defect | Sample flips | Status / step F1 | Full-input effect | Commit |
|---|---|---:|---|---|---|
| Native SQLite boolean decoding | SQL0/1 flags were not actual booleans, so strict checks missed active/disabled flows and restore permissions | 0 | 87.5% / 0.861111 unchanged | Five blocked requests recovered; six existing INCOMPLETE cases gain supported risk findings and remediation; no formerly supported case blocked | 8d17ee1 |
| Complete physical SQL row coverage | Whole stored JSON/row contents were treated like an API projection; extra native payload columns could be ignored | 0 | 87.5% / 0.861111 unchanged | Eleven unknown treatment obligations removed; no prediction change because those cases also lack retention proof | 80d02a6 |
| Timed, exact cross-file source references | A source_resource_id pointer was lost when its target existed only in a different file/operation | 1 (case_06) | Status unchanged; F1 0.861111 to0.864266 | No non-sample prediction or coverage change | 8074d84 |

Boolean conversion applies only to reviewed native SQL boolean columns and
integer0/1. NULL, strings, nonbinary numbers, ordinary numeric columns and JSON
numbers are not coerced. SQLite's Boolean storage convention is documented at
https://www.sqlite.org/datatype3.html#boolean_datatype .

Whole-row coverage follows SELECT * over the supplied physical schema and whole
stored JSON blob. It does not make a collection exhaustive or clear a replica.
Additional native columns are preserved separately from document fields so they
cannot silently hide PII or overwrite the document's values.

Cross-file binding uses an exact pointer plus a unique actual target service.
Target type comes from a target fact or an unambiguous declared service schema.
It creates association contexts, never a fabricated state readback. Both source
and witness times govern visibility; ambiguous/future-only links are rejected.
In case_06, the search row names the account explicitly and the activity record
names its service. This supports a future absence check on an already INCOMPLETE
case; it does not prove that the account is currently absent.

## C. Every original and remaining mismatch

sample_diagnosis.md contains all25 original samples with exact differing fields,
missing/extra resource keys, root categories and the resolved case_06. Its other
24 rows are the remaining non-exact cases. Ranked unresolved categories overlap:

| Root category | Samples |
|---|---:|
| No recovered primary-account subject link/current absence scope | 14 |
| Additional supported records omitted by canonical output | 7 |
| Ownerless readbacks, including anonymous analytics and unbound404s | 7 |
| Current availability cutoff excludes later-captured evidence | 5 |
| Expected security record absent from evidence | 2 |
| Missing retained-record policy condition blocks completion | 1 |
| Citation preference differs despite supported alternatives | 1 |

There is no separate formatting or ordering fix identified. Four blocked sample
rows are not four supported wrong-status predictions. For case_31, the clean
tombstone and security record are linked; the missing period blocks the entire
plan, while the expected primary-account key remains unbound.

## Audit of all61 originally blocked requests

blocked_case_audit.md gives one row for each original case, including resource,
physical store, reason and exact source locators. Five are recovered by native
SQL boolean decoding. Among the remaining56,54 have at least one unproven
retention condition. Other overlapping gaps are three archive-presence proofs,
three treatment-coverage obligations, and one unknown restore permission.

All owning JSON objects, physical SQL rows/embedded documents and decoded log
events were independently scanned for omitted keys. No retention/hold/expiry key
was found for the unresolved retained records in that raw structured scan. The
exceptions outside that index are the shift handover and five screenshot events;
their text/image regions were rechecked. The PDF's explicit retention date binds
one named security record, not all events of that subject. It cannot supply the
five missing periods. No adjacent-row date, currency conversion or subject
suffix was treated as proof. All11 screenshots and the relevant PDF pages were
visually rechecked; literal IDs were also searched through every media text set.

This is a bounded source audit, not proof that no future adapter improvement is
possible. The capture-time interpretation is a genuine specification ambiguity,
not a missing-byte claim. Unknown schema/policy semantics remain unknown.

## D. Milestone roadmap, conditional on legitimate inputs

These are dependency estimates from current mismatches, not measured future
scores. They assume each category is resolved correctly and causes no new
mismatch. They are not a code-only promise. Fourteen samples still require a
primary binding that was not recovered; leaving those14 unchanged caps the score
at18/32 even if every other row becomes exact. Therefore20/32 needs some new
legitimate primary-binding evidence or a demonstrated extraction discovery.

| Target | Concrete work and modules | Conditional gains | Missing input / risk |
|---|---|---|---|
| 15/32 | Recover exact primary bindings through references.py, json_evidence.py and log_evidence.py; parse explicitly exhaustive JSON scopes in json_evidence.py/state.py only when declared. Reconcile media aliases in media_evidence.py/references.py | Primary category alone could add5, reaching13; verified alias/canonical reconciliation could add4 more, reaching17 | Need subject-to-primary keys and actual absence scopes for COMPLETE cases. Need genuine external/internal aliases or corrected canonical omissions. Time/suffix assignment and deduplication by subject are prohibited |
| 20/32 | After the preceding dependencies, preserve historical ownership/query selectors for anonymous rows and404s through identities.py, references.py and json_evidence.py | Up to7 additional rows; hypothetical17 to24 | The null lookup selectors and stripped owner fields do not encode a recoverable owner. Require prior attributable readbacks, catalog bindings or explicit query selectors. Position/cohort similarity would overfit |
| 25/32 | Add legitimate historical availability evidence, or obtain a clarified retrospective-audit contract; update log_evidence.py/json_evidence.py and temporal handling in state.py/contracts.py only under that contract | At least1 additional row needed from hypothetical24; the later-captured source family affects up to4 otherwise resolved cases | Blanket removal of the capture cutoff degraded measured F1 and supported coverage. Any contract revision must explicitly reconcile existing time-safety tests; no silent date rewriting |
| 28/32 | Complete the remaining later-captured-source dependencies after primary/owner/alias resolution; use independently timestamped corroboration when possible | Up to4 combined gains from hypothetical24 to28 | Several source records are historical but the provided file was captured later. Clarify observation versus knowledge time; keep future state from clearing earlier PII. Counts are conditional and could shrink when new evidence adds obligations |
| 32/32 | Add the two missing security readbacks in extraction; supply case_31's retention applicability through policy.py/verifier.py; standardize supported citation selection in planner.py under a documented rule | Remaining4 after hypothetical28: citation case_09; missing security cases18/20; retained-policy case31 | Need actual source rows and policy support. The two security IDs are absent from participant evidence. Citation preference alone is not a correctness fix; canonical grading must define/accept equivalent proof |

The alias/canonical category is especially uncertain: distinct supported records
must remain distinct unless the sources prove equivalence. If the canonical
answers intentionally omit them, code cannot honestly make those rows exact
without clarified scope or corrected labels. More capable OCR/LLMs cannot derive
information that is not encoded in the provided sources.

## E. Generalization and ablations

The new rule removals are measured in blocked_case_audit.md. Removing boolean
decoding restores the five blocks; removing cross-file contexts restores7/32
exact; removing SQL row coverage restores eleven unknown treatment obligations.
These establish causal effects, not hidden-label accuracy. Opaque-ID/case
renaming and input-order tests guard against ID and order dependence. Mutations
cover future witnesses, ambiguous services, another replica, nonbinary flags and
hidden native-column PII. No existing333-test expectations were weakened.

The UNAPPROVED latest-capture citation experiment improves sample step F1 but
flips no exact row. It was not adopted or counted as a fix: the brief provides
no universal freshness preference for equivalent evidence. The UNAPPROVED
observation-time-only state experiment worsens F1 and blocks more full-input
requests; its identity resolver still uses the original visibility contract, so
it is a sensitivity test, not a complete retrospective implementation. It was
also not adopted. Original timestamps and production behavior stay unchanged.

Native SQL boolean semantics should transfer well to the same source schemas.
Whole-row interpretation transfers only to genuine stored-row snapshots, not
partial API/UI projections. Exact cross-file references should transfer when
the same explicit field semantics and unique service binding hold. Their only
visible flip currently comes from one sample, so the size of hidden benefit is
uncertain despite independent counterexamples. There are no non-sample labels:
we cannot claim that non-sample accuracy did not degrade, only report checked
source-grounded changes and no newly blocked requests.

Risk reduction requires organizer clarification of time/scope/citation rules,
corrected or enriched participant evidence, and an unseen independently authored
test set spanning different schemas/languages, conflicting ownership, partial
captures and retention periods. Leave-one-out over a repeatedly inspected set
would not make the32 samples held out. No sample-pattern-only gain is counted.
