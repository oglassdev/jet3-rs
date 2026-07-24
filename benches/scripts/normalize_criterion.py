#!/usr/bin/env python3
"""Normalize Criterion 0.5 result directories into commit-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from suite_identity import SuiteIdentityError, digest_for_commit, retained_blob

BENCHMARK_ID_PATTERN = re.compile(r"^BENCH-[A-Z0-9][A-Z0-9_-]*$")
NORMALIZED_ID_PATTERN = re.compile(
    r"^BENCH-[A-Z0-9][A-Z0-9_-]*--[0-9A-F]{16}$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)


class NormalizationError(ValueError):
    """Criterion evidence is incomplete, ambiguous, or not commit-bound."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NormalizationError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise NormalizationError(f"{label} must be a JSON object")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormalizationError(f"{label} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise NormalizationError(f"{label} cannot be represented as a finite float") from error
    if not math.isfinite(number) or number <= 0:
        raise NormalizationError(f"{label} must be finite and greater than zero")
    return number


def _inventory(manifest: dict[str, Any]) -> dict[str, str]:
    entries = manifest.get("benchmarks")
    if not isinstance(entries, list) or not entries:
        raise NormalizationError("benchmark manifest inventory must be non-empty")
    groups: dict[str, str] = {}
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise NormalizationError("benchmark inventory entry must be an object")
        identifier = entry.get("id")
        group = entry.get("criterion_group")
        if (
            not isinstance(identifier, str)
            or BENCHMARK_ID_PATTERN.fullmatch(identifier) is None
        ):
            raise NormalizationError("benchmark inventory contains an invalid id")
        if not isinstance(group, str) or not group:
            raise NormalizationError(f"{identifier} has no criterion_group")
        if identifier in identifiers or group in groups:
            raise NormalizationError("benchmark inventory has duplicate ids or groups")
        identifiers.add(identifier)
        groups[group] = identifier
    return groups


