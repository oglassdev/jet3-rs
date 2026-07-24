#!/usr/bin/env python3
"""Validate the fail-closed G0 repository, dependency, and provenance contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from validation import load_json, validate_support_matrix

CONTRACT_PATH = Path("docs/validation/repository-contract.json")
PROVENANCE_PATH = Path("docs/PROVENANCE.md")
SUPPORT_MATRIX_PATH = Path("docs/validation/support-matrix.json")
PROVENANCE_HEADING = re.compile(
    r"^### ((?:SRC|OBS|EXP|FIX)-[0-9]{4})\b", re.MULTILINE
)
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
PROHIBITED_PACKAGE_NAMES = {
    "dao",
    "j4rs",
    "jackcess",
    "jdbc",
    "jni",
    "libmdb",
    "mdbtools",
    "mdbtools-pure-rs",
    "odbc",
    "odbc-api",
    "ucanaccess",
}
PROHIBITED_RUNTIME_TEXT = re.compile(
    r"(?i)\b(?:mdbtools(?:-pure-rs)?|jackcess|ucanaccess|"
    r"odbc(?:-api)?|j4rs|jdbc|jni|dao\.dbengine)\b"
)
PROCESS_EXECUTION = re.compile(
    r"(?:std::process::Command|process::Command|Command::new\s*\()"
)
FFI_OR_UNSAFE = re.compile(
    r"(?:extern\s*\"(?:C|system)\"|\bunsafe\s+(?:fn|impl|trait)|\bunsafe\s*\{)"
)
FIXTURE_ROOTS = (
    PurePosixPath("fixtures/generated"),
    PurePosixPath("fixtures/malformed"),
    PurePosixPath("fixtures/regression"),
)


class ContractError(ValueError):
    """The checked repository contract cannot be loaded or inspected."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(value: Any, context: str) -> PurePosixPath:
    if not isinstance(value, str) or SAFE_PATH.fullmatch(value) is None:
        raise ContractError(f"{context}: unsafe repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"{context}: unsafe repository-relative path")
    return path


def _resolve_file(root: Path, value: Any, context: str) -> tuple[Path, str]:
    relative = _safe_path(value, context)
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


def _load_object(path: Path, description: str) -> dict[str, Any]:
    try:
        document = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot load {description}: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{description}: expected object")
    return document


def _exact_keys(
    document: dict[str, Any], expected: set[str], context: str, errors: list[str]
) -> None:
    actual = set(document)
    if actual != expected:
        errors.append(
            f"{context}: invalid keys; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_strings(
    values: Any, context: str, errors: list[str], *, nonempty: bool = True
) -> list[str]:
    if not isinstance(values, list) or (nonempty and not values):
        errors.append(f"{context}: expected {'non-empty ' if nonempty else ''}array")
        return []
    if any(not _nonempty(value) for value in values):
        errors.append(f"{context}: entries must be non-empty strings")
        return []
    rendered = list(values)
    if len(rendered) != len(set(rendered)):
        errors.append(f"{context}: duplicate entries")
    return rendered


def validate_contract_shape(document: dict[str, Any]) -> list[str]:
    """Validate the checked G0 inventory shape without consulting the filesystem."""
    errors: list[str] = []
    _exact_keys(
        document,
        {
            "schema_version",
            "workspace_packages",
            "allowed_runtime_packages",
            "format_knowledge",
            "fixtures",
        },
        "$",
        errors,
    )
    if document.get("schema_version") != 1:
        errors.append("$.schema_version: expected integer 1")

    workspace = document.get("workspace_packages")
    if not isinstance(workspace, dict):
        errors.append("$.workspace_packages: expected object")
    else:
        _exact_keys(workspace, {"production", "support"}, "$.workspace_packages", errors)
        seen_names: set[str] = set()
        seen_manifests: set[str] = set()
        for role in ("production", "support"):
            entries = workspace.get(role)
            if not isinstance(entries, list) or (role == "production" and not entries):
                errors.append(f"$.workspace_packages.{role}: invalid package array")
                continue
            for index, entry in enumerate(entries):
                context = f"$.workspace_packages.{role}[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{context}: expected object")
                    continue
                keys = {"name", "manifest", "crate_root"} if role == "production" else {
                    "name",
                    "manifest",
                }
                _exact_keys(entry, keys, context, errors)
                name = entry.get("name")
                if not _nonempty(name):
                    errors.append(f"{context}.name: expected non-empty string")
                elif name in seen_names:
                    errors.append(f"{context}.name: duplicate package {name}")
                else:
                    seen_names.add(name)
                for field in keys - {"name"}:
                    try:
                        path = _safe_path(entry.get(field), f"{context}.{field}").as_posix()
                    except ContractError as error:
                        errors.append(str(error))
                        continue
                    if field == "manifest":
                        if path in seen_manifests:
                            errors.append(f"{context}.manifest: duplicate path {path}")
                        seen_manifests.add(path)

    allowed = _unique_strings(
        document.get("allowed_runtime_packages"),
        "$.allowed_runtime_packages",
        errors,
    )
    production_names = {
        entry.get("name")
        for entry in (
            workspace.get("production", [])
            if isinstance(workspace, dict)
            else []
        )
        if isinstance(entry, dict)
    }
    if set(allowed) != production_names:
        errors.append(
            "$.allowed_runtime_packages: must exactly equal production package names"
        )

    knowledge = document.get("format_knowledge")
    if not isinstance(knowledge, dict):
        errors.append("$.format_knowledge: expected object")
    else:
        _exact_keys(
            knowledge,
            {"assertion_files", "reviewed_non_assertion_files"},
            "$.format_knowledge",
            errors,
        )
        seen_paths: set[str] = set()
        for category in ("assertion_files", "reviewed_non_assertion_files"):
            entries = knowledge.get(category)
            if not isinstance(entries, list) or (
                category == "assertion_files" and not entries
            ):
                errors.append(f"$.format_knowledge.{category}: invalid array")
                continue
            for index, entry in enumerate(entries):
                context = f"$.format_knowledge.{category}[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{context}: expected object")
                    continue
                keys = (
                    {"path", "sha256", "provenance_ids"}
                    if category == "assertion_files"
                    else {"path", "sha256", "reason"}
                )
                _exact_keys(entry, keys, context, errors)
                try:
                    path = _safe_path(entry.get("path"), f"{context}.path").as_posix()
                except ContractError as error:
                    errors.append(str(error))
                    path = ""
                if path in seen_paths:
                    errors.append(f"{context}.path: duplicate format inventory path")
                seen_paths.add(path)
                if not isinstance(entry.get("sha256"), str) or SHA256.fullmatch(
                    entry.get("sha256", "")
                ) is None:
                    errors.append(f"{context}.sha256: invalid SHA-256")
                if category == "assertion_files":
                    identifiers = _unique_strings(
                        entry.get("provenance_ids"),
                        f"{context}.provenance_ids",
                        errors,
                    )
                    if any(PROVENANCE_ID.fullmatch(identifier) is None for identifier in identifiers):
                        errors.append(f"{context}.provenance_ids: invalid provenance ID")
                elif not _nonempty(entry.get("reason")):
                    errors.append(f"{context}.reason: expected non-empty string")

    fixtures = document.get("fixtures")
    if not isinstance(fixtures, dict):
        errors.append("$.fixtures: expected object")
    else:
        _exact_keys(
            fixtures,
            {"repository_manifest", "seed_manifest", "external_observational"},
            "$.fixtures",
            errors,
        )
        for field in ("repository_manifest", "seed_manifest"):
            try:
                _safe_path(fixtures.get(field), f"$.fixtures.{field}")
            except ContractError as error:
                errors.append(str(error))
        external = fixtures.get("external_observational")
        if not isinstance(external, dict):
            errors.append("$.fixtures.external_observational: expected object")
        else:
            _exact_keys(
                external,
                {
                    "manifest",
                    "documentation",
                    "provenance",
                    "redistributable",
                    "regenerable",
                    "acceptance_fixture",
                },
                "$.fixtures.external_observational",
                errors,
            )
            for field in ("manifest", "documentation", "provenance"):
                try:
                    _safe_path(
                        external.get(field),
                        f"$.fixtures.external_observational.{field}",
                    )
                except ContractError as error:
                    errors.append(str(error))
            for field in ("redistributable", "regenerable", "acceptance_fixture"):
                if external.get(field) is not False:
                    errors.append(
                        f"$.fixtures.external_observational.{field}: must be false"
                    )
    return errors


