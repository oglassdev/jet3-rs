#!/usr/bin/env python3
"""Immutable inputs compiled independently for the DAO A4 validator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from protocol_validation import ValidationError as SchemaError
from protocol_validation import validate_schema_value


DAO_ROOT = Path(__file__).resolve().parents[1]
A4_ROOT = DAO_ROOT / "experiments" / "a4"
PLAN_PATH = A4_ROOT / "a4-row-anchored-maps.plan.json"
PLAN_SHA256 = "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
REVISION_PLAN_SHA256 = PLAN_SHA256
EXPERIMENT_ID = "DAO-A4-ROW-ANCHORED-MAPS-001"

SCHEMA_FILES: Mapping[str, str] = MappingProxyType({
    "dao_a4_analysis_report": "analysis-report.schema.json",
    "dao_a4_bundle_manifest": "bundle-manifest.schema.json",
    "dao_a4_schema_snapshot": "dao-schema-snapshot.schema.json",
    "dao_a4_frozen_derivation_candidates": "derivation-candidates.schema.json",
    "dao_a4_analyzer_dry_run_report": "dry-run-report.schema.json",
    "dao_a4_environment": "environment.schema.json",
    "dao_a4_h4_occurrence_evidence": "h4-occurrence-evidence.schema.json",
    "dao_a4_holdout_structure_receipt": "holdout-structure-receipt.schema.json",
    "dao_a4_independent_validation_report": "independent-validation-report.schema.json",
    "dao_a4_page_index": "page-index.schema.json",
    "dao_a4_row_anchored_maps_plan": "plan.schema.json",
    "dao_a4_reachability_transcript": "reachability-transcript.schema.json",
    "dao_a4_replica_artifact_manifest": "replica-artifact-manifest.schema.json",
    "dao_a4_replica_observation": "replica-observation.schema.json",
})

SCHEMA_SHA256: Mapping[str, str] = MappingProxyType({
    "analysis-report.schema.json": "bd1cdd62fdf6dae1ed756c092a90936be1318dc3a66e9d1d6309ecfa0d3d2010",
    "bundle-manifest.schema.json": "1b051fe4eeaf7dcdb7304cd885cc88d85630ccd76a7ea6d79b408c0d42272791",
    "dao-schema-snapshot.schema.json": "f890d9c7f710c033b357609ebab73ac51c68153cb067af7e40162930cf5d76c8",
    "derivation-candidates.schema.json": "1cf5829b14663a68c934ff4d16b1b95668291b21b645ad3ae0e93abe3c839a28",
    "dry-run-report.schema.json": "c9a47933675749d3c0a631eb902cd0402502687b1cfbe0dbf5ba22ed6c76b3d9",
    "environment.schema.json": "77f1a95a65d2d202205a8a15ed1a59934bb5d5cca6df2226ae118c90771d131f",
    "h4-occurrence-evidence.schema.json": "272f591f857fa93e059bd66175eef4c13309fbbc276a7755dcd8859a4c2e1ae8",
    "holdout-structure-receipt.schema.json": "92c255fa62a94c3855ea23441d3d1b5077dccf3148b3e255fd171c6508c8a769",
    "independent-validation-report.schema.json": "2771f1cda975ba22b100bb607df2525181fc62721e854a84c7934fd7ea73b233",
    "page-index.schema.json": "f612f77fb8e3180c15bf45681df3e51e4e8088803b297764d85a655de8ab099f",
    "plan.schema.json": "9da16374db62251c55c3df26e7a0d066b8ca2ad3e514c51ce2c0c20b00afec49",
    "reachability-transcript.schema.json": "beaa8179a9c0e5a3d26c1098494f6d0bf32c20ea87d162de65e0637aa3f95bb5",
    "replica-artifact-manifest.schema.json": "61640502ae032877b43895098dcbf3a466bd207600676390b652ebea1bfa8d2b",
    "replica-observation.schema.json": "d1ef77f91471935d99c3b9a1c0e7a4329a1d9d77cab8da51153cfb44de3d22cb",
})

EXPECTED_TAMPERS = (
    ("T1", "plan_binding_mismatch"),
    ("T2", "frozen_set_recomputation_mismatch"),
    ("T3", "schema_snapshot_mismatch"),
    ("T4", "analysis_report_mismatch"),
    ("T5", "campaign_timeout_exceeded"),
    ("T6", "predicate_layer_projection_mismatch"),
    ("T7", "holdout_projection_mismatch"),
    ("T8", "candidate_canonicalization_mismatch"),
    ("T9", "frozen_file_hash_mismatch"),
)


class ContractError(Exception):
    """A stable failure while loading preregistered validator inputs."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read(path: Path, maximum: int = 67_108_864) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ContractError("contract_file_missing", str(path)) from exc
    if not 0 < size <= maximum:
        raise ContractError("contract_file_size", str(path))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ContractError("contract_file_read", str(path)) from exc


