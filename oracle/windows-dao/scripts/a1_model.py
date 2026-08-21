#!/usr/bin/env python3
"""Preregistered three-layer model derivation for DAO-A1-ALLOCATION-MAPS-001.

Layer 1 is the caller-delimited global allocation-map record, layer 2 the two
TDEF pointers inside that record, and layer 3 the type-1 extended maps. Every
rule implemented here comes from the checked plan's `hypotheses` block; no
candidate formula, offset layout, or transition predicate is added.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a1_spec import (
    BASE_FORMULAS,
    CHECKPOINT_IDS,
    FINAL_PAGE_CEILING,
    IDLE_PAIRS,
    LADDER,
    PAGE_SIZE,
    POINTER_LAYOUTS,
    WORK_CEILING,
)

REPLICA_DISAGREEMENT = "replica_disagreement"
MISSING_CONVERSION = "missing_inline_to_indirect_conversion"
RESOURCE_BOUND_BREACH = "resource_bound_breach"
NO_SURVIVING_MODEL = "no_surviving_joint_model"
MULTIPLE_SURVIVING_MODELS = "multiple_surviving_joint_models"
IDLE_VOLATILITY = "idle_volatility"
UNRECONSTRUCTABLE_SNAPSHOT = "unreconstructable_snapshot"
AMBIGUOUS_RECORD_BOUNDARY = "ambiguous_record_boundary"
AMBIGUOUS_INLINE_BOUNDARY = "ambiguous_inline_boundary"
UNEXPLAINED_INLINE_SUFFIX = "unexplained_nonzero_inline_suffix"
HOLDOUT_PREDICTION_FAILURE = "holdout_prediction_failure"
INCOMPLETE_TRANSITION_EVIDENCE = "incomplete_transition_evidence"

METADATA_PAGE = 1
INLINE_TAG = 0x00
INDIRECT_TAG = 0x01
EXTENDED_MAP_TAG = 0x05
EXTENDED_MAP_HEADER_BYTES = 4
EXTENDED_MAP_BITS = (PAGE_SIZE - EXTENDED_MAP_HEADER_BYTES) * 8
POINTER_WIDTH = 4
INLINE_START_PAGE_WIDTH = 4
RETAINED_PAGE_STORE_CEILING = 536_870_912
PAGE_CACHE_ENTRIES = 4096

_L_LADDER = tuple(f"L_REL_{target:04d}" for target in LADDER)
_H_LADDER = tuple(f"H_REL_{target:04d}" for target in LADDER)
_P_LADDER = ("P_ABS_04096", "P_ABS_08192", "P_ABS_12288", "P_ABS_16480")


def _consecutive(ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(zip(ids, ids[1:], strict=False))


ORDINAL: dict[str, int] = {name: index for index, name in enumerate(CHECKPOINT_IDS)}
GROWTH_TRANSITIONS = _consecutive(_L_LADDER) + _consecutive(_P_LADDER) + _consecutive(_H_LADDER)
CHURN_TRANSITIONS = (
    ("L_REL_1280", "L_DELETE_ALTERNATING"),
    ("L_DELETE_ALTERNATING", "L_REINSERT_SAME"),
)
ALL_TRANSITIONS = tuple(
    pair for pair in _consecutive(CHECKPOINT_IDS) if pair not in set(IDLE_PAIRS)
)
POINTER_SCOPE = CHECKPOINT_IDS[ORDINAL["L_REL_0064"] :]
LAST_L_CHECKPOINT = "L_IDLE_REOPEN"
FINAL_CHECKPOINT = CHECKPOINT_IDS[-1]


class Abort(Exception):
    """A preregistered no-outcome condition detected during analysis."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WorkCounter:
    def __init__(self) -> None:
        self.value = 0

    def charge(self, units: int) -> None:
        if units < 0 or self.value + units > WORK_CEILING:
            raise Abort(RESOURCE_BOUND_BREACH)
        self.value += units


class PageStore:
    """Lazy content-addressed page reader with exact hash and size checks."""

    def __init__(self, root: Path, work: WorkCounter) -> None:
        self.root = root
        self.work = work
        self.bytes_read = 0
        self.cache: dict[str, bytes] = {}

    def get(self, digest: str) -> bytes:
        retained = self.cache.get(digest)
        if retained is not None:
            return retained
        path = self.root / f"{digest}.page"
        try:
            metadata = path.lstat()
            if path.is_symlink() or metadata.st_size != PAGE_SIZE:
                raise Abort(UNRECONSTRUCTABLE_SNAPSHOT)
            retained = path.read_bytes()
        except OSError as exc:
            raise Abort(UNRECONSTRUCTABLE_SNAPSHOT) from exc
        self.bytes_read += PAGE_SIZE
        if self.bytes_read > RETAINED_PAGE_STORE_CEILING:
            raise Abort(RESOURCE_BOUND_BREACH)
        self.work.charge(PAGE_SIZE)
        if hashlib.sha256(retained).hexdigest() != digest:
            raise Abort(UNRECONSTRUCTABLE_SNAPSHOT)
        if len(self.cache) >= PAGE_CACHE_ENTRIES:
            self.cache.clear()
        self.cache[digest] = retained
        return retained


