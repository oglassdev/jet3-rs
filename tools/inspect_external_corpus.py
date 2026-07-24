#!/usr/bin/env python3
"""Verify and observe the opt-in donated Jet candidate corpus, read-only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence, TextIO

ENVIRONMENT_VARIABLE = "JET3_EXTERNAL_FIXTURE_ROOT"
PURPOSE = "nonredistributable-read-only-corpus-verification"
MANIFEST_RELATIVE_PATH = PurePosixPath("docs/validation/external-corpus.json")
SIGNATURE_OFFSET = 4
SIGNATURE_LENGTH = 15
STRIDES = (1024, 2048)
HASH_CHUNK_BYTES = 1024 * 1024

TOP_LEVEL_KEYS = {
    "schema_version",
    "environment_variable",
    "purpose",
    "fixtures",
}
FIXTURE_KEYS = {"id", "path", "size_bytes", "sha256"}
FIXTURE_ID = re.compile(r"^FIX-[0-9]{4}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
RELATIVE_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)


class ContractError(ValueError):
    """The checked-in repository contract is invalid."""


class CorpusBlockedError(RuntimeError):
    """The opt-in external corpus is absent, unsafe, or does not match."""


def _canonical_json(document: Any) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _sha256_stream(source: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(HASH_CHUNK_BYTES), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(raw_path: Any, field: str) -> PurePosixPath:
    if (
        not isinstance(raw_path, str)
        or "\\" in raw_path
        or not RELATIVE_PATH.fullmatch(raw_path)
    ):
        raise ContractError(f"{field} must be a safe relative path")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(
        component in {"", ".", ".."} for component in relative.parts
    ):
        raise ContractError(f"{field} must be a safe relative path")
    return relative


def _load_manifest(repo_root: Path) -> tuple[dict[str, Any], str]:
    relative = _safe_relative_path(
        MANIFEST_RELATIVE_PATH.as_posix(), "external corpus manifest path"
    )
    candidate = repo_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (FileNotFoundError, OSError) as error:
        raise ContractError("external corpus manifest is missing") from error
    except ValueError as error:
        raise ContractError(
            "external corpus manifest escapes the repository"
        ) from error
    try:
        mode = resolved.stat().st_mode
        raw = resolved.read_bytes()
    except OSError as error:
        raise ContractError("external corpus manifest cannot be read") from error
    if not stat.S_ISREG(mode):
        raise ContractError("external corpus manifest must be a regular file")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("external corpus manifest is invalid JSON") from error
    _validate_manifest(document)
    return document, hashlib.sha256(raw).hexdigest()


def _validate_manifest(document: Any) -> None:
    if not isinstance(document, dict) or set(document) != TOP_LEVEL_KEYS:
        raise ContractError("external corpus manifest has an invalid top-level shape")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        raise ContractError("external corpus manifest schema_version must be integer 1")
    if document.get("environment_variable") != ENVIRONMENT_VARIABLE:
        raise ContractError(
            f"external corpus manifest must name {ENVIRONMENT_VARIABLE}"
        )
    if document.get("purpose") != PURPOSE:
        raise ContractError("external corpus manifest has an invalid purpose")

    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ContractError(
            "external corpus manifest fixtures must be a non-empty array"
        )

    identities: list[str] = []
    paths: set[str] = set()
    for index, fixture in enumerate(fixtures):
        location = f"external corpus manifest fixtures[{index}]"
        if not isinstance(fixture, dict) or set(fixture) != FIXTURE_KEYS:
            raise ContractError(f"{location} has an invalid shape")
        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not FIXTURE_ID.fullmatch(fixture_id):
            raise ContractError(f"{location}.id is invalid")
        if fixture_id in identities:
            raise ContractError(f"{location}.id is duplicated")
        identities.append(fixture_id)
        relative = _safe_relative_path(fixture.get("path"), f"{location}.path")
        normalized_path = relative.as_posix()
        if normalized_path in paths:
            raise ContractError(f"{location}.path is duplicated")
        paths.add(normalized_path)
        size_bytes = fixture.get("size_bytes")
        if (
            type(size_bytes) is not int
            or size_bytes < SIGNATURE_OFFSET + SIGNATURE_LENGTH
        ):
            raise ContractError(
                f"{location}.size_bytes must be an integer of at least "
                f"{SIGNATURE_OFFSET + SIGNATURE_LENGTH}"
            )
        digest = fixture.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ContractError(f"{location}.sha256 is invalid")
    if identities != sorted(identities):
        raise ContractError("external corpus manifest fixtures must be sorted by ID")


def _repository_state(repo_root: Path) -> tuple[str, bool]:
    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if top_level.returncode != 0:
        raise ContractError("cannot identify the Git repository")
    try:
        reported_root = Path(top_level.stdout.strip()).resolve(strict=True)
    except OSError as error:
        raise ContractError("Git returned an invalid repository root") from error
    if reported_root != repo_root:
        raise ContractError("the verifier must run from its repository root")

    commit_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = commit_result.stdout.strip()
    if commit_result.returncode != 0 or not GIT_COMMIT.fullmatch(commit):
        raise ContractError("cannot determine the exact Git commit")
    dirty_result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude)artifacts/acceptance/**",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if dirty_result.returncode != 0:
        raise ContractError("cannot determine the Git worktree state")
    return commit, bool(dirty_result.stdout)


def _resolve_fixture(
    corpus_root: Path, relative: PurePosixPath, fixture_id: str
) -> Path:
    candidate = corpus_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CorpusBlockedError(
            f"{fixture_id} is missing: {relative.as_posix()}"
        ) from error
    try:
        resolved.relative_to(corpus_root)
    except ValueError as error:
        raise CorpusBlockedError(
            f"{fixture_id} escapes the external corpus root: {relative.as_posix()}"
        ) from error
    try:
        mode = resolved.stat().st_mode
    except OSError as error:
        raise CorpusBlockedError(
            f"{fixture_id} cannot be inspected: {relative.as_posix()}"
        ) from error
    if not stat.S_ISREG(mode):
        raise CorpusBlockedError(
            f"{fixture_id} is not a regular file: {relative.as_posix()}"
        )
    return resolved


def _ascii_signature(signature: bytes) -> str:
    return "".join(chr(value) if 0x20 <= value <= 0x7E else "." for value in signature)


def _observe_fixture(corpus_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    fixture_id = fixture["id"]
    relative = _safe_relative_path(
        fixture["path"], f"external corpus fixture {fixture_id} path"
    )
    resolved = _resolve_fixture(corpus_root, relative, fixture_id)
    try:
        with resolved.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CorpusBlockedError(
                    f"{fixture_id} is not a regular file: {relative.as_posix()}"
                )
            if before.st_size != fixture["size_bytes"]:
                raise CorpusBlockedError(
                    f"{fixture_id} size mismatch: expected {fixture['size_bytes']}, "
                    f"found {before.st_size}"
                )

            digest = _sha256_stream(source)
            if digest != fixture["sha256"]:
                raise CorpusBlockedError(f"{fixture_id} SHA-256 mismatch")

            source.seek(SIGNATURE_OFFSET)
            signature = source.read(SIGNATURE_LENGTH)
            if len(signature) != SIGNATURE_LENGTH:
                raise CorpusBlockedError(
                    f"{fixture_id} is too short for the offset-4 signature"
                )

            stride_observations = []
            for stride in STRIDES:
                unique_values: set[int] = set()
                sample_count = 0
                for offset in range(0, before.st_size, stride):
                    source.seek(offset)
                    sample = source.read(1)
                    if len(sample) != 1:
                        raise CorpusBlockedError(
                            f"{fixture_id} changed during stride inspection"
                        )
                    unique_values.add(sample[0])
                    sample_count += 1
                stride_observations.append(
                    {
                        "sample_count": sample_count,
                        "stride_bytes": stride,
                        "unique_count": len(unique_values),
                        "unique_first_bytes": sorted(unique_values),
                    }
                )

            after = os.fstat(source.fileno())
    except CorpusBlockedError:
        raise
    except OSError as error:
        raise CorpusBlockedError(
            f"{fixture_id} cannot be read: {relative.as_posix()}"
        ) from error

    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise CorpusBlockedError(f"{fixture_id} changed during inspection")

    return {
        "id": fixture_id,
        "offset_4_signature": {
            "ascii": _ascii_signature(signature),
            "hex": signature.hex(),
        },
        "path": relative.as_posix(),
        "sha256": digest,
        "size_bytes": before.st_size,
        "stride_observations": stride_observations,
    }


def build_observation(repo_root: Path, external_root: Path) -> dict[str, Any]:
    """Validate the manifest and corpus, returning a deterministic observation."""
    try:
        normalized_repo_root = repo_root.resolve(strict=True)
    except OSError as error:
        raise ContractError("repository root is unavailable") from error
    if not normalized_repo_root.is_dir():
        raise ContractError("repository root is not a directory")

    manifest, manifest_sha256 = _load_manifest(normalized_repo_root)
    commit, dirty = _repository_state(normalized_repo_root)
    try:
        normalized_external_root = external_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CorpusBlockedError("external corpus root is unavailable") from error
    if not normalized_external_root.is_dir():
        raise CorpusBlockedError("external corpus root is not a directory")

    fixtures = [
        _observe_fixture(normalized_external_root, fixture)
        for fixture in manifest["fixtures"]
    ]
    return {
        "dirty": dirty,
        "fixtures": fixtures,
        "git_commit": commit,
        "manifest_sha256": manifest_sha256,
        "schema_version": 1,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    environment = os.environ if environ is None else environ
    if arguments:
        print("ERROR: this verifier accepts no arguments", file=errors)
        return 1
    raw_root = environment.get(ENVIRONMENT_VARIABLE)
    if not raw_root:
        print(f"BLOCKED: {ENVIRONMENT_VARIABLE} is not set", file=errors)
        return 2
    try:
        repo_root = Path(__file__).resolve().parents[1]
        observation = build_observation(repo_root, Path(raw_root))
    except CorpusBlockedError as error:
        print(f"BLOCKED: {error}", file=errors)
        return 2
    except ContractError as error:
        print(f"ERROR: {error}", file=errors)
        return 1
    print(_canonical_json(observation), end="", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
