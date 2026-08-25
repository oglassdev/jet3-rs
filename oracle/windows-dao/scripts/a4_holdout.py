#!/usr/bin/env python3
"""Post-freeze structural validation for the DAO A4 holdout replica."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from a4_bundle import (
    DERIVATION_REPLICA_COUNT,
    MAX_BUNDLE_BYTES,
    MAX_JSON_BYTES,
    PLAN_PATH,
    REPLICA_COUNT,
    ReplicaResult,
    _copy_replica,
    _inventory,
    _validate_environments,
    _validate_frozen,
    _validate_replica,
)
from a4_bundle_io import ArtifactCache, TreeFile
from a4_spec import (
    BOUNDS, EXPERIMENT_ID, PLAN_SHA256, REVISION_PLAN_SHA256,
    load_bounded_json, validate_schema,
)
from protocol_validation import ValidationError, canonical_json_bytes

FAN_IN_TIMEOUT_SECONDS = 900
HOLDOUT_TIMEOUT_SECONDS = 300
if BOUNDS["fan_in_timeout_seconds"] != FAN_IN_TIMEOUT_SECONDS:
    raise RuntimeError("checked A4 fan-in bound drifted")


def holdout_absent(bundle_root: Path) -> bool:
    """True while no holdout-replica artifact exists anywhere under the bundle."""
    tree, _ = _inventory(bundle_root.absolute())
    marker = f"replica-{REPLICA_COUNT:02d}"
    return not any(marker in path for path in tree)


def graft_holdout_replica(
    holdout_root: Path,
    bundle_root: Path,
    candidate_set: Path,
    candidate_sha256: str,
    campaign_id: str,
    producer_commit: str,
    *,
    bundle_tree: dict[str, TreeFile] | None = None,
    bundle_directories: set[str] | None = None,
    bundle_cache: ArtifactCache | None = None,
) -> ReplicaResult:
    """Copy the closed holdout replica into a bundle that holds only the frozen derivation set.

    The retained frozen candidate bytes are hash-checked before a single holdout
    byte is inventoried, so the graft itself is the freeze-order observable."""
    bundle_root = bundle_root.absolute()
    if bundle_tree is None or bundle_directories is None:
        if (
            bundle_tree is not None
            or bundle_directories is not None
            or bundle_cache is not None
        ):
            raise ValidationError("partial A4 holdout validation context")
        bundle_tree, bundle_directories = _inventory(bundle_root)
    if bundle_cache is None:
        bundle_cache = ArtifactCache(bundle_root, bundle_tree, MAX_BUNDLE_BYTES)
    elif bundle_cache.root != bundle_root or bundle_cache.tree is not bundle_tree:
        raise ValidationError("A4 holdout cache does not match its inventory")
    if any(f"replica-{REPLICA_COUNT:02d}" in path for path in bundle_tree):
        raise ValidationError("holdout replica artifacts already present before graft")
    candidate_locator = candidate_set.absolute().relative_to(bundle_root).as_posix()
    derivation = tuple(
        _validate_replica(
            bundle_root, bundle_tree, bundle_directories, bundle_cache,
            replica, campaign_id, closed=False,
        )
        for replica in range(1, DERIVATION_REPLICA_COUNT + 1)
    )
    expected = {PLAN_PATH, candidate_locator}
    for replica in derivation:
        expected.add(replica.manifest_path)
        expected.update(replica.entries)
    if set(bundle_tree) != expected:
        raise ValidationError(
            "bundle holds more than the plan, derivation replicas, and frozen set"
        )
    frozen_payload = bundle_cache.read(candidate_locator, MAX_JSON_BYTES)
    if bundle_cache.sha256(candidate_locator, MAX_JSON_BYTES) != candidate_sha256:
        raise ValidationError(f"frozen candidate hash: expected {candidate_sha256}")
    frozen = bundle_cache.json(candidate_locator, MAX_JSON_BYTES)[0]
    _validate_frozen(frozen, frozen_payload, campaign_id)
    root = Path(holdout_root).absolute()
    tree, directories = _inventory(root)
    holdout_cache = ArtifactCache(root, tree, MAX_BUNDLE_BYTES)
    holdout = _validate_replica(
        root, tree, directories, holdout_cache,
        REPLICA_COUNT, campaign_id, closed=True,
    )
    _validate_environments((*derivation, holdout))
    if holdout.producer_commit != producer_commit:
        raise ValidationError("expected holdout producer commit")
    copied = {
        replica.manifest_path: (replica.manifest_size, replica.manifest_sha256)
        for replica in derivation
    }
    for replica in derivation:
        copied.update({
            path: (entry["size_bytes"], entry["sha256"])
            for path, entry in replica.entries.items()
        })
    _copy_replica(holdout, bundle_root, copied)

    grafted_tree, grafted_directories = _inventory(bundle_root)
    if any(grafted_tree.get(path) != item for path, item in bundle_tree.items()):
        raise ValidationError("derivation bundle changed while grafting the holdout")
    bundle_cache.tree = grafted_tree
    grafted = _validate_replica(
        bundle_root, grafted_tree, grafted_directories, bundle_cache,
        REPLICA_COUNT, campaign_id, closed=False,
    )
    if grafted.producer_commit != producer_commit:
        raise ValidationError("expected grafted holdout producer commit")
    if grafted.manifest_sha256 != holdout.manifest_sha256:
        raise ValidationError("grafted holdout replica changed during structural validation")
    return grafted


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
    resolved_bundle = bundle_root.absolute()
    candidate_locator = candidate_set.absolute().relative_to(resolved_bundle).as_posix()
    bundle_tree, bundle_directories = _inventory(resolved_bundle)
    bundle_cache = ArtifactCache(resolved_bundle, bundle_tree, MAX_BUNDLE_BYTES)
    absent_before_graft = not any(
        f"replica-{REPLICA_COUNT:02d}" in path for path in bundle_tree
    )
    frozen_digest = bundle_cache.sha256(candidate_locator, MAX_JSON_BYTES)
    validated_after_candidate_freeze = (
        absent_before_graft
        and frozen_digest == candidate_sha256
        and state.get("document_type") == "dao_a4_internal_freeze_phase"
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
    replica = graft_holdout_replica(
        holdout_root,
        bundle_root,
        candidate_set,
        candidate_sha256,
        campaign_id,
        producer_commit,
        bundle_tree=bundle_tree,
        bundle_directories=bundle_directories,
        bundle_cache=bundle_cache,
    )
    receipt: dict[str, object] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_holdout_structure_receipt",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": replica.producer_commit,
        "campaign_id": replica.campaign_id,
        "derivation_candidate_set_sha256": candidate_sha256,
        "replica": replica.replica,
        "replica_artifact_manifest_sha256": replica.manifest_sha256,
        "validated_after_candidate_freeze": validated_after_candidate_freeze,
        "page_bytes_exposed_to_analyzer": page_bytes_exposed_to_analyzer,
        "result": "pass",
    }
    validate_schema(receipt, "dao_a4_holdout_structure_receipt")
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
        print(f"A4 holdout validation failed: {exc}", file=sys.stderr)
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
