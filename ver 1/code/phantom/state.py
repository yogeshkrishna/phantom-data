"""Subject linking and conservative per-store reconstruction, without policy."""

import copy
import re
from collections import defaultdict
from dataclasses import dataclass

from .contracts import EntityKey, EvidenceFact, SnapshotScope, parse_instant
from .identities import FIELD_ALIASES, IdentityResolution, normalize_identifier

STABLE_FIELDS = {"user_id", "customer_reference", "subject_reference", "customer_id"}
SELECTOR_FIELDS = {"query", "lookup_value", "subject_selector", "uploaded_by"}
PHONE_TEXT = re.compile(r"(?<![\w+])\+?\d[\d ().-]{5,}\d(?!\w)")


def text_tokens(value):
    """Index literal identifiers in prose; names never seed subject retrieval.

    Short untyped fragments are intentionally excluded. Phone candidates must
    be whole runs with at least seven digits, not substrings of another number.
    Candidate retrieval is followed by the same temporal/ownership checks.
    """
    for token in set(re.findall(r"\w+", value) + re.findall(r"[\w@.+-]+", value)):
        if len(token) >= 4:
            yield "literal", token.casefold()
    for match in PHONE_TEXT.finditer(value):
        phone = normalize_identifier("phone", match[0])
        if sum(c.isdigit() for c in phone) >= 7:
            yield "phone", phone


def text_matches(value, aliases):
    tokens = set(text_tokens(value))
    for field, alias in aliases:
        if field == "phone" and (field, alias) in tokens:
            return True
        if field in {"user_id", "email_hash"} and len(alias) >= 4:
            flags = re.I if field == "email_hash" else 0
            if re.search(r"(?<![\w@.+-])" + re.escape(alias) + r"(?![\w@.+-])", value, flags):
                return True
    return False


def _reference_field(key, value):
    if key in {"user_id", "customer_id"}:
        return "user_id"
    if "@" in value:
        return "email"
    if re.fullmatch(r"[a-fA-F0-9]{64}", value):
        return "email_hash"
    if re.fullmatch(r"\+?[\d ()-]{7,}", value):
        return "phone"
    return "user_id"


