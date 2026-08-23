#!/usr/bin/env python3
"""Fail-closed checked contract for DAO-A1-ALLOCATION-MAPS-001."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from protocol_validation import ProtocolSchemaSet, ValidationError, sha256

DAO_ROOT = SCRIPTS_ROOT.parent
A1_ROOT = DAO_ROOT / "experiments" / "a1"
CHECKED_PLAN = A1_ROOT / "a1-allocation-maps.plan.json"
PLAN_SHA256 = "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80"
EXPERIMENT_ID = "DAO-A1-ALLOCATION-MAPS-001"
PAGE_SIZE = 2048
REPLICAS = 3
CHECKPOINT_CEILING = 72
FINAL_PAGE_CEILING = 20480
JSON_BYTE_CEILING = 64 * 1024 * 1024
LOGICAL_READ_CEILING = 8 * 1024 * 1024 * 1024
INSERTED_ROW_CEILING = 200_000
CHANGED_HASH_CEILING = 1_500_000
CANDIDATE_CEILING = 1_000_000
WORK_CEILING = 250_000_000

TABLE_NAMES = ("A1TAB_A", "A1TAB_B", "A1TAB_C", "A1TAB_D")
ROLES = ("D", "L", "P", "H")
ROLE_BINDINGS = (
    {"D": "A1TAB_A", "L": "A1TAB_B", "P": "A1TAB_C", "H": "A1TAB_D"},
    {"D": "A1TAB_B", "L": "A1TAB_C", "P": "A1TAB_D", "H": "A1TAB_A"},
    {"D": "A1TAB_C", "L": "A1TAB_D", "P": "A1TAB_A", "H": "A1TAB_B"},
)
LADDER = (64, 512, 768, *range(896, 1089, 8), 1280)
CHECKPOINT_IDS = (
    "E0", "E0R", "D_GROW_0128", "D_DROP", "D_REGROW_0128",
    *(f"L_REL_{target:04d}" for target in LADDER),
    "L_DELETE_ALTERNATING", "L_REINSERT_SAME", "L_IDLE_REOPEN",
    "P_ABS_04096", "P_ABS_08192", "P_ABS_12288", "P_ABS_16480",
    *(f"H_REL_{target:04d}" for target in LADDER),
    "H_IDLE_REOPEN",
)
IDLE_PAIRS = (
    ("E0", "E0R"),
    ("L_REINSERT_SAME", "L_IDLE_REOPEN"),
    ("H_REL_1280", "H_IDLE_REOPEN"),
)
POINTER_LAYOUTS = ("u24le_page_then_u8_slot", "u8_slot_then_u24le_page")
BASE_FORMULAS = (
    "slot_relative_expected_0_16352",
    "referenced_page_relative",
    "slot_relative_off_by_minus_one",
    "slot_relative_off_by_plus_one",
    "referenced_page_relative_off_by_minus_one",
    "referenced_page_relative_off_by_plus_one",
)
CLAIMS = {
    "descriptive_provider_observation_only": True,
    "general_tdef_catalog_row_index_or_lval_layout": False,
    "unobserved_slot_or_base_behavior": False,
    "compaction_encryption_or_version_behavior": False,
    "rust_correctness": False,
    "dao_compatibility_or_support": False,
}

SCHEMAS = ProtocolSchemaSet(
    A1_ROOT,
    {
        "dao_a1_allocation_maps_plan": "plan.schema.json",
        "dao_a1_replica_observation": "replica-observation.schema.json",
        "dao_a1_page_index": "page-index.schema.json",
        "dao_a1_environment": "environment.schema.json",
        "dao_a1_bundle_manifest": "bundle-manifest.schema.json",
        "dao_a1_analysis_report": "analysis-report.schema.json",
    },
)


@dataclass(frozen=True)
class CheckedPlan:
    document: dict[str, Any]
    checkpoint_ids: tuple[str, ...]


def require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked A1 plan")


def require_keys(value: Any, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValidationError(f"{location}: expected exact fields {sorted(expected)}")
    return value


def load_bounded_json(path: Path, maximum: int = JSON_BYTE_CEILING) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValidationError(f"{path}: missing or exceeds {maximum}-byte JSON ceiling")
        payload = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect JSON: {exc}") from exc
    if len(payload) > maximum or payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: invalid bounded UTF-8 JSON")

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
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{path}: JSON root must be an object")
    return document


def expected_extant_roles(checkpoint_id: str) -> tuple[str, ...]:
    """Return the exact logical-role order present at one frozen checkpoint."""
    if checkpoint_id in ("E0", "E0R", "D_DROP"):
        return ()
    if checkpoint_id.startswith("D_"):
        return ("D",)
    if checkpoint_id.startswith("L_"):
        return ("D", "L")
    if checkpoint_id.startswith("P_"):
        return ("D", "L", "P")
    if checkpoint_id.startswith("H_"):
        return ("D", "L", "P", "H")
    raise ValidationError(f"unknown A1 checkpoint {checkpoint_id!r}")


def _row_payload(role: str, row_id: int) -> bytes:
    seed = f"A1|{role}|{row_id:010d}|".encode("ascii")
    return (seed * ((240 + len(seed) - 1) // len(seed)))[:240]


@functools.lru_cache(maxsize=1024)
def expected_reread_sha256(
    role: str, row_count: int, alternating_full_count: int | None = None
) -> str:
    """Compute the plan's exact bounded DAO reread digest."""
    if (
        role not in ROLES
        or isinstance(row_count, bool)
        or row_count < 0
        or row_count > INSERTED_ROW_CEILING
        or alternating_full_count is not None
        and (alternating_full_count < 0 or alternating_full_count > INSERTED_ROW_CEILING)
    ):
        raise ValidationError("DAO reread digest request exceeds the row contract")
    row_ids = (
        range(1, alternating_full_count + 1, 2)
        if alternating_full_count is not None
        else range(1, row_count + 1)
    )
    digest = hashlib.sha256()
    for row_id in row_ids:
        if row_id < 1 or row_id > 2_147_483_647:
            raise ValidationError("DAO reread row Id is outside positive dbLong range")
        payload = _row_payload(role, row_id)
        digest.update(row_id.to_bytes(4, "little", signed=True))
        digest.update(len(payload).to_bytes(2, "little"))
        digest.update(payload)
    return digest.hexdigest()


