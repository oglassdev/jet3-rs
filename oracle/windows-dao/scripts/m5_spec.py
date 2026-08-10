#!/usr/bin/env python3
"""Typed, immutable checked-plan contract for the DAO M5R4 campaign."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from protocol_validation import ValidationError

PROTOCOL_VERSION = "1.0.0"
EXPERIMENT_ID = "DAO-M5-COMPACT-CONFIRM-004"
PLAN_SHA256 = "7f9b49b18d75824843eb6269fafa25d1b21e4cd82c1bfe289af915ee0783aaed"
M4_EXPERIMENT_ID = "DAO-M4-HEADER-DISCRIMINATOR-003"
M4_MANIFEST_SHA256 = "0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d"
M4_PRODUCER_COMMIT = "35f5f55f0b7277fc07831db540eab7fa69a41a20"
M4_RUN_ID = "20260810T220332Z-m4-r2"
REMOTE_REF = "refs/heads/codex/m5r3-timeout-bounded"
PREFIX_BYTES = 2048
ANALYZED_BYTES = 1536
PHASES = ("source", "compact", "verify")
DATABASE_ROLES = (
    "source_database",
    "compact_input_database",
    "compacted_database",
    "verify_database",
)
PHASE_DATABASE_ROLES = {
    "source": ("source_database",),
    "compact": ("compact_input_database", "compacted_database"),
    "verify": ("verify_database",),
}
DATABASE_BASENAMES = {
    "source_database": "SOURCE.MDB",
    "compact_input_database": "COMPACT-INPUT.MDB",
    "compacted_database": "COMPACTED.MDB",
    "verify_database": "VERIFY.MDB",
}
EXPECTED_COMPARISONS = 648
EXPECTED_BYTE_VISITS = EXPECTED_COMPARISONS * 2 * ANALYZED_BYTES


@dataclass(frozen=True)
class CheckedPlan:
    document: dict[str, Any]
    samples: tuple[dict[str, Any], ...]
    samples_by_id: Mapping[str, dict[str, Any]]
    conditions: tuple[dict[str, Any], ...]
    conditions_by_id: Mapping[str, dict[str, Any]]
    bounds: Mapping[str, int]
    predicates: tuple[dict[str, Any], ...]
    outcome_rules: Mapping[str, Any]


def require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked projection")


def _integer(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{location}: integer required")
    return value


def compile_checked_plan(plan: dict[str, Any]) -> CheckedPlan:
    """Compile relational indexes after checking every scientific invariant."""
    if not isinstance(plan, dict):
        raise ValidationError("M5 plan must be an object")
    require_equal(plan.get("protocol_version"), PROTOCOL_VERSION, "$.protocol_version")
    require_equal(plan.get("document_type"), "dao_m5_plan", "$.document_type")
    require_equal(plan.get("experiment_id"), EXPERIMENT_ID, "$.experiment_id")
    require_equal(plan.get("remote_ref"), REMOTE_REF, "$.remote_ref")
    require_equal(
        plan.get("execution_gate"),
        {
            "status": "BLOCKED",
            "reason": "exact_m5r4_producer_commit_and_remote_ref_not_yet_established",
            "blocking_requirements": [
                "windows_dao_host_bound_to_the_exact_clean_pushed_producer_commit"
            ],
        },
        "$.execution_gate",
    )
    design = plan.get("design")
    analysis = plan.get("analysis")
    bounds = plan.get("bounds")
    if not isinstance(design, dict) or not isinstance(analysis, dict) or not isinstance(bounds, dict):
        raise ValidationError("M5 design, analysis, and bounds must be objects")
    require_equal(design.get("condition_count"), 36, "$.design.condition_count")
    require_equal(design.get("replicas_per_condition"), 3, "$.design.replicas_per_condition")
    require_equal(design.get("workers_per_sample"), 3, "$.design.workers_per_sample")
    require_equal(
        design.get("controller_post_worker_quiescence", {}).get("database_roles"),
        list(DATABASE_ROLES),
        "$.design.controller_post_worker_quiescence.database_roles",
    )
    expected_bounds = {
        "max_database_bytes": 1048576,
        "max_database_artifacts": 432,
        "max_total_database_bytes": 452984832,
        "max_validator_database_reads_per_run": 432,
        "max_validator_database_read_bytes_per_run": 452984832,
        "max_plan_bytes": 1048576,
        "max_sample_record_bytes": 65536,
        "max_analysis_report_bytes": 16777216,
        "prefix_bytes_per_phase": PREFIX_BYTES,
        "max_prefix_artifacts": 324,
        "max_total_prefix_bytes": 324 * PREFIX_BYTES,
        "max_analyzed_offsets": ANALYZED_BYTES,
        "max_comparisons": EXPECTED_COMPARISONS,
        "max_comparison_byte_visits": EXPECTED_BYTE_VISITS,
        "max_candidate_sets": 3,
        "max_worker_processes": 324,
        "worker_timeout_seconds": 120,
        "max_companion_bytes": 65536,
        "max_companion_artifacts": 432,
        "max_total_companion_bytes": 432 * 65536,
        "max_validator_companion_reads_per_run": 432,
        "max_validator_companion_read_bytes_per_run": 432 * 65536,
        "quiescence_records_per_sample": 4,
        "max_quiescence_records": 432,
        "max_quiescence_record_bytes": 16384,
    }
    for key, expected in expected_bounds.items():
        require_equal(bounds.get(key), expected, f"$.bounds.{key}")
    require_equal(analysis.get("retained_prefix_range"), {"start": 0, "end": PREFIX_BYTES}, "$.analysis.retained_prefix_range")
    require_equal(analysis.get("analyzed_ranges"), [{"start": 0, "end": ANALYZED_BYTES}], "$.analysis.analyzed_ranges")
    require_equal(
        analysis.get("comparison_counts"),
        {
            "paired_phase": 108,
            "within_condition": 324,
            "compact_versus_created_matched": 108,
            "source_versus_compacted_within_sample": 108,
            "total": 648,
        },
        "$.analysis.comparison_counts",
    )
    require_equal(analysis.get("physical_meaning_may_be_assigned"), False, "$.analysis.physical_meaning_may_be_assigned")
    require_equal(analysis.get("compatibility_may_be_claimed"), False, "$.analysis.compatibility_may_be_claimed")
    binding = analysis.get("m4_binding")
    if not isinstance(binding, dict):
        raise ValidationError("$.analysis.m4_binding: object required")
    for key, expected in (
        ("experiment_id", M4_EXPERIMENT_ID),
        ("bundle_manifest_sha256", M4_MANIFEST_SHA256),
        ("producer_commit", M4_PRODUCER_COMMIT),
        ("campaign_run_id", M4_RUN_ID),
        ("m4_bundle_is_read_only_input", True),
    ):
        require_equal(binding.get(key), expected, f"$.analysis.m4_binding.{key}")

    conditions = plan.get("conditions")
    samples = plan.get("samples")
    if not isinstance(conditions, list) or len(conditions) != 36:
        raise ValidationError("$.conditions: expected exactly 36 rows")
    if not isinstance(samples, list) or len(samples) != 108:
        raise ValidationError("$.samples: expected exactly 108 rows")
    condition_ids: list[str] = []
    conditions_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(conditions):
        if not isinstance(row, dict) or not isinstance(row.get("condition_id"), str):
            raise ValidationError(f"$.conditions[{index}]: invalid condition")
        condition_id = row["condition_id"]
        if condition_id in conditions_by_id:
            raise ValidationError(f"$.conditions: duplicate {condition_id}")
        if row.get("compact_encryption_option") == "dbDecrypt":
            require_equal(row.get("compact_encryption_api_value"), 4, f"{condition_id}.compact_encryption_api_value")
            require_equal(row.get("compact_option_value"), row["destination_version_api_value"] + 4, f"{condition_id}.compact_option_value")
        condition_ids.append(condition_id)
        conditions_by_id[condition_id] = row

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    by_block: dict[int, list[dict[str, Any]]] = {}
    for index, row in enumerate(samples):
        if not isinstance(row, dict):
            raise ValidationError(f"$.samples[{index}]: object required")
        condition_id = row.get("condition_id")
        replica = _integer(row.get("replica"), f"$.samples[{index}].replica")
        sample_id = f"M5-{condition_id}-{replica:02d}"
        require_equal(row.get("sample_id"), sample_id, f"$.samples[{index}].sample_id")
        require_equal(row.get("block"), replica, f"{sample_id}.block")
        require_equal(row.get("launch_ordinal"), index + 1, f"{sample_id}.launch_ordinal")
        base = f"evidence/samples/{sample_id}"
        for role, key in (
            ("source_database", "source_database_path"),
            ("compact_input_database", "compact_input_database_path"),
            ("compacted_database", "compacted_database_path"),
            ("verify_database", "verify_database_path"),
        ):
            expected = f"{base}/{DATABASE_BASENAMES[role]}"
            require_equal(row.get(key), expected, f"{sample_id}.{key}")
            if expected in seen_paths:
                raise ValidationError(f"$.samples: duplicate path {expected}")
            seen_paths.add(expected)
        require_equal(row.get("record_path"), f"{base}/record.json", f"{sample_id}.record_path")
        if sample_id in seen_ids or condition_id not in conditions_by_id:
            raise ValidationError(f"$.samples[{index}]: duplicate or unknown sample")
        seen_ids.add(sample_id)
        by_block.setdefault(replica, []).append(row)
    for block in range(1, 4):
        rows = sorted(by_block.get(block, ()), key=lambda row: row["position_in_block"])
        expected = condition_ids[12 * (block - 1) :] + condition_ids[: 12 * (block - 1)]
        require_equal([row["condition_id"] for row in rows], expected, f"$.samples block {block}")
        require_equal([row["position_in_block"] for row in rows], list(range(1, 37)), f"$.samples block {block} positions")
    require_equal([row["launch_ordinal"] for row in samples], list(range(1, 109)), "$.samples launch order")
    return CheckedPlan(
        document=plan,
        samples=tuple(samples),
        samples_by_id=MappingProxyType({row["sample_id"]: row for row in samples}),
        conditions=tuple(conditions),
        conditions_by_id=MappingProxyType(conditions_by_id),
        bounds=MappingProxyType(dict(bounds)),
        predicates=tuple(analysis["confirmation_predicates"]),
        outcome_rules=MappingProxyType(dict(analysis["scientific_outcome_rules"])),
    )
