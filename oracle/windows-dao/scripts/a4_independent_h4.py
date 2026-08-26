#!/usr/bin/env python3
"""Fresh A4 H4 recomputation from bundle pages and explicit upstream models."""
from __future__ import annotations
import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from typing import Any
from a4_independent_h4_contract import H4ValidationError, catalog_rows as _all_rows, first_cardinality_failure, is_resource_error
from a4_independent_h3 import (
    H3ValidationError,
    _bindings,
    _bitmap,
    _canonical,
    _compatible_identifier_indexes,
    _digest,
    _model,
    _page,
    _page_count,
    _predicate_row,
    _replica,
    _range,
    _row,
    _single_candidate,
    _single_model,
    _slots,
    _target,
    _unwrap,
)
def _candidate(
    model_type: str, model: dict[str, object], bindings: list[dict[str, object]] | None = None
) -> dict[str, object]:
    model_identity = {"model_type": model_type, "model": model}
    candidate_identity = dict(model_identity)
    result: dict[str, object] = {"model_type": model_type}
    if bindings is not None:
        result["canonical_model_id"] = _digest(model_identity)
        candidate_identity["instance_bindings"] = bindings
    result["canonical_candidate_id"] = _digest(candidate_identity)
    result["model"] = model
    if bindings is not None:
        result["instance_bindings"] = bindings
    if len(_canonical(result)) > 4096:
        raise H4ValidationError("resource_bound_breach", "candidate byte 4097")
    return result
def _layout_target(raw: bytes, layout: str) -> tuple[int, int]:
    if len(raw) != 4:
        raise H4ValidationError("locator_window_invalid")
    if layout == "u24le_page_then_u8_row":
        return int.from_bytes(raw[:3], "little"), raw[3]
    if layout == "u8_row_then_u24le_page":
        return int.from_bytes(raw[1:], "little"), raw[0]
    raise H4ValidationError("locator_layout_invalid")