def _decode(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("contract_json_invalid", label) from exc
    if not isinstance(value, dict):
        raise ContractError("contract_json_not_object", label)
    return value


@dataclass(frozen=True)
class IndependentContract:
    plan: Mapping[str, Any]
    plan_raw: bytes
    schemas: Mapping[str, Mapping[str, Any]]
    bounds: Mapping[str, int]
    checkpoint_ids: tuple[str, ...]
    predicate_ids: tuple[str, ...]
    campaign_predicates: tuple[str, ...]
    layer_predicates: Mapping[str, tuple[str, ...]]
    holdout_predicates: tuple[str, ...]
    tamper_cases: tuple[Mapping[str, str], ...]

    def validate_document(self, value: Any, document_type: str) -> None:
        try:
            name = SCHEMA_FILES[document_type]
            schema = self.schemas[name]
        except KeyError as exc:
            raise ContractError("unknown_document_type", document_type) from exc
        try:
            validate_schema_value(value, dict(schema), dict(schema), "$")
        except SchemaError as exc:
            raise ContractError("schema_validation_failed", f"{document_type}: {exc}") from exc


def _compile_sequences(plan: Mapping[str, Any]) -> tuple[
    tuple[str, ...], Mapping[str, tuple[str, ...]], tuple[str, ...], tuple[str, ...]
]:
    try:
        registry = plan["predicate_registry"]
        predicate_ids = tuple(registry["ids"])
        campaign = tuple(registry["campaign_evaluated_before_any_layer"])
        layers = {
            name: tuple(values)
            for name, values in registry["per_layer_ordered_predicates"].items()
        }
        holdout = tuple(registry["holdout_phase_ordered_predicates"])
        contracts = registry["predicate_contracts"]
    except (KeyError, TypeError) as exc:
        raise ContractError("predicate_contract_invalid") from exc
    flattened = campaign + tuple(item for values in layers.values() for item in values) + holdout
    if (
        len(predicate_ids) != 40
        or len(set(predicate_ids)) != 40
        or flattened != predicate_ids
        or [row.get("predicate_id") for row in contracts] != list(predicate_ids)
        or [row.get("order") for row in contracts] != list(range(1, 41))
    ):
        raise ContractError("predicate_contract_invalid")
    return predicate_ids, MappingProxyType(layers), campaign, holdout


def _canonical_named_rows(rows: Any, label: str, *, contiguous: bool) -> None:
    if not isinstance(rows, list):
        raise ContractError("schema_snapshot_mismatch", label)
    keys: list[tuple[int, bytes]] = []
    for row in rows:
        try:
            name = row["name"]
            cp1252 = bytes.fromhex(row["name_windows_1252_hex"])
            encoded16 = name.encode("utf-16-le")
            units = [
                encoded16[index] | encoded16[index + 1] << 8
                for index in range(0, len(encoded16), 2)
            ]
            if (
                name.encode("cp1252", errors="strict") != cp1252
                or name.encode() != bytes.fromhex(row["name_utf8_hex"])
                or row["name_utf16_code_units"] != units
            ):
                raise ValueError("name encoding differs")
            keys.append((row["ordinal"], cp1252))
        except (KeyError, TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ContractError("schema_snapshot_mismatch", label) from exc
    if (
        keys != sorted(keys)
        or len({key[0] for key in keys}) != len(keys)
        or len({key[1] for key in keys}) != len(keys)
        or contiguous and [key[0] for key in keys] != list(range(len(keys)))
    ):
        raise ContractError("schema_snapshot_mismatch", label)


def validate_canonical_snapshot(snapshot: Mapping[str, Any], label: str) -> None:
    """Enforce the plan's ordinal-then-CP1252 canonical snapshot ordering."""
    tables = snapshot["tables"]
    _canonical_named_rows(tables, f"{label}:tables", contiguous=True)
    roles: set[str] = set()
    for table in tables:
        role = table["logical_role"]
        if role in roles:
            raise ContractError("schema_snapshot_mismatch", f"{label}:roles")
        roles.add(role)
        _canonical_named_rows(table["fields"], f"{label}:fields", contiguous=True)
        _canonical_named_rows(table["indexes"], f"{label}:indexes", contiguous=False)
        for index in table["indexes"]:
            _canonical_named_rows(index["fields"], f"{label}:index-fields", contiguous=True)


def validate_snapshot_schedule(
    snapshot: Mapping[str, Any], plan: Mapping[str, Any], replica: int, checkpoint: str
) -> None:
    """Rebuild the exact scheduled DAO schema tuples from preregistered values."""
    tables_contract = plan["tables"]
    binding = next(row for row in tables_contract["role_bindings"] if row["replica"] == replica)
    descriptors = tables_contract["expected_schema_by_checkpoint"][checkpoint]
    tables = snapshot["tables"]
    if len(tables) != len(descriptors):
        raise ContractError("schema_snapshot_mismatch", checkpoint)
    definitions = {row["name"]: row for row in tables_contract["definition"]["fields"]}
    for ordinal, (table, descriptor) in enumerate(zip(tables, descriptors, strict=True)):
        parts = descriptor.split(":")
        role = parts[0]
        shape = parts[-1]
        lifecycle = f"{role}-v2" if role == "T2" and "v2" in parts else f"{role}-v1"
        expected_fields = [definitions["Id"]]
        if "payload" in shape:
            expected_fields.append(definitions["Payload"])
        if (
            table["ordinal"] != ordinal
            or table["logical_role"] != role
            or table["lifecycle_instance"] != lifecycle
            or table["name"] != binding[role]
            or table["attributes"] != tables_contract["definition"]["table_attributes_numeric"]
            or len(table["fields"]) != len(expected_fields)
        ):
            raise ContractError("schema_snapshot_mismatch", checkpoint)
        for field_ordinal, (field, expected) in enumerate(
            zip(table["fields"], expected_fields, strict=True)
        ):
            if (
                field["ordinal"] != field_ordinal
                or field["name"] != expected["name"]
                or field["type"] != expected["dao_type_numeric"]
                or field["size"] != expected["size"]
                or field["attributes"] != expected["attributes_numeric"]
                or field["required"] != expected["required"]
                or field["allow_zero_length"] != expected["allow_zero_length"]
            ):
                raise ContractError("schema_snapshot_mismatch", checkpoint)
        expects_index = "index" in shape
        if len(table["indexes"]) != int(expects_index):
            raise ContractError("schema_snapshot_mismatch", checkpoint)
        if expects_index:
            index = table["indexes"][0]
            expected_index = tables_contract["definition"]["index"]
            if (
                index["name"] != expected_index["name"]
                or index["primary"] != expected_index["primary"]
                or index["unique"] != expected_index["unique"]
                or index["required"] != expected_index["required"]
                or index["ignore_nulls"] != expected_index["ignore_nulls"]
                or len(index["fields"]) != 1
                or index["fields"][0]["name"] != expected_index["fields"][0]
                or index["fields"][0]["descending"] != expected_index["descending"]
            ):
                raise ContractError("schema_snapshot_mismatch", checkpoint)


def load_contract(
    plan_path: Path = PLAN_PATH,
    schema_root: Path = A4_ROOT,
) -> IndependentContract:
    """Load only the byte-pinned plan and schemas committed before acquisition."""
    raw = _read(plan_path)
    if sha256_bytes(raw) != PLAN_SHA256:
        raise ContractError("plan_binding_mismatch", str(plan_path))
    plan = _decode(raw, str(plan_path))
    schemas: dict[str, Mapping[str, Any]] = {}
    for name, expected in SCHEMA_SHA256.items():
        schema_raw = _read(schema_root / name)
        if sha256_bytes(schema_raw) != expected:
            raise ContractError("schema_binding_mismatch", name)
        schemas[name] = MappingProxyType(_decode(schema_raw, name))
    try:
        validate_schema_value(
            plan,
            dict(schemas["plan.schema.json"]),
            dict(schemas["plan.schema.json"]),
            "$",
        )
    except SchemaError as exc:
        raise ContractError("plan_schema_mismatch", str(exc)) from exc
    if (
        plan.get("experiment_id") != EXPERIMENT_ID
        or plan.get("implementation_rebinding", {}).get("required_plan_path")
        != "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json"
        or plan.get("artifacts", {}).get("plan")
        != "plan/a4-row-anchored-maps.plan.json"
    ):
        raise ContractError("plan_binding_mismatch")
    predicates, layers, campaign, holdout = _compile_sequences(plan)
    tampers = tuple(plan["independent_validator_contract"]["tamper_cases"])
    observed = tuple((row.get("id"), row.get("required_rejection")) for row in tampers)
    if observed != EXPECTED_TAMPERS or len({row[0] for row in observed}) != len(observed):
        raise ContractError("tamper_contract_invalid")
    bounds = plan["bounds"]
    if (
        bounds.get("page_size") != 2048
        or bounds.get("replicas") != 3
        or bounds.get("planned_checkpoints_per_replica") != 25
    ):
        raise ContractError("resource_contract_invalid")
    return IndependentContract(
        MappingProxyType(plan),
        raw,
        MappingProxyType(schemas),
        MappingProxyType(bounds),
        tuple(plan["checkpoint_design"]["checkpoint_ids"]),
        predicates,
        campaign,
        layers,
        holdout,
        tampers,
    )


CONTRACT = load_contract()
