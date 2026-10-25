"""Cross-file, exact resource references with timed, non-state association proof."""
from collections import defaultdict
from dataclasses import replace

from .contracts import EntityKey, EvidenceFact, parse_instant
from .record_snapshots import COLLECTION_CATEGORIES
from .state import leaves


def bind_resource_references(facts):
    """Preserve a source_resource_id edge when its target is in another source.

    A unique target service comes from actual target records/operations, never
    an ID prefix. A target category comes from target facts or an unambiguous
    declared service schema. Association contexts are not presence/absence
    readbacks. They carry both sources' timestamps; ambiguous links are ignored.
    """
    facts = tuple(facts)
    by_resource = defaultdict(list)
    schemas = defaultdict(set)
    for (system, _), category in COLLECTION_CATEGORIES.items():
        schemas[system].add(category)
    for fact in facts:
        by_resource[fact.entity.resource_id].append(fact)
    contexts, seen = [], set()
    for source in facts:
        if source.kind != "state":
            continue
        pointers = {value for path, value in leaves(source.stored_fields)
                    if path and path[-1] == "source_resource_id" and isinstance(value, str) and value}
        for resource in pointers:
            targets = [f for f in by_resource.get(resource, ()) if f.entity != source.entity]
            systems = {f.entity.system_id for f in targets}
            if len(systems) != 1:
                continue
            system = next(iter(systems))
            options = [(f, f.category if f.category != "UNKNOWN" else next(iter(schemas[system])))
                       for f in targets if f.category != "UNKNOWN" or len(schemas[system]) == 1]
            if not options:
                continue
            # Earliest admissible witness supplies the service binding. Later
            # unrelated observations must not delay an already supported edge.
            target, category = min(options, key=lambda item: (
                max(parse_instant(item[0].observed_at), parse_instant(item[0].source.available_at)),
                item[0].source.evidence_id, item[0].source.locator))
            observed = max((source.observed_at, target.observed_at), key=parse_instant)
            available = max((source.source.available_at, target.source.available_at), key=parse_instant)
            target_entity = EntityKey(system, system, resource)
            signature = (source.entity, target_entity, source.source, target.source)
            if signature in seen:
                continue
            seen.add(signature)
            reference = replace(source.source, available_at=available)
            metadata = dict(reference_field="source_resource_id", binding_evidence_id=target.source.evidence_id,
                            binding_locator=target.source.locator, association_only=True)
            contexts.append(EvidenceFact(source.entity, source.category, observed, "context", "LINKED", {}, {}, (),
                                         (target_entity,), metadata, reference))
            contexts.append(EvidenceFact(target_entity, category, observed, "context", "LINKED", {}, {}, (), (),
                                         metadata, reference))
    return facts + tuple(contexts)
