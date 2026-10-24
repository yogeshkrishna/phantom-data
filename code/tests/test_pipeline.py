"""End-to-end synthetic verdicts and adversarial plan mutations."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import AuditResult, EvidenceRef, to_csv_row
from phantom.identities import IdentityIndex
from phantom.pipeline import PreparedAudit, audit_request
from phantom.planner import AuditBlocked, plan
from phantom.policy import PolicyEngine
from phantom.state import StateIndex
from phantom.verifier import verify_result
from test_policy import rules
from test_state import A, B, C, fact


def request(text="Delete my personal data"):
    return dict(case_id="synthetic", email="a@example.test", requested_at=A, audit_at=C, request_text=text)


def prepared(facts):
    identities = IdentityIndex([dict(user_id="u", name="Person", email="a@example.test", phone="123",
                                     email_hash="hash", updated_at=A)], [], facts)
    return PreparedAudit({"deletion_rules": rules()}, identities, StateIndex(facts), PolicyEngine(rules()), (), 0, 0)


class PipelineTests(unittest.TestCase):
    def check(self, context, result, req=None, **kwargs):
        return verify_result(result, req or request(), context.identities, context.states, rules(), **kwargs)

    def test_three_independent_end_to_end_cases(self):
        for text, facts, status in (
            ("Delete my personal data", [fact(), fact({}, B, "ABSENT")], "COMPLETE"),
            ("Delete my personal data", [fact()], "INCOMPLETE"),
            ("Do not delete my account", [fact()], "NOT_APPLICABLE"),
        ):
            with self.subTest(status=status):
                req = request(text)
                context = prepared(facts)
                result = audit_request(context, req)
                self.assertEqual(result.deletion_status, status)
                to_csv_row(result)
                self.assertTrue(self.check(context, result, req).valid)

    def test_step_removal_and_wrong_citation_rejected(self):
        context = prepared([fact()])
        result = audit_request(context, request())
        for index in range(len(result.steps)):
            steps = tuple(replace(s, step_no=i+1) for i, s in enumerate(result.steps[:index]+result.steps[index+1:]))
            self.assertFalse(self.check(context, replace(result, steps=steps)).valid)
        steps = (replace(result.steps[0], evidence_id="made-up"), *result.steps[1:])
        self.assertFalse(self.check(context, replace(result, steps=steps)).valid)

    def test_wrong_resource_and_reordering_rejected(self):
        context = prepared([fact()])
        result = audit_request(context, request())
        steps = tuple(replace(s, resource_id="unknown") for s in result.steps)
        self.assertFalse(self.check(context, replace(result, steps=steps)).valid)
        self.assertFalse(self.check(context, replace(result, steps=result.steps[::-1])).valid)

    def test_complete_mutation_cannot_hide_existing_copy(self):
        clean = prepared([fact(), fact({}, B, "ABSENT")])
        complete = audit_request(clean, request())
        dirty_replica = prepared([fact(), fact({}, B, "ABSENT"), fact(store="replica")])
        self.assertFalse(self.check(dirty_replica, complete).valid)

    def test_cutoff_mutation_makes_proof_unavailable(self):
        context = prepared([fact(), fact({}, B, "ABSENT")])
        result = audit_request(context, request())
        self.assertFalse(self.check(context, result, dict(request(), audit_at=A)).valid)

    def test_future_source_mutation_rejected(self):
        context = prepared([fact(), fact({}, B, "ABSENT")])
        result = audit_request(context, request())
        future = fact({}, B, "ABSENT", source=EvidenceRef("file-02", "future", "x", "/", "2030-01-01T00:00:00Z"))
        changed = prepared([fact(), future])
        self.assertFalse(self.check(changed, result).valid)

    def test_incomplete_has_future_checks_for_clean_resources(self):
        context = prepared([fact(), fact({}, B, "ABSENT", resource="clean", query_identifiers={"user_id": ("u",)})])
        result = audit_request(context, request())
        checks = [s for s in result.steps if s.phase == "VERIFICATION"]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(s.evidence_id == "" for s in checks))

    def test_planner_compliance_flags_not_trusted(self):
        context = prepared([fact()])
        view = context.states.resolve(context.identities.resolve("a@example.test", C), C)
        obligations = tuple(replace(o, compliant=True, finding=None, remediation=None) for o in context.policy.evaluate(view))
        from phantom.intent import classify_intent
        forged = plan("synthetic", classify_intent("Delete my data"), obligations)
        self.assertFalse(self.check(context, forged).valid)

    def test_coverage_gap_cannot_claim_complete(self):
        context = prepared([fact(), fact({}, B, "ABSENT")])
        result = audit_request(context, request())
        self.assertFalse(self.check(context, result, coverage_issues=("UNSUPPORTED_EVIDENCE",)).valid)

    def test_multi_store_proof_requires_shared_citation(self):
        clean_a = fact({}, B, "ABSENT", query_identifiers={"user_id": ("u",)})
        clean_b = fact({}, B, "ABSENT", store="replica", query_identifiers={"user_id": ("u",)},
                       source=EvidenceRef("other", "other", "x", "/", B))
        with self.assertRaisesRegex(AuditBlocked, "NO_SINGLE_CITATION"):
            audit_request(prepared([clean_a, clean_b]), request())

    def test_unknown_not_fabricated_into_incomplete_or_complete(self):
        with self.assertRaises(AuditBlocked):
            audit_request(prepared([fact({}, query_identifiers={"user_id": ("u",)}, category="SUPPORT_MESSAGE")]), request())

    def test_reassigned_resource_does_not_authorize_deleting_another_owner(self):
        context = prepared([fact(), fact({"user_id": "other", "email": "other@example.test"}, B)])
        with self.assertRaisesRegex(AuditBlocked, "RESOURCE_OWNERSHIP_CONFLICT"):
            audit_request(context, request())
