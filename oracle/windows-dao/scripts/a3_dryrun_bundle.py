#!/usr/bin/env python3
"""On-disk A3 bundle materialisation for the synthetic dry run.

The generator's replicas are written in the plan's artifact layout (page-store
blobs, page indexes, observations, environments, replica manifests, plan copy)
so that the analyzer and the independent validator read identical bytes. The
holdout structure receipt is produced by a spawned process, as the workflow
does, and the bundle manifest closes the inventory after analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError, canonical_json_bytes
from a3_generator import PROVIDER_SHA256, SyntheticReplica
from a3_generator_schedule import ROLES, RollingHashes
from a3_spec import (
    BOUNDS, CHECKED_PLAN, CHECKPOINT_IDS, EXPERIMENT_ID, PAGE_SIZE, PLAN, PLAN_SHA256,
    load_bounded_json, validate_document,
)

ARTIFACTS = PLAN.document["artifacts"]
REPOSITORY_URL = PLAN.document["repository_binding"]["canonical_https_url"]
RECEIPT_TIMEOUT_SECONDS = 300
MAX_JSON_BYTES = BOUNDS["max_json_bytes"]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: dict[str, Any]) -> bytes:
    payload = canonical_json_bytes(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _entry(relative: str, role: str, payload: bytes, media_type: str = "application/json") -> dict[str, Any]:
    return {
        "path": relative, "role": role, "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload), "media_type": media_type,
    }


@dataclass(frozen=True)
class BundlePaths:
    root: Path
    campaign_id: str
    producer_commit: str

    @property
    def candidate_set(self) -> Path:
        return self.root / ARTIFACTS["frozen_candidate_set"]

    @property
    def receipt(self) -> Path:
        return self.root / ARTIFACTS["holdout_structure_receipt"]

    @property
    def report(self) -> Path:
        return self.root / ARTIFACTS["analysis_report"]

    @property
    def observations(self) -> list[Path]:
        return [self.root / relative for relative in ARTIFACTS["replica_observations"]]


def _environment(paths: BundlePaths, replica: int) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_environment", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "producer_commit": paths.producer_commit, "repository_url": REPOSITORY_URL,
        "campaign_id": paths.campaign_id, "status": "ready",
        "host": {
            "windows_version": "synthetic-dry-run", "process_architecture": "x86",
            "powershell_version": "5.1.0", "python_version": "3.13.0", "runner_image": "synthetic-dry-run",
        },
        "provider": {
            "prog_id": "DAO.DBEngine.36", "clsid": "{00000000-0000-0000-0000-000000000000}",
            "provider_version": "synthetic", "server_path": "synthetic:/dao360.dll",
            "server_file_version": "synthetic", "server_sha256": PROVIDER_SHA256,
        },
        "replica": replica, "matrix_job_id": f"a3-synthetic-replica-{replica}",
    }


def _page_index(paths: BundlePaths, replica: SyntheticReplica, ordinal: int, environment_sha256: str, previous: tuple[str, ...]) -> dict[str, Any]:
    checkpoint = CHECKPOINT_IDS[ordinal]
    hashes = replica.ordered_page_sha256[checkpoint]
    database = hashlib.sha256()
    for digest in hashes:
        database.update(replica.payloads[digest])
    changed = [
        page for page in range(max(len(previous), len(hashes)))
        if (previous[page] if page < len(previous) else None) != (hashes[page] if page < len(hashes) else None)
    ]
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_page_index", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "producer_commit": paths.producer_commit, "campaign_id": paths.campaign_id,
        "environment_sha256": environment_sha256, "provider_sha256": PROVIDER_SHA256, "replica": replica.replica,
        "checkpoint_id": checkpoint, "ordinal": ordinal,
        "predecessor_checkpoint_id": None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1],
        "page_count": len(hashes), "file_size_bytes": len(hashes) * PAGE_SIZE,
        "database_sha256": database.hexdigest(), "ordered_page_sha256": list(hashes),
        "changed_page_indices": changed,
    }


def _reread(replica: SyntheticReplica, hashes: RollingHashes, checkpoint: str, counts: dict[str, int], extant: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for role in ROLES:
        if role not in extant:
            continue
        count = counts[role]
        if checkpoint == "L_DELETE_ALL" and role == "L":
            count = replica.l_rows_reread_after_delete
        rows.append({"role": role, "row_count": count, "rolling_sha256": hashes.digest(role, count)})
    return rows


def _observation(paths: BundlePaths, replica: SyntheticReplica, environment_sha256: str, index_entries: list[dict[str, Any]], indexes: list[dict[str, Any]]) -> dict[str, Any]:
    binding = next(row for row in PLAN.document["tables"]["role_bindings"] if row["replica"] == replica.replica)
    hashes = RollingHashes()
    checkpoints = []
    for row, entry, index in zip(replica.schedule.checkpoints, index_entries, indexes):
        checkpoints.append({
            "checkpoint_id": row.checkpoint_id, "ordinal": row.ordinal,
            "actual_file_pages": row.actual_file_pages, "actual_size_bytes": row.actual_file_pages * PAGE_SIZE,
            "target_baseline_pages": row.target_baseline_pages, "target_threshold_pages": row.target_threshold_pages,
            "target_overshoot_pages": row.target_overshoot_pages, "inserted_rows_total": row.inserted_rows_total,
            "table_row_counts": dict(row.table_row_counts),
            "dao_reread": _reread(replica, hashes, row.checkpoint_id, row.table_row_counts, row.extant_roles),
            "quiescent": True,
            "post_close_companion": {"present_after_close": False, "observed_size_bytes": 0, "retained_for_physical_analysis": False},
            "page_index": {"path": entry["path"], "sha256": entry["sha256"], "size_bytes": entry["size_bytes"]},
        })
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_replica_observation", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "producer_commit": paths.producer_commit, "repository_url": REPOSITORY_URL,
        "campaign_id": paths.campaign_id,
        "matrix_job": {"job_id": f"a3-synthetic-replica-{replica.replica}", "replica_only": True, "shared_mutable_state": False},
        "environment_sha256": environment_sha256, "provider_sha256": PROVIDER_SHA256, "replica": replica.replica,
        "role_binding": {role: binding[role] for role in ROLES},
        "d_growth_observation": replica.schedule.d_growth_observation(),
        "logical_checkpoint_read_bytes": sum(index["file_size_bytes"] for index in indexes),
        "inserted_rows_total": replica.schedule.checkpoints[-1].inserted_rows_total,
        "changed_hash_entries": sum(len(index["changed_page_indices"]) for index in indexes),
        "checkpoints": checkpoints,
    }


def write_bundle(root: Path, replicas: tuple[SyntheticReplica, ...], campaign_id: str, producer_commit: str) -> BundlePaths:
    """Write every acquisition-side artifact; analysis artifacts come later."""
    if root.exists():
        raise ValidationError(f"A3 dry-run bundle root already exists: {root}")
    paths = BundlePaths(root, campaign_id, producer_commit)
    store = root / ARTIFACTS["page_store_directory"]
    store.mkdir(parents=True)
    plan_payload = CHECKED_PLAN.read_bytes()
    (root / ARTIFACTS["plan"]).parent.mkdir(parents=True)
    (root / ARTIFACTS["plan"]).write_bytes(plan_payload)
    written: set[str] = set()
    for replica in replicas:
        number = replica.replica
        entries: list[dict[str, Any]] = []
        environment_relative = ARTIFACTS["replica_environments"][number - 1]
        environment_payload = _write_json(root / environment_relative, _environment(paths, number))
        environment_sha256 = hashlib.sha256(environment_payload).hexdigest()
        entries.append(_entry(environment_relative, "environment", environment_payload))
        blob_entries: list[dict[str, Any]] = []
        for digest, payload in replica.payloads.items():
            if digest in replica.missing_blob_digests:
                continue
            relative = f"{ARTIFACTS['page_store_directory']}/{digest}.page"
            if digest not in written:
                (store / f"{digest}.page").write_bytes(payload)
                written.add(digest)
            blob_entries.append(_entry(relative, "page_blob", payload, "application/octet-stream"))
        index_entries, indexes = [], []
        previous: tuple[str, ...] = ()
        for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
            index = _page_index(paths, replica, ordinal, environment_sha256, previous)
            relative = f"{ARTIFACTS['page_index_directory']}/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
            payload = _write_json(root / relative, index)
            index_entries.append(_entry(relative, "page_index", payload))
            indexes.append(index)
            previous = replica.ordered_page_sha256[checkpoint]
        observation_relative = ARTIFACTS["replica_observations"][number - 1]
        observation_payload = _write_json(root / observation_relative, _observation(paths, replica, environment_sha256, index_entries, indexes))
        entries.append(_entry(observation_relative, "replica_observation", observation_payload))
        manifest = {
            "protocol_version": "1.0.0", "document_type": "dao_a3_replica_artifact_manifest", "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256, "producer_commit": producer_commit, "campaign_id": campaign_id,
            "matrix_job_id": f"a3-synthetic-replica-{number}", "replica": number,
            "environment_sha256": environment_sha256, "provider_sha256": PROVIDER_SHA256,
            "checkpoint_count": len(CHECKPOINT_IDS), "inventory_closed": True, "hashes_verified": True,
            "paths_closed": True, "files": entries + index_entries + blob_entries,
        }
        _write_json(root / ARTIFACTS["replica_artifact_manifests"][number - 1], manifest)
    return paths


def run_receipt_process(paths: BundlePaths, candidate_sha256: str) -> None:
    """Spawn the receipt writer exactly as the fan-in workflow spawns a3_holdout."""
    command = [
        sys.executable, "-B", str(Path(__file__)), "receipt", "--bundle-root", str(paths.root),
        "--candidate-sha256", candidate_sha256, "--campaign-id", paths.campaign_id,
        "--producer-commit", paths.producer_commit, "--output", str(paths.receipt),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, timeout=RECEIPT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"holdout receipt writer failed: {exc}") from exc
    if completed.returncode != 0:
        raise ValidationError("holdout receipt writer returned nonzero")


def write_receipt(root: Path, candidate_sha256: str, campaign_id: str, producer_commit: str, output: Path) -> dict[str, Any]:
    """Bind the holdout replica's manifest to the frozen set without reading page bytes."""
    manifest_path = root / ARTIFACTS["replica_artifact_manifests"][2]
    manifest = load_bounded_json(manifest_path, MAX_JSON_BYTES)
    validate_document(manifest)
    observation = load_bounded_json(root / ARTIFACTS["replica_observations"][2], MAX_JSON_BYTES)
    validate_document(observation)
    for document in (manifest, observation):
        if document["replica"] != 3 or document["campaign_id"] != campaign_id or document["producer_commit"] != producer_commit:
            raise ValidationError("holdout replica binding mismatch")
    listed = {row["path"]: row for row in manifest["files"]}
    for row in observation["checkpoints"]:
        reference = row["page_index"]
        entry = listed.get(reference["path"])
        if entry is None or entry["sha256"] != reference["sha256"] or entry["sha256"] != _sha256_file(root / reference["path"]):
            raise ValidationError("holdout page index is not bound to its manifest")
    receipt = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_holdout_structure_receipt", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "producer_commit": producer_commit, "campaign_id": campaign_id,
        "derivation_candidate_set_sha256": candidate_sha256, "replica": 3,
        "replica_artifact_manifest_sha256": _sha256_file(manifest_path),
        "validated_after_candidate_freeze": True, "page_bytes_exposed_to_analyzer": False, "result": "pass",
    }
    validate_document(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(canonical_json_bytes(receipt))
    return receipt


def finalize_manifest(paths: BundlePaths, report: dict[str, Any], created_utc: str) -> dict[str, Any]:
    """Close the inventory over everything on disk except the manifest itself."""
    root = paths.root
    roles = {
        ARTIFACTS["plan"]: "plan", ARTIFACTS["frozen_candidate_set"]: "frozen_candidate_set",
        ARTIFACTS["analysis_report"]: "analysis_report", ARTIFACTS["holdout_structure_receipt"]: "holdout_structure_receipt",
    }
    for relative in ARTIFACTS["replica_environments"]:
        roles[relative] = "environment"
    for relative in ARTIFACTS["replica_artifact_manifests"]:
        roles[relative] = "replica_artifact_manifest"
    for relative in ARTIFACTS["replica_observations"]:
        roles[relative] = "replica_observation"
    files, total, blobs = [], 0, 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == ARTIFACTS["bundle_manifest"]:
            raise ValidationError("bundle manifest already present")
        payload = path.read_bytes()
        if relative.startswith(ARTIFACTS["page_store_directory"] + "/"):
            role, media = "page_blob", "application/octet-stream"
            blobs += 1
        elif relative.startswith(ARTIFACTS["page_index_directory"] + "/"):
            role, media = "page_index", "application/json"
        else:
            role, media = roles[relative], "application/json"
        files.append(_entry(relative, role, payload, media))
        total += len(payload)
    by_path = {row["path"]: row for row in files}
    decisive = report["scientific_outcome"] == "one_or_more_submodels_predict_holdout"
    manifest = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_bundle_manifest", "experiment_id": EXPERIMENT_ID,
        "campaign_id": paths.campaign_id, "producer_commit": paths.producer_commit, "repository_url": REPOSITORY_URL,
        "created_utc": created_utc, "plan_sha256": PLAN_SHA256, "provider_sha256": PROVIDER_SHA256, "replica_count": 3,
        "replica_artifact_manifest_sha256": [by_path[relative]["sha256"] for relative in ARTIFACTS["replica_artifact_manifests"]],
        "checkpoint_count": len(CHECKPOINT_IDS) * 3, "page_blob_count": blobs,
        "bundle_size_bytes_excluding_manifest": total, "inventory_closed": True, "hashes_verified": True, "paths_closed": True,
        "execution_status": "analysis_complete", "campaign_failed": False, "analysis_report_retained": True,
        "analysis_scientific_outcome": report["scientific_outcome"],
        "bundle_status": "decisive_pending_independent_validation" if decisive else "complete_no_scientific_outcome",
        "independent_validation_status": "not_independently_validated", "files": files,
        "replica_environment_sha256": [by_path[relative]["sha256"] for relative in ARTIFACTS["replica_environments"]],
        "holdout_structure_receipt_sha256": by_path[ARTIFACTS["holdout_structure_receipt"]]["sha256"],
    }
    validate_document(manifest)
    _write_json(root / ARTIFACTS["bundle_manifest"], manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    receipt = commands.add_parser("receipt", help="write the post-freeze holdout structure receipt")
    receipt.add_argument("--bundle-root", type=Path, required=True)
    receipt.add_argument("--candidate-sha256", required=True)
    receipt.add_argument("--campaign-id", required=True)
    receipt.add_argument("--producer-commit", required=True)
    receipt.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if len(arguments.candidate_sha256) != 64 or any(c not in "0123456789abcdef" for c in arguments.candidate_sha256):
            raise ValidationError("candidate SHA-256 is not lowercase hexadecimal")
        document = write_receipt(
            arguments.bundle_root, arguments.candidate_sha256, arguments.campaign_id,
            arguments.producer_commit, arguments.output,
        )
    except (OSError, ValidationError, ValueError, KeyError) as exc:
        print(f"A3 dry-run holdout receipt failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(arguments.output), "replica_artifact_manifest_sha256": document["replica_artifact_manifest_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
