"""Build code.zip at the repository root.

Contents: README.md, code/ (source, tests, prompts, evaluation reports and synthetic custom
cases) and evaluation/ (identical copies of the two reports, for the AGENTS.md path).
Excluded: dataset/, .git/, virtual environments, caches (including the screenshot OCR cache,
which is text derived from the challenge evidence), secrets/.env files, nested archives.

Also excluded: any file byte-identical to a file in the external corpus. The independent
synthetic policy is explicitly checked and shipped with each custom suite. Every shipped
text file is checked against the identifiers in dataset/ (emails, phones, email hashes, names);
the build fails if any challenge identifier was copied into it.

    python code/package_submission.py [--out path/to/code.zip]
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
import zipfile
from pathlib import Path

from ghost_audit.atomic import atomic_write_bytes

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "code.zip"
EXCLUDED_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", ".mypy_cache",
                 "evidence_cache", ".cache", "cache", "caches", "planning", "artifacts", "runs", "dataset",
                 "secrets", "credentials", "transcripts", "build", "dist"}
EXCLUDED_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc", ".pyo"}
EXCLUDED_NAMES = {".env", ".DS_Store"}
REPORTS = ("evaluation_report.md", "usage_report.md")
SOURCE_SUFFIXES = {".py", ".md", ".txt", ".csv", ".json", ".sql", ".log"}
REQUIRED = ("README.md", "code/main.py", "code/validate_output.py", "code/package_submission.py",
            "code/requirements.txt", "code/requirements-lock.txt", "code/evaluation/main.py",
            "code/evaluation/custom_cases/build_custom_cases.py")


def corpus_dir() -> Path:
    return Path(os.environ.get("GHOST_AUDIT_DATA_DIR", REPO / "dataset"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_digests() -> set[str]:
    data = corpus_dir()
    return {_digest(p) for p in data.rglob("*") if p.is_file()} if data.exists() else set()


def wanted(path: Path, dataset_files: set[str] | None = None) -> bool:
    rel = path.relative_to(REPO)
    if any(part.lower() in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES or path.name.startswith(".env"):
        return False
    if "custom_cases" in rel.parts and path.name == "deletion_rules.csv":
        return False  # included only through the independent-policy preflight below
    if "data" in rel.parts and "custom_cases" not in rel.parts:
        return False
    if path.suffix.lower() not in SOURCE_SUFFIXES or path.name.lower() in {"log.txt", "output.csv", "usage.json"}:
        return False
    if "custom_cases" not in rel.parts and path.suffix.lower() not in {".py", ".md"}:
        if path.name not in {"requirements.txt", "requirements-lock.txt"}:
            return False
    if not path.is_file():
        return False
    return dataset_files is None or _digest(path) not in dataset_files


def dataset_identifiers() -> set[str]:
    """Personal identifiers of the challenge dataset (empty when dataset/ is not present)."""
    data = corpus_dir()
    found = set()
    if (data / "user_identities.csv").exists():
        with open(data / "user_identities.csv", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                found |= {row.get(k, "") for k in ("name", "email", "phone", "email_hash")}
    if (data / "user_audit_log.csv").exists():
        with open(data / "user_audit_log.csv", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                found |= {row.get("old_value", ""), row.get("new_value", "")}
    email = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
    texts = [p for p in sorted((data / "evidence").rglob("*")) if (data / "evidence").exists()
             and p.suffix.lower() in (".csv", ".json", ".log", ".txt", ".sql")]
    texts += sorted((REPO / "code" / "ghost_audit" / "evidence_cache").glob("*.txt"))  # local OCR text
    for path in texts:
        found |= set(email.findall(path.read_text(encoding="utf-8", errors="ignore").lower()))
    try:
        from pypdf import PdfReader

        for path in sorted((data / "evidence").rglob("*.pdf")):
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
            found |= set(email.findall(text.lower()))
    except ImportError:
        pass
    return {v.strip().lower() for v in found if len(v.strip()) >= 6}


def copied_identifiers(paths: list[Path]) -> list[str]:
    ids = dataset_identifiers()
    problems = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        hits = sorted(i for i in ids if i in text)
        if hits:
            problems.append(f"{path.relative_to(REPO)}: {len(hits)} dataset identifier(s)")
    return problems


def _synthetic_policies() -> list[Path]:
    """Policy fixtures must be independent synthetic data, never corpus copies."""
    root = REPO / "code" / "evaluation" / "custom_cases"
    policies = []
    for suite in sorted(p for p in root.iterdir() if (p / "data").is_dir()):
        path = suite / "data" / "deletion_rules.csv"
        if not path.is_file():
            raise ValueError(f"missing synthetic policy fixture: {path.relative_to(REPO)}")
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        if reader.fieldnames != ["rule_id", "data_category", "condition", "required_action"] or not rows:
            raise ValueError(f"invalid synthetic policy: {path.relative_to(REPO)}")
        if any(not row["rule_id"].startswith("synthetic_rule_") or
               row["condition"] != "Synthetic policy for isolated regression tests" for row in rows):
            raise ValueError(f"policy is not an independent synthetic fixture: {path.relative_to(REPO)}")
        policies.append(path)
    if not policies:
        raise ValueError("no synthetic evaluation suites found")
    return policies


def build(out: Path = OUT) -> list[str]:
    """Preflight and validate a complete archive before atomically publishing it."""
    out = Path(out)
    destination = out.resolve()
    if destination.is_relative_to(corpus_dir().resolve()):
        raise ValueError("archive output must be outside the read-only corpus")
    if destination.is_relative_to((REPO / "code").resolve()) or destination == (REPO / "README.md").resolve():
        raise ValueError("archive output must not overwrite submission source files")
    required = [REPO / name for name in REQUIRED]
    required += [REPO / "code" / "evaluation" / report for report in REPORTS]
    missing = [str(p.relative_to(REPO)) for p in required if not p.is_file() or not p.stat().st_size]
    if missing:
        raise ValueError("missing or empty required submission files: " + ", ".join(missing))
    copies = dataset_digests()
    sources = [REPO / "README.md"] + [p for p in sorted((REPO / "code").rglob("*")) if wanted(p, copies)]
    sources += _synthetic_policies()
    if any(p.is_symlink() or any(parent.is_symlink() for parent in p.parents if parent != REPO.parent)
           for p in sources):
        raise ValueError("refusing to package symlinked source files or directories")
    omitted = [str(p.relative_to(REPO)) for p in required if p not in sources]
    if omitted:
        raise ValueError("required files excluded by package filter: " + ", ".join(omitted))
    problems = copied_identifiers(sources)
    if problems:
        raise SystemExit("refusing to package files that contain challenge-dataset identifiers:\n  "
                         + "\n  ".join(problems))
    payloads = {p.relative_to(REPO).as_posix(): p.read_bytes() for p in sources}
    for name, value in payloads.items():
        try:
            value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"non-text source file is not permitted in code archive: {name}") from error
        if zipfile.is_zipfile(io.BytesIO(value)):
            raise ValueError(f"nested archive disguised as a source file: {name}")
    if any(hashlib.sha256(value).hexdigest() in copies for value in payloads.values()):
        raise ValueError("refusing to package a byte-identical corpus file")
    for report in REPORTS:
        payloads[f"evaluation/{report}"] = payloads[f"code/evaluation/{report}"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc, value in sorted(payloads.items()):
            # Stable metadata makes archive hashes reproducible across builds.
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, value)
    archive = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if zf.testzip() or set(zf.namelist()) != set(payloads):
            raise ValueError("constructed archive failed integrity checks")
        if any(zf.read(name) != value for name, value in payloads.items()):
            raise ValueError("constructed archive changed source bytes")
    atomic_write_bytes(out, archive)
    names = sorted(payloads)
    return names


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT), help="archive path (default: code.zip at the repository root)")
    target = Path(ap.parse_args().out)
    files = build(target)
    print(f"wrote {target} with {len(files)} files")
