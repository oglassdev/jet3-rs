#!/usr/bin/env python3
"""Validate the portable DAO protocol 1.2 differential read contract.

This module executes no DAO or COM operation and interprets no MDB byte. It
validates the declarative scenario inventory, the closed branch registry, and
canonical semantic snapshots against the checked-in schemas plus the
cross-field rules in `protocol/v1_2/README.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import build_v1_2_inventory
from protocol_validation import (
    ProtocolSchemaSet,
    ValidationError,
    canonical_json_bytes,
    load_json,
    load_json_with_bytes,
    validate_snapshot,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_ROOT = ROOT.parent.parent
SCHEMA_DIR = ROOT / "protocol" / "v1_2"
PROTOCOL_VERSION = "1.2.0"
SUPPORT_MATRIX = REPO_ROOT / "docs" / "validation" / "support-matrix.json"
BRANCH_REGISTRY = SCHEMA_DIR / "branch-registry.json"
SCENARIO_INVENTORY = SCHEMA_DIR / "scenarios.json"
SCHEMAS = {
    "dao_scenario_inventory": "scenarios.schema.json",
    "dao_branch_registry": "branch-registry.schema.json",
    "canonical_semantic_snapshot": "canonical-semantic-snapshot.schema.json",
    "rust_coverage_receipt": "coverage-receipt.schema.json",
}
SCHEMA_SET = ProtocolSchemaSet(SCHEMA_DIR, SCHEMAS)
FAMILY_FOR_MODE = {
    "rust_read_dao": "DAO-READ-",
    "dao_open_rust": "DAO-WRITE-",
    "dao_verify_rust_update": "DAO-UPDATE-",
}
ENABLED_MODES = frozenset({"rust_read_dao"})
DAO_TYPES = (
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
)
ENCODINGS_FOR_TYPE = {
    "dbBoolean": {"boolean"},
    "dbByte": {"integer"},
    "dbInteger": {"integer"},
    "dbLong": {"integer"},
    "dbCurrency": {"invariant_decimal"},
    "dbSingle": {"ieee_bits_hex"},
    "dbDouble": {"ieee_bits_hex"},
    "dbDate": {"invariant_datetime"},
    "dbBinary": {"lowercase_hex", "repeat_byte"},
    "dbText": {"unicode_string", "repeat_ascii"},
    "dbLongBinary": {"lowercase_hex", "repeat_byte"},
    "dbMemo": {"unicode_string", "repeat_ascii"},
    "dbGUID": {"guid"},
}
# Canonical typed-value kinds a snapshot may carry for each DAO column type.
KINDS_FOR_TYPE = {
    "dbBoolean": {"boolean"},
    "dbByte": {"byte"},
    "dbInteger": {"integer"},
    "dbLong": {"long"},
    "dbCurrency": {"currency"},
    "dbSingle": {"single"},
    "dbDouble": {"double"},
    "dbDate": {"datetime"},
    "dbBinary": {"binary"},
    "dbText": {"text"},
    "dbLongBinary": {"ole"},
    "dbMemo": {"memo"},
    "dbGUID": {"guid"},
}
# Kinds whose typed value has no physical field bytes to retain.
RAW_EXEMPT_KINDS = frozenset({"null", "boolean"})
# Physical widths of fixed-size non-null row values, in bytes.
RAW_WIDTH_FOR_TYPE = {
    "dbByte": 1,
    "dbInteger": 2,
    "dbLong": 4,
    "dbCurrency": 8,
    "dbSingle": 4,
    "dbDouble": 8,
    "dbDate": 8,
    "dbGUID": 16,
}
NORMALIZED_SIZE_FOR_TYPE = {
    "dbBoolean": 1,
    "dbByte": 1,
    "dbInteger": 2,
    "dbLong": 4,
    "dbCurrency": 8,
    "dbSingle": 4,
    "dbDouble": 8,
    "dbDate": 8,
    "dbLongBinary": 0,
    "dbMemo": 0,
    "dbGUID": 16,
}
COMPARABLE_COLUMN_ATTRIBUTE_MASK = 1 | 2 | 16
INTEGER_RANGES = {"dbByte": (0, 255), "dbInteger": (-32768, 32767), "dbLong": (-2147483648, 2147483647)}
IEEE_WIDTH = {"dbSingle": 8, "dbDouble": 16}
VALUE_PATTERNS = {
    "invariant_decimal": r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",
    "invariant_datetime": r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?",
    "lowercase_hex": r"(?:[0-9a-f]{2})+",
    "guid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
}
JSON_POINTER = re.compile(r"(?:/(?:[^/~]|~0|~1)*)+")

# The plan's named minimum read set (IMPLEMENTATION_PLAN.md Section 5.1).
# Every requirement maps to the exact scenario ids that satisfy it. A missing
# requirement must be listed in the inventory's deferred_requirements with the
# provenance it needs; --complete rejects any deferral.
REQUIRED_SCENARIOS: dict[str, tuple[str, ...]] = {
    "open.fresh_empty": ("DAO-READ-OPEN-EMPTY",),
    "open.after_growth": ("DAO-READ-OPEN-GROWN",),
    "open.largest_supported_size": ("DAO-READ-OPEN-LARGEST-SIZE",),
    "open.reject_jet4": ("DAO-READ-OPEN-REJECT-JET4",),
    "open.reject_encrypted": ("DAO-READ-OPEN-REJECT-ENCRYPTED",),
    "open.reject_password": ("DAO-READ-OPEN-REJECT-PASSWORD",),
    "allocation.small_inline": ("DAO-READ-ALLOC-SMALL-INLINE",),
    "allocation.inline_capacity_boundary": (
        "DAO-READ-ALLOC-INLINE-CAPACITY-BELOW",
        "DAO-READ-ALLOC-INLINE-CAPACITY-AT",
        "DAO-READ-ALLOC-INLINE-CAPACITY-ABOVE",
    ),
    "allocation.delete_reinsert_reuse": ("DAO-READ-ALLOC-DELETE-REINSERT",),
    "allocation.drop_recreate": ("DAO-READ-ALLOC-DROP-RECREATE",),
    "allocation.idle_reopen": ("DAO-READ-ALLOC-IDLE-REOPEN",),
    "allocation.inline_to_indirect": ("DAO-READ-ALLOC-EXTENDED-SLOT-1-ABOVE",),
    "allocation.extended_slot_1_boundary": (
        "DAO-READ-ALLOC-EXTENDED-SLOT-1-BELOW",
        "DAO-READ-ALLOC-EXTENDED-SLOT-1-AT",
        "DAO-READ-ALLOC-EXTENDED-SLOT-1-ABOVE",
    ),
    "allocation.further_extended_slots": ("DAO-READ-ALLOC-EXTENDED-SLOT-2-AT",),
    "allocation.multiple_tables": ("DAO-READ-ALLOC-MULTIPLE-TABLES",),
    "schema.every_type": tuple(f"DAO-READ-SCHEMA-TYPE-{dao_type[2:].upper()}" for dao_type in DAO_TYPES),
    "schema.every_index_form": (
        "DAO-READ-SCHEMA-INDEX-PRIMARY",
        "DAO-READ-SCHEMA-INDEX-UNIQUE",
        "DAO-READ-SCHEMA-INDEX-NONUNIQUE",
        "DAO-READ-SCHEMA-INDEX-COMPOSITE-ASCENDING",
        "DAO-READ-SCHEMA-INDEX-COMPOSITE-DESCENDING",
        "DAO-READ-SCHEMA-INDEX-COMPOSITE-MIXED",
    ),
    "schema.relationships": ("DAO-READ-SCHEMA-RELATIONSHIP", "DAO-READ-SCHEMA-RELATIONSHIP-CASCADE"),
    "values.null_per_type": tuple(f"DAO-READ-VALUES-{dao_type[2:].upper()}-NULL" for dao_type in DAO_TYPES if dao_type != "dbBoolean"),
    "values.boundaries_per_type": tuple(
        f"DAO-READ-VALUES-{dao_type[2:].upper()}-{label}"
        for dao_type in DAO_TYPES
        if dao_type not in ("dbBoolean", "dbMemo", "dbLongBinary")
        for label in ("MIN", "REP", "MAX")
    ) + ("DAO-READ-VALUES-BOOLEAN-FALSE", "DAO-READ-VALUES-BOOLEAN-TRUE"),
    "values.long_value_forms": tuple(
        f"DAO-READ-VALUES-{name}-{label}"
        for name in ("MEMO", "LONGBINARY")
        for label in ("INLINE-32", "SINGLE-PAGE-512", "CHAINED-2048", "CHAINED-4096", "MAX-32769")
    ),
    "values.code_page_cp1252": ("DAO-READ-VALUES-TEXT-REP",),
    "values.code_page_cp1251": ("DAO-READ-VALUES-TEXT-CP1251-REP",),
    "rows.streaming_forms": (
        "DAO-READ-ROWS-EMPTY-TABLE",
        "DAO-READ-ROWS-SINGLE",
        "DAO-READ-ROWS-DUPLICATES",
        "DAO-READ-ROWS-PAGE-SPAN",
        "DAO-READ-ROWS-DELETED-MIDDLE",
        "DAO-READ-ROWS-MANY",
    ),
}


def scenario_content_sha256(scenario: dict[str, Any]) -> str:
    """Return the SHA-256 of the canonical projection without content_sha256."""
    projection = {key: value for key, value in scenario.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def load_capability_ids(path: Path = SUPPORT_MATRIX) -> frozenset[str]:
    document = load_json(path)
    capabilities = document.get("capabilities") if isinstance(document, dict) else None
    if not isinstance(capabilities, list):
        raise ValidationError(f"{path}: support matrix lacks a capabilities array")
    return frozenset(entry["id"] for entry in capabilities if isinstance(entry, dict) and isinstance(entry.get("id"), str))


def validate_registry(document: dict[str, Any]) -> frozenset[str]:
    ids = [branch["id"] for branch in document["branches"]]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValidationError("$.branches: ids must be unique and sorted")
    for index, branch in enumerate(document["branches"]):
        if not (REPO_ROOT / branch["module"]).is_file():
            raise ValidationError(f"$.branches[{index}].module: {branch['module']} is not a checked-in source file")
    return frozenset(ids)


def load_branch_ids(path: Path = BRANCH_REGISTRY) -> frozenset[str]:
    document = load_json(path)
    if SCHEMA_SET.validate(document) != "dao_branch_registry":
        raise ValidationError(f"{path}: not a branch registry")
    return validate_registry(document)


def _validate_value(value: dict[str, Any], column: dict[str, Any], location: str) -> None:
    dao_type = column["dao_type"]
    encoding = value["encoding"]
    payload = value["value"]
    if encoding == "null":
        if column["required"]:
            raise ValidationError(f"{location}: null value for required field {column['name']!r}")
        if payload is not None:
            raise ValidationError(f"{location}.value: null encoding requires JSON null")
        return
    if encoding not in ENCODINGS_FOR_TYPE[dao_type]:
        raise ValidationError(f"{location}.encoding: {encoding} is not admitted for {dao_type}")
    if encoding == "boolean" and not isinstance(payload, bool):
        raise ValidationError(f"{location}.value: boolean encoding requires a boolean")
    if encoding == "integer":
        lower, upper = INTEGER_RANGES[dao_type]
        if isinstance(payload, bool) or not isinstance(payload, int) or not lower <= payload <= upper:
            raise ValidationError(f"{location}.value: integer outside the {dao_type} range")
    if encoding == "ieee_bits_hex" and (not isinstance(payload, str) or re.fullmatch(rf"[0-9a-f]{{{IEEE_WIDTH[dao_type]}}}", payload) is None):
        raise ValidationError(f"{location}.value: {dao_type} requires {IEEE_WIDTH[dao_type]} lowercase hex digits")
    pattern = VALUE_PATTERNS.get(encoding)
    if pattern is not None and (not isinstance(payload, str) or re.fullmatch(pattern, payload) is None):
        raise ValidationError(f"{location}.value: {encoding} text has the wrong shape")
    if encoding == "unicode_string" and not isinstance(payload, str):
        raise ValidationError(f"{location}.value: unicode_string requires a string")
    if encoding in ("repeat_byte", "repeat_ascii"):
        if not isinstance(payload, dict):
            raise ValidationError(f"{location}.value: {encoding} requires a unit/length object")
        unit = payload["unit"]
        if encoding == "repeat_byte" and re.fullmatch(r"[0-9a-f]{2}", unit) is None:
            raise ValidationError(f"{location}.value.unit: repeat_byte requires one lowercase hex byte")
        if encoding == "repeat_ascii" and re.fullmatch(r"[\x20-\x7e]", unit) is None:
            raise ValidationError(f"{location}.value.unit: repeat_ascii requires one printable ASCII character")
    size = column["size"]
    bounded = {"lowercase_hex": lambda text: len(text) // 2, "unicode_string": len, "repeat_byte": lambda spec: spec["length"], "repeat_ascii": lambda spec: spec["length"]}
    if dao_type in ("dbText", "dbBinary") and encoding in bounded and size is not None and bounded[encoding](payload) > size:
        raise ValidationError(f"{location}.value: exceeds the declared size {size}")


def _validate_recipe(recipe: dict[str, Any], location: str) -> None:
    steps = recipe["steps"]
    if steps[0]["action"] != "create_database" or steps[-1]["action"] != "close_database":
        raise ValidationError(f"{location}.steps: must start with create_database and end with close_database")
    if sum(step["action"] == "create_database" for step in steps) != 1 or sum(step["action"] == "close_database" for step in steps) != 1:
        raise ValidationError(f"{location}.steps: exactly one create_database and one close_database")
    tables: dict[str, dict[str, dict[str, Any]]] = {}
    for index, step in enumerate(steps):
        where = f"{location}.steps[{index}]"
        action = step["action"]
        if action == "create_table":
            if step["name"] in tables:
                raise ValidationError(f"{where}: table {step['name']!r} already exists")
            fields = {column["name"]: column for column in step["fields"]}
            if len(fields) != len(step["fields"]):
                raise ValidationError(f"{where}.fields: names must be unique")
            for field_index, column in enumerate(step["fields"]):
                sized = column["dao_type"] in ("dbText", "dbBinary")
                if sized != (column["size"] is not None):
                    raise ValidationError(f"{where}.fields[{field_index}].size: size is required exactly for dbText and dbBinary")
            index_names = [entry["name"] for entry in step["indexes"]]
            if len(index_names) != len(set(index_names)):
                raise ValidationError(f"{where}.indexes: names must be unique")
            if sum(entry["primary"] for entry in step["indexes"]) > 1:
                raise ValidationError(f"{where}.indexes: at most one primary index")
            for entry in step["indexes"]:
                names = [item["name"] for item in entry["fields"]]
                if len(names) != len(set(names)) or any(name not in fields for name in names):
                    raise ValidationError(f"{where}.indexes: index {entry['name']!r} references unknown or repeated fields")
                if entry["primary"] and not (entry["unique"] and entry["required"]):
                    raise ValidationError(f"{where}.indexes: primary index {entry['name']!r} must be unique and required")
            tables[step["name"]] = fields
        elif action == "create_relationship":
            for side in ("table", "foreign_table"):
                if step[side] not in tables:
                    raise ValidationError(f"{where}.{side}: unknown table {step[side]!r}")
            for pair in step["fields"]:
                if pair["field"] not in tables[step["table"]] or pair["foreign_field"] not in tables[step["foreign_table"]]:
                    raise ValidationError(f"{where}.fields: relationship references an unknown field")
        elif action in ("insert_rows", "insert_until_page_count", "delete_rows"):
            fields = tables.get(step["table"])
            if fields is None:
                raise ValidationError(f"{where}.table: unknown table {step['table']!r}")
            rows = [step["row"]] if action == "insert_until_page_count" else step.get("rows", [])
            for row_index, row in enumerate(rows):
                seen = set()
                for value_index, value in enumerate(row):
                    column = fields.get(value["field"])
                    if column is None or value["field"] in seen:
                        raise ValidationError(f"{where}.rows[{row_index}][{value_index}]: unknown or repeated field")
                    seen.add(value["field"])
                    _validate_value(value, column, f"{where}.rows[{row_index}][{value_index}]")
                for name, column in fields.items():
                    if column["required"] and name not in seen:
                        raise ValidationError(f"{where}.rows[{row_index}]: required field {name!r} is missing")
        elif action == "drop_table":
            if step["name"] not in tables:
                raise ValidationError(f"{where}.name: unknown table {step['name']!r}")
            del tables[step["name"]]


def validate_required_coverage(document: dict[str, Any], *, complete: bool) -> list[str]:
    """Check the plan's named minimum set; return the deferred requirement ids."""
    present = {scenario["id"]: scenario for scenario in document["scenarios"]}
    generated = {
        scenario["id"]: scenario
        for scenario in build_v1_2_inventory.build_inventory()["scenarios"]
    }
    deferred = [entry["requirement"] for entry in document["deferred_requirements"]]
    if deferred != sorted(deferred) or len(deferred) != len(set(deferred)):
        raise ValidationError("$.deferred_requirements: requirements must be unique and sorted")
    unknown = sorted(set(deferred) - set(REQUIRED_SCENARIOS))
    if unknown:
        raise ValidationError(f"$.deferred_requirements: not plan requirements: {unknown}")
    for requirement, scenario_ids in REQUIRED_SCENARIOS.items():
        missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in present]
        if missing and requirement not in deferred:
            raise ValidationError(f"requirement {requirement}: missing scenarios {missing} and not deferred")
        if not missing and requirement in deferred:
            raise ValidationError(f"requirement {requirement}: deferred although its scenarios are present")
        for scenario_id in scenario_ids:
            if scenario_id not in present:
                continue
            expected = generated.get(scenario_id)
            if expected is None:
                raise ValidationError(
                    f"requirement {requirement}: {scenario_id} has no generated contract"
                )
            if present[scenario_id] != expected:
                raise ValidationError(
                    f"requirement {requirement}: {scenario_id} differs from its generated contract"
                )
    if complete and deferred:
        raise ValidationError(f"inventory is incomplete: deferred requirements {deferred}")
    return deferred


