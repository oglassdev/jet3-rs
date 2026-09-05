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
spec = importlib.util.spec_from_file_location('delete_layout_tested', SCRIPTS / 'row_delete_layout.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class DeleteLayoutTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(m.PLAN.read_text())
        self.temporary = tempfile.TemporaryDirectory(prefix='delete-layout-test-'); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.result = {'document_type': 'dao_row_delete_layout_result', 'plan_sha256': m.identity(m.PLAN)['sha256'],
            'mutation_started': True, 'error': None, 'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'captures': []}
        for arm in self.plan['arms']:
            for replica in range(1, 4):
                for number, checkpoint in enumerate(self.plan['checkpoints']):
                    name = f"{arm['name']}-r{replica}-{checkpoint}.mdb"; path = self.root / name
                    data = bytearray(4096); data[2048] = 1; data[2100] = number; path.write_bytes(data)
                    snapshot = {'version': '3.0', 'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
                        'relations': [], 'queries': [], 'attributes': 0, 'indexes': [],
                        'fields': [{'name': name, 'type': 4, 'size': 4, 'attributes': 1} for name in ['Id', 'Value']],
                        'rows': m.expected_rows(arm, checkpoint, self.plan)}
                    self.result['captures'].append({'arm': arm['name'], 'replica': replica, 'checkpoint': checkpoint, 'file': name,
                        'observation': {'status': 'pass', 'error': None, 'before': m.identity(path), 'after': m.identity(path), 'snapshot': snapshot}})

    def decoded(self, data, wanted):
        return {'row_count': len(wanted), 'page_count': 2, 'rows': [{'page': 1, 'row': i, 'values': row, 'present': [True, True]} for i, row in enumerate(wanted)],
            'data_pages': [m.data_page(data, 1)], 'global_free_pages': [],
            'maps': {role: {'pages': [1]} for role in ['owned', 'available']}}

    def classify(self):
        with patch.object(m, 'observe', side_effect=self.decoded): return m.build_report(self.result, self.root, self.plan)

    def test_complete_hypothesis_false_is_answered(self):
        report = self.classify()
        self.assertEqual(report['outcome'], 'answered'); self.assertEqual(len(report['observations']), 9)
        self.assertTrue(all(not item['deleted_has_zero_length_c000'] for item in report['observations']))
        self.assertEqual(report['observations'][0]['transitions'][0]['changed_ranges'],
                         [{'offset': 2100, 'end': 2101, 'before_hex': '00', 'after_hex': '01'}])

    def test_failure_missing_identity_and_payload_are_not_promoted(self):
        original = copy.deepcopy(self.result)
        for failure in ['mutation', 'error', 'missing', 'duplicate', 'identity', 'payload', 'file']:
            self.result = copy.deepcopy(original); capture = self.result['captures'][0]
            if failure == 'mutation': self.result['mutation_started'] = False
            elif failure == 'error': self.result['error'] = 'after first mutation'
            elif failure == 'missing': self.result['captures'].pop()
            elif failure == 'duplicate': self.result['captures'].append(copy.deepcopy(capture))
            elif failure == 'identity': capture['observation']['after']['sha256'] = 'f' * 64
            elif failure == 'file': capture['file'] = 'different.mdb'
            else: capture['observation']['snapshot']['rows'][0][1] = 999
            self.assertEqual(self.classify()['outcome'], 'no_outcome', failure)

    def test_input_pin_mismatch_is_rejected(self):
        with patch.object(m, 'identity', return_value={'sha256': 'wrong'}):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'): m.verify_inputs()

    def test_whole_byte_growth_and_page_address_independent_signature(self):
        self.assertEqual(m.changed_ranges(b'abc', b'aXcYZ'), [
            {'offset': 1, 'end': 2, 'before_hex': '62', 'after_hex': '58'},
            {'offset': 3, 'end': 5, 'before_hex': '', 'after_hex': '595a'}])
        report = self.classify()['observations'][0]
        before = m.question_signature(report['checkpoints'], report['transitions'])
        moved = copy.deepcopy(report)
        for obs in moved['checkpoints'].values():
            for row in obs['rows']: row['page'] = 77
        for movement in moved['transitions']:
            for tracked in movement['tracked_data_pages']:
                tracked['page'] = 77
                for role in ['before', 'after']:
                    tracked[role]['image']['page'] = 77
                    raw = bytearray.fromhex(tracked[role]['image']['hex']); raw[4:8] = (88).to_bytes(4, 'little')
                    tracked[role]['image']['hex'] = raw.hex()
        self.assertEqual(before, m.question_signature(moved['checkpoints'], moved['transitions']))
        moved['transitions'][0]['tracked_data_pages'][0]['after']['available'] = False
        self.assertNotEqual(before, m.question_signature(moved['checkpoints'], moved['transitions']))


if __name__ == '__main__': unittest.main()