def _formula_pages(page: bytes, slot: int, reference: int, formula: str) -> set[int]:
    if page[:4] != b"\x05\x01\x00\x00":
        raise H4ValidationError("tag05_page_invalid")
    pages: set[int] = set()
    for bit in range(16352):
        if not page[4 + bit // 8] & (1 << (bit % 8)):
            continue
        base = slot if formula.startswith("slot_ordinal") else reference
        value = base * 16352 + bit
        if formula.endswith("minus_one"):
            value -= 1
        elif formula.endswith("plus_one"):
            value += 1
        if value >= 0:
            pages.add(value)
    return pages
def _traverse(
    replica: Any,
    number: int,
    checkpoint: str,
    tdef_page: int,
    h1: Mapping[str, Any],
    h2: Mapping[str, Any],
    h3: Mapping[str, Any],
    qualified: set[tuple[int, str, int]],
    work: dict[str, int],
) -> tuple[tuple[tuple[int, int], tuple[int, int]], set[int]]:
    tdef = _page(replica, checkpoint, tdef_page)
    if tdef is None or tdef[0] != 2:
        raise H4ValidationError("system_tdef_invalid")
    qualified.add((number, checkpoint, tdef_page))
    offsets = h1.get("locator_offsets")
    if not isinstance(offsets, Sequence) or list(offsets) != sorted(offsets) or len(offsets) != 2:
        raise H4ValidationError("locator_offsets_invalid")
    targets = tuple(_layout_target(tdef[offset : offset + 4], h1.get("layout")) for offset in offsets)
    decoded: dict[int, set[int]] = {}
    references: set[int] = set()
    for ordinal, (page_number, row_number) in enumerate(targets):
        page = _page(replica, checkpoint, page_number)
        if page is None:
            raise H4ValidationError("system_map_page_missing")
        raw, _start, _end = _row(page, row_number, h2.get("row_mask"))
        qualified.add((number, checkpoint, page_number))
        if raw[0] == 0:
            decoded[ordinal] = _bitmap(raw, h2.get("polarity"))
        elif raw[0] == 1:
            slots = _slots(raw)
            decoded[ordinal] = set()
            for slot, reference in enumerate(slots):
                if reference == 0:
                    continue
                if reference >= _page_count(replica, checkpoint):
                    raise H4ValidationError("system_reference_out_of_range")
                if reference not in references and len(references) >= 16:
                    raise H4ValidationError("resource_bound_breach", "system references")
                references.add(reference)
                reference_page = _page(replica, checkpoint, reference)
                if reference_page is None:
                    raise H4ValidationError("system_reference_missing")
                qualified.add((number, checkpoint, reference))
                decoded[ordinal].update(_formula_pages(
                    reference_page, slot, reference, h3.get("base_formula")
                ))
        else:
            raise H4ValidationError("system_map_tag_invalid")
    owned = h2.get("owned_in_use_locator_ordinal")
    available = h2.get("available_locator_ordinal")
    if sorted((owned, available)) != [0, 1] or not decoded[available] <= decoded[owned]:
        raise H4ValidationError("system_role_model_invalid")
    return targets, decoded[owned]
def _state_digest(replica: Any, checkpoint: str, page: int) -> str | None:
    if hasattr(replica, "state"):
        value = replica.state(checkpoint, page)
        if value is not None and (not isinstance(value, str) or len(value) != 64):
            raise H4ValidationError("page_state_invalid")
        return value
    raw = _page(replica, checkpoint, page)
    return None if raw is None else hashlib.sha256(raw).hexdigest()
def _changed(replica: Any, left: str, right: str, maximum: int) -> set[int]:
    count = max(_page_count(replica, left), _page_count(replica, right))
    if count > maximum:
        raise H4ValidationError("resource_bound_breach", "page count")
    return {page for page in range(count) if _state_digest(replica, left, page) != _state_digest(replica, right, page)}
def _retained_rows(
    replica: Any, operation: str, pages: set[int], checkpoints: list[str], mask: int,
    qualified: set[tuple[int, str, int]], number: int, work: dict[str, int] | None,
    charged: set[tuple[int, str, int, int]],
) -> list[tuple[int, int, int, int, bytes]]:
    before = checkpoints[checkpoints.index(operation) - 1]
    result = []
    for page_number in sorted(pages):
        page = _page(replica, operation, page_number)
        if page is None or page[0] != 1:
            continue
        qualified.add((number, operation, page_number))
        before_page = _page(replica, before, page_number)
        if before_page is not None:
            qualified.add((number, before, page_number))
        before_rows = {} if before_page is None or before_page[0] != 1 else {
            row_number: raw for row_number, _start, _end, raw in _all_rows(
                before_page, mask, (number, before, page_number), work, charged)
        }
        for row_number, start, end, raw in _all_rows(
                page, mask, (number, operation, page_number), work, charged):
            if before_rows.get(row_number) == raw:
                continue
            last = operation if operation == "T2_CREATE" else checkpoints[-1]
            stable = True
            for checkpoint in checkpoints[checkpoints.index(operation) : checkpoints.index(last) + 1]:
                retained = _page(replica, checkpoint, page_number)
                if retained is None or retained[0] != 1:
                    stable = False
                    break
                qualified.add((number, checkpoint, page_number))
                retained_rows = _all_rows(
                    retained, mask, (number, checkpoint, page_number), work, charged)
                if row_number >= len(retained_rows) or retained_rows[row_number][3] != raw:
                    stable = False
                    break
            if stable:
                result.append((page_number, row_number, start, end, raw))
    return result
def _operation_patterns(plan: Mapping[str, Any], replica: int, operation: str) -> list[tuple[str, bytes]]:
    role = {
        "T1_CREATE_ID": "T1", "T2_CREATE": "T2", "T2_RECREATE": "T2",
        "T3_CREATE": "T3", "T4_CREATE": "T4",
    }.get(operation)
    if operation == "T1_ADD_TEXT":
        name = "Payload"
    elif operation == "T1_ADD_INDEX":
        name = "A4IX_ID"
    else:
        bindings = plan.get("tables", {}).get("role_bindings", [])
        row = next((item for item in bindings if item.get("replica") == replica), None)
        if not isinstance(row, Mapping) or not isinstance(row.get(role), str):
            raise H4ValidationError("role_binding_missing")
        name = row[role]
    candidates = [(f"{operation}_CP1252", name.encode("cp1252")),
                  (f"{operation}_UTF8", name.encode("utf-8"))]
    unique: list[tuple[str, bytes]] = []
    for item in candidates:
        if item[1] not in [raw for _identifier, raw in unique]:
            unique.append(item)
    return unique
def _occurrences(row: bytes, patterns: list[tuple[str, bytes]]) -> list[dict[str, object]]:
    found: list[tuple[int, str, bytes]] = []
    for identifier, pattern in patterns:
        start = 0
        while True:
            position = row.find(pattern, start)
            if position < 0:
                break
            found.append((position, identifier, pattern))
            start = position + 1
    found.sort(key=lambda item: (item[0], item[1]))
    return [
        {"occurrence_index": index, "name_start": start,
         "matched_registered_pattern_id": identifier, "matched_bytes_hex": raw.hex()}
        for index, (start, identifier, raw) in enumerate(found)
    ]
def _bitmap_hex(indexes: set[int], size: int) -> str:
    raw = bytearray((size + 7) // 8)
    for index in indexes:
        if not 0 <= index < size:
            raise H4ValidationError("occurrence_index_invalid")
        raw[index // 8] |= 1 << (index % 8)
    return raw.hex()
def _structural_candidates(
    number: int,
    operation_rows: Mapping[str, tuple[dict[str, object], bytes, list[dict[str, object]]]],
    occurrence_hash: str,
    grammar: Mapping[str, Any],
    plan: Mapping[str, Any],
    work: dict[str, int],
) -> list[tuple[dict[str, object], dict[str, Any]]]:
    operations = grammar["operation_binding_order"]
    total_occurrences = sum(len(operation_rows[operation][2]) for operation in operations)
    work["h4_name_length_structural_tuples"] += total_occurrences * 165888
    equivalence: dict[tuple[object, ...], dict[str, Any]] = {}
    dimensions = itertools.product(
        grammar["kind_start_deltas"], grammar["kind_widths"], grammar["identifier_widths"],
        grammar["endianness"], grammar["name_length_start_deltas"], grammar["name_length_widths"],
        grammar["identifier_lifecycle_relations"],
    )
    for kind_delta, kind_width, identifier_width, endian, length_delta, length_width, lifecycle in dimensions:
        compatible: dict[str, set[int]] = {}
        decoded: dict[str, dict[int, tuple[int, int, int]]] = {}
        for operation in operations:
            _locator, row, occurrences = operation_rows[operation]
            compatible[operation], decoded[operation] = set(), {}
            plausible_lengths = set()
            for identifier, pattern in _operation_patterns(plan, number, operation):
                encoding = "utf-8" if "UTF8" in identifier else "cp1252"
                plausible_lengths.update((len(pattern), len(pattern.decode(encoding))))
            for occurrence in occurrences:
                index, name_start = occurrence["occurrence_index"], occurrence["name_start"]
                kind_start = name_start - kind_delta
                identifier_start = kind_start - identifier_width
                length_start = name_start - length_delta
                spans = [(identifier_start, kind_start), (kind_start, kind_start + kind_width),
                         (length_start, length_start + length_width),
                         (name_start, name_start + len(bytes.fromhex(occurrence["matched_bytes_hex"])))]
                if any(start < 0 or end > len(row) or start >= end for start, end in spans):
                    continue
                if any(max(a, c) < min(b, d) for i, (a, b) in enumerate(spans)
                       for c, d in spans[i + 1 :]):
                    continue
                kind = int.from_bytes(row[kind_start : kind_start + kind_width], endian)
                identifier = int.from_bytes(row[identifier_start:kind_start], endian)
                stored_length = int.from_bytes(row[length_start : length_start + length_width], endian)
                if stored_length not in plausible_lengths:
                    continue
                compatible[operation].add(index)
                decoded[operation][index] = (kind, identifier, stored_length)
        if any(not compatible[operation] for operation in operations):
            continue
        kind_values = {value[0] for rows in decoded.values() for value in rows.values()}
        if len(kind_values) != 3:
            continue
        for table, field, index_kind in itertools.permutations(sorted(kind_values)):
            kind_map = {"table": table, "field": field, "index": index_kind}
            identifiers: dict[str, dict[int, int]] = {}
            for operation in operations:
                role = "field" if operation == "T1_ADD_TEXT" else (
                    "index" if operation == "T1_ADD_INDEX" else "table")
                identifiers[operation] = {occurrence: value[1] for occurrence, value in
                                          decoded[operation].items() if value[0] == kind_map[role]}
            participating = _compatible_identifier_indexes(identifiers, lifecycle)
            if not participating:
                continue
            observed = tuple((operation, tuple((occurrence, decoded[operation][occurrence])
                             for occurrence in sorted(participating[operation])))
                             for operation in operations) + (lifecycle, tuple(kind_map.values()))
            entry = equivalence.get(observed)
            if entry is None:
                model = {"kind_start_delta": kind_delta, "kind_width": kind_width,
                         "identifier_width": identifier_width, "endianness": endian,
                         "name_length_start_delta": length_delta, "name_length_width": length_width,
                         "kind_mapping": kind_map, "identifier_lifecycle": lifecycle}
                filtered = {operation: {index: decoded[operation][index]
                            for index in participating[operation]} for operation in operations}
                equivalence[observed] = {"model": model, "count": 1,
                                         "compatible": participating, "decoded": filtered}
                if len(equivalence) > 4096:
                    raise H4ValidationError("resource_bound_breach", "candidate 4097")
            else:
                entry["count"] += 1
    result = []
    for entry in equivalence.values():
        compatible_rows = []
        for operation in operations:
            maximum = 290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
            indexes = entry["compatible"][operation]
            compatible_rows.append({"operation_id": operation,
                                    "compatible_occurrence_count": len(indexes),
                                    "compatible_occurrence_bitmap_hex": _bitmap_hex(indexes, maximum)})
        binding = {"replica": number, "occurrence_evidence_sha256": occurrence_hash,
                   "value_equivalent_tuple_count": entry["count"],
                   "compatible_occurrences_by_operation": compatible_rows}
        result.append((_candidate("h4_structural_field", entry["model"], [binding]), entry))
    result.sort(key=lambda item: item[0]["canonical_candidate_id"])
    return result
def _merge(candidate_1: dict[str, object], candidate_2: dict[str, object]) -> dict[str, object]:
    if candidate_1.get("model") != candidate_2.get("model"):
        raise H4ValidationError("replica_model_disagreement")
    bindings = list(candidate_1.get("instance_bindings", [])) + list(candidate_2.get("instance_bindings", []))
    return _candidate(candidate_1["model_type"], candidate_1["model"], bindings)
def _slot(
    candidates: list[dict[str, object]], terminal: str | None = None, measured: int | None = None,
    kind: str | None = None, stage: str | None = None, evidence: object = None,
) -> dict[str, object]:
    candidates = sorted(candidates, key=lambda row: str(row["canonical_candidate_id"]))
    applicable = terminal != "not_applicable"
    return {"status": "not_applicable" if not applicable else "no_outcome" if terminal else "model",
            "predicate_measured_survivor_count": 0 if not applicable else len(candidates) if measured is None else measured,
            "derivation_survivor_count": 1 if applicable and terminal is None else 0,
            "terminal_predicate_id": None if terminal in (None, "not_applicable") else terminal,
            "terminal_payload_kind": kind, "terminal_candidate_stage": stage,
            "candidates": candidates, "terminal_evidence": evidence,
            "canonical_candidates_sha256": _digest(candidates)}

def _ordered_predicates(plan: Mapping[str, Any], expected: list[str]) -> list[str]:
    registry = plan.get("predicate_registry")
    contracts = registry.get("predicate_contracts") if isinstance(registry, Mapping) else None
    if not isinstance(contracts, list):
        raise H4ValidationError("predicate_registry_invalid")
    selected = [
        row
        for row in contracts
        if isinstance(row, Mapping)
        and isinstance(row.get("predicate_id"), str)
        and row["predicate_id"].startswith("A4-H4-")
        and "HOLDOUT" not in row["predicate_id"]
    ]
    if any(not isinstance(row.get("order"), int) for row in selected):
        raise H4ValidationError("predicate_registry_invalid")
    selected.sort(key=lambda row: row["order"])
    actual = [row["predicate_id"] for row in selected]
    if actual != expected or len({row["order"] for row in selected}) != len(selected):
        raise H4ValidationError("predicate_registry_invalid")
    return actual
def recompute_h4(
    bundle: Mapping[str, Any], replicas: Mapping[Any, Any], h1_models: Any,
    h2_models: Any, h3_models: Mapping[Any, Any], plan: Mapping[str, Any],
) -> dict[str, object]:
    """Recompute all derivation H4 stages and registered H4 terminals."""
    grammar = plan.get("candidate_grammars", {}).get("h4", {})
    operations = grammar.get("operation_binding_order")
    if operations != grammar.get("required_operations") or not isinstance(operations, list) or len(operations) != 7:
        raise H4ValidationError("operation_order_invalid")
    checkpoints = plan.get("checkpoint_design", {}).get("checkpoint_ids")
    maximum_pages = plan.get("bounds", {}).get("max_final_pages_per_replica")
    maximum_candidates = plan.get("bounds", {}).get("max_candidate_models")
    if not isinstance(checkpoints, list) or not isinstance(maximum_pages, int) or maximum_candidates != 4096:
        raise H4ValidationError("plan_bounds_invalid")
    previous = {checkpoint: checkpoints[checkpoints.index(checkpoint) - 1] for checkpoint in operations}
    required_root_changes = {"T1_CREATE_ID", "T2_CREATE", "T2_RECREATE", "T3_CREATE", "T4_CREATE"}
    qualified: set[tuple[int, str, int]] = set()
    work = {"catalog_root_signatures": 0, "catalog_raw_rows": 0,
            "encoding_union_anchor_bytes": 0, "h4_name_length_structural_tuples": 0,
            "encoding_length_equivalence_candidates": 0}
    roots: dict[int, list[dict[str, object]]] = {}
    traversals: dict[int, dict[int, dict[str, Any]]] = {}
    for number in (1, 2):
        replica = _replica(replicas, number)
        if list(getattr(replica, "checkpoint_ids", checkpoints)) != checkpoints:
            raise H4ValidationError("checkpoint_order_mismatch")
        h1 = _model(_unwrap(h1_models, number))
        h2 = _model(_unwrap(h2_models, number))
        h3_source = _unwrap(h3_models, number)
        h3 = _model(h3_source.get("candidates", [h3_source])[0] if isinstance(
            h3_source.get("candidates"), list
        ) and h3_source.get("candidates") else h3_source)
        found, attempted = [], 0
        traversals[number] = {}
        for tdef_page in range(_page_count(replica, "EMPTY")):
            page = _page(replica, "EMPTY", tdef_page)
            if page is None or page[0] != 2:
                continue
            if attempted >= plan["bounds"]["max_qualified_pages_per_submodel"]:
                raise H4ValidationError("resource_bound_breach", "catalog root pages")
            attempted += 1
            streams: dict[str, set[int]] = {}
            signatures: dict[str, tuple[tuple[int, str | None], ...]] = {}
            locator_targets: dict[str, object] = {}
            valid = True
            for checkpoint in checkpoints:
                work["catalog_root_signatures"] += 1
                try:
                    targets, admitted = _traverse(replica, number, checkpoint, tdef_page,
                                                   h1, h2, h3, qualified, work)
                except (H4ValidationError, H3ValidationError) as exc:
                    if is_resource_error(exc):
                        raise
                    valid = False
                    break
                streams[checkpoint], locator_targets[checkpoint] = admitted, targets
                signatures[checkpoint] = tuple((page, _state_digest(replica, checkpoint, page))
                                               for page in sorted(admitted))
            if not valid or any(signatures[operation] == signatures[previous[operation]]
                                for operation in required_root_changes):
                continue
            model = {"root_selection_signature": "operation_delta_non_name_structure",
                     "locator_offsets": list(h1["locator_offsets"])}
            candidate = _candidate("h4_catalog_root", model,
                                   [{"replica": number, "tdef_page": tdef_page}])
            found.append(candidate)
            traversals[number][tdef_page] = {"admitted": streams, "targets": locator_targets}
            if len(found) > maximum_candidates:
                raise H4ValidationError("resource_bound_breach", "candidate 4097")
        roots[number] = sorted(found, key=lambda item: item["canonical_candidate_id"])
    root_failure = first_cardinality_failure({number: [len(roots[number])] for number in (1, 2)})
    predicate_ids = _ordered_predicates(plan, [
        "A4-H4-CATALOG-ROOT-NONE", "A4-H4-CATALOG-ROOT-MULTIPLE",
        "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED", "A4-H4-CATALOG-RECORD-NONE",
        "A4-H4-CATALOG-RECORD-MULTIPLE", "A4-H4-FIELD-MODEL-NONE",
        "A4-H4-FIELD-MODEL-MULTIPLE", "A4-H4-ENCODING-AMBIGUOUS",
        "A4-H4-REPLICA-DISAGREEMENT",
    ])
    if root_failure is not None:
        number, count, kind = root_failure
        terminal = predicate_ids[kind]
        return _finish_h4(_slot(roots[number], terminal, count, "candidate_set",
                                "h4_catalog_root"), _slot([], "not_applicable"),
                          _slot([], "not_applicable"), terminal, count, predicate_ids,
                          qualified, work, None, plan)
    if roots[1][0]["model"] != roots[2][0]["model"]:
        raise H4ValidationError("root_replica_model_disagreement")
    root = _merge(roots[1][0], roots[2][0])
    root_result = _slot([root])
    deltas: dict[int, dict[str, set[int]]] = {1: {}, 2: {}}
    outside: dict[str, object] | None = None
    for number in (1, 2):
        replica = _replica(replicas, number)
        root_page = roots[number][0]["instance_bindings"][0]["tdef_page"]
        root_streams = traversals[number][root_page]["admitted"]
        excluded_user: dict[str, set[int]] = {checkpoint: {0, 1} for checkpoint in checkpoints}
        for binding in _bindings(_unwrap(h1_models, number), number):
            for checkpoint in checkpoints:
                excluded_user[checkpoint].add(binding["tdef_page"])
                for ordinal in (0, 1):
                    excluded_user[checkpoint].add(_target(binding, ordinal)[0])
        admitted_source = h3_models.get("admitted_pages", {}) if isinstance(h3_models, Mapping) else {}
        admitted_by_checkpoint = admitted_source.get(number, admitted_source.get(str(number), {}))
        for checkpoint, pages in admitted_by_checkpoint.items():
            excluded_user.setdefault(checkpoint, {0, 1}).update(pages)
        for operation in operations:
            changed = _changed(replica, previous[operation], operation, maximum_pages)
            isolated = changed - excluded_user[operation]
            deltas[number][operation] = isolated
            unexpected = sorted(isolated - root_streams[operation])
            if unexpected and outside is None:
                page = unexpected[0]
                outside = {"kind": "schema_delta_outside", "input_model_id": root["canonical_candidate_id"],
                           "observation": {"replica": number, "operation_id": operation,
                           "checkpoint_before": previous[operation], "checkpoint_after": operation,
                           "page": page, "page_sha256_before": _state_digest(replica, previous[operation], page),
                           "page_sha256_after": _state_digest(replica, operation, page)}}
    if outside:
        terminal = predicate_ids[2]
        return _finish_h4(root_result, _slot([], terminal, 1, "invalid_observation", None, outside),
                          _slot([], "not_applicable"), terminal, 1, predicate_ids,
                          qualified, work, None, plan)
    groups: dict[int, dict[str, list[tuple[dict[str, object], bytes]]]] = {1: {}, 2: {}}
    catalog_charges: set[tuple[int, str, int, int]] = set()
    for number in (1, 2):
        replica = _replica(replicas, number)
        mask = _model(_unwrap(h2_models, number))["row_mask"]
        root_page = roots[number][0]["instance_bindings"][0]["tdef_page"]
        streams = traversals[number][root_page]["admitted"]
        for operation in operations:
            rows = []
            retained = _retained_rows(replica, operation, deltas[number][operation] & streams[operation],
                                      checkpoints, mask, qualified, number, work, catalog_charges)
            for page_number, row_number, start, end, raw in retained:
                locator = {"page": page_number, "row": row_number, "row_start": start, "row_end": end}
                model = {"replica": number,
                         "root_candidate_id": roots[number][0]["canonical_candidate_id"],
                         "operation_id": operation, "canonical_record_locator": locator}
                rows.append((_candidate("h4_operation_record", model), raw))
                if sum(len(group) for by_operation in groups.values() for group in by_operation.values()
                       ) + len(rows) > maximum_candidates:
                    raise H4ValidationError("resource_bound_breach", "candidate 4097")
            groups[number][operation] = sorted(rows, key=lambda item: item[0]["canonical_candidate_id"])
    group_failure = first_cardinality_failure({number: [len(groups[number][operation])
                                                 for operation in operations] for number in (1, 2)})
    if group_failure is not None:
        number, measured, kind = group_failure
        terminal = predicate_ids[3 + kind]
        candidates = sorted((item[0] for operation in operations for item in groups[number][operation]),
                            key=lambda item: item["canonical_candidate_id"])
        evidence = {"kind": "operation_groups", "groups": [
            {"operation_id": operation, "cardinality": len(groups[number][operation]),
             "candidate_ids": [item[0]["canonical_candidate_id"] for item in groups[number][operation]]}
            for operation in operations]}
        return _finish_h4(root_result, _slot(candidates, terminal, measured,
                          "grouped_candidate_set", "h4_operation_record", evidence),
                          _slot([], "not_applicable"), terminal, measured, predicate_ids,
                          qualified, work, None, plan)
    occurrence_groups = []
    operation_rows: dict[int, dict[str, tuple[dict[str, object], bytes, list[dict[str, object]]]]] = {1: {}, 2: {}}
    total_occurrences = 0
    for number in (1, 2):
        bindings = []
        for operation in operations:
            candidate, raw = groups[number][operation][0]
            patterns = _operation_patterns(plan, number, operation)
            work["encoding_union_anchor_bytes"] += len(raw) * len(patterns)
            occurrences = _occurrences(raw, patterns)
            total_occurrences += len(occurrences)
            maximum = 290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
            if len(occurrences) > maximum:
                raise H4ValidationError("resource_bound_breach", "occurrence identity")
            locator = candidate["model"]["canonical_record_locator"]
            bindings.append({"operation_id": operation, "canonical_record_locator": locator,
                             "occurrences": occurrences})
            operation_rows[number][operation] = (locator, raw, occurrences)
        occurrence_groups.append({"replica": number, "operation_bindings": bindings})
    if total_occurrences > plan["bounds"]["max_h4_occurrence_identities"]:
        raise H4ValidationError("resource_bound_breach", "occurrence 3701")
    occurrence = {"protocol_version": bundle.get("protocol_version", plan.get("protocol_version")),
                  "document_type": "dao_a4_h4_occurrence_evidence",
                  "experiment_id": plan.get("experiment_id"), "plan_sha256": bundle.get("plan_sha256"),
                  "revision_plan_sha256": bundle.get("revision_plan_sha256"),
                  "campaign_id": bundle.get("campaign_id"), "root_candidate_id": root["canonical_candidate_id"],
                  "replica_groups": occurrence_groups}
    occurrence_bytes = _canonical(occurrence)
    if len(occurrence_bytes) > plan["bounds"]["max_h4_occurrence_evidence_bytes"]:
        raise H4ValidationError("resource_bound_breach", "occurrence evidence bytes")
    occurrence_hash = hashlib.sha256(occurrence_bytes).hexdigest()
    structural = {number: _structural_candidates(number, operation_rows[number], occurrence_hash,
                                                  grammar, plan, work) for number in (1, 2)}
    structural_failure = first_cardinality_failure({number: [len(structural[number])]
                                                     for number in (1, 2)})
    if structural_failure is not None:
        number, count, kind = structural_failure
        terminal = predicate_ids[5 + kind]
        candidates = [item[0] for item in structural[number]]
        return _finish_h4(root_result, _slot(candidates, terminal, count, "candidate_set",
                          "h4_structural_field"), _slot([], "not_applicable"), terminal,
                          count, predicate_ids, qualified, work, occurrence, plan)
    merged_structural = _merge(structural[1][0][0], structural[2][0][0]) if (
        structural[1][0][0]["model"] == structural[2][0][0]["model"]
    ) else None
    structural_result = _slot([merged_structural] if merged_structural else
                              [structural[1][0][0], structural[2][0][0]])
    finals: dict[int, list[dict[str, object]]] = {1: [], 2: []}
    classes = grammar.get("name_length_equivalence_classes", [])
    for number in (1, 2):
        candidate, internal = structural[number][0]
        for equivalence in classes:
            selected = []
            for operation in operations:
                work["encoding_length_equivalence_candidates"] += 1
                matches = []
                for index in sorted(internal["compatible"][operation]):
                    occurrence_row = operation_rows[number][operation][2][index]
                    pattern_id = occurrence_row["matched_registered_pattern_id"]
                    length = internal["decoded"][operation][index][2]
                    raw_length = len(bytes.fromhex(occurrence_row["matched_bytes_hex"]))
                    scalar_length = len(bytes.fromhex(occurrence_row["matched_bytes_hex"]).decode(
                        "utf-8" if "UTF8" in pattern_id else "cp1252"))
                    registered = _operation_patterns(plan, number, operation)
                    matched = bytes.fromhex(occurrence_row["matched_bytes_hex"])
                    class_id = equivalence["id"]
                    fits = (class_id == "cp1252_single_byte_per_scalar" and matched == registered[0][1] and
                            length == raw_length == scalar_length) or (
                            class_id == "utf8_encoded_byte_count" and matched == registered[-1][1] and
                            length == raw_length) or (
                            class_id == "utf8_unicode_scalar_or_code_unit_count" and matched == registered[-1][1] and
                            length == scalar_length)
                    if fits:
                        matches.append(index)
                if not matches:
                    break
                selected.append({"operation_id": operation, "occurrence_index": matches[0]})
            if len(selected) == len(operations):
                model = {"structural_model_id": candidate["canonical_model_id"],
                         "encoding_length_equivalence_class": equivalence["id"]}
                binding = {"replica": number,
                           "structural_candidate_id": (merged_structural or candidate)["canonical_candidate_id"],
                           "selected_operation_occurrences": selected}
                finals[number].append(_candidate("h4_final_encoded_field", model, [binding]))
    for number in (1, 2):
        if len(finals[number]) != 1:
            terminal = predicate_ids[7]
            return _finish_h4(root_result, structural_result,
                              _slot(finals[number], terminal, len(finals[number]), "candidate_set",
                                    "h4_final_encoded_field"), terminal, len(finals[number]),
                              predicate_ids, qualified, work, occurrence, plan)
    if merged_structural is None or finals[1][0]["model"] != finals[2][0]["model"]:
        terminal = predicate_ids[8]
        structural_evidence = {"kind": "replica_pair", "entries": [
            {"replica": number, "canonical_model_id": structural[number][0][0]["canonical_model_id"],
             "canonical_candidate_id": structural[number][0][0]["canonical_candidate_id"],
             "complete_candidate": structural[number][0][0]} for number in (1, 2)]}
        final_evidence = {"kind": "replica_pair", "entries": [
            {"replica": number, "canonical_model_id": finals[number][0]["canonical_model_id"],
             "canonical_candidate_id": finals[number][0]["canonical_candidate_id"],
             "complete_candidate": finals[number][0]} for number in (1, 2)]}
        structural_result = _slot([], terminal, 2, "replica_pair", "h4_structural_field",
                                  structural_evidence)
        encoding_result = _slot([], terminal, 2, "replica_pair", "h4_final_encoded_field",
                                final_evidence)
        return _finish_h4(root_result, structural_result, encoding_result, terminal, 2,
                          predicate_ids, qualified, work, occurrence, plan)
    final = _merge(finals[1][0], finals[2][0])
    return _finish_h4(root_result, structural_result, _slot([final]), None, 1,
                      predicate_ids, qualified, work, occurrence, plan)
def _finish_h4(
    root: dict[str, object], structural: dict[str, object], encoding: dict[str, object],
    terminal: str | None, measured: int, predicates: list[str],
    qualified: set[tuple[int, str, int]], work: dict[str, int], occurrence: object,
    plan: Mapping[str, Any],
) -> dict[str, object]:
    terminal_index = len(predicates) if terminal is None else predicates.index(terminal)
    counts = [1] * len(predicates)
    if terminal is not None:
        counts[terminal_index] = measured
        if terminal_index in (1, 6):
            counts[terminal_index - 1] = measured
        counts[terminal_index + 1 :] = [0] * (len(predicates) - terminal_index - 1)
    rows = [_predicate_row(plan, predicate, "fail" if index == terminal_index else
                           "pass" if index < terminal_index else "not_applicable", counts[index])
            for index, predicate in enumerate(predicates)]
    return {"result": {"root_result": root, "structural_result": structural,
                       "encoding_result": encoding}, "predicates": rows,
            "qualified_pages": [{"replica": replica, "checkpoint_id": checkpoint,
                                 "page_number": page} for replica, checkpoint, page in sorted(qualified)],
            "work_charges": work, "occurrence_evidence": occurrence,
            "occurrence_evidence_bytes": None if occurrence is None else _canonical(occurrence)}
def _holdout_root_context(
    replica3: Any, h1_result: Mapping[str, Any], h2_result: Mapping[str, Any],
    h3_result: Mapping[str, Any], h4_result: Mapping[str, Any], plan: Mapping[str, Any],
) -> tuple[int, dict[str, set[int]], dict[str, int]]:
    checkpoints = plan["checkpoint_design"]["checkpoint_ids"]
    if list(getattr(replica3, "checkpoint_ids", checkpoints)) != checkpoints:
        raise H4ValidationError("checkpoint_order_mismatch")
    h1, h2, h3 = (_single_model(value) for value in (h1_result, h2_result, h3_result))
    frozen = h4_result.get("result", h4_result)
    if "root_result" in frozen:
        frozen = frozen["root_result"]
    root = _single_candidate(frozen)
    root_model = _model(root)
    if root_model.get("locator_offsets") != h1.get("locator_offsets"):
        raise H4ValidationError("holdout_root_model_mismatch")
    work: dict[str, int] = {}
    qualified: set[tuple[int, str, int]] = set()
    found, attempted = [], 0
    required = {"T1_CREATE_ID", "T2_CREATE", "T2_RECREATE", "T3_CREATE", "T4_CREATE"}
    for page_number in range(_page_count(replica3, "EMPTY")):
        page = _page(replica3, "EMPTY", page_number)
        if page is None or page[0] != 2:
            continue
        if attempted >= plan["bounds"]["max_qualified_pages_per_submodel"]:
            raise H4ValidationError("resource_bound_breach", "catalog root pages")
        attempted += 1
        streams: dict[str, set[int]] = {}
        signatures: dict[str, tuple[tuple[int, str | None], ...]] = {}
        try:
            for checkpoint in checkpoints:
                _targets, streams[checkpoint] = _traverse(
                    replica3, 3, checkpoint, page_number, h1, h2, h3, qualified, work
                )
                signatures[checkpoint] = tuple((page, _state_digest(replica3, checkpoint, page))
                                               for page in sorted(streams[checkpoint]))
        except (H3ValidationError, H4ValidationError) as exc:
            if is_resource_error(exc):
                raise
            continue
        if all(signatures[operation] != signatures[checkpoints[checkpoints.index(operation) - 1]]
               for operation in required):
            found.append((page_number, streams))
    if len(found) != 1:
        raise H4ValidationError("holdout_root_cardinality")
    return found[0][0], found[0][1], work

def predict_h4_root_holdout(
    replica3: Any, h1_result: Mapping[str, Any], h2_result: Mapping[str, Any],
    h3_result: Mapping[str, Any], h4_result: Mapping[str, Any], plan: Mapping[str, Any],
) -> bool:
    try:
        _holdout_root_context(replica3, h1_result, h2_result, h3_result, h4_result, plan)
        return True
    except (H3ValidationError, H4ValidationError) as exc:
        if is_resource_error(exc):
            raise
        return False
    except (KeyError, TypeError, ValueError, IndexError):
        return False
def predict_h4_fields_holdout(
    replica3: Any, h1_result: Mapping[str, Any], h2_result: Mapping[str, Any],
    h3_result: Mapping[str, Any], h4_result: Mapping[str, Any], plan: Mapping[str, Any],
) -> bool:
    try:
        _root_page, streams, work = _holdout_root_context(
            replica3, h1_result, h2_result, h3_result, h4_result, plan
        )
        checkpoints = plan["checkpoint_design"]["checkpoint_ids"]
        grammar = plan["candidate_grammars"]["h4"]
        operations = grammar["operation_binding_order"]
        h1, h2, h3 = (_single_model(value) for value in (h1_result, h2_result, h3_result))
        h1_candidate = _single_candidate(h1_result)
        frozen = h4_result.get("result", h4_result)
        structural = _single_candidate(frozen["structural_result"])
        final = _single_candidate(frozen["encoding_result"])
        structural_model, final_model = _model(structural), _model(final)
        if final_model.get("structural_model_id") != structural.get("canonical_model_id"):
            return False
        records: dict[str, bytes] = {}
        for operation in operations:
            before = checkpoints[checkpoints.index(operation) - 1]
            excluded = {0, 1}
            for binding in _bindings(h1_candidate, 3):
                if operation not in _range(binding, checkpoints):
                    continue
                excluded.add(binding["tdef_page"])
                excluded.update(_target(binding, ordinal)[0] for ordinal in (0, 1))
                _targets, user_pages = _traverse(
                    replica3, 3, operation, binding["tdef_page"], h1, h2, h3, set(), work
                )
                excluded.update(user_pages)
            changed = _changed(replica3, before, operation, plan["bounds"]["max_final_pages_per_replica"])
            candidates = [raw for _page_number, _row_number, _start, _end, raw in _retained_rows(
                replica3, operation, (changed - excluded) & streams[operation], checkpoints,
                h2["row_mask"], set(), 3, None, set()
            )]
            if len(candidates) != 1:
                return False
            records[operation] = candidates[0]
        choices: dict[str, dict[int, list[int]]] = {}
        for operation in operations:
            choices[operation] = {}
            for occurrence in _occurrences(records[operation], _operation_patterns(plan, 3, operation)):
                name_start = occurrence["name_start"]
                kind_start = name_start - structural_model["kind_start_delta"]
                identifier_start = kind_start - structural_model["identifier_width"]
                length_start = name_start - structural_model["name_length_start_delta"]
                spans = [(identifier_start, kind_start), (kind_start, kind_start + structural_model[
                    "kind_width"]), (length_start, length_start + structural_model["name_length_width"])]
                if any(start < 0 or end > name_start or start >= end for start, end in spans):
                    continue
                if any(max(a, c) < min(b, d) for index, (a, b) in enumerate(spans)
                       for c, d in spans[index + 1 :]):
                    continue
                row, endian = records[operation], structural_model["endianness"]
                kind = int.from_bytes(row[kind_start : kind_start + structural_model["kind_width"]], endian)
                expected_kind = "field" if operation == "T1_ADD_TEXT" else (
                    "index" if operation == "T1_ADD_INDEX" else "table")
                if kind != structural_model["kind_mapping"][expected_kind]:
                    continue
                identifier = int.from_bytes(row[identifier_start:kind_start], endian)
                length = int.from_bytes(row[length_start : length_start + structural_model[
                    "name_length_width"]], endian)
                raw = bytes.fromhex(occurrence["matched_bytes_hex"])
                pattern_id = occurrence["matched_registered_pattern_id"]
                scalar = len(raw.decode("utf-8" if "UTF8" in pattern_id else "cp1252"))
                class_id = final_model["encoding_length_equivalence_class"]
                registered = _operation_patterns(plan, 3, operation)
                fits = (class_id == "cp1252_single_byte_per_scalar" and raw == registered[0][1]
                        and length == len(raw) == scalar) or (class_id == "utf8_encoded_byte_count"
                        and raw == registered[-1][1] and length == len(raw)) or (
                        class_id == "utf8_unicode_scalar_or_code_unit_count" and raw == registered[-1][1]
                        and length == scalar)
                if fits:
                    choices[operation].setdefault(identifier, []).append(occurrence["occurrence_index"])
            if not choices[operation]:
                return False
        relation = structural_model["identifier_lifecycle"]
        t2_left, t2_right = choices["T2_CREATE"], choices["T2_RECREATE"]
        if relation == "stable_for_same_physical_name_including_t2_v1_v2":
            seeds = [(value, {value}) for value in set(t2_left) & set(t2_right)]
        else:
            seeds = [(None, {left, right}) for left in t2_left for right in t2_right if left != right]
        remaining = [operation for operation in operations if operation not in ("T2_CREATE", "T2_RECREATE")]
        def match(index: int, used: set[int]) -> bool:
            if index == len(remaining):
                return True
            return any(value not in used and match(index + 1, used | {value})
                       for value in choices[remaining[index]])
        return any(match(0, used) for _value, used in seeds)
    except (H3ValidationError, H4ValidationError) as exc:
        if is_resource_error(exc):
            raise
        return False
    except (KeyError, TypeError, ValueError, IndexError):
        return False