def validate_inventory(document: dict[str, Any], *, capability_ids: frozenset[str], branch_ids: frozenset[str], complete: bool = False) -> list[str]:
    """Validate cross-field inventory rules after schema validation."""
    scenarios = document["scenarios"]
    ids = [scenario["id"] for scenario in scenarios]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValidationError("$.scenarios: ids must be unique and sorted")
    for index, scenario in enumerate(scenarios):
        location = f"$.scenarios[{index}]"
        expected = scenario_content_sha256(scenario)
        if scenario["content_sha256"] != expected:
            raise ValidationError(f"{location}.content_sha256: expected {expected}")
        operation = scenario["operation"]
        mode = operation["mode"]
        if not scenario["id"].startswith(FAMILY_FOR_MODE[mode]):
            raise ValidationError(f"{location}.id: family does not agree with operation.mode")
        if mode not in ENABLED_MODES:
            raise ValidationError(f"{location}.operation.mode: {mode} is not enabled in protocol {PROTOCOL_VERSION}")
        if (operation["expected_outcome"] == "expected_error") != (operation["error_class"] is not None):
            raise ValidationError(f"{location}.operation: expected_error requires error_class and success forbids it")
        unknown = sorted(set(scenario["capability_ids"]) - capability_ids)
        if unknown:
            raise ValidationError(f"{location}.capability_ids: not in the support matrix: {unknown}")
        unknown = sorted(set(scenario["required_branches"]) - branch_ids)
        if unknown:
            raise ValidationError(f"{location}.required_branches: not in the branch registry: {unknown}")
        boundary = scenario["boundary"]
        if boundary is not None:
            forbidden = set(boundary["forbidden_branches"])
            unknown = sorted(forbidden - branch_ids)
            if unknown:
                raise ValidationError(
                    f"{location}.boundary.forbidden_branches: not in the branch registry: {unknown}"
                )
            overlap = sorted(forbidden & set(scenario["required_branches"]))
            if overlap:
                raise ValidationError(
                    f"{location}.boundary: branches cannot be both required and forbidden: {overlap}"
                )
        if mode == "rust_read_dao" and scenario["preserve_paths"]:
            raise ValidationError(f"{location}.preserve_paths: read scenarios preserve nothing")
        recipe = scenario["generator_recipe"]
        negative = recipe["database_version"] != "dbVersion30" or recipe["encrypted"] or recipe["password"] is not None
        if negative != (operation["expected_outcome"] == "expected_error"):
            raise ValidationError(f"{location}: unsupported opening states must be expected_error scenarios and vice versa")
        _validate_recipe(recipe, f"{location}.generator_recipe")
    return validate_required_coverage(document, complete=complete)


