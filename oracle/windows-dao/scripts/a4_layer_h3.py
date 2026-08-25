#!/usr/bin/env python3
"""Bounded A4 H3 indirect-traversal derivation.

H2 supplies already decoded type-0 owned sets and type-1 slot observations.
This module deliberately does not decode H1/H2 rows again.  It applies the
closed H3 grammar in predicate order, keeps physical observations replica
qualified, and separates reference validity from conversion structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from a4_measurements import MeasurementRecorder, measure
from a4_model import (
    A4AnalysisError,
    QualifiedPage,
    WorkLedger,
    canonical_object_id,
    canonical_model_id,
)
from a4_spec import BOUNDS, CANDIDATE_GRAMMARS, CHECKPOINT_IDS, PLAN


_GRAMMAR = CANDIDATE_GRAMMARS["h3"]
CONVERSIONS = tuple(_GRAMMAR["conversion_candidates"])
BASE_FORMULAS = tuple(_GRAMMAR["base_formulas"])
BITMAP_BITS = (int(BOUNDS["page_size"]) - 4) * 8
MAX_ADMITTED_PAGES = int(BOUNDS["max_qualified_pages_per_submodel"])
_CONVERSION = "structural_type_0_to_type_1_with_nonzero_u32_slots"
_COVERAGE = PLAN["checkpoint_design"]["transition_coverage"]
_IDLE_PAIRS = tuple(
    tuple(pair) for pair in PLAN["checkpoint_design"]["idle_pairs"]
)


def _growth_sequences() -> tuple[tuple[str, ...], ...]:
    sequences: list[tuple[str, ...]] = []
    for value in _COVERAGE.values():
        sequence = tuple(value)
        if len(sequence) < 2:
            continue
        roles = {checkpoint.split("_", 1)[0] for checkpoint in sequence[1:]}
        if (
            len(roles) == 1
            and roles <= set(PLAN["tables"]["logical_roles"])
            and any("_REL_" in checkpoint or "_ABS_" in checkpoint for checkpoint in sequence)
        ):
            sequences.append(sequence)
    return tuple(sequences)


def _operation_checkpoint(marker: str) -> str:
    matches = tuple(
        checkpoint
        for checkpoint, operation in PLAN["tables"]["checkpoint_operations"].items()
        if marker in operation
    )
    if len(matches) != 1:
        raise ValueError(f"A4 plan must declare exactly one {marker!r} checkpoint")
    return matches[0]


_GROW_SEQUENCES = _growth_sequences()
_DELETE_CHECKPOINT = _operation_checkpoint("Delete every")
_REINSERT_CHECKPOINT = _operation_checkpoint("Reinsert")
_DELETE_BEFORE = CHECKPOINT_IDS[CHECKPOINT_IDS.index(_DELETE_CHECKPOINT) - 1]


def _pages(values: Sequence[int], label: str) -> frozenset[int]:
    result: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must contain nonnegative integers")
        result.add(value)
    return frozenset(result)


@dataclass(frozen=True, order=True)
class SlotObservation:
    """One type-1 u32 slot and its referenced tag-05 bitmap, if readable."""

    slot_ordinal: int
    reference: int
    referenced_page_tag: int | None
    set_bits: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if (
            isinstance(self.slot_ordinal, bool)
            or not isinstance(self.slot_ordinal, int)
            or self.slot_ordinal < 0
            or isinstance(self.reference, bool)
            or not isinstance(self.reference, int)
            or self.reference < 0
        ):
            raise ValueError("H3 slot ordinal/reference must be nonnegative integers")
        bits = _pages(tuple(self.set_bits), "H3 bitmap bits")
        if any(bit >= BITMAP_BITS for bit in bits):
            raise ValueError("H3 bitmap bit exceeds the tag-05 capacity")
        object.__setattr__(self, "set_bits", bits)
        if self.reference == 0 and (self.referenced_page_tag is not None or bits):
            raise ValueError("an inactive H3 slot cannot carry referenced-page data")


@dataclass(frozen=True)
class TraversalObservation:
    """One H2-located row at one checkpoint.

    ``required_owned`` and ``forbidden_owned`` are the page constraints from
    the frozen H2 transition signature.  They let H3 test formula-decoded sets
    without importing or refitting H2 semantics.
    """

    replica: int
    checkpoint_id: str
    map_page: int
    representation: str
    logical_instance: str
    allocation_role: str = "owned_in_use"
    type0_owned: frozenset[int] = frozenset()
    slots: tuple[SlotObservation, ...] = ()
    required_owned: frozenset[int] = frozenset()
    forbidden_owned: frozenset[int] = frozenset()
    locator_ordinal: int = 0
    allocation_span: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.replica not in (1, 2, 3) or isinstance(self.replica, bool):
            raise ValueError("H3 replica must be 1, 2, or 3")
        if self.checkpoint_id not in CHECKPOINT_IDS:
            raise ValueError(f"unknown H3 checkpoint {self.checkpoint_id!r}")
        if (
            isinstance(self.map_page, bool)
            or not isinstance(self.map_page, int)
            or not 0 <= self.map_page < int(BOUNDS["max_final_pages_per_replica"])
        ):
            raise ValueError("H3 map page is outside the registered bound")
        if self.representation not in ("type_0", "type_1"):
            raise ValueError("H3 representation must be type_0 or type_1")
        if not isinstance(self.logical_instance, str) or not self.logical_instance:
            raise ValueError("H3 logical instance must be a nonempty string")
        if self.allocation_role not in ("owned_in_use", "available"):
            raise ValueError("H3 allocation role must be owned_in_use or available")
        if isinstance(self.locator_ordinal, bool) or self.locator_ordinal not in (0, 1):
            raise ValueError("H3 locator ordinal must be 0 or 1")
        for name in ("type0_owned", "required_owned", "forbidden_owned"):
            object.__setattr__(self, name, _pages(tuple(getattr(self, name)), name))
        if self.required_owned & self.forbidden_owned:
            raise ValueError("H3 owned constraints overlap")
        if self.representation == "type_0" and self.slots:
            raise ValueError("a type-0 observation cannot carry H3 slots")
        if self.representation == "type_1" and self.type0_owned:
            raise ValueError("a type-1 observation cannot carry a type-0 set")
        if self.allocation_span is not None:
            start, end = self.allocation_span
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not 0 <= start < end
            ):
                raise ValueError("H3 allocation span must be a nonempty page interval")
        ordinals = tuple(slot.slot_ordinal for slot in self.slots)
        if ordinals != tuple(range(len(ordinals))):
            raise ValueError("H3 slots must be complete and in ordinal order")


@dataclass(frozen=True)
class ConversionLeg:
    """A structural adjacent type-0 to type-1 transition."""

    before: TraversalObservation
    after: TraversalObservation

    def __post_init__(self) -> None:
        if self.before.replica != self.after.replica:
            raise ValueError("H3 conversion legs cannot cross replicas")
        if (
            self.before.logical_instance != self.after.logical_instance
            or self.before.allocation_role != self.after.allocation_role
            or self.before.locator_ordinal != self.after.locator_ordinal
        ):
            raise ValueError("H3 conversion legs cannot cross located rows")
        if self.before.representation != "type_0" or self.after.representation != "type_1":
            raise ValueError("H3 conversion leg must be type_0 then type_1")
        if not any(slot.reference for slot in self.after.slots):
            raise ValueError("H3 conversion leg must contain a nonzero u32 slot")


@dataclass(frozen=True)
class H3Candidate:
    model_type: str
    model: Mapping[str, Any]

    @property
    def canonical_model_id(self) -> str:
        return canonical_model_id(self.model_type, self.model)

    @property
    def canonical_candidate_id(self) -> str:
        return canonical_object_id({"model_type": self.model_type, "model": dict(self.model)})

    def document(self) -> dict[str, Any]:
        # The preregistered H3 candidate shape has no physical bindings and no
        # canonical_model_id field, even though agreement recomputes that id.
        return {
            "model_type": self.model_type,
            "canonical_candidate_id": self.canonical_candidate_id,
            "model": dict(self.model),
        }


@dataclass(frozen=True)
class H3Derivation:
    replica: int
    conversion: H3Candidate
    final: H3Candidate


def conversion_legs(
    observations: Sequence[TraversalObservation],
) -> tuple[ConversionLeg, ...]:
    """Return all adjacent structural conversions in checkpoint order."""
    legs: list[ConversionLeg] = []
    identities = sorted(
        {
            (row.logical_instance, row.allocation_role, row.locator_ordinal)
            for row in observations
        }
    )
    for identity in identities:
        ordered = sorted(
            (
                row
                for row in observations
                if (
                    row.logical_instance,
                    row.allocation_role,
                    row.locator_ordinal,
                )
                == identity
            ),
            key=lambda row: CHECKPOINT_IDS.index(row.checkpoint_id),
        )
        for before, after in zip(ordered, ordered[1:]):
            if (
                before.representation == "type_0"
                and after.representation == "type_1"
                and any(slot.reference for slot in after.slots)
            ):
                legs.append(ConversionLeg(before, after))
    return tuple(legs)


def reference_invalid(
    observation: TraversalObservation, page_count: int
) -> SlotObservation | None:
    """Return the first invalid active reference; zero slots are inactive."""
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        raise ValueError("H3 page count must be a positive integer")
    for slot in observation.slots:
        if slot.reference and (
            slot.reference >= page_count or slot.referenced_page_tag != 0x05
        ):
            return slot
    return None


def admitted_pages(
    observation: TraversalObservation,
    formula: str,
    *,
    maximum: int | None = None,
) -> frozenset[int]:
    """Decode set tag-05 bits with one registered base formula."""
    if formula not in BASE_FORMULAS:
        raise ValueError(f"unregistered H3 base formula {formula!r}")
    if maximum is not None and (
        isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1
    ):
        raise ValueError("H3 admitted-page maximum must be a positive integer")
    result: set[int] = set()
    for slot in observation.slots:
        if slot.reference == 0:
            continue
        for bit in slot.set_bits:
            if formula == "slot_ordinal_times_16352_plus_bit_index":
                page = slot.slot_ordinal * BITMAP_BITS + bit
            elif formula == "referenced_page_times_16352_plus_bit_index":
                page = slot.reference * BITMAP_BITS + bit
            elif formula == "slot_ordinal_times_16352_plus_bit_index_minus_one":
                page = slot.slot_ordinal * BITMAP_BITS + bit - 1
            else:
                page = slot.slot_ordinal * BITMAP_BITS + bit + 1
            if page >= 0:
                if page not in result and maximum is not None and len(result) == maximum:
                    raise A4AnalysisError(
                        "A4-RESOURCE-BOUND",
                        detail=f"H3 traversal admits more than {maximum} pages",
                    )
                result.add(page)
    return frozenset(result)


def input_regions_exercised(observations: Sequence[TraversalObservation]) -> bool:
    """Apply AMB-08's six-region coverage test literally."""
    inactive = any(slot.reference == 0 for row in observations for slot in row.slots)
    active = any(slot.reference != 0 for row in observations for slot in row.slots)
    bit_zero = any(0 in slot.set_bits for row in observations for slot in row.slots)
    nonzero_bit = any(
        any(bit != 0 for bit in slot.set_bits) for row in observations for slot in row.slots
    )
    last_bit = any(
        BITMAP_BITS - 1 in slot.set_bits
        for row in observations
        for slot in row.slots
    )
    boundary = False
    for row in observations:
        by_ordinal = {slot.slot_ordinal: slot for slot in row.slots if slot.reference}
        boundary = boundary or any(
            BITMAP_BITS - 1 in slot.set_bits
            and ordinal + 1 in by_ordinal
            and 0 in by_ordinal[ordinal + 1].set_bits
            for ordinal, slot in by_ordinal.items()
        )
    return inactive and active and bit_zero and nonzero_bit and last_bit and boundary


