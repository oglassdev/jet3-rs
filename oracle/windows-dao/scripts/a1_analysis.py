#!/usr/bin/env python3
"""Bounded, holdout-safe analysis for DAO-A1-ALLOCATION-MAPS-001.

The derivation replicas (1 and 2) alone produce the complete joint candidate
set. That set is frozen before the holdout replica is opened, and the holdout
is evaluated without refit, addition, deletion, relaxation, or reinterpretation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from a1_model import (
    HOLDOUT_PREDICTION_FAILURE,
    IDLE_VOLATILITY,
    MULTIPLE_SURVIVING_MODELS,
    NO_SURVIVING_MODEL,
    REPLICA_DISAGREEMENT,
    RESOURCE_BOUND_BREACH,
    UNRECONSTRUCTABLE_SNAPSHOT,
    Abort,
    PageStore,
    ReplicaIndexes,
    ReplicaView,
    WorkCounter,
    candidate_counts,
    derive,
    joint_shape,
    predicts_holdout,
    sole_model,
)
from a1_spec import (
    CANDIDATE_CEILING,
    CHECKPOINT_IDS,
    CLAIMS,
    EXPERIMENT_ID,
    PLAN_SHA256,
    REPLICAS,
    CheckedPlan,
    load_bounded_json,
    load_checked_plan,
    require_equal,
    validate_analysis_report,
    validate_page_index,
    validate_replica_observation,
)
from protocol_validation import ValidationError, canonical_json_bytes, sha256

DECISIVE_OUTCOME = "one_joint_model_predicts_holdout"
NO_OUTCOME = "no_scientific_outcome"
TERMINAL_REASONS = (IDLE_VOLATILITY, UNRECONSTRUCTABLE_SNAPSHOT, RESOURCE_BOUND_BREACH)
SHARED_BINDINGS = (
    "plan_sha256",
    "producer_commit",
    "repository_url",
    "run_id",
    "environment_sha256",
    "provider_sha256",
)


def _safe_bundle_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise ValidationError(f"unsafe A1 artifact path {relative!r}")
    resolved_root = root.resolve()
    resolved = (root / path).resolve()
    if resolved_root not in resolved.parents:
        raise ValidationError(f"A1 artifact escapes bundle root: {relative!r}")
    return resolved


def load_replica_indexes(
    plan: CheckedPlan, observation_path: Path, bundle_root: Path
) -> ReplicaIndexes:
    observation = validate_replica_observation(load_bounded_json(observation_path), plan)
    indexes: dict[str, dict[str, Any]] = {}
    prior: list[str] = []
    changed_total = 0
    for checkpoint in observation["checkpoints"]:
        reference = checkpoint["page_index"]
        path = _safe_bundle_path(bundle_root, reference["path"])
        if path.stat().st_size != reference["size_bytes"] or sha256(path) != reference["sha256"]:
            raise ValidationError(f"{reference['path']}: page-index artifact binding failed")
        index = load_bounded_json(path)
        hashes = validate_page_index(index, observation, checkpoint, prior)
        changed_total += len(index["changed_page_indices"])
        indexes[checkpoint["checkpoint_id"]] = index
        prior = hashes
    require_equal(changed_total, observation["changed_hash_entries"], "$.changed_hash_entries")
    return ReplicaIndexes(observation=observation, indexes=indexes)


def _require_bindings(replicas: list[ReplicaIndexes]) -> None:
    if [item.observation["replica"] for item in replicas] != list(range(1, REPLICAS + 1)):
        raise ValidationError("A1 analysis requires replicas 1, 2, and 3 in order")
    for key in SHARED_BINDINGS:
        require_equal(len({item.observation[key] for item in replicas}), 1, f"replica binding {key}")
    for item in replicas:
        if set(item.indexes) != set(CHECKPOINT_IDS):
            raise ValidationError("A1 analysis requires every planned checkpoint page index")


def _report(
    replicas: list[ReplicaIndexes],
    work: WorkCounter,
    examined: int,
    survivors: int,
    holdout_evaluated: bool,
    reasons: list[str],
    model: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_analysis_report",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "run_id": replicas[0].observation["run_id"],
        "producer_commit": replicas[0].observation["producer_commit"],
        "derivation_replicas": [1, 2],
        "holdout_replica": 3,
        "input_checkpoint_count": len(CHECKPOINT_IDS) * REPLICAS,
        "candidate_models_examined": examined,
        "derivation_survivor_count": survivors,
        "analysis_work_units": work.value,
        "holdout_evaluated": holdout_evaluated,
        "scientific_outcome": NO_OUTCOME if reasons else DECISIVE_OUTCOME,
        "no_outcome_reasons": sorted(set(reasons)),
        "surviving_model": None if reasons else model,
        "claims": CLAIMS,
    }


def build_analysis(
    plan: CheckedPlan,
    replicas: list[ReplicaIndexes],
    page_store_root: Path,
) -> dict[str, Any]:
    _require_bindings(replicas)
    work = WorkCounter()
    store = PageStore(page_store_root, work)
    reasons: list[str] = []
    examined = 0
    survivors = 0
    holdout_evaluated = False
    model: dict[str, Any] | None = None
    try:
        derivations = [derive(ReplicaView(item, store, work)) for item in replicas[:2]]
        if joint_shape(derivations[0]) != joint_shape(derivations[1]):
            raise Abort(REPLICA_DISAGREEMENT)
        candidates = candidate_counts(derivations[0], derivations[1], CANDIDATE_CEILING)
        examined = candidates.examined
        survivors = candidates.survivors
        if survivors == 0:
            raise Abort(NO_SURVIVING_MODEL)
        if survivors > 1:
            raise Abort(MULTIPLE_SURVIVING_MODELS)
        frozen = sole_model(derivations[0], candidates)
        holdout_evaluated = True
        try:
            holdout = derive(ReplicaView(replicas[2], store, work))
        except Abort as exc:
            if exc.reason in TERMINAL_REASONS:
                raise
            raise Abort(HOLDOUT_PREDICTION_FAILURE) from exc
        if not predicts_holdout(holdout, frozen):
            raise Abort(HOLDOUT_PREDICTION_FAILURE)
        model = frozen
    except Abort as exc:
        reasons.append(exc.reason)
    report = _report(replicas, work, examined, survivors, holdout_evaluated, reasons, model)
    validate_analysis_report(report)
    if len(canonical_json_bytes(report)) > plan.document["bounds"]["max_json_bytes"]:
        raise ValidationError("A1 analysis report exceeds JSON byte ceiling")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replica", action="append", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if len(arguments.replica) != REPLICAS:
            raise ValidationError("exactly three --replica paths are required")
        plan = load_checked_plan()
        replicas = [
            load_replica_indexes(plan, path, arguments.bundle_root) for path in arguments.replica
        ]
        report = build_analysis(plan, replicas, arguments.bundle_root / "page-store")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(canonical_json_bytes(report))
    except (OSError, ValidationError) as exc:
        print(f"A1 analysis failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"output": str(arguments.output), "scientific_outcome": report["scientific_outcome"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
