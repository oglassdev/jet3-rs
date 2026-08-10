#!/usr/bin/env python3
"""Typed, immutable checked-plan contract for the DAO M4 campaign."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from protocol_validation import ValidationError

PROTOCOL_VERSION = "1.0.0"
EXPERIMENT_ID = "DAO-M4-HEADER-DISCRIMINATOR-002"
PREFIX_BYTES = 2048
ANALYZED_BYTES = 1536
PHASES = ("creator", "reopen")
CONDITION_IDS = ("V20-U", "V20-E", "V30-U", "V30-E", "V40-U", "V40-E")
VERSION_OPTIONS = ("dbVersion20", "dbVersion30", "dbVersion40")
ENCRYPTION_OPTIONS = ("omitted", "dbEncrypt")
COMPARISON_KINDS = (
    "paired_phase",
    "within_condition",
    "matched_version",
    "matched_encryption",
)
EXPECTED_COMPARISONS = 324
EXPECTED_BYTE_VISITS = EXPECTED_COMPARISONS * 2 * ANALYZED_BYTES
READY_GATE = {
    "status": "READY",
    "reason": "checked_runner_analysis_and_bundle_validator_implemented",
}

EXPECTED_BOUNDS = {
    "max_database_bytes": 1048576,
    "max_database_artifacts": 72,
    "max_total_database_bytes": 75497472,
    "max_acquisition_database_reads": 288,
    "max_acquisition_database_read_bytes": 301989888,
    "max_validator_database_reads_per_run": 72,
    "max_validator_database_read_bytes_per_run": 75497472,
    "max_plan_bytes": 1048576,
    "max_sample_record_bytes": 65536,
    "max_analysis_report_bytes": 16777216,
    "prefix_bytes_per_phase": PREFIX_BYTES,
    "max_prefix_artifacts": 72,
    "max_total_prefix_bytes": 72 * PREFIX_BYTES,
    "max_companion_artifacts": 72,
    "max_companion_bytes_per_artifact": 65536,
    "max_total_companion_bytes": 72 * 65536,
    "max_acquisition_companion_reads": 72,
    "max_acquisition_companion_read_bytes": 72 * 65536,
    "max_validator_companion_reads_per_run": 72,
    "max_validator_companion_read_bytes_per_run": 72 * 65536,
    "min_payload_files": 579,
    "max_payload_files": 651,
    "max_analyzed_offsets": ANALYZED_BYTES,
    "max_comparisons": EXPECTED_COMPARISONS,
    "max_comparison_byte_visits": EXPECTED_BYTE_VISITS,
    "max_candidate_sets": 3,
    "max_worker_processes": 72,
    "worker_timeout_seconds": 120,
}

EXPECTED_PREDICATES = (
    {
        "candidate_set_id": "M4-CANDIDATE-VERSION-PAIRED",
        "factor": "version",
        "stability_scope": "all_conditions_all_replicas_both_phases",
        "predicate": (
            "V30_UNENCRYPTED_DIFFERS_FROM_V20_AND_V40_UNENCRYPTED_"
            "AND_ENCRYPTION_PAIRS_EQUAL_AT_ALL_VERSIONS"
        ),
        "comparison_occurrence_kind": "matched_version",
    },
    {
        "candidate_set_id": "M4-CANDIDATE-V30-ENCRYPTION",
        "factor": "encryption",
        "stability_scope": "v30_all_replicas_both_phases",
        "predicate": "V30_UNENCRYPTED_DIFFERS_FROM_V30_ENCRYPTED",
        "comparison_occurrence_kind": "matched_encryption_v30",
    },
    {
        "candidate_set_id": "M4-CANDIDATE-ALL-VERSION-ENCRYPTION",
        "factor": "encryption",
        "stability_scope": "all_conditions_all_replicas_both_phases",
        "predicate": "SAME_NONZERO_XOR_ENCRYPTION_EFFECT_AT_ALL_VERSIONS",
        "comparison_occurrence_kind": "matched_encryption_all_versions",
    },
)

EXPECTED_OUTCOME_RULES = {
    "candidate_offsets_observed_requires_all_nonempty": [
        "M4-CANDIDATE-VERSION-PAIRED",
        "M4-CANDIDATE-V30-ENCRYPTION",
    ],
    "inconclusive_requires_any_nonempty_unless_candidate_offsets_observed": True,
    "no_candidates_observed_requires_all_empty": True,
}


@dataclass(frozen=True)
class CheckedPlan:
    """Relationally checked view of the immutable M4 plan."""

    document: dict[str, Any]
    samples: tuple[dict[str, Any], ...]
    samples_by_id: Mapping[str, dict[str, Any]]
    conditions: tuple[dict[str, Any], ...]
    conditions_by_id: Mapping[str, dict[str, Any]]
    conditions_by_factor: Mapping[tuple[str, str], str]
    bounds: Mapping[str, int]
    candidate_predicates: tuple[dict[str, Any], ...]
    scientific_outcome_rules: Mapping[str, Any]


def _checked_condition_rows() -> list[dict[str, Any]]:
    rows = []
    for version, api, label in (
        ("20", 16, "2.0"),
        ("30", 32, "3.0"),
        ("40", 64, "4.0"),
    ):
        for encrypted, suffix, encryption, encryption_value in (
            (False, "U", "omitted", 0),
            (True, "E", "dbEncrypt", 2),
        ):
            rows.append(
                {
                    "condition_id": f"V{version}-{suffix}",
                    "version_option": f"dbVersion{version}",
                    "version_api_value": api,
                    "encryption_option": encryption,
                    "encryption_api_value": encryption_value,
                    "create_option_value": api + (2 if encrypted else 0),
                    "expected_dao_version": label,
                }
            )
    return rows


def _require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked projection")


def compile_checked_plan(plan: dict[str, Any]) -> CheckedPlan:
    """Validate exact M4 policy and compile its relational indexes once."""
    if not isinstance(plan, dict):
        raise ValidationError("M4 plan must be an object")
    _require_equal(plan.get("execution_gate"), READY_GATE, "$.execution_gate")
    _require_equal(plan.get("conditions"), _checked_condition_rows(), "$.conditions")
    _require_equal(plan.get("bounds"), EXPECTED_BOUNDS, "$.bounds")

    analysis = plan.get("analysis")
    if not isinstance(analysis, dict):
        raise ValidationError("$.analysis: object required")
    _require_equal(
        analysis.get("retained_prefix_range"),
        {"start": 0, "end": PREFIX_BYTES},
        "$.analysis.retained_prefix_range",
    )
    _require_equal(
        analysis.get("analyzed_ranges"),
        [{"start": 0, "end": ANALYZED_BYTES}],
        "$.analysis.analyzed_ranges",
    )
    _require_equal(
        analysis.get("excluded_ranges"),
        [
            {
                "start": ANALYZED_BYTES,
                "end": PREFIX_BYTES,
                "provenance_id": "SRC-0013",
            }
        ],
        "$.analysis.excluded_ranges",
    )
    _require_equal(
        analysis.get("comparison_kinds"),
        list(COMPARISON_KINDS),
        "$.analysis.comparison_kinds",
    )
    _require_equal(
        analysis.get("candidate_predicates"),
        list(EXPECTED_PREDICATES),
        "$.analysis.candidate_predicates",
    )
    _require_equal(
        analysis.get("scientific_outcome_rules"),
        EXPECTED_OUTCOME_RULES,
        "$.analysis.scientific_outcome_rules",
    )
    _require_equal(
        analysis.get("physical_meaning_may_be_assigned"),
        False,
        "$.analysis.physical_meaning_may_be_assigned",
    )
    _require_equal(
        analysis.get("compatibility_may_be_claimed"),
        False,
        "$.analysis.compatibility_may_be_claimed",
    )
    _require_equal(
        analysis.get("companion_bytes_analyzed"),
        False,
        "$.analysis.companion_bytes_analyzed",
    )

    conditions = tuple(plan["conditions"])
    samples = tuple(plan.get("samples", ()))
    if len(samples) != 36:
        raise ValidationError("$.samples: expected exactly 36 samples")
    _require_equal(
        [row["launch_ordinal"] for row in samples],
        list(range(1, 37)),
        "$.samples launch order",
    )
    ids: set[str] = set()
    paths: set[str] = set()
    ordinals: set[int] = set()
    by_block: dict[int, list[dict[str, Any]]] = {}
    for row in samples:
        condition = row["condition_id"]
        expected_id = f"M4-{condition}-{row['replica']:02d}"
        _require_equal(row["sample_id"], expected_id, "$.samples[].sample_id")
        _require_equal(row["replica"], row["block"], "$.samples[].replica")
        _require_equal(
            row["launch_ordinal"],
            (row["block"] - 1) * 6 + row["position_in_block"],
            "$.samples[].launch_ordinal",
        )
        base = f"evidence/samples/{expected_id}"
        for key, expected in (
            ("creator_database_path", f"{base}/creator.mdb"),
            ("reopen_database_path", f"{base}/reopen.mdb"),
            ("record_path", f"{base}/record.json"),
        ):
            _require_equal(row[key], expected, f"{expected_id}.{key}")
            if row[key] in paths:
                raise ValidationError(f"$.samples: duplicate declared path {row[key]!r}")
            paths.add(row[key])
        ids.add(expected_id)
        ordinals.add(row["launch_ordinal"])
        by_block.setdefault(row["block"], []).append(row)
    if len(ids) != 36 or ordinals != set(range(1, 37)):
        raise ValidationError("$.samples: IDs or launch ordinals are not complete")
    condition_ids = [row["condition_id"] for row in conditions]
    for block in range(1, 7):
        rows = sorted(
            by_block.get(block, []), key=lambda item: item["position_in_block"]
        )
        observed = [item["condition_id"] for item in rows]
        expected = condition_ids[block - 1 :] + condition_ids[: block - 1]
        _require_equal(observed, expected, f"$.samples block {block} cyclic schedule")

    within = 6 * 2 * (6 * 5 // 2)
    version = (3 * 2 // 2) * 2 * 6 * 2
    encryption = 3 * 6 * 2
    comparisons = 36 + within + version + encryption
    _require_equal(
        comparisons,
        EXPECTED_BOUNDS["max_comparisons"],
        "$.bounds.max_comparisons",
    )
    _require_equal(
        comparisons * ANALYZED_BYTES * 2,
        EXPECTED_BOUNDS["max_comparison_byte_visits"],
        "$.bounds.max_comparison_byte_visits",
    )

    conditions_by_id = {row["condition_id"]: row for row in conditions}
    conditions_by_factor = {
        (row["version_option"], row["encryption_option"]): row["condition_id"]
        for row in conditions
    }
    expected_factors = {
        (version_option, encryption_option)
        for version_option in VERSION_OPTIONS
        for encryption_option in ENCRYPTION_OPTIONS
    }
    if set(conditions_by_factor) != expected_factors:
        raise ValidationError("$.conditions: factorial projection is incomplete")
    return CheckedPlan(
        document=plan,
        samples=samples,
        samples_by_id=MappingProxyType({row["sample_id"]: row for row in samples}),
        conditions=conditions,
        conditions_by_id=MappingProxyType(conditions_by_id),
        conditions_by_factor=MappingProxyType(conditions_by_factor),
        bounds=MappingProxyType(dict(EXPECTED_BOUNDS)),
        candidate_predicates=EXPECTED_PREDICATES,
        scientific_outcome_rules=MappingProxyType(dict(EXPECTED_OUTCOME_RULES)),
    )
