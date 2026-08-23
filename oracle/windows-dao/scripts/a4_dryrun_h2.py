#!/usr/bin/env python3
"""A4-H2 (row identity and map role) reference evaluation per derivation replica.

A4 rule | implementation
--- | ---
Complete checked row-directory validation under both offset masks, byte-identical masks merged | :func:`locate_rows`
Deleted/overflow flags explicit | :func:`evaluate_replica`
Tag 00/01 with type-1 payload divisible by four | :func:`evaluate_replica`
Static role/polarity fit (AMB-05) then registered transition signatures (AMB-07) | :func:`static_fit`, :func:`transitions_hold`
H2 holdout: unchanged model re-applied to replica 3 | :func:`holdout`
"""

from __future__ import annotations

from typing import Any

from a4_dryrun_core import Context
from a4_dryrun_h1 import ReplicaLayer
from a4_pages import MapRow, Slot, decode_directory, decode_map_row, validate_directory
from a4_spec import (
    EVENT_BY_CHECKPOINT, INSTANCES, Instance, LAYER_PREDICATES, POLARITIES, ROLE_ASSIGNMENTS, ROW_MASKS, canonical_id,
)

H2 = LAYER_PREDICATES["h2_row_identity_map_role"]
Located = dict[tuple[str, str], list[tuple[Slot, bytes]]]  # (instance, checkpoint) -> [(slot, row bytes)] per locator ordinal


def locate_rows(ctx: Context, replica: int, bindings: dict[str, Any], mask: int) -> tuple[Located | None, str]:
    located: Located = {}
    for inst in INSTANCES:
        binding = bindings[inst.id]
        for cp in inst.checkpoints:
            rows = []
            for target_page, row in binding["targets"]:
                page = ctx.page(replica, cp, target_page) or b""
                slots = decode_directory(page, mask)
                ctx.charges.add("valid_path_row_directory_entries", len(slots))
                problem = validate_directory(slots)
                if problem:
                    return None, f"{inst.id} page {target_page} at {cp}: {problem}"
                slot = slots[row]
                rows.append((slot, page[slot.start: slot.end]))
            located[(inst.id, cp)] = rows
    return located, ""


def owned_available(rows: list[tuple[Slot, bytes]], assignment: str) -> tuple[bytes, bytes]:
    owned_ordinal = 0 if assignment == ROLE_ASSIGNMENTS[0] else 1
    return rows[owned_ordinal][1], rows[1 - owned_ordinal][1]


def decode_sets(row: bytes, polarity: str, ctx: Context) -> set[int] | None:
    decoded = decode_map_row(row)
    if isinstance(decoded, str) or decoded.kind != 0:
        return None
    ctx.charges.add("type_0_and_tag_05_bitmap_bits", len(decoded.bitmap) * 8)
    return decoded.type0_pages(polarity)


def static_fit(ctx: Context, replica: int, located: Located, polarity: str, assignment: str) -> str | None:
    for (inst_id, cp), rows in located.items():
        owned_row, available_row = owned_available(rows, assignment)
        owned = decode_sets(owned_row, polarity, ctx)
        available = decode_sets(available_row, polarity, ctx)
        limit = ctx.page_count(replica, cp)
        if owned is not None:
            if not owned:
                return f"{inst_id} at {cp}: owned/in-use set is empty"
            if max(owned) >= limit:
                return f"{inst_id} at {cp}: owned/in-use admits page beyond page_count"
        if available is not None:
            if available and max(available) >= limit:
                return f"{inst_id} at {cp}: available admits page beyond page_count"
            if owned is not None and not available <= owned:
                return f"{inst_id} at {cp}: available set is not within the owned set"
    return None


def transitions_hold(ctx: Context, located: Located, polarity: str, assignment: str, instances: tuple[Instance, ...] = INSTANCES) -> str | None:
    for inst in instances:
        cps = inst.checkpoints
        for before, after in zip(cps, cps[1:]):
            event = EVENT_BY_CHECKPOINT[after]
            ctx.charges.add("role_transition_evaluations")
            rows_before, rows_after = located[(inst.id, before)], located[(inst.id, after)]
            if event.kind == "idle":
                if [r[1] for r in rows_before] != [r[1] for r in rows_after]:
                    return f"{inst.id} {before}->{after}: rows differ across an idle leg"
                continue
            if event.role != inst.role or event.kind not in ("grow", "delete_all", "reinsert"):
                continue
            o_row_b, a_row_b = owned_available(rows_before, assignment)
            o_row_a, a_row_a = owned_available(rows_after, assignment)
            o_b, o_a = decode_sets(o_row_b, polarity, ctx), decode_sets(o_row_a, polarity, ctx)
            a_b, a_a = decode_sets(a_row_b, polarity, ctx), decode_sets(a_row_a, polarity, ctx)
            if None in (o_b, o_a, a_b, a_a):
                continue  # type-1 side: admitted set needs the H3 traversal (AMB-05)
            assert o_b is not None and o_a is not None and a_b is not None and a_a is not None
            leg = f"{inst.id} {before}->{after} ({event.kind})"
            if event.kind == "grow":
                if not o_b <= o_a:
                    return f"{leg}: owned/in-use removed pages {sorted(o_b - o_a)[:4]}"
                if (a_a - a_b) & (o_a - o_b):
                    return f"{leg}: available added pages the owned set gained"
            elif event.kind == "delete_all":
                changes = (o_b ^ o_a) | (a_b ^ a_a)
                if not o_a <= o_b or not a_b <= a_a or not changes <= o_b:
                    return f"{leg}: delete_all signature violated"
            else:
                changes = (o_b ^ o_a) | (a_b ^ a_a)
                if not o_b <= o_a or not a_a <= a_b or not changes <= (o_b | o_a):
                    return f"{leg}: reinsert signature violated"
    return None


