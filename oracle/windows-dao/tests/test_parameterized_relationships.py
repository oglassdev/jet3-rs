import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('parameterized_relationships', SCRIPTS / 'parameterized_relationships.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class ParameterizedRelationshipsTests(unittest.TestCase):
    def fixture(self, outbox):
        plan = json.loads(experiment.PLAN.read_text())
        result = {'document_type': 'dao_parameterized_relationships_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'replicas': [], 'error': None}
        for arm in plan['arms']:
            expected = arm['expected_control']
            snapshot = {key: copy.deepcopy(expected[key]) for key in ('version', 'tables', 'relations')}
            snapshot['schema'] = {name: {'attributes': 0, 'fields': copy.deepcopy(fields), 'rows': [], 'indexes': []}
                                  for name, fields in expected['columns'].items()}
            snapshot['schema'][arm['parent']]['indexes'] = [
                {'name': name, 'primary': primary, 'unique': True, 'foreign': False,
                 'required': False, 'ignore_nulls': False, 'fields': [{'name': column, 'attributes': 0}]}
                for name, column, primary in expected['parent_indexes']]
            image = arm['name'].encode()
            arm['candidate'] = {'size': len(image), 'sha256': experiment.hashlib.sha256(image).hexdigest()}
            observation = {'before': arm['candidate'], 'after': arm['candidate'], 'status': 'pass',
                           'endpoint': 'complete', 'error': None, 'snapshot': snapshot}
            for number in range(1, 4):
                result['replicas'].append({'arm': arm['name'], 'replica': number,
                                          'control': copy.deepcopy(observation), 'candidate': copy.deepcopy(observation)})
                for role in ('control', 'candidate'):
                    (outbox / f"{arm['name']}-{role}-r{number}.mdb").write_bytes(image)
        return plan, result

    def test_each_arm_requires_all_metadata_and_relation_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'observed_accepted')
            for replica in result['replicas'][3:]:
                replica['candidate']['snapshot']['relations'][0]['fields'][0]['foreign_name'] = 'Label3'
            report = experiment.build_report(result, outbox, plan)
            self.assertEqual(report['outcome'], 'not_observed_accepted')
            self.assertEqual(report['arms']['one-index']['outcome'], 'observed_accepted')
            self.assertEqual(report['arms']['two-index']['outcome'], 'not_observed_accepted')
            result['replicas'][3]['candidate']['snapshot']['schema']['Owners2']['indexes'][0]['required'] = True
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')

    def test_incomplete_controls_or_changed_files_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            for replica in result['replicas'][:3]:
                replica['control']['snapshot']['schema']['Accounts7']['rows'] = [[1, 2]]
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            plan, result = self.fixture(outbox)
            result['replicas'].pop()
            result['error'] = 'Mutation failed'
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            result['replicas'][0]['control']['before'] = {'size': 0, 'sha256': 'changed'}
            self.assertIn('read-only bytes changed', experiment.build_report(result, outbox, plan)['arms']['one-index']['reasons'][0])
            result['replicas'][0]['candidate']['before'] = {'size': 0, 'sha256': 'wrong'}
            with self.assertRaisesRegex(ValueError, 'starting identity'):
                experiment.build_report(result, outbox, plan)
            plan, result = self.fixture(outbox)
            (outbox / 'one-index-candidate-r1.mdb').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Retained image'):
                experiment.build_report(result, outbox, plan)

    def test_inventory_and_standalone_pins_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            result['replicas'][0]['arm'] = 'two-index'
            with self.assertRaisesRegex(ValueError, 'inventory'):
                experiment.build_report(result, outbox, plan)
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
