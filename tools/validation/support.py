"""Validate the compact support-matrix schema."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_PATH = Path("docs/validation/schema/support-matrix.schema.json")
CAPABILITY_KEYS = {"id", "implementation", "verification", "evidence"}
CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(?:[.][a-z][a-z0-9_]*)+$")
IMPLEMENTATION_STATES = {
    "not_started",
    "partial",
    "implemented",
    "out_of_scope_v1",
}
VERIFICATION_STATES = {
    "unverified",
    "internal_only",
    "independent_check",
    "dao_opened",
    "dao_differential",
    "not_applicable",
}


def _catalog(repo_root: Path) -> tuple[list[str], list[str]]:
    try:
        schema = json.loads((repo_root / SCHEMA_PATH).read_text(encoding="utf-8"))
        items = schema["properties"]["capabilities"]["prefixItems"]
        ids = [item["properties"]["id"]["const"] for item in items]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        return [], [f"cannot load capability catalog: {error}"]
    if not ids or len(ids) != len(set(ids)) or not all(
        isinstance(item, str) and CAPABILITY_ID.fullmatch(item) for item in ids
    ):
        return [], ["capability catalog contains invalid or duplicate IDs"]
    return ids, []


def _evidence_errors(value: Any, repo_root: Path, location: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{location}.evidence: expected array"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, reference in enumerate(value):
        item = f"{location}.evidence[{index}]"
        if not isinstance(reference, str) or not reference:
            errors.append(f"{item}: expected non-empty repository path")
            continue
        path = PurePosixPath(reference)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            errors.append(f"{item}: expected normalized relative repository path")
        elif not (repo_root / Path(*path.parts)).exists():
            errors.append(f"{item}: referenced path does not exist")
        if reference in seen:
            errors.append(f"{item}: duplicate evidence reference")
        seen.add(reference)
    return errors


def validate_support_matrix(document: Any, repo_root: Path) -> list[str]:
    """Return every support-matrix schema violation."""
    if not isinstance(document, dict):
        return ["$: expected object"]
    errors: list[str] = []
    if set(document) != {"schema_version", "capabilities"}:
        errors.append("$: expected only schema_version and capabilities")
    if document.get("schema_version") != 3 or isinstance(
        document.get("schema_version"), bool
    ):
        errors.append("$.schema_version: expected integer 3")
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list):
        return errors + ["$.capabilities: expected array"]

    expected_ids, catalog_errors = _catalog(repo_root)
    errors.extend(catalog_errors)
    observed_ids = [
        item.get("id") if isinstance(item, dict) else None for item in capabilities
    ]
    if not catalog_errors and observed_ids != expected_ids:
        errors.append("$.capabilities: capability catalog mismatch or noncanonical order")

    for index, capability in enumerate(capabilities):
        location = f"$.capabilities[{index}]"
        if not isinstance(capability, dict):
            errors.append(f"{location}: expected object")
            continue
        if set(capability) != CAPABILITY_KEYS:
            errors.append(f"{location}: expected exactly id, implementation, verification, evidence")
        capability_id = capability.get("id")
        implementation = capability.get("implementation")
        verification = capability.get("verification")
        evidence = capability.get("evidence")
        if not isinstance(capability_id, str) or not CAPABILITY_ID.fullmatch(capability_id):
            errors.append(f"{location}.id: invalid capability ID")
        if implementation not in IMPLEMENTATION_STATES:
            errors.append(f"{location}.implementation: unknown state")
        if verification not in VERIFICATION_STATES:
            errors.append(f"{location}.verification: unknown state")
        errors.extend(_evidence_errors(evidence, repo_root, location))
        if implementation == "not_started" and (
            verification != "unverified" or evidence != []
        ):
            errors.append(f"{location}: not_started must be unverified without evidence")
        if implementation == "out_of_scope_v1" and (
            verification != "not_applicable" or evidence != []
        ):
            errors.append(f"{location}: out_of_scope_v1 must be not_applicable without evidence")
        if implementation in {"partial", "implemented"} and verification == "not_applicable":
            errors.append(f"{location}: in-scope capability cannot be not_applicable")
        if verification == "unverified" and evidence != []:
            errors.append(f"{location}: unverified capability cannot cite evidence")
        if verification not in {"unverified", "not_applicable"} and evidence == []:
            errors.append(f"{location}: verified capability requires evidence")
    return errors
