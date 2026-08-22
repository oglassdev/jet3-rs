#!/usr/bin/env python3
"""Post-delimitation A3 global layers and the separate TDEF pointer layer.

A3 rule | implementation
--- | ---
R3-M01/M02 polarity walk | :func:`polarity_cross_check`
R3-G02 conversion attribution | :func:`conversion_checkpoint`
R3-G04 minimal b* then suffix | :func:`inline_boundary`
R3-G06 signature, validity, exclusion order | :func:`derive_tdef_candidates`
R3-G07 tag-1 slot activation | :func:`_activation`
R3-G01 extended bitmap formulas | :func:`derive_base`, :func:`formula_holds`
R3-G09 frozen-model-only holdout checks | :func:`predicts_global`, :func:`predicts_conversion`, :func:`predicts_base`, :func:`predicts_tdef`
R3-G10 candidate-local absence | :func:`_window_values`, :func:`_classification`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from a3_model import (
    CHECKPOINT_IDS, CHURN_TRANSITIONS, D_TRANSITIONS, HIGH_GROWTH,
    IDLE_PAIRS, LOW_GROWTH, PAGE_SIZE, P_GROWTH, TRANSITIONS, Abort,
    GlobalRecordModel, Prefix, Record, View, candidate_page_space,
    decode_inline, decode_pointer, extended_base, frozen_global_model_holds,
    inline_highwater_valid, qualify_tdef_pages, raw_not_in_use,
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
        left_payload = view.page_optional(left_name, record_page)
        right_payload = view.page_optional(right_name, record_page)
        if left_payload is None or right_payload is None:
            raise Abort("A3-POLARITY-CROSSCHECK")
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
    """R3 classifies global structural exclusion as unreachable."""
    del view, model
    return True


def _classification(view: View, model: GlobalRecordModel, checkpoint: str) -> str:
    payload = view.page_optional(checkpoint, model.record.page)
    if payload is None:
        return "neither"
    start, end = model.record.start, model.record.end
    if payload[start] == 0:
        state = decode_inline(payload, start, end, model.bit_polarity)
        return "inline" if inline_highwater_valid(state, view.page_count(checkpoint)) else "neither"
    if payload[start] == 1 and indirect_state(payload, start, end) is not None:
        return "indirect"
    return "neither"


def conversion_checkpoint(view: View, model: GlobalRecordModel) -> str:
    classes = [_classification(view, model, checkpoint) for checkpoint in CONVERSION_WINDOW]
    return CONVERSION_WINDOW[conversion_index(classes)]


def conversion_index(classes: Sequence[str]) -> int:
    """Apply R3-G02 attribution to an ordered classification sequence."""
    indirect = [index for index, kind in enumerate(classes) if kind == "indirect"]
    if not indirect or not any(kind == "inline" for kind in classes[: indirect[0]]):
        raise Abort("A3-CONVERSION-NONE")
    at = indirect[0]
    changes = sum(left != right for left, right in zip(classes, classes[1:]))
    if changes != 1:
        raise Abort("A3-CONVERSION-MULTIPLE")
    return at


def _activation(
    view: View, record_page: int, offset: int, width: int,
    indirect_start: int | None,
) -> int | None:
    for checkpoint in CHECKPOINT_IDS:
        payload = view.page_optional(checkpoint, record_page)
        if payload is None:
            continue
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
            payload = view.page_optional(checkpoint, record_page)
            if payload is None:
                raise Abort("A3-POINTER-VALIDITY")
            if indirect_start is not None and payload[indirect_start] != 1:
                continue
            reference = int.from_bytes(payload[offset:offset + width], "little")
            if reference == 0:
                continue
            referenced = view.page_optional(checkpoint, reference)
            if (
                not 1 <= reference < view.page_count(checkpoint)
                or reference >= maximum
                or referenced is None
                or referenced[0] != 0x05
            ):
                raise Abort("A3-POINTER-VALIDITY")


def inline_boundary(view: View, model: GlobalRecordModel, conversion: str) -> int:
    """Compute R3-G04's unique minimal extent, then test its quiet suffix."""
    start, end, page = model.record.start, model.record.end, model.record.page
    at = CONVERSION_WINDOW.index(conversion)
    inline_names = CONVERSION_WINDOW[:at]
    payloads = {name: view.page_optional(name, page) for name in inline_names}
    if any(payload is None for payload in payloads.values()):
        raise Abort("A3-CONVERSION-MULTIPLE")
    checked = {name: payload for name, payload in payloads.items() if payload is not None}
    boundary = max(
        start
        + 5
        + (view.page_count(checkpoint)
           - int.from_bytes(checked[checkpoint][start + 1:start + 5], "little")) // 8
        + 1
        for checkpoint in inline_names
    )
    for checkpoint, payload in checked.items():
        state = decode_inline(payload, start, boundary, model.bit_polarity)
        if not inline_highwater_valid(state, view.page_count(checkpoint)):
            raise Abort("A3-INLINE-BOUNDARY-NONE")
    expected = raw_not_in_use(model.bit_polarity)
    if any(
        value != expected
        for payload in checked.values()
        for value in payload[boundary:end]
    ):
        raise Abort("A3-INLINE-SUFFIX")
    return boundary


