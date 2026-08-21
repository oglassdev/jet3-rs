"""Synthetic global-map and TDEF page-state construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from a2_generator_schedule import Schedule
from a2_spec import BASE_FORMULAS, BIT_POLARITIES, CHECKPOINT_IDS, PAGE_SIZE, POINTER_LAYOUTS, ROLES
from protocol_validation import ValidationError


@dataclass(frozen=True)
class PageFixture:
    ordered_hashes: dict[str, tuple[str, ...]]
    payloads: dict[str, bytes]
    global_page: int
    tdef_page: int
    global_record: tuple[int, int]
    tdef_record: tuple[int, int]
    growth_pointer_offset: int
    delete_reinsert_pointer_offset: int
    inline_boundary: int
    inline_base: int
    pointer_layout: str
    extended_base_formula: str


def _set_bit(payload: bytearray, byte_start: int, bit: int, value: bool) -> None:
    offset, shift = divmod(bit, 8)
    mask = 1 << shift
    if value:
        payload[byte_start + offset] |= mask
    else:
        payload[byte_start + offset] &= ~mask


def _encode_reference(payload: bytearray, offset: int, page: int, slot: int, layout: str) -> None:
    if layout == "u24le_page_then_u8_slot":
        payload[offset : offset + 3] = page.to_bytes(3, "little")
        payload[offset + 3] = slot
    elif layout == "u8_slot_then_u24le_page":
        payload[offset] = slot
        payload[offset + 1 : offset + 4] = page.to_bytes(3, "little")
    else:
        raise ValidationError(f"unknown plan pointer layout {layout!r}")


def _extended_page(
    bit_polarity: str,
    in_use_bits: range | tuple[int, ...],
) -> bytes:
    header_bytes = len(ROLES)
    not_in_use_raw = 0xFF if bit_polarity == "set_means_not_in_use" else 0x00
    payload = bytearray([not_in_use_raw]) * PAGE_SIZE
    payload[:header_bytes] = bytes(header_bytes)
    payload[0] = len(ROLES) + 1
    in_use_bit = bit_polarity == "set_means_in_use"
    capacity = (PAGE_SIZE - header_bytes) * 8
    for bit in in_use_bits:
        if not 0 <= bit < capacity:
            raise ValidationError("synthetic extended-map bit exceeds its plan-derived page")
        _set_bit(payload, header_bytes, bit, in_use_bit)
    return bytes(payload)


def _anchor_checkpoint_id(
    schedule: Schedule, conversion_ordinal: int | None
) -> str:
    cutoff = len(CHECKPOINT_IDS) if conversion_ordinal is None else conversion_ordinal
    predecessors = [
        checkpoint_id
        for checkpoint_id in schedule.conversion_window
        if CHECKPOINT_IDS.index(checkpoint_id) < cutoff
    ]
    return predecessors[-1] if predecessors else schedule.conversion_window[0]


def _active_slot_count(
    row_ordinal: int,
    conversion_ordinal: int,
    requested_at_conversion: int,
    final_ordinal: int,
    slot_count: int,
) -> int:
    if row_ordinal == conversion_ordinal:
        return requested_at_conversion
    if conversion_ordinal >= final_ordinal:
        return requested_at_conversion
    if row_ordinal >= final_ordinal:
        return slot_count
    return max(requested_at_conversion, int(row_ordinal > conversion_ordinal))


def build_page_fixture(
    schedule: Schedule,
    *,
    conversion_ordinal: int | None,
    slot_activation_at_conversion: int,
    bit_polarity: str,
    anchor_fill_state: str,
    record_end_uniform_slack_bytes: int,
) -> PageFixture:
    """Build every checkpoint page state from schedule-derived quantities."""
    if bit_polarity not in BIT_POLARITIES:
        raise ValidationError("bit_polarity is absent from the checked plan")
    if anchor_fill_state not in ("empty", "partial", "full"):
        raise ValidationError("unknown anchor fill state")
    if slot_activation_at_conversion not in (0, 1, 2):
        raise ValidationError("slot activation count must be 0, 1, or 2")
    if conversion_ordinal is not None and not 1 <= conversion_ordinal < len(CHECKPOINT_IDS):
        raise ValidationError("conversion ordinal lies outside the A2 schedule")

    physical_names = len(ROLES)
    global_page = physical_names
    tdef_page = global_page + int(global_page + 1 < schedule.initial_pages)
    anchor_id = _anchor_checkpoint_id(schedule, conversion_ordinal)
    anchor_pages = schedule.checkpoint(anchor_id).actual_file_pages
    type_bytes = int(bool(BASE_FORMULAS))
    inline_base_bytes = len(ROLES)
    maximum_bitmap_bytes = (
        PAGE_SIZE
        - type_bytes
        - inline_base_bytes
        - record_end_uniform_slack_bytes
    )
    bitmap_bytes = min(
        maximum_bitmap_bytes,
        max(1, (anchor_pages - 1) // 8),
    )
    bitmap_bits = bitmap_bytes * 8
    record_bytes = (
        type_bytes
        + inline_base_bytes
        + bitmap_bytes
        + record_end_uniform_slack_bytes
    )
    global_start = PAGE_SIZE - record_bytes
    if global_start < 0:
        raise ValidationError("record-end slack exceeds the synthetic global page")
    bitmap_start = global_start + type_bytes + inline_base_bytes
    inline_boundary = PAGE_SIZE - record_end_uniform_slack_bytes
    inline_base = max(1, anchor_pages - bitmap_bits)
    tdef_start = len(ROLES) - len(ROLES)
    stable_flank = int(schedule.batch_rows > 0)
    pointer_bytes = len(ROLES) * 2
    tdef_end = tdef_start + pointer_bytes + stable_flank
    layout = POINTER_LAYOUTS[0]
    growth_pointer = tdef_start
    churn_pointer = growth_pointer + pointer_bytes // 2
    not_in_use_raw = 0xFF if bit_polarity == "set_means_not_in_use" else 0x00

    p_low = schedule.checkpoint("P_ABS_12288").target_threshold_pages
    p_high = schedule.checkpoint("P_ABS_16480").target_threshold_pages
    if p_low is None or p_high is None:
        raise ValidationError("absolute plan targets are missing")
    reference_pages = (p_low, p_high)
    filler = bytearray(PAGE_SIZE)
    filler[0] = len(ROLES) + stable_flank
    filler_bytes = bytes(filler)
    filler_hash = hashlib.sha256(filler_bytes).hexdigest()
    payloads: dict[str, bytes] = {filler_hash: filler_bytes}
    ordered: dict[str, tuple[str, ...]] = {}
    growth_generation = 0
    growth_reference_base = (
        schedule.checkpoint("D_REGROW_0128").actual_file_pages
        // len(BIT_POLARITIES)
    )
    stable_growth_ref = growth_reference_base
    churn_ref = max(1, schedule.initial_pages - stable_flank)
    d_checkpoints = {
        "E0",
        "E0R",
        "D_GROW_0128",
        "D_DROP",
        "D_RECREATE_EMPTY",
        "D_REGROW_0128",
    }

    for row in schedule.checkpoints:
        checkpoint_id = row.checkpoint_id
        global_payload = bytearray([0xA5]) * PAGE_SIZE
        global_payload[global_start:PAGE_SIZE] = bytes(record_bytes)
        indirect = (
            conversion_ordinal is not None
            and row.ordinal >= conversion_ordinal
        )
        global_payload[global_start] = int(indirect)
        if indirect:
            active = _active_slot_count(
                row.ordinal,
                conversion_ordinal,
                slot_activation_at_conversion,
                CHECKPOINT_IDS.index("H_REL_0904"),
                len(reference_pages),
            )
            for slot, reference in enumerate(reference_pages):
                value = reference if slot < active else 0
                offset = global_start + type_bytes + slot * len(ROLES)
                global_payload[offset : offset + len(ROLES)] = value.to_bytes(
                    len(ROLES), "little"
                )
        elif checkpoint_id in d_checkpoints:
            global_payload[bitmap_start:PAGE_SIZE] = (
                bytes([not_in_use_raw])
                * (PAGE_SIZE - bitmap_start)
            )
            global_payload[
                global_start + type_bytes : bitmap_start
            ] = inline_base.to_bytes(inline_base_bytes, "little")
            in_use_bit = bit_polarity == "set_means_in_use"
            if checkpoint_id in ("D_GROW_0128", "D_REGROW_0128"):
                _set_bit(global_payload, bitmap_start, 0, in_use_bit)
            if checkpoint_id == "D_REGROW_0128":
                _set_bit(
                    global_payload,
                    bitmap_start,
                    bitmap_bits - 1,
                    in_use_bit,
                )
        else:
            global_payload[bitmap_start:inline_boundary] = (
                bytes([not_in_use_raw]) * bitmap_bytes
            )
            global_payload[
                global_start + type_bytes : bitmap_start
            ] = inline_base.to_bytes(inline_base_bytes, "little")
            represented = min(
                bitmap_bits,
                max(0, row.actual_file_pages - inline_base),
            )
            in_use_bit = bit_polarity == "set_means_in_use"
            for page in range(represented):
                _set_bit(global_payload, bitmap_start, page, in_use_bit)

        tdef_payload = bytearray([0xFF]) * PAGE_SIZE
        if checkpoint_id.startswith(("L_REL_", "H_REL_")):
            growth_generation += 1
            stable_growth_ref = min(
                row.actual_file_pages - 1,
                growth_reference_base + growth_generation,
            )
        growth_slot = growth_generation % (len(ROLES) * len(BIT_POLARITIES))
        _encode_reference(tdef_payload, growth_pointer, stable_growth_ref, growth_slot, layout)
        current_churn_ref = 0 if checkpoint_id == "L_DELETE_ALL" else churn_ref
        _encode_reference(tdef_payload, churn_pointer, current_churn_ref, 1, layout)

        page_hashes = [filler_hash] * row.actual_file_pages
        for index, payload in ((global_page, bytes(global_payload)), (tdef_page, bytes(tdef_payload))):
            digest = hashlib.sha256(payload).hexdigest()
            payloads.setdefault(digest, payload)
            page_hashes[index] = digest
        if reference_pages[0] < row.actual_file_pages:
            represented = min(
                (PAGE_SIZE - len(ROLES)) * 8,
                row.actual_file_pages - reference_pages[0],
            )
            payload = _extended_page(bit_polarity, range(represented))
            digest = hashlib.sha256(payload).hexdigest()
            payloads.setdefault(digest, payload)
            page_hashes[reference_pages[0]] = digest
        if reference_pages[1] < row.actual_file_pages:
            payload = _extended_page(bit_polarity, ())
            digest = hashlib.sha256(payload).hexdigest()
            payloads.setdefault(digest, payload)
            page_hashes[reference_pages[1]] = digest
        ordered[checkpoint_id] = tuple(page_hashes)
    return PageFixture(
        ordered_hashes=ordered,
        payloads=payloads,
        global_page=global_page,
        tdef_page=tdef_page,
        global_record=(global_start, PAGE_SIZE),
        tdef_record=(tdef_start, tdef_end),
        growth_pointer_offset=growth_pointer,
        delete_reinsert_pointer_offset=churn_pointer,
        inline_boundary=inline_boundary,
        inline_base=inline_base,
        pointer_layout=layout,
        extended_base_formula=next(
            name
            for name in BASE_FORMULAS
            if name.startswith("referenced_page_relative")
            and "off_by" not in name
        ),
    )
