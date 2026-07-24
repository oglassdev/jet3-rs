"""Support-matrix shape, state, and cumulative-evidence validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    CAPABILITY_ID,
    IMPLEMENTATION_STATES,
    REQUIRED_VERIFICATION_STATES,
    VERIFICATION_RANK,
    VERIFICATION_STATES,
    check_keys,
    git_dirty,
    git_head,
    typename,
)
from .evidence import validate_evidence

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
    return (
        "supported"
        if rank is not None and required_rank is not None and rank >= required_rank
        else "experimental"
    )


def _validate_evidence_list(
    evidence: Any,
    capability_id: str,
    verification: str,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
    location: str,
) -> tuple[list[str], list[Any]]:
    if not isinstance(evidence, list):
        return [f"{location}.evidence: expected array, got {typename(evidence)}"], []
    errors = []
    seen: set[tuple[str, str]] = set()
    kinds: set[str] = set()
    for index, item in enumerate(evidence):
        item_errors, key = validate_evidence(
            item,
            index,
            capability_id,
            verification,
            repo_root,
            head_commit,
            worktree_dirty,
        )
        errors.extend(f"{location}.{error}" for error in item_errors)
        if key is not None:
            kinds.add(key[0])
            if key in seen:
                errors.append(
                    f"{location}.evidence[{index}]: duplicate evidence kind/path {key!r}"
                )
            seen.add(key)

    rank = VERIFICATION_RANK.get(verification, 0)
    if rank >= 1 and not kinds.intersection({"source", "test"}):
        errors.append(f"{location}.evidence: {verification} requires source or test evidence")
    if rank >= 2 and "independent_report" not in kinds:
        errors.append(f"{location}.evidence: {verification} requires an independent report")
    if rank >= 3 and "dao_bundle" not in kinds:
        errors.append(f"{location}.evidence: {verification} requires a DAO bundle")
    return errors, evidence


def _validate_scope_state(
    implementation: Any,
    verification: Any,
    required: Any,
    reason: Any,
    evidence: list[Any],
    location: str,
) -> list[str]:
    errors = []
    if implementation == "out_of_scope_v1":
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{location}.reason: out_of_scope_v1 requires a non-empty reason")
        if verification != "not_applicable":
            errors.append(f"{location}.verification: out_of_scope_v1 requires not_applicable")
        if required != "not_applicable":
            errors.append(
                f"{location}.required_verification: out_of_scope_v1 requires not_applicable"
            )
        if evidence:
            errors.append(f"{location}.evidence: out-of-scope entries take no evidence")
    elif implementation in IMPLEMENTATION_STATES:
        if verification == "not_applicable":
            errors.append(
                f"{location}.verification: in-scope capability cannot be not_applicable"
            )
        if required == "not_applicable":
            errors.append(
                f"{location}.required_verification: in-scope capability cannot be not_applicable"
            )
    if implementation == "not_started":
        if verification != "unverified":
            errors.append(f"{location}.verification: not_started requires unverified")
        if evidence:
            errors.append(f"{location}.evidence: not_started requires no evidence")
    if verification == "unverified" and evidence:
        errors.append(f"{location}.evidence: unverified requires no evidence")
    elif verification in VERIFICATION_RANK and verification != "unverified" and not evidence:
        errors.append(f"{location}.evidence: {verification} requires evidence")
    return errors

def _validate_capability(
    capability: Any,
    index: int,
    repo_root: Path,
    head_commit: str | None,
    worktree_dirty: bool | None,
) -> tuple[list[str], str | None]:
    location = f"$.capabilities[{index}]"
    if not isinstance(capability, dict):
        return [f"{location}: expected object, got {typename(capability)}"], None
    errors = check_keys(capability, CAPABILITY_KEYS, CAPABILITY_REQUIRED_KEYS, location)
    capability_id = capability.get("id")
    if not isinstance(capability_id, str) or not CAPABILITY_ID.fullmatch(capability_id):
        errors.append(f"{location}.id: expected a dotted lowercase capability identifier")
        return errors, None

    implementation = capability.get("implementation")
    verification = capability.get("verification")
    required = capability.get("required_verification")
    reason = capability.get("reason")
    if implementation not in IMPLEMENTATION_STATES:
        errors.append(f"{location}.implementation: unknown state {implementation!r}")
    if verification not in VERIFICATION_STATES:
        errors.append(f"{location}.verification: unknown state {verification!r}")
    if required not in REQUIRED_VERIFICATION_STATES:
        errors.append(f"{location}.required_verification: invalid requirement {required!r}")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        errors.append(f"{location}.reason: expected a non-empty string")

    evidence_errors, evidence = _validate_evidence_list(
        capability.get("evidence"),
        capability_id,
        str(verification),
        repo_root,
        head_commit,
        worktree_dirty,
        location,
    )
    errors.extend(evidence_errors)
    errors.extend(
        _validate_scope_state(
            implementation, verification, required, reason, evidence, location
        )
    )
    for label_key in ("label", "user_facing_label"):
        if capability.get(label_key) == "supported" and _derived_label(
            str(implementation), str(verification), str(required)
        ) != "supported":
            errors.append(f"{location}.{label_key}: unsupported 'supported' claim")
    return errors, capability_id


def _validate_header(document: dict[str, Any]) -> list[str]:
    errors = check_keys(document, TOP_LEVEL_KEYS, TOP_LEVEL_KEYS, "$")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 2:
        errors.append("$.schema_version: expected integer 2")
    scope = document.get("product_scope")
    if not isinstance(scope, dict):
        errors.append(f"$.product_scope: expected object, got {typename(scope)}")
    else:
        errors.extend(check_keys(scope, SCOPE_KEYS, SCOPE_KEYS, "$.product_scope"))
        if scope.get("format") != "Microsoft Jet 3 / Access 97":
            errors.append("$.product_scope.format: expected 'Microsoft Jet 3 / Access 97'")
        for key in ("encrypted", "runtime_external_mdb_dependency"):
            if scope.get(key) is not False:
                errors.append(f"$.product_scope.{key}: expected false")
    vocabulary = document.get("state_vocabulary")
    if not isinstance(vocabulary, dict):
        errors.append(f"$.state_vocabulary: expected object, got {typename(vocabulary)}")
    else:
        errors.extend(
            check_keys(vocabulary, VOCABULARY_KEYS, VOCABULARY_KEYS, "$.state_vocabulary")
        )
        if vocabulary.get("implementation") != list(IMPLEMENTATION_STATES):
            errors.append("$.state_vocabulary.implementation: vocabulary must exactly match")
        if vocabulary.get("verification") != list(VERIFICATION_STATES):
            errors.append("$.state_vocabulary.verification: vocabulary must exactly match")
    return errors


def validate_support_matrix(document: Any, repo_root: Path) -> list[str]:
    """Return all support-matrix contract violations."""
    if not isinstance(document, dict):
        return [f"$: expected object, got {typename(document)}"]
    errors = _validate_header(document)
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list):
        errors.append(f"$.capabilities: expected array, got {typename(capabilities)}")
        return errors
    if not capabilities:
        errors.append("$.capabilities: expected at least one capability")

    seen_ids: dict[str, int] = {}
    head_commit = git_head(repo_root)
    worktree_dirty = git_dirty(repo_root)
    for index, capability in enumerate(capabilities):
        item_errors, capability_id = _validate_capability(
            capability, index, repo_root, head_commit, worktree_dirty
        )
        errors.extend(item_errors)
        if capability_id is not None:
            if capability_id in seen_ids:
                errors.append(
                    f"$.capabilities[{index}].id: duplicate capability ID "
                    f"{capability_id!r}; first declared at index {seen_ids[capability_id]}"
                )
            else:
                seen_ids[capability_id] = index
    return errors
