"""Clean-checkout and build-closure identity for fuzz evidence."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from fuzz_evidence import EvidenceError, sanitized_process_environment, sha256

OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")
CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")


def git_output(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise EvidenceError(f"git {' '.join(args)} failed: {process.stderr.strip()}")
    return process.stdout.strip()


def require_clean_snapshot(root: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if status.returncode:
        raise EvidenceError(f"git status failed: {status.stderr.decode(errors='replace').strip()}")
    if status.stdout:
        raise EvidenceError(
            "fuzz campaign evidence requires a completely clean Git checkout"
        )
    return {
        "sha": git_output(root, "rev-parse", "HEAD"),
        "tree": git_output(root, "rev-parse", "HEAD^{tree}"),
        "clean": True,
    }


def require_external_output(root: Path, output: Path) -> Path:
    resolved = output.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return resolved
    raise EvidenceError("fuzz evidence output must be outside the Git checkout")


def copy_seeds(
    root: Path,
    corpus: Path,
    manifest: dict[str, Any],
    target_name: str,
) -> list[dict[str, str]]:
    corpus.mkdir()
    selected = [
        seed for seed in manifest["seeds"]
        if Path(seed["path"]).parts[-2] == target_name
    ]
    for seed in selected:
        shutil.copyfile(root / seed["path"], corpus / Path(seed["path"]).name)
    return [
        {"id": seed["id"], "path": seed["path"], "sha256": seed["sha256"]}
        for seed in selected
    ]


def tracked_files(root: Path) -> list[dict[str, str]]:
    process = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise EvidenceError(
            f"git ls-files failed: {process.stderr.decode(errors='replace').strip()}"
        )
    entries: list[dict[str, str]] = []
    for raw in process.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise EvidenceError("Git index contains an unrepresentable entry") from error
        if stage != "0" or not OBJECT_RE.fullmatch(object_id):
            raise EvidenceError("Git index contains an unresolved or malformed entry")
        if mode == "160000":
            raise EvidenceError("fuzz build closure does not permit Git submodules")
        entries.append({"path": path, "mode": mode, "object": object_id})
    if not entries:
        raise EvidenceError("Git index inventory is empty")
    return entries


def cargo_configs(root: Path, cargo_home: Path | None = None) -> list[dict[str, str]]:
    candidates: set[Path] = set()
    for directory in (root, *root.parents):
        candidates.add(directory / ".cargo/config.toml")
        candidates.add(directory / ".cargo/config")
    resolved_cargo_home = (
        cargo_home
        if cargo_home is not None
        else Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    ).resolve()
    candidates.add(resolved_cargo_home / "config.toml")
    candidates.add(resolved_cargo_home / "config")
    configs: list[dict[str, str]] = []
    for path in sorted(candidates):
        if path.is_symlink():
            raise EvidenceError(f"Cargo configuration may not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise EvidenceError(f"Cargo configuration is not a regular file: {path}")
        if path.is_file():
            configs.append({"path": str(path.resolve()), "sha256": sha256(path)})
    return configs


def capture_cargo_metadata(
    root: Path,
    output: Path,
    cargo_path: str,
    environment: dict[str, str],
) -> None:
    process_environment = sanitized_process_environment(environment)
    process = subprocess.run(
        [
            cargo_path,
            "metadata",
            "--format-version=1",
            "--locked",
            "--manifest-path",
            "fuzz/Cargo.toml",
        ],
        cwd=root,
        env=process_environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise EvidenceError(
            f"cargo metadata failed: {process.stderr.decode(errors='replace').strip()}"
        )
    try:
        json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise EvidenceError("cargo metadata did not produce valid JSON") from error
    output.write_bytes(process.stdout)
    with output.open("rb") as written:
        os.fsync(written.fileno())


def create_build_manifest(
    root: Path,
    snapshot: dict[str, Any],
    metadata_path: Path,
    environment: dict[str, str],
    toolchain: dict[str, Any],
) -> dict[str, Any]:
    lockfile = root / "fuzz/Cargo.lock"
    if not lockfile.is_file() or lockfile.is_symlink():
        raise EvidenceError("fuzz/Cargo.lock must be a regular non-symlink file")
    return {
        "schema_version": 1,
        "commit": snapshot,
        "tracked_files": tracked_files(root),
        "cargo_lock_sha256": sha256(lockfile),
        "cargo_configs": cargo_configs(root, Path(environment["CARGO_HOME"])),
        "cargo_metadata": {
            "path": metadata_path.name,
            "sha256": sha256(metadata_path),
        },
        "environment": environment,
        "toolchain": toolchain,
    }


def _validate_metadata(root: Path, path: Path) -> None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"retained Cargo metadata is invalid: {error}") from error
    if not isinstance(metadata, dict) or not isinstance(metadata.get("packages"), list):
        raise EvidenceError("retained Cargo metadata has no package closure")
    packages = metadata["packages"]
    if not packages or not isinstance(metadata.get("resolve"), dict):
        raise EvidenceError("retained Cargo metadata has an empty dependency closure")
    nodes = metadata["resolve"].get("nodes")
    if not isinstance(nodes, list):
        raise EvidenceError("retained Cargo metadata has no resolved dependency nodes")
    package_ids = {package.get("id") for package in packages if isinstance(package, dict)}
    node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
    if None in package_ids or None in node_ids or package_ids != node_ids:
        raise EvidenceError("retained Cargo metadata package and resolve closures disagree")
    try:
        lock = tomllib.loads((root / "fuzz/Cargo.lock").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise EvidenceError(f"fuzz Cargo lockfile is invalid: {error}") from error
    locked_packages = lock.get("package", [])
    if not isinstance(locked_packages, list):
        raise EvidenceError("fuzz Cargo lockfile package closure is malformed")
    tracked = {entry["path"] for entry in tracked_files(root)}
    for package in packages:
        if not isinstance(package, dict):
            raise EvidenceError("retained Cargo metadata package is malformed")
        source = package.get("source")
        manifest = package.get("manifest_path")
        if not isinstance(manifest, str):
            raise EvidenceError("retained Cargo metadata package has no manifest path")
        if source is None:
            try:
                relative = Path(manifest).resolve().relative_to(root.resolve()).as_posix()
            except ValueError as error:
                raise EvidenceError("path dependency escapes the clean checkout") from error
            if relative not in tracked:
                raise EvidenceError("path dependency manifest is not in the clean Git tree")
        else:
            matches = [
                locked for locked in locked_packages
                if isinstance(locked, dict)
                and locked.get("name") == package.get("name")
                and locked.get("version") == package.get("version")
                and locked.get("source") == source
            ]
            if len(matches) != 1:
                raise EvidenceError("external Cargo dependency is absent from the lockfile")
            if source.startswith("registry+") and (
                not isinstance(matches[0].get("checksum"), str)
                or not CHECKSUM_RE.fullmatch(matches[0]["checksum"])
            ):
                raise EvidenceError("registry Cargo dependency lacks a locked checksum")
            if source.startswith("git+") and not re.search(r"#[0-9a-f]{40}$", source):
                raise EvidenceError("Git Cargo dependency source is not commit-pinned")
            if not source.startswith(("registry+", "git+")):
                raise EvidenceError("external Cargo dependency source is unsupported")


def validate_build_manifest(
    root: Path,
    manifest_path: Path,
    metadata_path: Path,
    report: dict[str, Any],
    observer: dict[str, Any],
) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"retained build manifest is invalid: {error}") from error
    required = {
        "schema_version", "commit", "tracked_files", "cargo_lock_sha256",
        "cargo_configs", "cargo_metadata", "environment", "toolchain",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise EvidenceError("retained build manifest fields are incomplete or unknown")
    if manifest["schema_version"] != 1:
        raise EvidenceError("retained build manifest schema_version must be 1")
    snapshot = require_clean_snapshot(root)
    if manifest["commit"] != snapshot or report["commit"] != snapshot:
        raise EvidenceError("build manifest is not bound to the current clean commit")
    if manifest["tracked_files"] != tracked_files(root):
        raise EvidenceError("build manifest Git index inventory is stale")
    if manifest["cargo_lock_sha256"] != sha256(root / "fuzz/Cargo.lock"):
        raise EvidenceError("build manifest Cargo lock hash is stale")
    metadata_ref = manifest["cargo_metadata"]
    if not isinstance(metadata_ref, dict) or set(metadata_ref) != {"path", "sha256"}:
        raise EvidenceError("build manifest Cargo metadata reference is malformed")
    if metadata_ref["path"] != metadata_path.name or metadata_ref["sha256"] != sha256(
        metadata_path
    ):
        raise EvidenceError("build manifest Cargo metadata hash is stale")
    if manifest["toolchain"] != observer["toolchain"]:
        raise EvidenceError("build manifest toolchain disagrees with observer")
    environment = manifest["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "CARGO", "CARGO_HOME", "CARGO_INCREMENTAL", "CARGO_TARGET_DIR", "PATH",
        "RUSTC",
    }:
        raise EvidenceError("build manifest environment is not canonical")
    if any(not isinstance(value, str) or not value for value in environment.values()):
        raise EvidenceError("build manifest environment values must be non-empty text")
    if environment["CARGO_INCREMENTAL"] != "0":
        raise EvidenceError("build manifest must disable Cargo incremental compilation")
    if manifest["cargo_configs"] != cargo_configs(
        root, Path(environment["CARGO_HOME"])
    ):
        raise EvidenceError("build manifest Cargo configuration closure is stale")
    if environment != observer.get("build_environment"):
        raise EvidenceError("build manifest environment disagrees with observer")
    executable = Path(observer["executable"]["path"]).resolve()
    target_directory = Path(environment["CARGO_TARGET_DIR"]).resolve()
    try:
        executable.relative_to(target_directory)
    except ValueError as error:
        raise EvidenceError("executed fuzz binary was not built in the isolated target") from error
    _validate_metadata(root, metadata_path)
