"""Independent synthetic counterexamples and neighboring controls for opt-in fixes.

Every test selects its own candidate scope, so later release promotion cannot
silently change the baseline asserted by these counterexamples.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost_audit.activity import ActivityIndex
from ghost_audit.authorization import classify_request
from ghost_audit.candidates import CANDIDATES, candidate_scope, enabled
from ghost_audit.evidence.model import EvidenceDoc, Observation, REFERENCE_FIELDS, Scope
from ghost_audit.evidence.records import build_observation
from ghost_audit.identity import IdentityIndex, Subject
from ghost_audit.linker import CaseContext, Link, Linker
from ghost_audit.loaders import parse_ts
from ghost_audit.planner import Planner
from ghost_audit.policy import Policy
from ghost_audit.state import evaluate, reconstruct

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
AUDIT = T0 + timedelta(hours=5)


def observation(hour=1, rid="account_601", system="account_service", **kwargs):
    t = T0 + timedelta(hours=hour)
    return Observation(kwargs.pop("file_id", f"proof_{hour}"), kwargs.pop("captured_at", t), t,
                       system, rid, kwargs.pop("present", True), **kwargs)


def state(rows, treatment="DELETE", scopes=(), at=AUDIT):
    captured = {row.file_id: row.captured_at for row in rows}
    captured.update({scope.file_id: scope.captured_at for scope in scopes})
    rid = rows[0].resource_id if rows else "account_601"
    system = rows[0].system if rows else "account_service"
    return reconstruct(rid, system, treatment, "USER_PROFILE", rows, list(scopes), at, captured)


def case(authorized=True, at=AUDIT):
    return CaseContext("synthetic_request", {}, Subject("user_601", "Synthetic", {"person@example.test"}),
                       T0, T0 + timedelta(hours=1), at, "COMPLETE", authorized)


def policy():
    return Policy([
        {"data_category": category, "rule_id": category.lower(), "required_action": action, "condition": ""}
        for category, action in (("USER_PROFILE", "DELETE"), ("SUPPORT_MESSAGE", "REDACT"),
                                 ("SUPPORT_ATTACHMENT", "DELETE"), ("ANALYTICS_PROFILE", "ANONYMIZE"),
                                 ("SECURITY_AUDIT_LOG", "REDACT"), ("LEGAL_HOLD_RECORD", "RETAIN"),
                                 ("BACKUP_ARCHIVE", "RETAIN"), ("ACCOUNT_TOMBSTONE", "REDACT"))])


class CandidateSelectionTests(unittest.TestCase):
    def test_explicit_scope_replaces_and_restores_selection(self):
        with candidate_scope(["marker_content"]):
            self.assertTrue(enabled("marker_content"))
            with candidate_scope([]):
                self.assertFalse(enabled("marker_content"))
            with candidate_scope(None):
                from ghost_audit.candidates import DEFAULT_CANDIDATES
                self.assertEqual(enabled("marker_content"), "marker_content" in DEFAULT_CANDIDATES)
            self.assertTrue(enabled("marker_content"))
        with self.assertRaises(ValueError):
            with candidate_scope(["not_a_candidate"]):
                pass
        with self.assertRaises(ValueError):
            enabled("not_a_candidate")
        self.assertEqual(len(CANDIDATES), 12)


class ContentTests(unittest.TestCase):
    def test_misleading_markers_do_not_override_content(self):
        for marker in ("ANONYMIZED", "REDACTED"):
            for treatment in ("REDACT", "ANONYMIZE"):
                exposed = observation(status=marker, pii={"email": "person@example.test"})
                with candidate_scope([]):
                    self.assertTrue(evaluate(exposed, treatment))
                with candidate_scope(["marker_content"]):
                    self.assertFalse(evaluate(exposed, treatment))

    def test_clean_marker_absence_and_allowed_tombstone_still_pass(self):
        with candidate_scope(["marker_content"]):
            self.assertTrue(evaluate(observation(status="ANONYMIZED"), "ANONYMIZE"))
            self.assertTrue(evaluate(observation(present=False), "REDACT"))
            self.assertTrue(evaluate(observation(refs={"user_id": "user_601"}, flags={"projection_complete": True}), "REDACT"))
            self.assertFalse(evaluate(observation(status="ANONYMIZED", refs={"user_id": "user_601"}), "ANONYMIZE"))

    def test_raw_redacted_email_control_is_not_relabelled_as_baseline_bug(self):
        with candidate_scope([]):
            raw = build_observation({"resource_id": "support_601", "email": "person@example.test",
                                     "result": "FOUND", "message_text": "[REDACTED]"},
                                    file_id="readback", captured_at=T0, obs_time=T0)
            self.assertFalse(evaluate(raw, "REDACT"))

    def test_contradictory_absence_and_stored_content_stays_unknown(self):
        with candidate_scope(["marker_content"]):
            contradictory = build_observation(
                {"resource_id": "analytics_601", "result": "ANONYMIZED", "rows": 0,
                 "email": "person@example.test"}, file_id="readback", captured_at=T0, obs_time=T0)
            self.assertIsNone(evaluate(contradictory, "ANONYMIZE"))
            selector = build_observation(
                {"resource_id": "analytics_601", "result": "NOT_FOUND", "rows": 0,
                 "lookup_key": "person@example.test"}, file_id="readback", captured_at=T0, obs_time=T0)
            self.assertTrue(evaluate(selector, "ANONYMIZE"))
            self.assertFalse(selector.has_pii)

    def test_new_contradiction_supersedes_old_clean_proof_until_same_copy_readback(self):
        earlier = observation(1, rid="analytics_601", system="analytics_platform", present=False, store="primary")
        conflict = observation(2, rid="analytics_601", system="analytics_platform", present=False, store="primary",
                               pii={"email": "person@example.test"}, status="ANONYMIZED")
        later = observation(3, rid="analytics_601", system="analytics_platform", present=False, store="primary")
        with candidate_scope(["marker_content"]):
            result = state([earlier, conflict], "ANONYMIZE")
            self.assertIsNone(result.compliant)
            self.assertEqual(result.basis, "contradictory_content")
            self.assertEqual(result.deciding.file_id, conflict.file_id)
            self.assertEqual(result.citation, "")
            cleaned = state([earlier, conflict, later], "ANONYMIZE")
            self.assertTrue(cleaned.compliant)
            self.assertEqual(cleaned.citation, later.file_id)
            other_copy = replace(later, store="replica", replica=True)
            self.assertIsNone(state([earlier, conflict, other_copy], "ANONYMIZE").compliant)
            future = replace(conflict, obs_time=AUDIT + timedelta(seconds=1))
            self.assertTrue(state([earlier, future], "ANONYMIZE").compliant)
            known_exposed = replace(later, present=True, pii={"email": "person@example.test"})
            self.assertFalse(state([earlier, conflict, known_exposed], "ANONYMIZE").compliant)
            # A query selector is not stored contents and introduces no conflict.
            selector = replace(conflict, pii={}, link_keys={"email:person@example.test"})
            self.assertTrue(state([earlier, selector], "ANONYMIZE").compliant)

    def test_partial_projection_is_unknown_not_proved_clean(self):
        row = {"resource_id": "support_601", "result": "FOUND", "selected_fields": ["resource_id"]}
        with candidate_scope([]):
            legacy = build_observation(row, file_id="readback", captured_at=T0, obs_time=T0)
            self.assertTrue(evaluate(legacy, "REDACT"))
        with candidate_scope(["projection_completeness"]):
            partial = build_observation(row, file_id="readback", captured_at=T0, obs_time=T0)
            self.assertIsNone(evaluate(partial, "REDACT"))
            self.assertEqual(partial.flags["projected_fields"], ["resource_id"])
            complete = build_observation(dict(row, projection_complete=True, email="", message_text="[redacted]"),
                                         file_id="readback", captured_at=T0, obs_time=T0)
            self.assertTrue(evaluate(complete, "REDACT"))
            exposed = replace(complete, pii={"email": "person@example.test"})
            self.assertFalse(evaluate(exposed, "REDACT"))
            self.assertTrue(evaluate(replace(partial, present=False), "REDACT"))
            self.assertFalse(evaluate(replace(partial, status="IDENTIFIABLE"), "REDACT"))
            self.assertFalse(evaluate(replace(partial, refs={"user_id": "user_601"}), "ANONYMIZE"))

    def test_every_stable_reference_is_attribution_for_anonymization(self):
        for field in REFERENCE_FIELDS:
            row = observation(refs={field: "user_601"})
            with candidate_scope(["reference_fields"]):
                self.assertFalse(evaluate(row, "ANONYMIZE"), field)
                self.assertTrue(evaluate(row, "REDACT"), field)
        with candidate_scope([]):
            self.assertTrue(evaluate(observation(refs={"subject_ref": "user_601"}), "ANONYMIZE"))

    def test_message_excerpt_content_is_not_a_lookup_selector(self):
        row = {"resource_id": "support_601", "message_excerpt": "Write to person@example.test"}
        with candidate_scope([]):
            self.assertFalse(build_observation(row, file_id="source", captured_at=T0, obs_time=T0).has_pii)
        with candidate_scope(["reference_fields"]):
            self.assertTrue(build_observation(row, file_id="source", captured_at=T0, obs_time=T0).has_pii)
            for value in ("Thanks for your help", "[redacted]"):
                self.assertFalse(build_observation(dict(row, message_excerpt=value), file_id="source",
                                                   captured_at=T0, obs_time=T0).has_pii)
            lookup = build_observation({"resource_id": "support_601", "lookup_key": "person@example.test"},
                                       file_id="source", captured_at=T0, obs_time=T0)
            self.assertFalse(lookup.has_pii)
            self.assertIn("email:person@example.test", lookup.link_keys)


class TemporalStateTests(unittest.TestCase):
    def test_new_primary_absence_does_not_erase_surviving_replica(self):
        rows = [observation(1, store="primary", present=False),
                observation(2, store="replica", replica=True),
                observation(3, store="primary", present=False)]
        with candidate_scope([]):
            self.assertTrue(state(rows).compliant)
        with candidate_scope(["independent_copies"]):
            result = state(rows)
            self.assertFalse(result.compliant)
            self.assertEqual(result.citation, "proof_2")
            cleanup = observation(4, store="replica", replica=True, present=False)
            self.assertTrue(state(rows + [cleanup]).compliant)
            self.assertFalse(state(rows + [replace(cleanup, obs_time=AUDIT + timedelta(seconds=1))]).compliant)

    def test_copy_order_and_same_time_conflicts(self):
        rows = [observation(2, file_id="left", store="primary", present=False),
                observation(2, file_id="right", store="replica", replica=True)]
        with candidate_scope(["independent_copies"]):
            for ordering in (rows, list(reversed(rows))):
                self.assertFalse(state(ordering).compliant)
            self.assertTrue(state([rows[0], replace(rows[0], file_id="repeat")]).compliant)

    def test_filtered_and_wrong_store_snapshots_do_not_prove_absence(self):
        rows = [observation(1, store="primary")]
        scope = Scope("scope", T0 + timedelta(hours=4), T0 + timedelta(hours=4),
                      "account_service", "primary", set())
        with candidate_scope([]):
            self.assertTrue(state(rows, scopes=[scope]).compliant)
        with candidate_scope(["snapshot_coverage"]):
            self.assertFalse(state(rows, scopes=[scope]).compliant)
            self.assertFalse(state(rows, scopes=[replace(scope, exhaustive=True, store="other")]).compliant)
            self.assertFalse(state(rows, scopes=[replace(scope, exhaustive=True, selector_keys={"account_999"})]).compliant)
            self.assertTrue(state(rows, scopes=[replace(scope, exhaustive=True)]).compliant)
            self.assertTrue(state(rows, scopes=[replace(scope, exhaustive=True, selector_keys={"account_601"})]).compliant)

    def test_omission_is_unknown_without_explicit_exhaustiveness(self):
        scope = Scope("scope", T0, T0, "account_service", "primary")
        with candidate_scope(["snapshot_coverage"]):
            self.assertIsNone(state([], scopes=[scope]).compliant)
            self.assertTrue(state([], scopes=[replace(scope, exhaustive=True)]).compliant)
            self.assertIsNone(state([], scopes=[replace(scope, exhaustive=True, kind="query")]).compliant)

    def test_future_category_cannot_change_earlier_policy(self):
        clean = observation(1, rid="support_601", system="support_platform", record_type="SUPPORT_MESSAGE")
        future = replace(clean, obs_time=AUDIT + timedelta(seconds=1), captured_at=AUDIT + timedelta(seconds=1),
                         file_id="future", record_type="SUPPORT_ATTACHMENT")
        def treatment(rows):
            planner = Planner(policy(), {clean.resource_id: rows}, [], {o.file_id: o.captured_at for o in rows})
            return planner._state(clean.resource_id, clean.system, case())[0].treatment
        with candidate_scope([]):
            self.assertEqual(treatment([clean, future]), "DELETE")
        with candidate_scope(["temporal_categories"]):
            self.assertEqual(treatment([clean]), "REDACT")
            self.assertEqual(treatment([clean, future]), "REDACT")
            self.assertEqual(treatment([replace(future, obs_time=AUDIT)]), "DELETE")

    def test_stale_clean_citation_cannot_cross_reexposure(self):
        rows = [observation(1, present=False), observation(2),
                observation(3, present=False, captured_at=AUDIT + timedelta(days=1))]
        with candidate_scope([]):
            self.assertEqual(state(rows).citation, "proof_1")
        with candidate_scope(["citation_continuity"]):
            self.assertTrue(state(rows).compliant)
            self.assertEqual(state(rows).citation, "")
            fresh = observation(4, present=False)
            self.assertEqual(state(rows + [fresh]).citation, "proof_4")
            self.assertEqual(state([rows[0], rows[2]]).citation, "proof_1")

    def test_same_time_presence_remains_a_citable_contradiction(self):
        rows = [observation(2, present=False, file_id="clean"), observation(2, file_id="exposed")]
        for names in (["citation_continuity"], ["independent_copies"]):
            with candidate_scope(names):
                result = state(rows)
                self.assertFalse(result.compliant)
                self.assertEqual(result.citation, "exposed")

    def test_fractional_cutoff_and_timezone_equivalence(self):
        value = "2026-01-01T00:00:00.999999Z"
        with candidate_scope([]):
            self.assertEqual(parse_ts(value).microsecond, 0)
        with candidate_scope(["fractional_timestamps"]):
            self.assertEqual(parse_ts(value).microsecond, 999999)
            self.assertEqual(parse_ts(value), parse_ts("2026-01-01T05:30:00.999999+05:30"))
            cutoff = parse_ts("2026-01-01T00:00:00.500000Z")
            after = observation(0, present=True)
            after = replace(after, obs_time=parse_ts(value), captured_at=parse_ts(value))
            before = replace(after, obs_time=parse_ts("2026-01-01T00:00:00.499999Z"),
                             captured_at=T0, present=False, file_id="before")
            self.assertTrue(state([before, after], at=cutoff).compliant)
            self.assertFalse(state([before, replace(after, obs_time=cutoff, captured_at=cutoff)], at=cutoff).compliant)
            self.assertEqual(parse_ts("2026-01-01T00:00:00"), T0)

    def test_equally_shifted_times_keep_copy_conclusion(self):
        rows = [observation(1, store="primary", present=False), observation(2, store="replica", replica=True)]
        shift = timedelta(days=91)
        with candidate_scope(["independent_copies"]):
            original = state(rows)
            moved = state([replace(o, obs_time=o.obs_time + shift, captured_at=o.captured_at + shift) for o in rows], at=AUDIT + shift)
            self.assertEqual((original.compliant, original.citation), (moved.compliant, moved.citation))


class IdentityCandidateTests(unittest.TestCase):
    def index(self):
        return IdentityIndex(
            [{"user_id": "user_601", "name": "Synthetic", "email": "old@example.test", "updated_at": T0.isoformat()}],
            [{"audit_id": "future_change", "user_id": "user_601", "field_name": "email",
              "event_time": (AUDIT + timedelta(days=1)).isoformat(),
              "old_value": "old@example.test", "new_value": "future@example.test"}])

    def test_future_alias_is_unavailable_at_earlier_cutoff(self):
        idx = self.index()
        with candidate_scope([]):
            self.assertIsNotNone(idx.resolve_email("future@example.test", AUDIT))
        with candidate_scope(["temporal_identity"]):
            self.assertIsNone(idx.resolve_email("future@example.test", AUDIT))
            self.assertIsNotNone(idx.resolve_email("OLD@example.test", AUDIT))
            self.assertIsNotNone(idx.resolve_email("future@example.test", AUDIT + timedelta(days=2)))
            self.assertEqual(idx.owners_of({"email:future@example.test"}, AUDIT), set())

    def test_future_resource_ownership_does_not_rewrite_earlier_owner(self):
        idx = self.index()
        row = observation(1, link_keys={"email:old@example.test"})
        future = replace(row, file_id="future", obs_time=AUDIT + timedelta(days=1), captured_at=AUDIT + timedelta(days=1))
        evidence = SimpleNamespace(observations=[future], scopes=[], docs=[], files={})
        with candidate_scope(["temporal_identity"]):
            linker = Linker([case()], evidence, idx, ActivityIndex([]))
            self.assertEqual(linker.owners_known(row.resource_id, AUDIT), set())
            self.assertEqual(linker.owners_known(row.resource_id, AUDIT + timedelta(days=2)), {"user_601"})

    def test_future_updated_identity_is_unknown_without_earlier_audit(self):
        idx = IdentityIndex([{"user_id": "user_601", "name": "Synthetic", "email": "new@example.test",
                              "updated_at": (AUDIT + timedelta(days=1)).isoformat()}], [])
        with candidate_scope(["temporal_identity"]):
            self.assertIsNone(idx.resolve_email("new@example.test", AUDIT))

    def test_reused_email_remains_ambiguous_instead_of_merging_people(self):
        idx = IdentityIndex([{"user_id": user, "name": user, "email": "shared@example.test", "updated_at": T0.isoformat()}
                             for user in ("user_601", "user_602")], [])
        with candidate_scope(["temporal_identity"]):
            self.assertIsNone(idx.resolve_email("shared@example.test", AUDIT))
            self.assertEqual(idx.owners_of({"email:shared@example.test"}, AUDIT), {"user_601", "user_602"})


class DocumentCandidateTests(unittest.TestCase):
    def rows(self, text, names=("person@example.test",)):
        identity = IdentityIndex([{"user_id": f"user_{601 + i}", "name": f"Person {i}", "email": email}
                                  for i, email in enumerate(names)], [])
        evidence = SimpleNamespace(observations=[], scopes=[], files={"page": {"file_path": "console.txt"}},
                                   docs=[EvidenceDoc("page", T0, text)])
        return Linker([], evidence, identity, ActivityIndex([])).doc_observations

    def test_unrelated_row_cannot_contaminate_clean_support_record(self):
        text = "support_601 | [redacted]\nsupport_602 | person@example.test"
        with candidate_scope([]):
            self.assertTrue(self.rows(text)[0].has_pii)
        with candidate_scope(["document_rows"]):
            rows = self.rows(text)
            self.assertFalse(rows[0].has_pii)
            self.assertTrue(rows[1].has_pii)
            self.assertFalse(self.rows("support_601 | [redacted]")[0].has_pii)

    def test_hash_and_absence_are_row_local_facts(self):
        text = "analytics_601 | " + "a" * 64 + "\nsearch_601 | not found | person@example.test"
        with candidate_scope([]):
            rows = self.rows(text)
            self.assertTrue(next(o for o in rows if o.resource_id == "search_601").present)
        with candidate_scope(["document_rows"]):
            rows = self.rows(text)
            analytics = next(o for o in rows if o.resource_id == "analytics_601")
            search = next(o for o in rows if o.resource_id == "search_601")
            self.assertTrue(analytics.has_pii)
            self.assertFalse(evaluate(analytics, "ANONYMIZE"))
            self.assertFalse(search.present)
            self.assertFalse(search.has_pii)
            self.assertIn("email:person@example.test", search.link_keys)

    def test_identifier_renaming_and_row_reordering_preserve_facts(self):
        with candidate_scope(["document_rows"]):
            first = self.rows("support_601 | [redacted]\nsupport_602 | person@example.test")
            renamed = self.rows("support_702 | renamed@example.test\nsupport_701 | [redacted]", ("renamed@example.test",))
            self.assertEqual([o.has_pii for o in first], list(reversed([o.has_pii for o in renamed])))

    def test_absence_prose_is_not_a_record_result(self):
        with candidate_scope(["document_rows"]):
            body = self.rows("support_601 | message_text=My shipment was not found at reception | person@example.test")[0]
            self.assertTrue(body.present)
            self.assertTrue(body.has_pii)
            self.assertFalse(evaluate(body, "REDACT"))
            absent = self.rows("support_601 | result=NOT_FOUND | lookup=person@example.test")[0]
            self.assertFalse(absent.present)
            self.assertFalse(absent.has_pii)

    def test_same_line_records_have_separate_status_and_owner(self):
        with candidate_scope(["document_rows"]):
            rows = self.rows("search_601 | NOT_FOUND | person@example.test ; search_602 | FOUND | other@example.test",
                             ("person@example.test", "other@example.test"))
            by_id = {row.resource_id: row for row in rows}
            self.assertFalse(by_id["search_601"].present)
            self.assertTrue(by_id["search_602"].present)
            self.assertEqual(by_id["search_601"].link_keys, {"email:person@example.test"})
            self.assertEqual(by_id["search_602"].link_keys, {"email:other@example.test"})
            ambiguous = self.rows("search_601 and search_602 | NOT_FOUND | person@example.test")
            self.assertTrue(all(row.present is None and not row.link_keys for row in ambiguous))

    def test_technical_integrity_hash_is_not_personal_attribution(self):
        with candidate_scope(["document_rows"]):
            checksum = self.rows("analytics_601 | integrity_seal=sha256:" + "a" * 64)[0]
            self.assertFalse(checksum.has_pii)
            self.assertFalse(checksum.link_keys)
            email_hash = self.rows("analytics_601 | email_hash=sha256:" + "a" * 64)[0]
            self.assertTrue(email_hash.has_pii)
            self.assertIn("hash:" + "a" * 64, email_hash.link_keys)

    def test_flow_status_does_not_come_from_a_zero_subscription_count(self):
        with candidate_scope(["document_rows"]):
            inactive = self.rows("data_flow_601 | inactive | active subscriptions=0")[0]
            self.assertFalse(inactive.flags["enabled"])
            self.assertTrue(evaluate(inactive, "DISABLE_FLOW"))
            active = self.rows("data_flow_601 | active | subscription count=0")[0]
            self.assertTrue(active.flags["enabled"])
            self.assertFalse(evaluate(active, "DISABLE_FLOW"))

    def test_expired_hold_prose_does_not_override_allowed_restore(self):
        with candidate_scope(["document_rows"]):
            allowed = self.rows("backup_601 | restore allowed; legal hold has expired")[0]
            self.assertTrue(allowed.flags["restore_allowed"])
            self.assertFalse(evaluate(allowed, "BLOCK_RESTORE"))
            blocked = self.rows("backup_601 | restore blocked; archive available")[0]
            self.assertFalse(blocked.flags["restore_allowed"])
            self.assertTrue(evaluate(blocked, "BLOCK_RESTORE"))


class IntentAndProofTests(unittest.TestCase):
    def test_mixed_intent_counterexamples(self):
        requests = [
            "Please delete my account, but do not delete invoices that the supplied policy requires you to retain.",
            "Please delete my account and personal data and unsubscribe me from marketing."]
        for text in requests:
            with candidate_scope([]):
                self.assertFalse(classify_request(text).authorized)
            with candidate_scope(["intent_scope"]):
                self.assertTrue(classify_request(text).authorized)

    def test_hypothetical_and_explicit_refusal_do_not_authorize(self):
        requests = ["Explain what would happen if I delete my account. I have not authorized deletion.",
                    "What would happen if I delete my account?", "Do not delete my account. Unsubscribe me."]
        with candidate_scope([]):
            self.assertTrue(classify_request(requests[0]).authorized)
        with candidate_scope(["intent_scope"]):
            for text in requests:
                self.assertFalse(classify_request(text).authorized, text)

    def test_marketing_scoped_deletion_is_not_account_authorization(self):
        requests = ["Delete me from your marketing lists only.",
                    "Please delete my data from marketing lists only."]
        for names in ([], ["intent_scope"]):
            with candidate_scope(names):
                for text in requests:
                    self.assertFalse(classify_request(text).authorized, text)
        with candidate_scope(["intent_scope"]):
            self.assertTrue(classify_request("Please delete my account and unsubscribe me from marketing.").authorized)

    def test_information_and_conditional_questions_do_not_authorize(self):
        with candidate_scope(["intent_scope"]):
            for text in ["Can you explain how to delete my account?",
                         "If I decide to delete my account, please explain the policy.",
                         "I am not asking you to delete my account; I only want information."]:
                self.assertFalse(classify_request(text).authorized, text)
            self.assertTrue(classify_request("Please delete my account; explain the retention policy afterward.").authorized)

    def test_affirmative_deletion_survives_explanation_side_clause(self):
        with candidate_scope(["intent_scope"]):
            for text in ["Please delete my account and explain the deletion process.",
                         "Please permanently delete my account and explain how to verify it is gone."]:
                self.assertTrue(classify_request(text).authorized, text)
            self.assertFalse(classify_request("Can you explain how to delete my account?").authorized)

    def test_side_requests_and_multilingual_controls(self):
        with candidate_scope(["intent_scope"]):
            for text in ["Please remove me from marketing lists only.", "Download my data in a portable format.",
                         "Correct my address without deleting my records.", "Do not delete my profile."]:
                self.assertFalse(classify_request(text).authorized, text)
            for text in ["Please delete my account.", "Solicito a exclusão permanente da conta.",
                         "Je demande la suppression définitive du compte.", "削除を依頼します。", "삭제를 요청합니다."]:
                self.assertTrue(classify_request(text).authorized, text)

    def test_no_resources_is_not_completion_proof(self):
        planner = Planner(policy(), {}, [], {})
        with candidate_scope([]):
            self.assertEqual(planner.assess(case(), {}).status, "COMPLETE")
        with candidate_scope(["completion_proof"]):
            result = planner.assess(case(), {})
            self.assertEqual(result.status, "INCOMPLETE")
            self.assertEqual(result.steps, [])
            self.assertIn("no linked resource evidence", " ".join(result.notes))
            self.assertEqual(planner.assess(case(authorized=False), {}).status, "NOT_APPLICABLE")
            absent = observation(1, present=False)
            planner = Planner(policy(), {absent.resource_id: [absent]}, [], {absent.file_id: absent.captured_at})
            self.assertEqual(planner.assess(case(), {absent.resource_id: Link(absent.resource_id, absent.system, "evidence")}).status, "COMPLETE")


class PolicyAndProcessControls(unittest.TestCase):
    def test_retention_and_hold_expiration_boundaries(self):
        with candidate_scope(CANDIDATES):
            for category, key in (("SECURITY_AUDIT_LOG", "retention_until"), ("LEGAL_HOLD_RECORD", "retained_until")):
                active = policy().decide(category, {key: AUDIT.isoformat()}, AUDIT)
                expired = policy().decide(category, {key: (AUDIT - timedelta(microseconds=1)).isoformat()}, AUDIT)
                self.assertNotEqual(active.treatment, "DELETE")
                self.assertEqual(expired.treatment, "DELETE")
            self.assertTrue(evaluate(observation(refs={"user_id": "user_601"}, flags={"projection_complete": True}), "REDACT"))
            self.assertFalse(evaluate(observation(present=False), "RETAIN"))

    def test_flow_reenable_and_restore_recreation_controls(self):
        for names in ([], ["independent_copies"], list(CANDIDATES)):
            with candidate_scope(names):
                stopped = observation(1, rid="data_flow_601", system="integration_service", flags={"enabled": False})
                active = replace(stopped, file_id="new", obs_time=T0 + timedelta(hours=2), flags={"enabled": True})
                self.assertTrue(state([stopped], "DISABLE_FLOW").compliant)
                self.assertFalse(state([stopped, active], "DISABLE_FLOW").compliant)
                self.assertFalse(evaluate(replace(active, status="DISABLED", flags={"enabled": False, "scheduler_registered": True}), "DISABLE_FLOW"))
                self.assertFalse(evaluate(observation(status="BLOCKED", flags={"restore_allowed": True}), "BLOCK_RESTORE"))
                self.assertTrue(evaluate(observation(flags={"restore_allowed": False, "available": True}), "BLOCK_RESTORE"))
                deleted = observation(1, present=False)
                recreated = observation(2)
                self.assertFalse(state([deleted, recreated]).compliant)

    def test_unknown_policy_is_explicitly_characterized_for_review(self):
        # This is an unresolved inherited assumption, not a proposed fix. No
        # candidate silently invents a new operation or a retention treatment.
        with candidate_scope(CANDIDATES):
            decision = policy().decide("UNRECOGNIZED_CATEGORY", {}, AUDIT)
            self.assertEqual(decision.treatment, "DELETE")
            self.assertIn("no explicit rule", decision.note)


if __name__ == "__main__":
    unittest.main()
