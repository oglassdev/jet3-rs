#!/usr/bin/env python3
"""Assemble, finalize, and separately validate DAO A4 bundle trees.

This producer-side check is not the plan's independent recomputing validator."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from a4_analysis_input import ReplicaAnalysisInput

from a4_bundle_io import (
    ArtifactCache,
    TreeFile,
    copy_cached as _copy_cached,
    expected_directories as _expected_directories,
    inventory as _inventory,
    safe_locator as _safe_locator,
)
from a4_spec import (
    BOUNDS,
    CHECKED_PLAN_PATH,
    CHECKPOINT_IDS,
    EXPERIMENT_ID,
    LOGICAL_ROLES,
    PLAN,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    validate_schema,
)
from protocol_validation import ValidationError, canonical_json_bytes

PAGE_SIZE = 2_048
REPLICA_COUNT = 3
DERIVATION_REPLICA_COUNT = 2
CHECKPOINT_COUNT = 25
MAX_JSON_BYTES = 67_108_864
MAX_PAGE_BLOBS = 65_536
MAX_PAGE_STORE_BYTES = 134_217_728
MAX_BUNDLE_BYTES = 805_306_368
REPOSITORY_URL = "https://github.com/oglassdev/jet3-rs.git"
MANIFEST_PATH = "bundle-manifest.json"
PLAN_PATH = "plan/a4-row-anchored-maps.plan.json"
ROLES = tuple(LOGICAL_ROLES)
ROLE_BINDINGS = tuple(
    {role: row[role] for role in ROLES}
    for row in sorted(PLAN["tables"]["role_bindings"], key=lambda row: row["replica"])
)

_PINNED_BOUNDS = {
    "page_size": PAGE_SIZE,
    "replicas": REPLICA_COUNT,
    "planned_checkpoints_per_replica": CHECKPOINT_COUNT,
    "max_json_bytes": MAX_JSON_BYTES,
    "max_unique_page_blobs": MAX_PAGE_BLOBS,
    "max_retained_page_store_bytes": MAX_PAGE_STORE_BYTES,
    "max_bundle_bytes": MAX_BUNDLE_BYTES,
}
for _bound_name, _bound_value in _PINNED_BOUNDS.items():
    if BOUNDS[_bound_name] != _bound_value:
        raise RuntimeError(f"checked A4 bound drifted: {_bound_name}")
if len(CHECKPOINT_IDS) != CHECKPOINT_COUNT or len(ROLE_BINDINGS) != REPLICA_COUNT:
    raise RuntimeError("checked A4 checkpoint or replica count drifted")
if PLAN["artifacts"]["plan"] != PLAN_PATH:
    raise RuntimeError("checked A4 plan artifact path drifted")
@dataclass(frozen=True)
class ReplicaResult:
    root: Path
    cache: ArtifactCache
    replica: int
    manifest_path: str
    manifest: dict[str, Any]
    manifest_sha256: str
    manifest_size: int
    entries: dict[str, dict[str, Any]]
    environment: dict[str, Any]
    environment_sha256: str
    observation: dict[str, Any]
    provider_sha256: str
    producer_commit: str
    campaign_id: str
    page_paths: frozenset[str]
@dataclass(frozen=True)
class PayloadResult:
    tree: dict[str, TreeFile]
    directories: set[str]
    cache: ArtifactCache
    replicas: tuple[ReplicaResult, ...]
    expected_paths: frozenset[str]
    analysis: dict[str, Any] | None
    receipt: dict[str, Any] | None
    frozen_sha256: str | None


@dataclass(frozen=True)
class BundleReplicaSource:
    """Bounded page source backed by a structurally checked replica tree."""

    checkpoint_ids: tuple[str, ...]
    page_count: dict[str, int]
    ordered_page_sha256: dict[str, tuple[str, ...]]
    cache: ArtifactCache

    def page_bytes(self, sha256: str) -> bytes:
        return self.cache.read(f"page-store/{sha256}.page", PAGE_SIZE)
def _json_artifact(
    cache: ArtifactCache, locator: str
) -> tuple[dict[str, Any], bytes]:
    return cache.json(locator, MAX_JSON_BYTES)
def _require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValidationError(f"{label}: binding mismatch")
def _validate_typed(document: dict[str, Any], document_type: str, label: str) -> None:
    _require(document.get("document_type"), document_type, f"{label} document type")
    validate_schema(document, document_type)
def _validate_page_index(index: dict[str, Any], observation: dict[str, Any],
                         checkpoint: dict[str, Any], ordinal: int,
                         prior_hashes: list[str] | None) -> list[str]:
    _validate_typed(index, "dao_a4_page_index", checkpoint["page_index"]["path"])
    bindings = {
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": observation["producer_commit"],
        "campaign_id": observation["campaign_id"],
        "environment_sha256": observation["environment_sha256"],
        "provider_sha256": observation["provider_sha256"],
        "replica": observation["replica"],
        "checkpoint_id": CHECKPOINT_IDS[ordinal],
        "ordinal": ordinal,
        "predecessor_checkpoint_id": CHECKPOINT_IDS[ordinal - 1] if ordinal else None,
        "page_count": checkpoint["actual_file_pages"],
        "file_size_bytes": checkpoint["actual_size_bytes"],
    }
    for key, expected in bindings.items():
        _require(index[key], expected, f"page index {ordinal:02d} {key}")
    _require((checkpoint["checkpoint_id"], checkpoint["ordinal"]), (CHECKPOINT_IDS[ordinal], ordinal),
             f"checkpoint {ordinal:02d} identity")
    hashes = list(index["ordered_page_sha256"])
    _require((len(hashes), len(hashes) * PAGE_SIZE), (index["page_count"], index["file_size_bytes"]),
             f"page index {ordinal:02d} page accounting")
    expected_changed = [] if prior_hashes is None else [
        page for page in range(max(len(prior_hashes), len(hashes)))
        if page >= len(prior_hashes) or page >= len(hashes)
        or prior_hashes[page] != hashes[page]
    ]
    _require(index["changed_page_indices"], expected_changed, f"page index {ordinal:02d} changed pages")
    return hashes
def _entry_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    for index, entry in enumerate(manifest["files"]):
        locator = _safe_locator(entry["path"], f"$.files[{index}].path")
        if locator in entries or locator.casefold() in folded:
            raise ValidationError(f"$.files: duplicate path {locator!r}")
        entries[locator] = entry
        folded.add(locator.casefold())
    return entries
def _entry_payload(
    cache: ArtifactCache, entry: dict[str, Any]
) -> tuple[bytes, dict[str, Any] | None]:
    maximum = PAGE_SIZE if entry["role"] == "page_blob" else MAX_JSON_BYTES
    payload = cache.read(entry["path"], maximum)
    _require(len(payload), entry["size_bytes"], f"{entry['path']} size")
    _require(cache.sha256(entry["path"], maximum), entry["sha256"], f"{entry['path']} sha256")
    expected_media = "application/octet-stream" if entry["role"] == "page_blob" else "application/json"
    _require(entry["media_type"], expected_media, f"{entry['path']} media type")
    document = None if expected_media != "application/json" else cache.json(
        entry["path"], maximum
    )[0]
    return payload, document


def _validate_plan_entries(entries: dict[str, dict[str, Any]]) -> None:
    expected = {PLAN_PATH: ("plan", PLAN_SHA256)}
    for locator, (role, digest) in expected.items():
        entry = entries.get(locator)
        if entry is None:
            raise ValidationError(f"{locator}: missing retained plan inventory entry")
        _require(entry["role"], role, f"{locator} role")
        _require(entry["media_type"], "application/json", f"{locator} media type")
        _require(entry["sha256"], digest, f"{locator} pinned hash")


def _validate_replica(
    root: Path,
    tree: dict[str, TreeFile],
    directories: set[str],
    cache: ArtifactCache,
    replica: int,
    campaign_id: str | None,
    *,
    closed: bool,
) -> ReplicaResult:
    manifest_path = f"replica-artifacts/replica-{replica:02d}-manifest.json"
    manifest, manifest_payload = _json_artifact(cache, manifest_path)
    _validate_typed(manifest, "dao_a4_replica_artifact_manifest", manifest_path)
    _require(manifest["checkpoint_count"], CHECKPOINT_COUNT, f"replica {replica} checkpoint count")
    _require(manifest["replica"], replica, f"replica {replica} manifest replica")
    if campaign_id is not None:
        _require(manifest["campaign_id"], campaign_id, f"replica {replica} campaign")
    entries = _entry_map(manifest)
    if any(entry["role"] == "acquisition_log" for entry in entries.values()):
        raise ValidationError("replica outputs may not contain acquisition logs")
    expected_files = set(entries) | {manifest_path}
    if closed:
        _require(set(tree), expected_files, f"replica {replica} closed inventory")
        _require(directories, _expected_directories(expected_files), f"replica {replica} directory closure")

    documents: dict[str, dict[str, Any]] = {}
    for entry in entries.values():
        _, document = _entry_payload(cache, entry)
        if document is not None:
            documents[entry["path"]] = document

    environment_path = f"environment/replica-{replica:02d}.json"
    observation_path = f"observations/replica-{replica:02d}.json"
    environment_entry = entries.get(environment_path)
    observation_entry = entries.get(observation_path)
    if environment_entry is None or environment_entry["role"] != "environment":
        raise ValidationError(f"{environment_path}: missing environment role")
    if observation_entry is None or observation_entry["role"] != "replica_observation":
        raise ValidationError(f"{observation_path}: missing observation role")
    environment = documents[environment_path]
    observation = documents[observation_path]
    _validate_typed(environment, "dao_a4_environment", environment_path)
    _validate_typed(observation, "dao_a4_replica_observation", observation_path)
    _require(len(observation["checkpoints"]), CHECKPOINT_COUNT, f"{observation_path} checkpoints")
    expected_binding = {
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": manifest["producer_commit"],
        "campaign_id": manifest["campaign_id"],
        "replica": replica,
    }
    for key, expected in expected_binding.items():
        _require(environment[key], expected, f"{environment_path} {key}")
        _require(observation[key], expected, f"{observation_path} {key}")
    _require(environment["matrix_job_id"], manifest["matrix_job_id"], "environment matrix job")
    _require(observation["matrix_job"]["job_id"], manifest["matrix_job_id"], "observation matrix job")
    _require(environment_entry["sha256"], manifest["environment_sha256"], "environment manifest hash")
    _require(observation["environment_sha256"], environment_entry["sha256"], "observation environment hash")
    provider_sha256 = environment["provider"]["server_sha256"]
    _require(provider_sha256, manifest["provider_sha256"], "manifest provider hash")
    _require(observation["provider_sha256"], provider_sha256, "observation provider hash")
    _require(observation["repository_url"], REPOSITORY_URL, "observation repository URL")
    _require(observation["role_binding"], dict(ROLE_BINDINGS[replica - 1]), "observation role binding")

    referenced_pages: set[str] = set()
    prior_hashes: list[str] | None = None
    changed_total = 0
    schema_snapshots: dict[str, dict[str, Any]] = {}
    page_indexes: dict[str, dict[str, Any]] = {}
    for ordinal, checkpoint in enumerate(observation["checkpoints"]):
        checkpoint_id = CHECKPOINT_IDS[ordinal]
        expected_path = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint_id}.json"
        reference = checkpoint["page_index"]
        _require(reference["path"], expected_path, f"replica {replica} page index path")
        entry = entries.get(expected_path)
        if entry is None or entry["role"] != "page_index":
            raise ValidationError(f"{expected_path}: missing page-index role")
        _require(reference["sha256"], entry["sha256"], f"{expected_path} reference hash")
        _require(reference["size_bytes"], entry["size_bytes"], f"{expected_path} reference size")
        index = documents[expected_path]
        page_indexes[checkpoint_id] = index
        hashes = _validate_page_index(index, observation, checkpoint, ordinal, prior_hashes)
        reconstruction = hashlib.sha256()
        for digest in hashes:
            page_path = f"page-store/{digest}.page"
            page_entry = entries.get(page_path)
            if page_entry is None or page_entry["role"] != "page_blob":
                raise ValidationError(f"{page_path}: referenced blob is absent")
            payload = cache.read(page_path, PAGE_SIZE)
            _require(len(payload), PAGE_SIZE, f"{page_path} page size")
            _require(cache.sha256(page_path, PAGE_SIZE), digest, f"{page_path} content address")
            reconstruction.update(payload)
            referenced_pages.add(page_path)
        _require(reconstruction.hexdigest(), index["database_sha256"], f"{expected_path} database hash")
        changed_total += len(index["changed_page_indices"])
        prior_hashes = hashes
        schema_path = (
            f"schema-snapshots/replica-{replica:02d}/"
            f"{ordinal:02d}-{checkpoint_id}.json"
        )
        schema_reference = checkpoint["dao_schema_snapshot"]
        _require(schema_reference["path"], schema_path,
                 f"replica {replica} schema snapshot path")
        schema_entry = entries.get(schema_path)
        if schema_entry is None or schema_entry["role"] != "dao_schema_snapshot":
            raise ValidationError(f"{schema_path}: missing DAO schema-snapshot role")
        _require(schema_reference["sha256"], schema_entry["sha256"],
                 f"{schema_path} reference hash")
        _require(schema_reference["size_bytes"], schema_entry["size_bytes"],
                 f"{schema_path} reference size")
        snapshot = documents[schema_path]
        _validate_typed(snapshot, "dao_a4_schema_snapshot", schema_path)
        schema_snapshots[checkpoint_id] = snapshot
    _require(observation["changed_hash_entries"], changed_total, f"replica {replica} changed-hash total")
    _validate_target_disclosures(observation)
    page_paths = {path for path, entry in entries.items() if entry["role"] == "page_blob"}
    _require(page_paths, referenced_pages, f"replica {replica} page-store closure")
    if len(page_paths) > MAX_PAGE_BLOBS or len(page_paths) * PAGE_SIZE > MAX_PAGE_STORE_BYTES:
        raise ValidationError(f"replica {replica}: page-store bound exceeded")
    return ReplicaResult(
        root,
        cache,
        replica,
        manifest_path,
        manifest,
        cache.sha256(manifest_path, MAX_JSON_BYTES),
        len(manifest_payload),
        entries,
        environment,
        environment_entry["sha256"],
        observation,
        provider_sha256,
        manifest["producer_commit"],
        manifest["campaign_id"],
        frozenset(page_paths),
    )
def _validate_environments(replicas: Iterable[ReplicaResult]) -> None:
    rows = tuple(replicas)
    for attribute in ("campaign_id", "producer_commit", "provider_sha256"):
        if len({getattr(row, attribute) for row in rows}) != 1:
            raise ValidationError(f"cross-replica {attribute} differs")
    exact = (
        lambda row: row.environment["provider"]["prog_id"],
        lambda row: row.environment["provider"]["clsid"],
        lambda row: row.environment["provider"]["server_sha256"],
        lambda row: row.environment["host"]["process_architecture"],
        lambda row: int(row.environment["host"]["powershell_version"].split(".", 1)[0]),
    )
    for projection in exact:
        if len({projection(row) for row in rows}) != 1:
            raise ValidationError("cross-replica exact environment field differs")


def _validate_target_disclosures(observation: dict[str, Any]) -> None:
    checkpoints = {row["checkpoint_id"]: row for row in observation["checkpoints"]}
    baselines = {
        "T1": checkpoints["T4_CREATE"]["actual_file_pages"],
        "T4": checkpoints["T3_ABS_16480"]["actual_file_pages"],
    }
    for checkpoint_id, checkpoint in checkpoints.items():
        role = checkpoint_id.split("_", 1)[0]
        if role in baselines and checkpoint_id.startswith(f"{role}_REL_"):
            baseline = baselines[role]
            target = int(checkpoint_id.rsplit("_", 1)[1])
            threshold = baseline + target
        elif checkpoint_id.startswith("T3_ABS_"):
            baseline = None
            threshold = int(checkpoint_id.rsplit("_", 1)[1])
        else:
            continue
        _require(checkpoint["target_baseline_pages"], baseline,
                 f"{checkpoint_id} target baseline")
        _require(checkpoint["target_threshold_pages"], threshold,
                 f"{checkpoint_id} target threshold")
        _require(checkpoint["target_overshoot_pages"],
                 checkpoint["actual_file_pages"] - threshold,
                 f"{checkpoint_id} target overshoot")
        if checkpoint["actual_file_pages"] < threshold:
            raise ValidationError(f"{checkpoint_id}: target threshold was not reached")


def _validate_frozen(document: dict[str, Any], payload: bytes, campaign_id: str) -> None:
    validate_schema(document, "dao_a4_frozen_derivation_candidates")
    _require(payload, canonical_json_bytes(document).rstrip(b"\n"),
             "frozen candidate canonical bytes")
    bindings = {
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": campaign_id,
        "derivation_replicas": [1, 2],
    }
    for key, expected in bindings.items():
        _require(document[key], expected, f"frozen candidate {key}")
def _validate_analysis_payload(
    cache: ArtifactCache, replicas: tuple[ReplicaResult, ...]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    frozen_path = "analysis/derivation-candidates.json"
    analysis_path = "analysis/analysis-report.json"
    receipt_path = "analysis/holdout-structure-receipt.json"
    frozen, frozen_payload = _json_artifact(cache, frozen_path)
    report, _ = _json_artifact(cache, analysis_path)
    receipt, _ = _json_artifact(cache, receipt_path)
    campaign_id = replicas[0].campaign_id
    frozen_sha256 = cache.sha256(frozen_path, MAX_JSON_BYTES)
    _validate_frozen(frozen, frozen_payload, campaign_id)
    occurrence_reference = frozen.get("h4_occurrence_evidence")
    if occurrence_reference is not None:
        occurrence_path = occurrence_reference["path"]
        occurrence_payload = cache.read(occurrence_path, MAX_JSON_BYTES)
        _require(len(occurrence_payload), occurrence_reference["size_bytes"],
                 "H4 occurrence evidence size")
        _require(hashlib.sha256(occurrence_payload).hexdigest(),
                 occurrence_reference["sha256"], "H4 occurrence evidence hash")
        occurrence = cache.json(occurrence_path, MAX_JSON_BYTES)[0]
        validate_schema(occurrence, "dao_a4_h4_occurrence_evidence")
    _require(report.get("document_type"), "dao_a4_analysis_report", f"{analysis_path} document type")
    validate_schema(report, "dao_a4_analysis_report")
    _validate_typed(receipt, "dao_a4_holdout_structure_receipt", receipt_path)
    _require(report["plan_sha256"], PLAN_SHA256, "analysis plan_sha256")
    _require(report["holdout_replica"], REPLICA_COUNT, "analysis holdout replica")
    _require(receipt["replica"], REPLICA_COUNT, "holdout receipt replica")
    _require(receipt["result"], "pass", "holdout receipt result")
    bindings = {
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": campaign_id,
        "producer_commit": replicas[0].producer_commit,
        "derivation_candidate_set_sha256": frozen_sha256,
    }
    for key, expected in bindings.items():
        _require(report[key], expected, f"analysis {key}")
        _require(receipt[key], expected, f"holdout receipt {key}")
    _require(
        receipt["replica_artifact_manifest_sha256"],
        replicas[2].manifest_sha256,
        "holdout receipt replica manifest hash",
    )
    return report, receipt, frozen_sha256
def _validate_payload(root: Path, *, require_analysis: bool,
                      allow_manifest: bool = False,
                      replica_count: int = REPLICA_COUNT,
                      tree: dict[str, TreeFile] | None = None,
                      directories: set[str] | None = None,
                      cache: ArtifactCache | None = None) -> PayloadResult:
    root = root.absolute()
    if tree is None or directories is None:
        if tree is not None or directories is not None or cache is not None:
            raise ValidationError("partial A4 validation context")
        tree, directories = _inventory(root)
    if cache is None:
        cache = ArtifactCache(root, tree, MAX_BUNDLE_BYTES)
    elif cache.root != root or cache.tree is not tree:
        raise ValidationError("A4 validation cache does not match its inventory")
    retained_plans = {PLAN_PATH: (CHECKED_PLAN_PATH, PLAN_SHA256)}
    for locator, (checked_path, digest) in retained_plans.items():
        plan_payload = cache.read(locator, MAX_JSON_BYTES)
        _require(cache.sha256(locator, MAX_JSON_BYTES), digest,
                 f"retained {locator} hash")
        _require(plan_payload, checked_path.read_bytes(), f"retained checked {locator}")
    replicas = tuple(
        _validate_replica(
            root, tree, directories, cache, replica, None, closed=False
        )
        for replica in range(1, replica_count + 1)
    )
    _validate_environments(replicas)
    expected = set(retained_plans)
    for replica in replicas:
        expected.add(replica.manifest_path)
        expected.update(replica.entries)
    report = None
    receipt = None
    frozen_sha256 = None
    if require_analysis:
        analysis_paths = {
            "analysis/derivation-candidates.json",
            "analysis/analysis-report.json",
            "analysis/holdout-structure-receipt.json",
        }
        report, receipt, frozen_sha256 = _validate_analysis_payload(cache, replicas)
        frozen = cache.json("analysis/derivation-candidates.json", MAX_JSON_BYTES)[0]
        if frozen.get("h4_occurrence_evidence") is not None:
            analysis_paths.add(frozen["h4_occurrence_evidence"]["path"])
        expected.update(analysis_paths)
    complete_expected = expected | ({MANIFEST_PATH} if allow_manifest else set())
    _require(set(tree), complete_expected, "A4 payload closed inventory")
    _require(directories, _expected_directories(complete_expected),
             "A4 payload directory closure")
    page_paths = set().union(*(row.page_paths for row in replicas))
    if len(page_paths) > MAX_PAGE_BLOBS or len(page_paths) * PAGE_SIZE > MAX_PAGE_STORE_BYTES:
        raise ValidationError("A4 merged page store exceeds its fixed bound")
    if sum(item.size for item in tree.values()) > MAX_BUNDLE_BYTES:
        raise ValidationError("A4 bundle exceeds its fixed byte bound")
    return PayloadResult(tree, directories, cache, replicas, frozenset(expected),
                         report, receipt, frozen_sha256)
def _copy_replica(
    replica: ReplicaResult,
    destination: Path,
    copied: dict[str, tuple[int, str]],
) -> None:
    _copy_cached(
        replica.cache, destination, replica.manifest_path,
        replica.manifest_size, replica.manifest_sha256, MAX_JSON_BYTES, copied,
    )
    for entry in replica.entries.values():
        maximum = PAGE_SIZE if entry["role"] == "page_blob" else MAX_JSON_BYTES
        _copy_cached(
            replica.cache, destination, entry["path"],
            entry["size_bytes"], entry["sha256"], maximum, copied,
        )


def _analysis_input(replica: ReplicaResult) -> ReplicaAnalysisInput:
    indexes: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    counts: dict[str, dict[str, int]] = {}
    page_counts: dict[str, int] = {}
    page_hashes: dict[str, tuple[str, ...]] = {}
    for ordinal, checkpoint in enumerate(replica.observation["checkpoints"]):
        checkpoint_id = CHECKPOINT_IDS[ordinal]
        index_path = checkpoint["page_index"]["path"]
        snapshot_path = checkpoint["dao_schema_snapshot"]["path"]
        index = replica.cache.json(index_path, MAX_JSON_BYTES)[0]
        snapshot = replica.cache.json(snapshot_path, MAX_JSON_BYTES)[0]
        indexes[checkpoint_id] = index
        snapshots[checkpoint_id] = snapshot
        counts[checkpoint_id] = dict(checkpoint["table_row_counts"])
        page_counts[checkpoint_id] = int(index["page_count"])
        page_hashes[checkpoint_id] = tuple(index["ordered_page_sha256"])
    environment_path = f"environment/replica-{replica.replica:02d}.json"
    source = BundleReplicaSource(
        CHECKPOINT_IDS, page_counts, page_hashes, replica.cache
    )
    return ReplicaAnalysisInput(
        source=source,
        table_row_counts=counts,
        replica_observation=replica.observation,
        page_indexes=indexes,
        schema_snapshots=snapshots,
        artifact_manifest=replica.manifest,
        environment_payload=replica.cache.read(environment_path, MAX_JSON_BYTES),
    )


def analyze_bundle(
    bundle_root: Path,
    holdout_root: Path,
    campaign_id: str,
    producer_commit: str,
    holdout_command: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Freeze derivation bytes, materialize replica 3, then run A4 analysis."""
    from a4_analysis import analyze
    from a4_holdout import run_holdout_process

    bundle_root = bundle_root.absolute()
    holdout_root = holdout_root.absolute()
    payload = _validate_payload(
        bundle_root, require_analysis=False,
        replica_count=DERIVATION_REPLICA_COUNT,
    )
    _require(payload.replicas[0].campaign_id, campaign_id, "expected campaign")
    _require(payload.replicas[0].producer_commit, producer_commit, "expected producer")
    if holdout_root.exists():
        raise ValidationError("holdout root existed before derivation freeze")
    analysis_root = bundle_root / "analysis"
    analysis_root.mkdir()
    candidate_path = analysis_root / "derivation-candidates.json"
    occurrence_path = analysis_root / "h4-occurrence-evidence.json"
    report_path = analysis_root / "analysis-report.json"
    receipt_path = analysis_root / "holdout-structure-receipt.json"
    freeze_state = bundle_root.parent / f".{bundle_root.name}-a4-freeze.json"
    if freeze_state.exists():
        raise ValidationError("A4 freeze-state path already exists")

    def acquire_holdout(frozen_bytes: bytes, frozen_sha256: str) -> ReplicaAnalysisInput:
        with candidate_path.open("xb") as handle:
            handle.write(frozen_bytes)
        state = {
            "document_type": "dao_a4_internal_freeze_phase",
            "campaign_id": campaign_id,
            "producer_commit": producer_commit,
            "derivation_candidate_set_sha256": frozen_sha256,
            "freeze_phase_completed": True,
            "replica_3_artifact_existed_before_freeze_phase_completed": False,
            "analyzer_replica_3_opens_before_receipt": 0,
        }
        with freeze_state.open("xb") as handle:
            handle.write(canonical_json_bytes(state))
        if holdout_command is None:
            raise ValidationError("holdout materialization command is required")
        try:
            completed = subprocess.run(
                holdout_command, check=False, timeout=300,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError(f"holdout materialization failed: {exc}") from exc
        if completed.returncode != 0:
            raise ValidationError("holdout materialization returned nonzero")
        run_holdout_process(
            bundle_root, holdout_root, candidate_path, frozen_sha256,
            campaign_id, producer_commit, receipt_path, freeze_state,
        )
        tree, directories = _inventory(bundle_root)
        cache = ArtifactCache(bundle_root, tree, MAX_BUNDLE_BYTES)
        holdout = _validate_replica(
            bundle_root, tree, directories, cache, 3, campaign_id, closed=False
        )
        return _analysis_input(holdout)

    try:
        result = analyze(
            campaign_id,
            producer_commit,
            {replica.replica: _analysis_input(replica) for replica in payload.replicas},
            acquire_holdout,
        )
        if result.frozen.occurrence_evidence_bytes is not None:
            with occurrence_path.open("xb") as handle:
                handle.write(result.frozen.occurrence_evidence_bytes)
        with report_path.open("xb") as handle:
            handle.write(canonical_json_bytes(dict(result.report)))
    finally:
        try:
            freeze_state.unlink()
        except FileNotFoundError:
            pass
    return {
        "campaign_id": campaign_id,
        "derivation_candidate_set_sha256": result.frozen.sha256,
        "scientific_outcome": result.report["scientific_outcome"],
    }
def assemble_bundle(replica_roots: Iterable[Path], bundle_root: Path,
                    campaign_id: str, producer_commit: str) -> dict[str, Any]:
    """Assemble the derivation replicas only; the holdout is grafted after the freeze."""
    roots = tuple(Path(root).absolute() for root in replica_roots)
    if len(roots) != DERIVATION_REPLICA_COUNT:
        raise ValidationError("assemble requires exactly the two derivation replica roots")
    if bundle_root.exists():
        raise ValidationError("bundle root already exists")
    validated = []
    for replica, root in enumerate(roots, start=1):
        tree, directories = _inventory(root)
        cache = ArtifactCache(root, tree, MAX_BUNDLE_BYTES)
        validated.append(_validate_replica(
            root, tree, directories, cache, replica, campaign_id, closed=True))
    replicas = tuple(validated)
    _validate_environments(replicas)
    _require(replicas[0].producer_commit, producer_commit, "expected producer commit")
    bundle_parent = bundle_root.absolute().parent
    bundle_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".a4-assemble-", dir=bundle_parent))
    try:
        plan_path = staging.joinpath(*PLAN_PATH.split("/"))
        plan_path.parent.mkdir(parents=True)
        plan_path.write_bytes(CHECKED_PLAN_PATH.read_bytes())
        copied: dict[str, tuple[int, str]] = {}
        for replica in replicas:
            _copy_replica(replica, staging, copied)
        result = _validate_payload(staging, require_analysis=False,
                                   replica_count=DERIVATION_REPLICA_COUNT)
        os.replace(staging, bundle_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "bundle_root": str(bundle_root),
        "campaign_id": campaign_id,
        "producer_commit": result.replicas[0].producer_commit,
        "provider_sha256": result.replicas[0].provider_sha256,
    }
