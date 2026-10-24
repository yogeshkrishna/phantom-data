"""Read-only preparation and audited request execution. No expected-answer access."""

from dataclasses import dataclass
from pathlib import Path

from .contracts import AuditResult, EvidenceRef, parse_instant
from .corpus import read_manifest
from .identities import IdentityIndex
from .intent import classify_intent
from .json_evidence import EvidenceDiagnostic, read_json_evidence
from .planner import AuditBlocked, plan
from .policy import PolicyEngine
from .sql_evidence import read_sql_evidence
from .state import StateIndex
from .tabular import load_tables
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


def prepare(dataset_dir: Path) -> PreparedAudit:
    tables = load_tables(dataset_dir)
    entries = read_manifest(dataset_dir)
    facts, scopes, diagnostics = [], [], []
    supported = 0
    readers = {".json": read_json_evidence, ".sql": read_sql_evidence}
    for entry in entries:
        reader = readers.get(entry.path.suffix.lower())
        if reader is None:
            diagnostics.append(EvidenceDiagnostic("UNSUPPORTED_EVIDENCE", EvidenceRef(
                entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at),
                "Evidence not yet interpreted; completion is unproven"))
            continue
        batch = reader(entry)
        supported += 1
        facts.extend(batch.facts)
        scopes.extend(batch.scopes)
        diagnostics.extend(batch.diagnostics)
    return PreparedAudit(tables, IdentityIndex(tables["user_identities"], tables["user_audit_log"], facts),
                         StateIndex(facts, scopes), PolicyEngine(tables["deletion_rules"]),
                         tuple(diagnostics), len(entries), supported)


def audit_request(prepared: PreparedAudit, request: dict[str, str]) -> AuditResult:
    intent = classify_intent(request["request_text"])
    if intent.authorization == "NOT_REQUESTED":
        result = plan(request["case_id"], intent, ())
    else:
        identity = prepared.identities.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
        if identity.status != "RESOLVED":
            raise AuditBlocked("IDENTITY_NOT_RESOLVED")
        view = prepared.states.resolve(identity, request["audit_at"])
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