def _validate_plan_projection(document: dict[str, Any]) -> None:
    require_keys(
        document,
        {"protocol_version", "document_type", "experiment_id", "preregistration", "execution_gate", "repository_binding", "environment_binding", "question", "replicas", "tables", "checkpoint_design", "page_capture", "hypotheses", "decision_rules", "artifacts", "bounds", "claims"},
        "$",
    )
    for key, expected in (
        ("protocol_version", "1.0.0"),
        ("document_type", "dao_a1_allocation_maps_plan"),
        ("experiment_id", EXPERIMENT_ID),
    ):
        require_equal(document[key], expected, f"$.{key}")
    prereg = require_keys(document["preregistration"], {"provenance_entry", "recorded_utc_date", "acquisition_started", "checkpoint_ceiling_correction", "amendment_rule"}, "$.preregistration")
    require_equal(prereg["provenance_entry"], "EXP-0037", "$.preregistration.provenance_entry")
    require_equal(prereg["recorded_utc_date"], "2026-08-19", "$.preregistration.recorded_utc_date")
    require_equal(prereg["acquisition_started"], False, "$.preregistration.acquisition_started")
    gate = require_keys(document["execution_gate"], {"status", "blocking_requirements"}, "$.execution_gate")
    require_equal(gate["status"], "BLOCKED", "$.execution_gate.status")
    require_equal(gate["blocking_requirements"], ["checked_windows_acquisition", "independent_complete_bundle_validator", "exact_clean_pushed_producer_commit", "licensed_x86_dao_host_binding"], "$.execution_gate.blocking_requirements")
    require_equal(document["environment_binding"], {"operating_system": "Windows", "process_architecture": "x86", "powershell_major": 5, "python_version": "3.13.x", "dao_prog_id": "DAO.DBEngine.36", "provider_identity_and_binary_hash_required": True}, "$.environment_binding")
    require_equal(document["replicas"], {"count": 3, "derivation": [1, 2], "holdout": 3, "holdout_rule": "freeze the complete surviving joint model set from replicas 1 and 2 before reading replica 3; evaluate without refit, candidate addition, deletion, relaxation, or reinterpretation"}, "$.replicas")

    tables = require_keys(document["tables"], {"physical_names", "roles", "role_bindings", "definition", "row_algorithm"}, "$.tables")
    require_equal(tables["physical_names"], list(TABLE_NAMES), "$.tables.physical_names")
    require_equal(tables["roles"], list(ROLES), "$.tables.roles")
    expected_bindings = [{"replica": index, **binding} for index, binding in enumerate(ROLE_BINDINGS, 1)]
    require_equal(tables["role_bindings"], expected_bindings, "$.tables.role_bindings")
    require_keys(tables["definition"], {"indexed", "fields"}, "$.tables.definition")
    require_equal(tables["definition"]["indexed"], False, "$.tables.definition.indexed")
    require_equal(tables["definition"]["fields"], [{"name": "Id", "dao_type": "dbLong", "size": 4}, {"name": "Payload", "dao_type": "dbText", "size": 240, "fixed_length": True}], "$.tables.definition.fields")
    rows = require_keys(tables["row_algorithm"], {"id", "payload", "growth_batch_rows", "reread_order", "rolling_sha256", "delete_rule", "reread_requirement"}, "$.tables.row_algorithm")
    require_equal(rows["growth_batch_rows"], 32, "$.tables.row_algorithm.growth_batch_rows")

    checkpoints = require_keys(document["checkpoint_design"], {"count", "adaptive_checkpoints_allowed", "all_checkpoints_closed_and_quiescent", "d_growth_rule", "relative_growth_rule", "absolute_growth_rule", "checkpoint_ids", "idle_pairs"}, "$.checkpoint_design")
    require_equal(len(CHECKPOINT_IDS), 71, "derived checkpoint count")
    require_equal(checkpoints["count"], len(CHECKPOINT_IDS), "$.checkpoint_design.count")
    require_equal(checkpoints["checkpoint_ids"], list(CHECKPOINT_IDS), "$.checkpoint_design.checkpoint_ids")
    require_equal(checkpoints["idle_pairs"], [list(pair) for pair in IDLE_PAIRS], "$.checkpoint_design.idle_pairs")
    require_equal(checkpoints["adaptive_checkpoints_allowed"], False, "$.checkpoint_design.adaptive_checkpoints_allowed")
    require_equal(checkpoints["all_checkpoints_closed_and_quiescent"], True, "$.checkpoint_design.all_checkpoints_closed_and_quiescent")

    hypotheses = require_keys(document["hypotheses"], {"page_one", "negative_page_candidate_space", "tdef_pointer_layouts", "tdef_pointer_rule", "inline_extent_rule", "type1_rule", "extended_base_candidates", "extended_base_rule"}, "$.hypotheses")
    require_equal(hypotheses["tdef_pointer_layouts"], list(POINTER_LAYOUTS), "$.hypotheses.tdef_pointer_layouts")
    require_equal(hypotheses["extended_base_candidates"], list(BASE_FORMULAS), "$.hypotheses.extended_base_candidates")
    require_keys(document["decision_rules"], {"decisive", "no_scientific_outcome"}, "$.decision_rules")
    require_keys(document["artifacts"], {"plan", "environment", "replica_observations", "page_index_directory", "page_store_directory", "analysis_report", "bundle_manifest"}, "$.artifacts")
    require_equal(document["artifacts"]["replica_observations"], ["observations/replica-01.json", "observations/replica-02.json", "observations/replica-03.json"], "$.artifacts.replica_observations")

    expected_bounds = {
        "page_size": PAGE_SIZE, "replicas": REPLICAS,
        "max_checkpoints_per_replica": CHECKPOINT_CEILING,
        "planned_checkpoints_per_replica": len(CHECKPOINT_IDS),
        "max_final_pages_per_replica": FINAL_PAGE_CEILING,
        "max_logical_checkpoint_read_bytes_per_replica": LOGICAL_READ_CEILING,
        "max_unique_page_blobs": 262144,
        "max_retained_page_store_bytes": 512 * 1024 * 1024,
        "max_bundle_bytes": 768 * 1024 * 1024,
        "max_inserted_rows_per_replica": INSERTED_ROW_CEILING,
        "max_changed_hash_entries": CHANGED_HASH_CEILING,
        "max_candidate_models": CANDIDATE_CEILING,
        "max_analysis_work_units": WORK_CEILING,
        "max_json_bytes": JSON_BYTE_CEILING,
        "worker_timeout_seconds": 1800,
        "campaign_timeout_seconds": 7200,
        "max_child_log_bytes": 1024 * 1024,
        "max_companion_bytes_per_checkpoint": 64 * 1024,
    }
    require_equal(document["bounds"], expected_bounds, "$.bounds")
    require_equal(document["claims"], CLAIMS, "$.claims")


