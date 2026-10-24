"""Visible development-set evaluation. Expected answers never enter production."""
import json
import time
from pathlib import Path

from phantom.contracts import OUTPUT_COLUMNS, STEP_COLUMNS, to_csv_row
from phantom.corpus import read_csv_table
from phantom.metrics import compare_rows
from phantom.pipeline import audit_request, prepare
from phantom.planner import AuditBlocked
from phantom.tabular import TABLE_SPECS
from .fixtures import run_custom_cases


def evaluate(dataset_dir: Path, cache_dir: Path, report_path: Path):
    started=time.perf_counter()
    context=prepare(dataset_dir,cache_dir=cache_dir)
    preparation_seconds=time.perf_counter()-started
    requests=read_csv_table(dataset_dir/'sample_deletion_requests.csv',TABLE_SPECS[0][1],'case_id')
    predictions,blocked=[],{}
    started=time.perf_counter()
    for request in requests:
        try:predictions.append(to_csv_row(audit_request(context,request)))
        except AuditBlocked as exc:blocked[request['case_id']]=str(exc)
    audit_seconds=time.perf_counter()-started
    # Expected answers are loaded only AFTER every production attempt above.
    expected=read_csv_table(dataset_dir/'sample_output.csv',OUTPUT_COLUMNS,'case_id')
    metrics=compare_rows(expected,predictions)
    custom=run_custom_cases(context.tables['deletion_rules'])
    predicted={row['case_id']:row for row in predictions}
    errors=[]
    for row in expected:
        actual=predicted.get(row['case_id'])
        if actual==row:continue
        es=json.loads(row['steps_json']);ps=json.loads(actual['steps_json']) if actual else []
        if actual and actual['deletion_status']==row['deletion_status'] and es==ps:continue
        errors.append(dict(case_id=row['case_id'],expected_status=row['deletion_status'],
            actual_status=actual['deletion_status'] if actual else 'BLOCKED',
            expected_steps=len(es),actual_steps=len(ps),reason=blocked.get(row['case_id'],'STEP_OR_CITATION_MISMATCH'),
            expected=es,actual=ps))
    outcome=dict(metrics=metrics,blocked=blocked,errors=errors,custom=custom,
                 preparation_seconds=preparation_seconds,audit_seconds=audit_seconds,
                 context=context,predictions=predictions)
    write_report(outcome,report_path)
    return outcome


def write_report(outcome,path):
    metrics=outcome['metrics'];context=outcome['context']
    lines=['# Deletion audit evaluation','',
      'Measured on the supplied visible development samples. These are not held-out or hidden-test scores; the HackerRank aggregate formula is unknown.', '',
      'Predictions were attempted through the production pipeline before reading sample_output.csv. Blocked requests count as missing predictions and reduce coverage/accuracy.', '',
      '| Metric | Value |','|---|---|']
    for key in ('expected_cases','predicted_cases','status_accuracy','case_exact_accuracy','sequence_accuracy','expected_steps','predicted_steps','matched_steps','step_precision','step_recall','step_f1'):
        value=metrics[key];lines.append(f'| {key} | {value:.6f} |' if isinstance(value,float) else f'| {key} | {value} |')
    lines.extend(['','Positional field accuracy (unmatched/missing/extra positions are penalized):','', '| Field | Accuracy |','|---|---|'])
    lines.extend(f'| {key} | {value:.6f} |' for key,value in metrics['field_accuracy'].items())
    lines.extend(['',f"Missing cases: {', '.join(metrics['missing_case_ids']) or 'none'}. Extra cases: {', '.join(metrics['extra_case_ids']) or 'none'}.",
        '',f"Evidence: {context.files_supported}/{context.files_attempted} files interpreted, {len(context.diagnostics)} diagnostics. Preparation {outcome['preparation_seconds']:.3f}s; prepared sample attempts {outcome['audit_seconds']:.3f}s; compiler cache hit={context.cache_hit}.",
        '', '## Independent filesystem cases','', '| Case | Expected status | Exact result | Citation mutation rejected |','|---|---|---|---|'])
    for case in outcome['custom']:
        lines.append(f"| {case['name']} | {case['expected']['deletion_status']} | {'PASS' if case['passed'] else 'FAIL'} | {case['mutation_rejected']} |")
    lines.extend(['','Fixtures construct their own CSV/JSON/SQL sources and explicit expected plans; no production planner generates their labels. Full source/expectations are in fixtures.py.',''])
    for case in outcome['custom']:lines.append(f"- {case['name']}: {case['summary']}"+(f" Failure: {case.get('error','output mismatch')}." if not case['passed'] else ''))
    lines.extend(['','## Error inventory','', '| Case | Expected / actual status | Steps expected / actual | Diagnosis |','|---|---|---|---|'])
    for error in outcome['errors']:
        lines.append(f"| {error['case_id']} | {error['expected_status']} / {error['actual_status']} | {error['expected_steps']} / {error['actual_steps']} | {error['reason']} |")
    lines.extend(['','## Boundaries and improvements','',
      'Local OCR/model-derived evidence uses the same cutoff, scope and policy verifier as structured evidence. Raw extraction/cache data and model weights remain outside the submitted source. No paid API is used.', '',
      'Regression checks cover added PII, enabled connectors, unavailable future captures, missing retained records and forged citations. Earlier fixes include per-store state, partial-field preservation, query/payload separation and concurrent Windows cache writes.', '',
      'Remaining mismatches require source-level error analysis in stage12. No predictions were edited to match labels. A complete 518-row submission has not yet been demonstrated.'])
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
