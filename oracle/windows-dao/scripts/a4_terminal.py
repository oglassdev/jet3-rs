#!/usr/bin/env python3
"""Contract-directed frozen results for A4 derivation terminals."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from protocol_validation import ValidationError

from a4_model import A4AnalysisError, WorkLedger
from a4_measurements import PredicateMeasurement
from a4_spec import (
    PREDICATE_CONTRACTS,
    canonical_json_bytes,
    sha256_hex,
    validate_failure_count,
)


def _candidate_hash(candidates: Sequence[Mapping[str, Any]]) -> str:
    return sha256_hex(canonical_json_bytes(list(candidates)))


def not_applicable_result() -> dict[str, Any]:
    """Return the one schema shape for a downstream inapplicable slot."""
    candidates: list[dict[str, Any]] = []
    return {
        "status": "not_applicable",
        "predicate_measured_survivor_count": 0,
        "derivation_survivor_count": 0,
        "terminal_predicate_id": None,
        "terminal_payload_kind": None,
        "terminal_candidate_stage": None,
        "candidates": candidates,
        "terminal_evidence": None,
        "canonical_candidates_sha256": _candidate_hash(candidates),
    }


def decisive_result(
    candidate: Mapping[str, Any], ledger: WorkLedger
) -> dict[str, Any]:
    """Retain and charge one final-stage decisive candidate."""
    candidates = [dict(candidate)]
    ledger.charge_candidate_documents(candidates)
    return {
        "status": "model",
        "predicate_measured_survivor_count": 1,
        "derivation_survivor_count": 1,
        "terminal_predicate_id": None,
        "terminal_payload_kind": None,
        "terminal_candidate_stage": None,
        "candidates": candidates,
        "terminal_evidence": None,
        "canonical_candidates_sha256": _candidate_hash(candidates),
    }


@dataclass(frozen=True)
class DerivationTerminal:
    """A registered scientific terminal plus all already-completed slots."""

    predicate_id: str
    layers: Mapping[str, Any]
    h4_occurrence_evidence: Mapping[str, object] | None = None
    measurements: tuple[PredicateMeasurement, ...] = ()

    def __post_init__(self) -> None:
        contract = PREDICATE_CONTRACTS.get(self.predicate_id)
        if contract is None or contract["terminal_payload_schema"] == "none":
            raise ValidationError(
                f"{self.predicate_id}: is not a registered derivation terminal"
            )
        object.__setattr__(self, "layers", MappingProxyType(dict(self.layers)))


def terminal_result(
    error: A4AnalysisError,
    ledger: WorkLedger,
    *,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    terminal_evidence: Mapping[str, Any] | None = None,
    per_replica_counts: Sequence[int] | None = None,
    candidate_stage: str | None = None,
) -> dict[str, Any]:
    """Project one registered terminal solely through its predicate contract."""
    contract = PREDICATE_CONTRACTS.get(error.predicate_id)
    if contract is None:
        raise error
    payload_kind = contract["terminal_payload_schema"]
    if payload_kind == "none" or contract["scope"] == "campaign":
        raise error
    measured = error.survivor_count
    validate_failure_count(
        error.predicate_id, measured, per_replica_counts=per_replica_counts
    )
    retained = sorted(
        (dict(candidate) for candidate in candidates or ()),
        key=lambda candidate: candidate["canonical_candidate_id"],
    )
    evidence = None if terminal_evidence is None else dict(terminal_evidence)

    if payload_kind == "candidate_set":
        if len(retained) != measured or evidence is not None:
            raise ValidationError(
                f"{error.predicate_id}: candidate-set terminal payload differs from its count"
            )
    elif payload_kind == "replica_pair":
        if retained or evidence is None:
            raise ValidationError(
                f"{error.predicate_id}: replica-pair terminal payload is incomplete"
            )
        entries = evidence.get("entries")
        if not isinstance(entries, list) or len(entries) != 2 or any(
            not isinstance(entry, Mapping)
            or not isinstance(entry.get("complete_candidate"), Mapping)
            for entry in entries
        ):
            raise ValidationError(
                f"{error.predicate_id}: replica-pair candidates are incomplete"
            )
        ledger.charge_candidate_documents(
            [entry["complete_candidate"] for entry in entries]
        )
    elif payload_kind == "invalid_observation":
        if evidence is None or len(retained) > 1:
            raise ValidationError(
                f"{error.predicate_id}: invalid-observation terminal payload is incomplete"
            )
    elif payload_kind == "grouped_candidate_set":
        if evidence is None or evidence.get("kind") != "operation_groups":
            raise ValidationError(
                f"{error.predicate_id}: grouped terminal payload is incomplete"
            )
        groups = evidence.get("groups")
        if not isinstance(groups, list) or len(groups) != 7:
            raise ValidationError(
                f"{error.predicate_id}: grouped terminal must retain seven groups"
            )
        counts = [group.get("cardinality") for group in groups]
        expected = min(counts) if error.predicate_id.endswith("RECORD-NONE") else max(counts)
        if measured != expected:
            raise ValidationError(
                f"{error.predicate_id}: measured count differs from grouped cardinality"
            )
        retained_ids = {candidate["canonical_candidate_id"] for candidate in retained}
        grouped_ids = {
            candidate_id for group in groups for candidate_id in group.get("candidate_ids", [])
        }
        if retained_ids != grouped_ids or any(
            group.get("cardinality") != len(group.get("candidate_ids", []))
            for group in groups
        ):
            raise ValidationError(
                f"{error.predicate_id}: grouped candidate union is inconsistent"
            )
    else:
        raise ValidationError(
            f"{error.predicate_id}: unknown registered terminal payload {payload_kind!r}"
        )

    ledger.charge_candidate_documents(retained)
    return {
        "status": "no_outcome",
        "predicate_measured_survivor_count": measured,
        "derivation_survivor_count": 0,
        "terminal_predicate_id": error.predicate_id,
        "terminal_payload_kind": payload_kind,
        "terminal_candidate_stage": (
            contract["candidate_stage"]
            if candidate_stage is None
            else candidate_stage
        ),
        "candidates": retained,
        "terminal_evidence": evidence,
        "canonical_candidates_sha256": _candidate_hash(retained),
    }
