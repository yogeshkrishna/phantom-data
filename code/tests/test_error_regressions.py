"""General fixtures for errors discovered during source-level sample analysis."""
import sys
import unittest
from pathlib import Path
from dataclasses import replace

sys.path.insert(0,str(Path(__file__).parent.parent))
from phantom.contracts import EvidenceRef
from phantom.intent import classify_intent
from phantom.json_evidence import normalize_json
from phantom.policy import PolicyEngine
from phantom.state import StateIndex
from phantom.pipeline import audit_request
from phantom.planner import AuditBlocked
from phantom.verifier import verify_result
from test_state import A,B,C,fact,subject
from test_policy import rules,evaluate
from test_pipeline import prepared,request


class ErrorRegressionTests(unittest.TestCase):
    def test_shared_context_reference_is_redacted_not_anonymized(self):
        fields=dict(customer_reference='u',requester_name=None,requester_email=None,requester_phone=None)
        self.assertTrue(evaluate('SUPPORT_MESSAGE',fields)[0].compliant)
        analytics=dict(customer_reference='u',email=None,phone=None,email_hash=None)
        self.assertFalse(evaluate('ANALYTICS_PROFILE',analytics)[0].compliant)

    def test_omitted_previously_known_support_phone_is_not_null(self):
        old=fact({'user_id':'u','requester_phone':'123'},category='SUPPORT_MESSAGE')
        self.assertFalse(evaluate('SUPPORT_MESSAGE',dict(customer_reference='u',requester_name=None,requester_email=None),history=(old,))[0].compliant)

    def test_semantic_disabled_readback_supersedes_old_enabled_flag(self):
        source=EvidenceRef('new','readback.json','a'*64,'',B)
        document=dict(exported_at=B,origin='readback',record_count=1,records=[dict(recorded_at=B,
            system='service',resource_id='opaque',record_type='DATA_FLOW',result='DISABLED',subject_reference='u')])
        new=normalize_json(document,source).facts[0]
        old=fact({'user_id':'u','enabled':True},store='service',category='DATA_FLOW')
        view=StateIndex([old,new]).resolve(subject(),C)
        obligation=PolicyEngine(rules()).evaluate(view)[0]
        self.assertTrue(obligation.compliant)
        self.assertEqual(obligation.proofs[0].evidence_id,'new')

    def test_audit_dereference_does_not_authorize_newsletter_removal_as_account_deletion(self):
        text='I want to keep my account. Please remove me from the newsletter.'
        self.assertEqual(classify_intent(text).authorization,'NOT_REQUESTED')

    def test_coordinated_pii_redaction_is_authorized(self):
        self.assertEqual(classify_intent('The account is gone but the record shows my personal details. Please keep the reference and remove the rest.').authorization,'REQUESTED')

    def test_explicit_information_request_excludes_erasure_dereference(self):
        self.assertEqual(classify_intent('What personal information do you hold? This is not a request to erase it.').authorization,'NOT_REQUESTED')

    def test_blocked_archive_cannot_hide_removed_retained_copy(self):
        archive=fact({'user_id':'u','archive_key':'archive/object','restore_allowed':False},category='BACKUP_ARCHIVE')
        good=prepared([archive]);result=audit_request(good,request())
        missing=fact({},B,'ABSENT',category='BACKUP_ARCHIVE')
        bad=prepared([archive,missing])
        self.assertFalse(verify_result(result,request(),bad.identities,bad.states,rules()).valid)
        with self.assertRaises(AuditBlocked):audit_request(bad,request())
