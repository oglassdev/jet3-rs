#!/usr/bin/env python3
"""Validate DAO protocol documents and immutable evidence bundles.

This intentionally uses only the Python standard library so protocol checking
can run on development hosts that cannot execute the Windows DAO oracle.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCHEMA_DIR = ROOT / "protocol" / "v1"
SCHEMAS = {
    "dao_scenario": "scenario.schema.json",
    "canonical_snapshot": "canonical-snapshot.schema.json",
    "dao_environment": "environment.schema.json",
    "dao_operation_log": "operation-log.schema.json",
    "dao_evidence_report": "evidence-report.schema.json",
    "dao_bundle_manifest": "bundle-manifest.schema.json",
}
REFERENCE_ROLES = {
    "input": "scenario_input",
    "source_database": "source_database",
    "output_database": "output_database",
    "dao_snapshot": "dao_snapshot",
    "rust_snapshot": "rust_snapshot",
    "operation_log": "operation_log",
}
M0_SCENARIO_ID = "DAO-GEN-PROBE-001"


class ValidationError(Exception):
    """A protocol or bundle validation failure."""


def _load_json(path: Path) -> Any:
    try:
        retained = path.read_bytes()
        if retained.startswith(b"\xef\xbb\xbf"):
            raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")
        text = retained.decode("utf-8")

        def reject_nonfinite(value: str) -> None:
            raise ValueError(f"non-finite JSON number {value}")

        return json.loads(text, parse_constant=reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: cannot read JSON: {exc}") from exc
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid JSON value: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot hash file: {exc}") from exc
    return digest.hexdigest()


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


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
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


def _validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    location: str,
) -> None:
    if "$ref" in schema:
        target = _resolve_ref(root_schema, schema["$ref"])
        _validate_schema_value(value, target, root_schema, location)
        return

    if "anyOf" in schema:
        failures = []
        for alternative in schema["anyOf"]:
            try:
                _validate_schema_value(value, alternative, root_schema, location)
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
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValidationError(f"{location}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValidationError(f"{location}: does not match required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = value.replace("Z", "+00:00")
                parsed_time = dt.datetime.fromisoformat(parsed)
                if parsed_time.tzinfo is None:
                    raise ValueError("timezone is missing")
            except ValueError as exc:
                raise ValidationError(
                    f"{location}: invalid timezone-aware date-time"
                ) from exc

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        raise ValidationError(f"{location}: number is below minimum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{location}: array has too few items")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value
            ]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{location}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_schema_value(
                    item, item_schema, root_schema, f"{location}[{index}]"
                )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ValidationError(f"{location}: missing required key {required!r}")
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in properties:
                _validate_schema_value(
                    child, properties[key], root_schema, child_location
                )
            elif additional is False:
                raise ValidationError(f"{location}: unknown key {key!r}")
            elif isinstance(additional, dict):
                _validate_schema_value(child, additional, root_schema, child_location)


def _schema_for(document_type: str) -> dict[str, Any]:
    name = SCHEMAS.get(document_type)
    if name is None:
        raise ValidationError(f"unknown document_type {document_type!r}")
    schema = _load_json(SCHEMA_DIR / name)
    if not isinstance(schema, dict):
        raise ValidationError(f"{name}: schema root must be an object")
    return schema


def _validate_scenario(document: dict[str, Any]) -> None:
    family_for_mode = {
        "dao_generate_fixture": "DAO-GEN-",
        "rust_read_dao": "DAO-READ-",
        "dao_open_rust": "DAO-WRITE-",
        "dao_verify_rust_update": "DAO-UPDATE-",
    }
    prefix = family_for_mode[document["mode"]]
    if not document["scenario_id"].startswith(prefix):
        raise ValidationError(
            "$.scenario_id: family does not agree with $.mode"
        )
    database = document["database"]
    if database["input_role"] == "none":
        if "input_path" in database or "input_sha256" in database:
            raise ValidationError(
                "$.database: input path/hash are forbidden when input_role is none"
            )
    elif "input_path" not in database or "input_sha256" not in database:
        raise ValidationError(
            "$.database: non-empty input role requires input_path and input_sha256"
        )
    expected = document["expected"]
    if expected["outcome"] == "expected_error" and "error_class" not in expected:
        raise ValidationError(
            "$.expected: expected_error requires a stable error_class"
        )
    if document["mode"] == "dao_verify_rust_update" and not expected.get(
        "preserve_paths"
    ):
        raise ValidationError(
            "$.expected.preserve_paths: update verification requires preservation paths"
        )
    step_ids = [step["step_id"] for step in document["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise ValidationError("$.steps: step_id values must be unique")


def _validate_environment(document: dict[str, Any]) -> None:
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
        matching = []
        for candidate in document["provider_candidates"]:
            if (
                candidate["registered"]
                and candidate["activation"] == "succeeded"
                and candidate["dbversion30_test"]["status"] == "pass"
                and all(
                    candidate[field] == accepted[field]
                    for field in identity_fields
                )
            ):
                matching.append(candidate)
        if not matching:
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
    if kind in ("single", "double"):
        if (
            not isinstance(semantic, (int, float))
            or isinstance(semantic, bool)
            or not math.isfinite(semantic)
        ):
            raise ValidationError(
                f"{location}.value: {kind} requires a finite JSON number"
            )
    if kind in ("decimal", "currency"):
        if not isinstance(semantic, str) or re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", semantic
        ) is None:
            raise ValidationError(
                f"{location}.value: {kind} requires an invariant decimal string"
            )
    if kind == "datetime":
        if not isinstance(semantic, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?",
            semantic,
        ) is None:
            raise ValidationError(
                f"{location}.value: datetime requires a timezone-free ISO string"
            )
    if kind in ("text", "memo") and not isinstance(semantic, str):
        raise ValidationError(f"{location}.value: {kind} requires a string")
    if kind in ("binary", "ole"):
        if not isinstance(semantic, str) or re.fullmatch(
            r"(?:[0-9a-f]{2})*", semantic
        ) is None:
            raise ValidationError(
                f"{location}.value: {kind} requires lowercase even-length hex"
            )
    if kind == "guid":
        if not isinstance(semantic, str) or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            semantic,
        ) is None:
            raise ValidationError(
                f"{location}.value: guid requires lowercase hyphenated text"
            )
    if "code_page" in value and kind not in ("text", "memo"):
        raise ValidationError(
            f"{location}.code_page: only text or memo may declare a code page"
        )


def _validate_snapshot(document: dict[str, Any]) -> None:
    tables = document["tables"]
    table_names = [table["name"] for table in tables]
    if table_names != sorted(table_names) or len(table_names) != len(set(table_names)):
        raise ValidationError("$.tables: names must be unique and sorted")
    for table_index, table in enumerate(tables):
        location = f"$.tables[{table_index}]"
        columns = table["columns"]
        ordinals = [column["ordinal"] for column in columns]
        column_names = [column["name"] for column in columns]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValidationError(
                f"{location}.columns: ordinals must be unique and sorted"
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
        row_keys = [row["canonical_key"] for row in table["rows"]]
        if row_keys != sorted(row_keys) or len(row_keys) != len(set(row_keys)):
            raise ValidationError(
                f"{location}.rows: canonical keys must be unique and sorted"
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


def _validate_report(document: dict[str, Any]) -> None:
    scenario_ids = [item["scenario_id"] for item in document["scenarios"]]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValidationError("$.scenarios: scenario IDs must be unique")
    observed = Counter(item["status"] for item in document["scenarios"])
    counts = document["counts"]
    if counts["selected"] != len(document["scenarios"]):
        raise ValidationError("$.counts.selected: does not match scenario list")
    for status in ("pass", "fail", "blocked", "error", "skipped"):
        if counts[status] != observed[status]:
            raise ValidationError(f"$.counts.{status}: does not match scenario list")
    if document["status"] == "pass":
        if document["git"]["dirty"]:
            raise ValidationError("$.git.dirty: a passing evidence run must be clean")
        if any(counts[item] for item in ("fail", "blocked", "error", "skipped")):
            raise ValidationError("$.status: pass cannot contain non-passing scenarios")
        if counts["selected"] == 0:
            raise ValidationError("$.status: pass requires at least one scenario")


def _validate_operation_log(document: dict[str, Any]) -> None:
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


def validate_document(document: Any) -> str:
    if not isinstance(document, dict):
        raise ValidationError("$: protocol document must be an object")
    document_type = document.get("document_type")
    schema = _schema_for(document_type)
    _validate_schema_value(document, schema, schema, "$")
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValidationError("$.protocol_version: unsupported protocol version")
    if document_type == "dao_scenario":
        _validate_scenario(document)
    elif document_type == "canonical_snapshot":
        _validate_snapshot(document)
    elif document_type == "dao_environment":
        _validate_environment(document)
    elif document_type == "dao_operation_log":
        _validate_operation_log(document)
    elif document_type == "dao_evidence_report":
        _validate_report(document)
    return document_type


def validate_document_path(path: Path) -> str:
    document = _load_json(path)
    document_type = validate_document(document)
    if document_type == "canonical_snapshot":
        canonical = (
            json.dumps(
                document,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        try:
            retained = path.read_bytes()
        except OSError as exc:
            raise ValidationError(f"{path}: cannot read snapshot bytes: {exc}") from exc
        if retained != canonical:
            raise ValidationError(
                f"{path}: canonical snapshot bytes are not normalized"
            )
    return document_type


def validate_schemas() -> None:
    expected_draft = "https://json-schema.org/draft/2020-12/schema"
    for document_type, name in SCHEMAS.items():
        path = SCHEMA_DIR / name
        schema = _load_json(path)
        if not isinstance(schema, dict):
            raise ValidationError(f"{path}: schema root must be an object")
        if schema.get("$schema") != expected_draft:
            raise ValidationError(f"{path}: unexpected JSON Schema draft")
        if not isinstance(schema.get("$id"), str):
            raise ValidationError(f"{path}: missing $id")
        properties = schema.get("properties", {})
        if properties.get("document_type", {}).get("const") != document_type:
            raise ValidationError(f"{path}: document_type constant is inconsistent")

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if "$ref" in value:
                    _resolve_ref(schema, value["$ref"])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(schema)


def _safe_bundle_path(bundle: Path, relative: str) -> Path:
    candidate = bundle / relative
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(bundle.resolve())
    except (OSError, ValueError) as exc:
        raise ValidationError(f"unsafe bundle path {relative!r}") from exc
    return candidate


def _validate_manifest_payloads(
    bundle: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = bundle / "bundle-manifest.json"
    manifest = _load_json(manifest_path)
    if validate_document(manifest) != "dao_bundle_manifest":
        raise ValidationError(f"{manifest_path}: wrong document type")

    if bundle.name != manifest["run_id"]:
        raise ValidationError("bundle directory name does not match run_id")
    if bundle.parent.name != manifest["git_commit"]:
        raise ValidationError("bundle parent directory does not match git_commit")

    file_entries = manifest["files"]
    paths = [entry["path"] for entry in file_entries]
    if len(paths) != len(set(paths)):
        raise ValidationError("$.files: paths must be unique")
    entry_by_path = {entry["path"]: entry for entry in file_entries}
    actual_payloads = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_payloads != set(paths):
        missing = sorted(set(paths) - actual_payloads)
        unlisted = sorted(actual_payloads - set(paths))
        raise ValidationError(
            f"manifest/file mismatch; missing={missing}, unlisted={unlisted}"
        )
    for entry in file_entries:
        path = _safe_bundle_path(bundle, entry["path"])
        if not path.is_file():
            raise ValidationError(f"{entry['path']}: not a regular file")
        if path.stat().st_size != entry["size_bytes"]:
            raise ValidationError(f"{entry['path']}: size does not match manifest")
        if _sha256(path) != entry["sha256"]:
            raise ValidationError(f"{entry['path']}: SHA-256 does not match manifest")
    return manifest, entry_by_path


def _load_bound_report(
    bundle: Path,
    manifest: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    report_entry = entry_by_path.get(manifest["report_path"])
    if report_entry is None or report_entry["role"] != "report":
        raise ValidationError("$.report_path: missing report-role manifest entry")
    report_path = _safe_bundle_path(bundle, manifest["report_path"])
    report = _load_json(report_path)
    if validate_document(report) != "dao_evidence_report":
        raise ValidationError(f"{report_path}: wrong document type")
    if report["run_id"] != manifest["run_id"]:
        raise ValidationError("report and manifest run IDs differ")
    if report["git"]["commit"] != manifest["git_commit"]:
        raise ValidationError("report and manifest commits differ")
    if report["git"]["dirty"] != manifest["dirty"]:
        raise ValidationError("report and manifest dirty flags differ")
    if report["oracle_revision"] != manifest["git_commit"]:
        raise ValidationError("oracle revision is not the bundle git commit")
    if report["status"] != manifest["status"]:
        raise ValidationError("report and manifest statuses differ")
    report_ids = [item["scenario_id"] for item in report["scenarios"]]
    if set(report_ids) != set(manifest["scenario_ids"]):
        raise ValidationError("report and manifest scenario IDs differ")
    return report


def _validate_environment_binding(
    bundle: Path,
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> str:
    environment_ref = report["environment"]
    environment_entry = entry_by_path.get(environment_ref["path"])
    if environment_entry is None or environment_entry["role"] != "environment":
        raise ValidationError("report environment lacks environment-role manifest entry")
    if environment_entry["sha256"] != environment_ref["sha256"]:
        raise ValidationError("report and manifest environment hashes differ")
    environment_path = _safe_bundle_path(bundle, environment_ref["path"])
    environment = _load_json(environment_path)
    if validate_document(environment) != "dao_environment":
        raise ValidationError(f"{environment_path}: wrong document type")
    if report["status"] == "pass" and environment["status"] != "ready":
        raise ValidationError("passing report requires a ready DAO environment")
    return environment_ref["path"]


def _validate_report_references(
    bundle: Path,
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
) -> set[str]:
    referenced: list[tuple[str, dict[str, str]]] = []
    referenced_paths: set[str] = set()
    for scenario in report["scenarios"]:
        for key in REFERENCE_ROLES:
            reference = scenario[key]
            if reference is not None:
                referenced.append((key, reference))
                referenced_paths.add(reference["path"])
    for key, reference in referenced:
        entry = entry_by_path.get(reference["path"])
        if entry is None:
            raise ValidationError(
                f"{reference['path']}: report reference is absent from manifest"
            )
        if entry["role"] != REFERENCE_ROLES[key]:
            raise ValidationError(
                f"{reference['path']}: manifest role does not match report reference"
            )
        if entry["sha256"] != reference["sha256"]:
            raise ValidationError(
                f"{reference['path']}: report and manifest hashes differ"
            )
        if key in ("input", "dao_snapshot", "rust_snapshot", "operation_log"):
            validate_document_path(_safe_bundle_path(bundle, reference["path"]))
    return referenced_paths


def _validate_operation_binding(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
    scenario_input: dict[str, Any],
) -> None:
    reference = result["operation_log"]
    if reference is None:
        if result["status"] == "pass":
            raise ValidationError(
                f"{result['scenario_id']}: passing result lacks operation log"
            )
        return
    operation_log = _load_json(_safe_bundle_path(bundle, reference["path"]))
    bindings = (
        ("scenario_id", result["scenario_id"]),
        ("run_id", report["run_id"]),
        ("git_commit", report["git"]["commit"]),
        ("final_status", result["status"]),
    )
    for key, expected in bindings:
        if operation_log[key] != expected:
            raise ValidationError(
                f"{result['scenario_id']}: operation log {key} differs"
            )
    if result["status"] != "pass":
        return
    required_actions = [step["action"] for step in scenario_input["steps"]]
    expected_actions = [
        "activate_provider",
        *required_actions,
        "reopen_database",
        "snapshot",
        "finalize",
    ]
    actions = [entry["action"] for entry in operation_log["entries"]]
    if actions != expected_actions:
        raise ValidationError(
            f"{result['scenario_id']}: operation actions do not match scenario/lifecycle"
        )
    if any(entry["status"] != "pass" for entry in operation_log["entries"]):
        raise ValidationError(
            f"{result['scenario_id']}: passing log contains a failed operation"
        )


def _validate_m0_pass_artifacts(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
) -> None:
    expected_presence = {
        "source_database": False,
        "output_database": True,
        "dao_snapshot": True,
        "rust_snapshot": False,
        "operation_log": True,
    }
    for key, required in expected_presence.items():
        if (result[key] is not None) != required:
            raise ValidationError(
                f"{result['scenario_id']}: {key} violates M0 artifact contract"
            )
    database_hash = result["output_database"]["sha256"]
    snapshot = _load_json(
        _safe_bundle_path(bundle, result["dao_snapshot"]["path"])
    )
    if snapshot["scenario_id"] != result["scenario_id"]:
        raise ValidationError(f"{result['scenario_id']}: snapshot scenario differs")
    if snapshot["producer"]["kind"] != "dao":
        raise ValidationError(f"{result['scenario_id']}: snapshot producer differs")
    if snapshot["producer"]["source_revision"] != report["git"]["commit"]:
        raise ValidationError(f"{result['scenario_id']}: snapshot revision differs")
    if snapshot["database_sha256"] != database_hash:
        raise ValidationError(
            f"{result['scenario_id']}: snapshot/database hashes differ"
        )


def _validate_scenario_binding(
    bundle: Path,
    report: dict[str, Any],
    result: dict[str, Any],
) -> None:
    input_path = _safe_bundle_path(bundle, result["input"]["path"])
    scenario_input = _load_json(input_path)
    if validate_document(scenario_input) != "dao_scenario":
        raise ValidationError(f"{input_path}: wrong document type")
    if scenario_input["scenario_id"] != result["scenario_id"]:
        raise ValidationError(f"{result['scenario_id']}: result/input scenario IDs differ")
    if scenario_input["mode"] != result["mode"]:
        raise ValidationError(f"{result['scenario_id']}: result/input modes differ")
    if scenario_input["capabilities"] != result["capabilities"]:
        raise ValidationError(f"{result['scenario_id']}: result/input capabilities differ")
    if result["mode"] != "dao_generate_fixture":
        raise ValidationError(
            f"{result['scenario_id']}: differential mode is not implemented"
        )
    if result["scenario_id"] != M0_SCENARIO_ID:
        raise ValidationError(
            f"{result['scenario_id']}: only the checked M0 scenario is implemented"
        )
    _validate_operation_binding(bundle, report, result, scenario_input)
    if result["status"] == "pass":
        _validate_m0_pass_artifacts(bundle, report, result)


def _validate_exact_passing_payloads(
    report: dict[str, Any],
    entry_by_path: dict[str, dict[str, Any]],
    referenced_paths: set[str],
) -> None:
    if report["status"] == "pass" and set(entry_by_path) != referenced_paths:
        extras = sorted(set(entry_by_path) - referenced_paths)
        missing = sorted(referenced_paths - set(entry_by_path))
        raise ValidationError(
            f"passing bundle payload contract differs; extras={extras}, missing={missing}"
        )


def validate_bundle(bundle: Path) -> None:
    manifest, entry_by_path = _validate_manifest_payloads(bundle)
    report = _load_bound_report(bundle, manifest, entry_by_path)
    environment_path = _validate_environment_binding(bundle, report, entry_by_path)
    referenced_paths = _validate_report_references(bundle, report, entry_by_path)
    referenced_paths.update((manifest["report_path"], environment_path))
    for result in report["scenarios"]:
        _validate_scenario_binding(bundle, report, result)
    _validate_exact_passing_payloads(report, entry_by_path, referenced_paths)


if __name__ == "__main__":
    from protocol_cli import main
    raise SystemExit(
        main(
            schema_count=len(SCHEMAS),
            validate_schemas=validate_schemas,
            validate_document_path=validate_document_path,
            validate_bundle=validate_bundle,
            validation_error=ValidationError,
        )
    )
