#!/usr/bin/env python3
"""Fail-closed checked contract for DAO-A2-ALLOCATION-MAPS-001."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from protocol_validation import ProtocolSchemaSet, ValidationError, sha256
from a2_revision import EFFECTIVE_REQUIRED_CASES, REQUIRED_REACHABLE_PREDICATE_IDS

DAO_ROOT = SCRIPTS_ROOT.parent
A2_ROOT = DAO_ROOT / "experiments" / "a2"
CHECKED_PLAN = A2_ROOT / "a2-allocation-maps.plan.json"
PLAN_SHA256 = "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"
EXPERIMENT_ID = "DAO-A2-ALLOCATION-MAPS-001"

_SCHEMA_FILES = {
    "dao_a2_allocation_maps_plan": "plan.schema.json",
    "dao_a2_replica_observation": "replica-observation.schema.json",
    "dao_a2_page_index": "page-index.schema.json",
    "dao_a2_replica_artifact_manifest": "replica-artifact-manifest.schema.json",
    "dao_a2_bundle_manifest": "bundle-manifest.schema.json",
    "dao_a2_analysis_report": "analysis-report.schema.json",
    "dao_a2_analyzer_dry_run_report": "dry-run-report.schema.json",
    "dao_a2_holdout_structure_receipt": "holdout-structure-receipt.schema.json",
    "dao_a2_environment": "environment.schema.json",
}
SCHEMA_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "analysis-report.schema.json": "0f0e5d930a1da16de4a3df761f4797771d4f28a04c041a8276125e9ef7533e45",
        "bundle-manifest.schema.json": "9725fc9ab1b2fb9a7b6dd596634410511f99755de1c616dd2c2d3491705734a1",
        "dry-run-report.schema.json": "63fcdf26e5ea9a8f6a8b93ed3cd320d8c8759574221ee4e775c2ed4180468753",
        "environment.schema.json": "687e2984e3ba3e7e43b68317ad63f90cd03eb786fb570ab3c2cc1bd2b8b2451a",
        "holdout-structure-receipt.schema.json": "5d67c196edc2b5281809e1a61e693f79d4aa4b4c109a99479e1d9236a4777376",
        "page-index.schema.json": "a3cee31952d3d2adc2e6100973ef40ad0cb3366e8d7f4464b263eed2e4b99131",
        "plan.schema.json": "492c7e4f2de84fd44468499900a70dc5b8e5e32ab3d8510792b49ae8a498cd21",
        "replica-artifact-manifest.schema.json": "5f72af0c0d28cfbb7d4d5e7cf5c1479c74eaf5cf3407baf3e3113952ae2d8da7",
        "replica-observation.schema.json": "f474fba5e2c526c26d2d6b9d3d45255cc97b98190faa13a1c41e30b21b5609c7",
    }
)
SCHEMAS = ProtocolSchemaSet(A2_ROOT, _SCHEMA_FILES)


def require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked A2 plan")


def load_bounded_json(path: Path, maximum: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Load one regular, duplicate-key-free, bounded UTF-8 JSON object."""
    try:
        metadata = path.lstat()
        reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(f"{path}: JSON input must be a regular file")
        if metadata.st_size > maximum:
            raise ValidationError(f"{path}: exceeds {maximum}-byte JSON ceiling")
        payload = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect JSON: {exc}") from exc
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: JSON root must be an object")
    return value


@dataclass(frozen=True)
class CheckedPlan:
    """Validated plan plus its frequently consumed immutable projections."""

    document: dict[str, Any]
    checkpoint_ids: tuple[str, ...]
    checkpoint_ordinals: Mapping[str, int]
    predicate_ids: tuple[str, ...]
    predicate_reasons: Mapping[str, str]
    reason_predicates: Mapping[str, str]
    bounds: Mapping[str, int]


