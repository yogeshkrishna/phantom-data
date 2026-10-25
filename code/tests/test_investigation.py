"""Diagnostics distinguish membership/order/fields without changing evidence."""
import sys
import unittest
import json
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
from evaluation.investigation import (diff_steps, without_sql_booleans, without_sql_row_coverage,
                                      ObservationTimeOnly, raw_field_inventory)
from phantom.contracts import AuditStep, EvidenceRef
from phantom.state import StateIndex
from test_state import A, B, C, fact


class InvestigationTests(unittest.TestCase):
    def test_insertions_do_not_become_parameter_or_formatting_errors(self):
        first = asdict(AuditStep(1, "VERIFICATION", "service", "a", "VERIFY_RECORD_ABSENT", "ABSENT", "proof"))
        second = dict(first, step_no=2, resource_id="b")
        actual = [dict(second, step_no=1)]
        diff = diff_steps([first, second], actual)
        self.assertEqual(diff["missing"], [first])
        self.assertFalse(diff["changed"])
        self.assertFalse(diff["order_only"])

    def test_reordering_and_citation_mismatch_are_separate(self):
        first = asdict(AuditStep(1, "VERIFICATION", "service", "a", "VERIFY_RECORD_ABSENT", "ABSENT", "proof"))
        second = dict(first, step_no=2, resource_id="b")
        self.assertTrue(diff_steps([first, second], [dict(second, step_no=1), dict(first, step_no=2)])["order_only"])
        diff = diff_steps([first], [dict(first, evidence_id="other")])
        self.assertEqual(diff["changed"][0]["fields"], ["evidence_id"])
        self.assertFalse(diff["order_only"])

    def test_rule_removals_are_copies_and_native_sql_only(self):
        original = fact({"enabled": False}, metadata={"sql_table": "connector_subscriptions", "full_record": True})
        json_fact = fact({"enabled": False}, resource="json")
        weakened = without_sql_booleans((original, json_fact))
        self.assertIs(original.stored_fields["enabled"], False)
        self.assertIs(type(weakened[0].stored_fields["enabled"]), int)
        self.assertIs(weakened[1].stored_fields["enabled"], False)
        self.assertEqual(weakened[0].source, original.source)
        no_coverage = without_sql_row_coverage((original,))
        self.assertNotIn("full_record", no_coverage[0].metadata)
        self.assertTrue(original.metadata["full_record"])

    def test_unapproved_time_mode_never_rewrites_provenance(self):
        future = fact(time=A, source=EvidenceRef("future", "future.json", "a"*64, "/", "2030-01-01T00:00:00Z"))
        from phantom.contracts import parse_instant
        self.assertFalse(StateIndex._visible(future, parse_instant(C)))
        self.assertTrue(ObservationTimeOnly._visible(future, parse_instant(C)))
        self.assertEqual(future.source.available_at, "2030-01-01T00:00:00Z")

    def test_raw_audit_never_assigns_neighbors_retention_to_missing_record(self):
        from test_cache_compiler import dataset, write_csv
        with TemporaryDirectory() as directory:
            source = Path(directory) / "dataset"
            dataset(source)
            (source / "evidence.json").write_text(json.dumps(dict(records=[
                dict(resource_id="missing-boundary", subject_reference="u"),
                dict(resource_id="another-record", retention_until="2030-01-01T00:00:00Z")]
            )), encoding="utf-8")
            fields = raw_field_inventory(source)
        self.assertNotIn("retention_until", fields["missing-boundary"]["source"])
        self.assertIn("retention_until", fields["another-record"]["source"])


if __name__ == "__main__":
    unittest.main()
