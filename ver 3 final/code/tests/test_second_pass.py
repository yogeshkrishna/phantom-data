"""Second-pass regression tests: timestamps, identity uniqueness, evidence validity, ordering,
catalog conflicts, ambiguity labelling, partial captures and screenshot indicators."""

from __future__ import annotations

import csv
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

from ghost_audit.evidence.model import EvidenceDoc, Observation, Scope  # noqa: E402
from ghost_audit.identity import IdentityIndex  # noqa: E402
from ghost_audit.linker import Linker  # noqa: E402
from ghost_audit.loaders import parse_ts  # noqa: E402
from ghost_audit.output import order_steps, write_output  # noqa: E402
from ghost_audit.pipeline import run_pipeline  # noqa: E402
from ghost_audit.state import reconstruct  # noqa: E402

import validate_output  # noqa: E402

CUSTOM = CODE / "evaluation" / "custom_cases"
UTC = timezone.utc


def setUpModule():
    from test_final_review import ensure_fixture_rules

    ensure_fixture_rules()  # use the included synthetic policy


class TimestampTests(unittest.TestCase):
    def test_formats_and_offsets(self):
        self.assertEqual(parse_ts("2026-09-15T12:00:00Z"), datetime(2026, 9, 15, 12, tzinfo=UTC))
        self.assertEqual(parse_ts("2026-09-15T14:00:00+02:00"), datetime(2026, 9, 15, 12, tzinfo=UTC))
        self.assertEqual(parse_ts("2026-09-15 12:00:00"), datetime(2026, 9, 15, 12, tzinfo=UTC))
        self.assertIsNone(parse_ts(""))
        self.assertIsNone(parse_ts("not a time"))

    def test_cutoff_is_inclusive_and_strict_after(self):
        audit = datetime(2026, 9, 15, 12, tzinfo=UTC)

        def obs(t, present):
            return Observation("evidence_x", t, t, "account_service", "account_1", present)
        at_cutoff = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", [obs(audit, True)], [],
                                audit, {"evidence_x": audit})
        self.assertFalse(at_cutoff.compliant)  # an observation exactly at audit_at counts
        after = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE",
                            [obs(audit - timedelta(hours=1), False), obs(audit + timedelta(seconds=1), True)], [],
                            audit, {"evidence_x": audit})
        self.assertTrue(after.compliant)  # one second after the cutoff is ignored


class IdentityUniquenessTests(unittest.TestCase):
    def test_shared_identifier_never_links(self):
        rows = [{"user_id": "user_1", "name": "A", "email": "a@x.org", "phone": "+1 555 0100", "email_hash": "1" * 64,
                 "updated_at": ""},
                {"user_id": "user_2", "name": "B", "email": "b@x.org", "phone": "+1 555 0100", "email_hash": "2" * 64,
                 "updated_at": ""}]
        idx = IdentityIndex(rows, [])
        self.assertEqual(idx.owners_of({"phone:15550100"}), {"user_1", "user_2"})
        linker = Linker.__new__(Linker)
        linker.ambiguous_keys = {k for k, o in idx.key_owner.items() if len(o) > 1}
        self.assertNotIn("phone:15550100", linker._strong(idx.subjects["user_1"].keys))
        self.assertIn("email:a@x.org", linker._strong(idx.subjects["user_1"].keys))

    def test_email_resolution_is_exact(self):
        idx = IdentityIndex([{"user_id": "user_1", "name": "A", "email": "Ana@X.org", "phone": "", "email_hash": "",
                              "updated_at": ""}], [])
        self.assertEqual(idx.resolve_email("ana@x.ORG").user_id, "user_1")
        self.assertIsNone(idx.resolve_email("ana.jr@x.org"))
        self.assertIsNone(idx.resolve_email("na@x.org"))


