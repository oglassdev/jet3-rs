#!/usr/bin/env python3
"""A4 page codec using the checked provenance entry "Secondary documentation of Jet page, row-slot, and usage-map primitives".

Encoders build exact 2,048-byte pages; decoders read bytes back under the
plan's registered rules (row_directory_source, row_boundary_rule,
map_row_layouts). Nothing here carries a verdict: decoders return structure or
a typed failure reason and the evaluator decides.

A4 rule | implementation
--- | ---
Row count at [8,10), offsets at [10+2i,12+2i), masks 0x1fff/0x0fff, flags 0x8000/0x4000 | :func:`decode_directory`
start/end rule, 10+2*row_count <= start < end <= 2048, nonoverlap in directory order | :func:`validate_directory`
Type-0 tag 00 + u32 base + LSB-first bitmap; type-1 tag 01 + u32 slots | :func:`decode_map_row`
Tag-05 header [0,4) + 16,352 LSB-first bits | :func:`tag05_bits`
Locator layouts u24le_page_then_u8_row / u8_row_then_u24le_page | :func:`decode_locator`
"""

from __future__ import annotations

from dataclasses import dataclass

from a4_spec import DELETED_FLAG, OVERFLOW_FLAG, PAGE_SIZE, TAG05_BITS

DIRECTORY_START = 10
TYPE0_HEADER = 5
TAG_DATA, TAG_TDEF, TAG_EXTENDED = 0x01, 0x02, 0x05


def tag_of(page: bytes) -> int:
    return page[0]


# ----------------------------------------------------------------------------- encoders