def _validate_comparable_typed_value(value: dict[str, Any], location: str) -> None:
    kind = value["kind"]
    if kind not in RAW_EXEMPT_KINDS and "raw_hex" not in value:
        raise ValidationError(f"{location}: converted values must retain raw_hex")
    if kind in RAW_EXEMPT_KINDS and "raw_hex" in value:
        raise ValidationError(f"{location}: {kind} values carry no field bytes")
    if kind in ("text", "memo"):
        if "code_page" not in value:
            raise ValidationError(f"{location}: {kind} values must identify their code_page")
        code_page = value["code_page"]
        codec = {1251: "cp1251", 1252: "cp1252"}.get(code_page)
        if codec is None:
            raise ValidationError(
                f"{location}: text code_page must be Windows-1251 or Windows-1252"
            )
        try:
            raw = bytes.fromhex(value["raw_hex"])
        except ValueError as exc:
            raise ValidationError(
                f"{location}: text raw_hex must be valid lowercase hexadecimal"
            ) from exc
        try:
            decoded = raw.decode(codec, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"{location}: text raw_hex contains an undefined code-page byte"
            ) from exc
        if decoded != value["value"]:
            raise ValidationError(
                f"{location}: text raw_hex must decode exactly to value"
            )
    if kind == "ole" and value.get("raw_hex") != value["value"]:
        raise ValidationError(f"{location}: OLE raw_hex must equal the logical payload bytes")


