"""Visible, source-supported historical identity edges with explicit ambiguity."""

import copy
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .contracts import EvidenceFact, IdentifierMap, parse_instant

FIELDS = ("name", "email", "phone", "email_hash")
FIELD_ALIASES = {
    "name": "name", "full_name": "name", "display_name": "name", "requester_name": "name",
    "email": "email", "customer_email": "email", "owner_email": "email", "requester_email": "email",
    "phone": "phone", "customer_phone": "phone", "owner_phone": "phone", "requester_phone": "phone",
    "email_hash": "email_hash",
}


@dataclass(frozen=True, slots=True)
class IdentitySupport:
    evidence_id: str
    source_path: str
    locator: str
    available_at: str


@dataclass(frozen=True, slots=True)
class IdentityVersion:
    user_id: str
    field: str
    value: str
    effective_from: str | None
    effective_until: str | None
    source: IdentitySupport
    observation_only: bool = False


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    status: str  # RESOLVED, AMBIGUOUS or UNRESOLVED; internal, never CSV status.
    user_ids: tuple[str, ...]
    identifiers: IdentifierMap
    current: dict[str, str]
    versions: tuple[IdentityVersion, ...]
    supports: tuple[IdentitySupport, ...]
    issues: tuple[str, ...]


def normalize_identifier(field: str, value: str) -> str:
    """Comparison only: retain original aliases, plus-tags and country codes."""
    if not isinstance(value, str):
        raise ValueError("identifier must be a string")
    text = unicodedata.normalize("NFKC", value).strip()
    if field == "phone":
        return re.sub(r"[\s().-]", "", text)
    if field in {"email", "email_hash"}:
        return text.casefold()
    if field == "name":
        return " ".join(text.split()).casefold()
    return text


def _instant(value):
    try:
        return parse_instant(value)
    except (ValueError, TypeError):
        raise ValueError("invalid identity timestamp") from None


