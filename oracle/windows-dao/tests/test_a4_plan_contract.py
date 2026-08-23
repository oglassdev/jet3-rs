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
EVIDENCE_SCHEMA = EXPERIMENT / "h4-occurrence-evidence.schema.json"
BUNDLE_SCHEMA = EXPERIMENT / "bundle-manifest.schema.json"
BRIEF = EXPERIMENT / "design-inputs" / "a4-scope-approved.md"
CALIBRATION = EXPERIMENT / "design-inputs" / "a3-calibration-receipt.json"
README = EXPERIMENT / "README.md"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"

PLAN_SHA256 = "be6cecc23bad7bf25e71543023da074edf944c8f786bcd7703ef995e53708dc9"
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
H4_SLOTS = ["root_result", "structural_result", "encoding_result"]
RESULT_SLOTS = [*LAYERS[:3], *H4_SLOTS]
FINAL_STAGES = {
    "h1_tdef_to_map_row": "h1_locator_pair",
    "h2_row_identity_map_role": "h2_final_role",
    "h3_indirect_traversal": "h3_final_base_formula",
    "root_result": "h4_catalog_root",
    "structural_result": "h4_structural_field",
    "encoding_result": "h4_final_encoded_field",
}
STAGE_SLOTS = {
    "h1_tdef": "h1_tdef_to_map_row",
    "h1_target_valid_layout": "h1_tdef_to_map_row",
    "h1_locator_pair": "h1_tdef_to_map_row",
    "h2_final_role": "h2_row_identity_map_role",
    "h3_conversion": "h3_indirect_traversal",
    "h3_final_base_formula": "h3_indirect_traversal",
    "h4_catalog_root": "root_result",
    "h4_operation_record": "structural_result",
    "h4_structural_field": "structural_result",
    "h4_final_encoded_field": "encoding_result",
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
OPERATION_OCCURRENCE_MAX = {
    operation: 290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
    for operation in OPERATION_IDS
}
OPERATION_BITMAP_HEX = {
    operation: 74 if OPERATION_OCCURRENCE_MAX[operation] == 290 else 64
    for operation in OPERATION_IDS
}
LIFECYCLE_RANGES = {
    "T1-v1": {"start": "T1_CREATE_ID", "end": "T4_IDLE_R"},
    "T2-v1": {"start": "T2_CREATE", "end": "T2_CREATE"},
    "T2-v2": {"start": "T2_RECREATE", "end": "T4_IDLE_R"},
    "T3-v1": {"start": "T3_CREATE", "end": "T4_IDLE_R"},
    "T4-v1": {"start": "T4_CREATE", "end": "T4_IDLE_R"},
}
EQUIVALENCE_CLASSES = [
    "cp1252_single_byte_per_scalar",
    "utf8_encoded_byte_count",
    "utf8_unicode_scalar_or_code_unit_count",
]
EVIDENCE_PATH = "analysis/h4-occurrence-evidence.json"
ADVERSARIAL_CASES = {
    "multiple_count_2": "accept",
    "multiple_count_3": "accept",
    "multiple_count_4": "accept",
    "encoding_count_0": "accept",
    "encoding_count_2": "accept",
    "unregistered_candidate_id": "reject",
    "malformed_page": "reject",
    "earlier_predicate_invalidated": "reject",
    "resource_one_over": "reject",
    "work_counter_comparator_equality": "accept",
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


def bitmap_hex(operation: str, indexes: list[int]) -> str:
    width = OPERATION_BITMAP_HEX[operation]
    raw = bytearray(width // 2)
    for index in indexes:
        raw[index // 8] |= 1 << (index % 8)
    return raw.hex()


def bitmap_indexes(operation: str, value: str) -> list[int]:
    raw = bytes.fromhex(value)
    return [
        index
        for index in range(len(raw) * 8)
        if raw[index // 8] >> (index % 8) & 1
    ]


def h1_bindings(
    include_targets: bool, binding_variant: int = 0, replicas: tuple[int, ...] = (1, 2)
) -> list[dict[str, Any]]:
    bindings = []
    for replica in replicas:
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


def occurrence_evidence(occurrences_per_operation: int = 2) -> dict[str, Any]:
    bindings = []
    for index, operation in enumerate(OPERATION_IDS):
        if operation == "T1_ADD_TEXT":
            matched = "5061796c6f6164"
        elif operation == "T1_ADD_INDEX":
            matched = "413449585f4944"
        else:
            matched = "41345441425f4131"
        bindings.append(
            {
                "operation_id": operation,
                "canonical_record_locator": {
                    "page": 100 + index,
                    "row": 0,
                    "row_start": 12,
                    "row_end": 100 + index,
                },
                "occurrences": [
                    {
                        "occurrence_index": occurrence,
                        "name_start": 20 + index * 11 + occurrence * 40,
                        "matched_registered_pattern_id": f"{operation}_CP1252",
                        "matched_bytes_hex": matched,
                    }
                    for occurrence in range(occurrences_per_operation)
                ],
            }
        )
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_h4_occurrence_evidence",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": ZERO_SHA256,
        "revision_plan_sha256": ZERO_SHA256,
        "campaign_id": "synthetic",
        "root_candidate_id": ZERO_SHA256,
        "operation_bindings": bindings,
    }


EVIDENCE_DOCUMENT = occurrence_evidence()
EVIDENCE_BYTES = canonical_bytes(EVIDENCE_DOCUMENT)
EVIDENCE_SHA256 = hashlib.sha256(EVIDENCE_BYTES).hexdigest()
EVIDENCE_REFERENCE = {
    "path": EVIDENCE_PATH,
    "sha256": EVIDENCE_SHA256,
    "size_bytes": len(EVIDENCE_BYTES),
}


def structural_bindings(compatible: tuple[int, ...] = (0, 1)) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": operation,
            "compatible_occurrence_count": len(compatible),
            "compatible_occurrence_bitmap_hex": bitmap_hex(operation, list(compatible)),
        }
        for operation in OPERATION_IDS
    ]


def finish_candidate(
    stage: str, model: dict[str, Any], instance_bindings: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    value = {"model_type": stage, "model": model}
    result = {**value}
    if instance_bindings is not None:
        result["instance_bindings"] = instance_bindings
        result["canonical_model_id"] = canonical_sha256(value)
        identity: dict[str, Any] = {**value, "instance_bindings": instance_bindings}
    else:
        identity = value
    result["canonical_candidate_id"] = canonical_sha256(identity)
    return result


def candidate(
    stage: str,
    serial: int = 1,
    binding_variant: int = 0,
    replicas: tuple[int, ...] = (1, 2),
    operation: str | None = None,
    structural_id: str | None = None,
) -> dict[str, Any]:
    instance_bindings: list[dict[str, Any]] | None = None
    if stage == "h1_tdef":
        model: dict[str, Any] = {"tdef_lifecycle_signature": "new_tag_02_at_role_create"}
        instance_bindings = h1_bindings(False, binding_variant, replicas)
    elif stage == "h1_target_valid_layout":
        model = {
            "layout": "u8_row_then_u24le_page",
            "table_signature_id": "a3_page23_masked_record_0_92",
        }
        instance_bindings = h1_bindings(True, binding_variant, replicas)
    elif stage == "h1_locator_pair":
        model = {
            "layout": "u8_row_then_u24le_page" if serial % 2 else "u24le_page_then_u8_row",
            "table_signature_id": "a3_page23_masked_record_0_92",
            "locator_offsets": [35 + serial - 1, 39 + serial - 1],
        }
        instance_bindings = h1_bindings(True, binding_variant, replicas)
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
        model = {
            "root_candidate_id": ZERO_SHA256,
            "operation_id": operation or OPERATION_IDS[(serial - 1) % len(OPERATION_IDS)],
            "canonical_record_locator": {
                "page": 100 + serial,
                "row": 0,
                "row_start": 12,
                "row_end": 100,
            },
        }
    elif stage == "h4_structural_field":
        model = {
            "occurrence_evidence_sha256": EVIDENCE_SHA256,
            "kind_start_delta": min(serial, 16),
            "kind_width": 1,
            "identifier_width": 4,
            "endianness": "little",
            "name_length_start_delta": 1,
            "name_length_width": 1,
            "kind_mapping": {"table": 1, "field": 2, "index": 3},
            "identifier_lifecycle": (
                "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2"
                if serial % 2
                else "stable_for_same_physical_name_including_t2_v1_v2"
            ),
            "value_equivalent_tuple_count": 1 if serial % 2 else 2,
            "compatible_occurrences_by_operation": structural_bindings(),
        }
    elif stage == "h4_final_encoded_field":
        model = {
            "structural_candidate_id": structural_id
            or candidate("h4_structural_field")["canonical_candidate_id"],
            "encoding_length_equivalence_class": EQUIVALENCE_CLASSES[(serial - 1) % 3],
            "selected_operation_occurrences": [
                {"operation_id": operation_id, "occurrence_index": (serial - 1) % 2}
                for operation_id in OPERATION_IDS
            ],
        }
    else:
        raise AssertionError(f"unknown candidate stage {stage}")
    return finish_candidate(stage, model, instance_bindings)


def maximal_candidate(stage: str) -> dict[str, Any]:
    """The largest registered shape of a candidate stage, for byte-bound proofs."""
    page, row = 20479, 255
    bindings = [
        {
            "replica": replica,
            "logical_role": lifecycle.split("-")[0],
            "lifecycle_instance": lifecycle,
            "tdef_page": page,
            "locator_targets": [{"page": page, "row": row}, {"page": page - 1, "row": row}],
            "applicable_checkpoint_range": copy.deepcopy(LIFECYCLE_RANGES[lifecycle]),
        }
        for replica in (1, 2)
        for lifecycle in LIFECYCLE_RANGES
    ]
    if stage == "h1_tdef":
        for binding in bindings:
            del binding["locator_targets"]
        return finish_candidate(stage, {"tdef_lifecycle_signature": "preexisting_tag_02_hash_transition"}, bindings)
    if stage == "h1_target_valid_layout":
        return finish_candidate(stage, {"layout": "u24le_page_then_u8_row", "table_signature_id": "a3_page23_masked_record_0_92"}, bindings)
    if stage == "h1_locator_pair":
        return finish_candidate(stage, {"layout": "u24le_page_then_u8_row", "table_signature_id": "a3_page23_masked_record_0_92", "locator_offsets": [2040, 2044]}, bindings)
    if stage == "h4_structural_field":
        model = candidate(stage)["model"]
        model["kind_mapping"] = {"table": 4294967295, "field": 4294967294, "index": 4294967293}
        model["value_equivalent_tuple_count"] = 165888
        model["compatible_occurrences_by_operation"] = [
            {
                "operation_id": operation,
                "compatible_occurrence_count": OPERATION_OCCURRENCE_MAX[operation],
                "compatible_occurrence_bitmap_hex": bitmap_hex(operation, list(range(OPERATION_OCCURRENCE_MAX[operation]))),
            }
            for operation in OPERATION_IDS
        ]
        return finish_candidate(stage, model)
    if stage == "h4_final_encoded_field":
        model = candidate(stage)["model"]
        model["encoding_length_equivalence_class"] = "utf8_unicode_scalar_or_code_unit_count"
        model["selected_operation_occurrences"] = [
            {"operation_id": operation, "occurrence_index": OPERATION_OCCURRENCE_MAX[operation] - 1}
            for operation in OPERATION_IDS
        ]
        return finish_candidate(stage, model)
    if stage == "h4_operation_record":
        return finish_candidate(stage, {"root_candidate_id": "f" * 64, "operation_id": "T1_CREATE_ID", "canonical_record_locator": {"page": page, "row": row, "row_start": 2047, "row_end": 2048}})
    return candidate(stage, 2)


def maximal_occurrence_evidence() -> dict[str, Any]:
    document = occurrence_evidence(1)
    for binding in document["operation_bindings"]:
        operation = binding["operation_id"]
        binding["canonical_record_locator"] = {"page": 20479, "row": 255, "row_start": 2047, "row_end": 2048}
        binding["occurrences"] = [
            {
                "occurrence_index": index,
                "name_start": 2035 - index % 2,
                "matched_registered_pattern_id": f"{operation}_CP1252",
                "matched_bytes_hex": "c3" * 9 if operation in ("T1_CREATE_ID", "T2_CREATE") else "c3" * 7,
            }
            for index in range(OPERATION_OCCURRENCE_MAX[operation])
        ]
    return document


def invalid_observation(predicate_id: str, input_model_id: str) -> dict[str, Any]:
    common = {"replica": 1, "checkpoint_id": "T1_REL_0512", "page": 24}
    if predicate_id == "A4-H2-ROW-DIRECTORY-INVALID":
        kind, observation = "row_directory", {
            **common, "row_count": 2, "slot": 1, "raw_entry_u16le": 12,
            "masked_start_8191": 12, "masked_start_4095": 12,
            "reason": "start_below_directory_end",
        }
    elif predicate_id == "A4-H2-ROW-FLAGS-INVALID":
        kind, observation = "row_flags", {
            **common, "slot": 0, "raw_entry_u16le": 0x8000 | 1915,
            "deleted_flag_0x8000": True, "overflow_flag_0x4000": False,
        }
    elif predicate_id == "A4-H2-MAP-TAG-UNSUPPORTED":
        kind, observation = "map_tag", {
            **common, "slot": 0, "row_start": 1915, "row_end": 2048, "tag_byte": 2,
            "reason": "unsupported_tag",
        }
    elif predicate_id == "A4-H3-REFERENCE-INVALID":
        kind, observation = "reference", {
            **common, "slot_ordinal": 0, "referenced_page": 1574,
            "observed_tag_byte": 1, "reason": "not_tag_05",
        }
    elif predicate_id == "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED":
        kind, observation = "schema_delta_outside", {
            "replica": 1, "operation_id": "T3_CREATE", "checkpoint_before": "T2_RECREATE",
            "checkpoint_after": "T3_CREATE", "page": 4097,
            "page_sha256_before": None, "page_sha256_after": "a" * 64,
        }
    else:
        raise AssertionError(f"no invalid observation for {predicate_id}")
    return {"kind": kind, "input_model_id": input_model_id, "observation": observation}


def empty_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "predicate_measured_survivor_count": 0,
        "derivation_survivor_count": 0,
        "terminal_predicate_id": None,
        "terminal_payload_kind": None,
        "terminal_candidate_stage": None,
        "candidates": [],
        "terminal_evidence": None,
        "canonical_candidates_sha256": canonical_sha256([]),
    }


def sorted_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda item: item["canonical_candidate_id"])


def model_result(stage: str, structural_id: str | None = None) -> dict[str, Any]:
    result = empty_result("model")
    result["candidates"] = [candidate(stage, structural_id=structural_id)]
    result["predicate_measured_survivor_count"] = 1
    result["derivation_survivor_count"] = 1
    result["canonical_candidates_sha256"] = canonical_sha256(result["candidates"])
    return result


def terminal_result(
    contract: dict[str, Any],
    count: int,
    slot: str,
    upstream_model_id: str,
    structural_id: str | None = None,
) -> dict[str, Any]:
    payload = contract["terminal_payload_schema"]
    stage = contract["candidate_stage"]
    if payload == "replica_pair" and slot == "structural_result":
        stage = "h4_structural_field"
    predicate_id = contract["predicate_id"]
    result = empty_result("no_outcome")
    result["terminal_predicate_id"] = predicate_id
    result["terminal_payload_kind"] = payload
    result["terminal_candidate_stage"] = stage
    result["predicate_measured_survivor_count"] = count
    candidates: list[dict[str, Any]] = []
    evidence: dict[str, Any] | None = None
    if payload == "candidate_set":
        candidates = [
            candidate(stage, serial, binding_variant=serial - 1, replicas=(1,), structural_id=structural_id)
            for serial in range(1, count + 1)
        ]
    elif payload == "grouped_candidate_set":
        if predicate_id == "A4-H4-CATALOG-RECORD-NONE":
            shape = [0, 1, 1, 1, 1, 1, 1]
        else:
            shape = [count, 1, 1, 1, 1, 1, 1]
        groups = []
        serial = 1
        for operation, size in zip(OPERATION_IDS, shape, strict=True):
            members = []
            for _ in range(size):
                members.append(candidate(stage, serial, operation=operation))
                serial += 1
            candidates.extend(members)
            groups.append(
                {
                    "operation_id": operation,
                    "cardinality": size,
                    "candidate_ids": sorted(item["canonical_candidate_id"] for item in members),
                }
            )
        evidence = {"kind": "operation_groups", "groups": groups}
    elif payload == "replica_pair":
        pair_stage = stage
        entries = []
        for replica in (1, 2):
            if pair_stage == "h4_final_encoded_field":
                structural = candidate("h4_structural_field", replica, replicas=(replica,))
                item = candidate(pair_stage, replica, structural_id=structural["canonical_candidate_id"])
            else:
                item = candidate(pair_stage, replica, binding_variant=replica - 1, replicas=(replica,))
            entries.append(
                {
                    "replica": replica,
                    "canonical_model_id": item.get("canonical_model_id"),
                    "canonical_candidate_id": item["canonical_candidate_id"],
                    "complete_candidate": item,
                }
            )
        evidence = {"kind": "replica_pair", "entries": entries}
    elif payload == "invalid_observation":
        if stage is not None:
            candidates = [candidate(stage)]
            input_model_id = candidates[0]["canonical_candidate_id"]
        else:
            input_model_id = upstream_model_id
        evidence = invalid_observation(predicate_id, input_model_id)
    else:
        raise AssertionError(f"unexpected payload {payload}")
    result["candidates"] = sorted_candidates(candidates)
    result["terminal_evidence"] = evidence
    result["canonical_candidates_sha256"] = canonical_sha256(result["candidates"])
    return result


def default_failure_count(contract: dict[str, Any]) -> int:
    rule = contract["failure_survivor_count"]
    if "exact" in rule:
        return rule["exact"]
    if "minimum" in rule:
        return rule["minimum"]
    if "total_exact" in rule:
        return rule["total_exact"]
    first_range = rule["allowed_ranges"][0]
    return first_range["exact"] if "exact" in first_range else first_range["minimum"]


def validate_failure_count(contract: dict[str, Any], measured: int) -> None:
    rule = contract["failure_survivor_count"]
    if "exact" in rule:
        valid = measured == rule["exact"]
    elif "minimum" in rule:
        valid = measured >= rule["minimum"]
    elif "total_exact" in rule:
        valid = (
            measured == rule["total_exact"]
            and rule["per_replica_exact"] * rule["replica_count"] == rule["total_exact"]
        )
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
    contracts: list[dict[str, Any]],
    terminal_index: int | None,
    measured_terminal_count: int | None = None,
) -> dict[str, Any]:
    terminal = contracts[terminal_index] if terminal_index is not None else None
    terminal_count = measured_terminal_count
    if terminal_count is None and terminal is not None:
        terminal_count = default_failure_count(terminal)
    campaign_failure = terminal is not None and terminal["scope"] == "campaign"
    holdout_failure = terminal is not None and "HOLDOUT" in terminal["predicate_id"]
    terminal_slots = [] if terminal is None or campaign_failure or holdout_failure else terminal["result_slots"]
    first_terminal_slot = RESULT_SLOTS.index(terminal_slots[0]) if terminal_slots else len(RESULT_SLOTS)

    upstream_model_id = ZERO_SHA256
    structural_id: str | None = None
    slots: dict[str, dict[str, Any]] = {}
    for position, slot in enumerate(RESULT_SLOTS):
        if campaign_failure:
            slots[slot] = empty_result("not_applicable")
        elif slot in terminal_slots:
            assert terminal is not None and terminal_count is not None
            slots[slot] = terminal_result(terminal, terminal_count, slot, upstream_model_id, structural_id)
        elif position < first_terminal_slot:
            slots[slot] = model_result(FINAL_STAGES[slot], structural_id)
            upstream_model_id = slots[slot]["candidates"][0]["canonical_candidate_id"]
            if slot == "structural_result":
                structural_id = upstream_model_id
        else:
            slots[slot] = empty_result("not_applicable")
    layers = {name: slots[name] for name in LAYERS[:3]}
    layers["h4_catalog_bootstrap"] = {slot: slots[slot] for slot in H4_SLOTS}
    return layers


