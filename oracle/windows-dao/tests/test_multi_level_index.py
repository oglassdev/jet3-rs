import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import multi_level_index as experiment
import multi_level_index_structure as structure


class MultiLevelIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(experiment.PLAN.read_text())

    def test_complete_duplicate_traversal_and_seek_allow_only_matching_rows(self):
        tables = self.plan['arms']['relationship']
        snapshot = experiment.expected_snapshot('relationship', tables)
        child = snapshot['user_tables'][1]
        child['traversal'][0], child['traversal'][1] = child['traversal'][1], child['traversal'][0]
        child['seek'][1]['row'] = child['traversal'][1]
        self.assertTrue(experiment.normalize(snapshot, tables)[0])
        child['seek'][1]['row'] = [999, 0]
        self.assertFalse(experiment.normalize(snapshot, tables)[0])
        snapshot = experiment.expected_snapshot('relationship', tables)
        snapshot['user_tables'][1]['traversal'].pop()
        self.assertFalse(experiment.normalize(snapshot, tables)[0])

    def test_direction_payload_types_and_missing_seek_are_checked(self):
        tables = self.plan['arms']['composite']
        snapshot = experiment.expected_snapshot('composite', tables)
        self.assertTrue(experiment.normalize(snapshot, tables)[0])
        snapshot['user_tables'][1]['traversal'].reverse()
        self.assertFalse(experiment.normalize(snapshot, tables)[0])
        snapshot = experiment.expected_snapshot('composite', tables)
        snapshot['user_tables'][1]['rows'][0][2] = True
        self.assertFalse(experiment.normalize(snapshot, tables)[0])
        snapshot = experiment.expected_snapshot('composite', tables)
        snapshot['user_tables'][1]['seek'][-1]['row'] = snapshot['user_tables'][1]['rows'][0]
        self.assertFalse(experiment.normalize(snapshot, tables)[0])

    def test_analysis_and_preflight_share_input_pin_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'input.py').write_text('changed')
            plan = root / 'plan.json'
            plan.write_text(json.dumps(dict(self.plan, inputs={'input.py': '0' * 64})))
            with patch.object(experiment, 'ROOT', root), patch.object(experiment, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                    experiment.preflight(root)
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                    experiment.analyze(root)

    def fixture(self, root):
        plan = copy.deepcopy(self.plan)
        plan['inputs'] = {}
        result = dict(document_type='dao_multi_level_index_result', development_only=True,
                      environment={'process_bits': 32, 'provider': 'DAO.DBEngine.36'},
                      mutation_started=True, error=None, replicas=[])
        for arm, tables in plan['arms'].items():
            plan['candidates'][arm] = {'size': 1, 'sha256': experiment.hashlib.sha256(b'x').hexdigest()}
            for replica in range(1, 4):
                pair = dict(arm=arm, replica=replica)
                for role in ('candidate', 'control'):
                    (root / f'{arm}-{role}-r{replica}.mdb').write_bytes(b'x')
                    pair[role] = dict(before=plan['candidates'][arm], after=plan['candidates'][arm], status='pass', endpoint='complete', error=None,
                                      snapshot=experiment.expected_snapshot(arm, tables))
                result['replicas'].append(pair)
        plan_path = root / 'plan.json'
        plan_path.write_text(json.dumps(plan))
        result['plan_sha256'] = experiment.digest(plan_path)
        return plan_path, result

    def test_classifier_acceptance_failure_and_incomplete_control_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, result = self.fixture(root)
            def analyze():
                (root / 'result.json').write_text(json.dumps(result))
                with patch.object(experiment, 'PLAN', plan), patch.object(structure, 'observe', return_value=[]):
                    return experiment.analyze(root)['outcomes']
            self.assertEqual(set(analyze().values()), {'observed_accepted'})
            result['error'] = 'unexpected mutation failure'
            self.assertEqual(set(analyze().values()), {'no_outcome'})
            result['error'] = None
            result['replicas'][-1]['control']['status'] = 'fail'
            self.assertEqual(set(analyze().values()), {'no_outcome'})
            result['replicas'][-1]['control']['status'] = 'pass'
            for pair in result['replicas'][:3]:
                pair['candidate']['snapshot']['version'] = 'wrong'
            self.assertEqual(analyze()['primary'], 'not_observed_accepted')
            result['replicas'][0]['candidate']['snapshot']['version'] = '3.0'
            self.assertEqual(analyze()['primary'], 'no_outcome')
            (root / 'primary-candidate-r1.mdb').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Retained identity mismatch'):
                analyze()

    def tree_image(self):
        image = bytearray(4 * 2048)
        entries = [b'\x7f\x80\x00\x00' + bytes([key]) + b'\x00\x00\x06' + bytes([key]) for key in (1, 2, 3)]
        def page(number, records, branch=False, previous=0, following=0, tail=0, prefix=0):
            raw = bytearray(2048)
            raw[0:2] = bytes([3 if branch else 4, 1]); raw[21] = int(branch); raw[20] = prefix
            for offset, value in [(4, 20), (8, previous), (12, following), (16, tail)]:
                raw[offset:offset + 4] = value.to_bytes(4, 'little')
            start = prefix
            raw[248:248 + prefix] = records[0][:prefix]
            for record in records:
                suffix = record[prefix:]; end = start + len(suffix)
                raw[248 + start:248 + end] = suffix
                raw[22 + end // 8] |= 1 << (end % 8)
                start = end
            raw[2:4] = (1800 - start).to_bytes(2, 'little')
            image[number * 2048:(number + 1) * 2048] = raw
        page(2, entries[:2], following=3, prefix=3)
        page(3, entries[2:], previous=2)
        page(1, [entries[1] + (2).to_bytes(4, 'big')], branch=True, tail=3)
        return image, entries

    def test_tree_prefixes_full_separators_links_and_cycles(self):
        image, entries = self.tree_image()
        nodes, actual = structure.tree(image, 1, 20)
        self.assertEqual(actual, entries)
        self.assertEqual(len(nodes), 3)
        for offset in [2048 + 248 + 4, 2 * 2048 + 12, 2048 + 16]:
            changed = bytearray(image); changed[offset] ^= 1
            with self.assertRaises(structure.catalog.DecodeError):
                structure.tree(changed, 1, 20)

    def test_indirect_control_maps_decode_members_and_reject_outside_bits(self):
        data = bytearray(2 * 2048)
        data[2048:2052] = b'\x05\x01\x00\x00'
        data[2052] = 2
        record = b'\x01' + (1).to_bytes(4, 'little')
        with patch.object(structure.catalog, '_locator_row', return_value=record):
            self.assertEqual(structure.map_pages(data, {}, 'test'), {1})
            data[2052] = 4
            with self.assertRaisesRegex(structure.catalog.DecodeError, 'outside image'):
                structure.map_pages(data, {}, 'test')

    def test_declared_directory_limit_accepts_more_than_64_rows(self):
        image = bytearray(2048)
        image[8:10] = (256).to_bytes(2, 'little')
        for slot in range(256):
            image[10 + 2 * slot:12 + 2 * slot] = (2048 - slot - 1).to_bytes(2, 'little')
        self.assertEqual(len(structure.catalog._row_directory(image, 1)), 256)
        self.assertEqual(structure.catalog.MAX_ROWS_PER_PAGE, 1019)


if __name__ == '__main__':
    unittest.main()
