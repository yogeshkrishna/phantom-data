"""Measured full-input release gate; missing proof never becomes a CSV verdict."""
import argparse
import hashlib
import json
import platform
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from phantom.cache import atomic_replace, canonical
from phantom.contracts import to_csv_row, write_output
from phantom.pipeline import audit_request, prepare
from phantom.planner import AuditBlocked


def _write(path, content):
    from tempfile import NamedTemporaryFile
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        atomic_replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _gaps(prepared, request, error):
    identity = prepared.identities.resolve(request["email"], request["audit_at"], requested_at=request["requested_at"])
    view = prepared.states.resolve(identity, request["audit_at"])
    gaps = []
    for obligation in prepared.policy.evaluate(view):
        if obligation.compliant is not None:
            continue
        resource = next(r for r in view.resources if r.entity == obligation.entity)
        gaps.append(dict(system=resource.entity.system_id, store=resource.entity.store_id,
                         resource=resource.entity.resource_id, category=resource.category,
                         reasons=obligation.reasons or ("TREATMENT_COVERAGE_UNPROVEN",),
                         sources=sorted({f.source.evidence_id + ":" + f.source.locator for f in resource.latest})))
    return dict(case_id=request["case_id"], error=error,
                issues=sorted(set(view.issues + identity.issues)), gaps=gaps)


def run_release(dataset: Path, cache: Path, output: Path, run_dir: Path, report_dir: Path,
                repeats: int = 3) -> dict:
    """Attempt every request, replay deterministically, and write output only on PASS.

    Raises ValueError for invalid options or replay disagreement. Reports contain
    resource keys/provenance, never emails, names or source payloads. A BLOCKED
    result preserves any existing output and returns its actual supported count.
    """
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be an integer between 1 and 10")
    corpus = dataset.resolve()
    for destination in (cache, output, run_dir, report_dir):
        if destination.resolve().is_relative_to(corpus):
            raise ValueError("release destinations must stay outside the read-only dataset")
    started = time.perf_counter()
    prepared = prepare(dataset, cache_dir=cache)
    preparation = time.perf_counter() - started
    requests = prepared.tables["deletion_requests"]
    durations, reference = [], None
    for _ in range(repeats):
        results, blocked = [], []
        audit_started = time.perf_counter()
        for request in requests:
            try:
                results.append(audit_request(prepared, request))
            except AuditBlocked as exc:
                blocked.append((request, str(exc)))
        durations.append(time.perf_counter() - audit_started)
        rows = [to_csv_row(r) for r in results]
        signature = hashlib.sha256(canonical(dict(rows=rows, blocked=[
            (r["case_id"], error) for r, error in blocked]))).hexdigest()
        if reference is not None and signature != reference:
            raise ValueError("NONDETERMINISTIC_REPLAY")
        reference = signature
    details = [_gaps(prepared, request, error) for request, error in blocked]
    ready = not blocked and not prepared.diagnostics
    if ready:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_output(output, results, [r["case_id"] for r in requests])
    outcome = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                   status="PASS" if ready else "BLOCKED", request_count=len(requests),
                   supported_count=len(results), blocked_count=len(blocked),
                   statuses=dict(Counter(r.deletion_status for r in results)),
                   files_supported=prepared.files_supported, files_attempted=prepared.files_attempted,
                   diagnostics=[dict(code=d.code, evidence_id=d.source.evidence_id,
                                     locator=d.source.locator) for d in prepared.diagnostics],
                   preparation_seconds=preparation, audit_seconds=durations,
                   median_audit_seconds=statistics.median(durations),
                   first_attempt_seconds=preparation + durations[0], compiler_cache_hit=prepared.cache_hit,
                   replay_signature=reference, supported_predictions_sha256=hashlib.sha256(
                       json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                   output_written=ready, output_sha256=hashlib.sha256(output.read_bytes()).hexdigest() if ready else None,
                   cache_key=prepared.cache_key, python=platform.python_version(),
                   model_usage=prepared.model_usage, blocked=details)
    # Diagnostic output is deliberately named and kept separate from output.csv.
    # On a blocked run it contains only supported cases, never filler rows.
    run_dir.mkdir(parents=True, exist_ok=True)
    if results:
        write_output(run_dir / "supported_predictions.csv", results, [r.case_id for r in results])
    else:
        _write(run_dir / "supported_predictions.csv", "case_id,deletion_status,steps_json\n")
    _write(run_dir / "release_run.json", json.dumps(outcome, indent=2) + "\n")
    _reports(outcome, report_dir)
    return outcome


