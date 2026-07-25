"""Immutable, bounded filesystem operations for release-evidence overlays."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .release_evidence_model import (
    OVERLAY_NAME,
    Limits,
    ObjectIdentity,
    ReleaseEvidenceError,
    ResolvedFile,
    StableObjectIdentity,
    canonical_relative_path,
    fail,
)

REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
IO_CHUNK_BYTES = 1024 * 1024


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def is_reparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & REPARSE_FLAG)


def regular_metadata(path: Path, location: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"{location}: cannot inspect file: {error}")
    if stat.S_ISLNK(metadata.st_mode) or is_reparse(metadata):
        fail(f"{location}: links and reparse points are forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        fail(f"{location}: expected regular file")
    if metadata.st_nlink > 1:
        fail(f"{location}: hard-linked files are forbidden")
    return metadata


def directory_metadata(path: Path, location: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"{location}: cannot inspect directory: {error}")
    if stat.S_ISLNK(metadata.st_mode) or is_reparse(metadata):
        fail(f"{location}: links and reparse points are forbidden")
    if not stat.S_ISDIR(metadata.st_mode):
        fail(f"{location}: expected directory")
    return metadata


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return object_identity(left) == object_identity(right)


def object_identity(metadata: os.stat_result) -> ObjectIdentity:
    return ObjectIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def stable_object_identity(metadata: os.stat_result) -> StableObjectIdentity:
    platform_token = 0
    if os.name == "nt":
        platform_token = getattr(
            metadata,
            "st_birthtime_ns",
            getattr(metadata, "st_ctime_ns", 0),
        )
    return StableObjectIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=stat.S_IFMT(metadata.st_mode),
        platform_token=platform_token,
    )


def read_regular_snapshot(
    path: Path, limit: int, location: str
) -> tuple[bytes, ObjectIdentity]:
    before = regular_metadata(path, location)
    if before.st_size > limit:
        fail(f"{location}: file exceeds {limit} byte limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"{location}: cannot open file safely: {error}")
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or is_reparse(opened):
            fail(f"{location}: opened object is not a regular non-reparse file")
        if not _same_identity(before, opened):
            fail(f"{location}: file changed while it was opened")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(IO_CHUNK_BYTES, limit + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > limit:
                fail(f"{location}: file exceeds {limit} byte limit")
        after = os.fstat(descriptor)
        if not _same_identity(opened, after) or consumed != opened.st_size:
            fail(f"{location}: file changed while it was read")
        return b"".join(chunks), object_identity(opened)
    finally:
        os.close(descriptor)


def read_regular_bounded(path: Path, limit: int, location: str) -> bytes:
    return read_regular_snapshot(path, limit, location)[0]


def hash_regular_bounded(
    path: Path, limit: int, location: str
) -> tuple[int, str, ObjectIdentity]:
    before = regular_metadata(path, location)
    if before.st_size > limit:
        fail(f"{location}: file exceeds {limit} byte limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"{location}: cannot open file safely: {error}")
    digest = hashlib.sha256()
    consumed = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or is_reparse(opened):
            fail(f"{location}: opened object is not a regular non-reparse file")
        if not _same_identity(before, opened):
            fail(f"{location}: file changed while it was opened")
        while True:
            chunk = os.read(descriptor, min(IO_CHUNK_BYTES, limit + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > limit:
                fail(f"{location}: file exceeds {limit} byte limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if not _same_identity(opened, after) or consumed != opened.st_size:
            fail(f"{location}: file changed while it was read")
    finally:
        os.close(descriptor)
    return consumed, digest.hexdigest(), object_identity(opened)


def read_resolved_file(
    resolved: ResolvedFile, limit: int, location: str
) -> bytes:
    content, identity = read_regular_snapshot(resolved.path, limit, location)
    if (
        len(content) != resolved.size
        or sha256(content) != resolved.sha256
        or identity != resolved.identity
    ):
        fail(f"{location}: file changed after inventory resolution")
    return content


@dataclass(frozen=True)
class TreeSnapshot:
    files: tuple[tuple[str, ResolvedFile], ...]
    directories: tuple[tuple[str, ObjectIdentity], ...]

    def file_map(self) -> dict[str, ResolvedFile]:
        return dict(self.files)


def scan_regular_files(root: Path, limits: Limits) -> TreeSnapshot:
    directory_metadata(root, "overlay root")
    case_names: dict[str, str] = {}
    result: dict[str, ResolvedFile] = {}
    seen_file_identities: dict[tuple[int, int], str] = {}
    visited_directories: list[tuple[str, Path, os.stat_result]] = []
    total_size = 0

    def visit(directory: Path, prefix: tuple[str, ...]) -> bool:
        nonlocal total_size
        before = directory_metadata(directory, "overlay inventory directory")
        visited_directories.append(("/".join(prefix), directory, before))
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            fail(f"overlay inventory: cannot scan directory: {error}")
        has_file = False
        for entry in entries:
            relative = "/".join((*prefix, entry.name))
            canonical_relative_path(relative, "overlay inventory path")
            folded = relative.casefold()
            collision = case_names.get(folded)
            if collision is not None and collision != relative:
                fail(
                    f"overlay inventory: case-colliding paths {collision!r} "
                    f"and {relative!r}"
                )
            case_names[folded] = relative
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                fail(f"overlay inventory {relative!r}: cannot inspect: {error}")
            if entry.is_symlink() or is_reparse(metadata):
                fail(f"overlay inventory {relative!r}: links are forbidden")
            if stat.S_ISDIR(metadata.st_mode):
                if not visit(path, (*prefix, entry.name)):
                    fail(f"overlay inventory {relative!r}: empty directories forbidden")
                has_file = True
                continue
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"overlay inventory {relative!r}: special files forbidden")
            has_file = True
            if relative == OVERLAY_NAME:
                continue
            if len(result) >= limits.max_file_count:
                fail("overlay inventory: file-count limit exceeded")
            size, digest, identity = hash_regular_bounded(
                path,
                limits.max_file_bytes,
                f"overlay inventory {relative!r}",
            )
            total_size += size
            if total_size > limits.max_total_file_bytes:
                fail("overlay inventory: total-byte limit exceeded")
            stable_key = (identity.device, identity.inode)
            if all(stable_key):
                previous = seen_file_identities.get(stable_key)
                if previous is not None:
                    fail(
                        f"overlay inventory: {previous!r} and {relative!r} "
                        "alias the same file identity"
                    )
                seen_file_identities[stable_key] = relative
            result[relative] = ResolvedFile(
                relative_path=relative,
                path=path,
                size=size,
                sha256=digest,
                identity=identity,
            )
        return has_file

    visit(root, ())
    for _, directory, before in visited_directories:
        after = directory_metadata(directory, "overlay inventory directory")
        if not _same_identity(before, after):
            fail("overlay inventory: directory changed during resolution")
    return TreeSnapshot(
        files=tuple((path, result[path]) for path in sorted(result)),
        directories=tuple(
            (relative, object_identity(metadata))
            for relative, _, metadata in sorted(visited_directories)
        ),
    )


def copy_file_exclusive(source: ResolvedFile, destination: Path) -> None:
    location = f"staging source {source.relative_path!r}"
    before = regular_metadata(source.path, location)
    if object_identity(before) != source.identity:
        fail(f"{location}: file changed after inventory resolution")
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source.path, source_flags)
    except OSError as error:
        raise ReleaseEvidenceError(f"{location}: cannot open safely: {error}") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    )
    try:
        destination_descriptor = os.open(destination, destination_flags, 0o600)
    except OSError as error:
        os.close(source_descriptor)
        raise ReleaseEvidenceError(
            f"staging destination {source.relative_path!r}: "
            f"cannot create exclusively: {error}"
        ) from error
    digest = hashlib.sha256()
    consumed = 0
    try:
        opened = os.fstat(source_descriptor)
        if not stat.S_ISREG(opened.st_mode) or is_reparse(opened):
            fail(f"{location}: opened object is not a regular non-reparse file")
        if not _same_identity(before, opened):
            fail(f"{location}: file changed while it was opened")
        while True:
            chunk = os.read(
                source_descriptor,
                min(IO_CHUNK_BYTES, source.size + 1 - consumed),
            )
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > source.size:
                fail(f"{location}: file grew after inventory resolution")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    fail(
                        f"staging destination {source.relative_path!r}: short write"
                    )
                view = view[written:]
        after = os.fstat(source_descriptor)
        if (
            not _same_identity(opened, after)
            or consumed != source.size
            or digest.hexdigest() != source.sha256
        ):
            fail(f"{location}: file changed after inventory resolution")
        os.fsync(destination_descriptor)
    finally:
        os.close(source_descriptor)
        os.close(destination_descriptor)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise ReleaseEvidenceError(
            f"cannot synchronize staging directory {path}: {error}"
        ) from error
    finally:
        os.close(descriptor)


def atomic_publish_no_replace(staged: Path, destination: Path) -> None:
    """Atomically rename a directory without replacing an existing target."""

    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel32.MoveFileExW
        move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move.restype = ctypes.c_int
        if not move(str(staged), str(destination), 0x00000008):
            error = ctypes.get_last_error()
            raise ReleaseEvidenceError(
                f"atomic no-replace publication failed with Windows error {error}"
            )
        return
    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(staged)
    encoded_destination = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(encoded_source, encoded_destination, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as error:
            raise ReleaseEvidenceError(
                "atomic no-replace publication is unavailable on this Linux libc"
            ) from error
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(-100, encoded_source, -100, encoded_destination, 1)
    else:
        raise ReleaseEvidenceError(
            "atomic no-replace publication is unavailable on this platform"
        )
    if result != 0:
        error = ctypes.get_errno()
        raise ReleaseEvidenceError(
            f"atomic no-replace publication failed: {os.strerror(error)}"
        )
