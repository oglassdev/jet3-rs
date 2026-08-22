"""Fail-closed A2 bundle closure, schema, linkage, and snapshot validation.

This code shares no implementation with the A2 producer or analyzer.  Its only
repository inputs are the preregistered plan/revision and the adjacent schemas.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from a2_independent_core import PAGE_SIZE, ReplicaView

PLAN_SHA256 = "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"
REVISION_SHA256 = "977d352b6b7c042cf4d0f0cab793086842b3ad2b7da13b9c217020f00c5193c4"
SCHEMA_FILES = {
    "plan": "plan.schema.json",
    "bundle": "bundle-manifest.schema.json",
    "environment": "environment.schema.json",
    "observation": "replica-observation.schema.json",
    "replica_manifest": "replica-artifact-manifest.schema.json",
    "page_index": "page-index.schema.json",
    "report": "analysis-report.schema.json",
    "receipt": "holdout-structure-receipt.schema.json",
}


class BundleError(Exception):
    """A deterministic validation failure."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def load_json(path: Path, maximum: int) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BundleError(f"cannot stat {path}: {exc}") from exc
    if size < 1 or size > maximum:
        raise BundleError(f"JSON size outside 1..{maximum}: {path} ({size})")
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"invalid JSON {path}: {exc}") from exc


