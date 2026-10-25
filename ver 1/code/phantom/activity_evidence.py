"""CSV activity provenance; operation results never become deletion readbacks."""

import re
from collections import defaultdict

from .contracts import EntityKey, EvidenceFact, EvidenceRef, parse_instant
from .json_evidence import EvidenceBatch, EvidenceDiagnostic

OPERATIONS = {"DELETE_RECORD", "UPDATE_RECORD", "SYNC_RECORD", "INDEX_RECORD", "SEND_EMAIL",
              "REFRESH_CACHE", "RETAIN_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "CREATE_EXPORT",
              "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "RESTORE_BACKUP", "CLASSIFY_REQUEST"}


def normalize_activity(rows, source_sha256: str, existing_facts=()) -> EvidenceBatch:
    """Associate exact resource IDs only; no prefix/suffix categorization or time join.

    A category/system comes from already normalized evidence or explicit checker
    semantics. Unknown category is preserved on a claim, not guessed from its ID.
    """
    by_resource = defaultdict(set)
    for fact in existing_facts:
        if fact.category != "UNKNOWN":
            by_resource[fact.entity.resource_id].add((fact.entity.system_id, fact.category,
                                                    fact.observed_at, fact.source.available_at))
    facts, diagnostics = [], []
    for index, row in enumerate(rows):
        parse_instant(row["event_time"])
        source = EvidenceRef(row["activity_id"], "system_activity.csv", source_sha256,
                             f"/rows/{index}", row["event_time"])
        resource, event = row["resource_id"], row["event_type"]
        cutoff = parse_instant(row["event_time"])
        candidates = {(system, category) for system, category, observed, available in by_resource.get(resource, ())
                      if parse_instant(observed) <= cutoff and parse_instant(available) <= cutoff}
        matching = {candidate for candidate in candidates if candidate[0] in {row["source_system"], row["target_system"]}}
        options = matching or candidates
        system, category = next(iter(options)) if len(options) == 1 else (row["target_system"], "UNKNOWN")
        kind, state, fields, queries, refs = "claim", row["reported_result"], {}, {}, ()
        details = row["details"]
        if event == "CHECK_DATA_FLOW":
            system, category = row["target_system"], "DATA_FLOW"
            expected = f"Connector status check confirmed that data flow {resource} remains enabled."
            if details == expected and row["reported_result"] == "SUCCESS":
                kind, state, fields = "state", "ACTIVE", {"enabled": True}
        elif event == "CHECK_RESTORE_ACCESS":
            system, category = row["target_system"], "BACKUP_ARCHIVE"
            expected = f"Restore-access check confirmed that archive {resource} can still be restored."
            if details == expected and row["reported_result"] == "SUCCESS":
                kind, state, fields = "state", "ACTIVE", {"restore_allowed": True}
        elif event == "LINK_RESOURCE_IDENTITY":
            match = re.fullmatch(r"Data catalog linked (\S+) \((\S+)\) to (\S+@\S+) and (\S+)\.", details)
            attachment = re.fullmatch(r"Attachment catalog confirmed (\S+) \(([^)]+)\) belongs only to (\S+@\S+) and (\S+); no other subject is linked\.", details)
            match = match or attachment
            if match and match[1] == resource and row["reported_result"] == "SUCCESS":
                kind, state = "identity", "LINKED"
                fields = {"email": match[3], "user_id": match[4], "catalog_reference": match[2]}
                if attachment:
                    system, category = row["target_system"], "SUPPORT_ATTACHMENT"
            else:
                diagnostics.append(EvidenceDiagnostic("UNPARSED_ACTIVITY_IDENTITY", source, "Identity statement not supported"))
        elif event == "RESTORE_BACKUP":
            system, category = row["source_system"], "BACKUP_ARCHIVE"
            match = re.fullmatch(r"Restore job repopulated downstream resource (\S+)\.", details)
            if match:
                destination = match[1]
                destination_system = row["target_system"]
                refs = (EntityKey(destination_system, destination_system, destination),)
        elif event not in OPERATIONS:
            diagnostics.append(EvidenceDiagnostic("UNKNOWN_ACTIVITY_EVENT", source, "Activity event has no reviewed semantics"))
        metadata = dict(event_type=event, reported_result=row["reported_result"], details=details,
                        source_system=row["source_system"], target_system=row["target_system"])
        if event == "LINK_RESOURCE_IDENTITY" and kind == "identity":
            metadata["resource_aliases"] = (fields["catalog_reference"],)
            if attachment:
                metadata["subject_only"] = True
        facts.append(EvidenceFact(EntityKey(system, system, resource), category, row["event_time"], kind,
                                  state, fields, queries, tuple(sorted(fields)), refs, metadata, source))
    return EvidenceBatch("activity-csv", tuple(facts), (), tuple(diagnostics), len(rows), len(facts))
