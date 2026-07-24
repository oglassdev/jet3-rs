#!/usr/bin/env python3
"""Validate the portable DAO M1 (protocol 1.1) data contract.

This module executes no DAO or COM operation. It validates controlled plans,
canonical snapshots, exact semantic comparisons, and immutable bundle bindings.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCHEMA_DIR = ROOT / "protocol" / "v1_1"
EXAMPLES = ROOT / "examples"
PROTOCOL_VERSION = "1.1.0"
LADDER = [1, 2047, 2048, 2049, 32767, 32768, 32769]
SCHEMAS = {
    "dao_scenario": "scenario.schema.json",
    "dao_pair": "pair.schema.json",
    "canonical_snapshot": "canonical-snapshot.schema.json",
    "dao_environment": "environment.schema.json",
    "dao_operation_log": "operation-log.schema.json",
    "dao_evidence_report": "evidence-report.schema.json",
    "dao_bundle_manifest": "bundle-manifest.schema.json",
    "dao_example_inventory": "example-inventory.schema.json",
}
ROLES = {
    "environment": "environment",
    "input": None,
    "output_database": "output_database",
    "dao_snapshot": "dao_snapshot",
    "operation_log": "operation_log",
    "left_snapshot": "dao_snapshot",
    "right_snapshot": "dao_snapshot",
}

_SPEC = importlib.util.spec_from_file_location(
    "_dao_protocol_v1", HERE / "validate_protocol.py"
)
_V1 = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_V1)
ValidationError = _V1.ValidationError


def _load_json(path: Path) -> Any:
    return _V1._load_json(path)


def _sha256(path: Path) -> str:
    return _V1._sha256(path)


def _schema_for(document_type: str) -> dict[str, Any]:
    name = SCHEMAS.get(document_type)
    if name is None:
        raise ValidationError(f"unknown document_type {document_type!r}")
    schema = _load_json(SCHEMA_DIR / name)
    if not isinstance(schema, dict):
        raise ValidationError(f"{name}: schema root must be an object")
    return schema


def _walk_schema_constraints(value: Any, schema: dict[str, Any], root: dict[str, Any], location: str) -> None:
    """Enforce numeric maxima omitted by the small v1 schema evaluator."""
    if "$ref" in schema:
        _walk_schema_constraints(value, _V1._resolve_ref(root, schema["$ref"]), root, location)
        return
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                _V1._validate_schema_value(value, alternative, root, location)
            except ValidationError:
                continue
            _walk_schema_constraints(value, alternative, root, location)
            break
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            raise ValidationError(f"{location}: number is above maximum")
    if isinstance(value, list):
        maximum = schema.get("maxItems")
        if maximum is not None and len(value) > maximum:
            raise ValidationError(f"{location}: array has too many items")
        if "items" in schema:
            for index, item in enumerate(value):
                _walk_schema_constraints(item, schema["items"], root, f"{location}[{index}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                _walk_schema_constraints(child, properties[key], root, f"{location}.{key}")


def _require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the controlled recipe")


def _validate_recipe_shape(document: dict[str, Any]) -> None:
    steps = document["steps"]
    actions = [step["action"] for step in steps]
    recipe = document["recipe"]
    expected_actions = {
        "repeat_empty": ["create_database", "close_database"],
        "binary_marker": ["create_database", "create_table", "insert_row", "close_database"],
        "text_index_baseline": ["create_database", "create_table", "insert_row", "close_database"],
        "text_index_nonunique": ["create_database", "create_table", "insert_row", "close_database"],
        "memo_ladder": ["create_database", "create_table"] + ["insert_row"] * 7 + ["close_database"],
        "long_binary_ladder": ["create_database", "create_table"] + ["insert_row"] * 7 + ["close_database"],
    }[recipe]
    _require_equal(actions, expected_actions, "$.steps actions")
    _require_equal(
        steps[0]["arguments"],
        {"locale": ";LANGID=0x0409;CP=1252;COUNTRY=0", "version": "dbVersion30"},
        "$.steps[0].arguments",
    )

    if recipe == "repeat_empty":
        return
    table = steps[1]["arguments"]
    if recipe == "binary_marker":
        _require_equal(table, {
            "name": "BinaryMarker",
            "fields": [{"name": "marker", "dao_type": "dbBinary", "required": True}],
            "indexes": [],
        }, "$.steps[1].arguments")
        _require_equal(steps[2]["arguments"], {
            "table": "BinaryMarker",
            "values": [{"field": "marker", "dao_type": "dbBinary", "encoding": "lowercase_hex", "value": "0011223344556677"}],
        }, "$.steps[2].arguments")
        return
    if recipe.startswith("text_index_"):
        indexes: list[dict[str, Any]] = []
        if recipe == "text_index_nonunique":
            indexes = [{
                "name": "ix_marker",
                "fields": ["marker"],
                "primary": False,
                "unique": False,
                "required": False,
                "ignore_nulls": False,
            }]
        _require_equal(table, {
            "name": "TextMarker",
            "fields": [{"name": "marker", "dao_type": "dbText", "size": 8, "required": True}],
            "indexes": indexes,
        }, "$.steps[1].arguments")
        _require_equal(steps[2]["arguments"], {
            "table": "TextMarker",
            "values": [{"field": "marker", "dao_type": "dbText", "encoding": "unicode_string", "value": "JET3M1"}],
        }, "$.steps[2].arguments")
        return

    binary = recipe == "long_binary_ladder"
    dao_type = "dbLongBinary" if binary else "dbMemo"
    _require_equal(table, {
        "name": "LongValue",
        "fields": [{"name": "payload", "dao_type": dao_type, "required": True}],
        "indexes": [],
    }, "$.steps[1].arguments")
    observed_lengths = []
    for index, step in enumerate(steps[2:-1], start=2):
        value = step["arguments"]["values"][0]
        expected_value = {
            "field": "payload",
            "dao_type": dao_type,
            "encoding": "repeat_byte" if binary else "repeat_ascii",
            "length": value["length"],
            "byte" if binary else "ascii_character": 165 if binary else "M",
        }
        _require_equal(step["arguments"], {"table": "LongValue", "values": [expected_value]}, f"$.steps[{index}].arguments")
        observed_lengths.append(value["length"])
    _require_equal(observed_lengths, LADDER, "$.steps ladder lengths")


def _validate_scenario(document: dict[str, Any]) -> None:
    step_ids = [step["step_id"] for step in document["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise ValidationError("$.steps: step_id values must be unique")
    tables: dict[str, list[dict[str, Any]]] = {}
    for index, step in enumerate(document["steps"]):
        if step["action"] == "create_table":
            arguments = step["arguments"]
            name = arguments["name"]
            if name in tables:
                raise ValidationError(f"$.steps[{index}]: duplicate table {name!r}")
            fields = arguments["fields"]
            names = [field["name"] for field in fields]
            if len(names) != len(set(names)):
                raise ValidationError(f"$.steps[{index}].arguments.fields: duplicate names")
            for table_index in arguments["indexes"]:
                if any(name not in names for name in table_index["fields"]):
                    raise ValidationError(f"$.steps[{index}].arguments.indexes: unknown field")
            tables[name] = fields
        elif step["action"] == "insert_row":
            arguments = step["arguments"]
            fields = tables.get(arguments["table"])
            if fields is None:
                raise ValidationError(f"$.steps[{index}].arguments.table: table is not yet declared")
            values = arguments["values"]
            expected = [(field["name"], field["dao_type"]) for field in fields]
            observed = [(value["field"], value["dao_type"]) for value in values]
            if observed != expected:
                raise ValidationError(f"$.steps[{index}].arguments.values: order/type does not match fields")
            for field, value in zip(fields, values):
                if value["dao_type"] == "dbText" and len(value["value"]) > field["size"]:
                    raise ValidationError(f"$.steps[{index}].arguments.values: dbText exceeds field size")
    _validate_recipe_shape(document)


PAIR_PATHS = {
    "repeat_equivalence": ["/database_sha256", "/scenario_id"],
    "single_nonunique_index": ["/database_sha256", "/scenario_id", "/tables/0/indexes"],
}


def _validate_pair(document: dict[str, Any]) -> None:
    if document["left_scenario_id"] == document["right_scenario_id"]:
        raise ValidationError("$.right_scenario_id: pair sides must differ")
    _require_equal(
        document["allowed_difference_paths"],
        PAIR_PATHS[document["comparison_kind"]],
        "$.allowed_difference_paths",
    )


def _validate_snapshot_against_recipe(
    scenario: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    """Bind each passing DAO snapshot to the declared controlled semantics."""
    recipe = scenario["recipe"]
    tables = snapshot["tables"]
    if recipe == "repeat_empty":
        if tables:
            raise ValidationError(
                f"{scenario['scenario_id']}: empty recipe snapshot has tables"
            )
        return
    if len(tables) != 1:
        raise ValidationError(
            f"{scenario['scenario_id']}: controlled snapshot requires one table"
        )
    table_step = scenario["steps"][1]["arguments"]
    table = tables[0]
    if table["name"] != table_step["name"] or table["kind"] != "user":
        raise ValidationError(
            f"{scenario['scenario_id']}: snapshot table identity differs from recipe"
        )
    columns = table["columns"]
    fields = table_step["fields"]
    observed_columns = [(item["name"], item["dao_type"]) for item in columns]
    expected_columns = [(item["name"], item["dao_type"]) for item in fields]
    if observed_columns != expected_columns:
        raise ValidationError(
            f"{scenario['scenario_id']}: snapshot column order/type differs from recipe"
        )
    for field, column in zip(fields, columns):
        if field["dao_type"] == "dbText" and column["size"] != field["size"]:
            raise ValidationError(
                f"{scenario['scenario_id']}: snapshot dbText size differs from recipe"
            )
    declared_indexes = table_step["indexes"]
    indexes = table["indexes"]
    if len(indexes) != len(declared_indexes):
        raise ValidationError(
            f"{scenario['scenario_id']}: snapshot index count differs from recipe"
        )
    for declared, observed in zip(declared_indexes, indexes):
        expected_index = (
            declared["name"],
            declared["primary"],
            declared["unique"],
            declared["required"],
            declared["ignore_nulls"],
            declared["fields"],
        )
        observed_index = (
            observed["name"],
            observed["primary"],
            observed["unique"],
            observed["required"],
            observed["ignore_nulls"],
            [field["name"] for field in observed["fields"]],
        )
        if observed_index != expected_index:
            raise ValidationError(
                f"{scenario['scenario_id']}: snapshot index differs from recipe"
            )
    row_steps = [
        step["arguments"]
        for step in scenario["steps"]
        if step["action"] == "insert_row"
    ]
    if len(table["rows"]) != len(row_steps):
        raise ValidationError(
            f"{scenario['scenario_id']}: snapshot row count differs from recipe"
        )
    kind_for_type = {
        "dbBinary": "binary",
        "dbText": "text",
        "dbMemo": "memo",
        "dbLongBinary": "ole",
    }
    for row_number, (declared_row, observed_row) in enumerate(
        zip(row_steps, table["rows"])
    ):
        declared_values = declared_row["values"]
        if list(observed_row["values"]) != sorted(
            value["field"] for value in declared_values
        ):
            raise ValidationError(
                f"{scenario['scenario_id']}: snapshot row {row_number} fields differ"
            )
        for declared in declared_values:
            observed = observed_row["values"][declared["field"]]
            dao_type = declared["dao_type"]
            if dao_type in ("dbBinary", "dbText"):
                expected_value = declared["value"]
            elif dao_type == "dbMemo":
                expected_value = declared["ascii_character"] * declared["length"]
            else:
                expected_value = f"{declared['byte']:02x}" * declared["length"]
            if (observed["kind"], observed["value"]) != (
                kind_for_type[dao_type],
                expected_value,
            ):
                raise ValidationError(
                    f"{scenario['scenario_id']}: snapshot row {row_number} "
                    "type/value differs from recipe"
                )


def _validate_counts(document: dict[str, Any], key: str, results: list[dict[str, Any]]) -> None:
    counts = document[key]
    observed = Counter(result["status"] for result in results)
    if counts["selected"] != len(results):
        raise ValidationError(f"$.{key}.selected: does not match result list")
    for status in ("pass", "fail", "blocked", "error", "skipped"):
        if counts[status] != observed[status]:
            raise ValidationError(f"$.{key}.{status}: does not match result list")


def _validate_report(document: dict[str, Any]) -> None:
    scenario_ids = [result["scenario_id"] for result in document["scenarios"]]
    pair_ids = [result["pair_id"] for result in document["pairs"]]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValidationError("$.scenarios: scenario IDs must be unique")
    if len(pair_ids) != len(set(pair_ids)):
        raise ValidationError("$.pairs: pair IDs must be unique")
    _validate_counts(document, "scenario_counts", document["scenarios"])
    _validate_counts(document, "pair_counts", document["pairs"])
    known = set(scenario_ids)
    for index, pair in enumerate(document["pairs"]):
        if pair["left_scenario_id"] not in known or pair["right_scenario_id"] not in known:
            raise ValidationError(f"$.pairs[{index}]: pair side is absent from scenario results")
        if pair["observed_difference_paths"] != sorted(pair["observed_difference_paths"]):
            raise ValidationError(f"$.pairs[{index}].observed_difference_paths: must be sorted")
    if document["status"] == "pass":
        if document["git"]["dirty"]:
            raise ValidationError("$.git.dirty: a passing report must be clean")
        if not document["scenarios"]:
            raise ValidationError("$.status: pass requires scenarios")
        for key in ("scenario_counts", "pair_counts"):
            if any(document[key][status] for status in ("fail", "blocked", "error", "skipped")):
                raise ValidationError(f"$.status: pass contains non-passing {key}")


def validate_document(document: Any) -> str:
    if not isinstance(document, dict):
        raise ValidationError("$: protocol document must be an object")
    document_type = document.get("document_type")
    schema = _schema_for(document_type)
    _V1._validate_schema_value(document, schema, schema, "$")
    _walk_schema_constraints(document, schema, schema, "$")
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise ValidationError("$.protocol_version: unsupported protocol version")
    if document_type == "dao_scenario":
        _validate_scenario(document)
    elif document_type == "dao_pair":
        _validate_pair(document)
    elif document_type == "canonical_snapshot":
        _V1._validate_snapshot(document)
    elif document_type == "dao_environment":
        _V1._validate_environment(document)
    elif document_type == "dao_operation_log":
        _V1._validate_operation_log(document)
    elif document_type == "dao_evidence_report":
        _validate_report(document)
    return document_type


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def validate_example_inventory(path: Path, document: dict[str, Any]) -> None:
    entries = document["files"]
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValidationError("$.files: paths must be unique and sorted")
    actual = {
        candidate.name
        for candidate in path.parent.glob("*.json")
        if candidate.name != path.name
        and isinstance((loaded := _load_json(candidate)), dict)
        and loaded.get("protocol_version") == PROTOCOL_VERSION
        and loaded.get("document_type") in ("dao_scenario", "dao_pair")
    }
    if set(paths) != actual:
        raise ValidationError("$.files: inventory does not exactly cover M1 examples")
    for entry in entries:
        candidate = path.parent / entry["path"]
        loaded = _load_json(candidate)
        if validate_document(loaded) != entry["document_type"]:
            raise ValidationError(f"{entry['path']}: inventory document type differs")
        if _sha256(candidate) != entry["sha256"]:
            raise ValidationError(f"{entry['path']}: inventory SHA-256 differs")


def validate_document_path(path: Path) -> str:
    document = _load_json(path)
    document_type = validate_document(document)
    if document_type == "canonical_snapshot" and path.read_bytes() != _canonical_bytes(document):
        raise ValidationError(f"{path}: canonical snapshot bytes are not normalized")
    if document_type == "dao_example_inventory":
        validate_example_inventory(path, document)
    return document_type


def validate_schemas() -> None:
    for document_type, name in SCHEMAS.items():
        path = SCHEMA_DIR / name
        schema = _load_json(path)
        if not isinstance(schema, dict):
            raise ValidationError(f"{path}: schema root must be an object")
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValidationError(f"{path}: unexpected JSON Schema draft")
        if schema.get("properties", {}).get("document_type", {}).get("const") != document_type:
            raise ValidationError(f"{path}: document_type constant is inconsistent")
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if "$ref" in value:
                    _V1._resolve_ref(schema, value["$ref"])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(schema)


def _pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def compare_snapshots(left: dict[str, Any], right: dict[str, Any], allowed_paths: list[str]) -> list[str]:
    """Deep-compare canonical snapshots and require every exact allowance to differ."""
    allowed = set(allowed_paths)
    observed: set[str] = set()

    def walk(a: Any, b: Any, path: str) -> None:
        if path in allowed:
            if a == b:
                raise ValidationError(f"{path}: allowed difference was not observed")
            observed.add(path)
            return
        if type(a) is not type(b):
            raise ValidationError(f"{path or '/'}: unexpected semantic type difference")
        if isinstance(a, dict):
            if set(a) != set(b):
                raise ValidationError(f"{path or '/'}: unexpected semantic key difference")
            for key in sorted(a):
                walk(a[key], b[key], f"{path}/{_pointer_escape(key)}")
        elif isinstance(a, list):
            if len(a) != len(b):
                raise ValidationError(f"{path or '/'}: unexpected semantic length difference")
            for index, (left_item, right_item) in enumerate(zip(a, b)):
                walk(left_item, right_item, f"{path}/{index}")
        elif a != b:
            raise ValidationError(f"{path or '/'}: unexpected semantic value difference")

    walk(left, right, "")
    missing = allowed - observed
    if missing:
        raise ValidationError(f"allowed differences were not observed: {sorted(missing)}")
    return sorted(observed)


def _safe_path(bundle: Path, relative: str) -> Path:
    candidate = bundle / relative
    current = bundle
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValidationError(f"bundle symlinks are forbidden: {relative!r}")
    try:
        candidate.resolve(strict=False).relative_to(bundle.resolve())
    except (OSError, ValueError) as exc:
        raise ValidationError(f"unsafe bundle path {relative!r}") from exc
    return candidate


def _reference(bundle: Path, entries: dict[str, dict[str, Any]], reference: dict[str, str], role: str) -> Path:
    entry = entries.get(reference["path"])
    if entry is None or entry["role"] != role:
        raise ValidationError(f"{reference['path']}: missing {role}-role manifest entry")
    if entry["sha256"] != reference["sha256"]:
        raise ValidationError(f"{reference['path']}: report and manifest hashes differ")
    return _safe_path(bundle, reference["path"])


def validate_bundle(bundle: Path) -> None:
    manifest_path = bundle / "bundle-manifest.json"
    manifest = _load_json(manifest_path)
    if validate_document(manifest) != "dao_bundle_manifest":
        raise ValidationError("bundle manifest has wrong document type")
    if bundle.name != manifest["run_id"] or bundle.parent.name != manifest["git_commit"]:
        raise ValidationError("bundle directory identity differs from manifest")
    entries_list = manifest["files"]
    paths = [entry["path"] for entry in entries_list]
    if len(paths) != len(set(paths)):
        raise ValidationError("$.files: paths must be unique")
    entries = {entry["path"]: entry for entry in entries_list}
    actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file() and path != manifest_path}
    if actual != set(paths):
        raise ValidationError("manifest/file payload set differs")
    for entry in entries_list:
        path = _safe_path(bundle, entry["path"])
        if not path.is_file() or path.stat().st_size != entry["size_bytes"] or _sha256(path) != entry["sha256"]:
            raise ValidationError(f"{entry['path']}: payload identity differs")

    report_entry = entries.get(manifest["report_path"])
    if report_entry is None or report_entry["role"] != "report":
        raise ValidationError("$.report_path: missing report-role entry")
    report = _load_json(_safe_path(bundle, manifest["report_path"]))
    validate_document(report)
    if (report["run_id"], report["git"]["commit"], report["git"]["dirty"], report["status"]) != (
        manifest["run_id"], manifest["git_commit"], manifest["dirty"], manifest["status"]
    ):
        raise ValidationError("report and manifest run bindings differ")
    if report["oracle_revision"] != manifest["git_commit"]:
        raise ValidationError("oracle revision is not the bundle commit")
    if [item["scenario_id"] for item in report["scenarios"]] != manifest["scenario_ids"]:
        raise ValidationError("report and manifest scenario order differs")
    if [item["pair_id"] for item in report["pairs"]] != manifest["pair_ids"]:
        raise ValidationError("report and manifest pair order differs")
    environment = _load_json(_reference(bundle, entries, report["environment"], "environment"))
    validate_document(environment)
    if report["status"] == "pass" and environment["status"] != "ready":
        raise ValidationError("passing report requires a ready DAO environment")

    scenario_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    referenced = {manifest["report_path"], report["environment"]["path"]}
    for result in report["scenarios"]:
        input_path = _reference(bundle, entries, result["input"], "scenario_input")
        referenced.add(result["input"]["path"])
        scenario = _load_json(input_path)
        validate_document(scenario)
        if (result["scenario_id"], result["recipe"]) != (scenario["scenario_id"], scenario["recipe"]):
            raise ValidationError(f"{result['scenario_id']}: result/input binding differs")
        for key, role in (("output_database", "output_database"), ("dao_snapshot", "dao_snapshot"), ("operation_log", "operation_log")):
            reference = result[key]
            if reference is not None:
                _reference(bundle, entries, reference, role)
                referenced.add(reference["path"])
        if result["status"] == "pass":
            if any(result[key] is None for key in ("output_database", "dao_snapshot", "operation_log")):
                raise ValidationError(f"{result['scenario_id']}: passing result lacks artifacts")
            snapshot_path = _reference(bundle, entries, result["dao_snapshot"], "dao_snapshot")
            validate_document_path(snapshot_path)
            snapshot = _load_json(snapshot_path)
            if snapshot["scenario_id"] != result["scenario_id"] or snapshot["producer"]["kind"] != "dao":
                raise ValidationError(f"{result['scenario_id']}: snapshot identity differs")
            if snapshot["producer"]["source_revision"] != report["git"]["commit"]:
                raise ValidationError(f"{result['scenario_id']}: snapshot revision differs")
            if snapshot["database_sha256"] != result["output_database"]["sha256"]:
                raise ValidationError(f"{result['scenario_id']}: snapshot/database hash differs")
            _validate_snapshot_against_recipe(scenario, snapshot)
            log = _load_json(_reference(bundle, entries, result["operation_log"], "operation_log"))
            validate_document(log)
            if (log["run_id"], log["scenario_id"], log["git_commit"], log["final_status"]) != (
                report["run_id"], result["scenario_id"], report["git"]["commit"], "pass"
            ):
                raise ValidationError(f"{result['scenario_id']}: operation log binding differs")
            expected_actions = ["activate_provider"] + [step["action"] for step in scenario["steps"]] + ["reopen_database", "snapshot", "finalize"]
            if [entry["action"] for entry in log["entries"]] != expected_actions:
                raise ValidationError(f"{result['scenario_id']}: operation action order differs")
            scenario_by_id[result["scenario_id"]] = (result, snapshot)

    for result in report["pairs"]:
        pair_path = _reference(bundle, entries, result["input"], "pair_input")
        referenced.add(result["input"]["path"])
        pair = _load_json(pair_path)
        validate_document(pair)
        if (result["pair_id"], result["left_scenario_id"], result["right_scenario_id"]) != (
            pair["pair_id"], pair["left_scenario_id"], pair["right_scenario_id"]
        ):
            raise ValidationError(f"{result['pair_id']}: result/input binding differs")
        for key in ("left_snapshot", "right_snapshot"):
            if result[key] is not None:
                _reference(bundle, entries, result[key], "dao_snapshot")
                referenced.add(result[key]["path"])
        if result["status"] == "pass":
            if result["left_snapshot"] is None or result["right_snapshot"] is None:
                raise ValidationError(f"{result['pair_id']}: passing pair lacks snapshots")
            left_result, left = scenario_by_id.get(result["left_scenario_id"], (None, None))
            right_result, right = scenario_by_id.get(result["right_scenario_id"], (None, None))
            if left is None or right is None:
                raise ValidationError(f"{result['pair_id']}: passing pair sides are not passing scenarios")
            if result["left_snapshot"] != left_result["dao_snapshot"] or result["right_snapshot"] != right_result["dao_snapshot"]:
                raise ValidationError(f"{result['pair_id']}: pair snapshot references differ from scenario results")
            observed = compare_snapshots(left, right, pair["allowed_difference_paths"])
            if result["observed_difference_paths"] != observed:
                raise ValidationError(f"{result['pair_id']}: observed difference report differs")
    if report["status"] == "pass" and set(entries) != referenced:
        raise ValidationError("passing bundle contains unreferenced payloads")


if __name__ == "__main__":
    from protocol_cli import main
    raise SystemExit(main(
        schema_count=len(SCHEMAS),
        validate_schemas=validate_schemas,
        validate_document_path=validate_document_path,
        validate_bundle=validate_bundle,
        validation_error=ValidationError,
    ))
