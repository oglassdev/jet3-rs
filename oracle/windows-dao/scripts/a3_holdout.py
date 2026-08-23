#!/usr/bin/env python3
"""Post-freeze structural validation for the DAO A3 holdout replica."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from a3_bundle import (
    DERIVATION_REPLICA_COUNT,
    MAX_JSON_BYTES,
    PLAN_PATH,
    REPLICA_COUNT,
    ReplicaResult,
    _copy_replica,
    _inventory,
    _parse_json,
    _read_checked,
    _validate_environments,
    _validate_frozen,
    _validate_replica,
    validate_holdout_replica,
)
from a3_spec import (
    BOUNDS, EXPERIMENT_ID, PLAN_SHA256, load_bounded_json, validate_document,
)
from protocol_validation import ValidationError, canonical_json_bytes

FAN_IN_TIMEOUT_SECONDS = 900
HOLDOUT_TIMEOUT_SECONDS = 300
if BOUNDS["fan_in_timeout_seconds"] != FAN_IN_TIMEOUT_SECONDS:
    raise RuntimeError("checked A3 fan-in bound drifted")


def holdout_absent(bundle_root: Path) -> bool:
    """True while no holdout-replica artifact exists anywhere under the bundle."""
    tree, _ = _inventory(bundle_root.resolve())
    marker = f"replica-{REPLICA_COUNT:02d}"
    return not any(marker in path for path in tree)


def graft_holdout_replica(
    holdout_root: Path,
    bundle_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    campaign_id: str,
    producer_commit: str,
) -> ReplicaResult:
    """Copy the closed holdout replica into a bundle that holds only the frozen derivation set.

    The frozen candidate set is re-read and hash-checked on disk before a single
    holdout byte is inventoried, so the graft itself is the freeze-order observable."""
    bundle_root = bundle_root.resolve()
    if not holdout_absent(bundle_root):
        raise ValidationError("holdout replica artifacts already present before graft")
    candidate_locator = candidate_set.resolve().relative_to(bundle_root).as_posix()
    tree, directories = _inventory(bundle_root)
    derivation = tuple(
        _validate_replica(bundle_root, tree, directories, replica, campaign_id, closed=False)
        for replica in range(1, DERIVATION_REPLICA_COUNT + 1)
    )
    expected = {PLAN_PATH, candidate_locator}
    for replica in derivation:
        expected.add(replica.manifest_path)
        expected.update(replica.entries)
    if set(tree) != expected:
        raise ValidationError("bundle holds more than the plan, derivation replicas, and frozen set")
    frozen_payload = _read_checked(bundle_root, candidate_locator, tree, MAX_JSON_BYTES)
    _require_sha256(frozen_payload, candidate_sha256, "frozen candidate hash")
    _validate_frozen(
        _parse_json(frozen_payload, candidate_set.as_posix()), frozen_payload, campaign_id)
    root = Path(holdout_root).resolve()
    tree, directories = _inventory(root)
    holdout = _validate_replica(root, tree, directories, REPLICA_COUNT, campaign_id, closed=True)
    _validate_environments((*derivation, holdout))
    if holdout.producer_commit != producer_commit:
        raise ValidationError("expected holdout producer commit")
    _copy_replica(holdout, bundle_root)
    return holdout


def _require_sha256(payload: bytes, expected: str, label: str) -> None:
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValidationError(f"{label}: expected {expected}")


def run_holdout_process(
    bundle_root: Path,
    holdout_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    campaign_id: str,
    producer_commit: str,
    output: Path,
    freeze_state: Path,
) -> None:
    command = [
        sys.executable,
        "-B",
        str(Path(__file__)),
        "--bundle-root",
        str(bundle_root),
        "--holdout-replica-root",
        str(holdout_root),
        "--candidate-set",
        str(candidate_set),
        "--candidate-sha256",
        candidate_sha256,
        "--campaign-id",
        campaign_id,
        "--producer-commit",
        producer_commit,
        "--output",
        str(output),
        "--freeze-state",
        str(freeze_state),
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
    holdout_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    campaign_id: str,
    producer_commit: str,
    output: Path,
    freeze_state: Path,
) -> dict[str, object]:
    # Read the workflow's completed freeze marker and retained candidate bytes before
    # touching the separately downloaded holdout root.
    state = load_bounded_json(freeze_state, MAX_JSON_BYTES)
    resolved_bundle = bundle_root.resolve()
    candidate_locator = candidate_set.resolve().relative_to(resolved_bundle).as_posix()
    bundle_tree, _ = _inventory(resolved_bundle)
    absent_before_graft = not any(
        f"replica-{REPLICA_COUNT:02d}" in path for path in bundle_tree
    )
    frozen_bytes = _read_checked(
        resolved_bundle, candidate_locator, bundle_tree, MAX_JSON_BYTES
    )
    frozen_digest = hashlib.sha256(frozen_bytes).hexdigest()
    validated_after_candidate_freeze = (
        absent_before_graft
        and frozen_digest == candidate_sha256
        and state.get("document_type") == "dao_a3_internal_freeze_phase"
        and state.get("campaign_id") == campaign_id
        and state.get("producer_commit") == producer_commit
        and state.get("derivation_candidate_set_sha256") == frozen_digest
        and state.get("freeze_phase_completed") is True
        and state.get(
            "replica_3_artifact_existed_before_freeze_phase_completed"
        ) is False
    )
    opens = state.get("analyzer_replica_3_opens_before_receipt")
    page_bytes_exposed_to_analyzer = (
        not isinstance(opens, bool) and isinstance(opens, int) and opens > 0
    )
    if not validated_after_candidate_freeze:
        raise ValidationError("holdout replica reached the bundle before the candidate freeze")
    if page_bytes_exposed_to_analyzer or opens != 0:
        raise ValidationError("analyzer opened replica 3 before receipt acceptance")
    grafted = graft_holdout_replica(
        holdout_root, bundle_root, candidate_set, candidate_sha256, campaign_id, producer_commit)
    replica = validate_holdout_replica(
        bundle_root, candidate_set, candidate_sha256, campaign_id, producer_commit)
    if replica.manifest_sha256 != grafted.manifest_sha256:
        raise ValidationError("grafted holdout replica changed during structural validation")
    receipt: dict[str, object] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_holdout_structure_receipt",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": replica.producer_commit,
        "campaign_id": replica.campaign_id,
        "derivation_candidate_set_sha256": candidate_sha256,
        "replica": replica.replica,
        "replica_artifact_manifest_sha256": replica.manifest_sha256,
        "validated_after_candidate_freeze": validated_after_candidate_freeze,
        "page_bytes_exposed_to_analyzer": page_bytes_exposed_to_analyzer,
        "result": "pass",
    }
    validate_document(receipt)
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
    parser.add_argument("--holdout-replica-root", type=Path, required=True)
    parser.add_argument("--candidate-set", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--producer-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-state", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if len(arguments.candidate_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in arguments.candidate_sha256
        ):
            raise ValidationError("candidate SHA-256 is not lowercase hexadecimal")
        receipt = write_receipt(
            arguments.bundle_root,
            arguments.holdout_replica_root,
            arguments.candidate_set,
            arguments.candidate_sha256,
            arguments.campaign_id,
            arguments.producer_commit,
            arguments.output,
            arguments.freeze_state,
        )
    except (OSError, ValidationError, ValueError) as exc:
        print(f"A3 holdout validation failed: {exc}", file=sys.stderr)
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
