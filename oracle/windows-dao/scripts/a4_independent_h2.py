#!/usr/bin/env python3
"""Fresh plan-derived A4 H2 recomputation over H1-located page rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from protocol_validation import ValidationError as ProtocolValidationError


class ValidationError(ProtocolValidationError):
    """A stable independent-validator rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class H2Recomputation:
    layer: Mapping[str, Any]
    predicate_results: tuple[Mapping[str, Any], ...]
    qualified_pages: tuple[Mapping[str, Any], ...]
    work_charges: Mapping[str, int]
    per_replica_candidates: Mapping[int, tuple[Mapping[str, Any], ...]]


@dataclass(frozen=True)
class _Located:
    replica: int
    role: str
    instance: str
    checkpoint: str
    ordinal: int
    page: int
    slot: int
    raw_entry: int
    rows: Mapping[int, bytes]
    bounds: Mapping[int, tuple[int, int]]


class _State:
    def __init__(self, replica: object, number: int, checkpoints: Sequence[str], page_size: int):
        self.replica = replica
        self.number = number
        self.checkpoints = tuple(checkpoints)
        self.page_size = page_size
        self.cache: dict[tuple[str, int], bytes | None] = {}
        self.qualified: set[tuple[int, str, int]] = set()
        self.work = {
            "valid_path_row_directory_entries": 0,
            "invalid_path_row_directory_entries": 0,
            "type_1_slots": 0,
            "type_0_and_tag_05_bitmap_bits": 0,
            "role_transition_evaluations": 0,
        }
        self.row_work: set[tuple[str, int, int, int]] = set()

    def read(self, checkpoint: str, page: int) -> bytes | None:
        key = (checkpoint, page)
        if key not in self.cache:
            try:
                value = self.replica.page(checkpoint, page)
            except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
                raise ValidationError("independent_h2_page_read") from exc
            if value is not None and (
                not isinstance(value, bytes) or len(value) != self.page_size
            ):
                raise ValidationError("independent_h2_page_size")
            self.cache[key] = value
        value = self.cache[key]
        if value is not None:
            self.qualified.add((self.number, checkpoint, page))
        return value

    def page_count(self, checkpoint: str, limit: int) -> int:
        try:
            index = self.replica.index(checkpoint)
            value = index["page_count"]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValidationError("independent_h2_page_count") from exc
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise ValidationError("independent_h2_page_count")
        return value


def _sha(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("independent_h2_canonicalization") from exc
    return hashlib.sha256(raw).hexdigest()


def _candidate(model: Mapping[str, Any]) -> dict[str, Any]:
    core = {"model_type": "h2_final_role", "model": dict(model)}
    return {"model_type": "h2_final_role", "canonical_candidate_id": _sha(core), "model": dict(model)}


def _contracts(values: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]], predicate_ids: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    if isinstance(values, Mapping):
        rows = dict(values)
    else:
        if len(values) > 64:
            raise ValidationError("independent_h2_contract_bound")
        rows = {str(row.get("predicate_id")): row for row in values}
    if len(predicate_ids) != 7 or any(predicate not in rows for predicate in predicate_ids):
        raise ValidationError("independent_h2_contract_missing")
    return rows


def _sources(values: Mapping[int, object] | Sequence[object]) -> dict[int, object]:
    if isinstance(values, Mapping):
        output = {number: values[number] for number in (1, 2) if number in values}
    else:
        if len(values) > 3:
            raise ValidationError("independent_h2_replica_bound")
        output = {int(getattr(value, "number")): value for value in values}
    if tuple(sorted(output)) != (1, 2):
        raise ValidationError("independent_h2_replicas")
    return output


