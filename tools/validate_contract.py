#!/usr/bin/env python3
"""Validate the machine-readable jet3-rs support contract.

This deliberately uses only the Python standard library. The JSON Schemas in
docs/validation/schema are the documentation contract; this program enforces
the support-matrix rules needed by acceptance G0.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tempfile
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
REPOSITORY_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)


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


def _validate_capability(
    capability: Any,
    index: int,
    repo_root: Path,
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
        seen_evidence: set[str] = set()
        for evidence_index, evidence_path in enumerate(evidence):
            evidence_location = f"{location}.evidence[{evidence_index}]"
            errors.extend(
                _validate_evidence_path(evidence_path, repo_root, evidence_location)
            )
            if isinstance(evidence_path, str):
                if evidence_path in seen_evidence:
                    errors.append(
                        f"{evidence_location}: duplicate evidence path {evidence_path!r}"
                    )
                seen_evidence.add(evidence_path)

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
    if type(schema_version) is not int or schema_version != 1:
        errors.append("$.schema_version: expected integer 1")

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
    for index, capability in enumerate(capabilities):
        capability_errors, capability_id = _validate_capability(
            capability, index, repo_root
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
    unsafe_evidence["capabilities"][0]["evidence"] = ["../outside.json"]
    cases.append(("unsafe evidence path", unsafe_evidence, "unsafe evidence path"))

    missing_evidence = copy.deepcopy(current)
    missing_evidence["capabilities"][0]["implementation"] = "partial"
    missing_evidence["capabilities"][0]["verification"] = "internal_only"
    missing_evidence["capabilities"][0]["evidence"] = ["artifacts/missing.json"]
    cases.append(
        ("missing evidence path", missing_evidence, "evidence path does not exist")
    )

    false_claim = copy.deepcopy(current)
    false_claim["capabilities"][0]["label"] = "supported"
    cases.append(
        ("false supported claim", false_claim, "unsupported 'supported' claim")
    )

    failures = 0
    with tempfile.TemporaryDirectory() as temporary:
        # Exercise the existence branch without creating repository evidence.
        temporary_root = Path(temporary)
        existing = copy.deepcopy(current)
        for capability in existing["capabilities"]:
            if capability["implementation"] != "out_of_scope_v1":
                capability["implementation"] = "not_started"
                capability["verification"] = "unverified"
                capability["evidence"] = []
        existing["capabilities"][0]["implementation"] = "partial"
        existing["capabilities"][0]["verification"] = "internal_only"
        existing["capabilities"][0]["evidence"] = ["evidence.json"]
        (temporary_root / "evidence.json").write_text("{}", encoding="utf-8")
        existing_errors = validate_support_matrix(existing, temporary_root)
        if existing_errors:
            print(
                "not ok - existing safe evidence path:\n  "
                + "\n  ".join(existing_errors),
                file=sys.stderr,
            )
            failures += 1
        else:
            print("ok - existing safe evidence path")

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

    print(f"self-test: {len(cases) + 1 - failures} passed, {failures} failed")
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