def _validate_property_map(properties: dict[str, Any], location: str) -> None:
    for name, value in properties.items():
        if not name:
            raise ValidationError(f"{location}: property names must not be empty")
        _validate_comparable_typed_value(value, f"{location}[{name!r}]")


def _validate_table_integrity(table: dict[str, Any], location: str) -> None:
    _validate_property_map(table["properties"], f"{location}.properties")
    columns = {column["name"]: column for column in table["columns"]}
    for column_index, column in enumerate(table["columns"]):
        if not column["name"]:
            raise ValidationError(
                f"{location}.columns[{column_index}].name: must not be empty"
            )
        if column["dao_type"] not in KINDS_FOR_TYPE:
            raise ValidationError(f"{location}.columns[{column_index}].dao_type: unknown DAO type {column['dao_type']!r}")
        normalized_size = NORMALIZED_SIZE_FOR_TYPE.get(column["dao_type"])
        if normalized_size is None:
            normalized_size = column["size"]
            if not isinstance(normalized_size, int) or not 1 <= normalized_size <= 255:
                raise ValidationError(
                    f"{location}.columns[{column_index}].size: declared dbText/dbBinary size must be 1..255"
                )
        if column["size"] != normalized_size:
            raise ValidationError(
                f"{location}.columns[{column_index}].size: expected normalized size {normalized_size}"
            )
        attributes = column["attributes"]
        expected_auto = attributes == 17
        if attributes not in (1, 2, 17) or column["auto_increment"] != expected_auto:
            raise ValidationError(
                f"{location}.columns[{column_index}].attributes: expected normalized attributes 1, 2, or 17"
            )
        if column["auto_increment"] and column["dao_type"] != "dbLong":
            raise ValidationError(
                f"{location}.columns[{column_index}].auto_increment: only dbLong is admitted"
            )
        _validate_property_map(
            column["properties"], f"{location}.columns[{column_index}].properties"
        )
    if sum(index["primary"] for index in table["indexes"]) > 1:
        raise ValidationError(f"{location}.indexes: at most one primary index")
    for index_index, index in enumerate(table["indexes"]):
        if not index["name"]:
            raise ValidationError(
                f"{location}.indexes[{index_index}].name: must not be empty"
            )
        if index["primary"] and not (index["unique"] and index["required"]):
            raise ValidationError(
                f"{location}.indexes[{index_index}]: primary indexes must be unique and required"
            )
        _validate_property_map(
            index["properties"], f"{location}.indexes[{index_index}].properties"
        )
        names = [entry["name"] for entry in index["fields"]]
        if any(not name for name in names):
            raise ValidationError(
                f"{location}.indexes[{index_index}].fields: names must not be empty"
            )
        if len(names) != len(set(names)):
            raise ValidationError(f"{location}.indexes[{index_index}].fields: repeated column")
        unknown = [name for name in names if name not in columns]
        if unknown:
            raise ValidationError(f"{location}.indexes[{index_index}].fields: unknown columns {unknown}")
    for row_index, row in enumerate(table["rows"]):
        where = f"{location}.rows[{row_index}].values"
        if set(row["values"]) != set(columns):
            raise ValidationError(f"{where}: keys must equal the declared column names exactly")
        for name, value in row["values"].items():
            kind = value["kind"]
            allowed = KINDS_FOR_TYPE[columns[name]["dao_type"]] | {"null"}
            if kind not in allowed:
                raise ValidationError(f"{where}[{name!r}].kind: {kind} is not admitted for {columns[name]['dao_type']}")
            value_location = f"{where}[{name!r}]"
            _validate_comparable_typed_value(value, value_location)
            expected_width = RAW_WIDTH_FOR_TYPE.get(columns[name]["dao_type"])
            if kind != "null" and expected_width is not None:
                actual_width = len(value["raw_hex"]) // 2
                if actual_width != expected_width:
                    raise ValidationError(
                        f"{value_location}.raw_hex: expected {expected_width} bytes for "
                        f"{columns[name]['dao_type']}, got {actual_width}"
                    )


