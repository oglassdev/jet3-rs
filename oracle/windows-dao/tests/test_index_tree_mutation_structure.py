from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import index_tree_mutation_structure as structure
import test_multi_level_index as fixtures


class MutationTreeBoundsTests(unittest.TestCase):
    def test_retained_fence_accepts_deletion_but_rejects_misrouting_and_wrong_width(self):
        image, entries = fixtures.MultiLevelIndexTests().tree_image()
        # Remove key 2 from the compressed left leaf; its root separator remains.
        image[2 * 2048 + 22 + 15 // 8] &= ~(1 << (15 % 8))
        image[2 * 2048 + 2:2 * 2048 + 4] = (1800 - 9).to_bytes(2, 'little')
        nodes, actual = structure.tree(image, 1, 20)
        self.assertEqual(actual, [entries[0], entries[2]])
        self.assertEqual(nodes[0]['stale_separators'], 1)
        below = entries[0][:-1] + b'\x00'
        for separator in [below, entries[2], entries[1] + b'\x00', entries[1][:-1]]:
            damaged = bytearray(image)
            record = separator + (2).to_bytes(4, 'big')
            damaged[2048 + 22:2048 + 248] = bytes(226)
            end = len(record)
            damaged[2048 + 22 + end // 8] = 1 << (end % 8)
            damaged[2048 + 248:2048 + 248 + end] = record
            damaged[2048 + 2:2048 + 4] = (1800 - end).to_bytes(2, 'little')
            with self.subTest(separator=separator.hex()), self.assertRaises(structure.catalog.DecodeError):
                structure.tree(damaged, 1, 20)


if __name__ == '__main__':
    unittest.main()
