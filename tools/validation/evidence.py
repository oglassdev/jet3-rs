"""Typed evidence validation and verification-level adapters."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from pathlib import PurePosixPath
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
TEST_MANIFEST_PATH = Path("tests/manifest.json")


def load_test_manifest(
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load the stable test-ID index used by support evidence."""
    try:
        document = load_json(repo_root / TEST_MANIFEST_PATH)
    except (OSError, json.JSONDecodeError) as error:
        return {}, [f"test manifest: cannot load {TEST_MANIFEST_PATH}: {error}"]
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        return {}, ["test manifest: expected an object with a cases array"]

    index: dict[str, dict[str, Any]] = {}
    errors = []
    for case_index, case in enumerate(document["cases"]):
        location = f"test manifest cases[{case_index}]"
        if not isinstance(case, dict):
            errors.append(f"{location}: expected object")
            continue
        test_id = case.get("id")
        target = case.get("target")
        runtime_name = case.get("runtime_name")
        if not isinstance(test_id, str) or not SCENARIO_ID.fullmatch(test_id):
            errors.append(f"{location}.id: invalid stable test ID")
            continue
        if test_id in index:
            errors.append(f"{location}.id: duplicate stable test ID {test_id!r}")
            continue
        if not isinstance(target, str) or not target:
            errors.append(f"{location}.target: expected non-empty string")
            continue
        if not isinstance(runtime_name, str) or not runtime_name:
            errors.append(f"{location}.runtime_name: expected non-empty string")
            continue
        index[test_id] = case
    return index, errors


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


def _is_test_only_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        parsed.suffix == ".rs"
        and (
            parsed.name.endswith("_tests.rs")
            or "tests" in parsed.parts[:-1]
        )
    )


def _test_path_matches_case(path: str, case: dict[str, Any]) -> bool:
    parsed = PurePosixPath(path)
    target = case["target"]
    runtime_name = case["runtime_name"]
    parts = parsed.parts
    if len(parts) == 4 and parts[0] == "crates" and parts[2] == "tests":
        return parsed.stem == target
    if len(parts) != 4 or parts[0] != "crates" or parts[2] != "src":
        return False
    module = runtime_name.split("::", 1)[0]
    crate_target = parts[1].replace("-", "_")
    return crate_target == target and parsed.name == f"{module}_tests.rs"


def _test_function_is_retained(blob: bytes, runtime_name: str) -> bool:
    function_name = runtime_name.rsplit("::", 1)[-1].encode("ascii", errors="ignore")
    pattern = rb"\bfn\s+" + re.escape(function_name) + rb"\s*(?:<|\()"
    return re.search(pattern, blob) is not None


def _validate_test_evidence(
    path: str,
    scenarios: list[str],
    blob: bytes | None,
    manifest: dict[str, dict[str, Any]],
    location: str,
) -> list[str]:
    errors = []
    if not _is_test_only_path(path):
        errors.append(
            f"{location}.path: test evidence must reference a test-only Rust file"
        )
    for scenario_id in scenarios:
        case = manifest.get(scenario_id)
        if case is None:
            errors.append(
                f"{location}.scenario_ids: {scenario_id!r} is absent from "
                f"{TEST_MANIFEST_PATH}"
            )
            continue
        if not _test_path_matches_case(path, case):
            errors.append(
                f"{location}.scenario_ids: {scenario_id!r} does not map to {path!r}"
            )
            continue
        if blob is not None and not _test_function_is_retained(
            blob, case["runtime_name"]
        ):
            errors.append(
                f"{location}: retained test blob omits the manifested function for "
                f"{scenario_id!r}"
            )
    return errors


def validate_evidence(
    evidence: Any,
    evidence_index: int,
    capability_id: str,
    verification: str,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
    test_manifest: dict[str, dict[str, Any]],
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
    if kind == "test" and not scenarios:
        errors.append(f"{location}.scenario_ids: test evidence requires scenario IDs")
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

    retained_blob = None
    if (
        kind in {"source", "test"}
        and isinstance(commit, str)
        and GIT_COMMIT.fullmatch(commit)
        and isinstance(path, str)
        and isinstance(expected_hash, str)
        and SHA256.fullmatch(expected_hash)
    ):
        retained_blob = git_blob(repo_root, commit, path)
        if retained_blob is None:
            errors.append(f"{location}: {path!r} is not retained at commit {commit}")
        elif hashlib.sha256(retained_blob).hexdigest() != expected_hash:
            errors.append(f"{location}: retained git blob hash does not match evidence")
    if kind == "test" and isinstance(path, str):
        errors.extend(
            _validate_test_evidence(
                path, scenarios, retained_blob, test_manifest, location
            )
        )

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