def validate_semantic_snapshot(document: dict[str, Any]) -> None:
    """Validate 1.2 model integrity and row identity on top of the shared rules."""
    scenario = _scenario_for(document)
    _validate_outcome_for_scenario(document, scenario)
    if document["outcome"] == "opening_failure":
        return
    for table_index, table in enumerate(document["tables"]):
        if not table["name"]:
            raise ValidationError(f"$.tables[{table_index}].name: must not be empty")
    validate_snapshot(document)
    _validate_property_map(document["database_properties"], "$.database_properties")
    tables = {table["name"]: table for table in document["tables"]}
    for table_index, table in enumerate(document["tables"]):
        location = f"$.tables[{table_index}]"
        _validate_table_integrity(table, location)
        previous: tuple[str, int] | None = None
        for row_index, row in enumerate(table["rows"]):
            digest = hashlib.sha256(canonical_json_bytes(row["values"])).hexdigest()
            if row["canonical_key"] != digest:
                raise ValidationError(f"{location}.rows[{row_index}].canonical_key: expected {digest}")
            current = (row["canonical_key"], row["duplicate_ordinal"])
            if previous is not None and current <= previous:
                raise ValidationError(f"{location}.rows[{row_index}]: rows must ascend by canonical_key then duplicate_ordinal")
            expected_ordinal = previous[1] + 1 if previous is not None and previous[0] == current[0] else 0
            if current[1] != expected_ordinal:
                raise ValidationError(f"{location}.rows[{row_index}].duplicate_ordinal: expected {expected_ordinal}")
            previous = current
    for index, relationship in enumerate(document["relationships"]):
        location = f"$.relationships[{index}]"
        for key in ("name", "table", "foreign_table"):
            if not relationship[key]:
                raise ValidationError(f"{location}.{key}: must not be empty")
        for field_index, pair in enumerate(relationship["fields"]):
            for key in ("field", "foreign_field"):
                if not pair[key]:
                    raise ValidationError(
                        f"{location}.fields[{field_index}].{key}: must not be empty"
                    )
        _validate_property_map(relationship["properties"], f"{location}.properties")
        for side, column_key in (("table", "field"), ("foreign_table", "foreign_field")):
            table = tables.get(relationship[side])
            if table is None:
                raise ValidationError(f"{location}.{side}: unknown table {relationship[side]!r}")
            known = {column["name"] for column in table["columns"]}
            unknown = [pair[column_key] for pair in relationship["fields"] if pair[column_key] not in known]
            if unknown:
                raise ValidationError(f"{location}.fields: unknown {column_key} columns {unknown}")
        field_pairs = [
            (pair["field"], pair["foreign_field"])
            for pair in relationship["fields"]
        ]
        if len(field_pairs) != len(set(field_pairs)):
            raise ValidationError(f"{location}.fields: field pairs must be unique")
    for index, entry in enumerate(document["raw_preservation"]):
        for key in ("semantic_path", "purpose"):
            if not entry[key]:
                raise ValidationError(
                    f"$.raw_preservation[{index}].{key}: must not be empty"
                )
    raw_paths = [entry["semantic_path"] for entry in document["raw_preservation"]]
    if raw_paths != sorted(set(raw_paths)):
        raise ValidationError(
            "$.raw_preservation: semantic paths must be unique and canonically ordered"
        )
    for key in document["producer_extensions"]:
        if JSON_POINTER.fullmatch(key) is None:
            raise ValidationError(f"$.producer_extensions[{key!r}]: keys must be JSON pointers")
        if key.endswith("/jet_external_long_value_header"):
            _validate_external_long_value_header(document, key)


