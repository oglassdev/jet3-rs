#!/usr/bin/env python3
"""Validate the bounded schema-generalization experiment for issue #100."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import system_catalog as catalog


PAGE_BYTES = 2048
ENTRY_AREA_OFFSET = 248
ENTRY_AREA_LENGTH = PAGE_BYTES - ENTRY_AREA_OFFSET
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_PAGES = 512
MAX_PROBE_TABLES = 24
PROBE_RANGES = ((0x20, 0x7E), (0xA0, 0xFF))
EXCLUDED_PROBE_BYTES = frozenset({0x21, 0x2E, 0x5B, 0x5D, 0x60})
PROBE_GROUP_SIZE = 16
MAX_FIELDS = 16
MAX_INDEXES = 8
MAX_TEXT = 512
DOCUMENT_TYPE = "dao_schema_generalization_job_result"
REPORT_TYPE = "schema_generalization_report"
SCHEMA_CHECKPOINTS = ("empty", "alpha", "beta", "gamma", "delta")
CHECKPOINTS = (*SCHEMA_CHECKPOINTS, "names")
CREATED_TABLES = {"alpha": "Alpha", "beta": "Beta", "gamma": "Gamma", "delta": "Delta"}
# The exact schema each preregistered create must produce, as DAO reports it.
EXPECTED_SCHEMA: dict[str, dict[str, Any]] = {
    "Alpha": {
        "fields": [{"name": "Id", "size": 4, "type": 4}],
        "indexes": [],
    },
    "Beta": {
        "fields": [
            {"name": "Id", "size": 4, "type": 4},
            {"name": "Name", "size": 50, "type": 10},
            {"name": "Note", "size": 0, "type": 12},
        ],
        "indexes": [],
    },
    "Gamma": {
        "fields": [{"name": "Id", "size": 4, "type": 4}],
        "indexes": [
            {"fields": ["Id"], "name": "PrimaryKey", "primary": True, "unique": True}
        ],
    },
    "Delta": {
        "fields": [{"name": "Label", "size": 30, "type": 10}],
        "indexes": [
            {"fields": ["Label"], "name": "ByLabel", "primary": False, "unique": False}
        ],
    },
}
PAGE0_COUNTER = 1538
UNASSIGNED_ROLE = "unassigned"
LONG_KEY_MARKER = 0x7F
TEXT_KEY_MARKER = 0x7F
TEXT_KEY_TERMINATOR = 0x00
PROPERTY_MAGIC = b"KKD\x00"
PROPERTY_NAME_CHUNK = 0x0080
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")
SAFE_MDB = re.compile(
    r"^schema-generalization-r[1-3]-(empty|alpha|beta|gamma|delta|names)[.]mdb$"
)
QUESTION_NAMES = (
    "name_key_framing",
    "ascii_name_collation",
    "extended_name_keys",
    "object_and_ace_rows",
    "page_zero_and_page_assignment",
    "long_value_property_framing",
)


class AnalysisError(ValueError):
    """The producer result or retained artifact failed an integrity check."""


class DecodeError(ValueError):
    """A bounded checkpoint did not decode under the pinned hypotheses."""


# --- bounded document validation ---------------------------------------------


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def load_document(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AnalysisError("job result must be a regular file")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise AnalysisError("job result exceeds the JSON bound")
    try:
        value = json.loads(raw, object_pairs_hook=object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("job result is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AnalysisError("job result root must be an object")
    return value


def exact_object(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AnalysisError(f"{location} must contain exactly {sorted(keys)}")
    return value


def digest(value: Any, location: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise AnalysisError(f"{location} must be a lowercase SHA-256 digest")
    return value


def bounded_text(value: Any, location: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AnalysisError(f"{location} must be a string of at most {maximum} characters")
    return value


def bounded_integer(value: Any, location: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AnalysisError(f"{location} must be an integer in [{minimum},{maximum}]")
    return value


def validate_dao(value: Any, location: str) -> dict[str, Any]:
    item = exact_object(value, {"tabledefs"}, location)
    rows = item["tabledefs"]
    if not isinstance(rows, list) or len(rows) > catalog.MAX_TABLES + MAX_PROBE_TABLES:
        raise AnalysisError(f"{location}.tabledefs exceeds the table bound")
    for index, raw in enumerate(rows):
        row = exact_object(
            raw,
            {"name", "attributes", "date_created", "last_updated", "fields", "indexes", "error"},
            f"{location}.tabledefs[{index}]",
        )
        if row["error"] is not None:
            bounded_text(row["error"], f"{location}.tabledefs[{index}].error")
            continue
        bounded_text(row["name"], f"{location}.tabledefs[{index}].name", 256)
        bounded_integer(
            row["attributes"], f"{location}.tabledefs[{index}].attributes", -(1 << 31), (1 << 31) - 1
        )
        for key in ("date_created", "last_updated"):
            if not isinstance(row[key], (int, float)) or isinstance(row[key], bool):
                raise AnalysisError(f"{location}.tabledefs[{index}].{key} must be a number")
        fields = row["fields"]
        indexes = row["indexes"]
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise AnalysisError(f"{location}.tabledefs[{index}].fields exceeds the bound")
        if not isinstance(indexes, list) or len(indexes) > MAX_INDEXES:
            raise AnalysisError(f"{location}.tabledefs[{index}].indexes exceeds the bound")
        for position, entry in enumerate(fields):
            field = exact_object(
                entry, {"name", "type", "size"}, f"{location}.tabledefs[{index}].fields[{position}]"
            )
            bounded_text(field["name"], "field name", 256)
            bounded_integer(field["type"], "field type", 0, 1 << 16)
            bounded_integer(field["size"], "field size", 0, 1 << 20)
        for position, entry in enumerate(indexes):
            index_row = exact_object(
                entry,
                {"name", "primary", "unique", "fields"},
                f"{location}.tabledefs[{index}].indexes[{position}]",
            )
            bounded_text(index_row["name"], "index name", 256)
            for key in ("primary", "unique"):
                if not isinstance(index_row[key], bool):
                    raise AnalysisError(f"index {key} must be boolean")
            names = index_row["fields"]
            if not isinstance(names, list) or len(names) > MAX_FIELDS:
                raise AnalysisError("index fields exceed the bound")
            for name in names:
                bounded_text(name, "index field name", 256)
    return item


def read_checkpoint(
    root: Path, value: Any, replica: int, name: str
) -> tuple[bytes, bool, dict[str, Any]]:
    item = exact_object(
        value,
        {"name", "database", "size", "sha256", "sha256_after_metadata", "dao"},
        f"replica {replica} checkpoint {name}",
    )
    if item["name"] != name:
        raise AnalysisError(f"replica {replica} checkpoint order is invalid")
    database = item["database"]
    expected_name = f"schema-generalization-r{replica}-{name}.mdb"
    if database != expected_name or not SAFE_MDB.fullmatch(database):
        raise AnalysisError(f"replica {replica} checkpoint filename is invalid")
    size = item["size"]
    if (
        type(size) is not int
        or size < PAGE_BYTES
        or size > MAX_PAGES * PAGE_BYTES
        or size % PAGE_BYTES
    ):
        raise AnalysisError(f"replica {replica} checkpoint size is invalid")
    before = digest(item["sha256"], f"replica {replica} checkpoint digest")
    after = digest(item["sha256_after_metadata"], f"replica {replica} post-metadata digest")
    dao = validate_dao(item["dao"], f"replica {replica} checkpoint {name}.dao")
    path = root / database
    if not path.is_file() or path.is_symlink():
        raise AnalysisError(f"replica {replica} checkpoint is missing or not regular")
    raw = path.read_bytes()
    repaired = before != after
    retained = after if repaired else before
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != retained:
        raise AnalysisError(f"replica {replica} checkpoint bytes differ from metadata")
    return raw, repaired, dao


def expected_probe_inventory() -> list[dict[str, Any]]:
    """Rebuild the preregistered probed-name inventory from the pinned rules.

    The analyzer derives this independently of the producer so an incomplete,
    reordered, or differently constructed probe set is an inventory violation
    rather than a silently smaller observation.
    """
    inventory: list[dict[str, Any]] = []
    group = 0
    for first, last in PROBE_RANGES:
        probed = [
            value
            for value in range(first, last + 1)
            if value not in EXCLUDED_PROBE_BYTES
        ]
        for start in range(0, len(probed), PROBE_GROUP_SIZE):
            chunk = probed[start : start + PROBE_GROUP_SIZE]
            group += 1
            for suffix, points in (("Q", chunk), ("R", list(reversed(chunk)))):
                name = "P" + f"{group:02d}" + "".join(chr(point) for point in points) + suffix
                inventory.append({"code_points": points, "name": name})
    return inventory


def read_probe_attempts(
    value: Any, replica: int, *, complete: bool
) -> list[dict[str, Any]]:
    """Check the probed-name inventory against the pinned reconstruction.

    A replica that failed after its first mutation may have stopped before or
    during the probe phase, so its attempts are required to be an ordered
    prefix rather than the whole inventory. That keeps a post-mutation partial
    failure an honest no_outcome instead of a validation rejection.
    """
    expected = expected_probe_inventory()
    if len(expected) != MAX_PROBE_TABLES:
        raise AnalysisError("the pinned probe inventory does not hold 24 names")
    if not isinstance(value, list) or len(value) > len(expected):
        raise AnalysisError(f"replica {replica} probe attempts exceed the bound")
    if complete and len(value) != len(expected):
        raise AnalysisError(
            f"replica {replica} did not attempt exactly {len(expected)} probed names"
        )
    attempts = []
    for index, raw in enumerate(value):
        item = exact_object(
            raw, {"name", "code_points", "created", "error"}, f"replica {replica} probe {index}"
        )
        bounded_text(item["name"], f"replica {replica} probe {index} name", 64)
        points = item["code_points"]
        if not isinstance(points, list) or len(points) > 64:
            raise AnalysisError(f"replica {replica} probe {index} code points exceed the bound")
        for point in points:
            bounded_integer(point, f"replica {replica} probe {index} code point", 0x20, 0xFF)
        if not isinstance(item["created"], bool):
            raise AnalysisError(f"replica {replica} probe {index} created must be boolean")
        if item["error"] is not None:
            bounded_text(item["error"], f"replica {replica} probe {index} error")
        if item["created"] == (item["error"] is not None):
            raise AnalysisError(f"replica {replica} probe {index} mixes creation and failure")
        if item["name"] != expected[index]["name"] or points != expected[index]["code_points"]:
            raise AnalysisError(
                f"replica {replica} probe {index} differs from the preregistered inventory"
            )
        attempts.append(
            {
                "code_points": points,
                "created": item["created"],
                "error": item["error"],
                "name": item["name"],
            }
        )
    return attempts


# --- pinned structural decoding ----------------------------------------------


def index_boundaries(page: bytes, what: str) -> list[int]:
    boundaries: list[int] = []
    for byte_index, value in enumerate(page[22:ENTRY_AREA_OFFSET]):
        for bit in range(8):
            if value & (1 << bit):
                boundary = byte_index * 8 + bit
                if boundary > ENTRY_AREA_LENGTH:
                    raise DecodeError(f"{what} has a boundary outside its entry area")
                boundaries.append(boundary)
    return boundaries


def parent_name_root(definition: dict[str, Any]) -> int:
    """Locate the physical index keyed by ParentId then Name."""
    columns = definition["columns"]
    matches = []
    for entry in definition["physical_indexes"]:
        try:
            names = [columns[key["column"]]["name"] for key in entry["keys"]]
        except (IndexError, KeyError, TypeError) as error:
            raise DecodeError("a physical index has malformed key-column linkage") from error
        if names == ["ParentId", "Name"]:
            matches.append(entry)
    if len(matches) != 1:
        raise DecodeError(f"the catalog has {len(matches)} ParentId/Name physical indexes")
    root = matches[0]["root"]
    if type(root) is not int:
        raise DecodeError("the ParentId/Name index root is not an integer")
    return root


def leaf_index_entries(
    data: bytes, root: int, owner: int, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Losslessly reconstruct one single-leaf index root under EXP-0062."""
    what = f"index page {root}"
    page = catalog._page(data, root, what)
    if page[0] != 4 or page[1] != 1 or int.from_bytes(page[4:8], "little") != owner:
        raise DecodeError(f"{what} is not the leaf root owned by page {owner}")
    if any(page[offset : offset + 4] != b"\0\0\0\0" for offset in (8, 12, 16)):
        raise DecodeError(f"{what} has an unexpected sibling or child reference")
    if page[21] != 0:
        raise DecodeError(f"{what} is not a leaf; branched roots are outside this experiment")
    area = page[ENTRY_AREA_OFFSET:]
    prefix_length = page[20]
    boundaries = index_boundaries(page, what)
    expected_free = ENTRY_AREA_LENGTH - (boundaries[-1] if boundaries else 0)
    if int.from_bytes(page[2:4], "little") != expected_free:
        raise DecodeError(f"{what} free space disagrees with its boundary bitmap")
    row_by_locator = {(row["page"], row["row"]): row for row in rows}
    entries: list[dict[str, Any]] = []
    prior = prefix_length
    for ordinal, boundary in enumerate(boundaries):
        if boundary <= prior:
            raise DecodeError(f"{what} has a reversed or repeated boundary")
        suffix = area[prior:boundary]
        if len(suffix) <= 4:
            raise DecodeError(f"{what} entry {ordinal} is too short")
        trailer = suffix[-4:]
        row_page = int.from_bytes(trailer[:3], "big")
        row_slot = trailer[3]
        row = row_by_locator.get((row_page, row_slot))
        if row is None:
            raise DecodeError(f"{what} entry {ordinal} has no catalog row")
        entries.append(
            {
                "key": area[:prefix_length] + suffix[:-4],
                "row": row,
                "row_page": row_page,
                "row_slot": row_slot,
            }
        )
        prior = boundary
    if len(entries) != len(rows):
        raise DecodeError(f"{what} does not contain exactly one entry per catalog row")
    if len({(entry["row_page"], entry["row_slot"]) for entry in entries}) != len(entries):
        raise DecodeError(f"{what} repeats a catalog row locator")
    return entries


