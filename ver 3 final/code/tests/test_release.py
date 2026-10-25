"""Failure containment and independent evidence mutations."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import types

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
import main
from ghost_audit.atomic import atomic_write_bytes
from ghost_audit.output import write_output
from ghost_audit.release import validate_for_dataset, check_output_paths
from ghost_audit.verification import check_predictions
from ghost_audit.llm import estimate_cost

DATA = CODE / "evaluation/custom_cases/suite/data"


def step(**kwargs):
    return dict(step_no=1, phase="VERIFICATION", system_id="account_service", resource_id="account_synthetic",
                operation="VERIFY_RECORD_ABSENT", state="ABSENT", evidence_id="evidence_synthetic", **kwargs)


class PublicationTests(unittest.TestCase):
    def test_serialization_failure_preserves_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"previous valid artifact")
            bad = step()
            bad["state"] = object()
            with self.assertRaises(TypeError):
                write_output(out, [("synthetic_1", "COMPLETE", [bad])])
            self.assertEqual(out.read_bytes(), b"previous valid artifact")

    def test_malformed_contract_never_replaces_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"keep")
            malformed = step()
            malformed["step_no"] = True
            for rows in [[("synthetic_1", "COMPLETE", [malformed])], [("synthetic_1", "INCOMPLETE", [])]]:
                with self.assertRaises(ValueError):
                    write_output(out, rows)
            self.assertEqual(out.read_bytes(), b"keep")

    def test_replace_failure_preserves_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"keep")
            with patch("ghost_audit.atomic.os.replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    atomic_write_bytes(out, b"replacement")
            self.assertEqual(out.read_bytes(), b"keep")
            self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_stage_fsync_failure_preserves_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"keep")
            with patch("ghost_audit.atomic.os.fsync", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    atomic_write_bytes(out, b"replacement")
            self.assertEqual(out.read_bytes(), b"keep")
            self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_external_validation_precedes_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"keep")
            with self.assertRaises(ValueError):
                write_output(out, [("synthetic_missing", "COMPLETE", [step()])],
                             validator=lambda b: validate_for_dataset(b, DATA, "deletion_requests.csv"))
            self.assertEqual(out.read_bytes(), b"keep")

    def test_missing_media_blocks_cli_publication(self):
        failed = SimpleNamespace(coverage={"problems": [{"file_id": "synthetic_image", "status": "failed", "notes": "OCR missing"}]})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.csv"
            out.write_bytes(b"keep")
            with patch.object(main, "run_pipeline", return_value=failed):
                code = main.main(["--data-dir", str(DATA), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertEqual(out.read_bytes(), b"keep")

    def test_experimental_mode_cannot_replace_final_default_path(self):
        with patch.object(main, "run_pipeline") as pipeline:
            self.assertEqual(main.main(["--data-dir", str(DATA), "--candidate", "snapshot_coverage"]), 2)
            self.assertEqual(main.main(["--data-dir", str(DATA), "--llm"]), 2)
            pipeline.assert_not_called()

    def test_corpus_and_aliasing_output_paths_rejected(self):
        with self.assertRaises(ValueError):
            check_output_paths(DATA, [DATA / "anything.csv"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "same.csv"
            with self.assertRaises(ValueError):
                check_output_paths(DATA, [out, out])

    def test_unknown_model_price_stays_unknown(self):
        result = estimate_cost([{"model": "synthetic-unknown", "input_tokens": 10, "output_tokens": 2}])
        self.assertIsNone(result["synthetic-unknown"]["cost_usd"])

    def test_optional_model_response_must_match_actual_types(self):
        from ghost_audit.authorization import AuthorizationDecision
        from ghost_audit.llm import ClaudeFallback
        fallback = AuthorizationDecision(False, "unclear", [], False)
        sdk = types.SimpleNamespace(APIStatusError=RuntimeError, APIConnectionError=ConnectionError)
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace()))
        for payload in [[], {}, {"authorizes_deletion": "false", "intent": "deletion", "reason": "x"},
                        {"authorizes_deletion": True, "intent": "unrecognized", "reason": "x"},
                        {"authorizes_deletion": False, "intent": "access", "reason": 3}]:
            response = SimpleNamespace(model="synthetic-unverified", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                       stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])
            client.beta.messages.create = lambda **kwargs: response
            with patch.dict(sys.modules, {"anthropic": sdk}):
                self.assertIs(ClaudeFallback(client).classify("synthetic", fallback), fallback)

    def test_finding_without_remediation_cannot_be_exported(self):
        finding = step()
        finding.update(phase="FINDING", operation="REPORT_REMAINING_RECORD", state="PRESENT")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_output(Path(tmp) / "out.csv", [("synthetic_1", "INCOMPLETE", [finding])])

    def test_action_pair_cannot_cross_systems(self):
        from validate_output import validate_serialized
        from ghost_audit.output import serialize_output
        finding = step()
        finding.update(phase="FINDING", operation="REPORT_REMAINING_RECORD", state="PRESENT")
        remediation = dict(finding, step_no=2, phase="REMEDIATION", operation="DELETE_RECORD", state="ABSENT",
                           system_id="wrong_system", evidence_id="")
        verify = dict(remediation, step_no=3, phase="VERIFICATION", operation="VERIFY_RECORD_ABSENT")
        self.assertTrue(validate_serialized(serialize_output([("synthetic_1", "INCOMPLETE", [finding, remediation, verify])])))

    def test_consistent_suffix_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.csv"
            write_output(out, [("request10", "NOT_APPLICABLE", []), ("request2", "NOT_APPLICABLE", [])])
            with out.open(encoding="utf-8", newline="") as stream:
                self.assertEqual([r["case_id"] for r in csv.DictReader(stream)], ["request2", "request10"])


class IndependentCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (DATA / "deletion_requests.csv").open(encoding="utf-8", newline="") as stream:
            cls.request = next(csv.DictReader(stream))
        with (DATA / "evidence_files.csv").open(encoding="utf-8", newline="") as stream:
            cls.file = next(csv.DictReader(stream))
        from datetime import datetime, timezone
        cls.time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def report(self, assertion, *, present=False, keys=None, pii=None, refs=None, category="USER_PROFILE", flags=None, extra=None):
        fact = SimpleNamespace(resource_id="account_synthetic", system="account_service", file_id=self.file["file_id"],
                               obs_time=self.time, present=present, status=None, flags=flags or {}, pii=pii or {},
                               refs=refs or {}, link_keys=keys or set(), record_type=category)
        evidence = SimpleNamespace(observations=[fact] + (extra or []), scopes=[], docs=[])
        return check_predictions(DATA, [(self.request["case_id"], "COMPLETE", [assertion])], evidence=evidence)

    def test_valid_explicit_absence_is_not_contradicted(self):
        assertion = step()
        assertion["evidence_id"] = self.file["file_id"]
        report = self.report(assertion)
        self.assertEqual(report["errors"], 0)

    def test_fabricated_resource_is_detected(self):
        assertion = step()
        assertion.update(resource_id="synthetic_never_seen", evidence_id=self.file["file_id"])
        self.assertIn("unknown_resource", {i["code"] for i in self.report(assertion)["issues"]})

    def test_wrong_citation_and_state_are_detected(self):
        assertion = step()
        assertion["evidence_id"] = "synthetic_wrong_file"
        self.assertIn("citation_resource", {i["code"] for i in self.report(assertion)["issues"]})
        assertion["evidence_id"] = self.file["file_id"]
        self.assertIn("contradicted_state", {i["code"] for i in self.report(assertion, present=True)["issues"]})

    def test_checker_does_not_import_planner_decisions(self):
        import inspect
        import ghost_audit.verification as verification
        source = inspect.getsource(verification)
        self.assertNotIn("from .planner import", source)
        self.assertNotIn("from .state import", source)
        self.assertNotIn("from .policy import", source)

    def test_retention_and_wrong_owner_mutations_are_visible(self):
        assertion = step()
        assertion["evidence_id"] = self.file["file_id"]
        retained = self.report(assertion, category="SECURITY_AUDIT_LOG", flags={"retention_until": "2031-12-31T00:00:00Z"})
        self.assertIn("policy_conflict", {i["code"] for i in retained["issues"]})
        wrong_owner = self.report(assertion, keys={"user:user_902"})
        self.assertIn("ownership_conflict", {i["code"] for i in wrong_owner["issues"]})

    def test_newer_contradiction_in_same_citation_is_detected(self):
        from datetime import timedelta
        assertion = step()
        assertion["evidence_id"] = self.file["file_id"]
        newer = SimpleNamespace(resource_id="account_synthetic", system="account_service", file_id=self.file["file_id"],
                                obs_time=self.time + timedelta(hours=1), present=True, status=None, flags={}, pii={}, refs={},
                                link_keys=set(), record_type="USER_PROFILE")
        report = self.report(assertion, extra=[newer])
        self.assertIn("contradicted_state", {i["code"] for i in report["issues"]})

    def test_checker_report_paths_cannot_alias_or_write_corpus(self):
        import check_evidence
        with self.assertRaises(ValueError):
            check_evidence.main(["not-read.csv", "--data-dir", str(DATA), "--report", str(DATA / "deletion_rules.csv")])
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "same.csv")
            with self.assertRaises(SystemExit):
                check_evidence.main([out, "--data-dir", str(DATA), "--report", out])


if __name__ == "__main__":
    unittest.main()
