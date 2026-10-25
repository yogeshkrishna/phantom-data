"""Unstructured identifiers must be found without fuzzy or numeric joins."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import AuditResult, AuditStep
from phantom.identities import IdentityIndex
from phantom.intent import classify_intent
from phantom.planner import plan
from phantom.policy import PolicyEngine
from phantom.state import StateIndex
from phantom.verifier import verify_result
from test_policy import rules
from test_state import A, B, C, fact


class LiteralRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.identities = IdentityIndex([dict(
            user_id="subject_alpha", name="Test Person", email="a@example.test",
            phone="+1 (202) 555-0198", email_hash="abcdef12" * 8, updated_at=A)], [])
        self.identity = self.identities.resolve("a@example.test", C)

    def view(self, text, **changes):
        return StateIndex([fact({"message": text}, category="SUPPORT_MESSAGE", **changes)]).resolve(self.identity, C)

    def test_identifiers_embedded_in_nested_prose_are_retrieved(self):
        for value in ("subject_alpha", "abcdef12" * 8, "+1 202 555 0198"):
            with self.subTest(value=value):
                view = self.view("Please remove the reference: " + value + "; thank you.")
                self.assertEqual(len(view.resources), 1)
                obligation = PolicyEngine(rules()).evaluate(view)[0]
                self.assertEqual(obligation.finding, "REPORT_EXPOSED_PII")

    def test_prefixes_names_and_short_numeric_fragments_do_not_link(self):
        for text in ("subject_alphabet", "x" + "abcdef12" * 8,
                     "+1 202 555 01980", "Test Person", "receipt 0198"):
            with self.subTest(text=text):
                self.assertFalse(self.view(text).resources)

    def test_opaque_hash_with_punctuation_is_indexed_whole(self):
        identity = replace(self.identity, identifiers={**self.identity.identifiers, "email_hash": ("opaque-hash",)})
        state = StateIndex([fact({"message": "Stored opaque-hash; please remove"}, category="SUPPORT_MESSAGE")])
        self.assertEqual(len(state.resolve(identity, C).resources), 1)

    def test_exact_id_case_and_other_owner_are_respected(self):
        self.assertFalse(self.view("SUBJECT_ALPHA").resources)
        state = StateIndex([fact({"user_id": "someone_else", "message": "subject_alpha"})])
        self.assertFalse(state.resolve(self.identity, C).resources)

    def test_future_text_does_not_create_subject_link(self):
        source = replace(fact().source, available_at="2027-01-01T00:00:00Z")
        self.assertFalse(self.view("subject_alpha", source=source).resources)

    def test_historical_text_link_survives_explicit_redaction(self):
        old = fact({"message": "Call +1 202 555 0198"}, category="SUPPORT_MESSAGE")
        new = fact({"message": "[redacted]"}, B, "REDACTED", category="SUPPORT_MESSAGE",
                   metadata={"full_record": True})
        view = StateIndex([old, new]).resolve(self.identity, C)
        self.assertEqual(len(view.resources), 1)
        self.assertTrue(PolicyEngine(rules()).evaluate(view)[0].compliant)

    def test_independent_verifier_rejects_embedded_phone_as_clean(self):
        state = StateIndex([fact({"message": "Call +1 202 555 0198"}, category="SUPPORT_MESSAGE")])
        view = state.resolve(self.identity, C)
        request = dict(case_id="custom", email="a@example.test", requested_at=A,
                       audit_at=C, request_text="Please delete my personal data")
        result = plan("custom", classify_intent(request["request_text"]), PolicyEngine(rules()).evaluate(view))
        self.assertTrue(verify_result(result, request, self.identities, state, rules()).valid)
        forged = AuditResult("custom", "COMPLETE", (AuditStep(
            1, "VERIFICATION", "service", "opaque", "VERIFY_RECORD_REDACTED", "REDACTED", "file-01"),))
        self.assertFalse(verify_result(forged, request, self.identities, state, rules()).valid)

    def test_intent_cache_preserves_validation_and_frozen_result(self):
        with self.assertRaises(ValueError):
            classify_intent([])
        text = "Please delete my account"
        self.assertEqual(classify_intent(text), classify_intent(text))

    def test_name_in_sentence_is_exposure_after_independent_identity_link(self):
        state = StateIndex([fact({"customer_reference": "subject_alpha", "message": "Help Test Person."},
                                category="SUPPORT_MESSAGE")])
        view = state.resolve(self.identity, C)
        self.assertFalse(PolicyEngine(rules()).evaluate(view)[0].compliant)
        from phantom.verifier import _unsafe
        self.assertTrue(_unsafe(view.resources[0], self.identity, allow_stable=True))


if __name__ == "__main__":
    unittest.main()
