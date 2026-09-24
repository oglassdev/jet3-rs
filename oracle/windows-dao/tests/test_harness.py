"""Focused checks of the shared harness helpers: spec expansion, keys, comparisons, decoding."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compare  # noqa: E402
import dao  # noqa: E402
import keys  # noqa: E402
import structure  # noqa: E402


class SpecExpansion(unittest.TestCase):
    def test_inheritance_replicas_and_templates(self):
        spec = dao.expand({
            'replicas': [1, 2],
            'inputs': {'base': {'template': True, 'ops': [{'op': 'sql', 'text': 'A'}]},
                       'child': {'extends': 'base', 'ops': [{'op': 'sql', 'text': 'B'}]}},
            'cases': [{'name': 'edit', 'input': 'child', 'steps': [{'request': {'operation': 'drop_table', 'table': 'T'}},
                                                                    {'request': {}, 'native': [{'sql': 'X'}, {'sql': 'Y'}]}]}],
            'creations': [{'name': 'new', 'request': {'tables': []}}],
            'creation_replicas': [1, 2],
        })
        self.assertEqual(sorted(spec['inputs']), ['child-r1', 'child-r2'])
        self.assertEqual([op['text'] for op in spec['inputs']['child-r1']['ops']], ['A', 'B'])
        self.assertEqual([c['name'] for c in spec['cases']], ['edit-r1', 'edit-r2'])
        self.assertEqual(spec['cases'][0]['native_steps'], 3)
        self.assertEqual([(c['name'], c['candidate']) for c in spec['creations']], [('new-r1', 'new'), ('new-r2', 'new')])

    def test_code_page_requests_use_host_names(self):
        step = {'command': 'schema', 'request': {'operation': 'rename_table', 'table': 'T', 'name': 'Б'}}
        self.assertEqual(dao.native_ops(step, 1251)[0]['request']['name'], 'Á')
        self.assertEqual(dao.native_ops(step)[0]['request']['name'], 'Б')


class Keys(unittest.TestCase):
    def test_numeric_and_text_order(self):
        ascending = [keys.component(v, 4, False) for v in (-5, 0, 7)]
        self.assertEqual(ascending, sorted(ascending))
        descending = [keys.component(v, 4, True) for v in (-5, 0, 7)]
        self.assertEqual(descending, sorted(descending, reverse=True))
        self.assertLess(keys.general_text(b'a', False), keys.general_text(b'b', False))
        self.assertEqual(keys.general_text(b'A', False)[:1], keys.general_text(b'a', False)[:1])


class Comparisons(unittest.TestCase):
    def test_differences_report_paths(self):
        self.assertEqual(compare.differences({'a': [1, 2]}, {'a': [1, 3]}), [('/a/1', 2, 3)])
        self.assertEqual(compare.differences([1], [1, 2]), [('/length', 1, 2)])

    def test_placements_only_for_recorded_roles(self):
        record = {'kind': 0, 'length': 1, 'start': 0, 'references': []}
        left = {'T/table/owned': {'record': record, 'members': [3]}}
        right = {'T/table/owned': {'record': record, 'members': [4]}}
        with self.assertRaises(compare.Mismatch):
            compare.placements({}, left, right)
        self.assertEqual(len(compare.placements({'placement_roles': ['T/table/owned']}, left, right)), 1)
        with self.assertRaises(compare.Mismatch):
            compare.placements({'placement_roles': {'T/table/owned': [1, 2]}}, left, right)

    def test_native_residue_is_exact(self):
        before = bytes(8)
        after = bytearray(before)
        after[3] = 1
        case = {'native_residue': [{'offset': 3, 'before': 0, 'after': 1}]}
        self.assertEqual(compare.native_residue(case, before, bytes(after), None), [{'offset': 3, 'before': 0, 'native': 1}])
        after[5] = 9
        with self.assertRaises(compare.Mismatch):
            compare.native_residue(case, before, bytes(after), None)

    def test_row_model(self):
        rows = {'key': 'id', 'update': {'2': {'code': 'x'}}, 'insert': [{'id': 3, 'code': 'n'}], 'delete': [1]}
        model = compare.row_model(rows, [{'id': 1, 'code': 'a'}, {'id': 2, 'code': 'b'}])
        self.assertEqual(model, {2: {'id': 2, 'code': 'x'}, 3: {'id': 3, 'code': 'n'}})


class Decoding(unittest.TestCase):
    def test_row_directory(self):
        page = bytearray(structure.PAGE)
        page[0] = 1
        page[8:10] = (3).to_bytes(2, 'little')
        page[10:12] = (2000).to_bytes(2, 'little')
        page[12:14] = (0xc000).to_bytes(2, 'little')
        page[14:16] = (1900 | 0x8000).to_bytes(2, 'little')
        rows = structure.directory(bytes(page), 5)
        self.assertEqual([(r['start'], r['end'], r['hidden']) for r in rows], [(2000, 2048, False), (2000, 2000, True), (1900, 2000, True)])
        # A row may not start after its predecessor.
        page[8:10] = (4).to_bytes(2, 'little')
        page[16:18] = (1950).to_bytes(2, 'little')
        with self.assertRaises(structure.DecodeError):
            structure.directory(bytes(page), 5)


if __name__ == '__main__':
    unittest.main()
