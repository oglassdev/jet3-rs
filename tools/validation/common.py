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
