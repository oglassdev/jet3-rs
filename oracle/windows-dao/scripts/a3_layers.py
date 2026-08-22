#!/usr/bin/env python3
"""Post-delimitation A3 global layers and the separate TDEF pointer layer.

A3 rule | implementation
--- | ---
Tag-1 two-u32 layout and zero suffix | :func:`indirect_state`
Per-leg cross-check transcript and first-stop rule | :func:`polarity_cross_check`
Monotone inline-to-indirect classification | :func:`derive_conversion`
Activation-relative named validity window | :func:`validate_references`
Fixed-source inline boundary and suffix | :func:`inline_boundaries`
Slot-0 H_REL_0064 base discrimination | :func:`derive_base`
Ordered TDEF precondition/windows/record/multiplicity | :func:`derive_tdef_candidates`
Transition-structural pointer exclusion | :func:`pointer_windows`
Page-agnostic global-field exclusion | :func:`global_structural_valid`
TDEF inclusion-minimal stable-flank record | :func:`tdef_models`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from a3_model import (
    CHECKPOINT_IDS, CHURN_TRANSITIONS, D_TRANSITIONS, GROWTH_TRANSITIONS,
    IDLE_PAIRS, PAGE_SIZE, TRANSITIONS, Abort, GlobalRecordModel, Prefix,
    Record, View, candidate_page_space, decode_inline, decode_pointer,
    extended_base, inline_highwater_valid, raw_not_in_use,
)
from a3_spec import BASE_FORMULAS, CHECKPOINT_ORDINALS, POINTER_LAYOUTS

CONVERSION_WINDOW = tuple(TRANSITIONS["inline_to_indirect_conversion_window"])
VALIDITY_CHECKPOINTS = tuple(TRANSITIONS["pointer_validity_checkpoints"])
CROSS_CHECK_LEGS = tuple(tuple(pair) for pair in TRANSITIONS["polarity_cross_check_legs"])
EXTENDED_HEADER_BYTES = 4
EXTENDED_BITS = (PAGE_SIZE - EXTENDED_HEADER_BYTES) * 8


@dataclass(frozen=True)
class Leg:
    left_checkpoint_id: str
    right_checkpoint_id: str

    def document(self) -> dict[str, str]:
        return {"left_checkpoint_id": self.left_checkpoint_id, "right_checkpoint_id": self.right_checkpoint_id}


@dataclass(frozen=True)
class CrossCheckTranscript:
    evaluated_legs: tuple[Leg, ...] = ()
    representation_change_stop: Leg | None = None
    first_violating_leg: Leg | None = None
    first_violating_page: int | None = None

    def document(self) -> dict[str, Any]:
        return {
            "evaluated_legs": [leg.document() for leg in self.evaluated_legs],
            "representation_change_stop": None if self.representation_change_stop is None else self.representation_change_stop.document(),
            "first_violating_leg": None if self.first_violating_leg is None else self.first_violating_leg.document(),
            "first_violating_page": self.first_violating_page,
        }


@dataclass(frozen=True)
class IndirectState:
    tag: int
    slots: tuple[int, int]


@dataclass(frozen=True)
class ConversionModel:
    conversion_checkpoint_id: str
    conversion_ordinal: int
    indirect_tag: int
    active_slot_count_at_conversion: int
    active_slot_count_at_h_rel_0904: int
    inline_boundary: int
    slot_reference_pages: tuple[int, int]

    def document(self) -> dict[str, Any]:
        return {
            "conversion_checkpoint_id": self.conversion_checkpoint_id,
            "conversion_ordinal": self.conversion_ordinal,
            "indirect_tag": self.indirect_tag,
            "active_slot_count_at_conversion": self.active_slot_count_at_conversion,
            "active_slot_count_at_h_rel_0904": self.active_slot_count_at_h_rel_0904,
            "inline_boundary": self.inline_boundary,
            "slot_reference_pages": list(self.slot_reference_pages),
        }


@dataclass(frozen=True)
class BaseModel:
    extended_base_formula: str
    def document(self) -> dict[str, str]:
        return {"extended_base_formula": self.extended_base_formula}


@dataclass(frozen=True)
class TdefModel:
    record: Record
    pointer_layout: str
    growth_pointer_offset: int
    delete_reinsert_pointer_offset: int
    def document(self) -> dict[str, Any]:
        return {
            "record": self.record.document(), "pointer_layout": self.pointer_layout,
            "growth_pointer_offset": self.growth_pointer_offset,
            "delete_reinsert_pointer_offset": self.delete_reinsert_pointer_offset,
        }


def indirect_state(payload: bytes, start: int, end: int) -> IndirectState | None:
    if not 0 <= start <= end - 9 <= PAGE_SIZE - 9 or payload[start] != 1:
        return None
    if any(payload[start + 9:end]):
        return None
    return IndirectState(1, (
        int.from_bytes(payload[start + 1:start + 5], "little"),
        int.from_bytes(payload[start + 5:start + 9], "little"),
    ))


def _bit(state: Any, page: int) -> bool | None:
    if not state.base <= page < state.base + state.capacity:
        return None
    return page in state.in_use


def polarity_cross_check(view: View, model: GlobalRecordModel) -> CrossCheckTranscript:
    """Walk only representable appended pages, stopping at violation or tag change."""
    evaluated: list[Leg] = []
    start, end, record_page = model.record.start, model.record.end, model.record.page
    for left_name, right_name in CROSS_CHECK_LEGS:
        leg = Leg(left_name, right_name)
        left_payload, right_payload = view.page(left_name, record_page), view.page(right_name, record_page)
        left_tag, right_tag = left_payload[start], right_payload[start]
        if left_tag != right_tag:
            return CrossCheckTranscript(tuple(evaluated), leg, None, None)
        if left_tag != 0:
            continue
        left = decode_inline(left_payload, start, end, model.bit_polarity)
        right = decode_inline(right_payload, start, end, model.bit_polarity)
        if left is None or right is None:
            raise Abort("A3-POLARITY-CROSSCHECK")
        lower = view.page_count(left_name)
        upper = view.page_count(right_name)
        represented_lower = max(lower, left.base, right.base)
        represented_upper = min(upper, left.base + left.capacity, right.base + right.capacity)
        required = range(represented_lower, max(represented_lower, represented_upper))
        evaluated.append(leg)
        for page in required:
            if page >= 65536:
                raise Abort("A3-POINTER-VALIDITY")
            if _bit(left, page) is not False or _bit(right, page) is not True:
                return CrossCheckTranscript(tuple(evaluated), None, leg, page)
    return CrossCheckTranscript(tuple(evaluated), None, None, None)


def global_structural_valid(view: View, model: GlobalRecordModel) -> bool:
    """Reject record changes outside the declared D, growth, churn, and idle pairs."""
    allowed = set(D_TRANSITIONS + CHURN_TRANSITIONS + IDLE_PAIRS + CROSS_CHECK_LEGS)
    allowed.add(("E0R", "D_GROW_0128"))
    start, end, page = model.record.start, model.record.end, model.record.page
    for left, right in zip(CHECKPOINT_IDS, CHECKPOINT_IDS[1:]):
        if (left, right) in allowed:
            continue
        if view.page(left, page)[start:end] != view.page(right, page)[start:end]:
            return False
    return True


def _classification(view: View, model: GlobalRecordModel, checkpoint: str) -> str:
    payload = view.page(checkpoint, model.record.page)
    start, end = model.record.start, model.record.end
    if payload[start] == 0:
        state = decode_inline(payload, start, end, model.bit_polarity)
        return "inline" if inline_highwater_valid(state, view.page_count(checkpoint)) else "neither"
    if payload[start] == 1 and indirect_state(payload, start, end) is not None:
        return "indirect"
    return "neither"


def conversion_checkpoint(view: View, model: GlobalRecordModel) -> str:
    classes = [_classification(view, model, checkpoint) for checkpoint in CONVERSION_WINDOW]
    indirect = [index for index, kind in enumerate(classes) if kind == "indirect"]
    if not indirect:
        raise Abort("A3-CONVERSION-NONE")
    at = indirect[0]
    if at == 0 or any(kind != "inline" for kind in classes[:at]) or any(kind != "indirect" for kind in classes[at:]):
        raise Abort("A3-CONVERSION-MULTIPLE")
    return CONVERSION_WINDOW[at]


def _activation(
    view: View, record_page: int, offset: int, width: int,
    indirect_start: int | None,
) -> int | None:
    for checkpoint in CHECKPOINT_IDS:
        payload = view.page(checkpoint, record_page)
        if indirect_start is not None and payload[indirect_start] != 1:
            continue
        value = int.from_bytes(payload[offset:offset + width], "little")
        if value:
            return CHECKPOINT_ORDINALS[checkpoint]
    return None


def validate_references(
    view: View, record_page: int, references: Sequence[tuple[int, int, int]],
    *, indirect_start: int | None = None,
) -> None:
    """Validate only named checkpoints at/after each field's first nonzero activation."""
    space = candidate_page_space((view,))
    maximum = space.stop
    for offset, width, slot in sorted(references, key=lambda row: (row[0], row[2])):
        activation = _activation(view, record_page, offset, width, indirect_start)
        if activation is None:
            continue
        for checkpoint in VALIDITY_CHECKPOINTS:
            if CHECKPOINT_ORDINALS[checkpoint] < activation:
                continue
            payload = view.page(checkpoint, record_page)
            if indirect_start is not None and payload[indirect_start] != 1:
                continue
            reference = int.from_bytes(payload[offset:offset + width], "little")
            if reference == 0:
                continue
            if not 1 <= reference < view.page_count(checkpoint) or reference >= maximum or view.hash_at(checkpoint, reference) is None or view.page(checkpoint, reference)[0] != 0x05:
                raise Abort("A3-POINTER-VALIDITY")


