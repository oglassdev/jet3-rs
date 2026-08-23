#!/usr/bin/env python3
"""Bounded A3 page/record primitives, implemented from the frozen plan text.

A3 rule | implementation
--- | ---
R3-G10 explicit absence vs reconstruction | :meth:`View.page_optional`
Hash-only page qualification before enumeration | :func:`qualify_global_pages`, :func:`qualify_tdef_pages`
Tag/u32-base/LSB-first bitmap | :func:`decode_inline`
R3-G05 three anchors and unbounded D bases | :func:`global_start_candidates`
R3-G05 full-interval suffix and ordering | :func:`terminal_suffix_slack`, :func:`derive_global_record`
R3-G06 enumerated L/H/P transitions | :data:`GROWTH_TRANSITIONS`
Bounded O(1) prefix queries | :class:`Prefix`, :class:`WorkCounter`
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from protocol_validation import ValidationError
from a3_spec import (
    BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, PLAN, POLARITIES, PREDICATES,
)

MAX_FINAL_PAGES = BOUNDS["max_final_pages_per_replica"]
MAX_QUALIFIED_PAGES = BOUNDS["max_qualified_pages_per_submodel"]
MAX_RECORD_CANDIDATES = BOUNDS["max_record_candidates"]
MAX_CANDIDATE_MODELS = BOUNDS["max_candidate_models"]
MAX_WORK_UNITS = BOUNDS["max_analysis_work_units"]
MAX_PAGE_BLOBS = BOUNDS["max_unique_page_blobs"]
MAX_PAGE_BYTES = BOUNDS["max_retained_page_store_bytes"]
PER_PAGE_CANDIDATES = BOUNDS["max_record_candidates_per_page"]
TRANSITIONS = PLAN.document["checkpoint_design"]["transition_coverage"]
IDLE_PAIRS = tuple(tuple(pair) for pair in PLAN.document["checkpoint_design"]["idle_pairs"])
D_CHECKPOINTS = tuple(TRANSITIONS["global_map_record_set_abac"])


class ReplicaData(Protocol):
    @property
    def checkpoint_ids(self) -> Sequence[str]: ...
    @property
    def page_count(self) -> Mapping[str, int]: ...
    @property
    def ordered_page_sha256(self) -> Mapping[str, Sequence[str]]: ...
    def page_bytes(self, sha256: str) -> bytes: ...


class Abort(Exception):
    """One registered A3 terminal with its stable reason and literal layer."""
    def __init__(self, predicate_id: str, survivor_count: int = 0) -> None:
        try:
            self.reason, self.registered_layer = PREDICATES[predicate_id]
        except KeyError as exc:
            raise ValueError(f"unregistered A3 predicate {predicate_id!r}") from exc
        if isinstance(survivor_count, bool) or not isinstance(survivor_count, int) or survivor_count < 0:
            raise ValueError("A3 survivor count must be a nonnegative integer")
        self.predicate_id = predicate_id
        self.survivor_count = survivor_count
        super().__init__(f"{predicate_id}: {self.reason}")


class WorkCounter:
    def __init__(self) -> None:
        self.value = 0
        self.record_candidates = 0
        self.candidate_models = 0
        self.page_digests: set[str] = set()
        self.page_bytes_read = 0

    def charge(self, units: int) -> None:
        if isinstance(units, bool) or units < 0 or self.value + units > MAX_WORK_UNITS:
            raise Abort("A3-RESOURCE-BOUND")
        self.value += units

    def enumerate_intervals(self) -> None:
        self.enumerate_pages(1)

    def enumerate_pages(self, count: int, *, prefix_arrays_per_page: int = 0) -> None:
        values = (count, prefix_arrays_per_page)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise Abort("A3-RESOURCE-BOUND")
        candidates = count * PER_PAGE_CANDIDATES
        if self.record_candidates + candidates > MAX_RECORD_CANDIDATES:
            raise Abort("A3-RESOURCE-BOUND")
        prefix_cells = count * prefix_arrays_per_page * (PAGE_SIZE + 1)
        self.charge(candidates * 8 + prefix_cells)
        self.record_candidates += candidates

    def examine_models(self, count: int = 1) -> None:
        if count < 0 or self.candidate_models + count > MAX_CANDIDATE_MODELS:
            raise Abort("A3-RESOURCE-BOUND")
        self.candidate_models += count

    def opened(self, digest: str) -> None:
        if digest in self.page_digests:
            return
        if len(self.page_digests) >= MAX_PAGE_BLOBS or self.page_bytes_read + PAGE_SIZE > MAX_PAGE_BYTES:
            raise Abort("A3-RESOURCE-BOUND")
        self.page_digests.add(digest)
        self.page_bytes_read += PAGE_SIZE


class View:
    """Checked snapshot access; page counts are part of the reconstructed state."""
    def __init__(self, source: ReplicaData, work: WorkCounter) -> None:
        if tuple(source.checkpoint_ids) != CHECKPOINT_IDS:
            raise Abort("A3-SNAPSHOT-RECONSTRUCTION")
        self.source, self.work = source, work
        self._counts: dict[str, int] = {}
        self._hashes: dict[str, tuple[str, ...]] = {}
        for checkpoint in CHECKPOINT_IDS:
            try:
                count = source.page_count[checkpoint]
                hashes = tuple(source.ordered_page_sha256[checkpoint])
            except (KeyError, TypeError) as exc:
                raise Abort("A3-SNAPSHOT-RECONSTRUCTION") from exc
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_FINAL_PAGES or count != len(hashes):
                raise Abort("A3-SNAPSHOT-RECONSTRUCTION")
            if any(not isinstance(item, str) or len(item) != 64 for item in hashes):
                raise Abort("A3-SNAPSHOT-RECONSTRUCTION")
            self._counts[checkpoint], self._hashes[checkpoint] = count, hashes

    def page_count(self, checkpoint: str) -> int:
        return self._counts[checkpoint]

    def hashes(self, checkpoint: str) -> tuple[str, ...]:
        return self._hashes[checkpoint]

    def hash_at(self, checkpoint: str, page: int) -> str | None:
        hashes = self._hashes[checkpoint]
        return hashes[page] if 0 <= page < len(hashes) else None

    def page(self, checkpoint: str, page: int) -> bytes:
        payload = self.page_optional(checkpoint, page)
        if payload is None:
            raise IndexError(f"page {page} is absent at {checkpoint}")
        return payload

    def page_optional(self, checkpoint: str, page: int) -> bytes | None:
        """Return None for R3-G10 absence; abort only for corrupt listed state."""
        digest = self.hash_at(checkpoint, page)
        if digest is None:
            return None
        self.work.opened(digest)
        try:
            payload = self.source.page_bytes(digest)
        except (KeyError, OSError, ValueError, ValidationError) as exc:
            raise Abort("A3-SNAPSHOT-RECONSTRUCTION") from exc
        if len(payload) != PAGE_SIZE or hashlib.sha256(payload).hexdigest() != digest:
            raise Abort("A3-SNAPSHOT-RECONSTRUCTION")
        return payload

    def idle_pairs_identical(self) -> bool:
        for left, right in IDLE_PAIRS:
            if self.hashes(left) != self.hashes(right):
                return False
        return True


@dataclass(frozen=True)
class Prefix:
    cells: tuple[int, ...]

    @classmethod
    def from_flags(
        cls,
        flags: Sequence[bool],
        work: WorkCounter,
        *,
        charge_work: bool = True,
    ) -> "Prefix":
        if len(flags) != PAGE_SIZE:
            raise Abort("A3-SNAPSHOT-RECONSTRUCTION")
        cells = [0]
        for flag in flags:
            cells.append(cells[-1] + int(flag))
        if charge_work:
            work.charge(PAGE_SIZE + 1)
        return cls(tuple(cells))

    def count(self, start: int, end: int) -> int:
        if not 0 <= start <= end <= PAGE_SIZE:
            raise Abort("A3-RESOURCE-BOUND")
        return self.cells[end] - self.cells[start]

    def none(self, start: int, end: int) -> bool:
        return self.count(start, end) == 0


def _pairs(checkpoints: Sequence[str]) -> tuple[tuple[str, str], ...]:
    return tuple(zip(checkpoints, checkpoints[1:]))


LOW_GROWTH = _pairs(TRANSITIONS["tdef_low_growth"])
HIGH_GROWTH = _pairs(TRANSITIONS["tdef_high_growth"])
P_GROWTH = (
    ("L_IDLE_REOPEN", "P_ABS_04096"),
    ("P_ABS_04096", "P_ABS_08192"),
    ("P_ABS_08192", "P_ABS_12288"),
    ("P_ABS_12288", "P_ABS_16480"),
)
GROWTH_TRANSITIONS = LOW_GROWTH + HIGH_GROWTH + P_GROWTH
CHURN_TRANSITIONS = (("L_REL_1280", "L_DELETE_ALL"), ("L_DELETE_ALL", "L_REINSERT_SAME"))
D_TRANSITIONS = _pairs(D_CHECKPOINTS)


def candidate_page_space(views: Sequence[View]) -> range:
    maximum = max(view.page_count(checkpoint) for view in views for checkpoint in CHECKPOINT_IDS)
    if maximum > MAX_FINAL_PAGES:
        raise Abort("A3-RESOURCE-BOUND")
    return range(maximum)


def qualify_global_pages(view: View, pages: Sequence[int]) -> tuple[int, ...]:
    qualified = tuple(page for page in pages if view.hash_at("E0", page) != view.hash_at("D_GROW_0128", page) and view.hash_at("D_GROW_0128", page) != view.hash_at("D_DROP", page))
    if len(qualified) > MAX_QUALIFIED_PAGES:
        raise Abort("A3-RESOURCE-BOUND")
    return qualified


def qualify_tdef_pages(view: View, pages: Sequence[int]) -> tuple[int, ...]:
    qualified: list[int] = []
    for page in pages:
        if view.hash_at("E0", page) is None:
            continue
        growth = any(view.hash_at(a, page) != view.hash_at(b, page) for a, b in GROWTH_TRANSITIONS)
        churn = all(view.hash_at(a, page) != view.hash_at(b, page) for a, b in CHURN_TRANSITIONS)
        if growth and churn:
            qualified.append(page)
    if len(qualified) > MAX_QUALIFIED_PAGES:
        raise Abort("A3-RESOURCE-BOUND")
    return tuple(qualified)


@dataclass(frozen=True)
class Record:
    page: int
    start: int
    end: int
    def document(self) -> dict[str, int]:
        return {"page": self.page, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class GlobalRecordModel:
    record: Record
    bit_polarity: str
    zero_suffix_slack_bytes: int
    def document(self) -> dict[str, Any]:
        return {"record": self.record.document(), "bit_polarity": self.bit_polarity, "zero_suffix_slack_bytes": self.zero_suffix_slack_bytes}


@dataclass(frozen=True)
class InlineState:
    tag: int
    base: int
    capacity: int
    in_use: frozenset[int]


@dataclass(frozen=True)
class _InlineBits:
    tag: int
    base: int
    capacity: int
    in_use: int


def raw_not_in_use(polarity: str) -> int:
    return 0x00 if polarity == "set_means_in_use" else 0xFF


def decode_inline(payload: bytes, start: int, end: int, polarity: str) -> InlineState | None:
    if polarity not in POLARITIES or not 0 <= start < start + 5 < end <= PAGE_SIZE:
        return None
    base = int.from_bytes(payload[start + 1:start + 5], "little")
    bitmap = payload[start + 5:end]
    means_in_use = polarity == "set_means_in_use"
    pages = frozenset(
        base + byte_index * 8 + bit
        for byte_index, value in enumerate(bitmap)
        for bit in range(8)
        if bool(value & (1 << bit)) == means_in_use
    )
    return InlineState(payload[start], base, len(bitmap) * 8, pages)


def _decode_inline_bits(payload: bytes, start: int, polarity: str) -> _InlineBits:
    bitmap = payload[start + 5:PAGE_SIZE]
    capacity = len(bitmap) * 8
    raw = int.from_bytes(bitmap, "little")
    in_use = raw if polarity == "set_means_in_use" else raw ^ ((1 << capacity) - 1)
    return _InlineBits(
        payload[start], int.from_bytes(payload[start + 1:start + 5], "little"),
        capacity, in_use,
    )


def _bits_highwater_valid(state: _InlineBits, page_count: int) -> bool:
    relative = page_count - state.base
    if state.tag != 0 or not 0 <= state.base <= page_count or not 0 <= relative < state.capacity:
        return False
    lower = (1 << relative) - 1
    return state.in_use & lower == lower and not state.in_use & (1 << relative)


def _bits_d_relation(states: Mapping[str, _InlineBits]) -> bool:
    empty, grown = states["E0"], states["D_GROW_0128"]
    dropped, recreated, regrown = (
        states["D_DROP"], states["D_RECREATE_EMPTY"], states["D_REGROW_0128"]
    )

    def in_use(state: _InlineBits, page: int) -> bool:
        relative = page - state.base
        return 0 <= relative < state.capacity and bool(state.in_use & (1 << relative))

    def pages(state: _InlineBits):
        bits = state.in_use
        while bits:
            relative = (bits & -bits).bit_length() - 1
            yield state.base + relative
            bits &= bits - 1

    growth = [page for page in pages(grown) if not in_use(empty, page)]
    return (
        bool(growth)
        and not any(in_use(dropped, page) for page in growth)
        and not any(in_use(recreated, page) for page in growth)
        and all(in_use(regrown, page) for page in growth)
        and any(not in_use(grown, page) for page in pages(regrown))
    )


def inline_highwater_valid(state: InlineState | None, page_count: int) -> bool:
    if state is None or state.tag != 0 or not 0 <= state.base <= page_count < state.base + state.capacity:
        return False
    return all(page in state.in_use for page in range(state.base, page_count)) and page_count not in state.in_use


def d_set_relation(states: Mapping[str, InlineState]) -> bool:
    empty, grown = states["E0"].in_use, states["D_GROW_0128"].in_use
    dropped, recreated, regrown = states["D_DROP"].in_use, states["D_RECREATE_EMPTY"].in_use, states["D_REGROW_0128"].in_use
    growth = grown - empty
    return bool(growth) and not growth & dropped and not growth & recreated and growth <= regrown and bool(regrown - grown)


def terminal_suffix_slack(records: Mapping[str, bytes], start: int, polarity: str) -> int:
    changed = [
        offset
        for offset in range(start, PAGE_SIZE)
        if len({record[offset] for record in records.values()}) > 1
    ]
    if not changed:
        return 0
    suffix_start = changed[-1] + 1
    expected = raw_not_in_use(polarity)
    if any(record[offset] != expected for record in records.values() for offset in range(suffix_start, PAGE_SIZE)):
        return 0
    return PAGE_SIZE - suffix_start


def global_start_candidates(view: View, page: int, *, enumerate_candidates: bool = True) -> tuple[list[GlobalRecordModel], dict[str, bool]]:
    """Return full-end candidates; terminal end selection precedes polarity uniqueness."""
    if enumerate_candidates:
        view.work.enumerate_intervals()
    records = {checkpoint: view.page_optional(checkpoint, page) for checkpoint in D_CHECKPOINTS}
    if any(payload is None for payload in records.values()):
        return [], {"layout": False, "anchor": False, "relation": False, "suffix": False}
    checked_records = {name: payload for name, payload in records.items() if payload is not None}
    anchors = ("E0", "D_GROW_0128", "D_REGROW_0128")
    evidence = {"layout": False, "anchor": False, "relation": False, "suffix": False}
    models: list[GlobalRecordModel] = []
    for start in range(PAGE_SIZE - 5):
        if not all(checked_records[name][start] == 0 for name in anchors):
            continue
        evidence["layout"] = True
        for polarity in POLARITIES:
            view.work.examine_models()
            states = {
                name: _decode_inline_bits(payload, start, polarity)
                for name, payload in checked_records.items()
            }
            if not all(_bits_highwater_valid(states[name], view.page_count(name)) for name in anchors):
                continue
            evidence["anchor"] = True
            if not _bits_d_relation(states):
                continue
            evidence["relation"] = True
            slack = terminal_suffix_slack(checked_records, start, polarity)
            if slack < 16:
                continue
            evidence["suffix"] = True
            models.append(GlobalRecordModel(Record(page, start, PAGE_SIZE), polarity, slack))
    return models, evidence


def frozen_global_model_holds(view: View, frozen: GlobalRecordModel) -> bool:
    """R3-G09 re-check one frozen record/polarity without uniqueness refit."""
    if frozen.record.end != PAGE_SIZE or frozen.bit_polarity not in POLARITIES:
        return False
    records = {
        checkpoint: view.page_optional(checkpoint, frozen.record.page)
        for checkpoint in D_CHECKPOINTS
    }
    if any(payload is None for payload in records.values()):
        return False
    checked = {name: payload for name, payload in records.items() if payload is not None}
    start = frozen.record.start
    anchors = ("E0", "D_GROW_0128", "D_REGROW_0128")
    if not all(checked[name][start] == 0 for name in anchors):
        return False
    states = {
        name: _decode_inline_bits(payload, start, frozen.bit_polarity)
        for name, payload in checked.items()
    }
    return (
        all(_bits_highwater_valid(states[name], view.page_count(name)) for name in anchors)
        and _bits_d_relation(states)
        and terminal_suffix_slack(checked, start, frozen.bit_polarity) >= 16
    )


def derive_global_record(view: View, page: int, *, enumerate_candidates: bool = True) -> GlobalRecordModel:
    models, evidence = global_start_candidates(view, page, enumerate_candidates=enumerate_candidates)
    if not models:
        if not evidence["anchor"]:
            raise Abort("A3-GLOBAL-RECORD-NONE")
        if not evidence["relation"]:
            raise Abort("A3-D-SET-RELATION")
        raise Abort("A3-GLOBAL-RECORD-END")
    pairs = {(model.record.start, model.bit_polarity) for model in models}
    polarities = {polarity for _, polarity in pairs}
    if not polarities:
        raise Abort("A3-POLARITY-NONE")
    if len(polarities) > 1:
        raise Abort("A3-POLARITY-MULTIPLE", len(pairs))
    starts = {start for start, _ in pairs}
    if len(starts) > 1:
        raise Abort("A3-GLOBAL-RECORD-MULTIPLE", len(starts))
    return models[0]


T = TypeVar("T")


def resolve_page_models(survivors: Mapping[int, Sequence[T]], page_multiple: str, record_multiple: str) -> T | None:
    """Apply the plan's page-multiplicity test before within-page multiplicity."""
    nonempty = {page: tuple(values) for page, values in survivors.items() if values}
    if len(nonempty) > 1:
        raise Abort(page_multiple, sum(len(values) for values in nonempty.values()))
    if not nonempty:
        return None
    values = next(iter(nonempty.values()))
    if len(values) > 1:
        raise Abort(record_multiple, len(values))
    return values[0]


def decode_pointer(raw: bytes, layout: str) -> tuple[int, int]:
    if len(raw) != 4:
        raise ValueError("pointer window must be four bytes")
    if layout == "u24le_page_then_u8_slot":
        return int.from_bytes(raw[:3], "little"), raw[3]
    if layout == "u8_slot_then_u24le_page":
        return int.from_bytes(raw[1:], "little"), raw[0]
    raise ValueError(f"unknown pointer layout {layout!r}")


def extended_base(formula: str, slot: int, reference: int) -> int:
    bits = (PAGE_SIZE - 4) * 8
    return {
        "slot_relative_expected_0_16352": slot * bits,
        "slot_relative_off_by_minus_one": slot * bits - 1,
        "slot_relative_off_by_plus_one": slot * bits + 1,
        "referenced_page_relative": reference,
        "referenced_page_relative_off_by_minus_one": reference - 1,
        "referenced_page_relative_off_by_plus_one": reference + 1,
    }[formula]
