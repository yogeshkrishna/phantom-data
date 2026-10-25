"""Final-review regression tests: completion guard, finding citations, strict cutoff mode,
validator handling of verification-only INCOMPLETE cases, and packaging exclusions."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
REPO = CODE.parent
sys.path.insert(0, str(CODE))

from ghost_audit.evidence.model import Observation, Scope  # noqa: E402
from ghost_audit.linker import CaseContext, Link  # noqa: E402
from ghost_audit.pipeline import run_pipeline  # noqa: E402
from ghost_audit.planner import Planner  # noqa: E402
from ghost_audit.policy import Policy  # noqa: E402

import package_submission  # noqa: E402
import validate_output  # noqa: E402

UTC = timezone.utc
AUDIT = datetime(2026, 8, 20, 12, tzinfo=UTC)
CUSTOM = CODE / "evaluation" / "custom_cases"
with open(CODE / "evaluation" / "custom_cases" / "suite" / "data" / "deletion_rules.csv", encoding="utf-8") as _fh:
    RULES = list(csv.DictReader(_fh))


def ensure_fixture_rules():
    spec = importlib.util.spec_from_file_location("build_custom_cases", CUSTOM / "build_custom_cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_rules()


def _case():
    return CaseContext("case_x", {}, None, AUDIT - timedelta(days=9), AUDIT - timedelta(days=7), AUDIT,
                       "COMPLETE", True)


def _obs(file_id, rid, system, present, hours_before, **kw):
    t = AUDIT - timedelta(hours=hours_before)
    return Observation(file_id, t, t, system, rid, present, **kw)


def _planner(observations, scopes=()):
    by_res = {}
    for o in observations:
        by_res.setdefault(o.resource_id, []).append(o)
    files = {o.file_id: o.captured_at for o in observations}
    files.update({s.file_id: s.captured_at for s in scopes})
    return Planner(Policy(RULES), by_res, list(scopes), files)


AUDIT_REC = _obs("evidence_1", "audit_record_1", "security_service", True, 2,
                 flags={"retention_until": "2031-12-31T23:59:59Z"})
LINKS = {"audit_record_1": Link("audit_record_1", "security_service", "evidence", ["evidence_1"], "direct")}


class CompletionGuardTests(unittest.TestCase):
    def test_tie_candidate_still_present_blocks_complete(self):
        remaining = _obs("evidence_2", "account_1", "account_service", True, 1)
        res = _planner([AUDIT_REC, remaining]).assess(_case(), LINKS, [("account_1", "account_service")])
        self.assertEqual(res.status, "INCOMPLETE")
        # nothing is asserted about the unattributed account; only the attributed record's check remains
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in res.steps],
                         [("VERIFY_RECORD_PRESENT", "audit_record_1", "")])
        self.assertTrue(any("tie candidate" in n for n in res.notes))

    def test_tie_whose_candidates_are_all_compliant_allows_complete(self):
        gone = _obs("evidence_2", "account_1", "account_service", False, 1)
        res = _planner([AUDIT_REC, gone]).assess(_case(), LINKS, [("account_1", "account_service")])
        self.assertEqual(res.status, "COMPLETE")
        self.assertEqual([(s["operation"], s["evidence_id"]) for s in res.steps],
                         [("VERIFY_RECORD_PRESENT", "evidence_1")])
        self.assertTrue(any("unattributed tie" in n for n in res.notes))

    def test_unknown_state_of_a_corroborated_record_blocks_complete(self):
        links = dict(LINKS, analytics_1=Link("analytics_1", "analytics_platform", "workflow", ["activity_1"], "strong"))
        res = _planner([AUDIT_REC]).assess(_case(), links)
        self.assertEqual(res.status, "INCOMPLETE")
        self.assertTrue(any("analytics_1: state at audit_at unknown" in n for n in res.notes))

    def test_unknown_state_of_a_weak_inference_is_reported_not_blocking(self):
        links = dict(LINKS, analytics_1=Link("analytics_1", "analytics_platform", "workflow", ["activity_1"], "weak"))
        res = _planner([AUDIT_REC]).assess(_case(), links)
        self.assertEqual(res.status, "COMPLETE")
        self.assertTrue(any("analytics_1 (weak link): no citable evidence" in n for n in res.notes))

    def test_custom_guard_suite_never_reports_false_complete(self):
        ensure_fixture_rules()
        res = run_pipeline(CUSTOM / "suite_guard" / "data")
        self.assertEqual({r.case_id: r.status for r in res.results}, {"cust_13": "INCOMPLETE", "cust_14": "INCOMPLETE"})
        self.assertFalse(any(s["phase"] == "FINDING" for r in res.results for s in r.steps))


class FindingCitationTests(unittest.TestCase):
    def test_fallback_cites_only_a_file_showing_the_violation(self):
        redacted = _obs("evidence_3", "support_1", "support_platform", True, 5, status="REDACTED",
                        record_type="SUPPORT_MESSAGE")
        planner = _planner([redacted])
        link = Link("support_1", "support_platform", "evidence", ["evidence_3"], "direct")
        self.assertEqual(planner._fallback_evidence(link, "REDACT", AUDIT), "")  # mentions it, shows no violation
        exposed = _obs("evidence_4", "support_1", "support_platform", True, 4, record_type="SUPPORT_MESSAGE",
                       pii={"email": "someone@example.org"})
        self.assertEqual(_planner([redacted, exposed])._fallback_evidence(link, "REDACT", AUDIT), "evidence_4")

    def test_violation_seen_only_in_late_file_is_not_cited_and_not_complete(self):
        late = Observation("evidence_5", AUDIT + timedelta(days=1), AUDIT - timedelta(hours=3), "search_service",
                           "search_1", True)
        links = dict(LINKS, search_1=Link("search_1", "search_service", "evidence", ["evidence_5"], "direct"))
        res = _planner([AUDIT_REC, late]).assess(_case(), links)
        self.assertEqual(res.status, "INCOMPLETE")
        self.assertFalse(any(s["evidence_id"] == "evidence_5" for s in res.steps))
        self.assertTrue(any("captured after audit_at" in n for n in res.notes))


class StrictCutoffTests(unittest.TestCase):
    def test_public_examples_rely_on_pre_cutoff_lines_of_late_files(self):
        # case_26's account is identified only by a bundle exported two days after audit_at whose
        # inner snapshot predates the cutoff; the published answer includes it (interpretation A).
        data = os.environ.get("GHOST_AUDIT_TEST_DATA_DIR")
        if not data:
            self.skipTest("optional external-corpus regression; set GHOST_AUDIT_TEST_DATA_DIR")
        default = {r.case_id: r for r in run_pipeline(Path(data), "sample_deletion_requests.csv").results}
        strict = {r.case_id: r for r in run_pipeline(Path(data), "sample_deletion_requests.csv",
                                                     strict_capture_cutoff=True).results}
        self.assertIn("account_5684", {s["resource_id"] for s in default["case_26"].steps})
        self.assertNotIn("account_5684", {s["resource_id"] for s in strict["case_26"].steps})

    def test_strict_mode_ignores_late_files_for_state(self):
        early = _obs("evidence_6", "search_1", "search_service", True, 30)
        late_absent = Observation("evidence_7", AUDIT + timedelta(days=1), AUDIT - timedelta(hours=2),
                                  "search_service", "search_1", False)
        links = dict(LINKS, search_1=Link("search_1", "search_service", "evidence", ["evidence_6"], "direct"))
        planner = _planner([AUDIT_REC, early, late_absent])
        default = planner.assess(_case(), links)
        self.assertEqual(default.status, "COMPLETE")  # the pre-cutoff line informs state...
        self.assertNotIn("search_1", {s["resource_id"] for s in default.steps})  # ...but is never cited
        planner.strict_capture = True
        res = planner.assess(_case(), links)
        self.assertEqual(res.status, "INCOMPLETE")
        self.assertIn(("REPORT_REMAINING_RECORD", "evidence_6"),
                      {(s["operation"], s["evidence_id"]) for s in res.steps})


class ValidatorTests(unittest.TestCase):
    def test_verification_only_incomplete_is_a_warning_not_an_error(self):
        data = CODE / "evaluation" / "custom_cases" / "suite" / "data"
        with open(data.parent / "expected_output.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        target = next(r for r in rows if r["deletion_status"] == "COMPLETE")
        steps = json.loads(target["steps_json"])
        for s in steps:
            s["evidence_id"] = ""
        target.update(deletion_status="INCOMPLETE", steps_json=json.dumps(steps))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.csv"
            with open(out, "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, ["case_id", "deletion_status", "steps_json"], lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
            warnings = []
            self.assertEqual(validate_output.validate(out, data, "deletion_requests.csv", warnings), [])
            self.assertEqual(len(warnings), 1)


class PackagingTests(unittest.TestCase):
    def test_ocr_cache_and_dataset_copies_are_never_packaged(self):
        cache = CODE / "ghost_audit" / "evidence_cache" / "evidence_00_0000000000000000.txt"
        rules_copy = CUSTOM / "suite" / "data" / "deletion_rules.csv"
        digests = package_submission.dataset_digests()
        self.assertFalse(package_submission.wanted(cache, digests))
        self.assertFalse(package_submission.wanted(rules_copy, digests))
        self.assertTrue(package_submission.wanted(CODE / "main.py", digests))

    def test_shipped_sources_contain_no_dataset_identifiers(self):
        digests = package_submission.dataset_digests()
        sources = [REPO / "README.md"] + [p for p in sorted(CODE.rglob("*")) if package_submission.wanted(p, digests)]
        self.assertEqual(package_submission.copied_identifiers(sources), [])


if __name__ == "__main__":
    unittest.main()
