"""Classify index traversal and Seek independently of producer pass flags."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('indexed_rows', Path(__file__).parents[1] / 'scripts/indexed_rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IndexedRowsReportTests(unittest.TestCase):
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
            result = {'document_type': 'dao_indexed_rows_result', 'development_only': True,
                      'plan_sha256': MODULE.digest(plan_path), 'environment': {'process_bits': 32},
                      'mutation_started': True, 'error': None, 'replicas': []}
            for arm in MODULE.ARMS:
                rows = MODULE.expected_rows(arm)
                snapshot = {**MODULE.schema(plan, arm), 'rows': rows,
                            'traversal': sorted(rows, key=lambda row: (row['id'], row['payload'])),
                            'seek': [{'query': key, 'row': next(row for row in rows if row['id'] == key)}
                                     for key in sorted({row['id'] for row in rows})]}
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
                (root / 'primary-candidate-r1.mdb').write_bytes(b'changed')
            with patch.object(MODULE, 'PLAN', plan_path), patch('builtins.print'):
                MODULE.analyze(root)
            return json.loads((root / 'report.json').read_text())['outcomes']

    def test_duplicate_seek_may_choose_either_matching_payload(self):
        def change(result):
            replica = result['replicas'][6]
            rows = MODULE.expected_rows('ordinary')
            for item in replica['candidate']['snapshot']['seek']:
                item['row'] = next(row for row in reversed(rows) if row['id'] == item['query'])
        self.assertTrue(all(value == 'observed_accepted' for value in self.classify(change).values()))

    def test_missing_duplicate_in_traversal_is_negative(self):
        def change(result):
            for replica in result['replicas'][6:]:
                replica['candidate']['snapshot']['traversal'].pop()
        outcomes = self.classify(change)
        self.assertEqual(outcomes['ordinary'], 'not_observed_accepted')
        self.assertEqual(outcomes['primary'], 'observed_accepted')

    def test_wrong_seek_payload_is_negative(self):
        def change(result):
            for replica in result['replicas'][3:6]:
                replica['candidate']['snapshot']['seek'][0]['row']['payload'] = 'wrong'
        self.assertEqual(self.classify(change)['unique'], 'not_observed_accepted')

    def test_traversal_disagreement_and_control_failure_have_no_outcome(self):
        def disagreement(result):
            result['replicas'][0]['candidate']['snapshot']['traversal'].reverse()
        self.assertEqual(self.classify(disagreement)['primary'], 'no_outcome')
        def control_failure(result):
            result['replicas'][0]['control']['snapshot']['rows'] = []
        self.assertTrue(all(value == 'no_outcome' for value in self.classify(control_failure).values()))

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
