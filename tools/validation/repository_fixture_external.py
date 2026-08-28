"""Checked fixture, fuzz-seed, and external observational-corpus validation."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from .repository_common import (
    ContractError,
    FIXTURE_ROOTS,
    FUZZ_ID,
    CHECKED_PROTOCOL_TEST_RESOURCES,
    PROVENANCE_ID,
    SCENARIO_ID,
    SHA256,
    exact_keys,
    nonempty,
    resolve_file,
    safe_path,
    sha256,
)


def fixture_candidates(tracked: set[str]) -> set[str]:
    """Return tracked fixture payloads that require manifest entries."""
    candidates: set[str] = set()
    for raw in tracked:
        path = PurePosixPath(raw)
        if path.name in {"README.md", "manifest.json"}:
            continue
        if any(path.is_relative_to(root) for root in FIXTURE_ROOTS):
            candidates.add(raw)
    return candidates


def valid_protocol_test_resource_reference(
    root: Path, tracked: set[str], path: Any, digest: Any
) -> bool:
    """Recognize an exact, tracked, content-bound protocol test resource."""
    if (
        not isinstance(path, str)
        or path not in CHECKED_PROTOCOL_TEST_RESOURCES
        or path not in tracked
    ):
        return False
    try:
        resource, relative = resolve_file(root, path, "protocol test resource")
    except ContractError:
        return False
    return (
        relative == path
        and isinstance(digest, str)
        and SHA256.fullmatch(digest) is not None
        and sha256(resource) == digest
    )


def validate_repository_fixtures(
    root: Path,
    manifest: dict[str, Any],
    tracked: set[str],
    provenance_sections: dict[str, str],
    test_manifest: dict[str, Any],
) -> list[str]:
    """Validate checked generated, malformed, and regression fixture coverage."""
    errors: list[str] = []
    exact_keys(manifest, {"schema_version", "fixtures"}, "fixture manifest", errors)
    if manifest.get("schema_version") != 1:
        errors.append("fixture manifest schema_version must be integer 1")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        return errors + ["fixture manifest fixtures must be an array"]
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    indexed: dict[str, str] = {}
    expected_keys = {
        "id",
        "scenario_id",
        "provenance_id",
        "path",
        "sha256",
        "origin",
        "generator",
        "environment",
        "license",
        "reproduction_command",
    }
    for index, fixture in enumerate(fixtures):
        context = f"fixture manifest fixtures[{index}]"
        if not isinstance(fixture, dict):
            errors.append(f"{context}: expected object")
            continue
        exact_keys(fixture, expected_keys, context, errors)
        identifier = fixture.get("id")
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"FIX-[0-9]{4}", identifier) is None
        ):
            errors.append(f"{context}.id: invalid fixture ID")
        elif identifier in seen_ids:
            errors.append(f"{context}.id: duplicate fixture ID")
        seen_ids.add(identifier)
        scenario = fixture.get("scenario_id")
        if not isinstance(scenario, str) or SCENARIO_ID.fullmatch(scenario) is None:
            errors.append(f"{context}.scenario_id: invalid scenario ID")
        provenance_id = fixture.get("provenance_id")
        if (
            not isinstance(provenance_id, str)
            or PROVENANCE_ID.fullmatch(provenance_id) is None
        ):
            errors.append(f"{context}.provenance_id: invalid provenance ID")
        elif provenance_id not in provenance_sections:
            errors.append(f"{context}.provenance_id: missing provenance entry")
        try:
            path, relative = resolve_file(
                root, fixture.get("path"), f"{context}.path"
            )
        except ContractError as error:
            errors.append(str(error))
            continue
        pure = PurePosixPath(relative)
        if not any(
            pure.is_relative_to(root_path) for root_path in FIXTURE_ROOTS
        ):
            errors.append(f"{context}.path: fixture is outside checked fixture roots")
        if relative in seen_paths:
            errors.append(f"{context}.path: duplicate fixture path")
        seen_paths.add(relative)
        digest = fixture.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            errors.append(f"{context}.sha256: invalid SHA-256")
        elif sha256(path) != digest:
            errors.append(f"{context}.sha256: hash mismatch")
        for field in (
            "origin",
            "generator",
            "environment",
            "license",
            "reproduction_command",
        ):
            if not nonempty(fixture.get(field)):
                errors.append(f"{context}.{field}: expected non-empty string")
        indexed[relative] = digest if isinstance(digest, str) else ""

    candidates = fixture_candidates(tracked)
    if candidates != seen_paths:
        errors.append(
            "repository fixture inventory mismatch; "
            f"missing={sorted(candidates - seen_paths)}, "
            f"stale={sorted(seen_paths - candidates)}"
        )

    cases = test_manifest.get("cases")
    if not isinstance(cases, list):
        errors.append("test manifest cases must be an array")
    else:
        for case in cases:
            if not isinstance(case, dict):
                continue
            for reference in case.get("fixtures", []):
                if not isinstance(reference, dict):
                    continue
                path = reference.get("path")
                digest = reference.get("sha256")
                if indexed.get(path) != digest and not valid_protocol_test_resource_reference(
                    root, tracked, path, digest
                ):
                    errors.append(
                        f"test {case.get('id')} references an unmanifested fixture {path}"
                    )
    return errors


def validate_seed_manifest(
    root: Path, manifest: dict[str, Any], tracked: set[str]
) -> list[str]:
    """Validate every checked synthetic fuzz seed and its reproduction metadata."""
    errors: list[str] = []
    exact_keys(
        manifest,
        {"schema_version", "protocol_version", "seeds"},
        "seed manifest",
        errors,
    )
    if manifest.get("schema_version") != 1:
        errors.append("seed manifest schema_version must be integer 1")
    if manifest.get("protocol_version") != 1:
        errors.append("seed manifest protocol_version must be integer 1")
    seeds = manifest.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return errors + ["seed manifest seeds must be a non-empty array"]
    expected_keys = {
        "id",
        "path",
        "size_bytes",
        "sha256",
        "purpose",
        "origin",
        "generator",
        "environment",
        "rights",
        "reproduction_command",
    }
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, seed in enumerate(seeds):
        context = f"seed manifest seeds[{index}]"
        if not isinstance(seed, dict):
            errors.append(f"{context}: expected object")
            continue
        exact_keys(seed, expected_keys, context, errors)
        identifier = seed.get("id")
        if not isinstance(identifier, str) or FUZZ_ID.fullmatch(identifier) is None:
            errors.append(f"{context}.id: invalid fuzz scenario ID")
        elif identifier in seen_ids:
            errors.append(f"{context}.id: duplicate fuzz scenario ID")
        seen_ids.add(identifier)
        try:
            path, relative = resolve_file(root, seed.get("path"), f"{context}.path")
        except ContractError as error:
            errors.append(str(error))
            continue
        if not PurePosixPath(relative).is_relative_to(
            PurePosixPath("fuzz/corpus")
        ):
            errors.append(f"{context}.path: seed is outside fuzz/corpus")
        if relative in seen_paths:
            errors.append(f"{context}.path: duplicate seed path")
        seen_paths.add(relative)
        size = seed.get("size_bytes")
        if type(size) is not int or size < 0 or path.stat().st_size != size:
            errors.append(f"{context}.size_bytes: size mismatch")
        digest = seed.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            errors.append(f"{context}.sha256: invalid SHA-256")
        elif sha256(path) != digest:
            errors.append(f"{context}.sha256: hash mismatch")
        for field in (
            "purpose",
            "origin",
            "generator",
            "rights",
            "reproduction_command",
        ):
            if not nonempty(seed.get(field)):
                errors.append(f"{context}.{field}: expected non-empty string")
        environment = seed.get("environment")
        if not isinstance(environment, dict) or not environment:
            errors.append(f"{context}.environment: expected non-empty object")
        elif any(
            not nonempty(key) or not nonempty(value)
            for key, value in environment.items()
        ):
            errors.append(
                f"{context}.environment: keys and values must be non-empty strings"
            )

    candidates = {
        path
        for path in tracked
        if PurePosixPath(path).is_relative_to(PurePosixPath("fuzz/corpus"))
        and path != "fuzz/corpus/manifest.json"
    }
    if candidates != seen_paths:
        errors.append(
            "seed inventory mismatch; "
            f"missing={sorted(candidates - seen_paths)}, "
            f"stale={sorted(seen_paths - candidates)}"
        )
    return errors


def validate_external_observational_corpus(
    manifest: dict[str, Any],
    documentation: str,
    provenance_sections: dict[str, str],
    external_policy: dict[str, Any],
) -> list[str]:
    """Validate external identities without requiring or regenerating donated files."""
    errors: list[str] = []
    for field in ("redistributable", "regenerable", "acceptance_fixture"):
        if external_policy.get(field) is not False:
            errors.append(f"external observational policy {field} must be false")
    exact_keys(
        manifest,
        {
            "schema_version",
            "environment_variable",
            "purpose",
            "fixtures",
            "comparisons",
        },
        "external corpus manifest",
        errors,
    )
    if manifest.get("schema_version") != 2:
        errors.append("external corpus schema_version must be integer 2")
    if manifest.get("environment_variable") != "JET3_EXTERNAL_FIXTURE_ROOT":
        errors.append(
            "external corpus must remain opt-in through JET3_EXTERNAL_FIXTURE_ROOT"
        )
    if manifest.get("purpose") != "nonredistributable-read-only-corpus-verification":
        errors.append(
            "external corpus purpose must remain nonredistributable and read-only"
        )
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        return errors + ["external corpus fixtures must be a non-empty array"]
    seen: set[str] = set()
    fixture_ids: list[str] = []
    fixture_paths: set[str] = set()
    fixture_sizes: dict[str, int] = {}
    for index, fixture in enumerate(fixtures):
        context = f"external corpus fixtures[{index}]"
        if not isinstance(fixture, dict):
            errors.append(f"{context}: expected object")
            continue
        exact_keys(fixture, {"id", "path", "size_bytes", "sha256"}, context, errors)
        identifier = fixture.get("id")
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"FIX-[0-9]{4}", identifier) is None
        ):
            errors.append(f"{context}.id: invalid fixture ID")
            continue
        if identifier in seen:
            errors.append(f"{context}.id: duplicate fixture ID")
        seen.add(identifier)
        fixture_ids.append(identifier)
        raw_path = fixture.get("path")
        try:
            normalized_path = safe_path(raw_path, f"{context}.path").as_posix()
        except ContractError as error:
            errors.append(str(error))
            normalized_path = ""
        if normalized_path in fixture_paths:
            errors.append(f"{context}.path: duplicate external fixture path")
        fixture_paths.add(normalized_path)
        size = fixture.get("size_bytes")
        if type(size) is not int or size < 19:
            errors.append(f"{context}.size_bytes: invalid size")
        else:
            fixture_sizes[identifier] = size
        digest = fixture.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            errors.append(f"{context}.sha256: invalid SHA-256")
        section = provenance_sections.get(identifier)
        if section is None:
            errors.append(f"{context}: missing provenance section {identifier}")
            continue
        for label in ("- Origin:", "- Environment:", "- Protocol:", "- Rights:"):
            if label not in section:
                errors.append(f"{context}: provenance section lacks {label}")
        if isinstance(raw_path, str) and raw_path not in section:
            errors.append(f"{context}: provenance section lacks external locator")
        if isinstance(digest, str) and digest not in section:
            errors.append(f"{context}: provenance section lacks SHA-256")
        lowered = section.lower()
        if (
            "not redistributable" not in lowered
            and "no redistribution grant" not in lowered
        ):
            errors.append(f"{context}: provenance lacks nonredistribution restriction")
    if fixture_ids != sorted(fixture_ids):
        errors.append("external corpus fixtures must be sorted by ID")

    comparisons = manifest.get("comparisons")
    if not isinstance(comparisons, list):
        errors.append("external corpus comparisons must be an array")
    else:
        comparison_ids: list[str] = []
        fixture_pairs: set[tuple[str, str]] = set()
        for index, comparison in enumerate(comparisons):
            context = f"external corpus comparisons[{index}]"
            if not isinstance(comparison, dict):
                errors.append(f"{context}: expected object")
                continue
            exact_keys(
                comparison,
                {
                    "id",
                    "left_fixture_id",
                    "right_fixture_id",
                    "page_size_bytes",
                },
                context,
                errors,
            )
            identifier = comparison.get("id")
            if (
                not isinstance(identifier, str)
                or re.fullmatch(r"CMP-[0-9]{4}", identifier) is None
            ):
                errors.append(f"{context}.id: invalid comparison ID")
            elif identifier in comparison_ids:
                errors.append(f"{context}.id: duplicate comparison ID")
            comparison_ids.append(identifier)
            left = comparison.get("left_fixture_id")
            right = comparison.get("right_fixture_id")
            if (
                left not in fixture_sizes
                or right not in fixture_sizes
                or left == right
            ):
                errors.append(f"{context}: comparison fixture references are invalid")
            pair = (left, right)
            if pair in fixture_pairs:
                errors.append(f"{context}: duplicate directional fixture pair")
            fixture_pairs.add(pair)
            size = comparison.get("page_size_bytes")
            if type(size) is not int or size != 2048:
                errors.append(f"{context}.page_size_bytes: expected integer 2048")
            if left in fixture_sizes and right in fixture_sizes:
                left_size = fixture_sizes[left]
                right_size = fixture_sizes[right]
                if left_size != right_size:
                    errors.append(f"{context}: fixtures must have equal sizes")
                if left_size % 2048 != 0:
                    errors.append(
                        f"{context}: fixture size must be a multiple of 2048"
                    )
        if comparison_ids != sorted(comparison_ids):
            errors.append("external corpus comparisons must be sorted by ID")

    normalized_documentation = " ".join(documentation.split())
    required_documentation = (
        "optional exploratory inputs",
        "not distributed fixtures",
        "not acceptance evidence",
        "Do not commit",
    )
    for statement in required_documentation:
        if statement not in normalized_documentation:
            errors.append(f"external corpus documentation lacks {statement!r}")
    return errors
