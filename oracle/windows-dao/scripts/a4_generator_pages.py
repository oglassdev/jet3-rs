#!/usr/bin/env python3
"""Independent exact-page encoders for synthetic A4 campaigns.

This module only constructs bytes. It deliberately contains no decoder,
candidate enumeration, predicate result, or analyzer import.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from a4_spec import PAGE_SIZE, PLAN

TAG_DATA = 0x01
TAG_TDEF = 0x02
TAG_EXTENDED_USAGE = 0x05
DIRECTORY_START = 10
TYPE_0_HEADER_BYTES = 5
TAG_05_HEADER_BYTES = 4
TAG_05_BITS = (PAGE_SIZE - TAG_05_HEADER_BYTES) * 8


def _u16(value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{label} is outside u16")
    return value.to_bytes(2, "little")


def _u32(value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFFFF_FFFF:
        raise ValueError(f"{label} is outside u32")
    return value.to_bytes(4, "little")


def bitmap_bytes(bits: Iterable[int], capacity_bits: int | None = None) -> bytes:
    selected = frozenset(bits)
    if any(bit < 0 for bit in selected):
        raise ValueError("bitmap indices must be nonnegative")
    required = max(selected, default=-1) + 1
    if capacity_bits is None:
        capacity_bits = required
    if capacity_bits < 0 or required > capacity_bits:
        raise ValueError("bitmap index exceeds the declared capacity")
    payload = bytearray((capacity_bits + 7) // 8)
    for bit in selected:
        payload[bit // 8] |= 1 << (bit % 8)
    return bytes(payload)


def encode_locator(layout: str, page_number: int, row_number: int) -> bytes:
    layouts = tuple(PLAN["candidate_grammars"]["h1"]["locator_layouts"])
    if layout not in layouts:
        raise ValueError("locator layout is not registered")
    if not 0 <= page_number <= 0xFF_FFFF or not 0 <= row_number <= 0xFF:
        raise ValueError("locator target is outside its serialized domain")
    page = page_number.to_bytes(3, "little")
    return page + bytes([row_number]) if layout == layouts[0] else bytes([row_number]) + page


def data_page(
    rows: Sequence[bytes],
    *,
    raw_flags: Mapping[int, int] | None = None,
    tag: int = TAG_DATA,
) -> bytes:
    """Pack rows from the end of an exact 2,048-byte page in slot order."""
    if not 0 <= tag <= 0xFF:
        raise ValueError("page tag is outside one byte")
    if len(rows) > (PAGE_SIZE - DIRECTORY_START) // 2:
        raise ValueError("row count cannot fit the directory")
    flags = raw_flags or {}
    if set(flags) - set(range(len(rows))):
        raise ValueError("row flags name a nonexistent slot")
    page = bytearray(PAGE_SIZE)
    page[0] = tag
    page[1] = 0x01
    page[8:10] = _u16(len(rows), "row count")
    end = PAGE_SIZE
    floor = DIRECTORY_START + 2 * len(rows)
    for ordinal, row in enumerate(rows):
        start = end - len(row)
        if start < floor:
            raise ValueError("rows overlap the row directory")
        flag = flags.get(ordinal, 0)
        if flag & 0x0FFF:
            raise ValueError("row flags overlap the 0x0fff offset domain")
        raw = start | flag
        if raw > 0xFFFF:
            raise ValueError("row directory entry is outside u16")
        page[start:end] = row
        offset = DIRECTORY_START + 2 * ordinal
        page[offset : offset + 2] = raw.to_bytes(2, "little")
        end = start
    return bytes(page)


def type_0_row(
    base_page: int,
    pages: Iterable[int],
    *,
    polarity: str = "set_bit_owned_in_use",
    capacity_bits: int | None = None,
) -> bytes:
    polarities = tuple(PLAN["candidate_grammars"]["h2"]["type_0_polarities"])
    if polarity not in polarities:
        raise ValueError("type-0 polarity is not registered")
    selected = frozenset(pages)
    relative = frozenset(page - base_page for page in selected)
    if any(bit < 0 for bit in relative):
        raise ValueError("type-0 page precedes its base")
    body = bytearray(bitmap_bytes(relative, capacity_bits))
    if polarity != "set_bit_owned_in_use":
        for index in range(len(body)):
            body[index] ^= 0xFF
    return b"\x00" + _u32(base_page, "type-0 base") + bytes(body)


def type_1_row(references: Sequence[int], *, slot_count: int | None = None) -> bytes:
    count = len(references) if slot_count is None else slot_count
    if count < len(references):
        raise ValueError("type-1 slot count is below the reference count")
    slots = tuple(references) + (0,) * (count - len(references))
    return b"\x01" + b"".join(_u32(value, "type-1 reference") for value in slots)


def tag_05_page(bits: Iterable[int]) -> bytes:
    page = bytearray(PAGE_SIZE)
    page[:TAG_05_HEADER_BYTES] = bytes([TAG_EXTENDED_USAGE, 0x01, 0x00, 0x00])
    page[TAG_05_HEADER_BYTES:] = bitmap_bytes(bits, TAG_05_BITS)
    return bytes(page)


def masked_tdef_page(
    signature_id: str,
    locators: Mapping[int, bytes],
    *,
    version: int = 0,
) -> bytes:
    """Encode one of the two closed H1 signatures from the plan grammar."""
    grammar = PLAN["candidate_grammars"]["h1"]
    base = grammar["table_record_signature"]
    duplicate = grammar["pair_multiple_reachability_signature"]
    allowed = {base["signature_id"]: base, duplicate["signature_id"]: duplicate}
    if signature_id not in allowed:
        raise ValueError("TDEF signature is not registered")
    record = bytearray.fromhex(base["value_hex"])
    start, end = allowed[signature_id]["record_bounds"]
    if start != 0 or end != len(record):
        raise ValueError("registered signature record bounds are inconsistent")
    record[12:14] = _u16(version, "TDEF version")
    expected_offsets = {interval[0] for interval in allowed[signature_id]["locator_holes"]}
    if set(locators) != expected_offsets:
        raise ValueError("locator offsets do not match the selected signature")
    for offset, locator in locators.items():
        if len(locator) != 4:
            raise ValueError("locator must occupy four bytes")
        record[offset : offset + 4] = locator
    if signature_id == duplicate["signature_id"]:
        equality = duplicate["equal_byte_intervals"][0]
        left, right = equality["left"], equality["right"]
        if record[slice(*left)] != record[slice(*right)]:
            raise ValueError("duplicate signature locators violate registered equality")
        inequality = duplicate["mutual_exclusion_inequality"]
        fixed = bytes.fromhex(inequality["right"]["fixed_value_hex"])
        if record[slice(*inequality["left"])] == fixed:
            raise ValueError("duplicate signature does not exclude the base signature")
    page = bytearray(PAGE_SIZE)
    page[: len(record)] = record
    page[0] = TAG_TDEF
    return bytes(page)


def catalog_row(
    *,
    stamp: int,
    object_id: int,
    kind: int,
    name: bytes,
    stored_length: int | None = None,
) -> bytes:
    """Construct a bounded synthetic catalog row without interpreting it."""
    if not all(0 <= value <= 0xFF for value in (stamp, object_id, kind)):
        raise ValueError("catalog scalar is outside one byte")
    length = len(name) if stored_length is None else stored_length
    if not 0 <= length <= 0xFF:
        raise ValueError("catalog stored length is outside one byte")
    return bytes([stamp, object_id, kind, length]) + name


def empty_page(tag: int = TAG_DATA) -> bytes:
    return data_page((), tag=tag)
