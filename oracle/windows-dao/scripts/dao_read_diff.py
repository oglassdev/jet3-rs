#!/usr/bin/env python3
"""Canonicalize and compare protocol 1.2 DAO read snapshots."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import validate_protocol_v1_2 as protocol
from protocol_validation import ValidationError, load_json


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonicalize_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    """Order a producer document and derive content-addressed row identities."""
    result = copy.deepcopy(document)

    def normalize_typed_values(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("kind") in ("single", "double") and isinstance(
                value.get("value"), str
            ):
                parsed = float(value["value"])
                if not math.isfinite(parsed):
                    raise ValidationError("DAO snapshot contains a non-finite float")
                value["value"] = parsed
            for child in value.values():
                normalize_typed_values(child)
        elif isinstance(value, list):
            for child in value:
                normalize_typed_values(child)

    normalize_typed_values(result)
    result["tables"].sort(key=lambda table: table["name"])
    for table in result["tables"]:
        table["columns"].sort(key=lambda column: column["ordinal"])
        table["indexes"].sort(key=lambda index: index["name"])
        keyed_rows: list[tuple[str, bytes, dict[str, Any]]] = []
        for row in table["rows"]:
            values_bytes = canonical_bytes(row["values"])
            key = hashlib.sha256(values_bytes).hexdigest()
            row["canonical_key"] = key
            keyed_rows.append((key, values_bytes, row))
        keyed_rows.sort(key=lambda item: (item[0], item[1]))
        duplicate_counts: dict[bytes, int] = {}
        table["rows"] = []
        for _, values_bytes, row in keyed_rows:
            row["duplicate_ordinal"] = duplicate_counts.get(values_bytes, 0)
            duplicate_counts[values_bytes] = row["duplicate_ordinal"] + 1
            table["rows"].append(row)
    result["relationships"].sort(key=lambda relationship: relationship["name"])
    protocol.validate_document(result)
    return result


def comparison_document(document: dict[str, Any]) -> dict[str, Any]:
    projection = document["comparison_projection"]
    if projection != ["/producer", "/producer_extensions"]:
        raise ValidationError("snapshot has an unexpected comparison projection")
    result = copy.deepcopy(document)
    del result["producer"]
    del result["producer_extensions"]
    return result


def compare_snapshots(
    dao: dict[str, Any], rust: dict[str, Any]
) -> dict[str, Any]:
    if protocol.validate_document(dao) != "canonical_semantic_snapshot":
        raise ValidationError("DAO input is not a semantic snapshot")
    if protocol.validate_document(rust) != "canonical_semantic_snapshot":
        raise ValidationError("Rust input is not a semantic snapshot")
    if dao["producer"]["kind"] != "dao":
        raise ValidationError("DAO input has the wrong producer kind")
    if rust["producer"]["kind"] != "rust":
        raise ValidationError("Rust input has the wrong producer kind")
    for field in ("protocol_version", "scenario_id", "database_sha256"):
        if dao[field] != rust[field]:
            raise ValidationError(f"snapshot pair differs at {field}")
    dao_projection = canonical_bytes(comparison_document(dao))
    rust_projection = canonical_bytes(comparison_document(rust))
    if dao_projection != rust_projection:
        raise ValidationError("DAO and Rust comparison projections differ")
    return {
        "database_sha256": dao["database_sha256"],
        "document_type": "dao_read_comparison",
        "matched": True,
        "projection_sha256": hashlib.sha256(dao_projection).hexdigest(),
        "protocol_version": "1.2.0",
        "scenario_id": dao["scenario_id"],
    }


def write_canonical(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(document))


def minimal_snapshot(kind: str) -> dict[str, Any]:
    return {
        "comparison_projection": ["/producer", "/producer_extensions"],
        "database_properties": {},
        "database_sha256": "0" * 64,
        "document_type": "canonical_semantic_snapshot",
        "ordering": {
            "columns": "ordinal_ascending",
            "indexes": "name_codepoint_ascending",
            "object_keys": "unicode_codepoint_ascending",
            "objects": "name_codepoint_ascending",
            "relationships": "name_codepoint_ascending",
            "rows": "values_sha256_then_duplicate_ordinal",
        },
        "producer": {"kind": kind, "source_revision": "synthetic"},
        "producer_extensions": {},
        "protocol_version": "1.2.0",
        "raw_preservation": [],
        "relationships": [],
        "scenario_id": "DAO-READ-OPEN-EMPTY",
        "tables": [],
    }


def synthetic_dry_run(output: Path) -> None:
    dao = minimal_snapshot("dao")
    rust = minimal_snapshot("rust")
    matching = compare_snapshots(dao, rust)
    mismatched = copy.deepcopy(rust)
    mismatched["database_properties"]["Name"] = {
        "kind": "text",
        "value": "different",
        "raw_hex": "646966666572656e74",
        "code_page": 1252,
    }
    rejected = False
    try:
        compare_snapshots(dao, mismatched)
    except ValidationError:
        rejected = True
    if not rejected:
        raise ValidationError("synthetic mismatch was not rejected")
    write_canonical(
        output,
        {
            "compatibility_claim": False,
            "document_type": "dao_read_synthetic_dry_run",
            "matching_pair_accepted": matching["matched"],
            "mismatched_pair_rejected": rejected,
            "protocol_version": "1.2.0",
        },
    )


def evaluate_acquisition(
    manifest_path: Path,
    artifact_root: Path,
    rust_executable: Path,
    inventory_path: Path,
    source_revision: str,
    output: Path,
) -> None:
    """Run the Rust producer over one complete DAO acquisition."""
    manifest = load_json(manifest_path)
    inventory = load_json(inventory_path)
    if protocol.validate_document(inventory) != "dao_scenario_inventory":
        raise ValidationError("acquisition inventory has the wrong document type")
    if manifest.get("document_type") != "dao_read_manifest":
        raise ValidationError("acquisition manifest has the wrong document type")
    if manifest.get("protocol_version") != "1.2.0":
        raise ValidationError("acquisition manifest has the wrong protocol version")
    if manifest.get("source_revision") != source_revision:
        raise ValidationError("acquisition manifest has the wrong source revision")
    scenarios = {scenario["id"]: scenario for scenario in inventory["scenarios"]}
    entries = manifest.get("scenarios")
    if (
        not isinstance(entries, list)
        or len(entries) != len(scenarios)
        or not all(isinstance(entry, dict) for entry in entries)
        or {entry.get("scenario_id") for entry in entries} != set(scenarios)
    ):
        raise ValidationError("acquisition manifest does not cover the inventory exactly")
    results = []
    for entry in entries:
        scenario_id = entry["scenario_id"]
        if Path(entry["database"]).parts != (scenario_id, "database.mdb"):
            raise ValidationError(f"{scenario_id}: unsafe database artifact path")
        scenario = scenarios[scenario_id]
        expected_error = scenario["operation"]["expected_outcome"] == "expected_error"
        if entry.get("expected_error") is not expected_error:
            raise ValidationError(f"{scenario_id}: manifest expected outcome differs")
        database = artifact_root / entry["database"]
        database_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
        if database_sha256 != entry.get("database_sha256"):
            raise ValidationError(f"{scenario_id}: database digest differs from manifest")
        rust_root = artifact_root / scenario_id / "rust"
        try:
            completed = subprocess.run(
                [
                    str(rust_executable),
                    "snapshot",
                    str(database),
                    "--out",
                    str(rust_root),
                    "--scenario",
                    scenario_id,
                    "--source-revision",
                    source_revision,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired as error:
            raise ValidationError(
                f"{scenario_id}: Rust producer exceeded 900 seconds"
            ) from error
        if completed.returncode != 0:
            raise ValidationError(
                f"{scenario_id}: Rust producer failed: {completed.stderr.strip()}"
            )
        coverage = load_json(rust_root / "coverage.json")
        snapshot_path = rust_root / "snapshot.json"
        rust_snapshot = None if expected_error else load_json(snapshot_path)
        protocol.validate_pair(coverage, rust_snapshot)
        verdict = next(
            item for item in coverage["scenarios"] if item["id"] == scenario_id
        )
        if not verdict["satisfied"]:
            raise ValidationError(f"{scenario_id}: Rust coverage verdict is unsatisfied")
        comparison_sha256 = None
        if expected_error:
            if entry.get("snapshot") is not None or snapshot_path.exists():
                raise ValidationError(f"{scenario_id}: opening failure retained a snapshot")
        else:
            if Path(entry["snapshot"]).parts != (
                scenario_id,
                "dao-snapshot.raw.json",
            ):
                raise ValidationError(f"{scenario_id}: unsafe DAO snapshot path")
            dao_path = artifact_root / scenario_id / "dao-snapshot.json"
            dao = canonicalize_snapshot(load_json(artifact_root / entry["snapshot"]))
            write_canonical(dao_path, dao)
            comparison = compare_snapshots(dao, rust_snapshot)
            comparison_sha256 = comparison["projection_sha256"]
            write_canonical(
                artifact_root / scenario_id / "comparison.json", comparison
            )
        results.append(
            {
                "comparison_sha256": comparison_sha256,
                "database_sha256": database_sha256,
                "expected_error": expected_error,
                "matched": True,
                "scenario_id": scenario_id,
            }
        )
    results.sort(key=lambda item: item["scenario_id"])
    write_canonical(
        output,
        {
            "all_matched": True,
            "document_type": "dao_read_acquisition_report",
            "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "protocol_version": "1.2.0",
            "scenario_count": len(results),
            "scenarios": results,
            "source_revision": source_revision,
        },
    )


def validate_plan(plan_path: Path, repository_root: Path) -> None:
    plan = load_json(plan_path)
    if plan.get("document_type") != "dao_read_acquisition_plan":
        raise ValidationError("acquisition plan has the wrong document type")
    if plan.get("protocol_version") != "1.2.0":
        raise ValidationError("acquisition plan has the wrong protocol version")
    if plan.get("execution", {}).get("attempts") != 1:
        raise ValidationError("acquisition plan must permit exactly one attempt")
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValidationError("acquisition plan has no pinned inputs")
    for relative, expected in inputs.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValidationError(f"unsafe acquisition input path {relative!r}")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise ValidationError(f"invalid acquisition input digest for {relative}")
        actual = hashlib.sha256((repository_root / path).read_bytes()).hexdigest()
        if actual != expected:
            raise ValidationError(f"acquisition input digest differs for {relative}")
    source_trees = plan.get("source_trees")
    if not isinstance(source_trees, dict) or not source_trees:
        raise ValidationError("acquisition plan has no pinned source trees")
    for relative, expected in source_trees.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValidationError(f"unsafe source-tree path {relative!r}")
        actual = source_tree_sha256(repository_root / path)
        if actual != expected:
            raise ValidationError(f"acquisition source tree differs for {relative}")
    inventory = load_json(repository_root / "oracle/windows-dao/protocol/v1_2/scenarios.json")
    if len(inventory["scenarios"]) != plan["execution"]["scenario_count"]:
        raise ValidationError("acquisition scenario count differs from the inventory")


def source_tree_sha256(root: Path) -> str:
    """Hash relative file names and bytes for one checked source subtree."""
    if not root.is_dir():
        raise ValidationError(f"source tree is missing: {root}")
    hasher = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        hasher.update(relative)
        hasher.update(b"\0")
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    canonicalize = commands.add_parser("canonicalize")
    canonicalize.add_argument("input", type=Path)
    canonicalize.add_argument("output", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("dao", type=Path)
    compare.add_argument("rust", type=Path)
    compare.add_argument("output", type=Path)
    dry_run = commands.add_parser("synthetic-dry-run")
    dry_run.add_argument("output", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("manifest", type=Path)
    evaluate.add_argument("artifact_root", type=Path)
    evaluate.add_argument("rust_executable", type=Path)
    evaluate.add_argument("inventory", type=Path)
    evaluate.add_argument("source_revision")
    evaluate.add_argument("output", type=Path)
    plan = commands.add_parser("plan")
    plan.add_argument("plan", type=Path)
    plan.add_argument("repository_root", type=Path)
    return result


def main(arguments: list[str]) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "canonicalize":
            write_canonical(args.output, canonicalize_snapshot(load_json(args.input)))
        elif args.command == "compare":
            write_canonical(
                args.output,
                compare_snapshots(load_json(args.dao), load_json(args.rust)),
            )
        elif args.command == "synthetic-dry-run":
            synthetic_dry_run(args.output)
        elif args.command == "evaluate":
            evaluate_acquisition(
                args.manifest,
                args.artifact_root,
                args.rust_executable,
                args.inventory,
                args.source_revision,
                args.output,
            )
        else:
            validate_plan(args.plan, args.repository_root)
    except (OSError, ValueError, ValidationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