def inline_boundaries(
    view: View,
    model: GlobalRecordModel,
    conversion: str,
) -> tuple[int, ...]:
    """Compatibility wrapper for the single R3-G04 boundary."""
    return (inline_boundary(view, model, conversion),)


def derive_conversion(
    view: View,
    model: GlobalRecordModel,
    transcript: CrossCheckTranscript | None = None,
) -> tuple[ConversionModel, CrossCheckTranscript]:
    transcript = transcript or polarity_cross_check(view, model)
    if transcript.first_violating_leg is not None:
        raise Abort("A3-POLARITY-CROSSCHECK")
    conversion = conversion_checkpoint(view, model)
    payload = view.page_optional(conversion, model.record.page)
    if payload is None:
        raise Abort("A3-CONVERSION-MULTIPLE")
    state = indirect_state(payload, model.record.start, model.record.end)
    if state is None:
        raise Abort("A3-CONVERSION-MULTIPLE")
    active = sum(value != 0 for value in state.slots)
    if active == 0:
        raise Abort("A3-SLOT-ACTIVATION")
    final_payload = view.page_optional("H_REL_0904", model.record.page)
    final_state = (
        None
        if final_payload is None
        else indirect_state(final_payload, model.record.start, model.record.end)
    )
    if final_state is None or sum(value != 0 for value in final_state.slots) != 2:
        raise Abort("A3-SLOT-FINAL")
    start = model.record.start
    validate_references(
        view, model.record.page, ((start + 1, 4, 0), (start + 5, 4, 1)),
        indirect_start=start,
    )
    boundary = inline_boundary(view, model, conversion)
    return ConversionModel(
        conversion, CHECKPOINT_ORDINALS[conversion], 1, active, 2, boundary,
        state.slots,
    ), transcript


def _extended_bits(view: View, model: GlobalRecordModel, checkpoint: str) -> dict[int, tuple[int, int]]:
    record_payload = view.page_optional(checkpoint, model.record.page)
    if record_payload is None:
        return {}
    state = indirect_state(record_payload, model.record.start, model.record.end)
    if state is None:
        return {}
    result: dict[int, tuple[int, int]] = {}
    for slot, reference in enumerate(state.slots):
        if reference == 0:
            continue
        payload = view.page_optional(checkpoint, reference)
        if payload is None or payload[0] != 0x05:
            return {}
        raw = int.from_bytes(payload[EXTENDED_HEADER_BYTES:], "little")
        if model.bit_polarity == "set_means_not_in_use":
            raw ^= (1 << EXTENDED_BITS) - 1
        result[slot] = (reference, raw)
    return result


def _bitmap_bit(bits: int, index: int) -> bool:
    return bool(bits & (1 << index))


def _zero_range(bits: int, start: int, end: int) -> bool:
    if start >= end:
        return True
    return bits & (((1 << (end - start)) - 1) << start) == 0


