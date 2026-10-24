"""Subject linking and conservative per-store reconstruction, without policy."""

import copy
import re
from collections import defaultdict
from dataclasses import dataclass

from .contracts import EntityKey, EvidenceFact, SnapshotScope, parse_instant
from .identities import FIELD_ALIASES, IdentityResolution, normalize_identifier

STABLE_FIELDS = {"user_id", "customer_reference", "subject_reference", "customer_id"}


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
        field = ("email" if "@" in value else "user_id") if key in STABLE_FIELDS else FIELD_ALIASES.get(key)
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
        for fact in self.facts:
            parse_instant(fact.observed_at)
            parse_instant(fact.source.available_at)
            self._by_entity[fact.entity].append(fact)
            for token in identifier_tokens(fact.stored_fields):
                self._tokens[token].add(fact.entity)
            for field, values in fact.query_identifiers.items():
                field = "user_id" if field in STABLE_FIELDS else FIELD_ALIASES.get(field, field)
                if field != "name":
                    for value in values:
                        self._tokens[field, normalize_identifier(field, value)].add(fact.entity)
            # Free-text matching uses a separate exact boundary check at resolve.
            for _, value in leaves(fact.stored_fields):
                if isinstance(value, str):
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
            canonical = "user_id" if field in STABLE_FIELDS else FIELD_ALIASES.get(field, field)
            for value in values:
                if (canonical, normalize_identifier(canonical, value)) in aliases:
                    return True
                if canonical == "query" and any(value == alias for _, alias in aliases):
                    return True
        for _, value in leaves(fact.stored_fields):
            if isinstance(value, str):
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
        # Query selectors have no declared field type.
        for field, value in aliases:
            candidates.update(self._tokens.get(("query", value), ()))
        linked = {entity for entity in candidates if any(self._visible(fact, cutoff) and
                  self._matches(fact, identity, aliases) for fact in self._by_entity[entity])}
        # An explicit resource reference preserves historical association. No ID arithmetic.
        changed = True
        while changed:
            changed = False
            for entity, facts in self._by_entity.items():
                for fact in facts:
                    if not self._visible(fact, cutoff):
                        continue
                    connected = entity in linked or any(ref in linked for ref in fact.references)
                    if connected:
                        for key in (entity, *fact.references):
                            if key in self._by_entity and key not in linked:
                                linked.add(key)
                                changed = True
        resources = tuple(self._reconstruct(entity, cutoff, identity, aliases) for entity in sorted(linked, key=_key))
        return StateView(audit_at, identity, resources, tuple(sorted({issue for item in resources for issue in item.issues})))

    def _reconstruct(self, entity, cutoff, identity, aliases):
        history = [fact for fact in self._by_entity[entity] if self._visible(fact, cutoff)]
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
        state = next(iter(states)) if len(states) == 1 else "CONFLICT"
        return ResourceState(entity, category, state, latest,
                             tuple(item for field in sorted(fields) for item in fields[field]),
                             tuple(sorted(history, key=lambda f: (parse_instant(f.observed_at), f.source.source_path, f.source.locator))),
                             tuple(f for f in history if f.kind == "claim"), tuple(sorted(current_issues)))
