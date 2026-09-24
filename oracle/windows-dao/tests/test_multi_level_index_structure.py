from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import multi_level_index_structure as structure


def tree_image():
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


class MultiLevelIndexStructureTests(unittest.TestCase):
    def test_tree_prefixes_full_separators_links_and_cycles(self):
        image, entries = tree_image()
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
