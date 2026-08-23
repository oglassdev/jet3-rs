#!/usr/bin/env python3
"""Plan-literal byte recomputation for the A3 independent validator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from a3_independent_base import derive_extended_base
from a3_independent_bundle import LoadedBundle, Replica, ValidationError


POLARITIES = ("set_means_in_use", "set_means_not_in_use")
GLOBAL_D = ("E0", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128")
ANCHORS = ("E0", "D_GROW_0128", "D_REGROW_0128")
CHURN_LEGS = (("L_REL_1280", "L_DELETE_ALL"), ("L_DELETE_ALL", "L_REINSERT_SAME"))


@dataclass(frozen=True)
class GlobalCandidate:
    page: int
    start: int
    polarity: str
    slack: int

    def model(self) -> dict[str, Any]:
        return {
            "record": {"page": self.page, "start": self.start, "end": 2048},
            "bit_polarity": self.polarity,
            "zero_suffix_slack_bytes": self.slack,
        }


@dataclass(frozen=True)
class PointerWindow:
    page: int
    offset: int
    layout: str


def leg(left: str, right: str) -> dict[str, str]:
    return {"left_checkpoint_id": left, "right_checkpoint_id": right}


def _pairs(values: list[str]) -> list[tuple[str, str]]:
    return list(zip(values, values[1:]))


def growth_legs(plan: dict[str, Any]) -> list[tuple[str, str]]:
    coverage = plan["checkpoint_design"]["transition_coverage"]
    return _pairs(coverage["tdef_low_growth"]) + _pairs(coverage["tdef_high_growth"])


def _state_diff(replica: Replica, page: int, left: str, right: str) -> bool:
    return replica.state(left, page) != replica.state(right, page)


def qualify_pages(replica: Replica, plan: dict[str, Any]) -> dict[str, list[int]]:
    pages = sorted(replica.candidate_page_space())
    global_pages = [
        page
        for page in pages
        if _state_diff(replica, page, "E0", "D_GROW_0128")
        and _state_diff(replica, page, "D_GROW_0128", "D_DROP")
    ]
    growth = growth_legs(plan)
    tdef_pages = [
        page
        for page in pages
        if replica.state("E0", page) is not None
        and any(_state_diff(replica, page, left, right) for left, right in growth)
        and all(_state_diff(replica, page, left, right) for left, right in CHURN_LEGS)
    ]
    maximum = plan["bounds"]["max_qualified_pages_per_submodel"]
    if len(global_pages) > maximum or len(tdef_pages) > maximum:
        raise ValidationError("resource_bound_breach", "qualified pages")
    return {"global_map": global_pages, "tdef": tdef_pages}


def idle_equal(replica: Replica, plan: dict[str, Any]) -> bool:
    return all(
        replica.index(left)["ordered_page_sha256"] == replica.index(right)["ordered_page_sha256"]
        for left, right in plan["checkpoint_design"]["idle_pairs"]
    )


def _bit_set(page: bytes, byte_start: int, bit_index: int) -> bool:
    offset = byte_start + bit_index // 8
    return bool(page[offset] & (1 << (bit_index % 8)))


def _in_use(page: bytes, start: int, end: int, polarity: str) -> set[int]:
    base = int.from_bytes(page[start + 1 : start + 5], "little")
    capacity = 8 * (end - start - 5)
    result: set[int] = set()
    for bit_index in range(capacity):
        is_set = _bit_set(page, start + 5, bit_index)
        if is_set == (polarity == "set_means_in_use"):
            result.add(base + bit_index)
    return result


def _anchor_matches(replica: Replica, page_number: int, start: int, polarity: str) -> bool:
    for checkpoint_id in ANCHORS:
        page = replica.page(checkpoint_id, page_number)
        if page is None or page[start] != 0:
            return False
        base = int.from_bytes(page[start + 1 : start + 5], "little")
        page_count = replica.index(checkpoint_id)["page_count"]
        capacity = 8 * (2048 - start - 5)
        if not (0 <= base <= page_count < base + capacity):
            return False
        for physical_page in range(base, page_count):
            bit_index = physical_page - base
            is_set = _bit_set(page, start + 5, bit_index)
            if is_set != (polarity == "set_means_in_use"):
                return False
        sentinel = _bit_set(page, start + 5, page_count - base)
        if sentinel == (polarity == "set_means_in_use"):
            return False
    return True


def _d_relation(replica: Replica, page_number: int, start: int, polarity: str) -> bool:
    decoded: dict[str, set[int]] = {}
    for checkpoint_id in GLOBAL_D:
        page = replica.page(checkpoint_id, page_number)
        if page is None:
            return False
        decoded[checkpoint_id] = _in_use(page, start, 2048, polarity)
    initial = decoded["E0"]
    grown = decoded["D_GROW_0128"]
    dropped = decoded["D_DROP"]
    recreated = decoded["D_RECREATE_EMPTY"]
    regrown = decoded["D_REGROW_0128"]
    new = grown - initial
    return bool(new) and not (new & dropped) and not (new & recreated) and new <= regrown and bool(regrown - grown)


def _suffix_slack(replica: Replica, page_number: int, start: int, polarity: str) -> int | None:
    pages = [replica.page(checkpoint_id, page_number) for checkpoint_id in GLOBAL_D]
    if any(page is None for page in pages):
        return None
    concrete = [page for page in pages if page is not None]
    last_flip = start - 1
    for offset in range(start, 2048):
        if len({page[offset] for page in concrete}) != 1:
            last_flip = offset
    slack = 2048 - last_flip - 1
    expected = 0xFF if polarity == "set_means_not_in_use" else 0x00
    if slack < 16:
        return None
    if any(any(byte != expected for byte in page[last_flip + 1 :]) for page in concrete):
        return None
    return slack


def candidates_for_page(replica: Replica, page_number: int) -> tuple[set[GlobalCandidate], dict[str, bool]]:
    candidates: set[GlobalCandidate] = set()
    seen = {"anchor": False, "relation": False, "suffix": False}
    for start in range(0, 2043):
        for polarity in POLARITIES:
            if not _anchor_matches(replica, page_number, start, polarity):
                continue
            seen["anchor"] = True
            if not _d_relation(replica, page_number, start, polarity):
                continue
            seen["relation"] = True
            slack = _suffix_slack(replica, page_number, start, polarity)
            if slack is None:
                continue
            seen["suffix"] = True
            candidates.add(GlobalCandidate(page_number, start, polarity, slack))
    return candidates, seen


def no_layer(
    reason: str | None,
    predicate: str | None,
    applicable: bool = True,
    survivors: int = 0,
) -> dict[str, Any]:
    """A terminal layer; ``survivors`` is the R4-S01 count of the set the terminal classified."""
    return {
        "applicable": applicable,
        "derivation_survivor_count": survivors,
        "model": None,
        "no_outcome_reason": reason,
        "terminal_predicate_id": predicate,
    }


def disagreement_layer(replica_1: dict[str, Any]) -> dict[str, Any]:
    """A3-REPLICA-DISAGREEMENT carries replica 1's survivor count at its own stop (R4-S01)."""
    return no_layer(
        "replica_disagreement",
        "A3-REPLICA-DISAGREEMENT",
        survivors=replica_1["derivation_survivor_count"],
    )


