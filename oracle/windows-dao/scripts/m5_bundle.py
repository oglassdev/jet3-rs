#!/usr/bin/env python3
"""Complete manifest closure and exact analysis validation for M5R4 bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m5_analysis import build_analysis, canonical_analysis_bytes, load_validated_m4
from m5_phase import EXPECTED_ACTIONS, validate_sample_record
from m5_records import SCHEMA_SET, _phase_contract, load_checked_plan, parse_timestamp, resolve_bundle_path
from m5_snapshot import BundleSnapshot
from m5_spec import DATABASE_ROLES, M4_MANIFEST_SHA256, PHASES, PHASE_DATABASE_ROLES, PREFIX_BYTES, require_equal
from protocol_validation import ValidationError, validate_environment
from validate_m1_protocol import SCHEMA_SET as ENVIRONMENT_SCHEMA_SET

ROLE_BY_ARTIFACT = {
    "invocation": "phase_invocation", "operation_log": "operation_log",
    "snapshot": "semantic_snapshot", "worker_result": "phase_worker_result",
}


def _unique_role(index: dict[str, dict[str, Any]], role: str) -> tuple[str, dict[str, Any]]:
    selected = [(path, row) for path, row in index.items() if row["role"] == role]
    if len(selected) != 1:
        raise ValidationError(f"$.files: expected one {role!r} artifact")
    return selected[0]


def _load_environment(snapshot: BundleSnapshot, locator: str) -> tuple[dict[str, Any], str]:
    artifact = snapshot.artifact(locator)
    if artifact.document is None or artifact.size > 1048576:
        raise ValidationError(f"{locator}: expected bounded environment JSON")
    observed = ENVIRONMENT_SCHEMA_SET.validate(artifact.document)
    if observed != "dao_environment":
        raise ValidationError(f"{locator}: expected dao_environment")
    validate_environment(artifact.document)
    if artifact.document["status"] != "ready" or artifact.document["accepted_provider"] is None:
        raise ValidationError("M5 requires a ready accepted DAO provider")
    return artifact.document, artifact.sha256


def _expected_paths(sample: dict[str, Any], record: dict[str, Any], result_docs: dict[str, dict[str, Any]], quiescence_docs: dict[str, dict[str, Any]]) -> dict[str, str]:
    expected: dict[str, str] = {sample["record_path"]: "sample_record"}
    for key in ("source_database_path", "compact_input_database_path", "compacted_database_path", "verify_database_path"):
        expected[sample[key]] = "database"
    for phase in PHASES:
        result = result_docs[phase]
        expected[f"evidence/samples/{sample['sample_id']}/{phase}-invocation.json"] = "phase_invocation"
        expected[result["operation_log"]["path"]] = "operation_log"
        if result["snapshot"] is not None:
            expected[result["snapshot"]["path"]] = "semantic_snapshot"
        expected[record["phases"][phase]["worker_result"]["path"]] = "phase_worker_result"
        for observation in result["database_observations"]:
            if observation["prefix"] is not None:
                expected[observation["prefix"]["path"]] = "prefix"
    for ref in record["controller_clones"]:
        expected[ref["path"]] = "clone_log"
    for role in DATABASE_ROLES:
        ref = record["post_worker_quiescence"][role]
        expected[ref["path"]] = "post_worker_quiescence"
        companion = quiescence_docs[role]["companion"]
        if companion["state"] == "present":
            expected[companion["path"]] = "companion"
    return expected


def _validate_snapshot_sample(
    snapshot: BundleSnapshot,
    plan: dict[str, Any],
    plan_hash: str,
    sample: dict[str, Any],
    environment: dict[str, Any],
    environment_hash: str,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes], dict[str, str]]:
    record, _, record_hash = snapshot.load_document(sample["record_path"], plan["bounds"]["max_sample_record_bytes"], "dao_m5_sample_record")
    condition = next(row for row in plan["conditions"] if row["condition_id"] == sample["condition_id"])
    for key in ("sample_id", "condition_id", "replica", "block", "position_in_block", "launch_ordinal"):
        require_equal(record[key], sample[key], f"{sample['sample_id']}.{key}")
    require_equal(record["plan_sha256"], plan_hash, f"{sample['sample_id']}.plan_sha256")
    accepted = environment["accepted_provider"]
    assert accepted is not None
    provider_hash = accepted["server_sha256"]
    require_equal(record["producer_commit"], manifest["producer_commit"], f"{sample['sample_id']}.producer_commit")
    require_equal(record["environment_sha256"], environment_hash, f"{sample['sample_id']}.environment_sha256")
    require_equal(record["provider_sha256"], provider_hash, f"{sample['sample_id']}.provider_sha256")
    require_equal(record["m4_manifest_sha256"], M4_MANIFEST_SHA256, f"{sample['sample_id']}.m4_manifest_sha256")
    result_docs: dict[str, dict[str, Any]] = {}
    prefixes: dict[str, bytes] = {}
    worker_observations: dict[str, dict[str, dict[str, Any]]] = {}
    for phase in PHASES:
        result_ref = record["phases"][phase]["worker_result"]
        result, _, digest = snapshot.load_document(result_ref["path"], 65536, "dao_m5_worker_result")
        require_equal(digest, result_ref["sha256"], f"{sample['sample_id']}.{phase} result sha256")
        invocation_locator = f"evidence/samples/{sample['sample_id']}/{phase}-invocation.json"
        invocation, _, invocation_hash = snapshot.load_document(invocation_locator, 65536, "dao_m5_invocation")
        require_equal(invocation_hash, result["invocation_sha256"], f"{sample['sample_id']}.{phase} invocation sha256")
        require_equal(invocation["sample_id"], sample["sample_id"], "invocation sample_id")
        require_equal(invocation["condition_id"], sample["condition_id"], "invocation condition_id")
        require_equal(invocation["phase_id"], phase, "invocation phase_id")
        phase_ordinal = PHASES.index(phase) + 1
        worker_ordinal = 3 * sample["launch_ordinal"] - (3 - phase_ordinal)
        require_equal(invocation["phase_ordinal"], phase_ordinal, "invocation phase_ordinal")
        require_equal(invocation["worker_ordinal"], worker_ordinal, "invocation worker_ordinal")
        require_equal(invocation["worker_run_id"], f"{sample['sample_id']}-{phase.upper()}", "invocation worker_run_id")
        require_equal(invocation["campaign_run_id"], manifest["run_id"], "invocation campaign run")
        require_equal(invocation["producer_commit"], manifest["producer_commit"], "invocation producer commit")
        require_equal(invocation["plan_sha256"], plan_hash, "invocation plan_sha256")
        require_equal(invocation["environment_sha256"], environment_hash, "invocation environment sha256")
        require_equal(invocation["provider_sha256"], provider_hash, "invocation provider sha256")
        require_equal(invocation["plan_path"], "plan.json", "invocation plan path")
        require_equal(invocation["environment_path"], "environment.json", "invocation environment path")
        require_equal(invocation["result_path"], result_ref["path"], "invocation result path")
        require_equal(invocation["phase_contract"], _phase_contract(condition, phase), "invocation phase contract")
        expected_database_paths = {
            role: sample[f"{role[:-9]}_database_path"] if role != "compacted_database" else sample["compacted_database_path"]
            for role in PHASE_DATABASE_ROLES[phase]
        }
        require_equal(invocation["database_paths"], expected_database_paths, "invocation database paths")
        require_equal(invocation["m4_input"]["bundle_manifest_sha256"], M4_MANIFEST_SHA256, "invocation M4 binding")
        require_equal(result["sample_id"], sample["sample_id"], "result sample_id")
        require_equal(result["phase_id"], phase, "result phase_id")
        require_equal(result["phase_ordinal"], phase_ordinal, "result phase_ordinal")
        require_equal(result["worker_run_id"], invocation["worker_run_id"], "result worker_run_id")
        require_equal(result["worker_ordinal"], invocation["worker_ordinal"], "result worker_ordinal")
        require_equal(result["nonce"], invocation["nonce"], "result nonce")
        require_equal(result["provider"]["server_sha256"], provider_hash, "result provider hash")
        require_equal(result["provider"]["prog_id"], accepted["prog_id"], "result provider prog_id")
        require_equal(result["provider"]["clsid"].upper(), accepted["clsid"].upper(), "result provider clsid")
        require_equal(result["provider"]["powershell_version"], environment["runtime"]["powershell_version"], "result PowerShell version")
        operation = result["operation_log"]
        operation_document, _, operation_hash = snapshot.load_document(operation["path"], 65536, "dao_m5_operation_log")
        require_equal(operation_hash, operation["sha256"], "operation sha256")
        require_equal(operation_document["sample_id"], sample["sample_id"], "operation sample_id")
        require_equal(operation_document["phase_id"], phase, "operation phase_id")
        require_equal(operation_document["worker_run_id"], result["worker_run_id"], "operation worker_run_id")
        require_equal(operation_document["actions"], EXPECTED_ACTIONS[phase], "operation actions")
        if result["snapshot"] is not None:
            ref = result["snapshot"]
            snapshot_document, _, digest = snapshot.load_document(ref["path"], 65536, "dao_m5_snapshot")
            require_equal(digest, ref["sha256"], "snapshot sha256")
            expected_version = condition["expected_source_dao_version"] if phase == "source" else condition["expected_destination_dao_version"]
            require_equal(snapshot_document["sample_id"], sample["sample_id"], "snapshot sample_id")
            require_equal(snapshot_document["phase_id"], phase, "snapshot phase_id")
            require_equal(snapshot_document["dao_version"], expected_version, "snapshot dao_version")
        elif phase != "compact":
            raise ValidationError(f"{sample['sample_id']}.{phase}: snapshot is required")
        if phase == "compact" and result["snapshot"] is not None:
            raise ValidationError(f"{sample['sample_id']}.compact: snapshot is forbidden")
        indexed: dict[str, dict[str, Any]] = {}
        for observation in result["database_observations"]:
            role = observation["database_role"]
            if role in indexed:
                raise ValidationError(f"{sample['sample_id']}.{phase}: duplicate database role")
            indexed[role] = observation
            size, database_hash, database_prefix = snapshot.database_projection(observation["path"])
            require_equal(size, observation["bytes"], f"{role} bytes")
            require_equal(database_hash, observation["sha256"], f"{role} sha256")
            require_equal(hashlib.sha256(database_prefix).hexdigest(), observation["prefix_sha256"], f"{role} prefix sha256")
            if observation["prefix"] is not None:
                ref = observation["prefix"]
                retained = snapshot.binary_payload(ref["path"], "prefix")
                require_equal(retained, database_prefix, f"{role} retained prefix")
                require_equal(hashlib.sha256(retained).hexdigest(), ref["sha256"], f"{role} retained prefix sha256")
                prefixes[ref["path"]] = retained
        worker_observations[phase] = indexed
        require_equal(tuple(indexed), PHASE_DATABASE_ROLES[phase], f"{sample['sample_id']}.{phase} database roles")
        result_docs[phase] = result
    quiescence_docs: dict[str, dict[str, Any]] = {}
    role_phase = {"source_database": "source", "compact_input_database": "compact", "compacted_database": "compact", "verify_database": "verify"}
    for role in DATABASE_ROLES:
        ref = record["post_worker_quiescence"][role]
        document, _, digest = snapshot.load_document(ref["path"], plan["bounds"]["max_quiescence_record_bytes"], "dao_m5_post_worker_quiescence")
        require_equal(ref["path"], f"evidence/quiescence/{sample['sample_id']}/{role}.json", f"{role} quiescence locator")
        require_equal(digest, ref["sha256"], f"{role} quiescence sha256")
        phase = role_phase[role]
        result = result_docs[phase]
        require_equal(document["sample_id"], sample["sample_id"], f"{role} quiescence sample")
        require_equal(document["phase_id"], phase, f"{role} quiescence phase")
        require_equal(document["database_role"], role, f"{role} quiescence role")
        if not parse_timestamp(result["finished_at_utc"], "result.finished_at_utc") <= parse_timestamp(document["observation_started_at_utc"], "quiescence.observation_started_at_utc") <= parse_timestamp(document["observation_completed_at_utc"], "quiescence.observation_completed_at_utc"):
            raise ValidationError(f"{role}: quiescence chronology differs")
        observation = worker_observations[phase][role]
        for key in ("path", "bytes", "sha256", "prefix_sha256"):
            require_equal(document["database"][key], observation[key], f"{role} quiescence {key}")
        expected_companion = observation["path"][:-4] + ".ldb"
        require_equal(document["companion"]["path"], expected_companion, f"{role} companion path")
        if document["companion"]["state"] == "present":
            companion = document["companion"]
            payload = snapshot.binary_payload(companion["path"], "companion")
            require_equal(len(payload), companion["bytes"], f"{role} companion bytes")
            require_equal(hashlib.sha256(payload).hexdigest(), companion["sha256"], f"{role} companion sha256")
        elif expected_companion in snapshot.manifest_index:
            raise ValidationError(f"{role}: absent companion is manifest-bound")
        quiescence_docs[role] = document
    clone_ids = ("source_to_compact_input", "compacted_to_verify_input")
    expected_clone_paths = ((sample["source_database_path"], sample["compact_input_database_path"]), (sample["compacted_database_path"], sample["verify_database_path"]))
    clones: dict[str, dict[str, Any]] = {}
    for ref, clone_id, paths in zip(record["controller_clones"], clone_ids, expected_clone_paths):
        clone, _, digest = snapshot.load_document(ref["path"], 65536, "dao_m5_clone_log")
        require_equal(digest, ref["sha256"], f"{clone_id} sha256")
        require_equal(clone["clone_id"], clone_id, f"{clone_id} id")
        require_equal((clone["source_path"], clone["destination_path"]), paths, f"{clone_id} paths")
        require_equal(clone["source_sha256_before_clone"], clone["source_sha256_after_clone"], f"{clone_id} source hash")
        require_equal(clone["source_sha256_before_clone"], clone["destination_sha256"], f"{clone_id} destination hash")
        clones[clone_id] = clone
    for clone_id, source_role, destination_role, source_phase, destination_phase in (
        ("source_to_compact_input", "source_database", "compact_input_database", "source", "compact"),
        ("compacted_to_verify_input", "compacted_database", "verify_database", "compact", "verify"),
    ):
        clone = clones[clone_id]
        source_observation = worker_observations[source_phase][source_role]
        destination_observation = worker_observations[destination_phase][destination_role]
        require_equal(clone["source_bytes"], source_observation["bytes"], f"{clone_id} source bytes")
        require_equal(clone["source_sha256_before_clone"], source_observation["sha256"], f"{clone_id} source sha256")
        require_equal(clone["destination_bytes"], destination_observation["bytes"], f"{clone_id} destination bytes")
        require_equal(clone["destination_sha256"], destination_observation["sha256"], f"{clone_id} destination sha256")
    record["_results"] = result_docs
    return record, result_docs, prefixes, _expected_paths(sample, record, result_docs, quiescence_docs) | {sample["record_path"]: "sample_record", "_record_hash": record_hash}


def build_full_analysis(plan: dict[str, Any], plan_hash: str, records: list[dict[str, Any]], record_hashes: dict[str, str], prefixes: dict[str, bytes], validated_m4: dict[str, Any]) -> dict[str, Any]:
    result = {
        "protocol_version": "1.0.0", "document_type": "dao_m5_analysis_report",
        "experiment_id": "DAO-M5-COMPACT-CONFIRM-004", "plan_sha256": plan_hash,
        "m4_binding": {"bundle_manifest_sha256": M4_MANIFEST_SHA256, "producer_commit": "35f5f55f0b7277fc07831db540eab7fa69a41a20", "campaign_run_id": "20260810T220332Z-m4-r2"},
        "sample_records": [{"sample_id": sample["sample_id"], "record_path": sample["record_path"], "record_sha256": record_hashes[sample["record_path"]]} for sample in plan["samples"]],
    }
    result.update(build_analysis(plan, records, prefixes, validated_m4))
    SCHEMA_SET.validate(result)
    return result


def validate_bundle(root: Path, m4_root: Path) -> dict[str, Any]:
    """Validate one immutable complete bundle and independently recompute M5."""
    SCHEMA_SET.lint()
    validated_m4 = load_validated_m4(m4_root)
    snapshot = BundleSnapshot.capture(root)
    plan_path, plan_entry = _unique_role(snapshot.manifest_index, "plan")
    require_equal(plan_path, "plan.json", "plan locator")
    checked_plan, checked_hash = load_checked_plan()
    plan, _, plan_hash = snapshot.load_document(plan_path, 1048576, "dao_m5_plan")
    require_equal(plan, checked_plan, "retained plan")
    require_equal(plan_hash, checked_hash, "retained plan sha256")
    require_equal(plan_entry["sha256"], plan_hash, "manifest plan sha256")
    environment_path, _ = _unique_role(snapshot.manifest_index, "environment")
    require_equal(environment_path, "environment.json", "environment locator")
    environment, environment_hash = _load_environment(snapshot, environment_path)
    accepted = environment["accepted_provider"]
    assert accepted is not None
    records: list[dict[str, Any]] = []
    record_hashes: dict[str, str] = {}
    prefixes: dict[str, bytes] = {}
    expected_structural: dict[str, str] = {}
    for sample in plan["samples"]:
        record, _, sample_prefixes, expected = _validate_snapshot_sample(snapshot, plan, plan_hash, sample, environment, environment_hash, snapshot.manifest)
        record_hashes[sample["record_path"]] = expected.pop("_record_hash")
        for path, role in expected.items():
            if path in expected_structural:
                raise ValidationError(f"{path}: artifact path reused across samples")
            expected_structural[path] = role
        prefixes.update(sample_prefixes)
        records.append(record)
    workers = [record["_results"][phase] for record in records for phase in PHASES]
    if len({row["nonce"] for row in workers}) != 324 or len({row["worker_run_id"] for row in workers}) != 324 or {row["worker_ordinal"] for row in workers} != set(range(1, 325)):
        raise ValidationError("global worker nonces, run IDs, or ordinals are not unique/complete")
    identities = {(row["process_id"], parse_timestamp(row["started_at_utc"], "worker.started_at_utc")) for row in workers}
    if len(identities) != 324:
        raise ValidationError("global worker process/time identities are not unique")
    if len(prefixes) != 324 or sum(len(value) for value in prefixes.values()) != 324 * PREFIX_BYTES:
        raise ValidationError("retained prefix inventory is incomplete")
    structural = {path: row["role"] for path, row in snapshot.manifest_index.items() if row["role"] not in ("plan", "environment", "analysis_report")}
    require_equal(structural, expected_structural, "manifest referenced evidence closure")
    expected_analysis = build_full_analysis(plan, plan_hash, records, record_hashes, prefixes, validated_m4)
    analysis_path, _ = _unique_role(snapshot.manifest_index, "analysis_report")
    require_equal(analysis_path, "analysis.json", "analysis locator")
    retained = snapshot.binary_payload(analysis_path, "analysis_report")
    require_equal(retained, canonical_analysis_bytes(expected_analysis), "retained canonical analysis")
    require_equal(snapshot.manifest["m4_manifest_sha256"], M4_MANIFEST_SHA256, "manifest M4 binding")
    for record in records:
        record.pop("_results", None)
    snapshot.recheck()
    return {"manifest": snapshot.manifest, "plan": plan, "records": records, "prefixes": prefixes, "analysis": expected_analysis}


def build_analysis_from_stage(root: Path, m4_root: Path) -> dict[str, Any]:
    """Validate all pre-manifest samples and build the canonical report."""
    SCHEMA_SET.lint()
    plan, plan_hash = load_checked_plan(resolve_bundle_path(root, "plan.json"))
    validated_m4 = load_validated_m4(m4_root)
    records: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    prefixes: dict[str, bytes] = {}
    for sample in plan["samples"]:
        path = resolve_bundle_path(root, sample["record_path"])
        record = validate_sample_record(root, path)
        _, digest, _ = bounded_file_identity(path, plan["bounds"]["max_sample_record_bytes"], retain=False)
        hashes[sample["record_path"]] = digest
        results = record.pop("_validated_results")
        for phase, result in results.items():
            result.pop("_validated_result_hash", None)
            for observation in result["database_observations"]:
                if observation["prefix"] is not None:
                    prefix_ref = observation["prefix"]
                    size, prefix_hash, payload = bounded_file_identity(resolve_bundle_path(root, prefix_ref["path"]), PREFIX_BYTES, retain=True)
                    assert payload is not None
                    require_equal(size, PREFIX_BYTES, "prefix size")
                    require_equal(prefix_hash, prefix_ref["sha256"], "prefix sha256")
                    prefixes[prefix_ref["path"]] = payload
        record["_results"] = results
        records.append(record)
    return build_full_analysis(plan, plan_hash, records, hashes, prefixes, validated_m4)
