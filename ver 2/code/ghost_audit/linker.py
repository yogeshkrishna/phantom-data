"""Resource discovery: which records belong to each case's subject.

Every link carries a confidence class with a defined meaning:

  direct      an exact identifier of the subject (current or historical email, email hash, phone,
              stable user_id) on the record itself or on its lookup trace, observed within
              [requested_at, audit_at]; or a data-catalog row (LINK_RESOURCE_IDENTITY) naming the
              subject, not contradicted by another known customer's identifiers on the record.
  strong      a deletion-worker event that falls in exactly one request's reporting slot once
              requests already holding a directly-linked record of that system are excluded, and
              whose record is also present in that request's audit-time evidence (two independent
              sources: the activity stream and the audit capture).
  weak        any other workflow-slot inference: uniqueness that needs exclusivity propagation,
              learned run cadence, bundle record order, evidence-coverage consistency or the
              clean-run filter. These are timing/ordering/workflow heuristics.
  unresolved  a tie that the evidence cannot break, paired deterministically (`tie_guess`).

Workflow metadata (event time, reported result) is only ever a *linkage* clue. It is never
evidence of a record's state: state comes exclusively from evidence observations (state.py).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .activity import ActivityIndex
from .evidence.model import EMAIL_RE, Observation, keys_from_value, system_for_resource
from .evidence.registry import EvidenceIndex
from .identity import IdentityIndex, Subject

HOUR = timedelta(hours=1)
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
CONFIDENCE_RANK = {"direct": 3, "strong": 2, "weak": 1, "unresolved": 0}
OCR_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z_])(account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|cache|"
    r"email_delivery|marketing|legal_hold|object)_([0-9oO]{1,6})(?![0-9])"
)
STREAMING_WORDS = ("continuous", "streaming", "delivered", "healthy", "running", "active")
STOPPED_WORDS = ("disabled", "paused", "stopped", "inactive", "suspended")
RESTORE_BLOCK_WORDS = ("blocked", "deny", "denied", "restore disabled", "legal hold", "no restore")


@dataclass
class CaseContext:
    case_id: str
    request: dict
    subject: Subject | None
    requested_at: datetime
    reported_at: datetime
    audit_at: datetime
    reported_status: str
    authorized: bool
    predict: bool = True  # False for context-only requests (other request files)

    @property
    def keys(self) -> set:
        return self.subject.keys if self.subject else set()


@dataclass
class Link:
    resource_id: str
    system: str
    basis: str
    detail: list = field(default_factory=list)
    confidence: str = "direct"
    method: str = ""
    candidates: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


_GLUED_ID_RE = re.compile(
    r"((?:user|account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|cache|"
    r"email_delivery|marketing|legal_hold|object)_[0-9oO]+)[-–·](?=\S)")


def _norm_ocr(text: str) -> str:
    # OCR renders "user_20 · analytics_7438 · a@b.com" as "user_20-analytics_7438-a@b.com"; split the
    # glued identifiers so the e-mail regex does not swallow them into the local part.
    text = text.replace("·", " ")
    return _GLUED_ID_RE.sub(r"\1 ", _GLUED_ID_RE.sub(r"\1 ", text))


def wf_kind(a) -> str:
    d = a.details
    if a.event_type == "DELETE_RECORD" and a.target == "account_service" and "primary profile" in d:
        return "account"
    if "requested operation" in d or "stopped after" in d or "internal work" in d:
        return "direct"
    if "Policy action completed" in d:
        return "success"
    return "followup"


WINDOWS = {  # hours relative to reported_at
    "account": (-1.6, -0.4),
    "success": (-0.75, 0.05),
    "followup": (0.25, 2.3),
    "direct": (-0.35, 0.35),
}


class Linker:
    def __init__(self, cases: list[CaseContext], evidence: EvidenceIndex, identity: IdentityIndex,
                 activity: ActivityIndex, use_bundle_order: bool = False, strict_capture: bool = False):
        # Bundle record order is the weakest resolver (an ordering artifact with no public-sample
        # support), so it is opt-in; without it those ties stay recorded as ambiguous.
        self.use_bundle_order = use_bundle_order
        # strict_capture: a request may only use files whose manifest captured_at <= its audit_at
        # (and activity rows timed <= audit_at), for identity and ownership as well as state.
        self.strict_capture = strict_capture
        self.cases = cases
        self.by_case = {c.case_id: c for c in cases}
        self.evidence = evidence
        self.identity = identity
        self.activity = activity
        self.ambiguous_keys = {k for k, owners in identity.key_owner.items() if len(owners) > 1}
        self.doc_observations: list[Observation] = self._document_observations()
        self.all_observations = evidence.observations + self.doc_observations
        self.obs_by_resource = defaultdict(list)
        for o in self.all_observations:
            self.obs_by_resource[o.resource_id].append(o)
        for lst in self.obs_by_resource.values():
            lst.sort(key=lambda o: (o.obs_time, o.captured_at, o.file_id, o.position))
        self.owners = self._all_time_owners()
        self.owner_trail = self._owner_trail() if strict_capture else {}
        self.links: dict[str, dict[str, Link]] = {c.case_id: {} for c in cases}
        self.diagnostics: list[dict] = []
        self.event_log: dict[str, dict] = {}  # activity_id -> candidate trail and outcome
        self.cadence: list[int] = []

    # ------------------------------------------------------------------ documents
    def _document_observations(self) -> list[Observation]:
        out = []
        for doc in self.evidence.docs:
            text = _norm_ocr(doc.text)
            lines = text.splitlines()
            doc_keys = set()
            for line in lines:
                doc_keys |= keys_from_value(line)
            owners = self.identity.owners_of(doc_keys - self.ambiguous_keys)
            single_subject_keys = doc_keys if len(owners) == 1 else set()
            found: dict[str, set] = {}
            rows: dict[str, list] = defaultdict(list)

            def row_keys(line: str) -> set:
                # A row ties a record to a subject only through a direct identifier on that row
                # (email / phone / email hash); a bare user reference is accepted alongside one.
                keys = keys_from_value(line)
                return keys if any(k.split(":")[0] in ("email", "phone", "hash") for k in keys) else set()

            for line in lines:
                for m in OCR_RESOURCE_RE.finditer(line):
                    rid = f"{m.group(1)}_{m.group(2).replace('o', '0').replace('O', '0')}"
                    found.setdefault(rid, set()).update(row_keys(line))
                    rows[rid].append(line.lower())
            for link in self.activity.links:
                # catalog rows quote how the record is displayed in the evidence captured just before
                ref = link.display_ref.strip().lower()
                if not ref or abs((link.time - doc.captured_at).total_seconds()) > 86400:
                    continue
                pattern = re.compile(r"(?<![a-z0-9])" + re.escape(ref) + r"(?![0-9])")
                hits = [line for line in lines if pattern.search(line.lower()) or pattern.search(line.lower().replace(" ", ""))]
                if hits:
                    ctx = set()
                    for line in hits:
                        ctx |= row_keys(line)
                        rows[link.resource_id].append(line.lower())
                    found.setdefault(link.resource_id, set()).update(ctx)
            has_pii = bool(EMAIL_RE.search(text)) or any(k.startswith("phone:") for k in doc_keys)
            lower = text.lower()
            header = " ".join([l for l in lines if l.strip()][:4])
            for rid, ctx in found.items():
                system = system_for_resource(rid)
                if not system:
                    continue
                # A document only speaks for its own subject about the record it is *about*
                # (named in its header); other ids are tied to whoever shares their line.
                about_self = rid in header.replace("·", " ")
                keys = ctx | (single_subject_keys if about_self else set())
                row_text = " ".join(rows.get(rid, []))
                flags = {}
                if system == "integration_service":
                    # positive evidence only: a streaming/active indicator on the connector's own row
                    if any(w in row_text for w in STOPPED_WORDS):
                        flags["enabled"] = False
                    elif any(w in row_text for w in STREAMING_WORDS):
                        flags["enabled"] = True
                if system == "backup_service":
                    # the archive must be listed in a console that offers restores, with no block on its row
                    if any(w in row_text for w in RESTORE_BLOCK_WORDS):
                        flags["restore_allowed"] = False
                    elif "restore" in lower and "archive" in lower:
                        flags["restore_allowed"] = True
                m = re.search(r"retain until\s+(\d{1,2} \w{3} \d{4})", text, re.IGNORECASE)
                if m and system == "security_service":
                    flags["retention_until"] = datetime.strptime(m.group(1), "%d %b %Y").strftime("%Y-%m-%dT23:59:59Z")
                rtype = None
                path = self.evidence.files.get(doc.file_id, {}).get("file_path", "")
                if system == "support_platform":
                    rtype = "SUPPORT_ATTACHMENT" if "attachment" in path else "SUPPORT_MESSAGE"
                pii = {"document_identifiers": "present"} if has_pii and system not in (
                    "security_service", "backup_service", "integration_service") else {}
                record_ids = set()
                for line in rows.get(rid, []):
                    record_ids |= {k for k in keys_from_value(line) if k.split(":")[0] in ("email", "phone")}
                flags["row_identifiers"] = sorted(record_ids)
                out.append(Observation(
                    file_id=doc.file_id, captured_at=doc.captured_at, obs_time=doc.captured_at, system=system,
                    resource_id=rid, present=True, store="document", pii=pii,
                    link_keys=keys, record_type=rtype, flags=flags, kind="document",
                    collection=f"{doc.file_id}:document"))
        return out

    # ------------------------------------------------------------------ identity
    def _strong(self, keys: set) -> set:
        return {k for k in keys if not k.startswith("name:") and k not in self.ambiguous_keys}

    def _all_time_owners(self) -> dict[str, set]:
        owners = defaultdict(set)
        for o in self.all_observations:
            owners[o.resource_id] |= self.identity.owners_of(self._strong(o.link_keys))
        for link in self.activity.links:
            owners[link.resource_id] |= self.identity.owners_of(self._strong(link.keys))
        return owners

    def _owner_trail(self) -> dict[str, list]:
        trail = defaultdict(list)  # rid -> [(time available, owners)]
        for o in self.all_observations:
            owners = self.identity.owners_of(self._strong(o.link_keys))
            if owners:
                trail[o.resource_id].append((max(o.captured_at, o.obs_time), owners))
        for link in self.activity.links:
            owners = self.identity.owners_of(self._strong(link.keys))
            if owners:
                trail[link.resource_id].append((link.time, owners))
        return trail

    def owners_known(self, rid: str, at: datetime) -> set:
        """Subjects a record is tied to by identifiers available at `at` (all time if not strict)."""
        if not self.strict_capture:
            return self.owners.get(rid, set())
        return set().union(*[o for t, o in self.owner_trail.get(rid, []) if t <= at])

    def _available(self, o: Observation, case: CaseContext) -> bool:
        return not self.strict_capture or o.captured_at <= case.audit_at

    def _add(self, case: CaseContext, rid: str, basis: str, detail: str, confidence: str = "direct",
             method: str = "", candidates: list | None = None, conflicts: list | None = None):
        system = system_for_resource(rid) or ""
        link = self.links[case.case_id].get(rid)
        if link is None:
            self.links[case.case_id][rid] = Link(rid, system, basis, [detail], confidence, method or basis,
                                                 list(candidates or []), list(conflicts or []))
            return
        if detail not in link.detail:
            link.detail.append(detail)
        if CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[link.confidence]:
            link.confidence, link.basis, link.method = confidence, basis, method or basis
            link.candidates = list(candidates or [])
        for c in conflicts or []:
            if c not in link.conflicts:
                link.conflicts.append(c)

    def _catalog_conflict(self, link) -> tuple[bool, list]:
        """Check a catalog row against the identifiers the record itself displays.

        Returns (reject, notes). The row is rejected only when the record's own identifiers belong
        to a *different known customer* (a false join). Identifiers that resolve to nobody are
        recorded as an unresolved conflict but do not override the catalog."""
        catalog_owners = self.identity.owners_of(self._strong(link.keys))
        notes, reject = [], False
        for o in self.obs_by_resource.get(link.resource_id, []):
            if abs((o.captured_at - link.time).total_seconds()) > 86400:
                continue
            shown = set(o.flags.get("row_identifiers", [])) or {k for k in o.link_keys if k.split(":")[0] in ("email", "phone")}
            if not shown or shown & link.keys or shown & {k for u in catalog_owners for k in self.identity.subjects[u].keys}:
                continue
            others = self.identity.owners_of(self._strong(shown)) - catalog_owners
            if others:
                reject = True
                notes.append(f"{o.file_id} shows identifiers of {sorted(others)}; catalog row {link.activity_id} rejected")
            else:
                notes.append(f"{o.file_id} shows identifiers {sorted(shown)} that match no known customer; "
                             f"catalog row {link.activity_id} kept (unresolved conflict)")
        return reject, notes

    def link_direct(self):
        for case in self.cases:
            if not case.authorized or case.subject is None:
                continue
            keys = self._strong(case.keys)
            lo, hi = case.requested_at, case.audit_at
            for o in self.all_observations:
                if lo <= o.obs_time <= hi and o.link_keys & keys and self._available(o, case):
                    basis = "document" if o.kind == "document" else "evidence"
                    self._add(case, o.resource_id, basis, o.file_id, "direct", f"{basis}:identifier")
            for link in self.activity.links:
                if lo <= link.time <= hi and link.keys & keys:
                    reject, notes = self._catalog_conflict(link)
                    if reject:
                        self.diagnostics.append({"case_id": case.case_id, "resource_id": link.resource_id,
                                                 "basis": "catalog_rejected", "notes": notes})
                        continue
                    self._add(case, link.resource_id, "catalog", link.activity_id, "direct", "catalog",
                              conflicts=notes)

    # ------------------------------------------------------------------ workflow
    def _audit_evidence(self, case: CaseContext, rid: str) -> bool:
        """The record appears in evidence captured for this request's audit (within 30h before audit_at)."""
        return any(case.audit_at - 30 * HOUR <= o.captured_at <= case.audit_at and o.obs_time <= case.audit_at
                   for o in self.obs_by_resource.get(rid, []) if o.kind != "document")

    def _success_times(self, case: CaseContext) -> list:
        times = []
        for link in self.links[case.case_id].values():
            for detail in link.detail:
                act = self.activity.by_id.get(detail)
                if act is not None and act.source == "deletion_worker" and wf_kind(act) == "success":
                    times.append(act.time)
        return times

    def _learn_cadence(self) -> list[int]:
        """Spacing (seconds) between consecutive successful policy actions of the same request's run,
        learned only from requests whose actions are directly linked. Values that recur are kept."""
        diffs = Counter()
        for case in self.cases:
            direct = [l for l in self.links[case.case_id].values() if l.confidence == "direct"]
            times = sorted({self.activity.by_id[d].time for l in direct for d in l.detail
                            if d in self.activity.by_id and wf_kind(self.activity.by_id[d]) == "success"})
            for a, b in zip(times, times[1:]):
                gap = int((b - a).total_seconds())
                if 0 < gap < 3600:
                    diffs[gap] += 1
        total = sum(diffs.values())
        return sorted(g for g, n in diffs.items() if n >= 10 and n >= 0.1 * total)

    def assign_workflow(self):
        """Attach deletion-worker events to the request whose reporting slot they belong to.

        Constraints: event time inside the request's slot window for its event kind; a primary
        account submission's result must equal the reported workflow status (a linkage clue only);
        one record per system per request; records already tied to another subject are never
        reassigned. Every decision is recorded in self.event_log.
        """
        active = [c for c in self.cases if c.authorized and c.subject is not None]
        direct_systems = {c.case_id: {system_for_resource(r) for r in self.links[c.case_id]} for c in active}

        def systems_of(case):
            return {system_for_resource(r) for r in self.links[case.case_id]}

        pending = []
        for a in self.activity.workflow:
            kind = wf_kind(a)
            lo, hi = WINDOWS[kind]
            slot = [c for c in active if c.reported_at + lo * HOUR <= a.time <= c.reported_at + hi * HOUR]
            cands = [c for c in slot if kind != "account" or STATUS_RESULT.get(c.reported_status) == a.result]
            log = self.event_log.setdefault(a.activity_id, {
                "resource_id": a.resource_id, "kind": kind, "time": a.time.isoformat(), "result": a.result,
                "slot_candidates": [c.case_id for c in slot], "result_candidates": [c.case_id for c in cands],
                "filters": [], "assigned_to": None, "confidence": None, "method": None})
            known = {c.case_id: self.owners_known(a.resource_id, c.audit_at) for c in cands}
            if any(known.values()) if self.strict_capture else self.owners.get(a.resource_id):
                owned = [c for c in cands if c.subject.user_id in known[c.case_id]]
                blind = [c for c in cands if not known[c.case_id]]  # strict mode only
                if owned or not blind:
                    for c in owned:
                        self._add(c, a.resource_id, "workflow", a.activity_id, "direct", "identifier+slot")
                    log.update(assigned_to=[c.case_id for c in owned] or None, confidence="direct",
                               method="owned_by_identifier" if owned else "owned_by_other_subject")
                    continue  # belongs to an identified subject: never give it to someone else
                cands = blind
            if any(a.resource_id in self.links[c.case_id] for c in cands):
                continue
            pending.append((a, kind, cands))

        assigned_event: dict[str, str] = {}

        def record(a, case, confidence, method, cands):
            self._add(case, a.resource_id, "workflow" if confidence != "unresolved" else "tie_guess",
                      a.activity_id, confidence, method, [c.case_id for c in cands])
            assigned_event[a.activity_id] = case.case_id
            self.event_log[a.activity_id].update(assigned_to=case.case_id, confidence=confidence, method=method)

        # Phase A: unique after excluding only requests that hold a *directly* linked record of
        # this system. Corroborated by the request's audit evidence -> strong; otherwise weak.
        for a, kind, cands in pending:
            live = [c for c in cands if a.system not in direct_systems[c.case_id]]
            if len(live) == 1 and a.system not in systems_of(live[0]):
                strong = self._audit_evidence(live[0], a.resource_id)
                method = "slot_unique" if len(cands) == 1 else "slot_exclusion_by_direct_links"
                record(a, live[0], "strong" if strong else "weak", method + ("+audit_evidence" if strong else ""),
                       cands)
        # Phase B: exclusivity propagation through other inferred links (weak).
        changed = True
        while changed:
            changed = False
            for a, kind, cands in pending:
                if a.activity_id in assigned_event:
                    continue
                live = [c for c in cands if a.system not in systems_of(c)]
                if len(live) == 1:
                    record(a, live[0], "weak", "slot_exclusion_propagated", cands)
                    changed = True

        remaining = [(a, kind, [c for c in cands if a.system not in systems_of(c)])
                     for a, kind, cands in pending if a.activity_id not in assigned_event]
        remaining = [(a, k, cs) for a, k, cs in remaining if cs]
        self.cadence = self._learn_cadence()
        self._resolve_ties(remaining, assigned_event, systems_of, record)

    def _bundle_orders(self) -> list[list[tuple[str, int | None]]]:
        """Record sequences from snapshot bundles that are demonstrably listed in subject order."""
        cols = defaultdict(list)
        for o in self.evidence.observations:
            if o.kind.startswith("bundle:") and "operational_record_history" not in o.kind:
                cols[o.collection].append(o)
        orders = []
        for col in cols.values():
            col.sort(key=lambda o: o.position)
            seq = []
            for o in col:
                owners = self.identity.owners_of(self._strong(o.link_keys))
                seq.append((o.resource_id, min(int(u.split("_")[1]) for u in owners) if owners else None))
            known = [n for _, n in seq if n is not None]
            if len(known) >= 2 and all(x < y for x, y in zip(known, known[1:])):
                orders.append(seq)
        return orders

    def _had_failed_followup(self, case) -> bool:
        for link in self.links[case.case_id].values():
            for detail in link.detail:
                act = self.activity.by_id.get(detail)
                if act is not None and act.source == "deletion_worker" and wf_kind(act) == "followup":
                    return True
        return False

    def _resolve_ties(self, remaining, assigned_event, systems_of, record):
        """Weak tie-breakers, applied in a fixed order and logged per event; then guarded guessing."""

        def unum(c):
            return int(c.subject.user_id.split("_")[1])

        def narrow(aid, name, cs, new):
            new = new or cs
            if len(new) < len(cs):
                self.event_log[aid]["filters"].append({name: [c.case_id for c in new]})
            return new

        cand = {}
        for a, kind, cs in remaining:
            aid = a.activity_id
            seen = self.obs_by_resource.get(a.resource_id, [])
            cs = narrow(aid, "observed_within_request_window", cs,
                        [c for c in cs if any(c.requested_at <= o.obs_time <= c.audit_at and self._available(o, c)
                                              for o in seen)])
            cs = narrow(aid, "in_audit_capture", cs,
                        [c for c in cs if any(c.audit_at - 30 * HOUR <= o.captured_at <= c.audit_at for o in seen)])
            if kind == "success" and self.cadence:
                # Run cadence: consecutive policy actions of one deletion run are spaced by a learned
                # interval; an action exactly one interval away from a request's own action chains to it.
                chained = [c for c in cs if any(abs(abs((a.time - t).total_seconds()) - g) <= 1
                                                for t in self._success_times(c) for g in self.cadence)]
                cs = narrow(aid, "run_cadence", cs, chained)
            if kind == "success":
                cs = narrow(aid, "clean_run", cs, [c for c in cs if not self._had_failed_followup(c)])
            cand[aid] = (a, kind, cs)
        rid_event = {a.resource_id: a.activity_id for a, _, _ in remaining}
        orders = self._bundle_orders() if self.use_bundle_order else []

        def live(aid):
            a, kind, cs = cand[aid]
            return [c for c in cs if a.system not in systems_of(c)]

        def method_of(aid, extra=""):
            names = [next(iter(f)) for f in self.event_log[aid]["filters"]]
            return "+".join(names + ([extra] if extra else [])) or "slot_exclusion_propagated"

        progress = True
        while progress:
            progress = False
            narrowed = {aid: live(aid) for aid in cand if aid not in assigned_event}
            ordered = set()
            for seq in orders:
                for i, (rid, num) in enumerate(seq):
                    aid = rid_event.get(rid)
                    if num is not None or aid not in narrowed:
                        continue
                    lo = max([n for _, n in seq[:i] if n is not None], default=0)
                    hi = min([n for _, n in seq[i + 1:] if n is not None], default=10 ** 9)
                    new = [c for c in narrowed[aid] if lo < unum(c) < hi]
                    if new and len(new) < len(narrowed[aid]):
                        narrowed[aid] = new
                        ordered.add(aid)
                unknown = [rid_event[r] for r, n in seq if n is None and rid_event.get(r) in narrowed]
                for x, y in zip(unknown, unknown[1:]):
                    if narrowed[x] and narrowed[y]:
                        ymax = max(unum(c) for c in narrowed[y])
                        xmin = min(unum(c) for c in narrowed[x])
                        nx = [c for c in narrowed[x] if unum(c) < ymax] or narrowed[x]
                        ny = [c for c in narrowed[y] if unum(c) > xmin] or narrowed[y]
                        if len(nx) < len(narrowed[x]):
                            ordered.add(x)
                        if len(ny) < len(narrowed[y]):
                            ordered.add(y)
                        narrowed[x], narrowed[y] = nx, ny
            for aid, cs in narrowed.items():
                if len(cs) == 1:
                    a = cand[aid][0]
                    record(a, cs[0], "weak", method_of(aid, "bundle_order" if aid in ordered else ""), cand[aid][2])
                    progress = True
                    break

        self._coverage_consistency(cand, assigned_event, live, record, method_of)

        # Unresolved: every deletion request has exactly one primary account, so an account tie is a
        # true one-of-k choice; other record types are paired only when the tie is a perfect matching
        # (as many indistinguishable events as candidate requests).
        groups = defaultdict(list)
        for aid, (a, kind, cs) in cand.items():
            if aid in assigned_event:
                continue
            ls = live(aid)
            if ls:
                groups[(kind, a.system, tuple(sorted(c.case_id for c in ls)))].append(a)
        for (kind, _system, case_ids), events in sorted(groups.items()):
            if kind != "account" and len(events) != len(case_ids):
                for a in events:
                    self.event_log[a.activity_id].update(method="unassigned_ambiguous",
                                                         unresolved_candidates=list(case_ids))
                continue
            events.sort(key=lambda a: a.time)
            cases = sorted((self.by_case[c] for c in case_ids), key=unum)
            for a in events:
                free = [c for c in cases if a.system not in systems_of(c)]
                if not free:
                    break
                record(a, free[0], "unresolved", "tie_guess", [self.by_case[c] for c in case_ids])
                self.diagnostics.append({"case_id": free[0].case_id, "resource_id": a.resource_id,
                                         "basis": "tie_guess", "candidates": list(case_ids)})

    def _coverage_consistency(self, cand, assigned_event, live, record, method_of):
        """Evidence-coverage consistency for primary-account ties (weak).

        Within a tie, some candidate accounts carry an explicit check (e.g. a 404 lookup) in the
        candidates' audit capture and others appear in no evidence at all. A request whose run left
        failed or pending follow-up actions is the one whose account was not re-checked; a request
        with a clean run is the one whose account absence was checked. Applied only when this
        partition yields exactly one consistent pairing."""
        groups = defaultdict(list)
        for aid, (a, kind, cs) in cand.items():
            if aid not in assigned_event and kind == "account":
                ls = live(aid)
                if len(ls) > 1:
                    groups[tuple(sorted(c.case_id for c in ls))].append(a)
        for case_ids, events in groups.items():
            cases = [self.by_case[c] for c in case_ids]
            checked = [a for a in events if any(self._audit_evidence(c, a.resource_id) for c in cases)]
            unchecked = [a for a in events if a not in checked]
            clean = [c for c in cases if not self._had_failed_followup(c)]
            dirty = [c for c in cases if c not in clean]
            pairs = []
            if len(checked) == 1 and len(clean) == 1:
                pairs.append((checked[0], clean[0]))
            if len(unchecked) == 1 and len(dirty) == 1 and len(events) == len(cases):
                pairs.append((unchecked[0], dirty[0]))
            for a, c in pairs:
                if a.activity_id in assigned_event or a.system in {system_for_resource(r) for r in self.links[c.case_id]}:
                    continue
                self.event_log[a.activity_id]["filters"].append({"evidence_coverage": [c.case_id]})
                record(a, c, "weak", method_of(a.activity_id), cases)

    def tie_candidates(self) -> dict[str, set]:
        """case_id -> {(resource_id, system)} of every record in an unbroken tie the request is part of."""
        out = defaultdict(set)
        for links in self.links.values():
            for rid, link in links.items():
                if link.confidence == "unresolved":
                    for c in link.candidates:
                        out[c].add((rid, link.system))
        for e in self.event_log.values():
            if e.get("method") == "unassigned_ambiguous":
                for c in e.get("unresolved_candidates", []):
                    out[c].add((e["resource_id"], system_for_resource(e["resource_id"]) or ""))
        return out

    def run(self) -> dict[str, dict[str, Link]]:
        self.link_direct()
        self.assign_workflow()
        return self.links