def bitmap_bytes(bits: set[int], capacity_bits: int | None = None) -> bytes:
    if capacity_bits is None:
        capacity_bits = (max(bits) + 1) if bits else 0
    out = bytearray((capacity_bits + 7) // 8)
    for bit in bits:
        if bit < 0 or bit >= len(out) * 8:
            raise ValueError(f"bit {bit} outside capacity {len(out) * 8}")
        out[bit >> 3] |= 1 << (bit & 7)
    return bytes(out)


def type0_row(base: int, pages: set[int], polarity: str = "set_bit_owned_in_use") -> bytes:
    bits = {p - base for p in pages}
    if any(b < 0 for b in bits):
        raise ValueError("page below base")
    body = bitmap_bytes(bits)
    if polarity != "set_bit_owned_in_use":
        body = bytes(b ^ 0xFF for b in body)
    return bytes([0x00]) + base.to_bytes(4, "little") + body


def type1_row(references: list[int], slot_count: int) -> bytes:
    if len(references) > slot_count:
        raise ValueError("more references than slots")
    slots = list(references) + [0] * (slot_count - len(references))
    return bytes([0x01]) + b"".join(s.to_bytes(4, "little") for s in slots)


def data_page(rows: list[bytes], flags: dict[int, int] | None = None, tag: int = TAG_DATA) -> bytes:
    """Rows are packed from the page end in slot order: slot 0 ends at 2048."""
    page = bytearray(PAGE_SIZE)
    page[0], page[1] = tag, 0x01
    page[8:10] = len(rows).to_bytes(2, "little")
    end = PAGE_SIZE
    for slot, row in enumerate(rows):
        start = end - len(row)
        if start < DIRECTORY_START + 2 * len(rows):
            raise ValueError("rows overflow the page")
        page[start:end] = row
        raw = start | (flags or {}).get(slot, 0)
        page[DIRECTORY_START + 2 * slot: DIRECTORY_START + 2 * slot + 2] = raw.to_bytes(2, "little")
        end = start
    return bytes(page)


def tag05_page(bits: set[int]) -> bytes:
    page = bytearray(PAGE_SIZE)
    page[0:4] = bytes([TAG_EXTENDED, 0x01, 0x00, 0x00])
    page[4:] = bitmap_bytes(bits, TAG05_BITS)
    return bytes(page)


def encode_locator(layout: str, page: int, row: int) -> bytes:
    if layout == "u24le_page_then_u8_row":
        return page.to_bytes(3, "little") + bytes([row])
    if layout == "u8_row_then_u24le_page":
        return bytes([row]) + page.to_bytes(3, "little")
    raise ValueError(layout)


# ----------------------------------------------------------------------------- decoders

def decode_locator(window: bytes, layout: str) -> tuple[int, int]:
    """Return (page, row) for a four-byte window under a registered layout."""
    if layout == "u24le_page_then_u8_row":
        return int.from_bytes(window[0:3], "little"), window[3]
    if layout == "u8_row_then_u24le_page":
        return int.from_bytes(window[1:4], "little"), window[0]
    raise ValueError(layout)


@dataclass(frozen=True)
class Slot:
    ordinal: int
    raw: int
    start: int
    end: int
    deleted: bool
    overflow: bool


def row_count(page: bytes) -> int:
    return int.from_bytes(page[8:10], "little")


def decode_directory(page: bytes, mask: int) -> list[Slot]:
    """Decode every directory slot under one offset mask; no validity judgement."""
    count = row_count(page)
    slots: list[Slot] = []
    end = PAGE_SIZE
    for ordinal in range(count):
        at = DIRECTORY_START + 2 * ordinal
        raw = int.from_bytes(page[at: at + 2], "little") if at + 2 <= PAGE_SIZE else 0
        start = raw & mask
        slots.append(Slot(ordinal, raw, start, end, bool(raw & DELETED_FLAG), bool(raw & OVERFLOW_FLAG)))
        end = start
    return slots


def validate_directory(slots: list[Slot]) -> str | None:
    """Complete checked row-directory validation; returns the first violation or None."""
    floor = DIRECTORY_START + 2 * len(slots)
    previous_start = PAGE_SIZE
    for slot in slots:
        if slot.start < floor:
            return f"slot {slot.ordinal} start {slot.start} overlaps directory bytes below {floor}"
        if not slot.start < slot.end <= PAGE_SIZE:
            return f"slot {slot.ordinal} bounds [{slot.start},{slot.end}) are not increasing within the page"
        if slot.start >= previous_start:
            return f"slot {slot.ordinal} start {slot.start} does not precede slot {slot.ordinal - 1}"
        previous_start = slot.start
    return None


@dataclass(frozen=True)
class MapRow:
    kind: int  # 0 or 1
    base: int | None
    bitmap: bytes
    slots: tuple[int, ...]

    def type0_pages(self, polarity: str) -> set[int]:
        assert self.kind == 0 and self.base is not None
        want = 1 if polarity == "set_bit_owned_in_use" else 0
        out = set()
        for index in range(len(self.bitmap) * 8):
            if ((self.bitmap[index >> 3] >> (index & 7)) & 1) == want:
                out.add(self.base + index)
        return out


def decode_map_row(row: bytes) -> MapRow | str:
    """Decode a located row as a map row; a string is the plan-registered rejection reason."""
    if not row:
        return "empty row"
    tag = row[0]
    if tag == 0x00:
        if len(row) < TYPE0_HEADER:
            return "type-0 row shorter than tag plus u32 base"
        return MapRow(0, int.from_bytes(row[1:5], "little"), bytes(row[5:]), ())
    if tag == 0x01:
        if (len(row) - 1) % 4 != 0:
            return "type-1 payload length is not divisible by four"
        slots = tuple(int.from_bytes(row[1 + 4 * i: 5 + 4 * i], "little") for i in range((len(row) - 1) // 4))
        return MapRow(1, None, b"", slots)
    return f"row tag {tag:02x} is neither 00 nor 01"


def tag05_bits(page: bytes) -> list[int]:
    """Indices of set bits in a tag-05 page bitmap [4,2048), LSB-first."""
    out = []
    body = page[4:]
    for byte_index, value in enumerate(body):
        if value:
            for bit in range(8):
                if value >> bit & 1:
                    out.append(byte_index * 8 + bit)
    return out


def absolute_page(formula: str, slot_ordinal: int, reference: int, bit_index: int) -> int:
    if formula == "slot_ordinal_times_16352_plus_bit_index":
        return slot_ordinal * TAG05_BITS + bit_index
    if formula == "referenced_page_times_16352_plus_bit_index":
        return reference * TAG05_BITS + bit_index
    if formula == "slot_ordinal_times_16352_plus_bit_index_minus_one":
        return slot_ordinal * TAG05_BITS + bit_index - 1
    if formula == "slot_ordinal_times_16352_plus_bit_index_plus_one":
        return slot_ordinal * TAG05_BITS + bit_index + 1
    raise ValueError(formula)