def split_parent_name_key(key: bytes, what: str) -> tuple[int, bytes, list[int]]:
    """Split one non-null Long/Text composite key into its pinned sections.

    The text component is a run of primary weight bytes, none of which has a
    zero high nibble, followed by a nibble stream: one leading zero nibble,
    zero or more secondary nibbles, one terminating zero nibble, and zero
    padding to the byte boundary.
    """
    if len(key) < 7 or key[0] != LONG_KEY_MARKER or key[5] != TEXT_KEY_MARKER:
        raise DecodeError(f"{what} does not carry two non-null EXP-0062 key markers")
    parent = int.from_bytes(key[1:5], "big") ^ 0x8000_0000
    if parent >= 1 << 31:
        parent -= 1 << 32
    text = key[6:]
    boundary = next((index for index, byte in enumerate(text) if byte >> 4 == 0), None)
    if boundary is None:
        raise DecodeError(f"{what} text component has no secondary section")
    primary = text[:boundary]
    if not primary:
        raise DecodeError(f"{what} text component has no primary weight")
    nibbles = [nibble for byte in text[boundary:] for nibble in (byte >> 4, byte & 0x0F)]
    try:
        terminator = nibbles.index(TEXT_KEY_TERMINATOR, 1)
    except ValueError:
        raise DecodeError(f"{what} secondary section is unterminated") from None
    if any(nibble != 0 for nibble in nibbles[terminator:]):
        raise DecodeError(f"{what} secondary padding is nonzero")
    return parent, primary, nibbles[1:terminator]


