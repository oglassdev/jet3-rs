#!/usr/bin/env python3
"""Bounded A3 replica inputs used by the freeze and holdout phases."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from a3_model import CHECKPOINT_IDS, PAGE_SIZE, ReplicaData
from a3_spec import (
    BOUNDS,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    load_bounded_json,
    load_bounded_json_with_payload,
    validate_document,
)
from protocol_validation import ValidationError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts or "\\" in relative:
        raise ValidationError(f"unsafe A3 artifact path {relative!r}")
    resolved_root, resolved = root.resolve(), (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValidationError(f"A3 artifact escapes bundle root: {relative!r}")
    return resolved


@dataclass(frozen=True)
class ReplicaInput:
    data: ReplicaData
    replica: int
    campaign_id: str
    producer_commit: str
    provider_sha256: str
    churn_precondition_met: bool


class ReplicaSource(Protocol):
    def open(self) -> ReplicaInput: ...


@dataclass(frozen=True)
class LoadedReplicaSource:
    replica: ReplicaInput

    def open(self) -> ReplicaInput:
        return self.replica


class BundleReplicaData:
    def __init__(self, root: Path, indexes: dict[str, dict[str, Any]], checkpoint_ids: tuple[str, ...] = CHECKPOINT_IDS) -> None:
        self.root, self.indexes, self._checkpoint_ids = root, indexes, checkpoint_ids
        self._cache: dict[str, bytes] = {}

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return self._checkpoint_ids

    @property
    def page_count(self) -> dict[str, int]:
        return {name: int(index["page_count"]) for name, index in self.indexes.items()}

    @property
    def ordered_page_sha256(self) -> dict[str, tuple[str, ...]]:
        return {name: tuple(index["ordered_page_sha256"]) for name, index in self.indexes.items()}

    def page_bytes(self, digest: str) -> bytes:
        if digest in self._cache:
            return self._cache[digest]
        path = self.root / "page-store" / f"{digest}.page"
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PAGE_SIZE:
            raise OSError(f"unsafe A3 page blob {path}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(f"A3 page blob hash mismatch {path}")
        self._cache[digest] = payload
        return payload


@dataclass(frozen=True)
class BundleReplicaSource:
    observation_path: Path
    bundle_root: Path
    max_json_bytes: int = BOUNDS["max_json_bytes"]

    def open(self) -> ReplicaInput:
        observation = load_bounded_json(self.observation_path, self.max_json_bytes)
        validate_document(observation)
        if (
            observation["plan_sha256"] != PLAN_SHA256
            or observation["revision_plan_sha256"] != REVISION_PLAN_SHA256
        ):
            raise ValidationError("replica observation is not bound to the A3 plan chain")
        checkpoints = observation["checkpoints"]
        observed_ids = tuple(row["checkpoint_id"] for row in checkpoints)
        indexes: dict[str, dict[str, Any]] = {}
        prior: list[str] = []
        changed_total = 0
        for ordinal, checkpoint in enumerate(checkpoints):
            reference = checkpoint["page_index"]
            path = _safe_path(self.bundle_root, reference["path"])
            index, payload = load_bounded_json_with_payload(path, self.max_json_bytes)
            if (
                len(payload) != reference["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != reference["sha256"]
            ):
                raise ValidationError(f"{path}: page-index binding failed")
            validate_document(index)
            expected_predecessor = CHECKPOINT_IDS[ordinal - 1] if ordinal else None
            bindings = {
                "plan_sha256": PLAN_SHA256, "revision_plan_sha256": REVISION_PLAN_SHA256,
                "producer_commit": observation["producer_commit"],
                "campaign_id": observation["campaign_id"], "environment_sha256": observation["environment_sha256"],
                "provider_sha256": observation["provider_sha256"], "replica": observation["replica"],
                "checkpoint_id": checkpoint["checkpoint_id"], "ordinal": ordinal,
                "predecessor_checkpoint_id": expected_predecessor,
                "page_count": checkpoint["actual_file_pages"],
            }
            if any(index[key] != value for key, value in bindings.items()):
                raise ValidationError(f"{path}: page-index metadata binding mismatch")
            hashes = index["ordered_page_sha256"]
            expected_changed = []
            for page in range(max(len(prior), len(hashes))):
                prior_hash = prior[page] if page < len(prior) else None
                current_hash = hashes[page] if page < len(hashes) else None
                if prior_hash != current_hash:
                    expected_changed.append(page)
            if index["changed_page_indices"] != expected_changed:
                raise ValidationError(f"{path}: changed-page reconstruction failed")
            changed_total += len(expected_changed)
            prior, indexes[checkpoint["checkpoint_id"]] = hashes, index
        if changed_total != observation["changed_hash_entries"]:
            raise ValidationError("replica changed-hash total mismatch")
        by_id = {row["checkpoint_id"]: row for row in checkpoints}
        before, deleted = by_id["L_REL_1280"], by_id["L_DELETE_ALL"]
        reread = next((row["row_count"] for row in deleted["dao_reread"] if row["role"] == "L"), None)
        churn = before["table_row_counts"]["L"] != 0 and reread == 0
        return ReplicaInput(
            BundleReplicaData(self.bundle_root, indexes, observed_ids), observation["replica"],
            observation["campaign_id"], observation["producer_commit"], observation["provider_sha256"], churn,
        )