def _validate_external_long_value_header(document: dict[str, Any], key: str) -> None:
    if document["producer"]["kind"] != "rust":
        raise ValidationError(
            f"$.producer_extensions[{key!r}]: external Jet headers are Rust-only metadata"
        )
    match = re.fullmatch(
        r"/tables/(\d+)/rows/(\d+)/values/((?:~[01]|[^~/])+)"
        r"/jet_external_long_value_header",
        key,
    )
    if match is None:
        raise ValidationError(
            f"$.producer_extensions[{key!r}]: invalid external long-value association"
        )
    table_index, row_index = (int(match.group(1)), int(match.group(2)))
    column_name = match.group(3).replace("~1", "/").replace("~0", "~")
    try:
        value = document["tables"][table_index]["rows"][row_index]["values"][column_name]
    except (IndexError, KeyError):
        raise ValidationError(
            f"$.producer_extensions[{key!r}]: association does not resolve"
        ) from None
    if value["kind"] not in ("memo", "ole"):
        raise ValidationError(
            f"$.producer_extensions[{key!r}]: association must resolve to Memo/OLE"
        )
    header = document["producer_extensions"][key]
    if (
        header.get("kind") != "binary"
        or len(header.get("value", "")) != 24
        or header.get("raw_hex") != header.get("value")
    ):
        raise ValidationError(
            f"$.producer_extensions[{key!r}]: header must be an exact 12-byte binary value"
        )