def catalog_name_keys(data: bytes) -> list[dict[str, Any]]:
    """Record every lossless ParentId/Name key with its correlated catalog row."""
    definition, _, rows = catalog._discover_catalog(data)
    id_ordinal = catalog._ordinal(definition, "Id")
    parent_ordinal = catalog._ordinal(definition, "ParentId")
    name_ordinal = catalog._ordinal(definition, "Name")
    if None in (id_ordinal, parent_ordinal, name_ordinal):
        raise DecodeError("the catalog lacks an Id, ParentId, or Name column")
    observations = []
    for entry in leaf_index_entries(
        data, parent_name_root(definition), definition["root"], rows
    ):
        values = entry["row"]["values"]
        name = values[name_ordinal]
        parent_value = values[parent_ordinal]
        identity = values[id_ordinal]
        if not isinstance(name, str) or type(parent_value) is not int or type(identity) is not int:
            raise DecodeError("a catalog row has malformed identity fields")
        what = f"catalog key for {name!r}"
        parent, primary, secondary = split_parent_name_key(entry["key"], what)
        if parent != parent_value:
            raise DecodeError(f"{what} names parent {parent} but its row names {parent_value}")
        try:
            name_bytes = name.encode("cp1252")
        except UnicodeEncodeError as error:
            raise DecodeError(f"{what} row name is not representable in CP1252") from error
        observations.append(
            {
                "id": identity,
                "key_hex": entry["key"].hex(),
                "name": name,
                "name_hex": name_bytes.hex(),
                "parent_id": parent,
                "primary_hex": primary.hex(),
                "row_page": entry["row_page"],
                "row_slot": entry["row_slot"],
                "secondary_nibbles": secondary,
            }
        )
    observations.sort(key=lambda item: (item["parent_id"], item["name"]))
    return observations


