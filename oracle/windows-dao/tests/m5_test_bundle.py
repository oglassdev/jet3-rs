"""Schema-complete synthetic M5R3 evidence for contract tests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from m5_analysis import canonical_analysis_bytes
from m5_bundle import build_full_analysis
from m5_records import CHECKED_PLAN, _phase_contract
from m5_spec import DATABASE_ROLES, M4_MANIFEST_SHA256, PHASES
from test_validate_m1_protocol import ready_environment

COMMIT = "1" * 40
PROVIDER = "3" * 64
RUN_ID = "20260810T230000Z-m5-synthetic"
IDENTITY = {"volume_serial_number": "00000001", "file_index": "0000000000000001", "link_count": 1}


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(root: Path, locator: str) -> dict[str, str]:
    return {"path": locator, "sha256": digest(root / locator)}


def timestamp(origin: dt.datetime, seconds: int) -> str:
    return (origin + dt.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def m4_prefix(condition_id: str) -> bytes:
    payload = bytearray(2048)
    if condition_id.startswith("V30"):
        payload[0] = 1
        payload[1] = 3 if condition_id.endswith("-E") else 2
    return bytes(payload)


def synthetic_m4() -> dict[str, Any]:
    prefixes: dict[str, bytes] = {}
    records = []
    for condition in ("V20-U", "V20-E", "V30-U", "V30-E", "V40-U", "V40-E"):
        for replica in range(1, 7):
            sid = f"M4-{condition}-{replica:02d}"
            phases = {}
            for phase in ("creator", "reopen"):
                locator = f"m4/{sid}/{phase}.bin"
                prefixes[locator] = m4_prefix(condition)
                phases[phase] = {"post_close_file_observations": {"prefix_path": locator}}
            records.append({"sample_id": sid, "condition_id": condition, "phases": phases})
    return {
        "manifest": {"producer_commit": "35f5f55f0b7277fc07831db540eab7fa69a41a20", "run_id": "20260810T220332Z-m4-r2"},
        "records": records,
        "prefixes": prefixes,
        "analysis": {"candidate_sets": [
            {"candidate_set_id": "M4-CANDIDATE-VERSION-PAIRED", "absolute_offsets": [0]},
            {"candidate_set_id": "M4-CANDIDATE-V30-ENCRYPTION", "absolute_offsets": [1]},
        ]},
    }


def _operation(sample_id: str, phase: str, origin: dt.datetime) -> dict[str, Any]:
    actions = {
        "source": ["bindings_verified", "com_activated", "database_created", "version_read", "empty_schema_read", "database_closed", "prefix_observed"],
        "compact": ["bindings_verified", "clone_verified", "com_activated", "database_compacted", "database_closed", "prefix_observed"],
        "verify": ["bindings_verified", "clone_verified", "com_activated", "database_opened", "version_read", "empty_schema_read", "database_closed", "prefix_observed"],
    }[phase]
    return {"protocol_version": "1.0.0", "document_type": "dao_m5_operation_log", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sample_id, "phase_id": phase, "worker_run_id": f"{sample_id}-{phase.upper()}", "started_at_utc": timestamp(origin, 1), "completed_at_utc": timestamp(origin, 7), "actions": actions, "status": "pass"}


def _clone(root: Path, sample: dict[str, Any], clone_id: str, source: str, destination: str, origin: dt.datetime, roles: dict[str, str]) -> dict[str, str]:
    locator = f"evidence/samples/{sample['sample_id']}/{clone_id.replace('_','-')}-clone.json"
    payload = (root / source).read_bytes()
    digest_value = hashlib.sha256(payload).hexdigest()
    document = {"protocol_version": "1.0.0", "document_type": "dao_m5_clone_log", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sample["sample_id"], "clone_id": clone_id, "started_at_utc": timestamp(origin, 9), "completed_at_utc": timestamp(origin, 10), "source_path": source, "destination_path": destination, "source_bytes": len(payload), "destination_bytes": len(payload), "source_sha256_before_clone": digest_value, "source_sha256_after_clone": digest_value, "destination_sha256": digest_value, "source_file_identity": IDENTITY, "destination_file_identity": {**IDENTITY, "file_index": "0000000000000002"}, "exact_byte_clone": True, "source_unchanged_after_clone": True, "all_hashes_equal": True, "no_hardlink": True, "same_volume": True, "distinct_file_identity": True, "status": "pass"}
    write_json(root / locator, document)
    roles[locator] = "clone_log"
    return ref(root, locator)


def build_bundle(root: Path, present_companion: tuple[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = json.loads(CHECKED_PLAN.read_text(encoding="utf-8"))
    (root / "plan.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_bytes(CHECKED_PLAN.read_bytes())
    environment = ready_environment()
    environment["runtime"]["powershell_version"] = "5.1.19041.1"
    environment["accepted_provider"]["server_sha256"] = PROVIDER
    write_json(root / "environment.json", environment)
    plan_hash, environment_hash = digest(root / "plan.json"), digest(root / "environment.json")
    roles = {"plan.json": "plan", "environment.json": "environment"}
    conditions = {row["condition_id"]: row for row in plan["conditions"]}
    records: list[dict[str, Any]] = []
    record_hashes: dict[str, str] = {}
    prefixes: dict[str, bytes] = {}
    for sample in plan["samples"]:
        sid = sample["sample_id"]
        condition = conditions[sample["condition_id"]]
        origin = dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=sample["launch_ordinal"])
        source_payload = m4_prefix(condition["source_condition_id"])
        compact_payload = m4_prefix(condition["matched_m4_condition_id"])
        database_payloads = {"source_database": source_payload, "compact_input_database": source_payload, "compacted_database": compact_payload, "verify_database": compact_payload}
        for role, payload in database_payloads.items():
            key = f"{role[:-9]}_database_path" if role != "compacted_database" else "compacted_database_path"
            path = root / sample[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            roles[sample[key]] = "database"
        phase_results: dict[str, dict[str, Any]] = {}
        phase_refs: dict[str, dict[str, Any]] = {}
        role_phase = {"source": ("source_database",), "compact": ("compact_input_database", "compacted_database"), "verify": ("verify_database",)}
        for phase_index, phase in enumerate(PHASES, start=1):
            base = f"evidence/samples/{sid}"
            operation_locator = f"{base}/{phase.upper()}-operation-log.json"
            write_json(root / operation_locator, _operation(sid, phase, origin))
            roles[operation_locator] = "operation_log"
            snapshot_ref = None
            if phase != "compact":
                snapshot_locator = f"{base}/{phase.upper()}-snapshot.json"
                expected_version = condition["expected_source_dao_version"] if phase == "source" else condition["expected_destination_dao_version"]
                write_json(root / snapshot_locator, {"protocol_version": "1.0.0", "document_type": "dao_m5_snapshot", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sid, "phase_id": phase, "captured_at_utc": timestamp(origin, 4), "captured_while_database_open": True, "dao_version": expected_version, "empty_user_schema": True, "user_table_count": 0})
                roles[snapshot_locator] = "semantic_snapshot"
                snapshot_ref = ref(root, snapshot_locator)
            observations = []
            for role in role_phase[phase]:
                key = f"{role[:-9]}_database_path" if role != "compacted_database" else "compacted_database_path"
                locator = sample[key]
                payload = database_payloads[role]
                prefix_hash = hashlib.sha256(payload[:2048]).hexdigest()
                prefix_ref = None
                if role != "compact_input_database":
                    prefix_locator = f"{base}/{phase.upper()}.prefix.bin"
                    (root / prefix_locator).write_bytes(payload[:2048])
                    roles[prefix_locator] = "prefix"
                    prefixes[prefix_locator] = payload[:2048]
                    prefix_ref = ref(root, prefix_locator)
                observations.append({"database_role": role, "path": locator, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "prefix_sha256": prefix_hash, "prefix": prefix_ref})
            invocation_locator = f"{base}/{phase}-invocation.json"
            result_locator = f"{base}/{phase.upper()}-worker-result.json"
            database_paths = {row["database_role"]: row["path"] for row in observations}
            invocation = {"protocol_version": "1.0.0", "document_type": "dao_m5_invocation", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sid, "condition_id": sample["condition_id"], "phase_id": phase, "phase_ordinal": phase_index, "worker_run_id": f"{sid}-{phase.upper()}", "worker_ordinal": 3 * sample["launch_ordinal"] - (3 - phase_index), "nonce": f"{3 * sample['launch_ordinal'] - (3 - phase_index):032x}", "campaign_run_id": RUN_ID, "producer_commit": COMMIT, "repository_url": plan["repository_url"], "remote_ref": plan["remote_ref"], "repository_root": "/synthetic/repository", "plan_path": "plan.json", "plan_sha256": plan_hash, "environment_path": "environment.json", "environment_sha256": environment_hash, "provider_sha256": PROVIDER, "stage_root": "/synthetic/stage", "database_paths": database_paths, "result_path": result_locator, "phase_contract": _phase_contract(condition, phase), "m4_input": {"bundle_manifest_sha256": M4_MANIFEST_SHA256, "producer_commit": "35f5f55f0b7277fc07831db540eab7fa69a41a20", "campaign_run_id": "20260810T220332Z-m4-r2", "validated_before_com": True}, "created_at_utc": timestamp(origin, 0), "bindings_verified_before_com": True}
            write_json(root / invocation_locator, invocation)
            roles[invocation_locator] = "phase_invocation"
            result = {"protocol_version": "1.0.0", "document_type": "dao_m5_worker_result", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sid, "condition_id": sample["condition_id"], "phase_id": phase, "phase_ordinal": phase_index, "worker_run_id": f"{sid}-{phase.upper()}", "worker_ordinal": invocation["worker_ordinal"], "nonce": invocation["nonce"], "process_id": invocation["worker_ordinal"], "architecture": "x86", "provider": {"powershell_version": environment["runtime"]["powershell_version"], "prog_id": "DAO.DBEngine.36", "clsid": environment["accepted_provider"]["clsid"].upper(), "server_sha256": PROVIDER}, "started_at_utc": timestamp(origin, 1), "finished_at_utc": timestamp(origin, 8), "bindings_verified_before_com": True, "invocation_sha256": digest(root / invocation_locator), "operation_log": ref(root, operation_locator), "snapshot": snapshot_ref, "database_observations": observations, "execution_status": "pass"}
            write_json(root / result_locator, result)
            roles[result_locator] = "phase_worker_result"
            phase_results[phase] = result
            phase_refs[phase] = {"worker_result": ref(root, result_locator), "status": "pass"}
        clones = [
            _clone(root, sample, "source_to_compact_input", sample["source_database_path"], sample["compact_input_database_path"], origin, roles),
            _clone(root, sample, "compacted_to_verify_input", sample["compacted_database_path"], sample["verify_database_path"], origin, roles),
        ]
        quiescence_refs = {}
        owner_phase = {"source_database": "source", "compact_input_database": "compact", "compacted_database": "compact", "verify_database": "verify"}
        for role in DATABASE_ROLES:
            phase = owner_phase[role]
            observation = next(row for row in phase_results[phase]["database_observations"] if row["database_role"] == role)
            companion_path = observation["path"][:-4] + ".ldb"
            companion: dict[str, Any] = {"state": "absent", "path": companion_path, "checked_after_worker_exit": True}
            if present_companion == (sid, role):
                (root / companion_path).write_bytes(b"synthetic companion")
                roles[companion_path] = "companion"
                companion = {"state": "present", "path": companion_path, "bytes": (root / companion_path).stat().st_size, "sha256": digest(root / companion_path), "file_identity": IDENTITY, "exclusive_open_verified": True, "checked_after_worker_exit": True}
            locator = f"evidence/quiescence/{sid}/{role}.json"
            write_json(root / locator, {"protocol_version": "1.0.0", "document_type": "dao_m5_post_worker_quiescence", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "sample_id": sid, "phase_id": phase, "phase_ordinal": PHASES.index(phase) + 1, "database_role": role, "worker_run_id": phase_results[phase]["worker_run_id"], "worker_finished_at_utc": phase_results[phase]["finished_at_utc"], "observation_started_at_utc": timestamp(origin, 9), "observation_completed_at_utc": timestamp(origin, 10), "worker_exit_wait_completed": True, "database": {"path": observation["path"], "bytes": observation["bytes"], "sha256": observation["sha256"], "prefix_sha256": observation["prefix_sha256"], "file_identity": IDENTITY, "exclusive_open_verified": True, "matches_worker_observation": True}, "companion": companion, "status": "pass"})
            roles[locator] = "post_worker_quiescence"
            quiescence_refs[role] = ref(root, locator)
        record = {"protocol_version": "1.0.0", "document_type": "dao_m5_sample_record", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "plan_sha256": plan_hash, "producer_commit": COMMIT, "environment_sha256": environment_hash, "provider_sha256": PROVIDER, "m4_manifest_sha256": M4_MANIFEST_SHA256, "sample_id": sid, "condition_id": sample["condition_id"], "replica": sample["replica"], "block": sample["block"], "position_in_block": sample["position_in_block"], "launch_ordinal": sample["launch_ordinal"], "phases": phase_refs, "controller_clones": clones, "post_worker_quiescence": quiescence_refs, "execution_status": "pass", "_results": phase_results}
        retained = dict(record)
        retained.pop("_results")
        write_json(root / sample["record_path"], retained)
        roles[sample["record_path"]] = "sample_record"
        record_hashes[sample["record_path"]] = digest(root / sample["record_path"])
        records.append(record)
    analysis = build_full_analysis(plan, plan_hash, records, record_hashes, prefixes, synthetic_m4())
    (root / "analysis.json").write_bytes(canonical_analysis_bytes(analysis))
    roles["analysis.json"] = "analysis_report"
    for record in records:
        record.pop("_results", None)
    files = []
    for locator, role in sorted(roles.items()):
        files.append({"path": locator, "role": role, "sha256": digest(root / locator), "size_bytes": (root / locator).stat().st_size, "media_type": "application/octet-stream" if role in ("database", "prefix", "companion") else "application/json"})
    manifest = {"protocol_version": "1.0.0", "document_type": "dao_m5_bundle_manifest", "experiment_id": "DAO-M5-COMPACT-CONFIRM-003", "run_id": RUN_ID, "producer_commit": COMMIT, "created_at_utc": "2026-08-10T23:59:00Z", "sample_count": 108, "worker_count": 324, "m4_manifest_sha256": M4_MANIFEST_SHA256, "file_count": len(files), "bundle_tree_complete": True, "unexpected_files_present": False, "symlinks_or_reparses_present": False, "execution_status": "pass", "files": files}
    write_json(root / "bundle-manifest.json", manifest)
    return manifest, synthetic_m4()
