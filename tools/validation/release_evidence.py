"""Validate and safely stage detached exact-HEAD release evidence."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .release_evidence_adapters import (
    AdapterSelection,
    selected_adapter,
    validate_adapter_policy,
)
from .release_evidence_git import (
    git_blob,
    git_blob_size,
    git_has_gitlinks,
    git_head,
    git_status_tracked,
    git_status_untracked,
)
from .release_evidence_model import (
    ADAPTER_ID,
    ACCEPTANCE_UNTRACKED_EXCEPTION,
    COMMIT,
    HARD_MAX_OVERLAY_BYTES,
    IDENTIFIER,
    OVERLAY_NAME,
    OVERLAY_SCHEMA_PATH,
    POLICY_PATH,
    SHA256,
    VERIFICATIONS,
    Limits,
    ReleaseEvidenceError,
    ResolvedFile,
    ResolvedOverlay,
    StableObjectIdentity,
    bound_json_hard,
    bound_json_with_policy,
    canonical_json,
    canonical_relative_path,
    exact_keys,
    fail,
    parse_json,
    require_integer,
    validate_policy,
)
from .release_evidence_tree import (
    atomic_publish_no_replace as _atomic_publish_no_replace,
    copy_file_exclusive as _copy_file_exclusive,
    directory_metadata as _directory_metadata,
    fsync_directory as _fsync_directory,
    read_regular_bounded as _read_regular_bounded,
    read_regular_snapshot as _read_regular_snapshot,
    scan_regular_files as _scan_regular_files,
    sha256 as _sha256,
    stable_object_identity as _stable_object_identity,
)


def _clean_exact_head(
    repo_root: Path,
    expected_commit: str,
) -> None:
    """Check a quiescent workspace twice; this is not a transactional lock."""

    if not isinstance(expected_commit, str) or not COMMIT.fullmatch(expected_commit):
        fail("repository.commit: expected full lowercase commit")
    head = git_head(repo_root)
    if head != expected_commit:
        fail(f"repository.commit: expected current HEAD {head!r}")
    for _ in range(2):
        if git_has_gitlinks(repo_root):
            fail("repository: release evidence does not support tracked gitlinks")
        if git_status_tracked(repo_root):
            fail(
                "repository: exact-HEAD release evidence requires "
                "a clean index/worktree"
            )
        dirty = git_status_untracked(
            repo_root,
            (f":(top,exclude){ACCEPTANCE_UNTRACKED_EXCEPTION}",),
        )
        if dirty:
            fail("repository: exact-HEAD release evidence requires a clean worktree")
        closing_head = git_head(repo_root)
        if closing_head != expected_commit:
            fail("repository.commit: HEAD changed during exact-HEAD closure")


def _git_blob(repo_root: Path, commit: str, relative_path: str) -> bytes:
    object_name = f"{commit}:{relative_path}"
    size = git_blob_size(repo_root, object_name)
    if size > HARD_MAX_OVERLAY_BYTES:
        fail(f"contracts: {relative_path!r} exceeds checked blob-size limit")
    content = git_blob(repo_root, object_name, size)
    if len(content) != size:
        fail(f"contracts: Git returned truncated blob for {relative_path!r}")
    return content


def _checked_contract(
    repo_root: Path,
    commit: str,
    relative_path: str,
    expected_hash: str,
    location: str,
) -> bytes:
    canonical_relative_path(relative_path, f"{location}.path")
    if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
        fail(f"{location}.sha256: expected lowercase SHA-256")
    blob = _git_blob(repo_root, commit, relative_path)
    if _sha256(blob) != expected_hash:
        fail(f"{location}: bound Git blob hash mismatch")
    working = _read_regular_bounded(
        repo_root.joinpath(*PurePosixPath(relative_path).parts),
        HARD_MAX_OVERLAY_BYTES,
        f"{location}.working_tree",
    )
    if working != blob:
        fail(f"{location}: working file differs from bound Git blob")
    return blob


def _overlay_outside_repository(repo_root: Path, overlay_root: Path) -> None:
    repository = repo_root.resolve(strict=True)
    overlay = overlay_root.resolve(strict=True)
    if overlay == repository or overlay.is_relative_to(repository):
        fail("overlay root: detached release evidence must be outside the repository")


def _validate_contracts_and_policy(
    repo_root: Path,
    commit: str,
    raw_contracts: Any,
) -> tuple[Limits, tuple[AdapterSelection, ...]]:
    contracts = exact_keys(
        raw_contracts,
        {
            "overlay_schema_path",
            "overlay_schema_sha256",
            "policy_path",
            "policy_sha256",
        },
        "overlay.contracts",
    )
    if contracts["overlay_schema_path"] != OVERLAY_SCHEMA_PATH:
        fail(f"overlay.contracts.overlay_schema_path: expected {OVERLAY_SCHEMA_PATH!r}")
    _checked_contract(
        repo_root,
        commit,
        contracts["overlay_schema_path"],
        contracts["overlay_schema_sha256"],
        "overlay.contracts.overlay_schema",
    )
    if contracts["policy_path"] != POLICY_PATH:
        fail(f"overlay.contracts.policy_path: expected {POLICY_PATH!r}")
    policy_content = _checked_contract(
        repo_root,
        commit,
        contracts["policy_path"],
        contracts["policy_sha256"],
        "overlay.contracts.policy",
    )
    policy = parse_json(policy_content, "checked evidence policy")
    bound_json_hard(policy, "checked evidence policy")
    checker = lambda path, digest, location: _checked_contract(
        repo_root, commit, path, digest, location
    )
    return validate_policy(policy, checker, validate_adapter_policy)


def _validate_file_inventory(
    declared_files: Any,
    actual_files: dict[str, ResolvedFile],
    limits: Limits,
) -> list[str]:
    if (
        not isinstance(declared_files, list)
        or not 1 <= len(declared_files) <= limits.max_file_count
    ):
        fail("overlay.files: invalid file count")
    declared_paths: list[str] = []
    for index, raw in enumerate(declared_files):
        location = f"overlay.files[{index}]"
        item = exact_keys(raw, {"path", "sha256", "size"}, location)
        relative = canonical_relative_path(item["path"], f"{location}.path")
        if relative == OVERLAY_NAME:
            fail(f"{location}.path: overlay document must not inventory itself")
        expected_hash = item["sha256"]
        if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
            fail(f"{location}.sha256: expected lowercase SHA-256")
        size = item["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            fail(f"{location}.size: expected nonnegative integer")
        actual = actual_files.get(relative)
        if actual is None:
            fail(f"{location}.path: declared file is absent")
        if actual.size != size:
            fail(f"{location}: size mismatch")
        if actual.sha256 != expected_hash:
            fail(f"{location}: SHA-256 mismatch")
        declared_paths.append(relative)
    if declared_paths != sorted(set(declared_paths)):
        fail("overlay.files: paths must be unique and sorted")
    if set(declared_paths) != set(actual_files):
        fail("overlay.files: inventory is not exact and complete")
    return declared_paths


def _resolve_evidence_files(
    item: dict[str, Any],
    location: str,
    actual_files: dict[str, ResolvedFile],
    limits: Limits,
) -> tuple[ResolvedFile, ...]:
    file_paths = item["files"]
    if (
        not isinstance(file_paths, list)
        or not 1 <= len(file_paths) <= limits.max_files_per_evidence
    ):
        fail(f"{location}.files: invalid file count")
    canonical_files = [
        canonical_relative_path(path, f"{location}.files") for path in file_paths
    ]
    if canonical_files != sorted(set(canonical_files)):
        fail(f"{location}.files: paths must be unique and sorted")
    resolved: list[ResolvedFile] = []
    for relative in canonical_files:
        file = actual_files.get(relative)
        if file is None:
            fail(f"{location}.files: {relative!r} is absent from inventory")
        resolved.append(file)
    return tuple(resolved)


def _run_evidence_adapters(
    raw_entries: Any,
    actual_files: dict[str, ResolvedFile],
    policy_adapters: tuple[AdapterSelection, ...],
    commit: str,
    limits: Limits,
) -> tuple[tuple[tuple[str, dict[str, Any]], ...], set[str]]:
    if (
        not isinstance(raw_entries, list)
        or not 1 <= len(raw_entries) <= limits.max_evidence_count
    ):
        fail("overlay.evidence: invalid evidence count")
    evidence_ids: list[str] = []
    referenced: set[str] = set()
    outputs: list[tuple[str, dict[str, Any]]] = []
    adapter_file_visits = 0
    adapter_input_bytes = 0
    keys = {
        "id",
        "capability_id",
        "verification",
        "adapter",
        "files",
        "expected_output",
    }
    for index, raw in enumerate(raw_entries):
        location = f"overlay.evidence[{index}]"
        item = exact_keys(raw, keys, location)
        evidence_id = item["id"]
        capability_id = item["capability_id"]
        adapter_id = item["adapter"]
        verification = item["verification"]
        if not isinstance(evidence_id, str) or not IDENTIFIER.fullmatch(evidence_id):
            fail(f"{location}.id: invalid evidence ID")
        if not isinstance(capability_id, str) or not IDENTIFIER.fullmatch(capability_id):
            fail(f"{location}.capability_id: invalid capability ID")
        if not isinstance(adapter_id, str) or not ADAPTER_ID.fullmatch(adapter_id):
            fail(f"{location}.adapter: invalid adapter ID")
        if not isinstance(verification, str) or verification not in VERIFICATIONS:
            fail(f"{location}.verification: invalid verification level")
        resolved_files = _resolve_evidence_files(
            item, location, actual_files, limits
        )
        adapter_file_visits += len(resolved_files)
        adapter_input_bytes += sum(file.size for file in resolved_files)
        if adapter_file_visits > limits.max_adapter_file_visits:
            fail("overlay.evidence: adapter file-visit limit exceeded")
        if adapter_input_bytes > limits.max_adapter_input_bytes:
            fail("overlay.evidence: adapter input-byte limit exceeded")
        referenced.update(file.relative_path for file in resolved_files)
        selection = selected_adapter(policy_adapters, adapter_id)
        if selection is None:
            fail(f"{location}.adapter: unknown adapter {adapter_id!r}")
        status = selection.status
        if status != "enabled":
            fail(f"{location}.adapter: adapter {adapter_id!r} is {status}")
        if selection.spec.exact_verification != verification:
            fail(f"{location}.verification: intrinsic adapter mismatch")
        expected_output = item["expected_output"]
        if not isinstance(expected_output, dict):
            fail(f"{location}.expected_output: expected object")
        bound_json_with_policy(expected_output, limits, f"{location}.expected_output")
        implementation = selection.spec.implementation
        if implementation is None:
            fail(f"{location}.adapter: adapter implementation is unavailable")
        actual_output = implementation(item, resolved_files, commit, limits)
        if canonical_json(actual_output) != canonical_json(expected_output):
            fail(f"{location}.expected_output: adapter output mismatch")
        evidence_ids.append(evidence_id)
        outputs.append((evidence_id, actual_output))
    if evidence_ids != sorted(set(evidence_ids)):
        fail("overlay.evidence: IDs must be unique and sorted")
    return tuple(outputs), referenced


def _validate_overlay(
    repo_root: Path,
    overlay_root: Path,
    *,
    private_staged: bool,
) -> ResolvedOverlay:
    repo_root = repo_root.resolve(strict=True)
    _directory_metadata(overlay_root, "overlay root")
    overlay_root = overlay_root.resolve(strict=True)
    if not private_staged:
        _overlay_outside_repository(repo_root, overlay_root)
    overlay_content, overlay_identity = _read_regular_snapshot(
        overlay_root / OVERLAY_NAME,
        HARD_MAX_OVERLAY_BYTES,
        "release evidence overlay",
    )
    overlay = parse_json(overlay_content, "release evidence overlay")
    bound_json_hard(overlay, "release evidence overlay")
    overlay = exact_keys(
        overlay,
        {"schema_version", "repository", "contracts", "files", "evidence"},
        "overlay",
    )
    require_integer(overlay["schema_version"], 1, "overlay.schema_version")
    repository = exact_keys(
        overlay["repository"], {"commit", "dirty"}, "overlay.repository"
    )
    commit = repository["commit"]
    if repository["dirty"] is not False:
        fail("overlay.repository.dirty: release evidence must declare false")
    _clean_exact_head(repo_root, commit)
    limits, adapters = _validate_contracts_and_policy(
        repo_root, commit, overlay["contracts"]
    )
    if len(overlay_content) > limits.max_overlay_bytes:
        fail("release evidence overlay: checked size limit exceeded")
    bound_json_with_policy(overlay, limits, "release evidence overlay")
    initial_tree = _scan_regular_files(overlay_root, limits)
    actual_files = initial_tree.file_map()
    _validate_file_inventory(overlay["files"], actual_files, limits)
    outputs, referenced = _run_evidence_adapters(
        overlay["evidence"], actual_files, adapters, commit, limits
    )
    if referenced != set(actual_files):
        fail("overlay.evidence: every inventoried file must be referenced")
    overlay_closure, overlay_closure_identity = _read_regular_snapshot(
        overlay_root / OVERLAY_NAME,
        HARD_MAX_OVERLAY_BYTES,
        "release evidence overlay closure",
    )
    if (
        overlay_closure != overlay_content
        or overlay_closure_identity != overlay_identity
    ):
        fail("release evidence overlay: changed during validation")
    closed_tree = _scan_regular_files(overlay_root, limits)
    if closed_tree != initial_tree:
        fail("overlay inventory: changed during validation")
    closed_files = closed_tree.file_map()
    _clean_exact_head(repo_root, commit)
    closed_limits, closed_adapters = _validate_contracts_and_policy(
        repo_root, commit, overlay["contracts"]
    )
    if closed_limits != limits or closed_adapters != adapters:
        fail("overlay contracts: changed during validation")
    result = ResolvedOverlay(
        root=overlay_root,
        commit=commit,
        overlay_size=len(overlay_content),
        overlay_sha256=_sha256(overlay_content),
        overlay_identity=overlay_identity,
        files=tuple(closed_files[path] for path in sorted(closed_files)),
        outputs=outputs,
    )
    _clean_exact_head(repo_root, commit)
    return result


def validate_overlay(repo_root: Path, overlay_root: Path) -> ResolvedOverlay:
    """Validate a detached source overlay against the exact clean HEAD."""

    return _validate_overlay(repo_root, overlay_root, private_staged=False)


def _new_private_stage(destination_root: Path) -> Path:
    for _ in range(16):
        candidate = destination_root.parent / (
            f"{destination_root.name}-stage-{secrets.token_hex(8)}"
        )
        try:
            os.mkdir(candidate, 0o700)
            return candidate
        except FileExistsError:
            continue
    fail("cannot allocate a collision-free private staging directory")


def _require_stable_directory(
    path: Path,
    expected_identity: StableObjectIdentity,
    location: str,
) -> None:
    metadata = _directory_metadata(path, location)
    if _stable_object_identity(metadata) == expected_identity:
        return
    fail(f"{location}: directory identity changed")


def _trusted_staging_parent(path: Path) -> os.stat_result:
    """Require a POSIX parent chain protected from other local accounts."""

    parent_metadata = _directory_metadata(path, "staging parent")
    if parent_metadata.st_uid != os.geteuid():
        fail("staging parent: must be owned by the current account")
    for ancestor in (path, *path.parents):
        metadata = _directory_metadata(ancestor, "staging parent chain")
        shared_write = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if shared_write and not metadata.st_mode & stat.S_ISVTX:
            fail(
                "staging parent chain: shared-writable directories "
                "must use the sticky bit"
            )
    return parent_metadata


def stage_overlay(
    repo_root: Path,
    source_root: Path,
    destination_root: Path,
) -> ResolvedOverlay:
    """Publish under a quiescent, single-owner POSIX parent.

    The caller that owns the checked parent must not concurrently rename or
    mutate its staging paths. Other-account mutation is restricted by checked
    ownership and parent-chain permissions. The function rechecks identities
    around every phase, publishes with no-replace semantics, retains failed
    state for inspection, and never recursively deletes a path. Windows
    staging remains unavailable until equivalent ACL/handle checks exist.
    """

    if os.name == "nt":
        fail(
            "staging publication is unavailable on Windows pending "
            "trusted-parent handle enforcement"
        )
    resolved = validate_overlay(repo_root, source_root)
    repo_root = repo_root.resolve(strict=True)
    requested_destination = destination_root.absolute()
    try:
        resolved_parent = requested_destination.parent.resolve(strict=True)
    except OSError as error:
        fail(f"staging parent: cannot resolve directory: {error}")
    destination_root = resolved_parent / requested_destination.name
    acceptance_root = repo_root / "artifacts" / "acceptance"
    if destination_root.is_relative_to(repo_root) and not destination_root.is_relative_to(
        acceptance_root
    ):
        fail(
            "staging destination: repository publication is restricted "
            "to artifacts/acceptance"
        )
    parent_metadata = _trusted_staging_parent(destination_root.parent)
    parent_identity = _stable_object_identity(parent_metadata)
    try:
        destination_root.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        fail(f"staging destination: cannot inspect target: {error}")
    else:
        fail("staging destination already exists; refusing to overwrite")

    private_stage = _new_private_stage(destination_root)
    published = False
    try:
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        private_metadata = _directory_metadata(private_stage, "private staging root")
        private_identity = _stable_object_identity(private_metadata)
        if private_metadata.st_dev != parent_metadata.st_dev:
            fail("private stage and destination must be on the same volume")
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _require_stable_directory(
            private_stage, private_identity, "private staging root"
        )
        overlay_source = ResolvedFile(
            relative_path=OVERLAY_NAME,
            path=resolved.root / OVERLAY_NAME,
            size=resolved.overlay_size,
            sha256=resolved.overlay_sha256,
            identity=resolved.overlay_identity,
        )
        for source in resolved.files:
            _require_stable_directory(
                destination_root.parent, parent_identity, "staging parent"
            )
            _require_stable_directory(
                private_stage, private_identity, "private staging root"
            )
            destination = private_stage.joinpath(
                *PurePosixPath(source.relative_path).parts
            )
            _copy_file_exclusive(source, destination)
            _require_stable_directory(
                private_stage, private_identity, "private staging root"
            )
        overlay_destination = private_stage / OVERLAY_NAME
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _copy_file_exclusive(overlay_source, overlay_destination)
        _require_stable_directory(
            private_stage, private_identity, "private staging root"
        )
        directories = {
            private_stage,
            *(
                ancestor
                for file in resolved.files
                for ancestor in private_stage.joinpath(
                    *PurePosixPath(file.relative_path).parts
                ).parents
                if ancestor == private_stage or private_stage in ancestor.parents
            ),
        }
        for directory in sorted(
            directories, key=lambda item: len(item.parts), reverse=True
        ):
            _fsync_directory(directory)
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _require_stable_directory(
            private_stage, private_identity, "private staging root"
        )
        staged = _validate_overlay(
            repo_root, private_stage, private_staged=True
        )
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _require_stable_directory(
            private_stage, private_identity, "private staging root"
        )
        expected = tuple(
            (item.relative_path, item.size, item.sha256) for item in resolved.files
        )
        observed = tuple(
            (item.relative_path, item.size, item.sha256) for item in staged.files
        )
        if (
            staged.commit != resolved.commit
            or staged.overlay_sha256 != resolved.overlay_sha256
            or staged.outputs != resolved.outputs
            or observed != expected
        ):
            fail("staging destination differs from validated source")
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _atomic_publish_no_replace(private_stage, destination_root)
        published = True
        _require_stable_directory(
            destination_root.parent, parent_identity, "staging parent"
        )
        _require_stable_directory(
            destination_root, private_identity, "published staging root"
        )
        _fsync_directory(destination_root.parent)
        return _validate_overlay(
            repo_root, destination_root, private_staged=True
        )
    except BaseException as error:
        if published:
            raise ReleaseEvidenceError(
                f"published destination retained at {destination_root}: {error}"
            ) from error
        raise ReleaseEvidenceError(
            f"private stage retained at {private_stage}: {error}"
        ) from error