class EvidenceValidityTests(unittest.TestCase):
    def test_validator_rejects_unknown_and_late_evidence(self):
        data = CUSTOM / "suite" / "data"
        with open(data.parent / "expected_output.csv", encoding="utf-8") as fh:
            gold = list(csv.DictReader(fh))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.csv"
            rows = []
            for r in gold:
                steps = json.loads(r["steps_json"])
                if r["case_id"] == "cust_01":
                    steps[0]["evidence_id"] = "evidence_999"  # does not exist
                if r["case_id"] == "cust_02":
                    steps[0]["evidence_id"] = "evidence_903"  # captured after this synthetic case's audit_at
                if r["case_id"] == "cust_03":
                    steps[0]["evidence_id"] = "activity_9003"  # activity ids are valid evidence ids
                rows.append((r["case_id"], r["deletion_status"], steps))
            write_output(out, rows)
            errors = validate_output.validate(out, data, "deletion_requests.csv")
        self.assertTrue(any("cust_01" in e and "unknown evidence" in e for e in errors))
        self.assertTrue(any("cust_02" in e and "later than audit_at" in e for e in errors))
        self.assertFalse(any("cust_03" in e for e in errors))


class OrderingTests(unittest.TestCase):
    def test_flow_and_restore_blocks_precede_record_changes(self):
        def s(op, phase, state, system, rid):
            return {"step_no": 0, "phase": phase, "system_id": system, "resource_id": rid, "operation": op,
                    "state": state, "evidence_id": ""}
        steps = order_steps([s("DELETE_RECORD", "REMEDIATION", "ABSENT", "account_service", "account_1"),
                             s("BLOCK_RESTORE", "REMEDIATION", "BLOCKED", "backup_service", "backup_1"),
                             s("DISABLE_DATA_FLOW", "REMEDIATION", "DISABLED", "integration_service", "data_flow_1"),
                             s("REDACT_RECORD", "REMEDIATION", "REDACTED", "identity_service", "tombstone_1")])
        self.assertEqual([x["operation"] for x in steps],
                         ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD"])

    def test_resource_ids_sort_as_strings_within_system(self):
        def s(rid):
            return {"step_no": 0, "phase": "VERIFICATION", "system_id": "search_service", "resource_id": rid,
                    "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""}
        self.assertEqual([x["resource_id"] for x in order_steps([s("search_898"), s("search_1308")])],
                         ["search_1308", "search_898"])


class PartialCaptureTests(unittest.TestCase):
    def test_query_export_cannot_override_explicit_presence(self):
        audit = datetime(2026, 7, 20, 12, tzinfo=UTC)
        present = Observation("evidence_a", audit - timedelta(days=2), audit - timedelta(days=2), "account_service",
                              "account_1", True)
        query = Scope("evidence_b", audit - timedelta(hours=1), audit - timedelta(hours=1), "account_service",
                      "profile_lookups", set(), kind="query")
        snap = Scope("evidence_c", audit - timedelta(hours=1), audit - timedelta(hours=1), "account_service",
                     "customer_profiles", set(), kind="snapshot")
        files = {"evidence_a": present.captured_at, "evidence_b": query.captured_at, "evidence_c": snap.captured_at}
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", [present], [query], audit, files)
        self.assertFalse(st.compliant)
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", [present], [snap], audit, files)
        self.assertTrue(st.compliant)  # a snapshot taken for this audit that omits it does override
        self.assertEqual(st.basis, "omission_override")

    def test_old_filtered_capture_cannot_override(self):
        audit = datetime(2026, 9, 8, 13, tzinfo=UTC)
        present = Observation("evidence_a", audit - timedelta(days=6), audit - timedelta(days=6), "account_service",
                              "account_1", True, replica=True)
        old = Scope("evidence_c", audit - timedelta(days=3), audit - timedelta(days=3), "account_service",
                    "customer_profiles", set())
        files = {"evidence_a": present.captured_at, "evidence_c": old.captured_at}
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", [present], [old], audit, files,
                         since=audit - timedelta(days=10))
        self.assertFalse(st.compliant)  # public case_05 pattern


class _StubEvidence:
    def __init__(self, docs):
        self.docs = docs
        self.observations = []
        self.files = {d.file_id: {"file_path": f"evidence/images/{d.file_id}.png"} for d in docs}


class _StubActivity:
    links = []


class ScreenshotIndicatorTests(unittest.TestCase):
    def _obs(self, text):
        linker = Linker.__new__(Linker)
        linker.evidence = _StubEvidence([EvidenceDoc("evidence_1", datetime(2026, 8, 7, tzinfo=UTC), text)])
        linker.identity = IdentityIndex([], [])
        linker.activity = _StubActivity()
        linker.ambiguous_keys = set()
        return {o.resource_id: o for o in linker._document_observations()}

    def test_connector_needs_positive_indicator_on_its_row(self):
        obs = self._obs("Connections\ndata_flow_1 | warehouse | a@x.org | Continuous | 1,284\n"
                        "data_flow_2 | warehouse | b@x.org | Paused\ndata_flow_3 | warehouse | c@x.org")
        self.assertIs(obs["data_flow_1"].flags.get("enabled"), True)
        self.assertIs(obs["data_flow_2"].flags.get("enabled"), False)
        self.assertNotIn("enabled", obs["data_flow_3"].flags)  # no indicator -> unknown, not assumed

    def test_backup_restore_needs_a_restore_console(self):
        restorable = self._obs("Recovery Manager\nArchives\nbackup_1 | a@x.org | Archive\nRestore requests")
        listing = self._obs("Storage report\nbackup_1 | a@x.org | 84 GB")
        blocked = self._obs("Recovery Manager\nArchives\nbackup_1 | a@x.org | Archive | Restore blocked\nRestore")
        self.assertIs(restorable["backup_1"].flags.get("restore_allowed"), True)
        self.assertNotIn("restore_allowed", listing["backup_1"].flags)
        self.assertIs(blocked["backup_1"].flags.get("restore_allowed"), False)

    def test_glued_ocr_identifiers_are_split(self):
        obs = self._obs("user_2-analytics_71-test.person@example.org")
        self.assertIn("email:test.person@example.org", obs["analytics_71"].flags["row_identifiers"])


class PipelineRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = run_pipeline(CUSTOM / "suite" / "data", link_policy="full")
        cls.by_case = {r.case_id: r for r in cls.full.results}

    def test_case_63_catalog_conflict_is_kept_and_flagged(self):
        data = os.environ.get("GHOST_AUDIT_TEST_DATA_DIR")
        if not data:
            self.skipTest("optional external-corpus regression; set GHOST_AUDIT_TEST_DATA_DIR")
        run = run_pipeline(Path(data), link_policy="full")
        res = next(r for r in run.results if r.case_id == "case_63")
        link = next(r for r in res.resources if r["resource_id"] == "marketing_001")
        self.assertEqual(link["link"]["method"], "catalog")
        self.assertTrue(any("evidence_60" in c and "no known customer" in c for c in link["link"]["conflicts"]))

    def test_status_never_depends_on_weak_or_unresolved_links(self):
        self.assertTrue(all(r.status_relies_on == "direct" for r in self.full.results if r.status != "NOT_APPLICABLE"))

    def test_ties_are_labelled_unresolved_with_all_candidates(self):
        res = run_pipeline(CUSTOM / "suite_ambiguous" / "data", link_policy="full")
        accounts = [r for x in res.results for r in x.resources if r["resource_id"].startswith("account_")]
        self.assertEqual(len(accounts), 2)
        for r in accounts:
            self.assertEqual(r["link"]["confidence"], "unresolved")
            self.assertEqual(sorted(r["link"]["candidates"]), ["cust_11", "cust_12"])
        default = run_pipeline(CUSTOM / "suite_ambiguous" / "data")
        self.assertFalse(any(r["resource_id"].startswith("account_") for x in default.results for r in x.resources))

    def test_false_catalog_join_is_rejected(self):
        res = run_pipeline(CUSTOM / "suite" / "data")
        case = next(r for r in res.results if r.case_id == "cust_06")
        self.assertEqual(case.status, "COMPLETE")
        self.assertFalse(any(r["resource_id"] == "support_9601" for r in case.resources))


if __name__ == "__main__":
    unittest.main()
