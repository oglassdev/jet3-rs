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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Mapping, Sequence, TextIO, TypeVar

ENVIRONMENT_VARIABLE = "JET3_EXTERNAL_FIXTURE_ROOT"
PURPOSE = "nonredistributable-read-only-corpus-verification"
MANIFEST_RELATIVE_PATH = PurePosixPath("docs/validation/external-corpus.json")
SIGNATURE_OFFSET = 4
SIGNATURE_LENGTH = 15
STRIDES = (1024, 2048)
HASH_CHUNK_BYTES = 1024 * 1024
PAGE_BOUNDARY_BYTES = 2048


@dataclass(frozen=True)
class _StrideSurvey:
    """A checked-in observation protocol tied to its provenance record."""

    provenance_id: str
    stride_bytes: tuple[int, ...]


@dataclass(frozen=True)
class _BoundaryPrefixSurvey:
    """A checked-in observation protocol tied to its provenance record."""

    provenance_id: str
    stride_bytes: int
    prefix_bytes: int


# Keep experiment mechanics explicit and independent. Adding an observation must
# name its provenance record instead of piggybacking on a coincidentally equal
# stride used by another experiment.
STRIDE_SURVEY = _StrideSurvey("EXP-0001", STRIDES)
PAGE_BOUNDARY_SURVEY = _BoundaryPrefixSurvey(
    "EXP-0002", PAGE_BOUNDARY_BYTES, 2
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "environment_variable",
    "purpose",
    "fixtures",
    "comparisons",
}
FIXTURE_KEYS = {"id", "path", "size_bytes", "sha256"}
COMPARISON_KEYS = {
    "id",
    "left_fixture_id",
    "right_fixture_id",
    "page_size_bytes",
}
FIXTURE_ID = re.compile(r"^FIX-[0-9]{4}$")
COMPARISON_ID = re.compile(r"^CMP-[0-9]{4}$")
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


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    size_bytes: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _VerifiedFixture:
    fixture_id: str
    relative_path: PurePosixPath
    resolved_path: Path
    identity: _FileIdentity
    sha256: str


_Observation = TypeVar("_Observation")


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
        or document["schema_version"] != 2
    ):
        raise ContractError("external corpus manifest schema_version must be integer 2")
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

    fixture_sizes = {
        fixture["id"]: fixture["size_bytes"] for fixture in fixtures
    }
    comparisons = document.get("comparisons")
    if not isinstance(comparisons, list):
        raise ContractError("external corpus manifest comparisons must be an array")
    comparison_ids: list[str] = []
    fixture_pairs: set[tuple[str, str]] = set()
    for index, comparison in enumerate(comparisons):
        location = f"external corpus manifest comparisons[{index}]"
        if not isinstance(comparison, dict) or set(comparison) != COMPARISON_KEYS:
            raise ContractError(f"{location} has an invalid shape")
        comparison_id = comparison.get("id")
        if (
            not isinstance(comparison_id, str)
            or not COMPARISON_ID.fullmatch(comparison_id)
        ):
            raise ContractError(f"{location}.id is invalid")
        if comparison_id in comparison_ids:
            raise ContractError(f"{location}.id is duplicated")
        comparison_ids.append(comparison_id)
        left_id = comparison.get("left_fixture_id")
        right_id = comparison.get("right_fixture_id")
        if left_id not in fixture_sizes or right_id not in fixture_sizes:
            raise ContractError(f"{location} references an unknown fixture")
        if left_id == right_id:
            raise ContractError(f"{location} must reference two distinct fixtures")
        pair = (left_id, right_id)
        if pair in fixture_pairs:
            raise ContractError(f"{location} duplicates a fixture pair")
        fixture_pairs.add(pair)
        page_size = comparison.get("page_size_bytes")
        if type(page_size) is not int or page_size != PAGE_BOUNDARY_BYTES:
            raise ContractError(
                f"{location}.page_size_bytes must be integer "
                f"{PAGE_BOUNDARY_BYTES}"
            )
        left_size = fixture_sizes[left_id]
        right_size = fixture_sizes[right_id]
        if left_size != right_size:
            raise ContractError(f"{location} fixtures must have equal sizes")
        if left_size % PAGE_BOUNDARY_BYTES != 0:
            raise ContractError(
                f"{location} fixture size must be a multiple of "
                f"{PAGE_BOUNDARY_BYTES}"
            )
    if comparison_ids != sorted(comparison_ids):
        raise ContractError("external corpus manifest comparisons must be sorted by ID")


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


def _file_identity(file_stat: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        mode=file_stat.st_mode,
        size_bytes=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        changed_ns=file_stat.st_ctime_ns,
    )


