"""Boundary checks independent of the original adapter implementation tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef
from phantom.record_snapshots import extract_record_snapshot


class SnapshotBoundaryTests(unittest.TestCase):
    def test_invalid_locator_types(self):
        snapshot = dict(captured_at="2026-01-01T00:00:00Z", data_store="account_service",
                        collection="customer_profiles", records=[])
        for locator in (None, 1, False, [], "invalid"):
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                extract_record_snapshot(snapshot, EvidenceRef("e", "p", "a" * 64,
                                        locator, "2026-01-01T00:00:00Z"))

    def test_timestamp_error_does_not_expose_value(self):
        snapshot = dict(captured_at="private@example.test", data_store="account_service",
                        collection="customer_profiles", records=[])
        with self.assertRaises(ValueError) as caught:
            extract_record_snapshot(snapshot, EvidenceRef("e", "p", "a" * 64,
                                    "", "2026-01-01T00:00:00Z"))
        self.assertNotIn("private@example.test", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
