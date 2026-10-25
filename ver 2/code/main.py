"""Ghost Data Still Exists - deletion-audit agent entry point.

Usage (from the repository root):
    python code/main.py                       # audit dataset/deletion_requests.csv -> output.csv
    python code/main.py --requests sample_deletion_requests.csv --out sample_predictions.csv
    python code/main.py --llm                 # optional Claude fallback for ambiguous intent
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost_audit.config import DEFAULT_DATA_DIR, DEFAULT_OUTPUT  # noqa: E402
from ghost_audit.output import write_output  # noqa: E402
from ghost_audit.diagnostics import case_diagnostics, summary, summary_markdown  # noqa: E402
from ghost_audit.pipeline import DEFAULT_LINK_POLICY, LINK_POLICIES, run_pipeline  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit personal-data deletion requests.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="participant dataset directory")
    parser.add_argument("--requests", default="deletion_requests.csv", help="request CSV inside --data-dir")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="output CSV path")
    parser.add_argument("--diagnostics", default="", help="optional JSON file for per-case diagnostics")
    parser.add_argument("--summary", default="", help="optional Markdown file with the run diagnostics summary")
    parser.add_argument("--usage", default="", help="optional usage JSON path")
    parser.add_argument("--link-policy", default=DEFAULT_LINK_POLICY, choices=sorted(LINK_POLICIES),
                        help="which link confidence classes may produce steps (default: %(default)s)")
    parser.add_argument("--use-bundle-order", action="store_true",
                        help="also break ties by record order inside snapshot bundles (weak, off by default)")
    parser.add_argument("--strict-capture-cutoff", action="store_true",
                        help="ignore files exported after audit_at even for linking/state (comparison mode)")
    parser.add_argument("--llm", action="store_true", help="enable optional Claude fallback (needs ANTHROPIC_API_KEY)")
    args = parser.parse_args(argv)

    llm = None
    if args.llm:
        from ghost_audit.llm import ClaudeFallback

        llm = ClaudeFallback.from_env()
        if llm is None:
            print("--llm requested but ANTHROPIC_API_KEY is not set; continuing deterministically.", file=sys.stderr)

    started = time.time()
    run = run_pipeline(Path(args.data_dir), args.requests, llm=llm, link_policy=args.link_policy,
                       use_bundle_order=args.use_bundle_order, strict_capture_cutoff=args.strict_capture_cutoff)
    rows = [(r.case_id, r.status, r.steps) for r in run.results]
    write_output(Path(args.out), rows)

    statuses = {}
    for r in run.results:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    cov = run.coverage
    print(f"wrote {len(rows)} rows to {args.out} in {time.time() - started:.1f}s")
    print(f"status counts: {dict(sorted(statuses.items()))}")
    print(f"evidence files: {cov['files']} parsed_ok={sum(cov['ok_by_type'].values())} "
          f"observations={cov['observations']} problems={len(cov['problems'])}")
    for p in cov["problems"]:
        hint = " (is rapidocr-onnxruntime installed? see code/requirements.txt)" if p["type"] == "png" else ""
        print(f"WARNING: evidence {p['file_id']} ({p['path']}) {p['status']}: {p.get('notes', '')}{hint}",
              file=sys.stderr)
    print(f"model calls: {len(run.llm_usage)}")
    summ = summary(run)
    print(f"link policy: {args.link_policy}; output steps on weak/unresolved links: "
          f"{summ['share_of_steps_on_heuristics']:.1%}")
    if args.usage:
        # Telemetry is opt-in so normal runs need not write inside the source tree.
        from ghost_audit.llm import estimate_cost

        usage_path = Path(args.usage)
        usage_path.write_text(json.dumps({
            "requests_file": args.requests, "cases": len(rows), "llm_enabled": llm is not None,
            "link_policy": args.link_policy, "strict_capture_cutoff": args.strict_capture_cutoff,
            "model_calls": len(run.llm_usage),
            "per_model": estimate_cost(run.llm_usage), "calls": run.llm_usage,
        }, indent=2), encoding="utf-8")
    if args.diagnostics:
        payload = {"summary": summ, "cases": case_diagnostics(run), "workflow_events": run.event_log,
                   "linker_notes": run.linker_diagnostics, "llm_usage": run.llm_usage}
        Path(args.diagnostics).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if args.summary:
        Path(args.summary).write_text(summary_markdown(summ), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