def _compile_plan(document: dict[str, Any]) -> CheckedPlan:
    SCHEMAS.validate(document)
    require_equal(document["experiment_id"], EXPERIMENT_ID, "$.experiment_id")
    checkpoint_ids = tuple(document["checkpoint_design"]["checkpoint_ids"])
    require_equal(
        len(checkpoint_ids), document["checkpoint_design"]["count"],
        "$.checkpoint_design.count",
    )
    require_equal(len(checkpoint_ids), len(set(checkpoint_ids)), "checkpoint uniqueness")
    registry = document["predicate_registry"]
    predicate_ids = tuple(registry["ids"])
    mappings = registry["mappings"]
    require_equal(
        [row["predicate_id"] for row in mappings], list(predicate_ids),
        "$.predicate_registry.mappings order",
    )
    reasons = [row["reason"] for row in mappings]
    require_equal(len(reasons), len(set(reasons)), "predicate reason uniqueness")
    require_equal(
        set(reasons), set(document["decision_rules"]["no_scientific_outcome_identifiers"]),
        "decision reason registry",
    )
    bounds = document["bounds"]
    require_equal(bounds["page_size"], document["page_capture"]["page_size"], "page size")
    require_equal(bounds["replicas"], document["replicas"]["count"], "replica bound")
    require_equal(
        bounds["planned_checkpoints_per_replica"], len(checkpoint_ids),
        "planned checkpoint bound",
    )
    require_equal(
        bounds["max_record_candidates_per_page"],
        document["record_candidate_procedure"]["per_page_candidate_bound"],
        "record candidate per-page bound",
    )
    require_equal(
        bounds["max_record_candidates"],
        document["record_candidate_procedure"]["combined_record_candidate_bound"],
        "combined record candidate bound",
    )
    return CheckedPlan(
        document=document,
        checkpoint_ids=checkpoint_ids,
        checkpoint_ordinals=MappingProxyType(
            {checkpoint: ordinal for ordinal, checkpoint in enumerate(checkpoint_ids)}
        ),
        predicate_ids=predicate_ids,
        predicate_reasons=MappingProxyType(
            {row["predicate_id"]: row["reason"] for row in mappings}
        ),
        reason_predicates=MappingProxyType(
            {row["reason"]: row["predicate_id"] for row in mappings}
        ),
        bounds=MappingProxyType(dict(bounds)),
    )


def load_checked_schemas() -> ProtocolSchemaSet:
    """Hash-check and lint the complete closed A2 schema set."""
    require_equal(set(_SCHEMA_FILES.values()), set(SCHEMA_SHA256), "schema pin set")
    for name, expected in SCHEMA_SHA256.items():
        require_equal(sha256(A2_ROOT / name), expected, name)
    SCHEMAS.lint()
    return SCHEMAS


def load_checked_plan(path: Path = CHECKED_PLAN) -> CheckedPlan:
    """Load the exact preregistered plan after schema and SHA-256 checks."""
    load_checked_schemas()
    require_equal(sha256(path), PLAN_SHA256, "preregistered plan sha256")
    return _compile_plan(load_bounded_json(path))


_PLAN = load_checked_plan()
CHECKPOINT_IDS = _PLAN.checkpoint_ids
CHECKPOINT_ORDINALS = _PLAN.checkpoint_ordinals
PREDICATE_IDS = _PLAN.predicate_ids
PREDICATE_REASONS = _PLAN.predicate_reasons
REASON_PREDICATES = _PLAN.reason_predicates
REASON_IDS = tuple(_PLAN.reason_predicates)
BOUNDS = _PLAN.bounds
PAGE_SIZE = BOUNDS["page_size"]
ROLES = tuple(_PLAN.document["tables"]["roles"])
ROLE_BINDINGS = tuple(
    {role: row[role] for role in ROLES}
    for row in _PLAN.document["tables"]["role_bindings"]
)
BIT_POLARITIES = tuple(_PLAN.document["hypotheses"]["bit_polarity_candidates"])
POINTER_LAYOUTS = tuple(_PLAN.document["hypotheses"]["tdef_pointer_layouts"])
BASE_FORMULAS = tuple(_PLAN.document["hypotheses"]["extended_base_candidates"])


def _conversion_ordinals(label: str) -> tuple[int, ...]:
    source = _PLAN.document["analyzer_dry_run_contract"]["synthetic_input"][
        "free_parameters"
    ]["conversion_ordinal"]
    match = re.search(rf"{re.escape(label)} ordinals 1\.\.([0-9]+)", source)
    if match is None:
        raise ValidationError(f"{label} conversion ordinal bound is absent from the plan")
    return tuple(range(1, int(match.group(1)) + 1))


