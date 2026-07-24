"""Typed evidence validation and verification-level adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .common import (
    GIT_COMMIT,
    SCENARIO_ID,
    SHA256,
    VERIFICATION_RANK,
    check_keys,
    git_blob,
    load_json,
    sha256_file,
    validate_repository_path,
)

EVIDENCE_KINDS = ("source", "test", "independent_report", "dao_bundle")
EVIDENCE_KEYS = {
    "kind",
    "path",
    "sha256",
    "commit",
    "capability_id",
    "scenario_ids",
}
EVIDENCE_REQUIRED_KEYS = {
    "kind",
    "path",
    "sha256",
    "commit",
    "capability_id",
}


def _scenario_ids(evidence: dict[str, Any], location: str) -> tuple[list[str], list[str]]:
    value = evidence.get("scenario_ids")
    if value is None:
        return [], []
    valid = (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and SCENARIO_ID.fullmatch(item) for item in value)
        and len(set(value)) == len(value)
    )
    if valid:
        return value, []
    return [], [f"{location}.scenario_ids: expected unique stable scenario IDs"]


def _validate_independent_report(
    path: Path,
    capability_id: str,
    commit: str,
    scenario_ids: list[str],
    location: str,
) -> list[str]:
    try:
        report = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"{location}: cannot parse independent report: {error}"]
    if not isinstance(report, dict):
        return [f"{location}: independent report must be a JSON object"]

    errors = []
    if report.get("git_commit") != commit:
        errors.append(f"{location}: independent report commit does not match evidence")
    if report.get("dirty") is not False:
        errors.append(f"{location}: independent report is not clean-worktree eligible")
    if report.get("status") != "PASS":
        errors.append(f"{location}: independent report status is not PASS")
    capability_ids = report.get("capability_ids")
    if not isinstance(capability_ids, list) or capability_id not in capability_ids:
        errors.append(f"{location}: independent report omits capability ID")
    reported_scenarios = report.get("scenario_ids")
    if not isinstance(reported_scenarios, list) or not set(scenario_ids).issubset(
        reported_scenarios
    ):
        errors.append(f"{location}: independent report omits referenced scenarios")
    return errors


def _validate_kind_for_level(kind: Any, verification: str, location: str) -> list[str]:
    rank = VERIFICATION_RANK.get(verification, -1)
    if kind in {"source", "test"} and rank < 1:
        return [f"{location}.kind: {kind} evidence requires internal_only or higher"]
    if kind == "independent_report" and rank < 2:
        return [
            f"{location}.kind: independent_report requires independent_check or higher"
        ]
    if kind == "dao_bundle" and rank < 3:
        return [f"{location}.kind: dao_bundle requires a DAO verification state"]
    return []


def _validate_release_eligibility(
    kind: Any,
    commit: Any,
    head_commit: str | None,
    worktree_dirty: bool | None,
    location: str,
) -> list[str]:
    if kind not in {"independent_report", "dao_bundle"}:
        return []
    errors = []
    if head_commit is None:
        errors.append(f"{location}: cannot determine repository HEAD")
    elif commit != head_commit:
        errors.append(
            f"{location}.commit: release evidence must match HEAD {head_commit}"
        )
    if worktree_dirty is None:
        errors.append(f"{location}: cannot determine worktree state")
    elif worktree_dirty:
        errors.append(f"{location}: release evidence is ineligible on a dirty worktree")
    return errors


def validate_evidence(
    evidence: Any,
    evidence_index: int,
    capability_id: str,
    verification: str,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
) -> tuple[list[str], tuple[str, str] | None]:
    """Validate one typed evidence reference and return its kind/path key."""
    location = f"evidence[{evidence_index}]"
    if not isinstance(evidence, dict):
        return [f"{location}: expected typed evidence object"], None
    errors = check_keys(evidence, EVIDENCE_KEYS, EVIDENCE_REQUIRED_KEYS, location)
    kind = evidence.get("kind")
    path = evidence.get("path")
    expected_hash = evidence.get("sha256")
    commit = evidence.get("commit")

    if kind not in EVIDENCE_KINDS:
        errors.append(f"{location}.kind: unknown evidence kind {kind!r}")
    if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
        errors.append(f"{location}.sha256: expected lowercase SHA-256")
    if not isinstance(commit, str) or not GIT_COMMIT.fullmatch(commit):
        errors.append(f"{location}.commit: expected full lowercase git commit")
    if evidence.get("capability_id") != capability_id:
        errors.append(f"{location}.capability_id: expected {capability_id!r}")

    scenarios, scenario_errors = _scenario_ids(evidence, location)
    errors.extend(scenario_errors)
    if kind in {"independent_report", "dao_bundle"} and not scenarios:
        errors.append(f"{location}.scenario_ids: {kind} requires scenario IDs")
    errors.extend(validate_repository_path(path, repo_root, f"{location}.path"))

    candidate = repo_root / path if isinstance(path, str) else None
    if candidate is not None and not errors:
        if not candidate.is_file():
            errors.append(f"{location}.path: evidence must be a regular file")
        elif kind in {"independent_report", "dao_bundle"}:
            actual_hash = sha256_file(candidate)
            if actual_hash != expected_hash:
                errors.append(
                    f"{location}.sha256: expected {expected_hash}, got {actual_hash}"
                )

    if (
        kind in {"source", "test"}
        and isinstance(commit, str)
        and GIT_COMMIT.fullmatch(commit)
        and isinstance(path, str)
        and isinstance(expected_hash, str)
        and SHA256.fullmatch(expected_hash)
    ):
        blob = git_blob(repo_root, commit, path)
        if blob is None:
            errors.append(f"{location}: {path!r} is not retained at commit {commit}")
        elif hashlib.sha256(blob).hexdigest() != expected_hash:
            errors.append(f"{location}: retained git blob hash does not match evidence")

    errors.extend(_validate_kind_for_level(kind, verification, location))
    errors.extend(
        _validate_release_eligibility(
            kind, commit, head_commit, worktree_dirty, location
        )
    )
    if (
        kind == "independent_report"
        and candidate is not None
        and candidate.is_file()
        and isinstance(commit, str)
        and scenarios
    ):
        errors.extend(
            _validate_independent_report(
                candidate, capability_id, commit, scenarios, location
            )
        )
    if kind == "dao_bundle":
        errors.append(
            f"{location}: DAO bundle semantic validation is not integrated; "
            "DAO evidence fails closed"
        )
    key = (str(kind), str(path)) if kind is not None and path is not None else None
    return errors, key