def property_chunks(payload: bytes) -> list[dict[str, Any]]:
    """Decompose one long-value property payload under the pinned chunk framing."""
    if payload[: len(PROPERTY_MAGIC)] != PROPERTY_MAGIC:
        raise DecodeError("the property payload lacks the KKD magic")
    offset = len(PROPERTY_MAGIC)
    chunks: list[dict[str, Any]] = []
    while offset < len(payload):
        if offset + 6 > len(payload):
            raise DecodeError("a property chunk header is truncated")
        length = int.from_bytes(payload[offset : offset + 4], "little")
        kind = int.from_bytes(payload[offset + 4 : offset + 6], "little")
        if length < 6 or offset + length > len(payload):
            raise DecodeError(f"property chunk at {offset} has invalid length {length}")
        body = payload[offset + 6 : offset + length]
        chunk: dict[str, Any] = {
            "body_hex": body.hex(),
            "kind": kind,
            "length": length,
            "offset": offset,
        }
        if kind == PROPERTY_NAME_CHUNK:
            names = []
            position = 0
            while position < len(body):
                if position + 2 > len(body):
                    raise DecodeError("a property name entry is truncated")
                size = int.from_bytes(body[position : position + 2], "little")
                position += 2
                if position + size > len(body):
                    raise DecodeError("a property name exceeds its chunk")
                names.append(body[position : position + size].hex())
                position += size
            chunk["name_entries_hex"] = names
        chunks.append(chunk)
        offset += length
    if not chunks:
        raise DecodeError("the property payload holds no chunk")
    return chunks