def inline_boundaries(view: View, model: GlobalRecordModel, conversion: str) -> tuple[int, ...]:
    start, end, page = model.record.start, model.record.end, model.record.page
    at = CONVERSION_WINDOW.index(conversion)
    inline_names = CONVERSION_WINDOW[:at]
    indirect_names = CONVERSION_WINDOW[at:]
    explained: list[int] = []
    suffix_fail = False
    # "Complete bitmap" is the shortest byte extent that represents every
    # allocated page plus the required page-count sentinel at every inline
    # checkpoint. Padding bits in its last byte remain polarity-relative free.
    required_boundary = max(
        start + 5
        + (view.page_count(checkpoint)
           - int.from_bytes(view.page(checkpoint, page)[start + 1:start + 5], "little")
           + 8) // 8
        for checkpoint in inline_names
    )
    for boundary in range(start + 5, end + 1):
        if boundary != required_boundary:
            continue
        valid = True
        for checkpoint in inline_names:
            payload = view.page(checkpoint, page)
            state = decode_inline(payload, start, boundary, model.bit_polarity)
            if not inline_highwater_valid(state, view.page_count(checkpoint)):
                valid = False
                break
            if any(value != raw_not_in_use(model.bit_polarity) for value in payload[boundary:end]):
                suffix_fail = True
                valid = False
                break
        if not valid:
            continue
        if any(indirect_state(view.page(checkpoint, page), start, end) is None for checkpoint in indirect_names):
            continue
        explained.append(boundary)
    if not explained:
        raise Abort("A3-INLINE-SUFFIX" if suffix_fail else "A3-INLINE-BOUNDARY-NONE")
    if len(explained) > 1:
        raise Abort("A3-INLINE-BOUNDARY-MULTIPLE")
    return tuple(explained)


