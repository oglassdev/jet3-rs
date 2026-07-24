#!/usr/bin/env python3
"""Create and verify commit-bound cross-platform G1 CI evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCHEMA_VERSION = 1
TOOLCHAIN = "1.96.0"
RUST_COMMIT = "ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96"
PLATFORMS = ("linux", "macos", "windows")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

COMMANDS = (
    ("format", ("rustup", "run", TOOLCHAIN, "cargo", "fmt", "--all", "--check")),
    (
        "clippy",
        (
            "rustup",
            "run",
            TOOLCHAIN,
            "cargo",
            "clippy",
            "--workspace",
            "--all-targets",
            "--all-features",
            "--locked",
            "--",
            "-D",
            "warnings",
        ),
    ),
    (
        "tests",
        (
            "rustup",
            "run",
            TOOLCHAIN,
            "cargo",
            "test",
            "--workspace",
            "--all-targets",
            "--all-features",
            "--locked",
        ),
    ),
    (
        "public-docs",
        (
            "rustup",
            "run",
            TOOLCHAIN,
            "cargo",
            "doc",
            "--workspace",
            "--all-features",
            "--no-deps",
            "--locked",
        ),
    ),
    ("source-size", ("ci-evidence", "check-source-size")),
    (
        "test-inventory-reconciliation",
        (
            "python",
            "tools/reconcile_tests.py",
            "--repo-root",
            ".",
        ),
    ),
    (
        "malformed-input-inventory",
        ("ci-evidence", "check-malformed-input-inventory"),
    ),
)


class EvidenceError(RuntimeError):
    """Evidence is absent, stale, inconsistent, or has been modified."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(content)
    except FileExistsError as error:
        raise EvidenceError(f"refusing to overwrite evidence file: {path}") from error


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def git_state(repo: Path) -> tuple[str, bool]:
    commit = _git(repo, "rev-parse", "HEAD")
    if not COMMIT.fullmatch(commit):
        raise EvidenceError("git returned an invalid commit identifier")
    dirty = bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
    return commit, dirty


