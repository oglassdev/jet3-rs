#!/usr/bin/env python3
"""Bounded, analyzer-independent loading of a retained DAO A4 bundle."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from a4_dryrun_io import BoundedIoError, read_regular
from a4_independent_contract import (
    CONTRACT,
    EXPERIMENT_ID,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    ContractError,
    IndependentContract,
    sha256_bytes,
    validate_canonical_snapshot,
    validate_snapshot_schedule,
)


class ValidationError(Exception):
    """A stable independent-validation failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError("canonical_json_failure") from exc


def canonical_document_bytes(value: Any) -> bytes:
    """Canonical protocol document bytes include one final line feed."""
    return canonical_json_bytes(value) + b"\n"


def read_bytes(path: Path, maximum: int) -> bytes:
    try:
        return read_regular(path, maximum)
    except BoundedIoError as exc:
        raise ValidationError("file_size_bound", str(path)) from exc


def load_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("json_not_utf8", label) from exc
    if text.startswith("\ufeff"):
        raise ValidationError("json_bom", label)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("invalid_json", label) from exc
    if not isinstance(value, dict):
        raise ValidationError("json_not_object", label)
    return value


def _utc(value: Any) -> float:
    if not isinstance(value, str):
        raise ValidationError("campaign_timing_unparseable")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("campaign_timing_unparseable") from exc
    if moment.tzinfo is None:
        raise ValidationError("campaign_timing_unparseable")
    return moment.timestamp()


@dataclass
class PageStore:
    """One physical read per content digest, with replica-qualified accounting."""

    paths: Mapping[str, Path]
    maximum_blobs: int
    maximum_bytes: int
    _cache: dict[str, bytes] = field(default_factory=dict, init=False)
    _charged: dict[int, set[str]] = field(
        default_factory=lambda: {1: set(), 2: set(), 3: set()}, init=False
    )

    def preload(self, digest: str, payload: bytes) -> None:
        """Install one already bounded and hash-checked physical page read."""
        if digest in self._cache:
            if self._cache[digest] != payload:
                raise ValidationError("page_blob_hash_mismatch", digest)
            return
        if len(self._cache) >= self.maximum_blobs:
            raise ValidationError("resource_bound_breach", "page blob count")
        if len(payload) != 2048 or sha256_bytes(payload) != digest:
            raise ValidationError("page_blob_hash_mismatch", digest)
        self._cache[digest] = payload

    def read(self, digest: str, replica: int) -> bytes:
        try:
            path = self.paths[digest]
        except KeyError as exc:
            raise ValidationError("page_blob_missing", digest) from exc
        if digest not in self._cache:
            payload = read_bytes(path, 2048)
            self.preload(digest, payload)
        charged = self._charged[replica]
        charged.add(digest)
        if len(charged) * 2048 > self.maximum_bytes:
            raise ValidationError("resource_bound_breach", f"replica {replica} page reads")
        return self._cache[digest]

    def logical_read_bytes(self, replica: int) -> int:
        return len(self._charged[replica]) * 2048

    @property
    def physical_read_count(self) -> int:
        return len(self._cache)


@dataclass(frozen=True)
class Replica:
    number: int
    environment: Mapping[str, Any]
    artifact_manifest: Mapping[str, Any]
    observation: Mapping[str, Any]
    indexes: Mapping[str, Mapping[str, Any]]
    snapshots: Mapping[str, Mapping[str, Any]]
    store: PageStore

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return tuple(row["checkpoint_id"] for row in self.observation["checkpoints"])

    @property
    def logical_read_bytes(self) -> int:
        return self.store.logical_read_bytes(self.number)

    def index(self, checkpoint_id: str) -> Mapping[str, Any]:
        try:
            return self.indexes[checkpoint_id]
        except KeyError as exc:
            raise ValidationError("checkpoint_missing", f"r{self.number}:{checkpoint_id}") from exc

    def checkpoint_observation(self, checkpoint_id: str) -> Mapping[str, Any]:
        for row in self.observation["checkpoints"]:
            if row["checkpoint_id"] == checkpoint_id:
                return row
        raise ValidationError("checkpoint_observation_missing", checkpoint_id)

    def state(self, checkpoint_id: str, page_number: int) -> str | None:
        hashes = self.index(checkpoint_id)["ordered_page_sha256"]
        if 0 <= page_number < len(hashes):
            return hashes[page_number]
        return None

    def page(self, checkpoint_id: str, page_number: int) -> bytes | None:
        digest = self.state(checkpoint_id, page_number)
        return None if digest is None else self.store.read(digest, self.number)

    def page_bytes(self, digest: str) -> bytes:
        return self.store.read(digest, self.number)

    def candidate_page_space(self) -> range:
        return range(max(index["page_count"] for index in self.indexes.values()))


