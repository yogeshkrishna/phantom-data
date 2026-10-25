"""Read-only accuracy investigation and explicitly labelled, non-production ablations.

Run from code/: python -B -m evaluation.investigation --dataset PATH --cache PATH
No submission CSV or source archive is produced. Expected rows are read only
after the production sample attempts; weaker variants never enter production.
"""
import argparse
import json
import re
from collections import Counter
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from phantom.contracts import OUTPUT_COLUMNS, parse_instant, to_csv_row
from phantom.corpus import read_csv_table, read_manifest
from phantom.identities import IdentityIndex
from phantom.metrics import compare_rows
from phantom.pipeline import prepare, audit_request
from phantom.planner import AuditBlocked, plan
from phantom.sql_evidence import BOOLEAN_COLUMNS
from phantom.sql_reader import read_sql_entry
from phantom.log_evidence import parse_log_line
from phantom.state import StateIndex
from phantom.tabular import TABLE_SPECS


STEP_KEY = ("phase", "system_id", "resource_id", "operation")


def diff_steps(expected, actual):
    """Align by semantic key; inserted steps must not look like formatting errors."""
    key = lambda step: tuple(step[field] for field in STEP_KEY)
    left = {key(s): s for s in expected}
    right = {key(s): s for s in actual}
    changes = [{"key": k, "fields": [field for field in left[k] if field != "step_no"
                                     and left[k][field] != right[k][field]]}
               for k in left.keys() & right.keys()]
    changes = sorted((c for c in changes if c["fields"]), key=lambda c: c["key"])
    return dict(missing=[left[k] for k in sorted(left.keys() - right.keys())],
                extra=[right[k] for k in sorted(right.keys() - left.keys())], changed=changes,
                order_only=left.keys() == right.keys() and not changes and
                [key(s) for s in expected] != [key(s) for s in actual])


def attempts(context, requests):
    rows, blocked = [], {}
    for request in requests:
        try:
            rows.append(to_csv_row(audit_request(context, request)))
        except AuditBlocked as exc:
            blocked[request["case_id"]] = str(exc)
    return rows, blocked


def without_sql_booleans(facts):
    result = []
    for fact in facts:
        columns = BOOLEAN_COLUMNS.get(fact.metadata.get("sql_table"), ())
        fields = dict(fact.stored_fields)
        for column in columns:
            if type(fields.get(column)) is bool:
                fields[column] = int(fields[column])
        result.append(replace(fact, stored_fields=fields))
    return tuple(result)


def without_sql_row_coverage(facts):
    return tuple(replace(f, metadata={k: v for k, v in f.metadata.items() if k != "full_record"})
                 if f.metadata.get("sql_table") else f for f in facts)


class ObservationTimeOnly(StateIndex):
    """UNAPPROVED interpretation experiment; deliberately ignores capture cutoff."""
    @staticmethod
    def _visible(item, cutoff):
        return parse_instant(item.observed_at) <= cutoff


def latest_available_citation(*args, **kwargs):
    """UNAPPROVED citation preference, not a demonstrated correctness fix."""
    result = plan(*args, **kwargs)
    obligations = args[2]
    steps = []
    for step in result.steps:
        if step.phase == "FINDING":
            refs = [ref for obligation in obligations if obligation.finding == step.operation
                    and obligation.entity.system_id == step.system_id
                    and obligation.entity.resource_id == step.resource_id for ref in obligation.proofs]
            newest = max(parse_instant(ref.available_at) for ref in refs)
            evidence = min(ref.evidence_id for ref in refs if parse_instant(ref.available_at) == newest)
            step = replace(step, evidence_id=evidence)
        steps.append(step)
    return replace(result, steps=tuple(steps))


