"""Focused classification checks for the initial-row DAO experiment."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('initial_rows', Path(__file__).parents[1] / 'scripts/initial_rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InitialRowsReportTests(unittest.TestCase):
    def classify(self, change=lambda result: None, tamper=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = b'candidate bytes'
            candidate = root / 'source.mdb'
            candidate.write_bytes(image)
            expected = {'version': '3.0', 'rows': [{'id': None, 'code': None}, {'id': 1, 'code': 'one'}]}
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'candidate': MODULE.identity(candidate), 'expected_snapshot': expected}))
            result = {'document_type': 'dao_initial_rows_result', 'development_only': True,
                      'plan_sha256': MODULE.digest(plan), 'environment': {'process_bits': 32},
                      'mutation_started': True, 'error': None, 'replicas': []}
            for number in range(1, 4):
                replica = {'replica': number}
                for role in ('candidate', 'control'):
                    target = root / f'{role}-r{number}.mdb'
                    target.write_bytes(image)
                    replica[role] = {'before': MODULE.identity(target), 'after': MODULE.identity(target),
                                     'status': 'pass', 'endpoint': 'complete', 'error': None,
                                     'snapshot': json.loads(json.dumps(expected))}
                result['replicas'].append(replica)
            change(result)
            (root / 'result.json').write_text(json.dumps(result))
            if tamper:
                (root / 'candidate-r1.mdb').write_bytes(b'changed')
            with patch.object(MODULE, 'PLAN', plan), patch('builtins.print'):
                MODULE.analyze(root)
            return json.loads((root / 'report.json').read_text())['outcome']

    def test_accepts_row_multiset(self):
        def change(result):
            result['replicas'][0]['candidate']['snapshot']['rows'].reverse()
        self.assertEqual(self.classify(change), 'observed_accepted')

    def test_repeated_candidate_mismatch_is_negative_result(self):
        def change(result):
            for replica in result['replicas']:
                replica['candidate']['snapshot']['rows'] = []
        self.assertEqual(self.classify(change), 'not_observed_accepted')

    def test_control_failure_or_replica_disagreement_has_no_outcome(self):
        for role in ('control', 'candidate'):
            def change(result):
                result['replicas'][0][role]['snapshot']['rows'] = []
            with self.subTest(role=role):
                self.assertEqual(self.classify(change), 'no_outcome')

    def test_post_mutation_incomplete_job_has_no_outcome(self):
        def change(result):
            result['replicas'] = []
            result['error'] = 'CreateDatabase failed after mutation began'
        self.assertEqual(self.classify(change), 'no_outcome')

    def test_retained_image_tampering_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Retained identity mismatch'):
            self.classify(tamper=True)


if __name__ == '__main__':
    unittest.main()
