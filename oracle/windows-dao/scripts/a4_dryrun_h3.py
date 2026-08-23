#!/usr/bin/env python3
"""A4-H3 (indirect traversal) reference evaluation per derivation replica.

A4 rule | implementation
--- | ---
Structural type-0 -> type-1 conversion with a nonzero u32 slot, no tag test | :func:`conversions`
Zero slot inactive, nonzero slot an exact tag-05 reference | :func:`evaluate_replica`
Input-region coverage before formula fitting (AMB-08) | :func:`regions`
Four registered base formulas fitted by containment/transition signatures on decoded sets | :func:`formula_fits`
H3 holdout: unchanged formula re-applied to replica 3 | :func:`holdout`
"""

from __future__ import annotations

from typing import Any

from a4_dryrun_core import Context
from a4_dryrun_h1 import ReplicaLayer
from a4_pages import TAG_EXTENDED, MapRow, absolute_page, tag05_bits
from a4_spec import (
    BASE_FORMULAS, CONVERSIONS, EVENT_BY_CHECKPOINT, INSTANCES, LAYER_PREDICATES, TAG05_BITS, canonical_id,
)

H3 = LAYER_PREDICATES["h3_indirect_traversal"]
Owned = dict[tuple[str, str], MapRow]
REGION_NAMES = ("inactive_slot", "active_slot", "bit_index_zero", "bit_index_nonzero", "boundary_last_bit", "boundary_next_slot_first_bit")


def conversions(owned: Owned) -> list[tuple[str, str, str]]:
    out = []
    for inst in INSTANCES:
        cps = inst.checkpoints
        for before, after in zip(cps, cps[1:]):
            a, b = owned.get((inst.id, before)), owned.get((inst.id, after))
            if a is not None and b is not None and a.kind == 0 and b.kind == 1 and any(b.slots):
                out.append((inst.id, before, after))
    return out


def _bits(ctx: Context, replica: int, cp: str, page: int) -> list[int] | None:
    blob = ctx.page(replica, cp, page)
    if blob is None or blob[0] != TAG_EXTENDED:
        return None
    ctx.charges.add("type_0_and_tag_05_bitmap_bits", TAG05_BITS)
    return tag05_bits(blob)


def invalid_reference(ctx: Context, replica: int, owned: Owned) -> str | None:
    for (inst_id, cp), row in owned.items():
        if row.kind != 1:
            continue
        for ordinal, reference in enumerate(row.slots):
            if reference == 0:
                continue
            ctx.charges.add("type_1_slots")
            tag = ctx.tag(replica, cp, reference)
            if tag is None:
                return f"{inst_id} at {cp}: slot {ordinal} references page {reference} beyond page_count"
            if tag != TAG_EXTENDED:
                return f"{inst_id} at {cp}: slot {ordinal} references page {reference} with tag {tag:02x}"
    return None


def regions(ctx: Context, replica: int, owned: Owned) -> dict[str, bool]:
    seen = {name: False for name in REGION_NAMES}
    for (_, cp), row in owned.items():
        if row.kind != 1:
            continue
        for ordinal, reference in enumerate(row.slots):
            if reference == 0:
                seen["inactive_slot"] = True
                continue
            seen["active_slot"] = True
            bits = _bits(ctx, replica, cp, reference) or []
            if 0 in bits:
                seen["bit_index_zero"] = True
            if any(b > 0 for b in bits):
                seen["bit_index_nonzero"] = True
            following = row.slots[ordinal + 1] if ordinal + 1 < len(row.slots) else 0
            if following:
                if TAG05_BITS - 1 in bits:
                    seen["boundary_last_bit"] = True
                if 0 in (_bits(ctx, replica, cp, following) or []):
                    seen["boundary_next_slot_first_bit"] = True
    return seen


def admitted(ctx: Context, replica: int, cp: str, row: MapRow, polarity: str, formula: str) -> set[int]:
    if row.kind == 0:
        return row.type0_pages(polarity)
    out: set[int] = set()
    for ordinal, reference in enumerate(row.slots):
        if reference:
            for bit in _bits(ctx, replica, cp, reference) or []:
                out.add(absolute_page(formula, ordinal, reference, bit))
    return out


