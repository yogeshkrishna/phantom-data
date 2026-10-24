"""contracts.py – locked shared types and output helpers for the deletion-audit agent."""
import csv
import json
from tempfile import NamedTemporaryFile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Sequence
SCHEMA_VERSION = 'phantom.v1'
IdentifierMap = dict[str, tuple[str, ...]]
FactKind = Literal['state', 'claim', 'identity', 'context']

@dataclass(frozen=True, slots=True)
class EntityKey:
    system_id: str
    store_id: str
    resource_id: str

@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    source_path: str
    source_sha256: str
    locator: str
    available_at: str

@dataclass(frozen=True, slots=True)
class EvidenceFact:
    entity: EntityKey
    category: str
    observed_at: str
    kind: FactKind
    state: str
    stored_fields: dict[str, object]
    query_identifiers: IdentifierMap
    field_coverage: tuple[str, ...]
    references: tuple[EntityKey, ...]
    metadata: dict[str, object]
    source: EvidenceRef

@dataclass(frozen=True, slots=True)
class SnapshotScope:
    system_id: str
    store_id: str
    collection: str
    category: str
    observed_at: str
    complete: bool
    resource_ids: tuple[str, ...]
    query_identifiers: IdentifierMap
    source: EvidenceRef

@dataclass(frozen=True, slots=True)
class AuditStep:
    step_no: int
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

@dataclass(frozen=True, slots=True)
class AuditResult:
    case_id: str
    deletion_status: str
    steps: tuple[AuditStep, ...]
    issues: tuple[str, ...] = ()