def _toml(path: Path, context: str) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ContractError(f"{context}: invalid TOML: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{context}: expected TOML table")
    return document


def _production_source_files(root: Path, production: list[dict[str, Any]]) -> set[str]:
    sources: set[str] = set()
    for entry in production:
        manifest = root / entry["manifest"]
        source_root = manifest.parent / "src"
        for source in source_root.rglob("*.rs"):
            sources.add(source.relative_to(root).as_posix())
    return sources


def validate_workspace_and_sources(
    root: Path, document: dict[str, Any]
) -> tuple[list[str], set[str]]:
    """Validate workspace classification, unsafe lints, and runtime source boundaries."""
    errors: list[str] = []
    try:
        workspace_manifest = _toml(root / "Cargo.toml", "Cargo.toml")
    except ContractError as error:
        return [str(error)], set()
    workspace = workspace_manifest.get("workspace")
    if not isinstance(workspace, dict):
        return ["Cargo.toml: missing workspace table"], set()
    members = workspace.get("members")
    if not isinstance(members, list) or any(not _nonempty(item) for item in members):
        errors.append("Cargo.toml: workspace.members must be an explicit string array")
        members = []

    roles = document["workspace_packages"]
    classified = {
        entry["manifest"]
        for role in ("production", "support")
        for entry in roles[role]
    }
    expected_manifests = {f"{member}/Cargo.toml" for member in members}
    if classified != expected_manifests:
        errors.append(
            "workspace package classification mismatch; "
            f"missing={sorted(expected_manifests - classified)}, "
            f"unknown={sorted(classified - expected_manifests)}"
        )
    unsafe_lint = (
        workspace_manifest.get("workspace", {})
        .get("lints", {})
        .get("rust", {})
        .get("unsafe_code")
    )
    if unsafe_lint != "forbid":
        errors.append("Cargo.toml: workspace.lints.rust.unsafe_code must be 'forbid'")

    for role in ("production", "support"):
        for entry in roles[role]:
            context = entry["manifest"]
            try:
                manifest_path, _ = _resolve_file(root, context, context)
                manifest = _toml(manifest_path, context)
            except ContractError as error:
                errors.append(str(error))
                continue
            if manifest.get("package", {}).get("name") != entry["name"]:
                errors.append(f"{context}: package name does not match repository contract")
            if manifest.get("lints", {}).get("workspace") is not True:
                errors.append(f"{context}: package must inherit workspace lints")
            if (manifest_path.parent / "build.rs").exists():
                errors.append(f"{context}: production/support build.rs is forbidden")
            if manifest.get("package", {}).get("build") not in {None, False}:
                errors.append(f"{context}: custom package build script is forbidden")

    production = roles["production"]
    for entry in production:
        context = entry["crate_root"]
        try:
            crate_root, _ = _resolve_file(root, context, context)
            text = crate_root.read_text(encoding="utf-8")
        except (ContractError, OSError, UnicodeError) as error:
            errors.append(str(error))
            continue
        if "#![forbid(unsafe_code)]" not in text:
            errors.append(f"{context}: missing #![forbid(unsafe_code)]")

    source_files = _production_source_files(root, production)
    for relative in sorted(source_files):
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: cannot read production source: {error}")
            continue
        if PROCESS_EXECUTION.search(text):
            errors.append(
                f"{relative}: production source may not execute external runtime programs"
            )
        if FFI_OR_UNSAFE.search(text):
            errors.append(f"{relative}: FFI or unsafe production code is forbidden")
        if PROHIBITED_RUNTIME_TEXT.search(text):
            errors.append(
                f"{relative}: prohibited external database/Java runtime token"
            )
    return errors, source_files


