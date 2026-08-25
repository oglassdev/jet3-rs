#!/usr/bin/env python3
"""Fresh plan-derived A4 H1 recomputation over captured page bytes."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from protocol_validation import ValidationError as ProtocolValidationError


class ValidationError(ProtocolValidationError):
    """A stable independent-validator rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _schedule(plan: Mapping[str, Any]) -> tuple[
    tuple[tuple[str, str, str, str, str], ...],
    tuple[tuple[str, str, str], ...],
]:
    checkpoints = tuple(plan["checkpoint_design"]["checkpoint_ids"])
    expected = plan["tables"]["expected_schema_by_checkpoint"]
    roles = tuple(plan["candidate_grammars"]["h1"]["logical_roles"])
    if len(checkpoints) != 25 or len(roles) != 4:
        raise ValidationError("independent_h1_schedule_contract")

    def tokens(checkpoint: str) -> dict[str, str]:
        output: dict[str, str] = {}
        rows = expected.get(checkpoint)
        if not isinstance(rows, list) or len(rows) > len(roles):
            raise ValidationError("independent_h1_schema_schedule")
        for value in rows:
            if not isinstance(value, str) or ":" not in value:
                raise ValidationError("independent_h1_schema_schedule")
            role = value.split(":", 1)[0]
            if role not in roles or role in output:
                raise ValidationError("independent_h1_schema_schedule")
            output[role] = value
        return output

    states = {checkpoint: tokens(checkpoint) for checkpoint in checkpoints}
    instances: list[tuple[str, str, str, str, str]] = []
    for role in roles:
        index = 0
        while index < len(checkpoints):
            if role not in states[checkpoints[index]]:
                index += 1
                continue
            start = index
            token = states[checkpoints[index]][role]
            version = "v2" if ":v2:" in token else "v1"
            while index + 1 < len(checkpoints):
                following = states[checkpoints[index + 1]].get(role)
                following_version = (
                    "v2" if following is not None and ":v2:" in following else "v1"
                )
                if following is None or following_version != version:
                    break
                index += 1
            before = checkpoints[start - 1] if start else ""
            if not before:
                raise ValidationError("independent_h1_schema_schedule")
            instances.append((role, f"{role}-{version}", before, checkpoints[start], checkpoints[index]))
            index += 1
    order = {role: position for position, role in enumerate(roles)}
    instances.sort(key=lambda row: (order[row[0]], checkpoints.index(row[3])))
    schema_legs: list[tuple[str, str, str]] = []
    for left, right in zip(checkpoints, checkpoints[1:]):
        changed = [role for role in roles if states[left].get(role) != states[right].get(role)]
        if len(changed) > 1:
            raise ValidationError("independent_h1_schema_schedule")
        if changed:
            schema_legs.append((changed[0], left, right))
    if len(instances) != 5:
        raise ValidationError("independent_h1_schema_schedule")
    return tuple(instances), tuple(schema_legs)


@dataclass(frozen=True)
class H1Recomputation:
    layer: Mapping[str, Any]
    predicate_results: tuple[Mapping[str, Any], ...]
    qualified_pages: tuple[Mapping[str, Any], ...]
    work_charges: Mapping[str, int]
    per_replica_candidates: Mapping[int, tuple[Mapping[str, Any], ...]]


