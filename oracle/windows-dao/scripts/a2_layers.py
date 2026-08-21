#!/usr/bin/env python3
"""Post-delimitation global-map layers and the separate A2 TDEF sub-model."""

from __future__ import annotations

from dataclasses import dataclass

from a2_model import (
    BASE_FORMULAS,
    CHECKPOINT_IDS,
    CHURN_TRANSITIONS,
    D_CHECKPOINTS,
    D_TRANSITIONS,
    DRelationIndex,
    GROWTH_TRANSITIONS,
    IDLE_PAIRS,
    PAGE_SIZE,
    POINTER_LAYOUTS,
    TRANSITIONS,
    Abort,
    GlobalRecordModel,
    Prefix,
    Record,
    View,
    decode_pointer,
    extended_base,
)

CONVERSION_WINDOW = tuple(TRANSITIONS["inline_to_indirect_conversion_window"])
VALIDITY_CHECKPOINTS = tuple(TRANSITIONS["pointer_validity_checkpoints"])
CONVERSION_GROWTH = tuple(
    (left, right)
    for left, right in zip(CONVERSION_WINDOW, CONVERSION_WINDOW[1:], strict=False)
    if (left, right) != ("L_REL_1280", "L_REINSERT_SAME")
)
EXTENDED_HEADER_BYTES = 4
EXTENDED_BITS = (PAGE_SIZE - EXTENDED_HEADER_BYTES) * 8


