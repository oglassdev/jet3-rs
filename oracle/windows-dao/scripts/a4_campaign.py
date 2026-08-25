#!/usr/bin/env python3
"""Independent campaign-predicate checks for the DAO A4 analyzer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from protocol_validation import canonical_json_bytes as artifact_json_bytes

from a4_model import A4AnalysisError, ReplicaData, View
from a4_spec import (
    BOUNDS,
    CHECKPOINT_IDS,
    PAGE_SIZE,
    PLAN,
    ROLE_BINDINGS,
    sha256_hex,
    validate_schema,
)

_ROLES = tuple(PLAN["tables"]["logical_roles"])
_EXPECTED = PLAN["tables"]["expected_schema_by_checkpoint"]
_DEFINITION = PLAN["tables"]["definition"]
_IDLE_PAIRS = tuple(tuple(pair) for pair in PLAN["checkpoint_design"]["idle_pairs"])
_ENVIRONMENT_PATHS = tuple(PLAN["artifacts"]["replica_environments"])
_OBSERVATION_PATHS = tuple(PLAN["artifacts"]["replica_observations"])
_EMPTY_SHA256 = hashlib.sha256().hexdigest()


class CampaignReplicaInput(Protocol):
    source: ReplicaData
    table_row_counts: Mapping[str, Mapping[str, int]]
    replica_observation: Mapping[str, Any] | None
    page_indexes: Mapping[str, Mapping[str, Any]] | None
    schema_snapshots: Mapping[str, Mapping[str, Any]] | None
    artifact_manifest: Mapping[str, Any] | None
    environment_payload: bytes | None


@dataclass(frozen=True)
class CampaignResourceTotals:
    inserted_rows: int
    changed_hash_entries: int
    unique_page_blobs: int
    retained_page_store_bytes: int
    logical_checkpoint_read_bytes: int
    maximum_companion_bytes: int


@dataclass(frozen=True)
class CheckedCampaignReplica:
    view: View
    table_row_counts: Mapping[str, Mapping[str, int]]
    resources: CampaignResourceTotals
    environment_exact_fields: tuple[object, ...]
    matrix_job_id: str


def _fail(predicate: str, detail: str) -> None:
    raise A4AnalysisError(predicate, 0, detail=detail)


def _mapping(
    value: Mapping[str, Mapping[str, Any]] | None,
    replica: int,
    label: str,
    predicate: str,
) -> Mapping[str, Mapping[str, Any]]:
    if value is None or tuple(value) != CHECKPOINT_IDS:
        _fail(predicate, f"replica {replica}: {label} do not cover the exact schedule")
    return value


def _common_binding(
    document: Mapping[str, Any],
    *,
    replica: int,
    checkpoint: str | None,
    campaign_id: str,
    producer_commit: str,
    environment_sha256: str,
    provider_sha256: str,
    predicate: str,
) -> None:
    expected = {
        "producer_commit": producer_commit,
        "campaign_id": campaign_id,
        "environment_sha256": environment_sha256,
        "provider_sha256": provider_sha256,
        "replica": replica,
    }
    if checkpoint is not None:
        expected["checkpoint_id"] = checkpoint
        expected["ordinal"] = CHECKPOINT_IDS.index(checkpoint)
    for key, value in expected.items():
        if document.get(key) != value:
            _fail(predicate, f"replica {replica} {checkpoint or 'observation'}: wrong {key}")


def _artifact_matches(
    reference: Mapping[str, Any],
    document: Mapping[str, Any],
    manifest_files: Mapping[str, Mapping[str, Any]],
    expected_path: str,
    role: str,
) -> bool:
    payload = artifact_json_bytes(dict(document))
    entry = manifest_files.get(expected_path, {})
    return (
        reference.get("path") == expected_path
        and reference.get("sha256") == sha256_hex(payload)
        and reference.get("size_bytes") == len(payload)
        and entry.get("path") == expected_path
        and entry.get("role") == role
        and entry.get("sha256") == reference.get("sha256")
        and entry.get("size_bytes") == reference.get("size_bytes")
        and entry.get("media_type") == "application/json"
    )


def _checked_manifest(
    replica: int,
    value: CampaignReplicaInput,
    view: View,
    observation: Mapping[str, Any],
    campaign_id: str,
    producer_commit: str,
) -> tuple[Mapping[str, Mapping[str, Any]], tuple[object, ...], str | None]:
    manifest = value.artifact_manifest
    if manifest is None:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: artifact manifest is missing")
    try:
        validate_schema(dict(manifest), "dao_a4_replica_artifact_manifest")
    except Exception as exc:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: artifact manifest invalid: {exc}")
    _common_binding(
        manifest,
        replica=replica,
        checkpoint=None,
        campaign_id=campaign_id,
        producer_commit=producer_commit,
        environment_sha256=observation["environment_sha256"],
        provider_sha256=observation["provider_sha256"],
        predicate="A4-SCHEMA-SNAPSHOT",
    )
    if (
        manifest["checkpoint_count"] != len(CHECKPOINT_IDS)
        or not manifest["inventory_closed"]
        or not manifest["hashes_verified"]
        or not manifest["paths_closed"]
    ):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: artifact inventory is not closed")
    files = manifest["files"]
    by_path = {entry["path"]: entry for entry in files}
    if len(by_path) != len(files):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: artifact paths are duplicated")
    environment_path = _ENVIRONMENT_PATHS[replica - 1]
    observation_path = _OBSERVATION_PATHS[replica - 1]
    indexed_blobs = {
        digest
        for checkpoint in CHECKPOINT_IDS
        for digest in view.hashes(checkpoint)
    }
    expected_paths = {environment_path, observation_path}
    expected_paths.update(
        f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
        for ordinal, checkpoint in enumerate(CHECKPOINT_IDS)
    )
    expected_paths.update(
        f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
        for ordinal, checkpoint in enumerate(CHECKPOINT_IDS)
    )
    expected_paths.update(f"page-store/{digest}.page" for digest in indexed_blobs)
    reconstruction_error = None
    if set(by_path) != expected_paths:
        differing = set(by_path) ^ expected_paths
        reconstruction_paths = {
            path
            for path in differing
            if path.startswith("page-indexes/") or path.startswith("page-store/")
        }
        reconstruction_paths.update(
            path
            for path in differing & set(by_path)
            if by_path[path]["role"] in ("page_index", "page_blob")
        )
        if differing - reconstruction_paths:
            _fail(
                "A4-SCHEMA-SNAPSHOT",
                f"replica {replica}: artifact inventory differs from the closed plan paths",
            )
        reconstruction_error = "artifact inventory differs from the closed plan paths"
    environment_entry = by_path[environment_path]
    payload = value.environment_payload
    if not isinstance(payload, bytes) or not payload:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: environment bytes are missing")
    if len(payload) > int(BOUNDS["max_json_bytes"]):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: environment bytes exceed the bound")
    try:
        environment = json.loads(payload.decode("utf-8"))
        if not isinstance(environment, dict):
            raise ValueError("environment is not an object")
        if artifact_json_bytes(environment) != payload:
            raise ValueError("environment JSON is not canonical")
        validate_schema(environment, "dao_a4_environment")
    except Exception as exc:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: environment invalid: {exc}")
    environment_sha256 = sha256_hex(payload)
    if (
        environment_entry["role"] != "environment"
        or environment_entry["sha256"] != environment_sha256
        or environment_entry["size_bytes"] != len(payload)
        or environment_entry["media_type"] != "application/json"
        or observation["environment_sha256"] != environment_sha256
    ):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: environment manifest binding differs")
    if (
        environment["producer_commit"] != producer_commit
        or environment["campaign_id"] != campaign_id
        or environment["replica"] != replica
        or environment["matrix_job_id"] != observation["matrix_job"]["job_id"]
        or environment["provider"]["server_sha256"]
        != observation["provider_sha256"]
    ):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: environment cross-binding differs")
    exact_values = {
        "dao_prog_id": environment["provider"]["prog_id"],
        "provider_clsid": environment["provider"]["clsid"],
        "provider_binary_sha256": environment["provider"]["server_sha256"],
        "process_architecture": environment["host"]["process_architecture"],
        "powershell_major": int(
            environment["host"]["powershell_version"].split(".", 1)[0]
        ),
        "windows_ansi_code_page": environment["host"][
            "windows_ansi_code_page"
        ],
    }
    try:
        exact_fields = tuple(
            exact_values[field]
            for field in PLAN["environment_binding"]["cross_replica_exact_fields"]
        )
    except KeyError as exc:
        _fail("A4-SCHEMA-SNAPSHOT", f"unknown exact environment field: {exc}")
    if manifest["matrix_job_id"] != observation["matrix_job"]["job_id"]:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: manifest matrix job differs")
    blob_entries = [entry for entry in files if entry["role"] == "page_blob"]
    manifested_blob_digests = [entry["sha256"] for entry in blob_entries]
    canonical_blob_entries = all(
        entry["path"] == f"page-store/{entry['sha256']}.page"
        and entry["size_bytes"] == PAGE_SIZE
        and entry["media_type"] == "application/octet-stream"
        for entry in blob_entries
    )
    if (
        not canonical_blob_entries
        or len(manifested_blob_digests) != len(set(manifested_blob_digests))
        or set(manifested_blob_digests) != indexed_blobs
    ):
        reconstruction_error = "page-blob inventory differs"
    return by_path, exact_fields, reconstruction_error


def _check_observation_artifact(
    replica: int,
    observation: Mapping[str, Any],
    manifest_files: Mapping[str, Mapping[str, Any]],
) -> None:
    """Cross-bind actual observation bytes after earlier predicates run."""
    observation_path = _OBSERVATION_PATHS[replica - 1]
    if not _artifact_matches(
        manifest_files[observation_path],
        observation,
        manifest_files,
        observation_path,
        "replica_observation",
    ):
        _fail(
            "A4-SCHEMA-SNAPSHOT",
            f"replica {replica}: observation manifest binding differs",
        )


def _idle_bytes_equal(view: View, left: str, right: str) -> bool:
    left_hashes, right_hashes = view.hashes(left), view.hashes(right)
    if left_hashes != right_hashes:
        return False
    for page_number in range(len(left_hashes)):
        left_payload = view.page(left, page_number)
        right_payload = view.page(right, page_number)
        if left_payload != right_payload:
            return False
    return True


def _check_idle(
    replica: int,
    view: View,
    indexes: Mapping[str, Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> None:
    for left, right in _IDLE_PAIRS:
        left_index, right_index = indexes.get(left, {}), indexes.get(right, {})
        left_snapshot, right_snapshot = snapshots.get(left, {}), snapshots.get(right, {})
        if (
            not _idle_bytes_equal(view, left, right)
            or left_index.get("ordered_page_sha256")
            != right_index.get("ordered_page_sha256")
            or left_snapshot.get("tables") != right_snapshot.get("tables")
        ):
            _fail("A4-IDLE-EQUALITY", f"replica {replica}: idle pair {left}/{right} differs")


def _name_fields(name: str) -> dict[str, Any]:
    try:
        cp1252 = name.encode("cp1252")
        utf8 = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"name {name!r} is outside strict Windows-1252") from exc
    return {
        "name": name,
        "name_utf16_code_units": [ord(character) for character in name],
        "name_windows_1252_hex": cp1252.hex(),
        "name_utf8_hex": utf8.hex(),
    }


@lru_cache(maxsize=1024)
def expected_row_sha256(role: str, row_count: int) -> str:
    """Recompute the plan's deterministic DAO reread stream."""
    if role not in _ROLES or isinstance(row_count, bool) or not 0 <= row_count <= 200_000:
        raise ValueError("invalid A4 row-hash input")
    digest = hashlib.sha256()
    for identifier in range(1, row_count + 1):
        seed = f"A4|{role}|{identifier:010d}|"
        payload = (seed * ((240 + len(seed) - 1) // len(seed)))[:240].encode("ascii")
        digest.update(identifier.to_bytes(4, "little", signed=True))
        digest.update(len(payload).to_bytes(2, "little"))
        digest.update(payload)
    return digest.hexdigest()


def _token(token: str) -> tuple[str, str, bool, bool]:
    parts = token.split(":")
    role = parts[0]
    version = parts[1] if len(parts) > 2 and parts[1].startswith("v") else "v1"
    shape = parts[-1]
    return role, f"{role}-{version}", "payload" in shape, "index" in shape


def _expected_field(definition: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "ordinal_source": "Fields zero-based position after Refresh and the all-fields filter",
        **_name_fields(definition["name"]),
        "type": definition["dao_type_numeric"],
        "size": definition["size"],
        "attributes": definition["attributes_numeric"],
        "required": definition["required"],
        "allow_zero_length": definition["allow_zero_length"],
    }


def _expected_index() -> dict[str, Any]:
    index = _DEFINITION["index"]
    return {
        "ordinal": 0,
        "ordinal_source": "Indexes zero-based position after Refresh and exact A4IX_ID scheduled-name filtering",
        **_name_fields(index["name"]),
        "attributes": 0,
        "primary": index["primary"],
        "unique": index["unique"],
        "required": index["required"],
        "ignore_nulls": index["ignore_nulls"],
        "fields": [
            {
                "ordinal": 0,
                "ordinal_source": "Index.Fields zero-based position after Refresh and the all-fields filter",
                **_name_fields("Id"),
                "descending": index["descending"],
            }
        ],
    }


def expected_snapshot_tables(
    replica: int, checkpoint: str, row_counts: Mapping[str, int]
) -> list[dict[str, Any]]:
    """Build the exact plan-derived canonical table values for one snapshot."""
    result = []
    binding = ROLE_BINDINGS[replica]
    for ordinal, token in enumerate(_EXPECTED[checkpoint]):
        role, instance, has_payload, has_index = _token(token)
        count = row_counts[role]
        fields = [_expected_field(_DEFINITION["fields"][0], 0)]
        if has_payload:
            fields.append(_expected_field(_DEFINITION["fields"][1], 1))
        result.append(
            {
                "ordinal": ordinal,
                "ordinal_source": "TableDefs zero-based position after Refresh and exact extant scheduled-name filtering",
                "logical_role": role,
                "lifecycle_instance": instance,
                **_name_fields(binding[role]),
                "attributes": _DEFINITION["table_attributes_numeric"],
                "row_count": count,
                "rolling_row_sha256": expected_row_sha256(role, count),
                "fields": fields,
                "indexes": [_expected_index()] if has_index else [],
            }
        )
    return result


def _check_schema_snapshots(
    replica: int,
    value: CampaignReplicaInput,
    observation: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    indexes: Mapping[str, Mapping[str, Any]],
    campaign_id: str,
    producer_commit: str,
    manifest_files: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    environment = observation["environment_sha256"]
    provider = observation["provider_sha256"]
    rows: dict[str, dict[str, int]] = {}
    checkpoints = observation["checkpoints"]
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        snapshot = snapshots[checkpoint]
        try:
            validate_schema(dict(snapshot), "dao_a4_schema_snapshot")
        except Exception as exc:
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: {exc}")
        _common_binding(
            snapshot, replica=replica, checkpoint=checkpoint,
            campaign_id=campaign_id, producer_commit=producer_commit,
            environment_sha256=environment, provider_sha256=provider,
            predicate="A4-SCHEMA-SNAPSHOT",
        )
        reference = checkpoints[ordinal]["dao_schema_snapshot"]
        expected_path = f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
        if not _artifact_matches(
            reference, snapshot, manifest_files, expected_path, "dao_schema_snapshot"
        ):
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: snapshot reference differs")
        checkpoint_rows = checkpoints[ordinal]["table_row_counts"]
        expected_roles = {_token(token)[0] for token in _EXPECTED[checkpoint]}
        if any(
            role not in expected_roles and checkpoint_rows[role] != 0
            for role in _ROLES
        ):
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: absent role has rows")
        expected_tables = expected_snapshot_tables(replica, checkpoint, checkpoint_rows)
        if snapshot["tables"] != expected_tables:
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: canonical tables differ")
        reread = [
            {
                "role": table["logical_role"],
                "row_count": table["row_count"],
                "rolling_sha256": table["rolling_row_sha256"],
            }
            for table in snapshot["tables"]
        ]
        if checkpoints[ordinal]["dao_reread"] != reread:
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: DAO reread differs")
        if dict(value.table_row_counts[checkpoint]) != dict(checkpoint_rows):
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: analyzer row counts differ")
        rows[checkpoint] = dict(checkpoint_rows)
        database_hash = indexes[checkpoint].get("database_sha256")
        if not (
            snapshot["database_sha256_before_read"]
            == snapshot["database_sha256_after_read"]
            == database_hash
        ):
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint}: read changed database")
    return rows


def changed_page_indices(
    predecessor: Sequence[str] | None, current: Sequence[str]
) -> tuple[int, ...]:
    """Return every changed, appended, or removed ordered-page position."""
    if predecessor is None:
        return ()
    maximum = max(len(predecessor), len(current))
    return tuple(
        index
        for index in range(maximum)
        if (predecessor[index] if index < len(predecessor) else None)
        != (current[index] if index < len(current) else None)
    )


def _check_reconstruction(
    replica: int,
    value: CampaignReplicaInput,
    view: View,
    observation: Mapping[str, Any],
    indexes: Mapping[str, Mapping[str, Any]],
    campaign_id: str,
    producer_commit: str,
    manifest_files: Mapping[str, Mapping[str, Any]],
) -> tuple[View, tuple[tuple[str, ...], ...], int]:
    environment = observation["environment_sha256"]
    provider = observation["provider_sha256"]
    sequences: list[tuple[str, ...]] = []
    checkpoints = observation["checkpoints"]
    predecessor: tuple[str, ...] | None = None
    changed_total = 0
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        index = indexes[checkpoint]
        try:
            validate_schema(dict(index), "dao_a4_page_index")
        except Exception as exc:
            _fail("A4-SNAPSHOT-RECONSTRUCTION", f"replica {replica} {checkpoint}: {exc}")
        _common_binding(
            index, replica=replica, checkpoint=checkpoint,
            campaign_id=campaign_id, producer_commit=producer_commit,
            environment_sha256=environment, provider_sha256=provider,
            predicate="A4-SNAPSHOT-RECONSTRUCTION",
        )
        expected_predecessor = None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1]
        sequence = tuple(index["ordered_page_sha256"])
        changed = changed_page_indices(predecessor, sequence)
        checkpoint_row = checkpoints[ordinal]
        if (
            index["predecessor_checkpoint_id"] != expected_predecessor
            or index["page_count"] != len(sequence)
            or index["file_size_bytes"] != len(sequence) * PAGE_SIZE
            or index["changed_page_indices"] != list(changed)
            or checkpoint_row["actual_file_pages"] != len(sequence)
            or checkpoint_row["actual_size_bytes"] != len(sequence) * PAGE_SIZE
            or view.hashes(checkpoint) != sequence
            or value.source.page_count.get(checkpoint) != len(sequence)
            or not _artifact_matches(
                checkpoint_row["page_index"],
                index,
                manifest_files,
                f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
                "page_index",
            )
        ):
            _fail("A4-SNAPSHOT-RECONSTRUCTION", f"replica {replica} {checkpoint}: index cross-binding differs")
        database = hashlib.sha256()
        for page_number, digest in enumerate(sequence):
            try:
                payload = view.page(checkpoint, page_number)
            except (KeyError, OSError, TypeError, ValueError) as exc:
                _fail("A4-SNAPSHOT-RECONSTRUCTION", f"replica {replica} {checkpoint}: missing page {digest}: {exc}")
            if not isinstance(payload, bytes) or len(payload) != PAGE_SIZE or sha256_hex(payload) != digest:
                _fail("A4-SNAPSHOT-RECONSTRUCTION", f"replica {replica} {checkpoint}: page blob differs")
            database.update(payload)
        if database.hexdigest() != index["database_sha256"]:
            _fail("A4-SNAPSHOT-RECONSTRUCTION", f"replica {replica} {checkpoint}: database hash differs")
        sequences.append(sequence)
        predecessor = sequence
        changed_total += len(changed)
    return view, tuple(sequences), changed_total


def _inserted_rows(rows: Mapping[str, Mapping[str, int]]) -> tuple[int, tuple[int, ...]]:
    previous = {role: 0 for role in _ROLES}
    total = 0
    running = []
    for checkpoint in CHECKPOINT_IDS:
        current = rows[checkpoint]
        total += sum(max(0, current[role] - previous[role]) for role in _ROLES)
        running.append(total)
        previous = dict(current)
    return total, tuple(running)


def _relative_baselines() -> Mapping[str, str]:
    coverage = PLAN["checkpoint_design"]["transition_coverage"]
    result: dict[str, str] = {}
    for role in _ROLES:
        relative = tuple(
            checkpoint
            for checkpoint in CHECKPOINT_IDS
            if checkpoint.startswith(f"{role}_REL_")
        )
        if not relative:
            continue
        matches = [
            tuple(sequence)
            for sequence in coverage.values()
            if isinstance(sequence, list)
            and tuple(checkpoint for checkpoint in sequence if checkpoint in relative)
            == relative
            and sequence[0] not in relative
            and all(value.startswith(f"{role}_") for value in sequence[1:])
        ]
        if len(matches) != 1:
            raise ValueError(f"A4 {role} relative baseline is not unique")
        result[role] = matches[0][0]
    return result


_RELATIVE_BASELINES = _relative_baselines()
_GROWTH_CHECKPOINTS = tuple(
    checkpoint
    for checkpoint in CHECKPOINT_IDS
    if "_REL_" in checkpoint or "_ABS_" in checkpoint
)


def _check_growth_schedule(
    replica: int,
    checkpoints: Sequence[Mapping[str, Any]],
    growth_observations: Sequence[Mapping[str, Any]],
) -> None:
    """Recompute every scheduled growth disclosure from closed checkpoints."""
    if [row.get("checkpoint_id") for row in growth_observations] != list(
        _GROWTH_CHECKPOINTS
    ):
        _fail(
            "A4-SCHEMA-SNAPSHOT",
            f"replica {replica}: growth observations differ from the schedule",
        )
    by_checkpoint = {row["checkpoint_id"]: row for row in checkpoints}
    batch_rows = int(PLAN["tables"]["row_algorithm"]["growth_batch_rows"])
    for observation in growth_observations:
        checkpoint = observation["checkpoint_id"]
        row = by_checkpoint[checkpoint]
        ordinal = CHECKPOINT_IDS.index(checkpoint)
        previous = by_checkpoint[CHECKPOINT_IDS[ordinal - 1]]
        role = checkpoint.split("_", 1)[0]
        suffix = int(checkpoint.rsplit("_", 1)[1])
        if "_REL_" in checkpoint:
            baseline = by_checkpoint[_RELATIVE_BASELINES[role]]["actual_file_pages"]
            target = baseline + suffix
        else:
            baseline = None
            target = suffix
        achieved = row["actual_file_pages"]
        overshoot = achieved - target
        inserted = row["table_row_counts"][role] - previous["table_row_counts"][role]
        if (
            previous["actual_file_pages"] >= target
            or overshoot < 0
            or inserted <= 0
            or inserted % batch_rows
            or row["target_baseline_pages"] != baseline
            or row["target_threshold_pages"] != target
            or row["target_overshoot_pages"] != overshoot
            or dict(observation)
            != {
                "checkpoint_id": checkpoint,
                "baseline_pages": baseline,
                "target_pages": target,
                "achieved_pages": achieved,
                "overshoot_pages": overshoot,
                "rows": inserted,
            }
        ):
            _fail(
                "A4-SCHEMA-SNAPSHOT",
                f"replica {replica} {checkpoint}: growth arithmetic differs",
            )
    growth = set(_GROWTH_CHECKPOINTS)
    if any(
        checkpoint["checkpoint_id"] not in growth
        and any(
            checkpoint[field] is not None
            for field in (
                "target_baseline_pages",
                "target_threshold_pages",
                "target_overshoot_pages",
            )
        )
        for checkpoint in checkpoints
    ):
        _fail(
            "A4-SCHEMA-SNAPSHOT",
            f"replica {replica}: non-growth checkpoint carries a target",
        )
    previous_counts = {role: 0 for role in _ROLES}
    reinsert_counts = by_checkpoint["T1_REL_1280"]["table_row_counts"]
    for checkpoint in checkpoints:
        checkpoint_id = checkpoint["checkpoint_id"]
        current_counts = dict(checkpoint["table_row_counts"])
        if checkpoint_id in growth:
            changed_role = checkpoint_id.split("_", 1)[0]
            differs = any(
                current_counts[role] != previous_counts[role]
                for role in _ROLES
                if role != changed_role
            )
        else:
            expected = dict(previous_counts)
            if checkpoint_id == "T1_DELETE_ALL":
                expected["T1"] = 0
            elif checkpoint_id == "T1_REINSERT_SAME":
                expected["T1"] = reinsert_counts["T1"]
            differs = current_counts != expected
        if differs:
            _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica} {checkpoint_id}: unrelated rows differ")
        previous_counts = current_counts


