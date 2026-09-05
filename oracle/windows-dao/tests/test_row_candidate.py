"""Focused classification checks for the scalar-row candidate experiment."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('row_candidate', Path(__file__).parents[1] / 'scripts/row_candidate.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RowCandidateReportTests(unittest.TestCase):
    def classify(self, change=lambda result: None, tamper=False, tamper_input=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = b'candidate bytes'
            candidate = root / 'source.mdb'
            candidate.write_bytes(image)
            expected = {'version': '3.0', 'rows': [{'id': -1}, {'id': 0}, {'id': 1}]}
            source = root / 'producer.ps1'
            source.write_text('pinned producer')
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'experiment_id': 'test-rows', 'candidate': MODULE.identity(candidate),
                                        'inputs': {str(source): MODULE.digest(source)},
                                        'expected_snapshot': {'version': '3.0'},
                                        'row_range': {'first': -1, 'last': 1}}))
            result = {'document_type': 'dao_row_candidate_result', 'experiment_id': 'test-rows', 'development_only': True,
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
            if tamper_input:
                source.write_text('diagnostic edit')
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

    def test_modified_producer_rejected_before_analysis(self):
        with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
            self.classify(tamper_input=True)

    def test_retained_image_tampering_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Retained identity mismatch'):
            self.classify(tamper=True)


if __name__ == '__main__':
    unittest.main()
