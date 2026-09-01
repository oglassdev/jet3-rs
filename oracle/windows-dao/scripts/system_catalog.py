#!/usr/bin/env python3
"""Validate and decode one bounded local system-catalog experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
from typing import Any


PAGE_BYTES = 2048
MAX_PAGES = 64
MAX_REPLICAS = 3
MAX_TABLES = 16
MAX_COLUMNS = 64
MAX_ROWS_PER_PAGE = 64
MAX_PROPERTY_CHARS = 256
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 64
MAX_TEXT = 512
CHECKPOINT_NAMES = ("empty", "table1", "table2", "query", "relationship")
DOCUMENT_TYPE = "dao_system_catalog_job_result"
LONG_VALUE_DOCUMENT_TYPE = "dao_long_value_maps_job_result"
LONG_VALUE_FOLLOWUP_DOCUMENT_TYPE = "dao_long_value_maps_followup_job_result"
LONG_VALUE_CHECKPOINT_NAMES = ("empty", "table", "row")
DEFINITION_PREFIX = b"\x02\x01\x56\x43"
LONG_VALUE_OWNER = b"LVAL"
SYSTEM_FLAG = 0x80000000
PAGE0_COUNTER = 1538
PHYSICAL_TYPES = {
    1: "Boolean",
    2: "Byte",
    3: "Integer",
    4: "Long",
    5: "Currency",
    6: "Single",
    7: "Double",
    8: "Date",
    9: "Binary",
    10: "Text",
    11: "LongBinary",
    12: "Memo",
    15: "GUID",
}
MARKERS = (0x4E, 0x53)
COLUMN_RECORD = 18
PHYSICAL_INDEX_RECORD = 39
LOGICAL_INDEX_RECORD = 20
KEY_SLOTS = 10
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_MDB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}[.]mdb$", re.IGNORECASE)
QUESTION_NAMES = ("Q1", "Q2", "Q3", "Q4")


class AnalysisError(ValueError):
    """The producer result is malformed or fails an integrity check."""


class DecodeError(ValueError):
    """Checkpoint bytes do not decode under a pinned hypothesis."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the four-MiB bound")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"cannot load job result: {error}") from error
    if not isinstance(document, dict):
        raise AnalysisError("job result must be an object")
    return document


# --- producer JSON validation -------------------------------------------------