def validate_holdout_replica(bundle_root: Path, candidate_path: Path,
                             candidate_sha256: str, campaign_id: str,
                             producer_commit: str) -> ReplicaResult:
    root = bundle_root.absolute()
    tree, directories = _inventory(root)
    cache = ArtifactCache(root, tree, MAX_BUNDLE_BYTES)
    replica = _validate_replica(
        root, tree, directories, cache, REPLICA_COUNT, None, closed=False
    )
    _require(replica.campaign_id, campaign_id, "expected holdout campaign")
    _require(replica.producer_commit, producer_commit, "expected holdout producer")
    candidate_locator = candidate_path.absolute().relative_to(root).as_posix()
    payload = cache.read(candidate_locator, MAX_JSON_BYTES)
    _require(
        cache.sha256(candidate_locator, MAX_JSON_BYTES),
        candidate_sha256,
        "frozen candidate hash",
    )
    frozen = cache.json(candidate_locator, MAX_JSON_BYTES)[0]
    _validate_frozen(frozen, payload, replica.campaign_id)
    return replica
def _role_for_path(path: str, replicas: tuple[ReplicaResult, ...]) -> str:
    fixed = {
        PLAN_PATH: "plan",
        "analysis/derivation-candidates.json": "frozen_candidate_set",
        "analysis/analysis-report.json": "analysis_report",
        "analysis/h4-occurrence-evidence.json": "h4_occurrence_evidence",
        "analysis/holdout-structure-receipt.json": "holdout_structure_receipt",
    }
    if path in fixed:
        return fixed[path]
    for replica in replicas:
        if path == replica.manifest_path:
            return "replica_artifact_manifest"
        if path in replica.entries:
            return replica.entries[path]["role"]
    raise ValidationError(f"{path}: cannot assign bundle role")