def derive_conversion(view: View, model: GlobalRecordModel) -> tuple[ConversionModel, CrossCheckTranscript]:
    transcript = polarity_cross_check(view, model)
    if transcript.first_violating_leg is not None:
        raise Abort("A3-POLARITY-CROSSCHECK")
    conversion = conversion_checkpoint(view, model)
    payload = view.page(conversion, model.record.page)
    state = indirect_state(payload, model.record.start, model.record.end)
    if state is None:
        raise Abort("A3-CONVERSION-MULTIPLE")
    active = sum(value != 0 for value in state.slots)
    if active == 0:
        raise Abort("A3-SLOT-ACTIVATION")
    final_state = indirect_state(view.page("H_REL_0904", model.record.page), model.record.start, model.record.end)
    if final_state is None or sum(value != 0 for value in final_state.slots) != 2:
        raise Abort("A3-SLOT-FINAL")
    start = model.record.start
    validate_references(
        view, model.record.page, ((start + 1, 4, 0), (start + 5, 4, 1)),
        indirect_start=start,
    )
    boundary = inline_boundaries(view, model, conversion)[0]
    return ConversionModel(
        conversion, CHECKPOINT_ORDINALS[conversion], 1, active, 2, boundary,
        state.slots,
    ), transcript


def _extended_bits(view: View, model: GlobalRecordModel, checkpoint: str) -> dict[int, tuple[int, int]]:
    state = indirect_state(view.page(checkpoint, model.record.page), model.record.start, model.record.end)
    if state is None:
        return {}
    result: dict[int, tuple[int, int]] = {}
    for slot, reference in enumerate(state.slots):
        if reference == 0 or reference >= view.page_count(checkpoint):
            continue
        payload = view.page(checkpoint, reference)
        if payload[0] != 0x05:
            continue
        raw = int.from_bytes(payload[EXTENDED_HEADER_BYTES:], "little")
        if model.bit_polarity == "set_means_not_in_use":
            raw ^= (1 << EXTENDED_BITS) - 1
        result[slot] = (reference, raw)
    return result


