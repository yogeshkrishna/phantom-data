"""Reconstruct each resource's state at a case's audit_at cutoff.

Rules (and their status as requirement vs. inference):
* [requirement] only statements whose own time is <= audit_at are used; later events never change
  the answer;
* [requirement] a citation must come from a file whose manifest captured_at <= audit_at;
* [inference, confirmed by public case_09] a line timestamped before audit_at inside a file exported
  after audit_at may inform state and linking, but can never be cited;
* the latest observation that speaks to the required treatment decides; at one snapshot instant
  any surviving replica copy is a violation (replication drift);
* [inference] absence by omission, two strengths. Query-style lookups (service exports) never prove
  absence. (a) Override: a snapshot of the account store taken for this audit (file captured within
  30h before audit_at) that omits the account overrides an older explicit "present" statement.
  (b) Pure omission, only for an account with no explicit statement at all: the latest snapshot taken
  after the workflow reported (reported_at <= capture <= audit_at) that omits it is recorded as weak
  evidence of absence. A batch-filtered capture cannot override an explicit observation (public
  case_05: present in the account replica, merely unlisted in a later inventory of another batch).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .candidates import enabled
from .evidence.model import Observation, REFERENCE_FIELDS, Scope, is_blank

DIRECT_REF_FIELDS = ("customer_reference", "user_id", "subject_selector")
AUDIT_CAPTURE_WINDOW = timedelta(hours=30)


@dataclass
class ResourceState:
    resource_id: str
    system: str
    category: str
    compliant: bool | None  # None = no evidence about the required state
    present: bool | None
    deciding: Observation | Scope | None = None
    citation: str = ""  # file_id usable as evidence (captured <= audit_at)
    history: list = field(default_factory=list)
    basis: str = "unknown"  # observation | omission | omission_override | unknown
    late_only: bool = False  # verdict rests only on files captured after audit_at
    trail: list = field(default_factory=list)  # (file_id, captured_at, obs_time, kind, verdict)


def category_of(observations: list[Observation], system: str) -> str:
    types = Counter(o.record_type for o in observations if o.record_type)
    if "SUPPORT_ATTACHMENT" in types:
        return "SUPPORT_ATTACHMENT"
    if "SECONDARY_IDENTIFIER" in types:
        return "SECONDARY_IDENTIFIER"
    if types:
        return types.most_common(1)[0][0]
    from .evidence.records import SYSTEM_TYPES

    return SYSTEM_TYPES.get(system, "USER_PROFILE")


def evaluate(obs: Observation, treatment: str) -> bool | None:
    """True = compliant, False = violation, None = observation says nothing about it."""
    status = (obs.status or "").upper()
    f = obs.flags
    if treatment == "DELETE":
        return None if obs.present is None else (not obs.present)
    if treatment in ("REDACT", "ANONYMIZE"):
        if enabled("marker_content"):
            # A successful operation label cannot erase fields visible on its
            # own readback. Absence and harmless tombstone references still pass.
            if obs.has_pii:
                # A zero-row marker alongside stored contents is contradictory.
                # Lookup selectors are normalized separately and never enter pii.
                return None if obs.present is False else False
            if treatment == "ANONYMIZE" and obs.present is not False and any(
                    not is_blank(obs.refs.get(k)) for k in (
                        REFERENCE_FIELDS if enabled("reference_fields") else DIRECT_REF_FIELDS)):
                return False
        if enabled("projection_completeness") and obs.present is True and not obs.has_pii and status != "IDENTIFIABLE":
            if treatment == "ANONYMIZE" and any(not is_blank(obs.refs.get(k)) for k in (
                    REFERENCE_FIELDS if enabled("reference_fields") else DIRECT_REF_FIELDS)):
                return False
            coverage = [f.get(k) for k in ("projection_complete", "all_fields_returned", "content_complete")]
            if not any(value is True for value in coverage):
                return None
        if status in ("REDACTED", "ANONYMIZED"):
            if enabled("reference_fields") and treatment == "ANONYMIZE" and any(
                    not is_blank(obs.refs.get(k)) for k in REFERENCE_FIELDS):
                return False
            return True
        if status == "IDENTIFIABLE":
            return False
        if obs.present is False:
            return True
        if obs.present is None:
            return None
        if obs.has_pii:
            return False
        if treatment == "ANONYMIZE" and any(not is_blank(obs.refs.get(k)) for k in (
                REFERENCE_FIELDS if enabled("reference_fields") else DIRECT_REF_FIELDS)):
            return False
        return True
    if treatment == "DISABLE_FLOW":
        if status == "ACTIVE" or f.get("enabled") is True or f.get("scheduler_registered") is True:
            return False
        if status == "DISABLED" or f.get("enabled") is False:
            return True
        return None
    if treatment == "BLOCK_RESTORE":
        if status == "ACTIVE" or f.get("restore_allowed") is True or str(f.get("decision", "")).lower() == "allow":
            return False
        if f.get("eligible") is True and f.get("deletion_block") is not True:
            return False
        if status == "BLOCKED" or f.get("restore_allowed") is False or str(f.get("decision", "")).lower() == "deny" \
                or f.get("deletion_block") is True or f.get("eligible") is False:
            return True
        return None
    if treatment == "RETAIN":
        return None if obs.present is None else bool(obs.present)
    return None


def audit_capture(system: str, scopes: list[Scope], audit_at: datetime, file_captured: dict,
                  since: datetime | None = None, resource_id: str = "", stores: set | None = None) -> Scope | None:
    """Latest snapshot-type capture of `system` with obs_time >= `since` and file captured <= audit_at.
    Pass since=audit_at - AUDIT_CAPTURE_WINDOW for the capture taken for this audit."""
    captured = [s for s in scopes if s.system == system and s.kind == "snapshot" and s.obs_time <= audit_at
                and file_captured.get(s.file_id) and file_captured[s.file_id] <= audit_at
                and (since is None or s.obs_time >= since)]
    if enabled("snapshot_coverage"):
        captured = [s for s in captured if s.exhaustive is True
                    and (not stores or s.store in stores)
                    and (not s.selector_keys or resource_id in s.selector_keys)]
    if not captured:
        return None
    return max(captured, key=lambda s: (s.obs_time, file_captured[s.file_id]))


def reconstruct(resource_id: str, system: str, treatment: str, category: str, observations: list[Observation],
                scopes: list[Scope], audit_at: datetime, file_captured: dict,
                since: datetime | None = None, strict_capture: bool = False) -> ResourceState:
    """strict_capture=True applies the literal cutoff to whole files: a statement is usable only if
    its file's manifest captured_at is also <= audit_at (comparison mode; see evaluation report)."""
    usable = [o for o in observations if o.obs_time <= audit_at and
              (not strict_capture or (file_captured.get(o.file_id) and file_captured[o.file_id] <= audit_at))]
    if enabled("independent_copies"):
        return _independent_state(resource_id, system, treatment, category, usable, audit_at, file_captured)
    judged = [(o, evaluate(o, treatment)) for o in usable]
    if enabled("marker_content") and treatment in ("REDACT", "ANONYMIZE"):
        # A conflicting count/content readback is positive evidence that the
        # proof is unresolved. Do not silently discard it and reuse an earlier
        # clean reading. Only a later explicit reading of that same copy can
        # resolve the contradiction; another store's absence cannot do so.
        def copy_key(obs):
            return (str(obs.flags.get("physical_copy") or obs.store or "unspecified"), obs.replica)

        conflicts = [o for o, value in judged if o.present is False and o.has_pii and value is None]
        unresolved = [o for o in conflicts if not any(
            later.obs_time > o.obs_time and copy_key(later) == copy_key(o) and value is not None
            for later, value in judged)]
        known = [(o, value) for o, value in judged if value is not None]
        known_time = max((o.obs_time for o, _ in known), default=None)
        known_violation = any(value is False and o.obs_time == known_time for o, value in known)
        if unresolved and not known_violation:
            deciding = max(unresolved, key=lambda o: (o.obs_time, o.captured_at, o.file_id))
            return ResourceState(
                resource_id, system, category, None, None, deciding, "",
                [o.file_id for o in usable], "contradictory_content",
                not any(file_captured.get(o.file_id) and file_captured[o.file_id] <= audit_at for o in usable),
                [(o.file_id, file_captured.get(o.file_id), o.obs_time, o.kind, value) for o, value in judged])
    judged = [(o, v) for o, v in judged if v is not None]
    trail = [(o.file_id, file_captured.get(o.file_id), o.obs_time, o.kind, v) for o, v in judged]
    present = None
    for o in reversed(usable):
        if o.present is not None:
            present = o.present
            break
    account_delete = treatment == "DELETE" and system == "account_service"
    stores = {o.store for o in usable if o.store}
    capture = audit_capture(system, scopes, audit_at, file_captured,
                            audit_at - AUDIT_CAPTURE_WINDOW, resource_id, stores) if account_delete else None
    post_workflow = audit_capture(system, scopes, audit_at, file_captured, since, resource_id, stores) if account_delete else None

    if judged and capture is not None and capture.obs_time > judged[-1][0].obs_time:
        # The audit-time capture of the account store is newer than every explicit statement; it
        # changes the answer only when it contradicts the latest one.
        verdict = resource_id not in capture.listed
        if verdict != judged[-1][1]:
            return ResourceState(resource_id, system, category, verdict, not verdict, capture, capture.file_id,
                                 [o.file_id for o, _ in judged] + [capture.file_id], "omission_override",
                                 False, trail + [(capture.file_id, file_captured.get(capture.file_id),
                                                  capture.obs_time, "scope", verdict)])
    if judged:
        last_time = judged[-1][0].obs_time
        same_time = [(o, v) for o, v in judged if o.obs_time == last_time]
        # replica drift: at one snapshot instant any surviving copy is a violation
        verdict = all(v for _, v in same_time)
        deciding = next((o for o, v in reversed(same_time) if v == verdict), same_time[-1][0])
        citable = [o for o, v in judged if v == verdict and file_captured.get(o.file_id) and
                   file_captured[o.file_id] <= audit_at]
        if enabled("citation_continuity"):
            contradictory = [o.obs_time for o, value in judged if value != verdict]
            if contradictory:
                boundary = max(contradictory)
                citable = [o for o in citable if o.obs_time > boundary or (not verdict and o.obs_time == boundary)]
        citation = citable[-1].file_id if citable else ""
        if not citation and verdict and capture is not None and resource_id not in capture.listed:
            citation = capture.file_id  # explicit 404 only in a late export; audit capture agrees
        late_only = not any(file_captured.get(o.file_id) and file_captured[o.file_id] <= audit_at
                            for o, _ in judged)
        return ResourceState(resource_id, system, category, verdict, present, deciding, citation,
                             [o.file_id for o, _ in judged], "observation", late_only, trail)
    if post_workflow is not None:
        verdict = resource_id not in post_workflow.listed
        return ResourceState(resource_id, system, category, verdict, not verdict, post_workflow,
                             post_workflow.file_id if verdict else "", [post_workflow.file_id], "omission", False,
                             [(post_workflow.file_id, file_captured.get(post_workflow.file_id),
                               post_workflow.obs_time, "scope", verdict)])
    return ResourceState(resource_id, system, category, None, present, None, "", [], "unknown", False, trail)


