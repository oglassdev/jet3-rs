"""Version-neutral validation primitives for portable DAO protocols.

The schema evaluator intentionally implements a small, explicit subset of
JSON Schema. Schema linting fails closed if a protocol starts using a keyword
that this module does not enforce.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
JSON_TYPES = frozenset(
    ("null", "boolean", "integer", "number", "string", "array", "object")
)
SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    (
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "prefixItems",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    )
)


class ValidationError(Exception):
    """A protocol, schema, or bundle validation failure."""


def load_json_with_bytes(path: Path) -> tuple[Any, bytes]:
    """Load strict UTF-8 JSON and retain the exact bytes from the same read."""
    try:
        retained = path.read_bytes()
        if retained.startswith(b"\xef\xbb\xbf"):
            raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")
        text = retained.decode("utf-8")

        def reject_nonfinite(value: str) -> None:
            raise ValueError(f"non-finite JSON number {value}")

        return json.loads(text, parse_constant=reject_nonfinite), retained
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: cannot read JSON: {exc}") from exc
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid JSON value: {exc}") from exc


def load_json(path: Path) -> Any:
    """Load strict UTF-8 JSON without BOMs or non-finite numbers."""
    return load_json_with_bytes(path)[0]


def sha256(path: Path) -> str:
    """Return the SHA-256 of a regular protocol payload."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot hash file: {exc}") from exc
    return digest.hexdigest()


def canonical_json_bytes(document: dict[str, Any]) -> bytes:
    """Encode a protocol document in its canonical snapshot representation."""
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


