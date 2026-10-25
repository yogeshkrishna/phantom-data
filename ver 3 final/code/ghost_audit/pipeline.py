"""End-to-end audit pipeline shared by the CLI and the evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .activity import ActivityIndex
from .authorization import classify_request
from .config import EVIDENCE_CACHE_DIR
from .evidence.registry import EvidenceIndex
from .identity import IdentityIndex
from .linker import CONFIDENCE_RANK, CaseContext, Linker
from .loaders import Dataset, parse_ts
from .planner import CaseResult, Planner
from .policy import Policy

# Which link confidence classes a run may use (see linker.py for the definitions).
LINK_POLICIES = {
    "no-guess": {"direct", "strong", "weak"},  # default: never assert an unbroken tie as fact
    "full": {"direct", "strong", "weak", "unresolved"},  # also pair genuine ties deterministically
    "corroborated": {"direct", "strong"},  # only identifiers/catalog + corroborated unique slots
}
DEFAULT_LINK_POLICY = "no-guess"


@dataclass
class RunResult:
    results: list[CaseResult]
    coverage: dict
    linker_diagnostics: list
    auth: dict = field(default_factory=dict)
    llm_usage: list = field(default_factory=list)
    subjects: dict = field(default_factory=dict)
    event_log: dict = field(default_factory=dict)
    cadence: list = field(default_factory=list)
    link_policy: str = DEFAULT_LINK_POLICY
    ocr_usage: dict = field(default_factory=dict)
    evidence_index: object = field(default=None, repr=False)


def run_pipeline(data_dir: Path, requests_file: str = "deletion_requests.csv", llm=None,
                 link_policy: str = DEFAULT_LINK_POLICY, use_bundle_order: bool = False,
                 strict_capture_cutoff: bool = False) -> RunResult:
    """strict_capture_cutoff=False (default): a statement timestamped <= audit_at inside a file
    exported after audit_at may inform linking and state but is never cited. True: such files are
    ignored entirely for that request (comparison mode; see evaluation report)."""
    if EVIDENCE_CACHE_DIR.resolve().is_relative_to(Path(data_dir).resolve()):
        raise ValueError("OCR cache directory must be outside the read-only corpus")
    allowed = LINK_POLICIES[link_policy]
    from .evidence.documents import reset_ocr_telemetry, get_ocr_telemetry

    reset_ocr_telemetry()
    ds = Dataset(Path(data_dir), requests_file)
    evidence = EvidenceIndex(ds.data_dir, ds.manifest)
    identity = IdentityIndex(ds.identities, ds.audit_log)
    activity = ActivityIndex(ds.activity)
    policy = Policy(ds.rules)

    predict_ids = {r["case_id"] for r in ds.requests}
    cases: list[CaseContext] = []
    auth = {}
    subjects = {}
    for row in ds.all_requests():
        decision = classify_request(row["request_text"])
        if llm is not None and row["case_id"] in predict_ids and not decision.confident:
            decision = llm.classify(row["request_text"], decision)
        auth[row["case_id"]] = decision
        subject = identity.resolve_email(row["email"], audit_at=parse_ts(row["audit_at"]))
        cases.append(CaseContext(
            case_id=row["case_id"], request=row, subject=subject,
            requested_at=parse_ts(row["requested_at"]), reported_at=parse_ts(row["reported_at"]),
            audit_at=parse_ts(row["audit_at"]), reported_status=row["reported_status"],
            authorized=decision.authorized, predict=row["case_id"] in predict_ids))
        if row["case_id"] in predict_ids:
            email = row["email"].strip().lower()
            subjects[row["case_id"]] = None if subject is None else {
                "user_id": subject.user_id,
                "matched": "current_email" if email == subject.current_email else "historical_email",
                "identifier_changes": subject.history,
            }

    linker = Linker(cases, evidence, identity, activity, use_bundle_order=use_bundle_order,
                    strict_capture=strict_capture_cutoff)
    links = linker.run()
    ties = linker.tie_candidates()
    planner = Planner(policy, linker.obs_by_resource, evidence.scopes, evidence.captured,
                      strict_capture=strict_capture_cutoff)
    results = []
    for case in cases:
        if not case.predict:
            continue
        if case.authorized and case.subject is None:
            res = CaseResult(case.case_id, "INCOMPLETE", [], notes=["request email does not resolve to a subject"])
        else:
            kept = {rid: l for rid, l in links.get(case.case_id, {}).items() if l.confidence in allowed}
            res = planner.assess(case, kept, sorted(t for t in ties.get(case.case_id, ()) if t[0] not in kept))
            dropped = sorted(rid for rid, l in links.get(case.case_id, {}).items() if l.confidence not in allowed)
            if dropped:
                res.notes.append(f"excluded by link policy '{link_policy}': {', '.join(dropped)}")
        results.append(res)
    return RunResult(results, evidence.coverage_summary(), linker.diagnostics,
                     {k: {"label": v.label, "authorized": v.authorized, "cues": v.cues[:4]}
                      for k, v in auth.items() if k in predict_ids},
                     llm.usage if llm is not None else [], subjects, linker.event_log, linker.cadence, link_policy,
                     get_ocr_telemetry(), evidence)


__all__ = ["run_pipeline", "RunResult", "LINK_POLICIES", "DEFAULT_LINK_POLICY", "CONFIDENCE_RANK"]
