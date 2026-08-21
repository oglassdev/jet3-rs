#!/usr/bin/env python3
"""Record bounded physical-page observations from a validated M1 DAO bundle."""

from __future__ import annotations

# Provenance usage: EXP-0008.
import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m1_bundle_validation import (
    MAX_DATABASE_BYTES,
    bounded_file_identity,
    load_json,
)
from protocol_validation import ValidationError
from validate_m1_protocol import validate_bundle

# Microsoft page-size source SRC-0005 establishes the Jet 3 2 KiB page size.
PAGE_SIZE = 2048
MAX_SCENARIOS = 7
MAX_PAIRS = 2


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_database(path: Path) -> bytes:
    size, _, retained = bounded_file_identity(
        path, MAX_DATABASE_BYTES, retain=True
    )
    assert retained is not None
    if size == 0 or size % PAGE_SIZE:
        raise ValidationError(f"{path}: database is not page aligned")
    return retained


def page_hashes(value: bytes) -> list[str]:
    if not value or len(value) % PAGE_SIZE:
        raise ValidationError("database bytes must be nonempty and page aligned")
    return [
        sha256_bytes(value[offset : offset + PAGE_SIZE])
        for offset in range(0, len(value), PAGE_SIZE)
    ]


def analyze_pair(left: bytes, right: bytes) -> dict[str, Any]:
    left_pages = page_hashes(left)
    right_pages = page_hashes(right)
    page_count = max(len(left_pages), len(right_pages))
    differing_pages = [
        index
        for index in range(page_count)
        if index >= len(left_pages)
        or index >= len(right_pages)
        or left_pages[index] != right_pages[index]
    ]
    common = min(len(left), len(right))
    differing_byte_count = 0
    first_differing_offset: int | None = None
    last_differing_offset: int | None = None
    for index in range(common):
        if left[index] == right[index]:
            continue
        differing_byte_count += 1
        if first_differing_offset is None:
            first_differing_offset = index
        last_differing_offset = index
    return {
        "common_length": common,
        "differing_byte_count_in_common_length": differing_byte_count,
        "differing_page_indices": differing_pages,
        "first_differing_byte_offset": first_differing_offset,
        "last_differing_byte_offset": last_differing_offset,
        "left_only_bytes": max(0, len(left) - common),
        "right_only_bytes": max(0, len(right) - common),
    }


def require_clean_commit(repository: Path, expected: str) -> None:
    if len(expected) != 40 or any(char not in "0123456789abcdef" for char in expected):
        raise ValidationError("observer commit must be 40 lowercase hexadecimal digits")
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    if head != expected or dirty:
        raise ValidationError("observer requires the exact clean Git commit")


def build_observation(
    bundle: Path, manifest_sha256: str, observer_commit: str
) -> dict[str, Any]:
    manifest_path = bundle / "bundle-manifest.json"
    _, actual_manifest_hash, _ = bounded_file_identity(
        manifest_path, 1024 * 1024
    )
    if actual_manifest_hash != manifest_sha256:
        raise ValidationError("M1 manifest hash differs from the required identity")
    validate_bundle(bundle)
    manifest = load_json(manifest_path)
    report = load_json(bundle / manifest["report_path"])
    if (
        report["status"] != "pass"
        or len(report["scenarios"]) != MAX_SCENARIOS
        or len(report["pairs"]) != MAX_PAIRS
    ):
        raise ValidationError("M2 requires the complete passing M1 inventory")

    databases: dict[str, bytes] = {}
    scenarios: list[dict[str, Any]] = []
    for result in report["scenarios"]:
        reference = result["output_database"]
        if result["status"] != "pass" or reference is None:
            raise ValidationError("M2 requires a database for every M1 scenario")
        database = read_database(bundle / reference["path"])
        if sha256_bytes(database) != reference["sha256"]:
            raise ValidationError("M1 report database reference differs")
        scenario_id = result["scenario_id"]
        databases[scenario_id] = database
        hashes = page_hashes(database)
        scenarios.append(
            {
                "database_sha256": reference["sha256"],
                "page_count": len(hashes),
                "page_sha256": hashes,
                "scenario_id": scenario_id,
                "size_bytes": len(database),
            }
        )

    pairs: list[dict[str, Any]] = []
    for result in report["pairs"]:
        pair_input = load_json(bundle / result["input"]["path"])
        left_id = result["left_scenario_id"]
        right_id = result["right_scenario_id"]
        pairs.append(
            {
                **analyze_pair(databases[left_id], databases[right_id]),
                "allowed_semantic_difference_paths": pair_input[
                    "allowed_difference_paths"
                ],
                "left_scenario_id": left_id,
                "observed_semantic_difference_paths": result[
                    "observed_difference_paths"
                ],
                "pair_id": result["pair_id"],
                "right_scenario_id": right_id,
            }
        )

    return {
        "bundle_git_commit": manifest["git_commit"],
        "bundle_manifest_sha256": manifest_sha256,
        "bundle_run_id": manifest["run_id"],
        "document_type": "m1_physical_page_observation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "interpretation": (
            "Descriptive page and byte differences only; no MDB field, "
            "offset, structure, or compatibility conclusion is asserted."
        ),
        "observer_git_commit": observer_commit,
        "page_size": PAGE_SIZE,
        "pairs": pairs,
        "protocol_version": "1.0.0",
        "scenarios": scenarios,
    }


def publish_atomic(output: Path, document: dict[str, Any]) -> None:
    output = output.resolve()
    if output.exists():
        raise ValidationError("M2 output collision")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".m2-stage-{uuid.uuid4().hex}"
    payload = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    try:
        with stage.open("xb") as handle:
            os.chmod(stage, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(stage, output)
    finally:
        stage.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repository = args.repository_root.resolve()
        output = args.output.resolve()
        if output == repository or repository in output.parents:
            raise ValidationError("M2 output must remain outside the repository")
        require_clean_commit(repository, args.git_commit)
        observation = build_observation(
            args.bundle.resolve(),
            args.manifest_sha256,
            args.git_commit,
        )
        require_clean_commit(repository, args.git_commit)
        publish_atomic(output, observation)
        print(f"PASS: retained bounded M1 page observation at {output}")
        return 0
    except (OSError, subprocess.SubprocessError, ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
