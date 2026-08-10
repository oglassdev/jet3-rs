#!/usr/bin/env python3
"""Compare commit-bound benchmark measurements with a strict 15% policy."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

from suite_identity import (
    SuiteIdentityError,
    digest_for_commit,
    retained_blob,
)

LOWER_IS_BETTER = {
    "median_latency_ns",
    "peak_rss_bytes",
    "output_size_bytes",
}
HIGHER_IS_BETTER = {"throughput_per_second"}
MAXIMUM_THRESHOLD = 0.15
SUITE_ID = "BENCH-FORMAT-FOUNDATION-V1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BENCHMARK_ID_PATTERN = re.compile(r"^BENCH-[A-Z0-9][A-Z0-9_-]*$")
METADATA_MATCH_FIELDS = {
    "os",
    "architecture",
    "cpu",
    "logical_cpus",
    "memory_bytes",
    "rustc",
    "cargo",
}


class ComparisonError(ValueError):
    """The inputs cannot form a trustworthy comparison."""


def _positive_number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComparisonError(f"{context} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ComparisonError(f"{context} cannot be represented as a finite float") from error
    if not math.isfinite(number) or number <= 0:
        raise ComparisonError(f"{context} must be finite and greater than zero")
    return number


def _measurements(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    raw_measurements = document.get("measurements")
    if not isinstance(raw_measurements, list) or not raw_measurements:
        raise ComparisonError(f"{label}.measurements must be a non-empty array")

    indexed: dict[str, dict[str, Any]] = {}
    for measurement in raw_measurements:
        if not isinstance(measurement, dict):
            raise ComparisonError(f"{label} measurement must be an object")
        if set(measurement) != {"id", "throughput_unit", "metrics"}:
            raise ComparisonError(
                f"{label} measurement must contain exactly id, throughput_unit, and metrics"
            )
        identifier = measurement.get("id")
        throughput_unit = measurement.get("throughput_unit")
        metrics = measurement.get("metrics")
        if not isinstance(identifier, str) or BENCHMARK_ID_PATTERN.fullmatch(identifier) is None:
            raise ComparisonError(f"{label} measurement has an invalid id")
        if identifier in indexed:
            raise ComparisonError(f"{label} contains duplicate measurement {identifier}")
        if throughput_unit not in {"bytes", "elements"}:
            raise ComparisonError(
                f"{label}.{identifier}.throughput_unit must be bytes or elements"
            )
        if not isinstance(metrics, dict):
            raise ComparisonError(f"{label}.{identifier}.metrics must be an object")
        if set(metrics) != LOWER_IS_BETTER | HIGHER_IS_BETTER:
            raise ComparisonError(
                f"{label}.{identifier}.metrics must contain exactly the four required metrics"
            )
        indexed[identifier] = {**metrics, "_throughput_unit": throughput_unit}
    return indexed


def _metadata(document: dict[str, Any], label: str) -> dict[str, Any]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ComparisonError(f"{label}.metadata must be an object")

    git_commit = metadata.get("git_commit")
    if not isinstance(git_commit, str) or COMMIT_PATTERN.fullmatch(git_commit) is None:
        raise ComparisonError(f"{label}.metadata.git_commit must be 40 lowercase hex digits")
    if type(metadata.get("dirty")) is not bool:
        raise ComparisonError(f"{label}.metadata.dirty must be a boolean")

    captured_at = metadata.get("captured_at_utc")
    if not isinstance(captured_at, str):
        raise ComparisonError(f"{label}.metadata.captured_at_utc must be a timestamp")
    try:
        parsed_timestamp = datetime.datetime.fromisoformat(
            captured_at.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ComparisonError(
            f"{label}.metadata.captured_at_utc must be an ISO 8601 timestamp"
        ) from error
    if parsed_timestamp.utcoffset() is None:
        raise ComparisonError(
            f"{label}.metadata.captured_at_utc must include a UTC offset"
        )
    if parsed_timestamp.utcoffset() != datetime.timedelta(0):
        raise ComparisonError(f"{label}.metadata.captured_at_utc must be UTC")

    for field in ("os", "architecture", "cpu", "rustc", "cargo"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ComparisonError(f"{label}.metadata.{field} must be a non-empty string")
    for field in ("logical_cpus", "memory_bytes"):
        value = metadata.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ComparisonError(f"{label}.metadata.{field} must be a positive integer")
    for field in (
        "benchmark_manifest_sha256",
        "benchmark_lockfile_sha256",
        "suite_digest_sha256",
    ):
        value = metadata.get(field)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise ComparisonError(
                f"{label}.metadata.{field} must be 64 lowercase hex digits"
            )
    return metadata


def _artifact_references(document: dict[str, Any], label: str) -> list[dict[str, str]]:
    artifacts = document.get("raw_measurement_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ComparisonError(f"{label}.raw_measurement_artifacts must be non-empty")
    references: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise ComparisonError(
                f"{label} raw measurement artifact must contain path and sha256"
            )
        path = artifact.get("path")
        sha256 = artifact.get("sha256")
        if not isinstance(path, str) or not path:
            raise ComparisonError(f"{label} raw measurement artifact path is invalid")
        if path in seen_paths:
            raise ComparisonError(f"{label} repeats raw measurement artifact {path}")
        if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
            raise ComparisonError(
                f"{label} raw measurement artifact sha256 is invalid"
            )
        seen_paths.add(path)
        references.append({"path": path, "sha256": sha256})
    return sorted(references, key=lambda reference: reference["path"])


def _verify_commit_binding(
    document: dict[str, Any],
    label: str,
    repository_root: Path,
    measurements: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metadata = document["metadata"]
    commit = metadata["git_commit"]
    references = _artifact_references(document, label)
    try:
        suite_digest = digest_for_commit(repository_root, commit)
        manifest_blob = retained_blob(
            repository_root, commit, "benches/manifest.json"
        )
        lockfile_blob = retained_blob(repository_root, commit, "benches/Cargo.lock")
    except SuiteIdentityError as error:
        raise ComparisonError(f"{label} commit binding failed: {error}") from error

    if metadata["suite_digest_sha256"] != suite_digest:
        raise ComparisonError(f"{label} suite digest does not match retained commit")
    if metadata["benchmark_manifest_sha256"] != hashlib.sha256(
        manifest_blob
    ).hexdigest():
        raise ComparisonError(f"{label} manifest hash does not match retained commit")
    if metadata["benchmark_lockfile_sha256"] != hashlib.sha256(
        lockfile_blob
    ).hexdigest():
        raise ComparisonError(f"{label} lockfile hash does not match retained commit")

    retained_measurements: list[dict[str, Any]] = []
    for reference in references:
        try:
            blob = retained_blob(repository_root, commit, reference["path"])
        except SuiteIdentityError as error:
            raise ComparisonError(
                f"{label} raw measurement artifact is not retained: {error}"
            ) from error
        if hashlib.sha256(blob).hexdigest() != reference["sha256"]:
            raise ComparisonError(
                f"{label} raw measurement artifact hash does not match retained commit"
            )
        try:
            raw_document = json.loads(blob)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ComparisonError(
                f"{label} raw measurement artifact is not valid JSON"
            ) from error
        if not isinstance(raw_document, dict) or set(raw_document) != {"measurements"}:
            raise ComparisonError(
                f"{label} raw measurement artifact must contain only measurements"
            )
        raw_measurements = raw_document["measurements"]
        if not isinstance(raw_measurements, list):
            raise ComparisonError(
                f"{label} raw measurement artifact measurements must be an array"
            )
        retained_measurements.extend(raw_measurements)

    retained_index = _measurements(
        {"measurements": retained_measurements}, f"{label} retained raw"
    )
    if retained_index != measurements:
        raise ComparisonError(
            f"{label} normalized measurements differ from retained raw artifacts"
        )
    return {
        "suite_digest_sha256": suite_digest,
        "raw_measurement_artifacts": references,
    }


def _validate_documents(
    baseline: dict[str, Any], candidate: dict[str, Any], repository_root: Path
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    expected_keys = {
        "schema_version",
        "suite_id",
        "metadata",
        "measurements",
        "raw_measurement_artifacts",
    }
    if set(baseline) != expected_keys or set(candidate) != expected_keys:
        raise ComparisonError(
            "both inputs must contain exactly schema_version, suite_id, metadata, "
            "measurements, and raw_measurement_artifacts"
        )
    if (
        type(baseline.get("schema_version")) is not int
        or baseline.get("schema_version") != 1
        or type(candidate.get("schema_version")) is not int
        or candidate.get("schema_version") != 1
    ):
        raise ComparisonError("both inputs must use schema_version 1")
    if baseline.get("suite_id") != SUITE_ID or candidate.get("suite_id") != SUITE_ID:
        raise ComparisonError(f"both inputs must use suite_id {SUITE_ID}")

    baseline_metadata = _metadata(baseline, "baseline")
    candidate_metadata = _metadata(candidate, "candidate")
    if baseline_metadata.get("dirty") is not False:
        raise ComparisonError("baseline metadata must identify a clean tree")
    if candidate_metadata.get("dirty") is not False:
        raise ComparisonError("candidate metadata must identify a clean tree")
    for field in sorted(METADATA_MATCH_FIELDS):
        if baseline_metadata.get(field) != candidate_metadata.get(field):
            raise ComparisonError(f"metadata field differs: {field}")

    baseline_measurements = _measurements(baseline, "baseline")
    candidate_measurements = _measurements(candidate, "candidate")
    if baseline_measurements.keys() != candidate_measurements.keys():
        raise ComparisonError("baseline and candidate measurement IDs differ")
    baseline_binding = _verify_commit_binding(
        baseline, "baseline", repository_root, baseline_measurements
    )
    candidate_binding = _verify_commit_binding(
        candidate, "candidate", repository_root, candidate_measurements
    )
    if (
        baseline_binding["suite_digest_sha256"]
        != candidate_binding["suite_digest_sha256"]
    ):
        raise ComparisonError("baseline and candidate retained benchmark suites differ")
    return (
        baseline_measurements,
        candidate_measurements,
        baseline_binding,
        candidate_binding,
    )


def compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    threshold: float = 0.15,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic comparison report or raise ComparisonError."""
    if (
        not math.isfinite(threshold)
        or threshold < 0
        or threshold > MAXIMUM_THRESHOLD
    ):
        raise ComparisonError("threshold must be finite and in [0, 0.15]")

    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[2]
    (
        baseline_measurements,
        candidate_measurements,
        baseline_binding,
        candidate_binding,
    ) = _validate_documents(
        baseline, candidate, repository_root
    )
    regressions: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    for identifier in sorted(baseline_measurements):
        baseline_metrics = baseline_measurements[identifier]
        candidate_metrics = candidate_measurements[identifier]
        if baseline_metrics.keys() != candidate_metrics.keys():
            raise ComparisonError(f"metric set differs for {identifier}")
        baseline_unit = baseline_metrics["_throughput_unit"]
        candidate_unit = candidate_metrics["_throughput_unit"]
        if baseline_unit != candidate_unit:
            raise ComparisonError(f"throughput unit differs for {identifier}")

        for metric in sorted(baseline_metrics):
            if metric == "_throughput_unit":
                continue
            if metric not in LOWER_IS_BETTER | HIGHER_IS_BETTER:
                raise ComparisonError(f"unknown metric for {identifier}: {metric}")
            baseline_value = _positive_number(
                baseline_metrics[metric], f"baseline.{identifier}.{metric}"
            )
            candidate_value = _positive_number(
                candidate_metrics[metric], f"candidate.{identifier}.{metric}"
            )
            if metric in LOWER_IS_BETTER:
                degradation = (candidate_value - baseline_value) / baseline_value
                boundary = baseline_value * (1 + threshold)
                regressed = candidate_value > boundary and not math.isclose(
                    candidate_value, boundary, rel_tol=1e-12
                )
                direction = "lower_is_better"
            else:
                degradation = (baseline_value - candidate_value) / baseline_value
                boundary = baseline_value * (1 - threshold)
                regressed = candidate_value < boundary and not math.isclose(
                    candidate_value, boundary, rel_tol=1e-12
                )
                direction = "higher_is_better"

            entry = {
                "id": identifier,
                "metric": metric,
                "direction": direction,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "degradation_fraction": degradation,
                "regressed": regressed,
            }
            if metric == "throughput_per_second":
                entry["throughput_unit"] = baseline_unit
            comparisons.append(entry)
            if regressed:
                regressions.append(entry)

    return {
        "schema_version": 1,
        "suite_id": baseline["suite_id"],
        "baseline_git_commit": baseline["metadata"]["git_commit"],
        "candidate_git_commit": candidate["metadata"]["git_commit"],
        "suite_digest_sha256": baseline_binding["suite_digest_sha256"],
        "baseline_raw_measurement_artifacts": baseline_binding[
            "raw_measurement_artifacts"
        ],
        "candidate_raw_measurement_artifacts": candidate_binding[
            "raw_measurement_artifacts"
        ],
        "threshold_fraction": threshold,
        "status": "FAIL" if regressions else "PASS",
        "comparisons": comparisons,
        "regressions": regressions,
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ComparisonError(f"cannot read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ComparisonError(f"{path} must contain a JSON object")
    return document


def _write_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--threshold", type=float, default=0.15)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)

    try:
        report = compare(
            _load(arguments.baseline),
            _load(arguments.candidate),
            arguments.threshold,
            arguments.repository_root,
        )
    except ComparisonError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output is None:
        print(rendered)
    else:
        _write_atomic(arguments.output, report)
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
