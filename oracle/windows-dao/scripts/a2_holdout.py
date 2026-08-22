#!/usr/bin/env python3
"""Post-freeze structural validation for the DAO A2 holdout replica."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from a2_bundle import MAX_JSON_BYTES, validate_holdout_replica
from a2_spec import BOUNDS, EXPERIMENT_ID, PLAN_SHA256, validate_holdout_structure_receipt
from protocol_validation import ValidationError, canonical_json_bytes

FAN_IN_TIMEOUT_SECONDS = 900
HOLDOUT_TIMEOUT_SECONDS = 300
if BOUNDS["fan_in_timeout_seconds"] != FAN_IN_TIMEOUT_SECONDS:
    raise RuntimeError("checked A2 fan-in bound drifted")


def run_holdout_process(
    bundle_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    output: Path,
) -> None:
    command = [
        sys.executable,
        "-B",
        str(Path(__file__)),
        "--bundle-root",
        str(bundle_root),
        "--candidate-set",
        str(candidate_set),
        "--candidate-sha256",
        candidate_sha256,
        "--output",
        str(output),
    ]
    try:
        completed = subprocess.run(
            command, check=False, timeout=HOLDOUT_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"holdout structural validator failed: {exc}") from exc
    if completed.returncode != 0:
        raise ValidationError("holdout structural validator returned nonzero")


def write_receipt(
    bundle_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    output: Path,
) -> dict[str, object]:
    replica = validate_holdout_replica(bundle_root, candidate_set, candidate_sha256)
    receipt: dict[str, object] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_holdout_structure_receipt",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": replica.producer_commit,
        "campaign_id": replica.campaign_id,
        "derivation_candidate_set_sha256": candidate_sha256,
        "replica": replica.replica,
        "replica_artifact_manifest_sha256": replica.manifest_sha256,
        "validated_after_candidate_freeze": True,
        "page_bytes_exposed_to_analyzer": False,
        "result": "pass",
    }
    validate_holdout_structure_receipt(receipt)
    payload = canonical_json_bytes(receipt)
    if len(payload) > MAX_JSON_BYTES:
        raise ValidationError("holdout receipt exceeds its fixed JSON bound")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(payload)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--candidate-set", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if len(arguments.candidate_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in arguments.candidate_sha256
        ):
            raise ValidationError("candidate SHA-256 is not lowercase hexadecimal")
        receipt = write_receipt(
            arguments.bundle_root,
            arguments.candidate_set,
            arguments.candidate_sha256,
            arguments.output,
        )
    except (OSError, ValidationError, ValueError) as exc:
        print(f"A2 holdout validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "replica_artifact_manifest_sha256": receipt[
                    "replica_artifact_manifest_sha256"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