def _pinned_environment(repo: Path) -> dict[str, str]:
    result = subprocess.run(
        ["rustup", "which", "--toolchain", TOOLCHAIN, "rustc"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceError(f"pinned Rust {TOOLCHAIN} is unavailable")
    environment = os.environ.copy()
    environment["PATH"] = (
        str(Path(result.stdout.strip()).parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    return environment


def _toolchain(repo: Path, environment: dict[str, str]) -> dict[str, str]:
    result = subprocess.run(
        ["rustup", "run", TOOLCHAIN, "rustc", "--version", "--verbose"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise EvidenceError(f"pinned Rust {TOOLCHAIN} is unavailable")
    fields = {}
    for line in result.stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    if fields.get("release") != TOOLCHAIN:
        raise EvidenceError(
            f"expected Rust {TOOLCHAIN}, observed {fields.get('release', 'unknown')}"
        )
    if fields.get("commit-hash") != RUST_COMMIT:
        raise EvidenceError(
            f"Rust {TOOLCHAIN} commit drifted: "
            f"{fields.get('commit-hash', 'unknown')}"
        )
    return {
        "channel": TOOLCHAIN,
        "release": fields["release"],
        "commit_hash": fields.get("commit-hash", ""),
        "host": fields.get("host", ""),
        "llvm_version": fields.get("LLVM version", ""),
    }


def _source_size_check(repo: Path) -> tuple[int, str]:
    failures = []
    checked = 0
    try:
        contract = json.loads(
            (repo / "docs/validation/repository-contract.json").read_text(
                encoding="utf-8"
            )
        )
        packages = contract["workspace_packages"]["production"]
        source_roots = {
            (repo / package["crate_root"]).parent.resolve() for package in packages
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        return 1, f"cannot derive production source roots from repository contract: {error}\n"
    repository = repo.resolve()
    if not source_roots or any(
        not source_root.is_relative_to(repository) or not source_root.is_dir()
        for source_root in source_roots
    ):
        return 1, "repository contract contains an invalid production source root\n"
    source_files = {
        path
        for source_root in source_roots
        for path in source_root.rglob("*.rs")
    }
    for path in sorted(source_files):
        checked += 1
        line_count = len(path.read_bytes().splitlines())
        if line_count > 800:
            failures.append(
                f"{path.relative_to(repository).as_posix()}: {line_count} lines"
            )
    output = f"checked {checked} production Rust source files; maximum is 800 lines\n"
    if failures:
        output += "\n".join(failures) + "\n"
        return 1, output
    return 0, output


def _malformed_inventory_check(repo: Path) -> tuple[int, str]:
    try:
        manifest = json.loads((repo / "tests/manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return 1, f"cannot read tests/manifest.json: {error}\n"
    tests = manifest.get("cases", [])
    matches = []
    for test in tests if isinstance(tests, list) else []:
        text = " ".join(
            str(test.get(field, ""))
            for field in ("purpose", "distinct_invariant", "expected_result")
        ).lower()
        traceability = test.get("traceability_ids", [])
        if (
            isinstance(traceability, list)
            and "SAFE-01" in traceability
            and any(term in text for term in ("malformed", "structured", "bounded", "panic"))
        ):
            matches.append(test.get("id"))
    if not matches:
        return 1, "no SAFE-01 malformed-input test evidence is inventoried\n"
    return 0, (
        f"found {len(matches)} SAFE-01 structured/bounded malformed-input tests "
        "in tests/manifest.json\n"
    )


def _run_command(
    repo: Path,
    command_id: str,
    argv: Sequence[str],
    pinned_environment: dict[str, str],
) -> tuple[int, bytes]:
    if command_id == "source-size":
        exit_code, output = _source_size_check(repo)
        return exit_code, output.encode()
    if command_id == "malformed-input-inventory":
        exit_code, output = _malformed_inventory_check(repo)
        return exit_code, output.encode()
    environment = pinned_environment.copy()
    if command_id == "public-docs":
        environment["RUSTDOCFLAGS"] = "-D warnings -D missing-docs"
    try:
        result = subprocess.run(
            argv,
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    except OSError as error:
        return 127, f"cannot execute {argv[0]}: {error}\n".encode()
    return result.returncode, result.stdout


def run_platform(repo: Path, output: Path, platform_name: str) -> int:
    if platform_name not in PLATFORMS:
        raise EvidenceError(f"unsupported platform: {platform_name}")
    commit, dirty = git_state(repo)
    environment = _pinned_environment(repo)
    toolchain = _toolchain(repo, environment)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for command_id, argv in COMMANDS:
        exit_code, log = _run_command(repo, command_id, argv, environment)
        log_name = f"logs/{command_id}.log"
        log_path = output / log_name
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(log)
        results.append(
            {
                "id": command_id,
                "argv": list(argv),
                "environment": (
                    {"RUSTDOCFLAGS": "-D warnings -D missing-docs"}
                    if command_id == "public-docs"
                    else {}
                ),
                "exit_code": exit_code,
                "log": log_name,
                "log_sha256": _sha256_bytes(log),
            }
        )
    record = {
        "schema_version": SCHEMA_VERSION,
        "commit": commit,
        "dirty": dirty,
        "platform": platform_name,
        "toolchain": toolchain,
        "commands": results,
        "success": not dirty and all(item["exit_code"] == 0 for item in results),
    }
    _write_new(output / "platform-record.json", _canonical_json(record))
    return 0 if record["success"] else 1


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def validate_platform_record(path: Path, expected_commit: str | None = None) -> dict[str, object]:
    record = _load_json(path)
    required = {
        "schema_version",
        "commit",
        "dirty",
        "platform",
        "toolchain",
        "commands",
        "success",
    }
    if set(record) != required:
        raise EvidenceError(f"{path}: platform record fields drifted")
    if record["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError(f"{path}: unsupported schema version")
    commit = record["commit"]
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise EvidenceError(f"{path}: invalid commit")
    if expected_commit is not None and commit != expected_commit:
        raise EvidenceError(f"{path}: stale commit {commit}; expected {expected_commit}")
    if record["dirty"] is not False:
        raise EvidenceError(f"{path}: dirty CI runs cannot satisfy G1")
    if record["platform"] not in PLATFORMS:
        raise EvidenceError(f"{path}: invalid platform")
    toolchain = record["toolchain"]
    if not isinstance(toolchain, dict):
        raise EvidenceError(f"{path}: invalid toolchain record")
    metadata_values = tuple(
        toolchain.get(key) for key in ("commit_hash", "host", "llvm_version")
    )
    if (
        toolchain.get("channel") != TOOLCHAIN
        or toolchain.get("release") != TOOLCHAIN
        or toolchain.get("commit_hash") != RUST_COMMIT
        or not all(isinstance(value, str) and value for value in metadata_values)
    ):
        raise EvidenceError(f"{path}: toolchain is not pinned to Rust {TOOLCHAIN}")
    host = str(toolchain["host"])
    host_matches_platform = {
        "linux": "linux" in host,
        "macos": "apple-darwin" in host,
        "windows": "windows" in host,
    }
    if not host_matches_platform[str(record["platform"])]:
        raise EvidenceError(
            f"{path}: rustc host {host!r} does not match platform "
            f"{record['platform']!r}"
        )
    commands = record["commands"]
    if not isinstance(commands, list) or len(commands) != len(COMMANDS):
        raise EvidenceError(f"{path}: incomplete command inventory")
    for actual, (expected_id, expected_argv) in zip(commands, COMMANDS):
        if not isinstance(actual, dict) or set(actual) != {
            "id", "argv", "environment", "exit_code", "log", "log_sha256"
        }:
            raise EvidenceError(f"{path}: command record fields drifted")
        if actual["id"] != expected_id or actual["argv"] != list(expected_argv):
            raise EvidenceError(f"{path}: command inventory drift at {expected_id}")
        expected_environment = (
            {"RUSTDOCFLAGS": "-D warnings -D missing-docs"}
            if expected_id == "public-docs"
            else {}
        )
        if actual["environment"] != expected_environment:
            raise EvidenceError(f"{path}: command environment drift at {expected_id}")
        if actual["exit_code"] != 0:
            raise EvidenceError(f"{path}: {expected_id} did not pass")
        log = actual["log"]
        digest = actual["log_sha256"]
        if (
            not isinstance(log, str)
            or log != f"logs/{expected_id}.log"
            or Path(log).is_absolute()
            or ".." in Path(log).parts
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
        ):
            raise EvidenceError(f"{path}: invalid log metadata for {expected_id}")
        log_path = path.parent / log
        if not log_path.is_file() or _sha256_file(log_path) != digest:
            raise EvidenceError(f"{path}: log hash mismatch for {expected_id}")
    if record["success"] is not True:
        raise EvidenceError(f"{path}: record does not report success")
    return record


def aggregate(input_root: Path, output: Path, expected_commit: str) -> None:
    if not COMMIT.fullmatch(expected_commit):
        raise EvidenceError("expected commit must be a full lowercase git SHA")
    candidates = sorted(input_root.rglob("platform-record.json"))
    records: dict[str, tuple[Path, dict[str, object]]] = {}
    for candidate in candidates:
        record = validate_platform_record(candidate, expected_commit)
        platform_name = str(record["platform"])
        if platform_name in records:
            raise EvidenceError(f"duplicate platform record: {platform_name}")
        records[platform_name] = (candidate, record)
    missing = sorted(set(PLATFORMS) - set(records))
    if missing:
        raise EvidenceError(f"missing required platform records: {', '.join(missing)}")
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for platform_name in PLATFORMS:
        source, _ = records[platform_name]
        destination = output / platform_name
        shutil.copytree(source.parent, destination)
        record_path = destination / "platform-record.json"
        entries.append(
            {
                "platform": platform_name,
                "record": f"{platform_name}/platform-record.json",
                "record_sha256": _sha256_file(record_path),
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "commit": expected_commit,
        "required_platforms": list(PLATFORMS),
        "records": entries,
    }
    _write_new(output / "aggregate.json", _canonical_json(manifest))


def verify_aggregate(path: Path, expected_commit: str) -> None:
    manifest_path = path / "aggregate.json" if path.is_dir() else path
    manifest = _load_json(manifest_path)
    if set(manifest) != {
        "schema_version", "commit", "required_platforms", "records"
    }:
        raise EvidenceError("aggregate manifest fields drifted")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("unsupported aggregate schema version")
    if manifest["commit"] != expected_commit:
        raise EvidenceError(
            f"stale aggregate commit {manifest['commit']}; expected {expected_commit}"
        )
    if manifest["required_platforms"] != list(PLATFORMS):
        raise EvidenceError("aggregate required-platform inventory drifted")
    entries = manifest["records"]
    if not isinstance(entries, list) or len(entries) != len(PLATFORMS):
        raise EvidenceError("aggregate has incomplete platform inventory")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "platform", "record", "record_sha256"
        }:
            raise EvidenceError("aggregate record entry fields drifted")
        platform_name = entry["platform"]
        if platform_name in seen:
            raise EvidenceError(f"duplicate aggregate platform: {platform_name}")
        seen.add(platform_name)
        if platform_name not in PLATFORMS:
            raise EvidenceError(f"unknown aggregate platform: {platform_name}")
        relative = entry["record"]
        digest = entry["record_sha256"]
        if (
            not isinstance(relative, str)
            or relative != f"{platform_name}/platform-record.json"
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
        ):
            raise EvidenceError("invalid aggregate record metadata")
        record_path = manifest_path.parent / relative
        if not record_path.is_file() or _sha256_file(record_path) != digest:
            raise EvidenceError(f"platform record hash mismatch: {platform_name}")
        record = validate_platform_record(record_path, expected_commit)
        if record["platform"] != platform_name:
            raise EvidenceError("aggregate platform does not match platform record")
    if seen != set(PLATFORMS):
        raise EvidenceError("aggregate is missing a required platform")


def _default_platform() -> str:
    system = host_platform.system().lower()
    return {"darwin": "macos"}.get(system, system)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-platform")
    run_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--platform", choices=PLATFORMS, default=_default_platform())
    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--input-root", type=Path, required=True)
    aggregate_parser.add_argument("--output", type=Path, required=True)
    aggregate_parser.add_argument("--expected-commit", required=True)
    verify_parser = subparsers.add_parser("verify-aggregate")
    verify_parser.add_argument("path", type=Path)
    verify_parser.add_argument("--expected-commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "run-platform":
            return run_platform(
                args.repo_root.resolve(), args.output.resolve(), args.platform
            )
        if args.command == "aggregate":
            aggregate(args.input_root.resolve(), args.output.resolve(), args.expected_commit)
        else:
            verify_aggregate(args.path.resolve(), args.expected_commit)
    except EvidenceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
