# Ghost Data Still Exists — deletion-audit engine

A deterministic Python engine that resolves current and historical identifiers, parses evidence, reconstructs resource state at an audit cutoff, applies deletion policies, and writes ordered findings, remediation, and verification steps.

The output columns are `case_id,deletion_status,steps_json`. The default link policy is `no-guess`; `--link-policy`, `--strict-capture-cutoff`, and `--use-bundle-order` expose the existing comparison modes. The default mode can still use weak, uniquely inferred links. Evidence statements dated before the cutoff may inform state even when their containing export is later; such exports cannot be cited. Use strict capture mode to compare that interpretation.

## Installation and configuration

Python 3.12 on Windows is the verified environment. From this directory, create an environment and work directory outside the checkout, then set the external corpus path:

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

`code/requirements.txt` lists the direct pinned dependencies; `code/requirements-lock.txt` also pins their transitive dependencies. PDF extraction uses pypdf. Screenshot OCR uses the models bundled with RapidOCR and runs locally. The optional paid-model path requires an explicit `--llm` flag and a separately installed SDK; normal deterministic runs need neither an API key nor that SDK.

The corpus must contain the request CSVs, identity and audit tables, activity and policy tables, evidence manifest, and evidence files. Keep it read-only and outside this tree. `--data-dir` overrides the environment setting. Predictions never load the sample answer CSV.

## Run and validate

```powershell
& $py -B code/main.py --data-dir $env:GHOST_AUDIT_DATA_DIR --out (Join-Path $work "predictions.csv")
& $py -B code/validate_output.py (Join-Path $work "predictions.csv") --data-dir $env:GHOST_AUDIT_DATA_DIR
& $py -B -m unittest discover -s code/tests -v
& $py -B code/evaluation/main.py --custom
```

The tests use included fictional fixtures and an independent synthetic policy. Two corpus-specific regression tests are optional; set `GHOST_AUDIT_TEST_DATA_DIR` to the external corpus to enable them. Synthetic fixture answer files are test oracles, never production inputs.

To verify fresh OCR, point `GHOST_AUDIT_CACHE_DIR` at a new empty directory and run the engine. Repeat the same command to verify the warm cache. Both passes generate predictions anew. Diagnostics and usage telemetry are opt-in through `--diagnostics`, `--summary`, and `--usage`; supply paths outside this tree.

## Visible-sample evaluation

Generate the sample predictions before opening or scoring the reference answers:

```powershell
& $py -B code/main.py --data-dir $env:GHOST_AUDIT_DATA_DIR --requests sample_deletion_requests.csv --out (Join-Path $work "sample-predictions.csv")
& $py -B code/validate_output.py (Join-Path $work "sample-predictions.csv") --data-dir $env:GHOST_AUDIT_DATA_DIR --requests sample_deletion_requests.csv
& $py -B code/evaluation/main.py --pred (Join-Path $work "sample-predictions.csv") --gold (Join-Path $env:GHOST_AUDIT_DATA_DIR "sample_output.csv")
```

The scorer reports status, exact-case, step precision/recall/F1, field agreement, and sequence metrics. Visible-sample performance does not establish accuracy on the unlabeled evaluation requests.

## Provenance and licensing

This is a publication copy of a pre-existing deletion-audit implementation. Core logic is retained; copy-specific changes configure paths, make telemetry opt-in, and isolate tests from the challenge corpus. This copy makes no new authorship claim. The original source identity and copy manifest are retained in a private audit outside this tree.

No engine license grant or copyright notice was supplied in the inspected source. Confirm redistribution permission before public release; this README does not grant that permission. Dependencies retain their respective licenses and notices in their installed distributions.
