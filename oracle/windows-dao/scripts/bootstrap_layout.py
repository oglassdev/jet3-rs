#!/usr/bin/env python3
"""Validate and summarize one bounded local bootstrap-layout experiment."""

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
REPLICA_COUNT = 3
MAX_PAGES = 64
MAX_ITEMS = 64
MAX_JSON_BYTES = 1024 * 1024
TIMESTAMP_ANCHOR_WINDOW_BYTES = 64
BASE_CHECKPOINT_NAMES = ("empty", "created", "renamed")
CHECKPOINT_NAMES = (*BASE_CHECKPOINT_NAMES, "property-set")
CORRELATION_NAMES = ("date_created", "date_updated", "lvprop")
ENDPOINT_NAMES = (
    "open_database",
    "table_enumerated",
    "field_enumerated",
    "table_opened",
)
VARIANT_KINDS = {
    "candidate_page0",
    "candidate_date_created",
    "candidate_date_updated",
    "revert_existing_page",
    "zero_appended_page",
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
SAFE_MDB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}[.]mdb$", re.IGNORECASE)
HEX_BYTES = re.compile(r"^(?:[0-9a-f]{2})+$")


class AnalysisError(ValueError):
    """The producer result is malformed or fails an integrity check."""


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
        raise AnalysisError("job result exceeds the one-MiB bound")
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


