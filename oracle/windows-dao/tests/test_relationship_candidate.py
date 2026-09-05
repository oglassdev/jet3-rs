import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('relationship_candidate', SCRIPTS / 'relationship_candidate.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class RelationshipCandidateTests(unittest.TestCase):
    def fixture(self, outbox):
        plan = {'candidate': {'size': 9, 'sha256': experiment.hashlib.sha256(b'synthetic').hexdigest()},
                'expected_control': {'version': '3.0', 'tables': ['Child', 'Parent'], 'relations': [],
                                     'columns': {'Parent': [], 'Child': []}}}
        snapshot = {'version': '3.0', 'tables': ['Child', 'Parent'], 'relations': [], 'schema': {
            'Parent': {'attributes': 0, 'fields': [], 'rows': [], 'indexes': [
                {'name': 'ById', 'primary': True, 'unique': True, 'fields': [{'name': 'Id', 'attributes': 0}]},
                {'name': 'ByAlternate', 'primary': False, 'unique': True, 'fields': [{'name': 'Alternate', 'attributes': 0}]},
            ]}, 'Child': {'attributes': 0, 'fields': [], 'rows': [], 'indexes': []}}}
        observation = {'before': plan['candidate'], 'after': plan['candidate'], 'status': 'pass',
                       'endpoint': 'complete', 'error': None, 'snapshot': snapshot}
        result = {'document_type': 'dao_relationship_candidate_result', 'development_only': True,
                  'plan_sha256': 'pinned-plan', 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'replicas': [], 'error': None}
        for number in range(1, 4):
            result['replicas'].append({'replica': number, 'control': copy.deepcopy(observation), 'candidate': copy.deepcopy(observation)})
            for role in ('control', 'candidate'):
                (outbox / f'{role}-r{number}.mdb').write_bytes(b'synthetic')
        return plan, result

    def test_acceptance_requires_matching_index_metadata_and_unchanged_images(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'digest', return_value='pinned-plan'):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            # Identity uses the real candidate digest while plan hashing is mocked.
            with patch.object(experiment, 'identity', return_value=plan['candidate']):
                self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'observed_accepted')
                for replica in result['replicas']:
                    replica['candidate']['snapshot']['schema']['Parent']['indexes'][0]['unique'] = False
                self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'not_observed_accepted')
                result['replicas'][1]['candidate']['endpoint'] = 'schema'
                self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
                result['replicas'][0]['candidate']['before'] = {'size': 0, 'sha256': 'wrong'}
                with self.assertRaisesRegex(ValueError, 'starting identity'):
                    experiment.build_report(result, outbox, plan)

    def test_control_failure_and_partial_acquisition_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'digest', return_value='pinned-plan'):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            with patch.object(experiment, 'identity', return_value=plan['candidate']):
                result['replicas'].pop()
                result['error'] = 'DAO mutation failed'
                self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            with patch.object(experiment, 'identity', return_value={'size': 0, 'sha256': 'changed'}):
                with self.assertRaisesRegex(ValueError, 'Retained image'):
                    experiment.build_report(result, outbox, plan)

    def test_standalone_analysis_verifies_input_pins(self):
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
