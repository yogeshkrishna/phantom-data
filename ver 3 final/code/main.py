"""Audit a read-only corpus and publish a validated prediction CSV."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghost_audit.atomic import atomic_write_text
from ghost_audit.candidates import CANDIDATES, DEFAULT_CANDIDATES, candidate_scope
from ghost_audit.config import DEFAULT_DATA_DIR, DEFAULT_OUTPUT
from ghost_audit.diagnostics import case_diagnostics, summary, summary_markdown
from ghost_audit.output import write_output
from ghost_audit.pipeline import DEFAULT_LINK_POLICY, LINK_POLICIES, run_pipeline
from ghost_audit.release import check_output_paths, validate_dataset, validate_for_dataset


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--requests", default="deletion_requests.csv")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--diagnostics", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--usage", default="")
    parser.add_argument("--evidence-report", default="", help="independent advisory evidence checks as JSON")
    parser.add_argument("--link-policy", default=DEFAULT_LINK_POLICY, choices=sorted(LINK_POLICIES))
    parser.add_argument("--use-bundle-order", action="store_true")
    parser.add_argument("--strict-capture-cutoff", action="store_true")
    parser.add_argument("--allow-partial-evidence", action="store_true", help="exploratory only: bypass evidence coverage release gate")
    parser.add_argument("--candidate", action="append", choices=sorted(CANDIDATES), default=None,
                        help="experimental selection; repeat to combine; default uses promoted candidates")
    parser.add_argument("--llm", action="store_true", help="explicitly enable optional paid intent classifier")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    try:
        experimental = (args.llm or args.allow_partial_evidence or args.candidate is not None or args.strict_capture_cutoff
                        or args.use_bundle_order or args.link_policy != DEFAULT_LINK_POLICY)
        if experimental and Path(args.out).resolve() == Path(DEFAULT_OUTPUT).resolve():
            raise ValueError("experimental/comparison modes require --out outside the final output.csv path")
        paths = [Path(args.out)] + [Path(p) for p in (args.diagnostics, args.summary, args.usage, args.evidence_report) if p]
        check_output_paths(data_dir, paths)
        validate_dataset(data_dir, args.requests)
        llm = None
        if args.llm:
            from ghost_audit.llm import ClaudeFallback
            llm = ClaudeFallback.from_env()
            if llm is None:
                raise ValueError("--llm requested but optional SDK/credentials are unavailable")
        with candidate_scope(args.candidate):
            run = run_pipeline(data_dir, args.requests, llm=llm, link_policy=args.link_policy,
                               use_bundle_order=args.use_bundle_order, strict_capture_cutoff=args.strict_capture_cutoff)
        cov = run.coverage
        for problem in cov["problems"]:
            print(f"WARNING: evidence {problem['file_id']} {problem['status']}: {problem.get('notes', '')}", file=sys.stderr)
        if cov["problems"] and not args.allow_partial_evidence:
            raise ValueError(f"release blocked: {len(cov['problems'])} evidence files missing/failed/partial; existing output preserved")
        rows = [(r.case_id, r.status, r.steps) for r in run.results]
        summ = summary(run)
        usage = {
            "requests_file": args.requests, "cases": len(rows), "llm_enabled": llm is not None,
            "link_policy": args.link_policy, "strict_capture_cutoff": args.strict_capture_cutoff,
            "release_eligible": not cov["problems"] and not experimental,
            "candidate_override": args.candidate, "enabled_candidates": list(DEFAULT_CANDIDATES if args.candidate is None else args.candidate), "paid_model_calls": len(run.llm_usage),
            "local_ocr": run.ocr_usage, "calls": run.llm_usage,
        }
        from ghost_audit.llm import estimate_cost
        usage["per_model"] = estimate_cost(run.llm_usage)
        usage["input_tokens"] = sum(c.get("input_tokens", 0) for c in run.llm_usage)
        usage["output_tokens"] = sum(c.get("output_tokens", 0) for c in run.llm_usage)
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["tokens_per_request"] = usage["total_tokens"] / len(rows) if rows else 0
        costs = [v["cost_usd"] for v in usage["per_model"].values()]
        usage["api_cost_usd"] = sum(costs) if all(c is not None for c in costs) else None
        usage["api_cost_per_request_usd"] = usage["api_cost_usd"] / len(rows) if rows and usage["api_cost_usd"] is not None else None
        # Sidecars precede the final CSV, so sidecar failures preserve that artifact.
        if args.evidence_report:
            from ghost_audit.verification import check_predictions
            report = check_predictions(data_dir, rows, args.requests, evidence=run.evidence_index)
            atomic_write_text(Path(args.evidence_report), json.dumps(report, indent=2))
        if args.diagnostics:
            payload = {"summary": summ, "cases": case_diagnostics(run), "workflow_events": run.event_log,
                       "linker_notes": run.linker_diagnostics, "llm_usage": run.llm_usage}
            atomic_write_text(Path(args.diagnostics), json.dumps(payload, indent=2, default=str))
        if args.summary:
            atomic_write_text(Path(args.summary), summary_markdown(summ))
        usage["seconds_before_export"] = time.perf_counter() - started
        if args.usage:
            atomic_write_text(Path(args.usage), json.dumps(usage, indent=2))
        write_output(Path(args.out), rows, validator=lambda blob: validate_for_dataset(blob, data_dir, args.requests))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 2
    prefix = "EXPERIMENTAL: " if experimental else ""
    print(f"{prefix}wrote {len(rows)} rows to {args.out} in {time.perf_counter() - started:.1f}s")
    print(f"status counts: {dict(sorted(summ['status_counts'].items()))}")
    print(f"evidence files: {cov['files']} parsed_ok={sum(cov['ok_by_type'].values())} problems={len(cov['problems'])}")
    print(f"paid model calls: {len(run.llm_usage)}; local OCR inference calls: {run.ocr_usage.get('inference_calls', 0)}")
    print(f"link policy: {args.link_policy}; weak/unresolved step share: {summ['share_of_steps_on_heuristics']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
