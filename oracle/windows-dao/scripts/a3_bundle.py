#!/usr/bin/env python3
"""Assemble, finalize, and separately validate DAO A3 bundle trees.

This producer-side check is not the plan's independent recomputing validator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from a3_spec import (
    BOUNDS,
    CHECKED_PLAN,
    CHECKPOINT_IDS,
    EXPERIMENT_ID,
    PLAN,
    PLAN_SHA256,
    validate_analysis_report,
    validate_document,
    validate_frozen_candidates,
)
from protocol_validation import ValidationError, canonical_json_bytes

PAGE_SIZE = 2_048
REPLICA_COUNT = 3
CHECKPOINT_COUNT = 25
MAX_JSON_BYTES = 67_108_864
MAX_PAGE_BLOBS = 65_536
MAX_PAGE_STORE_BYTES = 536_870_912
MAX_BUNDLE_BYTES = 805_306_368
MAX_TREE_ENTRIES = 65_750
MAX_TREE_DEPTH = 8
REPOSITORY_URL = "https://github.com/oglassdev/jet3-rs.git"
MANIFEST_PATH = "bundle-manifest.json"
PLAN_PATH = "plan/a3-allocation-maps.plan.json"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ROLES = ("D", "L", "P", "H")
ROLE_BINDINGS = tuple(
    {role: row[role] for role in ROLES}
    for row in sorted(PLAN.document["tables"]["role_bindings"], key=lambda row: row["replica"])
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
        raise RuntimeError(f"checked A3 bound drifted: {_bound_name}")
if len(CHECKPOINT_IDS) != CHECKPOINT_COUNT or len(ROLE_BINDINGS) != REPLICA_COUNT:
    raise RuntimeError("checked A3 checkpoint or replica count drifted")
if PLAN.document["artifacts"]["plan"] != PLAN_PATH:
    raise RuntimeError("checked A3 plan artifact path drifted")
@dataclass(frozen=True)
class TreeFile:
    size: int
    modified_ns: int
    device: int
    inode: int
    links: int
@dataclass(frozen=True)
class ReplicaResult:
    root: Path
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
    replicas: tuple[ReplicaResult, ...]
    expected_paths: frozenset[str]
    analysis: dict[str, Any] | None
    receipt: dict[str, Any] | None
    frozen_sha256: str | None
def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )
def _safe_locator(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        raise ValidationError(f"{label}: unsafe artifact path")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ValidationError(f"{label}: unsafe artifact path")
    if candidate.as_posix() != value:
        raise ValidationError(f"{label}: non-canonical artifact path")
    return value
def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_nlink
def _path_identity(path: Path, metadata: os.stat_result) -> tuple[int, int, int]:
    if os.name != "nt":
        return _identity(metadata)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        return _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)
def _inventory(root: Path) -> tuple[dict[str, TreeFile], set[str]]:
    root = root.resolve()
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect tree root: {exc}") from exc
    if root.is_symlink() or _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError(f"{root}: tree root must be a regular directory")
    files: dict[str, TreeFile] = {}
    directories: set[str] = set()
    folded: dict[str, str] = {}
    identities: set[tuple[int, int]] = set()
    pending = [(root, 0)]
    seen = 0
    try:
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as children:
                for child in children:
                    seen += 1
                    if seen > MAX_TREE_ENTRIES:
                        raise ValidationError("A3 tree exceeds its entry ceiling")
                    child_metadata = child.stat(follow_symlinks=False)
                    if child.is_symlink() or _is_reparse(child_metadata):
                        raise ValidationError(f"{child.path}: links are forbidden")
                    locator = Path(child.path).relative_to(root).as_posix()
                    _safe_locator(locator, child.path)
                    prior = folded.get(locator.casefold())
                    if prior is not None and prior != locator:
                        raise ValidationError(f"{locator}: case-colliding tree entry")
                    folded[locator.casefold()] = locator
                    if stat.S_ISDIR(child_metadata.st_mode):
                        if depth >= MAX_TREE_DEPTH:
                            raise ValidationError("A3 tree exceeds its depth ceiling")
                        directories.add(locator)
                        pending.append((Path(child.path), depth + 1))
                    elif stat.S_ISREG(child_metadata.st_mode):
                        file_identity = _path_identity(Path(child.path), child_metadata)
                        identity = file_identity[:2]
                        if file_identity[2] != 1 or identity in identities:
                            raise ValidationError(f"{locator}: hard links are forbidden")
                        identities.add(identity)
                        files[locator] = TreeFile(
                            child_metadata.st_size,
                            child_metadata.st_mtime_ns,
                            *file_identity,
                        )
                    else:
                        raise ValidationError(f"{locator}: non-regular tree entry")
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{root}: cannot enumerate tree: {exc}") from exc
    return files, directories
def _expected_directories(paths: Iterable[str]) -> set[str]:
    return {
        parent.as_posix()
        for locator in paths
        for parent in Path(locator).parents
        if parent != Path(".")
    }
def _read_checked(root: Path, locator: str, tree: dict[str, TreeFile], maximum: int) -> bytes:
    try:
        expected = tree[locator]
    except KeyError as exc:
        raise ValidationError(f"{locator}: missing artifact") from exc
    if expected.size < 1 or expected.size > maximum:
        raise ValidationError(f"{locator}: artifact violates its byte ceiling")
    path = root.joinpath(*locator.split("/"))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        expected_identity = (expected.device, expected.inode, expected.links)
        if (
            path.is_symlink() or _is_reparse(before) or not stat.S_ISREG(before.st_mode)
            or before.st_size != expected.size
            or before.st_mtime_ns != expected.modified_ns
            or _identity(before) != expected_identity
        ):
            raise ValidationError(f"{locator}: artifact identity changed before read")
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if _identity(opened) != expected_identity:
                raise ValidationError(f"{locator}: artifact identity changed while opening")
            payload = handle.read(maximum + 1)
            after_open = os.fstat(handle.fileno())
        after = path.lstat()
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{locator}: cannot read artifact: {exc}") from exc
    if (
        len(payload) != expected.size or len(payload) > maximum
        or _identity(after_open) != expected_identity
        or path.is_symlink() or _is_reparse(after) or not stat.S_ISREG(after.st_mode)
        or after.st_size != expected.size
        or after.st_mtime_ns != expected.modified_ns
        or _identity(after) != expected_identity
    ):
        raise ValidationError(f"{locator}: artifact changed during read")
    return payload
def _parse_json(payload: bytes, locator: str) -> dict[str, Any]:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{locator}: UTF-8 byte-order marks are forbidden")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=unique,
                              parse_constant=reject)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{locator}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{locator}: JSON root must be an object")
    return document
def _json_artifact(root: Path, locator: str, tree: dict[str, TreeFile]) -> tuple[dict[str, Any], bytes]:
    payload = _read_checked(root, locator, tree, MAX_JSON_BYTES)
    return _parse_json(payload, locator), payload
def _require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValidationError(f"{label}: binding mismatch")
def _validate_typed(document: dict[str, Any], document_type: str, label: str) -> None:
    _require(document.get("document_type"), document_type, f"{label} document type")
    validate_document(document)
def _validate_page_index(index: dict[str, Any], observation: dict[str, Any],
                         checkpoint: dict[str, Any], ordinal: int,
                         prior_hashes: list[str]) -> list[str]:
    _validate_typed(index, "dao_a3_page_index", checkpoint["page_index"]["path"])
    bindings = {
        "plan_sha256": PLAN_SHA256,
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
    expected_changed = [
        page for page in range(max(len(prior_hashes), len(hashes)))
        if page >= len(prior_hashes) or page >= len(hashes) or prior_hashes[page] != hashes[page]
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
def _entry_payload(root: Path, tree: dict[str, TreeFile],
                   entry: dict[str, Any]) -> tuple[bytes, dict[str, Any] | None]:
    maximum = PAGE_SIZE if entry["role"] == "page_blob" else MAX_JSON_BYTES
    payload = _read_checked(root, entry["path"], tree, maximum)
    _require(len(payload), entry["size_bytes"], f"{entry['path']} size")
    _require(hashlib.sha256(payload).hexdigest(), entry["sha256"], f"{entry['path']} sha256")
    expected_media = "application/octet-stream" if entry["role"] == "page_blob" else "application/json"
    _require(entry["media_type"], expected_media, f"{entry['path']} media type")
    return payload, None if expected_media != "application/json" else _parse_json(payload, entry["path"])
def _validate_replica(
    root: Path,
    tree: dict[str, TreeFile],
    directories: set[str],
    replica: int,
    campaign_id: str | None,
    *,
    closed: bool,
) -> ReplicaResult:
    manifest_path = f"replica-artifacts/replica-{replica:02d}-manifest.json"
    manifest, manifest_payload = _json_artifact(root, manifest_path, tree)
    _validate_typed(manifest, "dao_a3_replica_artifact_manifest", manifest_path)
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
        _, document = _entry_payload(root, tree, entry)
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
    _validate_typed(environment, "dao_a3_environment", environment_path)
    _validate_typed(observation, "dao_a3_replica_observation", observation_path)
    _require(len(observation["checkpoints"]), CHECKPOINT_COUNT, f"{observation_path} checkpoints")
    expected_binding = {
        "plan_sha256": PLAN_SHA256,
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
    prior_hashes: list[str] = []
    changed_total = 0
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
        hashes = _validate_page_index(index, observation, checkpoint, ordinal, prior_hashes)
        reconstruction = hashlib.sha256()
        for digest in hashes:
            page_path = f"page-store/{digest}.page"
            page_entry = entries.get(page_path)
            if page_entry is None or page_entry["role"] != "page_blob":
                raise ValidationError(f"{page_path}: referenced blob is absent")
            payload = _read_checked(root, page_path, tree, PAGE_SIZE)
            _require(len(payload), PAGE_SIZE, f"{page_path} page size")
            _require(hashlib.sha256(payload).hexdigest(), digest, f"{page_path} content address")
            reconstruction.update(payload)
            referenced_pages.add(page_path)
        _require(reconstruction.hexdigest(), index["database_sha256"], f"{expected_path} database hash")
        changed_total += len(index["changed_page_indices"])
        prior_hashes = hashes
    _require(observation["changed_hash_entries"], changed_total, f"replica {replica} changed-hash total")
    page_paths = {path for path, entry in entries.items() if entry["role"] == "page_blob"}
    _require(page_paths, referenced_pages, f"replica {replica} page-store closure")
    if len(page_paths) > MAX_PAGE_BLOBS or len(page_paths) * PAGE_SIZE > MAX_PAGE_STORE_BYTES:
        raise ValidationError(f"replica {replica}: page-store bound exceeded")
    return ReplicaResult(
        root,
        replica,
        manifest_path,
        manifest,
        hashlib.sha256(manifest_payload).hexdigest(),
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
def _validate_frozen(document: dict[str, Any], payload: bytes, campaign_id: str) -> None:
    validate_frozen_candidates(document, payload)
    bindings = {
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "campaign_id": campaign_id,
        "derivation_replicas": [1, 2],
    }
    for key, expected in bindings.items():
        _require(document[key], expected, f"frozen candidate {key}")
def _validate_analysis_payload(root: Path, tree: dict[str, TreeFile],
                               replicas: tuple[ReplicaResult, ...]) -> tuple[dict[str, Any], dict[str, Any], str]:
    frozen_path = "analysis/derivation-candidates.json"
    analysis_path = "analysis/analysis-report.json"
    receipt_path = "analysis/holdout-structure-receipt.json"
    frozen, frozen_payload = _json_artifact(root, frozen_path, tree)
    report, _ = _json_artifact(root, analysis_path, tree)
    receipt, _ = _json_artifact(root, receipt_path, tree)
    campaign_id = replicas[0].campaign_id
    frozen_sha256 = hashlib.sha256(frozen_payload).hexdigest()
    _validate_frozen(frozen, frozen_payload, campaign_id)
    _require(report.get("document_type"), "dao_a3_analysis_report", f"{analysis_path} document type")
    validate_analysis_report(report, frozen)
    _validate_typed(receipt, "dao_a3_holdout_structure_receipt", receipt_path)
    _require(report["plan_sha256"], PLAN_SHA256, "analysis plan_sha256")
    _require(report["holdout_replica"], REPLICA_COUNT, "analysis holdout replica")
    _require(receipt["replica"], REPLICA_COUNT, "holdout receipt replica")
    _require(receipt["result"], "pass", "holdout receipt result")
    bindings = {
        "plan_sha256": PLAN_SHA256,
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
                      allow_manifest: bool = False) -> PayloadResult:
    root = root.resolve()
    tree, directories = _inventory(root)
    plan_payload = _read_checked(root, PLAN_PATH, tree, MAX_JSON_BYTES)
    _require(hashlib.sha256(plan_payload).hexdigest(), PLAN_SHA256, "retained plan hash")
    _require(plan_payload, CHECKED_PLAN.read_bytes(), "retained checked plan")
    replicas = tuple(
        _validate_replica(root, tree, directories, replica, None, closed=False)
        for replica in range(1, REPLICA_COUNT + 1)
    )
    _validate_environments(replicas)
    expected = {PLAN_PATH}
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
        expected.update(analysis_paths)
        report, receipt, frozen_sha256 = _validate_analysis_payload(root, tree, replicas)
    complete_expected = expected | ({MANIFEST_PATH} if allow_manifest else set())
    _require(set(tree), complete_expected, "A3 payload closed inventory")
    _require(directories, _expected_directories(complete_expected),
             "A3 payload directory closure")
    page_paths = set().union(*(row.page_paths for row in replicas))
    if len(page_paths) > MAX_PAGE_BLOBS or len(page_paths) * PAGE_SIZE > MAX_PAGE_STORE_BYTES:
        raise ValidationError("A3 merged page store exceeds its fixed bound")
    if sum(item.size for item in tree.values()) > MAX_BUNDLE_BYTES:
        raise ValidationError("A3 bundle exceeds its fixed byte bound")
    return PayloadResult(tree, directories, replicas, frozenset(expected),
                         report, receipt, frozen_sha256)
def _copy_verified(
    source_root: Path,
    destination_root: Path,
    locator: str,
    size: int,
    digest: str,
) -> None:
    source = source_root.joinpath(*locator.split("/"))
    destination = destination_root.joinpath(*locator.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise ValidationError(f"{locator}: merge collision is not a regular file")
        payload_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if destination.stat().st_size != size or payload_digest != digest:
            raise ValidationError(f"{locator}: merge collision differs")
        return
    source_metadata = source.lstat()
    if source.is_symlink() or _is_reparse(source_metadata) or not stat.S_ISREG(source_metadata.st_mode):
        raise ValidationError(f"{locator}: copy source is unsafe")
    observed = hashlib.sha256()
    total = 0
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            for chunk in iter(lambda: reader.read(65_536), b""):
                total += len(chunk)
                if total > size:
                    raise ValidationError(f"{locator}: copy source grew")
                observed.update(chunk)
                writer.write(chunk)
    except Exception:
        if destination.exists():
            destination.unlink()
        raise
    if total != size or observed.hexdigest() != digest:
        destination.unlink()
        raise ValidationError(f"{locator}: copy source binding failed")
def assemble_bundle(replica_roots: Iterable[Path], bundle_root: Path,
                    campaign_id: str, producer_commit: str) -> dict[str, Any]:
    roots = tuple(Path(root).resolve() for root in replica_roots)
    if len(roots) != REPLICA_COUNT:
        raise ValidationError("assemble requires exactly three replica roots")
    if bundle_root.exists():
        raise ValidationError("bundle root already exists")
    validated = []
    for replica, root in enumerate(roots, start=1):
        tree, directories = _inventory(root)
        validated.append(_validate_replica(
            root, tree, directories, replica, campaign_id, closed=True))
    replicas = tuple(validated)
    _validate_environments(replicas)
    _require(replicas[0].producer_commit, producer_commit, "expected producer commit")
    bundle_parent = bundle_root.resolve().parent
    bundle_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".a3-assemble-", dir=bundle_parent))
    try:
        plan_path = staging.joinpath(*PLAN_PATH.split("/"))
        plan_path.parent.mkdir(parents=True)
        plan_path.write_bytes(CHECKED_PLAN.read_bytes())
        for replica in replicas:
            _copy_verified(
                replica.root,
                staging,
                replica.manifest_path,
                replica.manifest_size,
                replica.manifest_sha256,
            )
            for entry in replica.entries.values():
                _copy_verified(
                    replica.root,
                    staging,
                    entry["path"],
                    entry["size_bytes"],
                    entry["sha256"],
                )
        result = _validate_payload(staging, require_analysis=False)
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
    tree, directories = _inventory(bundle_root.resolve())
    replica = _validate_replica(bundle_root.resolve(), tree, directories,
                                REPLICA_COUNT, None, closed=False)
    _require(replica.campaign_id, campaign_id, "expected holdout campaign")
    _require(replica.producer_commit, producer_commit, "expected holdout producer")
    payload = _read_checked(
        bundle_root.resolve(),
        candidate_path.resolve().relative_to(bundle_root.resolve()).as_posix(),
        tree,
        MAX_JSON_BYTES,
    )
    _require(hashlib.sha256(payload).hexdigest(), candidate_sha256, "frozen candidate hash")
    frozen = _parse_json(payload, candidate_path.as_posix())
    _validate_frozen(frozen, payload, replica.campaign_id)
    return replica
def _role_for_path(path: str, replicas: tuple[ReplicaResult, ...]) -> str:
    fixed = {
        PLAN_PATH: "plan",
        "analysis/derivation-candidates.json": "frozen_candidate_set",
        "analysis/analysis-report.json": "analysis_report",
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
def finalize_bundle(bundle_root: Path, campaign_id: str,
                    producer_commit: str) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
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
        retained = _read_checked(bundle_root, path, payload.tree, maximum)
        digest = hashlib.sha256(retained).hexdigest()
        files.append(
            {
                "path": path,
                "role": role,
                "sha256": digest,
                "size_bytes": item.size,
                "media_type": "application/octet-stream" if role == "page_blob" else "application/json",
            }
        )
    decisive = payload.analysis["scientific_outcome"] == "one_or_more_submodels_predict_holdout"
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_bundle_manifest",
        "experiment_id": EXPERIMENT_ID,
        "campaign_id": payload.replicas[0].campaign_id,
        "producer_commit": payload.replicas[0].producer_commit,
        "repository_url": REPOSITORY_URL,
        "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "plan_sha256": PLAN_SHA256,
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
        "holdout_structure_receipt_sha256": hashlib.sha256(_read_checked(
            bundle_root, "analysis/holdout-structure-receipt.json",
            payload.tree, MAX_JSON_BYTES)).hexdigest(),
        "analysis_report_retained": True,
        "analysis_scientific_outcome": payload.analysis["scientific_outcome"],
        "bundle_status": (
            "decisive_pending_independent_validation"
            if decisive
            else "complete_no_scientific_outcome"
        ),
        "independent_validation_status": "not_independently_validated",
        "files": files,
    }
    validate_document(manifest)
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > MAX_JSON_BYTES:
        raise ValidationError("bundle manifest exceeds its fixed JSON bound")
    if manifest["bundle_size_bytes_excluding_manifest"] + len(manifest_bytes) > MAX_BUNDLE_BYTES:
        raise ValidationError("complete A3 bundle exceeds its fixed byte bound")
    with (bundle_root / MANIFEST_PATH).open("xb") as handle:
        handle.write(manifest_bytes)
    return manifest
def validate_bundle(bundle_root: Path, campaign_id: str,
                    producer_commit: str) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
    tree, directories = _inventory(bundle_root)
    manifest, manifest_payload = _json_artifact(bundle_root, MANIFEST_PATH, tree)
    _validate_typed(manifest, "dao_a3_bundle_manifest", MANIFEST_PATH)
    entries = _entry_map(manifest)
    _require(set(tree), set(entries) | {MANIFEST_PATH}, "complete bundle inventory")
    _require(directories, _expected_directories(entries), "complete bundle directories")
    for entry in entries.values():
        _entry_payload(bundle_root, tree, entry)
    payload = _validate_payload(bundle_root, require_analysis=True,
                                allow_manifest=True)
    assert payload.analysis is not None and payload.receipt is not None
    _require(manifest["campaign_id"], campaign_id, "expected campaign")
    _require(manifest["producer_commit"], producer_commit, "expected producer commit")
    expected = {
        "experiment_id": EXPERIMENT_ID,
        "campaign_id": payload.replicas[0].campaign_id,
        "producer_commit": payload.replicas[0].producer_commit,
        "repository_url": REPOSITORY_URL,
        "plan_sha256": PLAN_SHA256,
        "replica_environment_sha256": [row.environment_sha256 for row in payload.replicas],
        "provider_sha256": payload.replicas[0].provider_sha256,
        "replica_artifact_manifest_sha256": [row.manifest_sha256 for row in payload.replicas],
        "holdout_structure_receipt_sha256": entries["analysis/holdout-structure-receipt.json"]["sha256"],
        "analysis_scientific_outcome": payload.analysis["scientific_outcome"],
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
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
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
    validate = subparsers.add_parser("validate")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--campaign-id", required=True)
    validate.add_argument("--producer-commit", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "assemble":
            result = assemble_bundle(arguments.replica_root, arguments.bundle_root,
                                     arguments.campaign_id, arguments.producer_commit)
        elif arguments.command == "finalize":
            manifest = finalize_bundle(
                arguments.bundle_root, arguments.campaign_id, arguments.producer_commit)
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
        print(f"A3 bundle {arguments.command} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