def normalize_dao_column_attributes(raw_attributes: int) -> int:
    """Mask DAO field attributes to the protocol's comparable bits."""
    return raw_attributes & COMPARABLE_COLUMN_ATTRIBUTE_MASK


def _scenario_for(
    document: dict[str, Any], *, scenario_inventory_path: Path = SCENARIO_INVENTORY
) -> dict[str, Any]:
    inventory = load_json(scenario_inventory_path)
    if SCHEMA_SET.validate(inventory) != "dao_scenario_inventory":
        raise ValidationError(f"{scenario_inventory_path}: not a scenario inventory")
    validate_inventory(
        inventory,
        capability_ids=load_capability_ids(),
        branch_ids=load_branch_ids(),
    )
    matches = [
        scenario
        for scenario in inventory["scenarios"]
        if scenario["id"] == document["scenario_id"]
    ]
    if not matches:
        raise ValidationError(
            f"$.scenario_id: unknown scenario {document['scenario_id']!r}"
        )
    if len(matches) != 1:
        raise ValidationError(
            f"$.scenario_id: scenario {document['scenario_id']!r} is not unique"
        )
    return matches[0]


def _validate_outcome_for_scenario(
    document: dict[str, Any], scenario: dict[str, Any]
) -> None:
    operation = scenario["operation"]
    if document["outcome"] == "opening_failure":
        if operation["expected_outcome"] != "expected_error":
            raise ValidationError(
                "$.outcome: opening_failure requires an expected_error scenario"
            )
        if document["error_class"] != operation["error_class"]:
            raise ValidationError(
                "$.error_class: opening failure does not match the scenario error class"
            )
    elif operation["expected_outcome"] != "success":
        raise ValidationError("$.outcome: success requires a success scenario")
    elif (
        document["document_type"] == "rust_coverage_receipt"
        and "open.rejected_format" in document["branches"]
    ):
        raise ValidationError(
            "$.branches: successful traversal cannot claim rejected-format coverage"
        )


def validate_coverage_receipt(
    document: dict[str, Any], *, scenario_inventory_path: Path = SCENARIO_INVENTORY
) -> None:
    """Bind a canonical receipt to its scenario and the closed branch registry."""
    branches = document["branches"]
    if branches != sorted(branches) or len(branches) != len(set(branches)):
        raise ValidationError("$.branches: branch ids must be unique and sorted")
    branch_ids = load_branch_ids()
    observed = set(branches)
    unknown = sorted(observed - branch_ids)
    if unknown:
        raise ValidationError(f"$.branches: not in the branch registry: {unknown}")

    scenario = _scenario_for(document, scenario_inventory_path=scenario_inventory_path)
    _validate_outcome_for_scenario(document, scenario)
    required = set(scenario["required_branches"])
    boundary = scenario["boundary"]
    forbidden = set(boundary["forbidden_branches"]) if boundary is not None else set()
    overlap = sorted(required & forbidden)
    if overlap:
        raise ValidationError(
            f"$.scenario_id: scenario branches cannot be both required and forbidden: {overlap}"
        )
    missing = sorted(required - observed)
    if missing:
        raise ValidationError(f"$.branches: missing required scenario branches: {missing}")
    rejected = sorted(forbidden & observed)
    if rejected:
        raise ValidationError(f"$.branches: contains forbidden scenario branches: {rejected}")