def _run12_calibration() -> Mapping[str, Any]:
    synthetic = _PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]
    text = synthetic["run12_calibration_case"]
    checkpoint_matches = [checkpoint for checkpoint in CHECKPOINT_IDS if checkpoint in text]
    polarity_matches = [polarity for polarity in BIT_POLARITIES if polarity in text]
    source_ordinal = re.search(r"source conversion ordinal ([0-9]+)", text)
    delete_delta = re.search(r"delete page delta \+([0-9]+)", text)
    slot_names = ("zero", "one", "two")
    slot_matches = [
        count
        for count in synthetic["free_parameters"]["slot_activation_at_conversion"]
        if count < len(slot_names) and f"{slot_names[count]} active slots" in text
    ]
    if (
        len(checkpoint_matches) != 1
        or len(polarity_matches) != 1
        or len(slot_matches) != 1
        or source_ordinal is None
        or delete_delta is None
    ):
        raise ValidationError("run12_calibration_case is not mechanically parseable")
    checkpoint = checkpoint_matches[0]
    return MappingProxyType(
        {
            "source_conversion_ordinal": int(source_ordinal.group(1)),
            "conversion_checkpoint_id": checkpoint,
            "a2_conversion_ordinal": CHECKPOINT_ORDINALS[checkpoint],
            "active_slot_count": slot_matches[0],
            "bit_polarity": polarity_matches[0],
            "delete_page_delta": int(delete_delta.group(1)),
            "scientific_evidence": False,
        }
    )


A2_CONVERSION_ORDINALS = _conversion_ordinals("A2")
LEGACY_CONVERSION_ORDINALS = _conversion_ordinals("A1 legacy")
require_equal(
    A2_CONVERSION_ORDINALS,
    tuple(range(1, len(CHECKPOINT_IDS))),
    "A2 conversion ordinal schedule",
)
RUN12_CALIBRATION = _run12_calibration()


def _schema(document: dict[str, Any], expected_type: str) -> None:
    require_equal(SCHEMAS.validate(document), expected_type, "$.document_type")
    if expected_type != "dao_a2_allocation_maps_plan":
        require_equal(document["plan_sha256"], PLAN_SHA256, "$.plan_sha256")


