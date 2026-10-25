"""Source archives exclude inputs, caches, credentials and generated artifacts."""
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
from package import ROOT_FILES, build_archive


class PackageTests(unittest.TestCase):
    def test_allowlist_and_reproducible_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "code"
            source.mkdir()
            for name in ROOT_FILES:
                (source / name).write_text("source\n", encoding="utf-8")
            for name in ("phantom/engine.py", "evaluation/evaluation_report.md", "tests/test_engine.py",
                         "dataset/raw.csv", "phantom/model.onnx", ".env", "log.txt", "WORK_STATE.md",
                         ".cache/hidden.py", "evaluation/stale_report.md", "phantom/__pycache__/raw.pyc"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            first, second = root / "first.zip", root / "second.zip"
            selected = build_archive(source, first)
            build_archive(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(set(selected), ROOT_FILES | {
                "phantom/engine.py", "evaluation/evaluation_report.md", "tests/test_engine.py"})
            with zipfile.ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())

    def test_nested_archive_and_missing_entrypoints_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "code"
            source.mkdir()
            with self.assertRaises(ValueError):
                build_archive(source, source / "code.zip")
            with self.assertRaises(ValueError):
                build_archive(source, root / "code.zip")


if __name__ == "__main__":
    unittest.main()
