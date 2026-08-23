"""Focused structural and arithmetic contracts for the DAO A4 base plan."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a4"
PLAN = EXPERIMENT / "a4-row-anchored-maps.plan.json"
PLAN_SCHEMA = EXPERIMENT / "plan.schema.json"
ANALYSIS_SCHEMA = EXPERIMENT / "analysis-report.schema.json"
DERIVATION_SCHEMA = EXPERIMENT / "derivation-candidates.schema.json"
SCHEMA_SNAPSHOT = EXPERIMENT / "dao-schema-snapshot.schema.json"
OBSERVATION_SCHEMA = EXPERIMENT / "replica-observation.schema.json"
DRY_RUN_SCHEMA = EXPERIMENT / "dry-run-report.schema.json"
REACHABILITY_TRANSCRIPT_SCHEMA = EXPERIMENT / "reachability-transcript.schema.json"
INDEPENDENT_SCHEMA = EXPERIMENT / "independent-validation-report.schema.json"
BUNDLE_SCHEMA = EXPERIMENT / "bundle-manifest.schema.json"
BRIEF = EXPERIMENT / "design-inputs" / "a4-scope-approved.md"
CALIBRATION = EXPERIMENT / "design-inputs" / "a3-calibration-receipt.json"
README = EXPERIMENT / "README.md"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"

PLAN_SHA256 = "550c6e566b8cb14492508cbf6a9b4e3980fe2ecc9729e61b7b9830d4bdd337c3"
BRIEF_SHA256 = "ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7"
CALIBRATION_SHA256 = "788605e1aeca015d88319ef78b3ae34adbec04527efaa11b79f5663474169d3e"
ZERO_SHA256 = "0" * 64
CHECKPOINTS = [
    "EMPTY",
    "EMPTY_R",
    "T1_CREATE_ID",
    "T1_ADD_TEXT",
    "T1_ADD_INDEX",
    "T2_CREATE",
    "T2_DROP",
    "T2_RECREATE",
    "T3_CREATE",
    "T4_CREATE",
    "T1_REL_0064",
    "T1_REL_0512",
    "T1_REL_0768",
    "T1_REL_1280",
    "T1_DELETE_ALL",
    "T1_REINSERT_SAME",
    "T1_IDLE_R",
    "T3_ABS_04096",
    "T3_ABS_08192",
    "T3_ABS_12288",
    "T3_ABS_16480",
    "T4_REL_0064",
    "T4_REL_0896",
    "T4_REL_0904",
    "T4_IDLE_R",
]
LAYERS = [
    "h1_tdef_to_map_row",
    "h2_row_identity_map_role",
    "h3_indirect_traversal",
    "h4_catalog_bootstrap",
]
FINAL_STAGES = {
    "h1_tdef_to_map_row": "h1_locator_pair",
    "h2_row_identity_map_role": "h2_final_role",
    "h3_indirect_traversal": "h3_final_base_formula",
    "root_result": "h4_catalog_root",
    "field_result": "h4_final_encoded_field",
}
OPERATION_IDS = [
    "T1_CREATE_ID",
    "T1_ADD_TEXT",
    "T1_ADD_INDEX",
    "T2_CREATE",
    "T2_RECREATE",
    "T3_CREATE",
    "T4_CREATE",
]
LIFECYCLE_RANGES = {
    "T1-v1": {"start": "T1_CREATE_ID", "end": "T4_IDLE_R"},
    "T2-v1": {"start": "T2_CREATE", "end": "T2_CREATE"},
    "T2-v2": {"start": "T2_RECREATE", "end": "T4_IDLE_R"},
    "T3-v1": {"start": "T3_CREATE", "end": "T4_IDLE_R"},
    "T4-v1": {"start": "T4_CREATE", "end": "T4_IDLE_R"},
}
TERMINAL_STAGES = {
    "A4-H1-TDEF-NONE": "h1_tdef",
    "A4-H1-TDEF-MULTIPLE": "h1_tdef",
    "A4-H1-LOCATOR-LAYOUT-NONE": "h1_target_valid_layout",
    "A4-H1-LOCATOR-LAYOUT-MULTIPLE": "h1_target_valid_layout",
    "A4-H1-LOCATOR-PAIR-NONE": "h1_locator_pair",
    "A4-H1-LOCATOR-PAIR-MULTIPLE": "h1_locator_pair",
    "A4-H1-TARGET-ROW-INVALID": "h1_locator_pair",
    "A4-H1-REPLICA-DISAGREEMENT": "h1_locator_pair",
    "A4-H2-ROW-DIRECTORY-INVALID": "h2_structural_decoder",
    "A4-H2-ROW-FLAGS-INVALID": "h2_structural_decoder",
    "A4-H2-MAP-TAG-UNSUPPORTED": "h2_structural_decoder",
    "A4-H2-ROLE-NONE": "h2_final_role",
    "A4-H2-ROLE-MULTIPLE": "h2_final_role",
    "A4-H2-TRANSITION-UNEXPLAINED": "h2_final_role",
    "A4-H2-REPLICA-DISAGREEMENT": "h2_final_role",
    "A4-H3-CONVERSION-NONE": "h3_conversion",
    "A4-H3-INACTIVE-SLOT-NONE": "h3_conversion",
    "A4-H3-REFERENCE-INVALID": "h3_conversion",
    "A4-H3-BASE-DISCRIMINATION": "h3_conversion",
    "A4-H3-BASE-NONE": "h3_final_base_formula",
    "A4-H3-BASE-MULTIPLE": "h3_final_base_formula",
    "A4-H3-REPLICA-DISAGREEMENT": "h3_final_base_formula",
    "A4-H4-CATALOG-ROOT-NONE": "h4_catalog_root",
    "A4-H4-CATALOG-ROOT-MULTIPLE": "h4_catalog_root",
    "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED": "h4_operation_record",
    "A4-H4-CATALOG-RECORD-NONE": "h4_operation_record",
    "A4-H4-CATALOG-RECORD-MULTIPLE": "h4_operation_record",
    "A4-H4-FIELD-MODEL-NONE": "h4_structural_field",
    "A4-H4-FIELD-MODEL-MULTIPLE": "h4_structural_field",
    "A4-H4-ENCODING-AMBIGUOUS": "h4_final_encoded_field",
    "A4-H4-REPLICA-DISAGREEMENT": "h4_final_encoded_field",
}

SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protocol_validation import (  # noqa: E402
    ValidationError,
    lint_schema,
    validate_schema_value,
)
def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def h1_bindings(include_targets: bool, binding_variant: int = 0) -> list[dict[str, Any]]:
    bindings = []
    for replica in (1, 2):
        for binding_serial, lifecycle in enumerate(LIFECYCLE_RANGES):
            binding = {
                "replica": replica,
                "logical_role": lifecycle.split("-")[0],
                "lifecycle_instance": lifecycle,
                "tdef_page": 20 + binding_serial + binding_variant,
                "applicable_checkpoint_range": copy.deepcopy(
                    LIFECYCLE_RANGES[lifecycle]
                ),
            }
            if include_targets:
                binding["locator_targets"] = [
                    {"page": 24 + binding_serial + binding_variant, "row": 0},
                    {"page": 24 + binding_serial + binding_variant, "row": 1},
                ]
            bindings.append(binding)
    return bindings


def compatible_occurrence(operation: str, index: int, serial: int) -> dict[str, Any]:
    if operation == "T1_ADD_TEXT":
        matched = "5061796c6f6164"
    elif operation == "T1_ADD_INDEX":
        matched = "413449585f4944"
    else:
        matched = "41345441425f4131"
    start = 20 + index * 11 + serial - 1
    return {
        "name_start": start,
        "matched_registered_pattern_id": f"{operation}_CP1252",
        "matched_bytes_hex": matched,
        "stored_length_field": {
            "row_relative_start": start - 1,
            "width": 1,
            "value": len(bytes.fromhex(matched)),
        },
    }


def operation_bindings(serial: int) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": operation,
            "canonical_record_locator": {
                "page": 100 + index,
                "row": 0,
                "row_start": 12,
                "row_end": 100 + index,
            },
            "compatible_name_occurrences": [
                compatible_occurrence(operation, index, serial)
            ],
        }
        for index, operation in enumerate(OPERATION_IDS)
    ]


def candidate(stage: str, serial: int = 1, binding_variant: int = 0) -> dict[str, Any]:
    instance_bindings: list[dict[str, Any]] | None = None
    if stage == "h1_tdef":
        model: dict[str, Any] = {"tdef_lifecycle_signature": "new_tag_02_at_role_create"}
        instance_bindings = h1_bindings(False, binding_variant)
    elif stage == "h1_target_valid_layout":
        model = {
            "layout": "u8_row_then_u24le_page",
            "table_signature_id": "a3_page23_masked_record_0_92",
        }
        instance_bindings = h1_bindings(True, binding_variant)
    elif stage == "h1_locator_pair":
        model = {
            "layout": "u8_row_then_u24le_page",
            "table_signature_id": "a3_page23_masked_record_0_92",
            "locator_offsets": [35 + serial - 1, 39 + serial - 1],
        }
        instance_bindings = h1_bindings(True, binding_variant)
    elif stage == "h2_structural_decoder":
        model = {"row_mask": 8191 if serial % 2 else 4095}
    elif stage == "h2_final_role":
        owned_ordinal = ((serial - 1) // 2) % 2
        model = {
            "row_mask": 8191 if serial % 2 else 4095,
            "polarity": "set_bit_owned_in_use" if serial % 2 else "clear_bit_owned_in_use",
            "owned_in_use_locator_ordinal": owned_ordinal,
            "available_locator_ordinal": 1 - owned_ordinal,
        }
    elif stage == "h3_conversion":
        model = {"conversion": "structural_type_0_to_type_1_with_nonzero_u32_slots"}
    elif stage == "h3_final_base_formula":
        formulas = [
            "slot_ordinal_times_16352_plus_bit_index",
            "referenced_page_times_16352_plus_bit_index",
            "slot_ordinal_times_16352_plus_bit_index_minus_one",
            "slot_ordinal_times_16352_plus_bit_index_plus_one",
        ]
        model = {
            "conversion": "structural_type_0_to_type_1_with_nonzero_u32_slots",
            "base_formula": formulas[(serial - 1) % len(formulas)],
        }
    elif stage == "h4_catalog_root":
        model = {"tdef_page": 20 + serial, "locator_offsets": [35, 39]}
    elif stage == "h4_operation_record":
        operation = OPERATION_IDS[(serial - 1) % len(OPERATION_IDS)]
        model = {
            "root_candidate_id": ZERO_SHA256,
            "operation_id": operation,
            "canonical_record_locator": {
                "page": 100 + serial,
                "row": 0,
                "row_start": 12,
                "row_end": 100,
            },
        }
    elif stage == "h4_structural_field":
        model = {
            "kind_start_delta": min(serial, 16),
            "kind_width": 1,
            "identifier_width": 4,
            "endianness": "little",
            "name_length_start_delta": 1,
            "name_length_width": 1,
            "name_length_endianness": "little",
            "kind_mapping": {"table": 1, "field": 2, "index": 3},
            "identifier_lifecycle": "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2",
            "operation_bindings": operation_bindings(serial),
        }
    elif stage == "h4_final_encoded_field":
        structural = candidate("h4_structural_field", serial)
        selected = [
            {
                "operation_id": binding["operation_id"],
                "selected_occurrence": copy.deepcopy(
                    binding["compatible_name_occurrences"][0]
                ),
            }
            for binding in structural["model"]["operation_bindings"]
        ]
        classes = [
            "cp1252_single_byte_per_scalar",
            "utf8_encoded_byte_count",
            "utf8_unicode_scalar_or_code_unit_count",
        ]
        model = {
            "structural_candidate_id": structural["canonical_candidate_id"],
            "encoding_length_equivalence_class": classes[(serial - 1) % 3],
            "selected_operation_occurrences": selected,
        }
    else:
        raise AssertionError(f"unknown candidate stage {stage}")
    value = {"model_type": stage, "model": model}
    result = {**value}
    if instance_bindings is not None:
        result["instance_bindings"] = instance_bindings
        result["canonical_model_id"] = canonical_sha256(value)
        candidate_identity = {**value, "instance_bindings": instance_bindings}
    else:
        candidate_identity = value
    result["canonical_candidate_id"] = canonical_sha256(candidate_identity)
    return result


def frozen_result(
    stage: str,
    status: str,
    terminal: str | None = None,
    count: int | None = None,
) -> dict[str, Any]:
    if count is None:
        count = 1 if status == "model" else 0
    candidates = [
        candidate(stage, serial, binding_variant=serial - 1)
        for serial in range(1, count + 1)
    ]
    candidates.sort(key=lambda item: item["canonical_candidate_id"])
    return {
        "status": status,
        "predicate_measured_survivor_count": count,
        "derivation_survivor_count": 1 if status == "model" else 0,
        "terminal_predicate_id": terminal,
        "terminal_candidate_stage": stage if status == "no_outcome" else None,
        "candidates": candidates,
        "canonical_candidates_sha256": canonical_sha256(candidates),
    }


def validate_frozen_result(value: dict[str, Any]) -> None:
    candidates = value["candidates"]
    count = value["predicate_measured_survivor_count"]
    final_count = value["derivation_survivor_count"]
    terminal = value["terminal_predicate_id"]
    if len(candidates) != count:
        raise AssertionError("candidate length and survivor count differ")
    if candidates != sorted(candidates, key=lambda item: item["canonical_candidate_id"]):
        raise AssertionError("candidate order is not canonical")
    ids = [item["canonical_candidate_id"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate canonical candidate id")
    if canonical_sha256(candidates) != value["canonical_candidates_sha256"]:
        raise AssertionError("canonical candidate hash mismatch")
    if value["status"] == "model" and (
        count != 1 or final_count != 1 or terminal is not None
        or value["terminal_candidate_stage"] is not None
    ):
        raise AssertionError("model must have one candidate and no terminal")
    if value["status"] == "not_applicable" and (
        count != 0 or final_count != 0 or candidates or terminal is not None
        or value["terminal_candidate_stage"] is not None
    ):
        raise AssertionError("not_applicable result has retained state")
    if value["status"] == "no_outcome" and (
        terminal is None or final_count != 0
        or value["terminal_candidate_stage"] is None
    ):
        raise AssertionError("no_outcome requires a terminal")
    for item in candidates:
        identity = {"model_type": item["model_type"], "model": item["model"]}
        if item["model_type"].startswith("h1_"):
            if item["canonical_model_id"] != canonical_sha256(identity):
                raise AssertionError("canonical H1 model id mismatch")
            identity["instance_bindings"] = item["instance_bindings"]
        if item["canonical_candidate_id"] != canonical_sha256(identity):
            raise AssertionError("canonical candidate id mismatch")
        if item["model_type"] == "h2_final_role":
            model = item["model"]
            if (
                model["owned_in_use_locator_ordinal"]
                == model["available_locator_ordinal"]
            ):
                raise AssertionError("H2 locator ordinals must differ")
        if item["model_type"].startswith("h1_"):
            offsets = item["model"].get("locator_offsets")
            if offsets is not None and offsets != sorted(offsets):
                raise AssertionError("locator offsets must be ascending")
            bindings = item["instance_bindings"]
            expected_order = [
                (replica, lifecycle)
                for replica in (1, 2)
                for lifecycle in ("T1-v1", "T2-v1", "T2-v2", "T3-v1", "T4-v1")
            ]
            if [(row["replica"], row["lifecycle_instance"]) for row in bindings] != expected_order:
                raise AssertionError("H1 instance binding order or coverage differs")
            for binding in bindings:
                lifecycle = binding["lifecycle_instance"]
                if binding["logical_role"] != lifecycle.split("-")[0]:
                    raise AssertionError("logical role differs from lifecycle prefix")
                if binding["applicable_checkpoint_range"] != LIFECYCLE_RANGES[lifecycle]:
                    raise AssertionError("H1 lifecycle range differs")
                checkpoint_range = binding["applicable_checkpoint_range"]
                if CHECKPOINTS.index(checkpoint_range["start"]) > CHECKPOINTS.index(checkpoint_range["end"]):
                    raise AssertionError("H1 lifecycle range is reversed")
                if "locator_targets" in binding:
                    targets = [(target["page"], target["row"]) for target in binding["locator_targets"]]
                    if len(targets) != len(set(targets)):
                        raise AssertionError("locator targets must be distinct")
        if item["model_type"] == "h4_structural_field":
            model = item["model"]
            if "encoding_length_equivalence_class" in model:
                raise AssertionError("structural H4 candidate consumed encoding")
            if model["name_length_endianness"] != model["endianness"]:
                raise AssertionError("stored name length has independent endianness")
            bindings = model["operation_bindings"]
            if [binding["operation_id"] for binding in bindings] != OPERATION_IDS:
                raise AssertionError("H4 operation bindings differ from frozen order")
            for binding in bindings:
                occurrences = binding["compatible_name_occurrences"]
                if not occurrences or occurrences != sorted(occurrences, key=canonical_bytes):
                    raise AssertionError("H4 compatible occurrences are not nonempty and canonical")
        if item["model_type"] == "h4_final_encoded_field":
            selected = item["model"]["selected_operation_occurrences"]
            if [row["operation_id"] for row in selected] != OPERATION_IDS:
                raise AssertionError("H4 selected occurrences differ from frozen order")


def validate_layer_semantics(layers: dict[str, Any]) -> None:
    for name in LAYERS[:3]:
        validate_frozen_result(layers[name])
        expected_stage = layers[name]["terminal_candidate_stage"] or FINAL_STAGES[name]
        if any(item["model_type"] != expected_stage for item in layers[name]["candidates"]):
            raise AssertionError("layer contains a foreign candidate stage")
    root = layers["h4_catalog_bootstrap"]["root_result"]
    field = layers["h4_catalog_bootstrap"]["field_result"]
    validate_frozen_result(root)
    validate_frozen_result(field)
    if root["status"] != "model" and field["status"] != "not_applicable":
        raise AssertionError("H4 fields require a decisive root")
    expected_root = root["terminal_candidate_stage"] or FINAL_STAGES["root_result"]
    if any(item["model_type"] != expected_root for item in root["candidates"]):
        raise AssertionError("H4 root contains a foreign candidate type")
    expected_field = field["terminal_candidate_stage"] or FINAL_STAGES["field_result"]
    if any(item["model_type"] != expected_field for item in field["candidates"]):
        raise AssertionError("H4 field contains a foreign candidate type")


def default_failure_count(contract: dict[str, Any]) -> int:
    rule = contract["failure_survivor_count"]
    if "exact" in rule:
        return rule["exact"]
    if "minimum" in rule:
        return rule["minimum"]
    first_range = rule["allowed_ranges"][0]
    return first_range["exact"] if "exact" in first_range else first_range["minimum"]


def validate_failure_count(contract: dict[str, Any], measured: int) -> None:
    rule = contract["failure_survivor_count"]
    if "exact" in rule:
        valid = measured == rule["exact"]
    elif "minimum" in rule:
        valid = measured >= rule["minimum"]
    else:
        valid = False
        for allowed in rule["allowed_ranges"]:
            if "exact" in allowed and measured == allowed["exact"]:
                valid = True
            elif "minimum" in allowed and measured >= allowed["minimum"]:
                valid = True
    if not valid:
        raise AssertionError("measured failure count violates predicate contract")


def build_layers_for_terminal(
    contracts: list[dict[str, Any]], terminal_index: int | None,
    measured_terminal_count: int | None = None,
) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    terminal_scope = contracts[terminal_index]["scope"] if terminal_index is not None else None
    terminal_id = contracts[terminal_index]["predicate_id"] if terminal_index is not None else None
    terminal_count = measured_terminal_count
    if terminal_count is None and terminal_index is not None:
        terminal_count = default_failure_count(contracts[terminal_index])
    scope_order = ["campaign", *LAYERS]

    def ordinary(name: str) -> dict[str, Any]:
        final_stage = FINAL_STAGES[name]
        if terminal_scope == "campaign":
            return frozen_result(final_stage, "not_applicable")
        if terminal_id is not None and "HOLDOUT" in terminal_id:
            return frozen_result(final_stage, "model")
        if terminal_scope is None or scope_order.index(name) < scope_order.index(terminal_scope):
            return frozen_result(final_stage, "model")
        if name == terminal_scope:
            return frozen_result(
                TERMINAL_STAGES[terminal_id],
                "no_outcome",
                terminal_id,
                terminal_count,
            )
        return frozen_result(final_stage, "not_applicable")

    for name in LAYERS[:3]:
        layers[name] = ordinary(name)

    root_type = FINAL_STAGES["root_result"]
    field_type = FINAL_STAGES["field_result"]
    if terminal_scope == "campaign" or (
        terminal_scope is not None and terminal_scope != "h4_catalog_bootstrap"
    ):
        h4 = {
            "root_result": frozen_result(root_type, "not_applicable"),
            "field_result": frozen_result(field_type, "not_applicable"),
        }
    elif terminal_scope is None or (terminal_id is not None and "HOLDOUT" in terminal_id):
        h4 = {
            "root_result": frozen_result(root_type, "model"),
            "field_result": frozen_result(field_type, "model"),
        }
    else:
        assert terminal_id is not None and terminal_count is not None
        root_terminals = {
            "A4-H4-CATALOG-ROOT-NONE",
            "A4-H4-CATALOG-ROOT-MULTIPLE",
        }
        if terminal_id in root_terminals:
            h4 = {
                "root_result": frozen_result(
                    root_type,
                    "no_outcome",
                    terminal_id,
                    terminal_count,
                ),
                "field_result": frozen_result(field_type, "not_applicable"),
            }
        else:
            h4 = {
                "root_result": frozen_result(root_type, "model"),
                "field_result": frozen_result(
                    TERMINAL_STAGES[terminal_id],
                    "no_outcome",
                    terminal_id,
                    terminal_count,
                ),
            }
    layers["h4_catalog_bootstrap"] = h4
    return layers


def build_report(
    plan: dict[str, Any], terminal_index: int | None = None,
    measured_terminal_count: int | None = None,
) -> dict[str, Any]:
    contracts = plan["predicate_registry"]["predicate_contracts"]
    results = []
    for index, contract in enumerate(contracts):
        if terminal_index is None:
            status, terminal = "pass", None
            count = 0 if contract["scope"] == "campaign" else 1
        else:
            if index < terminal_index:
                status = "pass"
            elif index == terminal_index:
                status = "fail"
            else:
                status = "not_applicable"
            terminal = contract["predicate_id"] if index == terminal_index else None
            if index == terminal_index:
                count = (
                    measured_terminal_count
                    if measured_terminal_count is not None
                    else default_failure_count(contract)
                )
            elif status == "not_applicable" or contract["scope"] == "campaign":
                count = 0
            else:
                count = 1
        results.append(
            {
                "predicate_id": contract["predicate_id"],
                "order": contract["order"],
                "scope": contract["scope"],
                "status": status,
                "terminal_predicate_id": terminal,
                "predicate_measured_survivor_count": count,
                "derivation_survivor_count": 1
                if (
                    contract["scope"] != "campaign"
                    and status != "not_applicable"
                    and (status == "pass" or "HOLDOUT" in contract["predicate_id"])
                )
                else 0,
                "reachability_fixture_id": contract["reachability_fixture_id"],
            }
        )
    holdout_results = build_holdout_results(contracts, terminal_index)
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_analysis_report",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": ZERO_SHA256,
        "revision_plan_sha256": ZERO_SHA256,
        "campaign_id": "synthetic",
        "producer_commit": "0" * 40,
        "derivation_replicas": [1, 2],
        "derivation_candidate_set_sha256": ZERO_SHA256,
        "holdout_replica": 3,
        "holdout_opened_after_freeze": True,
        "analyzer_logical_read_bytes_by_replica": [0, 0, 0],
        "predicate_results": results,
        "layers": build_layers_for_terminal(contracts, terminal_index, measured_terminal_count),
        "holdout_results": holdout_results,
        "transcripts": {
            "row_directories": [],
            "locators": [],
            "map_transitions": [],
            "reference_bitmaps": [],
            "catalog_roots": [],
            "catalog_fields": [],
        },
        "scientific_outcome": (
            "one_or_more_layers_predict_holdout"
            if any(item["status"] == "pass" for item in holdout_results.values())
            else "no_layer_predicts_holdout"
        ),
        "claims": copy.deepcopy(plan["claims"]),
    }


def build_holdout_results(
    contracts: list[dict[str, Any]], terminal_index: int | None
) -> dict[str, Any]:
    names = ["h1", "h2", "h3", "h4_root", "h4_fields"]
    holdout_ids = [
        "A4-H1-HOLDOUT-PREDICTION",
        "A4-H2-HOLDOUT-PREDICTION",
        "A4-H3-HOLDOUT-PREDICTION",
        "A4-H4-HOLDOUT-ROOT",
        "A4-H4-HOLDOUT-FIELDS",
    ]
    terminal_id = contracts[terminal_index]["predicate_id"] if terminal_index is not None else None
    if terminal_id not in holdout_ids:
        status = "pass" if terminal_id is None else "not_applicable"
        return {name: {"status": status, "terminal_predicate_id": None} for name in names}
    failed = holdout_ids.index(terminal_id)
    return {
        name: {
            "status": "pass" if index < failed else "fail" if index == failed else "not_applicable",
            "terminal_predicate_id": terminal_id if index == failed else None,
        }
        for index, name in enumerate(names)
    }


def build_frozen_document(report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_frozen_derivation_candidates",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": ZERO_SHA256,
        "revision_plan_sha256": ZERO_SHA256,
        "campaign_id": "synthetic",
        "derivation_replicas": [1, 2],
        "qualified_pages": [],
        "work_charges": {
            **{key: 0 for key in plan["work_model"]["terms"]},
            "total_work_units": 0,
        },
        "layers": copy.deepcopy(report["layers"]),
        "transcripts": copy.deepcopy(report["transcripts"]),
    }


def validate_report_semantics(report: dict[str, Any], plan: dict[str, Any]) -> None:
    contracts = plan["predicate_registry"]["predicate_contracts"]
    results = report["predicate_results"]
    expected_ids = [contract["predicate_id"] for contract in contracts]
    if [result["predicate_id"] for result in results] != expected_ids:
        raise AssertionError("predicate ids/order differ from the registry")
    if [result["order"] for result in results] != list(range(1, 41)):
        raise AssertionError("predicate order fields are not 1..40")
    if len(set(expected_ids)) != 40:
        raise AssertionError("duplicate predicate id")
    failed = [index for index, result in enumerate(results) if result["status"] == "fail"]
    if len(failed) > 1:
        raise AssertionError("more than one terminal")
    terminal_index = failed[0] if failed else None
    for index, (result, contract) in enumerate(zip(results, contracts, strict=True)):
        if result["scope"] != contract["scope"]:
            raise AssertionError("predicate scope mismatch")
        if result["reachability_fixture_id"] != contract["reachability_fixture_id"]:
            raise AssertionError("reachability fixture mismatch")
        if terminal_index is None or index < terminal_index:
            expected_status = "pass"
        elif index == terminal_index:
            expected_status = "fail"
        else:
            expected_status = "not_applicable"
        if result["status"] != expected_status:
            raise AssertionError("predicate status projection mismatch")
        expected_terminal = contract["predicate_id"] if index == terminal_index else None
        if result["terminal_predicate_id"] != expected_terminal:
            raise AssertionError("predicate terminal projection mismatch")
        measured_count = result["predicate_measured_survivor_count"]
        if index == terminal_index:
            validate_failure_count(contract, measured_count)
        else:
            expected_count = 0 if expected_status == "not_applicable" or contract["scope"] == "campaign" else 1
            if measured_count != expected_count:
                raise AssertionError("predicate measured survivor projection mismatch")
        retains_final_model = (
            contract["scope"] != "campaign"
            and expected_status != "not_applicable"
            and (expected_status == "pass" or "HOLDOUT" in contract["predicate_id"])
        )
        expected_final_count = 1 if retains_final_model else 0
        if result["derivation_survivor_count"] != expected_final_count:
            raise AssertionError("predicate final survivor projection mismatch")
    validate_layer_semantics(report["layers"])
    terminal_count = (
        results[terminal_index]["predicate_measured_survivor_count"]
        if terminal_index is not None else None
    )
    expected_layers = build_layers_for_terminal(contracts, terminal_index, terminal_count)

    def result_projection(value: dict[str, Any]) -> tuple[Any, ...]:
        return (
            value["status"],
            value["terminal_predicate_id"],
            value["terminal_candidate_stage"],
            value["predicate_measured_survivor_count"],
            value["derivation_survivor_count"],
            len(value["candidates"]),
        )

    for layer in LAYERS[:3]:
        if result_projection(report["layers"][layer]) != result_projection(
            expected_layers[layer]
        ):
            raise AssertionError("predicate/layer projection mismatch")
    for part in ("root_result", "field_result"):
        if result_projection(report["layers"]["h4_catalog_bootstrap"][part]) != result_projection(
            expected_layers["h4_catalog_bootstrap"][part]
        ):
            raise AssertionError("predicate/H4 projection mismatch")
    expected_holdout = build_holdout_results(contracts, terminal_index)
    if report["holdout_results"] != expected_holdout:
        raise AssertionError("predicate/holdout projection mismatch")
    projected = (
        "one_or_more_layers_predict_holdout"
        if any(item["status"] == "pass" for item in report["holdout_results"].values())
        else "no_layer_predicts_holdout"
    )
    if report["scientific_outcome"] != projected:
        raise AssertionError("scientific_outcome differs from holdout projection")


def validate_work_charges(charges: dict[str, int]) -> None:
    terms = [value for key, value in charges.items() if key != "total_work_units"]
    if charges["total_work_units"] != sum(terms):
        raise AssertionError("total_work_units mismatch")


def validate_analysis_work_bound(attempted_units: int, bound: int) -> None:
    if attempted_units > bound:
        raise AssertionError("analysis work bound exceeded before manifest creation")


def validate_frozen_file_hash(report: dict[str, Any], frozen_bytes: bytes) -> None:
    actual = hashlib.sha256(frozen_bytes).hexdigest()
    if report["derivation_candidate_set_sha256"] != actual:
        raise AssertionError("complete frozen-file SHA-256 mismatch")


def validate_snapshot_uniqueness(snapshot: dict[str, Any]) -> None:
    def unique(values: list[Any], label: str) -> None:
        if len(values) != len(set(values)):
            raise AssertionError(f"duplicate {label}")

    tables = snapshot["tables"]
    unique([table["logical_role"] for table in tables], "logical role")
    unique([table["name"] for table in tables], "table name")
    unique([table["ordinal"] for table in tables], "table ordinal")
    table_order = [(table["ordinal"], table["name_windows_1252_hex"]) for table in tables]
    if table_order != sorted(table_order):
        raise AssertionError("table ordering is not canonical")
    for table in tables:
        expected_units = [ord(character) for character in table["name"]]
        if table["name_utf16_code_units"] != expected_units:
            raise AssertionError("table BSTR code units differ from Name")
        if table["name_windows_1252_hex"] != table["name"].encode("cp1252").hex():
            raise AssertionError("table strict CP-1252 bytes differ from Name")
        if table["name_utf8_hex"] != table["name"].encode("utf-8").hex():
            raise AssertionError("table strict UTF-8 bytes differ from Name")
        if not table["lifecycle_instance"].startswith(table["logical_role"] + "-"):
            raise AssertionError("lifecycle instance does not match logical role")
        for collection_name in ("fields", "indexes"):
            collection = table[collection_name]
            unique([item["name"] for item in collection], f"{collection_name} name")
            unique([item["ordinal"] for item in collection], f"{collection_name} ordinal")
            order = [
                (item["ordinal"], item.get("name_windows_1252_hex", ""))
                for item in collection
            ]
            if order != sorted(order):
                raise AssertionError(f"{collection_name} ordering is not canonical")
            for item in collection:
                if item.get("name_utf16_code_units") != [
                    ord(character) for character in item["name"]
                ]:
                    raise AssertionError(f"{collection_name} BSTR code units differ")
                if item.get("name_windows_1252_hex") != item["name"].encode("cp1252").hex():
                    raise AssertionError(f"{collection_name} strict CP-1252 bytes differ")
                if item.get("name_utf8_hex") != item["name"].encode("utf-8").hex():
                    raise AssertionError(f"{collection_name} strict UTF-8 bytes differ")
        for index in table["indexes"]:
            unique([item["name"] for item in index["fields"]], "index field name")
            unique([item["ordinal"] for item in index["fields"]], "index field ordinal")
            order = [
                (item["ordinal"], item.get("name_windows_1252_hex", ""))
                for item in index["fields"]
            ]
            if order != sorted(order):
                raise AssertionError("index field ordering is not canonical")
            for item in index["fields"]:
                if item.get("name_utf16_code_units") != [
                    ord(character) for character in item["name"]
                ]:
                    raise AssertionError("index field BSTR code units differ")
                if item.get("name_windows_1252_hex") != item["name"].encode("cp1252").hex():
                    raise AssertionError("index field strict CP-1252 bytes differ")
                if item.get("name_utf8_hex") != item["name"].encode("utf-8").hex():
                    raise AssertionError("index field strict UTF-8 bytes differ")


def validate_growth_baseline(checkpoint_id: str, baseline: int | None) -> None:
    is_absolute = checkpoint_id.startswith("T3_ABS_")
    if is_absolute != (baseline is None):
        raise AssertionError("baseline nullability does not match checkpoint class")


def validate_snapshot_binding(
    snapshot: dict[str, Any],
    observation: dict[str, Any],
    page_index: dict[str, Any],
    manifest_entry: dict[str, Any],
    actual_bytes: bytes,
) -> None:
    for field in (
        "experiment_id",
        "plan_sha256",
        "revision_plan_sha256",
        "producer_commit",
        "campaign_id",
        "environment_sha256",
        "provider_sha256",
        "replica",
        "checkpoint_id",
        "ordinal",
    ):
        if snapshot[field] != observation[field]:
            raise AssertionError(f"snapshot binding mismatch: {field}")
    database_sha256 = page_index["database_sha256"]
    if not (
        snapshot["database_sha256_before_read"]
        == snapshot["database_sha256_after_read"]
        == database_sha256
    ):
        raise AssertionError("snapshot/page-index database hash mismatch")
    reference = observation["dao_schema_snapshot"]
    actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
    if not (
        reference["path"] == manifest_entry["path"]
        and reference["sha256"] == manifest_entry["sha256"] == actual_sha256
        and reference["size_bytes"] == manifest_entry["size_bytes"] == len(actual_bytes)
    ):
        raise AssertionError("snapshot reference/manifest/bytes mismatch")


class A4PlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_bytes = PLAN.read_bytes()
        cls.plan = json.loads(cls.plan_bytes)
        cls.plan_schema = json.loads(PLAN_SCHEMA.read_bytes())
        cls.analysis_schema = json.loads(ANALYSIS_SCHEMA.read_bytes())
        cls.derivation_schema = json.loads(DERIVATION_SCHEMA.read_bytes())

    def test_plan_hash_is_frozen_in_readme_and_exp_0052(self) -> None:
        self.assertEqual(hashlib.sha256(self.plan_bytes).hexdigest(), PLAN_SHA256)
        readme = README.read_text(encoding="utf-8")
        provenance = PROVENANCE.read_text(encoding="utf-8")
        self.assertIn(PLAN_SHA256, readme)
        self.assertIn("### EXP-0052", provenance)
        self.assertIn(PLAN_SHA256, provenance)
        ids = [int(value) for value in re.findall(r"^### EXP-(\d{4})", provenance, re.M)]
        self.assertEqual(max(ids), 52)
        self.assertEqual(len(ids), len(set(ids)))

    def test_approved_brief_and_calibration_receipt_are_hash_bound(self) -> None:
        self.assertEqual(hashlib.sha256(BRIEF.read_bytes()).hexdigest(), BRIEF_SHA256)
        self.assertEqual(
            hashlib.sha256(CALIBRATION.read_bytes()).hexdigest(),
            CALIBRATION_SHA256,
        )
        inputs = self.plan["preregistration"]["origin_disclosure"]["design_inputs"]
        self.assertEqual([item["sha256"] for item in inputs], [BRIEF_SHA256, CALIBRATION_SHA256])
        self.assertEqual(
            self.plan["record_candidate_procedure"]["calibration_receipt"]["sha256"],
            CALIBRATION_SHA256,
        )

    def test_plan_and_all_document_schemas_lint(self) -> None:
        for path in sorted(EXPERIMENT.glob("*.schema.json")):
            with self.subTest(schema=path.name):
                lint_schema(json.loads(path.read_bytes()))
        validate_schema_value(self.plan, self.plan_schema, self.plan_schema, "$")

    def test_exact_schedule_dao_protocol_and_expected_schema_are_frozen(self) -> None:
        design = self.plan["checkpoint_design"]
        self.assertEqual(design["count"], 25)
        self.assertEqual(design["checkpoint_ids"], CHECKPOINTS)
        self.assertFalse(design["adaptive_checkpoints_allowed"])
        self.assertTrue(design["all_checkpoints_closed_and_quiescent"])
        expected = self.plan["tables"]["expected_schema_by_checkpoint"]
        self.assertEqual(list(expected), CHECKPOINTS)
        self.assertEqual(expected["T2_CREATE"][-1], "T2:v1:id+payload")
        self.assertNotIn("T2:v1:id+payload", expected["T2_DROP"])
        self.assertEqual(expected["T2_RECREATE"][-1], "T2:v2:id+payload")
        protocol = self.plan["tables"]["dao_protocol"]
        for required in (
            "DAO.DBEngine.36.Workspaces(0)",
            "workspace.CreateDatabase",
            'workspace.OpenDatabase(path, False, True, "")',
            "dbVersion30 is numeric 32",
            "dbOpenDynaset numeric 2",
            "No DAO workspace BeginTrans",
            "three consecutive equal observations",
            "exceed 200000",
            "No compact or repair",
        ):
            self.assertIn(required, " ".join(protocol.values()))
        fields = self.plan["tables"]["definition"]["fields"]
        self.assertEqual(self.plan["tables"]["definition"]["table_attributes_numeric"], 0)
        self.assertEqual(
            [(field["required"], field["allow_zero_length"]) for field in fields],
            [(False, None), (False, False)],
        )
        index = self.plan["tables"]["definition"]["index"]
        self.assertEqual(
            [index[key] for key in ("primary", "unique", "required", "ignore_nulls")],
            [False, False, False, False],
        )
        self.assertFalse(index["descending"])
        construction = protocol["object_construction"]
        for required in (
            "TableDef Attributes = 0",
            "Id.Attributes = 0",
            "Payload.Attributes = dbFixedField",
            "numeric value 1",
            "Descending = False",
        ):
            self.assertIn(required, construction)

    def test_role_rotation_and_strict_name_capture_are_exact(self) -> None:
        tables = self.plan["tables"]
        names = tables["physical_names"]
        self.assertEqual(names, ["A4TAB_A1", "A4TAB_B2", "A4TAB_C3", "A4TAB_É4"])
        self.assertEqual({len(name) for name in names}, {8})
        self.assertEqual({len(name.encode("cp1252")) for name in names}, {8})
        self.assertEqual("A4TAB_É4".encode("cp1252").hex(), "41345441425fc934")
        capture = tables["identifier_discriminator"]["name_capture_rule"]
        for required in (
            "WideCharToMultiByte",
            "WC_NO_BEST_FIT_CHARS",
            "usedDefaultChar == FALSE",
            "strict UTF-8",
            "does not compare either candidate to physical bytes",
        ):
            self.assertIn(required, capture)
        grammar = self.plan["candidate_grammars"]["h4"]
        self.assertEqual(
            [item["u00c9_hex"] for item in grammar["name_encodings"]],
            ["c9", "c389"],
        )
        cp1252_class = grammar["name_length_equivalence_classes"][0]
        self.assertEqual(len(cp1252_class["members"]), 2)
        self.assertIn("no identifier within CP1252", cp1252_class["reason"])

    def test_all_40_abstract_fixture_labels_project_to_claimed_rows(self) -> None:
        registry = self.plan["predicate_registry"]
        flattened = registry["campaign_evaluated_before_any_layer"] + [
            predicate
            for sequence in registry["per_layer_ordered_predicates"].values()
            for predicate in sequence
        ] + registry["holdout_phase_ordered_predicates"]
        contracts = registry["predicate_contracts"]
        self.assertEqual(len(flattened), 40)
        self.assertEqual([item["predicate_id"] for item in contracts], flattened)
        self.assertEqual([item["order"] for item in contracts], list(range(1, 41)))
        self.assertEqual(
            registry["terminal_candidate_stage_by_predicate"], TERMINAL_STAGES
        )
        self.assertEqual(len({item["reachability_fixture_id"] for item in contracts}), 40)
        required = {
            "predicate_id", "order", "scope", "prerequisites", "input_candidate_set",
            "pass_iff", "fail_iff", "terminal_id", "failure_survivor_count",
            "later_status", "reachability_fixture_id", "reachability_fixture",
            "fixture_status",
        }
        for contract in contracts:
            self.assertEqual(set(contract), required)
            self.assertEqual(contract["terminal_id"], contract["predicate_id"])
            self.assertEqual(contract["later_status"], "not_applicable")
            self.assertTrue(contract["pass_iff"])
            self.assertTrue(contract["fail_iff"])
            self.assertTrue(contract["reachability_fixture"])
            self.assertEqual(
                contract["fixture_status"],
                "claimed_reachable; execution_required_before_dispatch",
            )
        evaluation = registry["evaluation_rule"]
        for phrase in ("Evaluation has two phases", "Derivation phase", "Holdout phase", "derivation layer depends", "sole terminal", "all 36 scientific predicates"):
            self.assertIn(phrase, evaluation)

    def test_abstract_terminal_reports_validate_schema_shapes(self) -> None:
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        for terminal_index in [None, *range(40)]:
            with self.subTest(terminal=terminal_index):
                report = build_report(self.plan, terminal_index)
                validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
                validate_report_semantics(report, self.plan)

    def test_minimum_counts_use_actual_measurement_not_the_minimum(self) -> None:
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        selected_ids = {
            "A4-H1-TDEF-MULTIPLE",
            "A4-H2-ROLE-MULTIPLE",
            "A4-H3-BASE-MULTIPLE",
            "A4-H4-FIELD-MODEL-MULTIPLE",
        }
        multiple_indexes = [
            index for index, contract in enumerate(contracts)
            if contract["predicate_id"] in selected_ids
        ]
        self.assertEqual(len(multiple_indexes), 4)
        for measured in (3, 4):
            for index in multiple_indexes:
                with self.subTest(predicate=contracts[index]["predicate_id"], measured=measured):
                    report = build_report(self.plan, index, measured)
                    validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
                    validate_report_semantics(report, self.plan)
                    layer = report["layers"][contracts[index]["scope"]]
                    if contracts[index]["scope"] == "h4_catalog_bootstrap":
                        layer = layer["field_result"]
                    self.assertEqual(layer["predicate_measured_survivor_count"], measured)
                    self.assertEqual(len(layer["candidates"]), measured)
        for index in multiple_indexes:
            with self.subTest(predicate=contracts[index]["predicate_id"], measured=1):
                report = build_report(self.plan, index, 1)
                with self.assertRaises(AssertionError):
                    validate_report_semantics(report, self.plan)

    def test_h4_structural_and_final_field_candidate_phases_are_distinct(self) -> None:
        for schema in (self.derivation_schema, self.analysis_schema):
            self.assertNotIn("h4FieldCandidate", schema["$defs"])
            self.assertIn("h4StructuralFieldCandidate", schema["$defs"])
            self.assertIn("h4FinalFieldCandidate", schema["$defs"])
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        by_id = {row["predicate_id"]: index for index, row in enumerate(contracts)}
        structural = build_report(
            self.plan, by_id["A4-H4-FIELD-MODEL-MULTIPLE"], 2
        )
        structural_result = structural["layers"]["h4_catalog_bootstrap"]["field_result"]
        validate_schema_value(structural, self.analysis_schema, self.analysis_schema, "$")
        validate_report_semantics(structural, self.plan)
        self.assertEqual(structural_result["terminal_candidate_stage"], "h4_structural_field")
        self.assertEqual(len(structural_result["candidates"]), 2)
        for item in structural_result["candidates"]:
            self.assertNotIn("encoding_length_equivalence_class", item["model"])
            self.assertNotIn("name_start", item["model"])
            self.assertEqual(
                item["model"]["name_length_endianness"],
                item["model"]["endianness"],
            )
            self.assertEqual(
                [binding["operation_id"] for binding in item["model"]["operation_bindings"]],
                OPERATION_IDS,
            )
        distinct_starts = [
            binding["compatible_name_occurrences"][0]["name_start"]
            for binding in structural_result["candidates"][0]["model"]["operation_bindings"]
        ]
        self.assertEqual(len(set(distinct_starts)), 7)

        for measured in (0, 2):
            with self.subTest(final_candidates=measured):
                final = build_report(
                    self.plan, by_id["A4-H4-ENCODING-AMBIGUOUS"], measured
                )
                field_result = final["layers"]["h4_catalog_bootstrap"]["field_result"]
                validate_schema_value(final, self.analysis_schema, self.analysis_schema, "$")
                validate_report_semantics(final, self.plan)
                self.assertEqual(field_result["terminal_candidate_stage"], "h4_final_encoded_field")
                self.assertEqual(len(field_result["candidates"]), measured)

    def test_h1_physical_ids_and_lifecycle_ranges_are_semantic(self) -> None:
        required_shapes = {
            "h1TdefCandidate", "h1TargetValidLayoutCandidate", "h1LocatorPairCandidate",
            "h2StructuralDecoderCandidate", "h2FinalRoleCandidate",
            "h3ConversionCandidate", "h3FinalBaseFormulaCandidate",
            "h4RootCandidate", "h4OperationRecordCandidate",
            "h4StructuralFieldCandidate", "h4FinalFieldCandidate",
        }
        for schema in (self.derivation_schema, self.analysis_schema):
            self.assertTrue(required_shapes.issubset(schema["$defs"]))
        first = candidate("h1_locator_pair", binding_variant=0)
        second = candidate("h1_locator_pair", binding_variant=1)
        self.assertEqual(first["canonical_model_id"], second["canonical_model_id"])
        self.assertNotEqual(first["canonical_candidate_id"], second["canonical_candidate_id"])
        result = frozen_result("h1_locator_pair", "model")
        result["candidates"] = sorted(
            [first, second], key=lambda item: item["canonical_candidate_id"]
        )
        result["predicate_measured_survivor_count"] = 2
        result["derivation_survivor_count"] = 0
        result["status"] = "no_outcome"
        result["terminal_predicate_id"] = "A4-H1-LOCATOR-PAIR-MULTIPLE"
        result["terminal_candidate_stage"] = "h1_locator_pair"
        result["canonical_candidates_sha256"] = canonical_sha256(result["candidates"])
        validate_frozen_result(result)

        invalid_range = frozen_result("h1_locator_pair", "model")
        invalid_range["candidates"][0]["instance_bindings"][1]["applicable_checkpoint_range"] = {
            "start": "T1_CREATE_ID",
            "end": "T4_IDLE_R",
        }
        invalid_range["canonical_candidates_sha256"] = canonical_sha256(
            invalid_range["candidates"]
        )
        with self.assertRaises(AssertionError):
            validate_frozen_result(invalid_range)

    def test_every_nonzero_early_terminal_serializes_without_downstream_choice(self) -> None:
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        for index, contract in enumerate(contracts[:35]):
            if contract["scope"] == "campaign" or default_failure_count(contract) == 0:
                continue
            report = build_report(self.plan, index)
            validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
            validate_report_semantics(report, self.plan)
            frozen = build_frozen_document(report, self.plan)
            validate_schema_value(frozen, self.derivation_schema, self.derivation_schema, "$")
            self.assertEqual(frozen["layers"], report["layers"])
            if contract["scope"] == "h4_catalog_bootstrap":
                part = (
                    "root_result"
                    if contract["predicate_id"].startswith("A4-H4-CATALOG-ROOT")
                    else "field_result"
                )
                result = report["layers"][contract["scope"]][part]
            else:
                result = report["layers"][contract["scope"]]
            self.assertEqual(result["terminal_candidate_stage"], TERMINAL_STAGES[contract["predicate_id"]])
            self.assertTrue(all(item["model_type"] == result["terminal_candidate_stage"] for item in result["candidates"]))

    def test_freeze_precedes_holdout_and_is_identical_for_pass_and_failure(self) -> None:
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        by_id = {row["predicate_id"]: index for index, row in enumerate(contracts)}
        indexes = [
            by_id["A4-H1-TDEF-NONE"],
            by_id["A4-H1-TDEF-MULTIPLE"],
            by_id["A4-H4-FIELD-MODEL-NONE"],
        ]
        for index in indexes:
            report = build_report(self.plan, index)
            freeze = {
                "protocol_version": "1.0.0",
                "document_type": "dao_a4_frozen_derivation_candidates",
                "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
                "plan_sha256": ZERO_SHA256,
                "revision_plan_sha256": ZERO_SHA256,
                "campaign_id": "synthetic",
                "derivation_replicas": [1, 2],
                "qualified_pages": [],
                "work_charges": {
                    "tdef_lifecycle_signatures": 0,
                    "raw_locator_windows": 0,
                    "raw_locator_pairs": 0,
                    "h1_target_validity_checks": 0,
                    "valid_path_row_directory_entries": 0,
                    "type_1_slots": 0,
                    "type_0_and_tag_05_bitmap_bits": 0,
                    "role_transition_evaluations": 0,
                    "base_formula_evaluations": 0,
                    "catalog_root_signatures": 0,
                    "catalog_raw_rows": 0,
                    "encoding_union_anchor_bytes": 0,
                    "h4_name_length_structural_tuples": 0,
                    "encoding_length_equivalence_candidates": 0,
                    "candidate_serializations": 0,
                    "total_work_units": 0,
                },
                "layers": copy.deepcopy(report["layers"]),
                "transcripts": copy.deepcopy(report["transcripts"]),
            }
            with self.subTest(terminal=contracts[index]["predicate_id"]):
                validate_schema_value(freeze, self.derivation_schema, self.derivation_schema, "$")
                validate_layer_semantics(freeze["layers"])
                validate_work_charges(freeze["work_charges"])
                self.assertEqual(freeze["layers"], report["layers"])
        partial = build_report(
            self.plan, by_id["A4-H4-FIELD-MODEL-NONE"]
        )["layers"]["h4_catalog_bootstrap"]
        self.assertEqual(partial["root_result"]["status"], "model")
        self.assertEqual(partial["field_result"]["status"], "no_outcome")
        pass_report = build_report(self.plan)
        frozen_pass = pass_report["layers"]
        frozen_document = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a4_frozen_derivation_candidates",
            "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
            "plan_sha256": ZERO_SHA256,
            "revision_plan_sha256": ZERO_SHA256,
            "campaign_id": "synthetic",
            "derivation_replicas": [1, 2],
            "qualified_pages": [],
            "work_charges": {
                **{
                    key: 0
                    for key in self.plan["work_model"]["terms"]
                },
                "total_work_units": 0,
            },
            "layers": frozen_pass,
            "transcripts": copy.deepcopy(pass_report["transcripts"]),
        }
        frozen_bytes = canonical_bytes(frozen_document)
        frozen_sha256 = hashlib.sha256(frozen_bytes).hexdigest()
        pass_report["derivation_candidate_set_sha256"] = frozen_sha256
        validate_frozen_file_hash(pass_report, frozen_bytes)
        failed_holdout = build_report(
            self.plan, by_id["A4-H4-HOLDOUT-ROOT"]
        )
        failed_holdout["derivation_candidate_set_sha256"] = frozen_sha256
        self.assertEqual(failed_holdout["layers"], frozen_pass)
        self.assertEqual(failed_holdout["holdout_results"]["h4_root"]["status"], "fail")
        self.assertEqual(
            failed_holdout["holdout_results"]["h4_fields"]["status"],
            "not_applicable",
        )
        self.assertEqual(
            canonical_bytes(failed_holdout["layers"]), canonical_bytes(frozen_pass)
        )
        self.assertEqual(
            pass_report["derivation_candidate_set_sha256"],
            failed_holdout["derivation_candidate_set_sha256"],
        )
        self.assertEqual(hashlib.sha256(frozen_bytes).hexdigest(), frozen_sha256)

    def test_malformed_duplicate_classes_and_equal_h2_ordinals_are_rejected(self) -> None:
        report = build_report(self.plan)
        malformed = copy.deepcopy(report)
        malformed["predicate_results"][1]["predicate_id"] = malformed["predicate_results"][0]["predicate_id"]
        with self.assertRaises(AssertionError):
            validate_report_semantics(malformed, self.plan)

        h2 = copy.deepcopy(report["layers"]["h2_row_identity_map_role"])
        h2["candidates"][0]["model"]["available_locator_ordinal"] = 0
        h2["canonical_candidates_sha256"] = canonical_sha256(h2["candidates"])
        with self.assertRaises(AssertionError):
            validate_frozen_result(h2)

        h1 = copy.deepcopy(report["layers"]["h1_tdef_to_map_row"])
        h1["candidates"][0]["instance_bindings"][0]["locator_targets"] = [
            {"page": 24, "row": 0},
            {"page": 24, "row": 0},
        ]
        h1["canonical_candidates_sha256"] = canonical_sha256(h1["candidates"])
        with self.assertRaises(AssertionError):
            validate_frozen_result(h1)

        stale_id = copy.deepcopy(report["layers"]["h1_tdef_to_map_row"])
        stale_id["candidates"][0]["model"]["layout"] = "u24le_page_then_u8_row"
        stale_id["canonical_candidates_sha256"] = canonical_sha256(
            stale_id["candidates"]
        )
        with self.assertRaises(AssertionError):
            validate_frozen_result(stale_id)

        reversed_offsets = copy.deepcopy(report["layers"]["h1_tdef_to_map_row"])
        candidate_value = reversed_offsets["candidates"][0]
        candidate_value["model"]["locator_offsets"] = [39, 35]
        candidate_value["canonical_model_id"] = canonical_sha256(
            {"model_type": candidate_value["model_type"], "model": candidate_value["model"]}
        )
        reversed_offsets["canonical_candidates_sha256"] = canonical_sha256(
            reversed_offsets["candidates"]
        )
        with self.assertRaises(AssertionError):
            validate_frozen_result(reversed_offsets)

        layer_mismatch = copy.deepcopy(report)
        layer_mismatch["layers"]["h1_tdef_to_map_row"] = frozen_result(
            "h1_locator_pair", "no_outcome", "A4-H1-TDEF-MULTIPLE", 2
        )
        with self.assertRaises(AssertionError):
            validate_report_semantics(layer_mismatch, self.plan)

        holdout_mismatch = copy.deepcopy(report)
        holdout_mismatch["holdout_results"] = {
            name: {"status": "not_applicable", "terminal_predicate_id": None}
            for name in ("h1", "h2", "h3", "h4_root", "h4_fields")
        }
        holdout_mismatch["scientific_outcome"] = "no_layer_predicts_holdout"
        with self.assertRaises(AssertionError):
            validate_report_semantics(holdout_mismatch, self.plan)

        frozen_bytes = canonical_bytes({"complete": "frozen document"})
        with self.assertRaises(AssertionError):
            validate_frozen_file_hash(report, frozen_bytes)

        tamper_ids = [case["id"] for case in self.plan["independent_validator_contract"]["tamper_cases"]]
        self.assertEqual(len(tamper_ids), len(set(tamper_ids)))
        with self.assertRaises(AssertionError):
            if len([*tamper_ids, tamper_ids[0]]) != len(set([*tamper_ids, tamper_ids[0]])):
                raise AssertionError("duplicate tamper id")

    def test_snapshot_uniqueness_and_strict_name_fields_are_semantically_checked(self) -> None:
        schema = json.loads(SCHEMA_SNAPSHOT.read_bytes())
        self.assertFalse(schema["properties"]["dao_identifier_observable"]["const"])
        self.assertIn("required", schema["$defs"]["index"]["required"])
        self.assertIn(
            {"type": "null"},
            schema["$defs"]["field"]["properties"]["allow_zero_length"]["anyOf"],
        )
        table = {
            "ordinal": 0,
            "ordinal_source": "TableDefs zero-based position after Refresh and exact extant scheduled-name filtering",
            "logical_role": "T1",
            "lifecycle_instance": "T1-v1",
            "name": "A4TAB_A1",
            "name_utf16_code_units": [ord(char) for char in "A4TAB_A1"],
            "name_windows_1252_hex": "A4TAB_A1".encode("cp1252").hex(),
            "name_utf8_hex": "A4TAB_A1".encode("utf-8").hex(),
            "attributes": 0,
            "row_count": 0,
            "rolling_row_sha256": ZERO_SHA256,
            "fields": [{
                "ordinal": 0,
                "ordinal_source": "Fields zero-based position after Refresh and the all-fields filter",
                "name": "Id",
                "name_utf16_code_units": [73, 100],
                "name_windows_1252_hex": "4964",
                "name_utf8_hex": "4964",
                "type": 4,
                "size": 4,
                "attributes": 0,
                "required": False,
                "allow_zero_length": None,
            }],
            "indexes": [],
        }
        snapshot = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a4_schema_snapshot",
            "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
            "plan_sha256": ZERO_SHA256,
            "revision_plan_sha256": ZERO_SHA256,
            "producer_commit": "0" * 40,
            "campaign_id": "synthetic",
            "environment_sha256": ZERO_SHA256,
            "provider_sha256": ZERO_SHA256,
            "replica": 1,
            "checkpoint_id": "T1_CREATE_ID",
            "ordinal": 2,
            "windows_ansi_code_page": 1252,
            "database_sha256_before_read": ZERO_SHA256,
            "database_sha256_after_read": ZERO_SHA256,
            "database_unchanged_by_read": True,
            "dao_identifier_observable": False,
            "identity_oracle": "listed_operation_instance_equality_only",
            "canonicalization": schema["properties"]["canonicalization"]["const"],
            "tables": [table],
        }
        validate_schema_value(snapshot, schema, schema, "$")
        validate_snapshot_uniqueness(snapshot)
        actual_bytes = canonical_bytes(snapshot)
        path = "schema-snapshots/replica-01/02-T1_CREATE_ID.json"
        reference = {
            "path": path,
            "sha256": hashlib.sha256(actual_bytes).hexdigest(),
            "size_bytes": len(actual_bytes),
        }
        observation_binding = {
            field: snapshot[field]
            for field in (
                "experiment_id", "plan_sha256", "revision_plan_sha256",
                "producer_commit", "campaign_id", "environment_sha256",
                "provider_sha256", "replica", "checkpoint_id", "ordinal",
            )
        }
        observation_binding["dao_schema_snapshot"] = reference
        manifest_entry = {"path": path, "role": "dao_schema_snapshot", **reference}
        validate_snapshot_binding(
            snapshot,
            observation_binding,
            {"database_sha256": ZERO_SHA256},
            manifest_entry,
            actual_bytes,
        )
        malformed_binding = copy.deepcopy(observation_binding)
        malformed_binding["replica"] = 2
        with self.assertRaises(AssertionError):
            validate_snapshot_binding(
                snapshot,
                malformed_binding,
                {"database_sha256": ZERO_SHA256},
                manifest_entry,
                actual_bytes,
            )
        mutations = []
        for key, value in (("logical_role", "T1"), ("name", "A4TAB_A1"), ("ordinal", 0)):
            malformed = copy.deepcopy(snapshot)
            duplicate = copy.deepcopy(table)
            duplicate["ordinal"] = 1
            duplicate["logical_role"] = "T2"
            duplicate["lifecycle_instance"] = "T2-v1"
            duplicate["name"] = "A4TAB_B2"
            duplicate["name_utf16_code_units"] = [ord(char) for char in "A4TAB_B2"]
            duplicate["name_windows_1252_hex"] = "A4TAB_B2".encode("cp1252").hex()
            duplicate["name_utf8_hex"] = "A4TAB_B2".encode("utf-8").hex()
            duplicate[key] = value
            malformed["tables"].append(duplicate)
            mutations.append(malformed)
        for malformed in mutations:
            with self.assertRaises(AssertionError):
                validate_snapshot_uniqueness(malformed)

        for duplicate_key in ("name", "ordinal"):
            malformed = copy.deepcopy(snapshot)
            duplicate = copy.deepcopy(table["fields"][0])
            duplicate["ordinal"] = 1
            duplicate["name"] = "Payload"
            if duplicate_key == "name":
                duplicate["name"] = "Id"
            else:
                duplicate["ordinal"] = 0
            malformed["tables"][0]["fields"].append(duplicate)
            with self.assertRaises(AssertionError):
                validate_snapshot_uniqueness(malformed)

        index = {
            "ordinal": 0,
            "ordinal_source": "Indexes zero-based position after Refresh and exact A4IX_ID scheduled-name filtering",
            "name": "A4IX_ID",
            "name_utf16_code_units": [ord(char) for char in "A4IX_ID"],
            "name_windows_1252_hex": "A4IX_ID".encode("cp1252").hex(),
            "name_utf8_hex": "A4IX_ID".encode("utf-8").hex(),
            "attributes": 0,
            "primary": False,
            "unique": False,
            "required": False,
            "ignore_nulls": False,
            "fields": [{
                "ordinal": 0,
                "ordinal_source": "Index.Fields zero-based position after Refresh and the all-fields filter",
                "name": "Id",
                "name_utf16_code_units": [73, 100],
                "name_windows_1252_hex": "4964",
                "name_utf8_hex": "4964",
                "descending": False,
            }],
        }
        for duplicate_key in ("name", "ordinal"):
            malformed = copy.deepcopy(snapshot)
            malformed["tables"][0]["indexes"] = [copy.deepcopy(index)]
            duplicate = copy.deepcopy(index)
            duplicate["ordinal"] = 1
            duplicate["name"] = "A4IX_OTHER"
            duplicate["name_utf16_code_units"] = [ord(char) for char in "A4IX_OTHER"]
            duplicate["name_windows_1252_hex"] = "A4IX_OTHER".encode("cp1252").hex()
            duplicate["name_utf8_hex"] = "A4IX_OTHER".encode("utf-8").hex()
            duplicate[duplicate_key] = index[duplicate_key]
            malformed["tables"][0]["indexes"].append(duplicate)
            with self.assertRaises(AssertionError):
                validate_snapshot_uniqueness(malformed)

        for duplicate_key in ("name", "ordinal"):
            malformed = copy.deepcopy(snapshot)
            malformed["tables"][0]["indexes"] = [copy.deepcopy(index)]
            duplicate = {
                "ordinal": 1,
                "ordinal_source": "Index.Fields zero-based position after Refresh and the all-fields filter",
                "name": "Payload",
                "name_utf16_code_units": [ord(char) for char in "Payload"],
                "name_windows_1252_hex": "Payload".encode("cp1252").hex(),
                "name_utf8_hex": "Payload".encode("utf-8").hex(),
                "descending": False,
            }
            duplicate[duplicate_key] = index["fields"][0][duplicate_key]
            malformed["tables"][0]["indexes"][0]["fields"].append(duplicate)
            with self.assertRaises(AssertionError):
                validate_snapshot_uniqueness(malformed)

    def test_schema_snapshot_is_required_at_all_75_replica_checkpoints(self) -> None:
        contract = self.plan["artifacts"]["dao_schema_snapshot_inventory_contract"]
        expected = []
        for replica in (1, 2, 3):
            for ordinal, checkpoint in enumerate(CHECKPOINTS):
                path = f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"
                expected.append((replica, ordinal, checkpoint, path))
        self.assertEqual(len(expected), 3 * 25)
        self.assertEqual(len(set(expected)), 75)
        self.assertEqual(contract["required_count"], 75)
        observation_refs = {(replica, ordinal, checkpoint): path for replica, ordinal, checkpoint, path in expected}
        manifest_entries = {path: {"role": "dao_schema_snapshot", "sha256": ZERO_SHA256, "size_bytes": 1} for *_, path in expected}
        for replica, ordinal, checkpoint, path in expected:
            self.assertEqual(observation_refs[(replica, ordinal, checkpoint)], path)
            self.assertEqual(manifest_entries[path]["role"], "dao_schema_snapshot")
        observation = json.loads(OBSERVATION_SCHEMA.read_bytes())
        self.assertIn("dao_schema_snapshot", observation["$defs"]["checkpoint"]["required"])
        manifest = json.loads(BUNDLE_SCHEMA.read_bytes())
        self.assertIn("dao_schema_snapshot", manifest["$defs"]["file"]["properties"]["role"]["enum"])

    def test_growth_baselines_row_cap_and_snapshot_cross_binding_are_exact(self) -> None:
        schema = json.loads(OBSERVATION_SCHEMA.read_bytes())
        self.assertEqual(schema["properties"]["inserted_rows_total"]["maximum"], 200000)
        for checkpoint in CHECKPOINTS:
            if checkpoint.startswith("T3_ABS_"):
                validate_growth_baseline(checkpoint, None)
            elif "_REL_" in checkpoint:
                validate_growth_baseline(checkpoint, 29)
        with self.assertRaises(AssertionError):
            validate_growth_baseline("T3_ABS_04096", 29)
        with self.assertRaises(AssertionError):
            validate_growth_baseline("T1_REL_0064", None)
        cross_binding = self.plan["page_capture"]["snapshot_cross_binding_rule"]
        for field in ("experiment", "plan", "revision", "commit", "campaign", "environment", "provider", "replica", "checkpoint id", "ordinal"):
            self.assertIn(field, cross_binding)
        self.assertIn("database_sha256_before_read == database_sha256_after_read == page_index.database_sha256", cross_binding)

    def test_locator_pair_and_work_bound_arithmetic_are_recomputed(self) -> None:
        bounds = self.plan["bounds"]
        grammar = self.plan["candidate_grammars"]
        checkpoints = self.plan["checkpoint_design"]["count"]
        qualified_pages = bounds["max_qualified_pages_per_submodel"]
        operation_instances = 7
        complete_row_bytes = bounds["page_size"] - 10 - 2
        occurrence_ceiling = 5 * (complete_row_bytes // 8) + 2 * (complete_row_bytes // 7)
        h4_inner_grammar = 16 * 3 * 3 * 2 * 16 * 3 * 6 * 2
        self.assertEqual(complete_row_bytes, 2036)
        self.assertEqual(occurrence_ceiling, 1850)
        self.assertEqual(h4_inner_grammar, 165888)
        one_layout = sum(range(1, 2042))
        self.assertEqual(one_layout, 2083861)
        self.assertEqual(2 * one_layout, bounds["max_locator_pairs_per_tdef_page"])
        self.assertEqual(16 * 2 * one_layout, bounds["max_locator_pairs"])
        expected_terms = {
            "tdef_lifecycle_signatures": qualified_pages * checkpoints * len(grammar["h1"]["tdef_lifecycle_signatures"]),
            "raw_locator_windows": qualified_pages * 4090,
            "raw_locator_pairs": qualified_pages * bounds["max_locator_pairs_per_tdef_page"],
            "h1_target_validity_checks": qualified_pages * len(grammar["h1"]["locator_layouts"]) * len(grammar["h1"]["table_record_signature"]["locator_holes"]) * checkpoints,
            "valid_path_row_directory_entries": qualified_pages * checkpoints * 679,
            "type_1_slots": qualified_pages * checkpoints * 2 * 508,
            "type_0_and_tag_05_bitmap_bits": qualified_pages * checkpoints * (16248 + 16352),
            "role_transition_evaluations": len(grammar["h2"]["row_masks"]) * len(grammar["h2"]["type_0_polarities"]) * len(grammar["h2"]["locator_role_assignments"]) * 5 * len(self.plan["tables"]["logical_roles"]) * checkpoints,
            "base_formula_evaluations": len(grammar["h3"]["base_formulas"]) * qualified_pages * checkpoints,
            "catalog_root_signatures": qualified_pages * checkpoints * len(self.plan["replicas"]["derivation"]) * len(grammar["h4"]["catalog_root_selection_signatures"]),
            "catalog_raw_rows": operation_instances * qualified_pages * 679,
            "encoding_union_anchor_bytes": 9 * complete_row_bytes,
            "h4_name_length_structural_tuples": occurrence_ceiling * h4_inner_grammar,
            "encoding_length_equivalence_candidates": operation_instances * len(grammar["h4"]["name_length_equivalence_classes"]),
            "candidate_serializations": bounds["max_candidate_models"],
        }
        terms = self.plan["work_model"]["terms"]
        self.assertEqual({key: value["units"] for key, value in terms.items()}, expected_terms)
        terminal_maximum = sum(expected_terms.values())
        self.assertEqual(expected_terms["encoding_union_anchor_bytes"], 18324)
        self.assertEqual(expected_terms["h4_name_length_structural_tuples"], 306892800)
        self.assertEqual(terminal_maximum, 387467081)
        terminal_paths = self.plan["work_model"]["terminal_path_maxima"]
        all_terms = {key: value["units"] for key, value in terms.items()}
        all_terms.update(
            {key: value["units"] for key, value in terminal_paths["alternative_terms"].items()}
        )
        recomputed_paths = {
            name: sum(all_terms[term] for term in term_names)
            for name, term_names in terminal_paths["term_table"].items()
        }
        self.assertEqual(recomputed_paths, terminal_paths["computed_units"])
        self.assertEqual(recomputed_paths["h4_latest_derivation_terminal"], terminal_maximum)
        self.assertLessEqual(terminal_maximum, bounds["max_analysis_work_units"])
        self.assertEqual(bounds["max_analysis_work_units"], 600000000)
        validate_analysis_work_bound(600000000, bounds["max_analysis_work_units"])
        with self.assertRaises(AssertionError):
            validate_analysis_work_bound(600000001, bounds["max_analysis_work_units"])
        self.assertEqual(bounds["max_retained_page_store_bytes"], 65536 * 2048)
        self.assertEqual(4096 * 4096 + 4097, bounds["max_canonical_candidates_array_bytes"])
        consumers = self.plan["work_model"]["logical_read_consumers"]
        read_total = sum(
            consumers[name]["bytes_per_replica"]
            for name in ("producer", "analyzer", "independent_validator")
        )
        self.assertEqual(read_total, consumers["total_bytes_per_replica"])
        self.assertEqual(read_total, 1317011456)
        self.assertLessEqual(read_total, bounds["max_logical_checkpoint_read_bytes_per_replica"])
        independent = json.loads(INDEPENDENT_SCHEMA.read_bytes())
        self.assertIn("logical_read_bytes_by_replica", independent["required"])
        self.assertEqual(independent["properties"]["tamper_results"]["minItems"], 9)
        self.assertEqual(self.plan["work_model"]["bound_classification"]["max_analysis_work_units"], "attainable_exact")

    def test_a3_page_23_raw_window_and_pair_charge_is_recomputed_when_available(self) -> None:
        expected = self.plan["candidate_grammars"]["h1"]["a3_page_23_recomputed_work"]
        root = Path(
            self.plan["preregistration"]["origin_disclosure"]["a3_calibration_bundle"]["local_read_only_path"]
        ) / "jet3-a3-bundle"
        if not root.exists():
            self.skipTest("read-only retained A3 calibration bundle is not mounted")
        pages = []
        indexes = []
        for path in sorted((root / "page-indexes" / "replica-01").glob("*.json")):
            index = json.loads(path.read_bytes())
            indexes.append(index)
            digest = index["ordered_page_sha256"][23]
            page = (root / "page-store" / f"{digest}.page").read_bytes()
            self.assertEqual(hashlib.sha256(page).hexdigest(), digest)
            pages.append(page)
        self.assertEqual(len(pages), 25)
        preserved: list[list[int]] = []
        for layout in ("page_row", "row_page"):
            offsets = []
            for offset in range(2045):
                for page in pages:
                    raw = page[offset : offset + 4]
                    number = int.from_bytes(raw[:3], "little") if layout == "page_row" else int.from_bytes(raw[1:], "little")
                    if number > 20479:
                        break
                else:
                    offsets.append(offset)
            preserved.append(offsets)
        pair_counts = [
            sum(1 for i, a in enumerate(offsets) for b in offsets[i + 1 :] if b - a >= 4)
            for offsets in preserved
        ]
        self.assertEqual([len(values) for values in preserved], [1872, 1872])
        self.assertEqual(pair_counts, [1745696, 1745696])
        self.assertEqual(4090 + sum(pair_counts), expected["raw_interval_and_pair_charge"])

        value = bytes.fromhex(
            self.plan["candidate_grammars"]["h1"]["table_record_signature"]["value_hex"]
        )
        mask = bytes.fromhex(
            self.plan["candidate_grammars"]["h1"]["table_record_signature"]["mask_hex"]
        )
        self.assertTrue(
            all(
                all((actual & keep) == (wanted & keep) for actual, wanted, keep in zip(page[:92], value, mask, strict=True))
                for page in pages
            )
        )

        def decode(page: bytes, offset: int, layout: str) -> tuple[int, int]:
            raw = page[offset : offset + 4]
            if layout == "page_row":
                return int.from_bytes(raw[:3], "little"), raw[3]
            return int.from_bytes(raw[1:], "little"), raw[0]

        def target_valid(index: dict[str, Any], target: tuple[int, int]) -> bool:
            page_number, row = target
            digests = index["ordered_page_sha256"]
            if page_number >= len(digests):
                return False
            digest = digests[page_number]
            page = (root / "page-store" / f"{digest}.page").read_bytes()
            self.assertEqual(hashlib.sha256(page).hexdigest(), digest)
            return page[0] == 1 and row < int.from_bytes(page[8:10], "little")

        valid_checkpoint_counts = []
        for layout in ("page_row", "row_page"):
            valid = 0
            for page, index in zip(pages, indexes, strict=True):
                targets = [decode(page, offset, layout) for offset in (35, 39)]
                if len(set(targets)) == 2 and all(target_valid(index, target) for target in targets):
                    valid += 1
            valid_checkpoint_counts.append(valid)
        self.assertEqual(valid_checkpoint_counts, [7, 25])
        self.assertEqual(
            [expected["target_valid_pairs_page_row"], expected["target_valid_pairs_row_page"]],
            [0, 1],
        )

    def test_work_total_and_layer_candidate_discriminator_are_semantic(self) -> None:
        report = build_report(self.plan)
        foreign = copy.deepcopy(report)
        foreign["layers"]["h1_tdef_to_map_row"]["candidates"] = [candidate("h4_catalog_root")]
        foreign["layers"]["h1_tdef_to_map_row"]["derivation_survivor_count"] = 1
        foreign["layers"]["h1_tdef_to_map_row"]["canonical_candidates_sha256"] = canonical_sha256(
            foreign["layers"]["h1_tdef_to_map_row"]["candidates"]
        )
        with self.assertRaises(ValidationError):
            validate_schema_value(foreign, self.analysis_schema, self.analysis_schema, "$")
        charges = {key: value["units"] for key, value in self.plan["work_model"]["terms"].items()}
        serialized_total = sum(charges.values())
        self.assertNotEqual(serialized_total, self.plan["bounds"]["max_analysis_work_units"])
        serialized = {**charges, "total_work_units": serialized_total}
        validate_work_charges(serialized)
        serialized["total_work_units"] += 1
        with self.assertRaises(AssertionError):
            validate_work_charges(serialized)

    def test_r5_timeout_and_revision_binding_are_complete(self) -> None:
        runtime = self.plan["runtime_design"]
        self.assertEqual(runtime["estimated_complete_wall_clock_seconds"], 1725)
        timing = runtime["campaign_headroom"]
        for phrase in ("run_started_at", "floor(created_utc - campaign_started_utc)", "Accept 2700", "reject 2701", "do not create a schema-valid bundle manifest", "recompute"):
            self.assertIn(phrase, timing)
        manifest = json.loads(BUNDLE_SCHEMA.read_bytes())
        self.assertEqual(manifest["properties"]["campaign_elapsed_seconds"]["maximum"], 2700)
        start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        created = start + dt.timedelta(seconds=2700, microseconds=999999)
        self.assertEqual(int((created - start).total_seconds()), 2700)
        self.assertGreater(int(((created + dt.timedelta(seconds=1)) - start).total_seconds()), 2700)
        dry = json.loads(DRY_RUN_SCHEMA.read_bytes())
        plan_index = dry["required"].index("plan_sha256")
        self.assertEqual(dry["required"][plan_index + 1], "revision_plan_sha256")
        self.assertIn("revision_plan_sha256", dry["properties"])
        transcript_binding = dry["properties"]["reachability_transcript"]
        self.assertEqual(
            transcript_binding["properties"]["path"]["const"],
            self.plan["artifacts"]["reachability_transcript"],
        )
        transcript = json.loads(REACHABILITY_TRANSCRIPT_SCHEMA.read_bytes())
        entries = transcript["properties"]["fixture_entries"]
        self.assertEqual((entries["minItems"], entries["maxItems"]), (40, 40))
        registry_ids = [
            row["predicate_id"]
            for row in self.plan["predicate_registry"]["predicate_contracts"]
        ]
        self.assertEqual(transcript["properties"]["registry_order"]["const"], registry_ids)
        self.assertIn("reachability_transcript_binding", self.plan["analyzer_dry_run_contract"])
        self.assertEqual(
            self.plan["predicate_registry"]["fixture_registry_status"],
            "claimed_reachable; execution_required_before_dispatch",
        )
        binding = self.plan["implementation_rebinding"]["revision_binding_rule"]
        self.assertEqual(binding["style"], "R5-V01")
        self.assertIn("both equal", binding["base_rule"])

    def test_calibration_receipt_bytes_and_decoder_arithmetic_are_recomputed(self) -> None:
        receipt = json.loads(CALIBRATION.read_bytes())
        locator = receipt["locator_example"]
        first, second, overlapping = locator["slices"]
        for item in (first, second):
            raw = bytes.fromhex(item["hex"])
            self.assertEqual(raw[0], item["u8_row_then_u24le_page"]["row"])
            self.assertEqual(int.from_bytes(raw[1:4], "little"), item["u8_row_then_u24le_page"]["page"])
        self.assertLess(overlapping["start"], first["end"])
        self.assertEqual([row["raw_directory_u16le"] & 0x1FFF for row in receipt["moving_row_examples"]], [1915, 1911, 1895, 1847, 1843])
        by_page_slot = {(row["page_number"], row["slot"]): row for row in receipt["map_prefix_examples"]}
        for row in receipt["map_prefix_examples"]:
            raw = bytes.fromhex(row["hex"])
            self.assertEqual(raw[0], row["map_type"])
            if row["map_type"] == 1:
                decoded = [
                    int.from_bytes(raw[offset : offset + 4], "little")
                    for offset in range(1, 1 + 4 * len(row["u32le_slots"]), 4)
                ]
                self.assertEqual(decoded, row["u32le_slots"])
            else:
                self.assertEqual(int.from_bytes(raw[1:5], "little"), row.get("base_u32le", 0))
        self.assertEqual(by_page_slot[(26, 0)]["u32le_slots"], [1574, 16353])
        boundary = receipt["polarity_boundary_example"]
        self.assertEqual(boundary["reported_first_violating_page"], 1021)
        for side in (boundary["left"], boundary["right"]):
            self.assertEqual(
                (side["row_end"] - side["row_start"] - 5) * 8,
                side["bitmap_capacity_bits"],
            )
        left_byte = int(boundary["left"]["physical_byte_hex"], 16)
        right_byte = int(boundary["right"]["physical_byte_hex"], 16)
        for page in range(1021, 1024):
            self.assertEqual((left_byte >> (page % 8)) & 1, 1)
            self.assertEqual((right_byte >> (page % 8)) & 1, 0)
        self.assertEqual(
            [item["page_number"] for item in receipt["tag_05_page_examples"]],
            [14848, 16352, 16353],
        )
        for item in receipt["tag_05_page_examples"]:
            self.assertEqual(bytes.fromhex(item["header_hex"]), b"\x05\x01\x00\x00")
            self.assertEqual(len(bytes.fromhex(item["first_bitmap_bytes_hex"])), 12)
        arithmetic = receipt["tag_05_bitmap_arithmetic"]
        self.assertEqual((arithmetic["page_size"] - arithmetic["header_bytes"]) * arithmetic["bits_per_byte"], arithmetic["bitmap_bits"])
        self.assertEqual(arithmetic["bitmap_bits"], 16352)

    def test_a3_only_fields_are_absent_and_claims_are_fail_closed(self) -> None:
        forbidden = {"polarity_cross_check", "globalRecordModel", "conversionModel", "baseModel", "tdefModel", "inline_boundary"}
        for path in (ANALYSIS_SCHEMA, DERIVATION_SCHEMA):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(name, text)
        expected_false = {
            "support_matrix_advancement", "dao_differential_verification",
            "dao_exposed_physical_oracle", "exact_allocation_set_equality",
            "general_jet3_or_jet4_behavior", "general_provider_or_locale_behavior",
            "physical_column_definition_layout", "physical_index_definition_or_node_layout",
            "row_value_layout", "relationship_layout", "memo_ole_or_long_value_layout",
            "writer_or_update_behavior", "free_space_preference", "preservation_behavior",
        }
        self.assertTrue(expected_false.issubset(self.plan["claims"]))
        self.assertTrue(self.plan["claims"]["descriptive_provider_observation_only"])
        self.assertTrue(all(value is False for key, value in self.plan["claims"].items() if key != "descriptive_provider_observation_only"))
        analysis_claims = set(self.analysis_schema["$defs"]["claims"]["required"])
        plan_claims = set(self.plan_schema["$defs"]["claims"]["required"])
        self.assertEqual(analysis_claims, set(self.plan["claims"]))
        self.assertEqual(plan_claims, set(self.plan["claims"]))


if __name__ == "__main__":
    unittest.main()
