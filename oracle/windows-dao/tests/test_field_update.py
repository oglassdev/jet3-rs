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
spec = importlib.util.spec_from_file_location('field_update_tested', SCRIPTS / 'field_update.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class FieldUpdateTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(m.PLAN.read_text())
        self.directory = tempfile.TemporaryDirectory(prefix='field-update-tests-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = {'version': '3.0', 'tables': ['Items', 'Later'], 'relations': [],
                         'queries': [{'name': 'KeepQuery', 'sql': 'SELECT [Id], [Value] FROM [Items];', 'type': 0}],
                         'user_tables': [{'name': table['name'], 'attributes': 0, 'indexes': [],
                            'fields': [{**field, 'attributes': 1, 'required': False, 'allow_zero_length': False, 'default_value': ''} for field in self.plan['fields']],
                            'rows': copy.deepcopy(table['rows'])} for table in self.plan['tables']]}
        self.result = {'plan_sha256': m.digest(m.PLAN), 'phase': 'complete', 'error': None, 'updates': [], 'phases': {}}
        self.phases = {phase: {'document_type': 'dao_field_update_phase', 'phase': phase, 'plan_sha256': m.digest(m.PLAN),
            'error': None, 'mutation_started': phase == 'create', 'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'observations': []}
            for phase in ['create', 'observe']}
        for arm in self.plan['arms']:
            for replica in range(1, 4):
                before = after = None
                for role in ['original', 'updated']:
                    path = self.root / f"{arm['name']}-r{replica}-{role}.mdb"
                    path.write_bytes(b'original' if role == 'original' else b'updated')
                    snapshot = copy.deepcopy(self.snapshot)
                    if role == 'updated':
                        table = next(t for t in snapshot['user_tables'] if t['name'] == arm['table'])
                        column = next(i for i, c in enumerate(self.plan['fields']) if c['name'] == arm['column'])
                        next(r for r in table['rows'] if r[0] == arm['selected_id'])[column] = arm['replacement']
                    observation = {'arm': arm['name'], 'replica': replica, 'role': role, 'observation': {
                        'file': path.name, 'before': m.identity(path), 'after': m.identity(path), 'status': 'pass', 'error': None, 'snapshot': snapshot}}
                    self.phases['observe']['observations'].append(observation)
                    if role == 'original':
                        self.phases['create']['observations'].append(copy.deepcopy(observation)); before = m.identity(path)
                    else: after = m.identity(path)
                self.result['updates'].append({'arm': arm['name'], 'replica': replica, 'original_before': before, 'original_after': before,
                                               'updated': after, 'locator': {}})

    def classify(self):
        for name, phase in self.phases.items():
            path = self.root / (name + '.json'); path.write_text(json.dumps(phase))
            self.result['phases'][name] = m.identity(path)
        with patch.object(m, 'patch_check', return_value={'length': 4}):
            return m.build_report(self.result, self.root, self.plan)

    def test_complete_and_order_independent_rows(self):
        self.assertEqual(self.classify()['outcome'], 'observed_accepted')
        for phase in self.phases.values():
            for entry in phase['observations']:
                for table in entry['observation']['snapshot']['user_tables']: table['rows'].reverse()
        self.assertEqual(self.classify()['outcome'], 'observed_accepted')

    def test_incomplete_identity_and_unrelated_query_fail(self):
        original = copy.deepcopy(self.phases)
        for change in ['missing', 'query', 'payload', 'identity', 'file', 'mutation']:
            self.phases = copy.deepcopy(original)
            phase = self.phases['observe']; entry = phase['observations'][1]['observation']
            if change == 'missing': phase['observations'].pop()
            elif change == 'query': entry['snapshot']['queries'][0]['sql'] = 'SELECT 99;'
            elif change == 'payload': entry['snapshot']['user_tables'][0]['rows'][1][2] = 'wrong'
            elif change == 'identity': entry['after']['sha256'] = 'f' * 64
            elif change == 'file': entry['file'] = 'unrelated.mdb'
            else: phase['mutation_started'] = True
            self.assertEqual(self.classify()['outcome'], 'no_outcome', change)

    def test_byte_preservation_and_locator_binding(self):
        arm = self.plan['arms'][0]
        locator = {'root': 20, 'page': 1, 'slot': 0, 'column': 0}
        original = bytearray(4096); original[2149:2153] = (1).to_bytes(4, 'little', signed=True)
        updated = bytearray(original); updated[2149:2153] = arm['replacement'].to_bytes(4, 'little', signed=True)
        table = {'columns': [{'type': 'Long', 'storage': 'fixed', 'size': 4, 'fixed_offset': 0}]}
        with patch.object(m.catalog, '_discover_catalog', return_value=({}, [], [{'values': ['Items', 20]}])), \
             patch.object(m.catalog, '_ordinal', side_effect=lambda definition, name: {'Name': 0, 'Id': 1 if definition == {} else 0}[name]), \
             patch.object(m.catalog, '_definition', return_value=table), \
             patch.object(m.catalog, '_table_pages', return_value=([1], [])), \
             patch.object(m.catalog, '_table_rows', return_value=[{'values': [1], 'page': 1, 'row': 0, 'present': [True]}]), \
             patch.object(m.catalog, '_row_directory', return_value=[{'start': 100}]):
            self.assertEqual(m.patch_check(bytes(original), bytes(updated), arm, locator)['offset'], 2149)
            updated[42] = 1
            with self.assertRaisesRegex(ValueError, 'Bytes outside'): m.patch_check(bytes(original), bytes(updated), arm, locator)
            with self.assertRaisesRegex(ValueError, 'Target row'): m.patch_check(bytes(original), bytes(updated), arm, {**locator, 'slot': 1})

    def test_plan_pins_and_explicit_failure_are_not_promoted(self):
        self.result['error'] = 'phase1 failed after mutation'
        self.assertEqual(self.classify()['outcome'], 'no_outcome')
        changed = copy.deepcopy(self.plan); changed['inputs'] = {'missing-file': '0' * 64}
        with patch.object(m.PLAN.__class__, 'read_text', return_value=json.dumps(changed)):
            with self.assertRaises(FileNotFoundError): m.verify_inputs()


if __name__ == '__main__': unittest.main()
