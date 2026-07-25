#!/usr/bin/env python3
"""Reconcile immutable Rust test inventory with a separate Cargo observation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from validation.common import (
    GIT_COMMIT,
    REPOSITORY_PATH,
    SHA256,
    git_dirty,
    git_head,
    load_traceability_registry,
    sha256_file,
)

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
TOP_KEYS = {"schema_version", "inventory_policy", "cases"}
POLICY_KEYS = {"include_ignored"}
CASE_KEYS = {
    "id",
    "target",
    "runtime_name",
    "traceability_ids",
    "purpose",
    "distinct_invariant",
    "fixtures",
    "expected_result",
}
OPTIONAL_CASE_KEYS = {"platforms"}
PLATFORMS = {"unix", "windows"}
CURRENT_PLATFORM = "windows" if os.name == "nt" else "unix"
FIXTURE_KEYS = {"path", "sha256"}
OBSERVATION_KEYS = {
    "schema_version",
    "git_commit",
    "dirty",
    "cargo_command",
    "tests",
    "counts",
}
OBSERVED_TEST_KEYS = {"target", "runtime_name", "ignored"}
COUNT_KEYS = {"runtime_total", "ignored", "meaningful"}
TEST_ID = re.compile(r"^(?:UT|IT|PROP|GOLD|CORR|REG)-[A-Z0-9][A-Z0-9_-]*$")
TARGET = re.compile(r"^[a-z][a-z0-9_]*$")
RUNTIME_NAME = re.compile(r"^[A-Za-z0-9_]+(?:::[A-Za-z0-9_]+)*$")
RUNNING = re.compile(r"^\s*Running .+ \((?:.*[/\\])?([^/\\()]+)\)\s*$")
LISTED_TEST = re.compile(r"^(.+): test$")
DOC_TESTS = re.compile(r"^\s*Doc-tests\s+(\S+)\s*$")
LIST_SUMMARY = re.compile(r"^\s*\d+ tests?, \d+ benchmarks?\s*$")
HASH_SUFFIX = re.compile(r"^(.+)-[0-9a-f]{7,}$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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


def _parse_merged_cargo_list(output: str) -> set[RuntimeTest]:
    """Parse legacy output whose producer has already merged both streams."""
    tests: set[RuntimeTest] = set()
    current_target: str | None = None
    in_doc_tests = False
    for raw_line in output.splitlines():
        raw_line = ANSI_ESCAPE.sub("", raw_line)
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
                "Doc-tests are outside the unit-test inventory and require "
                f"separate reconciliation: {listed.group(1)!r}"
            )
        if current_target is None:
            raise ValueError(f"listed test has no preceding Cargo target: {raw_line!r}")
        test = RuntimeTest(current_target, listed.group(1))
        if test in tests:
            raise ValueError(f"Cargo listed duplicate test {test.display}")
        tests.add(test)
    return tests


def _cargo_target_sequence(stderr: str) -> list[str | None]:
    """Extract Cargo's ordered executable/doctest sequence from stderr."""
    targets: list[str | None] = []
    for raw_line in stderr.splitlines():
        raw_line = ANSI_ESCAPE.sub("", raw_line)
        running = RUNNING.match(raw_line)
        if running:
            targets.append(_target_name(running.group(1)))
        elif DOC_TESTS.match(raw_line):
            targets.append(None)
    return targets


