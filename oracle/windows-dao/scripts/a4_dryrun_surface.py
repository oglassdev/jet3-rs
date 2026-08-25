#!/usr/bin/env python3
"""Materialize generator output as the exact A4 analyzer input surface."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a4_analysis_input import ReplicaAnalysisInput
from a4_campaign import changed_page_indices, expected_snapshot_tables
from a4_dryrun_fixtures import Fixture
from a4_dryrun_io import inventory_tree, read_regular
from a4_generator import SyntheticReplica, generate_replica
from a4_spec import (
    BOUNDS,
    CHECKPOINT_IDS,
    EXPERIMENT_ID,
    PAGE_SIZE,
    PLAN,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    ROLE_BINDINGS,
)
from protocol_validation import canonical_json_bytes


CAMPAIGN_ID = "a4-dryrun-synthetic"
PRODUCER_COMMIT = "0" * 40
PROVIDER_SHA256 = "2" * 64
PROVIDER_CLSID = "{00000100-0000-0010-8000-00AA006D2EA4}"
ROOT = Path(__file__).resolve().parents[3]
MAX_JSON_BYTES = int(BOUNDS["max_json_bytes"])
MAX_TREE_ENTRIES = int(BOUNDS["max_unique_page_blobs"]) + 128
CANONICALIZATION = json.loads(
    (ROOT / "oracle/windows-dao/experiments/a4/dao-schema-snapshot.schema.json").read_text()
)["properties"]["canonicalization"]["const"]


def _reference(document: Mapping[str, Any], path: str) -> dict[str, Any]:
    payload = canonical_json_bytes(dict(document))
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _environment(replica: int) -> bytes:
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_environment",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": PRODUCER_COMMIT,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "campaign_id": CAMPAIGN_ID,
        "replica": replica,
        "matrix_job_id": f"synthetic-replica-{replica}",
        "status": "ready",
        "host": {
            "windows_version": f"10.0.20348.{replica}",
            "process_architecture": "x86",
            "powershell_version": "5.1.20348.1",
            "python_version": f"3.13.{replica}",
            "runner_image": "windows-2022",
            "windows_ansi_code_page": 1252,
            "windows_oem_code_page": 437,
            "locale_name": "en-US",
        },
        "provider": {
            "prog_id": "DAO.DBEngine.36",
            "clsid": PROVIDER_CLSID,
            "provider_version": "3.60",
            "server_path": "C:/Program Files (x86)/Common Files/System/dao/dao360.dll",
            "server_file_version": "3.60.8618.0",
            "server_sha256": PROVIDER_SHA256,
        },
    }
    return canonical_json_bytes(document)


def _common(replica: int, environment_sha256: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": PRODUCER_COMMIT,
        "campaign_id": CAMPAIGN_ID,
        "environment_sha256": environment_sha256,
        "provider_sha256": PROVIDER_SHA256,
        "replica": replica,
    }


def _manifest(
    source: SyntheticReplica,
    environment: bytes,
    observation: dict[str, Any],
) -> dict[str, Any]:
    replica = source.replica
    files: list[dict[str, Any]] = [
        {
            "path": PLAN["artifacts"]["replica_environments"][replica - 1],
            "role": "environment",
            "sha256": hashlib.sha256(environment).hexdigest(),
            "size_bytes": len(environment),
            "media_type": "application/json",
        },
        {
            **_reference(observation, f"observations/replica-{replica:02d}.json"),
            "role": "replica_observation",
            "media_type": "application/json",
        },
    ]
    for checkpoint in observation["checkpoints"]:
        files.extend(
            (
                {**checkpoint["page_index"], "role": "page_index", "media_type": "application/json"},
                {**checkpoint["dao_schema_snapshot"], "role": "dao_schema_snapshot", "media_type": "application/json"},
            )
        )
    for digest in sorted(
        {
            digest
            for checkpoint in CHECKPOINT_IDS
            for digest in source.ordered_page_sha256[checkpoint]
        }
    ):
        files.append(
            {
                "path": f"page-store/{digest}.page",
                "role": "page_blob",
                "sha256": digest,
                "size_bytes": PAGE_SIZE,
                "media_type": "application/octet-stream",
            }
        )
    return {
        **_common(replica, hashlib.sha256(environment).hexdigest()),
        "document_type": "dao_a4_replica_artifact_manifest",
        "matrix_job_id": f"synthetic-replica-{replica}",
        "checkpoint_count": len(CHECKPOINT_IDS),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "files": files,
    }


def build_surface(source: SyntheticReplica) -> ReplicaAnalysisInput:
    replica = source.replica
    environment = _environment(replica)
    common = _common(replica, hashlib.sha256(environment).hexdigest())
    indexes: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    checkpoints = []
    growth = []
    predecessor: tuple[str, ...] | None = None
    previous_rows = {role: 0 for role in PLAN["tables"]["logical_roles"]}
    inserted_total = 0
    changed_total = 0
    logical_reads = 0
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        sequence = tuple(source.ordered_page_sha256[checkpoint])
        database = hashlib.sha256()
        for digest in sequence:
            database.update(source.page_bytes(digest))
        database_sha256 = database.hexdigest()
        changed = changed_page_indices(predecessor, sequence)
        changed_total += len(changed)
        logical_reads += len(sequence) * PAGE_SIZE
        index = {
            **common,
            "document_type": "dao_a4_page_index",
            "checkpoint_id": checkpoint,
            "ordinal": ordinal,
            "predecessor_checkpoint_id": None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1],
            "page_count": len(sequence),
            "file_size_bytes": len(sequence) * PAGE_SIZE,
            "database_sha256": database_sha256,
            "ordered_page_sha256": list(sequence),
            "changed_page_indices": list(changed),
        }
        indexes[checkpoint] = index
        rows = dict(source.row_counts[checkpoint])
        inserted = sum(max(0, rows[role] - previous_rows[role]) for role in previous_rows)
        inserted_total += inserted
        tables = expected_snapshot_tables(replica, checkpoint, rows)
        snapshot = {
            **common,
            "document_type": "dao_a4_schema_snapshot",
            "checkpoint_id": checkpoint,
            "ordinal": ordinal,
            "windows_ansi_code_page": 1252,
            "database_sha256_before_read": database_sha256,
            "database_sha256_after_read": database_sha256,
            "database_unchanged_by_read": True,
            "dao_identifier_observable": False,
            "identity_oracle": "listed_operation_instance_equality_only",
            "canonicalization": CANONICALIZATION,
            "tables": tables,
        }
        snapshots[checkpoint] = snapshot
        row = {
            "checkpoint_id": checkpoint,
            "ordinal": ordinal,
            "actual_file_pages": len(sequence),
            "actual_size_bytes": len(sequence) * PAGE_SIZE,
            "target_baseline_pages": None,
            "target_threshold_pages": None,
            "target_overshoot_pages": None,
            "inserted_rows_total": inserted_total,
            "table_row_counts": rows,
            "dao_reread": [
                {
                    "role": table["logical_role"],
                    "row_count": table["row_count"],
                    "rolling_sha256": table["rolling_row_sha256"],
                }
                for table in tables
            ],
            "quiescent": True,
            "post_close_companion": {
                "present_after_close": False,
                "observed_size_bytes": 0,
                "retained_for_physical_analysis": False,
            },
            "page_index": _reference(index, f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"),
            "dao_schema_snapshot": _reference(snapshot, f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"),
        }
        if "_REL_" in checkpoint or "_ABS_" in checkpoint:
            suffix = int(checkpoint.rsplit("_", 1)[1])
            baseline = (
                len(source.ordered_page_sha256["T4_CREATE"])
                if checkpoint.startswith("T1_")
                else len(source.ordered_page_sha256["T3_ABS_16480"])
                if checkpoint.startswith("T4_")
                else None
            )
            target = suffix if baseline is None else baseline + suffix
            overshoot = len(sequence) - target
            row.update(
                target_baseline_pages=baseline,
                target_threshold_pages=target,
                target_overshoot_pages=overshoot,
            )
            growth.append(
                {
                    "checkpoint_id": checkpoint,
                    "baseline_pages": baseline,
                    "target_pages": target,
                    "achieved_pages": len(sequence),
                    "overshoot_pages": overshoot,
                    "rows": inserted,
                }
            )
        checkpoints.append(row)
        previous_rows = rows
        predecessor = sequence
    observation = {
        **common,
        "document_type": "dao_a4_replica_observation",
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "matrix_job": {
            "job_id": f"synthetic-replica-{replica}",
            "replica_only": True,
            "shared_mutable_state": False,
        },
        "role_binding": dict(ROLE_BINDINGS[replica]),
        "growth_observations": growth,
        "logical_checkpoint_read_bytes": logical_reads,
        "inserted_rows_total": inserted_total,
        "changed_hash_entries": changed_total,
        "checkpoints": checkpoints,
    }
    manifest = _manifest(source, environment, observation)
    return ReplicaAnalysisInput(
        source,
        source.row_counts,
        observation,
        indexes,
        snapshots,
        manifest,
        environment,
    )


def _changed_surface(surface: ReplicaAnalysisInput, **changes: Any) -> ReplicaAnalysisInput:
    return ReplicaAnalysisInput(
        changes.get("source", surface.source),
        changes.get("table_row_counts", surface.table_row_counts),
        changes.get("replica_observation", surface.replica_observation),
        changes.get("page_indexes", surface.page_indexes),
        changes.get("schema_snapshots", surface.schema_snapshots),
        changes.get("artifact_manifest", surface.artifact_manifest),
        changes.get("environment_payload", surface.environment_payload),
    )


def patch_surface(surface: ReplicaAnalysisInput, name: str) -> ReplicaAnalysisInput:
    if name == "schema_ordinal":
        snapshots = copy.deepcopy(surface.schema_snapshots)
        snapshots["T1_CREATE_ID"]["ordinal"] = 4
        return _changed_surface(surface, schema_snapshots=snapshots)
    if name == "page_index_digest":
        indexes = copy.deepcopy(surface.page_indexes)
        indexes["T1_CREATE_ID"]["ordered_page_sha256"][0] = "f" * 64
        return _changed_surface(surface, page_indexes=indexes)
    if name == "changed_entries_one_over":
        return changed_entry_adversary(surface)
    raise ValueError(f"unknown A4 dry-run surface mutation {name!r}")


def changed_entry_adversary(surface: ReplicaAnalysisInput) -> ReplicaAnalysisInput:
    observation = copy.deepcopy(surface.replica_observation)
    indexes = copy.deepcopy(surface.page_indexes)
    snapshots = copy.deepcopy(surface.schema_snapshots)
    payloads = (bytes(PAGE_SIZE), bytes([1]) * PAGE_SIZE)
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    blobs = dict(zip(digests, payloads, strict=True))
    ordered: dict[str, tuple[str, ...]] = {}
    selected = 0
    idle_right = {right for _left, right in PLAN["checkpoint_design"]["idle_pairs"]}
    predecessor = None
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        if ordinal and checkpoint not in idle_right:
            selected ^= 1
        page_count = surface.source.page_count[checkpoint]
        sequence = (digests[selected],) * page_count
        ordered[checkpoint] = sequence
        changed = changed_page_indices(predecessor, sequence)
        database_sha256 = hashlib.sha256(payloads[selected] * page_count).hexdigest()
        indexes[checkpoint].update(
            database_sha256=database_sha256,
            ordered_page_sha256=list(sequence),
            changed_page_indices=list(changed),
        )
        snapshots[checkpoint]["database_sha256_before_read"] = database_sha256
        snapshots[checkpoint]["database_sha256_after_read"] = database_sha256
        row = observation["checkpoints"][ordinal]
        row["page_index"] = _reference(indexes[checkpoint], row["page_index"]["path"])
        row["dao_schema_snapshot"] = _reference(snapshots[checkpoint], row["dao_schema_snapshot"]["path"])
        predecessor = sequence
    observation["changed_hash_entries"] = int(
        PLAN["bounds"]["max_changed_hash_entries_per_replica"]
    )

    @dataclass(frozen=True)
    class Source:
        replica: int = 1
        checkpoint_ids: tuple[str, ...] = CHECKPOINT_IDS

        @property
        def page_count(self) -> Mapping[str, int]:
            return {checkpoint: len(sequence) for checkpoint, sequence in ordered.items()}

        @property
        def ordered_page_sha256(self) -> Mapping[str, tuple[str, ...]]:
            return ordered

        @staticmethod
        def page_bytes(digest: str) -> bytes:
            return blobs[digest]

    source = Source(surface.source.replica)
    manifest = _manifest(source, surface.environment_payload, observation)  # type: ignore[arg-type]
    return _changed_surface(
        surface,
        source=source,
        replica_observation=observation,
        page_indexes=indexes,
        schema_snapshots=snapshots,
        artifact_manifest=manifest,
    )


class FixtureInputs(Mapping[int, ReplicaAnalysisInput]):
    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.derivation = {replica: self._surface(replica) for replica in (1, 2)}

    def _surface(self, replica: int) -> ReplicaAnalysisInput:
        source = fixture_source(self.fixture, replica)
        surface = build_surface(source)
        for name in self.fixture.surface_patches_by_replica.get(replica, ()):
            surface = patch_surface(surface, name)
        return surface

    def __getitem__(self, key: int) -> ReplicaAnalysisInput:
        return self.derivation[key]

    def __iter__(self) -> Iterator[int]:
        return iter(self.derivation)

    def __len__(self) -> int:
        return len(self.derivation)

    def acquire_holdout(self, frozen_payload: bytes, frozen_sha256: str) -> ReplicaAnalysisInput:
        if hashlib.sha256(frozen_payload).hexdigest() != frozen_sha256:
            raise ValueError("A4 dry-run holdout received unfrozen bytes")
        return self._surface(3)


def fixture_source(fixture: Fixture, replica: int) -> SyntheticReplica:
    generated = generate_replica(fixture.parameters, replica)
    return fixture.source(replica, generated)


@dataclass(frozen=True)
class DiskSource:
    """Bounded page source backed by one serialized worker tree."""

    root: Path
    checkpoint_ids: tuple[str, ...]
    page_count: Mapping[str, int]
    ordered_page_sha256: Mapping[str, tuple[str, ...]]

    def page_bytes(self, digest: str) -> bytes:
        return read_regular(
            self.root / f"page-store/{digest}.page",
            PAGE_SIZE,
            exact_size=PAGE_SIZE,
        )


def _tree_json(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads(read_regular(root / relative, MAX_JSON_BYTES))
    if not isinstance(value, dict):
        raise ValueError(f"A4 dry-run tree document is not an object: {relative}")
    return value


def read_replica_tree(root: Path, replica: int) -> ReplicaAnalysisInput:
    """Read analyzer input from exact serialized fixture bytes."""
    inventory_tree(
        root,
        maximum_entries=MAX_TREE_ENTRIES,
        maximum_bytes=int(BOUNDS["max_bundle_bytes"]),
        maximum_file_bytes=MAX_JSON_BYTES,
        page_size=PAGE_SIZE,
    )
    observation = _tree_json(root, f"observations/replica-{replica:02d}.json")
    manifest = _tree_json(
        root, f"replica-artifacts/replica-{replica:02d}-manifest.json"
    )
    indexes = {}
    snapshots = {}
    counts = {}
    ordered = {}
    page_counts = {}
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        index = _tree_json(
            root,
            f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
        )
        snapshot = _tree_json(
            root,
            f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
        )
        indexes[checkpoint] = index
        snapshots[checkpoint] = snapshot
        counts[checkpoint] = dict(observation["checkpoints"][ordinal]["table_row_counts"])
        ordered[checkpoint] = tuple(index["ordered_page_sha256"])
        page_counts[checkpoint] = int(index["page_count"])
    source = DiskSource(
        root,
        CHECKPOINT_IDS,
        page_counts,
        ordered,
    )
    return ReplicaAnalysisInput(
        source,
        counts,
        observation,
        indexes,
        snapshots,
        manifest,
        read_regular(
            root / f"environment/replica-{replica:02d}.json",
            MAX_JSON_BYTES,
        ),
    )


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_replica_files(
    root: Path, replica: int, surface: ReplicaAnalysisInput
) -> None:
    _write(root / f"environment/replica-{replica:02d}.json", surface.environment_payload)
    _write(
        root / f"observations/replica-{replica:02d}.json",
        canonical_json_bytes(surface.replica_observation),
    )
    _write(
        root / f"replica-artifacts/replica-{replica:02d}-manifest.json",
        canonical_json_bytes(surface.artifact_manifest),
    )
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        _write(
            root
            / f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
            canonical_json_bytes(surface.page_indexes[checkpoint]),
        )
        _write(
            root
            / f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
            canonical_json_bytes(surface.schema_snapshots[checkpoint]),
        )
        for digest in surface.source.ordered_page_sha256[checkpoint]:
            page = root / f"page-store/{digest}.page"
            if not page.exists():
                _write(page, surface.source.page_bytes(digest))


def write_replica_tree(
    root: Path, replica: int, surface: ReplicaAnalysisInput
) -> None:
    """Write one worker-shaped replica tree from the exact analyzer surface."""
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    _write_replica_files(root, replica, surface)


def write_fixture_trees(root: Path, fixture: Fixture) -> tuple[Path, Path, Path]:
    """Materialize all three replicas without opening the holdout in an evaluator."""
    root.mkdir(parents=True, exist_ok=True)
    inputs = FixtureInputs(fixture)
    holdout = inputs._surface(3)
    roots = tuple(root / f"replica-{replica:02d}" for replica in (1, 2, 3))
    for replica, replica_root in enumerate(roots, start=1):
        surface = inputs[replica] if replica < 3 else holdout
        write_replica_tree(replica_root, replica, surface)
    return roots


def write_fixture_bundle_tree(root: Path, fixture: Fixture) -> Path:
    """Write the shared bytes read by both dry-run evaluator processes."""
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    inputs = FixtureInputs(fixture)
    for replica in (1, 2, 3):
        surface = inputs[replica] if replica < 3 else inputs._surface(3)
        _write_replica_files(root, replica, surface)
    return root
