# Phantom Data

Local, evidence-grounded deletion auditing for HackerRank Orchestrate's
[Ghost Data Still Exists](https://github.com/interviewstreet/hackerrank-orchestrate-october26/blob/main/problem_statement.md).

The Python 3.12 pipeline resolves historical identities, reads CSV/JSON/SQL/logs,
PDFs and screenshots, reconstructs each physical store at the audit cutoff, and
applies supplied policy. An independent checker validates every proposed result.
Neural OCR runs locally; no paid API is required.

[Setup and commands](code/README.md) | [Measured evaluation](code/evaluation/evaluation_report.md) |
[Release gates and missing proof](code/evaluation/release_readiness.md)

Visible development samples currently measure 87.5% status accuracy, 86.1% step F1
and 21.875% exact rows. These are not hidden-test scores. The full 518-request
attempt supports 457 results and blocks 61 for missing proof. The release command
will not invent those results or write a misleading final CSV.

Source, runtime usage and extracted-package checks are included in code.zip.
The corpus, private evidence caches, model binaries and transcript stay outside
the code archive. Transcript export retains genuine history and states known
provenance gaps.