def _libtest_list_blocks(stdout: str) -> list[list[str]]:
    """Extract the ordered per-executable test-name blocks from stdout."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in stdout.splitlines():
        raw_line = ANSI_ESCAPE.sub("", raw_line)
        listed = LISTED_TEST.fullmatch(raw_line)
        if listed:
            current.append(listed.group(1))
        elif LIST_SUMMARY.fullmatch(raw_line):
            blocks.append(current)
            current = []
    if current:
        raise ValueError("Cargo test listing ended without a libtest summary")
    return blocks


def parse_cargo_list(stdout: str, stderr: str | None = None) -> set[RuntimeTest]:
    """Parse Cargo/libtest output without relying on cross-stream ordering.

    Cargo writes target headers to stderr while each libtest executable writes
    its listing to stdout. When the streams are supplied separately, target
    headers and complete listing blocks are paired by their stable order within
    each stream. This prevents buffering or a CI log collector from assigning a
    test to whichever target header happened to be observed most recently.

    Passing only ``stdout`` retains support for already-merged captured output.
    """
    if stderr is None:
        return _parse_merged_cargo_list(stdout)

    targets = _cargo_target_sequence(stderr)
    blocks = _libtest_list_blocks(stdout)
    if len(targets) != len(blocks):
        raise ValueError(
            "Cargo target/listing count mismatch: "
            f"{len(targets)} target headers, {len(blocks)} listing blocks"
        )

    tests: set[RuntimeTest] = set()
    for target, names in zip(targets, blocks, strict=True):
        if target is None:
            if names:
                raise ValueError(
                    "Doc-tests are outside the unit-test inventory and require "
                    f"separate reconciliation: {names[0]!r}"
                )
            continue
        for name in names:
            test = RuntimeTest(target, name)
            if test in tests:
                raise ValueError(f"Cargo listed duplicate test {test.display}")
            tests.add(test)
    return tests


def _toolchain_channel(repo_root: Path) -> str:
    text = (repo_root / "rust-toolchain.toml").read_text(encoding="utf-8")
    matched = re.search(r'^\s*channel\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
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
    environment["CARGO_TERM_COLOR"] = "never"
    environment["PATH"] = (
        str(Path(located.stdout.strip()).parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    return environment


def _run_list(repo_root: Path, *, ignored_only: bool) -> set[RuntimeTest]:
    command = [*CARGO_COMMAND, *(["--ignored"] if ignored_only else [])]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        env=_cargo_environment(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed with {completed.returncode}:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return parse_cargo_list(completed.stdout, completed.stderr)


def cargo_inventory(repo_root: Path) -> tuple[set[RuntimeTest], set[RuntimeTest]]:
    """Return all listed tests and the ignored subset."""
    all_tests = _run_list(repo_root, ignored_only=False)
    ignored = _run_list(repo_root, ignored_only=True)
    unknown = ignored - all_tests
    if unknown:
        names = ", ".join(test.display for test in sorted(unknown))
        raise RuntimeError(f"ignored tests absent from full listing: {names}")
    return all_tests, ignored


def build_runtime_observation(
    runtime_tests: set[RuntimeTest],
    ignored_tests: set[RuntimeTest],
    *,
    git_commit: str,
    dirty: bool,
) -> dict[str, Any]:
    """Build deterministic ephemeral observation data from Cargo listings."""
    tests = [
        {
            "target": test.target,
            "runtime_name": test.name,
            "ignored": test in ignored_tests,
        }
        for test in sorted(runtime_tests)
    ]
    return {
        "schema_version": 1,
        "git_commit": git_commit,
        "dirty": dirty,
        "cargo_command": list(CARGO_COMMAND),
        "tests": tests,
        "counts": {
            "runtime_total": len(runtime_tests),
            "ignored": len(ignored_tests),
            "meaningful": len(runtime_tests - ignored_tests),
        },
    }


def validate_runtime_observation(
    observation: Any,
) -> tuple[set[RuntimeTest], set[RuntimeTest], dict[str, int], list[str]]:
    """Validate observation shape and derive runtime/ignored sets."""
    if not isinstance(observation, dict):
        return set(), set(), {}, ["observation: expected object"]
    errors = []
    if set(observation) != OBSERVATION_KEYS:
        errors.append("observation: invalid top-level keys")
    if observation.get("schema_version") != 1:
        errors.append("observation.schema_version: expected integer 1")
    commit = observation.get("git_commit")
    if not isinstance(commit, str) or not GIT_COMMIT.fullmatch(commit):
        errors.append("observation.git_commit: expected full lowercase git commit")
    if type(observation.get("dirty")) is not bool:
        errors.append("observation.dirty: expected boolean")
    if observation.get("cargo_command") != CARGO_COMMAND:
        errors.append("observation.cargo_command: command does not match contract")
    tests = observation.get("tests")
    runtime: set[RuntimeTest] = set()
    ignored: set[RuntimeTest] = set()
    if not isinstance(tests, list):
        errors.append("observation.tests: expected array")
        tests = []
    for index, item in enumerate(tests):
        location = f"observation.tests[{index}]"
        if not isinstance(item, dict) or set(item) != OBSERVED_TEST_KEYS:
            errors.append(f"{location}: invalid observed-test shape")
            continue
        target, name, is_ignored = (
            item.get("target"),
            item.get("runtime_name"),
            item.get("ignored"),
        )
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            errors.append(f"{location}.target: invalid target")
            continue
        if not isinstance(name, str) or not RUNTIME_NAME.fullmatch(name):
            errors.append(f"{location}.runtime_name: invalid name")
            continue
        if type(is_ignored) is not bool:
            errors.append(f"{location}.ignored: expected boolean")
            continue
        test = RuntimeTest(target, name)
        if test in runtime:
            errors.append(f"{location}: duplicate runtime test {test.display}")
        runtime.add(test)
        if is_ignored:
            ignored.add(test)
    if isinstance(tests, list):
        identities = [
            (item.get("target"), item.get("runtime_name"))
            for item in tests
            if isinstance(item, dict)
        ]
        if identities != sorted(identities):
            errors.append("observation.tests: entries must be sorted")
    expected_counts = {
        "runtime_total": len(runtime),
        "ignored": len(ignored),
        "meaningful": len(runtime - ignored),
    }
    counts = observation.get("counts")
    if not isinstance(counts, dict) or set(counts) != COUNT_KEYS:
        errors.append("observation.counts: invalid shape")
    elif counts != expected_counts:
        errors.append("observation.counts: values do not match observed tests")
    return runtime, ignored, expected_counts, errors


def _validate_fixture(fixture: Any, repo_root: Path, location: str) -> list[str]:
    if not isinstance(fixture, dict) or set(fixture) != FIXTURE_KEYS:
        return [f"{location}: expected exactly path and sha256"]
    raw_path, digest = fixture.get("path"), fixture.get("sha256")
    if not isinstance(raw_path, str) or not REPOSITORY_PATH.fullmatch(raw_path):
        return [f"{location}.path: unsafe repository-relative path"]
    candidate = repo_root.joinpath(*PurePosixPath(raw_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root.resolve(strict=True))
    except (FileNotFoundError, OSError):
        return [f"{location}.path: fixture does not exist"]
    except ValueError:
        return [f"{location}.path: fixture escapes repository"]
    errors = [] if resolved.is_file() else [f"{location}.path: expected regular file"]
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append(f"{location}.sha256: expected lowercase SHA-256")
    elif resolved.is_file() and sha256_file(resolved) != digest:
        errors.append(f"{location}.sha256: fixture hash mismatch")
    return errors


def _validate_case(
    case: Any, index: int, repo_root: Path, traceability_ids: set[str]
) -> tuple[list[str], RuntimeTest | None, bool]:
    location = f"$.cases[{index}]"
    if not isinstance(case, dict):
        return [f"{location}: expected object"], None, False
    errors = []
    if not CASE_KEYS.issubset(case) or not set(case).issubset(
        CASE_KEYS | OPTIONAL_CASE_KEYS
    ):
        errors.append(
            f"{location}: invalid keys; missing={sorted(CASE_KEYS - set(case))}, "
            f"unknown={sorted(set(case) - CASE_KEYS - OPTIONAL_CASE_KEYS)}"
        )
    test_id, target, name = case.get("id"), case.get("target"), case.get("runtime_name")
    if not isinstance(test_id, str) or not TEST_ID.fullmatch(test_id):
        errors.append(f"{location}.id: invalid stable test ID")
    if not isinstance(target, str) or not TARGET.fullmatch(target):
        errors.append(f"{location}.target: invalid Cargo target")
    if not isinstance(name, str) or not RUNTIME_NAME.fullmatch(name):
        errors.append(f"{location}.runtime_name: invalid libtest name")
    runtime = RuntimeTest(target, name) if isinstance(target, str) and isinstance(name, str) else None
    platforms = case.get("platforms", sorted(PLATFORMS))
    if (
        not isinstance(platforms, list)
        or not platforms
        or platforms != sorted(set(platforms))
        or any(platform not in PLATFORMS for platform in platforms)
    ):
        errors.append(
            f"{location}.platforms: expected sorted unique subset of {sorted(PLATFORMS)}"
        )
        platforms = []
    links = case.get("traceability_ids")
    if (
        not isinstance(links, list)
        or not links
        or len(set(links)) != len(links)
        or any(item not in traceability_ids for item in links)
    ):
        errors.append(f"{location}.traceability_ids: expected unique registered IDs")
    for field in ("purpose", "distinct_invariant", "expected_result"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            errors.append(f"{location}.{field}: expected non-empty string")
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
    return errors, runtime, CURRENT_PLATFORM in platforms


def reconcile_document(
    document: Any,
    observation: Any,
    repo_root: Path,
    *,
    expected_commit: str,
    expected_dirty: bool,
) -> tuple[dict[str, int], list[str]]:
    """Reconcile immutable inventory against separately validated observation."""
    observed_runtime, ignored, summary, errors = validate_runtime_observation(
        observation
    )
    if isinstance(observation, dict):
        if observation.get("git_commit") != expected_commit:
            errors.append("observation.git_commit: does not match expected HEAD")
        if observation.get("dirty") is not expected_dirty:
            errors.append("observation.dirty: does not match expected worktree state")
    registered_ids, registry_errors = load_traceability_registry(repo_root)
    errors.extend(registry_errors)
    if not isinstance(document, dict):
        return summary, errors + ["$: expected object"]
    if set(document) != TOP_KEYS:
        errors.append("$: invalid inventory top-level keys")
    if document.get("schema_version") != 2:
        errors.append("$.schema_version: expected integer 2")
    policy = document.get("inventory_policy")
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        errors.append("$.inventory_policy: invalid shape")
    elif policy.get("include_ignored") is not True:
        errors.append("$.inventory_policy.include_ignored: must be true")
    cases = document.get("cases")
    if not isinstance(cases, list):
        return summary, errors + ["$.cases: expected array"]

    ids: dict[str, int] = {}
    runtimes: dict[RuntimeTest, int] = {}
    active_runtimes: set[RuntimeTest] = set()
    invariants: dict[str, int] = {}
    for index, case in enumerate(cases):
        case_errors, case_runtime, active_on_platform = _validate_case(
            case, index, repo_root, registered_ids
        )
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
        if case_runtime is not None:
            if case_runtime in runtimes:
                errors.append(
                    f"$.cases[{index}]: duplicate runtime test {case_runtime.display}"
                )
            else:
                runtimes[case_runtime] = index
            if active_on_platform:
                active_runtimes.add(case_runtime)
    ordered_ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if ordered_ids != sorted(ordered_ids):
        errors.append("$.cases: entries must be sorted by stable test ID")
    manifested = active_runtimes
    for test in sorted(observed_runtime - manifested):
        errors.append(f"runtime test missing from manifest: {test.display}")
    for test in sorted(manifested - observed_runtime):
        errors.append(f"stale manifest test absent at runtime: {test.display}")
    if len(manifested) != summary.get("runtime_total"):
        errors.append("inventory size does not equal runtime observation total")
    if ignored - manifested:
        errors.append("ignored runtime tests are excluded despite inventory policy")
    return summary, errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--manifest", type=Path, default=Path("tests/manifest.json"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else repo_root / args.manifest
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        runtime, ignored = cargo_inventory(repo_root)
        commit = git_head(repo_root)
        dirty = git_dirty(repo_root)
        if commit is None or dirty is None:
            raise RuntimeError("cannot determine exact git commit and dirty state")
        observation = build_runtime_observation(
            runtime, ignored, git_commit=commit, dirty=dirty
        )
        summary, errors = reconcile_document(
            document,
            observation,
            repo_root,
            expected_commit=commit,
            expected_dirty=dirty,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"test reconciliation failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    output = {"observation": observation, "reconciliation": summary}
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