def long_value_payload(data: bytes, value: Any, what: str) -> dict[str, Any]:
    """Follow one EXP-0061 single-page external header to its exact row bytes."""
    if not isinstance(value, dict) or set(value) != {"inline_length", "long_value_header_hex"}:
        raise DecodeError(f"{what} is not one external long-value header")
    try:
        header = bytes.fromhex(value["long_value_header_hex"])
    except (TypeError, ValueError) as error:
        raise DecodeError(f"{what} header is not hex") from error
    if len(header) != 12 or value["inline_length"] != 12:
        raise DecodeError(f"{what} header is not 12 bytes")
    if header[8:12] != b"\0\0\0\0":
        raise DecodeError(f"{what} reserved header bytes are nonzero")
    control = int.from_bytes(header[:4], "little")
    if control & 0xFF000000 != 0x40000000:
        raise DecodeError(f"{what} is not the observed single-page external form")
    length = control & 0x00FFFFFF
    row_slot = header[4]
    page_number = int.from_bytes(header[5:8], "little")
    if length == 0 or length > PAGE_BYTES or page_number >= len(data) // PAGE_BYTES:
        raise DecodeError(f"{what} external reference is outside the bound")
    page = catalog._page(data, page_number, what)
    if page[0] != 1 or page[4:8] != b"LVAL":
        raise DecodeError(f"{what} does not target an LVAL data page")
    directory = catalog._row_directory(page, page_number)
    if row_slot >= len(directory):
        raise DecodeError(f"{what} row slot is absent")
    row = directory[row_slot]
    if row["hidden"] or row["overflow"]:
        raise DecodeError(f"{what} targets a flagged row")
    payload = page[row["start"] : row["end"]]
    if len(payload) != length:
        raise DecodeError(f"{what} payload length disagrees with its header")
    return {
        "chunks": property_chunks(payload),
        "header_hex": header.hex(),
        "length": length,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "row": row_slot,
    }


# --- per-replica observation ---------------------------------------------------


def catalog_table_rows(analysis: dict[str, Any], name: str) -> list[dict[str, Any]]:
    entry = analysis["system_rows"].get(name)
    if entry is None:
        raise DecodeError(f"the image has no {name} system table")
    definition = analysis["tables"][entry["root"]]["definition"]
    columns = definition["columns"]
    rows = [
        {column["name"]: row["values"][column["ordinal"]] for column in columns}
        for row in entry["rows"]
    ]
    return catalog._mask_dates(rows, columns)


def row_difference(before: list[Any], after: list[Any]) -> dict[str, list[Any]]:
    """Report the exact rows a create adds and any it removes or rewrites."""
    remaining = [json.dumps(row, sort_keys=True) for row in before]
    added = []
    for row in after:
        encoded = json.dumps(row, sort_keys=True)
        if encoded in remaining:
            remaining.remove(encoded)
        else:
            added.append(row)
    removed = [json.loads(encoded) for encoded in remaining]
    return {"added": added, "removed": removed}