def model_layer(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "applicable": True,
        "derivation_survivor_count": 1,
        "model": model,
        "no_outcome_reason": None,
        "terminal_predicate_id": None,
    }


def _derive_global_record_replica(
    replica: Replica,
    qualified: list[int],
) -> tuple[dict[str, Any], GlobalCandidate | None, list[GlobalCandidate]]:
    if not qualified:
        return no_layer("no_physical_page_satisfies_global_transition_predicates", "A3-GLOBAL-PAGE-NONE"), None, []
    found: set[GlobalCandidate] = set()
    aggregate = {"anchor": False, "relation": False, "suffix": False}
    for page in qualified:
        candidates, seen = candidates_for_page(replica, page)
        found.update(candidates)
        for key in aggregate:
            aggregate[key] = aggregate[key] or seen[key]
    transcript = sorted(found, key=lambda item: (item.page, item.start, item.polarity, item.slack))
    if not found:
        if not aggregate["anchor"]:
            reason, predicate = "no_global_record_candidate", "A3-GLOBAL-RECORD-NONE"
        elif not aggregate["relation"]:
            reason, predicate = "global_set_relation_not_satisfied", "A3-D-SET-RELATION"
        elif not aggregate["suffix"]:
            reason, predicate = "global_record_end_not_resolved", "A3-GLOBAL-RECORD-END"
        else:
            raise ValidationError("global_record_stage_inconsistent")
        return no_layer(reason, predicate), None, transcript
    polarities = {candidate.polarity for candidate in found}
    if len(polarities) > 1:
        return no_layer("multiple_bit_polarities_survive", "A3-POLARITY-MULTIPLE", survivors=len(found)), None, transcript
    pages = {candidate.page for candidate in found}
    if len(pages) > 1:
        return no_layer("multiple_physical_pages_satisfy_global_transition_predicates", "A3-GLOBAL-PAGE-MULTIPLE", survivors=len(found)), None, transcript
    starts = {candidate.start for candidate in found}
    if len(starts) > 1:
        return no_layer("multiple_global_record_boundaries_survive", "A3-GLOBAL-RECORD-MULTIPLE", survivors=len(found)), None, transcript
    candidate = next(iter(found))
    return model_layer(candidate.model()), candidate, transcript


