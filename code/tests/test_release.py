"""Release gates must preserve output on failure and measure genuine attempts."""
import csv
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
from release import run_release
from test_cache_compiler import dataset


class ReleaseTests(unittest.TestCase):
    def test_passing_fixture_produces_complete_output_and_reports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dataset"
            dataset(source)
            outcome = run_release(source, root / "cache", root / "out" / "output.csv",
                                  root / "runs", root / "reports", repeats=2)
            self.assertEqual(outcome["status"], "PASS")
            self.assertEqual(outcome["request_count"], 1)
            self.assertEqual(len(outcome["audit_seconds"]), 2)
            self.assertTrue(outcome["output_written"])
            self.assertTrue(outcome["output_sha256"])
            with (root / "out" / "output.csv").open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            self.assertIn("0 blocked", (root / "reports" / "usage_report.md").read_text())

    def test_blocked_fixture_preserves_output_and_records_missing_proof(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dataset"
            dataset(source)
            path = source / "evidence.json"
            raw = json.loads(path.read_text())
            raw["records"][0]["lookup_value"] = "unrelated@example.test"
            path.write_text(json.dumps(raw))
            output = root / "output.csv"
            output.write_bytes(b"prior result\n")
            outcome = run_release(source, root / "cache", output, root / "runs", root / "reports")
            self.assertEqual(outcome["status"], "BLOCKED")
            self.assertEqual(outcome["supported_count"], 0)
            self.assertEqual(output.read_bytes(), b"prior result\n")
            self.assertFalse(outcome["output_written"])
            self.assertIsNone(outcome["output_sha256"])
            self.assertEqual(outcome["blocked"][0]["error"], "NO_RELEVANT_RESOURCE_PROOF")
            self.assertNotIn("unrelated@example.test", (root / "reports" / "release_readiness.md").read_text())

    def test_corpus_output_and_invalid_repeat_count_rejected_before_loading(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dataset"
            dataset(source)
            with self.assertRaises(ValueError):
                run_release(source, root / "cache", source / "output.csv", root / "runs", root / "reports")
            with self.assertRaises(ValueError):
                run_release(source, root / "cache", root / "output.csv", root / "runs", root / "reports", repeats=0)


if __name__ == "__main__":
    unittest.main()
