"""Build a source-only archive and rehearse it from an isolated fresh directory."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_FILES = {"main.py", "release.py", "package.py", "README.md", "requirements.txt"}
REPORTS = {"evaluation_report.md", "error_analysis.md", "usage_report.md",
           "release_readiness.md", "package_rehearsal.md"}
DIRECTORIES = {"phantom", "tests", "prompts", "evaluation"}


def selected_files(source: Path):
    source = source.resolve()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not path.is_file() or any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        include = relative.as_posix() in ROOT_FILES
        if len(relative.parts) > 1 and relative.parts[0] in DIRECTORIES:
            include = path.suffix == ".py" or relative.parts[0] == "prompts" and path.suffix == ".md"
            include |= relative.parts[0] == "evaluation" and len(relative.parts) == 2 and path.name in REPORTS
        if include:
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("Archive source escapes its code directory")
            yield path, relative.as_posix()


def build_archive(source: Path, output: Path):
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("Archive must be outside its source directory")
    files = list(selected_files(source))
    if not ROOT_FILES <= {name for _, name in files}:
        raise ValueError("Missing required runnable source files")
    output.parent.mkdir(parents=True, exist_ok=True)
    from tempfile import NamedTemporaryFile
    from phantom.cache import atomic_replace
    temporary = None
    try:
        with NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, name in files:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        atomic_replace(temporary, output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in files}


def _child(arguments, cwd, expected_codes=(0,), extra_env=None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update(extra_env or {})
    process = subprocess.run([sys.executable, "-B", *arguments], cwd=cwd, env=environment,
                             capture_output=True, text=True, encoding="utf-8", timeout=180)
    if process.returncode not in expected_codes:
        raise RuntimeError(f"Extracted command failed ({process.returncode}): {process.stderr[-3000:]}")
    return process


def rehearse(archive: Path, dataset: Path, expected_run: Path):
    """Test extracted imports/tests and a cold-source/OCR run against a prior gate.

    Installed declared dependencies are reused. No compiler or OCR cache from
    the original checkout is supplied. The original read-only corpus is external.
    A reproduced BLOCKED gate is explicitly not called submission readiness.
    """
    expected = json.loads(expected_run.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="phantom-package-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                if not (root / name).resolve().is_relative_to(root.resolve()):
                    raise ValueError("Unsafe archive member")
            zipped.extractall(root)
        print("Rehearsal: checking extracted import location and unit tests", flush=True)
        _child(["-c", "import pathlib, phantom; "
                "assert pathlib.Path(phantom.__file__).resolve().is_relative_to(pathlib.Path.cwd()); "
                "print('Imports originate in extracted package')"], root)
        tests = _child(["-m", "unittest", "discover", "-s", "tests", "-q"], root,
                       extra_env={"PHANTOM_DATASET": str(dataset.resolve())})
        print("Rehearsal: cold preparation and all-request audit (no existing OCR cache)", flush=True)
        _child(["release.py", "--dataset", str(dataset.resolve()), "--cache", str(root / "fresh-cache"),
                "--output", str(root / "output.csv"), "--run-dir", str(root / "run"),
                "--report-dir", str(root / "reports"), "--repeats", "2"], root, (0, 2))
        actual = json.loads((root / "run" / "release_run.json").read_text(encoding="utf-8"))
        keys = ("status", "request_count", "supported_count", "blocked_count", "replay_signature", "output_sha256")
        if any(actual[key] != expected[key] for key in keys):
            raise ValueError("Extracted package changed verified results or coverage")
        return dict(reproduced=True, gate=actual["status"], request_count=actual["request_count"],
                    supported_count=actual["supported_count"], blocked_count=actual["blocked_count"],
                    imports_from_extracted_source=True, tests=tests.stderr.strip(),
                    cold_first_attempt_seconds=actual["first_attempt_seconds"],
                    cold_preparation_seconds=actual["preparation_seconds"],
                    audit_seconds=actual["audit_seconds"], model_usage=actual["model_usage"],
                    supported_predictions_sha256=actual["supported_predictions_sha256"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("code.zip"))
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--expected-run", type=Path, default=Path("runs/release/release_run.json"))
    args = parser.parse_args(argv)
    if args.rehearse and args.dataset is None:
        parser.error("--rehearse requires --dataset")
    source = Path(__file__).resolve().parent
    if args.dataset is not None and args.output.resolve().is_relative_to(args.dataset.resolve()):
        raise ValueError("Archive must remain outside the read-only dataset")
    files = build_archive(source, args.output)
    if args.rehearse:
        result = rehearse(args.output, args.dataset, args.expected_run)
        from release import _write
        _write(Path("runs/release/package_rehearsal.json"), json.dumps(result, indent=2) + "\n")
        usage = result["model_usage"] or {}
        report = ["# Extracted package rehearsal", "",
                  f"Reproduced gate: **{result['gate']}**. Supported {result['supported_count']}/"
                  f"{result['request_count']}; blocked {result['blocked_count']}.", "",
                  "Imports originated in the extracted source. PYTHONPATH/PYTHONHOME were removed. "
                  "Declared installed dependencies were reused, with a fresh compiler/OCR cache. "
                  "All unit tests passed from the extracted archive.", "",
                  f"Cold preparation: {result['cold_preparation_seconds']:.3f}s; first all-request attempt "
                  f"including preparation: {result['cold_first_attempt_seconds']:.3f}s. "
                  "Package installation/download is excluded.", "",
                  f"Cold OCR calls: {usage.get('calls', 0)}; summed engine inference: {usage.get('inference_seconds', 0):.3f}s. "
                  f"Extraction-cache hits: {usage.get('cache_hits', 0)}; workers: {usage.get('workers_used', 0)}. "
                  "Summed parallel engine time can exceed wall time.", "",
                  "Prepared audit trials: " + ", ".join(f"{n:.3f}s" for n in result["audit_seconds"]) + ".", "",
                  "Verified output/blocker replay signatures match the original checkout. "
                  "A reproduced BLOCKED gate is not a submission-ready prediction CSV.", "",
                  "```text", result["tests"], "```"]
        _write(source / "evaluation" / "package_rehearsal.md", "\n".join(report) + "\n")
        files = build_archive(source, args.output)
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise ValueError("Archive integrity check failed")
    print(json.dumps(dict(archive=str(args.output.resolve()), files=len(files), bytes=args.output.stat().st_size,
                         sha256=hashlib.sha256(args.output.read_bytes()).hexdigest()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
