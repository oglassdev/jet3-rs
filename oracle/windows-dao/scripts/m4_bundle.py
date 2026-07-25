#!/usr/bin/env python3
"""Complete-tree, database, and analysis validation for DAO M4 bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m4_analysis import build_analysis, canonical_analysis_bytes
from m4_campaign import validate_campaign_bindings_and_chronology
from m4_phase import (
    PhaseBindings,
    validate_phase_bindings,
    validate_sample_record,
)
from m4_records import (
    CHECKED_PLAN,
    SCHEMA_SET,
    ValidationError,
    load_checked_plan,
    load_document,
    parse_timestamp,
    require_equal,
    resolve_bundle_path,
)
from m4_snapshot import (
    BundleSnapshot,
    discover_bundle,
    read_stable_database,
)
from protocol_validation import validate_environment
from validate_m1_protocol import SCHEMA_SET as ENVIRONMENT_SCHEMA_SET

ARTIFACT_ROLES = {
    "invocation": "phase_invocation",
    "operation_log": "operation_log",
    "snapshot": "semantic_snapshot",
    "worker_result": "phase_worker_result",
}


def find_checked_plan(root: Path) -> tuple[Path, dict[str, Any], str]:
    """Find the unique exact checked plan in a bounded stage tree."""
    _, checked_hash, _ = bounded_file_identity(CHECKED_PLAN, 1048576, retain=False)
    matches = []
    for locator in sorted(discover_bundle(root)):
        if not locator.endswith(".json"):
            continue
        path = resolve_bundle_path(root, locator)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValidationError(f"{path}: cannot inspect plan candidate: {exc}") from exc
        if size > 1048576:
            continue
        _, digest, _ = bounded_file_identity(path, 1048576, retain=False)
        if digest == checked_hash:
            matches.append(path)
    if len(matches) != 1:
        raise ValidationError(f"stage must contain one exact checked plan, found {len(matches)}")
    plan, digest = load_checked_plan(matches[0])
    return matches[0], plan, digest


def _unique_role(
    manifest_index: dict[str, dict[str, Any]], role: str
) -> tuple[str, dict[str, Any]]:
    selected = [(path, entry) for path, entry in manifest_index.items() if entry["role"] == role]
    if len(selected) != 1:
        raise ValidationError(f"$.files: expected one {role!r} artifact")
    return selected[0]


def load_document_v1_environment(
    path: Path,
    source: BundleSnapshot | None = None,
    locator: str | None = None,
) -> tuple[dict[str, Any], int, str]:
    if source is None:
        from m4_records import load_bounded_json

        document, size, digest = load_bounded_json(path, 1024 * 1024)
    else:
        if locator is None:
            raise ValidationError("snapshot environment locator is required")
        document, size, digest = source.json_document(locator)
        if size > 1024 * 1024:
            raise ValidationError(f"{locator}: environment exceeds byte ceiling")
    observed = ENVIRONMENT_SCHEMA_SET.validate(document)
    if observed != "dao_environment":
        raise ValidationError(f"{path}: expected dao_environment")
    validate_environment(document)
    if document["status"] != "ready" or document["accepted_provider"] is None:
        raise ValidationError(f"{path}: M4 requires a ready accepted DAO provider")
    return document, size, digest


def _expected_manifest_bindings(
    records: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, str]:
    expected: dict[str, str] = {}

    def add(path: str, role: str) -> None:
        previous = expected.setdefault(path, role)
        if previous != role:
            raise ValidationError(f"{path}: conflicting expected artifact roles")

    for sample, record in zip(samples, records):
        add(sample["record_path"], "sample_record")
        add(sample["creator_database_path"], "database")
        add(sample["reopen_database_path"], "database")
        for phase in ("creator", "reopen"):
            phase_row = record["phases"][phase]
            for name, ref in phase_row["artifacts"].items():
                add(ref["path"], ARTIFACT_ROLES[name])
            add(phase_row["post_close_file_observations"]["prefix_path"], "prefix")
        add(record["controller_clone"]["clone_log"]["path"], "clone_log")
    return expected


def _validate_databases_and_prefixes(
    root: Path,
    plan: dict[str, Any],
    manifest_index: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    source: BundleSnapshot | None = None,
) -> dict[str, bytes]:
    bounds = plan["bounds"]
    observations: dict[
        str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
    ] = {}
    for record in records:
        for phase in ("creator", "reopen"):
            phase_row = record["phases"][phase]
            post = phase_row["post_close_file_observations"]
            database_path = post["database_path"]
            if database_path in observations:
                raise ValidationError(f"{database_path}: database path is reused")
            observations[database_path] = (post, phase_row, record)
    if len(observations) != bounds["max_database_artifacts"]:
        raise ValidationError("database inventory is not complete")
    total_bytes = 0
    prefixes: dict[str, bytes] = {}
    reads = 0
    for database_path, (post, phase_row, record) in observations.items():
        entry = manifest_index.get(database_path)
        if entry is None or entry["role"] != "database":
            raise ValidationError(f"{database_path}: missing database manifest entry")
        if source is None:
            size, digest, prefix = read_stable_database(
                resolve_bundle_path(root, database_path),
                bounds["max_database_bytes"],
            )
        else:
            size, digest, prefix = source.database_projection(database_path)
        reads += 1
        total_bytes += size
        require_equal(size, entry["size_bytes"], f"{database_path} manifest size")
        require_equal(digest, entry["sha256"], f"{database_path} manifest sha256")
        require_equal(size, post["database_bytes"], f"{database_path} record size")
        require_equal(digest, post["database_sha256"], f"{database_path} record sha256")
        prefix_path = post["prefix_path"]
        prefix_entry = manifest_index.get(prefix_path)
        if prefix_entry is None or prefix_entry["role"] != "prefix":
            raise ValidationError(f"{prefix_path}: missing prefix manifest entry")
        if source is None:
            prefix_file = resolve_bundle_path(root, prefix_path)
            prefix_size, prefix_hash, retained = bounded_file_identity(
                prefix_file, 2048, retain=True
            )
            assert retained is not None
        else:
            retained = source.binary_payload(prefix_path, "prefix")
            prefix_size, prefix_hash = source.file_identity(prefix_path)
        require_equal(prefix_size, 2048, f"{prefix_path} size")
        require_equal(retained, prefix, f"{prefix_path} database projection")
        require_equal(prefix_hash, post["prefix_sha256"], f"{prefix_path} record sha256")
        prefixes[prefix_path] = retained
        if phase_row["phase_id"] == "reopen":
            pre = phase_row["pre_com_file_binding"]
            clone = record["controller_clone"]
            require_equal(pre["database_path"], clone["destination_path"], f"{database_path} pre-COM clone path")
            require_equal(pre["database_bytes"], clone["destination_bytes"], f"{database_path} pre-COM clone size")
            require_equal(pre["database_sha256"], clone["destination_sha256"], f"{database_path} pre-COM clone hash")
    require_equal(reads, bounds["max_validator_database_reads_per_run"], "validator database reads")
    if total_bytes > bounds["max_validator_database_read_bytes_per_run"]:
        raise ValidationError("validator database read-byte ceiling exceeded")
    require_equal(len(prefixes), bounds["max_prefix_artifacts"], "prefix artifact count")
    require_equal(sum(len(value) for value in prefixes.values()), bounds["max_total_prefix_bytes"], "total prefix bytes")
    return prefixes


def build_full_analysis(
    plan: dict[str, Any],
    plan_sha256: str,
    samples: list[dict[str, Any]],
    records: list[dict[str, Any]],
    record_hashes: dict[str, str],
    prefixes: dict[str, bytes],
) -> dict[str, Any]:
    result = {
        "protocol_version": "1.0.0",
        "document_type": "dao_m4_analysis_report",
        "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
        "plan_sha256": plan_sha256,
        "sample_records": [
            {
                "sample_id": sample["sample_id"],
                "record_path": sample["record_path"],
                "record_sha256": record_hashes[sample["record_path"]],
            }
            for sample in samples
        ],
    }
    result.update(build_analysis(plan, records, prefixes))
    SCHEMA_SET.validate(result)
    if len(canonical_analysis_bytes(result)) > plan["bounds"]["max_analysis_report_bytes"]:
        raise ValidationError("analysis report exceeds checked byte ceiling")
    return result


def load_stage_records(
    root: Path,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, str]]:
    """Load and relationally validate all records in a complete pre-manifest stage."""
    SCHEMA_SET.lint()
    _, plan, plan_sha256 = find_checked_plan(root)
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    records = []
    hashes: dict[str, str] = {}
    for sample in plan["samples"]:
        record, _, digest = load_document(
            resolve_bundle_path(root, sample["record_path"]),
            plan["bounds"]["max_sample_record_bytes"],
            "dao_m4_sample_record",
        )
        validate_sample_record(
            root,
            record,
            sample,
            conditions[sample["condition_id"]],
            plan,
            plan_sha256,
        )
        records.append(record)
        hashes[sample["record_path"]] = digest
    workers = [
        record["phases"][phase]["worker"]
        for record in records
        for phase in ("creator", "reopen")
    ]
    for label, values in (
        (
            "identity",
            {
                (
                    row["process_id"],
                    parse_timestamp(row["started_at_utc"], "$.worker.started_at_utc"),
                )
                for row in workers
            },
        ),
        ("nonce", {row["nonce"] for row in workers}),
        ("run ID", {row["worker_run_id"] for row in workers}),
    ):
        if len(values) != 72:
            raise ValidationError(f"global worker {label} values are not unique")
    require_equal(
        {row["worker_ordinal"] for row in workers},
        set(range(1, 73)),
        "global worker ordinals",
    )
    validate_campaign_bindings_and_chronology(root, plan, records)
    return plan, plan_sha256, records, hashes


def build_analysis_from_stage(root: Path) -> dict[str, Any]:
    """Recompute analysis from a complete evidence stage before manifest creation."""
    plan, plan_sha256, records, hashes = load_stage_records(root)
    synthetic_index: dict[str, dict[str, Any]] = {}
    for record in records:
        for phase in ("creator", "reopen"):
            post = record["phases"][phase]["post_close_file_observations"]
            synthetic_index[post["database_path"]] = {
                "role": "database",
                "size_bytes": post["database_bytes"],
                "sha256": post["database_sha256"],
            }
            synthetic_index[post["prefix_path"]] = {"role": "prefix"}
    prefixes = _validate_databases_and_prefixes(root, plan, synthetic_index, records)
    return build_full_analysis(
        plan, plan_sha256, plan["samples"], records, hashes, prefixes
    )


def validate_one_sample(root: Path, record_path: Path) -> dict[str, Any]:
    """Validate a completed sample against the checked plan before publication."""
    SCHEMA_SET.lint()
    _, plan, plan_sha256 = find_checked_plan(root)
    record, _, _ = load_document(
        record_path,
        plan["bounds"]["max_sample_record_bytes"],
        "dao_m4_sample_record",
    )
    samples = {row["sample_id"]: row for row in plan["samples"]}
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    sample = samples.get(record["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: not in checked plan")
    expected_path = resolve_bundle_path(root, sample["record_path"])
    require_equal(record_path.resolve(strict=True), expected_path, "sample record path")
    validate_sample_record(
        root,
        record,
        sample,
        conditions[sample["condition_id"]],
        plan,
        plan_sha256,
    )
    return record


def _find_document_by_hash(
    root: Path, expected_hash: str, expected_type: str
) -> tuple[str, dict[str, Any]]:
    matches: list[tuple[str, dict[str, Any]]] = []
    for locator in sorted(discover_bundle(root)):
        if not locator.endswith(".json"):
            continue
        path = resolve_bundle_path(root, locator)
        if path.stat().st_size > 65536:
            continue
        _, digest, _ = bounded_file_identity(path, 65536, retain=False)
        if digest != expected_hash:
            continue
        document, _, _ = load_document(path, 65536, expected_type)
        matches.append((locator, document))
    if len(matches) != 1:
        raise ValidationError(
            f"expected one {expected_type} with hash {expected_hash}, found {len(matches)}"
        )
    return matches[0]


def validate_worker_result(root: Path, result_path: Path) -> dict[str, Any]:
    """Validate one just-completed worker result before controller use."""
    SCHEMA_SET.lint()
    _, plan, plan_sha256 = find_checked_plan(root)
    resolved_result = result_path.resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    if root_resolved not in resolved_result.parents:
        raise ValidationError("worker result is outside the bundle stage")
    result_locator = resolved_result.relative_to(root_resolved).as_posix()
    result, _, result_hash = load_document(
        resolved_result, 65536, "dao_m4_worker_result"
    )
    invocation_locator, invocation = _find_document_by_hash(
        root, result["invocation_sha256"], "dao_m4_invocation"
    )
    samples = {row["sample_id"]: row for row in plan["samples"]}
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    sample = samples.get(result["sample_id"])
    if sample is None:
        raise ValidationError("$.sample_id: not in checked plan")
    phase = result["phase_id"]
    condition = conditions[sample["condition_id"]]
    post = result["post_close_file_observations"]
    snapshot = result["snapshot"]
    log = result["operation_log"]
    worker = {
        "process_id": result["process_id"],
        "started_at_utc": result["started_at_utc"],
        "worker_run_id": result["worker_run_id"],
        "worker_ordinal": result["worker_ordinal"],
        "nonce": result["nonce"],
        "architecture": result["architecture"],
        "provider": result["provider"],
        "fresh_process": True,
        "bindings_verified_before_com": result["bindings_verified_before_com"],
    }
    snapshot_document, _, _ = load_document(
        resolve_bundle_path(root, snapshot["path"]),
        65536,
        "dao_m4_empty_schema_version_snapshot",
    )
    phase_row: dict[str, Any] = {
        "phase_id": phase,
        "phase_ordinal": result["phase_ordinal"],
        "worker": worker,
        "artifacts": {
            "invocation": {
                "path": invocation_locator,
                "sha256": result["invocation_sha256"],
            },
            "operation_log": log,
            "snapshot": snapshot,
            "worker_result": {
                "path": result_locator,
                "sha256": result_hash,
            },
        },
        "dao_observations_while_open": {
            "captured_while_database_open": snapshot_document[
                "captured_while_database_open"
            ],
            "dao_version": snapshot_document["dao_version"],
            "empty_user_schema": snapshot_document["empty_user_schema"],
            "user_table_count": snapshot_document["user_table_count"],
        },
        "post_close_file_observations": {
            "database_path": post["database_path"],
            "database_bytes": post["database_bytes"],
            "database_sha256": post["database_sha256"],
            "prefix_path": post["prefix"]["path"],
            "prefix_bytes": post["prefix_bytes"],
            "prefix_sha256": post["prefix"]["sha256"],
            "database_closed": True,
            "lock_file_absent_after_close": post["lock_file_absent_after_close"],
        },
        "status": result["execution_status"],
    }
    if phase == "reopen":
        pre = result["pre_com_file_binding"]
        if pre is None:
            raise ValidationError("$.pre_com_file_binding: reopen requires a binding")
        phase_row["pre_com_file_binding"] = pre | {"verified_before_com": True}
    clone_log = (
        invocation["phase_contract"]["clone_log"] if phase == "reopen" else None
    )
    validate_phase_bindings(
        root,
        PhaseBindings(
            producer_commit=invocation["producer_commit"],
            environment_sha256=invocation["environment_sha256"],
            provider_sha256=invocation["provider_sha256"],
            phase_row=phase_row,
            clone_log=clone_log,
        ),
        sample,
        condition,
        phase,
        plan,
        plan_sha256,
    )
    environment, _, environment_hash = load_document_v1_environment(
        resolve_bundle_path(root, invocation["environment_path"])
    )
    accepted = environment["accepted_provider"]
    assert accepted is not None
    require_equal(environment_hash, invocation["environment_sha256"], "$.environment_sha256")
    require_equal(result["provider"]["server_sha256"], accepted["server_sha256"], "$.provider.server_sha256")
    require_equal(result["provider"]["prog_id"], accepted["prog_id"], "$.provider.prog_id")
    require_equal(result["provider"]["clsid"].upper(), accepted["clsid"].upper(), "$.provider.clsid")
    size, digest, prefix = read_stable_database(
        resolve_bundle_path(root, post["database_path"]),
        plan["bounds"]["max_database_bytes"],
    )
    require_equal(size, post["database_bytes"], "$.post_close_file_observations.database_bytes")
    require_equal(digest, post["database_sha256"], "$.post_close_file_observations.database_sha256")
    prefix_size, prefix_hash, retained = bounded_file_identity(
        resolve_bundle_path(root, post["prefix"]["path"]), 2048, retain=True
    )
    require_equal(prefix_size, 2048, "$.post_close_file_observations.prefix_bytes")
    require_equal(prefix_hash, post["prefix"]["sha256"], "$.post_close_file_observations.prefix.sha256")
    require_equal(retained, prefix, "$.post_close_file_observations.prefix database projection")
    return result


def validate_bundle(root: Path) -> dict[str, Any]:
    """Validate one exact immutable snapshot of the complete M4 bundle."""
    SCHEMA_SET.lint()
    snapshot = BundleSnapshot.capture(root)
    manifest = snapshot.manifest
    manifest_index = snapshot.manifest_index
    plan_path, plan_entry = _unique_role(manifest_index, "plan")
    checked_plan, checked_hash = load_checked_plan()
    plan, _, plan_sha256 = snapshot.load_document(
        plan_path, 1048576, "dao_m4_plan"
    )
    require_equal(plan, checked_plan, "$.files checked plan document")
    require_equal(plan_sha256, checked_hash, "$.files checked plan sha256")
    require_equal(plan_entry["sha256"], plan_sha256, "$.files plan sha256")
    environment_path, environment_entry = _unique_role(manifest_index, "environment")
    environment, _, environment_hash = load_document_v1_environment(
        snapshot.root, snapshot, environment_path
    )
    require_equal(environment_hash, environment_entry["sha256"], "$.files environment sha256")
    accepted = environment["accepted_provider"]
    assert accepted is not None
    samples = plan["samples"]
    records = []
    record_hashes: dict[str, str] = {}
    for sample in samples:
        path = sample["record_path"]
        entry = manifest_index.get(path)
        if entry is None or entry["role"] != "sample_record":
            raise ValidationError(f"{path}: missing sample-record manifest entry")
        record, _, digest = snapshot.load_document(
            path,
            plan["bounds"]["max_sample_record_bytes"],
            "dao_m4_sample_record",
        )
        require_equal(digest, entry["sha256"], f"{path} manifest sha256")
        records.append(record)
        record_hashes[path] = digest
    samples_by_id = {sample["sample_id"]: sample for sample in samples}
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    validated_phases: dict[str, dict[str, Any]] = {}
    for record in records:
        sample = samples_by_id[record["sample_id"]]
        validated_phases[record["sample_id"]] = validate_sample_record(
            snapshot.root,
            record,
            sample,
            conditions[sample["condition_id"]],
            plan,
            plan_sha256,
            source=snapshot,
        )
        require_equal(record["producer_commit"], manifest["producer_commit"], f"{record['sample_id']} producer_commit")
        require_equal(record["environment_sha256"], environment_hash, f"{record['sample_id']} environment_sha256")
        require_equal(record["provider_sha256"], accepted["server_sha256"], f"{record['sample_id']} provider_sha256")
        for phase in ("creator", "reopen"):
            worker = record["phases"][phase]["worker"]
            require_equal(worker["provider"]["server_sha256"], accepted["server_sha256"], f"{record['sample_id']} provider hash")
            require_equal(worker["provider"]["prog_id"], accepted["prog_id"], f"{record['sample_id']} provider prog_id")
            require_equal(worker["provider"]["clsid"].upper(), accepted["clsid"].upper(), f"{record['sample_id']} provider clsid")
            require_equal(worker["provider"]["powershell_version"], environment["runtime"]["powershell_version"], f"{record['sample_id']} PowerShell version")
            invocation = validated_phases[record["sample_id"]][phase].invocation
            require_equal(invocation["campaign_run_id"], manifest["run_id"], f"{record['sample_id']} campaign_run_id")
            require_equal(invocation["environment_path"], environment_path, f"{record['sample_id']} environment_path")
            require_equal(invocation["plan_path"], plan_path, f"{record['sample_id']} plan_path")
    validate_campaign_bindings_and_chronology(
        snapshot.root, plan, records, manifest, source=snapshot
    )
    expected_refs = _expected_manifest_bindings(records, samples)
    structural = {
        path: entry["role"]
        for path, entry in manifest_index.items()
        if entry["role"] not in ("plan", "environment", "analysis_report")
    }
    require_equal(structural, expected_refs, "$.files referenced evidence closure")
    workers = [
        record["phases"][phase]["worker"]
        for record in records
        for phase in ("creator", "reopen")
    ]
    identities = {
        (
            worker["process_id"],
            parse_timestamp(worker["started_at_utc"], "$.worker.started_at_utc"),
        )
        for worker in workers
    }
    nonces = {worker["nonce"] for worker in workers}
    run_ids = {worker["worker_run_id"] for worker in workers}
    ordinals = {worker["worker_ordinal"] for worker in workers}
    if len(identities) != 72 or len(nonces) != 72 or len(run_ids) != 72:
        raise ValidationError("global worker identities, run IDs, or nonces are not unique")
    require_equal(ordinals, set(range(1, 73)), "global worker ordinals")
    prefixes = _validate_databases_and_prefixes(
        snapshot.root, plan, manifest_index, records, snapshot
    )
    expected_analysis = build_full_analysis(
        plan, plan_sha256, samples, records, record_hashes, prefixes
    )
    analysis_path, _ = _unique_role(manifest_index, "analysis_report")
    _analysis, _, _ = snapshot.load_document(
        analysis_path,
        plan["bounds"]["max_analysis_report_bytes"],
        "dao_m4_analysis_report",
    )
    retained_analysis = snapshot.binary_payload(analysis_path, "analysis_report")
    require_equal(
        retained_analysis,
        canonical_analysis_bytes(expected_analysis),
        "$.analysis canonical retained bytes",
    )
    snapshot.recheck()
    return {
        "manifest": manifest,
        "plan": plan,
        "records": records,
        "prefixes": prefixes,
        "analysis": expected_analysis,
    }