def _formula_matches(view: View, model: GlobalRecordModel, formula: str, pairs: Sequence[tuple[str, str]]) -> bool:
    for left, right in pairs:
        before, after = _extended_bits(view, model, left), _extended_bits(view, model, right)
        predicted: set[int] = set()
        for slot, (reference, bits) in after.items():
            prior = before.get(slot, (reference, 0))[1] if before.get(slot, (reference, 0))[0] == reference else 0
            fresh = bits & ~prior
            origin = extended_base(formula, slot, reference)
            while fresh:
                bit = (fresh & -fresh).bit_length() - 1
                predicted.add(origin + bit)
                fresh &= fresh - 1
        expected = set(range(view.page_count(left), view.page_count(right)))
        view.work.charge(len(predicted) + 1)
        if predicted != expected:
            return False
    return True


def derive_base(view: View, model: GlobalRecordModel, conversion: ConversionModel) -> BaseModel:
    before = _extended_bits(view, model, "P_ABS_16480").get(0)
    after = _extended_bits(view, model, "H_REL_0064").get(0)
    if before is None or after is None or before[0] != after[0] or not (after[1] & ~before[1]):
        raise Abort("A3-BASE-DISCRIMINATION")
    at = CONVERSION_WINDOW.index(conversion.conversion_checkpoint_id)
    pairs = [
        (left, right) for left, right in zip(CONVERSION_WINDOW, CONVERSION_WINDOW[1:])
        if CONVERSION_WINDOW.index(left) >= at and view.page_count(right) > view.page_count(left)
    ]
    survivors = tuple(formula for formula in BASE_FORMULAS if _formula_matches(view, model, formula, pairs))
    if not survivors:
        raise Abort("A3-BASE-NONE")
    if len(survivors) > 1:
        raise Abort("A3-BASE-MULTIPLE")
    return BaseModel(survivors[0])


def _window_values(view: View, page: int, offset: int, layout: str) -> dict[str, tuple[int, int]]:
    return {checkpoint: decode_pointer(view.page(checkpoint, page)[offset:offset + 4], layout) for checkpoint in CHECKPOINT_IDS}


def _stable(values: Mapping[str, tuple[int, int]], pairs: Sequence[tuple[str, str]]) -> bool:
    return all(values[left] == values[right] for left, right in pairs)


def _pointer_valid(view: View, record_page: int, offset: int, layout: str) -> bool:
    page_offset = offset if layout == "u24le_page_then_u8_slot" else offset + 1
    try:
        validate_references(view, record_page, ((page_offset, 3, 0),))
    except Abort as exc:
        if exc.predicate_id == "A3-POINTER-VALIDITY":
            return False
        raise
    values = _window_values(view, record_page, offset, layout)
    return any(page != 0 for page, _slot in values.values())


@dataclass(frozen=True)
class WindowCandidates:
    growth: Mapping[str, tuple[int, ...]]
    churn: Mapping[str, tuple[int, ...]]
    structural_failure: bool
    validity_failure: bool


def pointer_windows(view: View, page: int) -> WindowCandidates:
    growth = {layout: [] for layout in POINTER_LAYOUTS}
    churn = {layout: [] for layout in POINTER_LAYOUTS}
    structural = validity = False
    outside_growth = D_TRANSITIONS + CHURN_TRANSITIONS + IDLE_PAIRS
    outside_churn = D_TRANSITIONS + GROWTH_TRANSITIONS + IDLE_PAIRS
    for offset in range(PAGE_SIZE - 3):
        for layout in POINTER_LAYOUTS:
            values = _window_values(view, page, offset, layout)
            grows = any(values[a] != values[b] for a, b in GROWTH_TRANSITIONS)
            churns = values["L_REL_1280"] != values["L_DELETE_ALL"] and values["L_REL_1280"] == values["L_REINSERT_SAME"]
            growth_shape = grows and _stable(values, CHURN_TRANSITIONS)
            churn_shape = churns and _stable(values, GROWTH_TRANSITIONS)
            if growth_shape:
                if not _stable(values, outside_growth):
                    structural = True
                elif _pointer_valid(view, page, offset, layout):
                    growth[layout].append(offset)
                else:
                    validity = True
            if churn_shape:
                if not _stable(values, outside_churn):
                    structural = True
                elif _pointer_valid(view, page, offset, layout):
                    churn[layout].append(offset)
                else:
                    validity = True
    return WindowCandidates(
        {key: tuple(value) for key, value in growth.items()},
        {key: tuple(value) for key, value in churn.items()}, structural, validity,
    )


