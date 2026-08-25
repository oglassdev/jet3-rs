#!/usr/bin/env python3
"""Fresh H3 recomputation from retained pages and explicit H1/H2 models.

This module is deliberately dependency-free with respect to the A4 producer.
Its inputs are plain mappings or duck-typed replica readers; every format
constant used below is registered by the A4 plan or SRC-0020.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


class H3ValidationError(ValueError):
    """A fail-closed independent H3 recomputation error."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise H3ValidationError("canonical_json_invalid") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _candidate(model_type: str, model: dict[str, object]) -> dict[str, object]:
    identity = {"model_type": model_type, "model": model}
    return {"model_type": model_type, "canonical_candidate_id": _digest(identity), "model": model}


def _unwrap(source: Any, replica: int) -> Mapping[str, Any]:
    per_replica = getattr(source, "per_replica_candidates", None)
    if isinstance(per_replica, Mapping):
        source = per_replica
    elif isinstance(source, Mapping) and isinstance(source.get("per_replica"), Mapping):
        source = source["per_replica"]
    if isinstance(source, Mapping):
        value: Any = source.get(replica, source.get(str(replica), source))
    else:
        layer = getattr(source, "layer", None)
        if not isinstance(layer, Mapping):
            raise H3ValidationError("upstream_model_invalid", str(replica))
        value = layer
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 1:
            raise H3ValidationError("upstream_model_not_decisive", str(replica))
        value = value[0]
    if not isinstance(value, Mapping):
        raise H3ValidationError("upstream_model_invalid", str(replica))
    return value


