"""Unit tests for the deletion-audit components (python -m unittest discover code/tests)."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))

from ghost_audit.authorization import classify_request  # noqa: E402
from ghost_audit.evidence.log_lines import parse_line  # noqa: E402
from ghost_audit.evidence.model import Observation, Scope, keys_from_value  # noqa: E402
from ghost_audit.identity import IdentityIndex  # noqa: E402
from ghost_audit.output import order_steps, write_output  # noqa: E402
from ghost_audit.policy import Policy  # noqa: E402
from ghost_audit.state import reconstruct  # noqa: E402

import validate_output  # noqa: E402

T = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)  # noqa: E731
with open(CODE / "evaluation" / "custom_cases" / "suite" / "data" / "deletion_rules.csv", encoding="utf-8") as _fh:
    RULES = list(csv.DictReader(_fh))


class AuthorizationTests(unittest.TestCase):
    def test_multilingual_deletion(self):
        for text in ["Please permanently delete the account associated with this email address.",
                     "Solicito a exclusão permanente da conta associada a este e-mail.",
                     "Je demande la suppression définitive du compte associé à cette adresse e-mail.",
                     "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
                     "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.",
                     "Intenté eliminar mi cuenta ayer, pero la página terminó mostrando un error."]:
            self.assertTrue(classify_request(text).authorized, text)

    def test_non_deletion_intents(self):
        for text in ["Please stop the promotional emails. I am not asking for deletion.",
                     "Can you tell me what personal information you hold about me? This is an information "
                     "request, not a request to erase it.",
                     "Please remove me from marketing lists only. I still want receipts.",
                     "My surname is misspelled. Can you correct it without removing any of my records?",
                     "I'd like to download my account information in a portable format."]:
            self.assertFalse(classify_request(text).authorized, text)

    def test_deletion_with_permission_to_retain(self):
        self.assertTrue(classify_request("I'm deleting my account. You can keep anonymous statistics.").authorized)


class LogParserTests(unittest.TestCase):
    def test_formats(self):
        samples = {
            "kv": '2026-09-26T07:49:50Z level=INFO service=account_service trace_id=rb-1 event=profile_lookup_completed '
                  'resource_id=account_1 rows=0 http_status=404',
            "json": '2026-09-16T08:57:34Z {"severity":"INFO","service":"account_service","trace_id":"tr-1",'
                    '"resource_id":"account_1","event":"profile_lookup_completed","rows":"0"}',
            "syslog": '2026-08-22T10:51:17Z <15>1 node-1 account_service tr-497 - - level=DEBUG '
                      'resource_id="account_1" event="profile_lookup_completed" rows="1"',
            "pipe": '2026-07-03T07:51:32Z | DEBUG | security_service | tr-515 | audit_record_read_completed | '
                    'resource_id="audit_record_1" rows="1"',
            "bracket": '2026-07-28T05:51:21Z [INFO] [account_service] [tr-607] profile_lookup_completed: '
                       'resource_id="account_1" rows="0"',
            "cloud": '2026-09-30T11:51:03Z requestId=tr-226 level=DEBUG function=profile_service '
                     'message="resource_id=\\"account_1\\" event=\\"profile_lookup_completed\\" rows=\\"1\\""',
        }
        for fmt, line in samples.items():
            rec = parse_line(line)
            self.assertIsNotNone(rec, fmt)
            self.assertEqual(rec["format"], fmt)
            self.assertTrue(rec["attrs"]["resource_id"].endswith("_1"), fmt)
            self.assertTrue(rec["event"].endswith("_completed"), fmt)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.idx = IdentityIndex(
            [{"user_id": "user_1", "name": "Ana", "email": "ana.new@x.com", "phone": "+1 555 0100",
              "email_hash": "a" * 64, "updated_at": "2026-01-01T00:00:00Z"}],
            [{"audit_id": "audit_1", "user_id": "user_1", "event_time": "2025-01-01T00:00:00Z",
              "field_name": "email", "old_value": "ana.old@x.com", "new_value": "ana.new@x.com"}])

    def test_historical_and_case_insensitive_email(self):
        self.assertEqual(self.idx.resolve_email("ANA.OLD@X.COM").user_id, "user_1")
        self.assertEqual(self.idx.resolve_email("ana.new@x.com").user_id, "user_1")

    def test_decoy_variant_does_not_match(self):
        self.assertIsNone(self.idx.resolve_email("ana.old.jr@x.com"))
        self.assertEqual(self.idx.owners_of(keys_from_value("ana.old.jr@x.com")), set())


class PolicyStateTests(unittest.TestCase):
    def obs(self, t, present, captured=None, **kw):
        return Observation(file_id=kw.pop("file_id", "evidence_1"), captured_at=captured or t, obs_time=t,
                           system=kw.pop("system", "account_service"), resource_id="account_1", present=present, **kw)

    def test_post_audit_recreation_is_ignored(self):
        audit = T("2026-09-15T12:00")
        observations = [self.obs(T("2026-09-15T09:00"), False, file_id="evidence_a"),
                        self.obs(T("2026-09-17T12:00"), True, file_id="evidence_b")]
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", observations, [], audit,
                         {"evidence_a": T("2026-09-15T11:00"), "evidence_b": T("2026-09-17T12:00")})
        self.assertTrue(st.compliant)
        self.assertEqual(st.citation, "evidence_a")

    def test_late_captured_file_is_not_citable(self):
        audit = T("2026-09-16T09:00")
        observations = [self.obs(T("2026-09-16T08:57"), True, file_id="evidence_late")]
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", observations, [], audit,
                         {"evidence_late": T("2026-09-18T11:55")})
        self.assertFalse(st.compliant)
        self.assertEqual(st.citation, "")

    def test_replica_copy_is_a_violation(self):
        t = T("2026-09-21T10:00")
        observations = [self.obs(t, True, replica=True, store="customer_data_read_replica")]
        scope = Scope("evidence_1", t, t, "account_service", "customer_data_primary", set())
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", observations, [scope], t,
                         {"evidence_1": t})
        self.assertFalse(st.compliant)

    def test_absence_by_omission_from_captured_scope(self):
        t = T("2026-09-15T12:00")
        scope = Scope("evidence_51", t, t, "account_service", "customer_profiles", {"account_9"})
        st = reconstruct("account_1", "account_service", "DELETE", "USER_PROFILE", [], [scope], t,
                         {"evidence_51": t})
        self.assertTrue(st.compliant)
        self.assertEqual(st.citation, "evidence_51")

    def test_policy_decisions(self):
        policy = Policy(RULES)
        audit = T("2026-09-15T12:00")
        self.assertEqual(policy.decide("SUPPORT_MESSAGE", {}, audit).treatment, "REDACT")
        self.assertEqual(policy.decide("SUPPORT_ATTACHMENT", {}, audit).treatment, "DELETE")
        self.assertEqual(policy.decide("ANALYTICS_PROFILE", {}, audit).treatment, "ANONYMIZE")
        self.assertEqual(policy.decide("BACKUP_ARCHIVE", {}, audit).treatment, "BLOCK_RESTORE")
        self.assertEqual(policy.decide("ACCOUNT_TOMBSTONE", {}, audit).treatment, "REDACT")
        self.assertEqual(policy.decide("SECURITY_AUDIT_LOG", {"retention_until": "2031-12-31T23:59:59Z"},
                                       audit).treatment, "RETAIN")
        self.assertEqual(policy.decide("SECURITY_AUDIT_LOG", {"retention_until": "2026-01-01T00:00:00Z"},
                                       audit).treatment, "DELETE")
        self.assertEqual(policy.decide("DATA_FLOW", {}, audit).treatment, "DISABLE_FLOW")

    def test_tombstone_keeps_reference_but_not_pii(self):
        t = T("2026-09-15T12:00")
        ok = self.obs(t, True, system="identity_service", refs={"subject_reference": "user_1"})
        bad = self.obs(t, True, system="identity_service", refs={"subject_reference": "user_1"},
                       pii={"email": "a@b.com"})
        for o, expected in ((ok, True), (bad, False)):
            st = reconstruct("tombstone_1", "identity_service", "REDACT", "ACCOUNT_TOMBSTONE", [o], [], t,
                             {"evidence_1": t})
            self.assertEqual(st.compliant, expected)


class OutputTests(unittest.TestCase):
    def test_ordering_and_numbering(self):
        def s(phase, op, state, system, rid):
            return {"step_no": 0, "phase": phase, "system_id": system, "resource_id": rid, "operation": op,
                    "state": state, "evidence_id": ""}
        steps = order_steps([
            s("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "integration_service", "data_flow_1"),
            s("REMEDIATION", "DELETE_RECORD", "ABSENT", "search_service", "search_2"),
            s("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED", "integration_service", "data_flow_1"),
            s("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "search_service", "search_2"),
            s("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "account_service", "account_1"),
        ])
        self.assertEqual([x["operation"] for x in steps],
                         ["DISABLE_DATA_FLOW", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT",
                          "VERIFY_DATA_FLOW_DISABLED"])
        self.assertEqual(steps[2]["system_id"], "account_service")
        self.assertEqual([x["step_no"] for x in steps], [1, 2, 3, 4, 5])

    def test_validator_rejects_bad_pairs_and_order(self):
        data = CODE / "evaluation" / "custom_cases" / "suite" / "data"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.csv"
            with open(data.parent / "expected_output.csv", encoding="utf-8") as fh:
                gold = list(csv.DictReader(fh))
            rows = []
            for r in gold:
                steps = json.loads(r["steps_json"])
                if r["case_id"] == "cust_03":
                    steps[0]["state"] = "ABSENT"  # wrong state for REPORT_REMAINING_RECORD
                if r["case_id"] == "cust_01":
                    steps[0], steps[1] = steps[1], steps[0]  # ordering violation
                rows.append((r["case_id"], r["deletion_status"], steps))
            write_output(out, rows)
            errors = validate_output.validate(out, data, "deletion_requests.csv")
            self.assertTrue(any("cust_03" in e and "needs phase" in e for e in errors))
            self.assertTrue(any("cust_01" in e for e in errors))

    def test_reference_answers_pass_validator(self):
        data = CODE / "evaluation" / "custom_cases" / "suite" / "data"
        self.assertEqual(validate_output.validate(data.parent / "expected_output.csv", data, "deletion_requests.csv"), [])


if __name__ == "__main__":
    unittest.main()
