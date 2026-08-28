"""Shared boundaries for the fail-closed repository-contract validators."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .common import load_json, sha256_file

SAFE_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_ID = re.compile(
    r"^(?:DAO-(?:GEN|READ|WRITE|UPDATE)|UT|IT|PROP|GOLD|CORR|REG)-"
    r"[A-Z0-9][A-Z0-9_-]*$"
)
FUZZ_ID = re.compile(r"^FUZZ-[A-Z0-9][A-Z0-9_-]*$")
PROVENANCE_ID = re.compile(r"^(?:SRC|OBS|EXP|FIX)-[0-9]{4}$")
FIXTURE_ROOTS = (
    PurePosixPath("fixtures/generated"),
    PurePosixPath("fixtures/malformed"),
    PurePosixPath("fixtures/regression"),
)
CHECKED_PROTOCOL_TEST_RESOURCES = frozenset(
    {
        "oracle/windows-dao/protocol/v1_2/canonical-semantic-snapshot.schema.json",
        "oracle/windows-dao/protocol/v1_2/coverage-receipt.schema.json",
        "oracle/windows-dao/protocol/v1_2/fixtures/column-normalization-vectors.tsv",
        "oracle/windows-dao/protocol/v1_2/fixtures/long-value-comparison-vectors.tsv",
        "oracle/windows-dao/protocol/v1_2/fixtures/rejected-format-normalization-vectors.tsv",
        "oracle/windows-dao/protocol/v1_2/fixtures/row-key-vectors.tsv",
        "oracle/windows-dao/protocol/v1_2/fixtures/text-code-page-vectors.tsv",
        "oracle/windows-dao/protocol/v1_2/scenarios.json",
    }
)


class ContractError(ValueError):
    """The checked repository contract cannot be loaded or inspected."""


def sha256(path: Path) -> str:
    """Return the canonical streaming SHA-256 for a repository file."""
    return sha256_file(path)


def safe_path(value: Any, context: str) -> PurePosixPath:
    """Parse a constrained repository-relative path without touching the filesystem."""
    if not isinstance(value, str) or SAFE_PATH.fullmatch(value) is None:
        raise ContractError(f"{context}: unsafe repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{context}: unsafe repository-relative path")
    return path


def resolve_file(root: Path, value: Any, context: str) -> tuple[Path, str]:
    """Resolve a regular, non-symlink file confined beneath ``root``."""
    relative = safe_path(value, context)
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ContractError(f"{context}: missing file {relative.as_posix()}") from error
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ContractError(f"{context}: path escapes repository") from error
    if not resolved.is_file() or candidate.is_symlink():
        raise ContractError(f"{context}: must be a regular non-symlink file")
    return resolved, relative.as_posix()


def load_object(path: Path, description: str) -> dict[str, Any]:
    """Load a JSON object and normalize its filesystem/decoder failures."""
    try:
        document = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot load {description}: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{description}: expected object")
    return document


def exact_keys(
    document: dict[str, Any], expected: set[str], context: str, errors: list[str]
) -> None:
    """Append one deterministic error when an object's keys are not exact."""
    actual = set(document)
    if actual != expected:
        errors.append(
            f"{context}: invalid keys; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def nonempty(value: Any) -> bool:
    """Return whether a value is a non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def unique_strings(
    values: Any, context: str, errors: list[str], *, nonempty_array: bool = True
) -> list[str]:
    """Validate an array of unique non-empty strings and return valid entries."""
    if not isinstance(values, list) or (nonempty_array and not values):
        qualifier = "non-empty " if nonempty_array else ""
        errors.append(f"{context}: expected {qualifier}array")
        return []
    if any(not nonempty(value) for value in values):
        errors.append(f"{context}: entries must be non-empty strings")
        return []
    rendered = list(values)
    if len(rendered) != len(set(rendered)):
        errors.append(f"{context}: duplicate entries")
    return rendered