def _model(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = value.get("model", value)
    if not isinstance(nested, Mapping):
        raise H3ValidationError("upstream_model_invalid")
    return nested


def _bindings(value: Mapping[str, Any], replica: int) -> list[Mapping[str, Any]]:
    raw = value.get("instance_bindings", value.get("bindings"))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise H3ValidationError("h1_bindings_missing", str(replica))
    result = [item for item in raw if isinstance(item, Mapping) and item.get("replica") == replica]
    if not result:
        raise H3ValidationError("h1_bindings_missing", str(replica))
    return result


def _replica(replicas: Mapping[Any, Any], number: int) -> Any:
    try:
        return replicas[number] if number in replicas else replicas[str(number)]
    except (KeyError, TypeError) as exc:
        raise H3ValidationError("replica_missing", str(number)) from exc


def _checkpoints(replica: Any) -> list[str]:
    if hasattr(replica, "checkpoint_ids"):
        raw = replica.checkpoint_ids
    elif isinstance(replica, Mapping):
        raw = replica.get("checkpoint_ids")
        if raw is None:
            points = replica.get("checkpoints")
            raw = list(points) if isinstance(points, Mapping) else None
    else:
        raw = None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise H3ValidationError("checkpoint_order_missing")
    result = list(raw)
    if not result or any(not isinstance(item, str) for item in result) or len(set(result)) != len(result):
        raise H3ValidationError("checkpoint_order_invalid")
    return result


def _page(replica: Any, checkpoint: str, number: int) -> bytes | None:
    if number < 0:
        return None
    if hasattr(replica, "page"):
        value = replica.page(checkpoint, number)
    elif isinstance(replica, Mapping):
        points = replica.get("checkpoints")
        point = points.get(checkpoint) if isinstance(points, Mapping) else None
        pages = point.get("pages") if isinstance(point, Mapping) else None
        if isinstance(pages, Mapping):
            value = pages.get(number, pages.get(str(number)))
        elif isinstance(pages, Sequence) and not isinstance(pages, (str, bytes, bytearray)):
            value = pages[number] if number < len(pages) else None
        else:
            value = None
    else:
        value = None
    if value is None:
        return None
    if not isinstance(value, bytes) or len(value) != 2048:
        raise H3ValidationError("page_invalid", f"{checkpoint}:{number}")
    return value


def _page_count(replica: Any, checkpoint: str) -> int:
    if hasattr(replica, "index"):
        value = replica.index(checkpoint).get("page_count")
    elif isinstance(replica, Mapping):
        point = replica.get("checkpoints", {}).get(checkpoint, {})
        pages = point.get("pages") if isinstance(point, Mapping) else None
        value = point.get("page_count") if isinstance(point, Mapping) else None
        if value is None and isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
            value = len(pages)
    else:
        value = None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise H3ValidationError("page_count_invalid", checkpoint)
    return value


def _range(binding: Mapping[str, Any], order: list[str]) -> list[str]:
    raw = binding.get("applicable_checkpoint_range")
    if not isinstance(raw, Mapping):
        raise H3ValidationError("lifecycle_range_invalid")
    first = raw.get("start", raw.get("first_checkpoint_id", raw.get("start_checkpoint_id")))
    last = raw.get("end", raw.get("last_checkpoint_id", raw.get("end_checkpoint_id")))
    try:
        left, right = order.index(first), order.index(last)
    except ValueError as exc:
        raise H3ValidationError("lifecycle_range_invalid") from exc
    if left > right:
        raise H3ValidationError("lifecycle_range_invalid")
    return order[left : right + 1]


def _row(page: bytes, row: int, mask: int) -> tuple[bytes, int, int]:
    if page[0] != 1 or not isinstance(row, int) or not 0 <= row <= 255:
        raise H3ValidationError("map_row_invalid")
    count = int.from_bytes(page[8:10], "little")
    if row >= count or 10 + 2 * count > 2048:
        raise H3ValidationError("map_row_invalid")
    entries = [int.from_bytes(page[10 + 2 * i : 12 + 2 * i], "little") for i in range(count)]
    if any(raw & 0xC000 for raw in entries):
        raise H3ValidationError("map_row_flags_invalid")
    starts = [raw & mask for raw in entries]
    start, end = starts[row], 2048 if row == 0 else starts[row - 1]
    if not (10 + 2 * count <= start < end <= 2048):
        raise H3ValidationError("map_row_bounds_invalid")
    if any(not (starts[i] < starts[i - 1]) for i in range(1, count)):
        raise H3ValidationError("map_row_bounds_invalid")
    return page[start:end], start, end


def _target(binding: Mapping[str, Any], ordinal: int) -> tuple[int, int]:
    targets = binding.get("locator_targets")
    if not isinstance(targets, Sequence) or len(targets) != 2 or not isinstance(targets[ordinal], Mapping):
        raise H3ValidationError("locator_targets_invalid")
    page, row = targets[ordinal].get("page"), targets[ordinal].get("row")
    if not isinstance(page, int) or not isinstance(row, int):
        raise H3ValidationError("locator_targets_invalid")
    return page, row


def _bitmap(row: bytes, polarity: str) -> set[int]:
    if len(row) < 5 or row[0] != 0:
        raise H3ValidationError("type0_row_invalid")
    base = int.from_bytes(row[1:5], "little")
    wanted = polarity == "set_bit_owned_in_use"
    return {
        base + bit
        for bit in range((len(row) - 5) * 8)
        if bool(row[5 + bit // 8] & (1 << (bit % 8))) == wanted
    }


def _slots(row: bytes) -> list[int]:
    if not row or row[0] != 1 or (len(row) - 1) % 4:
        raise H3ValidationError("type1_row_invalid")
    return [int.from_bytes(row[pos : pos + 4], "little") for pos in range(1, len(row), 4)]


def _decode_reference(page: bytes, slot: int, reference: int, formula: str) -> set[int]:
    result: set[int] = set()
    for bit in range(16352):
        if not page[4 + bit // 8] & (1 << (bit % 8)):
            continue
        if formula == "slot_ordinal_times_16352_plus_bit_index":
            value = slot * 16352 + bit
        elif formula == "referenced_page_times_16352_plus_bit_index":
            value = reference * 16352 + bit
        elif formula == "slot_ordinal_times_16352_plus_bit_index_minus_one":
            value = slot * 16352 + bit - 1
        elif formula == "slot_ordinal_times_16352_plus_bit_index_plus_one":
            value = slot * 16352 + bit + 1
        else:
            raise H3ValidationError("base_formula_unregistered", formula)
        if value >= 0:
            result.add(value)
    return result


def _make_result(
    candidates: list[dict[str, object]], terminal: str | None, measured: int,
    stage: str | None, evidence: object = None,
) -> dict[str, object]:
    return {
        "status": "model" if terminal is None else "no_outcome",
        "predicate_measured_survivor_count": measured,
        "derivation_survivor_count": 1 if terminal is None else 0,
        "terminal_predicate_id": terminal,
        "terminal_payload_kind": None if terminal is None else (
            "invalid_observation" if terminal == "A4-H3-REFERENCE-INVALID" else
            "replica_pair" if terminal == "A4-H3-REPLICA-DISAGREEMENT" else "candidate_set"
        ),
        "terminal_candidate_stage": stage,
        "candidates": candidates,
        "terminal_evidence": evidence,
        "canonical_candidates_sha256": _digest(candidates),
    }


def _formula_transition_ok(
    states: list[dict[str, Any]], decoded: Mapping[tuple[int, str, int], set[int]],
    owned: int, available: int, plan: Mapping[str, Any],
) -> bool:
    def pages(state: Mapping[str, Any], ordinal: int) -> set[int]:
        row = state["rows"][ordinal]
        return row["decoded"] if row["tag"] == 0 else decoded[
            (state["binding"], state["checkpoint"], ordinal)
        ]

    by_role_checkpoint = {(state["role"], state["checkpoint"]): state for state in states}
    coverage = plan.get("checkpoint_design", {}).get("transition_coverage", {})
    growth = [key for key in coverage if key.endswith(("_growth", "_absolute", "_relative"))]
    if len(growth) != 3:
        return False
    for key in growth:
        role = key.split("_", 1)[0].upper()
        for left, right in zip(coverage[key], coverage[key][1:]):
            old, new = by_role_checkpoint.get((role, left)), by_role_checkpoint.get((role, right))
            if old is None or new is None:
                continue
            old_owned, new_owned = pages(old, owned), pages(new, owned)
            old_available, new_available = pages(old, available), pages(new, available)
            gained = new_owned - old_owned
            if not old_owned <= new_owned or gained & (new_available - old_available):
                return False
    churn = [key for key in coverage if key.endswith("_churn")]
    if len(churn) != 1 or len(coverage[churn[0]]) != 4:
        return False
    role = churn[0].split("_", 1)[0].upper()
    sequence = coverage[churn[0]]
    for left, right, deleting in ((sequence[0], sequence[1], True),
                                  (sequence[1], sequence[2], False)):
        old, new = by_role_checkpoint.get((role, left)), by_role_checkpoint.get((role, right))
        if old is None or new is None:
            continue
        old_owned, new_owned = pages(old, owned), pages(new, owned)
        old_available, new_available = pages(old, available), pages(new, available)
        if deleting and (not new_owned <= old_owned or not old_available <= new_available):
            return False
        if not deleting and (not old_owned <= new_owned or not new_available <= old_available):
            return False
    for left, right in plan.get("checkpoint_design", {}).get("idle_pairs", []):
        roles = {state["role"] for state in states}
        for role in roles:
            old, new = by_role_checkpoint.get((role, left)), by_role_checkpoint.get((role, right))
            if old is None and new is None:
                continue
            if old is None or new is None or any(pages(old, ordinal) != pages(new, ordinal)
                                                 for ordinal in (owned, available)):
                return False
    return True


def _ordered_predicates(plan: Mapping[str, Any], expected: list[str]) -> list[str]:
    registry = plan.get("predicate_registry")
    contracts = registry.get("predicate_contracts") if isinstance(registry, Mapping) else None
    if not isinstance(contracts, list):
        raise H3ValidationError("predicate_registry_invalid")
    selected = [
        row
        for row in contracts
        if isinstance(row, Mapping)
        and isinstance(row.get("predicate_id"), str)
        and row["predicate_id"].startswith("A4-H3-")
        and "HOLDOUT" not in row["predicate_id"]
    ]
    if any(not isinstance(row.get("order"), int) for row in selected):
        raise H3ValidationError("predicate_registry_invalid")
    selected.sort(key=lambda row: row["order"])
    actual = [row["predicate_id"] for row in selected]
    if actual != expected or len({row["order"] for row in selected}) != len(selected):
        raise H3ValidationError("predicate_registry_invalid")
    return actual


def _predicate_row(
    plan: Mapping[str, Any], predicate: str, status: str, measured: int,
) -> dict[str, object]:
    contract = next(row for row in plan["predicate_registry"]["predicate_contracts"]
                    if row.get("predicate_id") == predicate)
    return {"predicate_id": predicate, "order": contract["order"], "scope": contract["scope"],
            "status": status, "terminal_predicate_id": predicate if status == "fail" else None,
            "predicate_measured_survivor_count": measured,
            "derivation_survivor_count": 1 if status == "pass" else 0,
            "reachability_fixture_id": contract["reachability_fixture_id"]}


def _compatible_identifier_indexes(
    values: Mapping[str, Mapping[int, int]], lifecycle: str,
) -> dict[str, set[int]]:
    """Return occurrence indexes participating in a registered identifier matching."""
    operations = list(values)
    left, right = "T2_CREATE", "T2_RECREATE"
    if left not in values or right not in values:
        return {}

    def distinct_exists(sets: Mapping[str, set[int]], forced: tuple[str, int] | None = None) -> bool:
        chosen: dict[int, str] = {}
        if forced is not None:
            operation, identifier = forced
            chosen[identifier] = operation
        def visit(operation: str, seen: set[int]) -> bool:
            for identifier in sets[operation]:
                if identifier in seen or (forced and operation == forced[0] and identifier != forced[1]):
                    continue
                seen.add(identifier)
                prior = chosen.get(identifier)
                if prior is None or (prior != forced[0] and visit(prior, seen)):
                    chosen[identifier] = operation
                    return True
            return False
        return all(operation == (forced[0] if forced else None) or visit(operation, set())
                   for operation in sets)

    identifier_sets = {operation: set(rows.values()) for operation, rows in values.items()}
    participation = {operation: set() for operation in operations}
    for operation in operations:
        for index, identifier in values[operation].items():
            if lifecycle == "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2":
                fits = distinct_exists(identifier_sets, (operation, identifier))
            elif lifecycle == "stable_for_same_physical_name_including_t2_v1_v2":
                fits = False
                for shared in identifier_sets[left] & identifier_sets[right]:
                    if operation in (left, right) and identifier != shared:
                        continue
                    remaining = {key: choices - {shared} for key, choices in identifier_sets.items()
                                 if key not in (left, right)}
                    forced = None if operation in (left, right) else (operation, identifier)
                    if distinct_exists(remaining, forced):
                        fits = True
                        break
            else:
                fits = False
            if fits:
                participation[operation].add(index)
    return participation if all(participation.values()) else {}


def _replica_stages(
    number: int, replica: Any, h1: Mapping[str, Any], h2: Mapping[str, Any],
    checkpoints: list[str], conversion: dict[str, object], conversions: list[str],
    formulas: list[str], bounds: Mapping[str, Any], plan: Mapping[str, Any],
    qualified: set[tuple[int, str, int]], work: dict[str, int],
):
    actual_order = _checkpoints(replica)
    if actual_order != checkpoints:
        raise H3ValidationError("checkpoint_order_mismatch", str(number))
    mask, polarity = h2.get("row_mask"), h2.get("polarity")
    owned, available = h2.get("owned_in_use_locator_ordinal"), h2.get("available_locator_ordinal")
    if mask not in (0x1FFF, 0x0FFF) or polarity not in (
        "set_bit_owned_in_use", "clear_bit_owned_in_use"
    ) or sorted((owned, available)) != [0, 1]:
        raise H3ValidationError("h2_model_invalid", str(number))
    states: list[dict[str, Any]] = []
    for binding_index, binding in enumerate(_bindings(h1, number)):
        for checkpoint in _range(binding, actual_order):
            rows: dict[int, dict[str, Any]] = {}
            for ordinal in (0, 1):
                page_number, row_number = _target(binding, ordinal)
                page = _page(replica, checkpoint, page_number)
                if page is None:
                    raise H3ValidationError("map_page_missing")
                raw, start, end = _row(page, row_number, mask)
                qualified.add((number, checkpoint, page_number))
                entry: dict[str, Any] = {"tag": raw[0], "raw": raw, "page": page_number,
                                        "row": row_number, "start": start, "end": end}
                if raw[0] == 0:
                    entry["decoded"] = _bitmap(raw, polarity)
                elif raw[0] == 1:
                    entry["slots"] = _slots(raw)
                else:
                    raise H3ValidationError("map_tag_unsupported")
                rows[ordinal] = entry
            states.append({"binding": binding_index, "role": binding.get("logical_role"),
                           "checkpoint": checkpoint, "rows": rows})
    by_binding: dict[int, list[dict[str, Any]]] = {}
    for state in states:
        by_binding.setdefault(state["binding"], []).append(state)
    conversion_legs = []
    for sequence in by_binding.values():
        for left, right in zip(sequence, sequence[1:]):
            before, after = left["rows"][owned], right["rows"][owned]
            if before["tag"] == 0 and after["tag"] == 1 and any(after["slots"]):
                conversion_legs.append((before, after))
    if not conversion_legs:
        yield {"terminal": 0, "candidates": [], "measured": 0}
        return
    yield {"terminal": None, "candidates": [conversion], "measured": 1}
    type1 = [(state, ordinal, row) for state in states for ordinal, row in state["rows"].items()
             if row["tag"] == 1]
    inactive = any(0 in row["slots"] for _, _, row in type1)
    if not inactive:
        yield {"terminal": 1, "candidates": [conversion], "measured": 1}
        return
    yield {"terminal": None, "candidates": [conversion], "measured": 1}
    bad: dict[str, Any] | None = None
    referenced: dict[tuple[str, int], bytes] = {}
    reference_pages: dict[str, set[int]] = {}
    for state, _ordinal, row in type1:
        for slot, reference in enumerate(row["slots"]):
            if reference == 0:
                continue
            count = _page_count(replica, state["checkpoint"])
            reached = reference_pages.setdefault(state["checkpoint"], set())
            if reference < count and reference not in reached and len(reached) >= bounds.get(
                "max_qualified_pages_per_submodel", 16
            ):
                raise H3ValidationError("resource_bound_breach", "tag05 references")
            if reference < count:
                reached.add(reference)
            page = _page(replica, state["checkpoint"], reference) if reference < count else None
            reason = "out_of_range" if reference >= count else "missing_page" if page is None else (
                "not_tag_05" if page[:4] != b"\x05\x01\x00\x00" else None
            )
            if reason:
                bad = {"kind": "reference", "input_model_id": conversion["canonical_candidate_id"],
                       "observation": {"replica": number, "checkpoint_id": state["checkpoint"],
                       "page": row["page"], "slot_ordinal": slot, "referenced_page": reference,
                       "observed_tag_byte": None if page is None else page[0], "reason": reason}}
                break
            qualified.add((number, state["checkpoint"], reference))
            referenced[(state["checkpoint"], reference)] = page
        if bad:
            break
    if bad:
        yield {"terminal": 2, "candidates": [conversion], "measured": 1, "evidence": bad}
        return
    yield {"terminal": None, "candidates": [conversion], "measured": 1}
    work["type_0_and_tag_05_bitmap_bits"] += 16352 * len(referenced)
    regions = {"inactive": inactive, "active": False, "bit0": False, "nonzero": False,
               "boundary_last": False, "boundary_next": False}
    for state, _ordinal, row in type1:
        active_slots = {slot for slot, ref in enumerate(row["slots"]) if ref}
        regions["active"] |= bool(active_slots)
        set_bits: dict[int, set[int]] = {}
        for slot in active_slots:
            page = referenced[(state["checkpoint"], row["slots"][slot])]
            bits = {bit for bit in range(16352) if page[4 + bit // 8] & (1 << (bit % 8))}
            set_bits[slot] = bits
            regions["bit0"] |= 0 in bits
            regions["nonzero"] |= any(bit > 0 for bit in bits)
            regions["boundary_last"] |= 16351 in bits
        regions["boundary_next"] |= any(
            slot + 1 in active_slots and 16351 in bits and 0 in set_bits.get(slot + 1, set())
            for slot, bits in set_bits.items()
        )
    if not all(regions.values()):
        yield {"terminal": 3, "candidates": [conversion], "measured": 1}
        return
    yield {"terminal": None, "candidates": [conversion], "measured": 1}
    fitting = []
    decoded_by_formula: dict[str, dict[tuple[int, str, int], set[int]]] = {}
    for formula in formulas:
        decoded: dict[tuple[int, str, int], set[int]] = {}
        for state, ordinal, row in type1:
            pages: set[int] = set()
            for slot, reference in enumerate(row["slots"]):
                if reference:
                    pages |= _decode_reference(referenced[(state["checkpoint"], reference)], slot,
                                               reference, formula)
            decoded[(state["binding"], state["checkpoint"], ordinal)] = pages
        ok = True
        for before, after in conversion_legs:
            target = next(state for state in states if state["rows"][owned] is after)
            if not before["decoded"] <= decoded[(target["binding"], target["checkpoint"], owned)]:
                ok = False
                break
        if ok:
            ok = _formula_transition_ok(states, decoded, owned, available, plan)
        work["base_formula_evaluations"] += sum(state["rows"][owned]["tag"] == 1 for state in states)
        if ok:
            fitting.append(_candidate("h3_final_base_formula", {
                "conversion": conversions[0], "base_formula": formula,
            }))
            decoded_by_formula[formula] = decoded
        if len(fitting) > bounds.get("max_candidate_models"):
            raise H3ValidationError("resource_bound_breach", "candidate 4097")
    if not fitting:
        yield {"terminal": 4, "candidates": [], "measured": 0}
        return
    yield {"terminal": None, "candidates": fitting, "measured": len(fitting)}
    if len(fitting) > 1:
        yield {"terminal": 5, "candidates": fitting, "measured": len(fitting)}
        return
    formula = fitting[0]["model"]["base_formula"]
    yield {"terminal": None, "candidates": fitting, "measured": 1,
           "admitted": decoded_by_formula[formula]}


def recompute_h3(
    replicas: Mapping[Any, Any], h1_models: Any,
    h2_models: Any, plan: Mapping[str, Any],
) -> dict[str, object]:
    """Recompute derivation H3 for replicas 1 and 2 from retained page bytes."""
    grammar = plan.get("candidate_grammars", {}).get("h3", {})
    bounds = plan.get("bounds", {})
    formulas = grammar.get("base_formulas")
    conversions = grammar.get("conversion_candidates")
    if conversions != ["structural_type_0_to_type_1_with_nonzero_u32_slots"]:
        raise H3ValidationError("conversion_grammar_invalid")
    if not isinstance(formulas, list) or len(formulas) != 4 or len(set(formulas)) != 4:
        raise H3ValidationError("base_formula_grammar_invalid")
    checkpoints = plan.get("checkpoint_design", {}).get("checkpoint_ids")
    if not isinstance(checkpoints, list) or len(checkpoints) > bounds.get("max_checkpoints_per_replica", -1):
        raise H3ValidationError("checkpoint_plan_invalid")
    conversion = _candidate("h3_conversion", {"conversion": conversions[0]})
    qualified: set[tuple[int, str, int]] = set()
    work = {"type_0_and_tag_05_bitmap_bits": 0, "base_formula_evaluations": 0}
    per_replica: dict[int, dict[str, Any]] = {}
    predicate_ids = _ordered_predicates(plan, [
        "A4-H3-CONVERSION-NONE", "A4-H3-INACTIVE-SLOT-NONE", "A4-H3-REFERENCE-INVALID",
        "A4-H3-BASE-DISCRIMINATION", "A4-H3-BASE-NONE", "A4-H3-BASE-MULTIPLE",
        "A4-H3-REPLICA-DISAGREEMENT",
    ])
    runners = {number: _replica_stages(
        number, _replica(replicas, number), _unwrap(h1_models, number),
        _model(_unwrap(h2_models, number)), checkpoints, conversion, conversions,
        formulas, bounds, plan, qualified, work,
    ) for number in (1, 2)}
    terminal_number = None
    for index in range(6):
        for number in (1, 2):
            try:
                state = next(runners[number])
            except StopIteration as exc:
                raise H3ValidationError("predicate_stage_incomplete", str(number)) from exc
            per_replica[number] = state
            if state["terminal"] is not None:
                state["terminal"] = predicate_ids[index]
                terminal_number = number
                break
        if terminal_number is not None:
            break
    rows = []
    if terminal_number is not None:
        state = per_replica[terminal_number]
        terminal = state["terminal"]
        terminal_index = predicate_ids.index(terminal)
        for index, predicate in enumerate(predicate_ids):
            status = "fail" if index == terminal_index else "pass" if index < terminal_index else "not_applicable"
            if index in (0, 1, 2, 3):
                count = 0 if index == terminal_index and index == 0 else 1
            elif index in (4, 5):
                count = state["measured"] if terminal_index >= 4 else 0
            else:
                count = 2 if index == terminal_index else 0
            rows.append(_predicate_row(plan, predicate, status, count if status != "not_applicable" else 0))
        result = _make_result(state["candidates"], terminal, state["measured"],
                              "h3_conversion" if predicate_ids.index(terminal) <= 3 else
                              "h3_final_base_formula", state.get("evidence"))
    else:
        first, second = per_replica[1]["candidates"][0], per_replica[2]["candidates"][0]
        if first["model"] != second["model"]:
            terminal = predicate_ids[-1]
            evidence = {"kind": "replica_pair", "entries": [
                {"replica": number, "canonical_model_id": None,
                 "canonical_candidate_id": per_replica[number]["candidates"][0]["canonical_candidate_id"],
                 "complete_candidate": per_replica[number]["candidates"][0]} for number in (1, 2)
            ]}
            result = _make_result([], terminal, 2, "h3_final_base_formula", evidence)
            rows = [_predicate_row(plan, predicate, "fail" if predicate == terminal else "pass",
                                   2 if predicate == terminal else 1) for predicate in predicate_ids]
        else:
            terminal = None
            result = _make_result([first], None, 1, None)
            rows = [_predicate_row(plan, predicate, "pass", 1) for predicate in predicate_ids]
    admitted: dict[int, dict[str, list[int]]] = {}
    for number, state in per_replica.items():
        if "admitted" in state:
            combined: dict[str, set[int]] = {}
            for (_binding, checkpoint, ordinal), pages in state["admitted"].items():
                if ordinal == _model(_unwrap(h2_models, number))["owned_in_use_locator_ordinal"]:
                    combined.setdefault(checkpoint, set()).update(pages)
            admitted[number] = {checkpoint: sorted(pages) for checkpoint, pages in combined.items()}
    return {"result": result, "predicates": rows, "qualified_pages": [
        {"replica": r, "checkpoint_id": c, "page_number": p} for r, c, p in sorted(qualified)
    ], "work_charges": work, "admitted_pages": admitted, "per_replica": per_replica}


def _single_candidate(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value.get("result"), Mapping):
        value = value["result"]
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        if len(candidates) != 1 or not isinstance(candidates[0], Mapping):
            raise H3ValidationError("frozen_model_not_decisive")
        value = candidates[0]
    return value


def _single_model(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _model(_single_candidate(value))


def predict_h3_holdout(
    replica3: Any,
    h1_result: Mapping[str, Any],
    h2_result: Mapping[str, Any],
    h3_result: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> bool:
    """Apply the one frozen H3 formula to replica 3 without fitting a new one."""
    try:
        order = _checkpoints(replica3)
        if order != plan.get("checkpoint_design", {}).get("checkpoint_ids"):
            return False
        h1_model = _single_model(h1_result)
        h2_model = _single_model(h2_result)
        h3_model = _single_model(h3_result)
        formula = h3_model.get("base_formula")
        registered = plan.get("candidate_grammars", {}).get("h3", {}).get("base_formulas")
        if formula not in registered:
            return False
        mask = h2_model.get("row_mask")
        polarity = h2_model.get("polarity")
        owned = h2_model.get("owned_in_use_locator_ordinal")
        available = h2_model.get("available_locator_ordinal")
        if mask not in (0x1FFF, 0x0FFF) or sorted((owned, available)) != [0, 1]:
            return False
        states: list[dict[str, Any]] = []
        qualified_references: dict[tuple[str, int], bytes] = {}
        reference_pages: dict[str, set[int]] = {}
        for binding_index, binding in enumerate(_bindings(_single_candidate(h1_result), 3)):
            for checkpoint in _range(binding, order):
                rows: dict[int, dict[str, Any]] = {}
                for ordinal in (0, 1):
                    page_number, row_number = _target(binding, ordinal)
                    page = _page(replica3, checkpoint, page_number)
                    if page is None:
                        return False
                    raw, _start, _end = _row(page, row_number, mask)
                    entry: dict[str, Any] = {"tag": raw[0], "page": page_number}
                    if raw[0] == 0:
                        entry["decoded"] = _bitmap(raw, polarity)
                    elif raw[0] == 1:
                        entry["slots"] = _slots(raw)
                    else:
                        return False
                    rows[ordinal] = entry
                states.append({"binding": binding_index, "checkpoint": checkpoint, "rows": rows})
        type1 = [(state, ordinal, row) for state in states for ordinal, row in state["rows"].items()
                 if row["tag"] == 1]
        if not type1:
            return False
        for state, _ordinal, row in type1:
            for reference in row["slots"]:
                if reference == 0:
                    continue
                if reference >= _page_count(replica3, state["checkpoint"]):
                    return False
                reached = reference_pages.setdefault(state["checkpoint"], set())
                if reference not in reached and len(reached) >= plan["bounds"].get(
                    "max_qualified_pages_per_submodel", 16
                ):
                    raise H3ValidationError("resource_bound_breach", "tag05 references")
                reached.add(reference)
                page = _page(replica3, state["checkpoint"], reference)
                if page is None or page[:4] != b"\x05\x01\x00\x00":
                    return False
                qualified_references[(state["checkpoint"], reference)] = page
        regions = {"inactive": False, "active": False, "bit0": False, "nonzero": False,
                   "boundary_last": False, "boundary_next": False}
        decoded: dict[tuple[int, str, int], set[int]] = {}
        for state, ordinal, row in type1:
            active = {slot for slot, reference in enumerate(row["slots"]) if reference}
            regions["inactive"] |= 0 in row["slots"]
            regions["active"] |= bool(active)
            set_bits: dict[int, set[int]] = {}
            pages: set[int] = set()
            for slot in active:
                reference = row["slots"][slot]
                page = qualified_references[(state["checkpoint"], reference)]
                bits = {bit for bit in range(16352) if page[4 + bit // 8] & (1 << (bit % 8))}
                set_bits[slot] = bits
                regions["bit0"] |= 0 in bits
                regions["nonzero"] |= any(bit > 0 for bit in bits)
                regions["boundary_last"] |= 16351 in bits
                pages |= _decode_reference(page, slot, reference, formula)
            regions["boundary_next"] |= any(
                slot + 1 in active and 16351 in bits and 0 in set_bits.get(slot + 1, set())
                for slot, bits in set_bits.items()
            )
            decoded[(state["binding"], state["checkpoint"], ordinal)] = pages
        if not all(regions.values()):
            return False
        sequences: dict[int, list[dict[str, Any]]] = {}
        for state in states:
            sequences.setdefault(state["binding"], []).append(state)
        saw_conversion = False
        idle_pairs = {tuple(pair) for pair in plan.get("checkpoint_design", {}).get("idle_pairs", [])}
        for sequence in sequences.values():
            for left, right in zip(sequence, sequence[1:]):
                before, after = left["rows"][owned], right["rows"][owned]
                if before["tag"] == 0 and after["tag"] == 1 and any(after["slots"]):
                    saw_conversion = True
                    if not before["decoded"] <= decoded[(right["binding"], right["checkpoint"], owned)]:
                        return False
                if (left["checkpoint"], right["checkpoint"]) in idle_pairs:
                    for ordinal in (owned, available):
                        if left["rows"][ordinal]["tag"] != right["rows"][ordinal]["tag"]:
                            return False
                        if left["rows"][ordinal]["tag"] == 1 and decoded[
                            (left["binding"], left["checkpoint"], ordinal)
                        ] != decoded[(right["binding"], right["checkpoint"], ordinal)]:
                            return False
        return saw_conversion
    except H3ValidationError as exc:
        if exc.code == "resource_bound_breach":
            raise
        return False
    except (KeyError, TypeError, ValueError, IndexError):
        return False
