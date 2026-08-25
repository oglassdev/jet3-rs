#!/usr/bin/env python3
"""Independent evaluator for serialized A4 dry-run fixture bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from a4_independent_bundle import (
    BundleLoader,
    LoadedBundle,
    PageStore,
    Replica,
    ValidationError,
    canonical_document_bytes,
)
from a4_independent_campaign import recompute_campaign
from a4_independent_contract import CONTRACT, ContractError
from a4_independent_validator import _validate_candidates, recompute_bundle


def _read_document(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or raw != canonical_document_bytes(value):
        raise ValidationError("fixture_not_canonical", label)
    return value, raw


def _merge_roots(roots: Sequence[Path], destination: Path) -> None:
    destination.mkdir()
    for root in roots:
        shutil.copytree(root, destination, dirs_exist_ok=True)


def _campaign_bundle(roots: Sequence[Path], workspace: Path) -> LoadedBundle:
    if len(roots) != 3:
        raise ValidationError("fixture_replica_count")
    combined = workspace / "combined"
    _merge_roots(roots, combined)
    entries: dict[str, Mapping[str, Any]] = {}
    manifests: dict[int, Mapping[str, Any]] = {}
    for replica in (1, 2, 3):
        relative = f"replica-artifacts/replica-{replica:02d}-manifest.json"
        manifest, raw = _read_document(combined / relative, relative)
        CONTRACT.validate_document(manifest, "dao_a4_replica_artifact_manifest")
        manifests[replica] = manifest
        own: Mapping[str, Any] = {
            "path": relative,
            "role": "replica_artifact_manifest",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "media_type": "application/json",
        }
        for entry in (own, *manifest["files"]):
            path = str(entry["path"])
            payload = (combined / path).read_bytes()
            actual_entry = {
                **entry,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            existing = entries.get(path)
            if existing is not None and existing != actual_entry:
                raise ValidationError("fixture_inventory_disagreement", path)
            entries[path] = actual_entry
    declared = set(entries)
    actual = {
        path.relative_to(combined).as_posix()
        for path in combined.rglob("*")
        if path.is_file()
    }
    if actual != declared:
        raise ValidationError("fixture_inventory_not_closed")
    page_paths = {
        str(entry["sha256"]): combined / path
        for path, entry in entries.items()
        if entry["role"] == "page_blob"
    }
    store = PageStore(
        page_paths,
        int(CONTRACT.bounds["max_unique_page_blobs"]),
        int(CONTRACT.bounds["max_logical_checkpoint_read_bytes_per_replica"]),
    )
    replicas: dict[int, Replica] = {}
    for replica in (1, 2, 3):
        environment_path = f"environment/replica-{replica:02d}.json"
        observation_path = f"observations/replica-{replica:02d}.json"
        environment, _ = _read_document(combined / environment_path, environment_path)
        observation, _ = _read_document(combined / observation_path, observation_path)
        CONTRACT.validate_document(environment, "dao_a4_environment")
        CONTRACT.validate_document(observation, "dao_a4_replica_observation")
        indexes: dict[str, Mapping[str, Any]] = {}
        snapshots: dict[str, Mapping[str, Any]] = {}
        for ordinal, checkpoint in enumerate(CONTRACT.checkpoint_ids):
            index_path = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
            snapshot_path = f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
            index, _ = _read_document(combined / index_path, index_path)
            snapshot, _ = _read_document(combined / snapshot_path, snapshot_path)
            CONTRACT.validate_document(index, "dao_a4_page_index")
            indexes[checkpoint] = index
            snapshots[checkpoint] = snapshot
        replicas[replica] = Replica(
            replica,
            environment,
            manifests[replica],
            observation,
            indexes,
            snapshots,
            store,
        )
    first = manifests[1]
    manifest = {
        "experiment_id": first["experiment_id"],
        "plan_sha256": first["plan_sha256"],
        "revision_plan_sha256": first["revision_plan_sha256"],
        "producer_commit": first["producer_commit"],
        "campaign_id": first["campaign_id"],
        "provider_sha256": first["provider_sha256"],
        "page_blob_count": len(page_paths),
        "bundle_size_bytes_excluding_manifest": sum(
            int(entry["size_bytes"]) for entry in entries.values()
        ),
    }
    return LoadedBundle(
        combined,
        manifest,
        b"",
        "0" * 64,
        CONTRACT.plan,
        str(first["plan_sha256"]),
        entries,
        replicas,
        {},
        b"",
        {},
        {},
        None,
        None,
        store,
    )


def _candidate_hash_for(
    predicate_id: str, layers: Mapping[str, Any], frozen_sha256: str
) -> str:
    def visit(value: Any) -> str | None:
        if isinstance(value, Mapping):
            if value.get("terminal_predicate_id") == predicate_id:
                digest = value.get("canonical_candidates_sha256")
                if isinstance(digest, str):
                    return digest
            for child in value.values():
                found = visit(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = visit(child)
                if found is not None:
                    return found
        return None

    found = visit(layers)
    return found if found is not None else frozen_sha256


def _first_failure(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    try:
        return next(row for row in rows if row["status"] == "fail")
    except StopIteration as exc:
        raise ValidationError("dryrun_fixture_did_not_fail") from exc


def _evaluated(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evaluated = []
    for row in rows:
        if row["status"] == "not_applicable":
            continue
        evaluated.append(
            {
                "predicate_id": row["predicate_id"],
                "status": row["status"],
                "actual_survivor_count": row[
                    "predicate_measured_survivor_count"
                ],
            }
        )
        if row["status"] == "fail":
            break
    return evaluated


def evaluate_campaign(roots: Sequence[Path]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="a4-dryrun-independent-") as temporary:
        bundle = _campaign_bundle(roots, Path(temporary))
        evaluation = recompute_campaign(bundle)
    if evaluation.first_failure is None:
        raise ValidationError("dryrun_campaign_fixture_did_not_fail")
    row = next(
        item
        for item in evaluation.predicate_rows
        if item["predicate_id"] == evaluation.first_failure.predicate_id
    )
    empty = hashlib.sha256(canonical_document_bytes([]).rstrip(b"\n")).hexdigest()
    return {
        "first_failure_id": evaluation.first_failure.predicate_id,
        "measured_terminal_count": row["predicate_measured_survivor_count"],
        "candidate_set_sha256": empty,
        "evaluated_predicates": [
            {
                "predicate_id": item["predicate_id"],
                "status": item["status"],
                "actual_survivor_count": item["predicate_measured_survivor_count"],
            }
            for item in evaluation.predicate_rows
            if item["status"] != "not_applicable"
        ],
    }


def evaluate_bundle(root: Path) -> dict[str, Any]:
    bundle = BundleLoader(root).load()
    recomputed = recompute_bundle(bundle)
    failure = _first_failure(recomputed["predicate_results"])
    return {
        "first_failure_id": failure["predicate_id"],
        "measured_terminal_count": failure["predicate_measured_survivor_count"],
        "candidate_set_sha256": _candidate_hash_for(
            str(failure["predicate_id"]),
            recomputed["layers"],
            hashlib.sha256(bundle.frozen_raw).hexdigest(),
        ),
        "evaluated_predicates": _evaluated(recomputed["predicate_results"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle-root", type=Path)
    group.add_argument("--campaign-roots", nargs=3, type=Path)
    group.add_argument("--frozen", type=Path)
    group.add_argument("--work-value", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.frozen is not None:
        result = {"result": "accept"}
        try:
            value, _ = _read_document(args.frozen, str(args.frozen))
            CONTRACT.validate_document(value, "dao_a4_frozen_derivation_candidates")
            _validate_candidates(value["layers"])
        except (ContractError, ValidationError, KeyError, TypeError, ValueError):
            result = {"result": "reject"}
    elif args.work_value is not None:
        result = {
            "result": (
                "accept"
                if 0 <= args.work_value <= int(CONTRACT.bounds["max_analysis_work_units"])
                else "reject"
            )
        }
    elif args.bundle_root is not None:
        result = evaluate_bundle(args.bundle_root)
    else:
        result = evaluate_campaign(args.campaign_roots)
    args.output.write_bytes(canonical_document_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