def _file_entries(document: dict[str, Any], location: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    for index, entry in enumerate(document["files"]):
        path = entry["path"]
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts or "." in parts or "\\" in path:
            raise ValidationError(f"{location}[{index}].path: unsafe locator")
        if path in entries or path.casefold() in folded:
            raise ValidationError(f"{location}: duplicate or case-colliding path {path!r}")
        entries[path] = entry
        folded.add(path.casefold())
        media = {
            "page_blob": "application/octet-stream",
            "acquisition_log": "text/plain",
        }.get(entry["role"], "application/json")
        require_equal(entry["media_type"], media, f"{location}[{index}].media_type")
        if entry["role"] == "page_blob":
            require_equal(entry["size_bytes"], PAGE_SIZE, f"{location}[{index}].size_bytes")
            require_equal(Path(path).stem, entry["sha256"], f"{location}[{index}].path")
    return entries


def validate_plan(document: dict[str, Any]) -> CheckedPlan:
    """Validate an in-memory plan, including its pinned semantic projections."""
    require_equal(document, _PLAN.document, "checked plan document")
    return _compile_plan(document)


def validate_environment(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_environment")
    require_equal(
        document["repository_url"],
        _PLAN.document["repository_binding"]["canonical_https_url"],
        "$.repository_url",
    )
    require_equal(
        document["provider"]["prog_id"],
        _PLAN.document["environment_binding"]["dao_prog_id"],
        "$.provider.prog_id",
    )
    return document


def expected_reread_sha256(role: str, row_count: int) -> str:
    """Compute the exact plan-defined DAO reread digest."""
    return _expected_reread_sha256(role, row_count)


@functools.lru_cache(maxsize=4096)
def _expected_reread_sha256(role: str, row_count: int) -> str:
    if (
        role not in ROLES
        or isinstance(row_count, bool)
        or not 0 <= row_count <= BOUNDS["max_inserted_rows_per_replica"]
    ):
        raise ValidationError("DAO reread digest request exceeds the row contract")
    digest = hashlib.sha256()
    for row_id in range(1, row_count + 1):
        seed = f"A2|{role}|{row_id:010d}|".encode("ascii")
        payload = (seed * ((240 + len(seed) - 1) // len(seed)))[:240]
        digest.update(row_id.to_bytes(4, "little", signed=True))
        digest.update(len(payload).to_bytes(2, "little"))
        digest.update(payload)
    return digest.hexdigest()


def _is_target(checkpoint_id: str) -> bool:
    return checkpoint_id.startswith(("D_GROW_", "D_REGROW_", "L_REL_", "H_REL_", "P_ABS_"))


def _relative_baseline_sources(checkpoint_ids: tuple[str, ...]) -> dict[str, str]:
    family_sources: dict[str, str] = {}
    result: dict[str, str] = {}
    for ordinal, checkpoint_id in enumerate(checkpoint_ids):
        if not _is_target(checkpoint_id) or checkpoint_id.startswith("P_ABS_"):
            continue
        family = checkpoint_id.rsplit("_", 1)[0]
        if family not in family_sources:
            if ordinal == 0:
                raise ValidationError("relative target has no preceding baseline checkpoint")
            family_sources[family] = checkpoint_ids[ordinal - 1]
        result[checkpoint_id] = family_sources[family]
    return result


def validate_replica_observation(document: dict[str, Any], plan: CheckedPlan = _PLAN) -> dict[str, Any]:
    _schema(document, "dao_a2_replica_observation")
    replica = document["replica"]
    require_equal(document["role_binding"], ROLE_BINDINGS[replica - 1], "$.role_binding")
    checkpoints = document["checkpoints"]
    require_equal([row["checkpoint_id"] for row in checkpoints], list(plan.checkpoint_ids), "$.checkpoints order")
    logical_bytes = 0
    maximum_inserted = 0
    prior_inserted = 0
    checkpoints_by_id = {row["checkpoint_id"]: row for row in checkpoints}
    baseline_sources = _relative_baseline_sources(plan.checkpoint_ids)
    for ordinal, checkpoint in enumerate(checkpoints):
        location = f"$.checkpoints[{ordinal}]"
        checkpoint_id = checkpoint["checkpoint_id"]
        require_equal(checkpoint["ordinal"], ordinal, f"{location}.ordinal")
        require_equal(
            checkpoint["actual_size_bytes"],
            checkpoint["actual_file_pages"] * PAGE_SIZE,
            f"{location}.actual_size_bytes",
        )
        if _is_target(checkpoint_id):
            named = int(checkpoint_id.rsplit("_", 1)[1])
            if checkpoint_id.startswith("P_ABS_"):
                require_equal(checkpoint["target_baseline_pages"], None, f"{location}.target_baseline_pages")
                expected_target = named
            else:
                baseline = checkpoint["target_baseline_pages"]
                if baseline is None:
                    raise ValidationError(f"{location}.target_baseline_pages: missing")
                baseline_source = baseline_sources[checkpoint_id]
                require_equal(
                    baseline,
                    checkpoints_by_id[baseline_source]["actual_file_pages"],
                    f"{location}.target_baseline_pages",
                )
                expected_target = baseline + named
            require_equal(checkpoint["target_threshold_pages"], expected_target, f"{location}.target_threshold_pages")
            if checkpoint["actual_file_pages"] < expected_target:
                raise ValidationError(f"{location}.actual_file_pages: target not reached")
            require_equal(
                checkpoint["target_overshoot_pages"],
                checkpoint["actual_file_pages"] - expected_target,
                f"{location}.target_overshoot_pages",
            )
        else:
            for field in ("target_baseline_pages", "target_threshold_pages", "target_overshoot_pages"):
                require_equal(checkpoint[field], None, f"{location}.{field}")
        counts = checkpoint["table_row_counts"]
        reread = checkpoint["dao_reread"]
        reread_roles = [row["role"] for row in reread]
        if len(reread_roles) != len(set(reread_roles)):
            raise ValidationError(f"{location}.dao_reread: duplicate role")
        expected_roles = tuple(
            role
            for role in ROLES
            if not (checkpoint_id == "D_DROP" and role == "D")
        )
        require_equal(reread_roles, list(expected_roles), f"{location}.dao_reread roles")
        for row in reread:
            require_equal(row["row_count"], counts[row["role"]], f"{location}.dao_reread row_count")
            require_equal(
                row["rolling_sha256"],
                expected_reread_sha256(row["role"], row["row_count"]),
                f"{location}.dao_reread rolling_sha256",
            )
        inserted = checkpoint["inserted_rows_total"]
        if inserted < prior_inserted or inserted % _PLAN.document["tables"]["row_algorithm"]["growth_batch_rows"]:
            raise ValidationError(f"{location}.inserted_rows_total: invalid batch arithmetic")
        if ordinal == 0:
            require_equal(inserted, 0, f"{location}.inserted_rows_total")
            require_equal(counts, {role: 0 for role in ROLES}, f"{location}.table_row_counts")
        else:
            prior_counts = checkpoints[ordinal - 1]["table_row_counts"]
            inserted_delta = inserted - prior_inserted
            positive_row_delta = sum(
                max(0, counts[role] - prior_counts[role]) for role in ROLES
            )
            require_equal(inserted_delta, positive_row_delta, f"{location}.inserted_rows_total delta")
            allowed_role = next(
                (
                    role
                    for prefix, role in (
                        ("D_GROW_", "D"),
                        ("D_REGROW_", "D"),
                        ("L_REL_", "L"),
                        ("L_REINSERT_", "L"),
                        ("P_ABS_", "P"),
                        ("H_REL_", "H"),
                    )
                    if checkpoint_id.startswith(prefix)
                ),
                None,
            )
            for role in ROLES:
                if role == allowed_role:
                    continue
                deleted_role = {
                    "D_DROP": "D",
                    "L_DELETE_ALL": "L",
                }.get(checkpoint_id)
                expected_count = 0 if role == deleted_role else prior_counts[role]
                require_equal(counts[role], expected_count, f"{location}.table_row_counts.{role}")
        prior_inserted = inserted
        maximum_inserted = max(maximum_inserted, inserted)
        expected_path = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint_id}.json"
        require_equal(checkpoint["page_index"]["path"], expected_path, f"{location}.page_index.path")
        logical_bytes += checkpoint["actual_size_bytes"]
    require_equal(document["logical_checkpoint_read_bytes"], logical_bytes, "$.logical_checkpoint_read_bytes")
    require_equal(document["inserted_rows_total"], maximum_inserted, "$.inserted_rows_total")
    d = document["d_growth_observation"]
    first = checkpoints[CHECKPOINT_ORDINALS["D_GROW_0128"]]
    recreated = checkpoints[CHECKPOINT_ORDINALS["D_RECREATE_EMPTY"]]
    regrown = checkpoints[CHECKPOINT_ORDINALS["D_REGROW_0128"]]
    require_equal(
        d["first_baseline_pages"],
        first["target_baseline_pages"],
        "$.d_growth_observation.first_baseline_pages",
    )
    require_equal(d["first_target_pages"], first["target_threshold_pages"], "$.d_growth_observation.first_target_pages")
    require_equal(d["first_achieved_pages"], first["actual_file_pages"], "$.d_growth_observation.first_achieved_pages")
    require_equal(d["first_rows"], first["table_row_counts"]["D"], "$.d_growth_observation.first_rows")
    require_equal(
        d["regrowth_baseline_pages"],
        recreated["actual_file_pages"],
        "$.d_growth_observation.regrowth_baseline_pages",
    )
    require_equal(
        d["regrowth_target_pages"],
        regrown["target_threshold_pages"],
        "$.d_growth_observation.regrowth_target_pages",
    )
    require_equal(
        d["regrowth_achieved_pages"],
        regrown["actual_file_pages"],
        "$.d_growth_observation.regrowth_achieved_pages",
    )
    require_equal(d["regrowth_rows"], regrown["table_row_counts"]["D"], "$.d_growth_observation.regrowth_rows")
    if regrown["actual_file_pages"] <= first["actual_file_pages"]:
        raise ValidationError("D_REGROW_0128 must be strictly larger than D_GROW_0128")
    return document


def validate_page_index(
    document: dict[str, Any],
    observation: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    prior_hashes: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Validate one page index and, when supplied, its observation binding."""
    _schema(document, "dao_a2_page_index")
    ordinal = document["ordinal"]
    require_equal(document["checkpoint_id"], CHECKPOINT_IDS[ordinal], "$.checkpoint_id")
    require_equal(
        document["predecessor_checkpoint_id"],
        None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1],
        "$.predecessor_checkpoint_id",
    )
    hashes = document["ordered_page_sha256"]
    require_equal(len(hashes), document["page_count"], "$.ordered_page_sha256")
    require_equal(document["file_size_bytes"], len(hashes) * PAGE_SIZE, "$.file_size_bytes")
    changed = document["changed_page_indices"]
    require_equal(changed, sorted(changed), "$.changed_page_indices order")
    if prior_hashes is not None:
        expected_changed = [
            index
            for index in range(max(len(prior_hashes), len(hashes)))
            if index >= len(prior_hashes)
            or index >= len(hashes)
            or prior_hashes[index] != hashes[index]
        ]
        require_equal(changed, expected_changed, "$.changed_page_indices")
    if observation is not None:
        for key in ("producer_commit", "campaign_id", "environment_sha256", "provider_sha256", "replica"):
            require_equal(document[key], observation[key], f"$.{key}")
    if checkpoint is not None:
        bindings = (
            ("checkpoint_id", checkpoint["checkpoint_id"]),
            ("ordinal", checkpoint["ordinal"]),
            ("page_count", checkpoint["actual_file_pages"]),
            ("file_size_bytes", checkpoint["actual_size_bytes"]),
        )
        for key, expected in bindings:
            require_equal(document[key], expected, f"$.{key}")
    return hashes


def validate_replica_artifact_manifest(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_replica_artifact_manifest")
    entries = _file_entries(document, "$.files")
    replica = document["replica"]
    roles = [entry["role"] for entry in entries.values()]
    require_equal(roles.count("environment"), 1, "environment file count")
    require_equal(roles.count("replica_observation"), 1, "observation file count")
    require_equal(roles.count("page_index"), len(CHECKPOINT_IDS), "page-index file count")
    if roles.count("page_blob") < 1:
        raise ValidationError("$.files: replica artifact requires page blobs")
    return document


def validate_bundle_manifest(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_bundle_manifest")
    entries = _file_entries(document, "$.files")
    roles = [entry["role"] for entry in entries.values()]
    expected_counts = {
        "plan": 1,
        "environment": BOUNDS["replicas"],
        "replica_artifact_manifest": BOUNDS["replicas"],
        "replica_observation": BOUNDS["replicas"],
        "page_index": BOUNDS["replicas"] * len(CHECKPOINT_IDS),
        "frozen_candidate_set": 1,
        "analysis_report": 1,
        "holdout_structure_receipt": 1,
    }
    for role, expected in expected_counts.items():
        require_equal(roles.count(role), expected, f"{role} file count")
    require_equal(roles.count("page_blob"), document["page_blob_count"], "$.page_blob_count")
    require_equal(
        sum(entry["size_bytes"] for entry in entries.values()),
        document["bundle_size_bytes_excluding_manifest"],
        "$.bundle_size_bytes_excluding_manifest",
    )
    decisive = document["analysis_scientific_outcome"] == "one_or_more_submodels_predict_holdout"
    expected_status = (
        "decisive_pending_independent_validation"
        if decisive
        else "complete_no_scientific_outcome"
    )
    require_equal(document["bundle_status"], expected_status, "$.bundle_status")
    return document


def _layer_rows(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("global_map_record", document["submodels"]["global_map"]["record"]),
        ("global_map_conversion_inline", document["submodels"]["global_map"]["conversion_inline"]),
        ("global_map_extended_base", document["submodels"]["global_map"]["extended_base"]),
        ("tdef_pointer_pair", document["submodels"]["tdef"]["pointer_pair"]),
    ]


def validate_analysis_report(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_analysis_report")
    results = document["predicate_results"]
    result_ids = [row["predicate_id"] for row in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValidationError("$.predicate_results: predicate ids must be unique")
    mapping_rows = {row["predicate_id"]: row for row in _PLAN.document["predicate_registry"]["mappings"]}
    for index, result in enumerate(results):
        expected_layer = mapping_rows[result["predicate_id"]]["layer"]
        require_equal(result["layer"], expected_layer, f"$.predicate_results[{index}].layer")
    terminal_ids = set(document["terminal_predicate_ids"])
    failed_ids = {row["predicate_id"] for row in results if row["status"] == "fail"}
    require_equal(terminal_ids, failed_ids, "terminal predicate failures")
    expected_reasons = {PREDICATE_REASONS[predicate] for predicate in terminal_ids}
    require_equal(set(document["no_outcome_reasons"]), expected_reasons, "$.no_outcome_reasons")
    decisive_layers = 0
    layer_reasons: set[str] = set()
    for name, layer in _layer_rows(document):
        require_equal(
            layer["derivation_survivor_count"],
            document["derivation_survivor_counts"][name],
            f"{name} survivor count",
        )
        status = layer["status"]
        if status == "decisive_predicts_holdout":
            decisive_layers += 1
            if (
                layer["model"] is None
                or layer["derivation_survivor_count"] != 1
                or not layer["holdout_evaluated"]
                or layer["no_outcome_reasons"]
            ):
                raise ValidationError(f"{name}: malformed decisive layer")
        elif status == "no_outcome":
            if layer["model"] is not None or not layer["no_outcome_reasons"]:
                raise ValidationError(f"{name}: malformed no-outcome layer")
        elif any(
            (
                layer["model"] is not None,
                layer["derivation_survivor_count"],
                layer["holdout_evaluated"],
                layer["no_outcome_reasons"],
            )
        ):
            raise ValidationError(f"{name}: malformed not-applicable layer")
        layer_reasons.update(layer["no_outcome_reasons"])
    decisive = document["scientific_outcome"] == "one_or_more_submodels_predict_holdout"
    require_equal(decisive, decisive_layers > 0, "$.scientific_outcome")
    require_equal(
        document["holdout_evaluated"],
        any(layer["holdout_evaluated"] for _, layer in _layer_rows(document)),
        "$.holdout_evaluated",
    )
    if not layer_reasons <= expected_reasons:
        raise ValidationError("layer reasons are absent from terminal predicate results")
    conversion = document["submodels"]["global_map"]["conversion_inline"]["model"]
    global_record = document["submodels"]["global_map"]["record"]["model"]
    if conversion is not None:
        require_equal(
            conversion["conversion_checkpoint_id"],
            CHECKPOINT_IDS[conversion["conversion_ordinal"]],
            "conversion checkpoint identity",
        )
        if global_record is None:
            raise ValidationError("conversion layer requires a frozen global record")
        record = global_record["record"]
        if not record["start"] + 5 <= conversion["inline_boundary"] <= record["end"]:
            raise ValidationError("conversion inline boundary lies outside the global record")
        require_equal(
            len(conversion["slot_reference_pages"]),
            conversion["active_slot_count_at_h_rel_0904"],
            "conversion slot references",
        )
    base_model = document["submodels"]["global_map"]["extended_base"]["model"]
    if base_model is not None and conversion is None:
        raise ValidationError("extended-base layer requires a frozen conversion")
    tdef_model = document["submodels"]["tdef"]["pointer_pair"]["model"]
    if tdef_model is not None:
        record = tdef_model["record"]
        offsets = (
            tdef_model["growth_pointer_offset"],
            tdef_model["delete_reinsert_pointer_offset"],
        )
        if offsets[0] == offsets[1] or any(
            not record["start"] <= offset <= record["end"] - 4
            for offset in offsets
        ):
            raise ValidationError("TDEF pointer windows are not distinct and contained")
    for _, layer in _layer_rows(document):
        model = layer["model"]
        if model is not None and "record" in model:
            record = model["record"]
            if record["start"] >= record["end"]:
                raise ValidationError("layer record interval is empty")
    require_equal(document["claims"], {key: _PLAN.document["claims"][key] for key in document["claims"]}, "$.claims")
    return document


def validate_dry_run_report(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_analyzer_dry_run_report")
    if document["result"] != "pass":
        return document
    parameters = document["parameter_coverage"]
    synthetic = _PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]
    if document["source_kind"] == "a2_schedule_synthetic":
        require_equal(
            document["checkpoint_schedule_source"],
            "hash_pinned_a2_plan_checkpoint_design",
            "$.checkpoint_schedule_source",
        )
        require_equal(
            parameters["conversion_ordinals"],
            list(LEGACY_CONVERSION_ORDINALS),
            "$.parameter_coverage.conversion_ordinals",
        )
        require_equal(parameters["conversion_never"], True, "$.parameter_coverage.conversion_never")
        require_equal(
            parameters["run12_calibration"],
            dict(RUN12_CALIBRATION),
            "$.parameter_coverage.run12_calibration",
        )
        parameter_bindings = (
            ("slot_activation_counts", "slot_activation_at_conversion"),
            ("bit_polarities", "bit_polarity"),
            ("anchor_fill_states", "anchor_fill_state"),
            (
                "record_end_uniform_slack_bytes",
                "record_end_uniform_slack_bytes",
            ),
        )
        for report_key, plan_key in parameter_bindings:
            require_equal(
                parameters[report_key],
                synthetic["free_parameters"][plan_key],
                f"$.parameter_coverage.{report_key}",
            )
        require_equal(
            set(document["predicted_terminal_states"]),
            set(EFFECTIVE_REQUIRED_CASES),
            "$.predicted_terminal_states",
        )
        require_equal(set(document["terminal_predicate_ids"]), set(REQUIRED_REACHABLE_PREDICATE_IDS), "$.terminal_predicate_ids")
        if document["source_identity"]["generator_sha256"] is None:
            raise ValidationError("synthetic dry run requires a generator hash")
    else:
        require_equal(
            document["checkpoint_schedule_source"],
            "explicit_a1_legacy_projection",
            "$.checkpoint_schedule_source",
        )
        blob_ceiling = _PLAN.document["analyzer_dry_run_contract"][
            "retained_a1_input"
        ]["max_input_page_blobs"]
        if document["input_page_blob_count"] > blob_ceiling:
            raise ValidationError("$.input_page_blob_count: exceeds the plan ceiling")
        require_equal(document["source_identity"]["generator_sha256"], None, "$.source_identity.generator_sha256")
    return document


def validate_holdout_structure_receipt(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a2_holdout_structure_receipt")
    require_equal(document["replica"], _PLAN.document["replicas"]["holdout"], "$.replica")
    return document


_VALIDATORS = {
    "dao_a2_allocation_maps_plan": validate_plan,
    "dao_a2_replica_observation": validate_replica_observation,
    "dao_a2_page_index": validate_page_index,
    "dao_a2_replica_artifact_manifest": validate_replica_artifact_manifest,
    "dao_a2_bundle_manifest": validate_bundle_manifest,
    "dao_a2_analysis_report": validate_analysis_report,
    "dao_a2_analyzer_dry_run_report": validate_dry_run_report,
    "dao_a2_holdout_structure_receipt": validate_holdout_structure_receipt,
    "dao_a2_environment": validate_environment,
}


def validate_document(document: dict[str, Any]) -> Any:
    """Dispatch an A2 document to its schema and semantic validator."""
    try:
        validator = _VALIDATORS[document.get("document_type")]
    except (AttributeError, KeyError) as exc:
        raise ValidationError("unknown A2 document type") from exc
    return validator(document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=CHECKED_PLAN)
    args = parser.parse_args(argv)
    try:
        document = load_bounded_json(args.path, BOUNDS["max_json_bytes"])
        validate_document(document)
    except ValidationError as exc:
        print(f"A2 validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"A2 validation passed: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
