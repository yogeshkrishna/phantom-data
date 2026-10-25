"""Policy conditions, retained presence and monotonic privacy obligations."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.policy import PolicyEngine
from phantom.state import StateIndex
from test_state import A, B, C, fact, subject


def rules():
    delete = ("USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "USER_EXPORT", "MARKETING_PROFILE")
    rows = [dict(rule_id="rule-"+category, data_category=category, condition="Always", required_action="DELETE") for category in delete]
    specs = [
        ("SUPPORT_MESSAGE", "Conversation contains shared customer, agent, or order context that must be retained", "REDACT"),
        ("SUPPORT_ATTACHMENT", "Attachment belongs only to the deletion subject", "DELETE"),
        ("ANALYTICS_PROFILE", "Record is still attributable to a user", "ANONYMIZE"),
        ("PRODUCT_TELEMETRY", "Record is still attributable to a user", "ANONYMIZE"),
        ("EMAIL_DELIVERY_LOG", "Log contains a user email address", "REDACT"),
        ("SECURITY_AUDIT_LOG", "Record is within the supplied retention period", "RETAIN"),
        ("LEGAL_HOLD_RECORD", "A supplied legal hold is active", "RETAIN"),
        ("BACKUP_ARCHIVE", "Restore access can be blocked", "RETAIN"),
        ("ACCOUNT_TOMBSTONE", "Account is deleted and only a stable internal user_id is needed to preserve references to retained records", "REDACT"),
    ]
    rows.extend(dict(rule_id="rule-"+c, data_category=c, condition=condition, required_action=action) for c, condition, action in specs)
    return rows


def evaluate(category, fields=None, state="PRESENT", history=(), **kwargs):
    record = fact(fields, B, state, category=category, **kwargs)
    view = StateIndex((*history, record)).resolve(subject(), C)
    return PolicyEngine(rules()).evaluate(view)


class PolicyTests(unittest.TestCase):
    def test_every_csv_category_is_covered(self):
        self.assertEqual(len(PolicyEngine(rules()).rules), 15)
        for row in rules():
            with self.subTest(category=row['data_category']):
                obligations = evaluate(row['data_category'])
                self.assertTrue(obligations)
                self.assertEqual(obligations[0].rule_id, row['rule_id'])

    def test_delete_categories_require_absence(self):
        for category in ("USER_PROFILE", "CACHE_ENTRY", "USER_EXPORT", "MARKETING_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY"):
            self.assertFalse(evaluate(category)[0].compliant)
            self.assertTrue(evaluate(category, {}, "ABSENT", query_identifiers={"email": ("a@example.test",)})[0].compliant)

    def test_tombstone_retains_only_actual_stable_id(self):
        clean = dict(user_id="u", name=None, email=None, phone=None, email_hash=None, account_status="DELETED")
        self.assertTrue(evaluate("ACCOUNT_TOMBSTONE", clean)[0].compliant)
        for key, value in (("subject_reference", "a@example.test"), ("email_hash", "hash"), ("phone", "123")):
            self.assertFalse(evaluate("ACCOUNT_TOMBSTONE", dict(clean, **{key: value}))[0].compliant)
        self.assertIsNone(evaluate("ACCOUNT_TOMBSTONE", dict(clean, account_status="ACTIVE"))[0].compliant)

    def test_nested_free_text_and_hash_exposure(self):
        for fields in ({"message": "Contact a@example.test tomorrow"}, {"nested": {"hash": "hash"}}, {"email_hash": "different-hash"}):
            obligation = evaluate("SUPPORT_MESSAGE", dict(user_id="u", **fields))[0]
            self.assertFalse(obligation.compliant)
            self.assertEqual(obligation.finding, "REPORT_EXPOSED_PII")

    def test_analytics_stable_id_is_still_attributable(self):
        self.assertFalse(evaluate("ANALYTICS_PROFILE", {"user_id": "u"})[0].compliant)
        self.assertFalse(evaluate("PRODUCT_TELEMETRY", {"email_hash": "hash"})[0].compliant)

    def test_clean_anonymous_history_and_unknown_omitted_fields(self):
        old = fact(category="ANALYTICS_PROFILE")
        clean = evaluate("ANALYTICS_PROFILE", {"user_id": None, "email": None}, "ANONYMIZED", history=(old,))
        self.assertTrue(clean[0].compliant)
        unknown = evaluate("ANALYTICS_PROFILE", {"metric": 1}, query_identifiers={"user_id": ("u",)})
        self.assertIsNone(unknown[0].compliant)

    def test_query_email_not_exposed_record_field(self):
        self.assertTrue(evaluate("SUPPORT_MESSAGE", {}, "REDACTED", query_identifiers={"email": ("a@example.test",)})[0].compliant)

    def test_shared_attachment_not_silently_deleted(self):
        self.assertIsNone(evaluate("SUPPORT_ATTACHMENT", {"user_id": "u", "shared": True})[0].compliant)
        self.assertEqual(evaluate("SUPPORT_ATTACHMENT")[0].remediation, "DELETE_RECORD")

    def test_security_retains_stable_reference_and_period(self):
        fields = {"subject_reference": "u", "retention_until": "2030-01-01T00:00:00Z"}
        self.assertTrue(evaluate("SECURITY_AUDIT_LOG", fields)[0].compliant)
        self.assertIsNone(evaluate("SECURITY_AUDIT_LOG", dict(fields, retention_until=A))[0].compliant)
        self.assertIsNone(evaluate("SECURITY_AUDIT_LOG", {"subject_reference": "u"})[0].compliant)
        exposed = evaluate("SECURITY_AUDIT_LOG", dict(fields, email="a@example.test"))
        self.assertEqual({o.verification for o in exposed}, {"VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED"})
        self.assertNotIn("DELETE_RECORD", {o.remediation for o in exposed})

    def test_missing_retained_record_cannot_complete(self):
        old = fact({"subject_reference": "u", "retained_until": "2030-01-01T00:00:00Z"}, category="LEGAL_HOLD_RECORD")
        obligations = evaluate("LEGAL_HOLD_RECORD", {}, "ABSENT", history=(old,))
        self.assertIsNone(obligations[0].compliant)
        self.assertIn("REQUIRED_RETAINED_RECORD_MISSING", obligations[0].reasons)

    def test_enabled_flow_and_restore_are_separate_risks(self):
        for category, field, operation in (("DATA_FLOW", "enabled", "DISABLE_DATA_FLOW"), ("BACKUP_ARCHIVE", "restore_allowed", "BLOCK_RESTORE")):
            active = evaluate(category, {"user_id": "u", field: True})[-1]
            safe = evaluate(category, {"user_id": "u", field: False})[-1]
            self.assertFalse(active.compliant)
            self.assertEqual(active.remediation, operation)
            self.assertTrue(safe.compliant)

    def test_unknown_boolean_is_not_disabled(self):
        self.assertIsNone(evaluate("DATA_FLOW", {"user_id": "u", "enabled": "false"})[0].compliant)

    def test_blocked_restore_alone_does_not_prove_archive_available(self):
        obligations = evaluate("BACKUP_ARCHIVE", {}, "BLOCKED", query_identifiers={"user_id": ("u",)})
        self.assertIsNone(obligations[0].compliant)
        self.assertEqual(obligations[0].verification,"VERIFY_RESTORE_BLOCKED")

    def test_explicit_restore_conflict_is_unknown(self):
        o = evaluate("BACKUP_ARCHIVE", {"user_id": "u", "restore_allowed": True}, "BLOCKED")[-1]
        self.assertIsNone(o.compliant)

    def test_policy_action_drives_behavior_not_category_labels(self):
        changed = rules()
        changed[0] = dict(changed[0], required_action="ANONYMIZE")
        view = StateIndex([fact()]).resolve(subject(), C)
        self.assertEqual(PolicyEngine(changed).evaluate(view)[0].remediation, "ANONYMIZE_RECORD")

    def test_unknown_condition_rejected(self):
        with self.assertRaises(ValueError):
            PolicyEngine([dict(rule_id="r", data_category="USER_PROFILE", condition="invented", required_action="DELETE")])