def _resources(path: Path) -> dict[str, tuple[int, int]]:
    document = _load_object(path, "resource metrics")
    if set(document) != {"schema_version", "measurements"}:
        raise NormalizationError(
            "resource metrics must contain exactly schema_version and measurements"
        )
    if document["schema_version"] != 1:
        raise NormalizationError("resource metrics must use schema_version 1")
    entries = document["measurements"]
    if not isinstance(entries, list) or not entries:
        raise NormalizationError("resource measurement inventory must be non-empty")
    indexed: dict[str, tuple[int, int]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "criterion_id",
            "peak_rss_bytes",
            "output_size_bytes",
        }:
            raise NormalizationError(
                "each resource measurement requires criterion_id, peak_rss_bytes, "
                "and output_size_bytes"
            )
        criterion_id = entry["criterion_id"]
        if not isinstance(criterion_id, str) or not criterion_id:
            raise NormalizationError("resource criterion_id must be non-empty")
        if criterion_id in indexed:
            raise NormalizationError(f"duplicate resource measurement {criterion_id}")
        values: list[int] = []
        for field in ("peak_rss_bytes", "output_size_bytes"):
            value = entry[field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise NormalizationError(
                    f"{criterion_id}.{field} must be a positive integer"
                )
            values.append(value)
        indexed[criterion_id] = (values[0], values[1])
    return indexed


def _throughput(benchmark: dict[str, Any], median_ns: float) -> tuple[str, float]:
    raw = benchmark.get("throughput")
    if not isinstance(raw, dict) or len(raw) != 1:
        raise NormalizationError(
            f"{benchmark.get('full_id', 'Criterion case')} needs one throughput unit"
        )
    criterion_unit, amount = next(iter(raw.items()))
    units = {"Bytes": "bytes", "Elements": "elements"}
    if criterion_unit not in units:
        raise NormalizationError(f"unsupported Criterion throughput unit {criterion_unit}")
    quantity = _positive_number(amount, "Criterion throughput quantity")
    rate = quantity * 1_000_000_000.0 / median_ns
    return units[criterion_unit], _positive_number(rate, "derived throughput")


def normalize(
    criterion_root: Path, manifest_path: Path, resources_path: Path
) -> list[dict[str, Any]]:
    """Return stable, sorted measurements or raise NormalizationError."""
    manifest = _load_object(manifest_path, "benchmark manifest")
    groups = _inventory(manifest)
    resources = _resources(resources_path)
    measurements: list[dict[str, Any]] = []
    seen_cases: set[str] = set()
    observed_groups: set[str] = set()

    benchmark_paths = sorted(criterion_root.glob("**/new/benchmark.json"))
    if not benchmark_paths:
        raise NormalizationError("Criterion result inventory is empty")
    for benchmark_path in benchmark_paths:
        result_directory = benchmark_path.parent
        benchmark = _load_object(benchmark_path, "Criterion benchmark")
        full_id = benchmark.get("full_id")
        group_id = benchmark.get("group_id")
        if not isinstance(full_id, str) or not full_id:
            raise NormalizationError(f"{benchmark_path} has no full_id")
        if full_id in seen_cases:
            raise NormalizationError(f"duplicate Criterion case {full_id}")
        if group_id not in groups:
            raise NormalizationError(f"unmanifested Criterion group {group_id}")

        estimates = _load_object(result_directory / "estimates.json", "Criterion estimates")
        median = estimates.get("median")
        if not isinstance(median, dict):
            raise NormalizationError(f"{full_id} has no median estimate")
        median_ns = _positive_number(
            median.get("point_estimate"), f"{full_id}.median.point_estimate"
        )
        sample = _load_object(result_directory / "sample.json", "Criterion sample")
        times = sample.get("times")
        iterations = sample.get("iters")
        if (
            not isinstance(times, list)
            or not isinstance(iterations, list)
            or len(times) < 2
            or len(times) != len(iterations)
        ):
            raise NormalizationError(f"{full_id} needs at least two paired samples")
        unit, throughput = _throughput(benchmark, median_ns)
        if full_id not in resources:
            raise NormalizationError(f"missing resource metrics for {full_id}")
        peak_rss, output_size = resources[full_id]
        suffix = hashlib.sha256(full_id.encode("utf-8")).hexdigest()[:16].upper()
        measurements.append(
            {
                "id": f"{groups[group_id]}--{suffix}",
                "throughput_unit": unit,
                "metrics": {
                    "median_latency_ns": median_ns,
                    "throughput_per_second": throughput,
                    "peak_rss_bytes": peak_rss,
                    "output_size_bytes": output_size,
                },
            }
        )
        seen_cases.add(full_id)
        observed_groups.add(group_id)

    missing_groups = sorted(set(groups) - observed_groups)
    if missing_groups:
        raise NormalizationError(
            f"Criterion results omit manifest groups: {', '.join(missing_groups)}"
        )
    unused_resources = sorted(set(resources) - seen_cases)
    if unused_resources:
        raise NormalizationError(
            f"resource metrics contain unknown cases: {', '.join(unused_resources)}"
        )
    return sorted(measurements, key=lambda entry: entry["id"])


def _validate_measurements(measurements: Any, label: str) -> None:
    if not isinstance(measurements, list) or not measurements:
        raise NormalizationError(f"{label} measurements must be a non-empty array")
    identifiers: set[str] = set()
    for measurement in measurements:
        if not isinstance(measurement, dict) or set(measurement) != {
            "id",
            "throughput_unit",
            "metrics",
        }:
            raise NormalizationError(f"{label} contains a malformed measurement")
        identifier = measurement["id"]
        if (
            not isinstance(identifier, str)
            or NORMALIZED_ID_PATTERN.fullmatch(identifier) is None
        ):
            raise NormalizationError(f"{label} contains an invalid measurement id")
        if identifier in identifiers:
            raise NormalizationError(f"{label} contains duplicate measurement ids")
        identifiers.add(identifier)
        if measurement["throughput_unit"] not in {"bytes", "elements"}:
            raise NormalizationError(f"{identifier} has an invalid throughput unit")
        metrics = measurement["metrics"]
        if not isinstance(metrics, dict) or set(metrics) != {
            "median_latency_ns",
            "throughput_per_second",
            "peak_rss_bytes",
            "output_size_bytes",
        }:
            raise NormalizationError(f"{identifier} does not have four required metrics")
        for field, value in metrics.items():
            _positive_number(value, f"{identifier}.{field}")
    if [entry["id"] for entry in measurements] != sorted(identifiers):
        raise NormalizationError(f"{label} measurements are not canonically sorted")


def _raw_document(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    document = {"measurements": measurements}
    _validate_raw_document(document)
    return document


def _validate_raw_document(document: Any) -> None:
    if not isinstance(document, dict) or set(document) != {"measurements"}:
        raise NormalizationError(
            "raw document must contain exactly the measurements field"
        )
    _validate_measurements(document["measurements"], "raw document")


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise NormalizationError("metadata captured_at_utc must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise NormalizationError(
            "metadata captured_at_utc must be an ISO 8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise NormalizationError("metadata captured_at_utc must use UTC")


def _validate_metadata(metadata: Any) -> None:
    if not isinstance(metadata, dict):
        raise NormalizationError("bound document metadata must be an object")
    required = {
        "git_commit",
        "dirty",
        "captured_at_utc",
        "os",
        "architecture",
        "cpu",
        "logical_cpus",
        "memory_bytes",
        "rustc",
        "cargo",
        "benchmark_manifest_sha256",
        "benchmark_lockfile_sha256",
        "suite_digest_sha256",
    }
    missing = sorted(required - set(metadata))
    if missing:
        raise NormalizationError(
            f"bound document metadata omits fields: {', '.join(missing)}"
        )
    if metadata["dirty"] is not False:
        raise NormalizationError("metadata must identify a clean tree")
    commit = metadata["git_commit"]
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        raise NormalizationError("metadata git_commit must be 40 lowercase hex digits")
    _validate_timestamp(metadata["captured_at_utc"])
    for field in ("os", "architecture", "cpu", "rustc", "cargo"):
        if not isinstance(metadata[field], str) or not metadata[field].strip():
            raise NormalizationError(f"metadata {field} must be a non-empty string")
    for field in ("logical_cpus", "memory_bytes"):
        value = metadata[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise NormalizationError(f"metadata {field} must be a positive integer")
    for field in (
        "benchmark_manifest_sha256",
        "benchmark_lockfile_sha256",
        "suite_digest_sha256",
    ):
        value = metadata[field]
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise NormalizationError(f"metadata {field} must be lowercase SHA-256")


def _validate_bound_document(
    document: Any, raw_document: dict[str, Any]
) -> None:
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "suite_id",
        "metadata",
        "raw_measurement_artifacts",
        "measurements",
    }:
        raise NormalizationError("bound document has an unexpected field inventory")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise NormalizationError("bound document must use schema_version 1")
    if document["suite_id"] != "BENCH-FORMAT-FOUNDATION-V1":
        raise NormalizationError("bound document has an unexpected suite_id")
    _validate_metadata(document["metadata"])
    artifacts = document["raw_measurement_artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise NormalizationError("bound document must name exactly one raw artifact")
    artifact = artifacts[0]
    if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
        raise NormalizationError("bound document raw artifact is malformed")
    if (
        not isinstance(artifact["path"], str)
        or REPOSITORY_PATH_PATTERN.fullmatch(artifact["path"]) is None
    ):
        raise NormalizationError("bound document raw artifact path is invalid")
    if (
        not isinstance(artifact["sha256"], str)
        or SHA256_PATTERN.fullmatch(artifact["sha256"]) is None
    ):
        raise NormalizationError("bound document raw artifact hash is invalid")
    raw_bytes = (
        json.dumps(raw_document, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if artifact["sha256"] != hashlib.sha256(raw_bytes).hexdigest():
        raise NormalizationError("bound document raw artifact hash differs from raw output")
    _validate_measurements(document["measurements"], "bound document")
    if document["measurements"] != raw_document["measurements"]:
        raise NormalizationError("bound and raw document measurements differ")


def _stage_document(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            staged_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        return staged_path
    except OSError as error:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
        raise NormalizationError(f"cannot stage output {path}: {error}") from error


def _publish_documents(outputs: list[tuple[Path, dict[str, Any]]]) -> None:
    destinations = [path.absolute() for path, _ in outputs]
    if len(destinations) != len(set(destinations)):
        raise NormalizationError("related output destinations must be distinct")
    staged: list[tuple[Path, Path]] = []
    try:
        for path, document in outputs:
            staged.append((_stage_document(path, document), path))
        for temporary, path in staged:
            try:
                temporary.replace(path)
            except OSError as error:
                raise NormalizationError(f"cannot publish output {path}: {error}") from error
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def _bound_document(
    metadata_path: Path,
    raw_artifact_path: str,
    raw_bytes: bytes,
    repository_root: Path,
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    if REPOSITORY_PATH_PATTERN.fullmatch(raw_artifact_path) is None:
        raise NormalizationError("raw artifact path is not a repository-relative path")
    metadata = _load_object(metadata_path, "metadata")
    if metadata.get("dirty") is not False:
        raise NormalizationError("metadata must identify a clean tree")
    commit = metadata.get("git_commit")
    if not isinstance(commit, str):
        raise NormalizationError("metadata has no git_commit")
    try:
        retained_raw = retained_blob(repository_root, commit, raw_artifact_path)
        suite_digest = digest_for_commit(repository_root, commit)
        manifest_blob = retained_blob(repository_root, commit, "benches/manifest.json")
        lockfile_blob = retained_blob(repository_root, commit, "benches/Cargo.lock")
    except SuiteIdentityError as error:
        raise NormalizationError(f"commit binding failed: {error}") from error
    checks = {
        "suite_digest_sha256": suite_digest,
        "benchmark_manifest_sha256": hashlib.sha256(manifest_blob).hexdigest(),
        "benchmark_lockfile_sha256": hashlib.sha256(lockfile_blob).hexdigest(),
    }
    for field, expected in checks.items():
        if metadata.get(field) != expected:
            raise NormalizationError(f"metadata {field} does not match retained commit")
    if retained_raw != raw_bytes:
        raise NormalizationError("raw measurement artifact differs from retained commit")
    return {
        "schema_version": 1,
        "suite_id": "BENCH-FORMAT-FOUNDATION-V1",
        "metadata": metadata,
        "raw_measurement_artifacts": [
            {
                "path": raw_artifact_path,
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            }
        ],
        "measurements": measurements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--criterion-root", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "manifest.json",
    )
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--raw-artifact-path")
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    arguments = parser.parse_args(argv)
    binding_values = (
        arguments.metadata,
        arguments.output,
        arguments.raw_artifact_path,
    )
    if any(value is not None for value in binding_values) and not all(
        value is not None for value in binding_values
    ):
        parser.error("--metadata, --output, and --raw-artifact-path must be used together")
    try:
        measurements = normalize(
            arguments.criterion_root, arguments.manifest, arguments.resources
        )
        raw_document = _raw_document(measurements)
        raw_bytes = (
            json.dumps(raw_document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        outputs = [(arguments.raw_output, raw_document)]
        if arguments.output is not None:
            document = _bound_document(
                arguments.metadata,
                arguments.raw_artifact_path,
                raw_bytes,
                arguments.repository_root,
                measurements,
            )
            _validate_bound_document(document, raw_document)
            outputs.append((arguments.output, document))
        _publish_documents(outputs)
    except NormalizationError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