def require_resource_bounds(totals: CampaignResourceTotals) -> None:
    """Apply the six independently recomputed campaign resource ceilings."""
    checks = {
        "inserted rows": (totals.inserted_rows, BOUNDS["max_inserted_rows_per_replica"]),
        "changed hash entries": (totals.changed_hash_entries, BOUNDS["max_changed_hash_entries_per_replica"]),
        "unique page blobs": (totals.unique_page_blobs, BOUNDS["max_unique_page_blobs"]),
        "retained page store": (totals.retained_page_store_bytes, BOUNDS["max_retained_page_store_bytes"]),
        "checkpoint reads": (totals.logical_checkpoint_read_bytes, BOUNDS["max_logical_checkpoint_read_bytes_per_replica"]),
        "companion bytes": (totals.maximum_companion_bytes, BOUNDS["max_companion_bytes_per_checkpoint"]),
    }
    for name, (actual, maximum) in checks.items():
        if actual > int(maximum):
            _fail("A4-RESOURCE-BOUND", f"{name} {actual} exceeds {maximum}")


def check_campaign_replica(
    replica: int,
    value: CampaignReplicaInput,
    campaign_id: str,
    producer_commit: str,
) -> CheckedCampaignReplica:
    """Evaluate the four campaign predicates, in registered order."""
    observation = value.replica_observation
    indexes = {} if value.page_indexes is None else value.page_indexes
    snapshots = {} if value.schema_snapshots is None else value.schema_snapshots
    view = View(replica, value.source)
    _check_idle(replica, view, indexes, snapshots)
    indexes = _mapping(indexes, replica, "page indexes", "A4-SCHEMA-SNAPSHOT")
    snapshots = _mapping(snapshots, replica, "schema snapshots", "A4-SCHEMA-SNAPSHOT")
    if observation is None:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: observation is missing")
    try:
        validate_schema(dict(observation), "dao_a4_replica_observation")
    except Exception as exc:
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: observation invalid: {exc}")
    _common_binding(
        observation, replica=replica, checkpoint=None,
        campaign_id=campaign_id, producer_commit=producer_commit,
        environment_sha256=observation["environment_sha256"],
        provider_sha256=observation["provider_sha256"],
        predicate="A4-SCHEMA-SNAPSHOT",
    )
    if dict(observation["role_binding"]) != dict(ROLE_BINDINGS[replica]):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: role binding differs")
    checkpoints = observation["checkpoints"]
    if [row["checkpoint_id"] for row in checkpoints] != list(CHECKPOINT_IDS) or [row["ordinal"] for row in checkpoints] != list(range(len(CHECKPOINT_IDS))):
        _fail("A4-SCHEMA-SNAPSHOT", f"replica {replica}: observation schedule differs")
    _check_growth_schedule(replica, checkpoints, observation["growth_observations"])
    manifest_files, environment_exact_fields, reconstruction_error = (
        _checked_manifest(
            replica, value, view, observation, campaign_id, producer_commit
        )
    )
    _check_observation_artifact(replica, observation, manifest_files)
    rows = _check_schema_snapshots(
        replica, value, observation, snapshots, indexes, campaign_id, producer_commit,
        manifest_files,
    )
    if reconstruction_error is not None:
        _fail(
            "A4-SNAPSHOT-RECONSTRUCTION",
            f"replica {replica}: {reconstruction_error}",
        )
    view, sequences, changed_total = _check_reconstruction(
        replica, value, view, observation, indexes, campaign_id, producer_commit,
        manifest_files,
    )
    inserted, running = _inserted_rows(rows)
    if any(checkpoints[index]["inserted_rows_total"] != running[index] for index in range(len(checkpoints))):
        _fail("A4-RESOURCE-BOUND", f"replica {replica}: checkpoint inserted-row totals differ")
    unique = {digest for sequence in sequences for digest in sequence}
    companions = [row["post_close_companion"]["observed_size_bytes"] for row in checkpoints]
    totals = CampaignResourceTotals(
        inserted,
        changed_total,
        len(unique),
        len(unique) * PAGE_SIZE,
        sum(len(sequence) * PAGE_SIZE for sequence in sequences),
        max(companions, default=0),
    )
    require_resource_bounds(totals)
    if (
        observation["inserted_rows_total"] != totals.inserted_rows
        or observation["changed_hash_entries"] != totals.changed_hash_entries
        or observation["logical_checkpoint_read_bytes"] != totals.logical_checkpoint_read_bytes
    ):
        _fail("A4-RESOURCE-BOUND", f"replica {replica}: recorded resource totals differ")
    return CheckedCampaignReplica(
        view,
        rows,
        totals,
        environment_exact_fields,
        observation["matrix_job"]["job_id"],
    )