def formula_fits(ctx: Context, replica: int, owned: Owned, polarity: str, formula: str) -> str | None:
    for inst in INSTANCES:
        cps = inst.checkpoints
        sets = {}
        for cp in cps:
            row = owned.get((inst.id, cp))
            if row is None:
                return f"{inst.id} at {cp}: no located owned row"
            ctx.charges.add("base_formula_evaluations")
            sets[cp] = admitted(ctx, replica, cp, row, polarity, formula)
        for before, after in zip(cps, cps[1:]):
            a, b = owned[(inst.id, before)], owned[(inst.id, after)]
            event = EVENT_BY_CHECKPOINT[after]
            leg = f"{inst.id} {before}->{after}"
            if a.kind == 0 and b.kind == 1:
                if not sets[before] <= sets[after]:
                    return f"{leg}: conversion drops pages {sorted(sets[before] - sets[after])[:4]}"
            elif a.kind == 1 and b.kind == 1:
                if event.kind == "idle" and sets[before] != sets[after]:
                    return f"{leg}: idle leg changes the admitted set"
                if event.role != inst.role:
                    continue
                if event.kind in ("grow", "reinsert") and not sets[before] <= sets[after]:
                    return f"{leg}: {event.kind} removes admitted pages"
                if event.kind == "delete_all" and not sets[after] <= sets[before]:
                    return f"{leg}: delete_all adds admitted pages"
    return None


def evaluate_replica(ctx: Context, replica: int, owned: Owned, polarity: str) -> ReplicaLayer:
    out = ReplicaLayer()
    found = conversions(owned)
    out.stages["conversions"] = [{"instance": i, "before": b, "after": a} for i, b, a in found]
    if not found:
        return out.fail(H3[0], 0, "no structural type-0 to type-1 transition with a nonzero slot")
    out.ok(H3[0], 1)
    type1 = [row for row in owned.values() if row.kind == 1]
    if not any(0 in row.slots for row in type1):
        return out.fail(H3[1], 1, "every observed slot is nonzero")
    out.ok(H3[1], 1)
    problem = invalid_reference(ctx, replica, owned)
    if problem:
        return out.fail(H3[2], 1, problem)
    out.ok(H3[2], 1)
    seen = regions(ctx, replica, owned)
    out.stages["input_regions"] = seen
    missing = [name for name, ok in seen.items() if not ok]
    if missing:
        return out.fail(H3[3], 1, f"unexercised input regions: {missing}")
    out.ok(H3[3], 1)
    fitting, reasons = [], []
    for formula in BASE_FORMULAS:
        problem = formula_fits(ctx, replica, owned, polarity, formula)
        (reasons if problem else fitting).append(problem or formula)
    out.stages["fitting_formulas"] = list(fitting)
    if not fitting:
        return out.fail(H3[4], 0, "; ".join(reasons[:2]))
    out.ok(H3[4], len(fitting))
    if len(fitting) > 1:
        return out.fail(H3[5], len(fitting), "more than one base formula fits")
    out.ok(H3[5], 1)
    model = {"conversion_candidate": CONVERSIONS[0], "base_formula": fitting[0], "tag_05_bitmap_polarity": "set_bit_owned_in_use"}
    out.model = {"model_type": "h3_traversal_model", "model": model, "canonical_model_id": canonical_id({"model_type": "h3_traversal_model", "model": model})}
    return out


def holdout(ctx: Context, replica: int, frozen: dict[str, Any], owned: Owned, polarity: str) -> tuple[bool, str]:
    if not any(row.kind == 1 for row in owned.values()):
        ctx.notes.append("H3 holdout: replica 3 exposes no type-1 owned row; frozen formula vacuously confirmed (AMB-14)")
        return True, ""
    problem = invalid_reference(ctx, replica, owned) or formula_fits(ctx, replica, owned, polarity, frozen["model"]["base_formula"])
    return problem is None, problem or ""
