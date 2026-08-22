#!/usr/bin/env python3
"""Schedule-derived, non-evidential A3 analyzer fixture generator.

A3 rule | implementation
--- | ---
Every free parameter comes from the checked plan | :func:`iter_parameter_cases`
Tag/base/LSB-first inline map and highwaters | :func:`generate_synthetic_bundle`
Tag-1 two-slot indirect record | :func:`generate_synthetic_bundle`
Full-delete churn and growth-only TDEF windows | :func:`generate_synthetic_bundle`
Pointer targets carry byte-zero 0x05 | :func:`generate_synthetic_bundle`
H_REL_0064 unique base discriminator | :func:`generate_synthetic_bundle`
Idle equalities are generator-produced | :func:`generate_synthetic_bundle`
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from itertools import chain
from types import MappingProxyType
from typing import Iterator, Mapping

from protocol_validation import ValidationError
from a3_generator_schedule import Schedule, build_schedule
from a3_model import CHECKPOINT_IDS, PAGE_SIZE, TRANSITIONS, raw_not_in_use
from a3_spec import CHECKPOINT_ORDINALS, PLAN, PLAN_SHA256, POLARITIES, POINTER_LAYOUTS

FREE = PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
CONVERSION_WINDOW = tuple(TRANSITIONS["inline_to_indirect_conversion_window"])
GROWTH_CHECKPOINTS = tuple(chain(TRANSITIONS["tdef_low_growth"], TRANSITIONS["tdef_high_growth"]))


@dataclass(frozen=True)
class SyntheticParameters:
    conversion_ordinal: int | None = 20
    slot_activation_at_conversion: int = 2
    bit_polarity: str = "set_means_not_in_use"
    anchor_fill_state: str = "partial"
    record_end_uniform_slack_bytes: int = 32
    global_record_start: int = 0
    global_record_base: int = 0
    inline_tag_at_anchor: int = 0
    first_representation_change_leg: tuple[str, str] | None = None


@dataclass(frozen=True)
class SyntheticBundle:
    checkpoint_ids: tuple[str, ...]
    page_count: Mapping[str, int]
    ordered_page_sha256: Mapping[str, tuple[str, ...]]
    replica: int
    campaign_id: str
    producer_commit: str
    provider_sha256: str
    churn_precondition_met: bool
    parameters: SyntheticParameters
    schedule: Schedule
    global_page: int
    tdef_page: int
    global_record: tuple[int, int]
    tdef_record: tuple[int, int]
    growth_pointer_offset: int
    churn_pointer_offset: int
    _payloads: Mapping[str, bytes]

    def page_bytes(self, digest: str) -> bytes:
        payload = self._payloads[digest]
        if len(payload) != PAGE_SIZE or hashlib.sha256(payload).hexdigest() != digest:
            raise ValidationError("A3 synthetic content address failed")
        return payload


def calibration_parameters() -> SyntheticParameters:
    return SyntheticParameters(
        conversion_ordinal=20, slot_activation_at_conversion=2,
        bit_polarity="set_means_not_in_use", anchor_fill_state="partial",
        record_end_uniform_slack_bytes=32, global_record_start=0, global_record_base=0,
    )


def exp_0042_calibration_parameters() -> SyntheticParameters:
    return SyntheticParameters(
        conversion_ordinal=20, slot_activation_at_conversion=2,
        bit_polarity="set_means_not_in_use", anchor_fill_state="partial",
        record_end_uniform_slack_bytes=92, global_record_start=1915, global_record_base=0,
    )


def iter_parameter_cases() -> Iterator[tuple[str, SyntheticParameters]]:
    """Cover every value on every frozen axis without an unnecessary Cartesian product."""
    baseline = calibration_parameters()
    for ordinal in (*range(1, len(CHECKPOINT_IDS)), None):
        yield f"conversion_{ordinal}", replace(baseline, conversion_ordinal=ordinal)
    axes = (
        ("slots", "slot_activation_at_conversion", FREE["slot_activation_at_conversion"]),
        ("polarity", "bit_polarity", FREE["bit_polarity"]),
        ("fill", "anchor_fill_state", FREE["anchor_fill_state"]),
        ("slack", "record_end_uniform_slack_bytes", FREE["record_end_uniform_slack_bytes"]),
        ("start", "global_record_start", FREE["global_record_start"]),
        ("base", "global_record_base", FREE["global_record_base"]),
        ("anchor_tag", "inline_tag_at_anchor", FREE["inline_tag_at_anchor"]),
    )
    for label, field, values in axes:
        for value in values:
            yield f"{label}_{value}", replace(baseline, **{field: value})
    for left, right in (*TRANSITIONS["polarity_cross_check_legs"], (None, None)):
        leg = None if left is None else (left, right)
        yield f"representation_{left}_{right}", replace(baseline, first_representation_change_leg=leg)


def _set_bit(payload: bytearray, start: int, bit: int, value: bool) -> None:
    byte, shift = divmod(bit, 8)
    if not 0 <= start + byte < len(payload):
        return
    if value:
        payload[start + byte] |= 1 << shift
    else:
        payload[start + byte] &= ~(1 << shift)


def _inline_payload(parameters: SyntheticParameters, page_count: int, in_use_end: int, *, force_last_flip: bool = False) -> bytes:
    start, base = parameters.global_record_start, parameters.global_record_base
    if not 0 <= start <= PAGE_SIZE - 6:
        return bytes([0xA5]) * PAGE_SIZE
    unused = raw_not_in_use(parameters.bit_polarity)
    payload = bytearray([0xA5]) * PAGE_SIZE
    payload[start:PAGE_SIZE] = bytes([unused]) * (PAGE_SIZE - start)
    payload[start] = 0
    payload[start + 1:start + 5] = base.to_bytes(4, "little")
    means_in_use = parameters.bit_polarity == "set_means_in_use"
    for page in range(base, max(base, in_use_end)):
        _set_bit(payload, start + 5, page - base, means_in_use)
    _set_bit(payload, start + 5, page_count - base, not means_in_use)
    if force_last_flip:
        offset = PAGE_SIZE - parameters.record_end_uniform_slack_bytes - 1
        if offset >= start + 5:
            bit = (offset - (start + 5)) * 8
            _set_bit(payload, start + 5, bit, means_in_use)
    return bytes(payload)


def _indirect_payload(parameters: SyntheticParameters, active: int) -> bytes:
    start = parameters.global_record_start
    payload = bytearray(PAGE_SIZE)
    payload[:start] = bytes([0xA5]) * start
    if start > PAGE_SIZE - 9:
        return bytes(payload)
    payload[start] = 1
    references = (14848, 16352)
    for slot, reference in enumerate(references):
        value = reference if slot < active else 0
        offset = start + 1 + slot * 4
        payload[offset:offset + 4] = value.to_bytes(4, "little")
    return bytes(payload)


def _pointer_page(tag: int = 0x05) -> bytes:
    payload = bytearray(PAGE_SIZE)
    payload[0] = tag
    return bytes(payload)


def _extended_page(parameters: SyntheticParameters, reference: int, page_count: int) -> bytes:
    unused = raw_not_in_use(parameters.bit_polarity)
    payload = bytearray([unused]) * PAGE_SIZE
    payload[:4] = bytes((0x05, 0, 0, 0))
    means_in_use = parameters.bit_polarity == "set_means_in_use"
    if reference == 14848:
        for page in range(reference, page_count):
            _set_bit(payload, 4, page - reference, means_in_use)
    return bytes(payload)


def _encode_pointer(payload: bytearray, offset: int, page: int, slot: int, layout: str) -> None:
    if layout == "u24le_page_then_u8_slot":
        payload[offset:offset + 3] = page.to_bytes(3, "little")
        payload[offset + 3] = slot
    else:
        payload[offset] = slot
        payload[offset + 1:offset + 4] = page.to_bytes(3, "little")


def _tdef_payload(checkpoint: str, growth_index: int, layout: str, growth_offset: int, churn_offset: int) -> bytes:
    payload = bytearray([0xFF]) * PAGE_SIZE
    growth_page = 4 + growth_index % 16
    churn_page = 0 if checkpoint == "L_DELETE_ALL" else 24
    _encode_pointer(payload, growth_offset, growth_page, 1, layout)
    _encode_pointer(payload, churn_offset, churn_page, 2, layout)
    return bytes(payload)


def _active_count(parameters: SyntheticParameters, ordinal: int) -> int:
    conversion = parameters.conversion_ordinal
    if conversion is None or ordinal < conversion:
        return 0
    if ordinal == conversion:
        return parameters.slot_activation_at_conversion
    if conversion >= CHECKPOINT_ORDINALS["H_REL_0904"]:
        return parameters.slot_activation_at_conversion
    return min(2, max(parameters.slot_activation_at_conversion, 1 + int(ordinal > conversion)))


def generate_synthetic_bundle(parameters: SyntheticParameters | None = None, *, replica: int = 1) -> SyntheticBundle:
    selected = calibration_parameters() if parameters is None else parameters
    if selected.bit_polarity not in POLARITIES or selected.anchor_fill_state not in FREE["anchor_fill_state"]:
        raise ValidationError("A3 synthetic parameter is outside the plan")
    if selected.slot_activation_at_conversion not in FREE["slot_activation_at_conversion"]:
        raise ValidationError("A3 slot parameter is outside the plan")
    schedule = build_schedule()
    global_page, tdef_page = 1, 2
    # Offset zero makes the alternate slot-first growth interpretation
    # unavailable. The churn field ends at the page terminal, producing the
    # schema-bound minimal TDEF interval [0, 2048).
    growth_offset, churn_offset = 0, PAGE_SIZE - 4
    layout = POINTER_LAYOUTS[0]
    filler = bytes(PAGE_SIZE)
    filler_digest = hashlib.sha256(filler).hexdigest()
    pointer_payload = _pointer_page()
    pointer_digest = hashlib.sha256(pointer_payload).hexdigest()
    payloads: dict[str, bytes] = {filler_digest: filler, pointer_digest: pointer_payload}
    ordered: dict[str, tuple[str, ...]] = {}
    growth_index = 0
    forced_leg = selected.first_representation_change_leg
    force_at = CHECKPOINT_ORDINALS[forced_leg[1]] if forced_leg else None
    d_empty = schedule.initial_pages
    for row in schedule.checkpoints:
        name = row.checkpoint_id
        if name in GROWTH_CHECKPOINTS:
            growth_index += 1
        if name in {"E0", "E0R", "D_DROP", "D_RECREATE_EMPTY"}:
            global_payload = _inline_payload(selected, row.actual_file_pages, d_empty)
        elif name == "D_GROW_0128":
            global_payload = _inline_payload(selected, row.actual_file_pages, row.actual_file_pages)
        elif name == "D_REGROW_0128":
            global_payload = _inline_payload(selected, row.actual_file_pages, row.actual_file_pages, force_last_flip=True)
        else:
            indirect = selected.conversion_ordinal is not None and row.ordinal >= selected.conversion_ordinal
            if force_at is not None:
                indirect = row.ordinal >= force_at
            if name == CONVERSION_WINDOW[0] and selected.inline_tag_at_anchor == 1:
                indirect = True
            global_payload = _indirect_payload(selected, _active_count(selected, row.ordinal)) if indirect else _inline_payload(selected, row.actual_file_pages, row.actual_file_pages)
        tdef_payload = _tdef_payload(name, growth_index, layout, growth_offset, churn_offset)
        global_digest, tdef_digest = hashlib.sha256(global_payload).hexdigest(), hashlib.sha256(tdef_payload).hexdigest()
        payloads[global_digest], payloads[tdef_digest] = global_payload, tdef_payload
        hashes = [filler_digest] * row.actual_file_pages
        for page in range(4, min(32, row.actual_file_pages)):
            hashes[page] = pointer_digest
        hashes[global_page], hashes[tdef_page] = global_digest, tdef_digest
        if 14848 < row.actual_file_pages:
            extended = _extended_page(selected, 14848, row.actual_file_pages)
            digest = hashlib.sha256(extended).hexdigest()
            payloads[digest], hashes[14848] = extended, digest
        if 16352 < row.actual_file_pages:
            extended = _extended_page(selected, 16352, row.actual_file_pages)
            digest = hashlib.sha256(extended).hexdigest()
            payloads[digest], hashes[16352] = extended, digest
        ordered[name] = tuple(hashes)
    for left, right in PLAN.document["checkpoint_design"]["idle_pairs"]:
        ordered[right] = ordered[left]
    identity = hashlib.sha256(repr(selected).encode("utf-8")).hexdigest()
    return SyntheticBundle(
        CHECKPOINT_IDS, MappingProxyType({row.checkpoint_id: row.actual_file_pages for row in schedule.checkpoints}),
        MappingProxyType(ordered), replica, f"a3-synthetic-{identity[:16]}", PLAN_SHA256[:40],
        hashlib.sha256(b"DAO.DBEngine.36.synthetic").hexdigest(), True, selected, schedule,
        global_page, tdef_page, (selected.global_record_start, PAGE_SIZE),
        (0, PAGE_SIZE), growth_offset, churn_offset,
        MappingProxyType(payloads),
    )


def generate_synthetic_bundles(parameters: SyntheticParameters | None = None) -> tuple[SyntheticBundle, ...]:
    selected = calibration_parameters() if parameters is None else parameters
    first = generate_synthetic_bundle(selected, replica=1)
    return tuple(replace(first, replica=replica) for replica in (1, 2, 3))
