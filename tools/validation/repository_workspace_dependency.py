"""Workspace, production-source, and runtime-dependency validation."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .repository_common import ContractError, nonempty, resolve_file, sha256

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


def _is_custom_build(target: Any) -> bool:
    return (
        isinstance(target, dict)
        and isinstance(target.get("kind"), list)
        and "custom-build" in target["kind"]
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

    reviewed_scripts = {
        (entry["package"], entry["manifest"], entry["path"]): entry
        for entry in document["reviewed_build_scripts"]
    }
    actual_scripts: set[tuple[str, str, str]] = set()
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
            package_build = manifest.get("package", {}).get("build")
            candidates = set()
            default_build = manifest_path.parent / "build.rs"
            if default_build.exists():
                candidates.add(default_build)
            if isinstance(package_build, str):
                candidates.add(manifest_path.parent / package_build)
            elif package_build not in {None, False}:
                errors.append(f"{context}: invalid custom package build script")
            for candidate in candidates:
                try:
                    relative = candidate.relative_to(root).as_posix()
                except ValueError:
                    errors.append(f"{context}: custom package build script escapes repository")
                    continue
                identity = (entry["name"], entry["manifest"], relative)
                actual_scripts.add(identity)
                reviewed = reviewed_scripts.get(identity)
                if reviewed is None:
                    errors.append(
                        f"{context}: unreviewed production/support build script {relative}"
                    )
                    continue
                try:
                    script_path, _ = resolve_file(
                        root, relative, f"{context}: reviewed build script"
                    )
                except ContractError as error:
                    errors.append(str(error))
                    continue
                if sha256(script_path) != reviewed["sha256"]:
                    errors.append(f"{relative}: reviewed build script SHA-256 mismatch")

    for package, manifest, path in sorted(set(reviewed_scripts) - actual_scripts):
        errors.append(
            "reviewed build script is absent or package/manifest binding is stale: "
            f"{package} {manifest} {path}"
        )

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


def _cargo_lock_checksums(
    root: Path,
) -> tuple[dict[tuple[str, str, str], str], list[str]]:
    """Return exact external package identities and checksums from Cargo.lock."""
    try:
        document = _toml(root / "Cargo.lock", "Cargo.lock")
    except ContractError as error:
        return {}, [str(error)]
    packages = document.get("package")
    if not isinstance(packages, list):
        return {}, ["Cargo.lock: package must be an array"]
    checksums: dict[tuple[str, str, str], str] = {}
    errors: list[str] = []
    for index, package in enumerate(packages):
        if not isinstance(package, dict) or package.get("source") is None:
            continue
        name = package.get("name")
        version = package.get("version")
        source = package.get("source")
        checksum = package.get("checksum")
        if not all(nonempty(value) for value in (name, version, source, checksum)):
            errors.append(f"Cargo.lock package[{index}]: incomplete external identity")
            continue
        identity = (name, version, source)
        if identity in checksums:
            errors.append(
                "Cargo.lock: duplicate external package identity "
                f"{name} {version} {source}"
            )
            continue
        checksums[identity] = checksum
    return checksums, errors


def validate_dependency_graph(
    root: Path, document: dict[str, Any], metadata: dict[str, Any]
) -> list[str]:
    """Validate the complete normal/build dependency closure of production crates."""
    errors: list[str] = []
    packages = metadata.get("packages")
    resolve = metadata.get("resolve")
    if not isinstance(packages, list) or not isinstance(resolve, dict):
        return ["cargo metadata: missing packages or resolve graph"]
    package_by_id: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict) or not nonempty(package.get("id")):
            continue
        package_id = package["id"]
        if package_id in package_by_id:
            errors.append(f"cargo metadata: duplicate package id {package_id}")
        package_by_id[package_id] = package
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
    reviewed_scripts: dict[tuple[str, Path], dict[str, Any]] = {}
    for entry in document["reviewed_build_scripts"]:
        key = (entry["package"], (root / entry["manifest"]).resolve())
        if key in reviewed_scripts:
            errors.append(
                "duplicate reviewed build-script package/manifest binding: "
                f"{entry['package']} {entry['manifest']}"
            )
        reviewed_scripts[key] = entry
    reviewed_external: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in document["reviewed_external_runtime_packages"]:
        identity = (entry["name"], entry["version"], entry["source"])
        if identity in reviewed_external:
            errors.append(
                "duplicate reviewed external package: "
                f"{entry['name']} {entry['version']} {entry['source']}"
            )
        reviewed_external[identity] = entry
    actual_external: set[tuple[str, str, str]] = set()
    lock_checksums: dict[tuple[str, str, str], str] = {}
    if reviewed_external or any(
        isinstance(package_by_id.get(package_id), dict)
        and package_by_id[package_id].get("source") is not None
        for package_id in closure
    ):
        lock_checksums, lock_errors = _cargo_lock_checksums(root)
        errors.extend(lock_errors)

    for package_id in sorted(closure):
        package = package_by_id.get(package_id)
        if not isinstance(package, dict):
            errors.append(f"cargo metadata: dependency package absent for {package_id}")
            continue
        name = package.get("name")
        if isinstance(name, str) and name.lower() in PROHIBITED_PACKAGE_NAMES:
            errors.append(f"prohibited runtime dependency package: {name}")
        if package.get("links") not in {None, ""}:
            errors.append(f"native-linked runtime dependency is forbidden: {name}")
        targets = package.get("targets")
        if not isinstance(targets, list):
            errors.append(f"cargo metadata: package targets missing for {name}")
            has_custom_build = False
        else:
            has_custom_build = any(_is_custom_build(target) for target in targets)

        source = package.get("source")
        if source is None:
            if name not in allowed:
                errors.append(f"runtime dependency package is not allow-listed: {name}")
            manifest_value = package.get("manifest_path")
            if not nonempty(manifest_value):
                errors.append(f"cargo metadata: package manifest missing for {name}")
                manifest_path = None
            else:
                manifest_path = Path(manifest_value).resolve()
            reviewed_script = reviewed_scripts.get((name, manifest_path))
            if has_custom_build:
                custom_sources = {
                    Path(target["src_path"]).resolve()
                    for target in targets
                    if _is_custom_build(target) and nonempty(target.get("src_path"))
                }
                expected_source = (
                    (root / reviewed_script["path"]).resolve()
                    if reviewed_script is not None
                    else None
                )
                if reviewed_script is None or custom_sources != {expected_source}:
                    errors.append(
                        f"custom-build target is forbidden or mismatched in runtime closure: {name}"
                    )
            elif reviewed_script is not None:
                errors.append(
                    f"reviewed build script lacks custom-build target in metadata: {name}"
                )
            continue

        version = package.get("version")
        if not all(nonempty(value) for value in (name, version, source)):
            errors.append(f"cargo metadata: incomplete external package identity {package_id}")
            continue
        identity = (name, version, source)
        if identity in actual_external:
            errors.append(
                "cargo metadata: duplicate external package identity "
                f"{name} {version} {source}"
            )
        actual_external.add(identity)
        reviewed = reviewed_external.get(identity)
        if reviewed is None:
            errors.append(
                "runtime dependency package is not exactly reviewed: "
                f"{name} {version} {source}"
            )
            if any(entry["name"] == name for entry in reviewed_external.values()):
                errors.append(f"reviewed external package identity drift: {name}")
            if has_custom_build:
                errors.append(
                    f"custom-build target is forbidden in runtime closure: {name}"
                )
            continue

        expected_checksum = reviewed["checksum"]
        if lock_checksums.get(identity) != expected_checksum:
            errors.append(
                f"Cargo.lock checksum mismatch for reviewed external package: {name}"
            )
        metadata_checksum = package.get("checksum")
        if metadata_checksum is not None and metadata_checksum != expected_checksum:
            errors.append(
                f"cargo metadata checksum mismatch for reviewed external package: {name}"
            )
        allowed_custom_build = reviewed["allow_custom_build"]
        if has_custom_build and not allowed_custom_build:
            errors.append(
                f"custom-build target is not permitted for reviewed external package: {name}"
            )
        elif allowed_custom_build and not has_custom_build:
            errors.append(
                f"custom-build permission is stale for reviewed external package: {name}"
            )

    for name, version, source in sorted(set(reviewed_external) - actual_external):
        errors.append(
            "reviewed external package is absent from runtime closure: "
            f"{name} {version} {source}"
        )
    return errors