def _verify_fixture_identity(
    corpus_root: Path, fixture: dict[str, Any]
) -> _VerifiedFixture:
    """Verify the manifest-bound immutable identity before observations run."""
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
            after = os.fstat(source.fileno())
    except CorpusBlockedError:
        raise
    except OSError as error:
        raise CorpusBlockedError(
            f"{fixture_id} cannot be read: {relative.as_posix()}"
        ) from error

    identity = _file_identity(before)
    if identity != _file_identity(after):
        raise CorpusBlockedError(f"{fixture_id} changed during identity verification")
    return _VerifiedFixture(
        fixture_id=fixture_id,
        relative_path=relative,
        resolved_path=resolved,
        identity=identity,
        sha256=digest,
    )


def _run_fixture_observation(
    fixture: _VerifiedFixture,
    observation_name: str,
    observe: Callable[[BinaryIO], _Observation],
) -> _Observation:
    """Run one typed pass only while the verified identity remains unchanged."""
    try:
        with fixture.resolved_path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CorpusBlockedError(
                    f"{fixture.fixture_id} is not a regular file: "
                    f"{fixture.relative_path.as_posix()}"
                )
            if _file_identity(before) != fixture.identity:
                raise CorpusBlockedError(
                    f"{fixture.fixture_id} changed before {observation_name}"
                )
            result = observe(source)
            after = os.fstat(source.fileno())
    except CorpusBlockedError:
        raise
    except OSError as error:
        raise CorpusBlockedError(
            f"{fixture.fixture_id} cannot be read during {observation_name}"
        ) from error
    if _file_identity(after) != fixture.identity:
        raise CorpusBlockedError(
            f"{fixture.fixture_id} changed during {observation_name}"
        )
    return result


def _observe_signature(source: BinaryIO, fixture_id: str) -> dict[str, str]:
    source.seek(SIGNATURE_OFFSET)
    signature = source.read(SIGNATURE_LENGTH)
    if len(signature) != SIGNATURE_LENGTH:
        raise CorpusBlockedError(
            f"{fixture_id} is too short for the offset-4 signature"
        )
    return {
        "ascii": _ascii_signature(signature),
        "hex": signature.hex(),
    }


def _observe_stride_first_bytes(
    source: BinaryIO, fixture_id: str, size_bytes: int, stride_bytes: int
) -> dict[str, Any]:
    unique_values: set[int] = set()
    sample_count = 0
    for offset in range(0, size_bytes, stride_bytes):
        source.seek(offset)
        sample = source.read(1)
        if len(sample) != 1:
            raise CorpusBlockedError(
                f"{fixture_id} changed during stride inspection"
            )
        unique_values.add(sample[0])
        sample_count += 1
    return {
        "sample_count": sample_count,
        "stride_bytes": stride_bytes,
        "unique_count": len(unique_values),
        "unique_first_bytes": sorted(unique_values),
    }


def _observe_boundary_prefixes(
    source: BinaryIO,
    fixture_id: str,
    size_bytes: int,
    survey: _BoundaryPrefixSurvey,
) -> dict[str, Any]:
    first_byte_counts: Counter[int] = Counter()
    nonzero_first_byte_count = 0
    nonzero_first_byte_with_second_byte_0x01_count = 0
    sample_count = 0
    for offset in range(0, size_bytes, survey.stride_bytes):
        source.seek(offset)
        expected_width = min(survey.prefix_bytes, size_bytes - offset)
        sample = source.read(survey.prefix_bytes)
        if len(sample) != expected_width:
            raise CorpusBlockedError(
                f"{fixture_id} changed during page-boundary inspection"
            )
        first_byte = sample[0]
        first_byte_counts[first_byte] += 1
        nonzero_first_byte_count += first_byte != 0
        nonzero_first_byte_with_second_byte_0x01_count += (
            first_byte != 0 and len(sample) == 2 and sample[1] == 0x01
        )
        sample_count += 1
    return {
        "first_byte_counts": [
            {"count": count, "first_byte": first_byte}
            for first_byte, count in sorted(first_byte_counts.items())
        ],
        "nonzero_first_byte_count": nonzero_first_byte_count,
        "nonzero_first_byte_with_second_byte_0x01_count": (
            nonzero_first_byte_with_second_byte_0x01_count
        ),
        "sample_count": sample_count,
        "stride_bytes": survey.stride_bytes,
    }