def compile_checked_plan(document: dict[str, Any]) -> CheckedPlan:
    SCHEMAS.validate(document)
    _validate_plan_projection(document)
    return CheckedPlan(document=document, checkpoint_ids=CHECKPOINT_IDS)


def load_checked_plan(path: Path = CHECKED_PLAN) -> CheckedPlan:
    if PLAN_SHA256 == "TO_BE_BOUND" or sha256(path) != PLAN_SHA256:
        raise ValidationError("A1 plan bytes differ from the preregistration")
    return compile_checked_plan(load_bounded_json(path))


def validate_replica_observation(document: dict[str, Any], plan: CheckedPlan) -> dict[str, Any]:
    SCHEMAS.validate(document)
    require_equal(document["plan_sha256"], PLAN_SHA256, "$.plan_sha256")
    replica = document["replica"]
    require_equal(document["role_binding"], ROLE_BINDINGS[replica - 1], "$.role_binding")
    require_equal([item["checkpoint_id"] for item in document["checkpoints"]], list(plan.checkpoint_ids), "$.checkpoints checkpoint order")
    logical_bytes = 0
    maximum_inserted = 0
    l_full_count: int | None = None
    for ordinal, checkpoint in enumerate(document["checkpoints"]):
        location = f"$.checkpoints[{ordinal}]"
        require_equal(checkpoint["ordinal"], ordinal, f"{location}.ordinal")
        require_equal(checkpoint["actual_size_bytes"], checkpoint["actual_file_pages"] * PAGE_SIZE, f"{location}.actual_size_bytes")
        checkpoint_id = checkpoint["checkpoint_id"]
        is_relative = checkpoint_id.startswith(("D_GROW_", "D_REGROW_", "L_REL_", "H_REL_"))
        is_absolute = checkpoint_id.startswith("P_ABS_")
        if is_relative:
            target = int(checkpoint_id.rsplit("_", 1)[1])
            if checkpoint["target_baseline_pages"] is None:
                raise ValidationError(f"{location}.target_baseline_pages: relative target requires baseline")
            require_equal(checkpoint["target_threshold_pages"], checkpoint["target_baseline_pages"] + target, f"{location}.target_threshold_pages")
        elif is_absolute:
            require_equal(checkpoint["target_baseline_pages"], None, f"{location}.target_baseline_pages")
            require_equal(checkpoint["target_threshold_pages"], int(checkpoint_id.rsplit("_", 1)[1]), f"{location}.target_threshold_pages")
        else:
            for field in ("target_baseline_pages", "target_threshold_pages", "target_overshoot_pages"):
                require_equal(checkpoint[field], None, f"{location}.{field}")
        if is_relative or is_absolute:
            threshold = checkpoint["target_threshold_pages"]
            if checkpoint["actual_file_pages"] < threshold:
                raise ValidationError(f"{location}.actual_file_pages: target not reached")
            require_equal(checkpoint["target_overshoot_pages"], checkpoint["actual_file_pages"] - threshold, f"{location}.target_overshoot_pages")
        reread_roles = [row["role"] for row in checkpoint["dao_reread"]]
        expected_roles = expected_extant_roles(checkpoint_id)
        require_equal(reread_roles, list(expected_roles), f"{location}.dao_reread roles")
        for role in ROLES:
            if role not in expected_roles:
                require_equal(checkpoint["table_row_counts"][role], 0, f"{location}.table_row_counts.{role}")
        for row in checkpoint["dao_reread"]:
            require_equal(row["row_count"], checkpoint["table_row_counts"][row["role"]], f"{location}.dao_reread row_count")
            row_count = row["row_count"]
            if checkpoint_id == "L_DELETE_ALTERNATING" and row["role"] == "L":
                if l_full_count is None:
                    raise ValidationError(f"{location}: missing pre-delete L count")
                require_equal(row_count, (l_full_count + 1) // 2, f"{location}.dao_reread L delete count")
                expected_digest = expected_reread_sha256("L", row_count, l_full_count)
            else:
                expected_digest = expected_reread_sha256(row["role"], row_count)
            require_equal(row["rolling_sha256"], expected_digest, f"{location}.dao_reread rolling_sha256")
        if checkpoint_id == "L_REL_1280":
            l_full_count = checkpoint["table_row_counts"]["L"]
        elif checkpoint_id in ("L_REINSERT_SAME", "L_IDLE_REOPEN"):
            if l_full_count is None:
                raise ValidationError(f"{location}: missing L full-count anchor")
            require_equal(checkpoint["table_row_counts"]["L"], l_full_count, f"{location}.table_row_counts.L")
        expected_path = f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint['checkpoint_id']}.json"
        require_equal(checkpoint["page_index"]["path"], expected_path, f"{location}.page_index.path")
        logical_bytes += checkpoint["actual_size_bytes"]
        maximum_inserted = max(maximum_inserted, checkpoint["inserted_rows_total"])
    require_equal(document["logical_checkpoint_read_bytes"], logical_bytes, "$.logical_checkpoint_read_bytes")
    require_equal(document["inserted_rows_total"], maximum_inserted, "$.inserted_rows_total")
    if logical_bytes > LOGICAL_READ_CEILING:
        raise ValidationError("replica observation exceeds preregistered aggregate bounds")
    return document


