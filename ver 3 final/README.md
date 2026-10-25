# Ghost Data Still Exists — Version 3 deletion-audit engine

A deterministic Python engine that resolves current and historical identifiers, parses evidence, reconstructs resource state at an audit cutoff, applies deletion policies, and writes ordered findings, remediation, and verification steps.

The output columns are `case_id,deletion_status,steps_json`; every step has the seven published fields in the required operation order. The engine preserves policy-required records, distinguishes retention from deletion, and includes active data-flow and backup-restore risks in its ordered plan. Findings cite observed violations; future remediation and verification for an incomplete request have blank citations.

The default link policy is `no-guess`; `--link-policy`, `--strict-capture-cutoff`, and `--use-bundle-order` expose comparison modes. The default can use weak, uniquely inferred links. Pre-cutoff evidence statements may inform state even when their export is later; such exports cannot be cited. Strict capture mode compares another temporal interpretation. These assumptions and unresolved visible-reference gaps are documented in the [evaluation report](code/evaluation/evaluation_report.md).

## Approach and release selection

The pipeline resolves exact current and historical identifiers, parses the manifest evidence once, associates resources with subjects, reconstructs state at each audit cutoff, applies the supplied policy, and sorts findings, remediation and verification deterministically. Activity results remain claims. SQL snapshots are processed in memory; OCR runs locally and caches only validated extraction results. An independent advisory checker examines source support without importing planner decisions.

Seven tested corrections are enabled: content before outcome labels, attribution-field consistency, cutoff-safe category selection, citation continuity, scoped intent, missing-proof handling and fractional timestamps. Together they preserve all current predictions. Five broader candidates remain opt-in because their evidence requirements or measured regressions prevent promotion. `--candidate NAME` replaces the selected experiment set; it requires a separate `--out`. Comparison modes and `--llm` also require a separate output path. Experimental results are not automatically final submissions.

Use `python -B code/check_evidence.py output.csv --data-dir <external-corpus> --report <external-report.json>` for independent advisory checks. `--strict` returns nonzero on detected errors. Review notes about incomplete projections, weak links and snapshot coverage remain explicit and are not proof of successful deletion. The report path must differ from the input and stay outside the corpus.

## Installation and configuration

Python 3.12 on Windows is the verified environment; Linux execution has not been verified. From this directory, create an environment and work directory outside the checkout, then set the external corpus path:

```powershell
$work = Join-Path ([System.IO.Path]::GetTempPath()) "ghost-audit-work"
New-Item -ItemType Directory -Force $work | Out-Null
python -m venv (Join-Path $work "venv")
$py = Join-Path $work "venv/Scripts/python.exe"
& $py -m pip install -r code/requirements-lock.txt
$env:GHOST_AUDIT_DATA_DIR = Read-Host "External participant dataset directory"
$env:GHOST_AUDIT_CACHE_DIR = Join-Path $work "ocr-cache"
$env:PYTHONDONTWRITEBYTECODE = "1"
```

`code/requirements.txt` lists the direct pinned dependencies; `code/requirements-lock.txt` also pins their transitive dependencies. PDF extraction uses pypdf. Screenshot OCR uses the models bundled with RapidOCR and runs locally. The optional paid-model path is an unverified experiment requiring `--llm`, a separate SDK and an output path other than the final default. Its provider route is not part of the verified release. Normal runs need neither an API key nor that SDK.

The corpus must contain the request CSVs, identity and audit tables, activity and policy tables, evidence manifest, and evidence files. Keep it read-only and outside this tree. `--data-dir` overrides the environment setting. Predictions never load the sample answer CSV.

## Run and validate

```powershell
& $py -B code/main.py --data-dir $env:GHOST_AUDIT_DATA_DIR --out (Join-Path $work "predictions.csv")
& $py -B code/validate_output.py (Join-Path $work "predictions.csv") --data-dir $env:GHOST_AUDIT_DATA_DIR
$env:GHOST_AUDIT_TEST_DATA_DIR = $env:GHOST_AUDIT_DATA_DIR
& $py -B -m unittest discover -s code/tests -v
& $py -B code/evaluation/main.py --custom
```

The tests use included fictional fixtures and an independent synthetic policy. Two corpus-specific regression tests are optional; set `GHOST_AUDIT_TEST_DATA_DIR` to the external corpus to enable them. Synthetic fixture answer files are test oracles, never production inputs.