def _global_comparison_key(candidate: GlobalCandidate) -> tuple[int, int, str]:
    return candidate.page, candidate.start, candidate.polarity


def derive_global_record(
    replicas: list[Replica],
    qualified: dict[int, list[int]],
) -> tuple[dict[str, Any], GlobalCandidate | None, dict[int, list[GlobalCandidate]], list[str | None]]:
    evaluations = [
        _derive_global_record_replica(replica, qualified[replica.number]["global_map"])
        for replica in replicas
    ]
    terminals = [layer["terminal_predicate_id"] for layer, _, _ in evaluations]
    transcript = {replica.number: result[2] for replica, result in zip(replicas, evaluations)}
    if any(terminal is not None for terminal in terminals):
        if len(set(terminals)) == 1:
            return evaluations[0][0], None, transcript, terminals
        return disagreement_layer(evaluations[0][0]), None, transcript, terminals
    candidates = [candidate for _, candidate, _ in evaluations]
    concrete = [candidate for candidate in candidates if candidate is not None]
    if len(concrete) != len(replicas) or any(
        _global_comparison_key(candidate) != _global_comparison_key(concrete[0])
        for candidate in concrete[1:]
    ):
        return disagreement_layer(evaluations[0][0]), None, transcript, terminals
    candidate = GlobalCandidate(
        concrete[0].page,
        concrete[0].start,
        concrete[0].polarity,
        min(item.slack for item in concrete),
    )
    return model_layer(candidate.model()), candidate, transcript, terminals


def _record_page(replica: Replica, checkpoint_id: str, model: GlobalCandidate) -> bytes:
    page = replica.page(checkpoint_id, model.page)
    if page is None:
        raise ValidationError("global_record_page_absent", f"r{replica.number}:{checkpoint_id}")
    return page


def polarity_cross_check(replica: Replica, plan: dict[str, Any], model: GlobalCandidate) -> dict[str, Any]:
    evaluated: list[dict[str, str]] = []
    stop = None
    violating_leg = None
    violating_page = None
    for left, right in plan["checkpoint_design"]["transition_coverage"]["polarity_cross_check_legs"]:
        left_page = replica.page(left, model.page)
        right_page = replica.page(right, model.page)
        if left_page is None or right_page is None:
            continue
        left_tag, right_tag = left_page[model.start], right_page[model.start]
        if left_tag != right_tag:
            stop = leg(left, right)
            break
        if left_tag != 0:
            continue
        left_base = int.from_bytes(left_page[model.start + 1 : model.start + 5], "little")
        right_base = int.from_bytes(right_page[model.start + 1 : model.start + 5], "little")
        capacity = 8 * (2048 - model.start - 5)
        left_count = replica.index(left)["page_count"]
        right_count = replica.index(right)["page_count"]
        low = max(left_count, left_base, right_base)
        high = min(right_count, left_base + capacity, right_base + capacity, 65536)
        current_leg = leg(left, right)
        evaluated.append(current_leg)
        for physical_page in range(low, max(low, high)):
            left_set = _bit_set(left_page, model.start + 5, physical_page - left_base)
            right_set = _bit_set(right_page, model.start + 5, physical_page - right_base)
            left_in_use = left_set == (model.polarity == "set_means_in_use")
            right_in_use = right_set == (model.polarity == "set_means_in_use")
            if left_in_use or not right_in_use:
                violating_leg = current_leg
                violating_page = physical_page
                break
        if violating_leg is not None:
            break
    return {
        "evaluated_legs": evaluated,
        "representation_change_stop": stop,
        "first_violating_leg": violating_leg,
        "first_violating_page": violating_page,
    }


