#!/usr/bin/env python3
"""Generic bounded I/O primitives for serialized A4 dry-run inputs."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class BoundedIoError(ValueError):
    """A serialized input or child process exceeded a caller-supplied bound."""


@dataclass(frozen=True)
class TreeFile:
    relative: str
    path: Path
    size: int


@dataclass(frozen=True)
class ChildResult:
    returncode: int
    output: bytes


def read_regular(path: Path, maximum: int, *, exact_size: int | None = None) -> bytes:
    """Read at most ``maximum`` bytes from one non-symlink regular file."""
    if type(maximum) is not int or maximum < 0:
        raise BoundedIoError("invalid file bound")
    if exact_size is not None and (
        type(exact_size) is not int or not 0 <= exact_size <= maximum
    ):
        raise BoundedIoError("invalid exact file size")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BoundedIoError(f"missing bounded file: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BoundedIoError(f"bounded path is not a regular file: {path}")
    if metadata.st_size > maximum or (
        exact_size is not None and metadata.st_size != exact_size
    ):
        raise BoundedIoError(f"bounded file size differs: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BoundedIoError(f"cannot read bounded file: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum or (
            exact_size is not None and opened.st_size != exact_size
        ):
            raise BoundedIoError(f"bounded opened file size differs: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise BoundedIoError(f"cannot read bounded file: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum or (
        exact_size is not None and len(payload) != exact_size
    ):
        raise BoundedIoError(f"bounded file changed size while reading: {path}")
    return payload


def inventory_tree(
    root: Path,
    *,
    maximum_entries: int,
    maximum_bytes: int,
    maximum_file_bytes: int,
    page_size: int,
) -> tuple[TreeFile, ...]:
    """Boundedly enumerate regular files without following any symlink."""
    for value, label in (
        (maximum_entries, "entry"),
        (maximum_bytes, "tree-byte"),
        (maximum_file_bytes, "file-byte"),
        (page_size, "page-size"),
    ):
        if type(value) is not int or value <= 0:
            raise BoundedIoError(f"invalid {label} bound")
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise BoundedIoError(f"missing bounded tree: {root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise BoundedIoError(f"bounded tree root is not a directory: {root}")
    pending = [root]
    entry_count = 0
    total_bytes = 0
    files: list[TreeFile] = []
    while pending:
        directory = pending.pop()
        try:
            directory_metadata = directory.lstat()
            if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
                directory_metadata.st_mode
            ):
                raise BoundedIoError(
                    f"bounded tree directory is not a directory: {directory}"
                )
            scanner = os.scandir(directory)
        except OSError as exc:
            raise BoundedIoError(f"cannot enumerate bounded tree: {directory}") from exc
        with scanner:
            for entry in scanner:
                entry_count += 1
                if entry_count > maximum_entries:
                    raise BoundedIoError("bounded tree entry count exceeded")
                if entry.is_symlink():
                    raise BoundedIoError(
                        f"bounded tree contains a symlink: {entry.path}"
                    )
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise BoundedIoError(
                        f"bounded tree contains a special file: {path}"
                    )
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise BoundedIoError(
                        f"cannot stat bounded tree file: {path}"
                    ) from exc
                maximum = (
                    page_size if path.suffix == ".page" else maximum_file_bytes
                )
                if size < 0 or size > maximum or (
                    path.suffix == ".page" and size != page_size
                ):
                    raise BoundedIoError(f"bounded tree file size differs: {path}")
                total_bytes += size
                if total_bytes > maximum_bytes:
                    raise BoundedIoError("bounded tree byte count exceeded")
                files.append(
                    TreeFile(path.relative_to(root).as_posix(), path, size)
                )
    return tuple(sorted(files, key=lambda item: item.relative))


def copy_bounded_tree(
    source: Path,
    destination: Path,
    *,
    maximum_entries: int,
    maximum_bytes: int,
    maximum_file_bytes: int,
    page_size: int,
) -> None:
    """Copy only a preflighted bounded regular-file tree."""
    files = inventory_tree(
        source,
        maximum_entries=maximum_entries,
        maximum_bytes=maximum_bytes,
        maximum_file_bytes=maximum_file_bytes,
        page_size=page_size,
    )
    if destination.exists():
        raise BoundedIoError(f"bounded copy destination exists: {destination}")
    destination.mkdir(parents=True)
    copied = 0
    for item in files:
        maximum = page_size if item.path.suffix == ".page" else maximum_file_bytes
        payload = read_regular(
            item.path,
            maximum,
            exact_size=page_size if item.path.suffix == ".page" else None,
        )
        copied += len(payload)
        if copied > maximum_bytes:
            raise BoundedIoError("bounded copy byte count exceeded")
        target = destination / item.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def run_bounded_child(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    output_limit: int,
) -> ChildResult:
    """Run an argv-only child with one combined, streaming output ceiling."""
    if not command or not all(isinstance(item, str) and item for item in command):
        raise BoundedIoError("invalid child argument vector")
    if not 0 < timeout_seconds <= 300:
        raise BoundedIoError("invalid child timeout")
    if type(output_limit) is not int or not 0 <= output_limit <= 8 * 1024 * 1024:
        raise BoundedIoError("invalid child output bound")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise BoundedIoError("cannot start bounded child") from exc
    assert process.stdout is not None
    chunks: list[bytes] = []
    overflow = threading.Event()

    def consume() -> None:
        consumed = 0
        while True:
            try:
                chunk = process.stdout.read(min(64 * 1024, output_limit + 1 - consumed))
            except (OSError, ValueError):
                return
            if not chunk:
                return
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > output_limit:
                overflow.set()
                return

    reader = threading.Thread(target=consume, daemon=True, name="a4-dryrun-child-output")
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    while process.poll() is None:
        if overflow.is_set():
            failure = "bounded child output exceeded"
            break
        if time.monotonic() >= deadline:
            failure = "bounded child timeout exceeded"
            break
        time.sleep(0.02)
    if failure is not None:
        _terminate(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _terminate(process)
        process.wait()
    reader.join(timeout=2)
    if reader.is_alive():
        _terminate(process)
        raise BoundedIoError("bounded child output reader did not terminate")
    process.stdout.close()
    output = b"".join(chunks)
    if overflow.is_set() or len(output) > output_limit:
        raise BoundedIoError("bounded child output exceeded")
    if failure is not None:
        raise BoundedIoError(failure)
    return ChildResult(process.returncode, output)