@dataclass(frozen=True)
class ReplicaIndexes:
    observation: dict[str, Any]
    indexes: dict[str, dict[str, Any]]


class ReplicaView:
    """Checkpoint-ordered physical view of one replica."""

    def __init__(self, replica: ReplicaIndexes, store: PageStore, work: WorkCounter) -> None:
        self.replica = replica
        self.store = store
        self.work = work
        self._counts = {name: len(self._hashes(name)) for name in CHECKPOINT_IDS}
        if max(self._counts.values()) > FINAL_PAGE_CEILING:
            raise Abort(RESOURCE_BOUND_BREACH)

    def _hashes(self, checkpoint: str) -> list[str]:
        return self.replica.indexes[checkpoint]["ordered_page_sha256"]

    def page_count(self, checkpoint: str) -> int:
        return self._counts[checkpoint]

    def hash_at(self, checkpoint: str, page: int) -> str | None:
        values = self._hashes(checkpoint)
        return values[page] if page < len(values) else None

    def page(self, checkpoint: str, page: int) -> bytes:
        digest = self.hash_at(checkpoint, page)
        if digest is None:
            raise Abort(UNRECONSTRUCTABLE_SNAPSHOT)
        return self.store.get(digest)

    def idle_pairs_identical(self) -> bool:
        for left, right in IDLE_PAIRS:
            self.work.charge(1)
            if self._hashes(left) != self._hashes(right):
                return False
        return True


@dataclass(frozen=True)
class Derivation:
    record_page: int
    record_start: int
    record_end: int
    map_type_offset: int
    conversion_ordinal: int
    inline_boundary: int
    inline_pages: frozenset[int]
    low_slot: int
    high_slot: int
    low_reference_page: int
    high_reference_page: int
    used: dict[str, frozenset[int]]
    free: dict[str, frozenset[int]]
    bases: frozenset[str]


def _set_bits(value: int) -> Iterator[int]:
    while value:
        lowest = value & -value
        yield lowest.bit_length() - 1
        value ^= lowest


def decode_pointer(raw: bytes, layout: str) -> tuple[int, int]:
    if layout == "u24le_page_then_u8_slot":
        return int.from_bytes(raw[:3], "little"), raw[3]
    return int.from_bytes(raw[1:], "little"), raw[0]


def _page_tracks_allocation(view: ReplicaView, page: int) -> bool:
    """The plan's page-one transition predicates, applied to any physical page."""
    empty = view.hash_at("E0R", page)
    grown = view.hash_at("D_GROW_0128", page)
    dropped = view.hash_at("D_DROP", page)
    regrown = view.hash_at("D_REGROW_0128", page)
    low_first = view.hash_at("L_REL_0064", page)
    low_last = view.hash_at("L_REL_1280", page)
    high_first = view.hash_at("H_REL_0064", page)
    high_last = view.hash_at("H_REL_1280", page)
    if None in (empty, grown, dropped, regrown, low_first, low_last, high_first, high_last):
        return False
    aba = grown == regrown and grown != dropped and grown != empty
    return aba and low_first != low_last and high_first != high_last


def surviving_record_pages(view: ReplicaView) -> set[int]:
    observed = max(view.page_count(name) for name in CHECKPOINT_IDS)
    surviving: set[int] = set()
    for page in range(observed):
        view.work.charge(8)
        if _page_tracks_allocation(view, page):
            surviving.add(page)
    return surviving


def _record_interval(view: ReplicaView, page: int) -> tuple[int, int]:
    lowest = PAGE_SIZE
    highest = -1
    for left, right in ALL_TRANSITIONS:
        view.work.charge(1)
        if view.hash_at(left, page) == view.hash_at(right, page):
            continue
        before = view.page(left, page)
        after = view.page(right, page)
        first = 0
        while before[first] == after[first]:
            first += 1
        last = PAGE_SIZE - 1
        while before[last] == after[last]:
            last -= 1
        lowest = min(lowest, first)
        highest = max(highest, last)
    if highest < 0 or highest + 1 - lowest < INLINE_START_PAGE_WIDTH + 1:
        raise Abort(AMBIGUOUS_RECORD_BOUNDARY)
    return lowest, highest + 1