def _inline_valid(replica: Replica, checkpoint_id: str, model: GlobalCandidate) -> bool:
    page = replica.page(checkpoint_id, model.page)
    if page is None or page[model.start] != 0:
        return False
    base = int.from_bytes(page[model.start + 1 : model.start + 5], "little")
    count = replica.index(checkpoint_id)["page_count"]
    capacity = 8 * (2048 - model.start - 5)
    if not (0 <= base <= count < base + capacity):
        return False
    for physical_page in range(base, count):
        is_set = _bit_set(page, model.start + 5, physical_page - base)
        if is_set != (model.polarity == "set_means_in_use"):
            return False
    sentinel = _bit_set(page, model.start + 5, count - base)
    return sentinel != (model.polarity == "set_means_in_use")


def _indirect_slots(page: bytes, start: int) -> tuple[int, int]:
    return (
        int.from_bytes(page[start + 1 : start + 5], "little"),
        int.from_bytes(page[start + 5 : start + 9], "little"),
    )


def _indirect_valid(replica: Replica, checkpoint_id: str, model: GlobalCandidate) -> bool:
    page = replica.page(checkpoint_id, model.page)
    return page is not None and page[model.start] == 1 and all(byte == 0 for byte in page[model.start + 9 : 2048])


def _pointer_valid(replica: Replica, plan: dict[str, Any], references: dict[str, tuple[int, int]]) -> bool:
    schedule = plan["checkpoint_design"]["checkpoint_ids"]
    validity = set(plan["checkpoint_design"]["transition_coverage"]["pointer_validity_checkpoints"])
    candidate_space = replica.candidate_page_space()
    for slot in (0, 1):
        activation = next((index for index, checkpoint in enumerate(schedule) if references.get(checkpoint, (0, 0))[slot] != 0), None)
        if activation is None:
            continue
        for index, checkpoint in enumerate(schedule):
            if index < activation or checkpoint not in validity:
                continue
            reference = references.get(checkpoint, (0, 0))[slot]
            if reference == 0:
                continue
            page_count = replica.index(checkpoint)["page_count"]
            page = replica.page(checkpoint, reference) if 1 <= reference < page_count and reference in candidate_space else None
            if page is None or page[0] != 0x05:
                return False
    return True