def evidence_reference_for(layers: dict[str, Any]) -> dict[str, Any] | None:
    structural = layers["h4_catalog_bootstrap"]["structural_result"]
    if structural["status"] == "model" or structural["terminal_candidate_stage"] == "h4_structural_field":
        return copy.deepcopy(EVIDENCE_REFERENCE)
    if structural["terminal_payload_kind"] == "replica_pair":
        return copy.deepcopy(EVIDENCE_REFERENCE)
    return None


def validate_candidate_identity(item: dict[str, Any], replicas: tuple[int, ...]) -> None:
    identity = {"model_type": item["model_type"], "model": item["model"]}
    if item["model_type"].startswith("h1_"):
        if item["canonical_model_id"] != canonical_sha256(identity):
            raise AssertionError("canonical H1 model id mismatch")
        identity["instance_bindings"] = item["instance_bindings"]
    if item["canonical_candidate_id"] != canonical_sha256(identity):
        raise AssertionError("canonical candidate id mismatch")
    if item["model_type"] == "h2_final_role":
        model = item["model"]
        if model["owned_in_use_locator_ordinal"] == model["available_locator_ordinal"]:
            raise AssertionError("H2 locator ordinals must differ")
    if item["model_type"].startswith("h1_"):
        offsets = item["model"].get("locator_offsets")
        if offsets is not None and offsets != sorted(offsets):
            raise AssertionError("locator offsets must be ascending")
        bindings = item["instance_bindings"]
        expected_order = [
            (replica, lifecycle)
            for replica in replicas
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
        for forbidden in ("encoding_length_equivalence_class", "name_length_endianness", "operation_bindings", "name_start"):
            if forbidden in model:
                raise AssertionError(f"structural H4 candidate carries {forbidden}")
        bindings = model["compatible_occurrences_by_operation"]
        if [binding["operation_id"] for binding in bindings] != OPERATION_IDS:
            raise AssertionError("H4 operation bindings differ from frozen order")
        for binding in bindings:
            operation = binding["operation_id"]
            indexes = bitmap_indexes(operation, binding["compatible_occurrence_bitmap_hex"])
            if not indexes or len(indexes) != binding["compatible_occurrence_count"]:
                raise AssertionError("H4 compatible bitmap popcount differs from count")
            if max(indexes) >= OPERATION_OCCURRENCE_MAX[operation]:
                raise AssertionError("H4 compatible bitmap exceeds the operation occurrence maximum")
    if item["model_type"] == "h4_final_encoded_field":
        selected = item["model"]["selected_operation_occurrences"]
        if [row["operation_id"] for row in selected] != OPERATION_IDS:
            raise AssertionError("H4 selected occurrences differ from frozen order")


def validate_final_against_structural(final: dict[str, Any], structural: dict[str, Any]) -> None:
    if final["model"]["structural_candidate_id"] != structural["canonical_candidate_id"]:
        raise AssertionError("final H4 candidate references an orphan structural id")
    bitmaps = {
        binding["operation_id"]: binding["compatible_occurrence_bitmap_hex"]
        for binding in structural["model"]["compatible_occurrences_by_operation"]
    }
    for row in final["model"]["selected_operation_occurrences"]:
        if row["occurrence_index"] not in bitmap_indexes(row["operation_id"], bitmaps[row["operation_id"]]):
            raise AssertionError("selected occurrence is absent from the structural evidence")


def validate_frozen_result(value: dict[str, Any], slot: str) -> None:
    candidates = value["candidates"]
    count = value["predicate_measured_survivor_count"]
    final_count = value["derivation_survivor_count"]
    terminal = value["terminal_predicate_id"]
    payload = value["terminal_payload_kind"]
    stage = value["terminal_candidate_stage"]
    evidence = value["terminal_evidence"]
    if candidates != sorted_candidates(candidates):
        raise AssertionError("candidate order is not canonical")
    ids = [item["canonical_candidate_id"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate canonical candidate id")
    if canonical_sha256(candidates) != value["canonical_candidates_sha256"]:
        raise AssertionError("canonical candidate hash mismatch")
    expected_stage = stage or FINAL_STAGES[slot]
    if any(item["model_type"] != expected_stage for item in candidates):
        raise AssertionError("result contains a foreign candidate stage")
    if stage is not None and STAGE_SLOTS[stage] != slot:
        raise AssertionError("terminal candidate stage belongs to another result")
    if value["status"] == "model":
        replicas: tuple[int, ...] = (1, 2)
    else:
        failing = {row["replica"] for item in candidates for row in item.get("instance_bindings", [])}
        if len(failing) > 1:
            raise AssertionError("terminal candidate set mixes replicas (AMB-04)")
        replicas = tuple(failing) or (1,)
    for item in candidates:
        validate_candidate_identity(item, replicas)
    if value["status"] == "model":
        if count != 1 or final_count != 1 or len(candidates) != 1 or terminal is not None:
            raise AssertionError("model must have one candidate and no terminal")
        if payload is not None or stage is not None or evidence is not None:
            raise AssertionError("model retains terminal payload state")
        return
    if value["status"] == "not_applicable":
        if count or final_count or candidates or terminal is not None:
            raise AssertionError("not_applicable result has retained state")
        if payload is not None or stage is not None or evidence is not None:
            raise AssertionError("not_applicable result retains terminal payload state")
        return
    if terminal is None or final_count != 0 or payload is None:
        raise AssertionError("no_outcome requires a terminal and payload kind")
    if payload == "candidate_set":
        if evidence is not None or stage is None or len(candidates) != count:
            raise AssertionError("candidate_set payload must serialize exactly the counted set")
    elif payload == "grouped_candidate_set":
        if evidence is None or evidence["kind"] != "operation_groups" or stage != "h4_operation_record":
            raise AssertionError("grouped payload requires operation groups")
        groups = evidence["groups"]
        if [group["operation_id"] for group in groups] != OPERATION_IDS:
            raise AssertionError("operation groups differ from frozen order")
        by_operation: dict[str, list[str]] = {operation: [] for operation in OPERATION_IDS}
        for item in candidates:
            by_operation[item["model"]["operation_id"]].append(item["canonical_candidate_id"])
        for group in groups:
            members = sorted(by_operation[group["operation_id"]])
            if group["candidate_ids"] != members or group["cardinality"] != len(members):
                raise AssertionError("operation group membership or cardinality mismatch")
        cardinalities = [group["cardinality"] for group in groups]
        if terminal == "A4-H4-CATALOG-RECORD-NONE":
            if count != min(cardinalities) or count != 0:
                raise AssertionError("RECORD-NONE count must be the zero minimum group cardinality")
        elif count != max(cardinalities) or count < 2:
            raise AssertionError("RECORD-MULTIPLE count must be the maximum offending group cardinality")
    elif payload == "replica_pair":
        if evidence is None or evidence["kind"] != "replica_pair" or candidates or count != 2:
            raise AssertionError("replica_pair payload requires an empty union set and count 2")
        entries = evidence["entries"]
        if [entry["replica"] for entry in entries] != [1, 2]:
            raise AssertionError("replica pair is not in replica order")
        pair_stage = "h4_structural_field" if slot == "structural_result" else expected_stage
        for entry in entries:
            item = entry["complete_candidate"]
            if item["model_type"] != pair_stage:
                raise AssertionError("replica pair entry has a foreign stage")
            validate_candidate_identity(item, (entry["replica"],))
            if entry["canonical_candidate_id"] != item["canonical_candidate_id"]:
                raise AssertionError("replica pair candidate id mismatch")
            if entry["canonical_model_id"] != item.get("canonical_model_id"):
                raise AssertionError("replica pair model id mismatch")
        first, second = entries
        if pair_stage.startswith("h1_"):
            unequal = first["canonical_model_id"] != second["canonical_model_id"]
        else:
            unequal = first["canonical_candidate_id"] != second["canonical_candidate_id"]
        if not unequal and slot != "structural_result":
            raise AssertionError("replica pair models are equal")
    elif payload == "invalid_observation":
        if evidence is None or evidence["kind"] in ("replica_pair", "operation_groups"):
            raise AssertionError("invalid_observation payload requires an observation")
        if count != 1:
            raise AssertionError("invalid_observation counts exactly the one input model")
        if stage is None:
            if candidates:
                raise AssertionError("upstream-input invalid observation must not fabricate candidates")
        elif len(candidates) != 1 or candidates[0]["canonical_candidate_id"] != evidence["input_model_id"]:
            raise AssertionError("same-layer invalid observation must reference its one retained candidate")
    else:
        raise AssertionError("unknown terminal payload kind")


def validate_layer_semantics(
    layers: dict[str, Any], evidence_reference: dict[str, Any] | None
) -> None:
    upstream_ids: dict[str, str] = {}
    for slot in RESULT_SLOTS:
        result = layers[slot] if slot in layers else layers["h4_catalog_bootstrap"][slot]
        validate_frozen_result(result, slot)
        if result["status"] == "model":
            upstream_ids[slot] = result["candidates"][0]["canonical_candidate_id"]
    h2 = layers["h2_row_identity_map_role"]
    if h2["terminal_payload_kind"] == "invalid_observation":
        if h2["terminal_evidence"]["input_model_id"] != upstream_ids.get("h1_tdef_to_map_row"):
            raise AssertionError("H2 invalid observation does not reference the decisive H1 candidate")
    h4 = layers["h4_catalog_bootstrap"]
    root, structural, encoding = h4["root_result"], h4["structural_result"], h4["encoding_result"]
    if root["status"] != "model" and structural["status"] != "not_applicable":
        raise AssertionError("H4 structural result requires a decisive root")
    if structural["terminal_payload_kind"] == "invalid_observation":
        if structural["terminal_evidence"]["input_model_id"] != upstream_ids.get("root_result"):
            raise AssertionError("H4 OUTSIDE observation does not reference the decisive root")
    if structural["status"] != "model" and encoding["status"] != "not_applicable":
        if not (
            structural["terminal_payload_kind"] == "replica_pair"
            and encoding["terminal_payload_kind"] == "replica_pair"
            and structural["terminal_predicate_id"] == encoding["terminal_predicate_id"]
        ):
            raise AssertionError("H4 encoding result requires exactly one structural model")
    structural_began = structural["status"] == "model" or structural["terminal_candidate_stage"] == "h4_structural_field" or structural["terminal_payload_kind"] == "replica_pair"
    if structural_began != (evidence_reference is not None):
        raise AssertionError("h4_occurrence_evidence reference presence differs from structural enumeration")
    structural_candidates = list(structural["candidates"])
    if structural["terminal_payload_kind"] == "replica_pair":
        structural_candidates = [entry["complete_candidate"] for entry in structural["terminal_evidence"]["entries"]]
    for item in structural_candidates:
        if item["model_type"] != "h4_structural_field":
            continue
        if evidence_reference is None or item["model"]["occurrence_evidence_sha256"] != evidence_reference["sha256"]:
            raise AssertionError("structural candidate evidence hash differs from the frozen reference")
    if encoding["status"] == "not_applicable":
        return
    if encoding["terminal_payload_kind"] == "replica_pair":
        structural_entries = {entry["replica"]: entry["complete_candidate"] for entry in structural["terminal_evidence"]["entries"]}
        for entry in encoding["terminal_evidence"]["entries"]:
            validate_final_against_structural(entry["complete_candidate"], structural_entries[entry["replica"]])
        return
    if len(structural["candidates"]) != 1:
        raise AssertionError("final candidates exist without exactly one structural model")
    classes = [item["model"]["encoding_length_equivalence_class"] for item in encoding["candidates"]]
    if len(classes) != len(set(classes)):
        raise AssertionError("duplicate encoding equivalence class")
    for item in encoding["candidates"]:
        validate_final_against_structural(item, structural["candidates"][0])


def validate_evidence_document(document: dict[str, Any], reference: dict[str, Any], actual_bytes: bytes) -> None:
    if hashlib.sha256(actual_bytes).hexdigest() != reference["sha256"] or len(actual_bytes) != reference["size_bytes"]:
        raise AssertionError("h4_occurrence_evidence reference does not bind the retained bytes")
    if len(actual_bytes) > 524288:
        raise AssertionError("h4_occurrence_evidence exceeds 524,288 bytes")
    total = 0
    for binding in document["operation_bindings"]:
        occurrences = binding["occurrences"]
        indexes = [item["occurrence_index"] for item in occurrences]
        if indexes != list(range(len(occurrences))):
            raise AssertionError("occurrence indexes are not dense and ordered")
        if len(occurrences) > OPERATION_OCCURRENCE_MAX[binding["operation_id"]]:
            raise AssertionError("operation occurrence maximum exceeded")
        total += len(occurrences)
    if total > 1850:
        raise AssertionError("occurrence identity 1,851 exceeds the registered maximum")


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
    layers = build_layers_for_terminal(contracts, terminal_index, measured_terminal_count)
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
        "h4_occurrence_evidence": evidence_reference_for(layers),
        "layers": layers,
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
        "h4_occurrence_evidence": copy.deepcopy(report["h4_occurrence_evidence"]),
        "layers": copy.deepcopy(report["layers"]),
        "transcripts": copy.deepcopy(report["transcripts"]),
    }


def result_projection(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["status"],
        value["terminal_predicate_id"],
        value["terminal_payload_kind"],
        value["terminal_candidate_stage"],
        value["predicate_measured_survivor_count"],
        value["derivation_survivor_count"],
        len(value["candidates"]),
        None if value["terminal_evidence"] is None else value["terminal_evidence"]["kind"],
    )


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
    validate_layer_semantics(report["layers"], report["h4_occurrence_evidence"])
    terminal_count = (
        results[terminal_index]["predicate_measured_survivor_count"]
        if terminal_index is not None else None
    )
    expected_layers = build_layers_for_terminal(contracts, terminal_index, terminal_count)
    for layer in LAYERS[:3]:
        if result_projection(report["layers"][layer]) != result_projection(expected_layers[layer]):
            raise AssertionError("predicate/layer projection mismatch")
    for part in H4_SLOTS:
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


def validate_json_resource_bounds(
    candidate_count: int,
    largest_candidate_bytes: int,
    occurrence_identities: int,
    evidence_bytes: int,
    report_bytes: int,
    bounds: dict[str, int],
) -> None:
    if candidate_count > bounds["max_candidate_models"]:
        raise AssertionError("candidate 4,097 rejected before manifest creation")
    if largest_candidate_bytes > bounds["max_canonical_candidate_bytes"]:
        raise AssertionError("candidate byte 4,097 rejected before manifest creation")
    if occurrence_identities > bounds["max_h4_occurrence_identities"]:
        raise AssertionError("occurrence identity 1,851 rejected before manifest creation")
    if evidence_bytes > bounds["max_h4_occurrence_evidence_bytes"]:
        raise AssertionError("evidence byte 524,289 rejected before manifest creation")
    if report_bytes > bounds["max_json_bytes"]:
        raise AssertionError("report byte 67,108,865 rejected before manifest creation")


def build_transcript(plan: dict[str, Any]) -> dict[str, Any]:
    contracts = plan["predicate_registry"]["predicate_contracts"]
    entries = []
    for index, contract in enumerate(contracts):
        evaluated = [
            {"predicate_id": earlier["predicate_id"], "status": "pass", "actual_survivor_count": 0 if earlier["scope"] == "campaign" else 1}
            for earlier in contracts[:index]
        ]
        unreachable = contract["fixture_status"].startswith("unreachable_by_construction")
        count = 1 if unreachable else default_failure_count(contract)
        evaluated.append({"predicate_id": contract["predicate_id"], "status": "pass" if unreachable else "fail", "actual_survivor_count": count})
        evaluator = {
            "first_failure_id": None if unreachable else contract["predicate_id"],
            "measured_terminal_count": count,
            "candidate_set_sha256": canonical_sha256([contract["predicate_id"]]),
        }
        assertion = (
            {
                "enumeration_argument": contract["reachability_fixture"],
                "max_measured_count_across_sweep": 1,
                "fixtures_evaluating_predicate": [row["reachability_fixture_id"] for row in contracts[index + 1 :] if row["reachability_fixture_id"]],
            }
            if unreachable
            else None
        )
        entries.append(
            {
                "order": contract["order"],
                "predicate_id": contract["predicate_id"],
                "reachability_fixture_id": contract["reachability_fixture_id"],
                "baseline_fixture_sha256": canonical_sha256("baseline"),
                "mutation_sha256": canonical_sha256(["mutation", contract["predicate_id"]]),
                "page_index_inventory_sha256": canonical_sha256("page-index"),
                "page_blob_inventory_sha256": canonical_sha256("page-blob"),
                "enumerated_candidate_ids_by_stage": [],
                "evaluated_predicates": evaluated,
                "first_failure_id": None if unreachable else contract["predicate_id"],
                "unreachable_assertion": assertion,
                "analyzer_result": copy.deepcopy(evaluator),
                "independent_validator_result": copy.deepcopy(evaluator),
                "agreement": True,
            }
        )
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_reachability_transcript",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": ZERO_SHA256,
        "revision_plan_sha256": ZERO_SHA256,
        "harness_commit": "0" * 40,
        "independent_validator_commit": "0" * 40,
        "provenance_entry_id": "EXP-0053",
        "registry_order": [contract["predicate_id"] for contract in contracts],
        "fixture_entries": entries,
        "adversarial_case_outcomes": {
            case: {
                "expected": expected,
                "analyzer_result": expected,
                "independent_validator_result": expected,
                "agreement": True,
            }
            for case, expected in ADVERSARIAL_CASES.items()
        },
    }


def validate_transcript_semantics(transcript: dict[str, Any], plan: dict[str, Any]) -> None:
    contracts = plan["predicate_registry"]["predicate_contracts"]
    registry_ids = [contract["predicate_id"] for contract in contracts]
    fixture_ids = {contract["reachability_fixture_id"] for contract in contracts} - {None}
    if transcript["registry_order"] != registry_ids:
        raise AssertionError("transcript registry order differs")
    for index, (entry, contract) in enumerate(zip(transcript["fixture_entries"], contracts, strict=True)):
        if entry["predicate_id"] != contract["predicate_id"] or entry["order"] != index + 1:
            raise AssertionError("transcript entry is out of registry order")
        evaluated = entry["evaluated_predicates"]
        expected_prefix = registry_ids[: index + 1]
        if [row["predicate_id"] for row in evaluated] != expected_prefix:
            raise AssertionError("evaluated predicates are not the exact applicable prefix")
        unreachable = contract["fixture_status"].startswith("unreachable_by_construction")
        if (entry["unreachable_assertion"] is None) == unreachable:
            raise AssertionError("unreachable assertion presence differs from the contract")
        if [row["status"] for row in evaluated] != ["pass"] * index + ["pass" if unreachable else "fail"]:
            raise AssertionError("evaluated statuses are not pass-prefix then fail")
        if entry["first_failure_id"] != (None if unreachable else contract["predicate_id"]):
            raise AssertionError("first failure differs from the entry predicate")
        if unreachable:
            fixtures = entry["unreachable_assertion"]["fixtures_evaluating_predicate"]
            if not fixtures or any(fixture not in fixture_ids for fixture in fixtures):
                raise AssertionError("unreachable assertion names unknown fixtures")
            if evaluated[-1]["actual_survivor_count"] != 1:
                raise AssertionError("asserted-unreachable predicate must measure exactly one survivor")
        analyzer, validator = entry["analyzer_result"], entry["independent_validator_result"]
        if analyzer != validator or analyzer["first_failure_id"] != entry["first_failure_id"]:
            raise AssertionError("analyzer and validator results differ")
        if not unreachable:
            validate_failure_count(contract, analyzer["measured_terminal_count"])
        if evaluated[-1]["actual_survivor_count"] != analyzer["measured_terminal_count"]:
            raise AssertionError("terminal count differs from evaluated predicate count")
    outcomes = transcript["adversarial_case_outcomes"]
    if list(outcomes) != list(ADVERSARIAL_CASES):
        raise AssertionError("adversarial cases are not the exact registered set")
    for case, expected in ADVERSARIAL_CASES.items():
        outcome = outcomes[case]
        if outcome["expected"] != expected or outcome["analyzer_result"] != expected or outcome["independent_validator_result"] != expected:
            raise AssertionError(f"adversarial case {case} differs from its fixed expectation")


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
        self.assertNotIn("terminal_candidate_stage_by_predicate", registry)
        derived = {
            row["predicate_id"]: {
                "terminal_payload_schema": row["terminal_payload_schema"],
                "candidate_stage": row["candidate_stage"],
                "result_slots": row["result_slots"],
            }
            for row in contracts
            if row["scope"] != "campaign" and "HOLDOUT" not in row["predicate_id"]
        }
        self.assertEqual(registry["terminal_payload_by_predicate"], derived)
        self.assertEqual(len(derived), 31)
        self.assertEqual(len({item["reachability_fixture_id"] for item in contracts}), 40)
        unreachable = [item for item in contracts if item["fixture_status"].startswith("unreachable_by_construction")]
        self.assertEqual([item["predicate_id"] for item in unreachable], ["A4-H1-LOCATOR-PAIR-MULTIPLE"])
        self.assertIsNone(unreachable[0]["reachability_fixture_id"])
        for phrase in ("[35,39)", "[39,43)", "at most the single canonical pair", "enumeration"):
            self.assertIn(phrase, unreachable[0]["reachability_fixture"])
        required = {
            "predicate_id", "order", "scope", "prerequisites", "input_candidate_set",
            "counted_set_kind", "pass_iff", "fail_iff", "terminal_id",
            "failure_survivor_count", "terminal_payload_schema", "candidate_stage",
            "result_slots", "later_status", "reachability_fixture_id",
            "reachability_fixture", "fixture_status",
        }
        for contract in contracts:
            self.assertEqual(set(contract), required)
            self.assertEqual(contract["terminal_id"], contract["predicate_id"])
            self.assertEqual(contract["later_status"], "not_applicable")
            self.assertTrue(contract["pass_iff"])
            self.assertTrue(contract["fail_iff"])
            self.assertTrue(contract["reachability_fixture"])
            if contract["predicate_id"] != "A4-H1-LOCATOR-PAIR-MULTIPLE":
                self.assertEqual(
                    contract["fixture_status"],
                    "claimed_reachable; execution_required_before_dispatch",
                )
            payload = contract["terminal_payload_schema"]
            stage = contract["candidate_stage"]
            if contract["scope"] == "campaign" or "HOLDOUT" in contract["predicate_id"]:
                self.assertEqual((payload, stage), ("none", None))
                continue
            self.assertIn(payload, {"candidate_set", "grouped_candidate_set", "replica_pair", "invalid_observation"})
            if payload == "replica_pair":
                self.assertEqual(
                    contract["failure_survivor_count"],
                    {"per_replica_exact": 1, "replica_count": 2, "total_exact": 2},
                )
            if stage is None:
                self.assertEqual(payload, "invalid_observation")
            else:
                self.assertEqual(STAGE_SLOTS[stage], contract["result_slots"][-1])
        evaluation = registry["evaluation_rule"]
        for phrase in ("Evaluation has two phases", "Derivation phase", "Holdout phase", "derivation layer depends", "sole terminal", "all 36 scientific predicates", "terminal_payload_schema"):
            self.assertIn(phrase, evaluation)

    def test_abstract_terminal_reports_validate_schema_shapes(self) -> None:
        for terminal_index in [None, *range(40)]:
            with self.subTest(terminal=terminal_index):
                report = build_report(self.plan, terminal_index)
                validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
                validate_report_semantics(report, self.plan)
                frozen = build_frozen_document(report, self.plan)
                validate_schema_value(frozen, self.derivation_schema, self.derivation_schema, "$")
                self.assertEqual(frozen["layers"], report["layers"])

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
                    slot = contracts[index]["result_slots"][0]
                    layer = report["layers"].get(slot) or report["layers"]["h4_catalog_bootstrap"][slot]
                    self.assertEqual(layer["predicate_measured_survivor_count"], measured)
                    self.assertEqual(len(layer["candidates"]), measured)
        for index in multiple_indexes:
            with self.subTest(predicate=contracts[index]["predicate_id"], measured=1):
                report = build_report(self.plan, index, 1)
                with self.assertRaises(AssertionError):
                    validate_report_semantics(report, self.plan)

    def test_h4_structural_and_encoding_results_are_independently_frozen(self) -> None:
        for schema in (self.derivation_schema, self.analysis_schema):
            self.assertNotIn("h4FieldCandidate", schema["$defs"])
            self.assertNotIn("h4FieldResult", schema["$defs"])
            self.assertNotIn("h2StructuralDecoderCandidate", schema["$defs"])
            self.assertEqual(
                schema["$defs"]["h4Result"]["required"],
                ["root_result", "structural_result", "encoding_result"],
            )
            structural_model = schema["$defs"]["h4StructuralFieldCandidate"]["properties"]["model"]
            self.assertNotIn("name_length_endianness", structural_model["properties"])
            self.assertNotIn("operation_bindings", structural_model["properties"])
            self.assertIn("occurrence_evidence_sha256", structural_model["required"])
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        by_id = {row["predicate_id"]: index for index, row in enumerate(contracts)}

        def h4(report: dict[str, Any]) -> dict[str, Any]:
            return report["layers"]["h4_catalog_bootstrap"]

        def accept(report: dict[str, Any]) -> None:
            validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
            validate_report_semantics(report, self.plan)

        # structural count 2 with encoding not applicable
        multiple = build_report(self.plan, by_id["A4-H4-FIELD-MODEL-MULTIPLE"], 2)
        accept(multiple)
        self.assertEqual(h4(multiple)["structural_result"]["terminal_candidate_stage"], "h4_structural_field")
        self.assertEqual(len(h4(multiple)["structural_result"]["candidates"]), 2)
        self.assertEqual(h4(multiple)["encoding_result"], empty_result("not_applicable"))
        starts = {
            item["model"]["kind_start_delta"] for item in h4(multiple)["structural_result"]["candidates"]
        }
        self.assertEqual(len(starts), 2)

        # structural 1 plus final 0, and the same structural id plus two distinct classes
        for measured in (0, 2):
            with self.subTest(final_candidates=measured):
                ambiguous = build_report(self.plan, by_id["A4-H4-ENCODING-AMBIGUOUS"], measured)
                accept(ambiguous)
                structural = h4(ambiguous)["structural_result"]
                encoding = h4(ambiguous)["encoding_result"]
                self.assertEqual(structural["status"], "model")
                self.assertEqual(len(structural["candidates"]), 1)
                self.assertEqual(encoding["terminal_candidate_stage"], "h4_final_encoded_field")
                self.assertEqual(len(encoding["candidates"]), measured)
                structural_id = structural["candidates"][0]["canonical_candidate_id"]
                classes = []
                for item in encoding["candidates"]:
                    self.assertEqual(item["model"]["structural_candidate_id"], structural_id)
                    classes.append(item["model"]["encoding_length_equivalence_class"])
                self.assertEqual(len(set(classes)), measured)

        # decisive one-plus-one
        decisive = build_report(self.plan)
        accept(decisive)
        self.assertEqual(len(h4(decisive)["structural_result"]["candidates"]), 1)
        self.assertEqual(len(h4(decisive)["encoding_result"]["candidates"]), 1)
        validate_final_against_structural(
            h4(decisive)["encoding_result"]["candidates"][0],
            h4(decisive)["structural_result"]["candidates"][0],
        )

        def rehash(result: dict[str, Any]) -> None:
            for item in result["candidates"]:
                item["canonical_candidate_id"] = canonical_sha256(
                    {"model_type": item["model_type"], "model": item["model"]}
                )
            result["candidates"] = sorted_candidates(result["candidates"])
            result["canonical_candidates_sha256"] = canonical_sha256(result["candidates"])

        # orphan structural id
        orphan = copy.deepcopy(decisive)
        orphan_encoding = h4(orphan)["encoding_result"]
        orphan_encoding["candidates"][0]["model"]["structural_candidate_id"] = "f" * 64
        rehash(orphan_encoding)
        validate_schema_value(orphan, self.analysis_schema, self.analysis_schema, "$")
        with self.assertRaisesRegex(AssertionError, "orphan structural id"):
            validate_report_semantics(orphan, self.plan)

        # selected occurrence absent from the structural evidence
        absent = copy.deepcopy(decisive)
        absent_encoding = h4(absent)["encoding_result"]
        absent_encoding["candidates"][0]["model"]["selected_operation_occurrences"][2]["occurrence_index"] = 5
        rehash(absent_encoding)
        validate_schema_value(absent, self.analysis_schema, self.analysis_schema, "$")
        with self.assertRaisesRegex(AssertionError, "absent from the structural evidence"):
            validate_report_semantics(absent, self.plan)

        # duplicate equivalence classes
        duplicate = build_report(self.plan, by_id["A4-H4-ENCODING-AMBIGUOUS"], 2)
        duplicate_encoding = h4(duplicate)["encoding_result"]
        duplicate_encoding["candidates"][1]["model"]["encoding_length_equivalence_class"] = (
            duplicate_encoding["candidates"][0]["model"]["encoding_length_equivalence_class"]
        )
        rehash(duplicate_encoding)
        with self.assertRaisesRegex(AssertionError, "duplicate encoding equivalence class"):
            validate_report_semantics(duplicate, self.plan)

        # final candidates when structural cardinality is not exactly one
        not_unique = copy.deepcopy(multiple)
        h4(not_unique)["encoding_result"] = copy.deepcopy(h4(decisive)["encoding_result"])
        with self.assertRaises(AssertionError):
            validate_report_semantics(not_unique, self.plan)

        # structural candidate bound to a foreign evidence table
        foreign = copy.deepcopy(decisive)
        foreign["h4_occurrence_evidence"]["sha256"] = "e" * 64
        with self.assertRaisesRegex(AssertionError, "evidence hash differs"):
            validate_report_semantics(foreign, self.plan)

        # evidence document binding and membership
        evidence_schema = json.loads(EVIDENCE_SCHEMA.read_bytes())
        validate_schema_value(EVIDENCE_DOCUMENT, evidence_schema, evidence_schema, "$")
        validate_evidence_document(EVIDENCE_DOCUMENT, EVIDENCE_REFERENCE, EVIDENCE_BYTES)
        with self.assertRaises(AssertionError):
            validate_evidence_document(EVIDENCE_DOCUMENT, EVIDENCE_REFERENCE, EVIDENCE_BYTES + b"\n")

    def test_h1_physical_ids_and_lifecycle_ranges_are_semantic(self) -> None:
        required_shapes = {
            "h1TdefCandidate", "h1TargetValidLayoutCandidate", "h1LocatorPairCandidate",
            "h2FinalRoleCandidate", "h3ConversionCandidate", "h3FinalBaseFormulaCandidate",
            "h4RootCandidate", "h4OperationRecordCandidate",
            "h4StructuralFieldCandidate", "h4FinalFieldCandidate",
            "h1ReplicaPair", "h2ReplicaPair", "h3ReplicaPair",
            "h4StructuralReplicaPair", "h4EncodingReplicaPair",
            "rowDirectoryObservation", "rowFlagsObservation", "mapTagObservation",
            "referenceObservation", "schemaDeltaOutsideObservation", "operationGroups",
        }
        for schema in (self.derivation_schema, self.analysis_schema):
            self.assertTrue(required_shapes.issubset(schema["$defs"]))
        first = candidate("h1_locator_pair", binding_variant=0, replicas=(1,))
        second = candidate("h1_locator_pair", binding_variant=1, replicas=(1,))
        self.assertEqual(first["canonical_model_id"], second["canonical_model_id"])
        self.assertNotEqual(first["canonical_candidate_id"], second["canonical_candidate_id"])
        result = empty_result("no_outcome")
        result["candidates"] = sorted_candidates([first, second])
        result["predicate_measured_survivor_count"] = 2
        result["terminal_predicate_id"] = "A4-H1-LOCATOR-PAIR-MULTIPLE"
        result["terminal_payload_kind"] = "candidate_set"
        result["terminal_candidate_stage"] = "h1_locator_pair"
        result["canonical_candidates_sha256"] = canonical_sha256(result["candidates"])
        validate_frozen_result(result, "h1_tdef_to_map_row")
        mixed = copy.deepcopy(result)
        mixed["candidates"][1] = candidate("h1_locator_pair", binding_variant=1, replicas=(2,))
        mixed["candidates"] = sorted_candidates(mixed["candidates"])
        mixed["canonical_candidates_sha256"] = canonical_sha256(mixed["candidates"])
        with self.assertRaisesRegex(AssertionError, "mixes replicas"):
            validate_frozen_result(mixed, "h1_tdef_to_map_row")

        invalid_range = model_result("h1_locator_pair")
        invalid_range["candidates"][0]["instance_bindings"][1]["applicable_checkpoint_range"] = {
            "start": "T1_CREATE_ID",
            "end": "T4_IDLE_R",
        }
        invalid_range["canonical_candidates_sha256"] = canonical_sha256(
            invalid_range["candidates"]
        )
        with self.assertRaises(AssertionError):
            validate_frozen_result(invalid_range, "h1_tdef_to_map_row")

    def test_every_derivation_terminal_serializes_its_declared_payload(self) -> None:
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        payload_counts = {"candidate_set": 0, "grouped_candidate_set": 0, "replica_pair": 0, "invalid_observation": 0}
        for index, contract in enumerate(contracts[:35]):
            if contract["scope"] == "campaign":
                continue
            report = build_report(self.plan, index)
            validate_schema_value(report, self.analysis_schema, self.analysis_schema, "$")
            validate_report_semantics(report, self.plan)
            frozen = build_frozen_document(report, self.plan)
            validate_schema_value(frozen, self.derivation_schema, self.derivation_schema, "$")
            self.assertEqual(frozen["layers"], report["layers"])
            payload = contract["terminal_payload_schema"]
            payload_counts[payload] += 1
            for slot in contract["result_slots"]:
                result = report["layers"].get(slot) or report["layers"]["h4_catalog_bootstrap"][slot]
                self.assertEqual(result["terminal_predicate_id"], contract["predicate_id"])
                self.assertEqual(result["terminal_payload_kind"], payload)
                stage = result["terminal_candidate_stage"]
                self.assertTrue(all(item["model_type"] == stage for item in result["candidates"]))
                if payload == "candidate_set":
                    self.assertEqual(len(result["candidates"]), result["predicate_measured_survivor_count"])
                    self.assertIsNone(result["terminal_evidence"])
                elif payload == "replica_pair":
                    self.assertEqual(result["candidates"], [])
                    self.assertEqual(result["predicate_measured_survivor_count"], 2)
                    entries = result["terminal_evidence"]["entries"]
                    self.assertEqual([entry["replica"] for entry in entries], [1, 2])
                    self.assertNotEqual(entries[0]["canonical_candidate_id"], entries[1]["canonical_candidate_id"])
                elif payload == "invalid_observation":
                    self.assertEqual(result["predicate_measured_survivor_count"], 1)
                    if contract["candidate_stage"] is None:
                        self.assertEqual(result["candidates"], [])
                        self.assertIsNone(stage)
                    else:
                        self.assertEqual(len(result["candidates"]), 1)
                else:
                    self.assertEqual(result["terminal_evidence"]["kind"], "operation_groups")
        self.assertEqual(payload_counts, {"candidate_set": 20, "grouped_candidate_set": 2, "replica_pair": 4, "invalid_observation": 5})
        by_id = {row["predicate_id"]: index for index, row in enumerate(contracts)}

        # H2 directory invalidity references the decisive H1 candidate and has no H2 candidate
        directory = build_report(self.plan, by_id["A4-H2-ROW-DIRECTORY-INVALID"])
        h2 = directory["layers"]["h2_row_identity_map_role"]
        self.assertEqual(
            h2["terminal_evidence"]["input_model_id"],
            directory["layers"]["h1_tdef_to_map_row"]["candidates"][0]["canonical_candidate_id"],
        )
        fabricated = copy.deepcopy(directory)
        fabricated_h2 = fabricated["layers"]["h2_row_identity_map_role"]
        fabricated_h2["candidates"] = [candidate("h2_final_role")]
        fabricated_h2["canonical_candidates_sha256"] = canonical_sha256(fabricated_h2["candidates"])
        with self.assertRaisesRegex(AssertionError, "must not fabricate"):
            validate_report_semantics(fabricated, self.plan)
        wrong_input = copy.deepcopy(directory)
        wrong_input["layers"]["h2_row_identity_map_role"]["terminal_evidence"]["input_model_id"] = "a" * 64
        with self.assertRaisesRegex(AssertionError, "decisive H1 candidate"):
            validate_report_semantics(wrong_input, self.plan)

        # H4 OUTSIDE keeps the decisive root and stores no operation-record candidate
        outside = build_report(self.plan, by_id["A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED"])
        outside_h4 = outside["layers"]["h4_catalog_bootstrap"]
        self.assertEqual(outside_h4["root_result"]["status"], "model")
        self.assertEqual(outside_h4["structural_result"]["candidates"], [])
        self.assertEqual(outside_h4["structural_result"]["terminal_evidence"]["kind"], "schema_delta_outside")
        self.assertEqual(
            outside_h4["structural_result"]["terminal_evidence"]["input_model_id"],
            outside_h4["root_result"]["candidates"][0]["canonical_candidate_id"],
        )
        self.assertIsNone(outside["h4_occurrence_evidence"])
        fabricated_record = copy.deepcopy(outside)
        structural = fabricated_record["layers"]["h4_catalog_bootstrap"]["structural_result"]
        structural["candidates"] = [candidate("h4_operation_record")]
        structural["canonical_candidates_sha256"] = canonical_sha256(structural["candidates"])
        with self.assertRaises(AssertionError):
            validate_report_semantics(fabricated_record, self.plan)

        # grouped operation-record candidates: [2,1,1,1,1,1,1] is eight candidates, maxima 3 and 4
        for maximum in (2, 3, 4):
            with self.subTest(group_maximum=maximum):
                grouped = build_report(self.plan, by_id["A4-H4-CATALOG-RECORD-MULTIPLE"], maximum)
                validate_schema_value(grouped, self.analysis_schema, self.analysis_schema, "$")
                validate_report_semantics(grouped, self.plan)
                structural = grouped["layers"]["h4_catalog_bootstrap"]["structural_result"]
                cardinalities = [group["cardinality"] for group in structural["terminal_evidence"]["groups"]]
                self.assertEqual(cardinalities, [maximum, 1, 1, 1, 1, 1, 1])
                self.assertEqual(len(structural["candidates"]), maximum + 6)
                self.assertEqual(structural["predicate_measured_survivor_count"], maximum)
        flat = build_report(self.plan, by_id["A4-H4-CATALOG-RECORD-MULTIPLE"], 2)
        flat_structural = flat["layers"]["h4_catalog_bootstrap"]["structural_result"]
        flat_structural["predicate_measured_survivor_count"] = len(flat_structural["candidates"])
        flat["predicate_results"][by_id["A4-H4-CATALOG-RECORD-MULTIPLE"]]["predicate_measured_survivor_count"] = len(flat_structural["candidates"])
        with self.assertRaisesRegex(AssertionError, "maximum offending group"):
            validate_report_semantics(flat, self.plan)
        none = build_report(self.plan, by_id["A4-H4-CATALOG-RECORD-NONE"])
        none_structural = none["layers"]["h4_catalog_bootstrap"]["structural_result"]
        self.assertEqual([group["cardinality"] for group in none_structural["terminal_evidence"]["groups"]], [0, 1, 1, 1, 1, 1, 1])
        self.assertEqual(len(none_structural["candidates"]), 6)
        self.assertEqual(none_structural["predicate_measured_survivor_count"], 0)

        # replica disagreement is the unequal pair for every layer; an equal pair is rejected
        for predicate_id in ("A4-H1-REPLICA-DISAGREEMENT", "A4-H2-REPLICA-DISAGREEMENT", "A4-H3-REPLICA-DISAGREEMENT", "A4-H4-REPLICA-DISAGREEMENT"):
            with self.subTest(predicate=predicate_id):
                report = build_report(self.plan, by_id[predicate_id])
                slot = self.plan["predicate_registry"]["predicate_contracts"][by_id[predicate_id]]["result_slots"][-1]
                result = report["layers"].get(slot) or report["layers"]["h4_catalog_bootstrap"][slot]
                entries = result["terminal_evidence"]["entries"]
                if predicate_id.startswith("A4-H1"):
                    self.assertNotEqual(entries[0]["canonical_model_id"], entries[1]["canonical_model_id"])
                    self.assertEqual(len(entries[0]["complete_candidate"]["instance_bindings"]), 5)
                equal = copy.deepcopy(report)
                equal_result = equal["layers"].get(slot) or equal["layers"]["h4_catalog_bootstrap"][slot]
                equal_entries = equal_result["terminal_evidence"]["entries"]
                mirrored = copy.deepcopy(equal_entries[0])
                if predicate_id.startswith("A4-H1"):
                    mirrored["complete_candidate"]["instance_bindings"] = h1_bindings(True, 0, (2,))
                    mirrored["canonical_candidate_id"] = canonical_sha256(
                        {"model_type": "h1_locator_pair", "model": mirrored["complete_candidate"]["model"], "instance_bindings": mirrored["complete_candidate"]["instance_bindings"]}
                    )
                    mirrored["complete_candidate"]["canonical_candidate_id"] = mirrored["canonical_candidate_id"]
                mirrored["replica"] = 2
                equal_entries[1] = mirrored
                validate_schema_value(equal, self.analysis_schema, self.analysis_schema, "$")
                with self.assertRaisesRegex(AssertionError, "replica pair models are equal"):
                    validate_report_semantics(equal, self.plan)
        h4_pair = build_report(self.plan, by_id["A4-H4-REPLICA-DISAGREEMENT"])["layers"]["h4_catalog_bootstrap"]
        self.assertEqual(h4_pair["structural_result"]["terminal_payload_kind"], "replica_pair")
        self.assertEqual(h4_pair["encoding_result"]["terminal_payload_kind"], "replica_pair")
        self.assertEqual(h4_pair["structural_result"]["terminal_predicate_id"], h4_pair["encoding_result"]["terminal_predicate_id"])

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
            freeze = build_frozen_document(report, self.plan)
            with self.subTest(terminal=contracts[index]["predicate_id"]):
                validate_schema_value(freeze, self.derivation_schema, self.derivation_schema, "$")
                validate_layer_semantics(freeze["layers"], freeze["h4_occurrence_evidence"])
                validate_work_charges(freeze["work_charges"])
                self.assertEqual(freeze["layers"], report["layers"])
                self.assertNotIn("holdout_results", freeze)
        partial = build_report(
            self.plan, by_id["A4-H4-FIELD-MODEL-NONE"]
        )["layers"]["h4_catalog_bootstrap"]
        self.assertEqual(partial["root_result"]["status"], "model")
        self.assertEqual(partial["structural_result"]["status"], "no_outcome")
        self.assertEqual(partial["encoding_result"]["status"], "not_applicable")
        pass_report = build_report(self.plan)
        frozen_pass = pass_report["layers"]
        frozen_document = build_frozen_document(pass_report, self.plan)
        frozen_bytes = canonical_bytes(frozen_document)
        frozen_sha256 = hashlib.sha256(frozen_bytes).hexdigest()
        pass_report["derivation_candidate_set_sha256"] = frozen_sha256
        validate_frozen_file_hash(pass_report, frozen_bytes)
        failed_holdout = build_report(
            self.plan, by_id["A4-H4-HOLDOUT-ROOT"]
        )
        failed_holdout["derivation_candidate_set_sha256"] = frozen_sha256
        self.assertEqual(failed_holdout["layers"], frozen_pass)
        self.assertEqual(failed_holdout["h4_occurrence_evidence"], pass_report["h4_occurrence_evidence"])
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
            validate_frozen_result(h2, "h2_row_identity_map_role")

        h1 = copy.deepcopy(report["layers"]["h1_tdef_to_map_row"])
        h1["candidates"][0]["instance_bindings"][0]["locator_targets"] = [
            {"page": 24, "row": 0},
            {"page": 24, "row": 0},
        ]
        h1["canonical_candidates_sha256"] = canonical_sha256(h1["candidates"])
        with self.assertRaises(AssertionError):
            validate_frozen_result(h1, "h1_tdef_to_map_row")

        stale_id = copy.deepcopy(report["layers"]["h1_tdef_to_map_row"])
        stale_id["candidates"][0]["model"]["layout"] = "u24le_page_then_u8_row"
        stale_id["canonical_candidates_sha256"] = canonical_sha256(
            stale_id["candidates"]
        )
        with self.assertRaises(AssertionError):
            validate_frozen_result(stale_id, "h1_tdef_to_map_row")

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
            validate_frozen_result(reversed_offsets, "h1_tdef_to_map_row")

        layer_mismatch = copy.deepcopy(report)
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        tdef_multiple = next(row for row in contracts if row["predicate_id"] == "A4-H1-TDEF-MULTIPLE")
        layer_mismatch["layers"]["h1_tdef_to_map_row"] = terminal_result(
            tdef_multiple, 2, "h1_tdef_to_map_row", ZERO_SHA256
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

    def test_candidate_and_evidence_byte_bounds_close_the_json_proof(self) -> None:
        bounds = self.plan["bounds"]
        self.assertEqual(bounds["max_h4_occurrence_identities"], 1850)
        self.assertEqual(bounds["max_h4_occurrence_evidence_bytes"], 524288)
        largest = 0
        for stage in STAGE_SLOTS:
            with self.subTest(stage=stage):
                item = maximal_candidate(stage)
                schema_name = {
                    "h1_tdef": "h1TdefCandidate",
                    "h1_target_valid_layout": "h1TargetValidLayoutCandidate",
                    "h1_locator_pair": "h1LocatorPairCandidate",
                    "h2_final_role": "h2FinalRoleCandidate",
                    "h3_conversion": "h3ConversionCandidate",
                    "h3_final_base_formula": "h3FinalBaseFormulaCandidate",
                    "h4_catalog_root": "h4RootCandidate",
                    "h4_operation_record": "h4OperationRecordCandidate",
                    "h4_structural_field": "h4StructuralFieldCandidate",
                    "h4_final_encoded_field": "h4FinalFieldCandidate",
                }[stage]
                validate_schema_value(item, self.derivation_schema["$defs"][schema_name], self.derivation_schema, "$")
                size = len(canonical_bytes(item))
                largest = max(largest, size)
                self.assertLessEqual(size, bounds["max_canonical_candidate_bytes"])
        self.assertLess(largest, 2600)
        evidence_schema = json.loads(EVIDENCE_SCHEMA.read_bytes())
        maximal = maximal_occurrence_evidence()
        validate_schema_value(maximal, evidence_schema, evidence_schema, "$")
        maximal_bytes = canonical_bytes(maximal)
        identities = sum(len(binding["occurrences"]) for binding in maximal["operation_bindings"])
        self.assertEqual(identities, 1850)
        self.assertLessEqual(len(maximal_bytes), bounds["max_h4_occurrence_evidence_bytes"])
        reference = {"path": EVIDENCE_PATH, "sha256": hashlib.sha256(maximal_bytes).hexdigest(), "size_bytes": len(maximal_bytes)}
        validate_evidence_document(maximal, reference, maximal_bytes)
        overflow = copy.deepcopy(maximal)
        overflow["operation_bindings"][0]["occurrences"].append({**overflow["operation_bindings"][0]["occurrences"][-1], "occurrence_index": 254})
        with self.assertRaises(ValidationError):
            validate_schema_value(overflow, evidence_schema, evidence_schema, "$")
        closure_total = (
            bounds["max_canonical_candidates_array_bytes"] + 300000
            + bounds["max_h4_occurrence_evidence_bytes"]
            + 1024 * 512 * 3 + 1024 * 384 * 2 + 4096 * 512 + 1048576
        )
        self.assertEqual(bounds["max_canonical_candidates_array_bytes"], 4096 * 4096 + 4097)
        self.assertEqual(1024 * 512 * 3 + 1024 * 384 * 2 + 4096 * 512, 4456448)
        self.assertLess(closure_total, 23200000)
        self.assertLess(closure_total, bounds["max_json_bytes"])
        validate_json_resource_bounds(4096, 4096, 1850, 524288, 67108864, bounds)
        for kwargs, message in (
            ({"candidate_count": 4097}, "candidate 4,097"),
            ({"largest_candidate_bytes": 4097}, "candidate byte 4,097"),
            ({"occurrence_identities": 1851}, "occurrence identity 1,851"),
            ({"evidence_bytes": 524289}, "evidence byte 524,289"),
            ({"report_bytes": 67108865}, "report byte 67,108,865"),
        ):
            arguments = {"candidate_count": 4096, "largest_candidate_bytes": 4096, "occurrence_identities": 1850, "evidence_bytes": 524288, "report_bytes": 67108864, **kwargs}
            with self.assertRaisesRegex(AssertionError, message):
                validate_json_resource_bounds(bounds=bounds, **arguments)
        self.assertEqual(self.plan["artifacts"]["h4_occurrence_evidence"], EVIDENCE_PATH)
        manifest = json.loads(BUNDLE_SCHEMA.read_bytes())
        self.assertIn("h4_occurrence_evidence", manifest["$defs"]["file"]["properties"]["role"]["enum"])

    def test_work_bound_is_conservative_and_only_its_comparator_is_exercised(self) -> None:
        work = self.plan["work_model"]
        bounds = self.plan["bounds"]
        self.assertEqual(work["bound_classification"]["max_analysis_work_units"], "conservative_upper")
        self.assertEqual(work["terminal_path_maxima"]["computed_units"]["h4_latest_derivation_terminal"], 387467081)
        self.assertEqual(600000000 - 387467081, 212532919)
        self.assertIn("212,532,919", work["terminal_path_maxima"]["proof"])
        validate_analysis_work_bound(600000000, bounds["max_analysis_work_units"])
        with self.assertRaises(AssertionError):
            validate_analysis_work_bound(600000001, bounds["max_analysis_work_units"])
        clause = self.plan["analyzer_dry_run_contract"]["dry_run_honesty_clause"]
        self.assertIn("work_counter_comparator_equality", clause["required_cases"])
        self.assertNotIn("resource_exact_ceiling", clause["required_cases"])
        self.assertIn("resource_one_over", clause["required_cases"])
        self.assertIn("outside the 40", clause["work_counter_comparator_rule"])
        dry = json.loads(DRY_RUN_SCHEMA.read_bytes())
        coverage = dry["properties"]["parameter_coverage"]
        self.assertIn("work_counter_comparator_equality", coverage["required"])
        self.assertNotIn("resource_exact_ceiling", coverage["required"])
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        resource = next(row for row in contracts if row["predicate_id"] == "A4-RESOURCE-BOUND")
        self.assertIn("67,200", resource["reachability_fixture"])
        self.assertEqual(21 * 3200, 67200)
        self.assertGreater(67200, self.plan["bounds"]["max_changed_hash_entries_per_replica"])
        self.assertEqual(work["bound_classification"]["max_inserted_rows_per_replica"], "attainable_exact")
        for field, classification in work["bound_classification"].items():
            self.assertIn(field, bounds)
        self.assertEqual(set(work["bound_classification"]), set(bounds))

    def test_reachability_transcript_is_registry_ordered_and_adversarially_fixed(self) -> None:
        schema = json.loads(REACHABILITY_TRANSCRIPT_SCHEMA.read_bytes())
        entries = schema["properties"]["fixture_entries"]
        contracts = self.plan["predicate_registry"]["predicate_contracts"]
        self.assertEqual(len(entries["prefixItems"]), 40)
        self.assertIs(entries["items"], False)
        for position, contract in zip(entries["prefixItems"], contracts, strict=True):
            self.assertEqual(position["properties"]["order"]["const"], contract["order"])
            self.assertEqual(position["properties"]["predicate_id"]["const"], contract["predicate_id"])
            self.assertEqual(position["properties"]["reachability_fixture_id"]["const"], contract["reachability_fixture_id"])
            if contract["predicate_id"] == "A4-H1-LOCATOR-PAIR-MULTIPLE":
                self.assertIsNone(position["properties"]["first_failure_id"]["const"])
                self.assertEqual(position["properties"]["unreachable_assertion"], {"$ref": "#/$defs/unreachableAssertion"})
            else:
                self.assertEqual(position["properties"]["first_failure_id"]["const"], contract["predicate_id"])
                self.assertIsNone(position["properties"]["unreachable_assertion"]["const"])
        outcomes = schema["properties"]["adversarial_case_outcomes"]
        self.assertEqual(outcomes["required"], list(ADVERSARIAL_CASES))
        self.assertFalse(outcomes["additionalProperties"])
        for case, expected in ADVERSARIAL_CASES.items():
            self.assertEqual(outcomes["properties"][case]["properties"]["expected"]["const"], expected)
        transcript = build_transcript(self.plan)
        validate_schema_value(transcript, schema, schema, "$")
        validate_transcript_semantics(transcript, self.plan)

        same_predicate = copy.deepcopy(transcript)
        for order, entry in enumerate(same_predicate["fixture_entries"], start=1):
            entry.update(copy.deepcopy(transcript["fixture_entries"][0]))
            entry["order"] = order
            entry["mutation_sha256"] = canonical_sha256(["mutation", order])
        with self.assertRaises(ValidationError):
            validate_schema_value(same_predicate, schema, schema, "$")

        reordered = copy.deepcopy(transcript)
        reordered["fixture_entries"][0], reordered["fixture_entries"][1] = (
            reordered["fixture_entries"][1], reordered["fixture_entries"][0]
        )
        with self.assertRaises(ValidationError):
            validate_schema_value(reordered, schema, schema, "$")

        extra = copy.deepcopy(transcript)
        extra["fixture_entries"].append(copy.deepcopy(transcript["fixture_entries"][-1]))
        with self.assertRaises(ValidationError):
            validate_schema_value(extra, schema, schema, "$")

        accepted_malformed = copy.deepcopy(transcript)
        accepted_malformed["adversarial_case_outcomes"]["malformed_page"]["expected"] = "accept"
        with self.assertRaises(ValidationError):
            validate_schema_value(accepted_malformed, schema, schema, "$")
        renamed = copy.deepcopy(transcript)
        renamed["adversarial_case_outcomes"]["resource_exact_ceiling"] = renamed["adversarial_case_outcomes"].pop("work_counter_comparator_equality")
        with self.assertRaises(ValidationError):
            validate_schema_value(renamed, schema, schema, "$")

        reachable_row = next(
            index for index, entry in enumerate(transcript["fixture_entries"]) if entry["predicate_id"] == "A4-H1-LOCATOR-PAIR-MULTIPLE"
        )
        fabricated = copy.deepcopy(transcript)
        fabricated["fixture_entries"][reachable_row]["first_failure_id"] = "A4-H1-LOCATOR-PAIR-MULTIPLE"
        with self.assertRaises(ValidationError):
            validate_schema_value(fabricated, schema, schema, "$")
        null_elsewhere = copy.deepcopy(transcript)
        null_elsewhere["fixture_entries"][0]["first_failure_id"] = None
        with self.assertRaises(ValidationError):
            validate_schema_value(null_elsewhere, schema, schema, "$")
        silent = copy.deepcopy(transcript)
        silent["fixture_entries"][reachable_row]["unreachable_assertion"] = None
        with self.assertRaises(ValidationError):
            validate_schema_value(silent, schema, schema, "$")

        short_prefix = copy.deepcopy(transcript)
        del short_prefix["fixture_entries"][5]["evaluated_predicates"][2]
        validate_schema_value(short_prefix, schema, schema, "$")
        with self.assertRaisesRegex(AssertionError, "exact applicable prefix"):
            validate_transcript_semantics(short_prefix, self.plan)
        disagreeing = copy.deepcopy(transcript)
        disagreeing["fixture_entries"][7]["independent_validator_result"]["candidate_set_sha256"] = "b" * 64
        validate_schema_value(disagreeing, schema, schema, "$")
        with self.assertRaisesRegex(AssertionError, "analyzer and validator results differ"):
            validate_transcript_semantics(disagreeing, self.plan)
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
        self.assertEqual(self.plan["work_model"]["bound_classification"]["max_analysis_work_units"], "conservative_upper")

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
        self.assertIn("<= 20479", expected["decodability_rule"])
        looser = sum(
            1 for offset in range(2045)
            if all(int.from_bytes(page[offset : offset + 3], "little") <= 65535 for page in pages)
        )
        self.assertEqual(looser, 1875)
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

    def test_harness_ambiguities_are_resolved_by_stated_decisions(self) -> None:
        harness = self.plan["preregistration"]["origin_disclosure"]["executed_reference_harness"]
        self.assertEqual(harness["pull_request"], 74)
        self.assertRegex(harness["head_commit"], "^[0-9a-f]{40}$")
        self.assertRegex(harness["reference_transcript_sha256"], "^[0-9a-f]{64}$")
        self.assertIn("not the production analyzer", harness["role"])
        self.assertIn("never A4 evidence", harness["role"])
        resolutions = self.plan["harness_ambiguity_resolutions"]["resolutions"]
        self.assertEqual([row["id"] for row in resolutions], [f"AMB-{n:02d}" for n in range(1, 18)])
        for row in resolutions:
            self.assertEqual(set(row), {"id", "topic", "decision", "plan_locations"})
            self.assertGreater(len(row["decision"]), 80)
            self.assertTrue(row["plan_locations"])
        grammars = self.plan["candidate_grammars"]
        self.assertIn("<= 20479", grammars["h1"]["layout_candidate_rule"])
        self.assertIn("AMB-05", grammars["h2"]["static_fit_rule"])
        self.assertIn("AMB-07", grammars["h2"]["transition_signature"]["grow"])
        self.assertIn("AMB-08", grammars["h3"]["fit_rule"])
        self.assertIn("every one of the five", grammars["h4"]["catalog_root_selection_signature_rules"]["operation_delta_non_name_structure"])
        self.assertIn("page 0 and page 1", grammars["h4"]["isolated_delta_rule"])
        self.assertEqual(
            grammars["h4"]["raw_tuple_filter_order"][5:7],
            ["test_stored_length_plausibility", "deduplicate_value_equivalent_tuples"],
        )
        self.assertIn("value_equivalent_tuple_count", grammars["h4"]["field_candidate_shapes"])
        rule = self.plan["predicate_registry"]["evaluation_rule"]
        for phrase in ("replica 1 and then replica 2", "only after the frozen-document hash exists"):
            self.assertIn(phrase, rule)
        contracts = {row["predicate_id"]: row for row in self.plan["predicate_registry"]["predicate_contracts"]}
        self.assertIn("vacuous", contracts["A4-H3-HOLDOUT-PREDICTION"]["fail_iff"])
        self.assertIn("T1_REL_0512", contracts["A4-H2-TRANSITION-UNEXPLAINED"]["reachability_fixture"])
        comparator = self.plan["analyzer_dry_run_contract"]["dry_run_honesty_clause"]["work_counter_comparator_rule"]
        self.assertIn("no charge-injection hook", comparator)

    def test_a3_only_fields_are_absent_and_claims_are_fail_closed(self) -> None:
        forbidden = {
            "polarity_cross_check", "globalRecordModel", "conversionModel", "baseModel", "tdefModel", "inline_boundary",
            "h2StructuralDecoder", "field_result", "name_length_endianness", "compatible_name_occurrences",
        }
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
