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
import json
import re
import sys
from pathlib import Path
from typing import Any

from protocol_validation import (
    ProtocolSchemaSet,
    ValidationError,
    canonical_json_bytes,
    load_json,
    validate_snapshot,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_ROOT = ROOT.parent.parent
SCHEMA_DIR = ROOT / "protocol" / "v1_2"
PROTOCOL_VERSION = "1.2.0"
SUPPORT_MATRIX = REPO_ROOT / "docs" / "validation" / "support-matrix.json"
BRANCH_REGISTRY = SCHEMA_DIR / "branch-registry.json"
SCHEMAS = {
    "dao_scenario_inventory": "scenarios.schema.json",
    "dao_branch_registry": "branch-registry.schema.json",
    "canonical_semantic_snapshot": "canonical-semantic-snapshot.schema.json",
}
SCHEMA_SET = ProtocolSchemaSet(SCHEMA_DIR, SCHEMAS)
FAMILY_FOR_MODE = {
    "rust_read_dao": "DAO-READ-",
    "dao_open_rust": "DAO-WRITE-",
    "dao_verify_rust_update": "DAO-UPDATE-",
}
ENABLED_MODES = frozenset({"rust_read_dao"})
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
INTEGER_RANGES = {"dbByte": (0, 255), "dbInteger": (-32768, 32767), "dbLong": (-2147483648, 2147483647)}
IEEE_WIDTH = {"dbSingle": 8, "dbDouble": 16}
VALUE_PATTERNS = {
    "invariant_decimal": r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",
    "invariant_datetime": r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?",
    "lowercase_hex": r"(?:[0-9a-f]{2})+",
    "guid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
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


def _validate_value(value: dict[str, Any], field: dict[str, Any], location: str) -> None:
    dao_type = field["dao_type"]
    encoding = value["encoding"]
    payload = value["value"]
    if encoding == "null":
        if field["required"]:
            raise ValidationError(f"{location}: null value for required field {field['name']!r}")
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
    size = field["size"]
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
            fields = {field["name"]: field for field in step["fields"]}
            if len(fields) != len(step["fields"]):
                raise ValidationError(f"{where}.fields: names must be unique")
            for field_index, field in enumerate(step["fields"]):
                sized = field["dao_type"] in ("dbText", "dbBinary")
                if sized != (field["size"] is not None):
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
                    field = fields.get(value["field"])
                    if field is None or value["field"] in seen:
                        raise ValidationError(f"{where}.rows[{row_index}][{value_index}]: unknown or repeated field")
                    seen.add(value["field"])
                    _validate_value(value, field, f"{where}.rows[{row_index}][{value_index}]")
                for name, field in fields.items():
                    if field["required"] and name not in seen:
                        raise ValidationError(f"{where}.rows[{row_index}]: required field {name!r} is missing")
        elif action == "drop_table":
            if step["name"] not in tables:
                raise ValidationError(f"{where}.name: unknown table {step['name']!r}")
            del tables[step["name"]]


def validate_inventory(document: dict[str, Any], *, capability_ids: frozenset[str], branch_ids: frozenset[str]) -> None:
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
        if mode == "rust_read_dao" and scenario["preserve_paths"]:
            raise ValidationError(f"{location}.preserve_paths: read scenarios preserve nothing")
        recipe = scenario["generator_recipe"]
        negative = recipe["database_version"] != "dbVersion30" or recipe["encrypted"] or recipe["password"] is not None
        if negative != (operation["expected_outcome"] == "expected_error"):
            raise ValidationError(f"{location}: unsupported opening states must be expected_error scenarios and vice versa")
        _validate_recipe(recipe, f"{location}.generator_recipe")


def validate_semantic_snapshot(document: dict[str, Any]) -> None:
    """Validate 1.2 row identity on top of the shared snapshot rules."""
    validate_snapshot(document)
    for table_index, table in enumerate(document["tables"]):
        location = f"$.tables[{table_index}].rows"
        previous: tuple[str, int] | None = None
        for row_index, row in enumerate(table["rows"]):
            digest = hashlib.sha256(canonical_json_bytes(row["values"])).hexdigest()
            if row["canonical_key"] != digest:
                raise ValidationError(f"{location}[{row_index}].canonical_key: expected {digest}")
            current = (row["canonical_key"], row["duplicate_ordinal"])
            if previous is not None and current <= previous:
                raise ValidationError(f"{location}[{row_index}]: rows must ascend by canonical_key then duplicate_ordinal")
            expected_ordinal = previous[1] + 1 if previous is not None and previous[0] == current[0] else 0
            if current[1] != expected_ordinal:
                raise ValidationError(f"{location}[{row_index}].duplicate_ordinal: expected {expected_ordinal}")
            previous = current
    for key in document["producer_extensions"]:
        if re.fullmatch(r"(?:/(?:[^/~]|~0|~1)*)+", key) is None:
            raise ValidationError(f"$.producer_extensions[{key!r}]: keys must be JSON pointers")


def validate_document(document: Any) -> str:
    document_type = SCHEMA_SET.validate(document)
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValidationError("$.protocol_version: unsupported protocol version")
    if document_type == "dao_scenario_inventory":
        validate_inventory(document, capability_ids=load_capability_ids(), branch_ids=load_branch_ids())
    elif document_type == "dao_branch_registry":
        validate_registry(document)
    else:
        validate_semantic_snapshot(document)
    return document_type


def validate_document_path(path: Path) -> str:
    document = load_json(path)
    document_type = validate_document(document)
    if document_type == "canonical_semantic_snapshot" and path.read_bytes() != canonical_json_bytes(document):
        raise ValidationError(f"{path}: canonical snapshot bytes are not normalized")
    return document_type


def validate_schemas() -> None:
    SCHEMA_SET.lint()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate DAO protocol 1.2 documents.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schemas", help="lint all protocol 1.2 schema files")
    for name, help_text in (("inventory", "validate the scenario inventory"), ("document", "validate one snapshot or registry document")):
        subparsers.add_parser(name, help=help_text).add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "schemas":
            validate_schemas()
            print(f"PASS: {len(SCHEMAS)} protocol 1.2 schemas")
        else:
            document_type = validate_document_path(args.path)
            if args.command == "inventory" and document_type != "dao_scenario_inventory":
                raise ValidationError(f"{args.path}: not a scenario inventory")
            count = len(load_json(args.path)["scenarios"]) if document_type == "dao_scenario_inventory" else None
            suffix = f", {count} scenarios" if count is not None else ""
            print(f"PASS: {args.path} ({document_type}{suffix})")
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