def _inline_boundary(replica: Replica, checkpoints: list[str], model: GlobalCandidate) -> int:
    extents = []
    for checkpoint in checkpoints:
        page = _record_page(replica, checkpoint, model)
        base = int.from_bytes(page[model.start + 1 : model.start + 5], "little")
        page_count = replica.index(checkpoint)["page_count"]
        extents.append(model.start + 5 + (page_count - base) // 8 + 1)
    return max(extents)


def _derive_conversion_replica(
    replica: Replica,
    plan: dict[str, Any],
    model: GlobalCandidate,
    cross: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if cross["first_violating_leg"] is not None:
        return no_layer("growth_polarity_disagreement", "A3-POLARITY-CROSSCHECK"), cross
    window = plan["checkpoint_design"]["transition_coverage"]["inline_to_indirect_conversion_window"]
    classes: list[str] = []
    references: dict[str, tuple[int, int]] = {}
    for checkpoint_id in plan["checkpoint_design"]["checkpoint_ids"]:
        page = replica.page(checkpoint_id, model.page)
        references[checkpoint_id] = (0, 0) if page is None or page[model.start] != 1 else _indirect_slots(page, model.start)
    for checkpoint_id in window:
        if _inline_valid(replica, checkpoint_id, model):
            classes.append("inline")
        elif _indirect_valid(replica, checkpoint_id, model):
            classes.append("indirect")
        else:
            classes.append("neither")
    first_indirect = next((index for index, value in enumerate(classes) if value == "indirect"), None)
    if first_indirect is None or "inline" not in classes[:first_indirect]:
        return no_layer("missing_inline_to_indirect_conversion", "A3-CONVERSION-NONE"), cross
    class_changes = sum(left != right for left, right in zip(classes, classes[1:]))
    if class_changes != 1:
        return no_layer("multiple_inline_to_indirect_conversions", "A3-CONVERSION-MULTIPLE", survivors=class_changes), cross
    conversion = window[first_indirect]
    slots = references[conversion]
    if sum(value != 0 for value in slots) == 0:
        return no_layer("no_active_slot_at_conversion", "A3-SLOT-ACTIVATION", survivors=1), cross
    if sum(value != 0 for value in references["H_REL_0904"]) != 2:
        return no_layer("final_slot_activation_not_two", "A3-SLOT-FINAL", survivors=1), cross
    if not _pointer_valid(replica, plan, references):
        return no_layer("pointer_validity_failure", "A3-POINTER-VALIDITY", survivors=1), cross
    inline_phase = window[:first_indirect]
    boundary = _inline_boundary(replica, inline_phase, model)
    expected = 0xFF if model.polarity == "set_means_not_in_use" else 0x00
    if any(
        any(byte != expected for byte in _record_page(replica, checkpoint, model)[boundary:])
        for checkpoint in inline_phase
    ):
        return no_layer("unexplained_nonzero_inline_suffix", "A3-INLINE-SUFFIX", survivors=1), cross
    conversion_model = {
        "conversion_checkpoint_id": conversion,
        "conversion_ordinal": plan["checkpoint_design"]["checkpoint_ids"].index(conversion),
        "indirect_tag": 1,
        "active_slot_count_at_conversion": sum(value != 0 for value in slots),
        "active_slot_count_at_h_rel_0904": 2,
        "inline_boundary": boundary,
        "slot_reference_pages": list(slots),
    }
    return model_layer(conversion_model), cross


def derive_conversion(
    replicas: list[Replica],
    plan: dict[str, Any],
    model: GlobalCandidate | None,
    cross_checks: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[str | None]]:
    empty_cross = {"evaluated_legs": [], "representation_change_stop": None, "first_violating_leg": None, "first_violating_page": None}
    if model is None:
        return no_layer(None, None, False), empty_cross, [None for _ in replicas]
    evaluations = [
        _derive_conversion_replica(replica, plan, model, cross_checks[replica.number])
        for replica in replicas
    ]
    terminals = [layer["terminal_predicate_id"] for layer, _ in evaluations]
    frozen_cross = evaluations[0][1]
    if any(terminal is not None for terminal in terminals):
        if len(set(terminals)) == 1:
            return evaluations[0][0], frozen_cross, terminals
        return disagreement_layer(evaluations[0][0]), frozen_cross, terminals
    models = [layer["model"] for layer, _ in evaluations]
    crosses = [cross for _, cross in evaluations]
    if any(value != models[0] for value in models[1:]) or any(value != crosses[0] for value in crosses[1:]):
        return disagreement_layer(evaluations[0][0]), frozen_cross, terminals
    return model_layer(models[0]), frozen_cross, terminals


def _decode_pointer(page: bytes, offset: int, layout: str) -> tuple[int, int]:
    window = page[offset : offset + 4]
    if layout == "u24le_page_then_u8_slot":
        return int.from_bytes(window[:3], "little"), window[3]
    return int.from_bytes(window[1:], "little"), window[0]


def _window_stable(replica: Replica, page: int, offset: int, legs: Iterable[tuple[str, str]]) -> bool:
    for left, right in legs:
        left_page, right_page = replica.page(left, page), replica.page(right, page)
        if left_page is None or right_page is None or left_page[offset : offset + 4] != right_page[offset : offset + 4]:
            return False
    return True


def _reference_stable(
    replica: Replica,
    page: int,
    offset: int,
    layout: str,
    legs: Iterable[tuple[str, str]],
) -> bool:
    for left, right in legs:
        left_page, right_page = replica.page(left, page), replica.page(right, page)
        if (
            left_page is None
            or right_page is None
            or _decode_pointer(left_page, offset, layout)[0] != _decode_pointer(right_page, offset, layout)[0]
        ):
            return False
    return True


def _reference_valid(replica: Replica, plan: dict[str, Any], page: int, offset: int, layout: str) -> bool:
    schedule = plan["checkpoint_design"]["checkpoint_ids"]
    validity = set(plan["checkpoint_design"]["transition_coverage"]["pointer_validity_checkpoints"])
    values: list[int] = []
    for checkpoint in schedule:
        raw = replica.page(checkpoint, page)
        values.append(0 if raw is None else _decode_pointer(raw, offset, layout)[0])
    activation = next((index for index, value in enumerate(values) if value != 0), None)
    if activation is None:
        return False
    space = replica.candidate_page_space()
    for index, checkpoint in enumerate(schedule):
        reference = values[index]
        if index < activation or checkpoint not in validity or reference == 0:
            continue
        count = replica.index(checkpoint)["page_count"]
        target = replica.page(checkpoint, reference) if 1 <= reference < count and reference in space else None
        if target is None or target[0] != 0x05:
            return False
    return True


def pointer_windows(replica: Replica, plan: dict[str, Any], pages: list[int]) -> tuple[set[PointerWindow], set[PointerWindow]]:
    growth = growth_legs(plan)
    low_growth = _pairs(plan["checkpoint_design"]["transition_coverage"]["tdef_low_growth"])
    layouts = plan["hypotheses"]["tdef_pointer_layouts"]
    growth_windows: set[PointerWindow] = set()
    churn_windows: set[PointerWindow] = set()
    for page_number in pages:
        for offset in range(2045):
            for layout in layouts:
                if _reference_stable(replica, page_number, offset, layout, CHURN_LEGS):
                    values = []
                    for left, right in growth:
                        left_page, right_page = replica.page(left, page_number), replica.page(right, page_number)
                        if left_page is not None and right_page is not None:
                            values.append(
                                _decode_pointer(left_page, offset, layout)[0]
                                != _decode_pointer(right_page, offset, layout)[0]
                            )
                    if any(values):
                        growth_windows.add(PointerWindow(page_number, offset, layout))
                if _reference_stable(replica, page_number, offset, layout, low_growth):
                    before = replica.page("L_REL_1280", page_number)
                    deleted = replica.page("L_DELETE_ALL", page_number)
                    restored = replica.page("L_REINSERT_SAME", page_number)
                    if before is not None and deleted is not None and restored is not None:
                        first = _decode_pointer(before, offset, layout)
                        middle = _decode_pointer(deleted, offset, layout)
                        last = _decode_pointer(restored, offset, layout)
                        if first[0] != middle[0] and first[0] == last[0]:
                            churn_windows.add(PointerWindow(page_number, offset, layout))
    return growth_windows, churn_windows


def _stable_byte(replica: Replica, page: int, offset: int) -> bool:
    if not 0 <= offset < 2048:
        return True
    values = [replica.page(checkpoint, page) for checkpoint in replica.checkpoint_ids]
    return all(value is not None for value in values) and len({value[offset] for value in values if value is not None}) == 1


def _tdef_models(replica: Replica, growth: set[PointerWindow], churn: set[PointerWindow]) -> set[tuple[Any, ...]]:
    models: set[tuple[Any, ...]] = set()
    for left in growth:
        for right in churn:
            if (
                left.page != right.page
                or left.layout != right.layout
                or not (left.offset + 4 <= right.offset or right.offset + 4 <= left.offset)
            ):
                continue
            start = min(left.offset, right.offset)
            end = max(left.offset, right.offset) + 4
            if not _stable_byte(replica, left.page, start - 1) or not _stable_byte(replica, left.page, end):
                continue
            record_start = max(0, start - 1)
            record_end = min(2048, end + 1)
            pointer_bytes = set(range(left.offset, left.offset + 4)) | set(range(right.offset, right.offset + 4))
            if any(
                offset not in pointer_bytes and not _stable_byte(replica, left.page, offset)
                for offset in range(record_start, record_end)
            ):
                continue
            models.add((left.page, record_start, record_end, left.layout, left.offset, right.offset))
    return models


def _tdef_precondition(replica: Replica) -> bool:
    before = replica.checkpoint_observation("L_REL_1280")["table_row_counts"]["L"]
    deleted_rows = next(
        (item["row_count"] for item in replica.checkpoint_observation("L_DELETE_ALL")["dao_reread"] if item["role"] == "L"),
        None,
    )
    return before != 0 and deleted_rows == 0


def _p_growth_legs() -> list[tuple[str, str]]:
    return [
        ("L_IDLE_REOPEN", "P_ABS_04096"),
        ("P_ABS_04096", "P_ABS_08192"),
        ("P_ABS_08192", "P_ABS_12288"),
        ("P_ABS_12288", "P_ABS_16480"),
    ]


def _tdef_structural_valid(replica: Replica, plan: dict[str, Any], model: tuple[Any, ...]) -> bool:
    page, _, _, _, growth_offset, churn_offset = model
    schedule = plan["checkpoint_design"]["checkpoint_ids"]
    d_legs = _pairs(schedule[:6])
    idle = [tuple(value) for value in plan["checkpoint_design"]["idle_pairs"]]
    all_growth = growth_legs(plan) + _p_growth_legs()
    return _window_stable(replica, page, growth_offset, d_legs + idle) and _window_stable(
        replica,
        page,
        churn_offset,
        d_legs + all_growth + idle,
    )


def _derive_tdef_replica(replica: Replica, plan: dict[str, Any], qualified: list[int]) -> dict[str, Any]:
    if not qualified:
        return no_layer("no_physical_page_satisfies_tdef_transition_predicates", "A3-TDEF-PAGE-NONE")
    if not _tdef_precondition(replica):
        return no_layer("legacy_churn_precondition_not_met", "A3-CHURN-PRECONDITION")
    growth, churn = pointer_windows(replica, plan, qualified)
    if not growth:
        return no_layer("no_growth_only_pointer_candidate", "A3-GROWTH-POINTER-NONE")
    if not churn:
        return no_layer("no_delete_reinsert_only_pointer_candidate", "A3-CHURN-POINTER-NONE")
    models = _tdef_models(replica, growth, churn)
    if not models:
        return no_layer("no_tdef_record_candidate", "A3-TDEF-RECORD-NONE")
    record_keys = {(item[0], item[1], item[2]) for item in models}
    starts_by_page: dict[int, set[int]] = {}
    for page, start, _ in record_keys:
        starts_by_page.setdefault(page, set()).add(start)
    if len(starts_by_page) > 1:
        return no_layer("multiple_physical_pages_satisfy_tdef_transition_predicates", "A3-TDEF-PAGE-MULTIPLE", survivors=len(record_keys))
    if len(record_keys) > 1:
        return no_layer("multiple_tdef_record_boundaries_survive", "A3-TDEF-RECORD-MULTIPLE", survivors=len(record_keys))
    if len(models) > 1:
        return no_layer("multiple_pointer_models_survive", "A3-POINTER-MULTIPLE", survivors=len(models))
    model = next(iter(models))
    page, start, end, layout, growth_offset, churn_offset = model
    if not _reference_valid(replica, plan, page, growth_offset, layout) or not _reference_valid(
        replica, plan, page, churn_offset, layout
    ):
        return no_layer("pointer_validity_failure", "A3-POINTER-VALIDITY", survivors=1)
    if not _tdef_structural_valid(replica, plan, model):
        return no_layer("structural_field_exclusion_failure", "A3-STRUCTURAL-EXCLUSION", survivors=1)
    return model_layer(
        {
            "record": {"page": page, "start": start, "end": end},
            "pointer_layout": layout,
            "growth_pointer_offset": growth_offset,
            "delete_reinsert_pointer_offset": churn_offset,
        }
    )


def derive_tdef(
    replicas: list[Replica],
    plan: dict[str, Any],
    qualified: dict[int, list[int]],
) -> tuple[dict[str, Any], list[str | None]]:
    layers = [
        _derive_tdef_replica(replica, plan, qualified[replica.number]["tdef"])
        for replica in replicas
    ]
    terminals = [layer["terminal_predicate_id"] for layer in layers]
    if any(terminal is not None for terminal in terminals):
        if len(set(terminals)) == 1:
            return layers[0], terminals
        return disagreement_layer(layers[0]), terminals
    if any(layer["model"] != layers[0]["model"] for layer in layers[1:]):
        return disagreement_layer(layers[0]), terminals
    return model_layer(layers[0]["model"]), terminals


def recompute_derivation(bundle: LoadedBundle) -> dict[str, Any]:
    replicas = [bundle.replicas[number] for number in (1, 2)]
    plan = bundle.plan
    empty_cross = {
        "evaluated_legs": [],
        "representation_change_stop": None,
        "first_violating_leg": None,
        "first_violating_page": None,
    }
    idle_results = {replica.number: idle_equal(replica, plan) for replica in replicas}
    if not all(idle_results.values()):
        inapplicable = {name: no_layer(None, None, False) for name in (
            "global_map_record",
            "global_map_conversion_inline",
            "global_map_extended_base",
            "tdef_pointer_pair",
        )}
        return {
            "qualified_pages": {"global_map": [], "tdef": []},
            "per_replica_qualified_pages": {
                replica.number: {"global_map": [], "tdef": []} for replica in replicas
            },
            "polarity_cross_check": empty_cross,
            "layers": inapplicable,
            "record_candidates": {},
            "idle_equality": False,
            "campaign_terminal_predicate_id": "A3-IDLE-EQUALITY",
            "replica_terminals": {name: [None, None] for name in inapplicable},
            "record_candidate_enumerations": 0,
        }
    qualifications = {replica.number: qualify_pages(replica, plan) for replica in replicas}
    union_qualified = {
        key: sorted(set.union(*(set(value[key]) for value in qualifications.values())))
        for key in ("global_map", "tdef")
    }
    global_layer, global_model, record_candidates, global_terminals = derive_global_record(replicas, qualifications)
    cross_checks = {
        replica.number: polarity_cross_check(replica, plan, global_model)
        for replica in replicas
    } if global_model is not None else {}
    conversion_layer, cross_check, conversion_terminals = derive_conversion(replicas, plan, global_model, cross_checks)
    if conversion_layer["model"] is None:
        base_layer = no_layer(None, None, False)
        base_terminals: list[str | None] = [None, None]
    else:
        base_layer, base_terminals = derive_extended_base(replicas, plan, global_model, conversion_layer["model"])
    tdef_layer, tdef_terminals = derive_tdef(replicas, plan, qualifications)
    # R4-C01: each union qualified page is counted once across derivation replicas;
    # TDEF pages count when the churn precondition passed in at least one replica.
    enumerations = len(union_qualified["global_map"])
    if any(_tdef_precondition(replica) for replica in replicas):
        enumerations += len(union_qualified["tdef"])
    return {
        "qualified_pages": union_qualified,
        "per_replica_qualified_pages": qualifications,
        "polarity_cross_check": cross_check,
        "layers": {
            "global_map_record": global_layer,
            "global_map_conversion_inline": conversion_layer,
            "global_map_extended_base": base_layer,
            "tdef_pointer_pair": tdef_layer,
        },
        "record_candidates": {
            str(number): [candidate.model() for candidate in candidates]
            for number, candidates in record_candidates.items()
        },
        "idle_equality": True,
        "campaign_terminal_predicate_id": None,
        "replica_terminals": {
            "global_map_record": global_terminals,
            "global_map_conversion_inline": conversion_terminals,
            "global_map_extended_base": base_terminals,
            "tdef_pointer_pair": tdef_terminals,
        },
        "record_candidate_enumerations": enumerations,
    }
