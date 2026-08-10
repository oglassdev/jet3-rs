"""Clean-checkout and build-closure identity for fuzz evidence."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fuzz_evidence import EvidenceError, exact_process_environment, sha256, write_json

OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")
CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
BASE_ENVIRONMENT_KEYS = {
    "CARGO", "CARGO_HOME", "CARGO_INCREMENTAL", "CARGO_TARGET_DIR",
    "CARGO_TERM_COLOR", "LANG", "LC_ALL", "PATH", "RUSTC", "TMPDIR",
}
WINDOWS_ENVIRONMENT_KEYS = {"COMSPEC", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP"}


@dataclass(frozen=True)
class PreparedFuzzBuild:
    environment: dict[str, str]
    prebuild: dict[str, Any]
    manifest_path: Path
    metadata_path: Path
    target_directory: Path


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


def canonical_build_environment(
    cargo_path: str,
    cargo_fuzz_path: str,
    rustc_path: str,
    cargo_home: Path,
    target_directory: Path,
    temporary_directory: Path,
) -> dict[str, str]:
    process_temp = temporary_directory / "process-tmp"
    process_temp.mkdir()
    tool_directories = [
        str(Path(cargo_path).resolve().parent),
        str(Path(cargo_fuzz_path).resolve().parent),
        str(Path(rustc_path).resolve().parent),
    ]
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT")
        comspec = os.environ.get("COMSPEC")
        pathext = os.environ.get("PATHEXT")
        if not system_root or not comspec or not pathext:
            raise EvidenceError("Windows build environment lacks required system paths")
        tool_directories.extend([str(Path(system_root) / "System32"), system_root])
    else:
        tool_directories.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    path = os.pathsep.join(dict.fromkeys(tool_directories))
    environment = {
        "CARGO": str(Path(cargo_path).resolve()),
        "CARGO_HOME": str(cargo_home.resolve()),
        "CARGO_INCREMENTAL": "0",
        "CARGO_TARGET_DIR": str(target_directory.resolve()),
        "CARGO_TERM_COLOR": "never",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": path,
        "RUSTC": str(Path(rustc_path).resolve()),
        "TMPDIR": str(process_temp.resolve()),
    }
    if os.name == "nt":
        environment.update({
            "COMSPEC": comspec,
            "PATHEXT": pathext,
            "SYSTEMROOT": system_root,
            "TEMP": str(process_temp.resolve()),
            "TMP": str(process_temp.resolve()),
        })
    return exact_process_environment(environment)


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
    process_environment = exact_process_environment(environment)
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


def run_isolated_prebuild(
    root: Path,
    log_path: Path,
    command: list[str],
    environment: dict[str, str],
) -> dict[str, Any]:
    """Build outside runtime observation and retain the exact producer result."""
    process_environment = exact_process_environment(environment)
    with log_path.open("wb") as log:
        process = subprocess.run(
            command,
            cwd=root,
            env=process_environment,
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.flush()
        os.fsync(log.fileno())
    if process.returncode:
        diagnostic = log_path.read_text(encoding="utf-8", errors="replace").strip()
        if len(diagnostic) > 2000:
            diagnostic = diagnostic[-2000:]
        suffix = f": {diagnostic}" if diagnostic else ""
        raise EvidenceError(
            f"isolated fuzz prebuild failed with exit code {process.returncode}{suffix}"
        )
    return {
        "command": command,
        "log": {"path": log_path.name, "sha256": sha256(log_path)},
        "exit_code": process.returncode,
    }


def cargo_fuzz_command_prefix(
    cargo_fuzz_path: str,
    action: str,
    target_name: str,
    sanitizer: str,
    target_directory: Path | str,
) -> list[str]:
    """Construct the shared, canonical cargo-fuzz build/run command prefix."""
    if action not in {"build", "run"}:
        raise EvidenceError(f"unsupported cargo-fuzz action: {action}")
    return [
        cargo_fuzz_path,
        "fuzz",
        action,
        "--fuzz-dir",
        "fuzz",
        target_name,
        "--sanitizer",
        sanitizer,
        "--target-dir",
        str(target_directory),
    ]


def cargo_fuzz_runtime_command(
    cargo_fuzz_path: str,
    target_name: str,
    sanitizer: str,
    target_directory: str,
    corpus: Path | str,
    duration_seconds: int,
    deterministic_seed: int,
    max_len: int,
    peak_rss_limit_bytes: int,
) -> list[str]:
    """Construct the complete canonical cargo-fuzz runtime command."""
    return cargo_fuzz_command_prefix(
        cargo_fuzz_path,
        "run",
        target_name,
        sanitizer,
        target_directory,
    ) + [
        str(corpus),
        "--",
        f"-max_total_time={duration_seconds}",
        f"-seed={deterministic_seed}",
        f"-max_len={max_len}",
        f"-rss_limit_mb={peak_rss_limit_bytes // (1024 * 1024)}",
    ]


def validate_fuzz_runtime_command(
    command: Any,
    target_name: str,
    sanitizer: str,
    target_directory: str,
    duration_seconds: int,
    deterministic_seed: int,
    max_len: int,
    peak_rss_limit_bytes: int,
) -> None:
    """Require the exact canonical cargo-fuzz and libFuzzer runtime arguments."""
    if not isinstance(command, list) or not command or any(
        not isinstance(argument, str) or not argument for argument in command
    ):
        raise EvidenceError("observer command must be a non-empty string array")
    expected_prefix = cargo_fuzz_command_prefix(
        command[0],
        "run",
        target_name,
        sanitizer,
        target_directory,
    )
    if command[:len(expected_prefix)] != expected_prefix or "--" not in command:
        raise EvidenceError("observer command is not the canonical cargo-fuzz invocation")
    separator = command.index("--")
    if separator != len(expected_prefix) + 1:
        raise EvidenceError("observer command must name exactly one disposable corpus")
    expected_command = cargo_fuzz_runtime_command(
        command[0],
        target_name,
        sanitizer,
        target_directory,
        command[len(expected_prefix)],
        duration_seconds,
        deterministic_seed,
        max_len,
        peak_rss_limit_bytes,
    )
    if command != expected_command:
        raise EvidenceError(
            "observer command has stale or non-canonical libFuzzer limits"
        )


def locate_fuzz_executable(target_directory: Path, target_name: str) -> Path:
    """Resolve exactly one regular executable produced for a fuzz target."""
    executable_name = f"{target_name}.exe" if os.name == "nt" else target_name
    candidates = sorted(
        path.resolve()
        for path in target_directory.rglob(executable_name)
        if path.is_file() and not path.is_symlink()
    )
    if len(candidates) != 1:
        raise EvidenceError(
            "isolated fuzz prebuild must produce exactly one regular target "
            f"executable named {executable_name}; found {len(candidates)}"
        )
    executable = candidates[0]
    try:
        executable.relative_to(target_directory.resolve())
    except ValueError as error:
        raise EvidenceError("isolated fuzz executable escaped its target directory") from error
    return executable


def prebuild_fuzz_target(
    root: Path,
    temporary: Path,
    cargo_fuzz_path: str,
    target_name: str,
    sanitizer: str,
    target_directory: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Prebuild one target and bind retained evidence to its exact executable."""
    command = cargo_fuzz_command_prefix(
        cargo_fuzz_path,
        "build",
        target_name,
        sanitizer,
        environment["CARGO_TARGET_DIR"],
    )
    prebuild = run_isolated_prebuild(
        root,
        temporary / "prebuild.log",
        command,
        environment,
    )
    executable = locate_fuzz_executable(target_directory, target_name)
    prebuild["executable"] = {
        "path": str(executable),
        "sha256": sha256(executable),
    }
    return prebuild