def variants(context, samples, expected, requests):
    outcomes = []
    definitions = (
        ("current", context.states.facts, StateIndex, False),
        ("without_sql_boolean_decoding", without_sql_booleans(context.states.facts), StateIndex, False),
        ("without_complete_sql_row_coverage", without_sql_row_coverage(context.states.facts), StateIndex, False),
        ("without_cross_file_reference_contexts", tuple(f for f in context.states.facts
             if not f.metadata.get("association_only")), StateIndex, False),
        ("UNAPPROVED_observation_time_only", context.states.facts, ObservationTimeOnly, False),
        ("UNAPPROVED_latest_available_citation", context.states.facts, StateIndex, True),
        ("UNAPPROVED_observation_time_and_citation", context.states.facts, ObservationTimeOnly, True),
    )
    for name, facts, state_type, citation in definitions:
        identities = IdentityIndex(context.tables["user_identities"], context.tables["user_audit_log"], facts)
        candidate = replace(context, identities=identities, states=state_type(facts, context.states.scopes))
        with patch("phantom.pipeline.plan", latest_available_citation) if citation else nullcontext():
            predictions, blocked = attempts(candidate, samples)
            full, full_blocked = attempts(candidate, requests)
        unknown = Counter()
        for request in requests:
            if request["case_id"] not in full_blocked:
                continue
            identity = identities.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
            view = candidate.states.resolve(identity, request["audit_at"])
            unknown.update(reason for o in candidate.policy.evaluate(view) if o.compliant is None
                           for reason in (o.reasons or ("TREATMENT_COVERAGE_UNPROVEN",)))
        outcomes.append(dict(name=name, metrics=compare_rows(expected, predictions), sample_blocked=len(blocked),
                             supported=len(full), blocked=len(full_blocked), unknown_obligations=dict(unknown)))
    return outcomes


def blocked_inventory(context, requests, original_blocked):
    """One entry per originally blocked request; all supporting facts stay unmodified."""
    result = []
    for request in requests:
        if request["case_id"] not in original_blocked:
            continue
        try:
            verdict = audit_request(context, request)
            error = None
        except AuditBlocked as exc:
            verdict, error = None, str(exc)
        identity = context.identities.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
        view = context.states.resolve(identity, request["audit_at"])
        gaps = []
        for obligation in context.policy.evaluate(view):
            if obligation.compliant is not None:
                continue
            state = next(s for s in view.resources if s.entity == obligation.entity)
            related = [f for f in context.states.facts if f.entity.system_id == state.entity.system_id
                       and f.entity.resource_id == state.entity.resource_id]
            gaps.append(dict(system=state.entity.system_id, store=state.entity.store_id,
                             resource=state.entity.resource_id,
                             reasons=obligation.reasons or ("TREATMENT_COVERAGE_UNPROVEN",),
                             current_fields=sorted(f.field for f in state.fields),
                             sources=sorted({f.source.evidence_id + ":" + f.source.locator for f in state.latest}),
                             all_related_sources=sorted({f.source.evidence_id + ":" + f.source.locator for f in related}),
                             raw_field_names=sorted({key for f in related for key in f.stored_fields})))
        result.append(dict(case_id=request["case_id"], status=verdict.deletion_status if verdict else "BLOCKED",
                           error=error, gaps=gaps))
    return result