def _h1_candidate(h1: object) -> Mapping[str, Any]:
    layer = h1.get("layer", h1) if isinstance(h1, Mapping) else getattr(h1, "layer", None)
    if not isinstance(layer, Mapping) or layer.get("status") != "model":
        raise ValidationError("independent_h2_h1_not_model")
    candidates = layer.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValidationError("independent_h2_h1_cardinality")
    candidate = candidates[0]
    if not isinstance(candidate, Mapping) or candidate.get("model_type") != "h1_locator_pair":
        raise ValidationError("independent_h2_h1_candidate")
    return candidate


def _per_replica_h1(
    h1: object, merged: Mapping[str, Any]
) -> dict[int, Mapping[str, Any]]:
    supplied = (
        h1.get("per_replica_candidates")
        if isinstance(h1, Mapping)
        else getattr(h1, "per_replica_candidates", None)
    )
    output: dict[int, Mapping[str, Any]] = {}
    for replica in (1, 2):
        if isinstance(supplied, Mapping):
            rows = supplied.get(replica)
            if isinstance(rows, (list, tuple)) and len(rows) == 1:
                output[replica] = rows[0]
                continue
        bindings = [
            dict(row)
            for row in merged["instance_bindings"]
            if row.get("replica") == replica
        ]
        if len(bindings) != 5:
            raise ValidationError("independent_h2_h1_replica_bindings")
        core = {
            "model_type": "h1_locator_pair",
            "model": dict(merged["model"]),
        }
        output[replica] = {
            **core,
            "canonical_model_id": _sha(core),
            "canonical_candidate_id": _sha(
                {**core, "instance_bindings": bindings}
            ),
            "instance_bindings": bindings,
        }
    for replica, candidate in output.items():
        if (
            candidate.get("model") != merged.get("model")
            or {row.get("replica") for row in candidate["instance_bindings"]}
            != {replica}
        ):
            raise ValidationError("independent_h2_h1_replica_bindings")
    return output


