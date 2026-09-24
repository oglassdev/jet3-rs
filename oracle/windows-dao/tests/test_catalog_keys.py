from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import catalog_keys as ANALYZER  # noqa: E402

PAGE = ANALYZER.PAGE_BYTES
# The exact Alpha.LvProp payload recorded losslessly by EXP-0079.
ALPHA_LVPROP = bytes.fromhex(
    "4b4b440010000000800008005265717569726564170000000100080000000200"
    "4964090001010000010000"
)


def leaf_page(entries: list[tuple[bytes, int, int]], owner: int) -> bytes:
    page = bytearray(PAGE)
    page[0] = 4
    page[1] = 1
    page[4:8] = owner.to_bytes(4, "little")
    end = 0
    for key, row_page, row_slot in entries:
        record = key + row_page.to_bytes(3, "big") + bytes([row_slot])
        page[ANALYZER.ENTRY_AREA_OFFSET + end : ANALYZER.ENTRY_AREA_OFFSET + end + len(record)] = record
        end += len(record)
        page[22 + end // 8] |= 1 << (end % 8)
    page[2:4] = (ANALYZER.ENTRY_AREA_LENGTH - end).to_bytes(2, "little")
    return bytes(page)


def composite_key(parent: int, text: bytes) -> bytes:
    return (
        bytes([0x7F])
        + ((parent ^ 0x8000_0000) & 0xFFFF_FFFF).to_bytes(4, "big")
        + bytes([0x7F])
        + text
        + b"\x00"
    )


def catalog_row(identity: int, parent: int, name: str, row: int) -> dict[str, object]:
    values: list[object] = [identity, parent, name]
    values.extend([None] * 14)
    return {"page": 18, "row": row, "values": values}


class KeyDecodingTests(unittest.TestCase):
    def test_leaf_entry_is_lossless_and_correlates_its_catalog_row(self) -> None:
        key = bytes.fromhex("7f8f0000017f606d73696000")
        data = bytearray(23 * PAGE)
        data[9 * PAGE : 10 * PAGE] = leaf_page([(key, 18, 8)], 2)

        entries = ANALYZER.leaf_index_entries(
            bytes(data), 9, 2, [catalog_row(20, 0x0F00_0001, "Alpha", 8)]
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], key)
        parent, primary, secondary = ANALYZER.split_parent_name_key(key, "key")
        self.assertEqual(parent, 0x0F00_0001)
        self.assertEqual(primary.hex(), "606d736960")
        self.assertEqual(secondary, [])

    def test_branched_root_and_missing_locator_are_decode_errors(self) -> None:
        key = composite_key(0x0F00_0001, b"\x60\x6d")
        data = bytearray(23 * PAGE)
        data[9 * PAGE : 10 * PAGE] = leaf_page([(key, 18, 8)], 2)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "no catalog row"):
            ANALYZER.leaf_index_entries(bytes(data), 9, 2, [catalog_row(20, 1, "Alpha", 3)])

        branched = bytearray(data)
        branched[9 * PAGE + 21] = 1
        with self.assertRaisesRegex(ANALYZER.DecodeError, "not a leaf"):
            ANALYZER.leaf_index_entries(
                bytes(branched), 9, 2, [catalog_row(20, 0x0F00_0001, "Alpha", 8)]
            )

    def test_secondary_weights_are_decoded_as_a_nibble_stream(self) -> None:
        # One primary weight per name byte, then 0 3 4 5 7 6 8 0 as nibbles.
        key = b"\x7f\x8f\x00\x00\x01\x7f\x60\x60\x60" + bytes.fromhex("03457680")

        parent, primary, secondary = ANALYZER.split_parent_name_key(key, "key")

        self.assertEqual(parent, 0x0F00_0001)
        self.assertEqual(primary.hex(), "606060")
        self.assertEqual(secondary, [3, 4, 5, 7, 6, 8])

    def test_key_framing_requires_markers_a_primary_and_a_terminator(self) -> None:
        for broken, expected in (
            (b"\x00" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x00", "key markers"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x60", "no secondary section"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x00\x60", "no primary weight"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x0f\xff", "unterminated"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x03\x40\x0f", "padding is nonzero"),
        ):
            with self.assertRaisesRegex(ANALYZER.DecodeError, expected):
                ANALYZER.split_parent_name_key(broken, "key")


class PropertyFramingTests(unittest.TestCase):
    def test_recorded_alpha_payload_decomposes_into_two_chunks(self) -> None:
        chunks = ANALYZER.property_chunks(ALPHA_LVPROP)

        self.assertEqual([chunk["kind"] for chunk in chunks], [0x0080, 0x0001])
        self.assertEqual([chunk["length"] for chunk in chunks], [16, 23])
        self.assertEqual(
            chunks[0]["name_entries_hex"], [b"Required".hex()]
        )
        self.assertEqual(sum(chunk["length"] for chunk in chunks) + 4, len(ALPHA_LVPROP))

    def test_missing_magic_and_overrunning_chunk_are_decode_errors(self) -> None:
        with self.assertRaisesRegex(ANALYZER.DecodeError, "KKD magic"):
            ANALYZER.property_chunks(b"XXX\x00\x06\x00\x00\x00\x80\x00")
        with self.assertRaisesRegex(ANALYZER.DecodeError, "invalid length"):
            ANALYZER.property_chunks(b"KKD\x00" + b"\xff\x00\x00\x00\x80\x00")


if __name__ == "__main__":
    unittest.main()