def _map_type_offset(view: ReplicaView, page: int, interval: tuple[int, int]) -> tuple[int, int]:
    start, end = interval
    records = [view.page(name, page)[start:end] for name in CHECKPOINT_IDS]
    candidates: list[tuple[int, int]] = []
    for offset in range(start, end):
        view.work.charge(len(CHECKPOINT_IDS))
        column = [record[offset - start] for record in records]
        if column[0] != INLINE_TAG or INDIRECT_TAG not in column:
            continue
        first = column.index(INDIRECT_TAG)
        if first == 0:
            continue
        if any(value != INLINE_TAG for value in column[:first]):
            continue
        if any(value != INDIRECT_TAG for value in column[first:]):
            continue
        candidates.append((offset, first))
    if not candidates:
        raise Abort(MISSING_CONVERSION)
    if len(candidates) > 1:
        raise Abort(AMBIGUOUS_RECORD_BOUNDARY)
    return candidates[0]


def _inline_extent(
    view: ReplicaView,
    page: int,
    interval: tuple[int, int],
    type_offset: int,
    conversion_ordinal: int,
) -> tuple[int, frozenset[int]]:
    _, end = interval
    bitmap_start = type_offset + 1 + INLINE_START_PAGE_WIDTH
    if bitmap_start >= end:
        raise Abort(AMBIGUOUS_INLINE_BOUNDARY)
    anchor = CHECKPOINT_IDS[conversion_ordinal - 1]
    record = view.page(anchor, page)
    view.work.charge(end - bitmap_start)
    nonzero = [index for index in range(bitmap_start, end) if record[index]]
    if not nonzero:
        raise Abort(AMBIGUOUS_INLINE_BOUNDARY)
    boundary = nonzero[-1] + 1
    for name in CHECKPOINT_IDS[:conversion_ordinal]:
        view.work.charge(end - boundary)
        if any(view.page(name, page)[boundary:end]):
            raise Abort(UNEXPLAINED_INLINE_SUFFIX)
    first_page = int.from_bytes(record[type_offset + 1 : bitmap_start], "little")
    count = view.page_count(anchor)
    pages: set[int] = set()
    for index in range(bitmap_start, boundary):
        view.work.charge(8)
        for bit in _set_bits(record[index]):
            mapped = first_page + (index - bitmap_start) * 8 + bit
            if not 0 <= mapped < count:
                raise Abort(AMBIGUOUS_INLINE_BOUNDARY)
            pages.add(mapped)
    if not pages:
        raise Abort(AMBIGUOUS_INLINE_BOUNDARY)
    return boundary, frozenset(pages)


def _pointer_candidates(
    view: ReplicaView, page: int, interval: tuple[int, int]
) -> tuple[dict[str, frozenset[int]], dict[str, frozenset[int]]]:
    start, end = interval
    records = {name: view.page(name, page)[start:end] for name in POINTER_SCOPE}
    counts = {name: view.page_count(name) for name in POINTER_SCOPE}
    used: dict[str, set[int]] = {layout: set() for layout in POINTER_LAYOUTS}
    free: dict[str, set[int]] = {layout: set() for layout in POINTER_LAYOUTS}
    for offset in range(start, end - POINTER_WIDTH + 1):
        relative = offset - start
        windows = {
            name: record[relative : relative + POINTER_WIDTH] for name, record in records.items()
        }
        for layout in POINTER_LAYOUTS:
            view.work.charge(len(POINTER_SCOPE))
            if not all(
                1 <= decode_pointer(window, layout)[0] < counts[name]
                for name, window in windows.items()
            ):
                continue
            grows = any(windows[left] != windows[right] for left, right in GROWTH_TRANSITIONS)
            churns = any(windows[left] != windows[right] for left, right in CHURN_TRANSITIONS)
            if grows and not churns:
                used[layout].add(offset)
            elif churns and not grows:
                free[layout].add(offset)
    return (
        {layout: frozenset(offsets) for layout, offsets in used.items()},
        {layout: frozenset(offsets) for layout, offsets in free.items()},
    )


def _active_slots(
    view: ReplicaView, page: int, interval: tuple[int, int], type_offset: int, checkpoint: str
) -> dict[int, int]:
    start, end = interval
    array_start = type_offset + 1
    slots = (end - array_start) // POINTER_WIDTH
    record = view.page(checkpoint, page)
    view.work.charge(slots + 1)
    if any(record[array_start + slots * POINTER_WIDTH : end]):
        raise Abort(AMBIGUOUS_RECORD_BOUNDARY)
    active: dict[int, int] = {}
    for slot in range(slots):
        offset = array_start + slot * POINTER_WIDTH
        value = int.from_bytes(record[offset : offset + POINTER_WIDTH], "little")
        if value:
            active[slot] = value
    return active


