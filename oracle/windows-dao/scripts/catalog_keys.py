"""Catalog ParentId/Name index keys and LvProp payloads for the schema-name suites."""

from __future__ import annotations

import hashlib
from typing import Any

import system_catalog as catalog

PAGE_BYTES = 2048
ENTRY_AREA_OFFSET = 248
ENTRY_AREA_LENGTH = PAGE_BYTES - ENTRY_AREA_OFFSET
LONG_KEY_MARKER = 0x7F
TEXT_KEY_MARKER = 0x7F
TEXT_KEY_TERMINATOR = 0x00
PROPERTY_MAGIC = b"KKD\x00"
PROPERTY_NAME_CHUNK = 0x0080


class DecodeError(ValueError):
    """A bounded checkpoint did not decode under the pinned hypotheses."""


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
