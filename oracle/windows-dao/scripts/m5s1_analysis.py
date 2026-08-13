#!/usr/bin/env python3
"""Bounded set-reference analysis for the separately preregistered M5 successor."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Iterable

from m5s1_spec import (
    ANALYZED_BYTES,
    M4_EXPERIMENT_ID,
    M4_MANIFEST_SHA256,
    PREFIX_BYTES,
    CheckedSuccessorPlan,
)
from protocol_validation import ValidationError, canonical_json_bytes

M4_CONDITIONS = ("V20-U", "V20-E", "V30-U", "V30-E", "V40-U", "V40-E")
M4_PHASES = ("creator", "reopen")
M4_REPLICAS = 6
COMPACT_REPLICAS = 3
MAX_M4_OBSERVATIONS = len(M4_CONDITIONS) * len(M4_PHASES) * M4_REPLICAS
MAX_COMPACT_OBSERVATIONS = 36 * COMPACT_REPLICAS


@dataclass(frozen=True)
class M4PrefixObservation:
    """One independently validated M4 retained-prefix observation."""

    condition_id: str
    replica: int
    phase: str
    prefix: bytes


@dataclass(frozen=True)
class CompactPrefixObservation:
    """One primary compacted-database prefix from a successor sample."""

    condition_id: str
    replica: int
    prefix: bytes


def _bounded_list(rows: Iterable[object], maximum: int, label: str) -> list[object]:
    retained = list(islice(iter(rows), maximum + 1))
    if len(retained) > maximum:
        raise ValidationError(f"{label}: exceeds the checked observation ceiling")
    return retained


def _validate_prefix(prefix: object, label: str) -> bytes:
    if not isinstance(prefix, bytes) or len(prefix) != PREFIX_BYTES:
        raise ValidationError(f"{label}: exact {PREFIX_BYTES}-byte prefix required")
    return prefix


def _index_m4(
    rows: Iterable[M4PrefixObservation],
) -> dict[tuple[str, int, str], bytes]:
    indexed: dict[tuple[str, int, str], bytes] = {}
    for row in _bounded_list(rows, MAX_M4_OBSERVATIONS, "M4 observations"):
        if not isinstance(row, M4PrefixObservation):
            raise ValidationError("M4 observations: typed observation required")
        if row.condition_id not in M4_CONDITIONS:
            raise ValidationError(f"M4 observations: unknown condition {row.condition_id}")
        if isinstance(row.replica, bool) or row.replica not in range(1, M4_REPLICAS + 1):
            raise ValidationError("M4 observations: replica outside 1..6")
        if row.phase not in M4_PHASES:
            raise ValidationError(f"M4 observations: unknown phase {row.phase}")
        key = (row.condition_id, row.replica, row.phase)
        if key in indexed:
            raise ValidationError(f"M4 observations: duplicate identity {key}")
        indexed[key] = _validate_prefix(row.prefix, f"M4 observation {key}")
    expected = {
        (condition, replica, phase)
        for condition in M4_CONDITIONS
        for replica in range(1, M4_REPLICAS + 1)
        for phase in M4_PHASES
    }
    if set(indexed) != expected:
        raise ValidationError("M4 observations: incomplete exact identity set")
    return indexed


def _index_compact(
    plan: CheckedSuccessorPlan,
    rows: Iterable[CompactPrefixObservation],
) -> dict[tuple[str, int], bytes]:
    indexed: dict[tuple[str, int], bytes] = {}
    allowed = set(plan.condition_ids)
    for row in _bounded_list(
        rows, MAX_COMPACT_OBSERVATIONS, "compact observations"
    ):
        if not isinstance(row, CompactPrefixObservation):
            raise ValidationError("compact observations: typed observation required")
        if row.condition_id not in allowed:
            raise ValidationError(
                f"compact observations: unknown condition {row.condition_id}"
            )
        if isinstance(row.replica, bool) or row.replica not in range(
            1, COMPACT_REPLICAS + 1
        ):
            raise ValidationError("compact observations: replica outside 1..3")
        key = (row.condition_id, row.replica)
        if key in indexed:
            raise ValidationError(f"compact observations: duplicate identity {key}")
        indexed[key] = _validate_prefix(row.prefix, f"compact observation {key}")
    expected = {
        (condition, replica)
        for condition in plan.condition_ids
        for replica in range(1, COMPACT_REPLICAS + 1)
    }
    if set(indexed) != expected:
        raise ValidationError("compact observations: incomplete exact identity set")
    return indexed


def _reference_sets(
    indexed: dict[tuple[str, int, str], bytes],
) -> dict[str, tuple[frozenset[int], ...]]:
    references: dict[str, tuple[frozenset[int], ...]] = {}
    for condition in M4_CONDITIONS:
        prefixes = tuple(
            indexed[(condition, replica, phase)]
            for replica in range(1, M4_REPLICAS + 1)
            for phase in M4_PHASES
        )
        references[condition] = tuple(
            frozenset(prefix[offset] for prefix in prefixes)
            for offset in range(ANALYZED_BYTES)
        )
    return references


def build_analysis(
    plan: CheckedSuccessorPlan,
    m4_observations: Iterable[M4PrefixObservation],
    compact_observations: Iterable[CompactPrefixObservation],
) -> dict[str, object]:
    """Build the deterministic bounded membership report from complete inputs."""
    m4 = _index_m4(m4_observations)
    compact = _index_compact(plan, compact_observations)
    references = _reference_sets(m4)
    histogram: dict[str, int] = {}
    novel_offsets: list[dict[str, object]] = []
    novel_occurrences = 0

    for condition, matched_m4 in zip(
        plan.condition_ids, plan.matched_m4_conditions, strict=True
    ):
        reference = references[matched_m4]
        prefixes = tuple(
            compact[(condition, replica)]
            for replica in range(1, COMPACT_REPLICAS + 1)
        )
        for offset in range(ANALYZED_BYTES):
            allowed = reference[offset]
            cardinality = str(len(allowed))
            histogram[cardinality] = histogram.get(cardinality, 0) + 1
            novel = [
                (replica, prefix[offset])
                for replica, prefix in enumerate(prefixes, start=1)
                if prefix[offset] not in allowed
            ]
            if novel:
                novel_occurrences += len(novel)
                novel_offsets.append(
                    {
                        "condition_id": condition,
                        "absolute_offset": offset,
                        "occurrences": [
                            {"replica": replica, "value": value}
                            for replica, value in novel
                        ],
                    }
                )

    outcome = (
        "reference_sets_contain_all_compact_observations"
        if not novel_offsets
        else "compact_observations_extend_reference_sets"
    )
    report: dict[str, object] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_m5_successor_analysis",
        "experiment_id": plan.document["experiment_id"],
        "m4_binding": {
            "experiment_id": M4_EXPERIMENT_ID,
            "bundle_manifest_sha256": M4_MANIFEST_SHA256,
        },
        "analyzed_range": {"start": 0, "end": ANALYZED_BYTES},
        "reference_set_cardinality_histogram": dict(sorted(histogram.items())),
        "novel_value_condition_offsets": novel_offsets,
        "novel_value_occurrence_count": novel_occurrences,
        "scientific_outcome": outcome,
        "physical_meaning_assigned": False,
        "compatibility_claimed": False,
    }
    maximum = plan.document["bounds"]["max_analysis_report_bytes"]
    if len(canonical_json_bytes(report)) > maximum:
        raise ValidationError("M5 successor analysis report exceeds its byte ceiling")
    return report