def _observe_verified_fixture(fixture: _VerifiedFixture) -> dict[str, Any]:
    signature = _run_fixture_observation(
        fixture,
        "offset-4 signature observation",
        lambda source: _observe_signature(source, fixture.fixture_id),
    )
    stride_observations = [
        _run_fixture_observation(
            fixture,
            f"{STRIDE_SURVEY.provenance_id} stride {stride_bytes}",
            lambda source, stride=stride_bytes: _observe_stride_first_bytes(
                source,
                fixture.fixture_id,
                fixture.identity.size_bytes,
                stride,
            ),
        )
        for stride_bytes in STRIDE_SURVEY.stride_bytes
    ]
    page_boundary_observation = _run_fixture_observation(
        fixture,
        PAGE_BOUNDARY_SURVEY.provenance_id,
        lambda source: _observe_boundary_prefixes(
            source,
            fixture.fixture_id,
            fixture.identity.size_bytes,
            PAGE_BOUNDARY_SURVEY,
        ),
    )
    return {
        "id": fixture.fixture_id,
        "offset_4_signature": signature,
        "page_boundary_observation": page_boundary_observation,
        "path": fixture.relative_path.as_posix(),
        "sha256": fixture.sha256,
        "size_bytes": fixture.identity.size_bytes,
        "stride_observations": stride_observations,
    }


def _observe_fixture(corpus_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for one manifest fixture's complete observation."""
    return _observe_verified_fixture(_verify_fixture_identity(corpus_root, fixture))


def _observe_comparison(
    comparison: dict[str, Any],
    fixtures_by_id: dict[str, _VerifiedFixture],
) -> dict[str, Any]:
    comparison_id = comparison["id"]
    left_fixture = fixtures_by_id[comparison["left_fixture_id"]]
    right_fixture = fixtures_by_id[comparison["right_fixture_id"]]
    page_size = comparison["page_size_bytes"]
    try:
        with (
            left_fixture.resolved_path.open("rb") as left,
            right_fixture.resolved_path.open("rb") as right,
        ):
            left_before = os.fstat(left.fileno())
            right_before = os.fstat(right.fileno())
            for fixture, file_stat in (
                (left_fixture, left_before),
                (right_fixture, right_before),
            ):
                if not stat.S_ISREG(file_stat.st_mode):
                    raise CorpusBlockedError(
                        f"{fixture.fixture_id} is not a regular file: "
                        f"{fixture.relative_path.as_posix()}"
                    )
                if _file_identity(file_stat) != fixture.identity:
                    raise CorpusBlockedError(
                        f"{fixture.fixture_id} changed before {comparison_id}"
                    )

            transition_counts: Counter[tuple[int, int]] = Counter()
            changed_byte_count_by_offset = [0] * page_size
            equal_page_count = 0
            page_count = left_fixture.identity.size_bytes // page_size
            for page_index in range(page_count):
                left_page = left.read(page_size)
                right_page = right.read(page_size)
                if len(left_page) != page_size or len(right_page) != page_size:
                    raise CorpusBlockedError(
                        f"{comparison_id} changed during page comparison "
                        f"at page index {page_index}"
                    )
                transition_counts[(left_page[0], right_page[0])] += 1
                if left_page == right_page:
                    equal_page_count += 1
                else:
                    for byte_offset, (left_byte, right_byte) in enumerate(
                        zip(left_page, right_page, strict=True)
                    ):
                        if left_byte != right_byte:
                            changed_byte_count_by_offset[byte_offset] += 1

            left_after = os.fstat(left.fileno())
            right_after = os.fstat(right.fileno())
    except CorpusBlockedError:
        raise
    except OSError as error:
        raise CorpusBlockedError(
            f"{comparison_id} fixtures cannot be read"
        ) from error

    if left_fixture.identity != _file_identity(left_after):
        raise CorpusBlockedError(
            f"{left_fixture.fixture_id} changed during {comparison_id}"
        )
    if right_fixture.identity != _file_identity(right_after):
        raise CorpusBlockedError(
            f"{right_fixture.fixture_id} changed during {comparison_id}"
        )

    return {
        "changed_byte_count_by_offset": changed_byte_count_by_offset,
        "changed_page_count": page_count - equal_page_count,
        "equal_page_count": equal_page_count,
        "id": comparison_id,
        "left_fixture_id": left_fixture.fixture_id,
        "page_count": page_count,
        "page_size_bytes": page_size,
        "right_fixture_id": right_fixture.fixture_id,
        "same_index_first_byte_transitions": [
            {
                "left_first_byte": left_byte,
                "page_count": count,
                "right_first_byte": right_byte,
            }
            for (left_byte, right_byte), count in sorted(transition_counts.items())
        ],
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

    verified_fixtures = [
        _verify_fixture_identity(normalized_external_root, fixture)
        for fixture in manifest["fixtures"]
    ]
    fixtures_by_id = {
        fixture.fixture_id: fixture for fixture in verified_fixtures
    }
    fixtures = [
        _observe_verified_fixture(fixture) for fixture in verified_fixtures
    ]
    comparisons = [
        _observe_comparison(comparison, fixtures_by_id)
        for comparison in manifest["comparisons"]
    ]
    return {
        "comparisons": comparisons,
        "dirty": dirty,
        "fixtures": fixtures,
        "git_commit": commit,
        "manifest_sha256": manifest_sha256,
        "schema_version": 2,
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
