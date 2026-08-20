#!/usr/bin/env python3
"""Bounded, non-extracting validation for complete DAO A1 bundle trees."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a1_contract import (
    CHECKED_PLAN,
    MAX_DOCUMENT_BYTES,
    PLAN_SHA256,
    SCHEMA_SET,
    ValidationError,
    load_bounded_json,
    parse_json_bytes,
    require_equal,
    validate_document,
)

MANIFEST_NAME = "bundle-manifest.json"
MAX_MANIFEST_BYTES = 67_108_864
MAX_MANIFEST_FILES = 262_399
MAX_TREE_ENTRIES = 263_000
MAX_TREE_DEPTH = 8
MAX_BUNDLE_BYTES = 805_306_368
MAX_PAGE_BLOBS = 262_144
MAX_RETAINED_JSON_BYTES = 268_435_456
PAGE_BYTES = 2_048
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
LOCATOR = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]{0,127}/){0,7}"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z"
)
PAGE_LOCATOR = re.compile(r"page-store/[0-9a-f]{64}\.page\Z")


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    links: int


@dataclass(frozen=True)
class TreeFile:
    size: int
    modified_ns: int
    identity: FileIdentity


@dataclass(frozen=True)
class Artifact:
    locator: str
    role: str
    size: int
    sha256: str
    document: dict[str, Any] | None


_WINDOWS_IDENTITY_API: tuple[Any, Any, type[Any]] | None = None


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _descriptor_identity(descriptor: int, metadata: os.stat_result) -> FileIdentity:
    if os.name != "nt":
        return FileIdentity(metadata.st_dev, metadata.st_ino, metadata.st_nlink)

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
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
        query.restype = wintypes.BOOL
        _WINDOWS_IDENTITY_API = (library, query, ByHandleFileInformation)
    _, query, information_type = _WINDOWS_IDENTITY_API
    information = information_type()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    if not query(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise OSError(error, "GetFileInformationByHandle failed")
    return FileIdentity(
        information.volume_serial_number,
        (information.file_index_high << 32) | information.file_index_low,
        information.number_of_links,
    )


def _path_identity(path: Path, metadata: os.stat_result) -> FileIdentity:
    if os.name != "nt":
        return _descriptor_identity(-1, metadata)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        return _descriptor_identity(descriptor, os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _safe_locator(locator: Any, label: str) -> str:
    if not isinstance(locator, str) or LOCATOR.fullmatch(locator) is None:
        raise ValidationError(f"{label}: unsafe bundle locator")
    if any(part in (".", "..") for part in locator.split("/")):
        raise ValidationError(f"{label}: traversal is forbidden")
    return locator


def _inventory(root: Path) -> tuple[dict[str, TreeFile], set[str]]:
    """Enumerate a bounded tree once without following any filesystem alias."""
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect bundle root: {exc}") from exc
    if (
        root.is_symlink()
        or _is_reparse(root_metadata)
        or not stat.S_ISDIR(root_metadata.st_mode)
    ):
        raise ValidationError(f"{root}: bundle root must be a regular directory")

    files: dict[str, TreeFile] = {}
    directories: set[str] = set()
    identities: set[tuple[int, int]] = set()
    casefolded: dict[str, str] = {}
    pending = [(root, 0)]
    entries_seen = 0
    try:
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_TREE_ENTRIES:
                        raise ValidationError("bundle exceeds directory-entry limit")
                    metadata = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or _is_reparse(metadata):
                        raise ValidationError(
                            f"{entry.path}: links and reparse points are forbidden"
                        )
                    candidate = Path(entry.path)
                    locator = candidate.relative_to(root).as_posix()
                    _safe_locator(locator, entry.path)
                    folded = locator.casefold()
                    previous = casefolded.get(folded)
                    if previous is not None and previous != locator:
                        raise ValidationError(
                            f"{locator}: case-collides with another bundle entry"
                        )
                    casefolded[folded] = locator
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth >= MAX_TREE_DEPTH:
                            raise ValidationError("bundle exceeds directory-depth limit")
                        directories.add(locator)
                        pending.append((candidate, depth + 1))
                    elif stat.S_ISREG(metadata.st_mode):
                        identity = _path_identity(candidate, metadata)
                        identity_key = (identity.device, identity.inode)
                        if identity.links != 1 or identity_key in identities:
                            raise ValidationError(
                                f"{locator}: hard-linked bundle files are forbidden"
                            )
                        identities.add(identity_key)
                        files[locator] = TreeFile(
                            metadata.st_size,
                            metadata.st_mtime_ns,
                            identity,
                        )
                    else:
                        raise ValidationError(f"{locator}: non-regular bundle entry")
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{root}: cannot enumerate bundle: {exc}") from exc
    return files, directories


def _read_artifact(
    root: Path,
    locator: str,
    expected: TreeFile,
    maximum: int,
    *,
    retain_json: bool,
) -> tuple[int, str, dict[str, Any] | None]:
    """Hash one stable regular file in bounded chunks, retaining JSON only."""
    path = root.joinpath(*locator.split("/"))
    if expected.size > maximum:
        raise ValidationError(f"{locator}: artifact exceeds {maximum} bytes")
    digest = hashlib.sha256()
    retained = bytearray()
    total = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or _is_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size != expected.size
            or before.st_mtime_ns != expected.modified_ns
            or _path_identity(path, before) != expected.identity
        ):
            raise ValidationError(f"{locator}: identity changed before read")
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            opened_identity = _descriptor_identity(handle.fileno(), opened)
            if opened_identity != expected.identity:
                raise ValidationError(f"{locator}: identity changed while opening")
            while True:
                chunk = handle.read(min(65_536, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise ValidationError(f"{locator}: artifact exceeds {maximum} bytes")
                digest.update(chunk)
                if retain_json:
                    retained.extend(chunk)
            after_descriptor = os.fstat(handle.fileno())
            after_descriptor_identity = _descriptor_identity(
                handle.fileno(), after_descriptor
            )
        after_path = path.lstat()
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{locator}: cannot read artifact: {exc}") from exc
    if (
        total != expected.size
        or after_descriptor_identity != expected.identity
        or after_descriptor.st_size != expected.size
        or path.is_symlink()
        or _is_reparse(after_path)
        or not stat.S_ISREG(after_path.st_mode)
        or after_path.st_size != expected.size
        or after_path.st_mtime_ns != expected.modified_ns
        or _path_identity(path, after_path) != expected.identity
    ):
        raise ValidationError(f"{locator}: artifact changed during read")
    document = None
    if retain_json:
        value = parse_json_bytes(bytes(retained), locator)
        if not isinstance(value, dict):
            raise ValidationError(f"{locator}: JSON artifact must be an object")
        document = value
    return total, digest.hexdigest(), document


def _media_is_json(entry: dict[str, Any]) -> bool:
    return entry["media_type"] == "application/json"


def _read_page_payload(root: Path, locator: str, expected: TreeFile) -> bytes:
    """Re-read one immutable page for ordered snapshot reconstruction."""
    path = root.joinpath(*locator.split("/"))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or _is_reparse(before)
            or before.st_size != PAGE_BYTES
            or _path_identity(path, before) != expected.identity
        ):
            raise ValidationError(f"{locator}: page identity changed before reconstruction")
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if _descriptor_identity(handle.fileno(), opened) != expected.identity:
                raise ValidationError(f"{locator}: page identity changed while opening")
            payload = handle.read(PAGE_BYTES + 1)
            after_descriptor = os.fstat(handle.fileno())
            after_identity = _descriptor_identity(handle.fileno(), after_descriptor)
        after_path = path.lstat()
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{locator}: cannot reconstruct page: {exc}") from exc
    if (
        len(payload) != PAGE_BYTES
        or after_descriptor.st_size != PAGE_BYTES
        or after_identity != expected.identity
        or path.is_symlink()
        or _is_reparse(after_path)
        or not stat.S_ISREG(after_path.st_mode)
        or after_path.st_size != PAGE_BYTES
        or _path_identity(path, after_path) != expected.identity
    ):
        raise ValidationError(f"{locator}: page changed during reconstruction")
    return payload


class BundleSnapshot:
    """Exact inventory with intentionally ordered, lazy artifact reads."""

    def __init__(
        self,
        root: Path,
        inventory: dict[str, TreeFile],
        directories: set[str],
        manifest: dict[str, Any],
        manifest_sha256: str,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        self.root = root
        self.inventory = inventory
        self.directories = directories
        self.manifest = manifest
        self.manifest_sha256 = manifest_sha256
        self.entries = entries
        self._artifacts: dict[str, Artifact] = {}
        self._retained_json_bytes = 0

    @classmethod
    def capture(cls, supplied_root: Path) -> BundleSnapshot:
        root = Path(os.path.abspath(supplied_root))
        inventory, directories = _inventory(root)
        manifest_stamp = inventory.get(MANIFEST_NAME)
        if manifest_stamp is None:
            raise ValidationError(f"{MANIFEST_NAME}: missing")
        manifest_size, manifest_hash, manifest = _read_artifact(
            root,
            MANIFEST_NAME,
            manifest_stamp,
            MAX_MANIFEST_BYTES,
            retain_json=True,
        )
        assert manifest is not None
        document_type = validate_document(manifest)
        if "a1" not in document_type or not document_type.endswith("bundle_manifest"):
            raise ValidationError(f"{MANIFEST_NAME}: expected A1 bundle manifest")
        files = manifest["files"]
        if len(files) > MAX_MANIFEST_FILES:
            raise ValidationError("$.files: exceeds acquisition artifact-count ceiling")
        path_set: set[str] = set()
        folded: set[str] = set()
        total_bytes = 0
        page_blob_count = 0
        for index, entry in enumerate(files):
            locator = _safe_locator(entry["path"], f"$.files[{index}].path")
            if entry["role"] == "acquisition_log":
                raise ValidationError(
                    f"{locator}: role is not produced by the frozen A1 acquisition"
                )
            if locator == MANIFEST_NAME:
                raise ValidationError("$.files: manifest cannot inventory itself")
            if locator in path_set:
                raise ValidationError(f"$.files: duplicate path {locator!r}")
            if locator.casefold() in folded:
                raise ValidationError(f"$.files: case-colliding path {locator!r}")
            path_set.add(locator)
            folded.add(locator.casefold())
            total_bytes += entry["size_bytes"]
            expected_media = {
                "page_blob": "application/octet-stream",
                "acquisition_log": "text/plain",
            }.get(entry["role"], "application/json")
            require_equal(
                entry["media_type"],
                expected_media,
                f"{locator} media type",
            )
            if entry["role"] == "page_blob":
                page_blob_count += 1
                if PAGE_LOCATOR.fullmatch(locator) is None:
                    raise ValidationError(f"{locator}: invalid page-store locator")
                require_equal(entry["size_bytes"], PAGE_BYTES, f"{locator} size")
                require_equal(
                    Path(locator).stem,
                    entry["sha256"],
                    f"{locator} content address",
                )
        require_equal(
            total_bytes,
            manifest["bundle_size_bytes_excluding_manifest"],
            "$.bundle_size_bytes_excluding_manifest",
        )
        if total_bytes + manifest_size > MAX_BUNDLE_BYTES:
            raise ValidationError("bundle exceeds total retained-byte limit")
        if page_blob_count > MAX_PAGE_BLOBS:
            raise ValidationError("bundle exceeds page-blob count limit")
        discovered = set(inventory)
        expected = path_set | {MANIFEST_NAME}
        if discovered != expected:
            missing = sorted(expected - discovered)[:8]
            extra = sorted(discovered - expected)[:8]
            raise ValidationError(
                f"bundle inventory differs; missing={missing}, extra={extra}"
            )
        expected_directories = {
            parent.as_posix()
            for locator in path_set
            for parent in Path(locator).parents
            if parent != Path(".")
        }
        require_equal(directories, expected_directories, "bundle directory closure")

        entries = {entry["path"]: entry for entry in files}
        return cls(root, inventory, directories, manifest, manifest_hash, entries)

    def artifact(self, locator: str) -> Artifact:
        """Read, hash, and schema-check one artifact at its protocol phase."""
        cached = self._artifacts.get(locator)
        if cached is not None:
            return cached
        try:
            entry = self.entries[locator]
        except KeyError as exc:
            raise ValidationError(f"{locator}: artifact is absent") from exc
        role = entry["role"]
        is_json = _media_is_json(entry)
        maximum = MAX_DOCUMENT_BYTES if is_json else entry["size_bytes"]
        if role == "analysis_report":
            maximum = MAX_MANIFEST_BYTES
        if role == "page_blob":
            maximum = PAGE_BYTES
        size, digest, document = _read_artifact(
            self.root,
            locator,
            self.inventory[locator],
            maximum,
            retain_json=is_json,
        )
        require_equal(size, entry["size_bytes"], f"{locator} size")
        require_equal(digest, entry["sha256"], f"{locator} sha256")
        if is_json:
            self._retained_json_bytes += size
            if self._retained_json_bytes > MAX_RETAINED_JSON_BYTES:
                raise ValidationError("bundle exceeds retained JSON byte limit")
            assert document is not None
            SCHEMA_SET.validate_schema(document)
        captured = Artifact(locator, role, size, digest, document)
        self._artifacts[locator] = captured
        return captured

    def role_locators(self, role: str) -> list[str]:
        return [path for path, entry in self.entries.items() if entry["role"] == role]

    def unique_role_locator(self, role: str) -> str:
        selected = self.role_locators(role)
        if len(selected) != 1:
            raise ValidationError(f"$.files: expected exactly one {role!r} artifact")
        return selected[0]

    def unique_role(self, role: str) -> Artifact:
        return self.artifact(self.unique_role_locator(role))

    def recheck(self) -> None:
        observed, directories = _inventory(self.root)
        if observed != self.inventory or directories != self.directories:
            raise ValidationError("bundle tree changed during validation")


def _artifact_ref(snapshot: BundleSnapshot, reference: dict[str, Any], role: str) -> Artifact:
    locator = _safe_locator(reference["path"], "artifact reference path")
    try:
        artifact = snapshot.artifact(locator)
    except KeyError as exc:
        raise ValidationError(f"{locator}: referenced artifact is absent") from exc
    require_equal(artifact.role, role, f"{locator} role")
    require_equal(artifact.sha256, reference["sha256"], f"{locator} reference sha256")
    require_equal(artifact.size, reference["size_bytes"], f"{locator} reference size")
    return artifact


def _expected_checkpoints(plan: dict[str, Any]) -> list[str]:
    checkpoints = plan["checkpoint_design"]["checkpoint_ids"]
    require_equal(len(checkpoints), plan["checkpoint_design"]["count"], "plan checkpoint count")
    require_equal(
        len(checkpoints),
        plan["bounds"]["planned_checkpoints_per_replica"],
        "plan planned checkpoint count",
    )
    if len(checkpoints) > plan["bounds"]["max_checkpoints_per_replica"]:
        raise ValidationError("plan checkpoint count exceeds its ceiling")
    if len(checkpoints) != len(set(checkpoints)):
        raise ValidationError("plan checkpoint IDs are not unique")
    return checkpoints


def validate_bundle(root: Path) -> dict[str, Any]:
    """Validate A1 schema, inventory, and identity bindings only.

    Passing this function makes no physical-layout, scientific-outcome, DAO
    compatibility, or Rust-correctness claim.
    """
    SCHEMA_SET.lint()
    snapshot = BundleSnapshot.capture(root)
    plan_artifact = snapshot.unique_role("plan")
    environment_artifact = snapshot.unique_role("environment")
    analysis_locator = snapshot.unique_role_locator("analysis_report")
    assert plan_artifact.document is not None
    assert environment_artifact.document is not None
    plan = plan_artifact.document
    validate_document(plan, "dao_a1_allocation_maps_plan")
    checked_plan = load_bounded_json(CHECKED_PLAN)
    validate_document(checked_plan, "dao_a1_allocation_maps_plan")
    require_equal(plan, checked_plan, "retained checked plan")
    checked_plan_hash = hashlib.sha256(CHECKED_PLAN.read_bytes()).hexdigest()
    require_equal(checked_plan_hash, PLAN_SHA256, "preregistered plan sha256")
    require_equal(plan_artifact.sha256, checked_plan_hash, "retained checked plan sha256")
    checkpoints = _expected_checkpoints(plan)
    manifest = snapshot.manifest
    require_equal(manifest["experiment_id"], plan["experiment_id"], "manifest experiment")
    require_equal(manifest["repository_url"], plan["repository_binding"]["canonical_https_url"], "manifest repository")
    require_equal(manifest["plan_sha256"], plan_artifact.sha256, "manifest plan sha256")
    require_equal(
        manifest["environment_sha256"],
        environment_artifact.sha256,
        "manifest environment sha256",
    )
    require_equal(
        manifest["checkpoint_count"],
        plan["replicas"]["count"] * len(checkpoints),
        "manifest checkpoint count",
    )
    require_equal(manifest["replica_count"], plan["replicas"]["count"], "manifest replica count")
    environment = environment_artifact.document
    validate_document(environment, "dao_a1_environment")
    for key, expected in (
        ("experiment_id", plan["experiment_id"]),
        ("plan_sha256", plan_artifact.sha256),
        ("producer_commit", manifest["producer_commit"]),
        ("repository_url", manifest["repository_url"]),
        ("run_id", manifest["run_id"]),
    ):
        require_equal(environment[key], expected, f"environment {key}")
    provider_hash = environment["provider"]["server_sha256"]
    require_equal(manifest["provider_sha256"], provider_hash, "manifest provider sha256")
    expected_locations = plan["artifacts"]
    require_equal(plan_artifact.locator, expected_locations["plan"], "plan locator")
    require_equal(environment_artifact.locator, expected_locations["environment"], "environment locator")
    require_equal(analysis_locator, expected_locations["analysis_report"], "analysis locator")

    expected_pairs = {
        (replica, checkpoint)
        for replica in range(1, plan["replicas"]["count"] + 1)
        for checkpoint in checkpoints
    }
    observation_locators = snapshot.role_locators("replica_observation")
    if len(observation_locators) != plan["replicas"]["count"]:
        raise ValidationError("replica observation inventory is incomplete")
    observed_pairs: set[tuple[int, str]] = set()
    page_paths: set[str] = set()
    page_cache: dict[str, bytes] = {}
    index_paths: set[str] = set()
    seen_replicas: set[int] = set()
    observation_documents: list[dict[str, Any]] = []
    derivation_frozen = False
    for replica_ordinal in range(1, plan["replicas"]["count"] + 1):
        if replica_ordinal == plan["replicas"]["holdout"]:
            if snapshot.role_locators("derivation_candidate_set"):
                raise ValidationError(
                    "derivation candidate set requires independent recomputation"
                )
            derivation_frozen = True
        expected_observation = expected_locations["replica_observations"][
            replica_ordinal - 1
        ]
        artifact = snapshot.artifact(expected_observation)
        assert artifact.document is not None
        observation = artifact.document
        observation_documents.append(observation)
        validate_document(observation, "dao_a1_replica_observation")
        require_equal(observation["experiment_id"], plan["experiment_id"], "observation experiment")
        require_equal(observation["plan_sha256"], plan_artifact.sha256, "observation plan sha256")
        require_equal(
            observation["environment_sha256"],
            environment_artifact.sha256,
            "observation environment sha256",
        )
        require_equal(
            observation["producer_commit"],
            manifest["producer_commit"],
            "observation producer commit",
        )
        require_equal(observation["provider_sha256"], provider_hash, "observation provider sha256")
        require_equal(observation["run_id"], manifest["run_id"], "observation run id")
        require_equal(observation["repository_url"], manifest["repository_url"], "observation repository")
        replica = observation["replica"]
        require_equal(replica, replica_ordinal, "observation replica order")
        if replica == plan["replicas"]["holdout"] and not derivation_frozen:
            raise ValidationError("holdout was opened before derivation freeze")
        if replica in seen_replicas:
            raise ValidationError(f"replica {replica}: duplicate observation")
        seen_replicas.add(replica)
        require_equal(
            artifact.locator,
            expected_locations["replica_observations"][replica - 1],
            f"replica {replica} observation locator",
        )
        expected_binding = next(
            row for row in plan["tables"]["role_bindings"] if row["replica"] == replica
        )
        require_equal(
            observation["role_binding"],
            {role: expected_binding[role] for role in ("D", "L", "P", "H")},
            f"replica {replica} role binding",
        )
        checkpoint_rows = observation["checkpoints"]
        require_equal(len(checkpoint_rows), len(checkpoints), f"replica {replica} checkpoint count")
        previous_hashes: list[str] = []
        logical_bytes = 0
        changed_entries = 0
        for ordinal, (expected_id, checkpoint) in enumerate(zip(checkpoints, checkpoint_rows)):
            require_equal(checkpoint["checkpoint_id"], expected_id, "checkpoint order")
            observed_pairs.add((replica, checkpoint["checkpoint_id"]))
            page_index = _artifact_ref(snapshot, checkpoint["page_index"], "page_index")
            expected_index_path = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{expected_id}.json"
            require_equal(page_index.locator, expected_index_path, "page index locator")
            index_paths.add(page_index.locator)
            assert page_index.document is not None
            page_document = page_index.document
            validate_document(page_document, "dao_a1_page_index")
            require_equal(page_document["replica"], replica, "page index replica")
            require_equal(page_document["checkpoint_id"], expected_id, "page index checkpoint")
            require_equal(page_document["ordinal"], ordinal, "page index ordinal")
            require_equal(
                page_document["predecessor_checkpoint_id"],
                None if ordinal == 0 else checkpoints[ordinal - 1],
                "page index predecessor",
            )
            require_equal(page_document["plan_sha256"], plan_artifact.sha256, "page index plan sha256")
            require_equal(
                page_document["environment_sha256"],
                environment_artifact.sha256,
                "page index environment sha256",
            )
            require_equal(page_document["producer_commit"], manifest["producer_commit"], "page index producer commit")
            require_equal(page_document["provider_sha256"], provider_hash, "page index provider sha256")
            require_equal(page_document["run_id"], manifest["run_id"], "page index run id")
            page_hashes = page_document["ordered_page_sha256"]
            require_equal(len(page_hashes), page_document["page_count"], "page index page count")
            require_equal(page_document["file_size_bytes"], len(page_hashes) * PAGE_BYTES, "page index file bytes")
            require_equal(checkpoint["actual_file_pages"], len(page_hashes), "checkpoint page count")
            require_equal(checkpoint["actual_size_bytes"], page_document["file_size_bytes"], "checkpoint file size")
            compared = max(len(previous_hashes), len(page_hashes))
            expected_changed = [
                index
                for index in range(compared)
                if index >= len(previous_hashes)
                or index >= len(page_hashes)
                or previous_hashes[index] != page_hashes[index]
            ]
            require_equal(page_document["changed_page_indices"], expected_changed, "page index changed entries")
            changed_entries += len(expected_changed)
            logical_bytes += page_document["file_size_bytes"]
            reconstruction = hashlib.sha256()
            for digest in page_hashes:
                locator = f"page-store/{digest}.page"
                try:
                    page = snapshot.artifact(locator)
                except KeyError as exc:
                    raise ValidationError(f"{locator}: referenced page blob is absent") from exc
                require_equal(page.role, "page_blob", f"{locator} role")
                page_paths.add(locator)
                payload = page_cache.get(digest)
                if payload is None:
                    payload = _read_page_payload(
                        snapshot.root, locator, snapshot.inventory[locator]
                    )
                    require_equal(
                        hashlib.sha256(payload).hexdigest(),
                        digest,
                        f"{locator} reconstructed page sha256",
                    )
                    if len(page_cache) >= 256:
                        page_cache.pop(next(iter(page_cache)))
                    page_cache[digest] = payload
                reconstruction.update(payload)
            require_equal(reconstruction.hexdigest(), page_document["database_sha256"], "reconstructed database sha256")
            previous_hashes = page_hashes
        require_equal(logical_bytes, observation["logical_checkpoint_read_bytes"], f"replica {replica} logical bytes")
        require_equal(changed_entries, observation["changed_hash_entries"], f"replica {replica} changed entries")
        if logical_bytes > plan["bounds"]["max_logical_checkpoint_read_bytes_per_replica"]:
            raise ValidationError(f"replica {replica}: logical read-byte ceiling exceeded")
        if changed_entries > plan["bounds"]["max_changed_hash_entries"]:
            raise ValidationError(f"replica {replica}: changed-entry ceiling exceeded")
    require_equal(observed_pairs, expected_pairs, "checkpoint completeness")
    require_equal(seen_replicas, set(range(1, plan["replicas"]["count"] + 1)), "replica completeness")
    actual_indexes = set(snapshot.role_locators("page_index"))
    require_equal(actual_indexes, index_paths, "page-index closure")
    actual_pages = set(snapshot.role_locators("page_blob"))
    require_equal(actual_pages, page_paths, "referenced page-store closure")
    require_equal(manifest["page_blob_count"], len(actual_pages), "manifest page blob count")
    analysis_artifact = snapshot.artifact(analysis_locator)
    assert analysis_artifact.document is not None
    analysis = analysis_artifact.document
    validate_document(analysis, "dao_a1_analysis_report")
    for key, expected in (
        ("experiment_id", plan["experiment_id"]),
        ("plan_sha256", plan_artifact.sha256),
        ("producer_commit", manifest["producer_commit"]),
        ("run_id", manifest["run_id"]),
        ("derivation_replicas", plan["replicas"]["derivation"]),
        ("holdout_replica", plan["replicas"]["holdout"]),
        ("input_checkpoint_count", len(expected_pairs)),
    ):
        require_equal(analysis[key], expected, f"analysis {key}")
    snapshot.recheck()
    return {
        "manifest": manifest,
        "plan": plan,
        "environment": environment,
        "replica_observations": observation_documents,
        "analysis": analysis,
    }
