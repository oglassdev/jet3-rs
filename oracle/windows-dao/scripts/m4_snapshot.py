#!/usr/bin/env python3
"""One-pass immutable artifact snapshot for complete DAO M4 bundles."""

from __future__ import annotations

import hashlib
import os
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from m4_records import (
    SCHEMA_SET,
    ValidationError,
    parse_json_bytes,
    require_equal,
)

MANIFEST_NAME = "bundle-manifest.json"
MAX_TREE_ENTRIES = 768
MAX_TREE_DEPTH = 8
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PREFIX_BYTES = 2048
ROLE_COUNTS = {
    "plan": 1,
    "environment": 1,
    "analysis_report": 1,
    "sample_record": 36,
    "phase_invocation": 72,
    "phase_worker_result": 72,
    "operation_log": 72,
    "semantic_snapshot": 72,
    "clone_log": 36,
    "database": 72,
    "prefix": 72,
}
ROLE_BYTE_CEILINGS = {
    "plan": 1048576,
    "environment": 1048576,
    "analysis_report": 16777216,
    "sample_record": 65536,
    "phase_invocation": 65536,
    "phase_worker_result": 65536,
    "operation_log": 65536,
    "semantic_snapshot": 65536,
    "clone_log": 65536,
    "database": 1048576,
    "prefix": PREFIX_BYTES,
}
_WINDOWS_IDENTITY_API: tuple[Any, Any, type[Any]] | None = None


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


@dataclass(frozen=True)
class FileStamp:
    """Mutable file metadata, excluding handle-owned filesystem identity."""

    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    attributes: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileStamp:
        return cls(
            mode=value.st_mode,
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
            attributes=getattr(value, "st_file_attributes", 0),
        )


@dataclass(frozen=True)
class FileIdentity:
    """Filesystem identity and link count from an authoritative file handle."""

    device: int
    index: int
    links: int


@dataclass(frozen=True)
class TreeEntry:
    kind: str
    stamp: FileStamp
    identity: FileIdentity | None


@dataclass(frozen=True)
class CapturedArtifact:
    locator: str
    role: str
    size: int
    sha256: str
    payload: bytes | None
    prefix: bytes | None
    document: dict[str, Any] | None


