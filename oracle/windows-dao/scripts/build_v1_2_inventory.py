#!/usr/bin/env python3
"""Reproducibly build or check the protocol 1.2 differential read inventory.

The inventory is declarative experiment input. It records no observation and
its `expected_snapshot_sha256` members stay null until an accepted DAO run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from protocol_validation import canonical_json_bytes

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "protocol" / "v1_2" / "scenarios.json"
INLINE_MAP_PAGE_CAPACITY = 1024

OPEN_BRANCHES = ["open.signature_geometry", "open.header_page"]
CATALOG_BRANCHES = ["catalog.root_discovery", "catalog.record_stream"]
TABLE_BRANCHES = OPEN_BRANCHES + CATALOG_BRANCHES + [
    "allocation.inline_map",
    "tdef.single_page",
    "tdef.column_types",
    "rows.direct",
]
DAO_TYPES = [
    "dbBoolean",
    "dbByte",
    "dbInteger",
    "dbLong",
    "dbCurrency",
    "dbSingle",
    "dbDouble",
    "dbDate",
    "dbBinary",
    "dbText",
    "dbLongBinary",
    "dbMemo",
    "dbGUID",
]
TYPE_CAPABILITY = {
    "dbBoolean": "values.null_fixed_variable",
    "dbByte": "values.null_fixed_variable",
    "dbInteger": "values.null_fixed_variable",
    "dbLong": "values.null_fixed_variable",
    "dbCurrency": "values.date_currency_binary_guid_replication",
    "dbSingle": "values.null_fixed_variable",
    "dbDouble": "values.null_fixed_variable",
    "dbDate": "values.date_currency_binary_guid_replication",
    "dbBinary": "values.date_currency_binary_guid_replication",
    "dbText": "values.code_pages_lossless_raw",
    "dbLongBinary": "values.memo_ole_multi_page",
    "dbMemo": "values.memo_ole_multi_page",
    "dbGUID": "values.date_currency_binary_guid_replication",
}
# (min, representative, max) encodings per type; None means no such point.
TYPE_VALUES: dict[str, list[tuple[str, str, Any]]] = {
    "dbBoolean": [("MIN", "boolean", False), ("REP", "boolean", True)],
    "dbByte": [("MIN", "integer", 0), ("REP", "integer", 97), ("MAX", "integer", 255)],
    "dbInteger": [("MIN", "integer", -32768), ("REP", "integer", 12345), ("MAX", "integer", 32767)],
    "dbLong": [("MIN", "integer", -2147483648), ("REP", "integer", 123456789), ("MAX", "integer", 2147483647)],
    "dbCurrency": [
        ("MIN", "invariant_decimal", "-922337203685477.5808"),
        ("REP", "invariant_decimal", "12.3456"),
        ("MAX", "invariant_decimal", "922337203685477.5807"),
    ],
    "dbSingle": [
        ("MIN", "ieee_bits_hex", "ff7fffff"),
        ("REP", "ieee_bits_hex", "3fc00000"),
        ("MAX", "ieee_bits_hex", "7f7fffff"),
    ],
    "dbDouble": [
        ("MIN", "ieee_bits_hex", "ffefffffffffffff"),
        ("REP", "ieee_bits_hex", "3ff8000000000000"),
        ("MAX", "ieee_bits_hex", "7fefffffffffffff"),
    ],
    "dbDate": [
        ("MIN", "invariant_datetime", "0100-01-01T00:00:00"),
        ("REP", "invariant_datetime", "1999-12-31T23:59:59"),
        ("MAX", "invariant_datetime", "9999-12-31T23:59:59"),
    ],
    "dbBinary": [
        ("MIN", "lowercase_hex", "00"),
        ("REP", "lowercase_hex", "0011223344556677"),
        ("MAX", "repeat_byte", {"unit": "a5", "length": 255}),
    ],
    "dbText": [
        ("MIN", "unicode_string", "A"),
        ("REP", "unicode_string", "JET3 read"),
        ("MAX", "repeat_ascii", {"unit": "T", "length": 255}),
    ],
    "dbLongBinary": [
        ("MIN", "lowercase_hex", "00"),
        ("REP", "repeat_byte", {"unit": "a5", "length": 64}),
        ("MAX", "repeat_byte", {"unit": "a5", "length": 32769}),
    ],
    "dbMemo": [
        ("MIN", "unicode_string", "M"),
        ("REP", "unicode_string", "Memo text"),
        ("MAX", "repeat_ascii", {"unit": "M", "length": 32769}),
    ],
    "dbGUID": [
        ("MIN", "guid", "00000000-0000-0000-0000-000000000000"),
        ("REP", "guid", "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"),
        ("MAX", "guid", "ffffffff-ffff-ffff-ffff-ffffffffffff"),
    ],
}
LONG_VALUE_BRANCHES = {
    ("dbLongBinary", "MIN"): ["long_value.inline"],
    ("dbLongBinary", "REP"): ["long_value.inline"],
    ("dbLongBinary", "MAX"): ["long_value.chained"],
    ("dbMemo", "MIN"): ["long_value.inline"],
    ("dbMemo", "REP"): ["long_value.inline"],
    ("dbMemo", "MAX"): ["long_value.chained"],
}
TEXT_BRANCH = {"dbText": ["values.text_cp1252"]}


def _field(name: str, dao_type: str, size: int | None = None, required: bool = False) -> dict[str, Any]:
    if size is None and dao_type in ("dbText", "dbBinary"):
        size = 255
    return {"name": name, "dao_type": dao_type, "size": size, "required": required}


def _index(name: str, fields: list[tuple[str, bool]], *, primary: bool = False, unique: bool = False, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "fields": [{"name": field, "descending": descending} for field, descending in fields],
        "primary": primary,
        "unique": unique,
        "required": required,
        "ignore_nulls": False,
    }


def _value(field: str, encoding: str, value: Any) -> dict[str, Any]:
    return {"field": field, "encoding": encoding, "value": value}


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


def _scenario(
    scenario_id: str,
    capabilities: list[str],
    recipe: dict[str, Any],
    branches: list[str],
    *,
    boundary: dict[str, Any] | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    scenario = {
        "id": scenario_id,
        "capability_ids": sorted(capabilities),
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
            TABLE_BRANCHES + ["rows.deleted_skip"],
        ),
    ]
    negatives = [
        ("JET4", {"version": "dbVersion40"}, "unsupported_version"),
        ("ENCRYPTED", {"encrypted": True}, "encrypted_database"),
        ("PASSWORD", {"password": "jet3"}, "password_protected"),
    ]
    for suffix, options, error_class in negatives:
        scenarios.append(
            _scenario(
                f"DAO-READ-OPEN-REJECT-{suffix}",
                caps,
                _recipe([_id_table()], **options),
                OPEN_BRANCHES + ["open.rejected_format"],
                error_class=error_class,
            )
        )
    return scenarios


def _alloc_scenarios() -> list[dict[str, Any]]:
    caps = ["format.pages_allocation_usage", "rows.streaming_read"]
    scenarios = [
        _scenario("DAO-READ-ALLOC-SMALL-INLINE", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 8)]), TABLE_BRANCHES),
    ]
    for suffix, position, pages, branches in [
        ("INLINE-CAPACITY-BELOW", "below", INLINE_MAP_PAGE_CAPACITY - 1, ["allocation.inline_map"]),
        ("INLINE-CAPACITY-AT", "at", INLINE_MAP_PAGE_CAPACITY, ["allocation.inline_map"]),
        ("INLINE-CAPACITY-ABOVE", "above", INLINE_MAP_PAGE_CAPACITY + 1, ["allocation.indirect_map", "allocation.extended_slot"]),
    ]:
        scenarios.append(
            _scenario(
                f"DAO-READ-ALLOC-{suffix}",
                caps,
                _recipe([_id_table(), {"action": "insert_until_page_count", "table": "Items", "row": _id_row(), "page_count": pages}]),
                TABLE_BRANCHES + branches,
                boundary={"dimension": "inline_usage_map_page_capacity", "position": position},
            )
        )
    scenarios += [
        _scenario(
            "DAO-READ-ALLOC-DELETE-REINSERT",
            caps,
            _recipe([_id_table(), _insert("Items", [_id_row()], 64), {"action": "delete_rows", "table": "Items", "count": 32}, _insert("Items", [_id_row()], 32)]),
            TABLE_BRANCHES + ["rows.deleted_skip"],
        ),
        _scenario(
            "DAO-READ-ALLOC-DROP-RECREATE",
            caps + ["schema.catalog_and_table_definitions"],
            _recipe([_id_table(), _insert("Items", [_id_row()], 64), {"action": "drop_table", "name": "Items"}, _id_table(), _insert("Items", [_id_row()], 8)]),
            TABLE_BRANCHES,
        ),
        _scenario(
            "DAO-READ-ALLOC-IDLE-REOPEN",
            caps,
            _recipe([_id_table(), _insert("Items", [_id_row()], 8), {"action": "reopen"}, {"action": "reopen"}]),
            TABLE_BRANCHES,
        ),
        _scenario(
            "DAO-READ-ALLOC-MULTIPLE-TABLES",
            caps + ["schema.catalog_and_table_definitions"],
            _recipe([_id_table("Alpha"), _id_table("Beta"), _id_table("Gamma"), _insert("Alpha", [_id_row()], 8), _insert("Beta", [_id_row()], 64), _insert("Gamma", [_id_row()], 512)]),
            TABLE_BRANCHES + ["rows.overflow_pointer"],
        ),
    ]
    return scenarios


def _schema_scenarios() -> list[dict[str, Any]]:
    caps = ["schema.catalog_and_table_definitions"]
    scenarios = []
    for dao_type in DAO_TYPES:
        scenarios.append(
            _scenario(
                f"DAO-READ-SCHEMA-TYPE-{dao_type[2:].upper()}",
                caps,
                _recipe([_table("Typed", [_field("Id", "dbLong"), _field("Value", dao_type)])]),
                TABLE_BRANCHES,
            )
        )
    for suffix, size in [("TEXT-SIZE-1", 1), ("TEXT-SIZE-255", 255)]:
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", caps, _recipe([_table("Typed", [_field("Value", "dbText", size)])]), TABLE_BRANCHES))
    scenarios.append(
        _scenario("DAO-READ-SCHEMA-WIDE-TABLE", caps, _recipe([_table("Wide", [_field(f"F{index:02d}", DAO_TYPES[index % len(DAO_TYPES)]) for index in range(64)])]), TABLE_BRANCHES + ["tdef.continuation_chain"])
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
        branches = TABLE_BRANCHES + ["tdef.physical_index", "tdef.logical_index", "index.branch_leaf_traversal"]
        branches += ["index.composite_key_lossless"] if len(index["fields"]) > 1 else ["index.single_field_key"]
        rows = [[_value("Id", "integer", number), _value("Code", "unicode_string", f"C{number:02d}")] for number in (3, 1, 2)]
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", index_caps + extra, _recipe([_table("Keyed", two_columns, [index]), _insert("Keyed", rows)]), branches))
    relationship = {
        "action": "create_relationship",
        "name": "ParentChild",
        "table": "Parent",
        "foreign_table": "Child",
        "fields": [{"field": "Id", "foreign_field": "ParentId"}],
        "cascade_updates": False,
        "cascade_deletes": False,
    }
    parent = _table("Parent", [_field("Id", "dbLong")], [_index("PrimaryKey", [("Id", False)], primary=True, unique=True, required=True)])
    child = _table("Child", [_field("Id", "dbLong"), _field("ParentId", "dbLong")])
    for suffix, cascade in [("RELATIONSHIP", (False, False)), ("RELATIONSHIP-CASCADE", (True, True))]:
        step = dict(relationship, cascade_updates=cascade[0], cascade_deletes=cascade[1])
        scenarios.append(_scenario(f"DAO-READ-SCHEMA-{suffix}", index_caps, _recipe([parent, child, step]), TABLE_BRANCHES + ["tdef.physical_index", "tdef.logical_index", "tdef.relationship_reference"]))
    return scenarios


def _value_scenarios() -> list[dict[str, Any]]:
    scenarios = []
    for dao_type in DAO_TYPES:
        capability = TYPE_CAPABILITY[dao_type]
        type_name = dao_type[2:].upper()
        table = _table("Typed", [_field("Id", "dbLong"), _field("Value", dao_type)])
        base_branches = TABLE_BRANCHES + ["values.fixed_scalar"] + TEXT_BRANCH.get(dao_type, [])
        if dao_type != "dbBoolean":
            scenarios.append(
                _scenario(
                    f"DAO-READ-VALUES-{type_name}-NULL",
                    ["rows.streaming_read", "values.null_fixed_variable"],
                    _recipe([table, _insert("Typed", [[_value("Id", "integer", 1), _value("Value", "null", None)]])]),
                    TABLE_BRANCHES + ["values.null_field"],
                )
            )
        for suffix, encoding, value in TYPE_VALUES[dao_type]:
            branches = base_branches + LONG_VALUE_BRANCHES.get((dao_type, suffix), [])
            scenarios.append(
                _scenario(
                    f"DAO-READ-VALUES-{type_name}-{suffix}",
                    ["rows.streaming_read", capability],
                    _recipe([table, _insert("Typed", [[_value("Id", "integer", 1), _value("Value", encoding, value)]])]),
                    branches,
                )
            )
    for dao_type, unit, branch in [("dbMemo", "M", "long_value.single_page"), ("dbLongBinary", "a5", "long_value.single_page")]:
        type_name = dao_type[2:].upper()
        table = _table("Typed", [_field("Id", "dbLong"), _field("Value", dao_type)])
        encoding = "repeat_ascii" if dao_type == "dbMemo" else "repeat_byte"
        for length, position in [(2047, "below"), (2048, "at"), (2049, "above")]:
            scenarios.append(
                _scenario(
                    f"DAO-READ-VALUES-{type_name}-PAGE-{position.upper()}",
                    ["rows.streaming_read", "values.memo_ole_multi_page"],
                    _recipe([table, _insert("Typed", [[_value("Id", "integer", 1), _value("Value", encoding, {"unit": unit, "length": length})]])]),
                    TABLE_BRANCHES + [branch if position != "above" else "long_value.chained"],
                    boundary={"dimension": "long_value_page_payload", "position": position},
                )
            )
    scenarios.append(
        _scenario(
            "DAO-READ-VALUES-TEXT-CP1252-HIGH",
            ["rows.streaming_read", "values.code_pages_lossless_raw"],
            _recipe([_table("Typed", [_field("Id", "dbLong"), _field("Value", "dbText", 16)]), _insert("Typed", [[_value("Id", "integer", 1), _value("Value", "unicode_string", "Café €")]])]),
            TABLE_BRANCHES + ["values.text_cp1252"],
        )
    )
    return scenarios


def _rows_scenarios() -> list[dict[str, Any]]:
    caps = ["rows.streaming_read"]
    table = _table("Items", [_field("Id", "dbLong"), _field("Name", "dbText", 255), _field("Note", "dbText", 255)])
    long_row = [_value("Id", "integer", 1), _value("Name", "repeat_ascii", {"unit": "N", "length": 255}), _value("Note", "repeat_ascii", {"unit": "O", "length": 255})]
    return [
        _scenario("DAO-READ-ROWS-EMPTY-TABLE", caps, _recipe([_id_table()]), TABLE_BRANCHES),
        _scenario("DAO-READ-ROWS-SINGLE", caps, _recipe([_id_table(), _insert("Items", [_id_row()])]), TABLE_BRANCHES),
        _scenario("DAO-READ-ROWS-DUPLICATES", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 3)]), TABLE_BRANCHES),
        _scenario("DAO-READ-ROWS-PAGE-SPAN", caps, _recipe([table, _insert("Items", [long_row], 16)]), TABLE_BRANCHES + ["rows.overflow_pointer", "rows.wide_variable_layout"]),
        _scenario("DAO-READ-ROWS-DELETED-MIDDLE", caps, _recipe([_id_table(), _insert("Items", [_id_row()], 9), {"action": "delete_rows", "table": "Items", "count": 4}, _insert("Items", [_id_row()], 2)]), TABLE_BRANCHES + ["rows.deleted_skip"]),
        _scenario("DAO-READ-ROWS-MANY", caps + ["format.pages_allocation_usage"], _recipe([_id_table(), _insert("Items", [_id_row()], 4096)]), TABLE_BRANCHES + ["rows.overflow_pointer"]),
    ]


def build_inventory() -> dict[str, Any]:
    scenarios = _open_scenarios() + _alloc_scenarios() + _schema_scenarios() + _value_scenarios() + _rows_scenarios()
    scenarios.sort(key=lambda scenario: scenario["id"])
    return {"protocol_version": "1.2.0", "document_type": "dao_scenario_inventory", "scenarios": scenarios}


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