def _reports(outcome, directory):
    reasons = Counter(reason for case in outcome["blocked"] for gap in case["gaps"] for reason in gap["reasons"])
    lines = ["# Release readiness", "", f"Run: {outcome['timestamp_utc']}. Gate: **{outcome['status']}**.", "",
             f"Attempted {outcome['request_count']} requests; {outcome['supported_count']} supported, "
             f"{outcome['blocked_count']} blocked. Output written: {outcome['output_written']}.", "",
             f"Sources interpreted: {outcome['files_supported']}/{outcome['files_attempted']}; "
             f"diagnostics: {len(outcome['diagnostics'])}. Compiler cache hit: {outcome['compiler_cache_hit']}.", "",
             f"Preparation: {outcome['preparation_seconds']:.3f}s. Prepared audit trials: "
             + ", ".join(f"{n:.3f}s" for n in outcome["audit_seconds"]) + ".",
             f"First attempt including preparation: {outcome['first_attempt_seconds']:.3f}s. "
             "Dependency installation/model download is excluded.", "",
             "Replay includes independently verified predictions and blocked case/error pairs. "
             "Repeated trials agreed; this is reproducibility, not hidden-label accuracy.", "",
             f"Supported-prediction digest: `{outcome['supported_predictions_sha256']}`.", "",
             "## Missing proof", "", "| Reason | Obligations |", "|---|---|"]
    lines.extend(f"| {reason} | {count} |" for reason, count in sorted(reasons.items()))
    lines += ["", "Missing retention conditions require an explicit applicable boundary or legal-hold "
              "readback. Missing risk state requires an enabled/restore_allowed readback. Archive presence "
              "requires current catalog/presence proof. Treatment coverage requires a complete relevant "
              "field readback or explicit redaction/anonymization observation. Missing subject links "
              "require a selector or visible identity/history edge. Operation success alone is insufficient.", "",
              "| Case | Resource and missing proof |", "|---|---|"]
    for case in outcome["blocked"]:
        entries = [gap["system"] + "/" + gap["resource"] + " [" + gap["store"] + "]: " +
                   ", ".join(gap["reasons"]) + " (" + "; ".join(gap["sources"]) + ")" for gap in case["gaps"]]
        lines.append(f"| {case['case_id']} | " + "<br>".join(entries or [case["error"]]) + " |")
    _write(directory / "release_readiness.md", "\n".join(lines) + "\n")
    usage = outcome["model_usage"] or {}
    models = usage.get("models", {})
    report = ["# Measured runtime usage", "", f"Run: {outcome['timestamp_utc']}. Gate: {outcome['status']}.", "",
              f"Attempted {outcome['request_count']} requests; {outcome['supported_count']} supported; "
              f"{outcome['blocked_count']} blocked. output.csv produced: {outcome['output_written']}.", "",
              "This is a measured full-input attempt. A blocked attempt is not a compliant final-output run; "
              "regenerate this report after all release gates pass.", "",
              "| Provider/model | Calls in this attempt | Input/output tokens | API cost USD |",
              "|---|---|---|---|",
              f"| Local CPU RapidOCR {usage.get('package_version', 'not used')} / ONNX Runtime "
              f"{usage.get('runtime_version', 'not used')} | {usage.get('calls', 0)} | N/A: OCR has no LLM tokens | 0 |", "",
              f"OCR extraction-cache hits: {usage.get('cache_hits', 0)}. "
              f"Summed engine inference: {usage.get('inference_seconds', 0):.3f}s. "
              f"Compiled cache hit: {outcome['compiler_cache_hit']}.", "",
              f"Media wall time: {usage.get('media_wall_seconds', 0):.3f}s; "
              f"OCR workers used: {usage.get('workers_used', 0)}. Parallel engine time may exceed elapsed wall time.", "",
              "No language model runs in the audit pipeline. Overall LLM calls/tokens: 0. "
              "Average LLM tokens per attempted request: 0. Total/per-request paid API cost: USD 0. "
              "Local compute, electricity and development/coding-model usage are outside this runtime API figure.", "",
              "## Model identity", ""]
    report.extend(f"- {name}: SHA-256 `{digest}`" for name, digest in sorted(models.items()))
    report += ["", "Calls count whole OCR pipeline passes; internal detector/recognizer/classifier "
               "invocations are not individually instrumented. No per-component call totals are invented.", "",
               "Cached extraction/preparation may have used model inference earlier. That work is not "
               "counted as new inference in this attempt; the machine-readable run report preserves "
               "preparation_provenance where available. Cold and warm timings are reported separately "
               "when a clean-cache package rehearsal is performed."]
    _write(directory / "usage_report.md", "\n".join(report) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path(".cache/phantom"))
    parser.add_argument("--output", type=Path, default=Path("output.csv"))
    parser.add_argument("--run-dir", type=Path, default=Path("runs/release"))
    parser.add_argument("--report-dir", type=Path, default=Path(__file__).parent / "evaluation")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    result = run_release(args.dataset, args.cache, args.output, args.run_dir, args.report_dir, args.repeats)
    print(json.dumps({k: result[k] for k in ("status", "request_count", "supported_count", "blocked_count",
                      "output_written", "first_attempt_seconds", "median_audit_seconds")}, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
