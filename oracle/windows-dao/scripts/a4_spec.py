#!/usr/bin/env python3
"""Fail-closed checked contract primitives for the DAO A4 experiment.

This module binds consumers to the immutable A4 plan and schema family.  It
contains no analyzer or generator behavior; those modules consume the checked,
ordered views compiled here.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from protocol_validation import ProtocolSchemaSet, ValidationError

DAO_ROOT = Path(__file__).resolve().parents[1]
A4_ROOT = DAO_ROOT / "experiments" / "a4"
CHECKED_PLAN_PATH = A4_ROOT / "a4-row-anchored-maps.plan.json"
PLAN_SHA256 = "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
REVISION_PLAN_SHA256 = PLAN_SHA256
EXPERIMENT_ID = "DAO-A4-ROW-ANCHORED-MAPS-001"

_SCHEMA_FILES = {
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
}

SCHEMA_SHA256: Mapping[str, str] = MappingProxyType(
    {
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
    }
)

SCHEMAS = ProtocolSchemaSet(A4_ROOT, _SCHEMA_FILES)


def require_equal(actual: Any, expected: Any, location: str) -> None:
    """Reject a value that differs from the checked A4 contract."""
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked A4 contract")


def sha256_hex(payload: bytes) -> str:
    """Return a lowercase SHA-256 digest for retained bytes."""
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return sha256_hex(path.read_bytes())
    except OSError as exc:
        raise ValidationError(f"{path}: cannot hash file: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON value for A4 model and candidate identity hashing."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc


def canonical_model_id(model_type: str, model: Mapping[str, Any]) -> str:
    """Hash the replica-invariant identity shared by H1 and H4 models."""
    return sha256_hex(canonical_json_bytes({"model_type": model_type, "model": model}))


def canonical_candidate_id(
    model_type: str,
    model: Mapping[str, Any],
    instance_bindings: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Hash a complete registered candidate identity."""
    identity: dict[str, Any] = {"model_type": model_type, "model": model}
    if instance_bindings is not None:
        identity["instance_bindings"] = instance_bindings
    return sha256_hex(canonical_json_bytes(identity))


def load_bounded_json_with_payload(
    path: Path, maximum: int = 64 * 1024 * 1024
) -> tuple[dict[str, Any], bytes]:
    """Load one regular, bounded, duplicate-key-free UTF-8 JSON object."""
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValidationError("JSON byte ceiling must be a non-negative integer")
    try:
        metadata = path.lstat()
        reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(f"{path}: JSON input must be a regular file")
        if metadata.st_size > maximum:
            raise ValidationError(f"{path}: exceeds {maximum}-byte JSON ceiling")
        payload = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect JSON: {exc}") from exc
    if len(payload) > maximum:
        raise ValidationError(f"{path}: exceeds {maximum}-byte JSON ceiling")
    if len(payload) != metadata.st_size:
        raise ValidationError(f"{path}: JSON input changed while it was read")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: JSON root must be an object")
    return value, payload


