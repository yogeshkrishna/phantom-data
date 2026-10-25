"""Build failures preserve the last archive; extraction keeps independent evaluation runnable."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
import package_submission as package  # noqa: E402


class PackageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        shutil.copytree(CODE, self.root / "code", ignore=shutil.ignore_patterns("__pycache__", "evidence_cache"))
        shutil.copy(CODE.parent / "README.md", self.root / "README.md")
        for name in package.REPORTS:
            (self.root / "code" / "evaluation" / name).write_text("Synthetic report for isolated packaging test.\n",
                                                                 encoding="utf-8")
        self.repo_patch = patch.object(package, "REPO", self.root)
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)
        self.env_patch = patch.dict(os.environ, {"GHOST_AUDIT_DATA_DIR": str(Path(self.tmp.name) / "absent-corpus")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.target = Path(self.tmp.name) / "code.zip"

    def test_missing_report_preserves_previous_archive(self):
        package.build(self.target)
        previous = self.target.read_bytes()
        (self.root / "code" / "evaluation" / "usage_report.md").unlink()
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_archive_cannot_overwrite_corpus_or_submission_sources(self):
        package.build(self.target)
        previous_archive = self.target.read_bytes()
        corpus = Path(self.tmp.name) / "protected-corpus"
        corpus.mkdir()
        corpus_file = corpus / "deletion_rules.csv"
        corpus_file.write_bytes(b"synthetic corpus bytes must survive")
        targets = [self.root / "README.md", self.root / "code/main.py",
                   self.root / "code/evaluation/usage_report.md", self.root / "code/nested-output.zip", corpus_file]
        for target in targets:
            previous = target.read_bytes() if target.exists() else None
            with self.subTest(target=target), patch.dict(os.environ, {"GHOST_AUDIT_DATA_DIR": str(corpus)}):
                with self.assertRaisesRegex(ValueError, "archive output must"):
                    package.build(target)
            if previous is None:
                self.assertFalse(target.exists())
            else:
                self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(self.target.read_bytes(), previous_archive)

    def test_zip_write_and_publication_failures_preserve_previous_archive(self):
        package.build(self.target)
        previous = self.target.read_bytes()
        with patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("injected ZIP write failure")):
            with self.assertRaises(OSError):
                package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)
        with patch.object(package, "atomic_write_bytes", side_effect=OSError("injected publication failure")):
            with self.assertRaises(OSError):
                package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_clean_archive_has_identical_sources_policies_and_reproducible_hash(self):
        for relative in ("code/nested.zip", "code/log.txt", "code/output.csv", "code/.cache/derived.json",
                         "code/runs/predictions.csv", "code/dataset/private.csv", "code/data/corpus.csv",
                         "code/secrets/credentials.json", "code/.env", "code/raw-run.json", "code/transcript.log"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not ship", encoding="utf-8")
        names = package.build(self.target)
        previous = hashlib.sha256(self.target.read_bytes()).hexdigest()
        package.build(self.target)
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(), previous)
        with zipfile.ZipFile(self.target) as archive:
            self.assertIsNone(archive.testzip())
            for name in names:
                if name.startswith("code/") or name == "README.md":
                    self.assertEqual(archive.read(name), (self.root / name).read_bytes())
            for suite in ("suite", "suite_ambiguous", "suite_guard"):
                self.assertIn(f"code/evaluation/custom_cases/{suite}/data/deletion_rules.csv", names)
            self.assertEqual(archive.read("evaluation/usage_report.md"),
                             archive.read("code/evaluation/usage_report.md"))
        self.assertFalse(any(name.endswith(".zip") or Path(name).name in {"log.txt", "output.csv"} or "/.cache/" in name or
                             "/dataset/" in name or "/runs/" in name for name in names))
        self.assertNotIn("code/raw-run.json", names)
        self.assertNotIn("code/transcript.log", names)

    def test_copied_corpus_identifier_blocks_publication(self):
        package.build(self.target)
        previous = self.target.read_bytes()
        corpus = Path(self.tmp.name) / "corpus"
        corpus.mkdir()
        (corpus / "user_identities.csv").write_text(
            "user_id,name,email,phone,email_hash\nfictional_person,Fictional Example,fictional.person@example.invalid,,\n",
            encoding="utf-8")
        (self.root / "code" / "accidental_copy.py").write_text("CONTACT = 'fictional.person@example.invalid'\n",
                                                             encoding="utf-8")
        with patch.dict(os.environ, {"GHOST_AUDIT_DATA_DIR": str(corpus)}):
            with self.assertRaisesRegex(SystemExit, "contain challenge-dataset identifiers"):
                package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_disguised_nested_archive_cannot_enter_source_members(self):
        package.build(self.target)
        previous = self.target.read_bytes()
        with zipfile.ZipFile(self.root / "code" / "looks_like_source.py", "w") as nested:
            nested.writestr("hidden.txt", "not source")
        with self.assertRaisesRegex(ValueError, "non-text source|nested archive"):
            package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_missing_or_corpus_like_policy_fails_before_publication(self):
        package.build(self.target)
        previous = self.target.read_bytes()
        policy = self.root / "code/evaluation/custom_cases/suite/data/deletion_rules.csv"
        policy.write_text(policy.read_text(encoding="utf-8").replace("synthetic_rule_", "participant_rule_"),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not an independent synthetic"):
            package.build(self.target)
        self.assertEqual(self.target.read_bytes(), previous)
        policy.unlink()
        with self.assertRaisesRegex(ValueError, "missing synthetic policy"):
            package.build(self.target)

    def test_extracted_custom_evaluation_and_policy_rebuild_need_no_corpus(self):
        package.build(self.target)
        extracted = Path(self.tmp.name) / "extracted"
        with zipfile.ZipFile(self.target) as archive:
            archive.extractall(extracted)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "GHOST_AUDIT_DATA_DIR": str(extracted / "absent-corpus")}
        command = [sys.executable, "-B", "code/evaluation/main.py", "--custom"]
        result = subprocess.run(command, cwd=extracted, env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("exact_case_accuracy  1.0000"), 3, result.stdout)
        # The independent generator must also rebuild its first suite after the
        # old first-suite policy has been removed along with that suite.
        builder = extracted / "code/evaluation/custom_cases/build_custom_cases.py"
        rebuild = subprocess.run([sys.executable, "-B", str(builder)], cwd=extracted, env=env,
                                 text=True, capture_output=True, timeout=60)
        self.assertEqual(rebuild.returncode, 0, rebuild.stdout + rebuild.stderr)
        result = subprocess.run(command, cwd=extracted, env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("exact_case_accuracy  1.0000"), 3, result.stdout)


if __name__ == "__main__":
    unittest.main()