def _mask_models(ctx: Context, replica: int, bindings: dict[str, Any]) -> tuple[list[tuple[list[int], Located]], str]:
    """Valid masks grouped into canonical models with byte-identical complete bounds (ambiguity_rule)."""
    models: list[tuple[list[int], Located]] = []
    last_problem = ""
    for mask in ROW_MASKS:
        located, problem = locate_rows(ctx, replica, bindings, mask)
        if located is None:
            last_problem = problem
            continue
        bounds = {k: [(s.start, s.end) for s, _ in v] for k, v in located.items()}
        for members, existing in models:
            if {k: [(s.start, s.end) for s, _ in v] for k, v in existing.items()} == bounds:
                members.append(mask)
                break
        else:
            models.append(([mask], located))
    return models, last_problem


def evaluate_replica(ctx: Context, replica: int, bindings: dict[str, Any]) -> ReplicaLayer:
    out = ReplicaLayer()
    mask_models, problem = _mask_models(ctx, replica, bindings)
    if not mask_models:
        return out.fail(H2[0], 1, problem)
    out.ok(H2[0], len(mask_models))
    flagged = [(k, s.ordinal) for _, located in mask_models for k, rows in located.items() for s, _ in rows if s.deleted or s.overflow]
    if flagged:
        return out.fail(H2[1], 1, f"located row has deleted/overflow set: {flagged[0]}")
    out.ok(H2[1], 1)
    for _, located in mask_models:
        for key, rows in located.items():
            for _, row in rows:
                decoded = decode_map_row(row)
                if isinstance(decoded, str):
                    return out.fail(H2[2], 1, f"{key}: {decoded}")
                if decoded.kind == 1:
                    ctx.charges.add("type_1_slots", len(decoded.slots))
    out.ok(H2[2], 1)

    candidates = []
    reasons = []
    for members, located in mask_models:
        for polarity in POLARITIES:
            for assignment in ROLE_ASSIGNMENTS:
                problem = static_fit(ctx, replica, located, polarity, assignment)
                if problem:
                    reasons.append(f"{polarity}/{assignment}: {problem}")
                else:
                    candidates.append((members, polarity, assignment, located))
    out.stages["static_role_candidates"] = [{"row_masks": m, "polarity": p, "assignment": a} for m, p, a, _ in candidates]
    if not candidates:
        return out.fail(H2[3], 0, "; ".join(reasons[:2]))
    out.ok(H2[3], len(candidates))
    if len(candidates) > 1:
        return out.fail(H2[4], len(candidates), "multiple static role/polarity/mask candidates")
    out.ok(H2[4], 1)
    members, polarity, assignment, located = candidates[0]
    problem = transitions_hold(ctx, located, polarity, assignment)
    if problem:
        return out.fail(H2[5], 1, problem)
    out.ok(H2[5], 1)
    model = {"row_masks": members, "polarity": polarity, "locator_role_assignment": assignment}
    out.model = {"model_type": "h2_role_model", "model": model, "canonical_model_id": canonical_id({"model_type": "h2_role_model", "model": model})}
    out.bindings = {"located": located}
    return out


def holdout(ctx: Context, replica: int, frozen: dict[str, Any], bindings: dict[str, Any]) -> tuple[bool, str, Located | None]:
    model = frozen["model"]
    located, problem = locate_rows(ctx, replica, bindings, model["row_masks"][0])
    if located is None:
        return False, problem, None
    flagged = [k for k, rows in located.items() for s, _ in rows if s.deleted or s.overflow]
    if flagged:
        return False, f"flagged row at {flagged[0]}", located
    for key, rows in located.items():
        for _, row in rows:
            decoded = decode_map_row(row)
            if isinstance(decoded, str):
                return False, f"{key}: {decoded}", located
    problem = static_fit(ctx, replica, located, model["polarity"], model["locator_role_assignment"]) or transitions_hold(
        ctx, located, model["polarity"], model["locator_role_assignment"])
    return (problem is None), problem or "", located


def owned_rows(located: Located, assignment: str) -> dict[tuple[str, str], MapRow]:
    """Decoded owned/in-use map row per (instance, checkpoint) for the H3 layer."""
    out = {}
    for key, rows in located.items():
        decoded = decode_map_row(owned_available(rows, assignment)[0])
        if not isinstance(decoded, str):
            out[key] = decoded
    return out
