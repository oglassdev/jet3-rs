#!/usr/bin/env python3
"""Plan-literal byte recomputation for the A3 independent validator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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


def no_layer(reason: str | None, predicate: str | None, applicable: bool = True) -> dict[str, Any]:
    return {
        "applicable": applicable,
        "derivation_survivor_count": 0,
        "model": None,
        "no_outcome_reason": reason,
        "terminal_predicate_id": predicate,
    }


def model_layer(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "applicable": True,
        "derivation_survivor_count": 1,
        "model": model,
        "no_outcome_reason": None,
        "terminal_predicate_id": None,
    }


def derive_global_record(
    replicas: list[Replica], qualified: list[int]
) -> tuple[dict[str, Any], GlobalCandidate | None, dict[int, list[GlobalCandidate]]]:
    if not qualified:
        return no_layer("no_physical_page_satisfies_global_transition_predicates", "A3-GLOBAL-PAGE-NONE"), None, {}
    per_replica: dict[int, set[GlobalCandidate]] = {}
    seen_by_replica: dict[int, dict[str, bool]] = {}
    for replica in replicas:
        found: set[GlobalCandidate] = set()
        aggregate = {"anchor": False, "relation": False, "suffix": False}
        for page in qualified:
            candidates, seen = candidates_for_page(replica, page)
            found.update(candidates)
            for key in aggregate:
                aggregate[key] = aggregate[key] or seen[key]
        per_replica[replica.number] = found
        seen_by_replica[replica.number] = aggregate
    common = set.intersection(*per_replica.values()) if per_replica else set()
    transcript = {number: sorted(values, key=lambda item: (item.page, item.start, item.polarity, item.slack)) for number, values in per_replica.items()}
    if not common:
        if all(not seen["anchor"] for seen in seen_by_replica.values()):
            reason, predicate = "no_global_record_candidate", "A3-GLOBAL-RECORD-NONE"
        elif all(not seen["relation"] for seen in seen_by_replica.values()):
            reason, predicate = "global_set_relation_not_satisfied", "A3-D-SET-RELATION"
        elif all(not seen["suffix"] for seen in seen_by_replica.values()):
            reason, predicate = "global_record_end_not_resolved", "A3-GLOBAL-RECORD-END"
        else:
            reason, predicate = "replica_disagreement", "A3-REPLICA-DISAGREEMENT"
        return no_layer(reason, predicate), None, transcript
    pages = {candidate.page for candidate in common}
    starts_by_page = {page: {candidate.start for candidate in common if candidate.page == page} for page in pages}
    if len(pages) > 1 and all(len(starts) == 1 for starts in starts_by_page.values()):
        return no_layer("multiple_physical_pages_satisfy_global_transition_predicates", "A3-GLOBAL-PAGE-MULTIPLE"), None, transcript
    if any(len(starts) > 1 for starts in starts_by_page.values()):
        return no_layer("multiple_global_record_boundaries_survive", "A3-GLOBAL-RECORD-MULTIPLE"), None, transcript
    pairs = {(candidate.page, candidate.start) for candidate in common}
    if len(pairs) != 1:
        return no_layer("multiple_physical_pages_satisfy_global_transition_predicates", "A3-GLOBAL-PAGE-MULTIPLE"), None, transcript
    polarities = {candidate.polarity for candidate in common}
    if len(polarities) == 0:
        return no_layer("no_unique_bit_polarity", "A3-POLARITY-NONE"), None, transcript
    if len(polarities) > 1:
        return no_layer("multiple_bit_polarities_survive", "A3-POLARITY-MULTIPLE"), None, transcript
    if len(common) != 1:
        return no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT"), None, transcript
    candidate = next(iter(common))
    return model_layer(candidate.model()), candidate, transcript


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
        left_page = _record_page(replica, left, model)
        right_page = _record_page(replica, right, model)
        left_tag, right_tag = left_page[model.start], right_page[model.start]
        if left_tag != right_tag or left_tag != 0:
            stop = leg(left, right)
            break
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


def derive_conversion(
    replicas: list[Replica],
    plan: dict[str, Any],
    model: GlobalCandidate | None,
    cross_checks: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    empty_cross = {"evaluated_legs": [], "representation_change_stop": None, "first_violating_leg": None, "first_violating_page": None}
    if model is None:
        return no_layer(None, None, False), empty_cross
    values = list(cross_checks.values())
    if any(value != values[0] for value in values[1:]):
        return no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT"), values[0]
    cross = values[0]
    if cross["first_violating_leg"] is not None:
        return no_layer("growth_polarity_disagreement", "A3-POLARITY-CROSSCHECK"), cross
    window = plan["checkpoint_design"]["transition_coverage"]["inline_to_indirect_conversion_window"]
    replica_models: list[dict[str, Any]] = []
    for replica in replicas:
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
        transitions = [
            index for index in range(1, len(window))
            if classes[index - 1] == "inline" and classes[index] == "indirect"
        ]
        if not transitions:
            return no_layer("missing_inline_to_indirect_conversion", "A3-CONVERSION-NONE"), cross
        if len(transitions) > 1:
            return no_layer("multiple_inline_to_indirect_conversions", "A3-CONVERSION-MULTIPLE"), cross
        index = transitions[0]
        if not all(value == "inline" for value in classes[:index]) or not all(value == "indirect" for value in classes[index:]):
            reason = "multiple_inline_to_indirect_conversions" if "inline" in classes[index + 1 :] else "missing_inline_to_indirect_conversion"
            predicate = "A3-CONVERSION-MULTIPLE" if reason.startswith("multiple") else "A3-CONVERSION-NONE"
            return no_layer(reason, predicate), cross
        conversion = window[index]
        slots = references[conversion]
        if sum(value != 0 for value in slots) == 0:
            return no_layer("no_active_slot_at_conversion", "A3-SLOT-ACTIVATION"), cross
        if sum(value != 0 for value in references["H_REL_0904"]) != 2:
            return no_layer("final_slot_activation_not_two", "A3-SLOT-FINAL"), cross
        if not _pointer_valid(replica, plan, references):
            return no_layer("pointer_validity_failure", "A3-POINTER-VALIDITY"), cross
        inline_counts = [replica.index(checkpoint)["page_count"] for checkpoint in window[:index]]
        inline_bases = [int.from_bytes(_record_page(replica, checkpoint, model)[model.start + 1 : model.start + 5], "little") for checkpoint in window[:index]]
        required_bytes = max((count - base + 1 + 7) // 8 for count, base in zip(inline_counts, inline_bases))
        boundary = model.start + 5 + required_bytes
        expected = 0xFF if model.polarity == "set_means_not_in_use" else 0x00
        if boundary > 2048 or any(
            any(byte != expected for byte in _record_page(replica, checkpoint, model)[boundary:])
            for checkpoint in window[:index]
        ):
            return no_layer("no_inline_boundary_candidate", "A3-INLINE-BOUNDARY-NONE"), cross
        replica_models.append(
            {
                "conversion_checkpoint_id": conversion,
                "conversion_ordinal": plan["checkpoint_design"]["checkpoint_ids"].index(conversion),
                "indirect_tag": 1,
                "active_slot_count_at_conversion": sum(value != 0 for value in slots),
                "active_slot_count_at_h_rel_0904": 2,
                "inline_boundary": boundary,
                "slot_reference_pages": list(slots),
            }
        )
    if any(value != replica_models[0] for value in replica_models[1:]):
        return no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT"), cross
    return model_layer(replica_models[0]), cross


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
    idle = [tuple(value) for value in plan["checkpoint_design"]["idle_pairs"]]
    d_legs = _pairs(list(GLOBAL_D))
    layouts = plan["hypotheses"]["tdef_pointer_layouts"]
    growth_windows: set[PointerWindow] = set()
    churn_windows: set[PointerWindow] = set()
    for page_number in pages:
        for offset in range(2045):
            for layout in layouts:
                if _window_stable(replica, page_number, offset, CHURN_LEGS + tuple(d_legs) + tuple(idle)):
                    values = []
                    for left, right in growth:
                        left_page, right_page = replica.page(left, page_number), replica.page(right, page_number)
                        if left_page is not None and right_page is not None:
                            values.append(_decode_pointer(left_page, offset, layout) != _decode_pointer(right_page, offset, layout))
                    if any(values) and _reference_valid(replica, plan, page_number, offset, layout):
                        growth_windows.add(PointerWindow(page_number, offset, layout))
                if _window_stable(replica, page_number, offset, tuple(growth) + tuple(d_legs) + tuple(idle)):
                    before = replica.page("L_REL_1280", page_number)
                    deleted = replica.page("L_DELETE_ALL", page_number)
                    restored = replica.page("L_REINSERT_SAME", page_number)
                    if before is not None and deleted is not None and restored is not None:
                        first = _decode_pointer(before, offset, layout)
                        middle = _decode_pointer(deleted, offset, layout)
                        last = _decode_pointer(restored, offset, layout)
                        if first != middle and first == last and _reference_valid(replica, plan, page_number, offset, layout):
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
            if left.page != right.page or left.layout != right.layout or left.offset == right.offset:
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


def derive_tdef(replicas: list[Replica], plan: dict[str, Any], qualified: list[int]) -> dict[str, Any]:
    if not qualified:
        return no_layer("no_physical_page_satisfies_tdef_transition_predicates", "A3-TDEF-PAGE-NONE")
    for replica in replicas:
        before = replica.checkpoint_observation("L_REL_1280")["table_row_counts"]["L"]
        deleted_rows = next(
            (item["row_count"] for item in replica.checkpoint_observation("L_DELETE_ALL")["dao_reread"] if item["role"] == "L"),
            None,
        )
        if before == 0 or deleted_rows != 0:
            return no_layer("legacy_churn_precondition_not_met", "A3-CHURN-PRECONDITION")
    per_replica: dict[int, tuple[set[PointerWindow], set[PointerWindow], set[tuple[Any, ...]]]] = {}
    for replica in replicas:
        growth, churn = pointer_windows(replica, plan, qualified)
        if not growth:
            return no_layer("no_growth_only_pointer_candidate", "A3-GROWTH-POINTER-NONE")
        if not churn:
            return no_layer("no_delete_reinsert_only_pointer_candidate", "A3-CHURN-POINTER-NONE")
        per_replica[replica.number] = (growth, churn, _tdef_models(replica, growth, churn))
    if any(not values[2] for values in per_replica.values()):
        return no_layer("no_tdef_record_candidate", "A3-TDEF-RECORD-NONE")
    common = set.intersection(*(values[2] for values in per_replica.values()))
    if not common:
        return no_layer("replica_disagreement", "A3-REPLICA-DISAGREEMENT")
    record_keys = {(item[0], item[1], item[2]) for item in common}
    starts_by_page: dict[int, set[int]] = {}
    for page, start, _ in record_keys:
        starts_by_page.setdefault(page, set()).add(start)
    if len(starts_by_page) > 1 and all(len(starts) == 1 for starts in starts_by_page.values()):
        return no_layer("multiple_physical_pages_satisfy_tdef_transition_predicates", "A3-TDEF-PAGE-MULTIPLE")
    if len(record_keys) > 1:
        return no_layer("multiple_tdef_record_boundaries_survive", "A3-TDEF-RECORD-MULTIPLE")
    if len(common) > 1:
        return no_layer("multiple_pointer_models_survive", "A3-POINTER-MULTIPLE")
    page, start, end, layout, growth_offset, churn_offset = next(iter(common))
    return model_layer(
        {
            "record": {"page": page, "start": start, "end": end},
            "pointer_layout": layout,
            "growth_pointer_offset": growth_offset,
            "delete_reinsert_pointer_offset": churn_offset,
        }
    )


def recompute_derivation(bundle: LoadedBundle) -> dict[str, Any]:
    replicas = [bundle.replicas[number] for number in (1, 2)]
    plan = bundle.plan
    qualifications = {replica.number: qualify_pages(replica, plan) for replica in replicas}
    common_qualified = {
        key: sorted(set.intersection(*(set(value[key]) for value in qualifications.values())))
        for key in ("global_map", "tdef")
    }
    global_layer, global_model, record_candidates = derive_global_record(replicas, common_qualified["global_map"])
    cross_checks = {
        replica.number: polarity_cross_check(replica, plan, global_model)
        for replica in replicas
    } if global_model is not None else {}
    conversion_layer, cross_check = derive_conversion(replicas, plan, global_model, cross_checks)
    if conversion_layer["model"] is None:
        base_layer = no_layer(None, None, False)
    else:
        base_layer = derive_extended_base(replicas, plan, global_model, conversion_layer["model"])
    tdef_layer = derive_tdef(replicas, plan, common_qualified["tdef"])
    return {
        "qualified_pages": common_qualified,
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
        "idle_equality": all(idle_equal(replica, plan) for replica in replicas),
    }


def derive_extended_base(
    replicas: list[Replica],
    plan: dict[str, Any],
    global_model: GlobalCandidate,
    conversion_model: dict[str, Any],
) -> dict[str, Any]:
    formulas = plan["hypotheses"]["extended_base_candidates"]
    survivors_by_replica: list[set[str]] = []
    window = plan["checkpoint_design"]["transition_coverage"]["inline_to_indirect_conversion_window"]
    conversion_index = window.index(conversion_model["conversion_checkpoint_id"])

    def formula_base(formula: str, slot: int, reference: int) -> int:
        base = (0, 16352)[slot] if formula.startswith("slot_relative") else reference
        if formula.endswith("minus_one"):
            return base - 1
        if formula.endswith("plus_one"):
            return base + 1
        return base

    for replica in replicas:
        h_left_record = _record_page(replica, "P_ABS_16480", global_model)
        h_right_record = _record_page(replica, "H_REL_0064", global_model)
        h_left_reference = _indirect_slots(h_left_record, global_model.start)[0]
        h_right_reference = _indirect_slots(h_right_record, global_model.start)[0]
        h_left_page = replica.page("P_ABS_16480", h_left_reference) if h_left_reference else None
        h_right_page = replica.page("H_REL_0064", h_right_reference) if h_right_reference else None
        if (
            h_left_page is None
            or h_right_page is None
            or not any(_bit_set(h_left_page, 1, bit) != _bit_set(h_right_page, 1, bit) for bit in range(16352))
        ):
            return no_layer("insufficient_base_discrimination", "A3-BASE-DISCRIMINATION")
        survivors: set[str] = set()
        for formula in formulas:
            valid = True
            h64_discriminated = False
            for left, right in _pairs(window[conversion_index:]):
                left_record = _record_page(replica, left, global_model)
                right_record = _record_page(replica, right, global_model)
                left_references = _indirect_slots(left_record, global_model.start)
                right_references = _indirect_slots(right_record, global_model.start)
                append_low = replica.index(left)["page_count"]
                append_high = replica.index(right)["page_count"]
                for slot in (0, 1):
                    left_reference, right_reference = left_references[slot], right_references[slot]
                    if left_reference == 0 or right_reference == 0:
                        continue
                    left_page = replica.page(left, left_reference)
                    right_page = replica.page(right, right_reference)
                    if left_page is None or right_page is None or left_page[0] != 0x05 or right_page[0] != 0x05:
                        return no_layer("pointer_validity_failure", "A3-POINTER-VALIDITY")
                    left_base = formula_base(formula, slot, left_reference)
                    right_base = formula_base(formula, slot, right_reference)
                    low = max(left_base, right_base, 0)
                    high = min(left_base + 16352, right_base + 16352, 65536)
                    for physical_page in range(low, max(low, high)):
                        left_set = _bit_set(left_page, 1, physical_page - left_base)
                        right_set = _bit_set(right_page, 1, physical_page - right_base)
                        if left_set == right_set:
                            continue
                        left_in_use = left_set == (global_model.polarity == "set_means_in_use")
                        right_in_use = right_set == (global_model.polarity == "set_means_in_use")
                        if not (append_low <= physical_page < append_high and not left_in_use and right_in_use):
                            valid = False
                            break
                        if left == "P_ABS_16480" and right == "H_REL_0064" and slot == 0:
                            h64_discriminated = True
                    if not valid:
                        break
                    for physical_page in range(max(append_low, low), min(append_high, high)):
                        left_set = _bit_set(left_page, 1, physical_page - left_base)
                        right_set = _bit_set(right_page, 1, physical_page - right_base)
                        if left_set == (global_model.polarity == "set_means_in_use") or right_set != (global_model.polarity == "set_means_in_use"):
                            valid = False
                            break
                    if not valid:
                        break
                if not valid:
                    break
            if valid and h64_discriminated:
                survivors.add(formula)
        survivors_by_replica.append(survivors)
    common = set.intersection(*survivors_by_replica)
    if not common:
        return no_layer("no_extended_base_candidate", "A3-BASE-NONE")
    if len(common) > 1:
        return no_layer("multiple_extended_base_candidates", "A3-BASE-MULTIPLE")
    return model_layer({"extended_base_formula": next(iter(common))})
