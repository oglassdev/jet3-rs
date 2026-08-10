"""Shared constants, Git inspection, hashing, and safe path handling."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

IMPLEMENTATION_STATES = (
    "not_started",
    "partial",
    "implemented",
    "out_of_scope_v1",
)
VERIFICATION_STATES = (
    "unverified",
    "internal_only",
    "independent_check",
    "dao_opened",
    "dao_differential",
    "not_applicable",
)
REQUIRED_VERIFICATION_STATES = (
    "internal_only",
    "independent_check",
    "dao_opened",
    "dao_differential",
    "not_applicable",
)
VERIFICATION_RANK = {
    "unverified": 0,
    "internal_only": 1,
    "independent_check": 2,
    "dao_opened": 3,
    "dao_differential": 4,
}
CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_ID = re.compile(
    r"^(?:DAO-(?:GEN|READ|WRITE|UPDATE)|UT|IT|PROP|GOLD|CORR|REG)-"
    r"[A-Z0-9][A-Z0-9_-]*$"
)
TRACEABILITY_ID = re.compile(r"^[A-Z]+-[0-9]{2}$")
ACCEPTANCE_GATES = tuple(f"G{number}" for number in range(9))
TRACEABILITY_REGISTRY_KEYS = {"schema_version", "requirements"}
TRACEABILITY_REQUIREMENT_KEYS = {
    "id",
    "requirement",
    "acceptance_gates",
    "required_evidence",
}
REPOSITORY_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)


def typename(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def check_keys(
    value: dict[str, Any],
    expected: set[str],
    required: set[str],
    location: str,
) -> list[str]:
    errors = [
        f"{location}: missing required property {key!r}"
        for key in sorted(required - value.keys())
    ]
    for key in sorted(value.keys() - expected):
        if key in {"label", "user_facing_label"}:
            errors.append(
                f"{location}.{key}: user-facing labels are derived, not stored; "
                "a 'supported' claim requires commit-bound evidence"
            )
        else:
            errors.append(f"{location}: unknown property {key!r}")
    return errors


def validate_repository_path(
    raw_path: Any,
    repo_root: Path,
    location: str,
) -> list[str]:
    if not isinstance(raw_path, str) or not raw_path:
        return [f"{location}: expected a non-empty repository-relative path"]
    if "\\" in raw_path:
        return [f"{location}: use repository-relative paths with forward slashes"]
    if not REPOSITORY_PATH.fullmatch(raw_path):
        return [f"{location}: unsafe evidence path {raw_path!r}"]

    posix_path = PurePosixPath(raw_path)
    if posix_path.is_absolute() or any(
        part in {"", ".", ".."} for part in posix_path.parts
    ):
        return [f"{location}: unsafe evidence path {raw_path!r}"]
    candidate = repo_root.joinpath(*posix_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root.resolve(strict=True))
    except (FileNotFoundError, OSError):
        return [f"{location}: evidence path does not exist: {raw_path!r}"]
    except ValueError:
        return [f"{location}: evidence path escapes the repository: {raw_path!r}"]
    return []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(
    repo_root: Path, arguments: list[str], *, text: bool = True
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=text,
    )


def git_head(repo_root: Path) -> str | None:
    result = git(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit if GIT_COMMIT.fullmatch(commit) else None


def git_dirty(repo_root: Path) -> bool | None:
    result = git(
        repo_root,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude)artifacts/acceptance/**",
        ],
    )
    return None if result.returncode != 0 else bool(result.stdout)


def git_blob(repo_root: Path, commit: str, path: str) -> bytes | None:
    result = git(repo_root, ["show", f"{commit}:{path}"], text=False)
    return None if result.returncode != 0 else bytes(result.stdout)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def validate_traceability_registry(document: Any) -> tuple[set[str], list[str]]:
    """Validate the authoritative requirement-ID registry."""
    if not isinstance(document, dict):
        return set(), ["traceability registry: expected object"]
    errors = []
    if set(document) != TRACEABILITY_REGISTRY_KEYS:
        errors.append("traceability registry: invalid top-level keys")
    if document.get("schema_version") != 1:
        errors.append("traceability registry schema_version: expected integer 1")
    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        return set(), errors + ["traceability registry requirements: expected array"]
    ids: set[str] = set()
    ordered_ids = []
    for index, item in enumerate(requirements):
        location = f"traceability registry requirements[{index}]"
        if not isinstance(item, dict) or set(item) != TRACEABILITY_REQUIREMENT_KEYS:
            errors.append(f"{location}: invalid requirement shape")
            continue
        requirement_id = item.get("id")
        if not isinstance(requirement_id, str) or not TRACEABILITY_ID.fullmatch(
            requirement_id
        ):
            errors.append(f"{location}.id: invalid requirement ID")
        elif requirement_id in ids:
            errors.append(f"{location}.id: duplicate requirement ID")
        else:
            ids.add(requirement_id)
            ordered_ids.append(requirement_id)
        for field in ("requirement", "required_evidence"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{location}.{field}: expected non-empty string")
        gates = item.get("acceptance_gates")
        if (
            not isinstance(gates, list)
            or not gates
            or len(set(gates)) != len(gates)
            or any(gate not in ACCEPTANCE_GATES for gate in gates)
            or gates != sorted(gates, key=ACCEPTANCE_GATES.index)
        ):
            errors.append(f"{location}.acceptance_gates: invalid gate list")
    if ordered_ids != sorted(ordered_ids):
        errors.append("traceability registry requirements: IDs must be sorted")
    return ids, errors


def load_traceability_registry(repo_root: Path) -> tuple[set[str], list[str]]:
    """Load and validate the sole machine-readable traceability vocabulary."""
    path = repo_root / "docs/validation/traceability-ids.json"
    try:
        document = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return set(), [f"cannot load traceability registry: {error}"]
    return validate_traceability_registry(document)
