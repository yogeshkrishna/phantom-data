"""Read-only preparation and audited request execution. No expected-answer access."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .contracts import AuditResult, EvidenceRef, parse_instant
from .activity_evidence import normalize_activity
from .corpus import read_manifest
from .identities import IdentityIndex
from .intent import classify_intent
from .json_evidence import EvidenceDiagnostic, read_json_evidence
from .log_evidence import read_log_evidence
from .planner import AuditBlocked, plan
from .policy import PolicyEngine
from .sql_evidence import read_sql_evidence
from .state import StateIndex
from .tabular import load_tables
from .text_evidence import read_text_evidence
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
    activity_digest = hashlib.sha256((dataset_dir / "system_activity.csv").read_bytes()).hexdigest()
    tables = load_tables(dataset_dir)
    if hashlib.sha256((dataset_dir / "system_activity.csv").read_bytes()).hexdigest() != activity_digest:
        raise ValueError("activity CSV changed during loading")
    entries = read_manifest(dataset_dir)
    facts, scopes, diagnostics = [], [], []
    supported = 0
    readers = {".json": read_json_evidence, ".sql": read_sql_evidence,
               ".log": read_log_evidence, ".txt": read_text_evidence}
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
    activity = normalize_activity(tables["system_activity"], activity_digest, facts)
    facts.extend(activity.facts)
    diagnostics.extend(activity.diagnostics)
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