def _independent_state(resource_id, system, treatment, category, observations, audit_at, file_captured):
    """A read of one physical store never proves the contents of another store.

    Store labels are not guessed to be aliases. A source may explicitly supply
    physical_copy to join readings of the same copy; unknown copy identity stays
    in its own group. Snapshot omission is deliberately insufficient here.
    """
    copies = {}
    for obs in sorted(observations, key=lambda o: (o.obs_time, o.captured_at, o.file_id, o.position)):
        key = (str(obs.flags.get("physical_copy") or obs.store or "unspecified"), obs.replica)
        copies.setdefault(key, []).append(obs)
    if not copies:
        return ResourceState(resource_id, system, category, None, None)
    last = []
    trail = []
    for rows in copies.values():
        judged = [(o, evaluate(o, treatment)) for o in rows]
        trail.extend((o.file_id, file_captured.get(o.file_id), o.obs_time, o.kind, v) for o, v in judged)
        instant = rows[-1].obs_time
        latest = [(o, value) for o, value in judged if o.obs_time == instant]
        # A contradiction within one observation time cannot establish cleanup.
        value = False if any(v is False for _, v in latest) else (
            None if any(v is None for _, v in latest) else True)
        deciding = next((o for o, v in reversed(latest) if v is value), latest[-1][0])
        citable = [o for o, v in judged if v is value and file_captured.get(o.file_id)
                   and file_captured[o.file_id] <= audit_at]
        contradictions = [o.obs_time for o, v in judged if v is not None and v is not value]
        if contradictions:
            boundary = max(contradictions)
            citable = [o for o in citable if o.obs_time > boundary or (value is False and o.obs_time == boundary)]
        last.append((deciding, value, citable[-1].file_id if citable else ""))
    verdict = False if any(v is False for _, v, _ in last) else (
        None if any(v is None for _, v, _ in last) else True)
    witnesses = [(o, v, citation) for o, v, citation in last if v is verdict]
    deciding, _, citation = max(witnesses, key=lambda row: (bool(row[2]), row[0].obs_time))
    # Completion over several copies needs all their support; the CSV permits
    # one evidence_id only, so a common captured file must prove all copies.
    if verdict is True and len(last) > 1 and len({c for _, _, c in last}) != 1:
        citation = ""
    present = True if any(o.present is True for o, _, _ in last) else (
        False if all(o.present is False for o, _, _ in last) else None)
    late_only = not any(file_captured.get(o.file_id) and file_captured[o.file_id] <= audit_at
                        for o in observations)
    return ResourceState(resource_id, system, category, verdict, present, deciding, citation,
                         [o.file_id for o in observations], "independent_copies", late_only, trail)
