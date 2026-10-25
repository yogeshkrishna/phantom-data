# Phantom Data

Python 3.12. Install local OCR/PDF dependencies once:

```sh
python -m pip install -r code/requirements.txt
python code/main.py prepare --dataset PATH_TO_DATASET --cache .cache/phantom
python code/main.py check --dataset PATH_TO_DATASET --cache .cache/phantom
python code/main.py audit --dataset PATH_TO_DATASET --cache .cache/phantom --output output.csv
```

Inside an extracted code directory, omit `code/` from these commands.
The dataset stays external/read-only. No API keys or paid services are needed.
Neural OCR runs on first preparation; later runs reuse source/model-keyed facts.
Caches contain personal evidence and must stay private, outside Git/submissions.
Model binaries are installed as dependencies, excluded from submitted source.

Use `audit --case-id ID` (repeatable) for focused runs. The earlier invocation
without the `audit` subcommand still works. Prepare/check print each file's
layout/coverage/diagnostics and actual OCR usage. Exit 0 means success; exit 2
means unsupported proof or a source/configuration error. Audit never overwrites
output when any selected request blocks. No status is fabricated for CSV coverage.

```sh
python -B -m unittest discover -s code/tests -q
python code/main.py evaluate --dataset PATH_TO_DATASET --cache .cache/phantom --report code/evaluation/evaluation_report.md
```

Evaluation runs the visible samples and independent filesystem fixtures, writes
measured Markdown metrics and records missing predictions/errors. A successful
evaluation process does not mean perfect accuracy. Unknown fields, retention
periods and unresolved links remain reported gaps. Sample answers are evaluation-only.

Run the measured release gate and build/rehearse the source archive:

```sh
python -B code/release.py --dataset PATH_TO_DATASET --cache .cache/phantom
python -B code/package.py --output code.zip --rehearse --dataset PATH_TO_DATASET
```

The gate attempts every request and repeats audits to check deterministic results.
It writes `output.csv` only when every request is independently verified. Exit 2
means blocked; `evaluation/release_readiness.md` lists missing proof by resource.
`runs/release/supported_predictions.csv` is a partial diagnostic, never the final
submission. Existing output is preserved on failure; an old CSV is not evidence
of a passing current gate.

The archive contains runnable source, tests, prompts and current evaluation/
usage reports. It excludes datasets, environments, caches, weights and session
logs. Rehearsal extracts it into a fresh directory, checks import origins, runs
tests against the external corpus, then repeats all requests with an empty OCR/
compiler cache. Declared dependencies must already be installed. A reproduced
BLOCKED result proves portability but does not make predictions submission-ready.

Runtime inference uses local neural OCR; no runtime LLM or paid API is used.
Preparation/download, cold inference, warm preparation and prepared audits are
reported separately. Test optional corpus integration explicitly with
`PHANTOM_DATASET` set to the external dataset path.
