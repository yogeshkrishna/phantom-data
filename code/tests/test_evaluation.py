"""Evaluation penalizes missing output and never loads labels before prediction."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent.parent))
from evaluation.fixtures import run_custom_cases
from evaluation.main import evaluate
from phantom.contracts import AuditResult, AuditStep, OUTPUT_COLUMNS, to_csv_row
from phantom.planner import AuditBlocked
from phantom.tabular import TABLE_SPECS
from phantom.corpus import read_csv_table
from test_cache_compiler import dataset,write_csv
from test_policy import rules


class EvaluationTests(unittest.TestCase):
    def test_all_independent_filesystem_cases_and_mutations(self):
        results=run_custom_cases(rules())
        self.assertGreaterEqual(len(results),5)
        for case in results:
            with self.subTest(case=case['name']):
                self.assertTrue(case['passed'],case)
                self.assertIsNot(case['mutation_rejected'],False)

    def test_blocked_case_counts_as_missing_and_labels_loaded_last(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'data';dataset(source)
            rows=read_csv_table(source/'deletion_requests.csv',TABLE_SPECS[0][1],'case_id')
            write_csv(source/'sample_deletion_requests.csv',TABLE_SPECS[0][1],rows)
            expected=to_csv_row(AuditResult('independent','COMPLETE',(
                AuditStep(1,'VERIFICATION','account_service','opaque','VERIFY_RECORD_ABSENT','ABSENT','source'),)))
            write_csv(source/'sample_output.csv',OUTPUT_COLUMNS,[expected])
            attempted=[]
            def blocked(*args):
                attempted.append(True)
                raise AuditBlocked('independent missing proof')
            def read(path,*args,**kwargs):
                if path.name=='sample_output.csv':self.assertEqual(len(attempted),1)
                return read_csv_table(path,*args,**kwargs)
            with patch('evaluation.main.audit_request',blocked),patch('evaluation.main.read_csv_table',read):
                outcome=evaluate(source,root/'cache',root/'report.md')
            self.assertEqual(outcome['metrics']['status_accuracy'],0)
            self.assertEqual(outcome['metrics']['missing_case_ids'],['independent'])
            self.assertIn('BLOCKED',(root/'report.md').read_text())
