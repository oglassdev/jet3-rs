#!/usr/bin/env python3
"""EXP-0059/0105 definition decoder with the 255-column row-format bound.

This adapter retains the original field grammar and uses separate bounds from
historical system-catalog experiments.
"""
import hashlib
from typing import Any
import system_catalog as catalog
from system_catalog import (COLUMN_RECORD, DEFINITION_PREFIX, DecodeError, KEY_SLOTS,
    LOGICAL_INDEX_RECORD, MARKERS, MAX_ITEMS, MAX_PAGES, PAGE_BYTES,
    PHYSICAL_INDEX_RECORD, PHYSICAL_TYPES, _locator, _page, _take, _text, _u16, _u32)
MAX_COLUMNS = 255

def _definition(data: bytes, root: int) -> dict[str, Any]:
    what = f"definition {root}"
    page_count = len(data) // PAGE_BYTES
    image = _page(data, root, what)
    if image[:4] != DEFINITION_PREFIX:
        raise DecodeError(f"{what}: page {root} lacks the definition prefix")
    total = _u32(image, 8, what)
    if total < 45 or total > MAX_PAGES * PAGE_BYTES:
        raise DecodeError(f"{what}: logical length {total} is out of bounds")
    logical = bytearray(image)
    pages = [root]
    following = _u32(image, 4, what)
    while following:
        if following in pages or following >= page_count:
            raise DecodeError(f"{what}: continuation reference {following} is invalid")
        continuation = _page(data, following, what)
        if continuation[:4] != DEFINITION_PREFIX:
            raise DecodeError(f"{what}: page {following} lacks the definition prefix")
        pages.append(following)
        logical += continuation[8:]
        following = _u32(continuation, 4, what)
    if len(logical) < total:
        raise DecodeError(f"{what}: chain holds {len(logical)} of {total} logical bytes")
    body = bytes(logical[:total])

    def file_offset(logical_offset: int) -> int:
        if logical_offset < PAGE_BYTES:
            return root * PAGE_BYTES + logical_offset
        index, within = divmod(logical_offset - PAGE_BYTES, PAGE_BYTES - 8)
        return pages[index + 1] * PAGE_BYTES + 8 + within

    marker = body[20]
    if marker not in MARKERS:
        raise DecodeError(f"{what}: marker byte {marker:#04x} is not 0x4e or 0x53")
    column_count = _u16(body, 21, what)
    variable_count = _u16(body, 23, what)
    if _u16(body, 25, what) != column_count:
        raise DecodeError(f"{what}: repeated column count differs")
    logical_count = _u16(body, 27, what)
    if _u16(body, 29, what) != 0:
        raise DecodeError(f"{what}: bytes [29,31) are nonzero")
    physical_count = _u16(body, 31, what)
    if column_count > MAX_COLUMNS or physical_count > MAX_ITEMS or logical_count > MAX_ITEMS:
        raise DecodeError(f"{what}: counts exceed the experiment bounds")
    maps = {"owned": _locator(body, 35, what), "available": _locator(body, 39, what)}
    offset = 43
    prefixes = []
    for index in range(physical_count):
        raw = _take(body, offset, 8, what)
        prefixes.append(
            {
                "entry_count": _u32(raw, 4, what),
                "entry_count_offset": file_offset(offset + 4),
                "prefix_hex": raw[:4].hex(),
            }
        )
        offset += 8
    columns = []
    variables_seen = 0
    next_fixed = 0
    for index in range(column_count):
        raw = _take(body, offset, COLUMN_RECORD, what)
        offset += COLUMN_RECORD
        type_name = PHYSICAL_TYPES.get(raw[0])
        if type_name is None:
            raise DecodeError(f"{what}: column {index} has unknown physical type {raw[0]}")
        if _u16(raw, 1, what) != index:
            raise DecodeError(f"{what}: column {index} ordinal field is {_u16(raw, 1, what)}")
        class_byte = raw[13]
        variable_index = _u16(raw, 3, what)
        size = _u16(raw, 16, what)
        if class_byte & 0x07 == 2:
            storage = "variable"
            if variable_index != variables_seen:
                raise DecodeError(f"{what}: column {index} variable index {variable_index} is out of sequence")
            variables_seen += 1
            fixed_offset = None
        elif class_byte & 0x07 in (3, 7):
            storage = "fixed"
            fixed_offset = _u16(raw, 14, what)
            if type_name != "Boolean":
                if fixed_offset != next_fixed:
                    raise DecodeError(f"{what}: column {index} fixed offset {fixed_offset} is not {next_fixed}")
                next_fixed += size
        else:
            raise DecodeError(f"{what}: column {index} has unsupported class {class_byte:#04x}")
        columns.append(
            {
                "class": class_byte,
                "constant": _u16(raw, 7, what),
                "context_hex": raw[9:13].hex(),
                "fixed_offset": fixed_offset,
                "name": "",
                "ordinal": index,
                "ordinal_repeat": _u16(raw, 5, what),
                "size": size,
                "storage": storage,
                "type": type_name,
                "variable_index": variable_index,
            }
        )
    if variables_seen != variable_count:
        raise DecodeError(f"{what}: {variables_seen} variable columns differ from declared {variable_count}")
    for column in columns:
        length = _take(body, offset, 1, what)[0]
        raw_name = _take(body, offset + 1, length, what)
        offset += 1 + length
        name = _text(raw_name)
        if not name:
            raise DecodeError(f"{what}: column {column['ordinal']} name is empty or not CP1252")
        column["name"] = name
    physical_indexes = []
    for index in range(physical_count):
        raw = _take(body, offset, PHYSICAL_INDEX_RECORD, what)
        offset += PHYSICAL_INDEX_RECORD
        keys = []
        for slot in range(KEY_SLOTS):
            ordinal = _u16(raw, 3 * slot, what)
            if ordinal == 0xFFFF:
                continue
            if slot != len(keys):
                raise DecodeError(f"{what}: index {index} has a hole in its key slots")
            if ordinal >= column_count:
                raise DecodeError(f"{what}: index {index} key names column {ordinal}")
            keys.append({"column": ordinal, "direction": raw[3 * slot + 2]})
        physical_indexes.append(
            {
                "entry_count": prefixes[index]["entry_count"],
                "entry_count_offset": prefixes[index]["entry_count_offset"],
                "flags": raw[38],
                "index": index,
                "keys": keys,
                "map": {"row": raw[30], "page": int.from_bytes(raw[31:34], "little")},
                "prefix_hex": prefixes[index]["prefix_hex"],
                "root": _u32(raw, 34, what),
            }
        )
    logical_indexes = []
    for index in range(logical_count):
        raw = _take(body, offset, LOGICAL_INDEX_RECORD, what)
        offset += LOGICAL_INDEX_RECORD
        logical_indexes.append(
            {
                "class": raw[19],
                "name": "",
                "physical_index": _u32(raw, 0, what),
                "raw_hex": raw.hex(),
            }
        )
    for entry in logical_indexes:
        length = _take(body, offset, 1, what)[0]
        raw_name = _take(body, offset + 1, length, what)
        offset += 1 + length
        name = _text(raw_name)
        if name is None:
            raise DecodeError(f"{what}: logical index name is not CP1252")
        entry["name"] = name
    if offset + 2 > total or body[total - 2 :] != b"\xff\xff":
        raise DecodeError(f"{what}: logical definition does not end in ff ff")
    suffix = body[offset : total - 2]
    if len(suffix) % 10:
        raise DecodeError(f"{what}: long-value map suffix is not a sequence of 10-byte groups")
    long_value_maps = []
    seen_long_value_columns: set[int] = set()
    for group_offset in range(0, len(suffix), 10):
        raw = suffix[group_offset : group_offset + 10]
        ordinal = _u16(raw, 0, what)
        if ordinal >= column_count:
            raise DecodeError(f"{what}: long-value map names column {ordinal}")
        if ordinal in seen_long_value_columns:
            raise DecodeError(f"{what}: long-value map repeats column {ordinal}")
        column = columns[ordinal]
        if column["type"] not in ("Memo", "LongBinary"):
            raise DecodeError(
                f"{what}: long-value map names non-long-value column {ordinal}"
            )
        seen_long_value_columns.add(ordinal)
        long_value_maps.append(
            {
                "available": {"row": raw[6], "page": int.from_bytes(raw[7:10], "little")},
                "column": ordinal,
                "column_name": column["name"],
                "owned": {"row": raw[2], "page": int.from_bytes(raw[3:6], "little")},
            }
        )
    return {
        "columns": columns,
        "header_unknown_hex": body[16:20].hex() + body[33:35].hex(),
        "logical_indexes": logical_indexes,
        "logical_length": total,
        "long_value_maps": long_value_maps,
        "maps": maps,
        "marker": marker,
        "pages": pages,
        "physical_indexes": physical_indexes,
        "root": root,
        "row_count": _u32(body, 12, what),
        "row_count_offset": file_offset(12),
        "suffix_hex": suffix.hex(),
    }



def tables(data):
    catalog.MAX_ROWS_PER_PAGE = 256
    definition, _, records = catalog._discover_catalog(data)
    name, id = [catalog._ordinal(definition, column) for column in ('Name', 'Id')]
    roots = {r['values'][name]: r['values'][id] for r in records}
    return {name: _definition(data, roots[name]) for name in ('Items', 'Notes')}



def notes_identity(data):
    notes = tables(data)['Notes']
    pages = set(notes['pages'])
    for locator in [*notes['maps'].values(), *(locator for group in notes['long_value_maps'] for locator in (group['owned'], group['available']))]:
        pages.add(locator['page'])
        pages.update(catalog._locator_pages(data, locator, 'Notes allocation'))
    return {str(page): hashlib.sha256(data[page * 2048:(page + 1) * 2048]).hexdigest() for page in sorted(pages)}