def _type1_slots(
    view: ReplicaView,
    page: int,
    interval: tuple[int, int],
    type_offset: int,
    conversion_ordinal: int,
) -> tuple[int, int, int, int]:
    indirect = CHECKPOINT_IDS[conversion_ordinal:]
    low_phase = [name for name in indirect if ORDINAL[name] <= ORDINAL[LAST_L_CHECKPOINT]]
    if not low_phase:
        raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    low_active = _active_slots(view, page, interval, type_offset, low_phase[-1])
    if len(low_active) != 1:
        raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    low_slot = next(iter(low_active))
    final_active = _active_slots(view, page, interval, type_offset, FINAL_CHECKPOINT)
    if len(final_active) != 2 or low_slot not in final_active:
        raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    high_slot = max(final_active)
    if high_slot != low_slot + 1:
        raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    for name in indirect:
        active = _active_slots(view, page, interval, type_offset, name)
        if set(active) - {low_slot, high_slot}:
            raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
        for reference in active.values():
            if not 1 <= reference < view.page_count(name):
                raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
            if view.page(name, reference)[0] != EXTENDED_MAP_TAG:
                raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    return low_slot, high_slot, final_active[low_slot], final_active[high_slot]


def extended_base(formula: str, slot: int, reference_page: int) -> int:
    if formula == "slot_relative_expected_0_16352":
        return slot * EXTENDED_MAP_BITS
    if formula == "slot_relative_off_by_minus_one":
        return slot * EXTENDED_MAP_BITS - 1
    if formula == "slot_relative_off_by_plus_one":
        return slot * EXTENDED_MAP_BITS + 1
    if formula == "referenced_page_relative":
        return reference_page
    if formula == "referenced_page_relative_off_by_minus_one":
        return reference_page - 1
    return reference_page + 1


def _extended_bitmaps(
    view: ReplicaView, page: int, interval: tuple[int, int], type_offset: int, checkpoint: str
) -> dict[int, tuple[int, int]]:
    result: dict[int, tuple[int, int]] = {}
    for slot, reference in _active_slots(view, page, interval, type_offset, checkpoint).items():
        bitmap = view.page(checkpoint, reference)[EXTENDED_MAP_HEADER_BYTES:]
        result[slot] = (reference, int.from_bytes(bitmap, "little"))
    return result


def _base_candidates(
    view: ReplicaView,
    page: int,
    interval: tuple[int, int],
    type_offset: int,
    conversion_ordinal: int,
    inline_pages: frozenset[int],
) -> frozenset[str]:
    indirect = CHECKPOINT_IDS[conversion_ordinal:]
    bitmaps = {
        name: _extended_bitmaps(view, page, interval, type_offset, name) for name in indirect
    }
    survivors = set(BASE_FORMULAS)
    conversion = indirect[0]
    for formula in sorted(survivors):
        covered: set[int] = set()
        for slot, (reference, bits) in bitmaps[conversion].items():
            origin = extended_base(formula, slot, reference)
            view.work.charge(bits.bit_count())
            covered.update(origin + bit for bit in _set_bits(bits))
        if not inline_pages <= covered:
            survivors.discard(formula)
    exact_evidence = False
    for left, right in GROWTH_TRANSITIONS:
        if ORDINAL[left] < conversion_ordinal:
            continue
        before = bitmaps[left]
        after = bitmaps[right]
        delta = view.page_count(right) - view.page_count(left)
        fresh = {
            slot: bits & ~before.get(slot, (0, 0))[1] for slot, (_, bits) in after.items()
        }
        new_bits = sum(value.bit_count() for value in fresh.values())
        view.work.charge(new_bits + 1)
        exact = delta > 0 and new_bits == delta
        exact_evidence = exact_evidence or exact
        appended = set(range(view.page_count(left), view.page_count(right)))
        for formula in sorted(survivors):
            predicted = {
                extended_base(formula, slot, after[slot][0]) + bit
                for slot, value in fresh.items()
                for bit in _set_bits(value)
            }
            if exact:
                if predicted != appended:
                    survivors.discard(formula)
            elif any(not 0 <= mapped < view.page_count(right) for mapped in predicted):
                survivors.discard(formula)
        if not survivors:
            break
    if not exact_evidence:
        raise Abort(INCOMPLETE_TRANSITION_EVIDENCE)
    return frozenset(survivors)


