#!/usr/bin/env python3
"""Checked preregistration contract for the set-reference M5 successor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_validation import ValidationError, load_json, sha256

DAO_ROOT = Path(__file__).resolve().parents[1]
CHECKED_PLAN = (
    DAO_ROOT / "experiments" / "m5s1" / "m5-set-reference.plan.json"
)
PLAN_SHA256 = "3f2863fb51338aa2d6ef54553fcbe5b4826c8d98cce85151958b04d500611261"
EXPERIMENT_ID = "DAO-M5-SET-REFERENCE-001"
M4_EXPERIMENT_ID = "DAO-M4-HEADER-DISCRIMINATOR-003"
M4_MANIFEST_SHA256 = "0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d"
M4_PRODUCER_COMMIT = "35f5f55f0b7277fc07831db540eab7fa69a41a20"
M4_RUN_ID = "20260810T220332Z-m4-r2"
ANALYZED_BYTES = 1536
PREFIX_BYTES = 2048
CONDITION_COUNT = 36
REPLICAS = 3

SOURCE_VERSIONS = [
    {"name": "dbVersion20", "token": "20", "api_value": 16, "dao_version": "2.0"},
    {"name": "dbVersion30", "token": "30", "api_value": 32, "dao_version": "3.0"},
    {"name": "dbVersion40", "token": "40", "api_value": 64, "dao_version": "4.0"},
]
SOURCE_ENCRYPTION = [
    {"name": "omitted", "token": "U", "api_value": 0, "encrypted": False},
    {"name": "dbEncrypt", "token": "E", "api_value": 2, "encrypted": True},
]
VERSION_PAIRS = ["20-20", "20-30", "20-40", "30-30", "30-40", "40-40"]
COMPACT_ENCRYPTION = [
    {
        "name": "omitted",
        "token": "OMIT",
        "api_value": 0,
        "destination_state": "preserve_source",
    },
    {
        "name": "dbEncrypt",
        "token": "ENC",
        "api_value": 2,
        "destination_state": "encrypted",
    },
    {
        "name": "dbDecrypt",
        "token": "DEC",
        "api_value": 4,
        "destination_state": "unencrypted",
    },
]


@dataclass(frozen=True)
class CheckedSuccessorPlan:
    """Derived immutable condition and schedule projection."""

    document: dict[str, Any]
    condition_ids: tuple[str, ...]
    matched_m4_conditions: tuple[str, ...]
    schedule: tuple[tuple[str, ...], ...]


def require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked projection")


def require_keys(value: Any, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValidationError(f"{location}: expected exact object fields {sorted(expected)}")
    return value


def _derive_conditions(design: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    conditions: list[tuple[str, str]] = []
    for source_version in SOURCE_VERSIONS:
        source_token = source_version["token"]
        for source_encryption in SOURCE_ENCRYPTION:
            source_encryption_token = source_encryption["token"]
            for pair in VERSION_PAIRS:
                pair_source, destination_token = pair.split("-")
                if pair_source != source_token:
                    continue
                for compact_encryption in COMPACT_ENCRYPTION:
                    compact_token = compact_encryption["token"]
                    condition_id = (
                        f"S{source_token}{source_encryption_token}"
                        f"-D{destination_token}-{compact_token}"
                    )
                    if compact_token == "OMIT":
                        destination_encryption = source_encryption_token
                    elif compact_token == "ENC":
                        destination_encryption = "E"
                    else:
                        destination_encryption = "U"
                    conditions.append(
                        (condition_id, f"V{destination_token}-{destination_encryption}")
                    )
    require_equal(len(conditions), CONDITION_COUNT, "derived condition count")
    require_equal(len(set(conditions)), CONDITION_COUNT, "derived condition uniqueness")
    schedule = design["schedule"]
    require_equal(schedule["block_count"], REPLICAS, "$.acquisition_design.schedule.block_count")
    require_equal(
        schedule["conditions_per_block"],
        CONDITION_COUNT,
        "$.acquisition_design.schedule.conditions_per_block",
    )
    return tuple(conditions)


def compile_checked_plan(document: dict[str, Any]) -> CheckedSuccessorPlan:
    """Validate the preregistration and derive its complete fixed schedule."""
    require_keys(
        document,
        {
            "protocol_version",
            "document_type",
            "experiment_id",
            "preregistration",
            "provenance_ids",
            "related_experiments",
            "execution_gate",
            "m4_binding",
            "acquisition_design",
            "reference_semantics",
            "analysis",
            "bounds",
            "claims",
        },
        "$",
    )
    require_equal(document["protocol_version"], "1.0.0", "$.protocol_version")
    require_equal(document["document_type"], "dao_m5_successor_plan", "$.document_type")
    require_equal(document["experiment_id"], EXPERIMENT_ID, "$.experiment_id")
    require_equal(document["related_experiments"], ["EXP-0018", "EXP-0033"], "$.related_experiments")
    require_equal(
        document["provenance_ids"],
        ["SRC-0013", "SRC-0014", "SRC-0015", "SRC-0016", "SRC-0018", "SRC-0019"],
        "$.provenance_ids",
    )

    gate = document["execution_gate"]
    require_equal(gate["status"], "BLOCKED", "$.execution_gate.status")
    require_equal(
        gate["blocking_requirements"],
        [
            "independent_scientific_review",
            "checked_controller_workers_and_bundle_schemas",
            "checked_set_reference_analysis_and_independent_validator",
            "windows_dao_host_bound_to_an_exact_clean_pushed_successor_commit",
        ],
        "$.execution_gate.blocking_requirements",
    )
    preregistration = document["preregistration"]
    for key in (
        "prior_m5_artifacts_available",
        "prior_m5_acquisition_may_be_reused",
    ):
        require_equal(preregistration[key], False, f"$.preregistration.{key}")
    require_equal(
        preregistration["full_new_acquisition_required"],
        True,
        "$.preregistration.full_new_acquisition_required",
    )

    binding = document["m4_binding"]
    for key, expected in (
        ("experiment_id", M4_EXPERIMENT_ID),
        ("bundle_manifest_sha256", M4_MANIFEST_SHA256),
        ("producer_commit", M4_PRODUCER_COMMIT),
        ("campaign_run_id", M4_RUN_ID),
        ("scientific_outcome", "inconclusive"),
        ("read_only_input", True),
    ):
        require_equal(binding[key], expected, f"$.m4_binding.{key}")

    design = document["acquisition_design"]
    for key, expected in (
        ("condition_count", CONDITION_COUNT),
        ("replicas_per_condition", REPLICAS),
        ("sample_count", CONDITION_COUNT * REPLICAS),
        ("workers_per_sample", 3),
        ("worker_count", CONDITION_COUNT * REPLICAS * 3),
        ("phases", ["source", "compact", "verify"]),
        ("source_versions", SOURCE_VERSIONS),
        ("source_encryption_options", SOURCE_ENCRYPTION),
        ("destination_version_pairs", VERSION_PAIRS),
        ("compact_encryption_options", COMPACT_ENCRYPTION),
    ):
        require_equal(design[key], expected, f"$.acquisition_design.{key}")
    for key in (
        "fresh_workers",
        "fresh_databases",
        "controller_owned_exact_byte_clones",
        "controller_owned_post_worker_quiescence",
        "companions_excluded_from_analysis",
    ):
        require_equal(design["operational_contract"][key], True, f"$.acquisition_design.operational_contract.{key}")
    require_equal(
        design["operational_contract"]["m5r7_databases_or_prefixes_may_be_reused"],
        False,
        "$.acquisition_design.operational_contract.m5r7_databases_or_prefixes_may_be_reused",
    )

    conditions = _derive_conditions(design)
    condition_ids = tuple(condition for condition, _ in conditions)
    schedule = tuple(
        condition_ids[12 * block :] + condition_ids[: 12 * block]
        for block in range(REPLICAS)
    )

    semantics = document["reference_semantics"]
    for key, expected in (
        ("m4_observations_per_reference_unit", 12),
        ("m4_observation_phases", ["creator", "reopen"]),
        ("m4_replicas_per_condition", 6),
        ("empty_reference_set_allowed", False),
        ("representative_value_selection_allowed", False),
        ("unstable_offsets_may_be_deleted", False),
    ):
        require_equal(semantics[key], expected, f"$.reference_semantics.{key}")
    require_equal(
        semantics["construction"],
        "sorted distinct unsigned byte values from every one of the twelve validated M4 retained prefixes",
        "$.reference_semantics.construction",
    )
    require_equal(
        semantics["membership_rule"],
        "each primary M5 byte is tested for membership in the complete matched "
        "M4 reference set at the same absolute offset",
        "$.reference_semantics.membership_rule",
    )
    require_equal(
        semantics["novel_value_rule"],
        "a primary M5 byte is novel only when it is absent from the complete matched M4 reference set",
        "$.reference_semantics.novel_value_rule",
    )

    analysis = document["analysis"]
    require_equal(
        analysis["retained_prefix_range"],
        {"start": 0, "end": PREFIX_BYTES},
        "$.analysis.retained_prefix_range",
    )
    require_equal(analysis["analyzed_ranges"], [{"start": 0, "end": ANALYZED_BYTES}], "$.analysis.analyzed_ranges")
    require_equal(
        analysis["offset_1264_special_casing_allowed"],
        False,
        "$.analysis.offset_1264_special_casing_allowed",
    )
    require_equal(analysis["m4_candidate_sets_required"], False, "$.analysis.m4_candidate_sets_required")
    require_equal(analysis["physical_meaning_may_be_assigned"], False, "$.analysis.physical_meaning_may_be_assigned")
    require_equal(analysis["compatibility_may_be_claimed"], False, "$.analysis.compatibility_may_be_claimed")
    reference_units = CONDITION_COUNT * ANALYZED_BYTES
    membership_evaluations = reference_units * REPLICAS
    require_equal(analysis["primary_reference_units"], reference_units, "$.analysis.primary_reference_units")
    require_equal(
        analysis["primary_membership_evaluations"],
        membership_evaluations,
        "$.analysis.primary_membership_evaluations",
    )
    require_equal(
        analysis["scientific_outcome_rules"],
        {
            "reference_sets_contain_all_compact_observations": "every primary membership evaluation succeeds",
            "compact_observations_extend_reference_sets": "at least one primary membership evaluation fails",
            "no_scientific_outcome": (
                "acquisition bundle, M4 binding, reference construction, or "
                "successor validation fails"
            ),
        },
        "$.analysis.scientific_outcome_rules",
    )

    require_equal(
        document["bounds"],
        {
            "max_plan_bytes": 262144,
            "max_database_bytes": 1048576,
            "max_database_artifacts": 432,
            "max_total_database_bytes": 452984832,
            "max_acquisition_database_reads": 1620,
            "max_acquisition_database_read_bytes": 1698693120,
            "max_validator_database_reads_per_run": 432,
            "max_validator_database_read_bytes_per_run": 452984832,
            "max_prefix_artifacts": 324,
            "prefix_bytes_per_phase": PREFIX_BYTES,
            "max_total_prefix_bytes": 663552,
            "max_companion_bytes": 65536,
            "max_companion_artifacts": 432,
            "max_total_companion_bytes": 28311552,
            "max_acquisition_companion_reads": 432,
            "max_acquisition_companion_read_bytes": 28311552,
            "max_validator_companion_reads_per_run": 432,
            "max_validator_companion_read_bytes_per_run": 28311552,
            "quiescence_records_per_sample": 4,
            "max_quiescence_records": 432,
            "max_quiescence_record_bytes": 16384,
            "max_total_quiescence_record_bytes": 7077888,
            "max_sample_record_bytes": 65536,
            "max_reference_units": reference_units,
            "max_reference_set_members": 256,
            "max_primary_membership_evaluations": membership_evaluations,
            "max_novel_value_records": membership_evaluations,
            "max_worker_processes": CONDITION_COUNT * REPLICAS * 3,
            "worker_timeout_seconds": 120,
            "max_analysis_report_bytes": 16777216,
        },
        "$.bounds",
    )
    require_equal(
        document["claims"],
        {
            "descriptive_provider_observations_only": True,
            "format_field_identification": False,
            "rust_compatibility": False,
            "mdb_read_write_or_conversion_support": False,
        },
        "$.claims",
    )
    return CheckedSuccessorPlan(
        document=document,
        condition_ids=condition_ids,
        matched_m4_conditions=tuple(matched for _, matched in conditions),
        schedule=schedule,
    )


def load_checked_plan(path: Path = CHECKED_PLAN) -> CheckedSuccessorPlan:
    """Load only the exact preregistered plan bytes."""
    if sha256(path) != PLAN_SHA256:
        raise ValidationError("M5 successor plan bytes differ from the preregistration")
    document = load_json(path)
    if not isinstance(document, dict):
        raise ValidationError("M5 successor plan must be an object")
    return compile_checked_plan(document)