def _utc_epoch(value: str, label: str) -> float:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except (AttributeError, ValueError) as exc:
        raise ValidationError(f"{label}: invalid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{label}: timestamp lacks a UTC offset")
    return parsed.timestamp()


def _campaign_elapsed(campaign_started_utc: str, created_utc: str) -> int:
    elapsed = math.floor(
        _utc_epoch(created_utc, "created_utc")
        - _utc_epoch(campaign_started_utc, "campaign_started_utc")
    )
    if elapsed < 0 or elapsed > BOUNDS["campaign_timeout_seconds"]:
        raise ValidationError("campaign elapsed seconds exceed the retained-evidence bound")
    return elapsed


def _created_utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def finalize_bundle(bundle_root: Path, campaign_id: str, producer_commit: str,
                    campaign_started_utc: str, *, created_utc: str | None = None) -> dict[str, Any]:
    bundle_root = bundle_root.absolute()
    if (bundle_root / MANIFEST_PATH).exists():
        raise ValidationError("bundle manifest already exists")
    payload = _validate_payload(bundle_root, require_analysis=True)
    assert payload.analysis is not None and payload.receipt is not None
    _require(payload.replicas[0].campaign_id, campaign_id, "expected campaign")
    _require(payload.replicas[0].producer_commit, producer_commit, "expected producer")
    files = []
    for path in sorted(payload.expected_paths):
        item = payload.tree[path]
        role = _role_for_path(path, payload.replicas)
        maximum = PAGE_SIZE if role == "page_blob" else MAX_JSON_BYTES
        digest = payload.cache.sha256(path, maximum)
        files.append(
            {
                "path": path,
                "role": role,
                "sha256": digest,
                "size_bytes": item.size,
                "media_type": "application/octet-stream" if role == "page_blob" else "application/json",
            }
        )
    created_utc = created_utc or _created_utc_now()
    campaign_elapsed_seconds = _campaign_elapsed(campaign_started_utc, created_utc)
    receipt_sha256 = next(
        row["sha256"]
        for row in files
        if row["path"] == "analysis/holdout-structure-receipt.json"
    )
    decisive = payload.analysis["scientific_outcome"] == "one_or_more_layers_predict_holdout"
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_bundle_manifest",
        "experiment_id": EXPERIMENT_ID,
        "campaign_id": payload.replicas[0].campaign_id,
        "producer_commit": payload.replicas[0].producer_commit,
        "repository_url": REPOSITORY_URL,
        "created_utc": created_utc,
        "campaign_started_utc": campaign_started_utc,
        "campaign_elapsed_seconds": campaign_elapsed_seconds,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "replica_environment_sha256": [row.environment_sha256 for row in payload.replicas],
        "provider_sha256": payload.replicas[0].provider_sha256,
        "replica_count": REPLICA_COUNT,
        "replica_artifact_manifest_sha256": [row.manifest_sha256 for row in payload.replicas],
        "checkpoint_count": REPLICA_COUNT * CHECKPOINT_COUNT,
        "page_blob_count": sum(1 for row in files if row["role"] == "page_blob"),
        "bundle_size_bytes_excluding_manifest": sum(row["size_bytes"] for row in files),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "execution_status": "analysis_complete",
        "campaign_failed": False,
        "holdout_structure_receipt_sha256": receipt_sha256,
        "analysis_report_retained": True,
        "analysis_scientific_outcome": (
            "one_or_more_submodels_predict_holdout"
            if decisive else "no_submodel_predicts_holdout"
        ),
        "bundle_status": (
            "decisive_pending_independent_validation"
            if decisive
            else "complete_no_scientific_outcome"
        ),
        "independent_validation_status": "not_independently_validated",
        "files": files,
    }
    validate_schema(manifest, "dao_a4_bundle_manifest")
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > MAX_JSON_BYTES:
        raise ValidationError("bundle manifest exceeds its fixed JSON bound")
    if manifest["bundle_size_bytes_excluding_manifest"] + len(manifest_bytes) > MAX_BUNDLE_BYTES:
        raise ValidationError("complete A4 bundle exceeds its fixed byte bound")
    with (bundle_root / MANIFEST_PATH).open("xb") as handle:
        handle.write(manifest_bytes)
    return manifest
