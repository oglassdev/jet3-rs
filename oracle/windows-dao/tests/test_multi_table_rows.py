"""Classify complete multi-table schema, payloads and later index traversal."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('multi_table_rows', Path(__file__).parents[1] / 'scripts/multi_table_rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MultiTableRowsReportTests(unittest.TestCase):
    def classify(self, change=lambda result: None, tamper=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'producer.ps1'
            source.write_text('pinned producer')
            plan = {'inputs': {str(source): MODULE.digest(source)}, 'schema': {'version': '3.0'}, 'candidates': {}}
            for arm in MODULE.ARMS:
                path = root / f'{arm}.mdb'
                path.write_bytes(arm.encode())
                plan['candidates'][arm] = MODULE.identity(path)
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(plan))
            result = {'document_type': 'dao_multi_table_rows_result', 'development_only': True,
                      'plan_sha256': MODULE.digest(plan_path), 'environment': {'process_bits': 32},
                      'mutation_started': True, 'error': None, 'replicas': []}
            for arm in MODULE.ARMS:
                snapshot = MODULE.expected_snapshot(arm)
                for number in range(1, 4):
                    replica = {'arm': arm, 'replica': number}
                    for role in ('candidate', 'control'):
                        path = root / f'{arm}-{role}-r{number}.mdb'
                        path.write_bytes(arm.encode())
                        replica[role] = {'before': MODULE.identity(path), 'after': MODULE.identity(path),
                                         'status': 'pass', 'endpoint': 'complete', 'error': None,
                                         'snapshot': copy.deepcopy(snapshot)}
                    result['replicas'].append(replica)
            change(result)
            (root / 'result.json').write_text(json.dumps(result))
            if tamper == 'input':
                source.write_text('diagnostic edit')
            elif tamper == 'retained':
                (root / 'mixed-candidate-r1.mdb').write_bytes(b'changed')
            with patch.object(MODULE, 'PLAN', plan_path), patch('builtins.print'):
                MODULE.analyze(root)
            return json.loads((root / 'report.json').read_text())['outcomes']

    def test_all_tables_allow_only_snapshot_row_order_differences(self):
        def change(result):
            for table in result['replicas'][0]['candidate']['snapshot']['user_tables']:
                table['rows'].reverse()
        self.assertTrue(all(value == 'observed_accepted' for value in self.classify(change).values()))

    def test_later_table_payload_and_null_mismatches_are_negative(self):
        def change(result):
            for replica in result['replicas'][:3]:
                replica['candidate']['snapshot']['user_tables'][2]['rows'][1]['payload'] = ''
            for replica in result['replicas'][3:]:
                replica['candidate']['snapshot']['user_tables'][1]['rows'][0]['payload'] = '00'
        self.assertTrue(all(value == 'not_observed_accepted' for value in self.classify(change).values()))

    def test_table_inventory_and_index_flags_are_checked(self):
        for field in ('user_tables', 'tables'):
            def change(result):
                for replica in result['replicas'][:3]:
                    replica['candidate']['snapshot'][field].pop()
            self.assertEqual(self.classify(change)['mixed'], 'not_observed_accepted')
        def flags(result):
            for replica in result['replicas'][:3]:
                replica['candidate']['snapshot']['user_tables'][1]['indexes'][0]['foreign'] = True
        self.assertEqual(self.classify(flags)['mixed'], 'not_observed_accepted')

    def test_later_index_traversal_and_seek_must_match_complete_rows(self):
        for endpoint in ('traversal', 'seek'):
            def change(result):
                for replica in result['replicas'][:3]:
                    replica['candidate']['snapshot']['user_tables'][1][endpoint].reverse()
            self.assertEqual(self.classify(change)['mixed'], 'not_observed_accepted')

    def test_disagreement_control_failure_and_mutation_have_no_outcome(self):
        def disagreement(result):
            result['replicas'][0]['candidate']['snapshot']['user_tables'].pop()
        self.assertEqual(self.classify(disagreement)['mixed'], 'no_outcome')
        def control_failure(result):
            result['replicas'][0]['control']['snapshot']['user_tables'] = []
        def changed_image(result):
            result['replicas'][0]['control']['before']['sha256'] = '0' * 64
        for change in (control_failure, changed_image):
            self.assertTrue(all(value == 'no_outcome' for value in self.classify(change).values()))

    def test_incomplete_scientific_job_has_no_outcome(self):
        def change(result):
            result['replicas'].pop()
            result['error'] = 'CreateDatabase failed'
        self.assertTrue(all(value == 'no_outcome' for value in self.classify(change).values()))

    def test_modified_inputs_and_retained_files_are_rejected(self):
        for tamper in ('input', 'retained'):
            with self.subTest(tamper=tamper), self.assertRaises(ValueError):
                self.classify(tamper=tamper)


if __name__ == '__main__':
    unittest.main()
