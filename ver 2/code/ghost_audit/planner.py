"""Turn reconstructed resource states into the ordered findings/remediation/verification plan."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import OPERATIONS, TREATMENT_OPS
from .linker import CONFIDENCE_RANK, CaseContext, Link
from .output import order_steps
from .policy import Policy
from .state import ResourceState, category_of, evaluate, reconstruct


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    resources: list = field(default_factory=list)  # per-resource diagnostic dicts
    notes: list = field(default_factory=list)
    status_relies_on: str = "direct"  # weakest link confidence the status decision depends on
    step_confidence: list = field(default_factory=list)  # link confidence of each output step


def _step(phase_op: str, system: str, resource: str, evidence: str) -> dict:
    phase, state = OPERATIONS[phase_op]
    return {"step_no": 0, "phase": phase, "system_id": system, "resource_id": resource,
            "operation": phase_op, "state": state, "evidence_id": evidence}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


class Planner:
    def __init__(self, policy: Policy, obs_by_resource: dict, scopes: list, file_captured: dict,
                 strict_capture: bool = False):
        self.policy = policy
        self.obs_by_resource = obs_by_resource
        self.scopes = scopes
        self.file_captured = file_captured
        self.strict_capture = strict_capture

    def _state(self, rid: str, system: str, case: CaseContext):
        observations = self.obs_by_resource.get(rid, [])
        if self.strict_capture:
            observations = [o for o in observations if self.file_captured.get(o.file_id)
                            and self.file_captured[o.file_id] <= case.audit_at]
        category = category_of(observations, system)
        latest_flags = {}
        for o in observations:
            if o.obs_time <= case.audit_at:
                latest_flags.update(o.flags)
        decision = self.policy.decide(category, latest_flags, case.audit_at)
        state = reconstruct(rid, system, decision.treatment, category, observations, self.scopes,
                            case.audit_at, self.file_captured, since=case.reported_at,
                            strict_capture=self.strict_capture)
        return decision, state

    def assess(self, case: CaseContext, links: dict[str, Link], tie_candidates: list | None = None) -> CaseResult:
        """tie_candidates: (resource_id, system) of every record in an unresolved tie this request is
        part of. They are never written as steps, but COMPLETE is claimed only if all of them are
        proven compliant (then the requester's record is compliant whichever candidate it is)."""
        if not case.authorized:
            return CaseResult(case.case_id, "NOT_APPLICABLE", [], notes=["request does not authorize deletion"])
        states: list[tuple[Link, object, ResourceState]] = []
        for rid, link in sorted(links.items()):
            decision, state = self._state(rid, link.system, case)
            states.append((link, decision, state))

        notes = []
        # A violation becomes a finding only if a file captured <= audit_at shows the violation itself.
        violations, uncitable = [], []
        for link, decision, state in states:
            if state.compliant is not False:
                continue
            evidence = state.citation or self._fallback_evidence(link, decision.treatment, case.audit_at)
            (violations if evidence else uncitable).append((link, decision, state, evidence))
        # Completion is not established while a relevant record is unaccounted for.
        blockers = [f"{l.resource_id}: violation visible only in evidence captured after audit_at"
                    for l, _, _, _ in uncitable]
        for link, decision, state in states:
            if state.compliant is None and CONFIDENCE_RANK[link.confidence] >= CONFIDENCE_RANK["strong"]:
                blockers.append(f"{link.resource_id}: state at audit_at unknown")
        tie_states = []
        for rid, system in tie_candidates or []:
            _, st = self._state(rid, system, case)
            tie_states.append((rid, st.compliant))
            if st.compliant is not True:
                blockers.append(f"{rid}: tie candidate (possibly the requester's) not proven compliant")
        if tie_states and all(v is True for _, v in tie_states):
            notes.append(f"unattributed tie: all {len(tie_states)} candidate records proven compliant "
                         f"({', '.join(sorted(r for r, _ in tie_states))}); none asserted")

        steps = []
        if violations or blockers:
            # The schema has no UNKNOWN status: a case whose completion cannot be established is
            # INCOMPLETE. Only evidenced violations become findings; nothing is invented to fill gaps.
            status = "INCOMPLETE"
            for link, decision, state, evidence in violations:
                finding, remediation, _ = TREATMENT_OPS[decision.treatment]
                if finding:
                    steps.append(_step(finding, link.system, link.resource_id, evidence))
                if remediation:
                    steps.append(_step(remediation, link.system, link.resource_id, ""))
            for link, decision, state in states:
                verify = TREATMENT_OPS[decision.treatment][2]
                steps.append(_step(verify, link.system, link.resource_id, ""))
            notes.extend(f"completion not established: {b}" for b in blockers)
            if violations:
                weakest_needed = max((l.confidence for l, _, _, _ in violations), key=lambda c: CONFIDENCE_RANK[c])
            else:
                weakest_needed = "unestablished"
        else:
            status = "COMPLETE"
            for link, decision, state in states:
                if state.compliant is None or not state.citation:
                    notes.append(f"{link.resource_id} ({link.confidence} link): no citable evidence of final "
                                 f"state; omitted from verification")
                    continue
                verify = TREATMENT_OPS[decision.treatment][2]
                if state.present is False and decision.treatment in ("REDACT", "ANONYMIZE"):
                    verify = "VERIFY_RECORD_ABSENT"
                steps.append(_step(verify, link.system, link.resource_id, state.citation))
            weakest_needed = "direct"  # no violation: removing inferred links cannot create one
        ordered = order_steps(steps)
        confidence_of = {l.resource_id: l.confidence for l, _, _ in states}
        resources = [self._describe(l, d, s, case) for l, d, s in states]
        for l, _, _ in states:
            if l.conflicts:
                notes.extend(l.conflicts)
        return CaseResult(case.case_id, status, ordered, resources, notes,
                          status_relies_on=weakest_needed,
                          step_confidence=[confidence_of.get(s["resource_id"], "direct") for s in ordered])

    def _describe(self, link: Link, decision, state: ResourceState, case: CaseContext) -> dict:
        deciding = state.deciding
        return {
            "resource_id": link.resource_id, "system_id": link.system,
            "link": {"confidence": link.confidence, "method": link.method, "basis": link.basis,
                     "evidence_or_activity": link.detail[:6], "candidates": link.candidates,
                     "conflicts": link.conflicts},
            "policy": {"category": decision.category, "rule_id": decision.rule_id, "required_action": decision.action,
                       "treatment": decision.treatment, "condition": decision.note},
            "state_at_audit": {
                "compliant": state.compliant, "present": state.present, "basis": state.basis,
                "deciding_file": getattr(deciding, "file_id", None),
                "deciding_time": _iso(getattr(deciding, "obs_time", None)),
                "citation": state.citation, "rests_only_on_files_captured_after_audit": state.late_only,
                "trail": [{"file_id": f, "captured_at": _iso(c), "observed_at": _iso(t), "kind": k, "compliant": v}
                          for f, c, t, k, v in state.trail[-6:]],
            },
        }

    def _fallback_evidence(self, link: Link, treatment: str, audit_at) -> str:
        """Latest file captured <= audit_at whose own statement shows the violation (not merely a
        mention of the record)."""
        citable = [o for o in self.obs_by_resource.get(link.resource_id, [])
                   if o.obs_time <= audit_at and self.file_captured.get(o.file_id)
                   and self.file_captured[o.file_id] <= audit_at and evaluate(o, treatment) is False]
        return citable[-1].file_id if citable else ""