def _decoded_set(
    observation: TraversalObservation, formula: str
) -> frozenset[int]:
    if observation.representation == "type_0":
        return observation.type0_owned
    return admitted_pages(observation, formula)


def _transition_rows(
    observations: Sequence[TraversalObservation],
) -> dict[tuple[str, str, str], TraversalObservation]:
    rows: dict[tuple[str, str, str], TraversalObservation] = {}
    for row in observations:
        key = (row.logical_instance, row.allocation_role, row.checkpoint_id)
        if key in rows:
            raise ValueError("duplicate H3 transition observation")
        rows[key] = row
    return rows


def _state(
    rows: Mapping[tuple[str, str, str], TraversalObservation],
    instance: str,
    checkpoint: str,
    formula: str,
) -> tuple[frozenset[int], frozenset[int]] | None:
    owned = rows.get((instance, "owned_in_use", checkpoint))
    available = rows.get((instance, "available", checkpoint))
    if owned is None and available is None:
        return None
    if owned is None or available is None:
        raise ValueError("H3 transition state lacks one frozen allocation role")
    return _decoded_set(owned, formula), _decoded_set(available, formula)


def _changes_within_span(
    changes: frozenset[int],
    rows: Sequence[TraversalObservation],
) -> bool:
    spans = tuple(row.allocation_span for row in rows if row.allocation_span is not None)
    if not changes:
        return True
    return bool(spans) and all(
        any(start <= page < end for start, end in spans) for page in changes
    )


