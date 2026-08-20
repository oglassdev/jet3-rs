#!/usr/bin/env python3
"""Bounded, holdout-safe analysis for DAO-A1-ALLOCATION-MAPS-001."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a1_spec import (
    BASE_FORMULAS,
    CANDIDATE_CEILING,
    CHECKPOINT_IDS,
    CLAIMS,
    EXPERIMENT_ID,
    PAGE_SIZE,
    PLAN_SHA256,
    POINTER_LAYOUTS,
    WORK_CEILING,
    CheckedPlan,
    load_bounded_json,
    load_checked_plan,
    require_equal,
    validate_analysis_report,
    validate_page_index,
    validate_replica_observation,
)
from protocol_validation import ValidationError, canonical_json_bytes, sha256


@dataclass(frozen=True)
class ReplicaIndexes:
    observation: dict[str, Any]
    indexes: dict[str, dict[str, Any]]


class WorkCounter:
    def __init__(self) -> None:
        self.value = 0

    def charge(self, units: int) -> None:
        if units < 0 or self.value + units > WORK_CEILING:
            raise ValidationError("A1 analysis work-unit ceiling exceeded")
        self.value += units


class PageStore:
    """Lazy content-addressed page reader with exact hash and size checks."""

    def __init__(self, root: Path, work: WorkCounter) -> None:
        self.root = root
        self.work = work
        self.cache: dict[str, bytes] = {}

    def get(self, digest: str) -> bytes:
        retained = self.cache.get(digest)
        if retained is not None:
            return retained
        path = self.root / f"{digest}.page"
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size != PAGE_SIZE:
                raise ValidationError(f"page-store blob {digest} is missing or not exactly 2048 bytes")
            retained = path.read_bytes()
        except OSError as exc:
            raise ValidationError(f"cannot read page-store blob {digest}: {exc}") from exc
        self.work.charge(1)
        if hashlib.sha256(retained).hexdigest() != digest:
            raise ValidationError(f"page-store blob {digest} fails content-address check")
        self.cache[digest] = retained
        return retained


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
    observation = validate_replica_observation(
        load_bounded_json(observation_path), plan
    )
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


def _hashes(replica: ReplicaIndexes, checkpoint: str) -> list[str]:
    return replica.indexes[checkpoint]["ordered_page_sha256"]


def _idle_reasons(replicas: list[ReplicaIndexes]) -> list[str]:
    for replica in replicas:
        for left, right in (
            ("E0", "E0R"),
            ("L_REINSERT_SAME", "L_IDLE_REOPEN"),
            ("H_REL_1280", "H_IDLE_REOPEN"),
        ):
            if _hashes(replica, left) != _hashes(replica, right):
                return ["idle_volatility"]
    return []


def _hash_at(replica: ReplicaIndexes, checkpoint: str, page: int) -> str | None:
    values = _hashes(replica, checkpoint)
    return values[page] if page < len(values) else None


def _record_page_candidates(replica: ReplicaIndexes, work: WorkCounter) -> set[int]:
    maximum = max(len(_hashes(replica, checkpoint)) for checkpoint in CHECKPOINT_IDS)
    result: set[int] = set()
    for page in range(maximum):
        work.charge(8)
        d_grown = _hash_at(replica, "D_GROW_0128", page)
        d_drop = _hash_at(replica, "D_DROP", page)
        d_regrown = _hash_at(replica, "D_REGROW_0128", page)
        tracks_l = d_regrown != _hash_at(replica, "L_REL_0064", page)
        tracks_h = _hash_at(replica, "P_ABS_16480", page) != _hash_at(replica, "H_REL_0064", page)
        if d_grown is not None and d_grown == d_regrown and d_grown != d_drop and tracks_l and tracks_h:
            result.add(page)
    return result


def _record_interval(
    replica: ReplicaIndexes, page: int, store: PageStore, work: WorkCounter
) -> tuple[int, int] | None:
    changed: set[int] = set()
    excluded = {("E0", "E0R"), ("L_REINSERT_SAME", "L_IDLE_REOPEN"), ("H_REL_1280", "H_IDLE_REOPEN")}
    for left, right in zip(CHECKPOINT_IDS, CHECKPOINT_IDS[1:]):
        if (left, right) in excluded:
            continue
        left_hash = _hash_at(replica, left, page)
        right_hash = _hash_at(replica, right, page)
        if left_hash is None or right_hash is None or left_hash == right_hash:
            continue
        left_page = store.get(left_hash)
        right_page = store.get(right_hash)
        work.charge(PAGE_SIZE)
        changed.update(index for index, pair in enumerate(zip(left_page, right_page, strict=True)) if pair[0] != pair[1])
    if not changed:
        return None
    return min(changed), max(changed) + 1


def _decode_pointer(raw: bytes, layout: str) -> tuple[int, int]:
    if layout == "u24le_page_then_u8_slot":
        return int.from_bytes(raw[:3], "little"), raw[3]
    return int.from_bytes(raw[1:], "little"), raw[0]


def _pointer_candidates(
    replica: ReplicaIndexes,
    interval: tuple[int, int],
    store: PageStore,
    work: WorkCounter,
) -> tuple[set[tuple[int, str]], set[tuple[int, str]]]:
    start, end = interval
    anchors = {
        checkpoint: store.get(_hash_at(replica, checkpoint, 1) or "")
        for checkpoint in ("D_REGROW_0128", "L_REL_0064", "L_REL_1280", "L_DELETE_ALTERNATING", "L_REINSERT_SAME")
    }
    used: set[tuple[int, str]] = set()
    free: set[tuple[int, str]] = set()
    for offset in range(start, max(start, end - 3)):
        for layout in POINTER_LAYOUTS:
            work.charge(5)
            values = {name: page[offset : offset + 4] for name, page in anchors.items()}
            decoded = [_decode_pointer(value, layout) for value in values.values()]
            if all(page < FINAL_PAGE_LIMIT and slot <= 255 for page, slot in decoded):
                if values["D_REGROW_0128"] != values["L_REL_1280"] and values["L_REL_0064"] != values["L_REL_1280"]:
                    used.add((offset, layout))
                if values["L_REL_1280"] != values["L_DELETE_ALTERNATING"] and values["L_REL_1280"] == values["L_REINSERT_SAME"]:
                    free.add((offset, layout))
    return used, free


FINAL_PAGE_LIMIT = 20480


def build_analysis(
    plan: CheckedPlan,
    replicas: list[ReplicaIndexes],
    page_store_root: Path,
) -> dict[str, Any]:
    if [item.observation["replica"] for item in replicas] != [1, 2, 3]:
        raise ValidationError("A1 analysis requires replicas 1, 2, and 3 in order")
    shared = ("plan_sha256", "producer_commit", "repository_url", "run_id", "environment_sha256", "provider_sha256")
    for key in shared:
        require_equal(len({item.observation[key] for item in replicas}), 1, f"replica binding {key}")

    work = WorkCounter()
    reasons = _idle_reasons(replicas[:2])
    store = PageStore(page_store_root, work)
    candidate_models_examined = 0
    derivation_survivors = 0

    page_candidates = [_record_page_candidates(replica, work) for replica in replicas[:2]]
    if not reasons and (page_candidates[0] != page_candidates[1]):
        reasons.append("replica_disagreement")
    if not reasons and page_candidates[0] != {1}:
        reasons.append("ambiguous_record_boundary")

    intervals: list[tuple[int, int]] = []
    if not reasons:
        observed_intervals = [
            _record_interval(replica, 1, store, work) for replica in replicas[:2]
        ]
        if any(interval is None for interval in observed_intervals) or observed_intervals[0] != observed_intervals[1]:
            reasons.append("ambiguous_record_boundary")
        else:
            intervals = [interval for interval in observed_intervals if interval is not None]

    if not reasons:
        pointer_sets = [_pointer_candidates(replica, intervals[0], store, work) for replica in replicas[:2]]
        used = pointer_sets[0][0] & pointer_sets[1][0]
        free = pointer_sets[0][1] & pointer_sets[1][1]
        combinations = sum(
            len({offset for offset, candidate_layout in used if candidate_layout == layout})
            * len({offset for offset, candidate_layout in free if candidate_layout == layout})
            - len(
                {offset for offset, candidate_layout in used if candidate_layout == layout}
                & {offset for offset, candidate_layout in free if candidate_layout == layout}
            )
            for layout in POINTER_LAYOUTS
        )
        candidate_models_examined = combinations * len(BASE_FORMULAS)
        work.charge(min(candidate_models_examined, CANDIDATE_CEILING + 1))
        if candidate_models_examined > CANDIDATE_CEILING:
            reasons.append("resource_bound_breach")
        else:
            # Pointer deltas alone cannot prove the preregistered inline boundary,
            # conversion, active type-1 slots, and base formula jointly.
            reasons.append("missing_inline_to_indirect_conversion")

    if not reasons:
        reasons.append("no_surviving_joint_model")
    report: dict[str, Any] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a1_analysis_report",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "run_id": replicas[0].observation["run_id"],
        "producer_commit": replicas[0].observation["producer_commit"],
        "derivation_replicas": [1, 2],
        "holdout_replica": 3,
        "input_checkpoint_count": len(CHECKPOINT_IDS) * 3,
        "candidate_models_examined": candidate_models_examined,
        "derivation_survivor_count": derivation_survivors,
        "analysis_work_units": work.value,
        "holdout_evaluated": False,
        "scientific_outcome": "no_scientific_outcome",
        "no_outcome_reasons": sorted(set(reasons)),
        "surviving_model": None,
        "claims": CLAIMS,
    }
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
        if len(arguments.replica) != 3:
            raise ValidationError("exactly three --replica paths are required")
        plan = load_checked_plan()
        replicas = [load_replica_indexes(plan, path, arguments.bundle_root) for path in arguments.replica]
        report = build_analysis(plan, replicas, arguments.bundle_root / "page-store")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(canonical_json_bytes(report))
    except (OSError, ValidationError) as exc:
        print(f"A1 analysis failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(arguments.output), "scientific_outcome": report["scientific_outcome"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
