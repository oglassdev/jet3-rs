#!/usr/bin/env python3
"""Reproducibly build or check the protocol 1.2 differential read inventory.

The inventory is declarative experiment input. It records no observation and
its `expected_snapshot_sha256` members stay null until an accepted DAO run.
Required branches are declared only where recorded provenance establishes
which reader branch a case exercises; boundary cases exist only where a
threshold is recorded, and every plan-required case without a recorded
threshold is listed under `deferred_requirements` instead of being guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

from protocol_validation import canonical_json_bytes

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "protocol" / "v1_2" / "scenarios.json"
# EXP-0057: one type-05 bitmap covers 16,352 pages; absolute page is
# slot_ordinal * 16_352 + bit_index.
EXTENDED_SLOT_PAGES = 16_352

OPEN_BRANCHES = ["open.signature_geometry", "open.header_page"]
CATALOG_BRANCHES = ["catalog.root_discovery", "catalog.record_stream"]
BASE_TABLE_BRANCHES = OPEN_BRANCHES + CATALOG_BRANCHES + [
    "tdef.column_types",
]
INLINE_TABLE_BRANCHES = BASE_TABLE_BRANCHES + [
    "allocation.inline_map",
    "tdef.single_page",
]
INLINE_ROW_BRANCHES = INLINE_TABLE_BRANCHES + [
    "rows.direct",
]
INDIRECT_ROW_BRANCHES = BASE_TABLE_BRANCHES + [
    "allocation.indirect_map",
    "tdef.single_page",
    "rows.direct",
]
CONTINUATION_TABLE_BRANCHES = BASE_TABLE_BRANCHES + [
    "allocation.inline_map",
    "tdef.continuation_chain",
]
ALL_TYPES = "values.all_dao_jet3_table_types"
STORAGE_BRANCH = {
    "fixed": ["values.fixed_scalar"],
    "variable": ["values.variable_short"],
    "long": [],
}


@dataclass(frozen=True)
class Point:
    """One value case of a type: its encoding, payload, and recorded branches."""

    label: str
    encoding: str
    value: Any
    branches: tuple[str, ...] = ()


@dataclass(frozen=True)
class TypeCase:
    """One DAO type with its storage form, capability, and value cases."""

    dao_type: str
    storage: str
    capability: str
    size: int | None = None
    branches: tuple[str, ...] = ()
    points: tuple[Point, ...] = ()

    @property
    def name(self) -> str:
        return self.dao_type[2:].upper()

    def field(self, name: str = "Value") -> dict[str, Any]:
        return {"name": name, "dao_type": self.dao_type, "size": self.size, "required": False}

    def capabilities(self) -> list[str]:
        return ["rows.streaming_read", ALL_TYPES, self.capability]

    def point_branches(self, point: Point) -> list[str]:
        return INLINE_ROW_BRANCHES + STORAGE_BRANCH[self.storage] + list(self.branches) + list(point.branches)


def _ladder(unit: str, encoding: str) -> tuple[Point, ...]:
    # EXP-0061 controls: 32 inline, 512 single-page, 2048 and 4096 chained.
    return (
        Point("INLINE-32", encoding, {"unit": unit, "length": 32}, ("long_value.inline",)),
        Point("SINGLE-PAGE-512", encoding, {"unit": unit, "length": 512}, ("long_value.single_page",)),
        Point("CHAINED-2048", encoding, {"unit": unit, "length": 2048}, ("long_value.chained",)),
        Point("CHAINED-4096", encoding, {"unit": unit, "length": 4096}, ("long_value.chained",)),
        Point("MAX-32769", encoding, {"unit": unit, "length": 32769}),
    )


TYPE_CASES: tuple[TypeCase, ...] = (
    TypeCase("dbBoolean", "fixed", "values.null_fixed_variable", points=(Point("FALSE", "boolean", False), Point("TRUE", "boolean", True))),
    TypeCase("dbByte", "fixed", "values.null_fixed_variable", points=(Point("MIN", "integer", 0), Point("REP", "integer", 97), Point("MAX", "integer", 255))),
    TypeCase("dbInteger", "fixed", "values.null_fixed_variable", points=(Point("MIN", "integer", -32768), Point("REP", "integer", 12345), Point("MAX", "integer", 32767))),
    TypeCase("dbLong", "fixed", "values.null_fixed_variable", points=(Point("MIN", "integer", -2147483648), Point("REP", "integer", 123456789), Point("MAX", "integer", 2147483647))),
    TypeCase("dbCurrency", "fixed", "values.date_currency_binary_guid_replication", points=(Point("MIN", "invariant_decimal", "-922337203685477.5808"), Point("REP", "invariant_decimal", "12.3456"), Point("MAX", "invariant_decimal", "922337203685477.5807"))),
    TypeCase("dbSingle", "fixed", "values.null_fixed_variable", points=(Point("MIN", "ieee_bits_hex", "ff7fffff"), Point("REP", "ieee_bits_hex", "3fc00000"), Point("MAX", "ieee_bits_hex", "7f7fffff"))),
    TypeCase("dbDouble", "fixed", "values.null_fixed_variable", points=(Point("MIN", "ieee_bits_hex", "ffefffffffffffff"), Point("REP", "ieee_bits_hex", "3ff8000000000000"), Point("MAX", "ieee_bits_hex", "7fefffffffffffff"))),
    TypeCase("dbDate", "fixed", "values.date_currency_binary_guid_replication", points=(Point("MIN", "invariant_datetime", "0100-01-01T00:00:00"), Point("REP", "invariant_datetime", "1999-12-31T23:59:59"), Point("MAX", "invariant_datetime", "9999-12-31T23:59:59"))),
    TypeCase("dbBinary", "variable", "values.date_currency_binary_guid_replication", size=255, points=(Point("MIN", "lowercase_hex", "00"), Point("REP", "lowercase_hex", "0011223344556677"), Point("MAX", "repeat_byte", {"unit": "a5", "length": 255}))),
    TypeCase("dbText", "variable", "values.code_pages_lossless_raw", size=255, branches=("values.text_cp1252",), points=(Point("EMPTY", "unicode_string", ""), Point("MIN", "unicode_string", "A"), Point("REP", "unicode_string", "Café € Œ Ÿ"), Point("MAX", "repeat_ascii", {"unit": "T", "length": 255}))),
    TypeCase("dbLongBinary", "long", "values.memo_ole_multi_page", points=_ladder("a5", "repeat_byte")),
    TypeCase("dbMemo", "long", "values.memo_ole_multi_page", points=_ladder("M", "repeat_ascii")),
    TypeCase("dbGUID", "fixed", "values.date_currency_binary_guid_replication", points=(Point("MIN", "guid", "00000000-0000-0000-0000-000000000000"), Point("REP", "guid", "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"), Point("MAX", "guid", "ffffffff-ffff-ffff-ffff-ffffffffffff"))),
)

DEFERRED_REQUIREMENTS = [
    {
        "requirement": "open.largest_supported_size",
        "reason": "No recorded source or experiment fixes the largest supported Jet 3 database size; the scenario would guess a page count.",
        "provenance_needed": "A source or experiment entry recording the maximum dbVersion30 page count.",
    },
    {
        "requirement": "allocation.inline_capacity_boundary",
        "reason": "The inline usage-map page capacity is only an A3 design example, not an observation; no below/at/above page counts can be declared.",
        "provenance_needed": "An experiment recording the inline-to-indirect transition page count for a dbVersion30 table map.",
    },
    {
        "requirement": "allocation.further_extended_slots",
        "reason": "EXP-0057 observed slot ordinals 0 and 1 only; boundaries of later slots are formula-derived, not observed.",
        "provenance_needed": "An experiment observing a third or later type-05 slot reference.",
    },
    {
        "requirement": "values.code_page_cp1251",
        "reason": "EXP-0061 records that its CP1251 diagnostic did not establish CP1251 selection or physical encoding.",
        "provenance_needed": "An experiment establishing CP1251 database creation and its stored text bytes.",
    },
]


def _field(name: str, dao_type: str, size: int | None = None, required: bool = False) -> dict[str, Any]:
    if size is None and dao_type in ("dbText", "dbBinary"):
        size = 255
    return {"name": name, "dao_type": dao_type, "size": size, "required": required}


def _index(name: str, fields: list[tuple[str, bool]], *, primary: bool = False, unique: bool = False, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "fields": [{"name": column, "descending": descending} for column, descending in fields],
        "primary": primary,
        "unique": unique,
        "required": required,
        "ignore_nulls": False,
    }


def _value(column: str, encoding: str, value: Any) -> dict[str, Any]:
    return {"field": column, "encoding": encoding, "value": value}


def _recipe(steps: list[dict[str, Any]], *, version: str = "dbVersion30", encrypted: bool = False, password: str | None = None) -> dict[str, Any]:
    return {
        "producer": "dao",
        "database_version": version,
        "locale": ";LANGID=0x0409;CP=1252;COUNTRY=0",
        "encrypted": encrypted,
        "password": password,
        "steps": [{"action": "create_database"}, *steps, {"action": "close_database"}],
    }


def _table(name: str, fields: list[dict[str, Any]], indexes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"action": "create_table", "name": name, "fields": fields, "indexes": indexes or []}


def _insert(table: str, rows: list[list[dict[str, Any]]], repeat: int = 1) -> dict[str, Any]:
    return {"action": "insert_rows", "table": table, "rows": rows, "repeat": repeat}


def _scenario(scenario_id: str, capabilities: list[str], recipe: dict[str, Any], branches: list[str], *, boundary: dict[str, Any] | None = None, error_class: str | None = None) -> dict[str, Any]:
    scenario = {
        "id": scenario_id,
        "capability_ids": sorted(set(capabilities)),
        "boundary": boundary,
        "operation": {
            "mode": "rust_read_dao",
            "expected_outcome": "expected_error" if error_class else "success",
            "error_class": error_class,
        },
        "generator_recipe": recipe,
        "required_branches": sorted(set(branches)),
        "expected_snapshot_sha256": None,
        "preserve_paths": [],
    }
    scenario["content_sha256"] = hashlib.sha256(canonical_json_bytes(scenario)).hexdigest()
    return scenario


def _id_row() -> list[dict[str, Any]]:
    return [_value("Id", "integer", 1), _value("Name", "unicode_string", "row")]


def _id_table(name: str = "Items") -> dict[str, Any]:
    return _table(name, [_field("Id", "dbLong"), _field("Name", "dbText", 32)])


def _open_scenarios() -> list[dict[str, Any]]:
    caps = ["database.open", "format.header_and_version"]
    scenarios = [
        _scenario("DAO-READ-OPEN-EMPTY", caps, _recipe([]), OPEN_BRANCHES + CATALOG_BRANCHES),
        _scenario(
            "DAO-READ-OPEN-GROWN",
            caps + ["format.pages_allocation_usage"],
            _recipe([_id_table(), _insert("Items", [_id_row()], 256), {"action": "delete_rows", "table": "Items", "count": "all"}, {"action": "reopen"}]),
            INLINE_TABLE_BRANCHES + ["rows.deleted_skip"],
        ),
    ]
    for suffix, options, error_class in [
        ("JET4", {"version": "dbVersion40"}, "unsupported_version"),
        ("ENCRYPTED", {"encrypted": True}, "encrypted_database"),
        ("PASSWORD", {"password": "jet3"}, "password_protected"),
    ]:
        scenarios.append(_scenario(f"DAO-READ-OPEN-REJECT-{suffix}", caps, _recipe([_id_table()], **options), OPEN_BRANCHES + ["open.rejected_format"], error_class=error_class))
    return scenarios


def _alloc_scenarios() -> list[dict[str, Any]]:
    caps = ["format.pages_allocation_usage", "rows.streaming_read"]
    scenarios = [_scenario("DAO-READ-ALLOC-SMALL-INLINE", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 8)]), INLINE_ROW_BRANCHES)]
    for position, pages, branches, forbidden_branches in [
        ("below", EXTENDED_SLOT_PAGES - 1, ["allocation.indirect_map"], ["allocation.extended_slot"]),
        ("at", EXTENDED_SLOT_PAGES, ["allocation.indirect_map"], ["allocation.extended_slot"]),
        (
            "above",
            EXTENDED_SLOT_PAGES + 1,
            ["allocation.indirect_map", "allocation.extended_slot"],
            [],
        ),
    ]:
        scenarios.append(
            _scenario(
                f"DAO-READ-ALLOC-EXTENDED-SLOT-1-{position.upper()}",
                caps,
                _recipe(
                    [
                        _id_table(),
                        {
                            "action": "insert_until_page_count",
                            "table": "Items",
                            "row": _id_row(),
                            "page_count": pages,
                            "require_exact_page_count": True,
                        },
                    ]
                ),
                INDIRECT_ROW_BRANCHES + branches,
                boundary={
                    "dimension": "extended_slot_0_page_capacity",
                    "position": position,
                    "forbidden_branches": forbidden_branches,
                },
            )
        )
    scenarios += [
        _scenario("DAO-READ-ALLOC-DELETE-REINSERT", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 64), {"action": "delete_rows", "table": "Items", "count": 32}, _insert("Items", [_id_row()], 32)]), INLINE_ROW_BRANCHES + ["rows.deleted_skip"]),
        _scenario("DAO-READ-ALLOC-DROP-RECREATE", caps + ["schema.catalog_and_table_definitions"], _recipe([_id_table(), _insert("Items", [_id_row()], 64), {"action": "drop_table", "name": "Items"}, _id_table(), _insert("Items", [_id_row()], 8)]), INLINE_ROW_BRANCHES),
        _scenario("DAO-READ-ALLOC-IDLE-REOPEN", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 8), {"action": "reopen"}, {"action": "reopen"}]), INLINE_ROW_BRANCHES),
        _scenario("DAO-READ-ALLOC-MULTIPLE-TABLES", caps + ["schema.catalog_and_table_definitions"], _recipe([_id_table("Alpha"), _id_table("Beta"), _id_table("Gamma"), _insert("Alpha", [_id_row()], 8), _insert("Beta", [_id_row()], 64), _insert("Gamma", [_id_row()], 512)]), INLINE_ROW_BRANCHES),
    ]
    return scenarios


def _schema_scenarios() -> list[dict[str, Any]]:
    caps = ["schema.catalog_and_table_definitions"]
    scenarios = []
    for case in TYPE_CASES:
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-TYPE-{case.name}", caps + [ALL_TYPES], _recipe([_table("Typed", [_field("Id", "dbLong"), case.field()])]), INLINE_TABLE_BRANCHES))
    for suffix, size in [("TEXT-SIZE-1", 1), ("TEXT-SIZE-255", 255)]:
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", caps, _recipe([_table("Typed", [_field("Value", "dbText", size)])]), INLINE_TABLE_BRANCHES))
    # EXP-0059 records this exact probe as a 4,333-byte continuation chain.
    boundary_fields = [
        _field(
            f"Boundary_{index:02d}_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "dbText" if index % 2 else "dbLong",
            31 if index % 2 else None,
        )
        for index in range(64)
    ]
    scenarios.append(
        _scenario(
            "DAO-READ-SCHEMA-WIDE-TABLE",
            caps,
            _recipe([_table("BoundaryProbe", boundary_fields)]),
            CONTINUATION_TABLE_BRANCHES,
        )
    )
    index_caps = caps + ["indexes.primary_unique_non_unique"]
    two_columns = [_field("Id", "dbLong"), _field("Code", "dbText", 16)]
    for suffix, index, extra in [
        ("INDEX-PRIMARY", _index("PrimaryKey", [("Id", False)], primary=True, unique=True, required=True), []),
        ("INDEX-UNIQUE", _index("UniqueCode", [("Code", False)], unique=True), []),
        ("INDEX-NONUNIQUE", _index("ByCode", [("Code", False)]), []),
        ("INDEX-COMPOSITE-ASCENDING", _index("ByIdCode", [("Id", False), ("Code", False)]), ["indexes.composite_ascending_descending"]),
        ("INDEX-COMPOSITE-DESCENDING", _index("ByIdCodeDesc", [("Id", True), ("Code", True)]), ["indexes.composite_ascending_descending"]),
        ("INDEX-COMPOSITE-MIXED", _index("ByIdAscCodeDesc", [("Id", False), ("Code", True)]), ["indexes.composite_ascending_descending"]),
    ]:
        # EXP-0062 observed branches only in 4,096-row Long and 1,024-row
        # composite controls. This closed recipe has only three rows, so it
        # exercises definition and leaf-key behavior without claiming a root.
        branches = INLINE_ROW_BRANCHES + ["values.variable_short", "tdef.physical_index", "tdef.logical_index"]
        branches.append("index.composite_key_lossless" if len(index["fields"]) > 1 else "index.single_field_key")
        rows = [[_value("Id", "integer", number), _value("Code", "unicode_string", f"C{number:02d}")] for number in (3, 1, 2)]
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", index_caps + extra, _recipe([_table("Keyed", two_columns, [index]), _insert("Keyed", rows)]), branches))
    relationship = {"action": "create_relationship", "name": "ParentChild", "table": "Parent", "foreign_table": "Child", "fields": [{"field": "Id", "foreign_field": "ParentId"}], "cascade_updates": False, "cascade_deletes": False}
    parent = _table("Parent", [_field("Id", "dbLong")], [_index("PrimaryKey", [("Id", False)], primary=True, unique=True, required=True)])
    child = _table("Child", [_field("Id", "dbLong"), _field("ParentId", "dbLong")])
    for suffix, cascade in [("RELATIONSHIP", (False, False)), ("RELATIONSHIP-CASCADE", (True, True))]:
        step = dict(relationship, cascade_updates=cascade[0], cascade_deletes=cascade[1])
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", index_caps, _recipe([parent, child, step]), INLINE_TABLE_BRANCHES + ["tdef.physical_index", "tdef.logical_index", "tdef.relationship_reference"]))
    return scenarios


def _value_scenarios() -> list[dict[str, Any]]:
    scenarios = []
    for case in TYPE_CASES:
        table = _table("Typed", [_field("Id", "dbLong"), case.field()])
        if case.dao_type != "dbBoolean":
            scenarios.append(_scenario(f"DAO-READ-VALUES-{case.name}-NULL", ["rows.streaming_read", ALL_TYPES, "values.null_fixed_variable"], _recipe([table, _insert("Typed", [[_value("Id", "integer", 1), _value("Value", "null", None)]])]), INLINE_ROW_BRANCHES + ["values.null_field"]))
        for point in case.points:
            scenarios.append(_scenario(f"DAO-READ-VALUES-{case.name}-{point.label}", case.capabilities(), _recipe([table, _insert("Typed", [[_value("Id", "integer", 1), _value("Value", point.encoding, point.value)]])]), case.point_branches(point)))
    return scenarios


def _rows_scenarios() -> list[dict[str, Any]]:
    caps = ["rows.streaming_read"]
    # EXP-0060 records this exact 265-byte, one-variable row shape. The same
    # record ties overflow pointers to Edit/Update growth, not initial inserts.
    table = _table("Items", [_field("Id", "dbLong"), _field("Payload", "dbText", 255)])
    long_row = [_value("Id", "integer", 1), _value("Payload", "repeat_ascii", {"unit": "O", "length": 255})]
    return [
        _scenario("DAO-READ-ROWS-EMPTY-TABLE", caps, _recipe([_id_table()]), INLINE_TABLE_BRANCHES),
        _scenario("DAO-READ-ROWS-SINGLE", caps, _recipe([_id_table(), _insert("Items", [_id_row()])]), INLINE_ROW_BRANCHES),
        _scenario("DAO-READ-ROWS-DUPLICATES", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 3)]), INLINE_ROW_BRANCHES),
        _scenario("DAO-READ-ROWS-PAGE-SPAN", caps, _recipe([table, _insert("Items", [long_row], 16)]), INLINE_ROW_BRANCHES + ["values.variable_short", "rows.wide_variable_layout"]),
        _scenario("DAO-READ-ROWS-DELETED-MIDDLE", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 9), {"action": "delete_rows", "table": "Items", "count": 4}, _insert("Items", [_id_row()], 2)]), INLINE_ROW_BRANCHES + ["rows.deleted_skip"]),
        _scenario("DAO-READ-ROWS-MANY", caps + ["format.pages_allocation_usage"], _recipe([_id_table(), _insert("Items", [_id_row()], 4096)]), INLINE_ROW_BRANCHES),
    ]


def build_inventory() -> dict[str, Any]:
    scenarios = _open_scenarios() + _alloc_scenarios() + _schema_scenarios() + _value_scenarios() + _rows_scenarios()
    scenarios.sort(key=lambda scenario: scenario["id"])
    return {
        "protocol_version": "1.2.0",
        "document_type": "dao_scenario_inventory",
        "deferred_requirements": sorted(DEFERRED_REQUIREMENTS, key=lambda entry: entry["requirement"]),
        "scenarios": scenarios,
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed inventory is byte-identical")
    args = parser.parse_args()
    rendered = render(build_inventory())
    if args.check:
        if not INVENTORY.exists() or INVENTORY.read_text(encoding="utf-8") != rendered:
            print(f"FAIL: {INVENTORY} differs from the reproducible build")
            return 1
        print(f"PASS: {INVENTORY} is reproducible")
        return 0
    INVENTORY.write_text(rendered, encoding="utf-8")
    print(f"wrote {INVENTORY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
