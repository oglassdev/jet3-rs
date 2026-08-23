#!/usr/bin/env python3
"""R3 extended-allocation-map recomputation for the independent validator."""

from __future__ import annotations

from typing import Any, Protocol

from a3_independent_bundle import Replica


EXTENDED_BITMAP_START = 4
EXTENDED_BITMAP_BITS = 16352
BASE_DISCRIMINATOR = ("P_ABS_16480", "H_REL_0064")


class GlobalModel(Protocol):
    page: int
    start: int
    polarity: str


def _no_layer(reason: str, predicate: str) -> dict[str, Any]:
    return {
        "applicable": True,
        "derivation_survivor_count": 0,
        "model": None,
        "no_outcome_reason": reason,
        "terminal_predicate_id": predicate,
    }


def _model_layer(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "applicable": True,
        "derivation_survivor_count": 1,
        "model": model,
        "no_outcome_reason": None,
        "terminal_predicate_id": None,
    }


def _bit_set(page: bytes, bit: int) -> bool:
    offset = EXTENDED_BITMAP_START + bit // 8
    return bool(page[offset] & (1 << (bit % 8)))


def _indirect_slots(page: bytes, start: int) -> tuple[int, int]:
    return (
        int.from_bytes(page[start + 1 : start + 5], "little"),
        int.from_bytes(page[start + 5 : start + 9], "little"),
    )


def _formula_page(formula: str, slot: int, reference: int, bit: int) -> int:
    page = EXTENDED_BITMAP_BITS * slot + bit if formula.startswith("slot_relative") else reference + bit
    if formula.endswith("minus_one"):
        return page - 1
    if formula.endswith("plus_one"):
        return page + 1
    return page


def _extended_in_use(page: bytes, bit: int, polarity: str) -> bool:
    return _bit_set(page, bit) == (polarity == "set_means_in_use")


def _base_context(replica: Replica, global_model: GlobalModel) -> tuple[int, bytes, bytes] | None:
    left, right = BASE_DISCRIMINATOR
    left_record = replica.page(left, global_model.page)
    right_record = replica.page(right, global_model.page)
    if (
        left_record is None
        or right_record is None
        or left_record[global_model.start] != 1
        or right_record[global_model.start] != 1
    ):
        return None
    left_reference = _indirect_slots(left_record, global_model.start)[0]
    right_reference = _indirect_slots(right_record, global_model.start)[0]
    if left_reference == 0 or left_reference != right_reference:
        return None
    left_page = replica.page(left, left_reference)
    right_page = replica.page(right, right_reference)
    if left_page is None or right_page is None or left_page[0] != 0x05 or right_page[0] != 0x05:
        return None
    return left_reference, left_page, right_page


def base_formula_survives(
    replica: Replica,
    plan: dict[str, Any],
    global_model: GlobalModel,
    conversion_model: dict[str, Any],
    formula: str,
    require_flip: bool,
) -> bool:
    context = _base_context(replica, global_model)
    if context is None:
        return False
    reference, left_page, right_page = context
    flips = [
        bit
        for bit in range(EXTENDED_BITMAP_BITS)
        if _bit_set(left_page, bit) != _bit_set(right_page, bit)
    ]
    if require_flip and not flips:
        return False
    left, right = BASE_DISCRIMINATOR
    left_count = replica.index(left)["page_count"]
    right_count = replica.index(right)["page_count"]
    space = replica.candidate_page_space()
    for bit in flips:
        page_number = _formula_page(formula, 0, reference, bit)
        if not 1 <= page_number < min(right_count, 65536) or page_number not in space:
            return False
        if _extended_in_use(right_page, bit, global_model.polarity):
            if replica.state(left, page_number) == replica.state(right, page_number):
                return False
        elif page_number >= left_count:
            return False
    coverage = plan["checkpoint_design"]["transition_coverage"]
    schedule = plan["checkpoint_design"]["checkpoint_ids"]
    conversion_ordinal = conversion_model["conversion_ordinal"]
    for checkpoint in coverage["pointer_validity_checkpoints"]:
        if schedule.index(checkpoint) < conversion_ordinal:
            continue
        record = replica.page(checkpoint, global_model.page)
        if record is None or record[global_model.start] != 1:
            return False
        references = _indirect_slots(record, global_model.start)
        page_count = replica.index(checkpoint)["page_count"]
        for slot, slot_reference in enumerate(references):
            if slot_reference == 0:
                continue
            bitmap = replica.page(checkpoint, slot_reference)
            if bitmap is None or bitmap[0] != 0x05:
                return False
            for bit in range(EXTENDED_BITMAP_BITS):
                page_number = _formula_page(formula, slot, slot_reference, bit)
                if not 0 <= page_number < 65536:
                    continue
                in_use = _extended_in_use(bitmap, bit, global_model.polarity)
                if page_number == slot_reference and not in_use:
                    return False
                if page_number >= page_count and in_use:
                    return False
    return True


def _derive_replica(
    replica: Replica,
    plan: dict[str, Any],
    global_model: GlobalModel,
    conversion_model: dict[str, Any],
) -> dict[str, Any]:
    context = _base_context(replica, global_model)
    if context is None or not any(
        _bit_set(context[1], bit) != _bit_set(context[2], bit)
        for bit in range(EXTENDED_BITMAP_BITS)
    ):
        return _no_layer("insufficient_base_discrimination", "A3-BASE-DISCRIMINATION")
    survivors = {
        formula
        for formula in plan["hypotheses"]["extended_base_candidates"]
        if base_formula_survives(replica, plan, global_model, conversion_model, formula, True)
    }
    if not survivors:
        return _no_layer("no_extended_base_candidate", "A3-BASE-NONE")
    if len(survivors) > 1:
        return _no_layer("multiple_extended_base_candidates", "A3-BASE-MULTIPLE")
    return _model_layer({"extended_base_formula": next(iter(survivors))})


def derive_extended_base(
    replicas: list[Replica],
    plan: dict[str, Any],
    global_model: GlobalModel,
    conversion_model: dict[str, Any],
) -> tuple[dict[str, Any], list[str | None]]:
    layers = [_derive_replica(replica, plan, global_model, conversion_model) for replica in replicas]
    terminals = [layer["terminal_predicate_id"] for layer in layers]
    if any(terminal is not None for terminal in terminals):
        if len(set(terminals)) == 1:
            return layers[0], terminals
        return _no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT"), terminals
    if any(layer["model"] != layers[0]["model"] for layer in layers[1:]):
        return _no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT"), terminals
    return _model_layer(layers[0]["model"]), terminals
