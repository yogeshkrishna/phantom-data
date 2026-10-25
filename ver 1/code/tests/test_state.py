"""Temporal and physical-store counterexamples, independent of corpus IDs."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EntityKey, EvidenceFact, EvidenceRef, SnapshotScope
from phantom.identities import IdentityIndex
from phantom.state import StateIndex

A = "2026-01-01T00:00:00Z"
B = "2026-02-01T00:00:00Z"
C = "2026-03-01T00:00:00Z"


def subject():
    return IdentityIndex([dict(user_id="u", name="Person", email="a@example.test", phone="123",
                               email_hash="hash", updated_at=A)], []).resolve("a@example.test", C)


def fact(fields=None, time=A, state="PRESENT", store="primary", resource="opaque", **changes):
    fields = {"user_id": "u", "email": "a@example.test"} if fields is None else fields
    base = EvidenceFact(EntityKey("service", store, resource), "USER_PROFILE", time, "state", state,
                        fields, {}, tuple(fields), (), {"collection": "profiles"},
                        EvidenceRef("file-" + time[5:7], "evidence.json", "a" * 64, "/record", time))
    return replace(base, **changes)


class TemporalTests(unittest.TestCase):
    def view(self, *facts, scopes=()):
        return StateIndex(facts, scopes).resolve(subject(), C)

    def test_absence_then_resurrection(self):
        view = self.view(fact(), fact({}, B, "ABSENT"), fact(time=C))
        self.assertEqual(view.resources[0].state, "PRESENT")
        self.assertEqual(len(view.resources[0].history), 3)

    def test_primary_absence_does_not_clear_replica(self):
        view = self.view(fact(), fact({}, B, "ABSENT"), fact(store="replica"))
        self.assertEqual({r.entity.store_id: r.state for r in view.resources},
                         {"primary": "ABSENT", "replica": "PRESENT"})

    def test_future_availability_not_old_observation(self):
        invisible = fact({}, B, "ABSENT", source=EvidenceRef("future", "f", "x", "/", "2027-01-01T00:00:00Z"))
        self.assertEqual(self.view(fact(), invisible).resources[0].state, "PRESENT")

    def test_partial_field_does_not_erase_email(self):
        resource = self.view(fact(), fact({"status": "redacted"}, B)).resources[0]
        self.assertIn("email", {field.field for field in resource.fields})

    def test_explicit_full_readback_clears_old_fields(self):
        clean = fact({"status": "redacted"}, B, metadata={"collection": "profiles", "full_record": True})
        self.assertEqual([field.field for field in self.view(fact(), clean).resources[0].fields], ["status"])

    def test_full_and_partial_empty_scope(self):
        scope = SnapshotScope("service", "primary", "profiles", "USER_PROFILE", B, True, (), {}, fact().source)
        self.assertEqual(self.view(fact(), scopes=(scope,)).resources[0].state, "ABSENT")
        self.assertEqual(self.view(fact(), scopes=(replace(scope, complete=False),)).resources[0].state, "PRESENT")
        self.assertEqual(self.view(fact(), scopes=(replace(scope, store_id="other"),)).resources[0].state, "PRESENT")

    def test_scope_query_must_match_subject(self):
        scope = SnapshotScope("service", "primary", "profiles", "USER_PROFILE", B, True, (),
                              {"email": ("other@example.test",)}, fact().source)
        self.assertEqual(self.view(fact(), scopes=(scope,)).resources[0].state, "PRESENT")

    def test_conflict_not_broken_by_file_name(self):
        view = self.view(fact(), fact({}, A, "ABSENT"))
        self.assertEqual(view.resources[0].state, "CONFLICT")
        self.assertIn("CONFLICTING_STATE_READBACKS", view.issues)

    def test_later_readback_resolves_earlier_conflict(self):
        self.assertEqual(self.view(fact(), fact({}, A, "ABSENT"), fact({}, B, "ABSENT")).issues, ())

    def test_historical_link_survives_anonymization(self):
        clean = fact({"email": None, "user_id": None}, B, "ANONYMIZED")
        resource = self.view(fact(), clean).resources[0]
        self.assertEqual(resource.state, "ANONYMIZED")
        self.assertEqual(len(resource.history), 2)

    def test_unlinked_anonymous_record_and_name_are_not_subject(self):
        self.assertFalse(self.view(fact({"name": "Person"})).resources)

    def test_query_identifier_is_not_stored_pii(self):
        resource = self.view(fact({}, query_identifiers={"email": ("a@example.test",)})).resources[0]
        self.assertFalse(resource.fields)

    def test_untyped_lookup_supports_phone_and_hash(self):
        for field, value in (("phone", "123"), ("email_hash", "hash")):
            # Typed selector fields remain authoritative even for nonstandard synthetic hashes.
            resource = self.view(fact({}, query_identifiers={field: (value,)})).resources[0]
            self.assertFalse(resource.fields)

    def test_same_logical_resource_links_clean_replica_without_merging_fields(self):
        replica = fact({"email": None}, B, "ANONYMIZED", store="replica")
        view = self.view(fact(), replica)
        self.assertEqual(len(view.resources), 2)
        self.assertEqual(view.resources[1].fields[0].value, None)

    def test_claim_does_not_erase_state(self):
        resource = self.view(fact(), fact({}, B, "ABSENT", kind="claim")).resources[0]
        self.assertEqual(resource.state, "PRESENT")
        self.assertEqual(len(resource.claims), 1)

    def test_explicit_other_owner_overrides_reused_email(self):
        self.assertFalse(self.view(fact({"user_id": "other", "email": "a@example.test"})).resources)

    def test_reference_preserves_copy_without_numeric_join(self):
        linked = fact({"metric": 1}, resource="copy", references=(fact().entity,))
        self.assertEqual(len(self.view(fact(), linked, fact({"metric": 2}, resource="opaque2")).resources), 2)

    def test_unknown_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            StateIndex([fact(time="yesterday")])