def derive(view: ReplicaView) -> Derivation:
    """Derive the complete three-layer candidate state of one replica."""
    if not view.idle_pairs_identical():
        raise Abort(IDLE_VOLATILITY)
    pages = surviving_record_pages(view)
    if len(pages) != 1:
        raise Abort(AMBIGUOUS_RECORD_BOUNDARY)
    page = next(iter(pages))
    if page != METADATA_PAGE:
        raise Abort(AMBIGUOUS_RECORD_BOUNDARY)
    interval = _record_interval(view, page)
    type_offset, conversion_ordinal = _map_type_offset(view, page, interval)
    boundary, inline_pages = _inline_extent(view, page, interval, type_offset, conversion_ordinal)
    used, free = _pointer_candidates(view, page, interval)
    low_slot, high_slot, low_reference, high_reference = _type1_slots(
        view, page, interval, type_offset, conversion_ordinal
    )
    bases = _base_candidates(
        view, page, interval, type_offset, conversion_ordinal, inline_pages
    )
    return Derivation(
        record_page=page,
        record_start=interval[0],
        record_end=interval[1],
        map_type_offset=type_offset,
        conversion_ordinal=conversion_ordinal,
        inline_boundary=boundary,
        inline_pages=inline_pages,
        low_slot=low_slot,
        high_slot=high_slot,
        low_reference_page=low_reference,
        high_reference_page=high_reference,
        used=used,
        free=free,
        bases=bases,
    )


def joint_shape(derivation: Derivation) -> tuple[int, int, int, int, int, int, int]:
    """The replica-independent part of a joint model."""
    return (
        derivation.record_page,
        derivation.record_start,
        derivation.record_end,
        derivation.map_type_offset,
        derivation.inline_boundary,
        derivation.low_slot,
        derivation.high_slot,
    )


@dataclass(frozen=True)
class JointCandidates:
    examined: int
    survivors: int
    pairs: dict[str, tuple[frozenset[int], frozenset[int]]]
    bases: frozenset[str]


def candidate_counts(first: Derivation, second: Derivation, ceiling: int) -> JointCandidates:
    """Intersect the derivation replicas and count examined and surviving models."""
    pairs: dict[str, tuple[frozenset[int], frozenset[int]]] = {}
    bases = first.bases & second.bases
    examined = 0
    combinations = 0
    for layout in POINTER_LAYOUTS:
        used = first.used[layout] & second.used[layout]
        free = first.free[layout] & second.free[layout]
        pairs[layout] = (used, free)
        examined += len(used) * len(free) * len(BASE_FORMULAS)
        if examined > ceiling:
            raise Abort(RESOURCE_BOUND_BREACH)
        combinations += len(used) * len(free) - len(used & free)
    return JointCandidates(
        examined=examined, survivors=combinations * len(bases), pairs=pairs, bases=bases
    )


def sole_model(derivation: Derivation, candidates: JointCandidates) -> dict[str, Any]:
    """Build the single surviving joint model; the caller has checked uniqueness."""
    selected = [
        (layout, used, free)
        for layout, (used_set, free_set) in candidates.pairs.items()
        for used in sorted(used_set)
        for free in sorted(free_set)
        if used != free
    ]
    if len(selected) != 1 or len(candidates.bases) != 1:
        raise Abort(MULTIPLE_SURVIVING_MODELS)
    layout, used, free = selected[0]
    return {
        "metadata_page": derivation.record_page,
        "record_start": derivation.record_start,
        "record_end": derivation.record_end,
        "pointer_layout": layout,
        "used_pointer_offset": used,
        "free_pointer_offset": free,
        "inline_boundary": derivation.inline_boundary,
        "low_type1_slot": derivation.low_slot,
        "high_type1_slot": derivation.high_slot,
        "low_reference_page": derivation.low_reference_page,
        "high_reference_page": derivation.high_reference_page,
        "extended_base_formula": next(iter(candidates.bases)),
    }


def predicts_holdout(holdout: Derivation, model: dict[str, Any]) -> bool:
    """Evaluate the frozen model against the holdout without refitting it."""
    layout = model["pointer_layout"]
    return (
        holdout.record_page == model["metadata_page"]
        and holdout.record_start == model["record_start"]
        and holdout.record_end == model["record_end"]
        and holdout.inline_boundary == model["inline_boundary"]
        and holdout.low_slot == model["low_type1_slot"]
        and holdout.high_slot == model["high_type1_slot"]
        and model["used_pointer_offset"] in holdout.used[layout]
        and model["free_pointer_offset"] in holdout.free[layout]
        and model["extended_base_formula"] in holdout.bases
    )
