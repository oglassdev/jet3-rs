#!/usr/bin/env python3
"""Bounded A4 H2 allocation-row, polarity, and role derivation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from protocol_validation import ValidationError
from a4_layer_h1 import H1Binding, H1ReplicaCandidate
from a4_layer_h2_types import FrozenOwnedRow, MapRow
from a4_measurements import MeasurementRecorder, measure
from a4_model import A4AnalysisError, QualifiedPage, View, WorkLedger, canonical_model_id
from a4_spec import (
    BOUNDS,
    CANDIDATE_GRAMMARS,
    CHECKPOINT_IDS,
    PLAN,
    canonical_json_bytes,
    validate_failure_count,
)
_H2 = CANDIDATE_GRAMMARS["h2"]
_ROW_MASKS = tuple(int(value) for value in _H2["row_masks"])
_POLARITIES = tuple(_H2["type_0_polarities"])
_ASSIGNMENTS = tuple(_H2["locator_role_assignments"])
_ROLES = tuple(PLAN["tables"]["logical_roles"])
_MAX_CANDIDATES = int(BOUNDS["max_candidate_models"])
_MAX_CANDIDATE_BYTES = int(BOUNDS["max_canonical_candidate_bytes"])
_MAX_ROWS = int(BOUNDS["max_inserted_rows_per_replica"])
_PAGE_SIZE = int(BOUNDS["page_size"])
_ROW_DIRECTORY_CAPACITY = (_PAGE_SIZE - 10) // 2
_MAX_COMPLETE_ROWS = (_PAGE_SIZE - 10) // 3
_COVERAGE = PLAN["checkpoint_design"]["transition_coverage"]
_IDLE_PAIRS = tuple(tuple(pair) for pair in PLAN["checkpoint_design"]["idle_pairs"])
_GROW_SEQUENCES = tuple(
    tuple(_COVERAGE[name])
    for name in ("t1_growth", "t3_absolute", "t4_relative")
)
_GROW_ROLES = ("T1", "T3", "T4")
_TRANSITION_KINDS = tuple(_H2["transition_signature"])

@dataclass(frozen=True)
class RowBounds:
    raw_entry: int
    start: int
    end: int

@dataclass(frozen=True)
class Directory:
    row_count: int
    rows: tuple[RowBounds, ...]


@dataclass(frozen=True)
class H2ReplicaCandidate:
    replica: int
    row_mask: int
    polarity: str
    owned_in_use_locator_ordinal: int
    available_locator_ordinal: int

    @property
    def model(self) -> dict[str, Any]:
        return {
            "row_mask": self.row_mask,
            "polarity": self.polarity,
            "owned_in_use_locator_ordinal": self.owned_in_use_locator_ordinal,
            "available_locator_ordinal": self.available_locator_ordinal,
        }

    @property
    def canonical_model_id(self) -> str:
        return canonical_model_id("h2_final_role", self.model)

    @property
    def canonical_candidate_id(self) -> str:
        # H2 has no replica-specific binding in its registered candidate shape.
        return self.canonical_model_id

    def document(self) -> dict[str, Any]:
        document = {
            "model_type": "h2_final_role",
            "canonical_candidate_id": self.canonical_candidate_id,
            "model": self.model,
        }
        if len(canonical_json_bytes(document)) > _MAX_CANDIDATE_BYTES:
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND", detail="H2 canonical candidate exceeds its bound"
            )
        return document


class H2Terminal(A4AnalysisError):
    """Registered H2 terminal with its schema-directed frozen payload."""

    def __init__(
        self,
        predicate_id: str,
        survivor_count: int,
        *,
        candidates: Sequence[Mapping[str, Any]] = (),
        terminal_evidence: Mapping[str, Any] | None = None,
        per_replica_counts: Sequence[int] | None = None,
        detail: str | None = None,
    ) -> None:
        validate_failure_count(
            predicate_id,
            survivor_count,
            per_replica_counts=per_replica_counts,
        )
        self.candidates = tuple(dict(candidate) for candidate in candidates)
        self.terminal_evidence = (
            None if terminal_evidence is None else dict(terminal_evidence)
        )
        self.payload_kind = (
            "invalid_observation"
            if predicate_id
            in {
                "A4-H2-ROW-DIRECTORY-INVALID",
                "A4-H2-ROW-FLAGS-INVALID",
                "A4-H2-MAP-TAG-UNSUPPORTED",
            }
            else "replica_pair"
            if terminal_evidence is not None
            else "candidate_set"
        )
        self.candidate_stage = (
            "h2_final_role" if self.payload_kind != "invalid_observation" else None
        )
        super().__init__(predicate_id, survivor_count, detail=detail)


@dataclass(frozen=True)
class _DirectoryFailure:
    checkpoint: str
    page: int
    slot: int
    row_count: int
    raw_entry: int
    reason: str

    def evidence(self, replica: int, input_model_id: str) -> dict[str, Any]:
        return {
            "kind": "row_directory",
            "input_model_id": input_model_id,
            "observation": {
                "replica": replica,
                "checkpoint_id": self.checkpoint,
                "page": self.page,
                "row_count": self.row_count,
                "slot": self.slot,
                "raw_entry_u16le": self.raw_entry,
                "masked_start_8191": self.raw_entry & 8191,
                "masked_start_4095": self.raw_entry & 4095,
                "reason": self.reason,
            },
        }


def _directory(payload: bytes, mask: int) -> tuple[Directory | None, tuple[int, int, str] | None]:
    row_count = int.from_bytes(payload[8:10], "little")
    if row_count > _ROW_DIRECTORY_CAPACITY:
        raise A4AnalysisError(
            "A4-RESOURCE-BOUND", detail="row directory exceeds its bounded entry domain"
        )
    if row_count > _MAX_COMPLETE_ROWS:
        return None, (0, 0, "row_count_exceeds_capacity")
    directory_end = 10 + 2 * row_count
    rows: list[RowBounds] = []
    prior_start = _PAGE_SIZE
    for slot in range(row_count):
        offset = 10 + 2 * slot
        raw = int.from_bytes(payload[offset : offset + 2], "little")
        start = raw & mask
        end = prior_start
        if start < directory_end:
            return None, (slot, raw, "start_below_directory_end")
        if end > _PAGE_SIZE:
            return None, (slot, raw, "end_above_page")
        if start >= end:
            return None, (slot, raw, "start_not_below_end")
        rows.append(RowBounds(raw, start, end))
        prior_start = start
    return Directory(row_count, tuple(rows)), None


def _target_pages(binding: H1Binding) -> tuple[int, ...]:
    if binding.locator_targets is None:
        raise ValueError("H2 requires a complete H1 locator binding")
    return tuple(sorted({target.page for target in binding.locator_targets}))


def _checked_row_counts(
    row_counts: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    if tuple(row_counts) != tuple(CHECKPOINT_IDS):
        raise ValueError("H2 row counts must follow the exact checkpoint order")
    result: dict[str, dict[str, int]] = {}
    for checkpoint in CHECKPOINT_IDS:
        counts = row_counts[checkpoint]
        if set(counts) != set(_ROLES):
            raise ValueError(f"H2 row counts at {checkpoint} have the wrong roles")
        checked: dict[str, int] = {}
        for role in _ROLES:
            value = counts[role]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_ROWS
            ):
                raise ValueError(f"H2 row count for {role} at {checkpoint} is invalid")
            checked[role] = value
        result[checkpoint] = checked
    return result


def _directory_inventory(
    view: View,
    h1: H1ReplicaCandidate,
) -> tuple[
    dict[int, dict[tuple[str, str, int], Directory]],
    dict[int, _DirectoryFailure],
    int,
]:
    directories = {mask: {} for mask in _ROW_MASKS}
    first_failure: dict[int, _DirectoryFailure] = {}
    visited_entries: set[tuple[int, str, int, int]] = set()
    for binding in h1.bindings:
        for checkpoint in binding.checkpoints:
            for page in _target_pages(binding):
                payload = view.page_optional(checkpoint, page)
                if payload is None or payload[0] != 0x01:
                    raise A4AnalysisError(
                        "A4-SNAPSHOT-RECONSTRUCTION",
                        detail="H2 input no longer satisfies its decisive H1 model",
                    )
                row_count = int.from_bytes(payload[8:10], "little")
                for slot in range(min(row_count, _ROW_DIRECTORY_CAPACITY)):
                    visited_entries.add((view.replica, checkpoint, page, slot))
                key = (binding.lifecycle_instance, checkpoint, page)
                for mask in _ROW_MASKS:
                    directory, failure = _directory(payload, mask)
                    if directory is not None:
                        directories[mask][key] = directory
                    elif mask not in first_failure and failure is not None:
                        slot, raw, reason = failure
                        first_failure[mask] = _DirectoryFailure(
                            checkpoint, page, slot, row_count, raw, reason
                        )
    return directories, first_failure, len(visited_entries)


def _complete_masks(
    h1: H1ReplicaCandidate,
    directories: Mapping[int, Mapping[tuple[str, str, int], Directory]],
) -> tuple[int, ...]:
    required = {
        (binding.lifecycle_instance, checkpoint, page)
        for binding in h1.bindings
        for checkpoint in binding.checkpoints
        for page in _target_pages(binding)
    }
    representatives: list[int] = []
    seen_bounds: set[tuple[Any, ...]] = set()
    for mask in _ROW_MASKS:
        if set(directories[mask]) != required:
            continue
        signature = tuple(
            (
                key,
                tuple((row.start, row.end) for row in directories[mask][key].rows),
            )
            for key in sorted(required)
        )
        if signature not in seen_bounds:
            seen_bounds.add(signature)
            representatives.append(mask)
    return tuple(representatives)


def _first_flag_failure(
    view: View,
    h1: H1ReplicaCandidate,
    directories: Mapping[tuple[str, str, int], Directory],
) -> dict[str, Any] | None:
    for binding in h1.bindings:
        if binding.locator_targets is None:
            raise ValueError("H2 requires H1 locator targets")
        for checkpoint in binding.checkpoints:
            for target in binding.locator_targets:
                directory = directories[(binding.lifecycle_instance, checkpoint, target.page)]
                raw = directory.rows[target.row].raw_entry
                deleted = bool(raw & 0x8000)
                overflow = bool(raw & 0x4000)
                if deleted or overflow:
                    return {
                        "kind": "row_flags",
                        "input_model_id": h1.canonical_candidate_id,
                        "observation": {
                            "replica": view.replica,
                            "checkpoint_id": checkpoint,
                            "page": target.page,
                            "slot": target.row,
                            "raw_entry_u16le": raw,
                            "deleted_flag_0x8000": deleted,
                            "overflow_flag_0x4000": overflow,
                        },
                    }
    return None


def _map_row(payload: bytes, bounds: RowBounds) -> MapRow:
    row = payload[bounds.start : bounds.end]
    if not row:
        raise ValueError("H2 complete row unexpectedly has zero length")
    if row[0] == 0:
        if len(row) < 5:
            return MapRow(0, row, None, None)
        return MapRow(0, row, int.from_bytes(row[1:5], "little"), row[5:])
    return MapRow(row[0], row, None, None)


def _rows_for_mask(
    view: View,
    h1: H1ReplicaCandidate,
    mask: int,
    directories: Mapping[tuple[str, str, int], Directory],
    ledger: WorkLedger,
) -> tuple[dict[tuple[str, str, int], MapRow], dict[str, Any] | None]:
    rows: dict[tuple[str, str, int], MapRow] = {}
    first_invalid: dict[str, Any] | None = None
    for binding in h1.bindings:
        if binding.locator_targets is None:
            raise ValueError("H2 requires H1 locator targets")
        for checkpoint in binding.checkpoints:
            for ordinal, target in enumerate(binding.locator_targets):
                payload = view.page(checkpoint, target.page)
                bounds = directories[
                    (binding.lifecycle_instance, checkpoint, target.page)
                ].rows[target.row]
                row = _map_row(payload, bounds)
                rows[(binding.lifecycle_instance, checkpoint, ordinal)] = row
                if view.replica in (1, 2):
                    qualified = QualifiedPage(view.replica, checkpoint, target.page)
                    identity = (target.row, bounds.start, bounds.end)
                    if row.tag == 0 and row.bitmap is not None:
                        ledger.charge_qualified(
                            "type_0_and_tag_05_bitmap_bits", qualified,
                            len(row.bitmap) * 8, discriminator=identity,
                        )
                    elif row.tag == 1 and (len(row.payload) - 1) % 4 == 0:
                        ledger.charge_qualified(
                            "type_1_slots", qualified,
                            (len(row.payload) - 1) // 4, discriminator=identity,
                        )
                reason: str | None = None
                if row.tag not in (0, 1):
                    reason = "unsupported_tag"
                elif row.tag == 0 and (row.base is None or row.bitmap is None):
                    reason = "unsupported_tag"
                elif row.tag == 1 and (len(row.payload) - 1) % 4:
                    reason = "type_1_payload_not_u32_multiple"
                if first_invalid is None and reason is not None:
                    first_invalid = {
                        "kind": "map_tag",
                        "input_model_id": h1.canonical_candidate_id,
                        "observation": {
                            "replica": view.replica,
                            "checkpoint_id": checkpoint,
                            "page": target.page,
                            "slot": target.row,
                            "row_start": bounds.start,
                            "row_end": bounds.end,
                            "tag_byte": row.tag,
                            "reason": reason,
                        },
                    }
    return rows, first_invalid


def _admitted(row: MapRow, polarity: str) -> frozenset[int] | None:
    if row.tag != 0 or row.base is None or row.bitmap is None:
        return None
    selected: set[int] = set()
    for byte_ordinal, byte in enumerate(row.bitmap):
        for bit in range(8):
            is_set = bool(byte & (1 << bit))
            owned = is_set if polarity == "set_bit_owned_in_use" else not is_set
            if owned:
                selected.add(row.base + byte_ordinal * 8 + bit)
    return frozenset(selected)


def decode_frozen_owned_rows(
    view: View,
    h1: H1ReplicaCandidate,
    frozen: H2ReplicaCandidate,
    ledger: WorkLedger,
) -> tuple[FrozenOwnedRow, ...]:
    """Decode H3 input with the frozen H1/H2 choices, without refitting."""
    if view.replica not in (1, 2, 3) or h1.replica != view.replica:
        raise ValueError("frozen H2 decoding requires matching replica input")
    if frozen.replica not in (0, view.replica):
        raise ValueError("frozen H2 model is bound to another replica")
    if (
        frozen.row_mask not in _ROW_MASKS
        or frozen.polarity not in _POLARITIES
        or {frozen.owned_in_use_locator_ordinal, frozen.available_locator_ordinal}
        != {0, 1}
    ):
        raise ValueError("frozen H2 model is outside the closed grammar")
    directories, _failures, _entries = _directory_inventory(view, h1)
    required = {
        (binding.lifecycle_instance, checkpoint, page)
        for binding in h1.bindings
        for checkpoint in binding.checkpoints
        for page in _target_pages(binding)
    }
    selected = directories[frozen.row_mask]
    if set(selected) != required or _first_flag_failure(view, h1, selected) is not None:
        raise A4AnalysisError(
            "A4-SNAPSHOT-RECONSTRUCTION",
            detail="input contradicts the frozen H2 directory/flag model",
        )
    rows, tag_failure = _rows_for_mask(view, h1, frozen.row_mask, selected, ledger)
    if tag_failure is not None:
        raise A4AnalysisError(
            "A4-SNAPSHOT-RECONSTRUCTION",
            detail="input contradicts the frozen H2 map-row model",
        )
    observations: list[FrozenOwnedRow] = []
    allocation_rows = (
        ("owned_in_use", frozen.owned_in_use_locator_ordinal),
        ("available", frozen.available_locator_ordinal),
    )
    for binding in h1.bindings:
        if binding.locator_targets is None:
            raise ValueError("frozen H2 decoding requires H1 locator targets")
        for allocation_role, ordinal in allocation_rows:
            target = binding.locator_targets[ordinal]
            for checkpoint in binding.checkpoints:
                row = rows[(binding.lifecycle_instance, checkpoint, ordinal)]
                admitted = _admitted(row, frozen.polarity)
                references = (
                    tuple(
                        int.from_bytes(row.payload[offset : offset + 4], "little")
                        for offset in range(1, len(row.payload), 4)
                    )
                    if row.tag == 1
                    else None
                )
                span = (
                    (row.base, row.base + len(row.bitmap) * 8)
                    if row.tag == 0
                    and row.base is not None
                    and row.bitmap is not None
                    and row.bitmap
                    else None
                )
                observations.append(
                    FrozenOwnedRow(
                        view.replica,
                        binding.logical_role,
                        binding.lifecycle_instance,
                        allocation_role,
                        checkpoint,
                        target.page,
                        target.row,
                        "type_0" if row.tag == 0 else "type_1",
                        None if admitted is None else tuple(sorted(admitted)),
                        references,
                        span,
                    )
                )
    return tuple(observations)


def _assignment_ordinals(assignment: str) -> tuple[int, int]:
    if assignment == "ordinal_0_owned_ordinal_1_available":
        return 0, 1
    if assignment == "ordinal_1_owned_ordinal_0_available":
        return 1, 0
    raise ValidationError(f"unknown H2 locator-role assignment {assignment!r}")


def _binding_for_role(
    bindings: Sequence[H1Binding], role: str, left: str, right: str
) -> H1Binding | None:
    matches = [
        binding
        for binding in bindings
        if binding.logical_role == role
        and left in binding.checkpoints
        and right in binding.checkpoints
    ]
    if len(matches) > 1:
        raise ValueError("H2 found overlapping lifecycle bindings for one role")
    return matches[0] if matches else None


def _static_fits(
    view: View,
    h1: H1ReplicaCandidate,
    rows: Mapping[tuple[str, str, int], MapRow],
    row_counts: Mapping[str, Mapping[str, int]],
    polarity: str,
    owned_ordinal: int,
    available_ordinal: int,
) -> bool:
    for binding in h1.bindings:
        for checkpoint in binding.checkpoints:
            owned = _admitted(
                rows[(binding.lifecycle_instance, checkpoint, owned_ordinal)], polarity
            )
            available = _admitted(
                rows[(binding.lifecycle_instance, checkpoint, available_ordinal)], polarity
            )
            for admitted in (owned, available):
                if admitted is not None and any(
                    page >= view.page_count(checkpoint) for page in admitted
                ):
                    return False
            if owned is not None and available is not None:
                if not available <= owned:
                    return False
            if (
                row_counts[checkpoint][binding.logical_role] > 0
                and owned is not None
                and not owned
            ):
                return False
    return True


def _transition_charge(
    ledger: WorkLedger,
    replica: int,
    mask: int,
    polarity: str,
    assignment: str,
) -> None:
    for kind in _TRANSITION_KINDS:
        for role in _ROLES:
            for checkpoint in CHECKPOINT_IDS:
                ledger.charge_once(
                    "role_transition_evaluations",
                    (replica, mask, polarity, assignment, kind, role, checkpoint),
                )


def _state(
    rows: Mapping[tuple[str, str, int], MapRow],
    binding: H1Binding,
    checkpoint: str,
    polarity: str,
    owned_ordinal: int,
    available_ordinal: int,
) -> tuple[frozenset[int] | None, frozenset[int] | None]:
    return (
        _admitted(rows[(binding.lifecycle_instance, checkpoint, owned_ordinal)], polarity),
        _admitted(rows[(binding.lifecycle_instance, checkpoint, available_ordinal)], polarity),
    )


def _row_span(row: MapRow) -> range:
    if row.tag != 0 or row.base is None or row.bitmap is None:
        return range(0, 0)
    return range(row.base, row.base + len(row.bitmap) * 8)


def _transitions_fit(
    h1: H1ReplicaCandidate,
    rows: Mapping[tuple[str, str, int], MapRow],
    polarity: str,
    owned_ordinal: int,
    available_ordinal: int,
) -> bool:
    for left, right in _IDLE_PAIRS:
        for binding in h1.bindings:
            if left not in binding.checkpoints or right not in binding.checkpoints:
                continue
            for ordinal in (0, 1):
                if rows[(binding.lifecycle_instance, left, ordinal)].payload != rows[
                    (binding.lifecycle_instance, right, ordinal)
                ].payload:
                    return False

    for role, sequence in zip(_GROW_ROLES, _GROW_SEQUENCES):
        for left, right in zip(sequence, sequence[1:]):
            if not (right.startswith(f"{role}_REL_") or right.startswith(f"{role}_ABS_")):
                continue
            binding = _binding_for_role(h1.bindings, role, left, right)
            if binding is None:
                return False
            left_owned, left_available = _state(
                rows, binding, left, polarity, owned_ordinal, available_ordinal
            )
            right_owned, right_available = _state(
                rows, binding, right, polarity, owned_ordinal, available_ordinal
            )
            if None in (left_owned, left_available, right_owned, right_available):
                continue
            if not left_owned <= right_owned:
                return False
            gained = right_owned - left_owned
            if (right_available - left_available) & gained:
                return False

    t1 = _binding_for_role(
        h1.bindings, "T1", "T1_REL_1280", "T1_DELETE_ALL"
    )
    if t1 is None:
        return False
    delete_before = _state(
        rows, t1, "T1_REL_1280", polarity, owned_ordinal, available_ordinal
    )
    delete_after = _state(
        rows, t1, "T1_DELETE_ALL", polarity, owned_ordinal, available_ordinal
    )
    reinsert_after = _state(
        rows, t1, "T1_REINSERT_SAME", polarity, owned_ordinal, available_ordinal
    )
    if None not in delete_before + delete_after:
        before_owned, before_available = delete_before
        after_owned, after_available = delete_after
        if not after_owned <= before_owned or not before_available <= after_available:
            return False
        before_row = rows[(t1.lifecycle_instance, "T1_REL_1280", owned_ordinal)]
        after_row = rows[(t1.lifecycle_instance, "T1_DELETE_ALL", owned_ordinal)]
        span = set(_row_span(before_row)) | set(_row_span(after_row))
        if ((before_owned ^ after_owned) | (before_available ^ after_available)) - span:
            return False
    if None not in delete_after + reinsert_after:
        before_owned, before_available = delete_after
        after_owned, after_available = reinsert_after
        if not before_owned <= after_owned or not after_available <= before_available:
            return False
        before_row = rows[(t1.lifecycle_instance, "T1_DELETE_ALL", owned_ordinal)]
        after_row = rows[(t1.lifecycle_instance, "T1_REINSERT_SAME", owned_ordinal)]
        span = set(_row_span(before_row)) | set(_row_span(after_row))
        if ((before_owned ^ after_owned) | (before_available ^ after_available)) - span:
            return False
    return True


def derive_h2_replica(
    view: View,
    h1: H1ReplicaCandidate,
    table_row_counts: Mapping[str, Mapping[str, int]],
    ledger: WorkLedger,
    measurements: MeasurementRecorder | None = None,
) -> H2ReplicaCandidate:
    """Run H2's seven non-holdout predicates for one derivation replica."""
    if view.replica not in (1, 2) or h1.replica != view.replica:
        raise ValueError("H2 derivation requires matching replica-1/2 H1 input")
    input_count = len((h1,))
    row_counts = _checked_row_counts(table_row_counts)
    directories, directory_failures, entries = _directory_inventory(view, h1)
    masks = _complete_masks(h1, directories)
    ledger.charge(
        "valid_path_row_directory_entries"
        if masks
        else "invalid_path_row_directory_entries",
        entries,
    )
    measure(measurements, "A4-H2-ROW-DIRECTORY-INVALID", input_count, bool(masks), replica=view.replica)
    if not masks:
        failure = next(
            (directory_failures[mask] for mask in _ROW_MASKS if mask in directory_failures),
            None,
        )
        if failure is None:
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail="H2 directory-mask intersection is empty without an observation",
            )
        raise H2Terminal(
            "A4-H2-ROW-DIRECTORY-INVALID",
            1,
            terminal_evidence=failure.evidence(
                view.replica, h1.canonical_candidate_id
            ),
        )
    flag_failure = _first_flag_failure(view, h1, directories[masks[0]])
    measure(measurements, "A4-H2-ROW-FLAGS-INVALID", input_count, flag_failure is None, replica=view.replica)
    if flag_failure is not None:
        raise H2Terminal(
            "A4-H2-ROW-FLAGS-INVALID", 1, terminal_evidence=flag_failure
        )

    row_models: dict[int, dict[tuple[str, str, int], MapRow]] = {}
    tag_failures: dict[int, dict[str, Any]] = {}
    for mask in masks:
        rows, failure = _rows_for_mask(view, h1, mask, directories[mask], ledger)
        if failure is None:
            row_models[mask] = rows
        else:
            tag_failures[mask] = failure
    masks = tuple(mask for mask in masks if mask in row_models)
    measure(measurements, "A4-H2-MAP-TAG-UNSUPPORTED", input_count, bool(masks), replica=view.replica)
    if not masks:
        failure = next(tag_failures[mask] for mask in _ROW_MASKS if mask in tag_failures)
        raise H2Terminal(
            "A4-H2-MAP-TAG-UNSUPPORTED", 1, terminal_evidence=failure
        )

    static: list[tuple[H2ReplicaCandidate, str]] = []
    for mask in masks:
        for polarity in _POLARITIES:
            for assignment in _ASSIGNMENTS:
                owned, available = _assignment_ordinals(assignment)
                if _static_fits(
                    view,
                    h1,
                    row_models[mask],
                    row_counts,
                    polarity,
                    owned,
                    available,
                ):
                    static.append(
                        (
                            H2ReplicaCandidate(
                                view.replica, mask, polarity, owned, available
                            ),
                            assignment,
                        )
                    )
                    if len(static) > _MAX_CANDIDATES:
                        raise A4AnalysisError(
                            "A4-RESOURCE-BOUND",
                            detail="H2 candidate count exceeds its bound",
                        )
    measure(measurements, "A4-H2-ROLE-NONE", len(static), bool(static), replica=view.replica)
    if not static:
        raise H2Terminal("A4-H2-ROLE-NONE", 0)
    measure(measurements, "A4-H2-ROLE-MULTIPLE", len(static), len(static) == 1, replica=view.replica)
    if len(static) > 1:
        raise H2Terminal(
            "A4-H2-ROLE-MULTIPLE",
            len(static),
            candidates=tuple(candidate.document() for candidate, _ in static),
        )

    candidate, assignment = static[0]
    _transition_charge(
        ledger,
        view.replica,
        candidate.row_mask,
        candidate.polarity,
        assignment,
    )
    transitions_fit = _transitions_fit(
        h1,
        row_models[candidate.row_mask],
        candidate.polarity,
        candidate.owned_in_use_locator_ordinal,
        candidate.available_locator_ordinal,
    )
    measure(measurements, "A4-H2-TRANSITION-UNEXPLAINED", len((candidate,)), transitions_fit, replica=view.replica)
    if not transitions_fit:
        raise H2Terminal(
            "A4-H2-TRANSITION-UNEXPLAINED",
            1,
            candidates=(candidate.document(),),
        )
    return candidate