def load_bounded_json(path: Path, maximum: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Load a bounded protocol JSON object without retaining its bytes."""
    return load_bounded_json_with_payload(path, maximum)[0]


@dataclass(frozen=True)
class LifecycleRange:
    logical_role: str
    first_checkpoint: str
    last_checkpoint: str
    first_ordinal: int
    last_ordinal: int


@dataclass(frozen=True)
class CheckedPlan:
    document: dict[str, Any]
    checkpoint_ids: tuple[str, ...]
    checkpoint_ordinals: Mapping[str, int]
    predicate_ids: tuple[str, ...]
    ordered_predicate_contracts: tuple[Mapping[str, Any], ...]
    predicate_contracts: Mapping[str, Mapping[str, Any]]
    campaign_predicate_ids: tuple[str, ...]
    layer_predicate_ids: Mapping[str, tuple[str, ...]]
    holdout_predicate_ids: tuple[str, ...]
    grammars: Mapping[str, Any]
    bounds: Mapping[str, int]
    logical_roles: tuple[str, ...]
    role_bindings: Mapping[int, Mapping[str, str]]
    lifecycle_ranges: Mapping[str, LifecycleRange]


def _as_unique_strings(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{location}: expected an array of strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValidationError(f"{location}: values must be unique")
    return result


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _compile_lifecycle_ranges(
    checkpoint_ids: tuple[str, ...],
    expected_schema: Mapping[str, Any],
    logical_roles: tuple[str, ...],
) -> Mapping[str, LifecycleRange]:
    checkpoint_ordinals = {name: ordinal for ordinal, name in enumerate(checkpoint_ids)}
    extant: dict[str, tuple[str, list[str]]] = {}
    for checkpoint in checkpoint_ids:
        tokens = _as_unique_strings(
            expected_schema.get(checkpoint),
            f"$.tables.expected_schema_by_checkpoint.{checkpoint}",
        )
        seen_roles: set[str] = set()
        for token in tokens:
            fields = token.split(":")
            role = fields[0]
            if role not in logical_roles or role in seen_roles or len(fields) < 2:
                raise ValidationError(f"{checkpoint}: invalid lifecycle schema token {token!r}")
            seen_roles.add(role)
            version = fields[1] if len(fields) > 2 and fields[1].startswith("v") else "v1"
            instance = f"{role}-{version}"
            extant.setdefault(instance, (role, []))[1].append(checkpoint)
    ranges: dict[str, LifecycleRange] = {}
    for instance, (role, checkpoints) in extant.items():
        first, last = checkpoints[0], checkpoints[-1]
        first_ordinal = checkpoint_ordinals[first]
        last_ordinal = checkpoint_ordinals[last]
        if tuple(checkpoints) != checkpoint_ids[first_ordinal : last_ordinal + 1]:
            raise ValidationError(f"lifecycle range {instance}: reappears after absence")
        ranges[instance] = LifecycleRange(
            role, first, last, first_ordinal, last_ordinal
        )
    return MappingProxyType(ranges)


def _compile_plan(document: dict[str, Any]) -> CheckedPlan:
    require_equal(
        SCHEMAS.validate(document),
        "dao_a4_row_anchored_maps_plan",
        "$.document_type",
    )
    require_equal(document["experiment_id"], EXPERIMENT_ID, "$.experiment_id")

    checkpoint_design = document["checkpoint_design"]
    checkpoint_ids = _as_unique_strings(
        checkpoint_design["checkpoint_ids"], "$.checkpoint_design.checkpoint_ids"
    )
    require_equal(len(checkpoint_ids), checkpoint_design["count"], "checkpoint count")
    checkpoint_ordinals = MappingProxyType(
        {name: ordinal for ordinal, name in enumerate(checkpoint_ids)}
    )

    registry = document["predicate_registry"]
    predicate_ids = _as_unique_strings(registry["ids"], "$.predicate_registry.ids")
    contracts = registry["predicate_contracts"]
    if not isinstance(contracts, list):
        raise ValidationError("$.predicate_registry.predicate_contracts: expected array")
    require_equal(
        [contract["predicate_id"] for contract in contracts],
        list(predicate_ids),
        "predicate contract order",
    )
    require_equal(
        [contract["order"] for contract in contracts],
        list(range(1, len(predicate_ids) + 1)),
        "predicate numeric order",
    )
    require_equal(
        [contract["terminal_id"] for contract in contracts],
        list(predicate_ids),
        "predicate terminal ids",
    )
    fixture_ids = [contract["reachability_fixture_id"] for contract in contracts]
    require_equal(len(fixture_ids), len(set(fixture_ids)), "reachability fixture ids")

    campaign = _as_unique_strings(
        registry["campaign_evaluated_before_any_layer"],
        "$.predicate_registry.campaign_evaluated_before_any_layer",
    )
    raw_layers = registry["per_layer_ordered_predicates"]
    if not isinstance(raw_layers, dict):
        raise ValidationError("$.predicate_registry.per_layer_ordered_predicates: expected object")
    layer_sequences = {
        layer: _as_unique_strings(sequence, f"predicate layer {layer}")
        for layer, sequence in raw_layers.items()
    }
    holdout = _as_unique_strings(
        registry["holdout_phase_ordered_predicates"],
        "$.predicate_registry.holdout_phase_ordered_predicates",
    )
    flattened = campaign + tuple(
        predicate
        for sequence in layer_sequences.values()
        for predicate in sequence
    ) + holdout
    require_equal(flattened, predicate_ids, "predicate phase order")

    terminal_payloads = {
        contract["predicate_id"]: {
            "terminal_payload_schema": contract["terminal_payload_schema"],
            "candidate_stage": contract["candidate_stage"],
            "result_slots": contract["result_slots"],
        }
        for contract in contracts
        if contract["terminal_payload_schema"] != "none"
    }
    require_equal(
        registry["terminal_payload_by_predicate"],
        terminal_payloads,
        "terminal payload registry",
    )

    bounds = document["bounds"]
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in bounds.values()
    ):
        raise ValidationError("$.bounds: every bound must be a positive integer")
    require_equal(bounds["page_size"], document["page_capture"]["page_size"], "page size")
    require_equal(
        bounds["max_checkpoints_per_replica"] >= len(checkpoint_ids),
        True,
        "checkpoint bound",
    )

    tables = document["tables"]
    logical_roles = _as_unique_strings(tables["logical_roles"], "$.tables.logical_roles")
    require_equal(
        tuple(document["candidate_grammars"]["h1"]["logical_roles"]),
        logical_roles,
        "H1 logical roles",
    )
    physical_names = _as_unique_strings(tables["physical_names"], "$.tables.physical_names")
    role_bindings: dict[int, Mapping[str, str]] = {}
    for expected_replica, row in enumerate(tables["role_bindings"], start=1):
        require_equal(row["replica"], expected_replica, "role-binding replica order")
        require_equal(set(row), {"replica", *logical_roles}, "role-binding keys")
        binding = {role: row[role] for role in logical_roles}
        require_equal(tuple(sorted(binding.values())), tuple(sorted(physical_names)), "role-binding permutation")
        role_bindings[expected_replica] = MappingProxyType(binding)
    require_equal(tuple(role_bindings), tuple(range(1, document["replicas"]["count"] + 1)), "replica role bindings")
    require_equal(document["replicas"]["derivation"], [1, 2], "derivation replicas")
    require_equal(document["replicas"]["holdout"], 3, "holdout replica")

    frozen_contracts = tuple(_freeze_json(contract) for contract in contracts)
    contract_map = MappingProxyType(
        {
            predicate_id: frozen_contracts[index]
            for index, predicate_id in enumerate(predicate_ids)
        }
    )
    return CheckedPlan(
        document=document,
        checkpoint_ids=checkpoint_ids,
        checkpoint_ordinals=checkpoint_ordinals,
        predicate_ids=predicate_ids,
        ordered_predicate_contracts=frozen_contracts,
        predicate_contracts=contract_map,
        campaign_predicate_ids=campaign,
        layer_predicate_ids=MappingProxyType(layer_sequences),
        holdout_predicate_ids=holdout,
        grammars=_freeze_json(document["candidate_grammars"]),
        bounds=MappingProxyType(dict(bounds)),
        logical_roles=logical_roles,
        role_bindings=MappingProxyType(role_bindings),
        lifecycle_ranges=_compile_lifecycle_ranges(
            checkpoint_ids,
            tables["expected_schema_by_checkpoint"],
            logical_roles,
        ),
    )


def load_checked_plan(path: Path = CHECKED_PLAN_PATH) -> CheckedPlan:
    """Load the exact immutable A4 plan after checking every schema pin."""
    require_equal(set(SCHEMA_SHA256), set(_SCHEMA_FILES.values()), "schema pin set")
    for name, expected in SCHEMA_SHA256.items():
        require_equal(_sha256_file(A4_ROOT / name), expected, name)
    SCHEMAS.lint()
    require_equal(_sha256_file(path), PLAN_SHA256, "preregistered plan sha256")
    return _compile_plan(load_bounded_json(path))


def validate_schema(document: dict[str, Any], expected: str | None = None) -> str:
    """Validate an A4 document and its immutable plan/revision binding."""
    document_type = SCHEMAS.validate(document)
    if expected is not None:
        require_equal(document_type, expected, "$.document_type")
    if document_type == "dao_a4_row_anchored_maps_plan":
        require_equal(document, PLAN, "checked plan")
    else:
        require_equal(document.get("experiment_id"), EXPERIMENT_ID, "$.experiment_id")
        require_equal(document.get("plan_sha256"), PLAN_SHA256, "$.plan_sha256")
        require_equal(
            document.get("revision_plan_sha256"),
            REVISION_PLAN_SHA256,
            "$.revision_plan_sha256",
        )
    return document_type


def _valid_nonnegative_count(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{location}: expected a non-negative integer")
    return value


def count_contract_accepts(
    requirement: Mapping[str, Any],
    measured_count: int,
    *,
    per_replica_counts: Sequence[int] | None = None,
) -> bool:
    """Evaluate one registered failure-survivor-count contract."""
    measured = _valid_nonnegative_count(measured_count, "measured count")
    if "exact" in requirement:
        if set(requirement) != {"exact"}:
            raise ValidationError("exact count contract has unknown keys")
        expected = _valid_nonnegative_count(requirement["exact"], "exact count")
        return measured == expected
    if "minimum" in requirement:
        if set(requirement) - {"minimum", "source"}:
            raise ValidationError("minimum count contract has unknown keys")
        minimum = _valid_nonnegative_count(requirement["minimum"], "minimum count")
        return measured >= minimum
    if "allowed_ranges" in requirement:
        if set(requirement) - {"allowed_ranges", "source"}:
            raise ValidationError("range count contract has unknown keys")
        ranges = requirement["allowed_ranges"]
        if not isinstance(ranges, (list, tuple)) or not ranges:
            raise ValidationError("allowed_ranges must be a non-empty array")
        return any(count_contract_accepts(item, measured) for item in ranges)
    replica_keys = {"per_replica_exact", "replica_count", "total_exact"}
    if replica_keys <= set(requirement):
        if set(requirement) != replica_keys:
            raise ValidationError("per-replica count contract has unknown keys")
        per_replica = _valid_nonnegative_count(
            requirement["per_replica_exact"], "per-replica exact count"
        )
        replica_count = _valid_nonnegative_count(
            requirement["replica_count"], "replica count"
        )
        total = _valid_nonnegative_count(requirement["total_exact"], "total count")
        if replica_count == 0:
            raise ValidationError("replica count must be positive")
        if total != per_replica * replica_count:
            raise ValidationError("per-replica count contract has an inconsistent total")
        if measured != total:
            return False
        if per_replica_counts is None:
            return True
        counts = tuple(
            _valid_nonnegative_count(value, "per-replica count")
            for value in per_replica_counts
        )
        return (
            len(counts) == replica_count
            and all(value == per_replica for value in counts)
            and sum(counts) == measured
        )
    raise ValidationError("failure survivor-count contract has an unknown shape")


def validate_failure_count(
    predicate_id: str,
    measured_count: int,
    *,
    per_replica_counts: Sequence[int] | None = None,
) -> None:
    """Require a measured terminal count to satisfy its predicate contract."""
    contract = PREDICATE_CONTRACTS.get(predicate_id)
    if contract is None:
        raise ValidationError(f"unregistered A4 predicate {predicate_id!r}")
    requirement = contract["failure_survivor_count"]
    if not count_contract_accepts(
        requirement, measured_count, per_replica_counts=per_replica_counts
    ):
        raise ValidationError(
            f"{predicate_id}: measured failure count violates its contract"
        )


CHECKED_PLAN = load_checked_plan()
PLAN = CHECKED_PLAN.document
BOUNDS = CHECKED_PLAN.bounds
CHECKPOINT_IDS = CHECKED_PLAN.checkpoint_ids
CHECKPOINT_ORDINALS = CHECKED_PLAN.checkpoint_ordinals
PREDICATE_IDS = CHECKED_PLAN.predicate_ids
PREDICATE_CONTRACTS = CHECKED_PLAN.predicate_contracts
CAMPAIGN_PREDICATE_IDS = CHECKED_PLAN.campaign_predicate_ids
LAYER_PREDICATE_IDS = CHECKED_PLAN.layer_predicate_ids
HOLDOUT_PREDICATE_IDS = CHECKED_PLAN.holdout_predicate_ids
CANDIDATE_GRAMMARS = CHECKED_PLAN.grammars
LOGICAL_ROLES = CHECKED_PLAN.logical_roles
ROLE_BINDINGS = CHECKED_PLAN.role_bindings
LIFECYCLE_RANGES = CHECKED_PLAN.lifecycle_ranges
PAGE_SIZE = BOUNDS["page_size"]