def resolve_local_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve a local JSON Pointer reference within one schema."""
    if not ref.startswith("#/"):
        raise ValidationError(f"schema uses unsupported external reference {ref!r}")
    current: Any = root_schema
    for escaped_part in ref[2:].split("/"):
        part = escaped_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValidationError(f"schema contains unresolved reference {ref!r}")
        current = current[part]
    if not isinstance(current, dict):
        raise ValidationError(f"schema reference {ref!r} does not select an object")
    return current


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise ValidationError(f"schema uses unsupported JSON type {expected!r}")


def validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    location: str,
) -> None:
    """Validate a value once against every supported schema constraint."""
    if "$ref" in schema:
        validate_schema_value(
            value, resolve_local_ref(root_schema, schema["$ref"]), root_schema, location
        )
        return

    if "anyOf" in schema:
        failures = []
        for alternative in schema["anyOf"]:
            try:
                validate_schema_value(value, alternative, root_schema, location)
                break
            except ValidationError as exc:
                failures.append(str(exc))
        else:
            raise ValidationError(
                f"{location}: does not satisfy any allowed shape "
                f"({'; '.join(failures)})"
            )

    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{location}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{location}: value {value!r} is not allowed")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_json_type_matches(value, item) for item in expected_types):
            raise ValidationError(
                f"{location}: expected type {' or '.join(expected_types)}"
            )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValidationError(f"{location}: string is too short")
        if len(value) > schema.get("maxLength", math.inf):
            raise ValidationError(f"{location}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValidationError(f"{location}: does not match required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed_time = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed_time.tzinfo is None:
                    raise ValueError("timezone is missing")
            except ValueError as exc:
                raise ValidationError(
                    f"{location}: invalid timezone-aware date-time"
                ) from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -math.inf):
            raise ValidationError(f"{location}: number is below minimum")
        if value > schema.get("maximum", math.inf):
            raise ValidationError(f"{location}: number is above maximum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{location}: array has too few items")
        if len(value) > schema.get("maxItems", math.inf):
            raise ValidationError(f"{location}: array has too many items")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value
            ]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{location}: array items must be unique")
        prefix_items = schema.get("prefixItems", [])
        for index, item in enumerate(value[: len(prefix_items)]):
            validate_schema_value(
                item, prefix_items[index], root_schema, f"{location}[{index}]"
            )
        if "items" in schema:
            for index in range(len(prefix_items), len(value)):
                if schema["items"] is False:
                    raise ValidationError(
                        f"{location}[{index}]: items beyond prefixItems are forbidden"
                    )
                validate_schema_value(
                    value[index], schema["items"], root_schema, f"{location}[{index}]"
                )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ValidationError(f"{location}: missing required key {required!r}")
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in properties:
                validate_schema_value(
                    child, properties[key], root_schema, f"{location}.{key}"
                )
            elif additional is False:
                raise ValidationError(f"{location}: unknown key {key!r}")
            elif isinstance(additional, dict):
                validate_schema_value(
                    child, additional, root_schema, f"{location}.{key}"
                )


def _require_schema_map(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{location}: schema must be an object")
    return value


def lint_schema(schema: dict[str, Any]) -> None:
    """Check that a schema uses only the evaluator's complete keyword subset."""

    def walk(value: Any, location: str) -> None:
        node = _require_schema_map(value, location)
        unknown = set(node) - SUPPORTED_SCHEMA_KEYWORDS
        if unknown:
            raise ValidationError(
                f"{location}: unsupported schema keywords {sorted(unknown)}"
            )
        if "$ref" in node:
            if len(node) != 1:
                raise ValidationError(f"{location}: $ref siblings are unsupported")
            resolve_local_ref(schema, node["$ref"])
            return
        expected_type = node.get("type")
        if expected_type is not None:
            types = expected_type if isinstance(expected_type, list) else [expected_type]
            if not types or any(item not in JSON_TYPES for item in types):
                raise ValidationError(f"{location}.type: unsupported JSON type")
            if len(types) != len(set(types)):
                raise ValidationError(f"{location}.type: types must be unique")
        if "format" in node and node["format"] != "date-time":
            raise ValidationError(f"{location}.format: unsupported format")
        if "pattern" in node:
            try:
                re.compile(node["pattern"])
            except (TypeError, re.error) as exc:
                raise ValidationError(f"{location}.pattern: invalid regex") from exc
        for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
            if keyword in node and (
                not isinstance(node[keyword], int)
                or isinstance(node[keyword], bool)
                or node[keyword] < 0
            ):
                raise ValidationError(f"{location}.{keyword}: expected non-negative integer")
        for keyword in ("minimum", "maximum"):
            if keyword in node and (
                not isinstance(node[keyword], (int, float))
                or isinstance(node[keyword], bool)
                or not math.isfinite(node[keyword])
            ):
                raise ValidationError(f"{location}.{keyword}: expected finite number")
        if node.get("minLength", 0) > node.get("maxLength", math.inf):
            raise ValidationError(f"{location}: minLength exceeds maxLength")
        if node.get("minItems", 0) > node.get("maxItems", math.inf):
            raise ValidationError(f"{location}: minItems exceeds maxItems")
        if node.get("minimum", -math.inf) > node.get("maximum", math.inf):
            raise ValidationError(f"{location}: minimum exceeds maximum")
        if "required" in node:
            required = node["required"]
            if (
                not isinstance(required, list)
                or any(not isinstance(item, str) for item in required)
                or len(required) != len(set(required))
            ):
                raise ValidationError(f"{location}.required: expected unique strings")
        if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
            raise ValidationError(f"{location}.uniqueItems: expected boolean")
        if "enum" in node and (
            not isinstance(node["enum"], list) or not node["enum"]
        ):
            raise ValidationError(f"{location}.enum: expected non-empty array")
        for map_name in ("$defs", "properties"):
            if map_name in node:
                children = _require_schema_map(node[map_name], f"{location}.{map_name}")
                for name, child in children.items():
                    walk(child, f"{location}.{map_name}.{name}")
        if "prefixItems" in node:
            prefix_items = node["prefixItems"]
            if not isinstance(prefix_items, list) or not prefix_items:
                raise ValidationError(f"{location}.prefixItems: expected non-empty array")
            for index, child in enumerate(prefix_items):
                walk(child, f"{location}.prefixItems[{index}]")
        if "items" in node:
            if node["items"] is False:
                if "prefixItems" not in node:
                    raise ValidationError(
                        f"{location}.items: false requires prefixItems"
                    )
            elif node["items"] is True:
                raise ValidationError(f"{location}.items: true is unsupported")
            else:
                walk(node["items"], f"{location}.items")
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"], f"{location}.additionalProperties")
        elif "additionalProperties" in node and not isinstance(
            node["additionalProperties"], bool
        ):
            raise ValidationError(
                f"{location}.additionalProperties: expected boolean or schema"
            )
        if "anyOf" in node:
            alternatives = node["anyOf"]
            if not isinstance(alternatives, list) or not alternatives:
                raise ValidationError(f"{location}.anyOf: expected non-empty array")
            for index, alternative in enumerate(alternatives):
                walk(alternative, f"{location}.anyOf[{index}]")

    walk(schema, "$")


