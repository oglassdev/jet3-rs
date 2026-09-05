import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('composite_index', SCRIPTS / 'composite_index.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class CompositeIndexTests(unittest.TestCase):
    def fixture(self, outbox):
        plan = json.loads(experiment.PLAN.read_text())
        result = {'document_type': 'dao_composite_index_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'replicas': [], 'error': None}
        for name, arm in plan['arms'].items():
            snapshot = {**copy.deepcopy(plan['schema']), 'indexes': [{'name': 'ByKey', 'primary': False,
                'unique': arm['unique'], 'foreign': False, 'required': False, 'ignore_nulls': False,
                'fields': [{**field, 'attributes': int(field['descending'])} for field in arm['fields']]}], 'rows': copy.deepcopy(arm['rows']),
                'traversal': sorted(arm['rows'], key=lambda row: experiment.ordered_key(row, arm)),
                'seek': [{'query': query, 'row': next(row for row in arm['rows']
                    if list(experiment.key_values(row, arm)) == query)} for query in arm['queries']]}
            image = name.encode()
            identity = {'size': len(image), 'sha256': experiment.hashlib.sha256(image).hexdigest()}
            plan['candidates'][name] = identity
            observation = {'before': identity, 'after': identity, 'status': 'pass', 'endpoint': 'complete', 'error': None,
                           'snapshot': snapshot}
            for replica in range(1, 4):
                for role in ('control', 'candidate'):
                    (outbox / f'{name}-{role}-r{replica}.mdb').write_bytes(image)
                result['replicas'].append({'arm': name, 'replica': replica,
                    'candidate': copy.deepcopy(observation), 'control': copy.deepcopy(observation)})
        return plan, result

    def test_duplicate_seek_may_choose_either_full_row_but_traversal_requires_both(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            ordinary = experiment.ARMS[-1]
            self.assertTrue(all(value == 'observed_accepted' for value in experiment.build_report(result, outbox, plan)['outcomes'].values()))
            for replica in result['replicas'][-3:]:
                for seek in replica['candidate']['snapshot']['seek']:
                    if seek['query'] == [0, 0]:
                        seek['row'] = [0, 0, 12]
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcomes'][ordinary], 'observed_accepted')
            for replica in result['replicas'][-3:]:
                replica['candidate']['snapshot']['traversal'].remove([0, 0, 12])
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcomes'][ordinary], 'not_observed_accepted')

    def test_full_seek_binding_and_direction_flags_cannot_be_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            name = experiment.ARMS[1]
            for replica in result['replicas'][3:6]:
                replica['candidate']['snapshot']['seek'][0]['query'] = [-2147483648]
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcomes'][name], 'not_observed_accepted')
            plan, result = self.fixture(outbox)
            for replica in result['replicas'][:3]:
                replica['candidate']['snapshot']['indexes'][0]['fields'][0]['descending'] = False
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcomes'][experiment.ARMS[0]], 'not_observed_accepted')
            result['replicas'][0]['candidate']['snapshot']['indexes'][0]['required'] = True
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcomes'][experiment.ARMS[0]], 'no_outcome')

    def test_control_failure_incomplete_run_and_changed_identity_cannot_accept(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            result['replicas'][0]['control']['status'] = 'fail'
            self.assertTrue(all(value == 'no_outcome' for value in experiment.build_report(result, outbox, plan)['outcomes'].values()))
            plan, result = self.fixture(outbox)
            result['replicas'].pop()
            result['error'] = 'DAO failed after mutation'
            self.assertTrue(all(value == 'no_outcome' for value in experiment.build_report(result, outbox, plan)['outcomes'].values()))
            plan, result = self.fixture(outbox)
            result['replicas'][0]['control']['before'] = {'size': 0, 'sha256': 'changed'}
            self.assertFalse(experiment.build_report(result, outbox, plan)['unchanged'])
            result['replicas'][0]['candidate']['before'] = {'size': 0, 'sha256': 'wrong'}
            with self.assertRaisesRegex(ValueError, 'pinned identity'):
                experiment.build_report(result, outbox, plan)
            plan, result = self.fixture(outbox)
            (outbox / f'{experiment.ARMS[0]}-candidate-r1.mdb').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Retained identity'):
                experiment.build_report(result, outbox, plan)

    def test_analysis_checks_input_pins(self):
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