def _cargo_metadata(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ContractError(
            "cargo metadata failed: " + (completed.stderr.strip() or "unknown error")
        )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ContractError("cargo metadata returned invalid JSON") from error
    if not isinstance(document, dict):
        raise ContractError("cargo metadata returned a non-object")
    return document


def validate_dependency_graph(
    root: Path, document: dict[str, Any], metadata: dict[str, Any]
) -> list[str]:
    """Validate the complete normal/build dependency closure of production crates."""
    errors: list[str] = []
    packages = metadata.get("packages")
    resolve = metadata.get("resolve")
    if not isinstance(packages, list) or not isinstance(resolve, dict):
        return ["cargo metadata: missing packages or resolve graph"]
    package_by_id = {
        package.get("id"): package
        for package in packages
        if isinstance(package, dict) and _nonempty(package.get("id"))
    }
    nodes = resolve.get("nodes")
    if not isinstance(nodes, list):
        return ["cargo metadata: resolve.nodes must be an array"]
    node_by_id = {
        node.get("id"): node
        for node in nodes
        if isinstance(node, dict) and _nonempty(node.get("id"))
    }

    start_ids: list[str] = []
    for entry in document["workspace_packages"]["production"]:
        expected_manifest = (root / entry["manifest"]).resolve()
        matches = [
            package_id
            for package_id, package in package_by_id.items()
            if package.get("name") == entry["name"]
            and Path(package.get("manifest_path", "")).resolve() == expected_manifest
        ]
        if len(matches) != 1:
            errors.append(
                f"cargo metadata: expected exactly one production package {entry['name']}"
            )
        else:
            start_ids.append(matches[0])

    closure: set[str] = set()
    pending = list(start_ids)
    while pending:
        package_id = pending.pop()
        if package_id in closure:
            continue
        closure.add(package_id)
        node = node_by_id.get(package_id)
        if not isinstance(node, dict):
            errors.append(f"cargo metadata: missing resolve node {package_id}")
            continue
        deps = node.get("deps")
        if not isinstance(deps, list):
            errors.append(f"cargo metadata: invalid dependencies for {package_id}")
            continue
        for dependency in deps:
            if not isinstance(dependency, dict):
                errors.append(f"cargo metadata: invalid dependency entry for {package_id}")
                continue
            kinds = dependency.get("dep_kinds")
            if not isinstance(kinds, list):
                errors.append(f"cargo metadata: dependency kinds missing for {package_id}")
                continue
            included = any(
                isinstance(kind, dict)
                and kind.get("kind") in {None, "normal", "build"}
                for kind in kinds
            )
            dependency_id = dependency.get("pkg")
            if included and _nonempty(dependency_id):
                pending.append(dependency_id)

    allowed = set(document["allowed_runtime_packages"])
    for package_id in sorted(closure):
        package = package_by_id.get(package_id)
        if not isinstance(package, dict):
            errors.append(f"cargo metadata: dependency package absent for {package_id}")
            continue
        name = package.get("name")
        if name not in allowed:
            errors.append(f"runtime dependency package is not allow-listed: {name}")
        if isinstance(name, str) and name.lower() in PROHIBITED_PACKAGE_NAMES:
            errors.append(f"prohibited runtime dependency package: {name}")
        if package.get("links") not in {None, ""}:
            errors.append(f"native-linked runtime dependency is forbidden: {name}")
        if package.get("source") is not None:
            errors.append(f"runtime dependency must be a reviewed workspace package: {name}")
        targets = package.get("targets")
        if not isinstance(targets, list):
            errors.append(f"cargo metadata: package targets missing for {name}")
        elif any(
            isinstance(target, dict)
            and "custom-build" in target.get("kind", [])
            for target in targets
        ):
            errors.append(f"custom-build target is forbidden in runtime closure: {name}")
    return errors


def _tracked_files(root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ContractError("git ls-files failed")
    try:
        return {
            item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        }
    except UnicodeDecodeError as error:
        raise ContractError("git returned a non-UTF-8 tracked path") from error


def _provenance_sections(text: str) -> dict[str, str]:
    matches = list(PROVENANCE_HEADING.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.start():end]
    return sections


def validate_format_knowledge(
    root: Path,
    document: dict[str, Any],
    source_files: set[str],
    provenance_text: str,
) -> list[str]:
    """Hash-bind format-bearing files and validate every cited ledger ID."""
    errors: list[str] = []
    knowledge = document["format_knowledge"]
    assertion_entries = knowledge["assertion_files"]
    reviewed_entries = knowledge["reviewed_non_assertion_files"]
    inventory = {
        entry["path"]: entry for entry in [*assertion_entries, *reviewed_entries]
    }
    if source_files != set(inventory):
        errors.append(
            "format-knowledge inventory mismatch; "
            f"missing={sorted(source_files - set(inventory))}, "
            f"stale={sorted(set(inventory) - source_files)}"
        )

    sections = _provenance_sections(provenance_text)
    for entry in [*assertion_entries, *reviewed_entries]:
        relative = entry["path"]
        try:
            path, _ = _resolve_file(root, relative, relative)
        except ContractError as error:
            errors.append(str(error))
            continue
        if _sha256(path) != entry["sha256"]:
            errors.append(f"{relative}: format-knowledge SHA-256 mismatch")
            continue
        if entry in assertion_entries:
            text = path.read_text(encoding="utf-8")
            for identifier in entry["provenance_ids"]:
                if identifier not in text:
                    errors.append(
                        f"{relative}: cited provenance ID {identifier} is absent "
                        "from source"
                    )

    for entry in assertion_entries:
        relative = entry["path"]
        for identifier in entry["provenance_ids"]:
            if identifier not in sections:
                errors.append(f"{relative}: unknown provenance ID {identifier}")
    return errors


def _fixture_candidates(tracked: set[str]) -> set[str]:
    candidates: set[str] = set()
    for raw in tracked:
        path = PurePosixPath(raw)
        if path.name in {"README.md", "manifest.json"}:
            continue
        if any(path.is_relative_to(root) for root in FIXTURE_ROOTS):
            candidates.add(raw)
    return candidates


def validate_repository_fixtures(
    root: Path,
    manifest: dict[str, Any],
    tracked: set[str],
    provenance_sections: dict[str, str],
    test_manifest: dict[str, Any],
) -> list[str]:
    """Validate checked generated, malformed, and regression fixture coverage."""
    errors: list[str] = []
    _exact_keys(manifest, {"schema_version", "fixtures"}, "fixture manifest", errors)
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
        _exact_keys(fixture, expected_keys, context, errors)
        identifier = fixture.get("id")
        if not isinstance(identifier, str) or re.fullmatch(r"FIX-[0-9]{4}", identifier) is None:
            errors.append(f"{context}.id: invalid fixture ID")
        elif identifier in seen_ids:
            errors.append(f"{context}.id: duplicate fixture ID")
        seen_ids.add(identifier)
        scenario = fixture.get("scenario_id")
        if not isinstance(scenario, str) or SCENARIO_ID.fullmatch(scenario) is None:
            errors.append(f"{context}.scenario_id: invalid scenario ID")
        provenance_id = fixture.get("provenance_id")
        if not isinstance(provenance_id, str) or PROVENANCE_ID.fullmatch(provenance_id) is None:
            errors.append(f"{context}.provenance_id: invalid provenance ID")
        elif provenance_id not in provenance_sections:
            errors.append(f"{context}.provenance_id: missing provenance entry")
        try:
            path, relative = _resolve_file(root, fixture.get("path"), f"{context}.path")
        except ContractError as error:
            errors.append(str(error))
            continue
        pure = PurePosixPath(relative)
        if not any(pure.is_relative_to(root_path) for root_path in FIXTURE_ROOTS):
            errors.append(f"{context}.path: fixture is outside checked fixture roots")
        if relative in seen_paths:
            errors.append(f"{context}.path: duplicate fixture path")
        seen_paths.add(relative)
        digest = fixture.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            errors.append(f"{context}.sha256: invalid SHA-256")
        elif _sha256(path) != digest:
            errors.append(f"{context}.sha256: hash mismatch")
        for field in (
            "origin",
            "generator",
            "environment",
            "license",
            "reproduction_command",
        ):
            if not _nonempty(fixture.get(field)):
                errors.append(f"{context}.{field}: expected non-empty string")
        indexed[relative] = digest if isinstance(digest, str) else ""

    candidates = _fixture_candidates(tracked)
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
                if indexed.get(path) != reference.get("sha256"):
                    errors.append(
                        f"test {case.get('id')} references an unmanifested fixture {path}"
                    )
    return errors


def validate_seed_manifest(
    root: Path, manifest: dict[str, Any], tracked: set[str]
) -> list[str]:
    """Validate every checked synthetic fuzz seed and its reproduction metadata."""
    errors: list[str] = []
    _exact_keys(
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
        _exact_keys(seed, expected_keys, context, errors)
        identifier = seed.get("id")
        if not isinstance(identifier, str) or FUZZ_ID.fullmatch(identifier) is None:
            errors.append(f"{context}.id: invalid fuzz scenario ID")
        elif identifier in seen_ids:
            errors.append(f"{context}.id: duplicate fuzz scenario ID")
        seen_ids.add(identifier)
        try:
            path, relative = _resolve_file(root, seed.get("path"), f"{context}.path")
        except ContractError as error:
            errors.append(str(error))
            continue
        if not PurePosixPath(relative).is_relative_to(PurePosixPath("fuzz/corpus")):
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
        elif _sha256(path) != digest:
            errors.append(f"{context}.sha256: hash mismatch")
        for field in ("purpose", "origin", "generator", "rights", "reproduction_command"):
            if not _nonempty(seed.get(field)):
                errors.append(f"{context}.{field}: expected non-empty string")
        environment = seed.get("environment")
        if not isinstance(environment, dict) or not environment:
            errors.append(f"{context}.environment: expected non-empty object")
        elif any(not _nonempty(key) or not _nonempty(value) for key, value in environment.items()):
            errors.append(f"{context}.environment: keys and values must be non-empty strings")

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
    _exact_keys(
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
        errors.append("external corpus must remain opt-in through JET3_EXTERNAL_FIXTURE_ROOT")
    if manifest.get("purpose") != "nonredistributable-read-only-corpus-verification":
        errors.append("external corpus purpose must remain nonredistributable and read-only")
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
        _exact_keys(fixture, {"id", "path", "size_bytes", "sha256"}, context, errors)
        identifier = fixture.get("id")
        if not isinstance(identifier, str) or re.fullmatch(r"FIX-[0-9]{4}", identifier) is None:
            errors.append(f"{context}.id: invalid fixture ID")
            continue
        if identifier in seen:
            errors.append(f"{context}.id: duplicate fixture ID")
        seen.add(identifier)
        fixture_ids.append(identifier)
        raw_path = fixture.get("path")
        try:
            normalized_path = _safe_path(raw_path, f"{context}.path").as_posix()
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
        if "not redistributable" not in lowered and "no redistribution grant" not in lowered:
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
            _exact_keys(
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
            if not isinstance(identifier, str) or re.fullmatch(
                r"CMP-[0-9]{4}", identifier
            ) is None:
                errors.append(f"{context}.id: invalid comparison ID")
            elif identifier in comparison_ids:
                errors.append(f"{context}.id: duplicate comparison ID")
            comparison_ids.append(identifier)
            left = comparison.get("left_fixture_id")
            right = comparison.get("right_fixture_id")
            if left not in fixture_sizes or right not in fixture_sizes or left == right:
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


def validate_repository(
    root: Path,
    *,
    metadata: dict[str, Any] | None = None,
    tracked: set[str] | None = None,
) -> list[str]:
    """Return all G0 repository-contract violations in deterministic order."""
    root = root.resolve()
    contract = _load_object(root / CONTRACT_PATH, "repository contract")
    errors = validate_contract_shape(contract)
    if errors:
        return sorted(errors)

    workspace_errors, source_files = validate_workspace_and_sources(root, contract)
    errors.extend(workspace_errors)
    if metadata is None:
        metadata = _cargo_metadata(root)
    errors.extend(validate_dependency_graph(root, contract, metadata))
    if tracked is None:
        tracked = _tracked_files(root)

    provenance_path, _ = _resolve_file(root, PROVENANCE_PATH.as_posix(), "provenance")
    provenance_text = provenance_path.read_text(encoding="utf-8")
    provenance_sections = _provenance_sections(provenance_text)
    errors.extend(
        validate_format_knowledge(
            root, contract, source_files, provenance_text
        )
    )

    fixture_path, _ = _resolve_file(
        root, contract["fixtures"]["repository_manifest"], "repository fixture manifest"
    )
    seed_path, _ = _resolve_file(
        root, contract["fixtures"]["seed_manifest"], "seed manifest"
    )
    test_path, _ = _resolve_file(root, "tests/manifest.json", "test manifest")
    errors.extend(
        validate_repository_fixtures(
            root,
            _load_object(fixture_path, "repository fixture manifest"),
            tracked,
            provenance_sections,
            _load_object(test_path, "test manifest"),
        )
    )
    errors.extend(
        validate_seed_manifest(
            root,
            _load_object(seed_path, "seed manifest"),
            tracked,
        )
    )

    external_policy = contract["fixtures"]["external_observational"]
    external_manifest_path, _ = _resolve_file(
        root, external_policy["manifest"], "external corpus manifest"
    )
    external_docs_path, _ = _resolve_file(
        root, external_policy["documentation"], "external corpus documentation"
    )
    errors.extend(
        validate_external_observational_corpus(
            _load_object(external_manifest_path, "external corpus manifest"),
            external_docs_path.read_text(encoding="utf-8"),
            provenance_sections,
            external_policy,
        )
    )

    support_path, _ = _resolve_file(
        root, SUPPORT_MATRIX_PATH.as_posix(), "support matrix"
    )
    support = _load_object(support_path, "support matrix")
    errors.extend(
        f"support matrix: {error}"
        for error in validate_support_matrix(support, root)
    )
    return sorted(set(errors))


def _parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parse_args(arguments)
    try:
        errors = validate_repository(args.repo_root)
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"repository-contract validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1
    print("repository-contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
