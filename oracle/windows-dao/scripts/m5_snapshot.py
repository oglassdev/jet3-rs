#!/usr/bin/env python3
"""Bounded complete-tree snapshot for immutable M5R4 evidence bundles."""

from __future__ import annotations

import os
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from m1_bundle_validation import bounded_file_identity
from m4r1_snapshot import _is_reparse, _path_identity
from m5_records import SCHEMA_SET, parse_json_bytes, reject_alias_components
from m5_spec import PREFIX_BYTES, require_equal
from protocol_validation import ValidationError

MANIFEST_NAME = "bundle-manifest.json"
MAX_TREE_ENTRIES = 3600
MAX_TREE_DEPTH = 8
FIXED_ROLE_COUNTS = {
    "plan": 1, "environment": 1, "analysis_report": 1,
    "sample_record": 108, "phase_invocation": 324,
    "phase_worker_result": 324, "operation_log": 324,
    "semantic_snapshot": 216, "clone_log": 216, "database": 432,
    "prefix": 324, "post_worker_quiescence": 432,
}
ROLE_LIMITS = {
    "plan": 1048576, "environment": 1048576, "analysis_report": 16777216,
    "sample_record": 65536, "phase_invocation": 65536,
    "phase_worker_result": 65536, "operation_log": 65536,
    "semantic_snapshot": 65536, "clone_log": 65536, "database": 1048576,
    "prefix": PREFIX_BYTES, "post_worker_quiescence": 16384, "companion": 65536,
}


@dataclass(frozen=True)
class Stamp:
    mode: int
    size: int
    mtime_ns: int
    attributes: int
    device: int
    index: int
    links: int


@dataclass(frozen=True)
class Artifact:
    locator: str
    role: str
    size: int
    sha256: str
    payload: bytes | None
    prefix: bytes | None
    document: dict[str, Any] | None


def _stamp(path: Path) -> Stamp:
    try:
        metadata = path.lstat()
        identity = _path_identity(path, metadata)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect evidence identity: {exc}") from exc
    return Stamp(metadata.st_mode, metadata.st_size, metadata.st_mtime_ns, getattr(metadata, "st_file_attributes", 0), identity.device, identity.index, identity.links)


def _inventory(root: Path) -> dict[str, Stamp | None]:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect bundle root: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink() or _is_reparse(metadata):
        raise ValidationError("bundle root must be an ordinary directory")
    result: dict[str, Stamp | None] = {".": None}
    identities: set[tuple[int, int]] = set()
    pending = [(root, 0)]
    count = 0
    while pending:
        directory, depth = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ValidationError(f"{directory}: cannot enumerate bundle: {exc}") from exc
        for entry in entries:
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise ValidationError("bundle exceeds directory-entry ceiling")
            path = Path(entry.path)
            metadata = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or _is_reparse(metadata):
                raise ValidationError(f"{path}: symlinks and reparses are forbidden")
            locator = path.relative_to(root).as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                if depth >= MAX_TREE_DEPTH:
                    raise ValidationError("bundle exceeds directory-depth ceiling")
                result[locator] = None
                pending.append((path, depth + 1))
            elif stat.S_ISREG(metadata.st_mode):
                stamp = _stamp(path)
                identity = (stamp.device, stamp.index)
                if stamp.links != 1 or identity in identities:
                    raise ValidationError(f"{path}: hard links are forbidden")
                identities.add(identity)
                result[locator] = stamp
            else:
                raise ValidationError(f"{path}: non-regular evidence entry")
    return result


def _capture(root: Path, locator: str, role: str, ceiling: int, expected: Stamp) -> Artifact:
    path = root.joinpath(*locator.split("/"))
    before = _stamp(path)
    if before != expected:
        raise ValidationError(f"{locator}: identity changed before read")
    size, digest, retained = bounded_file_identity(path, ceiling, retain=True)
    assert retained is not None
    after = _stamp(path)
    if before != after:
        raise ValidationError(f"{locator}: artifact changed while read")
    payload: bytes | None = retained
    prefix: bytes | None = None
    document: dict[str, Any] | None = None
    if role == "database":
        if size < PREFIX_BYTES:
            raise ValidationError(f"{locator}: database shorter than retained prefix")
        prefix = retained[:PREFIX_BYTES]
        payload = None
    elif role not in ("prefix", "companion"):
        document = parse_json_bytes(retained, locator)
    return Artifact(locator, role, size, digest, payload, prefix, document)