class IdentityIndex:
    """Index raw identity sources once, then resolve using only visible support.

    A current row is an observation at its updated_at; fields are not backdated
    before that observation. Visible audit old/new values establish historical
    aliases and transitions, including when the current row is unavailable.
    No absent audit event is invented to fill an inconsistent chain.
    """
    def __init__(self, identities: Sequence[dict[str, str]],
                 audits: Sequence[dict[str, str]], facts: Sequence[EvidenceFact] = ()):
        for rows in (identities, audits, facts):
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
                raise ValueError("identity inputs must be sequences")
        self._current = {}
        self._audits = defaultdict(list)
        self._facts = tuple(copy.deepcopy(facts))
        self._email_edges = defaultdict(set)
        self._users = set()
        seen_audits = set()
        for index, original in enumerate(identities):
            if not isinstance(original, dict):
                raise ValueError("identity row must be a dictionary")
            row = dict(original)
            user = row.get("user_id")
            if not isinstance(user, str) or not user.strip() or user in self._current:
                raise ValueError("invalid or duplicate identity user ID")
            if any(not isinstance(row.get(field), str) for field in FIELDS):
                raise ValueError("invalid identity field")
            time = _instant(row.get("updated_at"))
            source = IdentitySupport("", "user_identities.csv", f"/rows/{index}", row["updated_at"])
            self._current[user] = (row, time, source)
            self._users.add(user)
            self._email_edges[normalize_identifier("email", row["email"])].add(user)
        for index, original in enumerate(audits):
            if not isinstance(original, dict):
                raise ValueError("audit row must be a dictionary")
            row = dict(original)
            if any(not isinstance(row.get(field), str) for field in
                   ("audit_id", "user_id", "field_name", "old_value", "new_value")):
                raise ValueError("invalid audit fields")
            if not row["audit_id"].strip() or row["audit_id"] in seen_audits or not row["user_id"].strip():
                raise ValueError("invalid or duplicate audit ID")
            field = FIELD_ALIASES.get(row["field_name"])
            if field not in FIELDS:
                raise ValueError("unsupported identity audit field")
            seen_audits.add(row["audit_id"])
            row["field_name"] = field
            time = _instant(row.get("event_time"))
            source = IdentitySupport(row["audit_id"], "user_audit_log.csv", f"/rows/{index}", row["event_time"])
            self._audits[row["user_id"]].append((time, row, source))
            self._users.add(row["user_id"])
            if field == "email":
                for key in ("old_value", "new_value"):
                    if row[key]:
                        self._email_edges[normalize_identifier("email", row[key])].add(row["user_id"])
        for user in self._audits:
            self._audits[user].sort(key=lambda item: (item[0], item[1]["audit_id"]))
        self._fact_observations = defaultdict(list)
        for fact in self._facts:
            observed, available = _instant(fact.observed_at), _instant(fact.source.available_at)
            if fact.kind != "state" or fact.state not in {"PRESENT", "REDACTED", "ANONYMIZED"}:
                continue
            source = IdentitySupport(fact.source.evidence_id, fact.source.source_path,
                                     fact.source.locator, fact.source.available_at)
            self._index_subtree(fact.stored_fields, observed, available, fact.observed_at, source)

    def _index_subtree(self, value, observed, available, observed_text, source):
        if isinstance(value, dict):
            users = {value[key] for key in ("user_id", "customer_reference", "subject_reference")
                     if isinstance(value.get(key), str) and value[key] in self._users}
            if len(users) == 1:
                user = next(iter(users))
                fields = [(FIELD_ALIASES[key], item) for key, item in value.items()
                          if key in FIELD_ALIASES and isinstance(item, str)]
                for field, item in fields:
                    self._fact_observations[user].append((observed, available, field, item, observed_text, source))
                    if field == "email" and item:
                        self._email_edges[normalize_identifier("email", item)].add(user)
            for key, item in value.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                child = IdentitySupport(source.evidence_id, source.source_path,
                                        source.locator + "/" + escaped, source.available_at)
                self._index_subtree(item, observed, available, observed_text, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                child = IdentitySupport(source.evidence_id, source.source_path,
                                        source.locator + f"/{index}", source.available_at)
                self._index_subtree(item, observed, available, observed_text, child)

    def _history(self, user, cutoff):
        versions, issues = [], set()
        anchors = defaultdict(list)
        copy_anchors = defaultdict(list)
        current = self._current.get(user)
        if current and current[1] <= cutoff:
            row, time, source = current
            for field in FIELDS:
                versions.append(IdentityVersion(user, field, row[field], row["updated_at"], None, source))
                anchors[field].append((time, row[field], source))
        elif current:
            issues.add("CURRENT_SNAPSHOT_AFTER_CUTOFF")
        by_field = defaultdict(list)
        for time, row, source in self._audits.get(user, ()):
            if time <= cutoff:
                by_field[row["field_name"]].append((time, row, source))
        for field, events in by_field.items():
            for index, (time, row, source) in enumerate(events):
                previous = events[index - 1] if index else None
                following = events[index + 1] if index + 1 < len(events) else None
                contiguous = previous and normalize_identifier(field, previous[1]["new_value"]) == normalize_identifier(field, row["old_value"])
                if previous and not contiguous:
                    issues.add("AUDIT_CHAIN_GAP")
                start = previous[1]["event_time"] if contiguous else None
                until = following[1]["event_time"] if following and normalize_identifier(field, row["new_value"]) == normalize_identifier(field, following[1]["old_value"]) else None
                versions.extend((IdentityVersion(user, field, row["old_value"], start, row["event_time"], source),
                                 IdentityVersion(user, field, row["new_value"], row["event_time"], until, source)))
                anchors[field].append((time, row["new_value"], source))
        for observed, available, field, value, observed_text, source in self._fact_observations.get(user, ()):
            if observed <= cutoff and available <= cutoff:
                # Copies prove an alias at this observation, not a canonical
                # identity transition or a continuous ownership interval.
                versions.append(IdentityVersion(user, field, value, observed_text, None, source, True))
                copy_anchors[field].append((observed, value, source))
        for field, values in copy_anchors.items():
            latest = max(item[0] for item in values)
            if len({normalize_identifier(field, value) for time, value, _ in values if time == latest}) > 1:
                issues.add("CONFLICTING_STORED_IDENTITY_OBSERVATIONS")
        state = {}
        for field, values in anchors.items():
            latest = max(item[0] for item in values)
            candidates = [value for time, value, _ in values if time == latest]
            if len({normalize_identifier(field, value) for value in candidates}) != 1:
                issues.add("CONFLICTING_IDENTITY_OBSERVATIONS")
            else:
                state[field] = sorted(candidates)[0]
            # A current snapshot disagreeing with the last visible audit is not
            # evidence of a nonexistent logged transition.
            events = by_field.get(field, ())
            if current and current[1] <= cutoff and events:
                row, time, _ = current
                if time >= events[-1][0] and normalize_identifier(field, row[field]) != normalize_identifier(field, events[-1][1]["new_value"]):
                    issues.add("CURRENT_AUDIT_GAP")
        bounded = []
        for version in versions:
            until = _instant(version.effective_until) if version.effective_until else None
            if version.effective_from and not version.observation_only:
                start = _instant(version.effective_from)
                changes = [time for time, value, _ in anchors[version.field]
                           if time > start and normalize_identifier(version.field, value) !=
                           normalize_identifier(version.field, version.value)]
                if changes and (until is None or min(changes) < until):
                    version = replace(version, effective_until=min(changes).isoformat())
            bounded.append(version)
        return bounded, state, issues

    def resolve(self, email: str, audit_at: str, *, requested_at: str | None = None) -> IdentityResolution:
        """Resolve a request email without joining ambiguous owners or future data."""
        if not isinstance(email, str) or not email.strip():
            raise ValueError("request email must be nonblank")
        cutoff = _instant(audit_at)
        requested = _instant(requested_at) if requested_at is not None else cutoff
        if requested > cutoff:
            raise ValueError("request occurs after audit cutoff")
        key = normalize_identifier("email", email)
        candidates = {}
        active = set()
        for user in sorted(self._email_edges.get(key, ())):
            versions, state, issues = self._history(user, cutoff)
            matches = [version for version in versions if version.field == "email" and
                       normalize_identifier("email", version.value) == key]
            if not matches:
                continue
            candidates[user] = (versions, state, issues)
            for match in matches:
                if issues & {"AUDIT_CHAIN_GAP", "CURRENT_AUDIT_GAP", "CONFLICTING_IDENTITY_OBSERVATIONS"}:
                    continue
                if match.observation_only:
                    if match.effective_from and _instant(match.effective_from) == requested:
                        active.add(user)
                    continue
                if match.effective_from and _instant(match.effective_from) <= requested and (
                    match.effective_until is None or requested < _instant(match.effective_until)):
                    active.add(user)
        if len(active) == 1 and len(candidates) > 1:
            owner = next(iter(active))
            candidates = {owner: candidates[owner]}
            candidates[owner][2].add("HISTORICAL_EMAIL_REUSE")
        if len(candidates) != 1:
            return IdentityResolution("AMBIGUOUS" if candidates else "UNRESOLVED", tuple(sorted(candidates)),
                                      {"email": (email,)}, {}, (), (),
                                      ("AMBIGUOUS_EMAIL_OWNERSHIP" if candidates else "NO_VISIBLE_IDENTITY_LINK",))
        user, (versions, state, issues) = next(iter(candidates.items()))
        aliases = defaultdict(set)
        aliases["user_id"].add(user)
        for version in versions:
            if version.value:
                aliases[version.field].add(version.value)
        if normalize_identifier("email", state.get("email", "")) != key:
            issues.add("HISTORICAL_EMAIL_MATCH")
        versions = tuple(sorted(set(versions), key=lambda version: (version.field,
                         version.effective_from or "", version.effective_until or "", version.value,
                         version.source.evidence_id, version.source.source_path, version.source.locator,
                         version.observation_only)))
        supports = tuple(sorted({version.source for version in versions}, key=lambda source:
                         (source.source_path, source.locator, source.evidence_id, source.available_at)))
        return IdentityResolution("RESOLVED", (user,),
                                  {field: tuple(sorted(values)) for field, values in sorted(aliases.items())},
                                  state, versions, supports, tuple(sorted(issues)))