def validate_document(document: Any, *, complete: bool = False) -> str:
    document_type = SCHEMA_SET.validate(document)
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValidationError("$.protocol_version: unsupported protocol version")
    if document_type == "dao_scenario_inventory":
        validate_inventory(document, capability_ids=load_capability_ids(), branch_ids=load_branch_ids(), complete=complete)
    elif document_type == "dao_branch_registry":
        validate_registry(document)
    elif document_type == "canonical_semantic_snapshot":
        validate_semantic_snapshot(document)
    else:
        validate_coverage_receipt(document)
    return document_type


def validate_document_path(path: Path, *, complete: bool = False) -> str:
    document = load_json(path)
    document_type = validate_document(document, complete=complete)
    if document_type in ("canonical_semantic_snapshot", "rust_coverage_receipt") and path.read_bytes() != canonical_json_bytes(document):
        raise ValidationError(f"{path}: canonical document bytes are not normalized")
    return document_type


def validate_artifact_pair(
    snapshot: dict[str, Any], coverage_receipt: dict[str, Any]
) -> None:
    """Validate and bind the two artifacts emitted by one Rust read outcome."""
    snapshot_type = validate_document(snapshot)
    if snapshot_type != "canonical_semantic_snapshot":
        raise ValidationError("snapshot: expected a canonical semantic snapshot")
    receipt_type = validate_document(coverage_receipt)
    if receipt_type != "rust_coverage_receipt":
        raise ValidationError("coverage receipt: expected a Rust coverage receipt")

    if snapshot["producer"]["kind"] != "rust":
        raise ValidationError(
            "$.producer.kind: a Rust coverage receipt requires a Rust snapshot producer"
        )
    bindings = (
        ("protocol_version", snapshot["protocol_version"], coverage_receipt["protocol_version"]),
        ("scenario_id", snapshot["scenario_id"], coverage_receipt["scenario_id"]),
        (
            "source_revision",
            snapshot["producer"]["source_revision"],
            coverage_receipt["source_revision"],
        ),
        ("database_sha256", snapshot["database_sha256"], coverage_receipt["database_sha256"]),
        ("outcome", snapshot["outcome"], coverage_receipt["outcome"]),
        ("error_class", snapshot["error_class"], coverage_receipt["error_class"]),
    )
    mismatches = [name for name, left, right in bindings if left != right]
    if mismatches:
        raise ValidationError(
            "$: snapshot and coverage receipt bindings differ: "
            + ", ".join(mismatches)
        )

    allocated_set = coverage_receipt["allocated_set_sha256"]
    if snapshot["outcome"] == "success" and allocated_set is None:
        raise ValidationError(
            "$.allocated_set_sha256: successful traversal requires allocation evidence"
        )
    if snapshot["outcome"] == "opening_failure" and allocated_set is not None:
        raise ValidationError(
            "$.allocated_set_sha256: opening failure forbids allocation evidence"
        )


def validate_artifact_pair_paths(snapshot_path: Path, receipt_path: Path) -> None:
    """Validate canonical artifact bytes and their cross-document bindings."""
    snapshot, snapshot_bytes = load_json_with_bytes(snapshot_path)
    receipt, receipt_bytes = load_json_with_bytes(receipt_path)
    if snapshot_bytes != canonical_json_bytes(snapshot):
        raise ValidationError(f"{snapshot_path}: canonical document bytes are not normalized")
    if receipt_bytes != canonical_json_bytes(receipt):
        raise ValidationError(f"{receipt_path}: canonical document bytes are not normalized")
    validate_artifact_pair(snapshot, receipt)


def validate_schemas() -> None:
    SCHEMA_SET.lint()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate DAO protocol 1.2 documents.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schemas", help="lint all protocol 1.2 schema files")
    inventory = subparsers.add_parser("inventory", help="validate the scenario inventory")
    inventory.add_argument("path", type=Path)
    inventory.add_argument("--complete", action="store_true", help="reject any deferred plan requirement")
    subparsers.add_parser("document", help="validate one snapshot or registry document").add_argument("path", type=Path)
    pair = subparsers.add_parser(
        "pair", help="validate one canonical Rust snapshot/coverage-receipt pair"
    )
    pair.add_argument("snapshot", type=Path)
    pair.add_argument("coverage_receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "schemas":
            validate_schemas()
            print(f"PASS: {len(SCHEMAS)} protocol 1.2 schemas")
        elif args.command == "inventory":
            document = load_json(args.path)
            if validate_document(document, complete=args.complete) != "dao_scenario_inventory":
                raise ValidationError(f"{args.path}: not a scenario inventory")
            deferred = len(document["deferred_requirements"])
            print(f"PASS: {args.path} ({len(document['scenarios'])} scenarios, {deferred} deferred plan requirements)")
        elif args.command == "document":
            document_type = validate_document_path(args.path)
            print(f"PASS: {args.path} ({document_type})")
        else:
            validate_artifact_pair_paths(args.snapshot, args.coverage_receipt)
            print(
                f"PASS: {args.snapshot} + {args.coverage_receipt} "
                "(canonical Rust artifact pair)"
            )
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