class ProtocolSchemaSet:
    """A closed mapping from document types to one protocol version's schemas."""

    def __init__(self, schema_dir: Path, schemas: dict[str, str]):
        self._schema_dir = schema_dir
        self._schemas = dict(schemas)

    def validate(self, document: Any) -> str:
        if not isinstance(document, dict):
            raise ValidationError("$: protocol document must be an object")
        document_type = document.get("document_type")
        name = self._schemas.get(document_type)
        if name is None:
            raise ValidationError(f"unknown document_type {document_type!r}")
        schema = load_json(self._schema_dir / name)
        if not isinstance(schema, dict):
            raise ValidationError(f"{name}: schema root must be an object")
        validate_schema_value(document, schema, schema, "$")
        return document_type

    def lint(self) -> None:
        for document_type, name in self._schemas.items():
            path = self._schema_dir / name
            schema = load_json(path)
            if not isinstance(schema, dict):
                raise ValidationError(f"{path}: schema root must be an object")
            if schema.get("$schema") != JSON_SCHEMA_DRAFT:
                raise ValidationError(f"{path}: unexpected JSON Schema draft")
            if not isinstance(schema.get("$id"), str):
                raise ValidationError(f"{path}: missing $id")
            properties = schema.get("properties", {})
            if properties.get("document_type", {}).get("const") != document_type:
                raise ValidationError(
                    f"{path}: document_type constant is inconsistent"
                )
            lint_schema(schema)


def validate_environment(document: dict[str, Any]) -> None:
    """Validate provider/host relationships shared by v1 and v1.1."""
    accepted = document["accepted_provider"]
    if document["status"] == "ready":
        if accepted is None:
            raise ValidationError(
                "$.accepted_provider: ready environment requires an accepted provider"
            )
        if not document["host"]["is_windows"]:
            raise ValidationError("$.host.is_windows: ready environment requires Windows")
        if document["host"]["process_architecture"] != accepted["registry_view"]:
            raise ValidationError(
                "$.accepted_provider.registry_view: does not match process architecture"
            )
        identity_fields = (
            "prog_id",
            "clsid",
            "registry_view",
            "registration_scope",
            "provider_version",
            "server_path",
            "server_file_version",
            "server_sha256",
        )
        if not any(
            candidate["registered"]
            and candidate["activation"] == "succeeded"
            and candidate["dbversion30_test"]["status"] == "pass"
            and all(candidate[field] == accepted[field] for field in identity_fields)
            for candidate in document["provider_candidates"]
        ):
            raise ValidationError(
                "$.accepted_provider: no matching candidate passed dbVersion30"
            )
    elif accepted is not None:
        raise ValidationError(
            "$.accepted_provider: only a ready environment may accept a provider"
        )