OUTPUT_COLUMNS = ('case_id', 'deletion_status', 'steps_json')
STEP_COLUMNS = ('step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id')
STATUS_VALUES = ('COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE')
OPERATION_RULES = (
    ('FINDING', 'REPORT_REMAINING_RECORD', 'PRESENT'),
    ('FINDING', 'REPORT_EXPOSED_PII', 'PRESENT'),
    ('FINDING', 'REPORT_ACTIVE_DATA_FLOW', 'ACTIVE'),
    ('FINDING', 'REPORT_RESTORE_RISK', 'ACTIVE'),
    ('REMEDIATION', 'DISABLE_DATA_FLOW', 'DISABLED'),
    ('REMEDIATION', 'BLOCK_RESTORE', 'BLOCKED'),
    ('REMEDIATION', 'DELETE_RECORD', 'ABSENT'),
    ('REMEDIATION', 'REDACT_RECORD', 'REDACTED'),
    ('REMEDIATION', 'ANONYMIZE_RECORD', 'ANONYMIZED'),
    ('VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    ('VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT'),
    ('VERIFICATION', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    ('VERIFICATION', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED'),
    ('VERIFICATION', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED'),
    ('VERIFICATION', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED'),
)
_OP_STATE: dict[tuple[str, str], str] = {(p, o): s for p, o, s in OPERATION_RULES}
_PHASE_ORD: dict[str, int] = {}
_OP_ORD: dict[tuple[str, str], int] = {}
for _i, (_ph, _op, _) in enumerate(OPERATION_RULES):
    _PHASE_ORD.setdefault(_ph, len(_PHASE_ORD))
    _OP_ORD[_ph, _op] = _i

def parse_instant(value: str) -> datetime:
    """Parse ISO-8601 with Z or explicit UTC offset; return UTC-aware datetime."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'parse_instant: missing/empty: {value!r}')
    s = value.strip()
    s = s[:-1] + '+00:00' if s.endswith('Z') else s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f'parse_instant: invalid {value!r}') from exc
    if dt.tzinfo is None:
        raise ValueError(f'parse_instant: naive {value!r}')
    return dt.astimezone(timezone.utc)

def is_available(item: EvidenceFact | SnapshotScope, audit_at: str) -> bool:
    """Validate all three instants before comparing either cutoff."""
    cutoff = parse_instant(audit_at)
    observed = parse_instant(item.observed_at)
    available = parse_instant(item.source.available_at)
    return observed <= cutoff and available <= cutoff

def validate_row(row: dict[str, str]) -> None:
    """Validate a CSV output row; raise ValueError on any structural violation."""
    if not isinstance(row, dict) or set(row) != set(OUTPUT_COLUMNS):
        raise ValueError('Output row must have exactly the required columns')
    if any((not isinstance(value, str) for value in row.values())):
        raise ValueError('Every output row value must be a string')
    if not row['case_id'].strip():
        raise ValueError('case_id must not be blank')
    st = row['deletion_status']
    if st not in STATUS_VALUES:
        raise ValueError(f'validate_row: bad status {st!r}')
    try:
        raw = json.loads(row['steps_json'])
    except json.JSONDecodeError as exc:
        raise ValueError(f'validate_row: invalid steps_json: {exc}') from exc
    if not isinstance(raw, list):
        raise ValueError('validate_row: steps_json must be array')
    if st == 'NOT_APPLICABLE':
        if raw:
            raise ValueError('validate_row: NOT_APPLICABLE requires empty steps')
        return
    if not raw:
        raise ValueError(f'validate_row: {st} requires nonempty steps')
    parsed: list[dict] = []
    for idx, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise ValueError(f'step {idx}: not an object')
        if set(s.keys()) != set(STEP_COLUMNS):
            raise ValueError(f'step {idx}: wrong keys {sorted(s.keys())}')
        sno = s['step_no']
        if isinstance(sno, bool) or not isinstance(sno, int):
            raise ValueError(f'step {idx}: step_no must be int')
        if sno != idx:
            raise ValueError(f'step {idx}: step_no={sno}')
        for col in STEP_COLUMNS:
            if col != 'step_no' and (not isinstance(s[col], str)):
                raise ValueError(f'step {idx}: {col} must be str')
        if not s['system_id'] or not s['resource_id']:
            raise ValueError(f'step {idx}: empty id field')
        ph, op, sta = (s['phase'], s['operation'], s['state'])
        exp = _OP_STATE.get((ph, op))
        if exp is None:
            raise ValueError(f'step {idx}: ({ph},{op}) not in OPERATION_RULES')
        if sta != exp:
            raise ValueError(f'step {idx}: state={sta!r} expected {exp!r}')
        parsed.append(s)

    def _sk(s: dict) -> tuple:
        return (
            _PHASE_ORD[s['phase']], _OP_ORD[s['phase'], s['operation']],
            s['system_id'], s['resource_id'],
        )
    for i in range(len(parsed) - 1):
        if _sk(parsed[i]) > _sk(parsed[i + 1]):
            raise ValueError(f'validate_row: order violation at {i + 1}')
    seen: set[tuple] = set()
    for s in parsed:
        k = (s['phase'], s['system_id'], s['resource_id'], s['operation'])
        if k in seen:
            raise ValueError(f'validate_row: duplicate {k}')
        seen.add(k)
    phases = {s['phase'] for s in parsed}
    if st == 'COMPLETE':
        if phases != {'VERIFICATION'}:
            raise ValueError('validate_row: COMPLETE needs only VERIFICATION')
        if any((not s['evidence_id'] for s in parsed)):
            raise ValueError('validate_row: COMPLETE needs evidence_id')
    elif st == 'INCOMPLETE':
        if not phases >= {'FINDING', 'REMEDIATION', 'VERIFICATION'}:
            raise ValueError('validate_row: INCOMPLETE needs all phases')
        for s in parsed:
            if s['phase'] == 'FINDING' and (not s['evidence_id']):
                raise ValueError('INCOMPLETE FINDING needs evidence_id')
            if s['phase'] in {'REMEDIATION', 'VERIFICATION'} and s['evidence_id']:
                raise ValueError(f"INCOMPLETE {s['phase']} evidence_id must be empty")

def to_csv_row(result: AuditResult) -> dict[str, str]:
    """Serialize AuditResult to a validated OUTPUT_COLUMNS dict."""
    row = {
        'case_id': result.case_id,
        'deletion_status': result.deletion_status,
        'steps_json': json.dumps(
            [asdict(step) for step in result.steps],
            ensure_ascii=False, separators=(',', ':'),
        ),
    }
    validate_row(row)
    return row

def write_output(path: Path, results: Sequence[AuditResult], expected_case_ids: Sequence[str]) -> None:
    """Validate first, then atomically replace using an exclusively owned sibling."""
    expected = list(expected_case_ids)
    if not expected or any((not isinstance(value, str) or not value.strip() for value in expected)):
        raise ValueError('Expected case IDs must be nonempty strings')
    if len(set(expected)) != len(expected):
        raise ValueError('Duplicate expected case IDs')
    rows = [to_csv_row(result) for result in results]
    ids = [row['case_id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate result case IDs')
    if set(ids) != set(expected):
        raise ValueError('Result coverage differs from expected case IDs')
    rows.sort(key=lambda row: row['case_id'])
    temporary = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='', dir=path.parent,
            prefix=f'.{path.name}.', suffix='.tmp', delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
