#!/usr/bin/env python3
"""Validate the machine-readable jet3-rs support contract.

This deliberately uses only the Python standard library. The JSON Schemas in
docs/validation/schema are the documentation contract; this program enforces
the support-matrix rules needed by acceptance G0.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
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
TOP_LEVEL_KEYS = {
    "schema_version",
    "product_scope",
    "state_vocabulary",
    "capabilities",
}
SCOPE_KEYS = {"format", "encrypted", "runtime_external_mdb_dependency"}
VOCABULARY_KEYS = {"implementation", "verification"}
CAPABILITY_REQUIRED_KEYS = {
    "id",
    "implementation",
    "verification",
    "required_verification",
    "evidence",
}
CAPABILITY_KEYS = CAPABILITY_REQUIRED_KEYS | {"reason"}
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


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _check_keys(
    value: dict[str, Any],
    expected: set[str],
    required: set[str],
    location: str,
) -> list[str]:
    errors = []
    for key in sorted(required - value.keys()):
        errors.append(f"{location}: missing required property {key!r}")
    for key in sorted(value.keys() - expected):
        if key in {"label", "user_facing_label"}:
            errors.append(
                f"{location}.{key}: user-facing labels are derived, not stored; "
                "a 'supported' claim requires commit-bound evidence"
            )
        else:
            errors.append(f"{location}: unknown property {key!r}")
    return errors


def _derived_label(
    implementation: str, verification: str, required_verification: str
) -> str:
    if implementation == "out_of_scope_v1":
        return "unsupported"
    if implementation == "not_started":
        return "planned"
    if implementation != "implemented":
        return "experimental"
    rank = VERIFICATION_RANK.get(verification)
    required_rank = VERIFICATION_RANK.get(required_verification)
    if rank is not None and required_rank is not None and rank >= required_rank:
        return "supported"
    return "experimental"


def _validate_evidence_path(
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
    if posix_path.is_absolute() or any(part in {"", ".", ".."} for part in posix_path.parts):
        return [f"{location}: unsafe evidence path {raw_path!r}"]

    candidate = repo_root.joinpath(*posix_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return [f"{location}: evidence path does not exist: {raw_path!r}"]

    try:
        resolved.relative_to(repo_root.resolve(strict=True))
    except ValueError:
        return [f"{location}: evidence path escapes the repository: {raw_path!r}"]
    return []


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(
    repo_root: Path, arguments: list[str], *, text: bool = True
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=text,
    )


def _git_head(repo_root: Path) -> str | None:
    result = _git(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit if GIT_COMMIT.fullmatch(commit) else None


def _git_dirty(repo_root: Path) -> bool | None:
    result = _git(
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
    if result.returncode != 0:
        return None
    return bool(result.stdout)


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes | None:
    result = _git(repo_root, ["show", f"{commit}:{path}"], text=False)
    if result.returncode != 0:
        return None
    return bytes(result.stdout)


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


def _validate_evidence(
    evidence: Any,
    evidence_index: int,
    capability_id: str,
    verification: str,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
) -> tuple[list[str], tuple[str, str] | None]:
    location = f"evidence[{evidence_index}]"
    if not isinstance(evidence, dict):
        return [f"{location}: expected typed evidence object"], None

    errors = _check_keys(
        evidence, EVIDENCE_KEYS, EVIDENCE_REQUIRED_KEYS, location
    )
    kind = evidence.get("kind")
    path = evidence.get("path")
    expected_hash = evidence.get("sha256")
    commit = evidence.get("commit")
    linked_capability = evidence.get("capability_id")
    scenario_ids = evidence.get("scenario_ids")

    if kind not in EVIDENCE_KINDS:
        errors.append(f"{location}.kind: unknown evidence kind {kind!r}")
    if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
        errors.append(f"{location}.sha256: expected lowercase SHA-256")
    if not isinstance(commit, str) or not GIT_COMMIT.fullmatch(commit):
        errors.append(f"{location}.commit: expected full lowercase git commit")
    if linked_capability != capability_id:
        errors.append(
            f"{location}.capability_id: expected {capability_id!r}, "
            f"got {linked_capability!r}"
        )

    if scenario_ids is not None:
        if (
            not isinstance(scenario_ids, list)
            or not scenario_ids
            or any(
                not isinstance(item, str) or not SCENARIO_ID.fullmatch(item)
                for item in scenario_ids
            )
            or len(set(scenario_ids)) != len(scenario_ids)
        ):
            errors.append(
                f"{location}.scenario_ids: expected unique stable scenario IDs"
            )
            valid_scenarios: list[str] = []
        else:
            valid_scenarios = scenario_ids
    else:
        valid_scenarios = []
    if kind in {"independent_report", "dao_bundle"} and not valid_scenarios:
        errors.append(f"{location}.scenario_ids: {kind} requires scenario IDs")

    if isinstance(path, str):
        errors.extend(_validate_evidence_path(path, repo_root, f"{location}.path"))
    else:
        errors.append(f"{location}.path: expected repository-relative path")

    candidate = repo_root / path if isinstance(path, str) else None
    if candidate is not None and not errors:
        if not candidate.is_file():
            errors.append(f"{location}.path: evidence must be a regular file")
        elif kind in {"independent_report", "dao_bundle"}:
            actual_hash = _sha256_file(candidate)
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
        blob = _git_blob(repo_root, commit, path)
        if blob is None:
            errors.append(
                f"{location}: {path!r} is not retained at commit {commit}"
            )
        elif hashlib.sha256(blob).hexdigest() != expected_hash:
            errors.append(
                f"{location}: retained git blob hash does not match evidence SHA-256"
            )

    rank = VERIFICATION_RANK.get(verification, -1)
    if kind in {"source", "test"} and rank < 1:
        errors.append(
            f"{location}.kind: {kind} evidence requires internal_only or higher"
        )
    if kind == "independent_report" and rank < 2:
        errors.append(
            f"{location}.kind: independent_report requires independent_check or higher"
        )
    if kind == "dao_bundle" and rank < 3:
        errors.append(f"{location}.kind: dao_bundle requires a DAO verification state")

    if kind in {"independent_report", "dao_bundle"}:
        if head_commit is None:
            errors.append(f"{location}: cannot determine repository HEAD")
        elif commit != head_commit:
            errors.append(
                f"{location}.commit: release evidence must match HEAD {head_commit}"
            )
        if worktree_dirty is None:
            errors.append(f"{location}: cannot determine worktree state")
        elif worktree_dirty:
            errors.append(
                f"{location}: release evidence is ineligible on a dirty worktree"
            )

    if (
        kind == "independent_report"
        and candidate is not None
        and candidate.is_file()
        and isinstance(commit, str)
        and valid_scenarios
    ):
        errors.extend(
            _validate_independent_report(
                candidate,
                capability_id,
                commit,
                valid_scenarios,
                location,
            )
        )

    if kind == "dao_bundle":
        errors.append(
            f"{location}: DAO bundle semantic validation is not integrated; "
            "DAO evidence fails closed"
        )

    key = (str(kind), str(path)) if kind is not None and path is not None else None
    return errors, key


def _validate_capability(
    capability: Any,
    index: int,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
) -> tuple[list[str], str | None]:
    location = f"$.capabilities[{index}]"
    if not isinstance(capability, dict):
        return [f"{location}: expected object, got {_typename(capability)}"], None

    errors = _check_keys(
        capability, CAPABILITY_KEYS, CAPABILITY_REQUIRED_KEYS, location
    )
    capability_id = capability.get("id")
    if not isinstance(capability_id, str) or not CAPABILITY_ID.fullmatch(capability_id):
        errors.append(
            f"{location}.id: expected a dotted lowercase capability identifier"
        )
        capability_id = None

    implementation = capability.get("implementation")
    verification = capability.get("verification")
    required = capability.get("required_verification")
    evidence = capability.get("evidence")
    reason = capability.get("reason")

    if implementation not in IMPLEMENTATION_STATES:
        errors.append(
            f"{location}.implementation: unknown state {implementation!r}"
        )
    if verification not in VERIFICATION_STATES:
        errors.append(f"{location}.verification: unknown state {verification!r}")
    if required not in REQUIRED_VERIFICATION_STATES:
        errors.append(
            f"{location}.required_verification: invalid requirement {required!r}"
        )

    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        errors.append(f"{location}.reason: expected a non-empty string")

    if not isinstance(evidence, list):
        errors.append(f"{location}.evidence: expected array, got {_typename(evidence)}")
        evidence_items: list[Any] = []
    else:
        evidence_items = evidence
        seen_evidence: set[tuple[str, str]] = set()
        evidence_kinds: set[str] = set()
        if isinstance(capability_id, str):
            for evidence_index, evidence_object in enumerate(evidence):
                evidence_errors, evidence_key = _validate_evidence(
                    evidence_object,
                    evidence_index,
                    capability_id,
                    str(verification),
                    repo_root,
                    head_commit,
                    worktree_dirty,
                )
                errors.extend(
                    f"{location}.{error}" for error in evidence_errors
                )
                if evidence_key is not None:
                    evidence_kinds.add(evidence_key[0])
                    if evidence_key in seen_evidence:
                        errors.append(
                            f"{location}.evidence[{evidence_index}]: duplicate "
                            f"evidence kind/path {evidence_key!r}"
                        )
                    seen_evidence.add(evidence_key)
        verification_rank = VERIFICATION_RANK.get(str(verification), 0)
        if verification_rank >= 1 and not evidence_kinds.intersection({"source", "test"}):
            errors.append(
                f"{location}.evidence: {verification} requires source or test evidence"
            )
        if verification_rank >= 2 and "independent_report" not in evidence_kinds:
            errors.append(
                f"{location}.evidence: {verification} requires an independent report"
            )
        if verification_rank >= 3 and "dao_bundle" not in evidence_kinds:
            errors.append(
                f"{location}.evidence: {verification} requires a DAO bundle"
            )

    if implementation == "out_of_scope_v1":
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                f"{location}.reason: out_of_scope_v1 requires a non-empty reason"
            )
        if verification != "not_applicable":
            errors.append(
                f"{location}.verification: out_of_scope_v1 requires not_applicable"
            )
        if required != "not_applicable":
            errors.append(
                f"{location}.required_verification: out_of_scope_v1 requires "
                "not_applicable"
            )
        if evidence_items:
            errors.append(f"{location}.evidence: out-of-scope entries take no evidence")
    elif implementation in IMPLEMENTATION_STATES:
        if verification == "not_applicable":
            errors.append(
                f"{location}.verification: in-scope capability cannot be not_applicable"
            )
        if required == "not_applicable":
            errors.append(
                f"{location}.required_verification: in-scope capability cannot be "
                "not_applicable"
            )

    if implementation == "not_started":
        if verification != "unverified":
            errors.append(
                f"{location}.verification: not_started requires unverified"
            )
        if evidence_items:
            errors.append(f"{location}.evidence: not_started requires no evidence")

    if verification == "unverified" and evidence_items:
        errors.append(f"{location}.evidence: unverified requires no evidence")
    elif verification in VERIFICATION_RANK and verification != "unverified":
        if not evidence_items:
            errors.append(
                f"{location}.evidence: {verification} requires at least one "
                "existing evidence path"
            )

    for label_key in ("label", "user_facing_label"):
        label = capability.get(label_key)
        if label == "supported":
            derived = _derived_label(
                str(implementation), str(verification), str(required)
            )
            if derived != "supported":
                errors.append(
                    f"{location}.{label_key}: unsupported 'supported' claim; "
                    f"derived label is {derived!r}"
                )

    return errors, capability_id


def validate_support_matrix(document: Any, repo_root: Path) -> list[str]:
    """Return all support-matrix contract violations."""
    if not isinstance(document, dict):
        return [f"$: expected object, got {_typename(document)}"]

    errors = _check_keys(document, TOP_LEVEL_KEYS, TOP_LEVEL_KEYS, "$")

    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        errors.append("$.schema_version: expected integer 2")

    scope = document.get("product_scope")
    if not isinstance(scope, dict):
        errors.append(
            f"$.product_scope: expected object, got {_typename(scope)}"
        )
    else:
        errors.extend(_check_keys(scope, SCOPE_KEYS, SCOPE_KEYS, "$.product_scope"))
        if scope.get("format") != "Microsoft Jet 3 / Access 97":
            errors.append(
                "$.product_scope.format: expected 'Microsoft Jet 3 / Access 97', "
                f"got {scope.get('format')!r}"
            )
        for key in ("encrypted", "runtime_external_mdb_dependency"):
            if scope.get(key) is not False:
                errors.append(
                    f"$.product_scope.{key}: expected false, got {scope.get(key)!r}"
                )

    vocabulary = document.get("state_vocabulary")
    if not isinstance(vocabulary, dict):
        errors.append(
            f"$.state_vocabulary: expected object, got {_typename(vocabulary)}"
        )
    else:
        errors.extend(
            _check_keys(
                vocabulary,
                VOCABULARY_KEYS,
                VOCABULARY_KEYS,
                "$.state_vocabulary",
            )
        )
        if vocabulary.get("implementation") != list(IMPLEMENTATION_STATES):
            errors.append(
                "$.state_vocabulary.implementation: vocabulary must exactly match "
                f"{list(IMPLEMENTATION_STATES)!r}"
            )
        if vocabulary.get("verification") != list(VERIFICATION_STATES):
            errors.append(
                "$.state_vocabulary.verification: vocabulary must exactly match "
                f"{list(VERIFICATION_STATES)!r}"
            )

    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list):
        errors.append(
            f"$.capabilities: expected array, got {_typename(capabilities)}"
        )
        return errors
    if not capabilities:
        errors.append("$.capabilities: expected at least one capability")

    seen_ids: dict[str, int] = {}
    head_commit = _git_head(repo_root)
    worktree_dirty = _git_dirty(repo_root)
    for index, capability in enumerate(capabilities):
        capability_errors, capability_id = _validate_capability(
            capability, index, repo_root, head_commit, worktree_dirty
        )
        errors.extend(capability_errors)
        if capability_id is not None:
            if capability_id in seen_ids:
                errors.append(
                    f"$.capabilities[{index}].id: duplicate capability ID "
                    f"{capability_id!r}; first declared at index "
                    f"{seen_ids[capability_id]}"
                )
            else:
                seen_ids[capability_id] = index

    return errors


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _self_test(repo_root: Path, matrix_path: Path) -> int:
    try:
        current = load_json(matrix_path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"self-test setup failed: {error}", file=sys.stderr)
        return 1
    head_commit = _git_head(repo_root)
    if head_commit is None:
        print("self-test setup failed: cannot determine git HEAD", file=sys.stderr)
        return 1
    readme_hash = _sha256_file(repo_root / "README.md")

    def evidence(
        kind: str,
        path: str,
        digest: str,
        capability_id: str = "database.open",
        *,
        scenarios: list[str] | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": kind,
            "path": path,
            "sha256": digest,
            "commit": head_commit,
            "capability_id": capability_id,
        }
        if scenarios is not None:
            item["scenario_ids"] = scenarios
        return item

    cases: list[tuple[str, Any, str | None]] = [
        ("current matrix", current, None),
    ]

    duplicate = copy.deepcopy(current)
    duplicate["capabilities"].append(copy.deepcopy(duplicate["capabilities"][0]))
    cases.append(("duplicate ID", duplicate, "duplicate capability ID"))

    vocabulary = copy.deepcopy(current)
    vocabulary["state_vocabulary"]["implementation"].append("complete")
    cases.append(("changed vocabulary", vocabulary, "vocabulary must exactly match"))

    invalid_combo = copy.deepcopy(current)
    invalid_combo["capabilities"][0]["verification"] = "not_applicable"
    cases.append(
        ("invalid state combination", invalid_combo, "in-scope capability cannot")
    )

    missing_reason = copy.deepcopy(current)
    del missing_reason["capabilities"][-1]["reason"]
    cases.append(("missing reason", missing_reason, "requires a non-empty reason"))

    unsafe_evidence = copy.deepcopy(current)
    unsafe_evidence["capabilities"][0]["implementation"] = "partial"
    unsafe_evidence["capabilities"][0]["verification"] = "internal_only"
    unsafe_evidence["capabilities"][0]["evidence"] = [
        evidence("source", "../outside.json", "0" * 64)
    ]
    cases.append(("unsafe evidence path", unsafe_evidence, "unsafe evidence path"))

    missing_evidence = copy.deepcopy(current)
    missing_evidence["capabilities"][0]["implementation"] = "partial"
    missing_evidence["capabilities"][0]["verification"] = "internal_only"
    missing_evidence["capabilities"][0]["evidence"] = [
        evidence("source", "artifacts/missing.json", "0" * 64)
    ]
    cases.append(
        ("missing evidence path", missing_evidence, "evidence path does not exist")
    )

    false_claim = copy.deepcopy(current)
    false_claim["capabilities"][0]["label"] = "supported"
    cases.append(
        ("false supported claim", false_claim, "unsupported 'supported' claim")
    )

    existing = copy.deepcopy(current)
    existing["capabilities"][0]["implementation"] = "partial"
    existing["capabilities"][0]["verification"] = "internal_only"
    existing["capabilities"][0]["evidence"] = [
        evidence("source", "README.md", readme_hash)
    ]
    cases.append(("existing typed evidence", existing, None))

    readme_as_dao = copy.deepcopy(current)
    readme_as_dao["capabilities"][0]["implementation"] = "implemented"
    readme_as_dao["capabilities"][0]["verification"] = "dao_differential"
    readme_as_dao["capabilities"][0]["evidence"] = [
        evidence("source", "README.md", readme_hash),
        evidence(
            "independent_report",
            "README.md",
            readme_hash,
            scenarios=["IT-README"],
        ),
        evidence(
            "dao_bundle",
            "README.md",
            readme_hash,
            scenarios=["DAO-READ-README"],
        ),
    ]
    cases.append(
        (
            "README cannot satisfy DAO evidence",
            readme_as_dao,
            "DAO bundle semantic validation is not integrated",
        )
    )

    wrong_kind = copy.deepcopy(current)
    wrong_kind["capabilities"][0]["implementation"] = "partial"
    wrong_kind["capabilities"][0]["verification"] = "independent_check"
    wrong_kind["capabilities"][0]["evidence"] = [
        evidence("source", "README.md", readme_hash)
    ]
    cases.append(
        (
            "independent state needs independent report",
            wrong_kind,
            "requires an independent report",
        )
    )

    failures = 0
    for name, document, expected_fragment in cases:
        errors = validate_support_matrix(document, repo_root)
        if expected_fragment is None:
            passed = not errors
        else:
            passed = any(expected_fragment in error for error in errors)
        if passed:
            print(f"ok - {name}")
        else:
            print(
                f"not ok - {name}: expected {expected_fragment!r}, got {errors!r}",
                file=sys.stderr,
            )
            failures += 1

    print(f"self-test: {len(cases) - failures} passed, {failures} failed")
    return 1 if failures else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate docs/validation/support-matrix.json"
    )
    parser.add_argument(
        "matrix",
        nargs="?",
        type=Path,
        help="support matrix path (default: docs/validation/support-matrix.json)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="repository root used to resolve evidence paths",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run validator corruption tests instead of normal validation",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    default_root = Path(__file__).resolve().parent.parent
    repo_root = (args.repo_root or default_root).resolve()
    matrix_path = args.matrix or repo_root / "docs/validation/support-matrix.json"
    matrix_path = matrix_path.resolve()

    if args.self_test:
        return _self_test(repo_root, matrix_path)

    try:
        document = load_json(matrix_path)
    except OSError as error:
        print(f"ERROR: cannot read {matrix_path}: {error}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"ERROR: invalid JSON in {matrix_path}: {error}", file=sys.stderr)
        return 1

    errors = validate_support_matrix(document, repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"support-matrix validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"support-matrix validation passed: "
        f"{len(document['capabilities'])} capabilities"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
