"""Read-only preparation and audited request execution. No expected-answer access."""

from dataclasses import dataclass
from pathlib import Path

from .contracts import AuditResult, EvidenceRef, parse_instant
from .json_evidence import EvidenceDiagnostic
from .identities import IdentityIndex
from .intent import classify_intent
from .planner import AuditBlocked, plan
from .policy import PolicyEngine
from .state import StateIndex
from .verifier import verify_result


@dataclass(frozen=True, slots=True)
class PreparedAudit:
    tables: dict
    identities: IdentityIndex
    states: StateIndex
    policy: PolicyEngine
    diagnostics: tuple[EvidenceDiagnostic, ...]
    files_attempted: int
    files_supported: int
    model_usage: dict | None = None
    inventory: tuple[dict, ...] = ()
    cache_key: str = ""
    cache_hit: bool = False


def prepare(dataset_dir: Path, *, media_cache: Path | None = None,
            cache_dir: Path | None = None) -> PreparedAudit:
    from .compiler import compile_corpus
    compiled = compile_corpus(dataset_dir, cache_dir=cache_dir, media_cache=media_cache)
    tables, facts = compiled.tables, compiled.facts
    return PreparedAudit(tables, IdentityIndex(tables["user_identities"], tables["user_audit_log"], facts),
                         StateIndex(facts, compiled.scopes), PolicyEngine(tables["deletion_rules"]),
                         compiled.diagnostics, len(compiled.inventory),
                         sum(item["status"] != "ERROR" for item in compiled.inventory),
                         compiled.model_usage, compiled.inventory, compiled.cache_key, compiled.cache_hit)


def audit_request(prepared: PreparedAudit, request: dict[str, str]) -> AuditResult:
    intent = classify_intent(request["request_text"])
    if intent.authorization == "NOT_REQUESTED":
        result = plan(request["case_id"], intent, ())
    else:
        identity = prepared.identities.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
        if identity.status != "RESOLVED":
            raise AuditBlocked("IDENTITY_NOT_RESOLVED")
        view = prepared.states.resolve(identity, request["audit_at"])
        if "RESOURCE_OWNERSHIP_CONFLICT" in view.issues:
            raise AuditBlocked("RESOURCE_OWNERSHIP_CONFLICT")
        visible_coverage = tuple(sorted({d.code for d in prepared.diagnostics
                                 if parse_instant(d.source.available_at) <= parse_instant(request["audit_at"])}))
        identity_issues = tuple(issue for issue in identity.issues if issue in {
            "AUDIT_CHAIN_GAP", "CURRENT_AUDIT_GAP", "CONFLICTING_IDENTITY_OBSERVATIONS"})
        result = plan(request["case_id"], intent, prepared.policy.evaluate(view),
                      issues=visible_coverage + view.issues + identity_issues)
    report = verify_result(result, request, prepared.identities, prepared.states,
                           prepared.tables["deletion_rules"], coverage_issues=tuple(
                               d.code for d in prepared.diagnostics if
                               parse_instant(d.source.available_at) <= parse_instant(request["audit_at"])))
    if not report.valid:
        raise AuditBlocked("VERIFIER_REJECTED:" + ",".join(report.issues))
    return result
