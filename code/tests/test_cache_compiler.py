"""Independent disk fixtures, cache invalidation and concurrent atomic writers."""
import csv
import hashlib
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.cache import JsonCache
from phantom.compiler import compile_corpus
from phantom.pipeline import prepare, audit_request
from phantom.tabular import TABLE_SPECS
from test_policy import rules
from test_state import A, B, C


def write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def dataset(path):
    path.mkdir()
    rows = {
        'deletion_requests': [dict(case_id='independent', email='a@example.test', requested_at=A,
                                  reported_status='COMPLETE', reported_at=B, audit_at=C, request_text='Delete my personal data')],
        'user_identities': [dict(user_id='u', name='Person', email='a@example.test', phone='123', email_hash='hash', updated_at=A)],
        'deletion_rules': rules(),
    }
    for name, columns, _ in TABLE_SPECS:
        write_csv(path/(name+'.csv'), columns, rows.get(name, []))
    evidence = dict(exported_at=B, origin='fixture', record_count=1, records=[dict(recorded_at=B,
                    system='account_service', resource_id='opaque', record_type='USER_PROFILE',
                    result='NOT_FOUND', lookup_value='a@example.test')])
    (path/'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
    write_csv(path/'evidence_files.csv', ('file_id','file_path','origin','captured_at'),
              [dict(file_id='source', file_path='evidence.json', origin='fixture', captured_at=B)])


class CacheTests(unittest.TestCase):
    def test_concurrent_atomic_writers(self):
        with TemporaryDirectory() as directory:
            cache = JsonCache(Path(directory))
            key = 'a'*64
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: cache.store(key, {'writer':i, 'values':list(range(100))}), range(12)))
            self.assertIn(cache.load(key)['writer'], range(12))
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_corrupt_partial_stale_and_path_injection(self):
        with TemporaryDirectory() as directory:
            cache = JsonCache(Path(directory));key='b'*64
            path=Path(directory)/(key+'.json')
            for content in ('{', '{"key":"wrong"}', '{"version":"old"}'):
                path.write_text(content)
                self.assertIsNone(cache.load(key))
                self.assertEqual(cache.last_error,'CORRUPT_CACHE_REBUILT')
            with self.assertRaises(ValueError):cache.load('../escape')

    def test_changed_payload_checksum_rejected(self):
        with TemporaryDirectory() as directory:
            cache=JsonCache(Path(directory));key='c'*64
            cache.store(key, {'meaning':'original'})
            path=Path(directory)/(key+'.json');raw=json.loads(path.read_text());raw['data']['meaning']='changed'
            path.write_text(json.dumps(raw))
            self.assertIsNone(cache.load(key))


class CompilerTests(unittest.TestCase):
    def test_roundtrip_deterministic_and_no_sample_answers_needed(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'dataset';dataset(source)
            first=prepare(source,cache_dir=root/'cache');second=prepare(source,cache_dir=root/'cache')
            self.assertFalse(first.cache_hit);self.assertTrue(second.cache_hit)
            self.assertEqual(first.states.facts,second.states.facts)
            self.assertEqual(audit_request(first,first.tables['deletion_requests'][0]),
                             audit_request(second,second.tables['deletion_requests'][0]))
            self.assertFalse((source/'sample_output.csv').exists())

    def test_source_bytes_and_availability_invalidate(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'dataset';dataset(source)
            first=compile_corpus(source,cache_dir=root/'cache')
            path=source/'evidence.json';path.write_text(path.read_text()+' ')
            changed=compile_corpus(source,cache_dir=root/'cache')
            self.assertNotEqual(first.cache_key,changed.cache_key)
            path=source/'evidence_files.csv';path.write_text(path.read_text().replace(B,C))
            later=compile_corpus(source,cache_dir=root/'cache')
            self.assertNotEqual(changed.cache_key,later.cache_key)
            self.assertEqual(later.facts[0].source.available_at,C)

    def test_corrupt_compiled_fact_is_rebuilt(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'dataset';dataset(source)
            first=compile_corpus(source,cache_dir=root/'cache')
            cache=JsonCache(root/'cache'/'compiled');payload=cache.load(first.cache_key)
            payload['facts'][0]['kind']='invented';cache.store(first.cache_key,payload)
            rebuilt=compile_corpus(source,cache_dir=root/'cache')
            self.assertFalse(rebuilt.cache_hit);self.assertEqual(rebuilt.facts[0].kind,'state')

    def test_source_error_inventory_survives_cache(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'dataset';dataset(source)
            (source/'evidence.json').write_text('{')
            first=compile_corpus(source,cache_dir=root/'cache');second=compile_corpus(source,cache_dir=root/'cache')
            self.assertEqual(first.diagnostics,second.diagnostics)
            self.assertEqual(second.inventory[0]['status'],'ERROR')
            self.assertEqual(second.diagnostics[0].code,'SOURCE_PARSE_ERROR')