def validate_bundle(bundle_root: Path, campaign_id: str,
                    producer_commit: str) -> dict[str, Any]:
    bundle_root = bundle_root.absolute()
    tree, directories = _inventory(bundle_root)
    cache = ArtifactCache(bundle_root, tree, MAX_BUNDLE_BYTES)
    manifest, _ = _json_artifact(cache, MANIFEST_PATH)
    _validate_typed(manifest, "dao_a4_bundle_manifest", MANIFEST_PATH)
    _require(
        manifest["campaign_elapsed_seconds"],
        _campaign_elapsed(manifest["campaign_started_utc"], manifest["created_utc"]),
        "bundle manifest campaign timing",
    )
    entries = _entry_map(manifest)
    _validate_plan_entries(entries)
    _require(set(tree), set(entries) | {MANIFEST_PATH}, "complete bundle inventory")
    _require(directories, _expected_directories(entries), "complete bundle directories")
    for entry in entries.values():
        _entry_payload(cache, entry)
    payload = _validate_payload(bundle_root, require_analysis=True,
                                allow_manifest=True, tree=tree,
                                directories=directories, cache=cache)
    assert payload.analysis is not None and payload.receipt is not None
    _require(manifest["campaign_id"], campaign_id, "expected campaign")
    _require(manifest["producer_commit"], producer_commit, "expected producer commit")
    expected = {
        "experiment_id": EXPERIMENT_ID,
        "campaign_id": payload.replicas[0].campaign_id,
        "producer_commit": payload.replicas[0].producer_commit,
        "repository_url": REPOSITORY_URL,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "replica_environment_sha256": [row.environment_sha256 for row in payload.replicas],
        "provider_sha256": payload.replicas[0].provider_sha256,
        "replica_artifact_manifest_sha256": [row.manifest_sha256 for row in payload.replicas],
        "holdout_structure_receipt_sha256": entries["analysis/holdout-structure-receipt.json"]["sha256"],
        "analysis_scientific_outcome": (
            "one_or_more_submodels_predict_holdout"
            if payload.analysis["scientific_outcome"]
            == "one_or_more_layers_predict_holdout"
            else "no_submodel_predicts_holdout"
        ),
    }
    for key, value in expected.items():
        _require(manifest[key], value, f"bundle manifest {key}")
    _require(
        manifest["bundle_size_bytes_excluding_manifest"],
        sum(entry["size_bytes"] for entry in entries.values()),
        "bundle manifest retained bytes",
    )
    _require(
        manifest["page_blob_count"],
        sum(entry["role"] == "page_blob" for entry in entries.values()),
        "bundle manifest page count",
    )
    observed_tree, observed_directories = _inventory(bundle_root)
    _require(observed_tree, tree, "bundle stability")
    _require(observed_directories, directories, "bundle directory stability")
    return {
        "manifest": manifest,
        "manifest_sha256": cache.sha256(MANIFEST_PATH, MAX_JSON_BYTES),
        "analysis": payload.analysis,
        "replicas": [row.observation for row in payload.replicas],
    }
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--replica-root", action="append", type=Path, required=True)
    assemble.add_argument("--bundle-root", type=Path, required=True)
    assemble.add_argument("--campaign-id", required=True)
    assemble.add_argument("--producer-commit", required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--bundle-root", type=Path, required=True)
    finalize.add_argument("--campaign-id", required=True)
    finalize.add_argument("--producer-commit", required=True)
    finalize.add_argument("--campaign-started-utc", required=True)
    analyze_command = subparsers.add_parser("analyze")
    analyze_command.add_argument("--bundle-root", type=Path, required=True)
    analyze_command.add_argument("--holdout-root", type=Path, required=True)
    analyze_command.add_argument("--campaign-id", required=True)
    analyze_command.add_argument("--producer-commit", required=True)
    analyze_command.add_argument("--holdout-command-executable", required=True)
    analyze_command.add_argument(
        "--holdout-command-argument", action="append", default=[]
    )
    validate = subparsers.add_parser("validate")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--campaign-id", required=True)
    validate.add_argument("--producer-commit", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "assemble":
            result = assemble_bundle(arguments.replica_root, arguments.bundle_root,
                                     arguments.campaign_id, arguments.producer_commit)
        elif arguments.command == "analyze":
            result = analyze_bundle(
                arguments.bundle_root,
                arguments.holdout_root,
                arguments.campaign_id,
                arguments.producer_commit,
                (
                    arguments.holdout_command_executable,
                    *arguments.holdout_command_argument,
                ),
            )
        elif arguments.command == "finalize":
            manifest = finalize_bundle(
                arguments.bundle_root, arguments.campaign_id, arguments.producer_commit,
                arguments.campaign_started_utc)
            result = {
                "bundle_root": str(arguments.bundle_root),
                "campaign_id": manifest["campaign_id"],
                "bundle_status": manifest["bundle_status"],
                "file_count": len(manifest["files"]),
            }
        else:
            bundle_root = arguments.bundle
            validation = validate_bundle(
                bundle_root, arguments.campaign_id, arguments.producer_commit)
            result = {
                "bundle_root": str(bundle_root),
                "campaign_id": validation["manifest"]["campaign_id"],
                "bundle_status": validation["manifest"]["bundle_status"],
                "manifest_sha256": validation["manifest_sha256"],
            }
    except (OSError, ValidationError) as exc:
        print(f"A4 bundle {arguments.command} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
