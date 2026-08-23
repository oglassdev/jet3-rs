#!/usr/bin/env python3
"""A4-H1 (TDEF-to-map-row location) reference evaluation per derivation replica.

A4 rule | implementation
--- | ---
TDEF lifecycle signatures (new tag 02 at create / preexisting hash transition) | :func:`tdef_matches`
4,090 raw window identities, syntactic decode, cross-checkpoint preservation | :func:`preserved_windows`
Canonical nonoverlapping pairs charged arithmetically, structural pairs at the exact holes | :func:`evaluate_replica`
Target existence, tag 01, row ordinal < row_count, distinct targets | :func:`targets_valid`
Replica-invariant model {layout, signature id, offsets} plus per-instance bindings | :func:`evaluate_replica`
H1 holdout: re-derive replica-3 bindings under the unchanged model | :func:`holdout`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from a4_dryrun_campaign import qualified_tag02_pages
from a4_dryrun_core import Context
from a4_pages import TAG_DATA, TAG_TDEF, decode_locator, row_count
from a4_spec import (
    EVENT_BY_CHECKPOINT, IDLE_PAIRS, INSTANCES, Instance, LAYER_PREDICATES, LOCATOR_HOLES, LOCATOR_LAYOUTS, PAGE_SIZE,
    SCHEMA_LIFECYCLE, SIGNATURE_MASK, SIGNATURE_VALUE, TDEF_SIGNATURES, canonical_id, sha256_hex,
)

H1 = LAYER_PREDICATES["h1_tdef_to_map_row"]
SIGNATURE_ID = sha256_hex(SIGNATURE_VALUE + SIGNATURE_MASK)
WINDOW_OFFSETS = range(0, PAGE_SIZE - 3)  # 2045 offsets per layout, 4090 raw window identities
MAX_SYNTACTIC_PAGE = 0xFFFF  # AMB-01


@dataclass
class ReplicaLayer:
    outcomes: list[tuple[str, bool, int, str]] = field(default_factory=list)
    model: dict[str, Any] | None = None
    bindings: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, Any] = field(default_factory=dict)

    def fail(self, predicate_id: str, count: int, detail: str = "") -> "ReplicaLayer":
        self.outcomes.append((predicate_id, False, count, detail))
        return self

    def ok(self, predicate_id: str, count: int) -> None:
        self.outcomes.append((predicate_id, True, count, ""))

    @property
    def terminal(self) -> str | None:
        return next((p for p, passed, _, _ in self.outcomes if not passed), None)


def signature_matches(page: bytes) -> bool:
    return all((page[i] & SIGNATURE_MASK[i]) == (SIGNATURE_VALUE[i] & SIGNATURE_MASK[i]) for i in range(len(SIGNATURE_VALUE)))


def tdef_matches(ctx: Context, replica: int, inst: Instance, page: int, signature: str) -> bool:
    before = ctx.predecessor(inst.create_checkpoint)
    tag_before = ctx.tag(replica, before, page) if before else None
    for checkpoint in inst.checkpoints:
        ctx.charges.add("tdef_lifecycle_signatures")
        if ctx.tag(replica, checkpoint, page) != TAG_TDEF:
            return False
    if signature == "new_tag_02_at_role_create":
        return tag_before != TAG_TDEF
    if tag_before != TAG_TDEF or ctx.page_hash(replica, before or "", page) == ctx.page_hash(replica, inst.create_checkpoint, page):
        return False
    for left, right in list(zip(SCHEMA_LIFECYCLE, SCHEMA_LIFECYCLE[1:])) + list(IDLE_PAIRS):
        if EVENT_BY_CHECKPOINT[right].role == inst.role:
            continue
        if ctx.page_hash(replica, left, page) != ctx.page_hash(replica, right, page):
            return False
    return True


def preserved_windows(ctx: Context, replica: int, page: int, checkpoints: tuple[str, ...], layout: str) -> set[int]:
    """Offsets whose decoded (page,row) is syntactically decodable and identical at every checkpoint."""
    pages = [ctx.page(replica, cp, page) or b"" for cp in checkpoints]
    out = set()
    for offset in WINDOW_OFFSETS:
        ctx.charges.add("raw_locator_windows")
        decoded = {decode_locator(p[offset: offset + 4], layout) for p in pages}
        if len(decoded) == 1 and next(iter(decoded))[0] <= MAX_SYNTACTIC_PAGE:
            out.add(offset)
    return out


def canonical_pair_count(offsets: set[int]) -> int:
    """Number of canonical pairs a<b with b-a>=4 among preserved offsets (counted, not materialised)."""
    ordered = sorted(offsets)
    total, j = 0, 0
    for i, b in enumerate(ordered):
        while ordered[j] <= b - 4:
            j += 1
        total += j
    return total


def target_of(page: bytes, offset: int, layout: str) -> tuple[int, int]:
    return decode_locator(page[offset: offset + 4], layout)


def targets_valid(ctx: Context, replica: int, page_bytes: bytes, checkpoint: str, offsets: tuple[int, int], layout: str) -> str | None:
    targets = [target_of(page_bytes, o, layout) for o in offsets]
    if targets[0] == targets[1]:
        return "duplicate targets"
    for target_page, row in targets:
        ctx.charges.add("h1_target_validity_checks")
        blob = ctx.page(replica, checkpoint, target_page)
        if blob is None:
            return f"target page {target_page} absent at {checkpoint}"
        if blob[0] != TAG_DATA:
            return f"target page {target_page} tag {blob[0]:02x} at {checkpoint}"
        if row >= row_count(blob):
            return f"row {row} out of range on page {target_page} at {checkpoint}"
    return None


def evaluate_replica(ctx: Context, replica: int) -> ReplicaLayer:
    out = ReplicaLayer()
    qualified = sorted(qualified_tag02_pages(ctx, replica))
    candidates: list[tuple[str, dict[str, int]]] = []
    for signature in TDEF_SIGNATURES:
        per_instance = {inst.id: [p for p in qualified if tdef_matches(ctx, replica, inst, p, signature)] for inst in INSTANCES}
        if all(per_instance.values()):
            combos: list[dict[str, int]] = [{}]
            for inst_id, pages in per_instance.items():
                combos = [{**c, inst_id: p} for c in combos for p in pages]
            candidates += [(signature, c) for c in combos]
    out.stages["tdef_candidates"] = [{"signature": s, "pages": c} for s, c in candidates]
    if not candidates:
        return out.fail(H1[0], 0, "no lifecycle-matching TDEF candidate")
    out.ok(H1[0], len(candidates))
    if len(candidates) > 1:
        return out.fail(H1[1], len(candidates), "multiple lifecycle-matching TDEF candidates")
    out.ok(H1[1], 1)
    signature, pages = candidates[0]

    preserved = {layout: {inst.id: preserved_windows(ctx, replica, pages[inst.id], inst.checkpoints, layout)
                          for inst in INSTANCES} for layout in LOCATOR_LAYOUTS}
    layouts_with_windows = [l for l in LOCATOR_LAYOUTS if any(preserved[l].values())]
    out.stages["preserved_windows"] = {l: {i: len(s) for i, s in m.items()} for l, m in preserved.items()}
    if not layouts_with_windows:
        return out.fail(H1[2], 0, "no syntactically preserved window under either layout")
    out.ok(H1[2], len(layouts_with_windows))

    holes = (LOCATOR_HOLES[0][0], LOCATOR_HOLES[1][0])
    structural: list[tuple[str, tuple[int, int]]] = []
    for layout in LOCATOR_LAYOUTS:
        for inst in INSTANCES:
            ctx.charges.add("raw_locator_pairs", canonical_pair_count(preserved[layout][inst.id]))
        if all(holes[0] in preserved[layout][i.id] and holes[1] in preserved[layout][i.id] for i in INSTANCES) and all(
                signature_matches(ctx.page(replica, cp, pages[i.id]) or b"") for i in INSTANCES for cp in i.checkpoints):
            structural.append((layout, holes))
    out.stages["structural_pairs"] = [{"layout": l, "offsets": list(o)} for l, o in structural]
    if not structural:
        return out.fail(H1[3], 0, "no masked, nonoverlapping, identity-preserved pair")
    out.ok(H1[3], len(structural))

    valid: list[tuple[str, tuple[int, int]]] = []
    reasons = []
    for layout, offsets in structural:
        problem = None
        for inst in INSTANCES:
            for cp in inst.checkpoints:
                problem = targets_valid(ctx, replica, ctx.page(replica, cp, pages[inst.id]) or b"", cp, offsets, layout)
                if problem:
                    break
            if problem:
                break
        if problem:
            reasons.append(f"{layout}: {problem}")
        else:
            valid.append((layout, offsets))
    out.stages["target_valid_pairs"] = [{"layout": l, "offsets": list(o)} for l, o in valid]
    if not valid:
        return out.fail(H1[4], 0, "; ".join(reasons))
    out.ok(H1[4], len(valid))
    layouts = sorted({l for l, _ in valid}, key=LOCATOR_LAYOUTS.index)
    if len(layouts) > 1:
        return out.fail(H1[5], len(layouts), "target-valid pairs under more than one layout")
    out.ok(H1[5], 1)
    pairs = [o for l, o in valid if l == layouts[0]]
    if len(pairs) > 1:
        return out.fail(H1[6], len(pairs), "more than one target-valid pair under the unique layout")
    out.ok(H1[6], 1)
    layout, offsets = layouts[0], pairs[0]
    model = {"layout": layout, "table_record_signature_id": SIGNATURE_ID, "locator_offsets": list(offsets),
             "tdef_lifecycle_signature": signature}
    out.model = {"model_type": "h1_locator_model", "model": model, "canonical_model_id": canonical_id({"model_type": "h1_locator_model", "model": model})}
    for inst in INSTANCES:
        page_bytes = ctx.page(replica, inst.create_checkpoint, pages[inst.id]) or b""
        out.bindings[inst.id] = {"logical_role": inst.role, "lifecycle_instance": inst.id, "tdef_page": pages[inst.id],
                                 "targets": [list(target_of(page_bytes, o, layout)) for o in offsets],
                                 "applicable_checkpoint_range": [inst.create_checkpoint, inst.last_checkpoint]}
    out.model["canonical_candidate_id"] = canonical_id({**out.model, "instance_bindings": out.bindings})
    return out


def holdout(ctx: Context, replica: int, frozen: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Apply the unchanged H1 model to the holdout replica and re-derive its bindings."""
    model = frozen["model"]
    layout, offsets, signature = model["layout"], tuple(model["locator_offsets"]), model["tdef_lifecycle_signature"]
    qualified = sorted(qualified_tag02_pages(ctx, replica))
    bindings: dict[str, Any] = {}
    for inst in INSTANCES:
        pages = [p for p in qualified if tdef_matches(ctx, replica, inst, p, signature)]
        if len(pages) != 1:
            return False, f"{inst.id}: {len(pages)} lifecycle-matching TDEF pages", bindings
        page = pages[0]
        for cp in inst.checkpoints:
            blob = ctx.page(replica, cp, page) or b""
            if not signature_matches(blob):
                return False, f"{inst.id}: signature mismatch at {cp}", bindings
            problem = targets_valid(ctx, replica, blob, cp, offsets, layout)
            if problem:
                return False, f"{inst.id}: {problem}", bindings
        first = ctx.page(replica, inst.create_checkpoint, page) or b""
        preserved = all(target_of(ctx.page(replica, cp, page) or b"", o, layout) == target_of(first, o, layout)
                        for cp in inst.checkpoints for o in offsets)
        if not preserved:
            return False, f"{inst.id}: locator identity not preserved", bindings
        bindings[inst.id] = {"logical_role": inst.role, "lifecycle_instance": inst.id, "tdef_page": page,
                             "targets": [list(target_of(first, o, layout)) for o in offsets],
                             "applicable_checkpoint_range": [inst.create_checkpoint, inst.last_checkpoint]}
    return True, "", bindings
