#!/usr/bin/env python3
"""Bounded, link-safe filesystem reads for A3 bundle validation."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from protocol_validation import ValidationError

MAX_TREE_ENTRIES = 65_750
MAX_TREE_DEPTH = 8
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class TreeFile:
    size: int
    modified_ns: int
    device: int
    inode: int
    links: int


def is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def safe_locator(value: Any, label: str) -> str:
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


def inventory(root: Path) -> tuple[dict[str, TreeFile], set[str]]:
    root = root.resolve()
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ValidationError(f"{root}: cannot inspect tree root: {exc}") from exc
    if root.is_symlink() or is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
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
                    if child.is_symlink() or is_reparse(child_metadata):
                        raise ValidationError(f"{child.path}: links are forbidden")
                    locator = Path(child.path).relative_to(root).as_posix()
                    safe_locator(locator, child.path)
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
                            child_metadata.st_size, child_metadata.st_mtime_ns,
                            *file_identity,
                        )
                    else:
                        raise ValidationError(f"{locator}: non-regular tree entry")
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"{root}: cannot enumerate tree: {exc}") from exc
    return files, directories


def expected_directories(paths: Iterable[str]) -> set[str]:
    return {
        parent.as_posix()
        for locator in paths
        for parent in Path(locator).parents
        if parent != Path(".")
    }


def read_checked(root: Path, locator: str, tree: dict[str, TreeFile], maximum: int) -> bytes:
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
            path.is_symlink() or is_reparse(before) or not stat.S_ISREG(before.st_mode)
            or before.st_size != expected.size or before.st_mtime_ns != expected.modified_ns
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
        or path.is_symlink() or is_reparse(after) or not stat.S_ISREG(after.st_mode)
        or after.st_size != expected.size or after.st_mtime_ns != expected.modified_ns
        or _identity(after) != expected_identity
    ):
        raise ValidationError(f"{locator}: artifact changed during read")
    return payload


def parse_json(payload: bytes, locator: str) -> dict[str, Any]:
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
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{locator}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{locator}: JSON root must be an object")
    return document