def _expect_keys(
    value: Any, required: set[str], optional: set[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{location} must be an object")
    missing = required - set(value)
    unexpected = set(value) - required - optional
    if missing:
        raise AnalysisError(f"{location} is missing fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise AnalysisError(
            f"{location} has unexpected fields: {', '.join(sorted(unexpected))}"
        )
    return value


def _integer(value: Any, location: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AnalysisError(
            f"{location} must be an integer between {minimum} and {maximum}"
        )
    return value


def _optional_integer(value: Any, location: str, minimum: int, maximum: int) -> int | None:
    return None if value is None else _integer(value, location, minimum, maximum)


def _string(value: Any, location: str, *, maximum: int = MAX_TEXT, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value) or len(value) > maximum:
        raise AnalysisError(f"{location} must be a string of at most {maximum} characters")
    return value


def _optional_string(value: Any, location: str, *, maximum: int = MAX_TEXT) -> str | None:
    return None if value is None else _string(value, location, maximum=maximum, empty=True)


def _optional_number(value: Any, location: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AnalysisError(f"{location} must be a finite number or null")
    return float(value)


def _digest(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{location} must be a lowercase SHA-256 digest")
    return value


def _database(value: Any, location: str) -> str:
    if not isinstance(value, str) or not SAFE_MDB.fullmatch(value):
        raise AnalysisError(f"{location} must be an MDB basename")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise AnalysisError(f"{location} must not contain a path")
    return value


def _size(value: Any, location: str) -> int:
    size = _integer(value, location, PAGE_BYTES, MAX_PAGES * PAGE_BYTES)
    if size % PAGE_BYTES:
        raise AnalysisError(f"{location} is not an exact sequence of 2-KiB pages")
    return size


def _list(value: Any, location: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise AnalysisError(f"{location} must be an array of at most {maximum} entries")
    return value


def _read_file(root: Path, database: str, size: int, digest: str, location: str) -> bytes:
    path = root / database
    if not path.is_file() or path.is_symlink():
        raise AnalysisError(f"{location}.database is not an adjacent regular file")
    data = path.read_bytes()
    if len(data) != size:
        raise AnalysisError(f"{location}.database size differs from metadata")
    if sha256(data) != digest:
        raise AnalysisError(f"{location}.database digest differs from metadata")
    return data


def _dao_item(
    value: Any, location: str, required: set[str], optional: set[str]
) -> dict[str, Any]:
    item = _expect_keys(value, required | {"name", "error"}, optional, location)
    _string(item["name"], f"{location}.name")
    _optional_string(item["error"], f"{location}.error", maximum=2048)
    return item


def _dao(value: Any, location: str) -> dict[str, Any]:
    item = _expect_keys(
        value,
        {"tabledefs", "containers", "querydefs", "relations", "properties"},
        set(),
        location,
    )
    for index, raw in enumerate(_list(item["tabledefs"], f"{location}.tabledefs", MAX_TABLES)):
        here = f"{location}.tabledefs[{index}]"
        entry = _dao_item(raw, here, {"attributes", "date_created", "last_updated"}, set())
        _optional_integer(entry["attributes"], f"{here}.attributes", -(1 << 31), (1 << 31) - 1)
        _optional_number(entry["date_created"], f"{here}.date_created")
        _optional_number(entry["last_updated"], f"{here}.last_updated")
    for index, raw in enumerate(_list(item["containers"], f"{location}.containers", MAX_ITEMS)):
        here = f"{location}.containers[{index}]"
        entry = _dao_item(raw, here, {"owner", "documents"}, set())
        _optional_string(entry["owner"], f"{here}.owner")
        for position, document in enumerate(
            _list(entry["documents"], f"{here}.documents", MAX_ITEMS)
        ):
            document_item = _dao_item(document, f"{here}.documents[{position}]", {"owner"}, set())
            _optional_string(document_item["owner"], f"{here}.documents[{position}].owner")
    for index, raw in enumerate(_list(item["querydefs"], f"{location}.querydefs", MAX_ITEMS)):
        here = f"{location}.querydefs[{index}]"
        entry = _dao_item(raw, here, {"sql", "date_created", "last_updated"}, set())
        _optional_string(entry["sql"], f"{here}.sql", maximum=8192)
        _optional_number(entry["date_created"], f"{here}.date_created")
        _optional_number(entry["last_updated"], f"{here}.last_updated")
    for index, raw in enumerate(_list(item["relations"], f"{location}.relations", MAX_ITEMS)):
        here = f"{location}.relations[{index}]"
        entry = _dao_item(
            raw, here, {"table", "foreign_table", "attributes", "fields"}, set()
        )
        _optional_string(entry["table"], f"{here}.table")
        _optional_string(entry["foreign_table"], f"{here}.foreign_table")
        _optional_integer(entry["attributes"], f"{here}.attributes", -(1 << 31), (1 << 31) - 1)
        for position, field in enumerate(_list(entry["fields"], f"{here}.fields", MAX_ITEMS)):
            pair = _expect_keys(
                field, {"name", "foreign_name"}, set(), f"{here}.fields[{position}]"
            )
            _optional_string(pair["name"], f"{here}.fields[{position}].name")
            _optional_string(pair["foreign_name"], f"{here}.fields[{position}].foreign_name")
    for index, raw in enumerate(_list(item["properties"], f"{location}.properties", MAX_ITEMS)):
        here = f"{location}.properties[{index}]"
        entry = _dao_item(raw, here, {"type", "value"}, set())
        _optional_integer(entry["type"], f"{here}.type", -(1 << 31), (1 << 31) - 1)
        _optional_string(entry["value"], f"{here}.value", maximum=MAX_PROPERTY_CHARS)
    return item


def _checkpoint(value: Any, location: str, root: Path) -> tuple[dict[str, Any], bytes]:
    item = _expect_keys(
        value,
        {"name", "database", "size", "sha256", "sha256_after_metadata", "dao"},
        set(),
        location,
    )
    if item["name"] not in CHECKPOINT_NAMES:
        raise AnalysisError(f"{location}.name is not preregistered")
    database = _database(item["database"], f"{location}.database")
    size = _size(item["size"], f"{location}.size")
    digest = _digest(item["sha256"], f"{location}.sha256")
    after = _digest(item["sha256_after_metadata"], f"{location}.sha256_after_metadata")
    dao = _dao(item["dao"], f"{location}.dao")
    data = _read_file(root, database, size, digest, location)
    return {
        "database": database,
        "dao": dao,
        "metadata_open_repaired": after != digest,
        "name": item["name"],
        "sha256": digest,
        "size": size,
    }, data


def _replica(value: Any, location: str, root: Path) -> dict[str, Any]:
    item = _expect_keys(value, {"replica", "status", "error", "checkpoints"}, set(), location)
    number = _integer(item["replica"], f"{location}.replica", 1, MAX_REPLICAS)
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError(f"{location}.status must be pass or fail")
    error = _optional_string(item["error"], f"{location}.error", maximum=2048)
    raw_checkpoints = _list(item["checkpoints"], f"{location}.checkpoints", len(CHECKPOINT_NAMES))
    if item["status"] == "pass" and (error is not None or len(raw_checkpoints) != len(CHECKPOINT_NAMES)):
        raise AnalysisError(f"{location} passed without every checkpoint and a null error")
    checkpoints: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}
    databases: set[str] = set()
    for index, raw in enumerate(raw_checkpoints):
        checkpoint, data = _checkpoint(raw, f"{location}.checkpoints[{index}]", root)
        if checkpoint["name"] != CHECKPOINT_NAMES[index]:
            raise AnalysisError(f"{location}.checkpoints[{index}] is out of preregistered order")
        if checkpoint["database"] in databases:
            raise AnalysisError(f"{location}.checkpoints repeats a database")
        databases.add(checkpoint["database"])
        checkpoints.append(checkpoint)
        images[checkpoint["name"]] = data
    return {
        "checkpoints": checkpoints,
        "error": error,
        "images": images,
        "replica": number,
        "status": item["status"],
    }


# --- byte-level decoding ------------------------------------------------------


def _take(data: bytes, offset: int, length: int, what: str) -> bytes:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise DecodeError(f"{what}: bytes [{offset},{offset + length}) exceed {len(data)}")
    return data[offset : offset + length]


def _u16(data: bytes, offset: int, what: str) -> int:
    return int.from_bytes(_take(data, offset, 2, what), "little")


def _u32(data: bytes, offset: int, what: str) -> int:
    return int.from_bytes(_take(data, offset, 4, what), "little")


def _page(data: bytes, page: int, what: str) -> bytes:
    if page < 0 or page >= len(data) // PAGE_BYTES:
        raise DecodeError(f"{what}: page {page} is outside the {len(data) // PAGE_BYTES}-page image")
    return data[page * PAGE_BYTES : (page + 1) * PAGE_BYTES]


def _text(raw: bytes) -> str | None:
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return None


def _row_directory(image: bytes, page: int) -> list[dict[str, Any]]:
    what = f"page {page} row directory"
    count = _u16(image, 8, what)
    if count > MAX_ROWS_PER_PAGE:
        raise DecodeError(f"{what}: {count} rows exceed the bound of {MAX_ROWS_PER_PAGE}")
    directory_end = 10 + 2 * count
    rows = []
    previous = PAGE_BYTES
    for index in range(count):
        raw = _u16(image, 10 + 2 * index, what)
        offset = raw & 0x1FFF
        if raw & 0x2000:
            raise DecodeError(f"{what}: row {index} carries the unknown flag 0x2000")
        if offset < directory_end or offset > previous:
            raise DecodeError(f"{what}: row {index} start {offset} is out of bounds")
        rows.append(
            {
                "row": index,
                "start": offset,
                "end": previous,
                "hidden": bool(raw & 0x8000),
                "overflow": bool(raw & 0x4000),
            }
        )
        previous = offset
    return rows


def _map_pages(raw: bytes, page_count: int, what: str, *, bounded: bool) -> set[int]:
    if len(raw) < 5:
        raise DecodeError(f"{what}: map record is shorter than five bytes")
    if raw[0] == 1:
        raise DecodeError(f"{what}: extended (type 1) map records are outside this experiment")
    if raw[0] != 0:
        raise DecodeError(f"{what}: unknown map record type {raw[0]}")
    start = _u32(raw, 1, what)
    pages: set[int] = set()
    for index, byte in enumerate(raw[5:]):
        for bit in range(8):
            if byte >> bit & 1:
                page = start + index * 8 + bit
                if page >= page_count:
                    if bounded:
                        raise DecodeError(f"{what}: map names page {page} beyond the image")
                    continue
                pages.add(page)
    return pages


def _locator(data: bytes, offset: int, what: str) -> dict[str, int]:
    raw = _u32(data, offset, what)
    return {"row": raw & 0xFF, "page": raw >> 8}


def _locator_row(data: bytes, locator: dict[str, int], what: str) -> bytes:
    image = _page(data, locator["page"], what)
    if image[0] != 1:
        raise DecodeError(f"{what}: map page {locator['page']} has tag {image[0]}")
    rows = _row_directory(image, locator["page"])
    if locator["row"] >= len(rows):
        raise DecodeError(f"{what}: map page {locator['page']} has no row {locator['row']}")
    entry = rows[locator["row"]]
    if entry["hidden"] or entry["overflow"]:
        raise DecodeError(f"{what}: map row {locator['row']} on page {locator['page']} is not active")
    return image[entry["start"] : entry["end"]]


def _locator_pages(data: bytes, locator: dict[str, int], what: str) -> set[int]:
    return _map_pages(_locator_row(data, locator, what), len(data) // PAGE_BYTES, what, bounded=True)


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


def _row_layout(columns: list[dict[str, Any]]) -> tuple[int, int]:
    fixed_end = 0
    variable_count = 0
    for column in columns:
        if column["storage"] == "variable":
            variable_count += 1
        elif column["type"] != "Boolean":
            fixed_end = max(fixed_end, column["fixed_offset"] + column["size"])
    return 1 + fixed_end, variable_count


def _decode_value(column: dict[str, Any], field: bytes | None, present: bool) -> Any:
    type_name = column["type"]
    if type_name == "Boolean":
        return present
    if field is None:
        return None
    if type_name in ("Long", "Integer", "Byte"):
        width = {"Long": 4, "Integer": 2, "Byte": 1}[type_name]
        if len(field) == width:
            return int.from_bytes(field, "little", signed=type_name != "Byte")
    elif type_name == "Date":
        if len(field) == 8:
            number = struct.unpack("<d", field)[0]
            if math.isfinite(number):
                return number
    elif type_name == "Text":
        text = _text(field)
        if text is not None:
            return text
    elif type_name == "Binary":
        return field.hex()
    elif type_name in ("LongBinary", "Memo"):
        return {"inline_length": len(field), "long_value_header_hex": field[:12].hex()}
    return {"raw_hex": field.hex()}


def _decode_row(raw: bytes, columns: list[dict[str, Any]], what: str) -> dict[str, Any]:
    count = len(columns)
    fixed_boundary, variable_count = _row_layout(columns)
    null_length = (count + 7) // 8
    if len(raw) < 1 + null_length:
        raise DecodeError(f"{what}: row is shorter than its header and presence map")
    if raw[0] != count:
        raise DecodeError(f"{what}: row column count {raw[0]} differs from schema {count}")
    null_start = len(raw) - null_length
    used = count % 8
    if used and raw[-1] & (0xFF ^ ((1 << used) - 1)):
        raise DecodeError(f"{what}: unused presence bits are nonzero")
    present = [bool(raw[null_start + ordinal // 8] >> (ordinal % 8) & 1) for ordinal in range(count)]
    if variable_count == 0:
        if null_start != fixed_boundary:
            raise DecodeError(f"{what}: fixed boundary {null_start} differs from schema {fixed_boundary}")
        boundaries = [fixed_boundary]
    else:
        count_position = null_start - 1
        if count_position < 0 or raw[count_position] != variable_count:
            raise DecodeError(f"{what}: variable count differs from schema {variable_count}")
        wide = len(raw) > 0xFF
        if wide and variable_count != 1:
            raise DecodeError(f"{what}: wide rows with several variable columns are unsupported")
        offsets_start = count_position - (variable_count + 1) - int(wide)
        if offsets_start < fixed_boundary:
            raise DecodeError(f"{what}: row trailer overlaps fixed data")
        boundaries = []
        for ordinal in range(variable_count + 1):
            reverse = variable_count - ordinal
            low = raw[offsets_start + reverse]
            if wide:
                low += 256 * (raw[offsets_start + variable_count + 1] >> reverse & 1)
            boundaries.append(low)
        if boundaries[0] != fixed_boundary:
            raise DecodeError(f"{what}: fixed boundary {boundaries[0]} differs from schema {fixed_boundary}")
        for index in range(variable_count):
            if boundaries[index] > boundaries[index + 1] or boundaries[index + 1] > offsets_start:
                raise DecodeError(f"{what}: variable column {index} has invalid bounds")
        if boundaries[-1] != offsets_start:
            raise DecodeError(f"{what}: variable data does not meet the row trailer")
    values = []
    for column in columns:
        ordinal = column["ordinal"]
        field: bytes | None
        if column["type"] == "Boolean" or not present[ordinal]:
            field = None
        elif column["storage"] == "fixed":
            start = 1 + column["fixed_offset"]
            field = raw[start : start + column["size"]]
        else:
            index = column["variable_index"]
            field = raw[boundaries[index] : boundaries[index + 1]]
        values.append(_decode_value(column, field, present[ordinal]))
    return {"present": present, "values": values}


def _table_rows(data: bytes, definition: dict[str, Any], data_pages: list[int]) -> list[dict[str, Any]]:
    rows = []
    for page in data_pages:
        image = _page(data, page, f"table {definition['root']}")
        for entry in _row_directory(image, page):
            if entry["hidden"]:
                continue
            what = f"table {definition['root']} page {page} row {entry['row']}"
            if entry["overflow"]:
                raise DecodeError(f"{what}: active overflow rows are outside this experiment")
            decoded = _decode_row(image[entry["start"] : entry["end"]], definition["columns"], what)
            rows.append({"page": page, "row": entry["row"], **decoded})
    return rows


def _ordinal(definition: dict[str, Any], name: str) -> int | None:
    for column in definition["columns"]:
        if column["name"] == name:
            return column["ordinal"]
    return None


def _table_pages(data: bytes, definition: dict[str, Any]) -> tuple[list[int], list[int]]:
    root = definition["root"]
    what = f"table {root} owned map"
    data_pages = []
    long_value_pages = []
    for page in sorted(_locator_pages(data, definition["maps"]["owned"], what)):
        image = _page(data, page, what)
        if image[0] != 1:
            raise DecodeError(f"{what}: owned page {page} has tag {image[0]}")
        if image[4:8] == LONG_VALUE_OWNER:
            long_value_pages.append(page)
        elif _u32(image, 4, what) != root:
            raise DecodeError(f"{what}: owned page {page} is owned by {_u32(image, 4, what)}")
        else:
            data_pages.append(page)
    return data_pages, long_value_pages


def _discover_catalog(data: bytes) -> tuple[dict[str, Any], list[int], list[dict[str, Any]]]:
    page_count = len(data) // PAGE_BYTES
    candidates = []
    failures = []
    for page in range(2, page_count):
        if data[page * PAGE_BYTES] != 2:
            continue
        try:
            definition = _definition(data, page)
            data_pages, _ = _table_pages(data, definition)
            rows = _table_rows(data, definition, data_pages)
        except DecodeError as error:
            failures.append(str(error))
            continue
        name = _ordinal(definition, "Name")
        if name is None:
            continue
        if any(row["values"][name] == "MSysObjects" for row in rows):
            candidates.append((definition, data_pages, rows))
    if len(candidates) != 1:
        roots = [candidate[0]["root"] for candidate in candidates]
        raise DecodeError(f"catalog discovery found roots {roots}; candidate failures: {failures}")
    return candidates[0]


def _assign_role(roles: dict[int, dict[str, Any]], page: int, role: str, owner: str | None) -> None:
    existing = roles.get(page)
    if existing is None:
        roles[page] = {"role": role, "owners": set()}
    elif existing["role"] != role:
        raise DecodeError(f"page {page} is both {existing['role']} and {role}")
    if owner is not None:
        roles[page]["owners"].add(owner)


def analyze_checkpoint(data: bytes) -> dict[str, Any]:
    """Decode one closed checkpoint image under H1 through H4."""
    page_count = len(data) // PAGE_BYTES
    tags = [data[page * PAGE_BYTES] for page in range(page_count)]
    if page_count < 2 or tags[0] != 0 or tags[1] != 1:
        raise DecodeError("pages 0 and 1 do not carry the header and global-map tags")
    global_rows = _row_directory(_page(data, 1, "global map"), 1)
    if not global_rows or global_rows[0]["hidden"]:
        raise DecodeError("page 1 has no active row 0")
    global_row = global_rows[0]
    free_pages = _map_pages(
        data[PAGE_BYTES + global_row["start"] : PAGE_BYTES + global_row["end"]],
        page_count,
        "global map",
        bounded=False,
    )

    catalog, catalog_pages, catalog_rows = _discover_catalog(data)
    id_ordinal = _ordinal(catalog, "Id")
    type_ordinal = _ordinal(catalog, "Type")
    flags_ordinal = _ordinal(catalog, "Flags")
    name_ordinal = _ordinal(catalog, "Name")
    if None in (id_ordinal, type_ordinal, flags_ordinal, name_ordinal):
        raise DecodeError("catalog lacks an Id, Type, Flags, or Name column")
    tables: dict[int, dict[str, Any]] = {}
    for row in catalog_rows:
        if row["values"][type_ordinal] != 1:
            continue
        ident = row["values"][id_ordinal]
        if not isinstance(ident, int) or not 2 <= ident < page_count or tags[ident] != 2:
            raise DecodeError(f"catalog table row names definition page {ident!r}")
        if ident in tables:
            raise DecodeError(f"catalog names definition page {ident} twice")
        flags = row["values"][flags_ordinal]
        name = row["values"][name_ordinal]
        if not isinstance(flags, int) or not isinstance(name, str):
            raise DecodeError(f"catalog row for definition {ident} lacks integer flags or text name")
        tables[ident] = {"flags": flags, "name": name}
    if len(tables) > MAX_TABLES:
        raise DecodeError(f"{len(tables)} catalog tables exceed the bound of {MAX_TABLES}")

    roles: dict[int, dict[str, Any]] = {}
    _assign_role(roles, 0, "header", None)
    _assign_role(roles, 1, "global_map", None)
    structures: list[dict[str, Any]] = []

    def structure(page: int, start: int, end: int, attribution: str, owner: str | None) -> None:
        structures.append(
            {
                "attribution": attribution,
                "end": page * PAGE_BYTES + end,
                "owner": owner,
                "page": page,
                "start": page * PAGE_BYTES + start,
            }
        )

    structure(0, PAGE0_COUNTER, PAGE0_COUNTER + 1, "page0_counter", None)
    structure(1, 0, 10 + 2 * len(global_rows), "row_directory", "global map")
    structure(1, global_row["start"], global_row["end"], "global_map", None)
    map_rows: dict[tuple[int, int], str] = {}
    map_pages: set[int] = set()
    for root in sorted(tables):
        entry = tables[root]
        definition = catalog if root == catalog["root"] else _definition(data, root)
        label = f"table {root} {entry['name']}"
        entry["definition"] = definition
        entry["label"] = label
        for page in definition["pages"]:
            role = "definition_root" if page == root else "definition_continuation"
            _assign_role(roles, page, role, label)
        for index, page in enumerate(definition["pages"]):
            start = 0 if index == 0 else 8
            page_logical = PAGE_BYTES if index == 0 else PAGE_BYTES - 8
            consumed = 0 if index == 0 else PAGE_BYTES + (index - 1) * (PAGE_BYTES - 8)
            remaining = definition["logical_length"] - consumed
            structure(page, start, start + min(page_logical, remaining), "definition_other", label)
        counter = definition["row_count_offset"]
        structure(counter // PAGE_BYTES, counter % PAGE_BYTES, counter % PAGE_BYTES + 4, "definition_row_count", label)
        data_pages, long_value_pages = (catalog_pages, []) if root == catalog["root"] else (None, None)
        if data_pages is None:
            data_pages, long_value_pages = _table_pages(data, definition)
        entry["data_pages"] = data_pages
        entry["long_value_pages"] = long_value_pages
        for page in data_pages:
            _assign_role(roles, page, "data", label)
        for page in long_value_pages:
            _assign_role(roles, page, "long_value", label)
        for kind in ("owned", "available"):
            locator = definition["maps"][kind]
            _locator_row(data, locator, f"{label} {kind} map")
            _assign_role(roles, locator["page"], "map_rows", label)
            map_rows[(locator["page"], locator["row"])] = f"{label} {kind} map"
            map_pages.add(locator["page"])
        for long_value_map in definition["long_value_maps"]:
            for kind in ("owned", "available"):
                locator = long_value_map[kind]
                _locator_row(
                    data,
                    locator,
                    f"{label} column {long_value_map['column_name']} {kind} map",
                )
                if locator["page"] not in roles:
                    _assign_role(roles, locator["page"], "long_value_map_rows", label)
                elif roles[locator["page"]]["role"] not in (
                    "map_rows",
                    "long_value_map_rows",
                ):
                    raise DecodeError(
                        f"page {locator['page']} is both {roles[locator['page']]['role']} and long_value_map_rows"
                    )
                roles[locator["page"]]["owners"].add(label)
                map_rows[(locator["page"], locator["row"])] = (
                    f"{label} column {long_value_map['column_name']} {kind} map"
                )
                map_pages.add(locator["page"])
        for index_entry in definition["physical_indexes"]:
            index_label = f"{label} index {index_entry['index']}"
            offset = index_entry["entry_count_offset"]
            structure(offset // PAGE_BYTES, offset % PAGE_BYTES, offset % PAGE_BYTES + 4, "index_entry_count", index_label)
            locator = index_entry["map"]
            index_pages = _locator_pages(data, locator, f"{index_label} map")
            _assign_role(roles, locator["page"], "map_rows", index_label)
            map_rows[(locator["page"], locator["row"])] = f"{index_label} map"
            map_pages.add(locator["page"])
            index_root = index_entry["root"]
            if not 0 <= index_root < page_count:
                raise DecodeError(f"{index_label} root {index_root} is outside the image")
            _assign_role(roles, index_root, "index_root", index_label)
            structure(index_root, 0, PAGE_BYTES, "index_page", index_label)
            for page in sorted(index_pages - {index_root}):
                _assign_role(roles, page, "index_page", index_label)
                structure(page, 0, PAGE_BYTES, "index_page", index_label)
    for root in sorted(tables):
        entry = tables[root]
        for page in entry["data_pages"]:
            image = _page(data, page, entry["label"])
            rows = _row_directory(image, page)
            structure(page, 0, 10 + 2 * len(rows), "row_directory", entry["label"])
            for row in rows:
                if row["end"] > row["start"]:
                    structure(page, row["start"], row["end"], "row_bytes", f"{entry['label']} row {row['row']}")
    for page in sorted(map_pages):
        image = _page(data, page, "map page")
        rows = _row_directory(image, page)
        structure(page, 0, 10 + 2 * len(rows), "row_directory", f"map page {page}")
        for row in rows:
            owner = map_rows.get((page, row["row"]), f"map page {page} row {row['row']} (no locator)")
            if row["end"] > row["start"]:
                structure(page, row["start"], row["end"], "map_bitmap", owner)
    for page in range(page_count):
        if page not in roles and tags[page] == 1 and data[page * PAGE_BYTES + 4 : page * PAGE_BYTES + 8] == LONG_VALUE_OWNER:
            _assign_role(roles, page, "long_value", "LVAL")
        if roles.get(page, {}).get("role") == "long_value":
            structure(page, 0, PAGE_BYTES, "long_value_page", ", ".join(sorted(roles[page]["owners"])))

    system_rows: dict[str, dict[str, Any]] = {}
    for root in sorted(tables):
        entry = tables[root]
        if entry["flags"] & SYSTEM_FLAG:
            rows = catalog_rows if root == catalog["root"] else _table_rows(data, entry["definition"], entry["data_pages"])
            if entry["name"] in system_rows:
                raise DecodeError(f"two system tables are named {entry['name']!r}")
            system_rows[entry["name"]] = {"root": root, "rows": rows}
    pages = [
        {
            "owners": sorted(roles[page]["owners"]) if page in roles else [],
            "page": page,
            "role": roles[page]["role"] if page in roles else "unassigned",
            "tag": tags[page],
        }
        for page in range(page_count)
    ]
    return {
        "catalog_root": catalog["root"],
        "data": data,
        "free_pages": sorted(free_pages),
        "pages": pages,
        "structures": structures,
        "system_rows": system_rows,
        "tables": tables,
    }


# --- question aggregation -----------------------------------------------------


def _first_difference(left: Any, right: Any, path: str) -> str | None:
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return f"{path} keys"
        for key in sorted(left):
            found = _first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path} length"
        for index, (one, other) in enumerate(zip(left, right)):
            found = _first_difference(one, other, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if left == right and type(left) is type(right) else path


def _definition_identity(entry: dict[str, Any]) -> dict[str, Any]:
    definition = entry["definition"]
    return {
        "columns": [
            {key: column[key] for key in column if key != "storage"}
            for column in definition["columns"]
        ],
        "header_unknown_hex": definition["header_unknown_hex"],
        "logical_indexes": definition["logical_indexes"],
        "logical_length": definition["logical_length"],
        "maps": definition["maps"],
        "marker": definition["marker"],
        "name": entry["name"],
        "pages": definition["pages"],
        "physical_indexes": [
            {
                key: index[key]
                for key in index
                if key not in ("entry_count", "entry_count_offset")
            }
            for index in definition["physical_indexes"]
        ],
        "root": definition["root"],
        "suffix_hex": definition["suffix_hex"],
        "long_value_maps": definition["long_value_maps"],
    }


def _analysis(analyses: list[dict[str, Any]], replica: int, checkpoint: str) -> dict[str, Any]:
    item = analyses[replica][checkpoint]
    if "error" in item:
        raise DecodeError(f"replica {replica + 1} checkpoint {checkpoint}: {item['error']}")
    return item


def _question_q1(analyses: list[dict[str, Any]], common: list[str]) -> dict[str, Any]:
    base = _analysis(analyses, 0, common[0])
    system = sorted(root for root, entry in base["tables"].items() if entry["flags"] & SYSTEM_FLAG)
    tables = []
    for root in system:
        reference = _definition_identity(base["tables"][root])
        row_counts: dict[str, list[int]] = {}
        entry_counts: dict[str, list[list[int]]] = {}
        for checkpoint in common:
            row_counts[checkpoint] = []
            entry_counts[checkpoint] = []
            for replica in range(len(analyses)):
                analysis = _analysis(analyses, replica, checkpoint)
                observed = sorted(
                    page for page, entry in analysis["tables"].items() if entry["flags"] & SYSTEM_FLAG
                )
                if observed != system:
                    raise DecodeError(
                        f"replica {replica + 1} checkpoint {checkpoint} system roots {observed} differ from {system}"
                    )
                entry = analysis["tables"][root]
                difference = _first_difference(reference, _definition_identity(entry), "definition")
                if difference:
                    raise DecodeError(
                        f"replica {replica + 1} checkpoint {checkpoint} table {root} differs at {difference}"
                    )
                row_counts[checkpoint].append(entry["definition"]["row_count"])
                entry_counts[checkpoint].append(
                    [index["entry_count"] for index in entry["definition"]["physical_indexes"]]
                )
        table = dict(reference)
        table["row_counts"] = row_counts
        for position, index in enumerate(table["physical_indexes"]):
            index["entry_counts"] = {
                checkpoint: [counts[position] for counts in entry_counts[checkpoint]]
                for checkpoint in common
            }
        tables.append(table)
    return {"status": "answered", "tables": tables}


def _question_q2(analyses: list[dict[str, Any]], common: list[str]) -> dict[str, Any]:
    checkpoints = {}
    for checkpoint in common:
        reference = _analysis(analyses, 0, checkpoint)
        for replica in range(1, len(analyses)):
            analysis = _analysis(analyses, replica, checkpoint)
            difference = _first_difference(reference["pages"], analysis["pages"], "pages")
            if difference:
                raise DecodeError(f"replica {replica + 1} checkpoint {checkpoint} differs at {difference}")
        checkpoints[checkpoint] = {
            "free_pages": reference["free_pages"],
            "pages": reference["pages"],
            "unassigned_pages": [page["page"] for page in reference["pages"] if page["role"] == "unassigned"],
        }
    return {"checkpoints": checkpoints, "status": "answered"}


def _generic_rows(table: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    columns = analysis["tables"][table["root"]]["definition"]["columns"]
    return [
        {column["name"]: row["values"][column["ordinal"]] for column in columns}
        for row in table["rows"]
    ]


def _mask_dates(rows: list[dict[str, Any]], columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = {column["name"] for column in columns if column["type"] == "Date"}
    return [{key: (None if key in dates else value) for key, value in row.items()} for row in rows]


def _dao_names(dao: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    names = []
    for container in dao["containers"]:
        for document in container["documents"]:
            names.append((f"container:{container['name']}:{document['name']}", document["name"], {"container": container["name"]}))
    for table in dao["tabledefs"]:
        names.append((f"tabledef:{table['name']}", table["name"], table))
    for query in dao["querydefs"]:
        names.append((f"querydef:{query['name']}", query["name"], query))
    for relation in dao["relations"]:
        names.append((f"relation:{relation['name']}", relation["name"], relation))
    return names


def _timestamp_match(dao_value: float | None, row_value: Any) -> bool | None:
    if dao_value is None:
        return None
    return isinstance(row_value, float) and row_value == dao_value


def _system_rows_observation(analysis: dict[str, Any], dao: dict[str, Any]) -> dict[str, Any]:
    system = analysis["system_rows"]
    if "MSysObjects" not in system:
        raise DecodeError("no system table is named MSysObjects")
    catalog = analysis["tables"][system["MSysObjects"]["root"]]["definition"]
    ordinals = {name: _ordinal(catalog, name) for name in ("Id", "ParentId", "Name", "Type", "Flags", "DateCreate", "DateUpdate")}
    missing = sorted(name for name, ordinal in ordinals.items() if ordinal is None)
    if missing:
        raise DecodeError(f"MSysObjects lacks columns {missing}")
    variable_names = [column["name"] for column in catalog["columns"] if column["storage"] == "variable"]
    rows = []
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_id: dict[Any, dict[str, Any]] = {}
    for raw in system["MSysObjects"]["rows"]:
        values = raw["values"]
        row = {
            "date_create": values[ordinals["DateCreate"]],
            "date_update": values[ordinals["DateUpdate"]],
            "flags": values[ordinals["Flags"]],
            "id": values[ordinals["Id"]],
            "name": values[ordinals["Name"]],
            "parent_id": values[ordinals["ParentId"]],
            "present_variable_columns": [
                column["name"]
                for column in catalog["columns"]
                if column["storage"] == "variable" and raw["present"][column["ordinal"]]
            ],
            "type": values[ordinals["Type"]],
        }
        rows.append(row)
        if isinstance(row["name"], str):
            by_name.setdefault(row["name"], []).append(row)
        by_id.setdefault(row["id"], row)
    container_ids = {}
    for container in dao["containers"]:
        matches = by_name.get(container["name"], [])
        if len(matches) == 1:
            container_ids[container["name"]] = matches[0]["id"]
    correlations = {}
    classes: dict[Any, str] = {}
    for key, name, item in _dao_names(dao):
        matches = by_name.get(name, [])
        row = matches[0] if len(matches) == 1 else None
        correlation: dict[str, Any] = {
            "date_created_match": None,
            "last_updated_match": None,
            "matches": len(matches),
            "parent_matches_container": None,
            "row_id": None if row is None else row["id"],
        }
        if row is not None:
            if key.startswith("container:"):
                container_id = container_ids.get(item["container"])
                correlation["parent_matches_container"] = None if container_id is None else row["parent_id"] == container_id
                if item["container"] == "Databases":
                    classes.setdefault(id(row), "database")
            else:
                if "date_created" in item:
                    correlation["date_created_match"] = _timestamp_match(item["date_created"], row["date_create"])
                    correlation["last_updated_match"] = _timestamp_match(item["last_updated"], row["date_update"])
                if key.startswith("tabledef:"):
                    attributes = item["attributes"] or 0
                    classes[id(row)] = "system_table" if attributes & SYSTEM_FLAG else "user_table"
                elif key.startswith("querydef:"):
                    classes[id(row)] = "query"
                else:
                    classes[id(row)] = "relationship"
        correlations[key] = correlation
    for container in dao["containers"]:
        for row in by_name.get(container["name"], []):
            classes[id(row)] = "container"
    class_observations: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = classes.get(id(row), "uncorrelated")
        observation = {"flags": row["flags"], "parent_id": row["parent_id"], "type": row["type"]}
        bucket = class_observations.setdefault(name, [])
        if observation not in bucket:
            bucket.append(observation)
    for bucket in class_observations.values():
        bucket.sort(key=lambda item: json.dumps(item, sort_keys=True))
    result: dict[str, Any] = {
        "class_observations": class_observations,
        "msys_objects": {"correlations": correlations, "root": system["MSysObjects"]["root"], "rows": rows},
        "variable_columns": variable_names,
    }
    for name in sorted(system):
        if name == "MSysObjects":
            continue
        table = system[name]
        entry: dict[str, Any] = {"root": table["root"], "rows": _generic_rows(table, analysis)}
        if name == "MSysACEs":
            entry["object_ids_without_catalog_row"] = sorted(
                {row.get("ObjectId") for row in entry["rows"] if row.get("ObjectId") not in by_id and isinstance(row.get("ObjectId"), int)}
            )
        result[name] = entry
    return result


def _comparable_q3(observation: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    comparable = {}
    for key, value in observation.items():
        if key == "msys_objects":
            comparable[key] = {
                "correlations": value["correlations"],
                "rows": [{**row, "date_create": None, "date_update": None} for row in value["rows"]],
            }
        elif isinstance(value, dict) and "rows" in value:
            columns = analysis["tables"][value["root"]]["definition"]["columns"]
            comparable[key] = {"root": value["root"], "rows": _mask_dates(value["rows"], columns)}
        else:
            comparable[key] = value
    return comparable


def _question_q3(
    analyses: list[dict[str, Any]], common: list[str], replicas: list[dict[str, Any]]
) -> dict[str, Any]:
    checkpoints = {}
    unresolved = []
    for checkpoint in common:
        reference = None
        for replica in range(len(analyses)):
            analysis = _analysis(analyses, replica, checkpoint)
            dao = next(item["dao"] for item in replicas[replica]["checkpoints"] if item["name"] == checkpoint)
            observation = _system_rows_observation(analysis, dao)
            comparable = _comparable_q3(observation, analysis)
            if reference is None:
                reference = comparable
                checkpoints[checkpoint] = observation
            else:
                difference = _first_difference(reference, comparable, "rows")
                if difference:
                    raise DecodeError(f"replica {replica + 1} checkpoint {checkpoint} differs at {difference}")
            for key, correlation in observation["msys_objects"]["correlations"].items():
                if correlation["matches"] != 1:
                    unresolved.append(f"replica {replica + 1} checkpoint {checkpoint} {key} matched {correlation['matches']} rows")
    result: dict[str, Any] = {"checkpoints": checkpoints, "status": "answered"}
    if unresolved:
        result["status"] = "no_outcome"
        result["reason"] = unresolved[0]
    return result


def _paint(structures: list[dict[str, Any]], page: int) -> list[int | None]:
    paint: list[int | None] = [None] * PAGE_BYTES
    for index, item in enumerate(structures):
        if item["page"] != page:
            continue
        for offset in range(item["start"] - page * PAGE_BYTES, item["end"] - page * PAGE_BYTES):
            paint[offset] = index
    return paint


def _coalesce(offsets: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for offset in offsets:
        if ranges and ranges[-1][1] == offset:
            ranges[-1] = (ranges[-1][0], offset + 1)
        else:
            ranges.append((offset, offset + 1))
    return ranges


def _attribute_transition(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_data, after_data = before["data"], after["data"]
    before_pages = len(before_data) // PAGE_BYTES
    after_pages = len(after_data) // PAGE_BYTES
    if after_pages < before_pages:
        raise DecodeError("checkpoint image shrank")
    ranges = []
    for page in range(before_pages):
        old = before_data[page * PAGE_BYTES : (page + 1) * PAGE_BYTES]
        new = after_data[page * PAGE_BYTES : (page + 1) * PAGE_BYTES]
        if old == new:
            continue
        changed = [offset for offset in range(PAGE_BYTES) if old[offset] != new[offset]]
        after_paint = _paint(after["structures"], page)
        before_paint = _paint(before["structures"], page)
        hits: dict[tuple[str, int], tuple[dict[str, Any], int]] = {}
        leftover = []
        for offset in changed:
            index = after_paint[offset]
            source = after["structures"]
            if index is None:
                index = before_paint[offset]
                source = before["structures"]
            if index is None:
                leftover.append(page * PAGE_BYTES + offset)
                continue
            key = ("after" if source is after["structures"] else "before", index)
            item, count = hits.get(key, (source[index], 0))
            hits[key] = (item, count + 1)
        for item, count in hits.values():
            ranges.append({**item, "changed_bytes": count})
        for start, end in _coalesce(leftover):
            ranges.append(
                {
                    "attribution": "unattributed",
                    "changed_bytes": end - start,
                    "end": end,
                    "owner": None,
                    "page": page,
                    "start": start,
                }
            )
    for page in range(before_pages, after_pages):
        role = after["pages"][page]
        owner = role["role"] + (f": {', '.join(role['owners'])}" if role["owners"] else "")
        ranges.append(
            {
                "attribution": "appended_page",
                "changed_bytes": PAGE_BYTES,
                "end": (page + 1) * PAGE_BYTES,
                "owner": owner,
                "page": page,
                "start": page * PAGE_BYTES,
            }
        )
    ranges.sort(key=lambda item: (item["page"], item["start"], item["end"], item["attribution"]))
    return ranges


def _question_q4(analyses: list[dict[str, Any]], common: list[str]) -> dict[str, Any]:
    transitions = []
    for before_name, after_name in zip(common, common[1:]):
        reference = None
        for replica in range(len(analyses)):
            attribution = _attribute_transition(
                _analysis(analyses, replica, before_name), _analysis(analyses, replica, after_name)
            )
            comparable = [{key: value for key, value in item.items() if key != "changed_bytes"} for item in attribution]
            if reference is None:
                reference = comparable
                transitions.append(
                    {
                        "from": before_name,
                        "ranges": attribution,
                        "to": after_name,
                        "unattributed_count": sum(1 for item in attribution if item["attribution"] == "unattributed"),
                    }
                )
            else:
                difference = _first_difference(reference, comparable, "ranges")
                if difference:
                    raise DecodeError(f"replica {replica + 1} transition {before_name}->{after_name} differs at {difference}")
    return {"status": "answered", "transitions": transitions}


def _incomplete_reason(document: dict[str, Any], replicas: list[dict[str, Any]]) -> str | None:
    for replica in replicas:
        if replica["status"] != "pass":
            return f"replica {replica['replica']} failed: {replica['error']}"
    if len(replicas) != MAX_REPLICAS:
        return f"{len(replicas)} of {MAX_REPLICAS} replicas were recorded"
    if document["status"] != "pass":
        return "job status is fail"
    return None


def _same_long_value_columns(
    expected: list[dict[str, Any]], mappings: list[dict[str, Any]], *, ordered: bool
) -> bool:
    expected_pairs = [
        (column["column"], column["column_name"]) for column in expected
    ]
    mapping_pairs = [
        (mapping["column"], mapping["column_name"]) for mapping in mappings
    ]
    return (
        expected_pairs == mapping_pairs
        if ordered
        else sorted(expected_pairs) == sorted(mapping_pairs)
    )


def _long_value_observation(analysis: dict[str, Any]) -> dict[str, Any]:
    tables = []
    for root in sorted(analysis["tables"]):
        table = analysis["tables"][root]
        expected_columns = [
            {"column": column["ordinal"], "column_name": column["name"]}
            for column in table["definition"]["columns"]
            if column["type"] in ("Memo", "LongBinary")
        ]
        mappings = []
        for mapping in table["definition"]["long_value_maps"]:
            mappings.append(
                {
                    **mapping,
                    "available_pages": sorted(
                        _locator_pages(
                            analysis["data"],
                            mapping["available"],
                            f"table {root} {table['name']} column {mapping['column_name']} available map",
                        )
                    ),
                    "owned_pages": sorted(
                        _locator_pages(
                            analysis["data"],
                            mapping["owned"],
                            f"table {root} {table['name']} column {mapping['column_name']} owned map",
                        )
                    ),
                }
            )
        tables.append(
            {
                "long_value_maps": mappings,
                "long_value_columns": expected_columns,
                "long_value_pages": table["long_value_pages"],
                "name": table["name"],
                "root": root,
                "suffix_complete": _same_long_value_columns(
                    expected_columns, mappings, ordered=True
                ),
                "suffix_set_complete": _same_long_value_columns(
                    expected_columns, mappings, ordered=False
                ),
            }
        )
    return {
        "pages": analysis["pages"],
        "tables": tables,
        "unassigned_pages": [
            page["page"] for page in analysis["pages"] if page["role"] == "unassigned"
        ],
    }


def build_long_value_report(
    document: dict[str, Any],
    replicas: list[dict[str, Any]],
    *,
    followup: bool = False,
) -> dict[str, Any]:
    analyses: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    decode_error = None
    for replica in replicas:
        per_analysis = {}
        per_observation = {}
        for checkpoint in replica["checkpoints"]:
            name = checkpoint["name"]
            try:
                analysis = analyze_checkpoint(replica["images"][name])
                per_analysis[name] = analysis
                per_observation[name] = _long_value_observation(analysis)
            except DecodeError as error:
                decode_error = f"replica {replica['replica']} checkpoint {name}: {error}"
                break
        analyses.append(per_analysis)
        observations.append(per_observation)
    reason = _incomplete_reason(document, replicas) or decode_error
    common_count = min(len(replica["checkpoints"]) for replica in replicas)
    common = list(LONG_VALUE_CHECKPOINT_NAMES[:common_count])
    if reason is None:
        for replica_index in range(1, len(observations)):
            for checkpoint in common:
                difference = _first_difference(
                    observations[0][checkpoint],
                    observations[replica_index][checkpoint],
                    f"replica {replica_index + 1} checkpoint {checkpoint}",
                )
                if difference is not None:
                    reason = difference
                    break
            if reason is not None:
                break
    predictions: dict[str, Any] = {}
    if reason is None and tuple(common) == LONG_VALUE_CHECKPOINT_NAMES:
        empty = observations[0]["empty"]
        table = observations[0]["table"]
        row = observations[0]["row"]
        gamma_table = next((entry for entry in table["tables"] if entry["name"] == "Gamma"), None)
        gamma_row = next((entry for entry in row["tables"] if entry["name"] == "Gamma"), None)
        if gamma_table is None or gamma_row is None:
            reason = "Gamma did not resolve to one catalog table at both post-create checkpoints"
        else:
            table_maps = gamma_table["long_value_maps"]
            row_maps = gamma_row["long_value_maps"]
            grammar = (
                len(table_maps) == 1
                and len(row_maps) == 1
                and table_maps[0]["column"] == 1
                and table_maps[0]["column_name"] == "Note"
                and table_maps[0]["owned"] == row_maps[0]["owned"]
                and table_maps[0]["available"] == row_maps[0]["available"]
            )
            added_owned = sorted(set(row_maps[0]["owned_pages"]) - set(table_maps[0]["owned_pages"])) if grammar else []
            if followup:
                table_long_value_pages = {
                    page["page"]
                    for page in table["pages"]
                    if page["role"] == "long_value"
                }
                added_long_value_pages = sorted(
                    page["page"]
                    for page in row["pages"]
                    if page["role"] == "long_value"
                    and page["page"] not in table_long_value_pages
                )
            else:
                added_long_value_pages = sorted(
                    set(gamma_row["long_value_pages"])
                    - set(gamma_table["long_value_pages"])
                )
            complete_suffixes = all(
                entry["suffix_set_complete" if followup else "suffix_complete"]
                for checkpoint in (empty, table, row)
                for entry in checkpoint["tables"]
            )
            map_tracks_external = bool(added_owned) and added_owned == added_long_value_pages
            predictions = {
                "all_empty_pages_assigned": empty["unassigned_pages"] == [],
                "all_long_value_columns_have_one_suffix_group": complete_suffixes,
                "gamma_note_has_one_suffix_group": grammar,
                "gamma_new_long_value_pages": added_long_value_pages,
                "note_owned_map_added_pages": added_owned,
                "note_owned_map_tracks_external_long_value_page": map_tracks_external,
            }
            if not all(
                (
                    predictions["all_empty_pages_assigned"],
                    complete_suffixes,
                    grammar,
                    map_tracks_external,
                )
            ):
                reason = "one or more preregistered H5 predictions was not observed"
    elif reason is None:
        reason = "not every preregistered checkpoint is present"
    status = "answered" if reason is None else "no_outcome"
    question = {
        "checkpoints": observations[0] if observations else {},
        "predictions": predictions,
        "status": status,
    }
    if reason is not None:
        question["reason"] = reason
    summaries = [
        {
            "checkpoints": [
                {
                    "database": checkpoint["database"],
                    "name": checkpoint["name"],
                    "sha256": checkpoint["sha256"],
                    "size": checkpoint["size"],
                }
                for checkpoint in replica["checkpoints"]
            ],
            "error": replica["error"],
            "replica": replica["replica"],
            "status": replica["status"],
        }
        for replica in replicas
    ]
    question_name = "H6" if followup else "H5"
    return {
        "checkpoints_compared": common,
        "compatibility_claim": False,
        "development_only": True,
        "document_type": (
            "long_value_maps_followup_report"
            if followup
            else "long_value_maps_report"
        ),
        "plan_sha256": document["plan_sha256"],
        "questions": {question_name: question},
        "replicas": summaries,
        "status": "accepted" if status == "answered" else "no_outcome",
        "support_movement": False,
    }


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate validated replicas into the deterministic report."""
    analyses = []
    for replica in replicas:
        per_checkpoint = {}
        for checkpoint in replica["checkpoints"]:
            try:
                per_checkpoint[checkpoint["name"]] = analyze_checkpoint(replica["images"][checkpoint["name"]])
            except DecodeError as error:
                per_checkpoint[checkpoint["name"]] = {"error": str(error)}
        analyses.append(per_checkpoint)
    common_count = min(len(replica["checkpoints"]) for replica in replicas)
    common = list(CHECKPOINT_NAMES[:common_count])
    incomplete = _incomplete_reason(document, replicas)
    questions: dict[str, dict[str, Any]] = {}
    for name in QUESTION_NAMES:
        try:
            if not common:
                raise DecodeError("no checkpoint is present in every replica")
            if name == "Q1":
                question = _question_q1(analyses, common)
            elif name == "Q2":
                question = _question_q2(analyses, common)
            elif name == "Q3":
                question = _question_q3(analyses, common, replicas)
            else:
                question = _question_q4(analyses, common) if len(common) > 1 else {"status": "answered", "transitions": []}
        except DecodeError as error:
            question = {"reason": str(error), "status": "no_outcome"}
        if question["status"] == "answered" and incomplete is not None:
            question = {**question, "reason": incomplete, "status": "no_outcome"}
        questions[name] = question
    summaries = []
    for replica in replicas:
        errors: set[str] = set()
        for checkpoint in replica["checkpoints"]:
            dao = checkpoint["dao"]
            items = list(dao["tabledefs"]) + list(dao["querydefs"]) + list(dao["relations"]) + list(dao["properties"])
            for container in dao["containers"]:
                items.append(container)
                items.extend(container["documents"])
            errors.update(item["error"] for item in items if item["error"] is not None)
        summaries.append(
            {
                "checkpoints": [
                    {
                        "database": checkpoint["database"],
                        "decoded": "error" not in analyses[replica["replica"] - 1][checkpoint["name"]],
                        "metadata_open_repaired": checkpoint["metadata_open_repaired"],
                        "name": checkpoint["name"],
                        "sha256": checkpoint["sha256"],
                        "size": checkpoint["size"],
                    }
                    for checkpoint in replica["checkpoints"]
                ],
                "dao_errors": sorted(errors),
                "error": replica["error"],
                "replica": replica["replica"],
                "status": replica["status"],
            }
        )
    answered = all(question["status"] == "answered" for question in questions.values())
    return {
        "checkpoints_compared": common,
        "compatibility_claim": False,
        "development_only": True,
        "document_type": "system_catalog_report",
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": summaries,
        "status": "accepted" if answered else "no_outcome",
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    global CHECKPOINT_NAMES
    expected = _digest(expected_plan_sha256, "--expected-plan-sha256")
    document = load_json(job_result)
    item = _expect_keys(
        document,
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"},
        set(),
        "$",
    )
    if item["document_type"] not in (
        DOCUMENT_TYPE,
        LONG_VALUE_DOCUMENT_TYPE,
        LONG_VALUE_FOLLOWUP_DOCUMENT_TYPE,
    ):
        raise AnalysisError(
            f"$.document_type must be {DOCUMENT_TYPE}, {LONG_VALUE_DOCUMENT_TYPE}, or {LONG_VALUE_FOLLOWUP_DOCUMENT_TYPE}"
        )
    if item["development_only"] is not True:
        raise AnalysisError("$.development_only must be true")
    _string(item["run_id"], "$.run_id", maximum=128)
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError("$.status must be pass or fail")
    if _digest(item["plan_sha256"], "$.plan_sha256") != expected:
        raise AnalysisError("job result plan digest differs from the approved plan")
    old_checkpoint_names = CHECKPOINT_NAMES
    if item["document_type"] in (
        LONG_VALUE_DOCUMENT_TYPE,
        LONG_VALUE_FOLLOWUP_DOCUMENT_TYPE,
    ):
        CHECKPOINT_NAMES = LONG_VALUE_CHECKPOINT_NAMES
    try:
        raw_replicas = item["replicas"]
        if not isinstance(raw_replicas, list) or not 1 <= len(raw_replicas) <= MAX_REPLICAS:
            raise AnalysisError(f"$.replicas must contain one through {MAX_REPLICAS} replicas")
        replicas = [
            _replica(raw, f"$.replicas[{index}]", job_result.parent)
            for index, raw in enumerate(raw_replicas)
        ]
        if [replica["replica"] for replica in replicas] != list(range(1, len(replicas) + 1)):
            raise AnalysisError("$.replicas must be numbered 1 through n in order")
        if item["document_type"] == LONG_VALUE_DOCUMENT_TYPE:
            report = build_long_value_report(item, replicas)
        elif item["document_type"] == LONG_VALUE_FOLLOWUP_DOCUMENT_TYPE:
            report = build_long_value_report(item, replicas, followup=True)
        else:
            report = build_report(item, replicas)
    finally:
        CHECKPOINT_NAMES = old_checkpoint_names
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(report))
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("job_result", type=Path)
    result.add_argument("--expected-plan-sha256", required=True)
    result.add_argument("--output", required=True, type=Path)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        evaluate(args.job_result, args.expected_plan_sha256, args.output)
    except (AnalysisError, OSError) as error:
        print(f"REJECTED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
