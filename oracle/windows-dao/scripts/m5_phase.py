#!/usr/bin/env python3
"""Relational worker, quiescence, clone, and sample checks for M5R6."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m5_records import (
    SCHEMA_SET,
    load_checked_plan,
    load_document,
    parse_timestamp,
    resolve_bundle_path,
    validate_invocation_document,
)
from m5_spec import (
    DATABASE_ROLES,
    M4_MANIFEST_SHA256,
    PHASES,
    PHASE_DATABASE_ROLES,
    PREFIX_BYTES,
    compile_checked_plan,
    require_equal,
)
from protocol_validation import ValidationError

EXPECTED_ACTIONS = {
    "source": [
        "bindings_verified", "com_activated", "database_created", "version_read",
        "empty_schema_read", "database_closed", "prefix_observed",
    ],
    "compact": [
        "bindings_verified", "clone_verified", "com_activated",
        "database_compacted", "database_closed", "prefix_observed",
    ],
    "verify": [
        "bindings_verified", "clone_verified", "com_activated", "database_opened",
        "version_read", "empty_schema_read", "database_closed", "prefix_observed",
    ],
}


def _artifact(root: Path, ref: dict[str, Any], limit: int, document_type: str) -> dict[str, Any]:
    document, _, digest = load_document(resolve_bundle_path(root, ref["path"]), limit, document_type)
    require_equal(digest, ref["sha256"], f"{ref['path']} sha256")
    return document


def _expected_database_path(sample: dict[str, Any], role: str) -> str:
    key = f"{role[:-9]}_database_path" if role != "compacted_database" else "compacted_database_path"
    return sample[key]


def _observation_index(result: dict[str, Any], sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    phase = result["phase_id"]
    observations = result["database_observations"]
    roles = [row["database_role"] for row in observations]
    require_equal(roles, list(PHASE_DATABASE_ROLES[phase]), "$.database_observations roles")
    indexed: dict[str, dict[str, Any]] = {}
    for row in observations:
        role = row["database_role"]
        require_equal(row["path"], _expected_database_path(sample, role), f"$.database_observations.{role}.path")
        if role == "compact_input_database":
            require_equal(row["prefix"], None, f"$.database_observations.{role}.prefix")
        else:
            if row["prefix"] is None:
                raise ValidationError(f"$.database_observations.{role}.prefix: retained prefix required")
            require_equal(row["prefix"]["path"], f"evidence/samples/{sample['sample_id']}/{phase.upper()}.prefix.bin", f"$.database_observations.{role}.prefix.path")
            require_equal(row["prefix"]["sha256"], row["prefix_sha256"], f"$.database_observations.{role}.prefix.sha256")
        indexed[role] = row
    return indexed


def validate_worker_result(root: Path, result_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate one completed isolated worker and its directly referenced artifacts."""
    plan, plan_hash = load_checked_plan()
    checked = compile_checked_plan(plan)
    result, _, result_hash = load_document(result_path, 65536, "dao_m5_worker_result")
    sample = checked.samples_by_id.get(result["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: absent from checked plan")
    phase = result["phase_id"]
    expected_result = f"evidence/samples/{sample['sample_id']}/{phase.upper()}-worker-result.json"
    require_equal(result_path.resolve(strict=True), resolve_bundle_path(root, expected_result).resolve(strict=True), "worker-result path")
    invitation_path = f"evidence/samples/{sample['sample_id']}/{phase}-invocation.json"
    invocation, _, invocation_hash = load_document(
        resolve_bundle_path(root, invitation_path), 65536, "dao_m5_invocation"
    )
    require_equal(invocation_hash, result["invocation_sha256"], "$.invocation_sha256")
    validate_invocation_document(invocation, plan, plan_hash, root, expected_result_path=expected_result)
    for actual, expected, location in (
        (result["condition_id"], sample["condition_id"], "$.condition_id"),
        (result["phase_ordinal"], PHASES.index(phase) + 1, "$.phase_ordinal"),
        (result["worker_run_id"], invocation["worker_run_id"], "$.worker_run_id"),
        (result["worker_ordinal"], invocation["worker_ordinal"], "$.worker_ordinal"),
        (result["nonce"], invocation["nonce"], "$.nonce"),
    ):
        require_equal(actual, expected, location)
    started = parse_timestamp(result["started_at_utc"], "$.started_at_utc")
    finished = parse_timestamp(result["finished_at_utc"], "$.finished_at_utc")
    if started > finished:
        raise ValidationError("worker finished before it started")
    operation = _artifact(root, result["operation_log"], 65536, "dao_m5_operation_log")
    require_equal(operation["actions"], EXPECTED_ACTIONS[phase], "operation actions")
    require_equal(operation["sample_id"], sample["sample_id"], "operation sample_id")
    require_equal(operation["phase_id"], phase, "operation phase_id")
    if phase == "compact":
        require_equal(result["snapshot"], None, "$.snapshot")
    else:
        if result["snapshot"] is None:
            raise ValidationError("$.snapshot: source/verify snapshot required")
        snapshot = _artifact(root, result["snapshot"], 65536, "dao_m5_snapshot")
        condition = checked.conditions_by_id[sample["condition_id"]]
        expected_version = condition["expected_source_dao_version"] if phase == "source" else condition["expected_destination_dao_version"]
        require_equal(snapshot["sample_id"], sample["sample_id"], "snapshot sample_id")
        require_equal(snapshot["phase_id"], phase, "snapshot phase_id")
        require_equal(snapshot["dao_version"], expected_version, "snapshot dao_version")
    observations = _observation_index(result, sample)
    for role, row in observations.items():
        database = resolve_bundle_path(root, row["path"])
        size, digest, retained = bounded_file_identity(database, plan["bounds"]["max_database_bytes"], retain=True)
        assert retained is not None
        require_equal(size, row["bytes"], f"{role} bytes")
        require_equal(digest, row["sha256"], f"{role} sha256")
        prefix = retained[:PREFIX_BYTES]
        import hashlib
        require_equal(hashlib.sha256(prefix).hexdigest(), row["prefix_sha256"], f"{role} prefix sha256")
        if row["prefix"] is not None:
            prefix_size, prefix_hash, prefix_bytes = bounded_file_identity(resolve_bundle_path(root, row["prefix"]["path"]), PREFIX_BYTES, retain=True)
            require_equal(prefix_size, PREFIX_BYTES, f"{role} prefix bytes")
            require_equal(prefix_hash, row["prefix"]["sha256"], f"{role} prefix artifact sha256")
            require_equal(prefix_bytes, prefix, f"{role} prefix database projection")
    result["_validated_result_hash"] = result_hash
    return result, invocation, observations


def _validate_quiescence_loaded(
    root: Path,
    quiescence_path: Path,
    document: dict[str, Any],
    result: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Bind a loaded quiescence record without rereading its worker databases."""
    plan, _ = load_checked_plan()
    role = document["database_role"]
    if role not in observations:
        raise ValidationError("$.database_role: not observed by the bound worker")
    expected_locator = f"evidence/quiescence/{document['sample_id']}/{role}.json"
    require_equal(quiescence_path.resolve(strict=True), resolve_bundle_path(root, expected_locator).resolve(strict=True), "quiescence path")
    for actual, expected, location in (
        (document["sample_id"], result["sample_id"], "$.sample_id"),
        (document["phase_id"], result["phase_id"], "$.phase_id"),
        (document["phase_ordinal"], result["phase_ordinal"], "$.phase_ordinal"),
        (document["worker_run_id"], result["worker_run_id"], "$.worker_run_id"),
        (document["worker_finished_at_utc"], result["finished_at_utc"], "$.worker_finished_at_utc"),
    ):
        require_equal(actual, expected, location)
    finished = parse_timestamp(document["worker_finished_at_utc"], "$.worker_finished_at_utc")
    observed = parse_timestamp(document["observation_started_at_utc"], "$.observation_started_at_utc")
    completed = parse_timestamp(document["observation_completed_at_utc"], "$.observation_completed_at_utc")
    if not finished <= observed <= completed:
        raise ValidationError("quiescence chronology is not monotonic after worker exit")
    worker_observation = observations[role]
    database = document["database"]
    for key in ("path", "bytes", "sha256", "prefix_sha256"):
        require_equal(database[key], worker_observation[key], f"$.database.{key}")
    expected_companion = database["path"][:-4] + ".ldb"
    require_equal(document["companion"]["path"], expected_companion, "$.companion.path")
    if document["companion"]["state"] == "present":
        companion = document["companion"]
        size, digest, _ = bounded_file_identity(resolve_bundle_path(root, companion["path"]), plan["bounds"]["max_companion_bytes"], retain=False)
        require_equal(size, companion["bytes"], "$.companion.bytes")
        require_equal(digest, companion["sha256"], "$.companion.sha256")
    elif resolve_bundle_path(root, expected_companion).exists():
        raise ValidationError("$.companion: recorded absent companion exists")
    return document


def validate_quiescence_document(root: Path, quiescence_path: Path, result_path: Path) -> dict[str, Any]:
    """Bind one controller post-worker observation to the worker's exact bytes."""
    plan, _ = load_checked_plan()
    document, _, _ = load_document(quiescence_path, plan["bounds"]["max_quiescence_record_bytes"], "dao_m5_post_worker_quiescence")
    result, _, observations = validate_worker_result(root, result_path)
    return _validate_quiescence_loaded(root, quiescence_path, document, result, observations)


def _validate_clone(root: Path, ref: dict[str, Any], sample: dict[str, Any], clone_id: str) -> dict[str, Any]:
    clone = _artifact(root, ref, 65536, "dao_m5_clone_log")
    require_equal(clone["sample_id"], sample["sample_id"], "clone sample_id")
    require_equal(clone["clone_id"], clone_id, "clone clone_id")
    expected = {
        "source_to_compact_input": (sample["source_database_path"], sample["compact_input_database_path"]),
        "compacted_to_verify_input": (sample["compacted_database_path"], sample["verify_database_path"]),
    }[clone_id]
    require_equal((clone["source_path"], clone["destination_path"]), expected, "clone paths")
    require_equal(clone["source_bytes"], clone["destination_bytes"], "clone bytes")
    require_equal(clone["source_sha256_before_clone"], clone["source_sha256_after_clone"], "clone source hashes")
    require_equal(clone["source_sha256_before_clone"], clone["destination_sha256"], "clone destination hash")
    if parse_timestamp(clone["started_at_utc"], "clone.started_at_utc") > parse_timestamp(clone["completed_at_utc"], "clone.completed_at_utc"):
        raise ValidationError("clone completed before it started")
    return clone


def validate_sample_record(root: Path, record_path: Path) -> dict[str, Any]:
    """Validate one complete M5 sample and all its evidence references."""
    plan, plan_hash = load_checked_plan()
    checked = compile_checked_plan(plan)
    record, _, _ = load_document(record_path, plan["bounds"]["max_sample_record_bytes"], "dao_m5_sample_record")
    sample = checked.samples_by_id.get(record["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: absent from checked plan")
    require_equal(record_path.resolve(strict=True), resolve_bundle_path(root, sample["record_path"]).resolve(strict=True), "sample record path")
    for key in ("sample_id", "condition_id", "replica", "block", "position_in_block", "launch_ordinal"):
        require_equal(record[key], sample[key], f"$.{key}")
    require_equal(record["plan_sha256"], plan_hash, "$.plan_sha256")
    require_equal(record["m4_manifest_sha256"], M4_MANIFEST_SHA256, "$.m4_manifest_sha256")
    results: dict[str, tuple[dict[str, Any], dict[str, dict[str, Any]]]] = {}
    for phase in PHASES:
        row = record["phases"][phase]
        result, _, observations = validate_worker_result(root, resolve_bundle_path(root, row["worker_result"]["path"]))
        require_equal(result["_validated_result_hash"], row["worker_result"]["sha256"], f"$.phases.{phase}.worker_result.sha256")
        require_equal(result["phase_id"], phase, f"$.phases.{phase}.phase_id")
        results[phase] = (result, observations)
    clone_ids = ("source_to_compact_input", "compacted_to_verify_input")
    clones = {
        clone_id: _validate_clone(root, ref, sample, clone_id)
        for ref, clone_id in zip(record["controller_clones"], clone_ids)
    }
    source_clone = clones["source_to_compact_input"]
    source_observation = results["source"][1]["source_database"]
    compact_input = results["compact"][1]["compact_input_database"]
    for clone_key, source_key, destination_key in (
        ("source_bytes", "bytes", "bytes"),
        ("source_sha256_before_clone", "sha256", "sha256"),
    ):
        require_equal(source_clone[clone_key], source_observation[source_key], f"source clone {clone_key}")
        target_clone_key = "destination_bytes" if clone_key == "source_bytes" else "destination_sha256"
        require_equal(source_clone[target_clone_key], compact_input[destination_key], f"source clone {target_clone_key}")
    verify_clone = clones["compacted_to_verify_input"]
    compacted = results["compact"][1]["compacted_database"]
    verify_input = results["verify"][1]["verify_database"]
    for clone_key, source_key, destination_key in (
        ("source_bytes", "bytes", "bytes"),
        ("source_sha256_before_clone", "sha256", "sha256"),
    ):
        require_equal(verify_clone[clone_key], compacted[source_key], f"verify clone {clone_key}")
        target_clone_key = "destination_bytes" if clone_key == "source_bytes" else "destination_sha256"
        require_equal(verify_clone[target_clone_key], verify_input[destination_key], f"verify clone {target_clone_key}")
    role_phase = {
        "source_database": "source", "compact_input_database": "compact",
        "compacted_database": "compact", "verify_database": "verify",
    }
    for role in DATABASE_ROLES:
        ref = record["post_worker_quiescence"][role]
        q_path = resolve_bundle_path(root, ref["path"])
        phase = role_phase[role]
        q, _, digest = load_document(q_path, plan["bounds"]["max_quiescence_record_bytes"], "dao_m5_post_worker_quiescence")
        result, observations = results[phase]
        _validate_quiescence_loaded(root, q_path, q, result, observations)
        require_equal(digest, ref["sha256"], f"$.post_worker_quiescence.{role}.sha256")
        require_equal(q["database_role"], role, f"$.post_worker_quiescence.{role}.database_role")
    record["_validated_results"] = {
        phase: result for phase, (result, _) in results.items()
    }
    return record
