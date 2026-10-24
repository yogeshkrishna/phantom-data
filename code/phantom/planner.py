"""Render grounded obligations into the locked logical-resource output schema."""

from collections import defaultdict

from .contracts import AuditResult, AuditStep, OPERATION_RULES, to_csv_row
from .intent import IntentDecision
from .policy import Obligation


class AuditBlocked(ValueError):
    """No honest, schema-valid result is supported; do not manufacture a status."""


def plan(case_id: str, intent: IntentDecision, obligations: tuple[Obligation, ...],
         *, issues: tuple[str, ...] = ()) -> AuditResult:
    if intent.authorization == "NOT_REQUESTED":
        return AuditResult(case_id, "NOT_APPLICABLE", ())
    if intent.authorization != "REQUESTED":
        raise AuditBlocked("AMBIGUOUS_AUTHORIZATION")
    if not obligations:
        raise AuditBlocked("NO_RELEVANT_RESOURCE_PROOF")
    failures = [o for o in obligations if o.finding and o.remediation]
    unknown = [o for o in obligations if o.compliant is None]
    complete = not failures and not unknown and not issues
    if not complete and not failures:
        raise AuditBlocked("UNPROVEN_COMPLETION_WITHOUT_SUPPORTED_FINDING")
    status = "COMPLETE" if complete else "INCOMPLETE"
    pending = defaultdict(list)
    all_issues = set(issues)
    for obligation in obligations:
        all_issues.update(obligation.reasons)
        system, resource = obligation.entity.system_id, obligation.entity.resource_id
        if obligation.finding and obligation.remediation:
            if not obligation.proofs:
                raise AuditBlocked("FINDING_WITHOUT_VISIBLE_CITATION")
            pending["FINDING", obligation.finding, system, resource].append(obligation)
            pending["REMEDIATION", obligation.remediation, system, resource].append(obligation)
        pending["VERIFICATION", obligation.verification, system, resource].append(obligation)
    order = {(phase, operation): index for index, (phase, operation, _) in enumerate(OPERATION_RULES)}
    states = {(phase, operation): state for phase, operation, state in OPERATION_RULES}
    steps = []
    for (phase, operation, system, resource), group in sorted(pending.items(),
            key=lambda item: (order[item[0][:2]], item[0][2], item[0][3])):
        if complete and phase != "VERIFICATION":
            continue
        evidence = ""
        if phase == "FINDING":
            evidence = sorted({ref.evidence_id for o in group for ref in o.proofs})[0]
        elif complete:
            # One output step cannot carry multiple citations. Require one source
            # that supports this check across EVERY physical store of this resource.
            common = set.intersection(*({ref.evidence_id for ref in o.proofs} for o in group))
            if not common:
                raise AuditBlocked("NO_SINGLE_CITATION_COVERS_PHYSICAL_STORES")
            evidence = sorted(common)[0]
        steps.append(AuditStep(len(steps) + 1, phase, system, resource, operation,
                               states[phase, operation], evidence))
    result = AuditResult(case_id, status, tuple(steps), tuple(sorted(all_issues)))
    to_csv_row(result)
    return result