def prepare_isolated_fuzz_build(
    root: Path,
    temporary: Path,
    snapshot: dict[str, Any],
    toolchain: dict[str, Any],
    target_name: str,
    sanitizer: str,
    cargo_home: Path,
) -> PreparedFuzzBuild:
    """Prepare and retain the complete isolated build closure for one campaign."""
    target_directory = temporary / "build-target"
    environment = canonical_build_environment(
        toolchain["cargo"]["path"],
        toolchain["cargo_fuzz"]["path"],
        toolchain["rustc"]["path"],
        cargo_home,
        target_directory,
        temporary,
    )
    metadata_path = temporary / "cargo-metadata.json"
    capture_cargo_metadata(
        root,
        metadata_path,
        toolchain["cargo"]["path"],
        environment,
    )
    if target_directory.exists():
        raise EvidenceError("Cargo metadata polluted the isolated build target")
    prebuild = prebuild_fuzz_target(
        root,
        temporary,
        toolchain["cargo_fuzz"]["path"],
        target_name,
        sanitizer,
        target_directory,
        environment,
    )
    manifest_path = temporary / "build.json"
    write_json(
        manifest_path,
        create_build_manifest(
            root,
            snapshot,
            metadata_path,
            environment,
            toolchain,
            prebuild,
        ),
    )
    return PreparedFuzzBuild(
        environment=environment,
        prebuild=prebuild,
        manifest_path=manifest_path,
        metadata_path=metadata_path,
        target_directory=target_directory,
    )


