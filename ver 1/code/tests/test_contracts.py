"""test_contracts.py – unit tests for phantom.contracts."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import AuditResult, AuditStep, EntityKey, EvidenceFact, EvidenceRef, SnapshotScope, OUTPUT_COLUMNS, STEP_COLUMNS, is_available, parse_instant, to_csv_row, validate_row, write_output
REF = EvidenceRef('ev-1', 'f.csv', 'abc', 'row:1', '2024-01-01T00:00:00Z')
KEY = EntityKey('SYS', 'DB', 'U1')
CUT = '2024-06-01T00:00:00Z'
FAR = '2025-01-01T00:00:00Z'

def _ref(a='2024-01-01T00:00:00Z'):
    return EvidenceRef('ev-1', 'f.csv', 'abc', 'row:1', a)

def _fact(o='2024-01-01T00:00:00Z', a='2024-01-01T00:00:00Z'):
    return EvidenceFact(KEY, 'user', o, 'state', 'PRESENT', {}, {}, (), (), {}, _ref(a))

def _scope(a='2024-01-01T00:00:00Z'):
    return SnapshotScope('SYS', 'DB', 'u', 'user', '2024-01-01T00:00:00Z', True, ('U1',), {}, _ref(a))

def _vs(n=1, ev='ev-1', sid='SYS', rid='U1'):
    return {'step_no': n, 'phase': 'VERIFICATION', 'system_id': sid, 'resource_id': rid, 'operation': 'VERIFY_RECORD_ABSENT', 'state': 'ABSENT', 'evidence_id': ev}

def _crow(cid='c1'):
    return {'case_id': cid, 'deletion_status': 'COMPLETE', 'steps_json': json.dumps([_vs()])}

def _cr(cid='case-1'):
    return AuditResult(cid, 'COMPLETE', (AuditStep(1, 'VERIFICATION', 'SYS', 'U1', 'VERIFY_RECORD_ABSENT', 'ABSENT', 'ev-1'),))

def _ir(cid='case-2'):
    return AuditResult(cid, 'INCOMPLETE', (AuditStep(1, 'FINDING', 'SYS', 'U1', 'REPORT_REMAINING_RECORD', 'PRESENT', 'ev-f'), AuditStep(2, 'REMEDIATION', 'SYS', 'U1', 'DELETE_RECORD', 'ABSENT', ''), AuditStep(3, 'VERIFICATION', 'SYS', 'U1', 'VERIFY_RECORD_ABSENT', 'ABSENT', '')))

def _inc(phases, eids):
    ops = {'FINDING': 'REPORT_REMAINING_RECORD', 'REMEDIATION': 'DELETE_RECORD', 'VERIFICATION': 'VERIFY_RECORD_ABSENT'}
    sts = {'FINDING': 'PRESENT', 'REMEDIATION': 'ABSENT', 'VERIFICATION': 'ABSENT'}
    return json.dumps([{'step_no': i + 1, 'phase': p, 'system_id': 'S', 'resource_id': 'R', 'operation': ops[p], 'state': sts[p], 'evidence_id': eids[i]} for i, p in enumerate(phases)])

def _irow(phases, eids):
    return {'case_id': 'c', 'deletion_status': 'INCOMPLETE', 'steps_json': _inc(phases, eids)}

class TestParseInstant(unittest.TestCase):

    def test_z(self):
        self.assertEqual(parse_instant('2024-06-01T12:00:00Z').utcoffset().total_seconds(), 0)

    def test_plus(self):
        dt = parse_instant('2024-06-01T17:30:00+05:30')
        self.assertEqual((dt.hour, dt.minute), (12, 0))

    def test_minus(self):
        self.assertEqual(parse_instant('2024-06-01T11:00:00-01:00').hour, 12)

    def test_equiv(self):
        self.assertEqual(parse_instant('2024-06-01T12:00:00Z'), parse_instant('2024-06-01T17:30:00+05:30'))

    def test_naive(self):
        self.assertRaises(ValueError, parse_instant, '2024-06-01T12:00:00')

    def test_empty(self):
        self.assertRaises(ValueError, parse_instant, '')

    def test_junk(self):
        self.assertRaises(ValueError, parse_instant, 'not-a-date')

class TestIsAvailable(unittest.TestCase):

    def test_both_before(self):
        self.assertTrue(is_available(_fact(), CUT))

    def test_obs_after(self):
        self.assertFalse(is_available(_fact(o=FAR), CUT))

    def test_avail_after(self):
        self.assertFalse(is_available(_fact(a=FAR), CUT))

    def test_exactly_at(self):
        self.assertTrue(is_available(_fact(o=CUT, a=CUT), CUT))

    def test_scope_avail_after(self):
        self.assertFalse(is_available(_scope(a=FAR), CUT))

    def test_bad_cutoff(self):
        self.assertRaises(ValueError, is_available, _fact(), 'bad')

class TestValidateRow(unittest.TestCase):

    def test_ok(self):
        validate_row(_crow())

    def test_na_ok(self):
        validate_row({'case_id': 'c', 'deletion_status': 'NOT_APPLICABLE', 'steps_json': '[]'})

    def test_na_nonempty(self):
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'NOT_APPLICABLE', 'steps_json': json.dumps([_vs()])})

    def test_missing_key(self):
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'COMPLETE'})

    def test_extra_key(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'x': 'y'})

    def test_bad_status(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'deletion_status': 'BAD'})

    def test_empty_cid(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'case_id': ''})

    def test_not_array(self):
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'COMPLETE', 'steps_json': '{"a":1}'})

    def test_bool_step_no(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'step_no': True}])})

    def test_wrong_num(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'step_no': 2}])})

    def test_extra_step_key(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'extra': 'x'}])})

    def test_wrong_state(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'state': 'PRESENT'}])})

    def test_bad_op(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'operation': 'NO_OP', 'state': 'X'}])})

    def test_empty_sid(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'system_id': ''}])})

    def test_empty_rid(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([{**_vs(), 'resource_id': ''}])})

    def test_complete_no_verification(self):
        s = {'step_no': 1, 'phase': 'FINDING', 'system_id': 'S', 'resource_id': 'R', 'operation': 'REPORT_REMAINING_RECORD', 'state': 'PRESENT', 'evidence_id': 'e'}
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'COMPLETE', 'steps_json': json.dumps([s])})

    def test_complete_empty_eid(self):
        self.assertRaises(ValueError, validate_row, {**_crow(), 'steps_json': json.dumps([_vs(ev='')])})

    def test_inc_missing_finding(self):
        self.assertRaises(ValueError, validate_row, _irow(['REMEDIATION', 'VERIFICATION'], ['', '']))

    def test_inc_finding_no_eid(self):
        self.assertRaises(ValueError, validate_row, _irow(['FINDING', 'REMEDIATION', 'VERIFICATION'], ['', '', '']))

    def test_inc_remediation_bad_eid(self):
        self.assertRaises(ValueError, validate_row, _irow(['FINDING', 'REMEDIATION', 'VERIFICATION'], ['e', 'BAD', '']))

    def test_duplicate_step(self):
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'COMPLETE', 'steps_json': json.dumps([_vs(1), {**_vs(1), 'step_no': 2}])})

    def test_ordering_violation(self):
        steps = [{'step_no': 1, 'phase': 'VERIFICATION', 'system_id': 'S', 'resource_id': 'R', 'operation': 'VERIFY_RECORD_ABSENT', 'state': 'ABSENT', 'evidence_id': 'e1'}, {'step_no': 2, 'phase': 'FINDING', 'system_id': 'S', 'resource_id': 'R', 'operation': 'REPORT_REMAINING_RECORD', 'state': 'PRESENT', 'evidence_id': 'e2'}]
        self.assertRaises(ValueError, validate_row, {'case_id': 'c', 'deletion_status': 'INCOMPLETE', 'steps_json': json.dumps(steps)})

class TestToCsvRowAndWriteOutput(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def test_roundtrip(self):
        row = to_csv_row(_cr())
        self.assertEqual(set(row.keys()), set(OUTPUT_COLUMNS))
        self.assertEqual(set(json.loads(row['steps_json'])[0].keys()), set(STEP_COLUMNS))

    def test_unicode_cid(self):
        self.assertIn('cäse-ñ', to_csv_row(AuditResult('cäse-ñ', 'NOT_APPLICABLE', ()))['case_id'])

    def test_issues_excluded(self):
        row = to_csv_row(AuditResult('c', 'NOT_APPLICABLE', (), ('internal',)))
        self.assertNotIn('issues', row)
        self.assertNotIn('internal', json.dumps(row))

    def test_compact_json(self):
        s = to_csv_row(_cr())['steps_json']
        self.assertNotIn(': ', s)
        self.assertNotIn(', ', s)

    def test_write(self):
        p = self.d / 'o.csv'
        write_output(p, [_cr()], ['case-1'])
        self.assertIn('case_id', p.read_text(encoding='utf-8'))

    def test_lf(self):
        p = self.d / 'o.csv'
        write_output(p, [_cr()], ['case-1'])
        self.assertNotIn(b'\r\n', p.read_bytes())

    def test_sorted(self):
        p = self.d / 'o.csv'
        write_output(p, [_cr('b'), _cr('a')], ['a', 'b'])
        ls = p.read_text(encoding='utf-8').splitlines()
        self.assertTrue(ls[1].startswith('a,'))
        self.assertTrue(ls[2].startswith('b,'))

    def test_missing_preserves(self):
        p = self.d / 'o.csv'
        p.write_text('orig', encoding='utf-8')
        self.assertRaises(ValueError, write_output, p, [], ['c1'])
        self.assertEqual(p.read_text(encoding='utf-8'), 'orig')

    def test_extra(self):
        self.assertRaises(ValueError, write_output, self.d / 'o.csv', [_cr('c1'), _cr('c2')], ['c1'])

    def test_dup_result(self):
        self.assertRaises(ValueError, write_output, self.d / 'o.csv', [_cr(), _cr()], ['case-1'])

    def test_dup_expected(self):
        self.assertRaises(ValueError, write_output, self.d / 'o.csv', [_cr()], ['case-1', 'case-1'])

    def test_empty_expected(self):
        self.assertRaises(ValueError, write_output, self.d / 'o.csv', [], [])

    def test_quoting(self):
        r = AuditResult('a,"b"', 'NOT_APPLICABLE', ())
        p = self.d / 'o.csv'
        write_output(p, [r], ['a,"b"'])
        with p.open(encoding='utf-8') as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]['case_id'], 'a,"b"')

    def test_unicode_csv(self):
        r = AuditResult('cäse-ñ', 'NOT_APPLICABLE', ())
        p = self.d / 'o.csv'
        write_output(p, [r], ['cäse-ñ'])
        self.assertIn('cäse-ñ', p.read_text(encoding='utf-8'))

    def test_incomplete_rt(self):
        p = self.d / 'o.csv'
        write_output(p, [_ir()], ['case-2'])
        with p.open(encoding='utf-8') as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]['deletion_status'], 'INCOMPLETE')
        self.assertEqual(len(json.loads(rows[0]['steps_json'])), 3)

class TestFactKindAndPartialScope(unittest.TestCase):

    def test_claim(self):
        f = EvidenceFact(KEY, 'user', '2024-01-01T00:00:00Z', 'claim', 'UNKNOWN', {}, {}, (), (), {}, REF)
        self.assertEqual(f.kind, 'claim')

    def test_partial_scope(self):
        s = SnapshotScope('SYS', 'DB', 'orders', 'order', '2024-01-01T00:00:00Z', False, ('O1',), {'email': ('a@b.com',)}, REF)
        self.assertFalse(s.complete)
        self.assertEqual(s.query_identifiers['email'], ('a@b.com',))
if __name__ == '__main__':
    unittest.main()