class BundleSnapshot:
    def __init__(self, root: Path, inventory: dict[str, Stamp | None], manifest: dict[str, Any], manifest_index: dict[str, dict[str, Any]], artifacts: dict[str, Artifact]) -> None:
        self.root = root
        self._inventory = inventory
        self.manifest = manifest
        self.manifest_index = manifest_index
        self._artifacts = artifacts

    @classmethod
    def capture(cls, supplied_root: Path) -> "BundleSnapshot":
        lexical_root = reject_alias_components(supplied_root, "supplied bundle root")
        try:
            supplied = lexical_root.lstat()
        except OSError as exc:
            raise ValidationError(f"{supplied_root}: cannot inspect supplied bundle root: {exc}") from exc
        if lexical_root.is_symlink() or _is_reparse(supplied):
            raise ValidationError("supplied bundle-root aliases and reparses are forbidden")
        root = lexical_root.resolve(strict=True)
        inventory = _inventory(root)
        stamp = inventory.get(MANIFEST_NAME)
        if stamp is None:
            raise ValidationError("bundle-manifest.json is missing")
        manifest_artifact = _capture(root, MANIFEST_NAME, "manifest", 16 * 1024 * 1024, stamp)
        assert manifest_artifact.document is not None
        manifest = manifest_artifact.document
        observed = SCHEMA_SET.validate(manifest)
        if observed != "dao_m5_bundle_manifest":
            raise ValidationError("bundle-manifest.json is not an M5R4 manifest")
        paths = [row["path"] for row in manifest["files"]]
        if len(paths) != len(set(paths)):
            raise ValidationError("$.files: duplicate manifest path")
        require_equal(manifest["file_count"], len(paths), "$.file_count")
        discovered = {path for path, value in inventory.items() if value is not None}
        expected = set(paths) | {MANIFEST_NAME}
        if discovered != expected:
            raise ValidationError(f"bundle tree differs; missing={sorted(expected-discovered)}, extra={sorted(discovered-expected)}")
        counts = Counter(row["role"] for row in manifest["files"])
        companion_count = counts.pop("companion", 0)
        if companion_count > 432:
            raise ValidationError("$.files: companion count exceeds ceiling")
        require_equal(dict(counts), FIXED_ROLE_COUNTS, "$.files role counts")
        artifacts: dict[str, Artifact] = {}
        companion_bytes = 0
        for entry in manifest["files"]:
            locator, role = entry["path"], entry["role"]
            ceiling = ROLE_LIMITS[role]
            expected_media = "application/octet-stream" if role in ("database", "prefix", "companion") else "application/json"
            require_equal(entry["media_type"], expected_media, f"{locator} media_type")
            captured = _capture(root, locator, role, ceiling, inventory[locator])
            require_equal(captured.size, entry["size_bytes"], f"{locator} size")
            require_equal(captured.sha256, entry["sha256"], f"{locator} sha256")
            if role == "prefix":
                require_equal(captured.size, PREFIX_BYTES, f"{locator} prefix size")
            if role == "companion":
                companion_bytes += captured.size
                if companion_bytes > 432 * 65536:
                    raise ValidationError("companion byte ceiling exceeded")
            artifacts[locator] = captured
        return cls(root, inventory, manifest, {row["path"]: row for row in manifest["files"]}, artifacts)

    def artifact(self, locator: str) -> Artifact:
        try:
            return self._artifacts[locator]
        except KeyError as exc:
            raise ValidationError(f"{locator}: artifact absent from snapshot") from exc

    def load_document(self, locator: str, maximum: int, expected_type: str) -> tuple[dict[str, Any], int, str]:
        artifact = self.artifact(locator)
        if artifact.size > maximum or artifact.document is None:
            raise ValidationError(f"{locator}: expected bounded JSON artifact")
        observed = SCHEMA_SET.validate(artifact.document)
        if observed != expected_type:
            raise ValidationError(f"{locator}: expected {expected_type}, got {observed}")
        return artifact.document, artifact.size, artifact.sha256

    def binary_payload(self, locator: str, role: str) -> bytes:
        artifact = self.artifact(locator)
        if artifact.role != role or artifact.payload is None:
            raise ValidationError(f"{locator}: expected {role} bytes")
        return artifact.payload

    def database_projection(self, locator: str) -> tuple[int, str, bytes]:
        artifact = self.artifact(locator)
        if artifact.role != "database" or artifact.prefix is None:
            raise ValidationError(f"{locator}: expected database")
        return artifact.size, artifact.sha256, artifact.prefix

    def recheck(self) -> None:
        if _inventory(self.root) != self._inventory:
            raise ValidationError("bundle tree or identities changed during validation")
