#!/usr/bin/env python3
"""Pure, bounded file-prefix analysis for the checked DAO M4 campaign."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Sequence

from m4_spec import (
    ANALYZED_BYTES,
    COMPARISON_KINDS,
    CONDITION_IDS,
    ENCRYPTION_OPTIONS,
    EXPECTED_BYTE_VISITS,
    EXPECTED_COMPARISONS,
    PHASES,
    PREFIX_BYTES,
    VERSION_OPTIONS,
    compile_checked_plan,
)
from protocol_validation import ValidationError


@dataclass(frozen=True)
class Observation:
    sample_id: str
    condition_id: str
    replica: int
    phase_id: str
    prefix: bytes


@dataclass(frozen=True)
class CheckedInputs:
    plan_samples: tuple[dict[str, Any], ...]
    conditions: dict[str, dict[str, Any]]
    by_factor: dict[tuple[str, str], str]
    observations: dict[tuple[str, str], Observation]
    samples_by_condition_replica: dict[tuple[str, int], str]
    max_comparisons: int
    max_byte_visits: int
    max_candidate_sets: int
    max_report_bytes: int
    candidate_predicates: tuple[dict[str, Any], ...]
    scientific_outcome_rules: Mapping[str, Any]


def canonical_analysis_bytes(document: dict[str, Any]) -> bytes:
    """Serialize an analysis result as deterministic canonical UTF-8 JSON."""
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label}: integer required")
    return value


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label}: object required")
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _expected_creation(condition: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "DBEngine.CreateDatabase",
        "version_option": condition["version_option"],
        "version_api_value": condition["version_api_value"],
        "encryption_option": condition["encryption_option"],
        "encryption_api_value": condition["encryption_api_value"],
        "create_option_value": condition["create_option_value"],
        "compact_database_used": False,
    }


def _check_record_phase(
    record: dict[str, Any],
    sample: dict[str, Any],
    condition: dict[str, Any],
    phase_id: str,
    prefix_paths: set[str],
) -> tuple[str, str]:
    phases = _dict(record.get("phases"), f"{sample['sample_id']}.phases")
    if set(phases) != set(PHASES):
        raise ValidationError(f"{sample['sample_id']}: phase inventory differs")
    phase = _dict(phases.get(phase_id), f"{sample['sample_id']}.{phase_id}")
    dao = _dict(
        phase.get("dao_observations_while_open"),
        f"{sample['sample_id']}.{phase_id}.dao_observations",
    )
    post_close = _dict(
        phase.get("post_close_file_observations"),
        f"{sample['sample_id']}.{phase_id}.post_close",
    )
    expected_ordinal = 1 if phase_id == "creator" else 2
    expected_database = sample[f"{phase_id}_database_path"]
    if (
        phase.get("phase_id") != phase_id
        or phase.get("phase_ordinal") != expected_ordinal
        or post_close.get("database_path") != expected_database
        or dao.get("dao_version") != condition["expected_dao_version"]
        or post_close.get("prefix_bytes") != PREFIX_BYTES
        or phase.get("status") != "pass"
    ):
        raise ValidationError(
            f"{sample['sample_id']}: {phase_id} projection differs"
        )
    prefix_path = post_close.get("prefix_path")
    prefix_sha256 = post_close.get("prefix_sha256")
    if (
        not isinstance(prefix_path, str)
        or not prefix_path
        or prefix_path in prefix_paths
        or not isinstance(prefix_sha256, str)
        or len(prefix_sha256) != 64
    ):
        raise ValidationError(
            f"{sample['sample_id']}: {phase_id} prefix reference differs"
        )
    prefix_paths.add(prefix_path)
    return prefix_path, prefix_sha256


def _check_inputs(
    plan: dict[str, Any],
    sample_records: Sequence[dict[str, Any]],
    prefixes: Mapping[str, bytes],
) -> CheckedInputs:
    checked_plan = compile_checked_plan(plan)
    conditions = dict(checked_plan.conditions_by_id)
    by_factor = dict(checked_plan.conditions_by_factor)
    plan_samples = checked_plan.samples
    if not isinstance(sample_records, Sequence) or isinstance(
        sample_records, (str, bytes, bytearray)
    ):
        raise ValidationError("M4 sample records must be a sequence")
    if len(sample_records) != 36:
        raise ValidationError("M4 analysis requires exactly 36 sample records")
    records: dict[str, dict[str, Any]] = {}
    for index, raw_record in enumerate(sample_records):
        record = _dict(raw_record, f"sample_records[{index}]")
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in records:
            raise ValidationError("M4 sample record identity is missing or duplicated")
        records[sample_id] = record
    if not isinstance(prefixes, Mapping) or len(prefixes) != 72:
        raise ValidationError("M4 analysis requires exactly 72 retained prefixes")

    observations: dict[tuple[str, str], Observation] = {}
    samples_by_condition_replica: dict[tuple[str, int], str] = {}
    prefix_paths: set[str] = set()
    sample_ids: set[str] = set()
    total_prefix_bytes = 0
    for launch_index, sample in enumerate(plan_samples, start=1):
        sample_id = sample.get("sample_id")
        condition_id = sample.get("condition_id")
        replica = _integer(sample.get("replica"), f"{sample_id}.replica")
        if (
            not isinstance(sample_id, str)
            or sample_id in sample_ids
            or condition_id not in conditions
            or replica < 1
            or replica > 6
            or sample.get("launch_ordinal") != launch_index
        ):
            raise ValidationError("M4 plan sample projection is incomplete or duplicated")
        sample_ids.add(sample_id)
        cohort_key = (condition_id, replica)
        if cohort_key in samples_by_condition_replica:
            raise ValidationError("M4 condition/replica projection is duplicated")
        samples_by_condition_replica[cohort_key] = sample_id
        record = records.get(sample_id)
        if record is None:
            raise ValidationError(f"{sample_id}: sample record is missing")
        for key in (
            "condition_id",
            "replica",
            "block",
            "position_in_block",
            "launch_ordinal",
        ):
            if record.get(key) != sample.get(key):
                raise ValidationError(f"{sample_id}: record {key} projection differs")
        condition = conditions[condition_id]
        if (
            record.get("execution_status") != "pass"
            or record.get("creation") != _expected_creation(condition)
        ):
            raise ValidationError(f"{sample_id}: checked record projection differs")
        for phase_id in PHASES:
            prefix_path, expected_hash = _check_record_phase(
                record, sample, condition, phase_id, prefix_paths
            )
            prefix = prefixes.get(prefix_path)
            if not isinstance(prefix, bytes) or len(prefix) != PREFIX_BYTES:
                raise ValidationError(
                    f"{sample_id}: {phase_id} prefix is missing or not 2048 bytes"
                )
            if _sha256(prefix) != expected_hash:
                raise ValidationError(f"{sample_id}: {phase_id} prefix hash differs")
            total_prefix_bytes += len(prefix)
            observations[(sample_id, phase_id)] = Observation(
                sample_id=sample_id,
                condition_id=condition_id,
                replica=replica,
                phase_id=phase_id,
                prefix=prefix,
            )
    if set(records) != sample_ids:
        raise ValidationError("M4 sample records contain an unplanned sample")
    if set(prefixes) != prefix_paths:
        raise ValidationError("M4 retained prefixes contain missing or unreferenced paths")
    if (
        len(samples_by_condition_replica) != 36
        or total_prefix_bytes != 72 * PREFIX_BYTES
    ):
        raise ValidationError("M4 factorial/prefix inventory is incomplete")
    return CheckedInputs(
        plan_samples=plan_samples,
        conditions=conditions,
        by_factor=by_factor,
        observations=observations,
        samples_by_condition_replica=samples_by_condition_replica,
        max_comparisons=checked_plan.bounds["max_comparisons"],
        max_byte_visits=checked_plan.bounds["max_comparison_byte_visits"],
        max_candidate_sets=checked_plan.bounds["max_candidate_sets"],
        max_report_bytes=checked_plan.bounds["max_analysis_report_bytes"],
        candidate_predicates=checked_plan.candidate_predicates,
        scientific_outcome_rules=checked_plan.scientific_outcome_rules,
    )


def _phase_ref(observation: Observation) -> dict[str, str]:
    return {
        "sample_id": observation.sample_id,
        "phase_id": observation.phase_id,
    }


def _build_comparisons(
    checked: CheckedInputs,
) -> tuple[list[dict[str, Any]], list[int], list[int], list[int]]:
    comparisons_result: list[dict[str, Any]] = []
    version_occurrences = [0] * ANALYZED_BYTES
    v30_encryption_occurrences = [0] * ANALYZED_BYTES
    all_encryption_occurrences = [0] * ANALYZED_BYTES
    byte_visits = 0

    def add(kind: str, left: Observation, right: Observation) -> None:
        nonlocal byte_visits
        if len(comparisons_result) >= checked.max_comparisons:
            raise ValidationError("M4 comparison count exceeded its ceiling")
        next_visits = byte_visits + (2 * ANALYZED_BYTES)
        if next_visits > checked.max_byte_visits:
            raise ValidationError("M4 comparison byte visits exceeded their ceiling")
        differing = [
            offset
            for offset in range(ANALYZED_BYTES)
            if left.prefix[offset] != right.prefix[offset]
        ]
        comparison = {
            "comparison_id": f"M4-CMP-{len(comparisons_result) + 1:03d}",
            "kind": kind,
            "left": _phase_ref(left),
            "right": _phase_ref(right),
            "differing_offsets": differing,
        }
        comparisons_result.append(comparison)
        byte_visits = next_visits
        if kind == "matched_version":
            for offset in differing:
                version_occurrences[offset] += 1
        elif kind == "matched_encryption":
            version = checked.conditions[left.condition_id]["version_option"]
            for offset in differing:
                all_encryption_occurrences[offset] += 1
                if version == "dbVersion30":
                    v30_encryption_occurrences[offset] += 1

    for sample in checked.plan_samples:
        sample_id = sample["sample_id"]
        add(
            "paired_phase",
            checked.observations[(sample_id, "creator")],
            checked.observations[(sample_id, "reopen")],
        )
    for condition_id in CONDITION_IDS:
        for phase_id in PHASES:
            for left_replica, right_replica in combinations(range(1, 7), 2):
                left_id = checked.samples_by_condition_replica[
                    (condition_id, left_replica)
                ]
                right_id = checked.samples_by_condition_replica[
                    (condition_id, right_replica)
                ]
                add(
                    "within_condition",
                    checked.observations[(left_id, phase_id)],
                    checked.observations[(right_id, phase_id)],
                )
    for encryption in ENCRYPTION_OPTIONS:
        for replica in range(1, 7):
            for phase_id in PHASES:
                for left_version, right_version in combinations(VERSION_OPTIONS, 2):
                    left_condition = checked.by_factor[(left_version, encryption)]
                    right_condition = checked.by_factor[(right_version, encryption)]
                    left_id = checked.samples_by_condition_replica[
                        (left_condition, replica)
                    ]
                    right_id = checked.samples_by_condition_replica[
                        (right_condition, replica)
                    ]
                    add(
                        "matched_version",
                        checked.observations[(left_id, phase_id)],
                        checked.observations[(right_id, phase_id)],
                    )
    for version in VERSION_OPTIONS:
        left_condition = checked.by_factor[(version, "omitted")]
        right_condition = checked.by_factor[(version, "dbEncrypt")]
        for replica in range(1, 7):
            for phase_id in PHASES:
                left_id = checked.samples_by_condition_replica[
                    (left_condition, replica)
                ]
                right_id = checked.samples_by_condition_replica[
                    (right_condition, replica)
                ]
                add(
                    "matched_encryption",
                    checked.observations[(left_id, phase_id)],
                    checked.observations[(right_id, phase_id)],
                )
    if (
        len(comparisons_result) != EXPECTED_COMPARISONS
        or byte_visits != EXPECTED_BYTE_VISITS
    ):
        raise ValidationError("M4 comparison topology is incomplete")
    return (
        comparisons_result,
        version_occurrences,
        v30_encryption_occurrences,
        all_encryption_occurrences,
    )


def _stable_condition_value(
    checked: CheckedInputs, condition_id: str, offset: int
) -> int | None:
    values = {
        checked.observations[
            (
                checked.samples_by_condition_replica[(condition_id, replica)],
                phase_id,
            )
        ].prefix[offset]
        for replica in range(1, 7)
        for phase_id in PHASES
    }
    return values.pop() if len(values) == 1 else None


def _candidate_offsets(
    checked: CheckedInputs,
) -> tuple[list[int], list[int], list[int]]:
    version_offsets: list[int] = []
    v30_encryption_offsets: list[int] = []
    all_encryption_offsets: list[int] = []
    for offset in range(ANALYZED_BYTES):
        stable = {
            condition_id: _stable_condition_value(
                checked, condition_id, offset
            )
            for condition_id in CONDITION_IDS
        }
        v30_unencrypted = stable[
            checked.by_factor[("dbVersion30", "omitted")]
        ]
        v30_encrypted = stable[
            checked.by_factor[("dbVersion30", "dbEncrypt")]
        ]
        if (
            v30_unencrypted is not None
            and v30_encrypted is not None
            and v30_unencrypted != v30_encrypted
        ):
            v30_encryption_offsets.append(offset)
        if any(value is None for value in stable.values()):
            continue
        values = {
            (version, encryption): stable[checked.by_factor[(version, encryption)]]
            for version in VERSION_OPTIONS
            for encryption in ENCRYPTION_OPTIONS
        }
        unencrypted = {
            version: values[(version, "omitted")] for version in VERSION_OPTIONS
        }
        if (
            all(
                values[(version, "omitted")] == values[(version, "dbEncrypt")]
                for version in VERSION_OPTIONS
            )
            and unencrypted["dbVersion30"] != unencrypted["dbVersion20"]
            and unencrypted["dbVersion30"] != unencrypted["dbVersion40"]
        ):
            version_offsets.append(offset)
        effects = [
            values[(version, "omitted")] ^ values[(version, "dbEncrypt")]
            for version in VERSION_OPTIONS
        ]
        if effects[0] != 0 and effects[0] == effects[1] == effects[2]:
            all_encryption_offsets.append(offset)
    return version_offsets, v30_encryption_offsets, all_encryption_offsets


def _candidate_set(
    declaration: dict[str, Any],
    offsets: list[int],
    occurrences: list[int],
) -> dict[str, Any]:
    return {
        "candidate_set_id": declaration["candidate_set_id"],
        "factor": declaration["factor"],
        "phase_id": "paired",
        "absolute_offsets": offsets,
        "comparison_occurrences": [
            {"offset": offset, "occurrences": occurrences[offset]}
            for offset in offsets
        ],
    }


def build_analysis(
    plan: dict[str, Any],
    sample_records: Sequence[dict[str, Any]],
    prefixes: Mapping[str, bytes],
) -> dict[str, Any]:
    """Build the exact deterministic M4 comparison and candidate result."""
    checked = _check_inputs(plan, sample_records, prefixes)
    comparisons_result, version_counts, v30_counts, all_encryption_counts = (
        _build_comparisons(checked)
    )
    version, v30_encryption, all_encryption = _candidate_offsets(checked)
    candidate_results = {
        (
            "V30_UNENCRYPTED_DIFFERS_FROM_V20_AND_V40_UNENCRYPTED_"
            "AND_ENCRYPTION_PAIRS_EQUAL_AT_ALL_VERSIONS"
        ): (version, version_counts),
        "V30_UNENCRYPTED_DIFFERS_FROM_V30_ENCRYPTED": (
            v30_encryption,
            v30_counts,
        ),
        "SAME_NONZERO_XOR_ENCRYPTION_EFFECT_AT_ALL_VERSIONS": (
            all_encryption,
            all_encryption_counts,
        ),
    }
    candidate_sets = []
    nonempty_ids: set[str] = set()
    for declaration in checked.candidate_predicates:
        offsets, occurrences = candidate_results[declaration["predicate"]]
        if offsets:
            candidate_sets.append(_candidate_set(declaration, offsets, occurrences))
            nonempty_ids.add(declaration["candidate_set_id"])
    if len(candidate_sets) > checked.max_candidate_sets:
        raise ValidationError("M4 candidate sets exceeded their ceiling")
    required_nonempty = set(
        checked.scientific_outcome_rules[
            "candidate_offsets_observed_requires_all_nonempty"
        ]
    )
    if required_nonempty.issubset(nonempty_ids):
        scientific_outcome = "candidate_offsets_observed"
    elif candidate_sets:
        scientific_outcome = "inconclusive"
    else:
        scientific_outcome = "no_candidates_observed"
    result = {
        "bounds": {
            "retained_prefix_range": {"start": 0, "end": PREFIX_BYTES},
            "analyzed_ranges": [{"start": 0, "end": ANALYZED_BYTES}],
            "excluded_ranges": [{"start": ANALYZED_BYTES, "end": PREFIX_BYTES}],
            "max_analyzed_offsets": ANALYZED_BYTES,
            "max_comparisons": EXPECTED_COMPARISONS,
        },
        "comparisons": comparisons_result,
        "candidate_sets": candidate_sets,
        "excluded_region_analyzed": False,
        "physical_meaning_assigned": False,
        "compatibility_claimed": False,
        "execution_status": "pass",
        "scientific_outcome": scientific_outcome,
    }
    if len(canonical_analysis_bytes(result)) > checked.max_report_bytes:
        raise ValidationError("M4 analysis result exceeded its byte ceiling")
    return result