@dataclass(frozen=True)
class LoadedBundle:
    root: Path
    manifest: Mapping[str, Any]
    manifest_raw: bytes
    manifest_sha256: str
    plan: Mapping[str, Any]
    plan_sha256: str
    entries: Mapping[str, Mapping[str, Any]]
    replicas: Mapping[int, Replica]
    frozen: Mapping[str, Any]
    frozen_raw: bytes
    report: Mapping[str, Any]
    receipt: Mapping[str, Any]
    occurrence_evidence: Mapping[str, Any] | None
    occurrence_evidence_raw: bytes | None
    page_store: PageStore


class BundleLoader:
    """Load and cross-bind one complete successful A4 evidence bundle."""

    def __init__(self, root: Path, contract: IndependentContract = CONTRACT) -> None:
        if root.is_symlink():
            raise ValidationError("bundle_symlink", str(root))
        try:
            self.root = root.resolve(strict=True)
        except OSError as exc:
            raise ValidationError("bundle_root_missing", str(root)) from exc
        if not self.root.is_dir():
            raise ValidationError("bundle_root_not_directory", str(root))
        self.contract = contract
        self.bounds = contract.bounds
        self._raw: dict[str, bytes] = {}
        self._documents: dict[str, dict[str, Any]] = {}

    def _safe(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValidationError("unsafe_path", str(relative))
        pure = PurePosixPath(relative)
        if pure.is_absolute() or pure.as_posix() != relative or any(
            part in ("", ".", "..") for part in pure.parts
        ):
            raise ValidationError("unsafe_path", relative)
        candidate = self.root
        for part in pure.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValidationError("bundle_symlink", relative)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValidationError("missing_file", relative) from exc
        if self.root not in resolved.parents:
            raise ValidationError("unsafe_path", relative)
        return resolved

    def _read_entry(self, relative: str, entry: Mapping[str, Any]) -> bytes:
        if relative in self._raw:
            return self._raw[relative]
        maximum = 2048 if entry["role"] == "page_blob" else int(self.bounds["max_json_bytes"])
        if entry["role"] == "acquisition_log":
            maximum = int(self.bounds["max_child_log_bytes"])
        payload = read_bytes(self._safe(relative), maximum)
        if len(payload) != entry["size_bytes"] or sha256_bytes(payload) != entry["sha256"]:
            raise ValidationError("manifest_file_mismatch", relative)
        self._raw[relative] = payload
        return payload

    def _document(self, relative: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        if relative not in self._documents:
            self._documents[relative] = load_json_bytes(
                self._read_entry(relative, entry), relative
            )
        return self._documents[relative]

    def _validate(self, value: Any, document_type: str, relative: str) -> None:
        try:
            self.contract.validate_document(value, document_type)
        except ContractError as exc:
            raise ValidationError("schema_validation_failed", f"{relative}: {exc.detail}") from exc

    def _manifest(self) -> tuple[dict[str, Any], bytes]:
        path = self._safe("bundle-manifest.json")
        raw = read_bytes(path, int(self.bounds["max_json_bytes"]))
        manifest = load_json_bytes(raw, "bundle-manifest.json")
        if manifest.get("plan_sha256") != PLAN_SHA256 or manifest.get(
            "revision_plan_sha256"
        ) != REVISION_PLAN_SHA256:
            raise ValidationError("plan_binding_mismatch")
        elapsed = manifest.get("campaign_elapsed_seconds")
        if isinstance(elapsed, int) and not isinstance(elapsed, bool):
            if elapsed > int(self.bounds["campaign_timeout_seconds"]):
                raise ValidationError("campaign_timeout_exceeded")
        started = _utc(manifest.get("campaign_started_utc"))
        created = _utc(manifest.get("created_utc"))
        if not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
            raise ValidationError("campaign_timing_mismatch")
        if created < started or math.floor(created - started) != elapsed:
            raise ValidationError("campaign_timing_mismatch")
        self._validate(manifest, "dao_a4_bundle_manifest", "bundle-manifest.json")
        return manifest, raw

    def _inventory(
        self, manifest: Mapping[str, Any]
    ) -> tuple[dict[str, Mapping[str, Any]], dict[str, Path]]:
        entries: dict[str, Mapping[str, Any]] = {}
        page_paths: dict[str, Path] = {}
        declared_total = 0
        for entry in manifest["files"]:
            relative = entry["path"]
            if relative in entries:
                raise ValidationError("manifest_duplicate_path", relative)
            path = self._safe(relative)
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise ValidationError("missing_file", relative) from exc
            if size != entry["size_bytes"]:
                raise ValidationError("manifest_file_mismatch", relative)
            declared_total += size
            entries[relative] = entry
            role = entry["role"]
            if role == "page_blob":
                digest = entry["sha256"]
                if (
                    entry["media_type"] != "application/octet-stream"
                    or entry["size_bytes"] != 2048
                    or relative != f"page-store/{digest}.page"
                    or digest in page_paths
                ):
                    raise ValidationError("page_blob_contract", relative)
                page_paths[digest] = path
            elif role == "acquisition_log":
                if entry["media_type"] != "text/plain" or size > int(
                    self.bounds["max_child_log_bytes"]
                ):
                    raise ValidationError("acquisition_log_contract", relative)
            elif entry["media_type"] != "application/json":
                raise ValidationError("manifest_media_type", relative)
        if (
            declared_total != manifest["bundle_size_bytes_excluding_manifest"]
            or declared_total > int(self.bounds["max_bundle_bytes"])
        ):
            raise ValidationError("manifest_size_total_mismatch")
        if (
            len(page_paths) != manifest["page_blob_count"]
            or len(page_paths) > int(self.bounds["max_unique_page_blobs"])
            or len(page_paths) * 2048 > int(self.bounds["max_retained_page_store_bytes"])
        ):
            raise ValidationError("resource_bound_breach", "aggregate page store")
        declared_files = set(entries) | {"bundle-manifest.json"}
        declared_directories: set[str] = set()
        for relative in declared_files:
            declared_directories.update(
                parent.as_posix() for parent in Path(relative).parents if parent != Path(".")
            )
        actual: set[str] = set()
        pending = [self.root]
        while pending:
            base = pending.pop()
            try:
                children = os.scandir(base)
            except OSError as exc:
                raise ValidationError("manifest_inventory_not_closed") from exc
            with children:
                for child in children:
                    relative = Path(child.path).relative_to(self.root).as_posix()
                    if child.is_symlink():
                        raise ValidationError("bundle_symlink", relative)
                    if child.is_dir(follow_symlinks=False):
                        if relative not in declared_directories:
                            raise ValidationError("manifest_inventory_not_closed")
                        pending.append(Path(child.path))
                    elif child.is_file(follow_symlinks=False) and relative in declared_files:
                        if relative != "bundle-manifest.json":
                            actual.add(relative)
                    else:
                        raise ValidationError("manifest_inventory_not_closed")
        if actual != set(entries):
            raise ValidationError("manifest_inventory_not_closed")
        # Page bytes remain untouched until every declared aggregate bound passes.
        for relative, entry in entries.items():
            if entry["role"] != "page_blob":
                self._read_entry(relative, entry)
        return entries, page_paths

    @staticmethod
    def _roles(entries: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
        roles: dict[str, list[str]] = {}
        for relative, entry in entries.items():
            roles.setdefault(entry["role"], []).append(relative)
        for paths in roles.values():
            paths.sort()
        required = {
            "plan": 1,
            "revision_plan": 0,
            "environment": 3,
            "replica_artifact_manifest": 3,
            "replica_observation": 3,
            "dao_schema_snapshot": 75,
            "page_index": 75,
            "frozen_candidate_set": 1,
            "analysis_report": 1,
            "holdout_structure_receipt": 1,
        }
        for role, count in required.items():
            if len(roles.get(role, [])) != count:
                raise ValidationError("manifest_role_count", role)
        if not roles.get("page_blob"):
            raise ValidationError("manifest_role_count", "page_blob")
        if len(roles.get("h4_occurrence_evidence", [])) > 1:
            raise ValidationError("manifest_role_count", "h4_occurrence_evidence")
        return roles

    def _common_binding(
        self, value: Mapping[str, Any], manifest: Mapping[str, Any], relative: str
    ) -> None:
        for key in (
            "experiment_id",
            "plan_sha256",
            "revision_plan_sha256",
            "producer_commit",
            "campaign_id",
        ):
            if key in value and value.get(key) != manifest.get(key):
                raise ValidationError("document_binding_mismatch", f"{relative}:{key}")

    def _growth(self, observation: Mapping[str, Any], checkpoints: Mapping[str, Any]) -> None:
        ids = [
            checkpoint
            for checkpoint in self.contract.checkpoint_ids
            if "_REL_" in checkpoint or "_ABS_" in checkpoint
        ]
        growth = observation["growth_observations"]
        if [row["checkpoint_id"] for row in growth] != ids or len(growth) != 11:
            raise ValidationError("growth_observation_mismatch")
        baseline_by_role = {
            "T1": checkpoints["T4_CREATE"]["actual_file_pages"],
            "T4": checkpoints["T3_ABS_16480"]["actual_file_pages"],
        }
        for row in growth:
            checkpoint = checkpoints[row["checkpoint_id"]]
            role = row["checkpoint_id"].split("_", 1)[0]
            suffix = int(row["checkpoint_id"].rsplit("_", 1)[1])
            baseline = baseline_by_role.get(role)
            target = suffix if baseline is None else baseline + suffix
            achieved = checkpoint["actual_file_pages"]
            if row != {
                "checkpoint_id": row["checkpoint_id"],
                "baseline_pages": baseline,
                "target_pages": target,
                "achieved_pages": achieved,
                "overshoot_pages": achieved - target,
                "rows": row["rows"],
            } or achieved < target or row["rows"] % 32:
                raise ValidationError("growth_observation_mismatch", row["checkpoint_id"])
            if (
                checkpoint["target_baseline_pages"] != baseline
                or checkpoint["target_threshold_pages"] != target
                or checkpoint["target_overshoot_pages"] != achieved - target
            ):
                raise ValidationError("growth_observation_mismatch", row["checkpoint_id"])

    def _replica(
        self,
        number: int,
        manifest: Mapping[str, Any],
        entries: Mapping[str, Mapping[str, Any]],
        store: PageStore,
    ) -> Replica:
        environment_path = f"environment/replica-{number:02d}.json"
        artifact_path = f"replica-artifacts/replica-{number:02d}-manifest.json"
        observation_path = f"observations/replica-{number:02d}.json"
        for path, role in (
            (environment_path, "environment"),
            (artifact_path, "replica_artifact_manifest"),
            (observation_path, "replica_observation"),
        ):
            if path not in entries or entries[path]["role"] != role:
                raise ValidationError("manifest_path_missing", path)
        environment = self._document(environment_path, entries[environment_path])
        artifact = self._document(artifact_path, entries[artifact_path])
        observation = self._document(observation_path, entries[observation_path])
        self._validate(environment, "dao_a4_environment", environment_path)
        self._validate(artifact, "dao_a4_replica_artifact_manifest", artifact_path)
        self._validate(observation, "dao_a4_replica_observation", observation_path)
        for path, value in (
            (environment_path, environment),
            (artifact_path, artifact),
            (observation_path, observation),
        ):
            self._common_binding(value, manifest, path)
            if value.get("replica") != number:
                raise ValidationError("replica_binding_mismatch", path)
        environment_sha = entries[environment_path]["sha256"]
        job = environment["matrix_job_id"]
        if (
            artifact["matrix_job_id"] != job
            or observation["matrix_job"]["job_id"] != job
            or artifact["environment_sha256"] != environment_sha
            or observation["environment_sha256"] != environment_sha
            or artifact["provider_sha256"] != manifest["provider_sha256"]
            or observation["provider_sha256"] != manifest["provider_sha256"]
        ):
            raise ValidationError("replica_cross_binding_mismatch", str(number))
        expected_binding = next(
            row for row in self.contract.plan["tables"]["role_bindings"] if row["replica"] == number
        )
        if observation["role_binding"] != {
            role: expected_binding[role] for role in self.contract.plan["tables"]["logical_roles"]
        }:
            raise ValidationError("role_binding_mismatch", str(number))
        indexes: dict[str, Mapping[str, Any]] = {}
        snapshots: dict[str, Mapping[str, Any]] = {}
        previous: list[str] = []
        checkpoint_rows = observation["checkpoints"]
        if len(checkpoint_rows) != 25:
            raise ValidationError("checkpoint_count_mismatch", str(number))
        for ordinal, checkpoint_id in enumerate(self.contract.checkpoint_ids):
            row = checkpoint_rows[ordinal]
            if row["checkpoint_id"] != checkpoint_id or row["ordinal"] != ordinal:
                raise ValidationError("checkpoint_order_mismatch", str(number))
            expected_index = f"page-indexes/replica-{number:02d}/{ordinal:02d}-{checkpoint_id}.json"
            expected_snapshot = f"schema-snapshots/replica-{number:02d}/{ordinal:02d}-{checkpoint_id}.json"
            for key, expected, role in (
                ("page_index", expected_index, "page_index"),
                ("dao_schema_snapshot", expected_snapshot, "dao_schema_snapshot"),
            ):
                reference = row[key]
                entry = entries.get(expected)
                if (
                    reference.get("path") != expected
                    or entry is None
                    or entry["role"] != role
                    or reference.get("sha256") != entry["sha256"]
                    or reference.get("size_bytes") != entry["size_bytes"]
                ):
                    raise ValidationError("checkpoint_reference_mismatch", expected)
            index = self._document(expected_index, entries[expected_index])
            snapshot = self._document(expected_snapshot, entries[expected_snapshot])
            self._validate(index, "dao_a4_page_index", expected_index)
            self._validate(snapshot, "dao_a4_schema_snapshot", expected_snapshot)
            for path, value in ((expected_index, index), (expected_snapshot, snapshot)):
                self._common_binding(value, manifest, path)
                if (
                    value["replica"] != number
                    or value["checkpoint_id"] != checkpoint_id
                    or value["ordinal"] != ordinal
                    or value["environment_sha256"] != environment_sha
                    or value["provider_sha256"] != manifest["provider_sha256"]
                ):
                    raise ValidationError("checkpoint_cross_binding_mismatch", path)
            hashes = index["ordered_page_sha256"]
            changed = [] if ordinal == 0 else [
                page
                for page in range(max(len(previous), len(hashes)))
                if (previous[page] if page < len(previous) else None)
                != (hashes[page] if page < len(hashes) else None)
            ]
            if (
                index["predecessor_checkpoint_id"]
                != (None if ordinal == 0 else self.contract.checkpoint_ids[ordinal - 1])
                or index["page_count"] != len(hashes)
                or index["file_size_bytes"] != len(hashes) * 2048
                or index["changed_page_indices"] != changed
                or row["actual_file_pages"] != len(hashes)
                or row["actual_size_bytes"] != len(hashes) * 2048
                or any(digest not in store.paths for digest in hashes)
                or snapshot["database_sha256_before_read"] != index["database_sha256"]
                or snapshot["database_sha256_after_read"] != index["database_sha256"]
            ):
                raise ValidationError("snapshot_reconstruction_mismatch", expected_index)
            try:
                validate_canonical_snapshot(snapshot, expected_snapshot)
                validate_snapshot_schedule(snapshot, self.contract.plan, number, checkpoint_id)
            except ContractError as exc:
                raise ValidationError(exc.code, exc.detail) from exc
            table_counts = row["table_row_counts"]
            if any(table_counts[table["logical_role"]] != table["row_count"] for table in snapshot["tables"]):
                raise ValidationError("schema_snapshot_mismatch", expected_snapshot)
            reread = [
                {
                    "role": table["logical_role"],
                    "row_count": table["row_count"],
                    "rolling_sha256": table["rolling_row_sha256"],
                }
                for table in snapshot["tables"]
            ]
            if row["dao_reread"] != reread:
                raise ValidationError("schema_snapshot_mismatch", expected_snapshot)
            indexes[checkpoint_id], snapshots[checkpoint_id], previous = index, snapshot, hashes
        checkpoints = {row["checkpoint_id"]: row for row in checkpoint_rows}
        self._growth(observation, checkpoints)
        logical = sum(index["file_size_bytes"] for index in indexes.values())
        changed_total = sum(len(index["changed_page_indices"]) for index in indexes.values())
        inserted_total = 0
        previous_counts = {role: 0 for role in self.contract.plan["tables"]["logical_roles"]}
        for row in checkpoint_rows:
            counts = row["table_row_counts"]
            inserted_total += sum(max(0, counts[role] - previous_counts[role]) for role in counts)
            previous_counts = counts
        for growth in observation["growth_observations"]:
            ordinal = self.contract.checkpoint_ids.index(growth["checkpoint_id"])
            role = growth["checkpoint_id"].split("_", 1)[0]
            before = checkpoint_rows[ordinal - 1]["table_row_counts"][role]
            if growth["rows"] != checkpoint_rows[ordinal]["table_row_counts"][role] - before:
                raise ValidationError("growth_observation_mismatch", growth["checkpoint_id"])
        if (
            observation["logical_checkpoint_read_bytes"] != logical
            or observation["changed_hash_entries"] != changed_total
            or observation["inserted_rows_total"] != inserted_total
            or logical > int(self.bounds["max_logical_checkpoint_read_bytes_per_replica"])
            or observation["inserted_rows_total"] > int(self.bounds["max_inserted_rows_per_replica"])
            or changed_total > int(self.bounds["max_changed_hash_entries_per_replica"])
        ):
            raise ValidationError("resource_bound_breach", f"replica {number} counters")
        required = {environment_path, observation_path}
        required |= {
            f"page-indexes/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
            for ordinal, checkpoint in enumerate(self.contract.checkpoint_ids)
        }
        required |= {
            f"schema-snapshots/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
            for ordinal, checkpoint in enumerate(self.contract.checkpoint_ids)
        }
        replica_digests = {digest for index in indexes.values() for digest in index["ordered_page_sha256"]}
        required |= {f"page-store/{digest}.page" for digest in replica_digests}
        listed = {row["path"] for row in artifact["files"]}
        allowed_logs = {path for path in listed if entries.get(path, {}).get("role") == "acquisition_log"}
        if listed != required | allowed_logs:
            raise ValidationError("replica_inventory_not_closed", str(number))
        for item in artifact["files"]:
            if item != entries.get(item["path"]):
                raise ValidationError("replica_inventory_outer_mismatch", item["path"])
        return Replica(number, environment, artifact, observation, indexes, snapshots, store)

    def _environment_closure(
        self, manifest: Mapping[str, Any], replicas: Mapping[int, Replica], entries: Mapping[str, Any]
    ) -> None:
        environments = [replicas[number].environment for number in (1, 2, 3)]
        exact = [
            (
                value["provider"]["prog_id"],
                value["provider"]["clsid"],
                value["provider"]["server_sha256"],
                value["host"]["process_architecture"],
                value["host"]["powershell_version"].split(".", 1)[0],
                value["host"]["windows_ansi_code_page"],
            )
            for value in environments
        ]
        jobs = {value["matrix_job_id"] for value in environments}
        env_paths = [f"environment/replica-{number:02d}.json" for number in (1, 2, 3)]
        artifact_paths = [f"replica-artifacts/replica-{number:02d}-manifest.json" for number in (1, 2, 3)]
        if (
            any(value != exact[0] for value in exact[1:])
            or exact[0][2] != manifest["provider_sha256"]
            or len(jobs) != 3
            or manifest["replica_environment_sha256"] != [entries[path]["sha256"] for path in env_paths]
            or manifest["replica_artifact_manifest_sha256"]
            != [entries[path]["sha256"] for path in artifact_paths]
        ):
            raise ValidationError("cross_replica_environment_mismatch")
        listed_logs = {
            item["path"]
            for replica in replicas.values()
            for item in replica.artifact_manifest["files"]
            if item["role"] == "acquisition_log"
        }
        outer_logs = {
            path for path, entry in entries.items() if entry["role"] == "acquisition_log"
        }
        if listed_logs != outer_logs:
            raise ValidationError("acquisition_log_inventory_mismatch")

    def _analysis_documents(
        self,
        manifest: Mapping[str, Any],
        entries: Mapping[str, Mapping[str, Any]],
        roles: Mapping[str, list[str]],
    ) -> tuple[
        Mapping[str, Any], bytes, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None, bytes | None
    ]:
        frozen_path = "analysis/derivation-candidates.json"
        report_path = "analysis/analysis-report.json"
        receipt_path = "analysis/holdout-structure-receipt.json"
        if (
            roles["frozen_candidate_set"] != [frozen_path]
            or roles["analysis_report"] != [report_path]
            or roles["holdout_structure_receipt"] != [receipt_path]
        ):
            raise ValidationError("analysis_artifact_path_mismatch")
        frozen = self._document(frozen_path, entries[frozen_path])
        report = self._document(report_path, entries[report_path])
        receipt = self._document(receipt_path, entries[receipt_path])
        self._validate(frozen, "dao_a4_frozen_derivation_candidates", frozen_path)
        self._validate(report, "dao_a4_analysis_report", report_path)
        self._validate(receipt, "dao_a4_holdout_structure_receipt", receipt_path)
        for path, value in ((frozen_path, frozen), (report_path, report), (receipt_path, receipt)):
            self._common_binding(value, manifest, path)
        frozen_raw = self._raw[frozen_path]
        if canonical_json_bytes(frozen) != frozen_raw:
            raise ValidationError("frozen_set_not_canonical")
        frozen_sha = sha256_bytes(frozen_raw)
        if (
            report["derivation_candidate_set_sha256"] != frozen_sha
            or receipt["derivation_candidate_set_sha256"] != frozen_sha
            or receipt["replica_artifact_manifest_sha256"]
            != entries["replica-artifacts/replica-03-manifest.json"]["sha256"]
            or manifest["holdout_structure_receipt_sha256"] != entries[receipt_path]["sha256"]
        ):
            raise ValidationError("frozen_file_hash_mismatch")
        reference = frozen["h4_occurrence_evidence"]
        occurrence: Mapping[str, Any] | None = None
        occurrence_raw: bytes | None = None
        occurrence_paths = roles.get("h4_occurrence_evidence", [])
        if reference is None:
            if occurrence_paths or report["h4_occurrence_evidence"] is not None:
                raise ValidationError("occurrence_evidence_link_mismatch")
        else:
            expected = "analysis/h4-occurrence-evidence.json"
            entry = entries.get(expected)
            if occurrence_paths != [expected] or entry is None or reference != {
                "path": expected,
                "sha256": entry["sha256"],
                "size_bytes": entry["size_bytes"],
            } or report["h4_occurrence_evidence"] != reference:
                raise ValidationError("occurrence_evidence_link_mismatch")
            occurrence = self._document(expected, entry)
            occurrence_raw = self._raw[expected]
            self._validate(occurrence, "dao_a4_h4_occurrence_evidence", expected)
            self._common_binding(occurrence, manifest, expected)
            if canonical_json_bytes(occurrence) != occurrence_raw:
                raise ValidationError("occurrence_evidence_not_canonical")
        expected_scientific = {
            "one_or_more_layers_predict_holdout": "one_or_more_submodels_predict_holdout",
            "no_layer_predicts_holdout": "no_submodel_predicts_holdout",
        }.get(report["scientific_outcome"])
        if (
            manifest["analysis_scientific_outcome"] != expected_scientific
            or manifest["bundle_status"]
            != (
                "decisive_pending_independent_validation"
                if expected_scientific == "one_or_more_submodels_predict_holdout"
                else "complete_no_scientific_outcome"
            )
        ):
            raise ValidationError("analysis_manifest_projection_mismatch")
        return frozen, frozen_raw, report, receipt, occurrence, occurrence_raw

    def load(self, *, open_holdout: bool = True) -> LoadedBundle:
        manifest, manifest_raw = self._manifest()
        entries, page_paths = self._inventory(manifest)
        roles = self._roles(entries)
        plan_path = "plan/a4-row-anchored-maps.plan.json"
        plan_entry = entries.get(plan_path)
        if (
            roles["plan"] != [plan_path]
            or plan_entry is None
            or plan_entry["sha256"] != PLAN_SHA256
            or self._raw[plan_path] != self.contract.plan_raw
        ):
            raise ValidationError("plan_binding_mismatch")
        store = PageStore(page_paths, int(self.bounds["max_unique_page_blobs"]),
                          int(self.bounds["max_retained_page_store_bytes"]))
        replicas = {number: self._replica(number, manifest, entries, store)
                    for number in (1, 2)}

        def verify_replica(replica: Replica) -> None:
            for checkpoint_id in self.contract.checkpoint_ids:
                index = replica.index(checkpoint_id)
                digest = hashlib.sha256()
                for page_number in range(index["page_count"]):
                    payload = replica.page(checkpoint_id, page_number)
                    if payload is None:
                        raise ValidationError("snapshot_page_absent")
                    digest.update(payload)
                if digest.hexdigest() != index["database_sha256"]:
                    raise ValidationError(
                        "snapshot_database_hash_mismatch",
                        f"r{replica.number}:{checkpoint_id}",
                    )

        for replica in replicas.values():
            verify_replica(replica)
        frozen, frozen_raw, report, receipt, occurrence, occurrence_raw = self._analysis_documents(
            manifest, entries, roles
        )
        replicas[3] = self._replica(3, manifest, entries, store)
        referenced = {
            digest
            for replica in replicas.values()
            for index in replica.indexes.values()
            for digest in index["ordered_page_sha256"]
        }
        if referenced != set(page_paths):
            raise ValidationError("page_store_not_closed")
        if open_holdout:
            verify_replica(replicas[3])
        self._environment_closure(manifest, replicas, entries)
        return LoadedBundle(
            self.root,
            manifest,
            manifest_raw,
            sha256_bytes(manifest_raw),
            self.contract.plan,
            PLAN_SHA256,
            entries,
            replicas,
            frozen,
            frozen_raw,
            report,
            receipt,
            occurrence,
            occurrence_raw,
            store,
        )
