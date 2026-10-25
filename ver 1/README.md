# Phantom Data

Local, evidence-grounded deletion auditing for HackerRank Orchestrate's
[Ghost Data Still Exists](https://github.com/interviewstreet/hackerrank-orchestrate-october26/blob/main/problem_statement.md).

The Python 3.12 pipeline resolves historical identities, reads CSV/JSON/SQL/logs,
PDFs and screenshots, reconstructs each physical store at the audit cutoff, and
applies supplied policy. An independent checker validates every proposed result.
Neural OCR runs locally; no paid API is required.

[Setup and commands](code/README.md) | [Measured evaluation](code/evaluation/evaluation_report.md) |
[Release gates and missing proof](code/evaluation/release_readiness.md)

Visible development samples currently measure 87.5% status accuracy, 86.43% step F1
and 25% exact rows (8/32). These are not hidden-test scores. The full 518-request
attempt supports 462 results and blocks 56 for missing proof. The release command
will not invent those results or write a misleading final CSV.

The previous code.zip is stale; this accuracy investigation did not rebuild it.
[Detailed results and remaining limitations](code/evaluation/accuracy_investigation.md).
The corpus, private evidence caches, model binaries and transcript stay outside
the code archive. Transcript export retains genuine history and states known
provenance gaps.
