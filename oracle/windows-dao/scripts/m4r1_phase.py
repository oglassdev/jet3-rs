#!/usr/bin/env python3
"""Explicit phase and sample validation boundaries for DAO M4 evidence."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m4r1_records import (
    ArtifactSource,
    CREATOR_ACTIONS,
    REOPEN_ACTIONS,
    SCHEMA_SET,
    ValidationError,
    _creation_projection,
    load_artifact_document,
    load_document,
    parse_timestamp,
    require_equal,
    resolve_bundle_path,
    validate_invocation_document,
)


@dataclass(frozen=True)
class PhaseBindings:
    """Record-level bindings required to validate one completed phase."""

    producer_commit: str
    environment_sha256: str
    provider_sha256: str
    phase_row: dict[str, Any]
    clone_log: dict[str, str] | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any], phase: str) -> PhaseBindings:
        clone_log = None
        if phase == "reopen":
            clone_log = record["controller_clone"]["clone_log"]
        return cls(
            producer_commit=record["producer_commit"],
            environment_sha256=record["environment_sha256"],
            provider_sha256=record["provider_sha256"],
            phase_row=record["phases"][phase],
            clone_log=clone_log,
        )


@dataclass(frozen=True)
class ValidatedPhase:
    invocation: dict[str, Any]
    result: dict[str, Any]
    log: dict[str, Any]
    snapshot: dict[str, Any]
    quiescence: dict[str, Any] | None
    started: dt.datetime
    finished: dt.datetime
    quiesced: dt.datetime | None


def _validate_common_projection(
    document: dict[str, Any],
    sample: dict[str, Any],
    phase: str,
    worker_id: str,
) -> None:
    require_equal(document["sample_id"], sample["sample_id"], "$.sample_id")
    require_equal(document["phase_id"], phase, "$.phase_id")
    require_equal(
        document["phase_ordinal"],
        1 if phase == "creator" else 2,
        "$.phase_ordinal",
    )
    require_equal(
        document.get("worker_run_id", worker_id), worker_id, "$.worker_run_id"
    )


def validate_phase_bindings(
    bundle_root: Path,
    bindings: PhaseBindings,
    sample: dict[str, Any],
    condition: dict[str, Any],
    phase: str,
    plan: dict[str, Any],
    plan_sha256: str,
    *,
    source: ArtifactSource | None = None,
) -> ValidatedPhase:
    """Validate one phase from explicit, complete record-level bindings."""
    phase_row = bindings.phase_row
    artifacts = phase_row["artifacts"]
    loaded: dict[str, dict[str, Any]] = {}
    types = {
        "invocation": "dao_m4_invocation",
        "operation_log": "dao_m4_operation_log",
        "snapshot": "dao_m4_empty_schema_version_snapshot",
        "worker_result": "dao_m4_worker_result",
    }
    if "post_worker_quiescence" in artifacts:
        types["post_worker_quiescence"] = "dao_m4_post_worker_quiescence"
    for name, expected_type in types.items():
        ref = artifacts[name]
        document, _, digest = load_artifact_document(
            bundle_root, ref["path"], 65536, expected_type, source
        )
        require_equal(
            digest, ref["sha256"], f"$.phases.{phase}.artifacts.{name}.sha256"
        )
        loaded[name] = document
    invocation = loaded["invocation"]
    result = loaded["worker_result"]
    log = loaded["operation_log"]
    snapshot = loaded["snapshot"]
    quiescence = loaded.get("post_worker_quiescence")
    worker = phase_row["worker"]
    worker_id = f"{sample['sample_id']}-{phase.upper()}"
    validate_invocation_document(
        invocation,
        plan,
        plan_sha256,
        bundle_root,
        expected_path=artifacts["worker_result"]["path"],
        source=source,
    )
    require_equal(
        invocation["producer_commit"],
        bindings.producer_commit,
        "$.invocation.producer_commit",
    )
    require_equal(
        invocation["environment_sha256"],
        bindings.environment_sha256,
        "$.invocation.environment_sha256",
    )
    require_equal(
        invocation["provider_sha256"],
        bindings.provider_sha256,
        "$.invocation.provider_sha256",
    )
    for document in (result, log, snapshot):
        _validate_common_projection(document, sample, phase, worker_id)
    if quiescence is not None:
        _validate_common_projection(quiescence, sample, phase, worker_id)
    require_equal(invocation["nonce"], worker["nonce"], "$.invocation.nonce")
    require_equal(
        invocation["worker_ordinal"],
        worker["worker_ordinal"],
        "$.invocation.worker_ordinal",
    )
    require_equal(result["nonce"], worker["nonce"], "$.worker_result.nonce")
    require_equal(
        result["worker_ordinal"],
        worker["worker_ordinal"],
        "$.worker_result.worker_ordinal",
    )
    require_equal(
        result["process_id"], worker["process_id"], "$.worker_result.process_id"
    )
    require_equal(
        result["architecture"],
        worker["architecture"],
        "$.worker_result.architecture",
    )
    require_equal(
        result["provider"], worker["provider"], "$.worker_result.provider"
    )
    require_equal(
        worker["provider"]["server_sha256"],
        bindings.provider_sha256,
        "$.worker.provider.server_sha256",
    )
    require_equal(
        result["started_at_utc"],
        worker["started_at_utc"],
        "$.worker_result.started_at_utc",
    )
    require_equal(
        result["invocation_sha256"],
        artifacts["invocation"]["sha256"],
        "$.worker_result.invocation_sha256",
    )
    require_equal(
        result["operation_log"],
        artifacts["operation_log"],
        "$.worker_result.operation_log",
    )
    require_equal(
        result["snapshot"], artifacts["snapshot"], "$.worker_result.snapshot"
    )
    actions = [entry["action"] for entry in log["entries"]]
    require_equal(
        actions,
        CREATOR_ACTIONS if phase == "creator" else REOPEN_ACTIONS,
        "$.operation_log.entries actions",
    )
    require_equal(
        [entry["sequence"] for entry in log["entries"]],
        list(range(1, len(actions) + 1)),
        "$.operation_log.entries sequence",
    )
    times = [
        parse_timestamp(
            entry["timestamp_utc"], "$.operation_log.entries[].timestamp_utc"
        )
        for entry in log["entries"]
    ]
    if times != sorted(times):
        raise ValidationError("$.operation_log.entries: timestamps are out of order")
    started = parse_timestamp(
        result["started_at_utc"], "$.worker_result.started_at_utc"
    )
    finished = parse_timestamp(
        result["finished_at_utc"], "$.worker_result.finished_at_utc"
    )
    if finished - started > dt.timedelta(
        seconds=plan["bounds"]["worker_timeout_seconds"]
    ):
        raise ValidationError(
            f"{sample['sample_id']} {phase}: worker elapsed time exceeds the "
            "checked timeout"
        )
    captured = parse_timestamp(
        snapshot["captured_at_utc"], "$.snapshot.captured_at_utc"
    )
    created = parse_timestamp(
        invocation["created_at_utc"], "$.invocation.created_at_utc"
    )
    if not (created <= started <= times[0] <= times[-1] <= finished):
        raise ValidationError(
            f"{sample['sample_id']} {phase}: invalid phase timestamp ordering"
        )
    action_times = {
        entry["action"]: parse_timestamp(
            entry["timestamp_utc"], "$.operation_log.entries[].timestamp_utc"
        )
        for entry in log["entries"]
    }
    observation_completed = max(
        action_times["version_read"], action_times["empty_schema_read"]
    )
    if not observation_completed <= captured < action_times["database_closed"]:
        raise ValidationError(
            f"{sample['sample_id']} {phase}: snapshot timestamp is not bounded "
            "by the while-open observation and database close events"
        )
    observations = phase_row["dao_observations_while_open"]
    for key in (
        "captured_while_database_open",
        "dao_version",
        "empty_user_schema",
        "user_table_count",
    ):
        require_equal(snapshot[key], observations[key], f"$.snapshot.{key}")
    require_equal(
        observations["dao_version"],
        condition["expected_dao_version"],
        f"$.phases.{phase}.dao_version",
    )
    post = phase_row["post_close_file_observations"]
    result_post = result["post_close_file_observations"]
    for key in ("database_path", "database_bytes", "database_sha256"):
        require_equal(
            result_post[key],
            post[key],
            f"$.worker_result.post_close.{key}",
        )
    require_equal(
        result_post["prefix"],
        {"path": post["prefix_path"], "sha256": post["prefix_sha256"]},
        "$.worker_result.post_close.prefix",
    )
    require_equal(
        result_post["prefix_bytes"],
        post["prefix_bytes"],
        "$.worker_result.post_close.prefix_bytes",
    )
    require_equal(
        post["database_path"],
        sample[f"{phase}_database_path"],
        f"$.phases.{phase}.database_path",
    )
    quiesced = None
    if quiescence is not None:
        projection = {
            key: quiescence[key]
            for key in (
                "worker_finished_at_utc",
                "observation_started_at_utc",
                "observation_completed_at_utc",
                "worker_exit_wait_completed",
                "database",
                "companion",
                "status",
            )
        }
        require_equal(
            projection,
            phase_row["post_worker_quiescence"],
            f"$.phases.{phase}.post_worker_quiescence",
        )
        require_equal(
            quiescence["worker_finished_at_utc"],
            result["finished_at_utc"],
            "$.post_worker_quiescence.worker_finished_at_utc",
        )
        database = quiescence["database"]
        require_equal(database["path"], post["database_path"], "$.quiescence.database.path")
        require_equal(database["bytes"], post["database_bytes"], "$.quiescence.database.bytes")
        require_equal(database["sha256"], post["database_sha256"], "$.quiescence.database.sha256")
        require_equal(database["prefix_sha256"], post["prefix_sha256"], "$.quiescence.database.prefix_sha256")
        expected_companion = post["database_path"][:-4] + ".ldb"
        require_equal(
            quiescence["companion"]["path"],
            expected_companion,
            "$.quiescence.companion.path",
        )
        quiescence_started = parse_timestamp(
            quiescence["observation_started_at_utc"],
            "$.quiescence.observation_started_at_utc",
        )
        quiesced = parse_timestamp(
            quiescence["observation_completed_at_utc"],
            "$.quiescence.observation_completed_at_utc",
        )
        if not finished <= quiescence_started <= quiesced:
            raise ValidationError(
                f"{sample['sample_id']} {phase}: post-worker quiescence chronology is invalid"
            )
        companion = quiescence["companion"]
        if companion["state"] == "absent":
            pass
        else:
            if source is None:
                size, digest, _ = bounded_file_identity(
                    bundle_root.joinpath(*companion["path"].split("/")),
                    plan["bounds"]["max_companion_bytes_per_artifact"],
                    retain=False,
                )
            else:
                size, digest = source.file_identity(companion["path"])
                source.binary_payload(companion["path"], "companion")
            require_equal(size, companion["bytes"], "$.companion.bytes")
            require_equal(digest, companion["sha256"], "$.companion.sha256")
    if phase == "creator":
        require_equal(
            result["pre_com_file_binding"],
            None,
            "$.worker_result.pre_com_file_binding",
        )
    else:
        pre = phase_row["pre_com_file_binding"]
        require_equal(
            result["pre_com_file_binding"],
            {
                key: pre[key]
                for key in ("database_path", "database_bytes", "database_sha256")
            },
            "$.worker_result.pre_com_file_binding",
        )
        contract = invocation["phase_contract"]
        require_equal(
            contract["pre_com_database_bytes"],
            pre["database_bytes"],
            "$.phase_contract.pre_com_database_bytes",
        )
        require_equal(
            contract["pre_com_database_sha256"],
            pre["database_sha256"],
            "$.phase_contract.pre_com_database_sha256",
        )
        if bindings.clone_log is None:
            raise ValidationError("reopen phase requires an explicit clone-log binding")
        require_equal(
            contract["clone_log"],
            bindings.clone_log,
            "$.phase_contract.clone_log",
        )
    return ValidatedPhase(
        invocation=invocation,
        result=result,
        log=log,
        snapshot=snapshot,
        quiescence=quiescence,
        started=started,
        finished=finished,
        quiesced=quiesced,
    )


def validate_phase_documents(
    bundle_root: Path,
    record: dict[str, Any],
    sample: dict[str, Any],
    condition: dict[str, Any],
    phase: str,
    plan: dict[str, Any],
    plan_sha256: str,
    *,
    source: ArtifactSource | None = None,
) -> ValidatedPhase:
    """Compatibility boundary for validating a phase in a full sample record."""
    return validate_phase_bindings(
        bundle_root,
        PhaseBindings.from_record(record, phase),
        sample,
        condition,
        phase,
        plan,
        plan_sha256,
        source=source,
    )


def validate_sample_record(
    bundle_root: Path,
    record: dict[str, Any],
    sample: dict[str, Any],
    condition: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    *,
    source: ArtifactSource | None = None,
) -> dict[str, ValidatedPhase]:
    """Validate a record, both phases, and the controller clone handoff."""
    SCHEMA_SET.validate(record)
    for key in (
        "sample_id",
        "condition_id",
        "replica",
        "block",
        "position_in_block",
        "launch_ordinal",
    ):
        require_equal(record[key], sample[key], f"$.{key}")
    require_equal(record["plan_sha256"], plan_sha256, "$.plan_sha256")
    require_equal(
        record["creation"], _creation_projection(condition, plan), "$.creation"
    )
    phases = {
        phase: validate_phase_documents(
            bundle_root,
            record,
            sample,
            condition,
            phase,
            plan,
            plan_sha256,
            source=source,
        )
        for phase in ("creator", "reopen")
    }
    clone_ref = record["controller_clone"]["clone_log"]
    clone, _, clone_hash = load_artifact_document(
        bundle_root,
        clone_ref["path"],
        65536,
        "dao_m4_clone_log",
        source,
    )
    require_equal(
        clone_hash,
        clone_ref["sha256"],
        "$.controller_clone.clone_log.sha256",
    )
    controller = record["controller_clone"]
    common = (
        "started_at_utc",
        "completed_at_utc",
        "source_path",
        "destination_path",
        "source_bytes",
        "destination_bytes",
        "source_sha256_before_clone",
        "source_sha256_after_clone",
        "destination_sha256",
        "source_file_identity",
        "destination_file_identity",
        "all_hashes_equal",
        "no_hardlink",
        "same_volume",
        "distinct_file_identity",
        "completed_before_reopen_com",
        "status",
    )
    for key in common:
        require_equal(clone[key], controller[key], f"$.controller_clone.{key}")
    require_equal(clone["sample_id"], sample["sample_id"], "$.clone_log.sample_id")
    require_equal(
        clone["reparse_free"],
        controller["source_reparse_free"]
        and controller["destination_reparse_free"],
        "$.clone_log.reparse_free",
    )
    hashes = {
        controller["source_sha256_before_clone"],
        controller["source_sha256_after_clone"],
        controller["destination_sha256"],
    }
    if len(hashes) != 1:
        raise ValidationError("$.controller_clone: three clone hashes differ")
    require_equal(
        controller["source_bytes"],
        controller["destination_bytes"],
        "$.controller_clone.destination_bytes",
    )
    require_equal(
        controller["source_path"],
        sample["creator_database_path"],
        "$.controller_clone.source_path",
    )
    require_equal(
        controller["destination_path"],
        sample["reopen_database_path"],
        "$.controller_clone.destination_path",
    )
    source_identity = controller["source_file_identity"]
    destination_identity = controller["destination_file_identity"]
    require_equal(
        source_identity["volume_serial_number"],
        destination_identity["volume_serial_number"],
        "$.controller_clone.same_volume",
    )
    if source_identity["file_index"] == destination_identity["file_index"]:
        raise ValidationError(
            "$.controller_clone: source and destination file identities are equal"
        )
    creator_post = record["phases"]["creator"]["post_close_file_observations"]
    reopen_pre = record["phases"]["reopen"]["pre_com_file_binding"]
    require_equal(
        (creator_post["database_bytes"], creator_post["database_sha256"]),
        (
            controller["source_bytes"],
            controller["source_sha256_before_clone"],
        ),
        "$.controller_clone source binding",
    )
    require_equal(
        (
            reopen_pre["database_path"],
            reopen_pre["database_bytes"],
            reopen_pre["database_sha256"],
        ),
        (
            controller["destination_path"],
            controller["destination_bytes"],
            controller["destination_sha256"],
        ),
        "$.controller_clone destination binding",
    )
    clone_started = parse_timestamp(
        controller["started_at_utc"], "$.controller_clone.started_at_utc"
    )
    clone_finished = parse_timestamp(
        controller["completed_at_utc"], "$.controller_clone.completed_at_utc"
    )
    if not (
        (phases["creator"].quiesced or phases["creator"].finished)
        <= clone_started
        <= clone_finished
        <= phases["reopen"].started
    ):
        raise ValidationError(
            "$.controller_clone: clone and phase timestamps are out of order"
        )
    if phases["reopen"].quiesced is None:
        raise ValidationError("reopen phase lacks post-worker quiescence")
    creator_worker = record["phases"]["creator"]["worker"]
    reopen_worker = record["phases"]["reopen"]["worker"]
    if creator_worker["worker_run_id"] == reopen_worker["worker_run_id"]:
        raise ValidationError("$.phases: paired worker run IDs must differ")
    if creator_worker["nonce"] == reopen_worker["nonce"]:
        raise ValidationError("$.phases: paired worker nonces must differ")
    return phases


def validate_quiescence_document(
    bundle_root: Path,
    quiescence_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    """Validate a controller post-worker observation before record assembly."""
    quiescence, _, _ = load_document(
        quiescence_path, 65536, "dao_m4_post_worker_quiescence"
    )
    result, _, _ = load_document(result_path, 65536, "dao_m4_worker_result")
    for key in ("sample_id", "phase_id", "phase_ordinal", "worker_run_id"):
        require_equal(quiescence[key], result[key], f"$.quiescence.{key}")
    require_equal(
        quiescence["worker_finished_at_utc"],
        result["finished_at_utc"],
        "$.quiescence.worker_finished_at_utc",
    )
    post = result["post_close_file_observations"]
    database = quiescence["database"]
    for observed, expected, location in (
        (database["path"], post["database_path"], "path"),
        (database["bytes"], post["database_bytes"], "bytes"),
        (database["sha256"], post["database_sha256"], "sha256"),
        (database["prefix_sha256"], post["prefix"]["sha256"], "prefix_sha256"),
    ):
        require_equal(observed, expected, f"$.quiescence.database.{location}")
    finished = parse_timestamp(result["finished_at_utc"], "$.result.finished_at_utc")
    started = parse_timestamp(
        quiescence["observation_started_at_utc"],
        "$.quiescence.observation_started_at_utc",
    )
    completed = parse_timestamp(
        quiescence["observation_completed_at_utc"],
        "$.quiescence.observation_completed_at_utc",
    )
    if not finished <= started <= completed:
        raise ValidationError("$.quiescence: observation chronology is invalid")
    database_path = resolve_bundle_path(bundle_root, database["path"])
    size, digest, prefix = bounded_file_identity(
        database_path, 1048576, retain=True
    )
    assert prefix is not None
    require_equal(size, database["bytes"], "$.quiescence.database actual bytes")
    require_equal(digest, database["sha256"], "$.quiescence.database actual sha256")
    import hashlib

    require_equal(
        hashlib.sha256(prefix[:2048]).hexdigest(),
        database["prefix_sha256"],
        "$.quiescence.database actual prefix sha256",
    )
    companion = quiescence["companion"]
    expected_companion = database["path"][:-4] + ".ldb"
    require_equal(companion["path"], expected_companion, "$.companion.path")
    companion_path = resolve_bundle_path(bundle_root, companion["path"])
    if companion["state"] == "absent":
        if companion_path.exists():
            raise ValidationError("$.companion: absent path exists")
    else:
        size, digest, _ = bounded_file_identity(
            companion_path, 65536, retain=False
        )
        require_equal(size, companion["bytes"], "$.companion.bytes")
        require_equal(digest, companion["sha256"], "$.companion.sha256")
    return quiescence
