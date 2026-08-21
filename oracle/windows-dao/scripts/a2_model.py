#!/usr/bin/env python3
"""Bounded record derivation primitives for DAO-A2-ALLOCATION-MAPS-001.

The model reads its constants and predicate registry from the hash-pinned A2
plan.  It intentionally knows nothing about the bundle layout; generators and
on-disk readers meet the same small :class:`ReplicaData` protocol.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from protocol_validation import ValidationError

EXPERIMENT_DIR = Path(__file__).resolve().parents[1] / "experiments" / "a2"
PLAN_PATH = EXPERIMENT_DIR / "a2-allocation-maps.plan.json"
PLAN_BYTES = PLAN_PATH.read_bytes()
PLAN = json.loads(PLAN_BYTES)
PLAN_SHA256 = hashlib.sha256(PLAN_BYTES).hexdigest()
EXPECTED_PLAN_SHA256 = "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"
if PLAN_SHA256 != EXPECTED_PLAN_SHA256:
    raise RuntimeError("A2 analyzer plan hash does not match the preregistration")

PAGE_SIZE = int(PLAN["bounds"]["page_size"])
MAX_FINAL_PAGES = int(PLAN["bounds"]["max_final_pages_per_replica"])
MAX_QUALIFIED_PAGES = int(PLAN["bounds"]["max_qualified_pages_per_submodel"])
MAX_RECORD_CANDIDATES = int(PLAN["bounds"]["max_record_candidates"])
MAX_CANDIDATE_MODELS = int(PLAN["bounds"]["max_candidate_models"])
MAX_WORK_UNITS = int(PLAN["bounds"]["max_analysis_work_units"])
MAX_PAGE_BLOBS = int(PLAN["bounds"]["max_unique_page_blobs"])
MAX_PAGE_BYTES = int(PLAN["bounds"]["max_retained_page_store_bytes"])
CHECKPOINT_IDS = tuple(PLAN["checkpoint_design"]["checkpoint_ids"])
IDLE_PAIRS = tuple(tuple(pair) for pair in PLAN["checkpoint_design"]["idle_pairs"])
TRANSITIONS = PLAN["checkpoint_design"]["transition_coverage"]
POLARITIES = tuple(PLAN["hypotheses"]["bit_polarity_candidates"])
POINTER_LAYOUTS = tuple(PLAN["hypotheses"]["tdef_pointer_layouts"])
BASE_FORMULAS = tuple(PLAN["hypotheses"]["extended_base_candidates"])
PER_PAGE_CANDIDATES = PAGE_SIZE * (PAGE_SIZE + 1) // 2

_MAPPINGS = PLAN["predicate_registry"]["mappings"]
PREDICATES = {item["predicate_id"]: (item["reason"], item["layer"]) for item in _MAPPINGS}
REASONS = {reason: predicate for predicate, (reason, _) in PREDICATES.items()}
if (
    len(PREDICATES) != len(_MAPPINGS)
    or len(REASONS) != len(_MAPPINGS)
    or tuple(PREDICATES) != tuple(PLAN["predicate_registry"]["ids"])
):
    raise RuntimeError("A2 predicate registry is not bijective")


class ReplicaData(Protocol):
    """The complete physical shape consumed from a replica or generator."""

    @property
    def checkpoint_ids(self) -> Sequence[str]: ...

    @property
    def page_count(self) -> Mapping[str, int]: ...

    @property
    def ordered_page_sha256(self) -> Mapping[str, Sequence[str]]: ...

    def page_bytes(self, sha256: str) -> bytes: ...


class Abort(Exception):
    """One preregistered terminal, carrying exactly one predicate mapping."""

    def __init__(self, predicate_id: str) -> None:
        try:
            self.reason, self.registered_layer = PREDICATES[predicate_id]
        except KeyError as exc:
            raise ValueError(f"unregistered A2 predicate {predicate_id!r}") from exc
        self.predicate_id = predicate_id
        super().__init__(f"{predicate_id}: {self.reason}")


class WorkCounter:
    """Fail-closed accounting for analyzer work and opened page blobs."""

    def __init__(self) -> None:
        self.value = 0
        self.record_candidates = 0
        self.candidate_models = 0
        self.page_digests: set[str] = set()
        self.page_bytes_read = 0

    def charge(self, units: int) -> None:
        if units < 0 or self.value + units > MAX_WORK_UNITS:
            raise Abort("A2-RESOURCE-BOUND")
        self.value += units

    def enumerate_intervals(self) -> None:
        if self.record_candidates + PER_PAGE_CANDIDATES > MAX_RECORD_CANDIDATES:
            raise Abort("A2-RESOURCE-BOUND")
        self.record_candidates += PER_PAGE_CANDIDATES
        self.charge(PER_PAGE_CANDIDATES * 8)

    def examine_models(self, count: int = 1) -> None:
        if count < 0 or self.candidate_models + count > MAX_CANDIDATE_MODELS:
            raise Abort("A2-RESOURCE-BOUND")
        self.candidate_models += count

    def opened(self, digest: str) -> None:
        if digest in self.page_digests:
            return
        if (
            len(self.page_digests) >= MAX_PAGE_BLOBS
            or self.page_bytes_read + PAGE_SIZE > MAX_PAGE_BYTES
        ):
            raise Abort("A2-RESOURCE-BOUND")
        self.page_digests.add(digest)
        self.page_bytes_read += PAGE_SIZE


class View:
    """Checked, checkpoint-ordered access to a :class:`ReplicaData`."""

    def __init__(self, source: ReplicaData, work: WorkCounter) -> None:
        if tuple(source.checkpoint_ids) != CHECKPOINT_IDS:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
        self.source = source
        self.work = work
        self._counts: dict[str, int] = {}
        self._hashes: dict[str, tuple[str, ...]] = {}
        for checkpoint in CHECKPOINT_IDS:
            try:
                count = source.page_count[checkpoint]
                hashes = tuple(source.ordered_page_sha256[checkpoint])
            except (KeyError, TypeError) as exc:
                raise Abort("A2-SNAPSHOT-RECONSTRUCTION") from exc
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 1 <= count <= MAX_FINAL_PAGES
                or count != len(hashes)
            ):
                raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
            if any(len(item) != 64 for item in hashes):
                raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
            self._counts[checkpoint] = count
            self._hashes[checkpoint] = hashes

    def page_count(self, checkpoint: str) -> int:
        return self._counts[checkpoint]

    def hashes(self, checkpoint: str) -> tuple[str, ...]:
        return self._hashes[checkpoint]

    def hash_at(self, checkpoint: str, page: int) -> str | None:
        hashes = self._hashes[checkpoint]
        return hashes[page] if 0 <= page < len(hashes) else None

    def page(self, checkpoint: str, page: int) -> bytes:
        digest = self.hash_at(checkpoint, page)
        if digest is None:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
        self.work.opened(digest)
        try:
            payload = self.source.page_bytes(digest)
        except (KeyError, OSError, ValueError, ValidationError) as exc:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION") from exc
        if len(payload) != PAGE_SIZE or hashlib.sha256(payload).hexdigest() != digest:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
        return payload

    def idle_pairs_identical(self) -> bool:
        for left, right in IDLE_PAIRS:
            self.work.charge(1)
            if self.hashes(left) != self.hashes(right):
                return False
        return True


@dataclass(frozen=True)
class Prefix:
    """A fixed-size prefix sum supporting O(1) interval predicates."""

    cells: tuple[int, ...]

    @classmethod
    def from_flags(cls, flags: Sequence[bool], work: WorkCounter) -> Prefix:
        if len(flags) != PAGE_SIZE:
            raise Abort("A2-SNAPSHOT-RECONSTRUCTION")
        cells = [0]
        for flag in flags:
            cells.append(cells[-1] + int(flag))
        work.charge(PAGE_SIZE + 1)
        return cls(tuple(cells))

    def count(self, start: int, end: int) -> int:
        if not 0 <= start <= end <= PAGE_SIZE:
            raise Abort("A2-RESOURCE-BOUND")
        return self.cells[end] - self.cells[start]

    def none(self, start: int, end: int) -> bool:
        return self.count(start, end) == 0


def _pairs(checkpoints: Sequence[str]) -> tuple[tuple[str, str], ...]:
    return tuple(zip(checkpoints, checkpoints[1:], strict=False))


LOW_GROWTH = _pairs(TRANSITIONS["tdef_low_growth"])
HIGH_GROWTH = _pairs(TRANSITIONS["tdef_high_growth"])
GROWTH_TRANSITIONS = LOW_GROWTH + HIGH_GROWTH
CHURN_TRANSITIONS = (
    ("L_REL_1280", "L_DELETE_ALL"),
    ("L_DELETE_ALL", "L_REINSERT_SAME"),
)
D_CHECKPOINTS = tuple(TRANSITIONS["global_map_record_set_abac"])
D_TRANSITIONS = _pairs(D_CHECKPOINTS)


def candidate_page_space(views: Sequence[View]) -> range:
    maximum = max(view.page_count(checkpoint) for view in views for checkpoint in CHECKPOINT_IDS)
    if maximum > MAX_FINAL_PAGES:
        raise Abort("A2-RESOURCE-BOUND")
    return range(maximum)


def qualify_global_pages(view: View, pages: Sequence[int]) -> tuple[int, ...]:
    """Hash-only global qualification; this function never opens a page blob."""
    qualified = tuple(
        page
        for page in pages
        if view.hash_at("E0", page) != view.hash_at("D_GROW_0128", page)
        and view.hash_at("D_GROW_0128", page) != view.hash_at("D_DROP", page)
    )
    view.work.charge(len(pages) * 2)
    if len(qualified) > MAX_QUALIFIED_PAGES:
        raise Abort("A2-RESOURCE-BOUND")
    return qualified


def qualify_tdef_pages(view: View, pages: Sequence[int]) -> tuple[int, ...]:
    """Hash-only TDEF qualification; this function never opens a page blob."""
    qualified: list[int] = []
    for page in pages:
        if view.hash_at("E0", page) is None:
            continue
        growth = any(view.hash_at(a, page) != view.hash_at(b, page) for a, b in GROWTH_TRANSITIONS)
        churn = all(view.hash_at(a, page) != view.hash_at(b, page) for a, b in CHURN_TRANSITIONS)
        view.work.charge(len(GROWTH_TRANSITIONS) + len(CHURN_TRANSITIONS))
        if growth and churn:
            qualified.append(page)
    if len(qualified) > MAX_QUALIFIED_PAGES:
        raise Abort("A2-RESOURCE-BOUND")
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

    def document(self) -> dict[str, object]:
        return {
            "record": self.record.document(),
            "bit_polarity": self.bit_polarity,
            "zero_suffix_slack_bytes": self.zero_suffix_slack_bytes,
        }


class DRelationIndex:
    """Fixed D byte predicates used by every interval in O(1)."""

    def __init__(self, records: dict[str, bytes], work: WorkCounter) -> None:
        empty = records["E0"]
        grown = records["D_GROW_0128"]
        dropped = records["D_DROP"]
        recreated = records["D_RECREATE_EMPTY"]
        regrown = records["D_REGROW_0128"]
        self.records = records
        self.growth: dict[str, Prefix] = {}
        self.release_violation: dict[str, Prefix] = {}
        self.recreate_release_violation: dict[str, Prefix] = {}
        self.regrowth_violation: dict[str, Prefix] = {}
        self.additional: dict[str, Prefix] = {}
        for polarity in POLARITIES:
            if polarity == "set_means_in_use":
                growth = [grown[index] & ~empty[index] & 0xFF for index in range(PAGE_SIZE)]
                release = [growth[index] & dropped[index] for index in range(PAGE_SIZE)]
                recreate_release = [
                    growth[index] & recreated[index] for index in range(PAGE_SIZE)
                ]
                violation = [growth[index] & ~regrown[index] & 0xFF for index in range(PAGE_SIZE)]
                additional = [regrown[index] & ~grown[index] & 0xFF for index in range(PAGE_SIZE)]
            else:
                growth = [(~grown[index]) & empty[index] & 0xFF for index in range(PAGE_SIZE)]
                release = [growth[index] & ~dropped[index] & 0xFF for index in range(PAGE_SIZE)]
                recreate_release = [
                    growth[index] & ~recreated[index] & 0xFF
                    for index in range(PAGE_SIZE)
                ]
                violation = [growth[index] & regrown[index] for index in range(PAGE_SIZE)]
                additional = [(~regrown[index]) & grown[index] & 0xFF for index in range(PAGE_SIZE)]
            self.growth[polarity] = Prefix.from_flags([bool(value) for value in growth], work)
            self.release_violation[polarity] = Prefix.from_flags(
                [bool(value) for value in release], work
            )
            self.recreate_release_violation[polarity] = Prefix.from_flags(
                [bool(value) for value in recreate_release], work
            )
            self.regrowth_violation[polarity] = Prefix.from_flags(
                [bool(value) for value in violation], work
            )
            self.additional[polarity] = Prefix.from_flags(
                [bool(value) for value in additional], work
            )
        changed_flags = [
            len({records[name][index] for name in D_CHECKPOINTS}) > 1
            for index in range(PAGE_SIZE)
        ]
        self.changed = Prefix.from_flags(changed_flags, work)
        self.last_changed_from = [-1] * (PAGE_SIZE + 1)
        last = -1
        for index in range(PAGE_SIZE - 1, -1, -1):
            if last < 0 and changed_flags[index]:
                last = index
            self.last_changed_from[index] = last
        self.bad_unused: dict[str, Prefix] = {}
        for polarity in POLARITIES:
            unused = 0x00 if polarity == "set_means_in_use" else 0xFF
            self.bad_unused[polarity] = Prefix.from_flags(
                [
                    any(records[name][index] != unused for name in D_CHECKPOINTS)
                    for index in range(PAGE_SIZE)
                ],
                work,
            )

    def decoded_in_use_pages(self, start: int, polarity: str, checkpoint: str) -> set[int]:
        record = self.records[checkpoint]
        base = int.from_bytes(record[start + 1 : start + 5], "little")
        bitmap = record[start + 5 :]
        set_means_in_use = polarity == "set_means_in_use"
        return {
            base + byte_index * 8 + bit
            for byte_index, value in enumerate(bitmap)
            for bit in range(8)
            if bool(value & (1 << bit)) == set_means_in_use
        }

    def polarity_direction(
        self,
        start: int,
        polarity: str,
        expected_highwater: Mapping[str, int] | None = None,
    ) -> bool:
        records = self.records
        if start + 5 >= PAGE_SIZE or any(record[start] != 0 for record in records.values()):
            return False
        bases = {
            int.from_bytes(record[start + 1 : start + 5], "little")
            for record in records.values()
        }
        bitmap_start = start + 5
        if len(bases) != 1 or self.growth[polarity].count(bitmap_start, PAGE_SIZE) == 0:
            return False
        if expected_highwater is None:
            return True
        base = next(iter(bases))
        if base >= min(expected_highwater.values()):
            return False
        for checkpoint, highwater in expected_highwater.items():
            pages = self.decoded_in_use_pages(start, polarity, checkpoint)
            if not set(range(base, highwater)) <= pages or highwater in pages:
                return False
        return True

    def relation(
        self,
        start: int,
        polarity: str,
        expected_highwater: Mapping[str, int] | None = None,
    ) -> bool:
        bitmap_start = start + 5
        return (
            self.polarity_direction(start, polarity, expected_highwater)
            and self.release_violation[polarity].none(bitmap_start, PAGE_SIZE)
            and self.recreate_release_violation[polarity].none(bitmap_start, PAGE_SIZE)
            and self.regrowth_violation[polarity].none(bitmap_start, PAGE_SIZE)
            and self.additional[polarity].count(bitmap_start, PAGE_SIZE) > 0
        )

    def suffix_slack(self, start: int, polarity: str) -> int:
        last_flip = self.last_changed_from[start + 5]
        if last_flip < 0:
            return 0
        suffix_start = last_flip + 1
        if not self.bad_unused[polarity].none(suffix_start, PAGE_SIZE):
            return 0
        return PAGE_SIZE - suffix_start


def _d_relation(
    records: dict[str, bytes],
    start: int,
    polarity: str,
    index: DRelationIndex | None = None,
) -> bool:
    """Compatibility wrapper used by pure holdout prediction tests."""
    relation = index or DRelationIndex(records, WorkCounter())
    return relation.relation(start, polarity)


def _suffix_slack(
    records: dict[str, bytes],
    start: int,
    polarity: str,
    work: WorkCounter,
    index: DRelationIndex | None = None,
) -> int:
    relation = index or DRelationIndex(records, work)
    return relation.suffix_slack(start, polarity)


def derive_global_record(
    view: View, page: int, *, enumerate_candidates: bool = True
) -> GlobalRecordModel:
    """Enumerate the fixed interval space and apply the D-only terminal tie-break."""
    if enumerate_candidates:
        view.work.enumerate_intervals()
    records = {name: view.page(name, page) for name in D_CHECKPOINTS}
    index = DRelationIndex(records, view.work)
    expected_highwater = {
        checkpoint: view.page_count(checkpoint)
        for checkpoint in ("E0", "D_GROW_0128", "D_REGROW_0128")
    }
    decodable = [
        start
        for start in range(PAGE_SIZE - 5)
        if all(record[start] == 0 for record in records.values())
    ]
    if not decodable:
        raise Abort("A2-GLOBAL-RECORD-NONE")
    def collect(
        highwater: Mapping[str, int] | None,
    ) -> tuple[list[tuple[int, str]], bool, bool]:
        matches: list[tuple[int, str]] = []
        failed = False
        direction_seen = False
        for start in decodable:
            view.work.examine_models(len(POLARITIES))
            polarities = [
                polarity
                for polarity in POLARITIES
                if index.polarity_direction(start, polarity, highwater)
            ]
            if len(polarities) > 1:
                raise Abort("A2-POLARITY-MULTIPLE")
            if polarities:
                direction_seen = True
                polarity = polarities[0]
                if index.relation(start, polarity, highwater):
                    matches.append((start, polarity))
                else:
                    failed = True
        return matches, failed, direction_seen

    related, set_relation_failed, direction_seen = collect(expected_highwater)
    if not direction_seen:
        related, set_relation_failed, _ = collect(None)
    if not related:
        raise Abort("A2-D-SET-RELATION" if set_relation_failed else "A2-POLARITY-NONE")
    resolved: list[GlobalRecordModel] = []
    for start, polarity in related:
        slack = index.suffix_slack(start, polarity)
        if slack >= 16:
            resolved.append(GlobalRecordModel(Record(page, start, PAGE_SIZE), polarity, slack))
    if not resolved:
        raise Abort("A2-GLOBAL-RECORD-END")
    starts = {model.record.start for model in resolved}
    if len(starts) > 1 or len(resolved) > 1:
        raise Abort("A2-GLOBAL-RECORD-MULTIPLE")
    return resolved[0]


def decode_pointer(raw: bytes, layout: str) -> tuple[int, int]:
    if layout == POINTER_LAYOUTS[0]:
        return int.from_bytes(raw[:3], "little"), raw[3]
    return int.from_bytes(raw[1:], "little"), raw[0]


def extended_base(formula: str, slot: int, reference_page: int) -> int:
    bits = (PAGE_SIZE - 4) * 8
    if formula == "slot_relative_expected_0_16352":
        return slot * bits
    if formula == "slot_relative_off_by_minus_one":
        return slot * bits - 1
    if formula == "slot_relative_off_by_plus_one":
        return slot * bits + 1
    if formula == "referenced_page_relative":
        return reference_page
    if formula == "referenced_page_relative_off_by_minus_one":
        return reference_page - 1
    return reference_page + 1
