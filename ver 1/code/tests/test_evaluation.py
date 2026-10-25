"""Evaluation penalizes missing output and never loads labels before prediction."""
import sys
import unittest
from dataclasses import replace
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
    def test_replica_ablation_exposes_a_semantic_regression(self):
        from evaluation.ablation import measure_ablations
        from test_pipeline import prepared,request
        from test_state import fact,B
        context=prepared([fact(),fact({},B,'ABSENT'),fact(store='replica')])
        # Explicit expected steps are independent of either measured planner run.
        expected=to_csv_row(AuditResult('synthetic','INCOMPLETE',(
            AuditStep(1,'FINDING','service','opaque','REPORT_REMAINING_RECORD','PRESENT','file-01'),
            AuditStep(2,'REMEDIATION','service','opaque','DELETE_RECORD','ABSENT',''),
            AuditStep(3,'VERIFICATION','service','opaque','VERIFY_RECORD_ABSENT','ABSENT',''))))
        context=replace(context,tables={**context.tables,
            'user_identities':[dict(user_id='u',name='Person',email='a@example.test',phone='123',email_hash='hash',updated_at='2026-01-01T00:00:00Z')],
            'user_audit_log':[]})
        results={r['name']:r for r in measure_ablations(context,[request()],[expected])}
        self.assertEqual(results['full']['metrics']['case_exact_accuracy'],1)
        self.assertEqual(results['without_replicas']['metrics']['status_accuracy'],0)

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