def transition_signature_fits(
    formula: str, observations: Sequence[TraversalObservation]
) -> bool:
    """Apply every frozen H2 transition rule to formula-decoded type-1 sets."""
    rows = _transition_rows(observations)
    instances = tuple(sorted({row.logical_instance for row in observations}))

    for left, right in _IDLE_PAIRS:
        for instance in instances:
            left_state = _state(rows, instance, left, formula)
            right_state = _state(rows, instance, right, formula)
            if left_state is None and right_state is None:
                continue
            if left_state is None or right_state is None or left_state != right_state:
                return False

    visited_growth: set[tuple[str, str]] = set()
    for sequence in _GROW_SEQUENCES:
        role = sequence[1].split("_", 1)[0]
        for left, right in zip(sequence, sequence[1:]):
            if not ("_REL_" in right or "_ABS_" in right):
                continue
            if (left, right) in visited_growth:
                continue
            visited_growth.add((left, right))
            matching = tuple(
                instance for instance in instances if instance.split("-", 1)[0] == role
            )
            for instance in matching:
                before = _state(rows, instance, left, formula)
                after = _state(rows, instance, right, formula)
                if before is None and after is None:
                    continue
                if before is None or after is None:
                    return False
                before_owned, before_available = before
                after_owned, after_available = after
                gained = after_owned - before_owned
                if not before_owned <= after_owned:
                    return False
                if gained & (after_available - before_available):
                    return False

    role = _DELETE_CHECKPOINT.split("_", 1)[0]
    matching = tuple(
        instance for instance in instances if instance.split("-", 1)[0] == role
    )
    for instance in matching:
        before = _state(rows, instance, _DELETE_BEFORE, formula)
        deleted = _state(rows, instance, _DELETE_CHECKPOINT, formula)
        reinserted = _state(rows, instance, _REINSERT_CHECKPOINT, formula)
        if before is None and deleted is None and reinserted is None:
            continue
        if before is None or deleted is None or reinserted is None:
            return False
        before_owned, before_available = before
        deleted_owned, deleted_available = deleted
        reinserted_owned, reinserted_available = reinserted
        if not deleted_owned <= before_owned or not before_available <= deleted_available:
            return False
        if not deleted_owned <= reinserted_owned or not reinserted_available <= deleted_available:
            return False
        delete_observations = tuple(
            row
            for row in observations
            if row.logical_instance == instance
            and row.checkpoint_id in (_DELETE_BEFORE, _DELETE_CHECKPOINT)
        )
        delete_changes = frozenset(
            (before_owned ^ deleted_owned)
            | (before_available ^ deleted_available)
        )
        if not _changes_within_span(delete_changes, delete_observations):
            return False
        reinsert_observations = tuple(
            row
            for row in observations
            if row.logical_instance == instance
            and row.checkpoint_id in (_DELETE_CHECKPOINT, _REINSERT_CHECKPOINT)
        )
        reinsert_changes = frozenset(
            (deleted_owned ^ reinserted_owned)
            | (deleted_available ^ reinserted_available)
        )
        if not _changes_within_span(reinsert_changes, reinsert_observations):
            return False
    return True


