"""Optional read-only integrations with the participant corpus, never answers."""
import hashlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.corpus import read_manifest
from phantom.json_evidence import read_json_evidence
from phantom.sql_evidence import read_sql_evidence

DATASET = Path(os.environ.get("PHANTOM_DATASET", Path(__file__).resolve().parents[3]
                              / "research" / "october26" / "dataset"))


@unittest.skipUnless((DATASET / "evidence_files.csv").is_file(), "Participant corpus not configured")
class RealEvidenceTests(unittest.TestCase):
    def test_sql_coverage_and_source_integrity(self):
        entries = [entry for entry in read_manifest(DATASET) if entry.path.suffix == ".sql"]
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(evidence_id=entry.file_id):
                batch = read_sql_evidence(entry)
                self.assertFalse(batch.diagnostics)
                self.assertEqual(batch.records_seen, batch.records_normalized)
                self.assertEqual(hashlib.sha256(entry.path.read_bytes()).hexdigest(), entry.sha256)

    def test_json_coverage_and_source_integrity(self):
        entries = [entry for entry in read_manifest(DATASET) if entry.path.suffix == ".json"]
        self.assertTrue(entries)
        layouts = set()
        for entry in entries:
            with self.subTest(evidence_id=entry.file_id):
                batch = read_json_evidence(entry)
                layouts.add(batch.layout)
                self.assertFalse(batch.diagnostics)
                self.assertEqual(batch.records_seen, batch.records_normalized)
                self.assertEqual(hashlib.sha256(entry.path.read_bytes()).hexdigest(), entry.sha256)
        self.assertEqual(layouts, {"snapshot_bundle", "service_exports", "standalone_records", "readback_history"})


if __name__ == "__main__":
    unittest.main()
