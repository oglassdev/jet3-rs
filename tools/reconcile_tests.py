#!/usr/bin/env python3
"""Reconcile the checked Rust test manifest with Cargo's runtime inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

CARGO_COMMAND = [
    "cargo",
    "test",
    "--workspace",
    "--all-targets",
    "--all-features",
    "--locked",
    "--",
    "--list",
]
TRACEABILITY_IDS = {
    "SCOPE-01",
    "SCOPE-02",
    "CLEAN-01",
    "API-01",
    "PHYS-01",
    "SCHEMA-01",
    "VALUE-01",
    "ROW-01",
    "LONG-01",
    "CRUD-01",
    "INDEX-01",
    "REL-01",
    "DET-01",
    "TXN-01",
    "SAFE-01",
    "VERIFY-01",
    "ORACLE-01",
    "TEST-01",
    "TOOL-01",
    "PERF-01",
    "CI-01",
    "RELEASE-01",
}
TOP_KEYS = {"schema_version", "cargo_command", "meaningful_case_count", "cases"}
CASE_KEYS = {
    "id",
    "target",
    "runtime_name",
    "traceability_ids",
    "purpose",
    "distinct_invariant",
    "fixtures",
    "expected_result",
    "ignored",
    "execution_status",
}
FIXTURE_KEYS = {"path", "sha256"}
TEST_ID = re.compile(r"^(?:UT|IT|PROP|GOLD|CORR|REG)-[A-Z0-9][A-Z0-9_-]*$")
TARGET = re.compile(r"^[a-z][a-z0-9_]*$")
RUNTIME_NAME = re.compile(r"^[A-Za-z0-9_]+(?:::[A-Za-z0-9_]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
RUNNING = re.compile(r"^\s*Running .+ \((?:.*[/\\])?([^/\\()]+)\)\s*$")
LISTED_TEST = re.compile(r"^(.+): test$")
DOC_TESTS = re.compile(r"^\s*Doc-tests\s+(\S+)\s*$")
HASH_SUFFIX = re.compile(r"^(.+)-[0-9a-f]{7,}$")


@dataclass(frozen=True, order=True)
class RuntimeTest:
    target: str
    name: str

    @property
    def display(self) -> str:
        return f"{self.target}::{self.name}"


def _target_name(executable: str) -> str:
    name = executable.removesuffix(".exe")
    matched = HASH_SUFFIX.fullmatch(name)
    return matched.group(1) if matched else name


def parse_cargo_list(output: str) -> set[RuntimeTest]:
    """Parse Cargo/libtest combined output into stable target/name identities."""
    tests: set[RuntimeTest] = set()
    current_target: str | None = None
    in_doc_tests = False
    for raw_line in output.splitlines():
        running = RUNNING.match(raw_line)
        if running:
            current_target = _target_name(running.group(1))
            in_doc_tests = False
            continue
        if DOC_TESTS.match(raw_line):
            current_target = None
            in_doc_tests = True
            continue
        listed = LISTED_TEST.fullmatch(raw_line)
        if not listed:
            continue
        if in_doc_tests:
            raise ValueError(
                "Doc-tests are outside the unit-test manifest and require "
                f"separate reconciliation: {listed.group(1)!r}"
            )
        if current_target is None:
            raise ValueError(f"listed test has no preceding Cargo target: {raw_line!r}")
        test = RuntimeTest(current_target, listed.group(1))
        if test in tests:
            raise ValueError(f"Cargo listed duplicate test {test.display}")
        tests.add(test)
    return tests


def _toolchain_channel(repo_root: Path) -> str:
    toolchain = (repo_root / "rust-toolchain.toml").read_text(encoding="utf-8")
    matched = re.search(r'^\s*channel\s*=\s*"([^"]+)"\s*$', toolchain, re.MULTILINE)
    if not matched:
        raise RuntimeError("rust-toolchain.toml does not declare a channel")
    return matched.group(1)


def _cargo_environment(repo_root: Path) -> dict[str, str]:
    channel = _toolchain_channel(repo_root)
    located = subprocess.run(
        ["rustup", "which", "--toolchain", channel, "rustc"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if located.returncode != 0:
        raise RuntimeError(f"cannot locate Rust {channel}: {located.stderr.strip()}")
    environment = os.environ.copy()
    toolchain_bin = str(Path(located.stdout.strip()).parent)
    environment["PATH"] = toolchain_bin + os.pathsep + environment.get("PATH", "")
    return environment


def _run_list(repo_root: Path, *, ignored_only: bool) -> set[RuntimeTest]:
    command = list(CARGO_COMMAND)
    if ignored_only:
        command.append("--ignored")
    completed = subprocess.run(
        command,
        cwd=repo_root,
        env=_cargo_environment(repo_root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed with {completed.returncode}:\n"
            f"{completed.stdout}"
        )
    return parse_cargo_list(completed.stdout)


def cargo_inventory(repo_root: Path) -> tuple[set[RuntimeTest], set[RuntimeTest]]:
    """Return all listed tests and the ignored subset."""
    all_tests = _run_list(repo_root, ignored_only=False)
    ignored = _run_list(repo_root, ignored_only=True)
    unknown_ignored = ignored - all_tests
    if unknown_ignored:
        names = ", ".join(test.display for test in sorted(unknown_ignored))
        raise RuntimeError(f"ignored listing contains tests absent from full listing: {names}")
    return all_tests, ignored


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_fixture(fixture: Any, repo_root: Path, location: str) -> list[str]:
    if not isinstance(fixture, dict):
        return [f"{location}: expected object"]
    errors = []
    if set(fixture) != FIXTURE_KEYS:
        errors.append(f"{location}: expected exactly path and sha256")
        return errors
    raw_path = fixture.get("path")
    digest = fixture.get("sha256")
    if not isinstance(raw_path, str) or not REPOSITORY_PATH.fullmatch(raw_path):
        errors.append(f"{location}.path: unsafe repository-relative path")
        return errors
    relative = PurePosixPath(raw_path)
    candidate = repo_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root.resolve(strict=True))
    except (FileNotFoundError, OSError):
        errors.append(f"{location}.path: fixture does not exist")
        return errors
    except ValueError:
        errors.append(f"{location}.path: fixture escapes repository")
        return errors
    if not resolved.is_file():
        errors.append(f"{location}.path: fixture must be a regular file")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append(f"{location}.sha256: expected lowercase SHA-256")
    elif resolved.is_file() and _sha256(resolved) != digest:
        errors.append(f"{location}.sha256: fixture hash mismatch")
    return errors


def _validate_case(case: Any, index: int, repo_root: Path) -> tuple[list[str], RuntimeTest | None]:
    location = f"$.cases[{index}]"
    if not isinstance(case, dict):
        return [f"{location}: expected object"], None
    errors = []
    if set(case) != CASE_KEYS:
        errors.append(
            f"{location}: invalid keys; missing={sorted(CASE_KEYS - set(case))}, "
            f"unknown={sorted(set(case) - CASE_KEYS)}"
        )
    test_id = case.get("id")
    if not isinstance(test_id, str) or not TEST_ID.fullmatch(test_id):
        errors.append(f"{location}.id: invalid stable test ID")
    target = case.get("target")
    name = case.get("runtime_name")
    if not isinstance(target, str) or not TARGET.fullmatch(target):
        errors.append(f"{location}.target: invalid Cargo target")
    if not isinstance(name, str) or not RUNTIME_NAME.fullmatch(name):
        errors.append(f"{location}.runtime_name: invalid libtest name")
    runtime = RuntimeTest(target, name) if isinstance(target, str) and isinstance(name, str) else None

    traceability = case.get("traceability_ids")
    if (
        not isinstance(traceability, list)
        or not traceability
        or len(set(traceability)) != len(traceability)
        or any(item not in TRACEABILITY_IDS for item in traceability)
    ):
        errors.append(f"{location}.traceability_ids: expected unique known IDs")
    for field in ("purpose", "distinct_invariant", "expected_result"):
        value = case.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{location}.{field}: expected non-empty string")
    ignored = case.get("ignored")
    if type(ignored) is not bool:
        errors.append(f"{location}.ignored: expected boolean")
    expected_status = "ignored" if ignored is True else "listed"
    if case.get("execution_status") != expected_status:
        errors.append(
            f"{location}.execution_status: expected {expected_status!r} for ignored={ignored!r}"
        )
    fixtures = case.get("fixtures")
    if not isinstance(fixtures, list):
        errors.append(f"{location}.fixtures: expected array")
    else:
        for fixture_index, fixture in enumerate(fixtures):
            errors.extend(
                _validate_fixture(
                    fixture, repo_root, f"{location}.fixtures[{fixture_index}]"
                )
            )
    return errors, runtime


def reconcile_document(
    document: Any,
    runtime_tests: set[RuntimeTest],
    ignored_tests: set[RuntimeTest],
    repo_root: Path,
) -> tuple[dict[str, int], list[str]]:
    """Validate a manifest and reconcile it with Cargo's unfiltered inventory."""
    if not isinstance(document, dict):
        return {}, ["$: expected object"]
    errors = []
    if set(document) != TOP_KEYS:
        errors.append(
            f"$: invalid keys; missing={sorted(TOP_KEYS - set(document))}, "
            f"unknown={sorted(set(document) - TOP_KEYS)}"
        )
    if document.get("schema_version") != 1:
        errors.append("$.schema_version: expected integer 1")
    if document.get("cargo_command") != CARGO_COMMAND:
        errors.append("$.cargo_command: command does not match the binding contract")
    cases = document.get("cases")
    if not isinstance(cases, list):
        return {}, errors + ["$.cases: expected array"]

    ids: dict[str, int] = {}
    runtimes: dict[RuntimeTest, int] = {}
    invariants: dict[str, int] = {}
    for index, case in enumerate(cases):
        case_errors, runtime = _validate_case(case, index, repo_root)
        errors.extend(case_errors)
        if not isinstance(case, dict):
            continue
        for value, seen, label in (
            (case.get("id"), ids, "test ID"),
            (case.get("distinct_invariant"), invariants, "distinct invariant"),
        ):
            if isinstance(value, str):
                if value in seen:
                    errors.append(
                        f"$.cases[{index}]: duplicate {label} {value!r}; "
                        f"first at index {seen[value]}"
                    )
                else:
                    seen[value] = index
        if runtime is not None:
            if runtime in runtimes:
                errors.append(
                    f"$.cases[{index}]: duplicate runtime test {runtime.display}; "
                    f"first at index {runtimes[runtime]}"
                )
            else:
                runtimes[runtime] = index
            actually_ignored = runtime in ignored_tests
            if case.get("ignored") is not actually_ignored:
                errors.append(
                    f"$.cases[{index}].ignored: manifest/runtime ignored state differs"
                )
    ordered_ids = [
        case.get("id") for case in cases if isinstance(case, dict)
    ]
    if ordered_ids != sorted(ordered_ids):
        errors.append("$.cases: entries must be sorted by stable test ID")

    manifested = set(runtimes)
    for test in sorted(runtime_tests - manifested):
        errors.append(f"runtime test missing from manifest: {test.display}")
    for test in sorted(manifested - runtime_tests):
        errors.append(f"stale manifest test absent at runtime: {test.display}")

    active_runtime = runtime_tests - ignored_tests
    meaningful = len(
        [
            case
            for case in cases
            if isinstance(case, dict) and case.get("ignored") is False
        ]
    )
    if document.get("meaningful_case_count") != meaningful:
        errors.append(
            "$.meaningful_case_count: does not equal manifested non-ignored cases"
        )
    if meaningful != len(active_runtime):
        errors.append(
            f"meaningful count {meaningful} does not equal active runtime count "
            f"{len(active_runtime)}"
        )
    summary = {
        "ignored": len(ignored_tests),
        "meaningful": meaningful,
        "runtime_total": len(runtime_tests),
    }
    return summary, errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/manifest.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.resolve()
    manifest_path = (
        args.manifest
        if args.manifest.is_absolute()
        else repo_root / args.manifest
    )
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        runtime, ignored = cargo_inventory(repo_root)
        summary, errors = reconcile_document(document, runtime, ignored, repo_root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"test reconciliation failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
