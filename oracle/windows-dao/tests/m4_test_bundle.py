"""Synthetic, schema-complete 507-file M4 bundle builder for validator tests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from m4_analysis import canonical_analysis_bytes
from m4_bundle import build_analysis_from_stage
from m4_records import CHECKED_PLAN
from test_validate_m1_protocol import ready_environment

COMMIT = "1" * 40
RUN_ID = "20260725T000000Z-m4-synthetic"
EXPERIMENT = "DAO-M4-HEADER-DISCRIMINATOR-001"
PROVIDER = "3" * 64
ROOTS = ("/synthetic/repository", "/synthetic/stage")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(origin: dt.datetime, seconds: int) -> str:
    return (origin + dt.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def artifact(root: Path, locator: str) -> dict[str, str]:
    return {"path": locator, "sha256": digest(root / locator)}


def creator_contract(condition: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "creator",
        "method": plan["design"]["creation_method"],
        "locale": plan["design"]["locale"],
        "version_option": condition["version_option"],
        "version_api_value": condition["version_api_value"],
        "encryption_option": condition["encryption_option"],
        "encryption_api_value": condition["encryption_api_value"],
        "create_option_value": condition["create_option_value"],
        "compact_database_used": False,
        "expected_dao_version": condition["expected_dao_version"],
    }


def operation_log(
    sample_id: str,
    phase: str,
    origin: dt.datetime,
) -> dict[str, Any]:
    actions = {
        "creator": [
            "bindings_verified",
            "com_activated",
            "database_created",
            "version_read",
            "empty_schema_read",
            "database_closed",
            "ldb_absence_verified",
            "prefix_observed",
        ],
        "reopen": [
            "bindings_verified",
            "clone_verified",
            "com_activated",
            "database_opened",
            "version_read",
            "empty_schema_read",
            "database_closed",
            "ldb_absence_verified",
            "prefix_observed",
        ],
    }[phase]
    first_second = 2 if phase == "creator" else 15
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_m4_operation_log",
        "experiment_id": EXPERIMENT,
        "sample_id": sample_id,
        "phase_id": phase,
        "phase_ordinal": 1 if phase == "creator" else 2,
        "worker_run_id": f"{sample_id}-{phase.upper()}",
        "entries": [
            {
                "sequence": index,
                "timestamp_utc": stamp(origin, first_second + index - 1),
                "action": action,
                "status": "pass",
            }
            for index, action in enumerate(actions, start=1)
        ],
        "final_status": "pass",
    }


def build_bundle(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
    plan_locator = "plan/checked-plan.json"
    environment_locator = "bindings/environment.json"
    (root / plan_locator).parent.mkdir(parents=True)
    (root / plan_locator).write_bytes(CHECKED_PLAN.read_bytes())
    environment = ready_environment()
    environment["runtime"]["powershell_version"] = "5.1.19041.1"
    write_json(root / environment_locator, environment)
    plan_hash = digest(root / plan_locator)
    environment_hash = digest(root / environment_locator)
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    records = []
    roles = {plan_locator: "plan", environment_locator: "environment"}
    for sample in plan["samples"]:
        sample_id = sample["sample_id"]
        condition = conditions[sample["condition_id"]]
        origin = dt.datetime(2026, 7, 25, tzinfo=dt.timezone.utc) + dt.timedelta(
            minutes=sample["launch_ordinal"]
        )
        sample_base = f"evidence/samples/{sample_id}"
        database_payload = bytes(
            [condition["create_option_value"], sample["replica"]]
        ) * 1024
        database_hash = hashlib.sha256(database_payload).hexdigest()
        for phase in ("creator", "reopen"):
            database_locator = sample[f"{phase}_database_path"]
            database_path = root / database_locator
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.write_bytes(database_payload)
            roles[database_locator] = "database"
            prefix_locator = f"{sample_base}/{phase}/prefix.bin"
            prefix_path = root / prefix_locator
            prefix_path.parent.mkdir(parents=True, exist_ok=True)
            prefix_path.write_bytes(database_payload)
            roles[prefix_locator] = "prefix"
        clone_locator = f"{sample_base}/clone.json"
        clone = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_clone_log",
            "experiment_id": EXPERIMENT,
            "sample_id": sample_id,
            "started_at_utc": stamp(origin, 11),
            "completed_at_utc": stamp(origin, 12),
            "source_path": sample["creator_database_path"],
            "destination_path": sample["reopen_database_path"],
            "source_bytes": 2048,
            "destination_bytes": 2048,
            "source_sha256_before_clone": database_hash,
            "source_sha256_after_clone": database_hash,
            "destination_sha256": database_hash,
            "source_file_identity": {
                "volume_serial_number": "00000001",
                "file_index": f"{sample['launch_ordinal'] * 2 - 1:016x}",
                "link_count": 1,
            },
            "destination_file_identity": {
                "volume_serial_number": "00000001",
                "file_index": f"{sample['launch_ordinal'] * 2:016x}",
                "link_count": 1,
            },
            "all_hashes_equal": True,
            "same_volume": True,
            "distinct_file_identity": True,
            "no_hardlink": True,
            "reparse_free": True,
            "completed_before_reopen_com": True,
            "status": "pass",
        }
        write_json(root / clone_locator, clone)
        roles[clone_locator] = "clone_log"
        phases = {}
        for phase in ("creator", "reopen"):
            phase_ordinal = 1 if phase == "creator" else 2
            worker_ordinal = 2 * sample["launch_ordinal"] - (1 if phase == "creator" else 0)
            worker_id = f"{sample_id}-{phase.upper()}"
            phase_base = f"{sample_base}/{phase}"
            invocation_locator = f"{phase_base}/invocation.json"
            result_locator = f"{phase_base}/worker-result.json"
            log_locator = f"{phase_base}/operation-log.json"
            snapshot_locator = f"{phase_base}/snapshot.json"
            prefix_locator = f"{phase_base}/prefix.bin"
            database_locator = sample[f"{phase}_database_path"]
            phase_contract = creator_contract(condition, plan)
            if phase == "reopen":
                phase_contract = {
                    "kind": "reopen",
                    "expected_dao_version": condition["expected_dao_version"],
                    "pre_com_database_bytes": 2048,
                    "pre_com_database_sha256": database_hash,
                    "clone_log": artifact(root, clone_locator),
                }
            invocation = {
                "protocol_version": "1.0.0",
                "document_type": "dao_m4_invocation",
                "experiment_id": EXPERIMENT,
                "sample_id": sample_id,
                "condition_id": sample["condition_id"],
                "phase_id": phase,
                "phase_ordinal": phase_ordinal,
                "worker_run_id": worker_id,
                "worker_ordinal": worker_ordinal,
                "nonce": f"{worker_ordinal:032x}",
                "campaign_run_id": RUN_ID,
                "producer_commit": COMMIT,
                "repository_url": plan["repository_url"],
                "remote_ref": plan["remote_ref"],
                "repository_root": ROOTS[0],
                "plan_path": plan_locator,
                "plan_sha256": plan_hash,
                "environment_path": environment_locator,
                "environment_sha256": environment_hash,
                "provider_sha256": PROVIDER,
                "stage_root": ROOTS[1],
                "database_path": database_locator,
                "result_path": result_locator,
                "phase_contract": phase_contract,
                "created_at_utc": stamp(origin, 0 if phase == "creator" else 13),
                "bindings_verified_before_com": True,
            }
            write_json(root / invocation_locator, invocation)
            roles[invocation_locator] = "phase_invocation"
            log = operation_log(sample_id, phase, origin)
            write_json(root / log_locator, log)
            roles[log_locator] = "operation_log"
            snapshot = {
                "protocol_version": "1.0.0",
                "document_type": "dao_m4_empty_schema_version_snapshot",
                "experiment_id": EXPERIMENT,
                "sample_id": sample_id,
                "phase_id": phase,
                "phase_ordinal": phase_ordinal,
                "captured_while_database_open": True,
                "captured_at_utc": stamp(origin, 6 if phase == "creator" else 20),
                "dao_version": condition["expected_dao_version"],
                "empty_user_schema": True,
                "user_table_count": 0,
            }
            write_json(root / snapshot_locator, snapshot)
            roles[snapshot_locator] = "semantic_snapshot"
            provider = {
                "powershell_version": "5.1.19041.1",
                "prog_id": "DAO.DBEngine.36",
                "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                "server_sha256": PROVIDER,
            }
            pre_com = None
            if phase == "reopen":
                pre_com = {
                    "database_path": database_locator,
                    "database_bytes": 2048,
                    "database_sha256": database_hash,
                }
            result = {
                "protocol_version": "1.0.0",
                "document_type": "dao_m4_worker_result",
                "experiment_id": EXPERIMENT,
                "sample_id": sample_id,
                "phase_id": phase,
                "phase_ordinal": phase_ordinal,
                "worker_run_id": worker_id,
                "worker_ordinal": worker_ordinal,
                "nonce": f"{worker_ordinal:032x}",
                "process_id": 1000 + worker_ordinal,
                "architecture": "x86",
                "provider": provider,
                "started_at_utc": stamp(origin, 1 if phase == "creator" else 14),
                "finished_at_utc": stamp(origin, 10 if phase == "creator" else 24),
                "bindings_verified_before_com": True,
                "invocation_sha256": digest(root / invocation_locator),
                "operation_log": artifact(root, log_locator),
                "snapshot": artifact(root, snapshot_locator),
                "pre_com_file_binding": pre_com,
                "post_close_file_observations": {
                    "database_path": database_locator,
                    "database_bytes": 2048,
                    "database_sha256": database_hash,
                    "prefix": artifact(root, prefix_locator),
                    "prefix_bytes": 2048,
                    "lock_file_absent_after_close": True,
                },
                "execution_status": "pass",
            }
            write_json(root / result_locator, result)
            roles[result_locator] = "phase_worker_result"
            worker = {
                "process_id": result["process_id"],
                "started_at_utc": result["started_at_utc"],
                "worker_run_id": worker_id,
                "worker_ordinal": worker_ordinal,
                "nonce": result["nonce"],
                "architecture": "x86",
                "provider": provider,
                "fresh_process": True,
                "bindings_verified_before_com": True,
            }
            post = {
                "database_path": database_locator,
                "database_bytes": 2048,
                "database_sha256": database_hash,
                "prefix_path": prefix_locator,
                "prefix_bytes": 2048,
                "prefix_sha256": digest(root / prefix_locator),
                "database_closed": True,
                "lock_file_absent_after_close": True,
            }
            phase_record = {
                "phase_id": phase,
                "phase_ordinal": phase_ordinal,
                "worker": worker,
                "artifacts": {
                    "invocation": artifact(root, invocation_locator),
                    "operation_log": artifact(root, log_locator),
                    "snapshot": artifact(root, snapshot_locator),
                    "worker_result": artifact(root, result_locator),
                },
                "dao_observations_while_open": {
                    "captured_while_database_open": True,
                    "dao_version": condition["expected_dao_version"],
                    "empty_user_schema": True,
                    "user_table_count": 0,
                },
                "post_close_file_observations": post,
                "status": "pass",
            }
            if phase == "reopen":
                phase_record["pre_com_file_binding"] = pre_com | {
                    "verified_before_com": True
                }
            phases[phase] = phase_record
        controller_clone = {
            "owner": "controller",
            "clone_log": artifact(root, clone_locator),
            "started_at_utc": clone["started_at_utc"],
            "completed_at_utc": clone["completed_at_utc"],
            "source_path": clone["source_path"],
            "destination_path": clone["destination_path"],
            "source_bytes": 2048,
            "destination_bytes": 2048,
            "source_sha256_before_clone": database_hash,
            "source_sha256_after_clone": database_hash,
            "destination_sha256": database_hash,
            "source_file_identity": clone["source_file_identity"],
            "destination_file_identity": clone["destination_file_identity"],
            "creator_closed_before_clone": True,
            "source_immutable_during_clone": True,
            "source_unchanged_after_clone": True,
            "all_hashes_equal": True,
            "exact_byte_clone": True,
            "source_reparse_free": True,
            "destination_reparse_free": True,
            "no_hardlink": True,
            "same_volume": True,
            "distinct_file_identity": True,
            "identities_preserved_by_same_volume_publish_rename": True,
            "completed_before_reopen_com": True,
            "reopen_bindings_verified_before_com": True,
            "status": "pass",
        }
        creation = creator_contract(condition, plan)
        creation.pop("kind")
        creation.pop("locale")
        creation.pop("expected_dao_version")
        record = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_sample_record",
            "experiment_id": EXPERIMENT,
            "plan_sha256": plan_hash,
            "producer_commit": COMMIT,
            "environment_sha256": environment_hash,
            "provider_sha256": PROVIDER,
            "sample_id": sample_id,
            "condition_id": sample["condition_id"],
            "replica": sample["replica"],
            "block": sample["block"],
            "position_in_block": sample["position_in_block"],
            "launch_ordinal": sample["launch_ordinal"],
            "creation": creation,
            "phases": phases,
            "controller_clone": controller_clone,
            "execution_status": "pass",
        }
        write_json(root / sample["record_path"], record)
        roles[sample["record_path"]] = "sample_record"
        records.append(record)
    analysis = build_analysis_from_stage(root)
    analysis_locator = "analysis/report.json"
    (root / analysis_locator).parent.mkdir(parents=True)
    (root / analysis_locator).write_bytes(canonical_analysis_bytes(analysis))
    roles[analysis_locator] = "analysis_report"
    files = []
    for locator, role in sorted(roles.items()):
        path = root / locator
        files.append(
            {
                "path": locator,
                "role": role,
                "sha256": digest(path),
                "size_bytes": path.stat().st_size,
                "media_type": (
                    "application/octet-stream"
                    if role in ("database", "prefix")
                    else "application/json"
                ),
            }
        )
    assert len(files) == 507
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_m4_bundle_manifest",
        "experiment_id": EXPERIMENT,
        "run_id": RUN_ID,
        "producer_commit": COMMIT,
        "created_at_utc": "2026-07-25T23:59:59Z",
        "sample_count": 36,
        "worker_count": 72,
        "file_count": 507,
        "bundle_tree_complete": True,
        "unexpected_files_present": False,
        "symlinks_or_reparses_present": False,
        "execution_status": "pass",
        "files": files,
    }
    write_json(root / "bundle-manifest.json", manifest)
    return plan, records