def _stable_byte(view: View, page: int, offset: int) -> bool:
    return len({view.page(checkpoint, page)[offset] for checkpoint in CHECKPOINT_IDS}) == 1


def tdef_models(view: View, page: int, windows: WindowCandidates) -> tuple[TdefModel, ...]:
    changed = Prefix.from_flags([
        len({view.page(checkpoint, page)[offset] for checkpoint in CHECKPOINT_IDS}) > 1
        for offset in range(PAGE_SIZE)
    ], view.work)
    models: list[TdefModel] = []
    for layout in POINTER_LAYOUTS:
        for growth in windows.growth[layout]:
            for churn in windows.churn[layout]:
                if growth == churn or abs(growth - churn) < 4:
                    continue
                first, second = sorted((growth, churn))
                core_end = second + 4
                start = first - 1 if first else first
                end = core_end + 1 if core_end < PAGE_SIZE else core_end
                if start < first and not _stable_byte(view, page, start):
                    continue
                if end > core_end and not _stable_byte(view, page, end - 1):
                    continue
                if changed.count(start, first) + changed.count(first + 4, second) + changed.count(second + 4, end):
                    continue
                models.append(TdefModel(Record(page, start, end), layout, growth, churn))
    view.work.examine_models(len(models))
    return tuple(models)


def derive_tdef_candidates(view: View, pages: Sequence[int], churn_precondition_met: bool, *, enumerate_candidates: bool = True) -> tuple[TdefModel, ...]:
    """Evaluate the five registered TDEF stages once, in their literal order."""
    if not churn_precondition_met:
        raise Abort("A3-CHURN-PRECONDITION")
    all_windows: dict[int, WindowCandidates] = {}
    for page in pages:
        if enumerate_candidates:
            view.work.enumerate_intervals()
        all_windows[page] = pointer_windows(view, page)
    if not any(any(window.growth.values()) for window in all_windows.values()):
        if any(window.structural_failure for window in all_windows.values()):
            raise Abort("A3-STRUCTURAL-EXCLUSION")
        if any(window.validity_failure for window in all_windows.values()):
            raise Abort("A3-POINTER-VALIDITY")
        raise Abort("A3-GROWTH-POINTER-NONE")
    if not any(any(window.churn.values()) for window in all_windows.values()):
        if any(window.structural_failure for window in all_windows.values()):
            raise Abort("A3-STRUCTURAL-EXCLUSION")
        if any(window.validity_failure for window in all_windows.values()):
            raise Abort("A3-POINTER-VALIDITY")
        raise Abort("A3-CHURN-POINTER-NONE")
    per_page = {page: tdef_models(view, page, window) for page, window in all_windows.items()}
    total = [model for models in per_page.values() for model in models]
    if not total:
        raise Abort("A3-TDEF-RECORD-NONE")
    record_pages = {model.record.page for model in total}
    records = {model.record for model in total}
    if len(record_pages) > 1:
        raise Abort("A3-TDEF-PAGE-MULTIPLE")
    if len(records) > 1:
        raise Abort("A3-TDEF-RECORD-MULTIPLE")
    if len(total) > 1:
        raise Abort("A3-POINTER-MULTIPLE")
    return tuple(total)


def predicts_global(view: View, frozen: GlobalRecordModel) -> bool:
    from a3_model import global_start_candidates
    models, _evidence = global_start_candidates(view, frozen.record.page, enumerate_candidates=False)
    return frozen in models


def predicts_conversion(view: View, global_model: GlobalRecordModel, frozen: ConversionModel) -> bool:
    try:
        model, transcript = derive_conversion(view, global_model)
        return model == frozen and transcript.first_violating_leg is None
    except Abort as exc:
        if exc.predicate_id in {"A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"}:
            raise
        return False


def predicts_base(view: View, global_model: GlobalRecordModel, conversion: ConversionModel, frozen: BaseModel) -> bool:
    try:
        return derive_base(view, global_model, conversion) == frozen
    except Abort as exc:
        if exc.predicate_id in {"A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"}:
            raise
        return False


def predicts_tdef(view: View, frozen: TdefModel, churn_precondition_met: bool) -> bool:
    try:
        return derive_tdef_candidates(view, (frozen.record.page,), churn_precondition_met, enumerate_candidates=False) == (frozen,)
    except Abort as exc:
        if exc.predicate_id in {"A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"}:
            raise
        return False
