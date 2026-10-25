"""Build code.zip at the repository root.

Contents: README.md, code/ (source, tests, prompts, evaluation reports and synthetic custom
cases) and evaluation/ (identical copies of the two reports, for the AGENTS.md path).
Excluded: dataset/, .git/, virtual environments, caches (including the screenshot OCR cache,
which is text derived from the challenge evidence), secrets/.env files, nested archives.

Also excluded: any file byte-identical to a file in dataset/ (the custom fixtures' copy of
deletion_rules.csv is restored from dataset/ at evaluation time). Before writing, every shipped
text file is checked against the identifiers in dataset/ (emails, phones, email hashes, names);
the build fails if any challenge identifier was copied into it.

    python code/package_submission.py [--out path/to/code.zip]
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "code.zip"
EXCLUDED_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", ".mypy_cache",
                 "evidence_cache"}
EXCLUDED_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc", ".pyo"}
EXCLUDED_NAMES = {".env", ".DS_Store"}
REPORTS = ("evaluation_report.md", "usage_report.md")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_digests() -> set[str]:
    data = REPO / "dataset"
    return {_digest(p) for p in data.rglob("*") if p.is_file()} if data.exists() else set()


def wanted(path: Path, dataset_files: set[str] | None = None) -> bool:
    rel = path.relative_to(REPO)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES or path.name.startswith(".env"):
        return False
    if "custom_cases" in rel.parts and path.name == "deletion_rules.csv":
        return False  # the challenge's policy file; restored by build_custom_cases.ensure_rules()
    if not path.is_file():
        return False
    return dataset_files is None or _digest(path) not in dataset_files


def dataset_identifiers() -> set[str]:
    """Personal identifiers of the challenge dataset (empty when dataset/ is not present)."""
    data = REPO / "dataset"
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


def build(out: Path = OUT) -> list[str]:
    copies = dataset_digests()
    sources = [REPO / "README.md"] + [p for p in sorted((REPO / "code").rglob("*")) if wanted(p, copies)]
    problems = copied_identifiers(sources)
    if problems:
        raise SystemExit("refusing to package files that contain challenge-dataset identifiers:\n  "
                         + "\n  ".join(problems))
    names = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(REPO / "README.md", "README.md")
        names.append("README.md")
        for path in sources[1:]:
            arc = path.relative_to(REPO).as_posix()
            zf.write(path, arc)
            names.append(arc)
        for report in REPORTS:
            src = REPO / "code" / "evaluation" / report
            zf.write(src, f"evaluation/{report}")
            names.append(f"evaluation/{report}")
    return names


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT), help="archive path (default: code.zip at the repository root)")
    target = Path(ap.parse_args().out)
    files = build(target)
    print(f"wrote {target} with {len(files)} files")