def formula_fits(
    formula: str,
    observations: Sequence[TraversalObservation],
    legs: Sequence[ConversionLeg],
    ledger: WorkLedger | None = None,
) -> bool:
    """Test conversion containment followed by frozen H2 set constraints."""
    decoded = {id(row): admitted_pages(row, formula) for row in observations if row.representation == "type_1"}
    if ledger is not None:
        for row in observations:
            if row.representation == "type_1":
                ledger.charge_qualified(
                    "base_formula_evaluations",
                    QualifiedPage(row.replica, row.checkpoint_id, row.map_page),
                    discriminator=formula,
                )
    for leg in legs:
        if not leg.before.type0_owned <= decoded[id(leg.after)]:
            return False
    for row in observations:
        if row.representation != "type_1":
            continue
        owned = decoded[id(row)]
        if not row.required_owned <= owned or row.forbidden_owned & owned:
            return False
    return transition_signature_fits(formula, observations)


def derive_h3(
    replica: int,
    observations: Sequence[TraversalObservation],
    page_counts: Mapping[str, int],
    ledger: WorkLedger | None = None,
    measurements: MeasurementRecorder | None = None,
) -> H3Derivation:
    """Evaluate H3 derivation predicates through BASE-MULTIPLE in order."""
    if replica not in (1, 2) or isinstance(replica, bool):
        raise ValueError("H3 derivation replica must be 1 or 2")
    rows = tuple(observations)
    if not rows or any(row.replica != replica for row in rows):
        raise ValueError("H3 observations must be nonempty and replica-local")

    legs = conversion_legs(rows)
    conversion_candidates = tuple(
        candidate for candidate in CONVERSIONS if legs and candidate == _CONVERSION
    )
    conversion_count = len(conversion_candidates)
    measure(measurements, "A4-H3-CONVERSION-NONE", conversion_count, conversion_count == 1, replica=replica)
    if conversion_count == 0:
        raise A4AnalysisError("A4-H3-CONVERSION-NONE")
    conversion = H3Candidate("h3_conversion", {"conversion": _CONVERSION})
    conversion_model_count = len((conversion,))

    inactive_slot = any(slot.reference == 0 for row in rows for slot in row.slots)
    measure(measurements, "A4-H3-INACTIVE-SLOT-NONE", conversion_model_count, inactive_slot, replica=replica)
    if not inactive_slot:
        raise A4AnalysisError("A4-H3-INACTIVE-SLOT-NONE", 1)

    invalid_reference: tuple[TraversalObservation, SlotObservation] | None = None
    for row in rows:
        if row.representation != "type_1":
            continue
        try:
            count = page_counts[row.checkpoint_id]
        except KeyError as exc:
            raise ValueError(f"missing H3 page count for {row.checkpoint_id}") from exc
        invalid = reference_invalid(row, count)
        if invalid is not None:
            invalid_reference = (row, invalid)
            break
    measure(measurements, "A4-H3-REFERENCE-INVALID", conversion_model_count, invalid_reference is None, replica=replica)
    if invalid_reference is not None:
        row, invalid = invalid_reference
        raise A4AnalysisError(
            "A4-H3-REFERENCE-INVALID", 1,
            detail=(f"replica {replica} {row.checkpoint_id} slot "
                    f"{invalid.slot_ordinal} references {invalid.reference}"),
        )

    discriminates = input_regions_exercised(rows)
    measure(measurements, "A4-H3-BASE-DISCRIMINATION", conversion_model_count, discriminates, replica=replica)
    if not discriminates:
        raise A4AnalysisError("A4-H3-BASE-DISCRIMINATION", 1)

    survivors = tuple(
        formula
        for formula in BASE_FORMULAS
        if formula_fits(formula, rows, legs, ledger)
    )
    measure(measurements, "A4-H3-BASE-NONE", len(survivors), bool(survivors), replica=replica)
    if not survivors:
        raise A4AnalysisError("A4-H3-BASE-NONE")
    measure(measurements, "A4-H3-BASE-MULTIPLE", len(survivors), len(survivors) == 1, replica=replica)
    if len(survivors) > 1:
        raise A4AnalysisError("A4-H3-BASE-MULTIPLE", len(survivors))
    final = H3Candidate(
        "h3_final_base_formula",
        {"conversion": _CONVERSION, "base_formula": survivors[0]},
    )
    return H3Derivation(replica, conversion, final)


