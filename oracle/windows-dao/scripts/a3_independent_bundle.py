#!/usr/bin/env python3
"""Bounded A3 bundle loading for the independent validator.

This module intentionally contains no analyzer imports or scientific model
logic.  It provides generic JSON-schema, SHA-256, path-closure, and snapshot
access primitives used by the independent recomputation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


EXP_0042_MANIFEST_SHA256 = "9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46"


class ValidationError(Exception):
    """A bounded, stable validation failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValidationError("missing_file", str(path)) from exc
    if size < 0 or size > maximum:
        raise ValidationError("file_size_bound", str(path))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError("file_read_failure", str(path)) from exc


def load_json(path: Path, maximum: int) -> tuple[Any, bytes]:
    raw = read_bytes(path, maximum)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("json_not_utf8", str(path)) from exc
    if text.startswith("\ufeff"):
        raise ValidationError("json_bom", str(path))
    try:
        return json.loads(text), raw
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("invalid_json", str(path)) from exc


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class SchemaChecker:
    """Small draft-2020-12 checker covering every keyword in the A3 schemas."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.root = schema

    def check(self, value: Any) -> None:
        self._check(value, self.root, "$")

    def _resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise ValidationError("schema_external_ref", reference)
        current: Any = self.root
        for part in reference[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or part not in current:
                raise ValidationError("schema_bad_ref", reference)
            current = current[part]
        if not isinstance(current, dict):
            raise ValidationError("schema_bad_ref", reference)
        return current

    @staticmethod
    def _same(left: Any, right: Any) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return type(left) is type(right) and left == right
        return left == right

    @staticmethod
    def _type_matches(value: Any, name: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(name, False)

    def _check(self, value: Any, schema: dict[str, Any], where: str) -> None:
        if "$ref" in schema:
            self._check(value, self._resolve(schema["$ref"]), where)
        if "anyOf" in schema:
            matches = 0
            for choice in schema["anyOf"]:
                try:
                    self._check(value, choice, where)
                except ValidationError:
                    pass
                else:
                    matches += 1
            if matches == 0:
                raise ValidationError("schema_anyof", where)
        if "const" in schema and not self._same(value, schema["const"]):
            raise ValidationError("schema_const", where)
        if "enum" in schema and not any(self._same(value, item) for item in schema["enum"]):
            raise ValidationError("schema_enum", where)
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not all(isinstance(name, str) for name in names) or not any(
                self._type_matches(value, name) for name in names
            ):
                raise ValidationError("schema_type", where)
        if isinstance(value, dict):
            required = schema.get("required", [])
            if not all(isinstance(key, str) for key in required):
                raise ValidationError("schema_definition", where)
            for key in required:
                if key not in value:
                    raise ValidationError("schema_required", f"{where}.{key}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = set(value) - set(properties)
                if extras:
                    raise ValidationError("schema_extra", f"{where}.{sorted(extras)[0]}")
            for key, child in properties.items():
                if key in value:
                    self._check(value[key], child, f"{where}.{key}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
                raise ValidationError("schema_array_length", where)
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise ValidationError("schema_array_unique", where)
            if "items" in schema:
                for index, item in enumerate(value):
                    self._check(item, schema["items"], f"{where}[{index}]")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
                raise ValidationError("schema_string_length", where)
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise ValidationError("schema_pattern", where)
            if schema.get("format") == "date-time":
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError("schema_datetime", where) from exc
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < schema.get("minimum", value) or value > schema.get("maximum", value):
                raise ValidationError("schema_numeric_bound", where)


@dataclass
class Replica:
    number: int
    observation: dict[str, Any]
    indexes: dict[str, dict[str, Any]]
    root: Path
    page_paths: dict[str, Path]
    max_page_blobs: int
    max_page_bytes: int

    def __post_init__(self) -> None:
        self._page_cache: dict[str, bytes] = {}
        self._opened_bytes = 0

    @property
    def checkpoint_ids(self) -> list[str]:
        return [checkpoint["checkpoint_id"] for checkpoint in self.observation["checkpoints"]]

    def index(self, checkpoint_id: str) -> dict[str, Any]:
        try:
            return self.indexes[checkpoint_id]
        except KeyError as exc:
            raise ValidationError("checkpoint_missing", f"r{self.number}:{checkpoint_id}") from exc

    def state(self, checkpoint_id: str, page: int) -> str | None:
        hashes = self.index(checkpoint_id)["ordered_page_sha256"]
        return hashes[page] if 0 <= page < len(hashes) else None

    def page(self, checkpoint_id: str, page: int) -> bytes | None:
        digest = self.state(checkpoint_id, page)
        if digest is None:
            return None
        if digest not in self._page_cache:
            if len(self._page_cache) >= self.max_page_blobs:
                raise ValidationError("resource_bound_breach", "page blob count")
            try:
                path = self.page_paths[digest]
            except KeyError as exc:
                raise ValidationError("page_blob_missing", digest) from exc
            raw = read_bytes(path, 2048)
            if len(raw) != 2048 or sha256_bytes(raw) != digest:
                raise ValidationError("page_blob_hash_mismatch", digest)
            self._opened_bytes += len(raw)
            if self._opened_bytes > self.max_page_bytes:
                raise ValidationError("resource_bound_breach", "page read bytes")
            self._page_cache[digest] = raw
        return self._page_cache[digest]

    def candidate_page_space(self) -> set[int]:
        largest = max(len(index["ordered_page_sha256"]) for index in self.indexes.values())
        return set(range(largest))

    def checkpoint_observation(self, checkpoint_id: str) -> dict[str, Any]:
        for checkpoint in self.observation["checkpoints"]:
            if checkpoint["checkpoint_id"] == checkpoint_id:
                return checkpoint
        raise ValidationError("checkpoint_observation_missing", checkpoint_id)


@dataclass
class LoadedBundle:
    root: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    replicas: dict[int, Replica]
    report: dict[str, Any] | None
    frozen: dict[str, Any] | None
    frozen_raw: bytes | None
    receipt: dict[str, Any] | None
    legacy_recompute: bool


class BundleLoader:
    def __init__(self, root: Path, plan_path: Path, recompute_only: bool = False) -> None:
        self.root = root.resolve()
        self.plan_path = plan_path.resolve()
        self.recompute_only = recompute_only
        self.plan, self.plan_raw = load_json(self.plan_path, 67_108_864)
        if not isinstance(self.plan, dict):
            raise ValidationError("plan_not_object")
        self.bounds = self.plan["bounds"]
        self.schemas = self.plan_path.parent

    def _safe(self, relative: str) -> Path:
        if not relative or relative.startswith("/") or "\\" in relative:
            raise ValidationError("unsafe_path", relative)
        parts = Path(relative).parts
        if any(part in ("", ".", "..") for part in parts):
            raise ValidationError("unsafe_path", relative)
        candidate = self.root.joinpath(*parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValidationError("missing_file", relative) from exc
        if self.root not in resolved.parents or candidate.is_symlink():
            raise ValidationError("unsafe_path", relative)
        return resolved

    def _schema(self, name: str, value: Any) -> None:
        schema, _ = load_json(self.schemas / name, 67_108_864)
        if not isinstance(schema, dict):
            raise ValidationError("schema_not_object", name)
        SchemaChecker(schema).check(value)

    def _load_inventory(self, manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
        entries: dict[str, dict[str, Any]] = {}
        page_paths: dict[str, Path] = {}
        total = 0
        for entry in manifest["files"]:
            relative = entry["path"]
            if relative in entries:
                raise ValidationError("manifest_duplicate_path", relative)
            path = self._safe(relative)
            raw = read_bytes(path, self.bounds["max_json_bytes"] if entry["media_type"] != "application/octet-stream" else 2048)
            if len(raw) != entry["size_bytes"] or sha256_bytes(raw) != entry["sha256"]:
                raise ValidationError("manifest_file_mismatch", relative)
            total += len(raw)
            entries[relative] = entry
            if entry["role"] == "page_blob":
                if len(raw) != 2048 or Path(relative).name != f"{entry['sha256']}.page":
                    raise ValidationError("page_blob_contract", relative)
                page_paths[entry["sha256"]] = path
        if total != manifest["bundle_size_bytes_excluding_manifest"]:
            raise ValidationError("manifest_size_total_mismatch")
        if len(page_paths) != manifest["page_blob_count"]:
            raise ValidationError("manifest_page_blob_count_mismatch")
        actual: set[str] = set()
        for directory, names, files in os.walk(self.root, followlinks=False):
            directory_path = Path(directory)
            for name in names:
                if (directory_path / name).is_symlink():
                    raise ValidationError("bundle_symlink", str(directory_path / name))
            for name in files:
                path = directory_path / name
                if path.is_symlink():
                    raise ValidationError("bundle_symlink", str(path))
                relative = path.relative_to(self.root).as_posix()
                if relative != "bundle-manifest.json":
                    actual.add(relative)
        if actual != set(entries):
            raise ValidationError("manifest_inventory_not_closed")
        return entries, page_paths

    def _load_replica(
        self,
        number: int,
        page_paths: dict[str, Path],
        validate_a3: bool,
    ) -> Replica:
        observation_path = self._safe(f"observations/replica-{number:02d}.json")
        observation, _ = load_json(observation_path, self.bounds["max_json_bytes"])
        if not isinstance(observation, dict):
            raise ValidationError("observation_not_object")
        if validate_a3:
            self._schema("replica-observation.schema.json", observation)
            expected_binding = next(item for item in self.plan["tables"]["role_bindings"] if item["replica"] == number)
            expected_binding = {role: expected_binding[role] for role in ("D", "L", "P", "H")}
            if observation["role_binding"] != expected_binding:
                raise ValidationError("role_binding_mismatch", str(number))
        indexes: dict[str, dict[str, Any]] = {}
        expected_ids = self.plan["checkpoint_design"]["checkpoint_ids"]
        checkpoints = observation.get("checkpoints")
        if not isinstance(checkpoints, list) or len(checkpoints) != len(expected_ids):
            raise ValidationError("checkpoint_count_mismatch", f"replica {number}")
        for ordinal, checkpoint in enumerate(checkpoints):
            if checkpoint.get("checkpoint_id") != expected_ids[ordinal] or checkpoint.get("ordinal") != ordinal:
                raise ValidationError("checkpoint_order_mismatch", f"replica {number}")
            reference = checkpoint.get("page_index")
            if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                raise ValidationError("page_index_reference_invalid")
            path = self._safe(reference["path"])
            index, raw = load_json(path, self.bounds["max_json_bytes"])
            if not isinstance(index, dict):
                raise ValidationError("page_index_not_object")
            if sha256_bytes(raw) != reference.get("sha256") or len(raw) != reference.get("size_bytes"):
                raise ValidationError("page_index_reference_mismatch", reference["path"])
            if validate_a3:
                self._schema("page-index.schema.json", index)
            hashes = index.get("ordered_page_sha256")
            if (
                index.get("replica") != number
                or index.get("checkpoint_id") != expected_ids[ordinal]
                or index.get("ordinal") != ordinal
                or not isinstance(hashes, list)
                or index.get("page_count") != len(hashes)
                or checkpoint.get("actual_file_pages") != len(hashes)
                or index.get("file_size_bytes") != len(hashes) * 2048
                or checkpoint.get("actual_size_bytes") != len(hashes) * 2048
            ):
                raise ValidationError("snapshot_shape_mismatch", reference["path"])
            predecessor = None if ordinal == 0 else expected_ids[ordinal - 1]
            if index.get("predecessor_checkpoint_id") != predecessor:
                raise ValidationError("snapshot_predecessor_mismatch", reference["path"])
            if any(not isinstance(digest, str) or digest not in page_paths for digest in hashes):
                raise ValidationError("snapshot_page_blob_missing", reference["path"])
            previous = [] if ordinal == 0 else indexes[expected_ids[ordinal - 1]]["ordered_page_sha256"]
            changed = [page for page in range(max(len(previous), len(hashes))) if (previous[page] if page < len(previous) else None) != (hashes[page] if page < len(hashes) else None)]
            if index.get("changed_page_indices") != changed:
                raise ValidationError("changed_page_indices_mismatch", reference["path"])
            indexes[expected_ids[ordinal]] = index
        if validate_a3:
            first = indexes["E0"]["page_count"]
            first_achieved = indexes["D_GROW_0128"]["page_count"]
            regrowth = indexes["D_RECREATE_EMPTY"]["page_count"]
            regrowth_achieved = indexes["D_REGROW_0128"]["page_count"]
            growth = observation["d_growth_observation"]
            if growth != {
                "first_baseline_pages": first,
                "first_target_pages": first + 128,
                "first_achieved_pages": first_achieved,
                "first_rows": growth["first_rows"],
                "regrowth_baseline_pages": regrowth,
                "regrowth_target_pages": regrowth + 128,
                "regrowth_achieved_pages": regrowth_achieved,
                "regrowth_rows": growth["regrowth_rows"],
            }:
                raise ValidationError("d_growth_binding_mismatch", str(number))
            if (
                first_achieved < first + 128
                or regrowth_achieved < regrowth + 128
                or regrowth_achieved <= first_achieved
                or growth["first_rows"] % 32
                or growth["regrowth_rows"] % 32
            ):
                raise ValidationError("d_growth_arithmetic_mismatch", str(number))
            logical = sum(index["file_size_bytes"] for index in indexes.values())
            changed_total = sum(len(index["changed_page_indices"]) for index in indexes.values())
            if observation["logical_checkpoint_read_bytes"] != logical or observation["changed_hash_entries"] != changed_total:
                raise ValidationError("observation_counter_mismatch", str(number))
        return Replica(
            number,
            observation,
            indexes,
            self.root,
            page_paths,
            self.bounds["max_unique_page_blobs"],
            self.bounds["max_logical_checkpoint_read_bytes_per_replica"],
        )

    def _verify_snapshot_hashes(self, replica: Replica) -> None:
        for checkpoint_id in replica.checkpoint_ids:
            index = replica.index(checkpoint_id)
            digest = hashlib.sha256()
            for page_number in range(index["page_count"]):
                page = replica.page(checkpoint_id, page_number)
                if page is None:
                    raise ValidationError("snapshot_page_absent")
                digest.update(page)
            if digest.hexdigest() != index["database_sha256"]:
                raise ValidationError("snapshot_database_hash_mismatch", f"r{replica.number}:{checkpoint_id}")

    def load(self) -> LoadedBundle:
        manifest_path = self._safe("bundle-manifest.json")
        manifest, manifest_raw = load_json(manifest_path, self.bounds["max_json_bytes"])
        if not isinstance(manifest, dict):
            raise ValidationError("manifest_not_object")
        legacy = manifest.get("experiment_id") == "DAO-A2-ALLOCATION-MAPS-001"
        if legacy and not self.recompute_only:
            raise ValidationError("wrong_experiment_id")
        if not legacy:
            self._schema("bundle-manifest.schema.json", manifest)
        plan_sha = sha256_bytes(self.plan_raw)
        if not legacy and manifest.get("plan_sha256") != plan_sha:
            raise ValidationError("plan_hash_mismatch")

        if legacy:
            if sha256_bytes(manifest_raw) != EXP_0042_MANIFEST_SHA256:
                raise ValidationError("exp_0042_manifest_hash_mismatch")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) > 65_650:
                raise ValidationError("resource_bound_breach", "legacy manifest files")
            legacy_entries = {
                entry.get("path"): entry
                for entry in files
                if isinstance(entry, dict) and isinstance(entry.get("path"), str)
            }
            for prefix in (
                "observations/replica-01.json", "observations/replica-02.json",
                "page-indexes/replica-01/", "page-indexes/replica-02/",
            ):
                selected = [item for path, item in legacy_entries.items() if path == prefix or path.startswith(prefix)]
                if not selected:
                    raise ValidationError("legacy_inventory_incomplete", prefix)
                for entry in selected:
                    path = self._safe(entry["path"])
                    raw = read_bytes(path, self.bounds["max_json_bytes"])
                    if len(raw) != entry.get("size_bytes") or sha256_bytes(raw) != entry.get("sha256"):
                        raise ValidationError("manifest_file_mismatch", entry["path"])
            page_paths = {
                entry["sha256"]: self._safe(entry["path"])
                for entry in files
                if isinstance(entry, dict) and entry.get("role") == "page_blob"
            }
            if len(page_paths) > self.bounds["max_unique_page_blobs"]:
                raise ValidationError("resource_bound_breach", "legacy page blobs")
            replica_numbers = (1, 2)
        else:
            entries, page_paths = self._load_inventory(manifest)
            replica_numbers = (1, 2, 3)
            self._validate_documents(manifest, entries)

        replicas = {number: self._load_replica(number, page_paths, not legacy) for number in replica_numbers}
        if not legacy:
            for replica in replicas.values():
                self._verify_snapshot_hashes(replica)
            report, _ = load_json(self._safe("analysis/analysis-report.json"), self.bounds["max_json_bytes"])
            frozen, frozen_raw = load_json(self._safe("analysis/derivation-candidates.json"), self.bounds["max_json_bytes"])
            receipt, _ = load_json(self._safe("analysis/holdout-structure-receipt.json"), self.bounds["max_json_bytes"])
        else:
            report = frozen = receipt = frozen_raw = None
        return LoadedBundle(
            self.root,
            manifest,
            sha256_bytes(manifest_raw),
            self.plan,
            plan_sha,
            replicas,
            report,
            frozen,
            frozen_raw,
            receipt,
            legacy,
        )

    def _validate_documents(self, manifest: dict[str, Any], entries: dict[str, dict[str, Any]]) -> None:
        roles = {entry["role"]: [] for entry in entries.values()}
        for relative, entry in entries.items():
            roles.setdefault(entry["role"], []).append(relative)
        required_counts = {
            "plan": 1,
            "environment": 3,
            "replica_artifact_manifest": 3,
            "replica_observation": 3,
            "page_index": 75,
            "frozen_candidate_set": 1,
            "analysis_report": 1,
            "holdout_structure_receipt": 1,
        }
        for role, count in required_counts.items():
            if len(roles.get(role, [])) != count:
                raise ValidationError("manifest_role_count", role)
        plan_relative = roles["plan"][0]
        if plan_relative != "plan/a3-allocation-maps.plan.json" or entries[plan_relative]["sha256"] != manifest["plan_sha256"]:
            raise ValidationError("bundle_plan_binding_mismatch")
        schema_by_role = {
            "environment": "environment.schema.json",
            "replica_artifact_manifest": "replica-artifact-manifest.schema.json",
            "replica_observation": "replica-observation.schema.json",
            "page_index": "page-index.schema.json",
            "frozen_candidate_set": "derivation-candidates.schema.json",
            "analysis_report": "analysis-report.schema.json",
            "holdout_structure_receipt": "holdout-structure-receipt.schema.json",
        }
        for role, schema in schema_by_role.items():
            for relative in roles[role]:
                value, _ = load_json(self._safe(relative), self.bounds["max_json_bytes"])
                self._schema(schema, value)
                if isinstance(value, dict):
                    for key in ("campaign_id", "producer_commit", "plan_sha256"):
                        if key in value and value[key] != manifest[key]:
                            raise ValidationError("document_binding_mismatch", f"{relative}:{key}")
        frozen_path = roles["frozen_candidate_set"][0]
        frozen, raw = load_json(self._safe(frozen_path), self.bounds["max_json_bytes"])
        if raw != canonical_json_bytes(frozen):
            raise ValidationError("frozen_set_not_canonical")
        receipt, _ = load_json(self._safe(roles["holdout_structure_receipt"][0]), self.bounds["max_json_bytes"])
        report, _ = load_json(self._safe(roles["analysis_report"][0]), self.bounds["max_json_bytes"])
        frozen_sha = sha256_bytes(raw)
        if receipt.get("derivation_candidate_set_sha256") != frozen_sha or report.get("derivation_candidate_set_sha256") != frozen_sha:
            raise ValidationError("frozen_set_hash_link_mismatch")
        receipt_entry = entries[roles["holdout_structure_receipt"][0]]
        if manifest["holdout_structure_receipt_sha256"] != receipt_entry["sha256"]:
            raise ValidationError("receipt_manifest_link_mismatch")
        environments = sorted(roles["environment"])
        replica_manifests = sorted(roles["replica_artifact_manifest"])
        if manifest["replica_environment_sha256"] != [entries[path]["sha256"] for path in environments]:
            raise ValidationError("environment_manifest_link_mismatch")
        if manifest["replica_artifact_manifest_sha256"] != [entries[path]["sha256"] for path in replica_manifests]:
            raise ValidationError("replica_manifest_link_mismatch")
        environment_values = [load_json(self._safe(path), self.bounds["max_json_bytes"])[0] for path in environments]
        exact = [
            (value["provider"]["prog_id"], value["provider"]["clsid"], value["provider"]["server_sha256"], value["host"]["process_architecture"], value["host"]["powershell_version"].split(".")[0])
            for value in environment_values
        ]
        if any(value != exact[0] for value in exact[1:]) or exact[0][2] != manifest["provider_sha256"]:
            raise ValidationError("cross_replica_environment_mismatch")
        for number, path in enumerate(replica_manifests, 1):
            replica_manifest, _ = load_json(self._safe(path), self.bounds["max_json_bytes"])
            expected_environment = f"environment/replica-{number:02d}.json"
            expected_observation = f"observations/replica-{number:02d}.json"
            expected_indexes = {f"page-indexes/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json" for ordinal, checkpoint in enumerate(self.plan["checkpoint_design"]["checkpoint_ids"])}
            required = {expected_environment, expected_observation} | expected_indexes
            listed: set[str] = set()
            for item in replica_manifest["files"]:
                relative = item["path"]
                if relative not in entries or item != entries[relative]:
                    raise ValidationError("replica_inventory_outer_mismatch", relative)
                listed.add(relative)
            if not required <= listed:
                raise ValidationError("replica_inventory_incomplete", str(number))
            if replica_manifest["environment_sha256"] != entries[expected_environment]["sha256"]:
                raise ValidationError("replica_environment_link_mismatch", str(number))
            if replica_manifest["provider_sha256"] != manifest["provider_sha256"]:
                raise ValidationError("replica_provider_link_mismatch", str(number))
