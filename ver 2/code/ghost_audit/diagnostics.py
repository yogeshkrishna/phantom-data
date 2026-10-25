"""Per-case diagnostics and a run summary (kept outside output.csv)."""

from __future__ import annotations

from collections import Counter

LEVELS = ("direct", "strong", "weak", "unresolved")


def case_diagnostics(run) -> dict:
    out = {}
    for r in run.results:
        heur = sum(1 for c in r.step_confidence if c in ("weak", "unresolved"))
        out[r.case_id] = {
            "status": r.status,
            "subject": run.subjects.get(r.case_id),
            "authorization": run.auth.get(r.case_id),
            "status_relies_on": r.status_relies_on,
            "steps": len(r.steps),
            "steps_by_link_confidence": dict(Counter(r.step_confidence)),
            "steps_relying_on_heuristics": heur,
            "resources": r.resources,
            "notes": r.notes,
        }
    return out


def summary(run) -> dict:
    links = Counter()
    methods = Counter()
    steps = Counter()
    status_rel = Counter()
    cases_with_heur_steps = 0
    late_only = omission = override = 0
    conflicts = []
    guard = Counter()  # completion guard: why completion could not be established
    ties_all_compliant = 0
    for r in run.results:
        for res in r.resources:
            links[res["link"]["confidence"]] += 1
            methods[(res["link"]["confidence"], res["link"]["method"])] += 1
            basis = res["state_at_audit"]["basis"]
            omission += basis == "omission"
            override += basis == "omission_override"
            late_only += bool(res["state_at_audit"]["rests_only_on_files_captured_after_audit"])
            if res["link"]["conflicts"]:
                conflicts.append({"case_id": r.case_id, "resource_id": res["resource_id"],
                                  "conflicts": res["link"]["conflicts"]})
        for n in r.notes:
            if n.startswith("completion not established: "):
                guard[n.split(": ", 2)[2].split(" (")[0]] += 1
        ties_all_compliant += any(n.startswith("unattributed tie: all") for n in r.notes)
        steps.update(r.step_confidence)
        if r.status != "NOT_APPLICABLE":
            status_rel[(r.status, r.status_relies_on)] += 1
        cases_with_heur_steps += any(c in ("weak", "unresolved") for c in r.step_confidence)
    events = Counter()
    ambiguous = []
    for aid, e in run.event_log.items():
        method = e.get("method") or ("no_candidate_request" if not e["result_candidates"] else "unassigned")
        events[(e.get("confidence") or "-", method)] += 1
        if method == "unassigned_ambiguous":
            ambiguous.append({"activity_id": aid, "resource_id": e["resource_id"],
                              "candidates": e.get("unresolved_candidates", [])})
    total_steps = sum(steps.values())
    cov = run.coverage
    return {
        "link_policy": run.link_policy,
        "cases": len(run.results),
        "status_counts": dict(Counter(r.status for r in run.results)),
        "linked_resources_by_confidence": {k: links.get(k, 0) for k in LEVELS},
        "linked_resources_by_method": {f"{c}:{m}": n for (c, m), n in sorted(methods.items())},
        "output_steps_by_link_confidence": {k: steps.get(k, 0) for k in LEVELS},
        "output_steps_total": total_steps,
        "share_of_steps_on_heuristics": round((steps["weak"] + steps["unresolved"]) / total_steps, 4) if total_steps else 0,
        "cases_with_any_heuristic_step": cases_with_heur_steps,
        "status_decision_relies_on": {f"{s}:{c}": n for (s, c), n in sorted(status_rel.items())},
        "workflow_events": {f"{c}:{m}": n for (c, m), n in sorted(events.items())},
        "unassigned_ambiguous_events": len(ambiguous),
        "unassigned_ambiguous_examples": ambiguous[:10],
        "learned_run_cadence_seconds": run.cadence,
        "states_by_omission": omission,
        "states_by_omission_override": override,
        "states_resting_only_on_post_cutoff_files": late_only,
        "completion_guard_blocks": dict(guard),
        "cases_blocked_by_completion_guard": sum(
            any(n.startswith("completion not established") for n in r.notes) for r in run.results),
        "cases_with_unattributed_tie_all_candidates_compliant": ties_all_compliant,
        "catalog_conflicts": conflicts,
        "rejected_catalog_links": [d for d in run.linker_diagnostics if d.get("basis") == "catalog_rejected"],
        "evidence_coverage": {"files": cov["files"], "by_type": cov["by_type"], "parsed_ok": cov["ok_by_type"],
                              "problems": cov["problems"], "observations": cov["observations"],
                              "scopes": cov["scopes"], "documents": cov["documents"]},
    }


def summary_markdown(s: dict) -> str:
    lines = ["# Run diagnostics summary", "",
             f"- Link policy: `{s['link_policy']}`; cases: {s['cases']}; statuses: {s['status_counts']}",
             f"- Output steps: {s['output_steps_total']}; share resting on weak/unresolved links: "
             f"{s['share_of_steps_on_heuristics']:.1%}; cases with any such step: {s['cases_with_any_heuristic_step']}",
             "", "| Link confidence | Linked resources | Output steps |", "|---|---|---|"]
    for k in LEVELS:
        lines.append(f"| {k} | {s['linked_resources_by_confidence'][k]} | {s['output_steps_by_link_confidence'][k]} |")
    lines += ["", "Status decision relies on (status:weakest link needed):", ""]
    lines += [f"- {k}: {v}" for k, v in s["status_decision_relies_on"].items()]
    lines += ["", "Linking methods:", ""] + [f"- {k}: {v}" for k, v in s["linked_resources_by_method"].items()]
    lines += ["", "Deletion-worker events (confidence:outcome):", ""]
    lines += [f"- {k}: {v}" for k, v in s["workflow_events"].items()]
    lines += ["", f"- Learned run cadence (s): {s['learned_run_cadence_seconds']}",
              f"- States by omission: {s['states_by_omission']}; omission overrides: {s['states_by_omission_override']}",
              f"- States resting only on files captured after audit_at: {s['states_resting_only_on_post_cutoff_files']}",
              f"- Completion guard: {s['cases_blocked_by_completion_guard']} cases not claimed COMPLETE "
              f"({s['completion_guard_blocks'] or 'no blocks'}); cases whose unattributed tie had every candidate "
              f"proven compliant: {s['cases_with_unattributed_tie_all_candidates_compliant']}",
              f"- Catalog conflicts: {len(s['catalog_conflicts'])}; rejected catalog links: {len(s['rejected_catalog_links'])}",
              f"- Evidence: {s['evidence_coverage']['files']} files, problems: {len(s['evidence_coverage']['problems'])}, "
              f"observations: {s['evidence_coverage']['observations']}"]
    return "\n".join(lines) + "\n"