def formula_holds(
    view: View,
    model: GlobalRecordModel,
    conversion: ConversionModel,
    formula: str,
    *,
    require_flip: bool,
) -> bool:
    """Evaluate R3-G01 conditions (a), then (b), for one formula."""
    left_name, right_name = "P_ABS_16480", "H_REL_0064"
    before = _extended_bits(view, model, left_name).get(0)
    after = _extended_bits(view, model, right_name).get(0)
    if before is None or after is None or before[0] != after[0]:
        return False
    reference, before_bits = before
    after_bits = after[1]
    flips = before_bits ^ after_bits
    if require_flip and not flips:
        return False
    candidate_stop = candidate_page_space((view,)).stop
    pending = flips
    while pending:
        bit = (pending & -pending).bit_length() - 1
        page = extended_base(formula, 0, reference) + bit
        if not 0 <= page < 65536 or not 1 <= page < view.page_count(right_name):
            return False
        if page >= candidate_stop:
            return False
        became_in_use = not _bitmap_bit(before_bits, bit) and _bitmap_bit(after_bits, bit)
        if became_in_use:
            if view.hash_at(left_name, page) == view.hash_at(right_name, page):
                return False
        elif page >= view.page_count(left_name):
            return False
        pending &= pending - 1

    for checkpoint in VALIDITY_CHECKPOINTS:
        if CHECKPOINT_ORDINALS[checkpoint] < conversion.conversion_ordinal:
            continue
        record_payload = view.page_optional(checkpoint, model.record.page)
        state = (
            None
            if record_payload is None
            else indirect_state(record_payload, model.record.start, model.record.end)
        )
        extended = _extended_bits(view, model, checkpoint)
        active_count = 0 if state is None else sum(reference != 0 for reference in state.slots)
        if len(extended) != active_count:
            return False
        for slot, (slot_reference, bits) in extended.items():
            origin = extended_base(formula, slot, slot_reference)
            self_bit = slot_reference - origin
            if 0 <= self_bit < EXTENDED_BITS and not _bitmap_bit(bits, self_bit):
                return False
            sentinel_bit = view.page_count(checkpoint) - origin
            if 0 <= sentinel_bit < EXTENDED_BITS and _bitmap_bit(bits, sentinel_bit):
                return False
            first_beyond = max(0, view.page_count(checkpoint) + 1 - origin, -origin)
            end_representable = min(EXTENDED_BITS, 65536 - origin)
            if not _zero_range(bits, first_beyond, end_representable):
                return False
    view.work.charge(bin(flips).count("1") + len(VALIDITY_CHECKPOINTS))
    return True


def derive_base(view: View, model: GlobalRecordModel, conversion: ConversionModel) -> BaseModel:
    before = _extended_bits(view, model, "P_ABS_16480").get(0)
    after = _extended_bits(view, model, "H_REL_0064").get(0)
    if before is None or after is None or before[0] != after[0] or not (after[1] & ~before[1]):
        raise Abort("A3-BASE-DISCRIMINATION")
    survivors = tuple(
        formula
        for formula in BASE_FORMULAS
        if formula_holds(view, model, conversion, formula, require_flip=True)
    )
    if not survivors:
        raise Abort("A3-BASE-NONE")
    if len(survivors) > 1:
        raise Abort("A3-BASE-MULTIPLE")
    return BaseModel(survivors[0])


def _window_values(
    view: View,
    page: int,
    offset: int,
    layout: str,
) -> dict[str, tuple[int, int]] | None:
    values: dict[str, tuple[int, int]] = {}
    for checkpoint in CHECKPOINT_IDS:
        payload = view.page_optional(checkpoint, page)
        if payload is None:
            return None
        values[checkpoint] = decode_pointer(payload[offset:offset + 4], layout)
    return values


def _stable(values: Mapping[str, tuple[int, int]], pairs: Sequence[tuple[str, str]]) -> bool:
    return all(values[left] == values[right] for left, right in pairs)


@dataclass(frozen=True)
class WindowCandidates:
    growth: Mapping[str, tuple[int, ...]]
    churn: Mapping[str, tuple[int, ...]]


def pointer_windows(view: View, page: int) -> WindowCandidates:
    growth = {layout: [] for layout in POINTER_LAYOUTS}
    churn = {layout: [] for layout in POINTER_LAYOUTS}
    for offset in range(PAGE_SIZE - 3):
        for layout in POINTER_LAYOUTS:
            values = _window_values(view, page, offset, layout)
            if values is None:
                continue
            grows = any(values[a] != values[b] for a, b in LOW_GROWTH + HIGH_GROWTH)
            churns = values["L_REL_1280"] != values["L_DELETE_ALL"] and values["L_REL_1280"] == values["L_REINSERT_SAME"]
            growth_shape = grows and _stable(values, CHURN_TRANSITIONS)
            churn_shape = churns and _stable(values, LOW_GROWTH)
            if growth_shape:
                growth[layout].append(offset)
            if churn_shape:
                churn[layout].append(offset)
    return WindowCandidates(
        {key: tuple(value) for key, value in growth.items()},
        {key: tuple(value) for key, value in churn.items()},
    )