Normal CLI publication requires every manifest evidence file to be processed successfully. A missing OCR dependency, unreadable file or parser failure stops publication and preserves the previous output. `--allow-partial-evidence` is an explicitly exploratory override; its outputs are unsuitable for release. CSV publication validates the complete serialized contract, stages and reads back a sibling temporary file, then atomically replaces the destination. This protects the prior artifact on handled write/validation failures; filesystem atomic-replace support remains required.

To verify fresh OCR, point `GHOST_AUDIT_CACHE_DIR` at a new empty directory and run the engine. Repeat the same command to verify the warm cache. Both passes generate predictions anew. Diagnostics and usage telemetry are opt-in through `--diagnostics`, `--summary`, and `--usage`; keep runtime paths outside the read-only corpus.

## Visible-sample evaluation

Generate the sample predictions before opening or scoring the reference answers:

```powershell
& $py -B code/main.py --data-dir $env:GHOST_AUDIT_DATA_DIR --requests sample_deletion_requests.csv --out (Join-Path $work "sample-predictions.csv")
& $py -B code/validate_output.py (Join-Path $work "sample-predictions.csv") --data-dir $env:GHOST_AUDIT_DATA_DIR --requests sample_deletion_requests.csv
& $py -B code/evaluation/main.py --pred (Join-Path $work "sample-predictions.csv") --gold (Join-Path $env:GHOST_AUDIT_DATA_DIR "sample_output.csv")
```

The scorer rejects duplicate, missing and extra case IDs and malformed contracts before scoring. Exact-case equality compares status and all seven fields in array order, including `step_no`. Step F1 matches a multiset of the other six fields; sequence accuracy and LCS compare ordered phase/operation/system/resource keys. Positional field agreement is another diagnostic. Known blank citations in supplied reference rows are reported as reference limitations; predictions must still supply COMPLETE proof. Visible-sample performance does not establish accuracy on the unlabeled evaluation requests.

Evaluation narratives, independent custom scenarios, measured results and remaining uncertainties are in [evaluation_report.md](code/evaluation/evaluation_report.md). [usage_report.md](code/evaluation/usage_report.md) accounts separately for local OCR, cache hits and optional paid text-model usage. The deterministic default makes no paid model calls.

## Package and rehearse

```powershell
& $py -B code/package_submission.py --out (Join-Path $work "code.zip")
$extracted = Join-Path $work "extracted"
Expand-Archive -LiteralPath (Join-Path $work "code.zip") -DestinationPath $extracted
Push-Location $extracted
& $py -B code/main.py --data-dir $env:GHOST_AUDIT_DATA_DIR --out (Join-Path $work "extracted-output.csv")
& $py -B code/validate_output.py (Join-Path $work "extracted-output.csv") --data-dir $env:GHOST_AUDIT_DATA_DIR
& $py -B -m unittest discover -s code/tests -v
& $py -B code/evaluation/main.py --custom
Pop-Location
Get-FileHash -Algorithm SHA256 (Join-Path $work "predictions.csv"), (Join-Path $work "extracted-output.csv")
```

For an independent installation rehearsal, create a new external environment and install the extracted `code/requirements-lock.txt` there before these commands. Compare source and extracted output bytes after both fresh and warm cache runs. The final implementation review records the actual rehearsed environment and artifact hashes.

The packager requires README, runnable sources, pinned dependencies and both nonempty reports before opening a publication path. It preserves independent synthetic fixtures, excludes corpus copies, environments, caches, generated runs, planning records, transcripts, secrets and nested archives, validates the staged ZIP and atomically replaces the destination. Its stable member metadata makes repeated builds of unchanged sources reproducible. Keep the genuine `log.txt` separate from the code archive.

## Provenance and licensing

This is a locally reviewed continuation of a pre-existing deletion-audit implementation. Version 3 adds validated publication, ingestion gates, bounded SQL, validated OCR caches, evaluation integrity checks and isolated semantic experiments. Semantic candidates require separate correctness and non-regression review before default promotion; the evaluation report identifies the selected default and deferred candidates. The original source identity, baseline hashes and per-case changes are retained outside packaged code. This copy makes no new authorship claim.

No engine license grant or copyright notice was supplied in the inspected source. Confirm redistribution permission before public release; this README does not grant that permission. Dependencies retain their respective licenses and notices in their installed distributions.