def _descriptor_identity(
    descriptor: int, metadata: os.stat_result | None = None
) -> FileIdentity:
    """Return a stable file identity without trusting Windows stat emulation."""
    if os.name != "nt":
        observed = metadata if metadata is not None else os.fstat(descriptor)
        return FileIdentity(observed.st_dev, observed.st_ino, observed.st_nlink)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    global _WINDOWS_IDENTITY_API
    if _WINDOWS_IDENTITY_API is None:

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation_time", FileTime),
                ("last_access_time", FileTime),
                ("last_write_time", FileTime),
                ("volume_serial_number", wintypes.DWORD),
                ("file_size_high", wintypes.DWORD),
                ("file_size_low", wintypes.DWORD),
                ("number_of_links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        library = ctypes.WinDLL("kernel32", use_last_error=True)
        query = library.GetFileInformationByHandle
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ByHandleFileInformation),
        ]
        query.restype = wintypes.BOOL
        _WINDOWS_IDENTITY_API = (
            library,
            query,
            ByHandleFileInformation,
        )
    _, query, information_type = _WINDOWS_IDENTITY_API
    information = information_type()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    if not query(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise OSError(error, "GetFileInformationByHandle failed")
    index = (information.file_index_high << 32) | information.file_index_low
    return FileIdentity(
        information.volume_serial_number,
        index,
        information.number_of_links,
    )


def _open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _path_identity(path: Path, metadata: os.stat_result) -> FileIdentity:
    if os.name != "nt":
        return _descriptor_identity(-1, metadata)
    descriptor = os.open(path, _open_flags())
    try:
        return _descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


def _tree_inventory(root: Path) -> dict[str, TreeEntry]:
    """Enumerate one bounded tree without following aliases or links."""
    try:
        root_meta = root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect bundle root: {exc}") from exc
    if (
        not stat.S_ISDIR(root_meta.st_mode)
        or root.is_symlink()
        or _is_reparse(root_meta)
    ):
        raise ValidationError(f"{root}: bundle root must be a regular directory")
    inventory = {
        ".": TreeEntry("directory", FileStamp.from_stat(root_meta), None)
    }
    pending = [(root, 0)]
    identities: set[tuple[int, int]] = set()
    visited = 0
    try:
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_TREE_ENTRIES:
                        raise ValidationError(
                            "bundle exceeds directory-entry limit"
                        )
                    metadata = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or _is_reparse(metadata):
                        raise ValidationError(
                            f"{entry.path}: links and reparses are forbidden"
                        )
                    candidate = Path(entry.path)
                    locator = candidate.relative_to(root).as_posix()
                    stamp = FileStamp.from_stat(metadata)
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth >= MAX_TREE_DEPTH:
                            raise ValidationError(
                                "bundle exceeds directory-depth limit"
                            )
                        inventory[locator] = TreeEntry("directory", stamp, None)
                        pending.append((candidate, depth + 1))
                    elif stat.S_ISREG(metadata.st_mode):
                        identity = _path_identity(candidate, metadata)
                        identity_key = (identity.device, identity.index)
                        if identity.links != 1 or identity_key in identities:
                            raise ValidationError(
                                f"{entry.path}: hard links are forbidden"
                            )
                        identities.add(identity_key)
                        inventory[locator] = TreeEntry(
                            "file", stamp, identity
                        )
                    else:
                        raise ValidationError(
                            f"{entry.path}: non-regular bundle entry"
                        )
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{root}: cannot enumerate bundle: {exc}") from exc
    return inventory


def discover_bundle(root: Path) -> set[str]:
    """Compatibility query returning regular-file locators from one scan."""
    return {
        locator
        for locator, entry in _tree_inventory(root).items()
        if entry.kind == "file"
    }


def read_stable_database(path: Path, maximum: int) -> tuple[int, str, bytes]:
    """Read one loose database once with the snapshot identity discipline."""
    try:
        metadata = path.lstat()
        identity = _path_identity(path, metadata)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect database: {exc}") from exc
    captured = _read_captured(
        path.parent,
        path.name,
        TreeEntry(
            "file",
            FileStamp.from_stat(metadata),
            identity,
        ),
        maximum,
        role="database",
    )
    assert captured.prefix is not None
    return captured.size, captured.sha256, captured.prefix


def _read_captured(
    root: Path,
    locator: str,
    expected: TreeEntry,
    maximum: int,
    *,
    role: str,
) -> CapturedArtifact:
    """Read one file once, checking its path and descriptor identities."""
    path = root.joinpath(*locator.split("/"))
    digest = hashlib.sha256()
    retained = bytearray()
    total = 0
    try:
        before = FileStamp.from_stat(path.lstat())
        if before != expected.stamp or expected.kind != "file":
            raise ValidationError(f"{locator}: identity changed before snapshot read")
        descriptor = os.open(path, _open_flags())
        with os.fdopen(descriptor, "rb") as handle:
            opened = FileStamp.from_stat(os.fstat(handle.fileno()))
            opened_identity = _descriptor_identity(handle.fileno())
            if (
                expected.identity is None
                or opened_identity != expected.identity
                or opened.size != before.size
                or not stat.S_ISREG(opened.mode)
            ):
                raise ValidationError(
                    f"{locator}: identity changed while opening snapshot"
                )
            while True:
                chunk = handle.read(min(64 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise ValidationError(
                        f"{locator}: artifact exceeds {maximum} bytes"
                    )
                digest.update(chunk)
                if role == "database":
                    if len(retained) < PREFIX_BYTES:
                        retained.extend(chunk[: PREFIX_BYTES - len(retained)])
                else:
                    retained.extend(chunk)
            after_descriptor = FileStamp.from_stat(os.fstat(handle.fileno()))
        after_metadata = path.lstat()
        after_path = FileStamp.from_stat(after_metadata)
        after_identity = _path_identity(path, after_metadata)
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{locator}: cannot capture artifact: {exc}") from exc
    if (
        opened != after_descriptor
        or after_path != before
        or after_identity != opened_identity
    ):
        raise ValidationError(f"{locator}: artifact changed during snapshot read")
    if role == "database" and len(retained) != PREFIX_BYTES:
        raise ValidationError(f"{locator}: database is shorter than retained prefix")
    payload = None if role == "database" else bytes(retained)
    document = None
    if role not in ("database", "prefix"):
        assert payload is not None
        document = parse_json_bytes(payload, locator)
    return CapturedArtifact(
        locator=locator,
        role=role,
        size=total,
        sha256=digest.hexdigest(),
        payload=payload,
        prefix=bytes(retained) if role == "database" else None,
        document=document,
    )


class BundleSnapshot:
    """Manifest-bound immutable bytes and database projections for one bundle."""

    def __init__(
        self,
        root: Path,
        inventory: dict[str, TreeEntry],
        manifest: dict[str, Any],
        manifest_artifact: CapturedArtifact,
        manifest_index: dict[str, dict[str, Any]],
        artifacts: dict[str, CapturedArtifact],
    ) -> None:
        self.root = root
        self._inventory = inventory
        self.manifest = manifest
        self.manifest_artifact = manifest_artifact
        self.manifest_index = manifest_index
        self._artifacts = artifacts

    @classmethod
    def capture(cls, root: Path) -> BundleSnapshot:
        lexical_root = Path(os.path.abspath(root))
        try:
            supplied = lexical_root.lstat()
            resolved_root = lexical_root.resolve(strict=True)
        except OSError as exc:
            raise ValidationError(
                f"{root}: cannot resolve bundle root: {exc}"
            ) from exc
        if (
            lexical_root.is_symlink()
            or _is_reparse(supplied)
        ):
            raise ValidationError(
                f"{root}: bundle root aliases and reparses are forbidden"
            )
        root = resolved_root
        inventory = _tree_inventory(root)
        manifest_entry = inventory.get(MANIFEST_NAME)
        if manifest_entry is None:
            raise ValidationError("bundle-manifest.json is missing")
        manifest_artifact = _read_captured(
            root,
            MANIFEST_NAME,
            manifest_entry,
            16 * 1024 * 1024,
            role="manifest",
        )
        assert manifest_artifact.document is not None
        manifest = manifest_artifact.document
        observed = SCHEMA_SET.validate(manifest)
        if observed != "dao_m4_bundle_manifest":
            raise ValidationError(
                "bundle-manifest.json: expected dao_m4_bundle_manifest"
            )
        files = manifest["files"]
        paths = [entry["path"] for entry in files]
        if len(paths) != len(set(paths)):
            raise ValidationError("$.files: manifest paths must be unique")
        discovered_files = {
            locator
            for locator, entry in inventory.items()
            if entry.kind == "file"
        }
        expected_tree = set(paths) | {MANIFEST_NAME}
        if discovered_files != expected_tree:
            missing = sorted(expected_tree - discovered_files)
            extra = sorted(discovered_files - expected_tree)
            raise ValidationError(
                f"bundle tree differs; missing={missing}, extra={extra}"
            )
        counts = Counter(entry["role"] for entry in files)
        require_equal(dict(counts), ROLE_COUNTS, "$.files role counts")
        manifest_index = {entry["path"]: entry for entry in files}
        artifacts: dict[str, CapturedArtifact] = {}
        for entry in files:
            locator = entry["path"]
            role = entry["role"]
            ceiling = ROLE_BYTE_CEILINGS[role]
            if entry["size_bytes"] > ceiling:
                raise ValidationError(
                    f"$.files {locator}: {role} exceeds its byte ceiling"
                )
            expected_media = (
                "application/octet-stream"
                if role in ("database", "prefix")
                else "application/json"
            )
            require_equal(
                entry["media_type"],
                expected_media,
                f"$.files {locator} media_type",
            )
            captured = _read_captured(
                root, locator, inventory[locator], ceiling, role=role
            )
            require_equal(
                captured.size, entry["size_bytes"], f"$.files {locator} size"
            )
            require_equal(
                captured.sha256, entry["sha256"], f"$.files {locator} sha256"
            )
            if role == "prefix":
                require_equal(
                    captured.size, PREFIX_BYTES, f"$.files {locator} size"
                )
            artifacts[locator] = captured
        return cls(
            root,
            inventory,
            manifest,
            manifest_artifact,
            manifest_index,
            artifacts,
        )

    def artifact(self, locator: str) -> CapturedArtifact:
        try:
            return self._artifacts[locator]
        except KeyError as exc:
            raise ValidationError(f"{locator}: artifact is not in the snapshot") from exc

    def load_document(
        self, locator: str, maximum_bytes: int, expected_type: str
    ) -> tuple[dict[str, Any], int, str]:
        artifact = self.artifact(locator)
        if artifact.size > maximum_bytes:
            raise ValidationError(
                f"{locator}: artifact exceeds {maximum_bytes} bytes"
            )
        if artifact.document is None:
            raise ValidationError(f"{locator}: expected a JSON document")
        observed = SCHEMA_SET.validate(artifact.document)
        if observed != expected_type:
            raise ValidationError(
                f"{locator}: expected document_type {expected_type!r}, "
                f"got {observed!r}"
            )
        return artifact.document, artifact.size, artifact.sha256

    def json_document(self, locator: str) -> tuple[dict[str, Any], int, str]:
        artifact = self.artifact(locator)
        if artifact.document is None:
            raise ValidationError(f"{locator}: expected a JSON document")
        return artifact.document, artifact.size, artifact.sha256

    def file_identity(self, locator: str) -> tuple[int, str]:
        artifact = self.artifact(locator)
        return artifact.size, artifact.sha256

    def database_projection(self, locator: str) -> tuple[int, str, bytes]:
        artifact = self.artifact(locator)
        if artifact.role != "database" or artifact.prefix is None:
            raise ValidationError(f"{locator}: expected a database artifact")
        return artifact.size, artifact.sha256, artifact.prefix

    def binary_payload(self, locator: str, expected_role: str) -> bytes:
        artifact = self.artifact(locator)
        if artifact.role != expected_role or artifact.payload is None:
            raise ValidationError(f"{locator}: expected {expected_role} bytes")
        return artifact.payload

    def recheck(self) -> None:
        """Require the exact tree and every identity to remain unchanged."""
        observed = _tree_inventory(self.root)
        if observed != self._inventory:
            raise ValidationError(
                "bundle tree or file identities changed during validation"
            )