def _expect_keys(
    value: Any,
    required: set[str],
    optional: set[str],
    location: str,
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


def _detail(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise AnalysisError(f"{location} must be a nonempty string of at most 512 characters")
    return value


def _digest(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{location} must be a lowercase SHA-256 digest")
    return value


def _name(value: Any, location: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise AnalysisError(f"{location} is malformed")
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


def _ranges(
    value: Any,
    location: str,
    *,
    maximum: int,
    page: int | None,
    allow_empty: bool = False,
) -> list[dict[str, int]]:
    minimum = 0 if allow_empty else 1
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_ITEMS:
        raise AnalysisError(
            f"{location} must contain between {minimum} and {MAX_ITEMS} ranges"
        )
    result: list[dict[str, int]] = []
    previous_end = -1
    page_start = page * PAGE_BYTES if page is not None else 0
    page_end = page_start + PAGE_BYTES if page is not None else maximum
    for index, raw in enumerate(value):
        item = _expect_keys(raw, {"start", "end"}, set(), f"{location}[{index}]")
        start = _integer(item["start"], f"{location}[{index}].start", 0, maximum)
        end = _integer(item["end"], f"{location}[{index}].end", 0, maximum)
        if start >= end:
            raise AnalysisError(f"{location}[{index}] must be a nonempty half-open range")
        if start < previous_end:
            raise AnalysisError(f"{location} must be sorted and nonoverlapping")
        if page is not None and not (page_start <= start < end <= page_end):
            raise AnalysisError(f"{location}[{index}] is outside page {page}")
        result.append({"start": start, "end": end})
        previous_end = end
    return result


def _endpoints(value: dict[str, Any], location: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for name in ENDPOINT_NAMES:
        if name not in value or type(value[name]) is not bool:
            raise AnalysisError(f"{location}.{name} must be boolean")
        result[name] = value[name]
    return result


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


def _dao(value: Any, checkpoint: str, location: str) -> dict[str, Any]:
    if checkpoint == "empty":
        item = _expect_keys(value, {"table_definition_count"}, set(), location)
        _integer(item["table_definition_count"], f"{location}.table_definition_count", 0, 128)
        return item
    item = _expect_keys(
        value,
        {
            "table_name",
            "date_created_oadate",
            "last_updated_oadate",
            "fields",
            "lvprop",
        },
        set(),
        location,
    )
    if not isinstance(item["table_name"], str) or not item["table_name"]:
        raise AnalysisError(f"{location}.table_name must be nonempty")
    for field in ("date_created_oadate", "last_updated_oadate"):
        number = item[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise AnalysisError(f"{location}.{field} must be a finite number")
    fields = item["fields"]
    if not isinstance(fields, list) or len(fields) > 128:
        raise AnalysisError(f"{location}.fields must contain at most 128 entries")
    for index, raw in enumerate(fields):
        entry = _expect_keys(raw, {"name", "type"}, set(), f"{location}.fields[{index}]")
        if not isinstance(entry["name"], str) or not entry["name"]:
            raise AnalysisError(f"{location}.fields[{index}].name must be nonempty")
        _integer(entry["type"], f"{location}.fields[{index}].type", -32768, 32767)
    lvprop = _expect_keys(
        item["lvprop"], {"status", "detail"}, {"length", "bytes_hex"}, f"{location}.lvprop"
    )
    _detail(lvprop["detail"], f"{location}.lvprop.detail")
    if lvprop["status"] not in ("captured", "no_outcome"):
        raise AnalysisError(f"{location}.lvprop.status must be captured or no_outcome")
    if lvprop["status"] == "captured":
        if set(lvprop) != {"status", "detail", "length", "bytes_hex"}:
            raise AnalysisError(f"{location}.lvprop captured evidence is incomplete")
        length = _integer(lvprop["length"], f"{location}.lvprop.length", 1, 65536)
        encoded = lvprop["bytes_hex"]
        if not isinstance(encoded, str) or not HEX_BYTES.fullmatch(encoded):
            raise AnalysisError(f"{location}.lvprop.bytes_hex must be lowercase hex")
        if len(encoded) != length * 2:
            raise AnalysisError(f"{location}.lvprop.length differs from bytes_hex")
    elif set(lvprop) != {"status", "detail"}:
        raise AnalysisError(f"{location}.lvprop no_outcome must not carry captured bytes")
    return item


def _checkpoint(value: Any, location: str, root: Path) -> tuple[dict[str, Any], bytes]:
    item = _expect_keys(
        value,
        {"name", "database", "size", "page_count", "sha256"},
        {"dao"},
        location,
    )
    if item["name"] not in CHECKPOINT_NAMES:
        raise AnalysisError(f"{location}.name is not preregistered")
    database = _database(item["database"], f"{location}.database")
    size = _size(item["size"], f"{location}.size")
    page_count = _integer(item["page_count"], f"{location}.page_count", 1, MAX_PAGES)
    if page_count != size // PAGE_BYTES:
        raise AnalysisError(f"{location}.page_count differs from size")
    digest = _digest(item["sha256"], f"{location}.sha256")
    dao = _dao(item["dao"], item["name"], f"{location}.dao") if "dao" in item else None
    data = _read_file(root, database, size, digest, location)
    return {
        "database": database,
        "dao": dao,
        "name": item["name"],
        "page_count": page_count,
        "sha256": digest,
        "size": size,
    }, data


def _difference_ranges(before: bytes, after: bytes, page: int) -> list[dict[str, int]]:
    start = page * PAGE_BYTES
    result: list[dict[str, int]] = []
    for offset in range(start, start + PAGE_BYTES):
        if before[offset] == after[offset]:
            continue
        if result and result[-1]["end"] == offset:
            result[-1]["end"] = offset + 1
        else:
            result.append({"start": offset, "end": offset + 1})
    return result


def _offsets(ranges: list[dict[str, int]]) -> set[int]:
    return {offset for item in ranges for offset in range(item["start"], item["end"])}


def _find_all(data: bytes, needle: bytes) -> list[int]:
    result: list[int] = []
    start = 0
    while True:
        found = data.find(needle, start)
        if found < 0:
            return result
        result.append(found)
        start = found + 1


def _reported_date_correlation(
    value: Any,
    location: str,
    renamed: bytes,
    oadate: float | None,
    other: float | None,
) -> dict[str, Any]:
    item = _expect_keys(value, {"status", "detail"}, {"method", "offsets"}, location)
    _detail(item["detail"], f"{location}.detail")
    matches = [] if oadate is None else _find_all(renamed, struct.pack("<d", oadate))
    resolved_matches = matches
    method = "unique_exact"
    if oadate is not None and other is not None and oadate != other and len(matches) != 1:
        anchors = _find_all(renamed, struct.pack("<d", other))
        if len(anchors) == 1:
            resolved_matches = [
                offset
                for offset in matches
                if abs(offset - anchors[0]) <= TIMESTAMP_ANCHOR_WINDOW_BYTES
            ]
            method = "other_timestamp_anchor"
    resolved = (
        oadate is not None
        and other is not None
        and oadate != other
        and len(resolved_matches) == 1
    )
    if resolved:
        if (
            item["status"] != "resolved"
            or item.get("method") != method
            or item.get("offsets") != resolved_matches
        ):
            raise AnalysisError(f"{location} differs from independently scanned OADate bytes")
        return {"method": method, "offsets": resolved_matches, "status": "resolved"}
    if item["status"] != "no_outcome" or "offsets" in item or "method" in item:
        raise AnalysisError(f"{location} must be no_outcome for ambiguous OADate bytes")
    return {"status": "no_outcome"}


def _lvprop_locator(data: bytes, payload: bytes) -> tuple[int, int, int] | None:
    locators: list[tuple[int, int]] = []
    for page in range(len(data) // PAGE_BYTES):
        start = page * PAGE_BYTES
        image = data[start : start + PAGE_BYTES]
        if image[0] != 1 or image[4:8] != b"LVAL":
            continue
        rows = int.from_bytes(image[8:10], "little")
        directory_end = 10 + 2 * rows
        if rows > 256 or directory_end > PAGE_BYTES:
            continue
        prior = PAGE_BYTES
        for row in range(rows):
            raw = int.from_bytes(image[10 + 2 * row : 12 + 2 * row], "little")
            offset = raw & 0x1FFF
            if raw & 0xE000 or offset < directory_end or offset >= prior:
                break
            if image[offset:prior] == payload:
                locators.append((page, row))
            prior = offset
    if len(locators) != 1:
        return None
    page, row = locators[0]
    header = struct.pack("<I", len(payload) | 0x40000000)
    header += bytes([row]) + page.to_bytes(3, "little") + bytes(4)
    offsets = _find_all(data, header)
    return (offsets[0], page, row) if len(offsets) == 1 else None


def _reported_lvprop_correlation(
    value: Any,
    location: str,
    renamed: bytes,
    dao: dict[str, Any] | None,
) -> dict[str, Any]:
    item = _expect_keys(
        value,
        {"status", "detail"},
        {"header_offset", "payload_page", "payload_row"},
        location,
    )
    _detail(item["detail"], f"{location}.detail")
    payload = None
    if dao is not None and dao["lvprop"]["status"] == "captured":
        payload = bytes.fromhex(dao["lvprop"]["bytes_hex"])
    locator = None if payload is None else _lvprop_locator(renamed, payload)
    if locator is None:
        evidence = [item.get(name) for name in ("header_offset", "payload_page", "payload_row")]
        if item["status"] != "no_outcome" or any(part is not None for part in evidence):
            raise AnalysisError(f"{location} must be no_outcome without unique LvProp evidence")
        return {"status": "no_outcome"}
    expected = {
        "header_offset": locator[0],
        "payload_page": locator[1],
        "payload_row": locator[2],
    }
    if item["status"] != "resolved" or any(item.get(name) != found for name, found in expected.items()):
        raise AnalysisError(f"{location} differs from independently reconstructed LvProp evidence")
    return {"status": "resolved", **expected}


def _baseline(
    value: Any,
    location: str,
    root: Path,
    created: dict[str, Any],
    created_bytes: bytes,
) -> dict[str, Any]:
    item = _expect_keys(
        value,
        {"database", "sha256_before_open", "sha256_after_open", *ENDPOINT_NAMES},
        {"detail"},
        location,
    )
    database = _database(item["database"], f"{location}.database")
    before = _digest(item["sha256_before_open"], f"{location}.sha256_before_open")
    after = _digest(item["sha256_after_open"], f"{location}.sha256_after_open")
    if "detail" in item:
        _detail(item["detail"], f"{location}.detail")
    endpoints = _endpoints(item, location)
    current = _read_file(root, database, created["size"], after, location)
    if before != created["sha256"]:
        raise AnalysisError(f"{location} pre-open hash differs from the created checkpoint")
    repaired = before != after
    if not repaired and current != created_bytes:
        raise AnalysisError(f"{location} is not an exact created-checkpoint clone")
    return {"endpoints": endpoints, "repaired": repaired}


def _sufficiency(
    value: Any,
    location: str,
    root: Path,
    empty_bytes: bytes,
    created: dict[str, Any],
    created_bytes: bytes,
    page0_ranges: list[dict[str, int]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    item = _expect_keys(
        value,
        {
            "database",
            "size",
            "sha256_before_open",
            "sha256_after_open",
            "endpoints",
        },
        {"detail"},
        location,
    )
    database = _database(item["database"], f"{location}.database")
    size = _size(item["size"], f"{location}.size")
    if size != created["size"]:
        raise AnalysisError(f"{location}.size differs from the created checkpoint")
    before = _digest(item["sha256_before_open"], f"{location}.sha256_before_open")
    after = _digest(item["sha256_after_open"], f"{location}.sha256_after_open")
    endpoint_item = _expect_keys(
        item["endpoints"], set(ENDPOINT_NAMES), {"detail"}, f"{location}.endpoints"
    )
    endpoints = _endpoints(endpoint_item, f"{location}.endpoints")
    if "detail" in endpoint_item:
        _detail(endpoint_item["detail"], f"{location}.endpoints.detail")
    if "detail" in item:
        _detail(item["detail"], f"{location}.detail")

    expected = bytearray(created["size"])
    expected[: len(empty_bytes)] = empty_bytes
    for group in [{"ranges": page0_ranges}, *groups]:
        for part in group["ranges"]:
            start, end = part["start"], part["end"]
            expected[start:end] = created_bytes[start:end]
    expected_bytes = bytes(expected)
    if expected_bytes != created_bytes:
        raise AnalysisError(f"{location} mutation groups do not reconstruct the created checkpoint")
    if before != sha256(expected_bytes):
        raise AnalysisError(f"{location} pre-open digest differs from independent reconstruction")
    current = _read_file(root, database, size, after, location)
    repaired = before != after
    if not repaired and current != expected_bytes:
        raise AnalysisError(f"{location} differs from independent reconstruction")
    return {"endpoints": endpoints, "repaired": repaired}


def _groups(
    value: Any,
    location: str,
    maximum: int,
    page_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise AnalysisError(f"{location} must be an array of at most {MAX_ITEMS} groups")
    result = []
    for index, raw in enumerate(value):
        here = f"{location}[{index}]"
        item = _expect_keys(raw, {"name", "page", "ranges"}, set(), here)
        page = _integer(item["page"], f"{here}.page", 0, page_count - 1)
        result.append(
            {
                "name": _name(item["name"], f"{here}.name"),
                "page": page,
                "ranges": _ranges(
                    item["ranges"], f"{here}.ranges", maximum=maximum, page=page
                ),
            }
        )
    return result


def _page0(
    values_raw: Any,
    ranges_raw: Any,
    images: dict[str, bytes],
    location: str,
) -> tuple[dict[str, int], dict[str, list[dict[str, int]]], bool]:
    values_item = _expect_keys(
        values_raw, set(BASE_CHECKPOINT_NAMES), set(), f"{location}.page0_values"
    )
    values = {
        name: _integer(values_item[name], f"{location}.page0_values.{name}", 0, 255)
        for name in BASE_CHECKPOINT_NAMES
    }
    for name in BASE_CHECKPOINT_NAMES:
        if images[name][1538] != values[name]:
            raise AnalysisError(f"{location}.page0_values.{name} differs from checkpoint")
    ranges_item = _expect_keys(
        ranges_raw,
        {"empty_to_created", "created_to_renamed"},
        set(),
        f"{location}.page0_changed_ranges",
    )
    transitions = {
        name: _ranges(
            ranges_item[name],
            f"{location}.page0_changed_ranges.{name}",
            maximum=PAGE_BYTES,
            page=0,
            allow_empty=True,
        )
        for name in ("empty_to_created", "created_to_renamed")
    }
    actual = {
        "empty_to_created": _difference_ranges(images["empty"], images["created"], 0),
        "created_to_renamed": _difference_ranges(images["created"], images["renamed"], 0),
    }
    if transitions != actual:
        raise AnalysisError(f"{location}.page0_changed_ranges differ from checkpoints")
    observed = _offsets(transitions["empty_to_created"])
    if 1538 not in observed:
        raise AnalysisError(f"{location}.page0 transition omits byte 1538")
    renamed_observed = _offsets(transitions["created_to_renamed"])
    isolated = observed == {1538} and renamed_observed <= {1538}
    return values, transitions, isolated


def _parse_variant(
    value: Any,
    location: str,
    root: Path,
    maximum: int,
    page_count: int,
) -> tuple[dict[str, Any], bytes]:
    required = {
        "name",
        "kind",
        "database",
        "size",
        "base_checkpoint",
        "ranges",
        "sha256_before_open",
        "sha256_after_open",
        "endpoints",
    }
    item = _expect_keys(value, required, {"page", "detail"}, location)
    if item["kind"] not in VARIANT_KINDS:
        raise AnalysisError(f"{location}.kind is not preregistered")
    if item["base_checkpoint"] not in ("created", "renamed"):
        raise AnalysisError(f"{location}.base_checkpoint must be created or renamed")
    database = _database(item["database"], f"{location}.database")
    size = _size(item["size"], f"{location}.size")
    if size != maximum:
        raise AnalysisError(f"{location}.size differs from its checkpoint size")
    before = _digest(item["sha256_before_open"], f"{location}.sha256_before_open")
    after = _digest(item["sha256_after_open"], f"{location}.sha256_after_open")
    page = None
    if "page" in item:
        page = _integer(item["page"], f"{location}.page", 0, page_count - 1)
    ranges = _ranges(item["ranges"], f"{location}.ranges", maximum=maximum, page=page)
    endpoints_item = _expect_keys(
        item["endpoints"], set(ENDPOINT_NAMES), {"detail"}, f"{location}.endpoints"
    )
    endpoints = _endpoints(endpoints_item, f"{location}.endpoints")
    if "detail" in endpoints_item:
        _detail(endpoints_item["detail"], f"{location}.endpoints.detail")
    if "detail" in item:
        _detail(item["detail"], f"{location}.detail")
    data = _read_file(root, database, size, after, location)
    return {
        "base_checkpoint": item["base_checkpoint"],
        "database": database,
        "endpoints": endpoints,
        "kind": item["kind"],
        "name": _name(item["name"], f"{location}.name"),
        "page": page,
        "ranges": ranges,
        "repaired": before != after,
        "sha256": before,
    }, data


def _apply_source(base: bytes, source: bytes | None, ranges: list[dict[str, int]]) -> bytes:
    result = bytearray(base)
    for item in ranges:
        start, end = item["start"], item["end"]
        result[start:end] = bytes(end - start) if source is None else source[start:end]
    return bytes(result)


def _variant_outcome(variant: dict[str, Any]) -> str:
    if variant["repaired"]:
        return "no_outcome"
    return "not_observed_necessary" if all(variant["endpoints"].values()) else "necessary"


def _validate_complete_replica(
    item: dict[str, Any], root: Path, location: str
) -> dict[str, Any]:
    raw_checkpoints = item["checkpoints"]
    if not isinstance(raw_checkpoints, list) or len(raw_checkpoints) != 4:
        raise AnalysisError(f"{location}.checkpoints must contain exactly four entries")
    checkpoints: dict[str, dict[str, Any]] = {}
    images: dict[str, bytes] = {}
    databases: set[str] = set()
    for index, raw in enumerate(raw_checkpoints):
        checkpoint, data = _checkpoint(raw, f"{location}.checkpoints[{index}]", root)
        if checkpoint["name"] in checkpoints or checkpoint["database"] in databases:
            raise AnalysisError(f"{location}.checkpoints contains a duplicate")
        checkpoints[checkpoint["name"]] = checkpoint
        images[checkpoint["name"]] = data
        databases.add(checkpoint["database"])
    if set(checkpoints) != set(CHECKPOINT_NAMES):
        raise AnalysisError(f"{location}.checkpoints do not match the required names")
    if checkpoints["created"]["page_count"] < checkpoints["empty"]["page_count"]:
        raise AnalysisError(f"{location}.created is shorter than empty")
    if checkpoints["renamed"]["size"] != checkpoints["created"]["size"]:
        raise AnalysisError(f"{location}.renamed size differs from created")
    if checkpoints["property-set"]["size"] < checkpoints["renamed"]["size"]:
        raise AnalysisError(f"{location}.property-set is shorter than renamed")
    baseline = _baseline(
        item["baseline"], f"{location}.baseline", root, checkpoints["created"], images["created"]
    )

    renamed_dao = checkpoints["renamed"]["dao"]
    property_dao = checkpoints["property-set"]["dao"]
    created_size = checkpoints["created"]["size"]
    page_count = checkpoints["created"]["page_count"]
    correlations_item = _expect_keys(
        item["correlations"], set(CORRELATION_NAMES), set(), f"{location}.correlations"
    )
    created_date = None if renamed_dao is None else float(renamed_dao["date_created_oadate"])
    updated_date = None if renamed_dao is None else float(renamed_dao["last_updated_oadate"])
    correlations = {
        "date_created": _reported_date_correlation(
            correlations_item["date_created"],
            f"{location}.correlations.date_created",
            images["renamed"],
            created_date,
            updated_date,
        ),
        "date_updated": _reported_date_correlation(
            correlations_item["date_updated"],
            f"{location}.correlations.date_updated",
            images["renamed"],
            updated_date,
            created_date,
        ),
        "lvprop": _reported_lvprop_correlation(
            correlations_item["lvprop"],
            f"{location}.correlations.lvprop",
            images["property-set"],
            property_dao,
        ),
    }
    page0_values, page0_ranges, page0_isolated = _page0(
        item["page0_values"], item["page0_changed_ranges"], images, location
    )

    changed = _groups(
        item["changed_page_groups"], f"{location}.changed_page_groups", created_size, page_count
    )
    appended = _groups(
        item["appended_page_groups"], f"{location}.appended_page_groups", created_size, page_count
    )
    if len(changed) + len(appended) > MAX_ITEMS:
        raise AnalysisError(f"{location} lists too many mutation groups")
    group_names = [group["name"] for group in changed + appended]
    if len(group_names) != len(set(group_names)):
        raise AnalysisError(f"{location} repeats a mutation-group name")
    empty_pages = checkpoints["empty"]["page_count"]
    if any(group["page"] == 0 or group["page"] >= empty_pages for group in changed):
        raise AnalysisError(f"{location}.changed_page_groups must name existing non-page0 pages")
    if any(group["page"] < empty_pages for group in appended):
        raise AnalysisError(f"{location}.appended_page_groups names an existing page")
    actual_changed = {
        page: _difference_ranges(images["empty"], images["created"], page)
        for page in range(1, empty_pages)
        if images["empty"][page * PAGE_BYTES : (page + 1) * PAGE_BYTES]
        != images["created"][page * PAGE_BYTES : (page + 1) * PAGE_BYTES]
    }
    listed: dict[int, list[dict[str, int]]] = {}
    for group in changed:
        listed.setdefault(group["page"], []).extend(group["ranges"])
    if set(listed) != set(actual_changed):
        raise AnalysisError(f"{location}.changed_page_groups omit or add changed pages")
    for page, ranges in listed.items():
        if _offsets(ranges) != _offsets(actual_changed[page]):
            raise AnalysisError(f"{location}.changed_page_groups differ from page {page} bytes")
        if sum(part["end"] - part["start"] for part in ranges) != len(_offsets(ranges)):
            raise AnalysisError(f"{location}.changed_page_groups overlap on page {page}")
    expected_appended = set(range(empty_pages, page_count))
    appended_pages = [group["page"] for group in appended]
    if len(appended_pages) != len(set(appended_pages)) or set(appended_pages) != expected_appended:
        raise AnalysisError(f"{location}.appended_page_groups must name each appended page once")
    for group in appended:
        page = group["page"]
        expected = [{"start": page * PAGE_BYTES, "end": (page + 1) * PAGE_BYTES}]
        if group["ranges"] != expected:
            raise AnalysisError(f"{location}.appended_page_groups must cover complete pages")

    sufficiency = _sufficiency(
        item["sufficiency"],
        f"{location}.sufficiency",
        root,
        images["empty"],
        checkpoints["created"],
        images["created"],
        page0_ranges["empty_to_created"],
        changed + appended,
    )

    raw_variants = item["variants"]
    if not isinstance(raw_variants, list) or not 1 <= len(raw_variants) <= MAX_ITEMS:
        raise AnalysisError(f"{location}.variants must contain 1 through {MAX_ITEMS} entries")
    variants_with_data = [
        _parse_variant(raw, f"{location}.variants[{index}]", root, created_size, page_count)
        for index, raw in enumerate(raw_variants)
    ]
    variants = [pair[0] for pair in variants_with_data]
    if len({variant["name"] for variant in variants}) != len(variants):
        raise AnalysisError(f"{location}.variants repeats a name")
    by_kind = {
        kind: [variant for variant in variants if variant["kind"] == kind]
        for kind in VARIANT_KINDS
    }
    page0 = by_kind["candidate_page0"]
    if (
        len(page0) != 1
        or page0[0]["name"] != "page0-byte-1538"
        or page0[0]["page"] != 0
        or page0[0]["ranges"] != [{"start": 1538, "end": 1539}]
    ):
        raise AnalysisError(f"{location} lacks the fixed candidate-page0 ablation")
    date_kinds = {
        "date_created": "candidate_date_created",
        "date_updated": "candidate_date_updated",
    }
    for field, kind in date_kinds.items():
        candidates = by_kind[kind]
        resolved = correlations[field]["status"] == "resolved"
        if len(candidates) != (1 if resolved else 0):
            raise AnalysisError(f"{location} has the wrong number of {kind} variants")
        if resolved:
            expected_name = "date-created-zero" if field == "date_created" else "date-updated-zero"
            if candidates[0]["name"] != expected_name:
                raise AnalysisError(f"{location}.{kind} has an unexpected name")
            expected = [
                {"start": offset, "end": offset + 8}
                for offset in correlations[field]["offsets"]
            ]
            if candidates[0]["ranges"] != expected:
                raise AnalysisError(f"{location}.{kind} ranges differ from correlation offsets")

    group_by_name = {group["name"]: ("revert_existing_page", group) for group in changed}
    group_by_name.update({group["name"]: ("zero_appended_page", group) for group in appended})
    group_variants = [
        variant
        for variant in variants
        if variant["kind"] in ("revert_existing_page", "zero_appended_page")
    ]
    if {variant["name"] for variant in group_variants} != set(group_by_name):
        raise AnalysisError(f"{location} lacks exactly one variant per mutation group")
    for variant in group_variants:
        expected_kind, group = group_by_name[variant["name"]]
        if (
            variant["kind"] != expected_kind
            or variant["page"] != group["page"]
            or variant["ranges"] != group["ranges"]
        ):
            raise AnalysisError(f"{location}.{variant['name']} differs from its mutation group")

    data_by_name = {variant["name"]: data for variant, data in variants_with_data}
    for variant in variants:
        kind = variant["kind"]
        expected_base = "renamed" if kind.startswith("candidate_date_") else "created"
        if variant["base_checkpoint"] != expected_base:
            raise AnalysisError(f"{location}.{variant['name']} has the wrong base checkpoint")
        source = images["empty"] if kind in ("candidate_page0", "revert_existing_page") else None
        expected = _apply_source(images[expected_base], source, variant["ranges"])
        if sha256(expected) != variant["sha256"]:
            raise AnalysisError(f"{location}.{variant['name']} differs outside its declared mutation")
        if not variant["repaired"] and data_by_name[variant["name"]] != expected:
            raise AnalysisError(f"{location}.{variant['name']} differs outside its declared mutation")
        if variant["sha256"] == checkpoints[expected_base]["sha256"]:
            raise AnalysisError(f"{location}.{variant['name']} did not mutate its base checkpoint")

    summarized = [
        {
            "base_checkpoint": variant["base_checkpoint"],
            "endpoints": variant["endpoints"],
            "kind": variant["kind"],
            "name": variant["name"],
            "outcome": _variant_outcome(variant),
            "page": variant["page"],
            "ranges": variant["ranges"],
        }
        for variant in sorted(variants, key=lambda entry: entry["name"])
    ]
    return {
        "baseline": baseline,
        "checkpoints": checkpoints,
        "complete": True,
        "correlations": correlations,
        "groups": {
            group["name"]: {"kind": kind, "page": group["page"], "ranges": group["ranges"]}
            for kind, groups in (
                ("revert_existing_page", changed),
                ("zero_appended_page", appended),
            )
            for group in groups
        },
        "page0_changed_ranges": page0_ranges,
        "page0_isolated": page0_isolated,
        "page0_values": page0_values,
        "replica": item["replica"],
        "status": "pass",
        "sufficiency": sufficiency,
        "variants": summarized,
    }


def _validate_partial_replica(
    item: dict[str, Any], root: Path, location: str
) -> dict[str, Any]:
    raw = item.get("checkpoints", [])
    if not isinstance(raw, list) or len(raw) > 4:
        raise AnalysisError(f"{location}.checkpoints must contain at most four entries")
    checkpoints: dict[str, dict[str, Any]] = {}
    images: dict[str, bytes] = {}
    databases: set[str] = set()
    for index, value in enumerate(raw):
        checkpoint, data = _checkpoint(value, f"{location}.checkpoints[{index}]", root)
        if checkpoint["name"] in checkpoints or checkpoint["database"] in databases:
            raise AnalysisError(f"{location}.checkpoints contains a duplicate")
        checkpoints[checkpoint["name"]] = checkpoint
        images[checkpoint["name"]] = data
        databases.add(checkpoint["database"])

    if "baseline" in item:
        baseline = _expect_keys(
            item["baseline"],
            {"database", "sha256_before_open", "sha256_after_open", *ENDPOINT_NAMES},
            {"detail"},
            f"{location}.baseline",
        )
        _endpoints(baseline, f"{location}.baseline")
        if "detail" in baseline:
            _detail(baseline["detail"], f"{location}.baseline.detail")
        identity = [
            baseline["database"],
            baseline["sha256_before_open"],
            baseline["sha256_after_open"],
        ]
        if all(value is None for value in identity):
            pass
        elif any(value is None for value in identity):
            raise AnalysisError(f"{location}.baseline has incomplete file identity")
        else:
            if "created" not in checkpoints:
                raise AnalysisError(f"{location}.baseline lacks its created checkpoint")
            _baseline(
                item["baseline"],
                f"{location}.baseline",
                root,
                checkpoints["created"],
                images["created"],
            )
    page0_ranges: dict[str, list[dict[str, int]]] | None = None
    has_page0 = "page0_values" in item or "page0_changed_ranges" in item
    if has_page0:
        values = item.get("page0_values")
        ranges = item.get("page0_changed_ranges")
        if values is None and ranges is None:
            pass
        elif values is None or ranges is None:
            raise AnalysisError(f"{location}.page0 evidence is incomplete")
        else:
            if not {"empty", "created", "renamed"} <= set(checkpoints):
                raise AnalysisError(f"{location}.page0 evidence lacks all checkpoints")
            _, page0_ranges, _ = _page0(values, ranges, images, location)

    correlations: dict[str, dict[str, Any]] | None = None
    if "correlations" in item:
        raw_correlations = _expect_keys(
            item["correlations"], set(CORRELATION_NAMES), set(), f"{location}.correlations"
        )
        if "renamed" in checkpoints:
            dao = checkpoints["renamed"]["dao"]
            created_date = None if dao is None else float(dao["date_created_oadate"])
            updated_date = None if dao is None else float(dao["last_updated_oadate"])
            correlations = {
                "date_created": _reported_date_correlation(
                    raw_correlations["date_created"],
                    f"{location}.correlations.date_created",
                    images["renamed"],
                    created_date,
                    updated_date,
                ),
                "date_updated": _reported_date_correlation(
                    raw_correlations["date_updated"],
                    f"{location}.correlations.date_updated",
                    images["renamed"],
                    updated_date,
                    created_date,
                ),
                "lvprop": _reported_lvprop_correlation(
                    raw_correlations["lvprop"],
                    f"{location}.correlations.lvprop",
                    images.get("property-set", b""),
                    checkpoints.get("property-set", {}).get("dao"),
                ),
            }
        else:
            correlations = {}
            for name in ("date_created", "date_updated"):
                correlation = _expect_keys(
                    raw_correlations[name],
                    {"status", "detail"},
                    {"method", "offsets"},
                    f"{location}.correlations.{name}",
                )
                _detail(correlation["detail"], f"{location}.correlations.{name}.detail")
                if (
                    correlation["status"] != "no_outcome"
                    or "offsets" in correlation
                    or "method" in correlation
                ):
                    raise AnalysisError(
                        f"{location}.correlations.{name} needs a renamed checkpoint"
                    )
                correlations[name] = {"status": "no_outcome"}
            lvprop = _expect_keys(
                raw_correlations["lvprop"],
                {"status", "detail"},
                {"header_offset", "payload_page", "payload_row"},
                f"{location}.correlations.lvprop",
            )
            _detail(lvprop["detail"], f"{location}.correlations.lvprop.detail")
            evidence = [
                lvprop.get(field)
                for field in ("header_offset", "payload_page", "payload_row")
            ]
            if lvprop["status"] != "no_outcome" or any(value is not None for value in evidence):
                raise AnalysisError(f"{location}.correlations.lvprop needs a renamed checkpoint")
            correlations["lvprop"] = {"status": "no_outcome"}

    changed: list[dict[str, Any]] = []
    appended: list[dict[str, Any]] = []
    for field in ("changed_page_groups", "appended_page_groups"):
        if field not in item:
            continue
        if "created" not in checkpoints:
            if item[field] != []:
                raise AnalysisError(f"{location}.{field} lacks the created checkpoint")
            continue
        parsed = _groups(
            item[field],
            f"{location}.{field}",
            checkpoints["created"]["size"],
            checkpoints["created"]["page_count"],
        )
        if field == "changed_page_groups":
            changed = parsed
        else:
            appended = parsed
    group_names = [group["name"] for group in changed + appended]
    if len(group_names) != len(set(group_names)):
        raise AnalysisError(f"{location} repeats a mutation-group name")
    if "empty" in checkpoints and "created" in checkpoints:
        empty_pages = checkpoints["empty"]["page_count"]
        if any(group["page"] == 0 or group["page"] >= empty_pages for group in changed):
            raise AnalysisError(f"{location}.changed_page_groups has an invalid page")
        if any(group["page"] < empty_pages for group in appended):
            raise AnalysisError(f"{location}.appended_page_groups has an invalid page")
        changed_pages = [group["page"] for group in changed]
        appended_pages = [group["page"] for group in appended]
        if len(changed_pages) != len(set(changed_pages)) or len(appended_pages) != len(
            set(appended_pages)
        ):
            raise AnalysisError(f"{location} repeats a mutation-group page")
        for group in changed:
            actual = _difference_ranges(images["empty"], images["created"], group["page"])
            if group["ranges"] != actual:
                raise AnalysisError(f"{location}.{group['name']} differs from checkpoint bytes")
        for group in appended:
            page = group["page"]
            if group["ranges"] != [
                {"start": page * PAGE_BYTES, "end": (page + 1) * PAGE_BYTES}
            ]:
                raise AnalysisError(f"{location}.{group['name']} does not cover its full page")

    if "sufficiency" in item:
        sufficiency = _expect_keys(
            item["sufficiency"],
            {
                "database",
                "size",
                "sha256_before_open",
                "sha256_after_open",
                "endpoints",
            },
            {"detail"},
            f"{location}.sufficiency",
        )
        endpoint_item = _expect_keys(
            sufficiency["endpoints"],
            set(ENDPOINT_NAMES),
            {"detail"},
            f"{location}.sufficiency.endpoints",
        )
        _endpoints(endpoint_item, f"{location}.sufficiency.endpoints")
        if "detail" in endpoint_item:
            _detail(
                endpoint_item["detail"], f"{location}.sufficiency.endpoints.detail"
            )
        if "detail" in sufficiency:
            _detail(sufficiency["detail"], f"{location}.sufficiency.detail")
        identity = [
            sufficiency["database"],
            sufficiency["sha256_before_open"],
            sufficiency["sha256_after_open"],
        ]
        if all(value is None for value in identity):
            if sufficiency["size"] != 0:
                raise AnalysisError(f"{location}.sufficiency empty identity has nonzero size")
        elif any(value is None for value in identity):
            raise AnalysisError(f"{location}.sufficiency has incomplete file identity")
        else:
            if not {"empty", "created"} <= set(checkpoints) or page0_ranges is None:
                raise AnalysisError(
                    f"{location}.sufficiency lacks its reconstruction checkpoints"
                )
            _sufficiency(
                sufficiency,
                f"{location}.sufficiency",
                root,
                images["empty"],
                checkpoints["created"],
                images["created"],
                page0_ranges["empty_to_created"],
                changed + appended,
            )

    if "variants" in item:
        raw_variants = item["variants"]
        if not isinstance(raw_variants, list) or len(raw_variants) > MAX_ITEMS:
            raise AnalysisError(f"{location}.variants must be a bounded array")
        if raw_variants and "created" not in checkpoints:
            raise AnalysisError(f"{location}.variants lack the created checkpoint")
        maximum = checkpoints["created"]["size"] if "created" in checkpoints else PAGE_BYTES
        page_count = (
            checkpoints["created"]["page_count"] if "created" in checkpoints else 1
        )
        parsed = [
            _parse_variant(raw_variant, f"{location}.variants[{index}]", root, maximum, page_count)
            for index, raw_variant in enumerate(raw_variants)
        ]
        variants = [pair[0] for pair in parsed]
        if len({variant["name"] for variant in variants}) != len(variants):
            raise AnalysisError(f"{location}.variants repeats a name")
        groups = {
            group["name"]: (kind, group)
            for kind, entries in (
                ("revert_existing_page", changed),
                ("zero_appended_page", appended),
            )
            for group in entries
        }
        for variant, current in parsed:
            kind = variant["kind"]
            base = "renamed" if kind.startswith("candidate_date_") else "created"
            if variant["base_checkpoint"] != base or base not in images:
                raise AnalysisError(f"{location}.{variant['name']} has an invalid base checkpoint")
            if kind == "candidate_page0":
                if (
                    variant["name"] != "page0-byte-1538"
                    or variant["page"] != 0
                    or variant["ranges"] != [{"start": 1538, "end": 1539}]
                ):
                    raise AnalysisError(f"{location}.{variant['name']} has an invalid page0 range")
                source = images.get("empty")
                if source is None:
                    raise AnalysisError(f"{location}.{variant['name']} lacks the empty checkpoint")
            elif kind.startswith("candidate_date_"):
                field = "date_created" if kind.endswith("created") else "date_updated"
                if correlations is None or correlations[field]["status"] != "resolved":
                    raise AnalysisError(f"{location}.{variant['name']} lacks a resolved correlation")
                expected_ranges = [
                    {"start": offset, "end": offset + 8}
                    for offset in correlations[field]["offsets"]
                ]
                if variant["ranges"] != expected_ranges:
                    raise AnalysisError(f"{location}.{variant['name']} has invalid timestamp ranges")
                expected_name = (
                    "date-created-zero" if field == "date_created" else "date-updated-zero"
                )
                if variant["name"] != expected_name:
                    raise AnalysisError(f"{location}.{variant['name']} has an invalid name")
                source = None
            else:
                expected = groups.get(variant["name"])
                if expected is None or expected[0] != kind or expected[1]["page"] != variant["page"] or expected[1]["ranges"] != variant["ranges"]:
                    raise AnalysisError(f"{location}.{variant['name']} is not a declared mutation group")
                source = images.get("empty") if kind == "revert_existing_page" else None
                if kind == "revert_existing_page" and source is None:
                    raise AnalysisError(f"{location}.{variant['name']} lacks the empty checkpoint")
            expected_bytes = _apply_source(images[base], source, variant["ranges"])
            if sha256(expected_bytes) != variant["sha256"]:
                raise AnalysisError(f"{location}.{variant['name']} differs outside its declared mutation")
            if not variant["repaired"] and current != expected_bytes:
                raise AnalysisError(f"{location}.{variant['name']} differs outside its declared mutation")
            if variant["sha256"] == checkpoints[base]["sha256"]:
                raise AnalysisError(f"{location}.{variant['name']} did not mutate its base checkpoint")
    return {
        "checkpoint_sha256": {
            name: checkpoint["sha256"] for name, checkpoint in sorted(checkpoints.items())
        },
        "complete": False,
        "detail": item["detail"],
        "replica": item["replica"],
        "status": "no_outcome",
    }


def _validate_replica(value: Any, root: Path, location: str) -> dict[str, Any]:
    inventory = {
        "checkpoints",
        "page0_values",
        "page0_changed_ranges",
        "baseline",
        "correlations",
        "changed_page_groups",
        "appended_page_groups",
        "sufficiency",
        "variants",
    }
    item = _expect_keys(value, {"replica", "status", "detail"}, inventory, location)
    item["replica"] = _integer(item["replica"], f"{location}.replica", 1, 3)
    _detail(item["detail"], f"{location}.detail")
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError(f"{location}.status must be pass or fail")
    if item["status"] == "fail":
        return _validate_partial_replica(item, root, location)
    missing = inventory - set(item)
    if missing:
        raise AnalysisError(
            f"{location} is missing completed inventories: {', '.join(sorted(missing))}"
        )
    return _validate_complete_replica(item, root, location)


def _aggregate(values: list[str], label: str) -> dict[str, Any]:
    if "no_outcome" in values:
        return {
            "reason": f"at least one {label} observation changed during DAO open",
            "status": "no_outcome",
        }
    if len(set(values)) == 1:
        return {"outcome": values[0], "status": "answered"}
    return {"reason": f"replicas disagree for {label}", "status": "no_outcome"}


def _aggregate_variant_observations(
    observations: list[dict[str, Any]], label: str
) -> dict[str, Any]:
    endpoint_maps = [observation["endpoints"] for observation in observations]
    if any(endpoints != endpoint_maps[0] for endpoints in endpoint_maps[1:]):
        return {
            "reason": f"replicas disagree on the DAO endpoint map for {label}",
            "status": "no_outcome",
        }
    aggregate = _aggregate(
        [observation["outcome"] for observation in observations], label
    )
    return {**aggregate, "endpoints": endpoint_maps[0]}


def _resolved_correlation(
    replicas: list[dict[str, Any]], field: str, unresolved_reason: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    correlations = [replica["correlations"][field] for replica in replicas]
    if any(correlation["status"] != "resolved" for correlation in correlations):
        return None, {"reason": unresolved_reason, "status": "no_outcome"}
    if any(correlation != correlations[0] for correlation in correlations[1:]):
        return None, {
            "reason": f"replicas disagree on the {field} correlation evidence",
            "status": "no_outcome",
        }
    return correlations[0], None


def build_report(document: dict[str, Any], replicas: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(replica["complete"] for replica in replicas)
    if not complete:
        reason = "at least one replica recorded a partial no_outcome"
        questions = {
            name: {"reason": reason, "status": "no_outcome"}
            for name in (
                "candidate_page0",
                "candidate_catalog_fields",
                "composed_image_sufficiency",
                "required_mutation_groups",
            )
        }
    else:
        page0 = (
            _aggregate_variant_observations(
                [
                    next(
                        variant
                        for variant in replica["variants"]
                        if variant["kind"] == "candidate_page0"
                    )
                    for replica in replicas
                ],
                "candidate_page0",
            )
            if all(replica["page0_isolated"] for replica in replicas)
            else {
                "reason": "create-and-rename page0 changes were not isolated to byte 1538",
                "status": "no_outcome",
            }
        )
        fields: dict[str, dict[str, Any]] = {}
        for field, kind in (
            ("date_created", "candidate_date_created"),
            ("date_updated", "candidate_date_updated"),
        ):
            correlation, no_outcome = _resolved_correlation(
                replicas,
                field,
                "at least one replica did not resolve the correlation",
            )
            if no_outcome is not None:
                fields[field] = no_outcome
            else:
                observations = [
                    next(
                        variant
                        for variant in replica["variants"]
                        if variant["kind"] == kind
                    )
                    for replica in replicas
                ]
                fields[field] = {
                    **_aggregate_variant_observations(observations, field),
                    "evidence": correlation,
                }
        lvprop_correlation, lvprop_no_outcome = _resolved_correlation(
            replicas,
            "lvprop",
            "at least one replica did not resolve the structural correlation",
        )
        fields["lvprop"] = (
            lvprop_no_outcome
            if lvprop_no_outcome is not None
            else {
                "evidence": lvprop_correlation,
                "outcome": "resolved",
                "status": "answered",
            }
        )
        catalog = {
            "fields": fields,
            "status": (
                "answered"
                if all(field["status"] == "answered" for field in fields.values())
                else "no_outcome"
            ),
        }
        group_names = [set(replica["groups"]) for replica in replicas]
        groups: dict[str, dict[str, Any]] = {}
        groups_status = "answered"
        groups_reason = ""
        if any(names != group_names[0] for names in group_names[1:]):
            groups_status = "no_outcome"
            groups_reason = "replicas disagree on mutation-group names"
        else:
            for name in sorted(group_names[0]):
                definitions = [replica["groups"][name] for replica in replicas]
                if any(definition != definitions[0] for definition in definitions[1:]):
                    groups[name] = {
                        "reason": "replicas disagree on the mutation-group definition",
                        "status": "no_outcome",
                    }
                    groups_status = "no_outcome"
                    continue
                observations = [
                    next(
                        variant
                        for variant in replica["variants"]
                        if variant["name"] == name
                    )
                    for replica in replicas
                ]
                aggregate = _aggregate_variant_observations(observations, name)
                groups[name] = {**definitions[0], **aggregate}
                if aggregate["status"] == "no_outcome":
                    groups_status = "no_outcome"
        mutation_groups: dict[str, Any] = {"groups": groups, "status": groups_status}
        if groups_reason:
            mutation_groups["reason"] = groups_reason
        baseline_failed = any(
            replica["baseline"]["repaired"]
            or not all(replica["baseline"]["endpoints"].values())
            for replica in replicas
        )
        endpoint_maps = [replica["sufficiency"]["endpoints"] for replica in replicas]
        if baseline_failed:
            sufficiency = {
                "reason": "at least one created baseline failed or changed during DAO open",
                "status": "no_outcome",
            }
        elif any(endpoints != endpoint_maps[0] for endpoints in endpoint_maps[1:]):
            sufficiency = {
                "reason": "replicas disagree on the composed-image DAO endpoint map",
                "status": "no_outcome",
            }
        else:
            sufficiency = {
                **_aggregate(
                    [
                        "no_outcome"
                        if replica["sufficiency"]["repaired"]
                        else (
                            "observed_sufficient"
                            if all(replica["sufficiency"]["endpoints"].values())
                            else "not_observed_sufficient"
                        )
                        for replica in replicas
                    ],
                    "composed_image_sufficiency",
                ),
                "endpoints": endpoint_maps[0],
            }
        questions = {
            "candidate_catalog_fields": catalog,
            "candidate_page0": page0,
            "composed_image_sufficiency": sufficiency,
            "required_mutation_groups": mutation_groups,
        }

    summaries = []
    for replica in replicas:
        if not replica["complete"]:
            summaries.append(
                {
                    "checkpoint_sha256": replica["checkpoint_sha256"],
                    "detail": replica["detail"],
                    "replica": replica["replica"],
                    "status": "no_outcome",
                }
            )
            continue
        summaries.append(
            {
                "baseline_passed": (
                    not replica["baseline"]["repaired"]
                    and all(replica["baseline"]["endpoints"].values())
                ),
                "checkpoint_sha256": {
                    name: replica["checkpoints"][name]["sha256"] for name in CHECKPOINT_NAMES
                },
                "correlations": {
                    name: replica["correlations"][name]["status"]
                    for name in CORRELATION_NAMES
                },
                "mutation_group_count": len(replica["groups"]),
                "page0_changed_ranges": replica["page0_changed_ranges"],
                "page0_values": replica["page0_values"],
                "replica": replica["replica"],
                "status": "pass",
                "sufficiency": {
                    "endpoints": replica["sufficiency"]["endpoints"],
                    "repaired": replica["sufficiency"]["repaired"],
                },
                "variants": replica["variants"],
            }
        )
    status = "accepted"
    if (
        document["status"] != "pass"
        or not complete
        or any(
            replica["baseline"]["repaired"]
            or not all(replica["baseline"]["endpoints"].values())
            for replica in replicas
            if replica["complete"]
        )
        or any(question["status"] == "no_outcome" for question in questions.values())
    ):
        status = "no_outcome"
    bounded_sufficiency = (
        status == "accepted"
        and questions["composed_image_sufficiency"].get("outcome")
        == "observed_sufficient"
    )
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": "bootstrap_layout_report",
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": summaries,
        "status": status,
        "sufficiency_claim": bounded_sufficiency,
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected = _digest(expected_plan_sha256, "--expected-plan-sha256")
    document = load_json(job_result)
    item = _expect_keys(
        document,
        {"development_only", "status", "detail", "plan_sha256", "replicas"},
        set(),
        "$",
    )
    if item["development_only"] is not True:
        raise AnalysisError("$.development_only must be true")
    if item["status"] not in ("pass", "fail"):
        raise AnalysisError("$.status must be pass or fail")
    _detail(item["detail"], "$.detail")
    if _digest(item["plan_sha256"], "$.plan_sha256") != expected:
        raise AnalysisError("job result plan digest differs from the approved plan")
    raw_replicas = item["replicas"]
    if not isinstance(raw_replicas, list) or len(raw_replicas) != REPLICA_COUNT:
        raise AnalysisError("$.replicas must contain exactly three replicas")
    replicas = [
        _validate_replica(raw, job_result.parent, f"$.replicas[{index}]")
        for index, raw in enumerate(raw_replicas)
    ]
    indexes = [replica["replica"] for replica in replicas]
    if sorted(indexes) != [1, 2, 3] or len(indexes) != len(set(indexes)):
        raise AnalysisError("$.replicas must be indexed exactly 1 through 3")
    replicas.sort(key=lambda replica: replica["replica"])
    report = build_report(item, replicas)
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
        print(f"bootstrap-layout analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
