#!/usr/bin/env python3
"""Strict JSON Schema and semantic validation primitives for DAO A1."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from protocol_validation import (
    JSON_SCHEMA_DRAFT,
    ValidationError,
    lint_schema,
    validate_schema_value,
)

SCHEMA_DIR = SCRIPTS_ROOT.parent / "experiments" / "a1"
CHECKED_PLAN = SCHEMA_DIR / "a1-allocation-maps.plan.json"
PLAN_SHA256 = "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80"
MAX_SCHEMA_BYTES = 1_048_576
MAX_DOCUMENT_BYTES = 2_097_152


def parse_json_bytes(payload: bytes, label: str) -> Any:
    """Parse duplicate-free, finite, BOM-free UTF-8 JSON."""
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{label}: UTF-8 byte-order marks are forbidden")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    def finite_number(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        decoded = payload.decode("utf-8")
        return json.loads(
            decoded,
            object_pairs_hook=unique_object,
            parse_constant=finite_number,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{label}: invalid JSON: {exc}") from exc


def _regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect file: {exc}") from exc
    reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
        raise ValidationError(f"{path}: expected a regular non-link file")
    return metadata


def load_bounded_json(path: Path, maximum: int = MAX_DOCUMENT_BYTES) -> Any:
    """Load a strict JSON document without accepting an oversized file."""
    metadata = _regular_file(path)
    if metadata.st_size > maximum:
        raise ValidationError(f"{path}: JSON exceeds {maximum} bytes")
    try:
        with path.open("rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot read JSON: {exc}") from exc
    if len(payload) > maximum:
        raise ValidationError(f"{path}: JSON exceeds {maximum} bytes")
    return parse_json_bytes(payload, str(path))


class A1SchemaSet:
    """Closed, linted schema mapping derived from A1 schema constants."""

    def __init__(self, schema_dir: Path = SCHEMA_DIR) -> None:
        self.schema_dir = schema_dir
        self._schemas: dict[str, tuple[Path, dict[str, Any]]] | None = None

    def _load(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        if self._schemas is not None:
            return self._schemas
        try:
            paths = sorted(self.schema_dir.glob("*.schema.json"))
        except OSError as exc:
            raise ValidationError(f"{self.schema_dir}: cannot list schemas: {exc}") from exc
        if not paths:
            raise ValidationError(f"{self.schema_dir}: no A1 schemas found")
        schemas: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in paths:
            document = load_bounded_json(path, MAX_SCHEMA_BYTES)
            if not isinstance(document, dict):
                raise ValidationError(f"{path}: schema root must be an object")
            if document.get("$schema") != JSON_SCHEMA_DRAFT:
                raise ValidationError(f"{path}: unexpected JSON Schema draft")
            if not isinstance(document.get("$id"), str):
                raise ValidationError(f"{path}: schema requires $id")
            properties = document.get("properties")
            if not isinstance(properties, dict):
                raise ValidationError(f"{path}: schema requires properties")
            document_type = properties.get("document_type", {}).get("const")
            if not isinstance(document_type, str) or not document_type:
                raise ValidationError(f"{path}: document_type const is missing")
            if document_type in schemas:
                raise ValidationError(
                    f"{path}: duplicate schema for document_type {document_type!r}"
                )
            lint_schema(document)
            schemas[document_type] = (path, document)
        self._schemas = schemas
        return schemas

    def lint(self) -> None:
        self._load()

    def validate_schema(self, document: Any, expected_type: str | None = None) -> str:
        """Perform structural schema validation only."""
        if not isinstance(document, dict):
            raise ValidationError("$: A1 document must be an object")
        document_type = document.get("document_type")
        if not isinstance(document_type, str):
            raise ValidationError("$.document_type: expected a string")
        if expected_type is not None and document_type != expected_type:
            raise ValidationError(
                f"$.document_type: expected {expected_type!r}, got {document_type!r}"
            )
        try:
            _, schema = self._load()[document_type]
        except KeyError as exc:
            raise ValidationError(f"unknown A1 document_type {document_type!r}") from exc
        validate_schema_value(document, schema, schema, "$")
        return document_type


SCHEMA_SET = A1SchemaSet()


def _unique(items: list[Any], label: str) -> None:
    encoded = [
        json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for item in items
    ]
    if len(encoded) != len(set(encoded)):
        raise ValidationError(f"{label}: values must be unique")


def _require_finite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError(f"{label}: non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            _require_finite(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _require_finite(child, f"{label}[{index}]")


SemanticValidator = Callable[[dict[str, Any]], None]
_SEMANTIC_VALIDATORS: dict[str, SemanticValidator] = {}


def semantic_validator(document_type: str) -> Callable[[SemanticValidator], SemanticValidator]:
    def register(function: SemanticValidator) -> SemanticValidator:
        if document_type in _SEMANTIC_VALIDATORS:
            raise RuntimeError(f"duplicate semantic validator for {document_type}")
        _SEMANTIC_VALIDATORS[document_type] = function
        return function

    return register


def validate_semantics(document: dict[str, Any]) -> None:
    """Perform relationship validation separately from JSON Schema."""
    _require_finite(document, "$")
    document_type = document.get("document_type")
    validator = _SEMANTIC_VALIDATORS.get(document_type)
    if validator is not None:
        try:
            validator(document)
        except (KeyError, TypeError) as exc:
            raise ValidationError(
                f"$: malformed fields for {document_type!r} semantics"
            ) from exc


def validate_document(document: Any, expected_type: str | None = None) -> str:
    """Run structural then semantic validation as two explicit passes."""
    document_type = SCHEMA_SET.validate_schema(document, expected_type)
    assert isinstance(document, dict)
    validate_semantics(document)
    return document_type


def require_equal(observed: Any, expected: Any, label: str) -> None:
    if observed != expected:
        raise ValidationError(f"{label}: observed value differs from the bound value")


def _row_payload(role: str, row_id: int) -> bytes:
    seed = f"A1|{role}|{row_id:010d}|".encode("ascii")
    return (seed * ((240 + len(seed) - 1) // len(seed)))[:240]


@functools.lru_cache(maxsize=1024)
def _reread_sha256(
    role: str, row_count: int, alternating_full_count: int | None = None
) -> str:
    if role not in ("D", "L", "P", "H") or not 0 <= row_count <= 200_000:
        raise ValidationError("DAO reread digest request exceeds the row contract")
    if alternating_full_count is not None and not 0 <= alternating_full_count <= 200_000:
        raise ValidationError("alternating reread exceeds the row contract")
    row_ids = (
        range(1, alternating_full_count + 1, 2)
        if alternating_full_count is not None
        else range(1, row_count + 1)
    )
    digest = hashlib.sha256()
    for row_id in row_ids:
        payload = _row_payload(role, row_id)
        digest.update(row_id.to_bytes(4, "little", signed=True))
        digest.update(len(payload).to_bytes(2, "little"))
        digest.update(payload)
    return digest.hexdigest()


@semantic_validator("dao_a1_allocation_maps_plan")
def _validate_plan(document: dict[str, Any]) -> None:
    checkpoints = document["checkpoint_design"]["checkpoint_ids"]
    _unique(checkpoints, "$.checkpoint_design.checkpoint_ids")
    require_equal(
        len(checkpoints),
        document["checkpoint_design"]["count"],
        "$.checkpoint_design.count",
    )
    require_equal(
        len(checkpoints),
        document["bounds"]["planned_checkpoints_per_replica"],
        "$.bounds.planned_checkpoints_per_replica",
    )
    if len(checkpoints) > document["bounds"]["max_checkpoints_per_replica"]:
        raise ValidationError("$.checkpoint_design: count exceeds its ceiling")
    require_equal(document["replicas"]["count"], document["bounds"]["replicas"], "$.replicas.count")
    if set(document["replicas"]["derivation"]) | {document["replicas"]["holdout"]} != set(
        range(1, document["replicas"]["count"] + 1)
    ):
        raise ValidationError("$.replicas: derivation and holdout do not partition replicas")
    bindings = document["tables"]["role_bindings"]
    require_equal([row["replica"] for row in bindings], [1, 2, 3], "$.tables.role_bindings replicas")
    for index, binding in enumerate(bindings):
        roles = [binding[role] for role in ("D", "L", "P", "H")]
        if len(roles) != len(set(roles)):
            raise ValidationError(f"$.tables.role_bindings[{index}]: physical tables must be unique")
    require_equal(document["bounds"]["page_size"], document["page_capture"]["page_size"], "$.page_capture.page_size")


@semantic_validator("dao_a1_environment")
def _validate_environment(document: dict[str, Any]) -> None:
    if document["status"] != "ready":
        raise ValidationError("$.status: A1 acquisition requires a ready environment")


@semantic_validator("dao_a1_replica_observation")
def _validate_replica(document: dict[str, Any]) -> None:
    role_binding = document["role_binding"]
    if len(set(role_binding.values())) != 4:
        raise ValidationError("$.role_binding: physical tables must be unique")
    checkpoints = document["checkpoints"]
    require_equal(
        [row["ordinal"] for row in checkpoints],
        list(range(len(checkpoints))),
        "$.checkpoints ordinals",
    )
    inserted_totals = [checkpoint["inserted_rows_total"] for checkpoint in checkpoints]
    if inserted_totals != sorted(inserted_totals):
        raise ValidationError("$.checkpoints: inserted row totals must be monotonic")
    require_equal(
        document["inserted_rows_total"],
        inserted_totals[-1],
        "$.inserted_rows_total",
    )
    require_equal(
        document["logical_checkpoint_read_bytes"],
        sum(checkpoint["actual_size_bytes"] for checkpoint in checkpoints),
        "$.logical_checkpoint_read_bytes",
    )
    l_full_count: int | None = None
    for index, checkpoint in enumerate(checkpoints):
        require_equal(
            checkpoint["page_index"]["path"],
            f"page-indexes/replica-{document['replica']:02d}/"
            f"{index:02d}-{checkpoint['checkpoint_id']}.json",
            f"$.checkpoints[{index}].page_index.path",
        )
        require_equal(
            checkpoint["actual_size_bytes"],
            checkpoint["actual_file_pages"] * 2_048,
            f"$.checkpoints[{index}].actual_size_bytes",
        )
        checkpoint_id = checkpoint["checkpoint_id"]
        if checkpoint_id in ("E0", "E0R", "D_DROP"):
            expected_roles: list[str] = []
        elif checkpoint_id.startswith("D_"):
            expected_roles = ["D"]
        elif checkpoint_id.startswith("L_"):
            expected_roles = ["D", "L"]
        elif checkpoint_id.startswith("P_"):
            expected_roles = ["D", "L", "P"]
        elif checkpoint_id.startswith("H_"):
            expected_roles = ["D", "L", "P", "H"]
        else:
            raise ValidationError(f"$.checkpoints[{index}]: unknown checkpoint family")
        rereads = checkpoint["dao_reread"]
        roles = [row["role"] for row in rereads]
        if roles != expected_roles:
            raise ValidationError(
                f"$.checkpoints[{index}].dao_reread: extant-role set differs"
            )
        for reread in rereads:
            require_equal(
                reread["row_count"],
                checkpoint["table_row_counts"][reread["role"]],
                f"$.checkpoints[{index}].dao_reread row count",
            )
            row_count = reread["row_count"]
            if checkpoint_id == "L_DELETE_ALTERNATING" and reread["role"] == "L":
                if l_full_count is None:
                    raise ValidationError(
                        f"$.checkpoints[{index}]: missing pre-delete L count"
                    )
                require_equal(
                    row_count,
                    (l_full_count + 1) // 2,
                    f"$.checkpoints[{index}].dao_reread L delete count",
                )
                expected_digest = _reread_sha256("L", row_count, l_full_count)
            else:
                expected_digest = _reread_sha256(reread["role"], row_count)
            require_equal(
                reread["rolling_sha256"],
                expected_digest,
                f"$.checkpoints[{index}].dao_reread rolling sha256",
            )
        for absent_role in set(("D", "L", "P", "H")) - set(expected_roles):
            require_equal(
                checkpoint["table_row_counts"][absent_role],
                0,
                f"$.checkpoints[{index}].table_row_counts.{absent_role}",
            )
        if checkpoint_id == "L_REL_1280":
            l_full_count = checkpoint["table_row_counts"]["L"]
        elif checkpoint_id in ("L_REINSERT_SAME", "L_IDLE_REOPEN"):
            if l_full_count is None:
                raise ValidationError(
                    f"$.checkpoints[{index}]: missing L full-count anchor"
                )
            require_equal(
                checkpoint["table_row_counts"]["L"],
                l_full_count,
                f"$.checkpoints[{index}].table_row_counts.L",
            )
        baseline = checkpoint["target_baseline_pages"]
        threshold = checkpoint["target_threshold_pages"]
        overshoot = checkpoint["target_overshoot_pages"]
        relative = checkpoint_id.startswith(
            ("D_GROW_", "D_REGROW_", "L_REL_", "H_REL_")
        )
        absolute = checkpoint_id.startswith("P_ABS_")
        if relative:
            if baseline is None:
                raise ValidationError(
                    f"$.checkpoints[{index}]: relative target requires baseline"
                )
            require_equal(
                threshold,
                baseline + int(checkpoint_id.rsplit("_", 1)[1]),
                f"$.checkpoints[{index}].target_threshold_pages",
            )
        elif absolute:
            require_equal(baseline, None, f"$.checkpoints[{index}].target_baseline_pages")
            require_equal(
                threshold,
                int(checkpoint_id.rsplit("_", 1)[1]),
                f"$.checkpoints[{index}].target_threshold_pages",
            )
        elif baseline is not None or threshold is not None or overshoot is not None:
            raise ValidationError(
                f"$.checkpoints[{index}]: non-target checkpoint requires null target fields"
            )
        if relative or absolute:
            assert threshold is not None
            if checkpoint["actual_file_pages"] < threshold:
                raise ValidationError(f"$.checkpoints[{index}]: target was not reached")
            require_equal(
                overshoot,
                checkpoint["actual_file_pages"] - threshold,
                f"$.checkpoints[{index}].target_overshoot_pages",
            )


@semantic_validator("dao_a1_page_index")
def _validate_page_index(document: dict[str, Any]) -> None:
    pages = document["ordered_page_sha256"]
    require_equal(len(pages), document["page_count"], "$.page_count")
    require_equal(document["file_size_bytes"], len(pages) * 2_048, "$.file_size_bytes")
    changed = document["changed_page_indices"]
    if changed != sorted(changed):
        raise ValidationError("$.changed_page_indices: values must be sorted")
    if document["ordinal"] == 0:
        if document["predecessor_checkpoint_id"] is not None:
            raise ValidationError("$.predecessor_checkpoint_id: first checkpoint requires null")
    elif document["predecessor_checkpoint_id"] is None:
        raise ValidationError("$.predecessor_checkpoint_id: later checkpoint requires predecessor")


@semantic_validator("dao_a1_analysis_report")
def _validate_analysis(document: dict[str, Any]) -> None:
    if document["derivation_survivor_count"] > document["candidate_models_examined"]:
        raise ValidationError("$: derivation survivors exceed examined candidates")
    decisive = document["scientific_outcome"] == "one_joint_model_predicts_holdout"
    if decisive:
        raise ValidationError(
            "$: decisive outcome requires an independent recomputing validator"
        )
    if document["surviving_model"] is not None or not document["no_outcome_reasons"]:
        raise ValidationError("$: no-outcome report requires reasons and no surviving model")


@semantic_validator("dao_a1_bundle_manifest")
def _validate_manifest(document: dict[str, Any]) -> None:
    files = document["files"]
    if len(files) > 262_399:
        raise ValidationError("$.files: exceeds acquisition artifact-count ceiling")
    if any(row["role"] == "acquisition_log" for row in files):
        raise ValidationError(
            "$.files: acquisition_log is not produced by the frozen acquisition"
        )
    paths = [row["path"] for row in files]
    if len(paths) != len(set(paths)):
        raise ValidationError("$.files: paths must be unique")
    if len({path.casefold() for path in paths}) != len(paths):
        raise ValidationError("$.files: paths must not case-collide")
    if "bundle-manifest.json" in paths:
        raise ValidationError("$.files: manifest cannot inventory itself")
    require_equal(
        document["bundle_size_bytes_excluding_manifest"],
        sum(row["size_bytes"] for row in files),
        "$.bundle_size_bytes_excluding_manifest",
    )
    require_equal(
        document["page_blob_count"],
        sum(row["role"] == "page_blob" for row in files),
        "$.page_blob_count",
    )
    for index, row in enumerate(files):
        expected_media = {
            "page_blob": "application/octet-stream",
            "acquisition_log": "text/plain",
        }.get(row["role"], "application/json")
        require_equal(row["media_type"], expected_media, f"$.files[{index}].media_type")


def _absolute_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise argparse.ArgumentTypeError("expected an existing absolute file")
    return path


def _absolute_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError("expected an existing absolute directory")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("validate-plan")
    plan.add_argument("plan", type=_absolute_file)
    document = commands.add_parser("validate-document")
    document.add_argument("document", type=_absolute_file)
    bundle = commands.add_parser("validate-bundle")
    bundle.add_argument("bundle_root", type=_absolute_directory)
    return result


def run(arguments: argparse.Namespace) -> str:
    SCHEMA_SET.lint()
    if arguments.command == "validate-plan":
        document = load_bounded_json(arguments.plan)
        validate_document(document, "dao_a1_allocation_maps_plan")
        return "PASS: checked DAO A1 plan"
    if arguments.command == "validate-document":
        document = load_bounded_json(arguments.document)
        document_type = validate_document(document)
        return (
            f"PASS: checked {document_type} schema and local semantics; "
            "cross-artifact bindings not evaluated"
        )
    from a1_bundle import validate_bundle

    validated = validate_bundle(arguments.bundle_root)
    return (
        f"PASS: checked DAO A1 bundle structure and bindings "
        f"{validated['manifest']['run_id']}; scientific outcome not validated"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        print(run(parser().parse_args(argv)))
        return 0
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
