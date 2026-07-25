#!/usr/bin/env python3
"""Canonical identity for the retained format-neutral benchmark suite."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

SUITE_PATHS = (
    "benches/Cargo.lock",
    "benches/Cargo.toml",
    "benches/binary_writer_benchmark.rs",
    "benches/comparison-input.schema.json",
    "benches/format_primitives.rs",
    "benches/manifest.json",
    "benches/raw_page_stream_benchmark.rs",
    "benches/resource-metrics.schema.json",
    "benches/scripts/capture_metadata.sh",
    "benches/scripts/compare_baseline.py",
    "benches/scripts/normalize_criterion.py",
    "benches/scripts/suite_identity.py",
    "benches/scripts/validate_benchmark_manifest.py",
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
DOMAIN_PREFIX = b"jet3-rs-benchmark-suite-v1\0"


class SuiteIdentityError(ValueError):
    """The requested suite identity cannot be established."""


def _git(repository_root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise SuiteIdentityError(f"cannot execute git: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SuiteIdentityError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def resolve_commit(repository_root: Path, commit: str) -> str:
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise SuiteIdentityError("commit must be 40 lowercase hex digits")
    resolved = _git(repository_root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    resolved_commit = resolved.decode("ascii", errors="strict").strip()
    if resolved_commit != commit:
        raise SuiteIdentityError(f"commit did not resolve exactly: {commit}")
    return resolved_commit


def retained_blob(repository_root: Path, commit: str, path: str) -> bytes:
    if REPOSITORY_PATH_PATTERN.fullmatch(path) is None:
        raise SuiteIdentityError(f"invalid retained repository path: {path}")
    return _git(repository_root, "show", f"{commit}:{path}")


def _digest(blobs: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(DOMAIN_PREFIX)
    for path, blob in sorted(blobs):
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
    return digest.hexdigest()


def digest_for_commit(repository_root: Path, commit: str) -> str:
    resolved = resolve_commit(repository_root, commit)
    blobs = [
        (path, retained_blob(repository_root, resolved, path)) for path in SUITE_PATHS
    ]
    return _digest(blobs)


def digest_for_worktree(repository_root: Path) -> str:
    blobs: list[tuple[str, bytes]] = []
    for path in SUITE_PATHS:
        absolute_path = repository_root / path
        try:
            blob = absolute_path.read_bytes()
        except OSError as error:
            raise SuiteIdentityError(f"cannot read suite source {path}: {error}") from error
        blobs.append((path, blob))
    return _digest(blobs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--commit")
    selector.add_argument("--worktree", action="store_true")
    arguments = parser.parse_args()

    try:
        if arguments.worktree:
            digest = digest_for_worktree(arguments.repository_root)
        else:
            digest = digest_for_commit(arguments.repository_root, arguments.commit)
    except SuiteIdentityError as error:
        parser.exit(2, f"BLOCKED: {error}\n")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