def _stable_byte(view: View, page: int, offset: int) -> bool:
    payloads = [view.page_optional(checkpoint, page) for checkpoint in CHECKPOINT_IDS]
    return all(payload is not None for payload in payloads) and len({
        payload[offset] for payload in payloads if payload is not None
    }) == 1


def tdef_models(view: View, page: int, windows: WindowCandidates) -> tuple[TdefModel, ...]:
    payloads = [view.page_optional(checkpoint, page) for checkpoint in CHECKPOINT_IDS]
    if any(payload is None for payload in payloads):
        return ()
    changed = Prefix.from_flags([
        len({payload[offset] for payload in payloads if payload is not None}) > 1
        for offset in range(PAGE_SIZE)
    ], view.work)
    models: list[TdefModel] = []
    for layout in POINTER_LAYOUTS:
        for growth in windows.growth[layout]:
            for churn in windows.churn[layout]:
                if abs(growth - churn) < 4:
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


def _tdef_valid(view: View, model: TdefModel) -> bool:
    offsets = (model.growth_pointer_offset, model.delete_reinsert_pointer_offset)
    references = tuple(
        (
            offset if model.pointer_layout == "u24le_page_then_u8_slot" else offset + 1,
            3,
            slot,
        )
        for slot, offset in enumerate(offsets)
    )
    try:
        validate_references(view, model.record.page, references)
    except Abort as exc:
        if exc.predicate_id == "A3-POINTER-VALIDITY":
            return False
        raise
    return True


def _tdef_structural(view: View, model: TdefModel) -> bool:
    growth = _window_values(
        view, model.record.page, model.growth_pointer_offset, model.pointer_layout
    )
    churn = _window_values(
        view,
        model.record.page,
        model.delete_reinsert_pointer_offset,
        model.pointer_layout,
    )
    if growth is None or churn is None:
        return False
    return _stable(growth, D_TRANSITIONS + IDLE_PAIRS) and _stable(
        churn,
        D_TRANSITIONS + LOW_GROWTH + P_GROWTH + HIGH_GROWTH + IDLE_PAIRS,
    )


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
        raise Abort("A3-GROWTH-POINTER-NONE")
    if not any(any(window.churn.values()) for window in all_windows.values()):
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
    if not _tdef_valid(view, total[0]):
        raise Abort("A3-POINTER-VALIDITY")
    if not _tdef_structural(view, total[0]):
        raise Abort("A3-STRUCTURAL-EXCLUSION")
    return tuple(total)


def predicts_global(view: View, frozen: GlobalRecordModel) -> bool:
    return frozen_global_model_holds(view, frozen)


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
        return formula_holds(
            view,
            global_model,
            conversion,
            frozen.extended_base_formula,
            require_flip=False,
        )
    except Abort as exc:
        if exc.predicate_id in {"A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"}:
            raise
        return False


def predicts_tdef(view: View, frozen: TdefModel, churn_precondition_met: bool) -> bool:
    if not churn_precondition_met:
        return False
    page = frozen.record.page
    if qualify_tdef_pages(view, (page,)) != (page,):
        return False
    growth_values = _window_values(
        view, page, frozen.growth_pointer_offset, frozen.pointer_layout
    )
    churn_values = _window_values(
        view, page, frozen.delete_reinsert_pointer_offset, frozen.pointer_layout
    )
    if growth_values is None or churn_values is None:
        return False
    growth_holds = (
        any(
            growth_values[left] != growth_values[right]
            for left, right in LOW_GROWTH + HIGH_GROWTH
        )
        and _stable(growth_values, CHURN_TRANSITIONS)
    )
    churn_holds = (
        churn_values["L_REL_1280"] != churn_values["L_DELETE_ALL"]
        and churn_values["L_REL_1280"] == churn_values["L_REINSERT_SAME"]
        and _stable(churn_values, LOW_GROWTH)
    )
    if not growth_holds or not churn_holds:
        return False
    windows = WindowCandidates(
        {
            layout: ((frozen.growth_pointer_offset,) if layout == frozen.pointer_layout else ())
            for layout in POINTER_LAYOUTS
        },
        {
            layout: ((frozen.delete_reinsert_pointer_offset,) if layout == frozen.pointer_layout else ())
            for layout in POINTER_LAYOUTS
        },
    )
    return (
        tdef_models(view, page, windows) == (frozen,)
        and _tdef_valid(view, frozen)
        and _tdef_structural(view, frozen)
    )