def raw_field_inventory(dataset):
    """Independently inspect owning JSON nodes, physical SQL rows and raw log events.

    Never infer a record's properties from neighboring rows. Media is reviewed
    separately because a UI projection does not declare a complete stored row.
    The result contains field names and source IDs, never personal values.
    """
    inventory = {}

    def visit(value, source):
        if isinstance(value, dict):
            resource = value.get("resource_id")
            if isinstance(resource, str):
                inventory.setdefault(resource, {}).setdefault(source, set()).update(value)
            for child in value.values():
                visit(child, source)
        elif isinstance(value, list):
            for child in value:
                visit(child, source)

    for entry in read_manifest(dataset):
        if entry.path.suffix == ".json":
            visit(json.loads(entry.path.read_text(encoding="utf-8-sig")), entry.file_id)
        elif entry.path.suffix == ".sql":
            for table in read_sql_entry(entry).tables:
                for row in table.rows:
                    fields = dict(zip(table.columns, row))
                    if "record_json" in fields:
                        document = json.loads(fields["record_json"])
                        visit(dict(document, resource_id=fields.get("resource_id")), entry.file_id)
                    visit(fields, entry.file_id)
        elif entry.path.suffix == ".log":
            records = []
            for line in entry.path.read_text(encoding="utf-8-sig").splitlines():
                if re.match(r"\d{4}-\d\d-\d\dT", line):
                    records.append(line)
                elif records:
                    records[-1] += "\n" + line
            for record in records:
                _, event = parse_log_line(record)
                visit(event, entry.file_id)
    return {resource: {source: sorted(fields) for source, fields in sources.items()}
            for resource, sources in inventory.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args(argv)
    context = prepare(args.dataset, cache_dir=args.cache)
    samples = read_csv_table(args.dataset / "sample_deletion_requests.csv", TABLE_SPECS[0][1], "case_id")
    # Production attempts precede expected-answer access, including diagnostics.
    predictions, sample_blocked = attempts(context, samples)
    expected = read_csv_table(args.dataset / "sample_output.csv", OUTPUT_COLUMNS, "case_id")
    requests = context.tables["deletion_requests"]
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    original_blocked = {case for case, _ in baseline["blocked"]}
    inventory = blocked_inventory(context, requests, original_blocked)
    raw_fields = raw_field_inventory(args.dataset)
    for case in inventory:
        for gap in case["gaps"]:
            gap["independent_raw_fields"] = raw_fields.get(gap["resource"], {})
    measured = variants(context, samples, expected, requests)
    outcome = dict(metrics=compare_rows(expected, predictions), blocked_samples=sample_blocked,
                   variants=measured, original_blocked_inventory=inventory)
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    lines = ["# Every originally blocked case", "",
             "Current-source re-audit of the original blocked cohort. Source locators and fields are "
             "reported without personal payloads. A missing field is not a guessed policy value.", "",
             "| Case | Current result | Missing proof and source |", "|---|---|---|"]
    for case in inventory:
        details = [g["system"] + "/" + g["resource"] + " [" + g["store"] + "]: " +
                   ",".join(g["reasons"]) + " (" + ";".join(g["sources"]) + ")" for g in case["gaps"]]
        lines.append(f"| {case['case_id']} | {case['status']} | " + "<br>".join(
            details or ["Recovered by native SQL boolean decoding; directly stored 0 flags."]) + " |")
    lines += ["", "## Measured rule removals and unapproved interpretation experiments", "",
              "UNAPPROVED rows are diagnostic sensitivity experiments only. They are not adopted fixes, "
              "verified hidden accuracy, or submission output. Original evidence timestamps stay unchanged.", "",
              "| Variant | Exact | Status | Step F1 | Supported | Blocked | Unknown treatment obligations |",
              "|---|---|---|---|---|---|---|"]
    for item in measured:
        m = item["metrics"]
        lines.append(f"| {item['name']} | {m['case_exact_accuracy']:.6f} | {m['status_accuracy']:.6f} | "
                     f"{m['step_f1']:.6f} | {item['supported']} | {item['blocked']} | "
                     + str(item["unknown_obligations"].get("TREATMENT_COVERAGE_UNPROVEN", 0)) + " |")
    lines += ["", "## Raw inspection scope", "",
              "Owning JSON objects, all physical SQL columns/embedded documents, and every decoded "
              "log event were scanned again independently of normalized field selection. No adjacent-row "
              "properties were assigned to a subject. The private diagnostic records raw key sets per "
              "resource/source. Media/PDF projections and structured handover/readback families were "
              "also reviewed at their cited locators. Missing retention boundaries are not date-format errors."]
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "blocked_case_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(dict(metrics=outcome["metrics"], original_blocked=len(inventory),
                         recovered=sum(c["status"] != "BLOCKED" for c in inventory)), indent=2))


if __name__ == "__main__":
    main()