def _validate_typed_value(value: dict[str, Any], location: str) -> None:
    kind = value["kind"]
    semantic = value["value"]
    integer = isinstance(semantic, int) and not isinstance(semantic, bool)
    if kind == "null" and semantic is not None:
        raise ValidationError(f"{location}.value: null kind requires JSON null")
    if kind == "boolean" and not isinstance(semantic, bool):
        raise ValidationError(f"{location}.value: boolean kind requires boolean")
    integer_ranges = {
        "byte": (0, 255),
        "integer": (-32768, 32767),
        "long": (-2147483648, 2147483647),
    }
    if kind in integer_ranges:
        lower, upper = integer_ranges[kind]
        if not integer or semantic < lower or semantic > upper:
            raise ValidationError(
                f"{location}.value: {kind} is outside its canonical integer range"
            )
    if kind in ("single", "double") and (
        not isinstance(semantic, float) or not math.isfinite(semantic)
    ):
        raise ValidationError(
            f"{location}.value: {kind} requires a finite JSON floating-point number"
        )
    if kind in ("decimal", "currency") and (
        not isinstance(semantic, str)
        or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", semantic) is None
    ):
        raise ValidationError(
            f"{location}.value: {kind} requires an invariant decimal string"
        )
    if kind == "datetime" and (
        not isinstance(semantic, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?",
            semantic,
        )
        is None
    ):
        raise ValidationError(
            f"{location}.value: datetime requires a timezone-free ISO string"
        )
    if kind in ("text", "memo") and not isinstance(semantic, str):
        raise ValidationError(f"{location}.value: {kind} requires a string")
    if kind in ("binary", "ole") and (
        not isinstance(semantic, str)
        or re.fullmatch(r"(?:[0-9a-f]{2})*", semantic) is None
    ):
        raise ValidationError(
            f"{location}.value: {kind} requires lowercase even-length hex"
        )
    if kind == "guid" and (
        not isinstance(semantic, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            semantic,
        )
        is None
    ):
        raise ValidationError(
            f"{location}.value: guid requires lowercase hyphenated text"
        )
    if "code_page" in value and kind not in ("text", "memo"):
        raise ValidationError(
            f"{location}.code_page: only text or memo may declare a code page"
        )


def validate_snapshot(document: dict[str, Any]) -> None:
    """Validate canonical snapshot ordering and typed values."""
    tables = document["tables"]
    table_names = [table["name"] for table in tables]
    if table_names != sorted(table_names) or len(table_names) != len(set(table_names)):
        raise ValidationError("$.tables: names must be unique and sorted")
    for table_index, table in enumerate(tables):
        location = f"$.tables[{table_index}]"
        columns = table["columns"]
        ordinals = [column["ordinal"] for column in columns]
        column_names = [column["name"] for column in columns]
        if ordinals != list(range(len(columns))):
            raise ValidationError(
                f"{location}.columns: ordinals must be contiguous from zero"
            )
        if len(column_names) != len(set(column_names)):
            raise ValidationError(f"{location}.columns: names must be unique")
        index_names = [index["name"] for index in table["indexes"]]
        if index_names != sorted(index_names) or len(index_names) != len(
            set(index_names)
        ):
            raise ValidationError(
                f"{location}.indexes: names must be unique and sorted"
            )
        row_keys = [
            (
                row["canonical_key"],
                json.dumps(
                    row["values"],
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
            for row in table["rows"]
        ]
        if row_keys != sorted(row_keys):
            raise ValidationError(
                f"{location}.rows: canonical keys and values must be sorted"
            )
    relationship_names = [
        relationship["name"] for relationship in document["relationships"]
    ]
    if relationship_names != sorted(relationship_names) or len(
        relationship_names
    ) != len(set(relationship_names)):
        raise ValidationError("$.relationships: names must be unique and sorted")

    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            if "kind" in value and "value" in value:
                _validate_typed_value(value, location)
            for key, child in value.items():
                walk(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")

    walk(document, "$")


def validate_operation_log(document: dict[str, Any]) -> None:
    """Validate operation sequence and final status relationships."""
    sequences = [entry["sequence"] for entry in document["entries"]]
    if sequences != list(range(1, len(sequences) + 1)):
        raise ValidationError("$.entries: sequence values must be contiguous from one")
    if document["entries"][-1]["status"] != document["final_status"]:
        raise ValidationError(
            "$.final_status: does not match the final operation entry"
        )
    if document["final_status"] == "pass" and any(
        entry["status"] != "pass" for entry in document["entries"]
    ):
        raise ValidationError(
            "$.entries: a passing operation log cannot contain an earlier failure"
        )