def create_build_manifest(
    root: Path,
    snapshot: dict[str, Any],
    metadata_path: Path,
    environment: dict[str, str],
    toolchain: dict[str, Any],
    prebuild: dict[str, Any],
) -> dict[str, Any]:
    lockfile = root / "fuzz/Cargo.lock"
    if not lockfile.is_file() or lockfile.is_symlink():
        raise EvidenceError("fuzz/Cargo.lock must be a regular non-symlink file")
    return {
        "schema_version": 2,
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
        "prebuild": prebuild,
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
        "cargo_configs", "cargo_metadata", "environment", "toolchain", "prebuild",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise EvidenceError("retained build manifest fields are incomplete or unknown")
    if manifest["schema_version"] != 2:
        raise EvidenceError("retained build manifest schema_version must be 2")
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
    expected_keys = BASE_ENVIRONMENT_KEYS | (
        WINDOWS_ENVIRONMENT_KEYS if os.name == "nt" else set()
    )
    if not isinstance(environment, dict) or set(environment) != expected_keys:
        raise EvidenceError("build manifest environment is not canonical")
    if any(not isinstance(value, str) or not value for value in environment.values()):
        raise EvidenceError("build manifest environment values must be non-empty text")
    if (
        environment["CARGO_INCREMENTAL"] != "0"
        or environment["CARGO_TERM_COLOR"] != "never"
        or environment["LANG"] != "C"
        or environment["LC_ALL"] != "C"
    ):
        raise EvidenceError("build manifest deterministic environment controls are stale")
    if Path(environment["CARGO"]).resolve() != Path(
        manifest["toolchain"]["cargo"]["path"]
    ).resolve() or Path(environment["RUSTC"]).resolve() != Path(
        manifest["toolchain"]["rustc"]["path"]
    ).resolve():
        raise EvidenceError("build manifest tool paths disagree with the environment")
    path_directories = [
        str(Path(manifest["toolchain"][name]["path"]).resolve().parent)
        for name in ("cargo", "cargo_fuzz", "rustc")
    ]
    if os.name == "nt":
        path_directories.extend([
            str(Path(environment["SYSTEMROOT"]) / "System32"),
            environment["SYSTEMROOT"],
        ])
    else:
        path_directories.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    if environment["PATH"] != os.pathsep.join(dict.fromkeys(path_directories)):
        raise EvidenceError("build manifest PATH is not the controlled tool path")
    if manifest["cargo_configs"] != cargo_configs(
        root, Path(environment["CARGO_HOME"])
    ):
        raise EvidenceError("build manifest Cargo configuration closure is stale")
    if environment != observer.get("build_environment"):
        raise EvidenceError("build manifest environment disagrees with observer")
    executable = Path(observer["executable"]["path"]).resolve()
    target_directory = Path(environment["CARGO_TARGET_DIR"]).resolve()
    if Path(environment["TMPDIR"]).resolve().parent != target_directory.parent:
        raise EvidenceError("build temporary directory is outside the isolated build root")
    try:
        executable.relative_to(target_directory)
    except ValueError as error:
        raise EvidenceError("executed fuzz binary was not built in the isolated target") from error
    prebuild = manifest["prebuild"]
    if not isinstance(prebuild, dict) or set(prebuild) != {
        "command", "log", "exit_code", "executable",
    }:
        raise EvidenceError("retained prebuild evidence is malformed")
    expected_command = cargo_fuzz_command_prefix(
        manifest["toolchain"]["cargo_fuzz"]["path"],
        "build",
        report["target"],
        report["campaign"]["sanitizer"],
        environment["CARGO_TARGET_DIR"],
    )
    if prebuild["command"] != expected_command:
        raise EvidenceError("retained prebuild command is not canonical")
    if prebuild["exit_code"] != 0 or isinstance(prebuild["exit_code"], bool):
        raise EvidenceError("retained prebuild did not exit successfully")
    log_ref = prebuild["log"]
    if not isinstance(log_ref, dict) or set(log_ref) != {"path", "sha256"}:
        raise EvidenceError("retained prebuild log reference is malformed")
    raw_log_path = log_ref["path"]
    if (
        not isinstance(raw_log_path, str)
        or not raw_log_path
        or Path(raw_log_path).parts != (raw_log_path,)
    ):
        raise EvidenceError("retained prebuild log path is not a single file name")
    prebuild_log = manifest_path.parent / raw_log_path
    if not prebuild_log.is_file() or prebuild_log.is_symlink():
        raise EvidenceError("retained prebuild log is missing, a symlink, or not regular")
    if log_ref["sha256"] != sha256(prebuild_log):
        raise EvidenceError("retained prebuild log hash is stale")
    prebuild_executable = prebuild["executable"]
    if not isinstance(prebuild_executable, dict) or set(prebuild_executable) != {
        "path", "sha256",
    }:
        raise EvidenceError("retained prebuild executable identity is malformed")
    if prebuild_executable != observer["executable"]:
        raise EvidenceError(
            "runtime executable identity disagrees with the isolated prebuild"
        )
    _validate_metadata(root, metadata_path)