def validate_page_index(
    document: dict[str, Any],
    observation: dict[str, Any],
    checkpoint: dict[str, Any],
    prior_hashes: list[str],
) -> list[str]:
    """Validate one page index and its exact predecessor delta."""
    SCHEMAS.validate(document)
    ordinal = checkpoint["ordinal"]
    for key in ("plan_sha256", "producer_commit", "run_id", "environment_sha256", "provider_sha256", "replica"):
        require_equal(document[key], observation[key], f"page index $.{key}")
    require_equal(document["checkpoint_id"], checkpoint["checkpoint_id"], "page index $.checkpoint_id")
    require_equal(document["ordinal"], ordinal, "page index $.ordinal")
    predecessor = None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1]
    require_equal(document["predecessor_checkpoint_id"], predecessor, "page index $.predecessor_checkpoint_id")
    require_equal(document["page_count"], checkpoint["actual_file_pages"], "page index $.page_count")
    require_equal(document["file_size_bytes"], checkpoint["actual_size_bytes"], "page index $.file_size_bytes")
    hashes = document["ordered_page_sha256"]
    require_equal(len(hashes), document["page_count"], "page index $.ordered_page_sha256")
    changed = [
        index
        for index in range(max(len(prior_hashes), len(hashes)))
        if index >= len(prior_hashes)
        or index >= len(hashes)
        or prior_hashes[index] != hashes[index]
    ]
    require_equal(document["changed_page_indices"], changed, "page index $.changed_page_indices")
    return hashes


