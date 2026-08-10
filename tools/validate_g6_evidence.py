#!/usr/bin/env python3
"""Fail-closed validation for commit-bound G6 coverage and mutation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_INVENTORY = Path("docs/validation/g6/core-modules.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SAFE_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
CLASSIFICATIONS = {"format", "safety", "format_safety"}
MUTATION_SCOPES = {"encoding_decoding", "allocation", "row_packing", "index"}
SURVIVOR_STATUSES = {"survived", "timeout"}
DISPOSITION_STATUSES = SURVIVOR_STATUSES | {"equivalent", "unreachable"}
MUTATION_PRODUCER_FORMAT = "cargo-mutants-outcomes-v26-json"
NATIVE_MUTATION_SUMMARIES = {
    "CaughtMutant": "killed",
    "MissedMutant": "survived",
    "Timeout": "timeout",
    "Unviable": "unviable",
}
CARGO_MUTANTS_V26 = re.compile(r"^26[.][0-9]+[.][0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class EvidenceError(ValueError):
    """Evidence is malformed, stale, incomplete, or below the G6 contract."""


@dataclass(frozen=True)
class CoreModule:
    path: str
    classification: str
    sha256: str


@dataclass(frozen=True)
class NativeMutant:
    mutant_id: str
    path: str
    line: int
    status: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path, description: str) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"cannot load {description}: {error}") from error


def _keys(value: dict[str, Any], expected: set[str], location: str) -> None:
    if set(value) != expected:
        raise EvidenceError(
            f"{location}: invalid keys; missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{location}: expected non-empty string")
    return value


def repo_path(value: Any, location: str) -> str:
    """Normalise a repository-relative path, rejecting absolute or escaping values."""
    if not isinstance(value, str) or SAFE_PATH.fullmatch(value) is None:
        raise EvidenceError(f"{location}: unsafe repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidenceError(f"{location}: unsafe repository-relative path")
    return path.as_posix()


def _regular_file(root: Path, relative: str, location: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise EvidenceError(f"{location}: missing or escaping file {relative}") from error
    if not resolved.is_file() or candidate.is_symlink():
        raise EvidenceError(f"{location}: must be a regular non-symlink file")
    return resolved


def load_inventory(root: Path, inventory_path: Path) -> list[CoreModule]:
    """Load, hash-check, and prove completeness of the checked core inventory."""
    root = root.resolve()
    inventory_path = inventory_path.resolve()
    document = _load(inventory_path, "core-module inventory")
    if not isinstance(document, dict):
        raise EvidenceError("inventory: expected object")
    _keys(document, {"schema_version", "source_root", "modules"}, "inventory")
    if document["schema_version"] != 1:
        raise EvidenceError("inventory.schema_version: expected integer 1")
    source_root = repo_path(document["source_root"], "inventory.source_root")
    if source_root != "crates/jet3/src":
        raise EvidenceError("inventory.source_root: must be crates/jet3/src")
    raw_modules = document["modules"]
    if not isinstance(raw_modules, list) or not raw_modules:
        raise EvidenceError("inventory.modules: expected non-empty array")

    modules: list[CoreModule] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_modules):
        location = f"inventory.modules[{index}]"
        if not isinstance(raw, dict):
            raise EvidenceError(f"{location}: expected object")
        _keys(raw, {"path", "classification", "sha256"}, location)
        path = repo_path(raw["path"], f"{location}.path")
        if path in seen:
            raise EvidenceError(f"{location}.path: duplicate {path}")
        seen.add(path)
        classification = raw["classification"]
        if classification not in CLASSIFICATIONS:
            raise EvidenceError(f"{location}.classification: invalid classification")
        expected_hash = raw["sha256"]
        if not isinstance(expected_hash, str) or SHA256.fullmatch(expected_hash) is None:
            raise EvidenceError(f"{location}.sha256: invalid SHA-256")
        source = _regular_file(root, path, f"{location}.path")
        if _sha256(source) != expected_hash:
            raise EvidenceError(f"{location}: stale source hash for {path}")
        modules.append(CoreModule(path, classification, expected_hash))

    source_dir = _regular_directory(root, source_root)
    discovered = {
        path.relative_to(root).as_posix()
        for path in source_dir.glob("*.rs")
        if path.is_file() and not path.is_symlink() and not path.name.endswith("_tests.rs")
    }
    if seen != discovered:
        raise EvidenceError(
            "inventory.modules: core inventory mismatch; "
            f"missing={sorted(discovered - seen)}, extra={sorted(seen - discovered)}"
        )
    if [module.path for module in modules] != sorted(seen):
        raise EvidenceError("inventory.modules: paths must be sorted")
    return modules


def _regular_directory(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise EvidenceError(f"inventory.source_root: invalid directory {relative}") from error
    if not resolved.is_dir() or candidate.is_symlink():
        raise EvidenceError("inventory.source_root: must be a non-symlink directory")
    return resolved


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=root, check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise EvidenceError(f"git {' '.join(arguments)} failed")
    return result.stdout.strip()


def observed_binding(root: Path) -> dict[str, Any]:
    """Collect identity used to compare an evidence envelope with this checkout."""
    commit = _git(root, "rev-parse", "HEAD")
    if COMMIT.fullmatch(commit) is None:
        raise EvidenceError("git HEAD is not a full commit ID")
    dirty = bool(
        _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude)artifacts/acceptance/**",
        )
    )
    toolchain_path = _regular_file(root, "rust-toolchain.toml", "toolchain")
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "rust_toolchain_sha256": _sha256(toolchain_path),
    }


def validate_binding(
    root: Path,
    envelope: dict[str, Any],
    inventory_path: Path,
    modules: list[CoreModule],
    observed: dict[str, Any] | None = None,
) -> Path:
    expected = {
        "schema_version",
        "kind",
        "git_commit",
        "git_dirty",
        "rust_toolchain_sha256",
        "tool",
        "command",
        "inventory_sha256",
        "sources",
        "report",
    }
    _keys(envelope, expected, "evidence")
    if envelope["schema_version"] != 1:
        raise EvidenceError("evidence.schema_version: expected integer 1")
    actual = observed if observed is not None else observed_binding(root)
    if envelope["git_commit"] != actual["git_commit"]:
        raise EvidenceError("evidence.git_commit: does not match current HEAD")
    if envelope["git_dirty"] is not False:
        raise EvidenceError("evidence.git_dirty: release evidence must be clean")
    if envelope["git_dirty"] != actual["git_dirty"]:
        raise EvidenceError("evidence.git_dirty: does not match current worktree")
    if envelope["rust_toolchain_sha256"] != actual["rust_toolchain_sha256"]:
        raise EvidenceError("evidence.rust_toolchain_sha256: stale toolchain binding")
    _text(envelope["tool"], "evidence.tool")
    _text(envelope["command"], "evidence.command")
    if envelope["inventory_sha256"] != _sha256(inventory_path):
        raise EvidenceError("evidence.inventory_sha256: stale inventory binding")

    sources = envelope["sources"]
    if not isinstance(sources, list):
        raise EvidenceError("evidence.sources: expected array")
    actual_sources: dict[str, str] = {}
    for index, source in enumerate(sources):
        location = f"evidence.sources[{index}]"
        if not isinstance(source, dict):
            raise EvidenceError(f"{location}: expected object")
        _keys(source, {"path", "sha256"}, location)
        path = repo_path(source["path"], f"{location}.path")
        sha256 = source["sha256"]
        if path in actual_sources:
            raise EvidenceError(f"{location}.path: duplicate {path}")
        if not isinstance(sha256, str) or SHA256.fullmatch(sha256) is None:
            raise EvidenceError(f"{location}.sha256: invalid SHA-256")
        actual_sources[path] = sha256
    expected_sources = {module.path: module.sha256 for module in modules}
    if actual_sources != expected_sources:
        raise EvidenceError("evidence.sources: must exactly match checked core inventory")

    report = envelope["report"]
    if not isinstance(report, dict):
        raise EvidenceError("evidence.report: expected object")
    _keys(report, {"path", "sha256", "format"}, "evidence.report")
    report_path = repo_path(report["path"], "evidence.report.path")
    resolved = _regular_file(root, report_path, "evidence.report.path")
    if report["sha256"] != _sha256(resolved):
        raise EvidenceError("evidence.report.sha256: stale report hash")
    return resolved


def _counter(summary: dict[str, Any], name: str, location: str) -> tuple[int, int]:
    counter = summary.get(name)
    if not isinstance(counter, dict):
        raise EvidenceError(f"{location}.{name}: missing counter")
    total, covered = counter.get("count"), counter.get("covered")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or not isinstance(covered, int)
        or isinstance(covered, bool)
        or total <= 0
        or covered < 0
        or covered > total
    ):
        raise EvidenceError(f"{location}.{name}: invalid or vacuous counter")
    return total, covered


def _coverage_name(filename: Any, root: Path) -> str | None:
    if not isinstance(filename, str):
        return None
    candidate = Path(filename)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        return resolved.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def validate_json_coverage(
    report_path: Path, root: Path, core_paths: set[str]
) -> dict[str, tuple[int, int]]:
    """Total line and region counters for the core files in an llvm-cov JSON export."""
    report = _load(report_path, "LLVM coverage report")
    if not isinstance(report, dict) or report.get("type") != "llvm.coverage.json.export":
        raise EvidenceError("coverage report: expected llvm.coverage.json.export")
    data = report.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise EvidenceError("coverage report.data: expected exactly one export object")
    files = data[0].get("files")
    if not isinstance(files, list) or not files:
        raise EvidenceError("coverage report.files: expected non-empty array")
    found: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise EvidenceError(f"coverage report.files[{index}]: expected object")
        name = _coverage_name(item.get("filename"), root)
        if name in core_paths:
            if name in found:
                raise EvidenceError(f"coverage report: duplicate core file {name}")
            summary = item.get("summary")
            if not isinstance(summary, dict):
                raise EvidenceError(f"coverage report: missing summary for {name}")
            found[name] = summary
    if set(found) != core_paths:
        raise EvidenceError(
            "coverage report: excluded core files; "
            f"missing={sorted(core_paths - set(found))}"
        )
    totals = {"lines": [0, 0], "regions": [0, 0]}
    for path, summary in found.items():
        for metric in totals:
            total, covered = _counter(summary, metric, f"coverage report {path}")
            totals[metric][0] += total
            totals[metric][1] += covered
    return {name: (values[0], values[1]) for name, values in totals.items()}


def _validate_lcov(
    report_path: Path, root: Path, core_paths: set[str]
) -> dict[str, tuple[int, int]]:
    try:
        lines = report_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvidenceError(f"cannot load LCOV report: {error}") from error
    records: dict[str, dict[str, int]] = {}
    current: str | None = None
    values: dict[str, int] = {}
    for line in lines:
        if line.startswith("SF:"):
            if current is not None:
                raise EvidenceError("LCOV report: nested SF record")
            current = _coverage_name(line[3:], root)
            values = {}
        elif line == "end_of_record":
            if current in core_paths:
                if current in records:
                    raise EvidenceError(f"LCOV report: duplicate core file {current}")
                records[current] = values
            current = None
            values = {}
        elif current is not None and ":" in line:
            key, raw = line.split(":", 1)
            if key in {"LF", "LH", "BRF", "BRH"}:
                try:
                    values[key] = int(raw)
                except ValueError as error:
                    raise EvidenceError(f"LCOV report: invalid {key} counter") from error
    if current is not None:
        raise EvidenceError("LCOV report: unterminated record")
    if set(records) != core_paths:
        raise EvidenceError(
            "LCOV report: excluded core files; "
            f"missing={sorted(core_paths - set(records))}"
        )
    totals = {"lines": [0, 0], "branches": [0, 0]}
    for path, record in records.items():
        for metric, total_key, covered_key in (
            ("lines", "LF", "LH"),
            ("branches", "BRF", "BRH"),
        ):
            total, covered = record.get(total_key), record.get(covered_key)
            if (
                total is None
                or covered is None
                or total <= 0
                or covered < 0
                or covered > total
            ):
                raise EvidenceError(f"LCOV report {path}: invalid or vacuous {metric}")
            totals[metric][0] += total
            totals[metric][1] += covered
    return {name: (values[0], values[1]) for name, values in totals.items()}


def meets(covered: int, total: int, percent: int) -> bool:
    """Report whether covered/total reaches percent without floating-point rounding."""
    return covered * 100 >= total * percent


def validate_coverage(
    root: Path,
    envelope_path: Path,
    inventory_path: Path,
    observed: dict[str, Any] | None = None,
) -> dict[str, tuple[int, int]]:
    modules = load_inventory(root, inventory_path)
    envelope = _load(envelope_path, "coverage evidence")
    if not isinstance(envelope, dict) or envelope.get("kind") != "coverage":
        raise EvidenceError("coverage evidence.kind: expected coverage")
    report_path = validate_binding(root, envelope, inventory_path, modules, observed)
    report_format = envelope["report"]["format"]
    core_paths = {module.path for module in modules}
    if report_format == "llvm-cov-json":
        metrics = validate_json_coverage(report_path, root, core_paths)
        secondary = "regions"
    elif report_format == "lcov":
        metrics = _validate_lcov(report_path, root, core_paths)
        secondary = "branches"
    else:
        raise EvidenceError("coverage evidence.report.format: unsupported format")
    if not meets(metrics["lines"][1], metrics["lines"][0], 90):
        raise EvidenceError("coverage report: line coverage is below 90%")
    if not meets(metrics[secondary][1], metrics[secondary][0], 80):
        raise EvidenceError(f"coverage report: {secondary} coverage is below 80%")
    return metrics


def _disposition(record: dict[str, Any], location: str, root: Path) -> None:
    disposition = record.get("disposition")
    if not isinstance(disposition, dict):
        raise EvidenceError(f"{location}.disposition: required")
    _keys(
        disposition,
        {"owner", "rationale", "risk", "action", "tool_confirmation"},
        f"{location}.disposition",
    )
    for field in ("owner", "rationale", "risk", "action"):
        _text(disposition[field], f"{location}.disposition.{field}")
    confirmation = disposition["tool_confirmation"]
    if record["status"] in {"equivalent", "unreachable"}:
        if not isinstance(confirmation, dict):
            raise EvidenceError(
                f"{location}.disposition.tool_confirmation: "
                "required hash-bound artifact"
            )
        confirmation_location = f"{location}.disposition.tool_confirmation"
        _keys(confirmation, {"tool", "path", "sha256"}, confirmation_location)
        _text(confirmation["tool"], f"{confirmation_location}.tool")
        path = repo_path(confirmation["path"], f"{confirmation_location}.path")
        expected_hash = confirmation["sha256"]
        if (
            not isinstance(expected_hash, str)
            or SHA256.fullmatch(expected_hash) is None
        ):
            raise EvidenceError(f"{confirmation_location}.sha256: invalid SHA-256")
        artifact = _regular_file(root, path, f"{confirmation_location}.path")
        if _sha256(artifact) != expected_hash:
            raise EvidenceError(f"{confirmation_location}: stale artifact hash")
    elif confirmation is not None:
        raise EvidenceError(
            f"{location}.disposition.tool_confirmation: must be null"
        )


def _nonnegative_integer(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceError(f"{location}: expected nonnegative integer")
    return value


def _line_column(value: Any, location: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{location}: expected object")
    _keys(value, {"line", "column"}, location)
    line = value["line"]
    column = value["column"]
    if not isinstance(line, int) or isinstance(line, bool) or line <= 0:
        raise EvidenceError(f"{location}.line: expected positive integer")
    if not isinstance(column, int) or isinstance(column, bool) or column <= 0:
        raise EvidenceError(f"{location}.column: expected positive integer")
    return line, column


def _native_mutant(value: Any, location: str, status: str) -> NativeMutant:
    if not isinstance(value, dict):
        raise EvidenceError(f"{location}: expected object")
    _keys(
        value,
        {"name", "package", "file", "function", "span", "replacement", "genre"},
        location,
    )
    mutant_id = _text(value["name"], f"{location}.name")
    _text(value["package"], f"{location}.package")
    path = repo_path(value["file"], f"{location}.file")
    if value["function"] is not None and not isinstance(value["function"], dict):
        raise EvidenceError(f"{location}.function: expected object or null")
    span = value["span"]
    if not isinstance(span, dict):
        raise EvidenceError(f"{location}.span: expected object")
    _keys(span, {"start", "end"}, f"{location}.span")
    line, column = _line_column(span["start"], f"{location}.span.start")
    end_line, end_column = _line_column(span["end"], f"{location}.span.end")
    if (end_line, end_column) <= (line, column):
        raise EvidenceError(f"{location}.span: end must follow start")
    if not isinstance(value["replacement"], str):
        raise EvidenceError(f"{location}.replacement: expected string")
    _text(value["genre"], f"{location}.genre")
    return NativeMutant(mutant_id, path, line, status)


def _parse_cargo_mutants_v26(path: Path) -> dict[str, NativeMutant]:
    report = _load(path, "cargo-mutants v26 outcomes report")
    if not isinstance(report, dict):
        raise EvidenceError("cargo-mutants outcomes: expected object")
    _keys(
        report,
        {
            "outcomes",
            "total_mutants",
            "missed",
            "caught",
            "timeout",
            "unviable",
            "success",
            "start_time",
            "end_time",
            "cargo_mutants_version",
        },
        "cargo-mutants outcomes",
    )
    version = _text(
        report["cargo_mutants_version"],
        "cargo-mutants outcomes.cargo_mutants_version",
    )
    if CARGO_MUTANTS_V26.fullmatch(version) is None:
        raise EvidenceError(
            "cargo-mutants outcomes.cargo_mutants_version: "
            "expected supported 26.x producer"
        )
    _text(report["start_time"], "cargo-mutants outcomes.start_time")
    _text(report["end_time"], "cargo-mutants outcomes.end_time")
    counters = {
        field: _nonnegative_integer(
            report[field], f"cargo-mutants outcomes.{field}"
        )
        for field in (
            "total_mutants",
            "missed",
            "caught",
            "timeout",
            "unviable",
            "success",
        )
    }
    outcomes = report["outcomes"]
    if not isinstance(outcomes, list) or not outcomes:
        raise EvidenceError("cargo-mutants outcomes.outcomes: expected non-empty array")

    native: dict[str, NativeMutant] = {}
    baseline_count = 0
    observed_counts = {summary: 0 for summary in NATIVE_MUTATION_SUMMARIES}
    for index, outcome in enumerate(outcomes):
        location = f"cargo-mutants outcomes.outcomes[{index}]"
        if not isinstance(outcome, dict):
            raise EvidenceError(f"{location}: expected object")
        _keys(
            outcome,
            {"scenario", "summary", "log_path", "diff_path", "phase_results"},
            location,
        )
        summary = _text(outcome["summary"], f"{location}.summary")
        _text(outcome["log_path"], f"{location}.log_path")
        if (
            not isinstance(outcome["phase_results"], list)
            or not outcome["phase_results"]
        ):
            raise EvidenceError(f"{location}.phase_results: expected non-empty array")
        scenario = outcome["scenario"]
        if scenario == "Baseline":
            baseline_count += 1
            if summary != "Success":
                raise EvidenceError(f"{location}: baseline did not succeed")
            if outcome["diff_path"] is not None:
                raise EvidenceError(f"{location}.diff_path: baseline must be null")
            continue
        if not isinstance(scenario, dict):
            raise EvidenceError(f"{location}.scenario: expected Baseline or object")
        _keys(scenario, {"Mutant"}, f"{location}.scenario")
        if summary not in NATIVE_MUTATION_SUMMARIES:
            raise EvidenceError(f"{location}.summary: unsupported mutant outcome")
        _text(outcome["diff_path"], f"{location}.diff_path")
        status = NATIVE_MUTATION_SUMMARIES[summary]
        parsed = _native_mutant(
            scenario["Mutant"], f"{location}.scenario.Mutant", status
        )
        if parsed.mutant_id in native:
            raise EvidenceError(
                f"{location}.scenario.Mutant.name: duplicate native mutant identity"
            )
        native[parsed.mutant_id] = parsed
        observed_counts[summary] += 1

    if baseline_count != 1:
        raise EvidenceError(
            "cargo-mutants outcomes: expected exactly one successful baseline"
        )
    expected_counts = {
        "CaughtMutant": counters["caught"],
        "MissedMutant": counters["missed"],
        "Timeout": counters["timeout"],
        "Unviable": counters["unviable"],
    }
    if observed_counts != expected_counts:
        raise EvidenceError(
            "cargo-mutants outcomes: summary counters do not match mutant outcomes"
        )
    if counters["total_mutants"] != len(native):
        raise EvidenceError(
            "cargo-mutants outcomes.total_mutants: does not match mutant outcomes"
        )
    if counters["success"] != 1:
        raise EvidenceError(
            "cargo-mutants outcomes.success: expected one successful baseline"
        )
    return native


def _native_producer_records(
    producer_format: str, producer_artifact: Path
) -> dict[str, NativeMutant]:
    if producer_format != MUTATION_PRODUCER_FORMAT:
        raise EvidenceError(
            "mutation report.producer_report.format: unsupported native format"
        )
    return _parse_cargo_mutants_v26(producer_artifact)


def _validate_mutation_report(
    report_path: Path, modules: list[CoreModule], root: Path
) -> tuple[int, int]:
    report = _load(report_path, "mutation report")
    if not isinstance(report, dict):
        raise EvidenceError("mutation report: expected object")
    _keys(
        report,
        {"schema_version", "scopes", "producer_report", "mutants"},
        "mutation report",
    )
    if report["schema_version"] != 2:
        raise EvidenceError("mutation report.schema_version: expected integer 2")
    scopes = report["scopes"]
    if (
        not isinstance(scopes, list)
        or set(scopes) != MUTATION_SCOPES
        or len(scopes) != len(MUTATION_SCOPES)
    ):
        raise EvidenceError("mutation report.scopes: all four G6 scopes are required")
    producer = report["producer_report"]
    if not isinstance(producer, dict):
        raise EvidenceError("mutation report.producer_report: expected object")
    _keys(
        producer,
        {"path", "sha256", "format"},
        "mutation report.producer_report",
    )
    producer_path = repo_path(
        producer["path"], "mutation report.producer_report.path"
    )
    producer_format = _text(
        producer["format"], "mutation report.producer_report.format"
    )
    producer_artifact = _regular_file(
        root, producer_path, "mutation report.producer_report.path"
    )
    producer_hash = producer["sha256"]
    if (
        not isinstance(producer_hash, str)
        or SHA256.fullmatch(producer_hash) is None
        or _sha256(producer_artifact) != producer_hash
    ):
        raise EvidenceError("mutation report.producer_report: stale artifact hash")
    native_mutants = _native_producer_records(producer_format, producer_artifact)
    mutants = report["mutants"]
    if not isinstance(mutants, list) or not mutants:
        raise EvidenceError("mutation report.mutants: expected non-empty array")
    module_by_path = {module.path: module for module in modules}
    ids: set[str] = set()
    covered_paths: set[str] = set()
    killed = 0
    denominator = 0
    scored_scopes: set[str] = set()
    for index, record in enumerate(mutants):
        location = f"mutation report.mutants[{index}]"
        if not isinstance(record, dict):
            raise EvidenceError(f"{location}: expected object")
        _keys(
            record,
            {
                "id",
                "path",
                "line",
                "status",
                "producer_status",
                "scope",
                "invariant_kind",
                "invariant_ids",
                "disposition",
            },
            location,
        )
        mutant_id = _text(record["id"], f"{location}.id")
        if mutant_id in ids:
            raise EvidenceError(f"{location}.id: duplicate")
        ids.add(mutant_id)
        path = repo_path(record["path"], f"{location}.path")
        if path not in module_by_path:
            raise EvidenceError(f"{location}.path: not a checked core module")
        covered_paths.add(path)
        line = record["line"]
        if not isinstance(line, int) or isinstance(line, bool) or line <= 0:
            raise EvidenceError(f"{location}.line: expected positive integer")
        status = record["status"]
        if status not in {
            "killed",
            "survived",
            "timeout",
            "unviable",
            "equivalent",
            "unreachable",
        }:
            raise EvidenceError(f"{location}.status: invalid status")
        producer_status = record["producer_status"]
        if producer_status not in {"killed", "survived", "timeout", "unviable"}:
            raise EvidenceError(f"{location}.producer_status: invalid native status")
        native = native_mutants.get(mutant_id)
        if native is None:
            raise EvidenceError(f"{location}.id: not present in native producer report")
        if path != native.path or line != native.line:
            raise EvidenceError(
                f"{location}: identity does not match native producer report"
            )
        if producer_status != native.status:
            raise EvidenceError(
                f"{location}.producer_status: does not match native producer outcome"
            )
        permitted_statuses = {producer_status}
        if producer_status == "survived":
            permitted_statuses.update({"equivalent", "unreachable"})
        elif producer_status == "unviable":
            permitted_statuses.add("unreachable")
        if status not in permitted_statuses:
            raise EvidenceError(
                f"{location}.status: unsupported native-status reclassification"
            )
        scope = record["scope"]
        if scope not in MUTATION_SCOPES:
            raise EvidenceError(f"{location}.scope: invalid G6 scope")
        invariant_kind = record["invariant_kind"]
        if invariant_kind not in {"none", "format", "safety", "format_safety"}:
            raise EvidenceError(f"{location}.invariant_kind: invalid classification")
        invariant_ids = record["invariant_ids"]
        if (
            not isinstance(invariant_ids, list)
            or len(invariant_ids) != len(set(invariant_ids))
            or any(not isinstance(item, str) or not item.strip() for item in invariant_ids)
        ):
            raise EvidenceError(f"{location}.invariant_ids: invalid array")
        if (invariant_kind == "none") != (not invariant_ids):
            raise EvidenceError(
                f"{location}: invariant_kind and invariant_ids disagree"
            )
        if status in DISPOSITION_STATUSES:
            _disposition(record, location, root)
        elif record["disposition"] is not None:
            raise EvidenceError(f"{location}.disposition: must be null for {status}")
        if status == "killed":
            killed += 1
            denominator += 1
            scored_scopes.add(scope)
        elif status in SURVIVOR_STATUSES:
            denominator += 1
            scored_scopes.add(scope)
            if invariant_kind != "none":
                raise EvidenceError(
                    f"{location}: survivor affects a format/safety invariant"
                )
    if ids != set(native_mutants):
        raise EvidenceError(
            "mutation report: normalized/native mutant identity mismatch; "
            f"missing={sorted(set(native_mutants) - ids)}, "
            f"extra={sorted(ids - set(native_mutants))}"
        )
    if covered_paths != set(module_by_path):
        raise EvidenceError(
            "mutation report: excluded core files; "
            f"missing={sorted(set(module_by_path) - covered_paths)}"
        )
    if scored_scopes != MUTATION_SCOPES:
        raise EvidenceError(
            "mutation report: scopes without scored mutants; "
            f"missing={sorted(MUTATION_SCOPES - scored_scopes)}"
        )
    if denominator <= 0:
        raise EvidenceError("mutation report: vacuous scored-mutant denominator")
    if not meets(killed, denominator, 85):
        raise EvidenceError("mutation report: core mutation score is below 85%")
    return killed, denominator


def validate_mutation(
    root: Path,
    envelope_path: Path,
    inventory_path: Path,
    observed: dict[str, Any] | None = None,
) -> tuple[int, int]:
    modules = load_inventory(root, inventory_path)
    envelope = _load(envelope_path, "mutation evidence")
    if not isinstance(envelope, dict) or envelope.get("kind") != "mutation":
        raise EvidenceError("mutation evidence.kind: expected mutation")
    report_path = validate_binding(root, envelope, inventory_path, modules, observed)
    if envelope["report"]["format"] != "g6-mutation-json":
        raise EvidenceError("mutation evidence.report.format: unsupported format")
    return _validate_mutation_report(report_path, modules, root)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("inventory", "coverage", "mutation"))
    parser.add_argument("evidence", nargs="?", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = args.repo_root.resolve()
    inventory = (
        args.inventory.resolve()
        if args.inventory
        else root / DEFAULT_INVENTORY
    )
    try:
        modules = load_inventory(root, inventory)
        if args.kind == "inventory":
            if args.evidence is not None:
                raise EvidenceError("inventory validation does not take evidence")
            print(f"G6 core inventory valid: {len(modules)} modules")
            return 0
        if args.evidence is None:
            raise EvidenceError(f"{args.kind} validation requires an evidence envelope")
        evidence = args.evidence.resolve()
        if args.kind == "coverage":
            metrics = validate_coverage(root, evidence, inventory)
            rendered = ", ".join(
                f"{name}={covered}/{total}"
                for name, (total, covered) in metrics.items()
            )
            print(f"G6 coverage evidence valid: {rendered}")
        else:
            killed, denominator = validate_mutation(root, evidence, inventory)
            print(f"G6 mutation evidence valid: score={killed}/{denominator}")
        return 0
    except EvidenceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
