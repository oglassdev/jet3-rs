import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import multi_level_index_reanalysis as secondary
import test_multi_level_index as original_tests


class ReanalysisTests(unittest.TestCase):
    def test_class_two_is_recorded_without_forcing_height_equality(self):
        image, entries = original_tests.MultiLevelIndexTests().tree_image()
        image[2048 + 21] = 2
        nodes, actual = secondary.tree(image, 1, 20)
        self.assertEqual(actual, entries)
        root = nodes[0]
        self.assertEqual((root['header_class'], root['subtree_height']), (2, 1))
        summary = secondary.height_summary([{'name': 'Rows', 'indexes': [{'root': 1, 'depth': 2, 'nodes': nodes}]}])
        self.assertFalse(summary[0]['all_classes_equal_height'])
        for offset, value in [(2048 + 21, 3), (2 * 2048 + 21, 1), (2048 + 248 + 4, 0)]:
            corrupt = bytearray(image); corrupt[offset] = value
            with self.assertRaises(secondary.catalog.DecodeError):
                secondary.tree(corrupt, 1, 20)

    def test_three_levels_derive_height_from_children(self):
        image, entries = original_tests.MultiLevelIndexTests().tree_image()
        image.extend(bytes(3 * 2048))
        image[4 * 2048:5 * 2048] = image[2048:2 * 2048]
        image[5 * 2048:7 * 2048] = image[2 * 2048:4 * 2048]
        image[4 * 2048 + 16:4 * 2048 + 20] = (6).to_bytes(4, 'little')
        image[4 * 2048 + 257:4 * 2048 + 261] = (5).to_bytes(4, 'big')
        image[5 * 2048 + 12:5 * 2048 + 16] = (6).to_bytes(4, 'little')
        image[6 * 2048 + 8:6 * 2048 + 12] = (5).to_bytes(4, 'little')
        # Reuse the two-subtree shape with distinct greater right-hand keys.
        for page in [4, 5, 6]:
            image[page * 2048 + 248] = 0x80
        image[2 * 2048 + 12:2 * 2048 + 16] = (3).to_bytes(4, 'little')
        image[3 * 2048 + 12:3 * 2048 + 16] = (5).to_bytes(4, 'little')
        image[5 * 2048 + 8:5 * 2048 + 12] = (3).to_bytes(4, 'little')
        image[4 * 2048 + 8:4 * 2048 + 12] = (1).to_bytes(4, 'little')
        image[2048 + 12:2048 + 16] = (4).to_bytes(4, 'little')
        new = bytearray(2048); new[:2] = b'\x03\x01'; new[21] = 2
        new[4:8] = (20).to_bytes(4, 'little'); new[16:20] = (4).to_bytes(4, 'little')
        separator = entries[-1] + (1).to_bytes(4, 'big')
        new[248:261] = separator; new[22 + 13 // 8] = 1 << (13 % 8)
        new[2:4] = (1800 - 13).to_bytes(2, 'little')
        image.extend(new)
        nodes, _ = secondary.tree(image, 7, 20)
        self.assertEqual(nodes[0]['subtree_height'], 2)
        self.assertTrue(all(node['header_class'] == node['subtree_height'] for node in nodes))

    def test_secondary_outcome_preserves_original_and_gates_on_controls(self):
        original_tests.MultiLevelIndexTests.setUpClass()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            initial_path, result = original_tests.MultiLevelIndexTests().fixture(source)
            initial = json.loads(initial_path.read_text())
            old = {'outcomes': {arm: 'no_outcome' for arm in secondary.original.ARMS}}
            (source / 'report.json').write_text(json.dumps(old))
            plan = {'retained': {'result.json': {}, 'report.json': {}}}
            def analyze():
                (source / 'result.json').write_text(json.dumps(result))
                with patch.object(secondary.original, 'PLAN', initial_path), patch.object(secondary, 'PLAN', initial_path), patch.object(secondary.original.structure, 'observe', return_value=[]):
                    return secondary.build_report(source, plan, initial)
            report = analyze()
            self.assertEqual(report['original_outcomes'], old['outcomes'])
            self.assertEqual(set(report['outcomes'].values()), {'observed_accepted'})
            result['replicas'][0]['control']['status'] = 'fail'
            self.assertEqual(set(analyze()['outcomes'].values()), {'no_outcome'})
            result['replicas'][0]['control']['status'] = 'pass'
            result['error'] = 'unexpected original acquisition failure'
            self.assertEqual(set(analyze()['outcomes'].values()), {'no_outcome'})

    def test_input_drift_rejected_and_original_directory_cannot_receive_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            (root / 'input.py').write_text('changed')
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'inputs': {'input.py': '0' * 64}}))
            with patch.object(secondary, 'ROOT', root), patch.object(secondary, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'input pin mismatch'):
                    secondary.verify(source)
            with patch.object(secondary, 'verify', return_value=({}, {})):
                with self.assertRaisesRegex(ValueError, 'outside'):
                    secondary.analyze(source, source / 'secondary-report.json')
            self.assertEqual(list(source.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