def _applicable(checkpoints: Sequence[str], bounds: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        left = checkpoints.index(bounds["start"])
        right = checkpoints.index(bounds["end"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError("independent_h2_lifecycle_range") from exc
    if left > right:
        raise ValidationError("independent_h2_lifecycle_range")
    return tuple(checkpoints[left : right + 1])


def _directory(blob: bytes, mask: int) -> tuple[tuple[tuple[int, int], ...] | None, tuple[int, int, str] | None, int]:
    row_count = int.from_bytes(blob[8:10], "little")
    directory_end = 10 + 2 * row_count
    if row_count > 1019 or directory_end > len(blob):
        return None, (0, 0, "row_count_exceeds_capacity"), row_count
    starts = [int.from_bytes(blob[10 + 2 * slot : 12 + 2 * slot], "little") & mask for slot in range(row_count)]
    bounds: list[tuple[int, int]] = []
    prior = len(blob)
    for slot, start in enumerate(starts):
        reason = None
        if start < directory_end:
            reason = "start_below_directory_end"
        elif start >= prior:
            reason = (
                "start_not_below_end" if slot == 0 else "overlap_in_directory_order"
            )
        elif prior > len(blob):
            reason = "end_above_page"
        elif slot and start >= starts[slot - 1]:
            reason = "overlap_in_directory_order"
        if reason is not None:
            raw = int.from_bytes(blob[10 + 2 * slot : 12 + 2 * slot], "little")
            return None, (slot, raw, reason), row_count
        bounds.append((start, prior))
        prior = start
    return tuple(bounds), None, row_count


def _invalid_directory_evidence(input_id: str, state: _State, checkpoint: str, page: int, blob: bytes, slot: int, raw: int, reason: str) -> dict[str, Any]:
    return {
        "kind": "row_directory", "input_model_id": input_id,
        "observation": {
            "replica": state.number, "checkpoint_id": checkpoint, "page": page,
            "row_count": min(int.from_bytes(blob[8:10], "little"), 1019), "slot": min(slot, 255),
            "raw_entry_u16le": raw, "masked_start_8191": raw & 8191,
            "masked_start_4095": raw & 4095, "reason": reason,
        },
    }


def _directory_stage(
    state: _State,
    h1: Mapping[str, Any],
    masks: Sequence[int],
    role_order: Mapping[str, int],
) -> tuple[list[_Located], tuple[int, ...], Mapping[str, Any] | None]:
    bindings = [row for row in h1["instance_bindings"] if row["replica"] == state.number]
    bindings.sort(key=lambda row: (role_order[row["logical_role"]], row["lifecycle_instance"]))
    located: list[_Located] = []
    page_results: dict[tuple[str, int], dict[int, tuple[tuple[int, int], ...] | None]] = {}
    entries_seen = 0
    for binding in bindings:
        for checkpoint in _applicable(state.checkpoints, binding["applicable_checkpoint_range"]):
            for ordinal, target in enumerate(binding["locator_targets"]):
                page, slot = target["page"], target["row"]
                blob = state.read(checkpoint, page)
                if blob is None or blob[0] != 1:
                    raise ValidationError("independent_h2_h1_target_changed")
                key = (checkpoint, page)
                if key not in page_results:
                    page_results[key] = {}
                    first_failure = None
                    row_count = int.from_bytes(blob[8:10], "little")
                    if row_count <= 1019:
                        entries_seen += row_count
                    for mask in masks:
                        bounds, failure, _ = _directory(blob, mask)
                        page_results[key][mask] = bounds
                        if first_failure is None and failure is not None:
                            first_failure = failure
                    valid = [mask for mask in masks if page_results[key][mask] is not None]
                    if not valid:
                        state.work["invalid_path_row_directory_entries"] = entries_seen
                        assert first_failure is not None
                        return [], (), _invalid_directory_evidence(
                            h1["canonical_candidate_id"],
                            state,
                            checkpoint,
                            page,
                            blob,
                            *first_failure,
                        )
                valid_rows: dict[int, bytes] = {}
                valid_bounds: dict[int, tuple[int, int]] = {}
                raw_entry = int.from_bytes(blob[10 + 2 * slot : 12 + 2 * slot], "little")
                for mask in masks:
                    bounds = page_results[key][mask]
                    if bounds is not None:
                        if slot >= len(bounds):
                            raise ValidationError("independent_h2_slot_range")
                        start, end = bounds[slot]
                        valid_rows[mask] = blob[start:end]
                        valid_bounds[mask] = (start, end)
                located.append(_Located(state.number, binding["logical_role"], binding["lifecycle_instance"], checkpoint, ordinal, page, slot, raw_entry, valid_rows, valid_bounds))
    state.work["valid_path_row_directory_entries"] = entries_seen
    surviving = [mask for mask in masks if all(mask in row.rows for row in located)]
    representatives: list[int] = []
    fingerprints: set[tuple[tuple[int, int], ...]] = set()
    for mask in surviving:
        fingerprint = tuple(row.bounds[mask] for row in located)
        if fingerprint not in fingerprints:
            fingerprints.add(fingerprint)
            representatives.append(mask)
    return located, tuple(representatives), None


def _flags_evidence(input_id: str, row: _Located) -> dict[str, Any]:
    return {
        "kind": "row_flags", "input_model_id": input_id,
        "observation": {
            "replica": row.replica, "checkpoint_id": row.checkpoint, "page": row.page,
            "slot": row.slot, "raw_entry_u16le": row.raw_entry,
            "deleted_flag_0x8000": bool(row.raw_entry & 0x8000),
            "overflow_flag_0x4000": bool(row.raw_entry & 0x4000),
        },
    }


def _tag_evidence(input_id: str, row: _Located, mask: int, reason: str) -> dict[str, Any]:
    start, end = row.bounds[mask]
    payload = row.rows[mask]
    return {
        "kind": "map_tag", "input_model_id": input_id,
        "observation": {
            "replica": row.replica, "checkpoint_id": row.checkpoint, "page": row.page,
            "slot": row.slot, "row_start": start, "row_end": end,
            "tag_byte": payload[0] if payload else 255, "reason": reason,
        },
    }


def _supported_masks(located: Sequence[_Located], masks: Sequence[int], input_id: str) -> tuple[tuple[int, ...], Mapping[str, Any] | None]:
    output = []
    first = None
    for mask in masks:
        valid = True
        for row in located:
            payload = row.rows[mask]
            reason = None
            if not payload or payload[0] not in (0, 1):
                reason = "unsupported_tag"
            elif payload[0] == 1 and (len(payload) - 1) % 4:
                reason = "type_1_payload_not_u32_multiple"
            if reason:
                valid = False
                if first is None:
                    first = _tag_evidence(input_id, row, mask, reason)
                break
        if valid:
            output.append(mask)
    return tuple(output), first


def _decode(state: _State, row: _Located, mask: int, polarity: str) -> set[int] | None:
    payload = row.rows[mask]
    key = (row.checkpoint, row.page, row.bounds[mask][0], row.bounds[mask][1])
    if payload[0] == 1:
        if key not in state.row_work:
            state.row_work.add(key)
            state.work["type_1_slots"] += (len(payload) - 1) // 4
        return None
    if len(payload) < 5:
        return set()
    if key not in state.row_work:
        state.row_work.add(key)
        state.work["type_0_and_tag_05_bitmap_bits"] += (len(payload) - 5) * 8
    base = int.from_bytes(payload[1:5], "little")
    owned_when_set = polarity == "set_bit_owned_in_use"
    return {
        base + byte_index * 8 + bit
        for byte_index, value in enumerate(payload[5:])
        for bit in range(8)
        if bool(value & (1 << bit)) == owned_when_set
    }


def _row_counts(values: Mapping[int, Mapping[str, Mapping[str, int]]], replica: int, checkpoint: str, role: str) -> int:
    try:
        value = values[replica][checkpoint][role]
    except (KeyError, TypeError) as exc:
        raise ValidationError("independent_h2_snapshot_row_count") from exc
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 200000:
        raise ValidationError("independent_h2_snapshot_row_count")
    return value


def _static_candidates(state: _State, located: Sequence[_Located], masks: Sequence[int], plan: Mapping[str, Any], row_counts: Mapping[int, Mapping[str, Mapping[str, int]]], page_limit: int) -> tuple[list[dict[str, Any]], dict[str, dict[tuple[str, str, int], set[int] | None]]]:
    grammar = plan["candidate_grammars"]["h2"]
    output = []
    decoded_by_id: dict[str, dict[tuple[str, str, int], set[int] | None]] = {}
    for mask in masks:
        for polarity in grammar["type_0_polarities"]:
            decoded = {(row.instance, row.checkpoint, row.ordinal): _decode(state, row, mask, polarity) for row in located}
            for assignment in grammar["locator_role_assignments"]:
                owned_ordinal = 0 if assignment.startswith("ordinal_0") else 1
                available_ordinal = 1 - owned_ordinal
                fits = True
                grouped = sorted({(row.instance, row.checkpoint, row.role) for row in located})
                for instance, checkpoint, role in grouped:
                    owned = decoded[(instance, checkpoint, owned_ordinal)]
                    available = decoded[(instance, checkpoint, available_ordinal)]
                    if owned is None or available is None:
                        continue
                    page_count = state.page_count(checkpoint, page_limit)
                    if any(page >= page_count for page in owned | available) or not available <= owned:
                        fits = False
                        break
                    if _row_counts(row_counts, state.number, checkpoint, role) > 0 and not owned:
                        fits = False
                        break
                if fits:
                    candidate = _candidate({
                        "row_mask": mask, "polarity": polarity,
                        "owned_in_use_locator_ordinal": owned_ordinal,
                        "available_locator_ordinal": available_ordinal,
                    })
                    output.append(candidate)
                    decoded_by_id[candidate["canonical_candidate_id"]] = decoded
    output.sort(key=lambda row: row["canonical_candidate_id"])
    return output, decoded_by_id


def _transition_ok(state: _State, candidate: Mapping[str, Any], decoded: Mapping[tuple[str, str, int], set[int] | None], located: Sequence[_Located], plan: Mapping[str, Any]) -> bool:
    owned_ordinal = candidate["model"]["owned_in_use_locator_ordinal"]
    available_ordinal = 1 - owned_ordinal
    by_instance = {row.role: row.instance for row in located}

    def sets(role: str, checkpoint: str) -> tuple[set[int] | None, set[int] | None]:
        instance = by_instance.get(role)
        if instance is None:
            return None, None
        return decoded.get((instance, checkpoint, owned_ordinal)), decoded.get((instance, checkpoint, available_ordinal))

    coverage = plan["checkpoint_design"]["transition_coverage"]
    transition_kinds = tuple(plan["candidate_grammars"]["h2"]["transition_signature"])
    roles = tuple(plan["candidate_grammars"]["h1"]["logical_roles"])
    state.work["role_transition_evaluations"] += (
        len(transition_kinds) * len(roles) * len(state.checkpoints)
    )
    growth_keys = tuple(
        key
        for key in coverage
        if key.endswith(("_growth", "_absolute", "_relative"))
    )
    if len(growth_keys) != 3:
        raise ValidationError("independent_h2_transition_contract")
    for key in growth_keys:
        role = key.split("_", 1)[0].upper()
        sequence = tuple(coverage[key])
        for left, right in zip(sequence, sequence[1:]):
            old_owned, old_available = sets(role, left)
            new_owned, new_available = sets(role, right)
            if None in (old_owned, old_available, new_owned, new_available):
                continue
            assert old_owned is not None and old_available is not None and new_owned is not None and new_available is not None
            gained = new_owned - old_owned
            if not old_owned <= new_owned or gained & (new_available - old_available):
                return False
    churn_keys = tuple(key for key in coverage if key.endswith("_churn"))
    if len(churn_keys) != 1:
        raise ValidationError("independent_h2_transition_contract")
    churn = tuple(coverage[churn_keys[0]])
    if len(churn) != 4:
        raise ValidationError("independent_h2_transition_contract")
    churn_role = churn_keys[0].split("_", 1)[0].upper()
    for left, right, kind in ((churn[0], churn[1], "delete"), (churn[1], churn[2], "reinsert")):
        old_owned, old_available = sets(churn_role, left)
        new_owned, new_available = sets(churn_role, right)
        if None in (old_owned, old_available, new_owned, new_available):
            continue
        assert old_owned is not None and old_available is not None and new_owned is not None and new_available is not None
        if kind == "delete" and (not new_owned <= old_owned or not old_available <= new_available):
            return False
        if kind == "reinsert" and (not old_owned <= new_owned or not new_available <= old_available):
            return False
    payloads = {(row.role, row.checkpoint, row.ordinal): row.rows[candidate["model"]["row_mask"]] for row in located}
    for left, right in plan["checkpoint_design"]["idle_pairs"]:
        for role in plan["candidate_grammars"]["h1"]["logical_roles"]:
            for ordinal in (0, 1):
                old = payloads.get((role, left, ordinal))
                new = payloads.get((role, right, ordinal))
                if old is None and new is None:
                    continue
                if old != new:
                    return False
    return True


def _predicate_row(contract: Mapping[str, Any], status: str, measured: int, survivor: int, terminal: str | None) -> dict[str, Any]:
    return {
        "predicate_id": contract["predicate_id"], "order": contract["order"],
        "scope": contract["scope"], "status": status, "terminal_predicate_id": terminal,
        "predicate_measured_survivor_count": measured, "derivation_survivor_count": survivor,
        "reachability_fixture_id": contract["reachability_fixture_id"],
    }


def _layer(status: str, candidates: Sequence[Mapping[str, Any]], measured: int, terminal: str | None, kind: str | None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = sorted((dict(row) for row in candidates), key=lambda row: row["canonical_candidate_id"])
    return {
        "status": status, "predicate_measured_survivor_count": measured,
        "derivation_survivor_count": 1 if status == "model" else 0,
        "terminal_predicate_id": terminal, "terminal_payload_kind": kind,
        "terminal_candidate_stage": "h2_final_role" if kind in ("candidate_set", "replica_pair") else None,
        "candidates": rows, "terminal_evidence": None if evidence is None else dict(evidence),
        "canonical_candidates_sha256": _sha(rows),
    }


def recompute_h2(
    replicas: Mapping[int, object] | Sequence[object],
    h1: object,
    *,
    plan: Mapping[str, Any],
    predicate_contracts: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    snapshot_row_counts: Mapping[int, Mapping[str, Mapping[str, int]]],
    derivation_replica_numbers: Sequence[int] = (1, 2),
) -> H2Recomputation:
    """Recompute H2 without consulting analyzer models or expected results."""
    if tuple(derivation_replica_numbers) != (1, 2):
        raise ValidationError("independent_h2_derivation_replicas")
    try:
        predicate_ids = tuple(
            plan["predicate_registry"]["per_layer_ordered_predicates"][
                "h2_row_identity_map_role"
            ]
        )
    except (KeyError, TypeError) as exc:
        raise ValidationError("independent_h2_predicate_contract") from exc
    contracts = _contracts(predicate_contracts, predicate_ids)
    source = _sources(replicas)
    h1_candidate = _h1_candidate(h1)
    h1_by_replica = _per_replica_h1(h1, h1_candidate)
    checkpoints = tuple(plan["checkpoint_design"]["checkpoint_ids"])
    bounds = plan["bounds"]
    if (
        len(checkpoints) != 25
        or bounds["page_size"] != 2048
        or bounds["max_final_pages_per_replica"] != 20480
        or bounds["max_candidate_models"] != 4096
    ):
        raise ValidationError("independent_h2_plan_bounds")
    states = {number: _State(source[number], number, checkpoints, 2048) for number in (1, 2)}
    masks = tuple(plan["candidate_grammars"]["h2"]["row_masks"])
    roles = tuple(plan["candidate_grammars"]["h1"]["logical_roles"])
    if len(roles) != 4 or len(set(roles)) != 4:
        raise ValidationError("independent_h2_role_contract")
    role_order = {role: index for index, role in enumerate(roles)}
    located: dict[int, list[_Located]] = {}
    valid_masks: dict[int, tuple[int, ...]] = {}
    predicates: list[dict[str, Any]] = []

    for number in (1, 2):
        located[number], valid_masks[number], evidence = _directory_stage(
            states[number], h1_by_replica[number], masks, role_order
        )
        if evidence is not None:
            for state in states.values():
                state.work["invalid_path_row_directory_entries"] += state.work[
                    "valid_path_row_directory_entries"
                ]
                state.work["valid_path_row_directory_entries"] = 0
            predicate = predicate_ids[0]
            predicates.append(_predicate_row(contracts[predicate], "fail", 1, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 1, predicate, "invalid_observation", evidence), predicates, {})
    predicates.append(_predicate_row(contracts[predicate_ids[0]], "pass", 1, 1, None))

    for number in (1, 2):
        flagged = next((row for row in located[number] if row.raw_entry & 0xC000), None)
        if flagged is not None:
            predicate = predicate_ids[1]
            predicates.append(_predicate_row(contracts[predicate], "fail", 1, 0, predicate))
            evidence = _flags_evidence(
                h1_by_replica[number]["canonical_candidate_id"], flagged
            )
            return _finish(states, _layer("no_outcome", [], 1, predicate, "invalid_observation", evidence), predicates, {})
    predicates.append(_predicate_row(contracts[predicate_ids[1]], "pass", 1, 1, None))

    supported: dict[int, tuple[int, ...]] = {}
    for number in (1, 2):
        supported[number], evidence = _supported_masks(
            located[number],
            valid_masks[number],
            h1_by_replica[number]["canonical_candidate_id"],
        )
        if not supported[number]:
            predicate = predicate_ids[2]
            predicates.append(_predicate_row(contracts[predicate], "fail", 1, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 1, predicate, "invalid_observation", evidence), predicates, {})
    predicates.append(_predicate_row(contracts[predicate_ids[2]], "pass", 1, 1, None))

    candidates: dict[int, list[dict[str, Any]]] = {}
    decoded: dict[int, dict[str, dict[tuple[str, str, int], set[int] | None]]] = {}
    for number in (1, 2):
        candidates[number], decoded[number] = _static_candidates(states[number], located[number], supported[number], plan, snapshot_row_counts, bounds["max_final_pages_per_replica"])
        if not candidates[number]:
            predicate = predicate_ids[3]
            predicates.append(_predicate_row(contracts[predicate], "fail", 0, 0, predicate))
            return _finish(states, _layer("no_outcome", [], 0, predicate, "candidate_set"), predicates, candidates)
    predicates.append(_predicate_row(contracts[predicate_ids[3]], "pass", min(len(rows) for rows in candidates.values()), 1, None))
    for number in (1, 2):
        if len(candidates[number]) > 1:
            predicate = predicate_ids[4]
            predicates.append(_predicate_row(contracts[predicate], "fail", len(candidates[number]), 0, predicate))
            return _finish(states, _layer("no_outcome", candidates[number], len(candidates[number]), predicate, "candidate_set"), predicates, candidates)
    predicates.append(_predicate_row(contracts[predicate_ids[4]], "pass", 1, 1, None))

    for number in (1, 2):
        candidate = candidates[number][0]
        if not _transition_ok(states[number], candidate, decoded[number][candidate["canonical_candidate_id"]], located[number], plan):
            predicate = predicate_ids[5]
            predicates.append(_predicate_row(contracts[predicate], "fail", 1, 0, predicate))
            return _finish(states, _layer("no_outcome", [candidate], 1, predicate, "candidate_set"), predicates, candidates)
    predicates.append(_predicate_row(contracts[predicate_ids[5]], "pass", 1, 1, None))

    if candidates[1][0]["canonical_candidate_id"] != candidates[2][0]["canonical_candidate_id"]:
        predicate = predicate_ids[6]
        evidence = {"kind": "replica_pair", "entries": [
            {"replica": number, "canonical_model_id": candidates[number][0]["canonical_candidate_id"], "canonical_candidate_id": candidates[number][0]["canonical_candidate_id"], "complete_candidate": candidates[number][0]}
            for number in (1, 2)
        ]}
        predicates.append(_predicate_row(contracts[predicate], "fail", 2, 0, predicate))
        return _finish(states, _layer("no_outcome", [], 2, predicate, "replica_pair", evidence), predicates, candidates)
    predicates.append(_predicate_row(contracts[predicate_ids[6]], "pass", 1, 1, None))
    final = candidates[1][0]
    return _finish(states, _layer("model", [final], 1, None, None), predicates, {1: [final], 2: [final]})


def _frozen_candidate(value: Mapping[str, Any], model_type: str) -> Mapping[str, Any]:
    if value.get("model_type") == model_type:
        return value
    layer = value.get("layer", value)
    candidates = layer.get("candidates") if isinstance(layer, Mapping) else None
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or candidates[0].get("model_type") != model_type
    ):
        raise ValidationError("independent_h2_holdout_frozen_model")
    return candidates[0]


def _frozen_static_ok(
    state: _State,
    located: Sequence[_Located],
    model: Mapping[str, Any],
    row_counts: Mapping[int, Mapping[str, Mapping[str, int]]],
    page_limit: int,
) -> tuple[bool, dict[tuple[str, str, int], set[int] | None]]:
    mask = model["row_mask"]
    polarity = model["polarity"]
    owned_ordinal = model["owned_in_use_locator_ordinal"]
    available_ordinal = model["available_locator_ordinal"]
    if {owned_ordinal, available_ordinal} != {0, 1}:
        raise ValidationError("independent_h2_holdout_role_model")
    decoded = {
        (row.instance, row.checkpoint, row.ordinal): _decode(
            state, row, mask, polarity
        )
        for row in located
    }
    for instance, checkpoint, role in sorted(
        {(row.instance, row.checkpoint, row.role) for row in located}
    ):
        owned = decoded[(instance, checkpoint, owned_ordinal)]
        available = decoded[(instance, checkpoint, available_ordinal)]
        if owned is None or available is None:
            continue
        if (
            any(page >= state.page_count(checkpoint, page_limit) for page in owned | available)
            or not available <= owned
            or (_row_counts(row_counts, 3, checkpoint, role) > 0 and not owned)
        ):
            return False, decoded
    return True, decoded


def predict_h2_holdout(
    replica3: object,
    holdout_h1_candidate: Mapping[str, Any],
    frozen_h2_result: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    snapshot_row_counts: Mapping[int, Mapping[str, Mapping[str, int]]],
) -> bool:
    """Apply frozen H1 bindings and the unchanged H2 model to replica 3.

    ``holdout_h1_candidate`` is the unique candidate returned by
    :func:`a4_independent_h1.apply_h1_holdout`; accepting it explicitly keeps
    this module independent of the H1 implementation and prevents H2 refit.
    """
    h1_candidate = _frozen_candidate(holdout_h1_candidate, "h1_locator_pair")
    if {row.get("replica") for row in h1_candidate["instance_bindings"]} != {3}:
        raise ValidationError("independent_h2_holdout_h1_binding")
    h2_candidate = _frozen_candidate(frozen_h2_result, "h2_final_role")
    checkpoints = tuple(plan["checkpoint_design"]["checkpoint_ids"])
    bounds = plan["bounds"]
    state = _State(replica3, 3, checkpoints, bounds["page_size"])
    roles = tuple(plan["candidate_grammars"]["h1"]["logical_roles"])
    role_order = {role: index for index, role in enumerate(roles)}
    mask = h2_candidate["model"]["row_mask"]
    located, masks, evidence = _directory_stage(
        state, h1_candidate, (mask,), role_order
    )
    if evidence is not None or masks != (mask,):
        return False
    if any(row.raw_entry & 0xC000 for row in located):
        return False
    supported, _ = _supported_masks(
        located, masks, h1_candidate["canonical_candidate_id"]
    )
    if supported != (mask,):
        return False
    fits, decoded = _frozen_static_ok(
        state,
        located,
        h2_candidate["model"],
        snapshot_row_counts,
        bounds["max_final_pages_per_replica"],
    )
    return fits and _transition_ok(
        state, h2_candidate, decoded, located, plan
    )


def _finish(states: Mapping[int, _State], layer: Mapping[str, Any], predicates: Sequence[Mapping[str, Any]], candidates: Mapping[int, Sequence[Mapping[str, Any]]]) -> H2Recomputation:
    qualified = sorted({identity for state in states.values() for identity in state.qualified}, key=lambda row: (row[0], states[row[0]].checkpoints.index(row[1]), row[2]))
    names = next(iter(states.values())).work
    work = {name: sum(state.work[name] for state in states.values()) for name in names}
    return H2Recomputation(
        dict(layer), tuple(dict(row) for row in predicates),
        tuple({"replica": replica, "checkpoint_id": checkpoint, "page_number": page} for replica, checkpoint, page in qualified),
        work, {number: tuple(dict(row) for row in candidates.get(number, ())) for number in states},
    )