def validate_analysis_report(document: dict[str, Any]) -> None:
    SCHEMAS.validate(document)
    if document["derivation_survivor_count"] > document["candidate_models_examined"]:
        raise ValidationError("A1 derivation survivors exceed examined candidates")
    if document["scientific_outcome"] == "one_joint_model_predicts_holdout":
        if document["derivation_survivor_count"] != 1 or not document["holdout_evaluated"] or document["surviving_model"] is None or document["no_outcome_reasons"]:
            raise ValidationError("decisive A1 report must contain exactly one holdout-predictive model")
        model = document["surviving_model"]
        if model["record_start"] >= model["record_end"]:
            raise ValidationError("A1 model record interval is empty or reversed")
        if not model["record_start"] <= model["used_pointer_offset"] <= model["record_end"] - 4:
            raise ValidationError("A1 used pointer lies outside the record")
        if not model["record_start"] <= model["free_pointer_offset"] <= model["record_end"] - 4:
            raise ValidationError("A1 free pointer lies outside the record")
        if model["used_pointer_offset"] == model["free_pointer_offset"]:
            raise ValidationError("A1 used and free pointer offsets must be distinct")
        if model["high_type1_slot"] != model["low_type1_slot"] + 1:
            raise ValidationError("A1 high type-1 slot must immediately follow low slot")
    elif document["surviving_model"] is not None or not document["no_outcome_reasons"]:
        raise ValidationError("no-outcome A1 report must omit a model and state a reason")
    require_equal(document["claims"], CLAIMS, "$.claims")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("validate-plan")
    plan_parser.add_argument("path", nargs="?", type=Path, default=CHECKED_PLAN)
    observation_parser = subparsers.add_parser("validate-observation")
    observation_parser.add_argument("path", type=Path)
    report_parser = subparsers.add_parser("validate-report")
    report_parser.add_argument("path", type=Path)
    arguments = parser.parse_args(argv)
    try:
        SCHEMAS.lint()
        if arguments.command == "validate-plan":
            load_checked_plan(arguments.path)
        elif arguments.command == "validate-observation":
            plan = load_checked_plan()
            validate_replica_observation(load_bounded_json(arguments.path), plan)
        else:
            validate_analysis_report(load_bounded_json(arguments.path))
    except ValidationError as exc:
        print(f"A1 validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"A1 {arguments.command} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