def agree_h3(
    left: H3Derivation, right: H3Derivation,
    measurements: MeasurementRecorder | None = None,
) -> H3Candidate:
    """Apply the replica-agreement predicate to two unique derivations."""
    if (left.replica, right.replica) != (1, 2):
        raise ValueError("H3 agreement requires replicas 1 then 2")
    model_count = len({left.final.canonical_model_id, right.final.canonical_model_id})
    agrees = model_count == 1
    measure(measurements, "A4-H3-REPLICA-DISAGREEMENT", model_count, agrees)
    if not agrees:
        raise A4AnalysisError("A4-H3-REPLICA-DISAGREEMENT", 2)
    return left.final


def predicts_h3(
    frozen: H3Candidate,
    observations: Sequence[TraversalObservation],
    page_counts: Mapping[str, int],
) -> bool:
    """Evaluate the unchanged H3 model on holdout without refitting."""
    if frozen.model_type != "h3_final_base_formula":
        raise ValueError("H3 holdout requires a final frozen model")
    rows = tuple(observations)
    if not rows or any(row.replica != 3 for row in rows):
        return False
    legs = conversion_legs(rows)
    if not legs or not input_regions_exercised(rows):
        return False
    for row in rows:
        if row.representation == "type_1":
            count = page_counts.get(row.checkpoint_id)
            if count is None or reference_invalid(row, count) is not None:
                return False
    formula = frozen.model.get("base_formula")
    return isinstance(formula, str) and formula_fits(formula, rows, legs)
