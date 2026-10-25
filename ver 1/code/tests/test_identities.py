"""Identity ownership and temporal grounding tests use opaque synthetic IDs."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EntityKey, EvidenceFact, EvidenceRef
from phantom.identities import IdentityIndex, normalize_identifier

JAN = "2026-01-01T00:00:00Z"
FEB = "2026-02-01T00:00:00Z"
MAR = "2026-03-01T00:00:00Z"
APR = "2026-04-01T00:00:00Z"


def person(user="subject-A", email="current@example.test", updated=JAN, **values):
    row = dict(user_id=user, name="Shared Name", email=email, phone="+1 (555) 123-4567",
               email_hash="opaque-hash", updated_at=updated)
    row.update(values)
    return row


def change(old="old@example.test", new="current@example.test", time=FEB, user="subject-A",
           audit="audit-A", field="email"):
    return dict(audit_id=audit, user_id=user, event_time=time, field_name=field,
                old_value=old, new_value=new)


def observation(fields, observed=JAN, available=JAN):
    return EvidenceFact(EntityKey("account_service", "primary", "arbitrary-resource"),
                        "USER_PROFILE", observed, "state", "PRESENT", fields, {},
                        tuple(fields), (), {}, EvidenceRef("file", "record.json", "a" * 64,
                        "/record", available))


class IdentityTests(unittest.TestCase):
    def test_current_exact_and_normalized_email_with_source(self):
        index = IdentityIndex([person()], [])
        result = index.resolve(" CURRENT@EXAMPLE.TEST ", MAR)
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.user_ids, ("subject-A",))
        self.assertEqual(result.identifiers["email"], ("current@example.test",))
        self.assertEqual(result.supports[0].source_path, "user_identities.csv")
        self.assertEqual(result.supports[0].evidence_id, "")

    def test_historical_email_and_current_phone_reconstructed(self):
        index = IdentityIndex([person(updated=APR)], [change(),
                              change("+1-111", "+1-222", JAN, audit="audit-phone", field="phone")])
        result = index.resolve("old@example.test", MAR)
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.current["email"], "current@example.test")
        self.assertEqual(result.current["phone"], "+1-222")
        self.assertNotIn("name", result.current)
        self.assertIn("CURRENT_SNAPSHOT_AFTER_CUTOFF", result.issues)
        self.assertEqual({source.evidence_id for source in result.supports}, {"audit-A", "audit-phone"})

    def test_future_events_and_current_row_cannot_seed_identity(self):
        index = IdentityIndex([person(updated=APR)], [change(time=APR)])
        self.assertEqual(index.resolve("old@example.test", MAR).status, "UNRESOLVED")
        self.assertEqual(index.resolve("current@example.test", MAR).status, "UNRESOLVED")

    def test_future_alias_not_in_visible_result(self):
        index = IdentityIndex([person(email="old@example.test")], [change(time=APR)])
        result = index.resolve("old@example.test", MAR)
        self.assertNotIn("current@example.test", result.identifiers["email"])
        self.assertFalse(any(source.evidence_id == "audit-A" for source in result.supports))

    def test_reused_email_is_resolved_by_explicit_ownership_window(self):
        rows = [person(email="shared@example.test"), person("subject-B", "shared@example.test", FEB)]
        events = [change("shared@example.test", "new@example.test", FEB)]
        result = IdentityIndex(rows, events).resolve("shared@example.test", MAR, requested_at=MAR)
        self.assertEqual(result.user_ids, ("subject-B",))
        self.assertIn("HISTORICAL_EMAIL_REUSE", result.issues)
        historical = IdentityIndex(rows, events).resolve("shared@example.test", MAR,
                     requested_at="2026-01-15T00:00:00Z")
        self.assertEqual(historical.user_ids, ("subject-A",))

    def test_shared_email_ambiguous_and_never_unions_people(self):
        rows = [person(email="shared@example.test"), person("subject-B", "shared@example.test")]
        result = IdentityIndex(rows, []).resolve("shared@example.test", MAR)
        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertEqual(result.user_ids, ("subject-A", "subject-B"))
        self.assertEqual(result.current, {})
        self.assertNotIn("phone", result.identifiers)

    def test_equal_name_not_an_identity_edge(self):
        rows = [person(), person("subject-B", "different@example.test")]
        result = IdentityIndex(rows, []).resolve("current@example.test", MAR)
        self.assertEqual(result.user_ids, ("subject-A",))
        self.assertNotIn("different@example.test", result.identifiers["email"])

    def test_multiple_changes_and_inconsistent_chain(self):
        events = [change("a@example.test", "b@example.test", JAN),
                  change("wrong@example.test", "c@example.test", FEB, audit="audit-B")]
        result = IdentityIndex([], events).resolve("a@example.test", MAR)
        self.assertEqual(result.current["email"], "c@example.test")
        self.assertIn("AUDIT_CHAIN_GAP", result.issues)

    def test_same_time_conflict_does_not_choose_lexical_event(self):
        events = [change("old@example.test", "a@example.test", JAN),
                  change("old@example.test", "b@example.test", JAN, audit="audit-B")]
        result = IdentityIndex([], events).resolve("old@example.test", MAR)
        self.assertNotIn("email", result.current)
        self.assertIn("CONFLICTING_IDENTITY_OBSERVATIONS", result.issues)

    def test_visible_fact_links_exact_stable_reference_and_citation(self):
        fields = dict(customer_reference="subject-A", email="observed@example.test")
        index = IdentityIndex([person(updated=APR)], [], [observation(fields)])
        result = index.resolve("observed@example.test", MAR)
        self.assertEqual(result.user_ids, ("subject-A",))
        self.assertEqual(result.supports[0].evidence_id, "file")
        self.assertNotIn("current@example.test", result.identifiers["email"])

    def test_later_secondary_copy_cannot_rewrite_canonical_current_identity(self):
        fields = dict(customer_reference="subject-A", email="stale-copy@example.test")
        index = IdentityIndex([person()], [], [observation(fields, observed=FEB, available=FEB)])
        result = index.resolve("stale-copy@example.test", MAR)
        self.assertEqual(result.current["email"], "current@example.test")
        self.assertIn("stale-copy@example.test", result.identifiers["email"])
        self.assertTrue(all(v.observation_only for v in result.versions if v.source.evidence_id == "file"))

    def test_conflicting_copies_diagnosed_without_losing_canonical_identity(self):
        first = observation(dict(customer_reference="subject-A", email="first@example.test"), FEB, FEB)
        second = observation(dict(customer_reference="subject-A", email="second@example.test"), FEB, FEB)
        result = IdentityIndex([person()], [], [first, second]).resolve("current@example.test", MAR)
        self.assertEqual(result.current["email"], "current@example.test")
        self.assertIn("CONFLICTING_STORED_IDENTITY_OBSERVATIONS", result.issues)

    def test_future_capture_and_query_only_cannot_link(self):
        fields = dict(customer_reference="subject-A", email="observed@example.test")
        index = IdentityIndex([person(updated=APR)], [], [observation(fields, available=APR)])
        self.assertEqual(index.resolve("observed@example.test", MAR).status, "UNRESOLVED")
        fact = observation({})
        fact.query_identifiers["lookup_value"] = ("subject-A", "observed@example.test")
        index = IdentityIndex([person(updated=APR)], [], [fact])
        self.assertEqual(index.resolve("observed@example.test", MAR).status, "UNRESOLVED")

    def test_normalization_keeps_plus_tags_and_country_codes(self):
        self.assertEqual(normalize_identifier("email", " A+B@Example.Test "), "a+b@example.test")
        self.assertEqual(normalize_identifier("phone", "+1 (555) 123-4567"), "+15551234567")
        self.assertNotEqual(normalize_identifier("phone", "555 123 4567"), "+15551234567")

    def test_inputs_copied_and_results_independent(self):
        rows, events = [person()], [change()]
        before = copy.deepcopy((rows, events))
        index = IdentityIndex(rows, events)
        first = index.resolve("old@example.test", MAR)
        first.current["email"] = "changed"
        self.assertNotEqual(index.resolve("old@example.test", MAR).current["email"], "changed")
        self.assertEqual((rows, events), before)

    def test_invalid_inputs_and_chronology(self):
        for rows, events in ((None, []), ([person(), person()], []), ([], [change(), change()])):
            with self.assertRaises(ValueError):
                IdentityIndex(rows, events)
        with self.assertRaises(ValueError):
            IdentityIndex([person()], []).resolve("current@example.test", JAN, requested_at=FEB)


if __name__ == "__main__":
    unittest.main()