def agree_h2_replicas(
    replica_1: H2ReplicaCandidate, replica_2: H2ReplicaCandidate,
    measurements: MeasurementRecorder | None = None,
) -> H2ReplicaCandidate:
    """Apply H2 replica agreement to the replica-invariant role model."""
    if (replica_1.replica, replica_2.replica) != (1, 2):
        raise ValueError("H2 replica agreement requires replicas 1 then 2")
    model_count = len({replica_1.canonical_model_id, replica_2.canonical_model_id})
    agrees = model_count == 1
    measure(measurements, "A4-H2-REPLICA-DISAGREEMENT", model_count, agrees)
    if not agrees:
        entries = []
        for candidate in (replica_1, replica_2):
            entries.append(
                {
                    "replica": candidate.replica,
                    "canonical_model_id": candidate.canonical_model_id,
                    "canonical_candidate_id": candidate.canonical_candidate_id,
                    "complete_candidate": candidate.document(),
                }
            )
        raise H2Terminal(
            "A4-H2-REPLICA-DISAGREEMENT",
            2,
            terminal_evidence={"kind": "replica_pair", "entries": entries},
            per_replica_counts=(1, 1),
        )
    return H2ReplicaCandidate(
        0,
        replica_1.row_mask,
        replica_1.polarity,
        replica_1.owned_in_use_locator_ordinal,
        replica_1.available_locator_ordinal,
    )
