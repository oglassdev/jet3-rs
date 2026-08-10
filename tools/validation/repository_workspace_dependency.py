"""Workspace, production-source, and runtime-dependency validation."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .repository_common import ContractError, nonempty, resolve_file

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


def _toml(path: Path, context: str) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ContractError(f"{context}: invalid TOML: {error}") from error
    if not isinstance(document, dict):
        raise ContractError(f"{context}: expected TOML table")
    return document


def _production_source_files(
    root: Path, production: list[dict[str, Any]]
) -> set[str]:
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
    if not isinstance(members, list) or any(not nonempty(item) for item in members):
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
                manifest_path, _ = resolve_file(root, context, context)
                manifest = _toml(manifest_path, context)
            except ContractError as error:
                errors.append(str(error))
                continue
            if manifest.get("package", {}).get("name") != entry["name"]:
                errors.append(
                    f"{context}: package name does not match repository contract"
                )
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
            crate_root, _ = resolve_file(root, context, context)
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


def cargo_metadata(root: Path) -> dict[str, Any]:
    """Load the locked Cargo dependency graph."""
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
        if isinstance(package, dict) and nonempty(package.get("id"))
    }
    nodes = resolve.get("nodes")
    if not isinstance(nodes, list):
        return ["cargo metadata: resolve.nodes must be an array"]
    node_by_id = {
        node.get("id"): node
        for node in nodes
        if isinstance(node, dict) and nonempty(node.get("id"))
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
                errors.append(
                    f"cargo metadata: invalid dependency entry for {package_id}"
                )
                continue
            kinds = dependency.get("dep_kinds")
            if not isinstance(kinds, list):
                errors.append(
                    f"cargo metadata: dependency kinds missing for {package_id}"
                )
                continue
            included = any(
                isinstance(kind, dict)
                and kind.get("kind") in {None, "normal", "build"}
                for kind in kinds
            )
            dependency_id = dependency.get("pkg")
            if included and nonempty(dependency_id):
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
            errors.append(
                f"runtime dependency must be a reviewed workspace package: {name}"
            )
        targets = package.get("targets")
        if not isinstance(targets, list):
            errors.append(f"cargo metadata: package targets missing for {name}")
        elif any(
            isinstance(target, dict) and "custom-build" in target.get("kind", [])
            for target in targets
        ):
            errors.append(f"custom-build target is forbidden in runtime closure: {name}")
    return errors