@dataclass(frozen=True)
class ConversionModel:
    conversion_checkpoint_id: str
    conversion_ordinal: int
    active_slot_count_at_conversion: int
    active_slot_count_at_h_rel_0904: int
    inline_boundary: int
    slot_reference_pages: tuple[int, ...]

    def document(self) -> dict[str, object]:
        return {
            "conversion_checkpoint_id": self.conversion_checkpoint_id,
            "conversion_ordinal": self.conversion_ordinal,
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

    def document(self) -> dict[str, object]:
        return {
            "record": self.record.document(),
            "pointer_layout": self.pointer_layout,
            "growth_pointer_offset": self.growth_pointer_offset,
            "delete_reinsert_pointer_offset": self.delete_reinsert_pointer_offset,
        }


def _active_slots(record: bytes, start: int) -> dict[int, int]:
    slots: dict[int, int] = {}
    for slot in range(2):
        offset = start + 1 + slot * 4
        value = int.from_bytes(record[offset : offset + 4], "little")
        if value:
            slots[slot] = value
    return slots


def _conversion_checkpoint(view: View, global_model: GlobalRecordModel) -> str:
    start = global_model.record.start
    tags = [
        view.page(checkpoint, global_model.record.page)[start]
        for checkpoint in CONVERSION_WINDOW
    ]
    transitions = [index for index in range(1, len(tags)) if tags[index] != tags[index - 1]]
    if not transitions or 1 not in tags:
        raise Abort("A2-CONVERSION-NONE")
    if (
        len(transitions) != 1
        or transitions[0] == 0
        or any(tag != 0 for tag in tags[: transitions[0]])
        or any(tag != 1 for tag in tags[transitions[0] :])
    ):
        raise Abort("A2-CONVERSION-MULTIPLE")
    return CONVERSION_WINDOW[transitions[0]]


def _validate_slot_references(
    view: View, global_model: GlobalRecordModel, conversion_checkpoint: str
) -> None:
    start = global_model.record.start
    active_seen = False
    conversion_at = CHECKPOINT_IDS.index(conversion_checkpoint)
    for checkpoint in VALIDITY_CHECKPOINTS:
        if CHECKPOINT_IDS.index(checkpoint) < conversion_at:
            continue
        slots = _active_slots(view.page(checkpoint, global_model.record.page), start)
        for reference in slots.values():
            active_seen = True
            if not 0 < reference < view.page_count(checkpoint):
                raise Abort("A2-POINTER-VALIDITY")
            if view.page(checkpoint, reference)[0] != 0x05:
                raise Abort("A2-POINTER-VALIDITY")
    if not active_seen:
        raise Abort("A2-POINTER-VALIDITY")


def _polarity_cross_check(view: View, global_model: GlobalRecordModel) -> None:
    page = global_model.record.page
    start = global_model.record.start + 5
    end = global_model.record.end
    expect_set = global_model.bit_polarity == "set_means_in_use"
    for left, right in CONVERSION_GROWTH:
        if view.page_count(right) <= view.page_count(left):
            continue
        before_record = view.page(left, page)
        after_record = view.page(right, page)
        before_tag = before_record[global_model.record.start]
        after_tag = after_record[global_model.record.start]
        pairs: list[tuple[bytes, bytes]] = []
        if before_tag == after_tag == 0:
            pairs.append((before_record[start:end], after_record[start:end]))
        elif before_tag == after_tag == 1:
            before_slots = _active_slots(before_record, global_model.record.start)
            after_slots = _active_slots(after_record, global_model.record.start)
            for slot, reference in after_slots.items():
                prior_reference = before_slots.get(slot)
                before = (
                    view.page(left, prior_reference)[EXTENDED_HEADER_BYTES:]
                    if prior_reference is not None
                    else bytes(PAGE_SIZE - EXTENDED_HEADER_BYTES)
                )
                after = view.page(right, reference)[EXTENDED_HEADER_BYTES:]
                pairs.append((before, after))
        else:
            # The representation changes at conversion; D has already fixed
            # polarity and the same-direction checks resume after conversion.
            continue
        changed_in_direction = any(
            any(
                ((~old) & new & 0xFF) if expect_set else (old & (~new) & 0xFF)
                for old, new in zip(before, after, strict=True)
            )
            for before, after in pairs
        )
        view.work.charge(sum(len(before) for before, _ in pairs) + 1)
        if not changed_in_direction:
            raise Abort("A2-POLARITY-CROSSCHECK")


def _inline_predicates(
    view: View, global_model: GlobalRecordModel, conversion_checkpoint: str
) -> tuple[int, tuple[str, ...], dict[str, bytes], Prefix, Prefix]:
    record = global_model.record
    start = record.start
    conversion_index = CONVERSION_WINDOW.index(conversion_checkpoint)
    inline_checkpoints = CONVERSION_WINDOW[:conversion_index]
    payloads = {
        checkpoint: view.page(checkpoint, record.page) for checkpoint in CONVERSION_WINDOW
    }
    bitmap_start = start + 5
    mismatch_flags: list[bool] = []
    nonzero_flags: list[bool] = []
    for offset in range(PAGE_SIZE):
        mismatch = False
        for checkpoint in inline_checkpoints:
            payload = payloads[checkpoint]
            first_page = int.from_bytes(payload[start + 1 : start + 5], "little")
            wanted_count = max(0, view.page_count(checkpoint) - first_page)
            relative_bit = max(0, offset - bitmap_start) * 8
            wanted = 0
            for bit in range(8):
                if relative_bit + bit < wanted_count:
                    wanted |= 1 << bit
            if global_model.bit_polarity == "set_means_not_in_use":
                wanted ^= 0xFF
            mismatch |= payload[offset] != wanted
        mismatch_flags.append(mismatch)
        nonzero_flags.append(any(payload[offset] != 0 for payload in payloads.values()))
    mismatch = Prefix.from_flags(mismatch_flags, view.work)
    nonzero = Prefix.from_flags(nonzero_flags, view.work)
    return bitmap_start, inline_checkpoints, payloads, mismatch, nonzero


def _inline_boundary_status(
    view: View,
    global_model: GlobalRecordModel,
    boundary: int,
    predicates: tuple[int, tuple[str, ...], dict[str, bytes], Prefix, Prefix],
) -> str:
    bitmap_start, inline_checkpoints, payloads, mismatch, nonzero = predicates
    start = global_model.record.start
    capacity = (boundary - bitmap_start) * 8
    capacity_witnessed = any(
        max(
            0,
            view.page_count(checkpoint)
            - int.from_bytes(payloads[checkpoint][start + 1 : start + 5], "little"),
        )
        == capacity
        for checkpoint in inline_checkpoints
    )
    if not capacity_witnessed or not mismatch.none(bitmap_start, boundary):
        return "unexplained"
    if not nonzero.none(boundary, global_model.record.end):
        return "suffix"
    return "survives"


def _inline_boundaries(
    view: View, global_model: GlobalRecordModel, conversion_checkpoint: str
) -> tuple[int, ...]:
    record = global_model.record
    predicates = _inline_predicates(view, global_model, conversion_checkpoint)
    explained: list[int] = []
    quiet: list[int] = []
    for boundary in range(record.start + 5, record.end + 1):
        status = _inline_boundary_status(view, global_model, boundary, predicates)
        if status != "unexplained":
            explained.append(boundary)
        if status == "survives":
            quiet.append(boundary)
    if not explained:
        raise Abort("A2-INLINE-BOUNDARY-NONE")
    if not quiet:
        raise Abort("A2-INLINE-SUFFIX")
    if len(quiet) > 1:
        raise Abort("A2-INLINE-BOUNDARY-MULTIPLE")
    return tuple(quiet)


def derive_conversion(view: View, global_model: GlobalRecordModel) -> ConversionModel:
    """Evaluate L/P/H only after the D record and polarity are frozen."""
    conversion = _conversion_checkpoint(view, global_model)
    start = global_model.record.start
    at_conversion = _active_slots(view.page(conversion, global_model.record.page), start)
    if not at_conversion:
        raise Abort("A2-SLOT-ACTIVATION")
    final = _active_slots(view.page("H_REL_0904", global_model.record.page), start)
    if len(final) != 2:
        raise Abort("A2-SLOT-FINAL")
    _validate_slot_references(view, global_model, conversion)
    _polarity_cross_check(view, global_model)
    boundary = _inline_boundaries(view, global_model, conversion)[0]
    return ConversionModel(
        conversion,
        CHECKPOINT_IDS.index(conversion),
        len(at_conversion),
        len(final),
        boundary,
        tuple(final.values()),
    )


def _extended_state(
    view: View, global_model: GlobalRecordModel, checkpoint: str
) -> dict[int, tuple[int, int]]:
    record_page = view.page(checkpoint, global_model.record.page)
    result: dict[int, tuple[int, int]] = {}
    for slot, reference in _active_slots(record_page, global_model.record.start).items():
        if not 0 < reference < view.page_count(checkpoint):
            raise Abort("A2-POINTER-VALIDITY")
        payload = view.page(checkpoint, reference)
        if payload[0] != 0x05:
            raise Abort("A2-POINTER-VALIDITY")
        raw = int.from_bytes(payload[EXTENDED_HEADER_BYTES:], "little")
        if global_model.bit_polarity == "set_means_not_in_use":
            raw ^= (1 << EXTENDED_BITS) - 1
        result[slot] = (reference, raw)
    return result


def _base_inputs(
    view: View, global_model: GlobalRecordModel, conversion: ConversionModel
) -> tuple[list[tuple[str, str]], dict[str, dict[int, tuple[int, int]]]]:
    conversion_at = CONVERSION_WINDOW.index(conversion.conversion_checkpoint_id)
    pairs = [
        (left, right)
        for left, right in CONVERSION_GROWTH
        if CONVERSION_WINDOW.index(left) >= conversion_at
        and view.page_count(right) > view.page_count(left)
    ]
    states = {
        checkpoint: _extended_state(view, global_model, checkpoint)
        for pair in pairs
        for checkpoint in pair
    }
    return pairs, states


def _base_formula_matches(
    view: View,
    formula: str,
    pairs: list[tuple[str, str]],
    states: dict[str, dict[int, tuple[int, int]]],
) -> bool:
    for left, right in pairs:
        before = states[left]
        after = states[right]
        predicted: set[int] = set()
        for slot, (reference, bits) in after.items():
            fresh = bits & ~before.get(slot, (0, 0))[1]
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


def base_candidates(
    view: View, global_model: GlobalRecordModel, conversion: ConversionModel
) -> tuple[str, ...]:
    pairs, states = _base_inputs(view, global_model, conversion)
    discriminator = states.get("H_REL_0064", {}).get(0, (0, 0))[1] & ~states.get(
        "P_ABS_16480", {}
    ).get(0, (0, 0))[1]
    if discriminator == 0:
        raise Abort("A2-BASE-DISCRIMINATION")
    survivors: list[str] = []
    for formula in BASE_FORMULAS:
        if _base_formula_matches(view, formula, pairs, states):
            survivors.append(formula)
    return tuple(survivors)


def derive_base(
    view: View, global_model: GlobalRecordModel, conversion: ConversionModel
) -> BaseModel:
    survivors = base_candidates(view, global_model, conversion)
    if not survivors:
        raise Abort("A2-BASE-NONE")
    if len(survivors) > 1:
        raise Abort("A2-BASE-MULTIPLE")
    return BaseModel(survivors[0])


def _window_values(view: View, page: int, offset: int, layout: str) -> dict[str, tuple[int, int]]:
    return {
        checkpoint: decode_pointer(view.page(checkpoint, page)[offset : offset + 4], layout)
        for checkpoint in CHECKPOINT_IDS
    }


def _stable(values: dict[str, tuple[int, int]], pairs: tuple[tuple[str, str], ...]) -> bool:
    return all(values[left] == values[right] for left, right in pairs)


def _valid_pointer_values(view: View, values: dict[str, tuple[int, int]]) -> bool:
    active = False
    for checkpoint in VALIDITY_CHECKPOINTS:
        page, _ = values[checkpoint]
        if page == 0:
            continue
        active = True
        if page >= view.page_count(checkpoint) or view.page(checkpoint, page)[0] != 0x05:
            return False
    return active


def _growth_pointer_matches(view: View, values: dict[str, tuple[int, int]]) -> bool:
    grows = any(values[left] != values[right] for left, right in GROWTH_TRANSITIONS)
    outside = D_TRANSITIONS + CHURN_TRANSITIONS + IDLE_PAIRS
    return (
        grows
        and _stable(values, CHURN_TRANSITIONS)
        and _stable(values, outside)
        and _valid_pointer_values(view, values)
    )


def _churn_pointer_matches(view: View, values: dict[str, tuple[int, int]]) -> bool:
    churns = values["L_REL_1280"] != values["L_DELETE_ALL"] and (
        values["L_REL_1280"] == values["L_REINSERT_SAME"]
    )
    outside = D_TRANSITIONS + GROWTH_TRANSITIONS + IDLE_PAIRS
    return (
        churns
        and _stable(values, GROWTH_TRANSITIONS)
        and _stable(values, outside)
        and _valid_pointer_values(view, values)
    )


def _pointer_candidates(
    view: View, page: int
) -> tuple[dict[str, list[int]], dict[str, list[int]], bool, bool]:
    growth = {layout: [] for layout in POINTER_LAYOUTS}
    churn = {layout: [] for layout in POINTER_LAYOUTS}
    structural_failure = False
    validity_failure = False
    outside_growth = D_TRANSITIONS + CHURN_TRANSITIONS + IDLE_PAIRS
    outside_churn = D_TRANSITIONS + GROWTH_TRANSITIONS + IDLE_PAIRS
    for offset in range(PAGE_SIZE - 3):
        for layout in POINTER_LAYOUTS:
            values = _window_values(view, page, offset, layout)
            grows = any(values[a] != values[b] for a, b in GROWTH_TRANSITIONS)
            churns = values["L_REL_1280"] != values["L_DELETE_ALL"] and (
                values["L_REL_1280"] == values["L_REINSERT_SAME"]
            )
            growth_shape = grows and _stable(values, CHURN_TRANSITIONS)
            churn_shape = churns and _stable(values, GROWTH_TRANSITIONS)
            if growth_shape and not _stable(values, outside_growth):
                structural_failure = True
            elif growth_shape:
                if _valid_pointer_values(view, values):
                    growth[layout].append(offset)
                else:
                    validity_failure = True
            if churn_shape and not _stable(values, outside_churn):
                structural_failure = True
            elif churn_shape:
                if _valid_pointer_values(view, values):
                    churn[layout].append(offset)
                else:
                    validity_failure = True
    return growth, churn, structural_failure, validity_failure


def _stable_byte(view: View, page: int, offset: int) -> bool:
    return len({view.page(checkpoint, page)[offset] for checkpoint in CHECKPOINT_IDS}) == 1


def derive_tdef(
    view: View,
    page: int,
    churn_precondition_met: bool,
    *,
    enumerate_candidates: bool = True,
) -> TdefModel:
    if enumerate_candidates:
        view.work.enumerate_intervals()
    if not churn_precondition_met:
        raise Abort("A2-CHURN-PRECONDITION")
    growth, churn, structural, invalid = _pointer_candidates(view, page)
    if not any(growth.values()):
        if structural:
            raise Abort("A2-STRUCTURAL-EXCLUSION")
        if invalid:
            raise Abort("A2-POINTER-VALIDITY")
        raise Abort("A2-GROWTH-POINTER-NONE")
    if not any(churn.values()):
        if structural:
            raise Abort("A2-STRUCTURAL-EXCLUSION")
        if invalid:
            raise Abort("A2-POINTER-VALIDITY")
        raise Abort("A2-CHURN-POINTER-NONE")
    models: list[TdefModel] = []
    undelimited = False
    changed = Prefix.from_flags(
        [
            len({view.page(checkpoint, page)[offset] for checkpoint in CHECKPOINT_IDS}) > 1
            for offset in range(PAGE_SIZE)
        ],
        view.work,
    )
    for layout in POINTER_LAYOUTS:
        for growth_offset in growth[layout]:
            for churn_offset in churn[layout]:
                if abs(growth_offset - churn_offset) < 4:
                    continue
                core_start = min(growth_offset, churn_offset)
                core_end = max(growth_offset, churn_offset) + 4
                start = core_start - 1 if core_start else core_start
                end = core_end + 1 if core_end < PAGE_SIZE else core_end
                if (start < core_start and not _stable_byte(view, page, start)) or (
                    end > core_end and not _stable_byte(view, page, end - 1)
                ):
                    undelimited = True
                    continue
                outside = (
                    changed.count(start, growth_offset)
                    + changed.count(growth_offset + 4, churn_offset)
                    + changed.count(churn_offset + 4, end)
                    if growth_offset < churn_offset
                    else changed.count(start, churn_offset)
                    + changed.count(churn_offset + 4, growth_offset)
                    + changed.count(growth_offset + 4, end)
                )
                if outside:
                    undelimited = True
                    continue
                models.append(
                    TdefModel(Record(page, start, end), layout, growth_offset, churn_offset)
                )
    view.work.examine_models(len(models))
    if not models:
        if undelimited:
            raise Abort("A2-TDEF-RECORD-NONE")
        raise Abort("A2-POINTER-MULTIPLE")
    records = {model.record for model in models}
    if len(records) > 1:
        raise Abort("A2-TDEF-RECORD-MULTIPLE")
    if len(models) > 1:
        raise Abort("A2-POINTER-MULTIPLE")
    return models[0]


def predicts_global(view: View, frozen: GlobalRecordModel) -> bool:
    """Apply one frozen record and polarity; do not enumerate holdout candidates."""
    records = {name: view.page(name, frozen.record.page) for name in D_CHECKPOINTS}
    index = DRelationIndex(records, view.work)
    return index.relation(frozen.record.start, frozen.bit_polarity) and index.suffix_slack(
        frozen.record.start, frozen.bit_polarity
    ) == frozen.zero_suffix_slack_bytes


def predicts_conversion(
    view: View, global_model: GlobalRecordModel, frozen: ConversionModel
) -> bool:
    try:
        checkpoint = _conversion_checkpoint(view, global_model)
        if checkpoint != frozen.conversion_checkpoint_id:
            return False
        start = global_model.record.start
        active = _active_slots(view.page(checkpoint, global_model.record.page), start)
        if len(active) != frozen.active_slot_count_at_conversion:
            return False
        if len(_active_slots(view.page("H_REL_0904", global_model.record.page), start)) != 2:
            return False
        _validate_slot_references(view, global_model, checkpoint)
        _polarity_cross_check(view, global_model)
        return _inline_boundary_matches(view, global_model, checkpoint, frozen.inline_boundary)
    except Abort:
        return False


def _inline_boundary_matches(
    view: View, global_model: GlobalRecordModel, conversion_checkpoint: str, boundary: int
) -> bool:
    if not global_model.record.start + 5 <= boundary <= global_model.record.end:
        return False
    predicates = _inline_predicates(view, global_model, conversion_checkpoint)
    return _inline_boundary_status(view, global_model, boundary, predicates) == "survives"


def predicts_base(
    view: View, global_model: GlobalRecordModel, conversion: ConversionModel, frozen: BaseModel
) -> bool:
    try:
        pairs, states = _base_inputs(view, global_model, conversion)
        discriminator = states.get("H_REL_0064", {}).get(0, (0, 0))[1] & ~states.get(
            "P_ABS_16480", {}
        ).get(0, (0, 0))[1]
        return discriminator != 0 and _base_formula_matches(
            view, frozen.extended_base_formula, pairs, states
        )
    except Abort:
        return False


def predicts_tdef(view: View, frozen: TdefModel, churn_precondition_met: bool) -> bool:
    """Test the frozen page, record, layout and offsets without candidate search."""
    if not churn_precondition_met:
        return False
    try:
        growth = _window_values(
            view, frozen.record.page, frozen.growth_pointer_offset, frozen.pointer_layout
        )
        churn = _window_values(
            view,
            frozen.record.page,
            frozen.delete_reinsert_pointer_offset,
            frozen.pointer_layout,
        )
        if not _growth_pointer_matches(view, growth):
            return False
        if not _churn_pointer_matches(view, churn):
            return False
        core_start = min(frozen.growth_pointer_offset, frozen.delete_reinsert_pointer_offset)
        core_end = max(frozen.growth_pointer_offset, frozen.delete_reinsert_pointer_offset) + 4
        expected = Record(
            frozen.record.page,
            core_start - 1 if core_start else core_start,
            core_end + 1 if core_end < PAGE_SIZE else core_end,
        )
        if expected != frozen.record or (
            expected.start < core_start
            and not _stable_byte(view, expected.page, expected.start)
        ):
            return False
        if expected.end > core_end and not _stable_byte(
            view, expected.page, expected.end - 1
        ):
            return False
        changed = Prefix.from_flags(
            [
                len(
                    {
                        view.page(checkpoint, expected.page)[offset]
                        for checkpoint in CHECKPOINT_IDS
                    }
                )
                > 1
                for offset in range(PAGE_SIZE)
            ],
            view.work,
        )
        first = min(frozen.growth_pointer_offset, frozen.delete_reinsert_pointer_offset)
        second = max(frozen.growth_pointer_offset, frozen.delete_reinsert_pointer_offset)
        outside = (
            changed.count(expected.start, first)
            + changed.count(first + 4, second)
            + changed.count(second + 4, expected.end)
        )
        return outside == 0
    except Abort:
        return False
