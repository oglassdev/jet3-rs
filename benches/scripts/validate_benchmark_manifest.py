#!/usr/bin/env python3
"""Validate the binding G7 benchmark ledger and its retained artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

BENCHMARK_ID = re.compile(r"^BENCH-[A-Z0-9][A-Z0-9_-]*$")
TRACEABILITY_ID = re.compile(r"^[A-Z]+-[0-9]{2}$")
REPOSITORY_PATH = re.compile(
    r"^[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*"
    r"(?:/[A-Za-z0-9_-]+(?:[.][A-Za-z0-9_-]+)*)*$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OPERATIONS = {
    "open",
    "catalog_load",
    "table_scan",
    "indexed_lookup",
    "database_create",
    "insert",
    "update",
    "delete",
    "memo_ole_access",
    "semantic_verification",
    "adversarial_parse",
}
BASELINE_FIELDS = {
    "id",
    "traceability_ids",
    "operation",
    "scenario_id",
    "row_count",
    "artifacts",
    "hardware",
    "os",
    "toolchain",
    "sample_count",
    "median_latency_ns",
    "latency_percentiles_ns",
    "throughput_per_second",
    "peak_rss_bytes",
    "output_size_bytes",
}


class ManifestError(ValueError):
    """The proposed binding benchmark ledger is not trustworthy."""


def validate_contract(schema: dict[str, Any]) -> None:
    """Fail closed if the binding schema changes beyond this validator."""
    try:
        baseline = schema["$defs"]["baseline"]
        properties = baseline["properties"]
        required = baseline["required"]
        operations = properties["operation"]["enum"]
    except (KeyError, TypeError) as error:
        raise ManifestError("binding benchmark schema has an unexpected shape") from error
    if schema.get("$id") != "urn:jet3-rs:validation:benchmark-manifest:1":
        raise ManifestError("binding benchmark schema id is not version 1")
    if set(required) != BASELINE_FIELDS or set(properties) != BASELINE_FIELDS:
        raise ManifestError("validator and binding baseline fields differ")
    if set(operations) != OPERATIONS:
        raise ManifestError("validator and binding operation inventory differ")


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(f"{label} must be an integer >= {minimum}")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _artifact(
    value: Any, label: str, repository_root: Path | None
) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ManifestError(f"{label} must contain exactly path and sha256")
    path = value["path"]
    digest = value["sha256"]
    if not isinstance(path, str) or REPOSITORY_PATH.fullmatch(path) is None:
        raise ManifestError(f"{label}.path is not a repository path")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise ManifestError(f"{label}.sha256 is not lowercase SHA-256")
    if repository_root is not None:
        try:
            root = repository_root.resolve(strict=True)
            candidate = root / path
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise ManifestError(f"{label} cannot be read: {error}") from error
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ManifestError(f"{label} escapes the repository") from error
        if candidate.is_symlink() or not resolved.is_file():
            raise ManifestError(f"{label} is not a regular repository file")
        try:
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError as error:
            raise ManifestError(f"{label} cannot be read: {error}") from error
        if actual != digest:
            raise ManifestError(f"{label} hash does not match")
    return path, digest


def validate(document: dict[str, Any], repository_root: Path | None = None) -> None:
    """Validate schema-v1 structure plus non-vacuity and artifact integrity."""
    if set(document) != {"schema_version", "baselines"}:
        raise ManifestError("ledger must contain exactly schema_version and baselines")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ManifestError("ledger must use schema_version 1")
    baselines = document["baselines"]
    if not isinstance(baselines, list) or not baselines:
        raise ManifestError("baselines must be a non-empty array")
    seen_ids: set[str] = set()
    for index, baseline in enumerate(baselines):
        label = f"baselines[{index}]"
        if not isinstance(baseline, dict) or set(baseline) != BASELINE_FIELDS:
            raise ManifestError(f"{label} does not match the binding baseline fields")
        identifier = baseline["id"]
        if not isinstance(identifier, str) or BENCHMARK_ID.fullmatch(identifier) is None:
            raise ManifestError(f"{label}.id is invalid")
        if identifier in seen_ids:
            raise ManifestError(f"duplicate baseline id {identifier}")
        seen_ids.add(identifier)

        traceability = baseline["traceability_ids"]
        if not isinstance(traceability, list) or not traceability:
            raise ManifestError(f"{label}.traceability_ids must be non-empty")
        if len(set(traceability)) != len(traceability) or any(
            not isinstance(value, str)
            or TRACEABILITY_ID.fullmatch(value) is None
            for value in traceability
        ):
            raise ManifestError(f"{label}.traceability_ids are invalid or duplicated")
        if baseline["operation"] not in OPERATIONS:
            raise ManifestError(f"{label}.operation is invalid")
        _nonempty(baseline["scenario_id"], f"{label}.scenario_id")
        _integer(baseline["row_count"], f"{label}.row_count")
        for field in ("hardware", "os", "toolchain"):
            _nonempty(baseline[field], f"{label}.{field}")
        _integer(baseline["sample_count"], f"{label}.sample_count", 2)
        median = _integer(baseline["median_latency_ns"], f"{label}.median_latency_ns")

        percentiles = baseline["latency_percentiles_ns"]
        if not isinstance(percentiles, dict) or set(percentiles) != {"p50", "p90", "p99"}:
            raise ManifestError(f"{label}.latency_percentiles_ns is invalid")
        p50 = _integer(percentiles["p50"], f"{label}.p50")
        p90 = _integer(percentiles["p90"], f"{label}.p90")
        p99 = _integer(percentiles["p99"], f"{label}.p99")
        if not p50 <= p90 <= p99:
            raise ManifestError(f"{label} latency percentiles are not ordered")
        if median != p50:
            raise ManifestError(f"{label} median_latency_ns must equal p50")

        throughput = baseline["throughput_per_second"]
        if (
            isinstance(throughput, bool)
            or not isinstance(throughput, (int, float))
            or not math.isfinite(float(throughput))
            or throughput <= 0
        ):
            raise ManifestError(f"{label}.throughput_per_second must be finite and positive")
        _integer(baseline["peak_rss_bytes"], f"{label}.peak_rss_bytes")
        _integer(baseline["output_size_bytes"], f"{label}.output_size_bytes")

        artifacts = baseline["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise ManifestError(f"{label}.artifacts must be non-empty")
        normalized = [
            _artifact(artifact, f"{label}.artifacts[{artifact_index}]", repository_root)
            for artifact_index, artifact in enumerate(artifacts)
        ]
        if len({path for path, _digest in normalized}) != len(normalized):
            raise ManifestError(f"{label}.artifacts contains duplicates")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "docs/validation/schema/benchmark-manifest.schema.json",
    )
    arguments = parser.parse_args(argv)
    try:
        schema = json.loads(arguments.schema.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ManifestError("binding benchmark schema must be a JSON object")
        validate_contract(schema)
        document = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ManifestError("ledger must be a JSON object")
        validate(document, arguments.repository_root)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ManifestError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    print("PASS: benchmark ledger matches the binding schema and evidence checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