class _ReplicaState:
    def __init__(self, replica: object, number: int, checkpoints: Sequence[str], page_size: int):
        self.replica = replica
        self.number = number
        self.checkpoints = tuple(checkpoints)
        self.page_size = page_size
        self.pages: dict[tuple[str, int], bytes | None] = {}
        self.qualified: set[tuple[int, str, int]] = set()
        self.work = {
            "tdef_lifecycle_signatures": 0,
            "raw_locator_windows": 0,
            "raw_locator_pairs": 0,
            "h1_target_validity_checks": 0,
        }
        self.target_charges: set[tuple[str, str, int, int]] = set()
        self.locator_charged: set[int] = set()
        self.tdef_pages: tuple[int, ...] = ()
        observed = tuple(getattr(replica, "checkpoint_ids", checkpoints))
        if observed != tuple(checkpoints):
            raise ValidationError("independent_h1_checkpoint_order")

    def state(self, checkpoint: str, page: int) -> str | None:
        try:
            value = self.replica.state(checkpoint, page)
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
            raise ValidationError("independent_h1_state_read") from exc
        if value is not None and (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValidationError("independent_h1_state_invalid")
        return value

    def read(self, checkpoint: str, page: int, *, qualify: bool = False) -> bytes | None:
        key = (checkpoint, page)
        if key not in self.pages:
            try:
                value = self.replica.page(checkpoint, page)
            except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
                raise ValidationError("independent_h1_page_read") from exc
            if value is not None and (
                not isinstance(value, bytes) or len(value) != self.page_size
            ):
                raise ValidationError("independent_h1_page_size")
            self.pages[key] = value
        value = self.pages[key]
        if qualify:
            self.qualified.add((self.number, checkpoint, page))
        return value

    def page_count(self, checkpoint: str, limit: int) -> int:
        try:
            index = self.replica.index(checkpoint)
            value = index["page_count"]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValidationError("independent_h1_page_count") from exc
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise ValidationError("independent_h1_page_count")
        return value


def _sha(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("independent_h1_canonicalization") from exc
    return hashlib.sha256(payload).hexdigest()


def _candidate(model_type: str, model: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = [dict(value) for value in bindings]
    core = {"model_type": model_type, "model": dict(model)}
    return {
        "model_type": model_type,
        "canonical_model_id": _sha(core),
        "canonical_candidate_id": _sha({**core, "instance_bindings": ordered}),
        "model": dict(model),
        "instance_bindings": ordered,
    }


def _contracts(values: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]], predicate_ids: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    if isinstance(values, Mapping):
        rows = dict(values)
    else:
        if len(values) > 64:
            raise ValidationError("independent_h1_contract_bound")
        rows = {str(row.get("predicate_id")): row for row in values}
    if len(predicate_ids) != 8 or any(predicate not in rows for predicate in predicate_ids):
        raise ValidationError("independent_h1_contract_missing")
    return rows


def _replicas(values: Mapping[int, object] | Sequence[object], numbers: Sequence[int]) -> dict[int, object]:
    if isinstance(values, Mapping):
        output = {number: values[number] for number in numbers if number in values}
    else:
        if len(values) > 3:
            raise ValidationError("independent_h1_replica_bound")
        output = {int(getattr(value, "number")): value for value in values}
    if tuple(sorted(output)) != tuple(numbers):
        raise ValidationError("independent_h1_replicas")
    return output


def _tag(page: bytes | None) -> int | None:
    return None if page is None else page[0]


def _qualified_pool(state: _ReplicaState, max_pages: int) -> tuple[int, ...]:
    pages: set[int] = set()
    for checkpoint in state.checkpoints:
        for page in range(state.page_count(checkpoint, max_pages)):
            if _tag(state.read(checkpoint, page)) == 2:
                pages.add(page)
                if len(pages) > 16:
                    raise ValidationError("A4-RESOURCE-BOUND")
    return tuple(sorted(pages))


def _applicable(checkpoints: Sequence[str], start: str, end: str) -> tuple[str, ...]:
    try:
        left, right = checkpoints.index(start), checkpoints.index(end)
    except ValueError as exc:
        raise ValidationError("independent_h1_lifecycle_checkpoint") from exc
    if left > right:
        raise ValidationError("independent_h1_lifecycle_range")
    return tuple(checkpoints[left : right + 1])


def _signature_fits(
    state: _ReplicaState,
    page: int,
    signature: str,
    role: str,
    instance: str,
    before: str,
    after: str,
    end: str,
    idle_pairs: Sequence[tuple[str, str]],
    schema_legs: Sequence[tuple[str, str, str]],
) -> bool:
    applicable = _applicable(state.checkpoints, after, end)
    blobs = [state.read(cp, page, qualify=True) for cp in applicable]
    if any(_tag(blob) != 2 for blob in blobs):
        return False
    prior = state.read(before, page, qualify=True)
    first = state.read(after, page, qualify=True)
    if signature == "new_tag_02_at_role_create":
        if _tag(prior) == 2 or _tag(first) != 2:
            return False
        return instance != "T2-v1" or _tag(state.read("T2_DROP", page, qualify=True)) != 2
    if signature != "preexisting_tag_02_hash_transition":
        raise ValidationError("independent_h1_signature")
    if _tag(prior) != 2 or _tag(first) != 2 or prior == first:
        return False
    for changed_role, left, right in schema_legs:
        if changed_role != role and left in applicable and right in applicable:
            left_blob = state.read(left, page, qualify=True)
            right_blob = state.read(right, page, qualify=True)
            if left_blob != right_blob:
                return False
    for left, right in idle_pairs:
        if left not in applicable or right not in applicable:
            continue
        left_blob = state.read(left, page, qualify=True)
        right_blob = state.read(right, page, qualify=True)
        if left_blob != right_blob:
            return False
    if instance == "T2-v1":
        if state.state("T2_CREATE", page) == state.state("T2_DROP", page):
            return False
    if instance == "T2-v2":
        if state.state("T2_DROP", page) == state.state("T2_RECREATE", page):
            return False
    return True


def _tdef_stage(
    state: _ReplicaState,
    plan: Mapping[str, Any],
    max_pages: int,
    max_candidates: int,
    instances: Sequence[tuple[str, str, str, str, str]],
    schema_legs: Sequence[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    grammar = plan["candidate_grammars"]["h1"]
    signatures = tuple(grammar["tdef_lifecycle_signatures"])
    if signatures != ("new_tag_02_at_role_create", "preexisting_tag_02_hash_transition"):
        raise ValidationError("independent_h1_signature_contract")
    pool = _qualified_pool(state, max_pages)
    state.tdef_pages = pool
    for page in pool:
        for checkpoint in state.checkpoints:
            state.read(checkpoint, page, qualify=True)
    state.work["tdef_lifecycle_signatures"] += len(pool) * len(signatures) * len(state.checkpoints)
    idle_pairs = tuple(tuple(value) for value in plan["checkpoint_design"]["idle_pairs"])
    output: list[dict[str, Any]] = []
    for signature in signatures:
        choices: list[list[dict[str, Any]]] = []
        for role, instance, before, after, end in instances:
            rows = []
            for page in pool:
                if _signature_fits(
                    state, page, signature, role, instance, before, after, end,
                    idle_pairs, schema_legs,
                ):
                    rows.append({
                        "replica": state.number,
                        "logical_role": role,
                        "lifecycle_instance": instance,
                        "tdef_page": page,
                        "applicable_checkpoint_range": {"start": after, "end": end},
                    })
            choices.append(rows)
        if any(not rows for rows in choices):
            continue
        count = 1
        for rows in choices:
            count *= len(rows)
            if count > max_candidates:
                raise ValidationError("A4-RESOURCE-BOUND")
        for bindings in itertools.product(*choices):
            output.append(_candidate("h1_tdef", {"tdef_lifecycle_signature": signature}, bindings))
            if len(output) > max_candidates:
                raise ValidationError("A4-RESOURCE-BOUND")
    return sorted(output, key=lambda row: row["canonical_candidate_id"])


def _decode_window(blob: bytes, offset: int, layout: str) -> tuple[int, int]:
    raw = blob[offset : offset + 4]
    if len(raw) != 4:
        raise ValidationError("independent_h1_window_bounds")
    if layout == "u24le_page_then_u8_row":
        return int.from_bytes(raw[:3], "little"), raw[3]
    if layout == "u8_row_then_u24le_page":
        return int.from_bytes(raw[1:], "little"), raw[0]
    raise ValidationError("independent_h1_layout")


def _window_stage(state: _ReplicaState, tdef: Mapping[str, Any], plan: Mapping[str, Any], page_limit: int) -> dict[str, dict[int, tuple[int, ...]]]:
    layouts = tuple(plan["candidate_grammars"]["h1"]["locator_layouts"])
    pages = sorted({binding["tdef_page"] for binding in tdef["instance_bindings"]})
    output: dict[str, dict[int, tuple[int, ...]]] = {layout: {} for layout in layouts}
    for page in state.tdef_pages:
        if page not in state.locator_charged:
            state.locator_charged.add(page)
            state.work["raw_locator_windows"] += 4090
            state.work["raw_locator_pairs"] += 4_167_722
    for page in pages:
        blobs = [state.read(cp, page, qualify=True) for cp in state.checkpoints]
        for layout in layouts:
            preserved: list[int] = []
            for offset in range(2045):
                if all(blob is not None and _decode_window(blob, offset, layout)[0] < page_limit for blob in blobs):
                    preserved.append(offset)
            output[layout][page] = tuple(preserved)
    return output


def _mask_matches(blob: bytes, signature: Mapping[str, Any]) -> bool:
    start, end = signature["record_bounds"]
    value = bytes.fromhex(signature["value_hex"])
    mask = bytearray.fromhex(signature["mask_hex"])
    if len(value) != end - start or len(mask) != end - start or end > len(blob):
        raise ValidationError("independent_h1_signature_bounds")
    if "mask_derivation" in signature:
        for override in signature["mask_derivation"]["overrides"]:
            left, right = override["interval"]
            mask[left - start : right - start] = bytes.fromhex(override["mask_hex"])
    record = blob[start:end]
    if any((actual & bitmask) != (expected & bitmask) for actual, expected, bitmask in zip(record, value, mask)):
        return False
    for relation in signature.get("equal_byte_intervals", ()):
        left, right = relation["left"], relation["right"]
        if blob[left[0] : left[1]] != blob[right[0] : right[1]]:
            return False
    inequality = signature.get("mutual_exclusion_inequality")
    if inequality is not None:
        left = inequality["left"]
        if blob[left[0] : left[1]] == bytes.fromhex(inequality["right"]["fixed_value_hex"]):
            return False
    return True


def _structural_stage(state: _ReplicaState, tdef: Mapping[str, Any], windows: Mapping[str, Mapping[int, Sequence[int]]], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    grammar = plan["candidate_grammars"]["h1"]
    specs = (grammar["table_record_signature"], grammar["pair_multiple_reachability_signature"])
    base_spec = grammar["table_record_signature"]
    pages = sorted({binding["tdef_page"] for binding in tdef["instance_bindings"]})
    output: list[dict[str, Any]] = []
    for layout in grammar["locator_layouts"]:
        common = set(range(2045))
        for page in pages:
            common.intersection_update(windows[layout][page])
        for spec in specs:
            effective_spec = dict(spec)
            effective_spec.setdefault("value_hex", base_spec["value_hex"])
            effective_spec.setdefault("mask_hex", base_spec["mask_hex"])
            holes = tuple(tuple(value) for value in spec["locator_holes"])
            pairs = ((holes[0][0], holes[1][0]),) if len(holes) == 2 else tuple(
                (left[0], right[0]) for left, right in itertools.combinations(holes, 2)
            )
            for offsets in pairs:
                if offsets[0] not in common or offsets[1] not in common or offsets[1] - offsets[0] < 4:
                    continue
                bindings: list[dict[str, Any]] = []
                fits = True
                for binding in tdef["instance_bindings"]:
                    checkpoints = _applicable(state.checkpoints, binding["applicable_checkpoint_range"]["start"], binding["applicable_checkpoint_range"]["end"])
                    observed: tuple[tuple[int, int], tuple[int, int]] | None = None
                    for checkpoint in checkpoints:
                        blob = state.read(checkpoint, binding["tdef_page"], qualify=True)
                        if blob is None or not _mask_matches(blob, effective_spec):
                            fits = False
                            break
                        targets = (_decode_window(blob, offsets[0], layout), _decode_window(blob, offsets[1], layout))
                        if observed is None:
                            observed = targets
                        elif observed != targets:
                            fits = False
                            break
                    if not fits or observed is None:
                        break
                    bindings.append({**binding, "locator_targets": [
                        {"page": observed[0][0], "row": observed[0][1]},
                        {"page": observed[1][0], "row": observed[1][1]},
                    ]})
                if fits:
                    output.append(_candidate("h1_locator_pair", {
                        "layout": layout,
                        "table_signature_id": spec["signature_id"],
                        "locator_offsets": list(offsets),
                    }, bindings))
    return sorted(output, key=lambda row: row["canonical_candidate_id"])


def _target_stage(state: _ReplicaState, candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        layout = candidate["model"]["layout"]
        valid = True
        for binding in candidate["instance_bindings"]:
            targets = tuple((row["page"], row["row"]) for row in binding["locator_targets"])
            if targets[0] == targets[1]:
                valid = False
                break
            checkpoints = _applicable(state.checkpoints, binding["applicable_checkpoint_range"]["start"], binding["applicable_checkpoint_range"]["end"])
            for checkpoint in checkpoints:
                for page, row in targets:
                    charge = (checkpoint, layout, page, row)
                    if charge not in state.target_charges:
                        state.target_charges.add(charge)
                        state.work["h1_target_validity_checks"] += 1
                    blob = state.read(checkpoint, page, qualify=True)
                    if blob is None or blob[0] != 1 or row >= int.from_bytes(blob[8:10], "little"):
                        valid = False
                        break
                if not valid:
                    break
            if not valid:
                break
        if valid:
            output.append(dict(candidate))
    return output


def _layout_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], Mapping[str, Any]] = {}
    for candidate in candidates:
        key = (candidate["model"]["layout"], candidate["model"]["table_signature_id"])
        grouped.setdefault(key, candidate)
    output = []
    for (layout, signature), source in grouped.items():
        output.append(_candidate("h1_target_valid_layout", {
            "layout": layout, "table_signature_id": signature,
        }, source["instance_bindings"]))
    return sorted(output, key=lambda row: row["canonical_candidate_id"])


def _predicate_row(contract: Mapping[str, Any], status: str, measured: int, survivor: int, terminal: str | None) -> dict[str, Any]:
    return {
        "predicate_id": contract["predicate_id"],
        "order": contract["order"],
        "scope": contract["scope"],
        "status": status,
        "terminal_predicate_id": terminal,
        "predicate_measured_survivor_count": measured,
        "derivation_survivor_count": survivor,
        "reachability_fixture_id": contract["reachability_fixture_id"],
    }


def _layer(status: str, candidates: Sequence[Mapping[str, Any]], measured: int, terminal: str | None, kind: str | None, stage: str | None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = sorted((dict(value) for value in candidates), key=lambda row: row["canonical_candidate_id"])
    return {
        "status": status,
        "predicate_measured_survivor_count": measured,
        "derivation_survivor_count": 1 if status == "model" else 0,
        "terminal_predicate_id": terminal,
        "terminal_payload_kind": kind,
        "terminal_candidate_stage": stage,
        "candidates": rows,
        "terminal_evidence": None if evidence is None else dict(evidence),
        "canonical_candidates_sha256": _sha(rows),
    }


def recompute_h1(
    replicas: Mapping[int, object] | Sequence[object],
    *,
    plan: Mapping[str, Any],
    predicate_contracts: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    derivation_replica_numbers: Sequence[int] = (1, 2),
) -> H1Recomputation:
    """Recompute H1 with registered predicate-major cutoff and work accounting."""
    try:
        predicate_ids = tuple(
            plan["predicate_registry"]["per_layer_ordered_predicates"][
                "h1_tdef_to_map_row"
            ]
        )
    except (KeyError, TypeError) as exc:
        raise ValidationError("independent_h1_predicate_contract") from exc
    contracts = _contracts(predicate_contracts, predicate_ids)
    numbers = tuple(derivation_replica_numbers)
    if numbers != (1, 2):
        raise ValidationError("independent_h1_derivation_replicas")
    source = _replicas(replicas, numbers)
    checkpoints = tuple(plan["checkpoint_design"]["checkpoint_ids"])
    bounds = plan["bounds"]
    if (
        len(checkpoints) != 25
        or bounds["page_size"] != 2048
        or bounds["max_final_pages_per_replica"] != 20480
        or bounds["max_candidate_models"] != 4096
    ):
        raise ValidationError("independent_h1_plan_bounds")
    page_limit = int(bounds["max_final_pages_per_replica"])
    max_candidates = int(bounds["max_candidate_models"])
    instances, schema_legs = _schedule(plan)
    states = {number: _ReplicaState(source[number], number, checkpoints, 2048) for number in numbers}
    tdefs: dict[int, list[dict[str, Any]]] = {}
    predicates: list[dict[str, Any]] = []

    for number in numbers:
        tdefs[number] = _tdef_stage(
            states[number], plan, page_limit, max_candidates, instances, schema_legs
        )
        if not tdefs[number]:
            predicate = predicate_ids[0]
            predicates.append(_predicate_row(contracts[predicate], "fail", 0, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 0, predicate, "candidate_set", "h1_tdef"), predicates, tdefs)
    predicates.append(_predicate_row(contracts[predicate_ids[0]], "pass", min(len(v) for v in tdefs.values()), 1, None))
    for number in numbers:
        if len(tdefs[number]) > 1:
            predicate = predicate_ids[1]
            predicates.append(_predicate_row(contracts[predicate], "fail", len(tdefs[number]), 0, predicate))
            return _finish(states, _layer("no_outcome", tdefs[number], len(tdefs[number]), predicate, "candidate_set", "h1_tdef"), predicates, tdefs)
    predicates.append(_predicate_row(contracts[predicate_ids[1]], "pass", 1, 1, None))

    windows: dict[int, dict[str, dict[int, tuple[int, ...]]]] = {}
    for number in numbers:
        windows[number] = _window_stage(states[number], tdefs[number][0], plan, page_limit)
        layouts = sum(
            any(values for values in windows[number][layout].values())
            for layout in windows[number]
        )
        if layouts == 0:
            predicate = predicate_ids[2]
            predicates.append(_predicate_row(contracts[predicate], "fail", 0, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 0, predicate, "candidate_set", "h1_target_valid_layout"), predicates, tdefs)
    predicates.append(_predicate_row(
        contracts[predicate_ids[2]],
        "pass",
        min(
            sum(
                any(values for values in windows[number][layout].values())
                for layout in windows[number]
            )
            for number in numbers
        ),
        1,
        None,
    ))

    structural: dict[int, list[dict[str, Any]]] = {}
    for number in numbers:
        structural[number] = _structural_stage(states[number], tdefs[number][0], windows[number], plan)
        if not structural[number]:
            predicate = predicate_ids[3]
            predicates.append(_predicate_row(contracts[predicate], "fail", 0, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 0, predicate, "candidate_set", "h1_locator_pair"), predicates, structural)
    predicates.append(_predicate_row(contracts[predicate_ids[3]], "pass", min(len(v) for v in structural.values()), 1, None))

    targets: dict[int, list[dict[str, Any]]] = {}
    for number in numbers:
        targets[number] = _target_stage(states[number], structural[number])
        if not targets[number]:
            predicate = predicate_ids[4]
            predicates.append(_predicate_row(contracts[predicate], "fail", 0, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 0, predicate, "candidate_set", "h1_locator_pair"), predicates, targets)
    predicates.append(_predicate_row(contracts[predicate_ids[4]], "pass", min(len(v) for v in targets.values()), 1, None))

    layouts = {number: _layout_candidates(targets[number]) for number in numbers}
    for number in numbers:
        unique_layouts = {row["model"]["layout"] for row in layouts[number]}
        if len(unique_layouts) > 1:
            predicate = predicate_ids[5]
            candidates = layouts[number]
            predicates.append(_predicate_row(contracts[predicate], "fail", len(unique_layouts), 0, predicate))
            return _finish(states, _layer("no_outcome", candidates, len(unique_layouts), predicate, "candidate_set", "h1_target_valid_layout"), predicates, layouts)
    predicates.append(_predicate_row(contracts[predicate_ids[5]], "pass", 1, 1, None))
    for number in numbers:
        if len(targets[number]) > 1:
            predicate = predicate_ids[6]
            predicates.append(_predicate_row(contracts[predicate], "fail", len(targets[number]), 0, predicate))
            return _finish(states, _layer("no_outcome", targets[number], len(targets[number]), predicate, "candidate_set", "h1_locator_pair"), predicates, targets)
    predicates.append(_predicate_row(contracts[predicate_ids[6]], "pass", 1, 1, None))

    left, right = targets[1][0], targets[2][0]
    if left["canonical_model_id"] != right["canonical_model_id"]:
        predicate = predicate_ids[7]
        evidence = {"kind": "replica_pair", "entries": [
            {"replica": number, "canonical_model_id": targets[number][0]["canonical_model_id"], "canonical_candidate_id": targets[number][0]["canonical_candidate_id"], "complete_candidate": targets[number][0]}
            for number in numbers
        ]}
        predicates.append(_predicate_row(contracts[predicate], "fail", 2, 0, predicate))
        return _finish(states, _layer("no_outcome", [], 2, predicate, "replica_pair", "h1_locator_pair", evidence), predicates, targets)
    predicates.append(_predicate_row(contracts[predicate_ids[7]], "pass", 1, 1, None))
    merged_bindings = [binding for number in numbers for binding in targets[number][0]["instance_bindings"]]
    final = _candidate("h1_locator_pair", left["model"], merged_bindings)
    return _finish(
        states,
        _layer("model", [final], 1, None, None, None),
        predicates,
        targets,
    )


def apply_h1_holdout(
    replica3: object,
    frozen_h1_result: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Apply the frozen H1 model to replica 3 without selecting a new model."""
    layer = frozen_h1_result.get("layer", frozen_h1_result)
    candidates = layer.get("candidates") if isinstance(layer, Mapping) else None
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or candidates[0].get("model_type") != "h1_locator_pair"
    ):
        raise ValidationError("independent_h1_holdout_frozen_model")
    frozen = candidates[0]
    checkpoints = tuple(plan["checkpoint_design"]["checkpoint_ids"])
    bounds = plan["bounds"]
    state = _ReplicaState(replica3, 3, checkpoints, bounds["page_size"])
    instances, schema_legs = _schedule(plan)
    tdefs = _tdef_stage(
        state,
        plan,
        bounds["max_final_pages_per_replica"],
        bounds["max_candidate_models"],
        instances,
        schema_legs,
    )
    matches: list[dict[str, Any]] = []
    for tdef in tdefs:
        windows = _window_stage(
            state, tdef, plan, bounds["max_final_pages_per_replica"]
        )
        structural = _structural_stage(state, tdef, windows, plan)
        for candidate in _target_stage(state, structural):
            if candidate["model"] == frozen["model"]:
                matches.append(candidate)
                if len(matches) > 1:
                    return None
    return matches[0] if len(matches) == 1 else None


def predict_h1_holdout(
    replica3: object,
    frozen_h1_result: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
) -> bool:
    """Return whether replica 3 uniquely satisfies the unchanged H1 model."""
    return apply_h1_holdout(replica3, frozen_h1_result, plan=plan) is not None


def _finish(states: Mapping[int, _ReplicaState], layer: Mapping[str, Any], predicates: Sequence[Mapping[str, Any]], candidates: Mapping[int, Sequence[Mapping[str, Any]]]) -> H1Recomputation:
    qualified = sorted({identity for state in states.values() for identity in state.qualified}, key=lambda row: (row[0], states[row[0]].checkpoints.index(row[1]), row[2]))
    work = {name: sum(state.work[name] for state in states.values()) for name in next(iter(states.values())).work}
    return H1Recomputation(
        dict(layer), tuple(dict(row) for row in predicates),
        tuple({"replica": replica, "checkpoint_id": checkpoint, "page_number": page} for replica, checkpoint, page in qualified),
        work,
        {number: tuple(dict(row) for row in candidates.get(number, ())) for number in states},
    )