def leaves(value, path=()):
    """Yield field paths and scalar values; never execute embedded content."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, path + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, path + (str(index),))
    else:
        yield path, value


def identifier_tokens(fields):
    for path, value in leaves(fields):
        if not isinstance(value, str) or not value.strip():
            continue
        key = path[-1] if path else ""
        field = _reference_field(key, value) if key in STABLE_FIELDS | SELECTOR_FIELDS else FIELD_ALIASES.get(key)
        if field and field != "name":
            yield field, normalize_identifier(field, value)


def _key(entity):
    return entity.system_id, entity.store_id, entity.resource_id


@dataclass(frozen=True, slots=True)
class FieldObservation:
    field: str
    value: object
    fact: EvidenceFact


@dataclass(frozen=True, slots=True)
class ResourceState:
    entity: EntityKey
    category: str
    state: str
    latest: tuple[EvidenceFact, ...]
    fields: tuple[FieldObservation, ...]
    history: tuple[EvidenceFact, ...]
    claims: tuple[EvidenceFact, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateView:
    audit_at: str
    identity: IdentityResolution
    resources: tuple[ResourceState, ...]
    issues: tuple[str, ...]


class StateIndex:
    def __init__(self, facts=(), scopes=()):
        self.facts = tuple(copy.deepcopy(facts))
        self.scopes = tuple(copy.deepcopy(scopes))
        self._by_entity = defaultdict(list)
        self._tokens = defaultdict(set)
        self._scopes = defaultdict(list)
        self._reference_edges = defaultdict(list)
        self._logical_entities = defaultdict(set)
        self._logical_claims = defaultdict(list)
        for fact in self.facts:
            parse_instant(fact.observed_at)
            parse_instant(fact.source.available_at)
            self._by_entity[fact.entity].append(fact)
            logical = fact.entity.system_id, fact.entity.resource_id
            self._logical_entities[logical].add(fact.entity)
            if fact.kind == "claim":
                self._logical_claims[logical].append(fact)
            for reference in fact.references:
                self._reference_edges[fact.entity].append((reference, fact))
                self._reference_edges[reference].append((fact.entity, fact))
            for token in identifier_tokens(fact.stored_fields):
                self._tokens[token].add(fact.entity)
            for field, values in fact.query_identifiers.items():
                if field != "name":
                    for value in values:
                        canonical = _reference_field(field, value) if field in STABLE_FIELDS | SELECTOR_FIELDS else FIELD_ALIASES.get(field, field)
                        self._tokens[canonical, normalize_identifier(canonical, value)].add(fact.entity)
            # Free-text matching uses a separate exact boundary check at resolve.
            for _, value in leaves(fact.stored_fields):
                if isinstance(value, str):
                    for token in text_tokens(value):
                        self._tokens[token].add(fact.entity)
                    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[\w-]+", value):
                        self._tokens["email", normalize_identifier("email", email)].add(fact.entity)
        for scope in self.scopes:
            parse_instant(scope.observed_at)
            parse_instant(scope.source.available_at)
            self._scopes[scope.system_id, scope.store_id, scope.category].append(scope)

    @staticmethod
    def _visible(item, cutoff):
        return parse_instant(item.observed_at) <= cutoff and parse_instant(item.source.available_at) <= cutoff

    @staticmethod
    def _matches(fact, identity, aliases):
        tokens = set(identifier_tokens(fact.stored_fields))
        owners = {value for field, value in tokens if field == "user_id"}
        if owners and not owners.intersection(identity.user_ids):
            return False  # A reused email cannot override an explicit other owner.
        if tokens & aliases:
            return True
        for field, values in fact.query_identifiers.items():
            for value in values:
                canonical = _reference_field(field, value) if field in STABLE_FIELDS | SELECTOR_FIELDS else FIELD_ALIASES.get(field, field)
                if (canonical, normalize_identifier(canonical, value)) in aliases:
                    return True
        for _, value in leaves(fact.stored_fields):
            if isinstance(value, str):
                if text_matches(value, aliases):
                    return True
                emails = re.findall(r"[\w.+-]+@[\w.-]+\.[\w-]+", value)
                if any(("email", normalize_identifier("email", email)) in aliases for email in emails):
                    return True
        return False

    def resolve(self, identity: IdentityResolution, audit_at: str) -> StateView:
        cutoff = parse_instant(audit_at)
        if identity.status != "RESOLVED":
            return StateView(audit_at, identity, (), ("IDENTITY_NOT_RESOLVED",))
        aliases = {(field, normalize_identifier(field, value))
                   for field, values in identity.identifiers.items() if field != "name" for value in values}
        candidates = set()
        for token in aliases:
            candidates.update(self._tokens.get(token, ()))
            if token[0] in {"user_id", "email_hash"}:
                candidates.update(self._tokens.get(("literal", token[1].casefold()), ()))
        # Query selectors have no declared field type.
        for field, value in aliases:
            candidates.update(self._tokens.get(("query", value), ()))
        linked = {entity for entity in candidates if any(self._visible(fact, cutoff) and
                  self._matches(fact, identity, aliases) for fact in self._by_entity[entity])}
        # An explicit resource reference preserves historical association. No ID arithmetic.
        pending = list(linked)
        while pending:
            entity = pending.pop()
            # Equal service/resource IDs explicitly name the same logical record;
            # its physical stores remain separate reconstruction obligations.
            for neighbor in self._logical_entities[entity.system_id, entity.resource_id]:
                if neighbor not in linked and any(self._visible(f, cutoff) for f in self._by_entity[neighbor]):
                    linked.add(neighbor)
                    pending.append(neighbor)
            for neighbor, support in self._reference_edges.get(entity, ()):
                if neighbor in self._by_entity and neighbor not in linked and self._visible(support, cutoff) and any(
                        self._visible(fact, cutoff) for fact in self._by_entity[neighbor]):
                    linked.add(neighbor)
                    pending.append(neighbor)
        # A claim with no physical store declaration is not an invented extra copy.
        has_state = {(entity.system_id, entity.resource_id) for entity in linked
                     if any(f.kind == "state" and self._visible(f, cutoff) for f in self._by_entity[entity])}
        linked = {entity for entity in linked if (entity.system_id, entity.resource_id) not in has_state or
                  any(f.kind == "state" and self._visible(f, cutoff) for f in self._by_entity[entity])}
        resources = tuple(self._reconstruct(entity, cutoff, identity, aliases) for entity in sorted(linked, key=_key))
        return StateView(audit_at, identity, resources, tuple(sorted({issue for item in resources for issue in item.issues})))

    def _reconstruct(self, entity, cutoff, identity, aliases):
        history = [fact for fact in self._by_entity[entity] if self._visible(fact, cutoff)]
        history.extend(fact for fact in self._logical_claims[entity.system_id, entity.resource_id]
                       if fact.entity != entity and self._visible(fact, cutoff))
        observations = [fact for fact in history if fact.kind == "state"]
        category_set = {fact.category for fact in history if fact.category != "UNKNOWN"}
        issues = set()
        if len(category_set) != 1:
            issues.add("CATEGORY_CONFLICT" if category_set else "UNKNOWN_CATEGORY")
        category = next(iter(category_set)) if len(category_set) == 1 else "UNKNOWN"
        for scope in self._scopes.get((entity.system_id, entity.store_id, category), ()):
            if not scope.complete or not self._visible(scope, cutoff) or entity.resource_id in scope.resource_ids:
                continue
            collections = {fact.metadata.get("collection") for fact in history}
            if scope.collection not in collections:
                continue
            if scope.query_identifiers:
                # A subject-specific scope cannot clear records of a different subject.
                selector = EvidenceFact(entity, category, scope.observed_at, "context", "", {},
                                        scope.query_identifiers, (), (), {}, scope.source)
                if not self._matches(selector, identity, aliases):
                    continue
            observations.append(EvidenceFact(entity, category, scope.observed_at, "state", "ABSENT",
                                            {}, {}, (), (), {"scope_absence": True}, scope.source))
        if not observations:
            return ResourceState(entity, category, "UNKNOWN", (), (), tuple(history),
                                 tuple(f for f in history if f.kind == "claim"), ("NO_STATE_READBACK",))
        groups = defaultdict(list)
        for fact in observations:
            groups[parse_instant(fact.observed_at)].append(fact)
        fields = {}
        latest = ()
        for time in sorted(groups):
            latest = tuple(groups[time])
            if all(fact.metadata.get("full_record") is True for fact in latest):
                fields.clear()
            states = {fact.state for fact in latest}
            if "ABSENT" in states:
                if len(states) == 1:
                    fields.clear()
                else:
                    issues.add("CONFLICTING_STATE_READBACKS")
            covered = {field for fact in latest if fact.state != "ABSENT" for field in fact.field_coverage}
            for field in covered:
                versions = [FieldObservation(field, copy.deepcopy(fact.stored_fields[field]), fact)
                            for fact in latest if field in fact.field_coverage and field in fact.stored_fields]
                if versions:
                    fields[field] = versions
                else:
                    issues.add("COVERAGE_WITHOUT_VALUE")
            for versions in fields.values():
                if len({repr(item.value) for item in versions}) > 1:
                    issues.add("CONFLICTING_FIELD_READBACKS")
        states = {fact.state for fact in latest}
        # Historical conflicts remain in history, but resolved later observations are usable.
        current_issues = set()
        if len(states) > 1:
            current_issues.add("CONFLICTING_STATE_READBACKS")
        if any(len({repr(item.value) for item in values}) > 1 for values in fields.values()):
            current_issues.add("CONFLICTING_FIELD_READBACKS")
        current_issues.update(issues & {"CATEGORY_CONFLICT", "UNKNOWN_CATEGORY", "COVERAGE_WITHOUT_VALUE"})
        latest_time = max(groups)
        writes = {"UPDATE_RECORD", "SYNC_RECORD", "INDEX_RECORD", "CREATE_EXPORT", "RESTORE_BACKUP", "REFRESH_CACHE"}
        if any(fact.kind == "claim" and fact.metadata.get("event_type") in writes and
               fact.metadata.get("reported_result") == "SUCCESS" and parse_instant(fact.observed_at) >= latest_time
               for fact in history):
            current_issues.add("UNVERIFIED_LATER_WRITE")
        state = next(iter(states)) if len(states) == 1 else "CONFLICT"
        latest_owners = {value for fact in latest for field, value in identifier_tokens(fact.stored_fields)
                         if field == "user_id"}
        if latest_owners and not latest_owners.intersection(identity.user_ids):
            current_issues.add("RESOURCE_OWNERSHIP_CONFLICT")
        return ResourceState(entity, category, state, latest,
                             tuple(item for field in sorted(fields) for item in fields[field]),
                             tuple(sorted(history, key=lambda f: (parse_instant(f.observed_at), f.source.source_path, f.source.locator))),
                             tuple(f for f in history if f.kind == "claim"), tuple(sorted(current_issues)))