class SchemaChecker:
    """Small Draft 2020-12 checker for the constructs used by A2 schemas."""

    def __init__(self, schema: dict[str, Any]):
        self.schema = schema

    def check(self, value: Any) -> None:
        self._check(value, self.schema, "$")

    def _resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise BundleError(f"external schema reference is forbidden: {reference}")
        value: Any = self.schema
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        if not isinstance(value, dict):
            raise BundleError(f"schema reference is not an object: {reference}")
        return value

    def _fail(self, location: str, message: str) -> None:
        raise BundleError(f"schema violation at {location}: {message}")

    def _check(self, value: Any, schema: dict[str, Any], location: str) -> None:
        if "$ref" in schema:
            self._check(value, self._resolve(schema["$ref"]), location)
            return
        if "anyOf" in schema:
            failures = []
            for option in schema["anyOf"]:
                try:
                    self._check(value, option, location)
                    break
                except BundleError as exc:
                    failures.append(str(exc))
            else:
                self._fail(location, "no anyOf branch matched: " + " | ".join(failures))
        expected = schema.get("type")
        if expected is not None and not self._type_matches(value, expected):
            self._fail(location, f"expected {expected}, got {type(value).__name__}")
        if "const" in schema and value != schema["const"]:
            self._fail(location, f"expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            self._fail(location, f"value {value!r} is not registered")
        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [key for key in required if key not in value]
            if missing:
                self._fail(location, f"missing members {missing}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = sorted(set(value) - set(properties))
                if extras:
                    self._fail(location, f"unexpected members {extras}")
            for key, child in value.items():
                if key in properties:
                    self._check(child, properties[key], f"{location}.{key}")
        elif isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                self._fail(location, "array is too short")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                self._fail(location, "array is too long")
            if schema.get("uniqueItems"):
                encoded = [
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    for item in value
                ]
                if len(encoded) != len(set(encoded)):
                    self._fail(location, "array items are not unique")
            if isinstance(schema.get("items"), dict):
                for index, child in enumerate(value):
                    self._check(child, schema["items"], f"{location}[{index}]")
        elif isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                self._fail(location, "string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                self._fail(location, "string is too long")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                self._fail(location, f"string does not match {schema['pattern']}")
            if schema.get("format") == "date-time":
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    self._fail(location, f"invalid date-time: {exc}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                self._fail(location, f"value is below {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                self._fail(location, f"value is above {schema['maximum']}")

    @staticmethod
    def _type_matches(value: Any, expected: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(expected, False)


@dataclass(frozen=True)
class Contract:
    plan: dict[str, Any]
    plan_bytes: bytes
    schemas: dict[str, SchemaChecker]
    directory: Path

    @classmethod
    def load(cls, scripts_directory: Path) -> Contract:
        directory = scripts_directory.parent / "experiments" / "a2"
        plan_path = directory / "a2-allocation-maps.plan.json"
        revision_path = directory / "a2-allocation-maps-r2.plan.json"
        plan_bytes = plan_path.read_bytes()
        revision_bytes = revision_path.read_bytes()
        if sha256_bytes(plan_bytes) != PLAN_SHA256:
            raise BundleError("repository A2 plan hash does not match EXP-0040")
        if sha256_bytes(revision_bytes) != REVISION_SHA256:
            raise BundleError("repository A2 R2 hash does not match EXP-0041")
        plan = json.loads(plan_bytes, object_pairs_hook=_unique_object)
        revision = json.loads(revision_bytes, object_pairs_hook=_unique_object)
        if revision["preregistration"]["original_plan"]["sha256"] != PLAN_SHA256:
            raise BundleError("R2 does not pin the loaded original plan")
        schemas = {
            name: SchemaChecker(load_json(directory / filename, 1_000_000))
            for name, filename in SCHEMA_FILES.items()
        }
        schemas["plan"].check(plan)
        return cls(plan, plan_bytes, schemas, directory)


@dataclass
class VerifiedBundle:
    root: Path
    contract: Contract
    manifest: dict[str, Any]
    report: dict[str, Any]
    observations: dict[int, dict[str, Any]]
    indexes: dict[int, dict[str, dict[str, Any]]]
    blobs: dict[str, bytes]

    def views(self, replicas: tuple[int, ...]) -> tuple[ReplicaView, ...]:
        checkpoint_ids = tuple(
            self.contract.plan["checkpoint_design"]["checkpoint_ids"]
        )
        return tuple(
            ReplicaView(
                replica,
                checkpoint_ids,
                {
                    checkpoint: tuple(
                        self.indexes[replica][checkpoint]["ordered_page_sha256"]
                    )
                    for checkpoint in checkpoint_ids
                },
                {
                    checkpoint: self.indexes[replica][checkpoint]["page_count"]
                    for checkpoint in checkpoint_ids
                },
                self.blobs.__getitem__,
            )
            for replica in replicas
        )


def _safe_path(text: str) -> PurePosixPath:
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or str(path) != text
    ):
        raise BundleError(f"unsafe or noncanonical manifest path: {text}")
    return path


def _walk_regular_files(root: Path) -> set[str]:
    files: set[str] = set()
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in names:
            if (base / name).is_symlink():
                raise BundleError(f"symlinked directory is forbidden: {base / name}")
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise BundleError(f"non-regular bundle entry is forbidden: {path}")
            files.add(path.relative_to(root).as_posix())
    return files


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
    except OSError as exc:
        raise BundleError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _same_identity(
    document: dict[str, Any],
    manifest: dict[str, Any],
    *,
    environment: str | None = None,
) -> None:
    for key in ("plan_sha256", "producer_commit", "campaign_id", "provider_sha256"):
        if key in document and document[key] != manifest[key]:
            raise BundleError(f"identity mismatch for {key}")
    if environment is not None and document.get("environment_sha256") != environment:
        raise BundleError("environment hash linkage mismatch")


def verify_bundle(root: Path, contract: Contract) -> VerifiedBundle:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise BundleError("bundle root must be a real directory")
    max_json = contract.plan["bounds"]["max_json_bytes"]
    manifest_path = root / "bundle-manifest.json"
    manifest = load_json(manifest_path, max_json)
    contract.schemas["bundle"].check(manifest)
    if manifest["plan_sha256"] != PLAN_SHA256:
        raise BundleError("bundle is not bound to the frozen A2 plan")

    entries: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    for entry in manifest["files"]:
        path_text = str(_safe_path(entry["path"]))
        if path_text in entries:
            raise BundleError(f"duplicate manifest path: {path_text}")
        entries[path_text] = entry
    actual = _walk_regular_files(root)
    expected = set(entries) | {"bundle-manifest.json"}
    if actual != expected:
        missing = sorted(expected - actual)[:8]
        extra = sorted(actual - expected)[:8]
        raise BundleError(f"bundle closure mismatch; missing={missing}, extra={extra}")

    total_size = 0
    for path_text, entry in sorted(entries.items()):
        path = root / path_text
        size = path.stat().st_size
        total_size += size
        if size != entry["size_bytes"]:
            raise BundleError(f"size mismatch: {path_text}")
        digest = _hash_file(path)
        if digest != entry["sha256"]:
            raise BundleError(f"SHA-256 mismatch: {path_text}")
        if entry["role"] == "page_blob":
            match = re.fullmatch(r"page-store/([0-9a-f]{64})\.page", path_text)
            if match is None or match.group(1) != digest or size != PAGE_SIZE:
                raise BundleError(f"invalid content-addressed page blob: {path_text}")
            blobs[digest] = path.read_bytes()
    if total_size != manifest["bundle_size_bytes_excluding_manifest"]:
        raise BundleError("bundle size total does not match manifest")
    if len(blobs) != manifest["page_blob_count"]:
        raise BundleError("page blob count does not match manifest")
    if len(blobs) > contract.plan["bounds"]["max_unique_page_blobs"]:
        raise BundleError("page blob count breaches the plan bound")
    if (
        sum(map(len, blobs.values()))
        > contract.plan["bounds"]["max_retained_page_store_bytes"]
    ):
        raise BundleError("page store breaches the plan byte bound")

    plan_entry = entries.get("plan/a2-allocation-maps.plan.json")
    if (
        plan_entry is None
        or (root / plan_entry["path"]).read_bytes() != contract.plan_bytes
    ):
        raise BundleError("bundled plan is not byte-identical to the frozen plan")

    observations: dict[int, dict[str, Any]] = {}
    indexes: dict[int, dict[str, dict[str, Any]]] = {}
    replica_manifests: dict[int, dict[str, Any]] = {}
    environments: dict[int, dict[str, Any]] = {}
    checkpoint_ids = tuple(contract.plan["checkpoint_design"]["checkpoint_ids"])
    referenced_blobs: set[str] = set()
    nested_paths: set[str] = set()

    for replica in (1, 2, 3):
        suffix = f"{replica:02d}"
        environment_path = f"environment/replica-{suffix}.json"
        observation_path = f"observations/replica-{suffix}.json"
        replica_manifest_path = f"replica-artifacts/replica-{suffix}-manifest.json"
        environment = load_json(root / environment_path, max_json)
        observation = load_json(root / observation_path, max_json)
        replica_manifest = load_json(root / replica_manifest_path, max_json)
        contract.schemas["environment"].check(environment)
        contract.schemas["observation"].check(observation)
        contract.schemas["replica_manifest"].check(replica_manifest)
        if environment["replica"] != replica or observation["replica"] != replica:
            raise BundleError("replica number does not match artifact path")
        if replica_manifest["replica"] != replica:
            raise BundleError("replica manifest number does not match path")
        environment_hash = entries[environment_path]["sha256"]
        _same_identity(environment, manifest)
        _same_identity(observation, manifest, environment=environment_hash)
        _same_identity(replica_manifest, manifest, environment=environment_hash)
        if replica_manifest["matrix_job_id"] != observation["matrix_job"]["job_id"]:
            raise BundleError("matrix job linkage mismatch")
        expected_binding = dict(contract.plan["tables"]["role_bindings"][replica - 1])
        expected_binding.pop("replica")
        if observation["role_binding"] != expected_binding:
            raise BundleError("role binding differs from plan")

        by_checkpoint: dict[str, dict[str, Any]] = {}
        previous: dict[str, Any] | None = None
        changed_total = 0
        logical_bytes = 0
        for ordinal, checkpoint in enumerate(checkpoint_ids):
            index_path = (
                f"page-indexes/replica-{suffix}/{ordinal:02d}-{checkpoint}.json"
            )
            index = load_json(root / index_path, max_json)
            contract.schemas["page_index"].check(index)
            _same_identity(index, manifest, environment=environment_hash)
            if (
                index["replica"] != replica
                or index["checkpoint_id"] != checkpoint
                or index["ordinal"] != ordinal
            ):
                raise BundleError(f"page-index schedule mismatch: {index_path}")
            predecessor = None if ordinal == 0 else checkpoint_ids[ordinal - 1]
            if index["predecessor_checkpoint_id"] != predecessor:
                raise BundleError(f"page-index predecessor mismatch: {index_path}")
            hashes = index["ordered_page_sha256"]
            if (
                len(hashes) != index["page_count"]
                or index["file_size_bytes"] != len(hashes) * PAGE_SIZE
            ):
                raise BundleError(f"page-index extent mismatch: {index_path}")
            if any(digest not in blobs for digest in hashes):
                raise BundleError(f"page-index references a missing blob: {index_path}")
            expected_changed = []
            if previous is None:
                expected_changed = list(range(len(hashes)))
            else:
                old = previous["ordered_page_sha256"]
                expected_changed = [
                    page
                    for page in range(max(len(old), len(hashes)))
                    if (old[page] if page < len(old) else None)
                    != (hashes[page] if page < len(hashes) else None)
                ]
            if index["changed_page_indices"] != expected_changed:
                raise BundleError(f"changed-page list is not exact: {index_path}")
            database = hashlib.sha256()
            for digest in hashes:
                database.update(blobs[digest])
            if database.hexdigest() != index["database_sha256"]:
                raise BundleError(
                    f"snapshot reconstruction hash mismatch: {index_path}"
                )
            observation_checkpoint = observation["checkpoints"][ordinal]
            reference = observation_checkpoint["page_index"]
            if reference != {
                "path": index_path,
                "sha256": entries[index_path]["sha256"],
                "size_bytes": entries[index_path]["size_bytes"],
            }:
                raise BundleError(
                    f"observation page-index reference mismatch: {index_path}"
                )
            if (
                observation_checkpoint["checkpoint_id"] != checkpoint
                or observation_checkpoint["ordinal"] != ordinal
            ):
                raise BundleError("observation checkpoint schedule mismatch")
            if (
                observation_checkpoint["actual_file_pages"] != len(hashes)
                or observation_checkpoint["actual_size_bytes"]
                != len(hashes) * PAGE_SIZE
            ):
                raise BundleError("observation extent differs from page index")
            referenced_blobs.update(hashes)
            changed_total += len(expected_changed)
            logical_bytes += len(hashes) * PAGE_SIZE
            by_checkpoint[checkpoint] = index
            previous = index
        if changed_total != observation["changed_hash_entries"]:
            raise BundleError("observation changed-hash total is not recomputable")
        if logical_bytes != observation["logical_checkpoint_read_bytes"]:
            raise BundleError("observation logical-read total is not recomputable")
        if (
            logical_bytes
            > contract.plan["bounds"]["max_logical_checkpoint_read_bytes_per_replica"]
        ):
            raise BundleError("logical checkpoint reads breach the plan bound")
        growth = observation["d_growth_observation"]
        if growth["first_target_pages"] != growth["first_baseline_pages"] + 128:
            raise BundleError("first D target is not baseline + 128")
        if growth["regrowth_target_pages"] != growth["regrowth_baseline_pages"] + 128:
            raise BundleError("D regrowth target is not baseline + 128")
        if growth["first_achieved_pages"] < growth["first_target_pages"]:
            raise BundleError("first D growth did not reach target")
        if growth["regrowth_achieved_pages"] < growth["regrowth_target_pages"]:
            raise BundleError("D regrowth did not reach target")
        if growth["regrowth_achieved_pages"] <= growth["first_achieved_pages"]:
            raise BundleError("D regrowth is not strictly larger")

        nested = {item["path"]: item for item in replica_manifest["files"]}
        for path_text, item in nested.items():
            if path_text not in entries or item != entries[path_text]:
                raise BundleError(
                    f"replica manifest entry differs from outer manifest: {path_text}"
                )
        fixed = {environment_path, observation_path} | {
            f"page-indexes/replica-{suffix}/{ordinal:02d}-{checkpoint}.json"
            for ordinal, checkpoint in enumerate(checkpoint_ids)
        }
        expected_nested = fixed | {
            f"page-store/{digest}.page"
            for index in by_checkpoint.values()
            for digest in index["ordered_page_sha256"]
        }
        expected_nested |= {
            path for path, item in nested.items() if item["role"] == "acquisition_log"
        }
        if set(nested) != expected_nested:
            raise BundleError(
                f"replica manifest closure mismatch for replica {replica}"
            )
        nested_paths.update(nested)
        observations[replica] = observation
        indexes[replica] = by_checkpoint
        replica_manifests[replica] = replica_manifest
        environments[replica] = environment

    if set(blobs) != referenced_blobs:
        raise BundleError("page store contains an unreferenced or missing blob")
    provider_fields = [
        (
            environments[replica]["provider"]["prog_id"],
            environments[replica]["provider"]["clsid"],
            environments[replica]["provider"]["server_sha256"],
            environments[replica]["host"]["process_architecture"],
            environments[replica]["host"]["powershell_version"].split(".")[0],
        )
        for replica in (1, 2, 3)
    ]
    if len(set(provider_fields)) != 1:
        raise BundleError("cross-replica provider identity differs")

    report_path = "analysis/analysis-report.json"
    candidates_path = "analysis/derivation-candidates.json"
    receipt_path = "analysis/holdout-structure-receipt.json"
    report = load_json(root / report_path, max_json)
    receipt = load_json(root / receipt_path, max_json)
    contract.schemas["report"].check(report)
    contract.schemas["receipt"].check(receipt)
    _same_identity(report, manifest)
    _same_identity(receipt, manifest)
    if report["derivation_candidate_set_sha256"] != entries[candidates_path]["sha256"]:
        raise BundleError("report candidate-set hash linkage mismatch")
    if receipt["derivation_candidate_set_sha256"] != entries[candidates_path]["sha256"]:
        raise BundleError("receipt candidate-set hash linkage mismatch")
    if (
        receipt["replica_artifact_manifest_sha256"]
        != entries["replica-artifacts/replica-03-manifest.json"]["sha256"]
    ):
        raise BundleError("receipt holdout-manifest linkage mismatch")
    if manifest["holdout_structure_receipt_sha256"] != entries[receipt_path]["sha256"]:
        raise BundleError("bundle receipt hash linkage mismatch")
    manifest_hashes = [
        entries[f"replica-artifacts/replica-{replica:02d}-manifest.json"]["sha256"]
        for replica in (1, 2, 3)
    ]
    if manifest["replica_artifact_manifest_sha256"] != manifest_hashes:
        raise BundleError("bundle replica-manifest hash linkage mismatch")
    environment_hashes = [
        entries[f"environment/replica-{replica:02d}.json"]["sha256"]
        for replica in (1, 2, 3)
    ]
    if manifest["replica_environment_sha256"] != environment_hashes:
        raise BundleError("bundle environment hash linkage mismatch")
    if report["scientific_outcome"] != manifest["analysis_scientific_outcome"]:
        raise BundleError("bundle/report scientific outcome mismatch")
    if report["input_checkpoint_count"] != len(checkpoint_ids) * 3:
        raise BundleError("report checkpoint count is not recomputable")

    outer_fixed = {
        "plan/a2-allocation-maps.plan.json",
        report_path,
        candidates_path,
        receipt_path,
    } | {
        f"replica-artifacts/replica-{replica:02d}-manifest.json"
        for replica in (1, 2, 3)
    }
    if set(entries) != nested_paths | outer_fixed:
        raise BundleError(
            "outer manifest has files outside the nested and analysis closure"
        )
    return VerifiedBundle(
        root, contract, manifest, report, observations, indexes, blobs
    )
