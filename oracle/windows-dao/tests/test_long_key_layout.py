import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('long_key_layout', SCRIPTS / 'long_key_layout.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class LongKeyLayoutTests(unittest.TestCase):
    def plan(self):
        return json.loads(experiment.PLAN.read_text())

    def test_hypothesis_is_explicit_and_locators_resolve_exactly_once(self):
        arm = self.plan()['arms'][0]
        self.assertEqual(experiment.hypothesized_key([-2147483648, 0, 0], arm), '80ffffffff')
        self.assertEqual(experiment.hypothesized_key([2147483647, 0, 0], arm), '8000000000')
        rows = [{'page': 23, 'row': 0, 'values': [0, 0, 0]}]
        entries = [{'row_page': 23, 'row': 0, 'key_hex': 'deadbeef'}]
        bindings = experiment.bind_rows(rows, entries, arm)
        self.assertEqual(bindings[0]['key_hex'], 'deadbeef')  # Unknown bytes remain observable.
        for broken in [[], entries * 2, [dict(entries[0], row=1)]]:
            with self.assertRaises(experiment.catalog.DecodeError):
                experiment.bind_rows(rows, broken, arm)

    def fixture(self, outbox):
        plan = self.plan()
        result = {'document_type': 'dao_long_key_layout_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'replicas': [], 'error': None}
        for arm in plan['arms']:
            snapshot = {'version': '3.0', 'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
                        'fields': [{'name': name, 'type': 4, 'size': 4} for name in ('A', 'B', 'Tag')],
                        'indexes': [{'name': 'ByKey', 'primary': False, 'unique': arm['unique'],
                                     'required': False, 'fields': arm['fields']}], 'rows': arm['rows'],
                        'traversal': sorted(arm['rows'], key=lambda row: experiment.key_values(row, arm))}
            for replica in range(1, 4):
                name = f"{arm['name']}-r{replica}.mdb"
                (outbox / name).write_bytes(b'synthetic')
                identity = experiment.identity(outbox / name)
                result['replicas'].append({'arm': arm['name'], 'replica': replica, 'file': name,
                                          'before': identity, 'after': identity, 'status': 'pass',
                                          'endpoint': 'complete', 'error': None, 'snapshot': copy.deepcopy(snapshot)})
        return plan, result

    @staticmethod
    def decoded(_data, arm):
        rows = sorted(arm['rows'], key=lambda row: experiment.key_values(row, arm))
        return {'bindings': [{'row_page': 23, 'row': n, 'values': row, 'key_hex': 'abcdef'}
                             for n, row in enumerate(rows)], 'hypothesis_matches': False,
                'physical_index': {'entry_count': len(rows), 'flags': 1, 'keys': arm['fields']}}

    def test_stable_hypothesis_refutation_answers_but_order_and_replica_failures_do_not(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=self.decoded):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'answered')
            result['replicas'][0]['snapshot']['traversal'].reverse()
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            plan, result = self.fixture(outbox)
            result['replicas'][0]['snapshot']['indexes'][0]['required'] = True
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            result['replicas'].pop()
            result['error'] = 'DAO failed after mutation'
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')

    def test_identities_inventory_and_standalone_pins_are_required(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=self.decoded):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            result['replicas'][0]['before'] = {'size': 0, 'sha256': 'changed'}
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')
            (outbox / result['replicas'][0]['file']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                experiment.build_report(result, outbox, plan)
            result['replicas'][0]['replica'] = 3
            with self.assertRaisesRegex(ValueError, 'inventory'):
                experiment.build_report(result, outbox, plan)
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
