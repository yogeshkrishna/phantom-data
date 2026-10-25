# Phantom Data

Local deletion auditing for HackerRank Orchestrate's
[Ghost Data Still Exists](https://github.com/interviewstreet/hackerrank-orchestrate-october26/blob/main/problem_statement.md).

All three versions resolve identifiers, inspect structured evidence and local media,
apply deletion policy, and emit `case_id,deletion_status,steps_json`.
Normal execution uses local OCR and requires no paid API.

| Version | Purpose | Setup and evaluation |
| --- | --- | --- |
| [ver 1](ver%201/) | Preserved public baseline from `c056cb6` | [Original instructions](ver%201/code/README.md), [measured results](ver%201/code/evaluation/evaluation_report.md) |
| [ver 2](ver%202/) | Operational parallel engine with independently reproduced output | [Installation, audit, validation and tests](ver%202/README.md) |
| [ver 3 final](ver%203%20final/) | Reviewed continuation with validated publication, bounded SQL, OCR cache integrity and tested reasoning corrections | [Setup and architecture](ver%203%20final/README.md), [evaluation](ver%203%20final/code/evaluation/evaluation_report.md), [usage](ver%203%20final/code/evaluation/usage_report.md) |

On the 32 visible development samples, ver 1 measured 8 exact rows,
87.5% status accuracy and 86.43% step F1. Ver 2 reproduced 28 exact rows,
100% status accuracy and 98.95% step F1. These are development measurements,
not hidden-test accuracy claims. Ver 2 generated all 518 requested rows;
ver 1's preserved interpretation supports 462 and blocks 56 for missing proof.

## Running locally

Use Python 3.12 and follow the selected version's instructions from inside its
directory. The versions have separate dependencies and command-line
interfaces; use separate environments.

Supply the official dataset externally. It is not included here. Keep evidence,
OCR caches, generated predictions, environments and private development logs
outside Git. Required synthetic fixtures remain with each engine's tests.

Ver 2's source and publication copy produced byte-identical 518-row outputs
with both fresh and cached OCR. Its 43 tests passed with external corpus
regressions enabled, and its 14 synthetic evaluation cases passed. Fresh OCR
and warm runs have different costs; consult the version instructions before
interpreting performance or enabling inference comparison settings.

Ver 3 preserves Ver 2's complete output byte-for-byte while improving fault
containment, evidence-ingestion checks, cache validation, evaluation integrity
and packaging. It passes 139 tests and all 14 synthetic evaluation cases. The
extracted code archive reproduced all 518 rows with fresh and cached OCR.
Measured sample prediction accuracy remains unchanged; the evaluation report
records the selected corrections, deferred experiments and remaining limits.

The historical ver 1 documentation is preserved within that directory and
describes the baseline as it existed before this reorganization. Shared
contributor rules remain in [AGENTS.md](AGENTS.md). Private transcripts,
datasets, local release archives and investigation artifacts are excluded from
Git. Rejected semantic candidates remain explicit comparison modes rather than
the default. No repository-wide software license has been added by this import.