def page_roles(analysis: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {entry["page"]: entry for entry in analysis["pages"]}


def analyze_schema_transition(
    before: bytes, after: bytes, before_analysis: dict[str, Any], after_analysis: dict[str, Any]
) -> dict[str, Any]:
    before_pages = len(before) // PAGE_BYTES
    after_pages = len(after) // PAGE_BYTES
    if after_pages < before_pages:
        raise DecodeError("a create shrank the database image")
    roles = page_roles(after_analysis)
    appended = []
    for page in range(before_pages, after_pages):
        entry = roles.get(page)
        # The role decoder emits an entry for every page and falls back to
        # "unassigned", so an unattributed appended page must fail H5 here.
        if entry is None or entry["role"] == UNASSIGNED_ROLE:
            raise DecodeError(f"appended page {page} is not attributed to a decoded structure")
        appended.append({"owners": entry["owners"], "page": page, "role": entry["role"]})
    changed = [
        offset
        for offset in range(PAGE_BYTES)
        if before[offset] != after[offset]
    ]
    return {
        "appended_pages": appended,
        "objects": row_difference(
            catalog_table_rows(before_analysis, "MSysObjects"),
            catalog_table_rows(after_analysis, "MSysObjects"),
        ),
        "aces": row_difference(
            catalog_table_rows(before_analysis, "MSysACEs"),
            catalog_table_rows(after_analysis, "MSysACEs"),
        ),
        "page_count": {"after": after_pages, "before": before_pages},
        "page_zero": {
            "after": after[PAGE0_COUNTER],
            "before": before[PAGE0_COUNTER],
            "changed_offsets": changed,
        },
    }


def analyze_long_values(data: bytes, analysis: dict[str, Any]) -> dict[str, Any]:
    definition = analysis["tables"][analysis["catalog_root"]]["definition"]
    name_ordinal = catalog._ordinal(definition, "Name")
    lvprop_ordinal = catalog._ordinal(definition, "LvProp")
    if name_ordinal is None or lvprop_ordinal is None:
        raise DecodeError("the catalog lacks a Name or LvProp column")
    entry = analysis["system_rows"]["MSysObjects"]
    observed: dict[str, Any] = {}
    for row in entry["rows"]:
        values = row["values"]
        if len(values) <= max(name_ordinal, lvprop_ordinal):
            raise DecodeError("a catalog row is too short for Name and LvProp")
        name = values[name_ordinal]
        value = values[lvprop_ordinal]
        if not isinstance(name, str) or value is None:
            continue
        if name in observed:
            raise DecodeError(f"the catalog holds two rows named {name!r}")
        observed[name] = long_value_payload(data, value, f"{name}.LvProp")
    return observed


def correlate_probe_attempts(
    attempts: list[dict[str, Any]], name_keys: list[dict[str, Any]]
) -> None:
    """Require each probe outcome to agree with the catalog that run captured.

    DAO can raise after partially completing a mutation, so a name reported as
    created must be present and a name reported as rejected must be absent.
    """
    present = {observation["name"] for observation in name_keys}
    for attempt in attempts:
        if attempt["created"] and attempt["name"] not in present:
            raise DecodeError(
                f"probed name {attempt['name']!r} was created but is absent from the catalog"
            )
        if not attempt["created"] and attempt["name"] in present:
            raise DecodeError(
                f"probed name {attempt['name']!r} was rejected but is present in the catalog"
            )


def dao_user_tables(dao: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tables = {}
    for row in dao["tabledefs"]:
        if row["error"] is not None or row["name"].startswith("MSys"):
            continue
        tables[row["name"]] = {
            "fields": [
                {"name": field["name"], "size": field["size"], "type": field["type"]}
                for field in row["fields"]
            ],
            "indexes": sorted(
                (
                    {
                        "fields": list(entry["fields"]),
                        "name": entry["name"],
                        "primary": entry["primary"],
                        "unique": entry["unique"],
                    }
                    for entry in row["indexes"]
                ),
                key=lambda entry: entry["name"],
            ),
        }
    return tables


def correlate_schema_mutations(
    analyses: dict[str, dict[str, Any]], snapshots: dict[str, dict[str, Any]]
) -> None:
    """Require each fixed create to have produced its preregistered table.

    Without this, a no-op or incomplete mutation would still be labelled with
    the table the plan intended to create.
    """
    created: list[str] = []
    for checkpoint in SCHEMA_CHECKPOINTS:
        if checkpoint in CREATED_TABLES:
            created.append(CREATED_TABLES[checkpoint])
        decoded = sorted(
            entry["name"]
            for entry in analyses[checkpoint]["tables"].values()
            if not entry["flags"] & catalog.SYSTEM_FLAG
        )
        if decoded != sorted(created):
            raise DecodeError(
                f"checkpoint {checkpoint} decodes user tables {decoded}; expected {sorted(created)}"
            )
        observed = dao_user_tables(snapshots[checkpoint])
        if sorted(observed) != sorted(created):
            raise DecodeError(
                f"checkpoint {checkpoint} reports DAO tables {sorted(observed)}; expected {sorted(created)}"
            )
        for name in created:
            if observed[name] != EXPECTED_SCHEMA[name]:
                raise DecodeError(
                    f"checkpoint {checkpoint} table {name} differs from its preregistered schema"
                )


def analyze_replica(
    images: dict[str, bytes],
    snapshots: dict[str, dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    analyses = {
        name: catalog.analyze_checkpoint(images[name]) for name in SCHEMA_CHECKPOINTS
    }
    correlate_schema_mutations(analyses, snapshots)
    transitions = {}
    for index, name in enumerate(SCHEMA_CHECKPOINTS[1:]):
        previous = SCHEMA_CHECKPOINTS[index]
        transitions[name] = analyze_schema_transition(
            images[previous], images[name], analyses[previous], analyses[name]
        )
    name_keys = catalog_name_keys(images["names"])
    correlate_probe_attempts(attempts, name_keys)
    return {
        "long_values": analyze_long_values(images["delta"], analyses["delta"]),
        "name_keys": name_keys,
        "schema_keys": catalog_name_keys(images["delta"]),
        "transitions": transitions,
    }


# --- question aggregation ------------------------------------------------------


def is_ascii_name(observation: dict[str, Any]) -> bool:
    name = bytes.fromhex(observation["name_hex"])
    return bool(name) and max(name) <= 0x7E


def collation_map(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive one context-free primary weight map from the ASCII name keys.

    An ASCII name is admitted only when its primary section holds exactly one
    weight per name byte and its secondary section is empty, so each weight
    aligns with exactly one source byte without solving for expansions.
    """
    mapping: dict[int, int] = {}
    conflicts: list[dict[str, Any]] = []
    length_mismatches: list[dict[str, Any]] = []
    secondary_names: list[str] = []
    for observation in observations:
        name = bytes.fromhex(observation["name_hex"])
        primary = bytes.fromhex(observation["primary_hex"])
        if observation["secondary_nibbles"]:
            secondary_names.append(observation["name"])
            continue
        if len(name) != len(primary):
            length_mismatches.append(
                {
                    "name": observation["name"],
                    "name_bytes": len(name),
                    "primary_bytes": len(primary),
                }
            )
            continue
        for source, target in zip(name, primary):
            existing = mapping.get(source)
            if existing is None:
                mapping[source] = target
            elif existing != target:
                conflicts.append(
                    {
                        "name": observation["name"],
                        "observed": target,
                        "previous": existing,
                        "source": source,
                    }
                )
    return {
        "conflicts": conflicts,
        "length_mismatches": length_mismatches,
        "map": {f"{source:02x}": f"{mapping[source]:02x}" for source in sorted(mapping)},
        "names_with_secondary_weights": sorted(secondary_names),
    }


def question_name_key_framing(
    name_keys: list[list[dict[str, Any]]], schema_keys: list[list[dict[str, Any]]]
) -> dict[str, Any]:
    """Report the lossless keys of both the probed-name and four-table images."""
    for keys in (name_keys, schema_keys):
        if any(entry != keys[0] for entry in keys[1:]):
            return {
                "reason": "replicas disagree on the lossless catalog keys",
                "status": "no_outcome",
            }
    return {"names": name_keys[0], "schema": schema_keys[0], "status": "answered"}


def question_ascii_name_collation(
    name_keys: list[list[dict[str, Any]]],
    schema_keys: list[list[dict[str, Any]]],
    attempts: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    if any(attempt != attempts[0] for attempt in attempts[1:]):
        return {"reason": "replicas disagree on the probed-name inventory", "status": "no_outcome"}
    derived = [
        collation_map(
            [
                observation
                for observation in name_keys[index] + schema_keys[index]
                if is_ascii_name(observation)
            ]
        )
        for index in range(len(name_keys))
    ]
    if any(entry != derived[0] for entry in derived[1:]):
        return {"reason": "replicas derive different collation maps", "status": "no_outcome"}
    observation = derived[0]
    if (
        observation["conflicts"]
        or observation["length_mismatches"]
        or observation["names_with_secondary_weights"]
    ):
        return {
            "conflicts": observation["conflicts"],
            "length_mismatches": observation["length_mismatches"],
            "names_with_secondary_weights": observation["names_with_secondary_weights"],
            "reason": "the ASCII keys are not one context-free primary weight map",
            "status": "no_outcome",
        }
    rejected = sorted(
        {
            point
            for attempt in attempts[0]
            if not attempt["created"]
            for point in attempt["code_points"]
        }
    )
    mapped = {int(source, 16) for source in observation["map"]}
    return {
        "map": observation["map"],
        "rejected_name_code_points": rejected,
        "status": "answered",
        "unmapped_ascii_code_points": sorted(
            point
            for attempt in attempts[0]
            for point in attempt["code_points"]
            if point <= 0x7E and point not in mapped
        ),
    }


def question_extended_name_keys(
    name_keys: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Record the non-ASCII name keys losslessly without deriving a map."""
    selected = [
        [observation for observation in keys if not is_ascii_name(observation)]
        for keys in name_keys
    ]
    if any(entry != selected[0] for entry in selected[1:]):
        return {"reason": "replicas disagree on the extended name keys", "status": "no_outcome"}
    return {"keys": selected[0], "status": "answered"}


def question_transitions(
    replicas: list[dict[str, Any]], projection: str
) -> dict[str, Any]:
    checkpoints = {}
    for name in SCHEMA_CHECKPOINTS[1:]:
        observations = [replica["transitions"][name] for replica in replicas]
        if projection == "rows":
            values = [
                {"aces": entry["aces"], "objects": entry["objects"]} for entry in observations
            ]
        else:
            values = [
                {
                    "appended_pages": entry["appended_pages"],
                    "page_count": entry["page_count"],
                    "page_zero": entry["page_zero"],
                }
                for entry in observations
            ]
        if any(value != values[0] for value in values[1:]):
            return {
                "reason": f"replicas disagree on the {name} transition",
                "status": "no_outcome",
            }
        checkpoints[name] = {"table": CREATED_TABLES[name], **values[0]}
    return {"status": "answered", "transitions": checkpoints}


def question_long_values(replicas: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [replica["long_values"] for replica in replicas]
    if any(entry != observations[0] for entry in observations[1:]):
        return {"reason": "replicas disagree on the property payloads", "status": "no_outcome"}
    return {"objects": observations[0], "status": "answered"}


def build_report(
    document: dict[str, Any], replicas: list[dict[str, Any]]
) -> dict[str, Any]:
    complete = [replica for replica in replicas if "observation" in replica]
    if document["status"] != "pass" or len(complete) != len(replicas):
        if any(replica.get("metadata_repaired") for replica in replicas):
            reason = "DAO metadata access changed at least one checkpoint"
        elif any("decode_error" in replica for replica in replicas):
            reason = "at least one checkpoint did not decode under the pinned hypotheses"
        else:
            reason = "at least one replica did not complete"
        questions = {name: {"reason": reason, "status": "no_outcome"} for name in QUESTION_NAMES}
    else:
        observations = [replica["observation"] for replica in complete]
        questions = {
            "name_key_framing": question_name_key_framing(
                [observation["name_keys"] for observation in observations],
                [observation["schema_keys"] for observation in observations],
            ),
            "ascii_name_collation": question_ascii_name_collation(
                [observation["name_keys"] for observation in observations],
                [observation["schema_keys"] for observation in observations],
                [replica["probe_attempts"] for replica in complete],
            ),
            "extended_name_keys": question_extended_name_keys(
                [observation["name_keys"] for observation in observations]
            ),
            "object_and_ace_rows": question_transitions(observations, "rows"),
            "page_zero_and_page_assignment": question_transitions(observations, "pages"),
            "long_value_property_framing": question_long_values(observations),
        }
    return {
        "compatibility_claim": False,
        "development_only": True,
        "document_type": REPORT_TYPE,
        "plan_sha256": document["plan_sha256"],
        "questions": questions,
        "replicas": [
            {key: value for key, value in replica.items() if key != "observation"}
            for replica in replicas
        ],
        "status": (
            "accepted"
            if all(question["status"] == "answered" for question in questions.values())
            else "no_outcome"
        ),
        "support_movement": False,
    }


def evaluate(job_result: Path, expected_plan_sha256: str, output: Path) -> dict[str, Any]:
    expected = digest(expected_plan_sha256, "--expected-plan-sha256")
    document = exact_object(
        load_document(job_result),
        {"document_type", "development_only", "plan_sha256", "run_id", "status", "replicas"},
        "$",
    )
    if document["document_type"] != DOCUMENT_TYPE:
        raise AnalysisError("job result document type is invalid")
    if document["development_only"] is not True or document["status"] not in ("pass", "fail"):
        raise AnalysisError("job result status fields are invalid")
    if digest(document["plan_sha256"], "$.plan_sha256") != expected:
        raise AnalysisError("job result plan digest differs from the approved plan")
    if not isinstance(document["run_id"], str) or not SAFE_RUN_ID.fullmatch(document["run_id"]):
        raise AnalysisError("$.run_id is invalid")
    raw_replicas = document["replicas"]
    if not isinstance(raw_replicas, list) or len(raw_replicas) != 3:
        raise AnalysisError("$.replicas must contain exactly three replicas")
    replicas: list[dict[str, Any]] = []
    referenced: list[str] = []
    for position, raw in enumerate(raw_replicas):
        item = exact_object(
            raw,
            {"replica", "status", "error", "checkpoints", "probe_attempts"},
            f"replicas[{position}]",
        )
        replica = item["replica"]
        if type(replica) is not int or replica != position + 1:
            raise AnalysisError("replicas must be numbered 1 through 3 in order")
        if item["status"] not in ("pass", "fail"):
            raise AnalysisError(f"replica {replica} status is invalid")
        if item["error"] is not None:
            bounded_text(item["error"], f"replica {replica} error")
        checkpoints = item["checkpoints"]
        if not isinstance(checkpoints, list) or len(checkpoints) > len(CHECKPOINTS):
            raise AnalysisError(f"replica {replica} checkpoints are invalid")
        images: dict[str, bytes] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        repaired: list[str] = []
        for index, value in enumerate(checkpoints):
            name = CHECKPOINTS[index]
            image, metadata_repaired, dao = read_checkpoint(
                job_result.parent, value, replica, name
            )
            images[name] = image
            snapshots[name] = dao
            referenced.append(f"schema-generalization-r{replica}-{name}.mdb")
            if metadata_repaired:
                repaired.append(name)
        entry: dict[str, Any] = {
            "error": item["error"],
            "probe_attempts": read_probe_attempts(
                item["probe_attempts"], replica, complete=item["status"] == "pass"
            ),
            "replica": replica,
            "status": item["status"],
        }
        if item["status"] == "pass":
            if tuple(images) != CHECKPOINTS or item["error"] is not None:
                raise AnalysisError(f"replica {replica} pass inventory is incomplete")
            if repaired:
                entry["metadata_repaired"] = repaired
            else:
                try:
                    entry["observation"] = analyze_replica(
                        images, snapshots, entry["probe_attempts"]
                    )
                except (catalog.DecodeError, DecodeError) as error:
                    entry["decode_error"] = str(error)
        elif item["error"] is None:
            raise AnalysisError(f"replica {replica} failure omits its error")
        replicas.append(entry)
    aggregate = "pass" if all(entry["status"] == "pass" for entry in replicas) else "fail"
    if document["status"] != aggregate:
        raise AnalysisError("job result status disagrees with replica statuses")
    retained = sorted(path.name for path in job_result.parent.glob("*.mdb"))
    if retained != sorted(referenced):
        raise AnalysisError("retained MDB inventory differs from the job result")
    report = build_report(document, replicas)
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
        print(f"schema generalization analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
