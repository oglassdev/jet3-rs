#!/usr/bin/env python3
"""Schedule-derived, non-evidential A3 replica synthesis (page bytes only).

A3 rule | implementation
--- | ---
Every free parameter comes from the checked plan | :class:`SyntheticParameters`
Tag/base/LSB-first inline map, highwaters, page_count sentinel | :func:`_inline_record`
Tag-1 two-slot indirect record with raw-zero suffix | :func:`_indirect_record`
D A/B/A/C relation with strictly larger regrowth | :func:`_global_in_use`
R3-G01 extended bitmaps with a slot-0 discriminating flip | :func:`_extended_page`
Growth-only / full-delete churn-only TDEF windows (R3-G06) | :func:`_tdef_page`
Independent per-replica overshoot walks (R3 honesty clause) | :func:`generate_replica`
Single named perturbation per reachability fixture | :data:`PERTURBATIONS`
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping

from protocol_validation import ValidationError
from a3_generator_schedule import (
    ANCHOR_FILL_UNITS, REPLICA_PROFILES, Schedule, build_schedule, e0_baseline_pages,
)
from a3_spec import CHECKPOINT_IDS, CHECKPOINT_ORDINALS, PAGE_SIZE, PLAN, POLARITIES

FREE = PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
TRANSITIONS = PLAN.document["checkpoint_design"]["transition_coverage"]
IDLE_PAIRS = tuple(tuple(pair) for pair in PLAN.document["checkpoint_design"]["idle_pairs"])
CROSS_CHECK_LEGS = tuple(tuple(pair) for pair in TRANSITIONS["polarity_cross_check_legs"])
GROWTH_CHECKPOINTS = tuple(TRANSITIONS["tdef_low_growth"]) + tuple(TRANSITIONS["tdef_high_growth"])
D_CHECKPOINTS = tuple(TRANSITIONS["global_map_record_set_abac"])
ANCHORS = ("E0", "D_GROW_0128", "D_REGROW_0128")
DISCRIMINATOR_RIGHT = TRANSITIONS["pointer_validity_checkpoints"][1]
EXTENDED_HEADER = 4
EXTENDED_BITS = (PAGE_SIZE - EXTENDED_HEADER) * 8
SLOT_REFERENCES = (14848, 16352)
GLOBAL_PAGE, TDEF_PAGE, SECOND_GLOBAL_PAGE = 1, 2, 3
ALLOC_PAGES = range(4, 32)
DECOY_FIRST_PAGE = 32
GROWTH_TARGET_PAGES = range(4, 20)
CHURN_TARGET_PAGE, CHURN_ALTERNATE_PAGE = 24, 25
GROWTH_OFFSET, CHURN_OFFSET = 0, PAGE_SIZE - 4
POINTER_LAYOUT = "u24le_page_then_u8_slot"
# A deleted-state reference whose only differing byte is the u24 high byte keeps
# the churn signature unique to one window (R3-G06 distinctness by non-overlap).
DELETED_CHURN_REFERENCE = CHURN_TARGET_PAGE + (1 << 16)
FILLER = 0xA5
PROVIDER_SHA256 = hashlib.sha256(b"A3 synthetic DAO provider; no binary exists").hexdigest()

PERTURBATIONS: Mapping[str, str] = {
    "idle_pair_volatile": "E0R carries one byte that differs from E0",
    "missing_page_blob": "one listed page hash has no blob in the page store",
    "seventeen_qualified_pages": "sixteen decoy pages qualify beside the global and tdef pages",
    "sixteen_qualified_pages": "fifteen decoy pages bring both qualified sets to the exact ceiling",
    "global_page_static_on_drop": "the global map does not change on D_GROW_0128 to D_DROP",
    "anchor_highwater_hole": "one page below page_count decodes not-in-use at D_GROW_0128",
    "anchor_sentinel_in_use": "page_count itself decodes in-use at E0",
    "truncated_base_field": "the only tag-0 start leaves fewer than four base bytes",
    "d_drop_keeps_growth_pages": "D_DROP keeps the first-growth pages in use",
    "record_end_not_uniform": "byte 2047 is stably the in-use byte at every D checkpoint",
    "duplicate_global_start": "a second inline record at start 1024 survives on the global page",
    "second_global_page_same_polarity": "page 3 carries an identical global record",
    "second_global_page_opposite_polarity": "page 3 carries a global record of the other polarity",
    "cross_check_first_page_violation": "leg [L_REL_0512,L_REL_0768] misses the first appended page",
    "cross_check_later_page_violation": "leg [P_ABS_04096,P_ABS_08192] misses the fourth appended page",
    "conversion_reverts": "the record returns to tag 0 at H_REL_0064",
    "slot_one_never_activates": "slot-1 stays zero through H_REL_0904",
    "inline_suffix_byte": "byte 2000 is stably the in-use byte at every inline-phase checkpoint",
    "slot_reference_not_0x05": "the slot-0 page carries byte zero 0x01",
    "no_slot0_flip": "no slot-0 bit flips across [P_ABS_16480,H_REL_0064]",
    "extended_self_bit_clear": "the slot-0 map marks its own page not-in-use",
    "extended_off_by_one_ambiguous": "the slot-0 map keeps its neighbours in use so the off-by-one origins also survive",
    "replica_two_converts_earlier": "derivation replica 2 converts at P_ABS_12288",
    "holdout_converts_earlier": "the holdout replica converts at P_ABS_12288",
    "holdout_contradicts_every_layer": "the holdout replica inverts polarity and loses the churn return",
    "tdef_churn_pointer_static": "the churn pointer never changes",
    "second_tdef_page": "page 3 carries an identical tdef record",
    "tdef_record_gap_changes": "byte 1000 of the tdef page changes at every checkpoint",
    "second_churn_window": "a second churn window sits at offset 1020",
    "churn_changes_two_bytes": "the deleted-state reference differs in two u24 bytes",
    "growth_pointer_changes_on_delete": "the growth pointer also changes at L_DELETE_ALL",
    "churn_pointer_no_return": "the churn pointer returns to a different page",
    "growth_target_not_0x05": "the growth pointer targets a data page",
    "churn_pointer_changes_on_p_growth": "the churn pointer changes on P_ABS_04096 to P_ABS_08192",
    "delete_reread_nonzero": "the L_DELETE_ALL reread still returns every L row",
    "legacy_alternating_delete": "the L_DELETE_ALL reread returns half the L rows",
}


@dataclass(frozen=True)
class SyntheticParameters:
    conversion_ordinal: int | None = 20
    slot_activation_at_conversion: int = 2
    bit_polarity: str = "set_means_not_in_use"
    anchor_fill_state: str = "partial"
    record_end_uniform_slack_bytes: int = 32
    global_record_start: int = 0
    global_record_base: int = 0
    inline_tag_at_anchor: int = 0
    first_representation_change_leg: tuple[str, str] | None = None
    e0_baseline_pages: int | None = None
    perturbation: str | None = None

    def effective_conversion(self, replica: int) -> int | None:
        if self.perturbation == "replica_two_converts_earlier" and replica == 2:
            return CHECKPOINT_ORDINALS["P_ABS_12288"]
        if self.perturbation == "holdout_converts_earlier" and replica == 3:
            return CHECKPOINT_ORDINALS["P_ABS_12288"]
        if self.first_representation_change_leg is not None:
            return CHECKPOINT_ORDINALS[self.first_representation_change_leg[1]]
        return self.conversion_ordinal

    def polarity(self, replica: int) -> str:
        if self.perturbation == "holdout_contradicts_every_layer" and replica == 3:
            return next(item for item in POLARITIES if item != self.bit_polarity)
        return self.bit_polarity

    def validate(self) -> None:
        if self.bit_polarity not in POLARITIES or self.anchor_fill_state not in ANCHOR_FILL_UNITS:
            raise ValidationError("A3 synthetic parameter is outside the plan")
        if self.slot_activation_at_conversion not in FREE["slot_activation_at_conversion"]:
            raise ValidationError("A3 slot parameter is outside the plan")
        if self.conversion_ordinal is not None and not 1 <= self.conversion_ordinal < len(CHECKPOINT_IDS):
            raise ValidationError("A3 conversion ordinal is outside the schedule")
        if self.perturbation is not None and self.perturbation not in PERTURBATIONS:
            raise ValidationError(f"unknown A3 perturbation {self.perturbation!r}")
        if not 0 <= self.global_record_start < PAGE_SIZE or self.global_record_base < 0:
            raise ValidationError("A3 global record geometry is outside the page")


def calibration_parameters() -> SyntheticParameters:
    return SyntheticParameters()


def exp_0042_calibration_parameters() -> SyntheticParameters:
    """The disclosed EXP-0042 geometry, derived from its parameters, never its bytes."""
    return SyntheticParameters(
        global_record_start=1915, record_end_uniform_slack_bytes=92, e0_baseline_pages=29,
    )


@dataclass(frozen=True)
class SyntheticReplica:
    replica: int
    parameters: SyntheticParameters
    schedule: Schedule
    ordered_page_sha256: Mapping[str, tuple[str, ...]]
    payloads: Mapping[str, bytes]
    l_rows_before_delete: int
    l_rows_reread_after_delete: int
    missing_blob_digests: frozenset[str] = field(default_factory=frozenset)

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return CHECKPOINT_IDS

    @property
    def page_count(self) -> Mapping[str, int]:
        return {name: len(hashes) for name, hashes in self.ordered_page_sha256.items()}

    def page_bytes(self, digest: str) -> bytes:
        if digest in self.missing_blob_digests:
            raise KeyError(digest)
        return self.payloads[digest]

    # In-memory ReplicaInput fields, so the analyzer can consume a replica without a bundle.
    campaign_id = "a3-synthetic-in-memory"
    producer_commit = "0" * 40
    provider_sha256 = PROVIDER_SHA256
    global_page = GLOBAL_PAGE
    tdef_page = TDEF_PAGE

    @property
    def churn_precondition_met(self) -> bool:
        return self.l_rows_before_delete != 0 and self.l_rows_reread_after_delete == 0


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bitmap_bytes(in_use: int, capacity_bits: int, polarity: str) -> bytes:
    mask = (1 << capacity_bits) - 1
    raw = in_use & mask if polarity == "set_means_in_use" else (~in_use) & mask
    return raw.to_bytes(capacity_bits // 8, "little")


def _range_bits(low: int, high: int) -> int:
    return ((1 << (high - low)) - 1) << low if high > low else 0


def _inline_record(
    page: bytearray, start: int, base: int, polarity: str, in_use_pages: int, *, tag: int = 0,
) -> None:
    """Write tag, u32 base, and the LSB-first bitmap of absolute pages >= base."""
    if start > PAGE_SIZE - 6:
        page[start] = tag
        page[start + 1:PAGE_SIZE] = base.to_bytes(4, "little")[:PAGE_SIZE - start - 1]
        return
    capacity_bits = (PAGE_SIZE - start - 5) * 8
    page[start] = tag
    page[start + 1:start + 5] = base.to_bytes(4, "little")
    page[start + 5:PAGE_SIZE] = _bitmap_bytes(in_use_pages >> base, capacity_bits, polarity)


def _indirect_record(page: bytearray, start: int, slots: tuple[int, int]) -> None:
    page[start] = 1
    for slot, reference in enumerate(slots):
        offset = start + 1 + 4 * slot
        page[offset:offset + 4] = reference.to_bytes(4, "little")[:max(0, PAGE_SIZE - offset)]
    page[start + 9:PAGE_SIZE] = bytes(max(0, PAGE_SIZE - start - 9))


def _slack_offset(parameters: SyntheticParameters, regrow_pages: int) -> int | None:
    """The byte whose D_REGROW_0128-only flip pins the last D-flipped byte.

    ``None`` when the bitmap cannot hold it or the natural D highwater already
    reaches that byte (the requested slack is then realised by the bitmap itself).
    """
    offset = PAGE_SIZE - parameters.record_end_uniform_slack_bytes - 1
    natural_last = parameters.global_record_start + 5 + (regrow_pages - 1 - parameters.global_record_base) // 8
    return offset if offset > natural_last else None


class ReplicaBuilder:
    def __init__(self, parameters: SyntheticParameters, replica: int) -> None:
        parameters.validate()
        if replica not in REPLICA_PROFILES:
            raise ValidationError("A3 replica ordinal is outside the plan")
        self.parameters, self.replica = parameters, replica
        initial = e0_baseline_pages(parameters.anchor_fill_state)
        if parameters.e0_baseline_pages is not None:
            initial = parameters.e0_baseline_pages
        self.schedule = build_schedule(profile=REPLICA_PROFILES[replica], initial_pages=initial)
        self.conversion = parameters.effective_conversion(replica)
        self.polarity = parameters.polarity(replica)
        self.payloads: dict[str, bytes] = {}
        # Pages reserved after the slot-0 map page: free at P_ABS_16480, data afterwards.
        self.hole_pages = range(SLOT_REFERENCES[0] + 1, SLOT_REFERENCES[0] + 1 + self.schedule.pages_per_batch)
        self.map_holes: range = self.hole_pages
        if parameters.perturbation == "no_slot0_flip":
            self.hole_pages = self.map_holes = range(0)
        if parameters.perturbation == "extended_off_by_one_ambiguous":
            self.map_holes = range(SLOT_REFERENCES[0] + 2, SLOT_REFERENCES[0] + 3)
        self.growth_index = 0

    # -- page store -------------------------------------------------------
    def store(self, payload: bytes) -> str:
        digest = _digest(bytes(payload))
        self.payloads.setdefault(digest, bytes(payload))
        return digest

    def constant(self, tag: int, label: bytes) -> str:
        page = bytearray(PAGE_SIZE)
        page[0] = tag
        page[1:1 + len(label)] = label
        return self.store(page)

    # -- global map --------------------------------------------------------
    def _global_in_use(self, checkpoint: str, count: int) -> int:
        """Absolute in-use page set as an integer bitmap for one inline checkpoint."""
        base = self.parameters.global_record_base
        e0 = self.schedule.page_count("E0")
        perturbation = self.parameters.perturbation
        if checkpoint in {"D_DROP", "D_RECREATE_EMPTY"}:
            in_use = _range_bits(base, e0)
            if perturbation == "global_page_static_on_drop" and checkpoint == "D_DROP":
                in_use = _range_bits(base, self.schedule.page_count("D_GROW_0128"))
            if perturbation == "d_drop_keeps_growth_pages" and checkpoint == "D_DROP":
                in_use = _range_bits(base, self.schedule.page_count("D_GROW_0128") + 1)
        else:
            in_use = _range_bits(base, count)
        if perturbation == "anchor_highwater_hole" and checkpoint == "D_GROW_0128":
            in_use &= ~(1 << (base + 5))
        if perturbation == "anchor_sentinel_in_use" and checkpoint == "E0":
            in_use |= 1 << count
        return in_use

    def _cross_check_violation(self, checkpoint: str, in_use: int) -> int:
        perturbation = self.parameters.perturbation
        legs = {
            "cross_check_first_page_violation": (CROSS_CHECK_LEGS[2], 0),
            "cross_check_later_page_violation": (CROSS_CHECK_LEGS[9], 3),
        }
        if perturbation in legs and checkpoint == legs[perturbation][0][1]:
            left_count = self.schedule.page_count(legs[perturbation][0][0])
            in_use &= ~(1 << (left_count + legs[perturbation][1]))
        return in_use

    def _slots(self, ordinal: int) -> tuple[int, int]:
        assert self.conversion is not None
        active = self.parameters.slot_activation_at_conversion if ordinal == self.conversion else 2
        if self.parameters.perturbation == "slot_one_never_activates":
            active = min(active, 1)
        return tuple(reference if slot < active else 0 for slot, reference in enumerate(SLOT_REFERENCES))

    def _is_indirect(self, ordinal: int) -> bool:
        if self.conversion is None or ordinal < self.conversion:
            return False
        if self.parameters.perturbation == "conversion_reverts" and ordinal == CHECKPOINT_ORDINALS[DISCRIMINATOR_RIGHT]:
            return False
        return True

    def global_page(self, checkpoint: str, count: int, *, polarity: str | None = None, start: int | None = None) -> bytes:
        parameters = self.parameters
        ordinal = CHECKPOINT_ORDINALS[checkpoint]
        polarity = polarity or self.polarity
        start = parameters.global_record_start if start is None else start
        page = bytearray([FILLER]) * PAGE_SIZE
        if self._is_indirect(ordinal):
            _indirect_record(page, start, self._slots(ordinal))
            return bytes(page)
        in_use = self._cross_check_violation(checkpoint, self._global_in_use(checkpoint, count))
        tag = parameters.inline_tag_at_anchor if checkpoint in ANCHORS else 0
        if parameters.perturbation == "truncated_base_field":
            start = PAGE_SIZE - 4
            page[100] = ordinal + 1
        _inline_record(page, start, parameters.global_record_base, polarity, in_use, tag=tag)
        if parameters.perturbation == "duplicate_global_start":
            _inline_record(page, 1024, parameters.global_record_base, polarity, in_use)
        in_use_byte = 0xFF if polarity == "set_means_in_use" else 0x00
        offset = _slack_offset(parameters, self.schedule.page_count("D_REGROW_0128"))
        if checkpoint == "D_REGROW_0128" and offset is not None:
            page[offset] = in_use_byte if polarity == "set_means_in_use" else 0xFE
        if parameters.perturbation == "record_end_not_uniform":
            page[PAGE_SIZE - 1] = in_use_byte
        if parameters.perturbation == "inline_suffix_byte" and ordinal >= CHECKPOINT_ORDINALS["L_REL_0064"]:
            page[2000] = in_use_byte
        return bytes(page)

    # -- extended maps -----------------------------------------------------
    def _extended_page(self, slot: int, checkpoint: str, count: int) -> bytes:
        reference = SLOT_REFERENCES[slot]
        origin = slot * EXTENDED_BITS
        in_use = _range_bits(0, max(0, min(count, origin + EXTENDED_BITS) - origin))
        ordinal = CHECKPOINT_ORDINALS[checkpoint]
        perturbation = self.parameters.perturbation
        if slot == 0 and ordinal < CHECKPOINT_ORDINALS[DISCRIMINATOR_RIGHT]:
            for hole in self.map_holes:
                in_use &= ~(1 << hole)
        if slot == 0 and perturbation == "extended_self_bit_clear":
            in_use &= ~(1 << reference)
        page = bytearray(EXTENDED_HEADER) + _bitmap_bytes(in_use, EXTENDED_BITS, self.polarity)
        page[0] = 0x01 if (slot == 0 and perturbation == "slot_reference_not_0x05") else 0x05
        return bytes(page)

    # -- tdef ----------------------------------------------------------------
    def _tdef_page(self, checkpoint: str) -> bytes:
        perturbation = self.parameters.perturbation
        ordinal = CHECKPOINT_ORDINALS[checkpoint]
        page = bytearray([0xFF]) * PAGE_SIZE
        growth = GROWTH_TARGET_PAGES[self.growth_index % len(GROWTH_TARGET_PAGES)]
        if perturbation == "growth_target_not_0x05" and ordinal >= CHECKPOINT_ORDINALS["P_ABS_16480"]:
            growth = DECOY_FIRST_PAGE + 8
        if perturbation == "growth_pointer_changes_on_delete" and checkpoint == "L_DELETE_ALL":
            growth = GROWTH_TARGET_PAGES[(self.growth_index + 1) % len(GROWTH_TARGET_PAGES)]
        churn = CHURN_TARGET_PAGE
        if perturbation == "churn_pointer_changes_on_p_growth" and ordinal >= CHECKPOINT_ORDINALS["P_ABS_08192"]:
            churn = CHURN_ALTERNATE_PAGE
        if checkpoint == "L_DELETE_ALL" and perturbation != "tdef_churn_pointer_static":
            churn += 1 << 16
            if perturbation == "churn_changes_two_bytes":
                churn += 1 << 8
        if perturbation == "churn_pointer_no_return" and ordinal > CHECKPOINT_ORDINALS["L_DELETE_ALL"]:
            # Keep the delete-time high byte too, so no u24 window anywhere returns to its pre-delete value.
            churn = CHURN_ALTERNATE_PAGE + (1 << 16)
        if perturbation == "holdout_contradicts_every_layer" and self.replica == 3 and ordinal > CHECKPOINT_ORDINALS["L_DELETE_ALL"]:
            churn = CHURN_ALTERNATE_PAGE
        page[GROWTH_OFFSET:GROWTH_OFFSET + 3] = growth.to_bytes(3, "little")
        page[GROWTH_OFFSET + 3] = 1
        page[CHURN_OFFSET:CHURN_OFFSET + 3] = churn.to_bytes(3, "little")
        page[CHURN_OFFSET + 3] = 2
        if perturbation == "second_churn_window":
            page[1020:1023] = churn.to_bytes(3, "little")
            page[1023] = 3
        if perturbation == "tdef_record_gap_changes":
            page[1000] = ordinal
        return bytes(page)

    # -- whole replica -----------------------------------------------------
    def build(self) -> SyntheticReplica:
        parameters = self.parameters
        perturbation = parameters.perturbation
        header = self.constant(0x00, b"A3-SYNTHETIC-HEADER")
        alloc = self.constant(0x05, b"A3-SYNTHETIC-ALLOC")
        data = self.constant(0x01, b"A3-SYNTHETIC-DATA")
        free = self.constant(0x00, b"A3-SYNTHETIC-FREE")
        decoys = {"seventeen_qualified_pages": 16, "sixteen_qualified_pages": 15}.get(perturbation or "", 0)
        ordered: dict[str, tuple[str, ...]] = {}
        missing: set[str] = set()
        for row in self.schedule.checkpoints:
            name, count, ordinal = row.checkpoint_id, row.actual_file_pages, row.ordinal
            if name in GROWTH_CHECKPOINTS:
                self.growth_index += 1
            hashes = [data] * count
            hashes[0] = header
            for page in ALLOC_PAGES:
                if page < count:
                    hashes[page] = alloc
            for page in range(DECOY_FIRST_PAGE, DECOY_FIRST_PAGE + decoys):
                if page < count:
                    hashes[page] = self.constant(0x01, b"A3-DECOY-%d-%d" % (page, ordinal))
            hashes[GLOBAL_PAGE] = self.store(self.global_page(name, count))
            hashes[TDEF_PAGE] = self.store(self._tdef_page(name))
            if perturbation == "second_global_page_same_polarity":
                hashes[SECOND_GLOBAL_PAGE] = hashes[GLOBAL_PAGE]
            if perturbation == "second_global_page_opposite_polarity":
                other = next(item for item in POLARITIES if item != self.polarity)
                hashes[SECOND_GLOBAL_PAGE] = self.store(self.global_page(name, count, polarity=other))
            if perturbation == "second_tdef_page":
                hashes[SECOND_GLOBAL_PAGE] = hashes[TDEF_PAGE]
            for slot, reference in enumerate(SLOT_REFERENCES):
                if reference < count:
                    hashes[reference] = self.store(self._extended_page(slot, name, count))
            for hole in self.hole_pages:
                if hole < count and ordinal < CHECKPOINT_ORDINALS[DISCRIMINATOR_RIGHT]:
                    hashes[hole] = free
            if perturbation == "idle_pair_volatile" and name == "E0R":
                hashes[count - 1] = self.constant(0x01, b"A3-IDLE-VOLATILE")
            if perturbation == "missing_page_blob" and name == "D_GROW_0128":
                missing.add(hashes[GLOBAL_PAGE])
            ordered[name] = tuple(hashes)
        for left, right in IDLE_PAIRS:
            if perturbation == "idle_pair_volatile" and right == "E0R":
                continue
            ordered[right] = ordered[left]
        l_rows = self.schedule.checkpoint("L_REL_1280").table_row_counts["L"]
        reread = {"delete_reread_nonzero": l_rows, "legacy_alternating_delete": l_rows // 2}.get(perturbation or "", 0)
        return SyntheticReplica(
            self.replica, parameters, self.schedule, ordered, dict(self.payloads), l_rows, reread,
            frozenset(missing),
        )


def generate_replica(parameters: SyntheticParameters, replica: int) -> SyntheticReplica:
    return ReplicaBuilder(parameters, replica).build()


def generate_synthetic_bundle(parameters: SyntheticParameters | None = None, replica: int = 1) -> SyntheticReplica:
    return generate_replica(parameters or calibration_parameters(), replica)


def generate_synthetic_bundles(parameters: SyntheticParameters) -> tuple[SyntheticReplica, ...]:
    return generate_replicas(parameters)


def generate_replicas(parameters: SyntheticParameters) -> tuple[SyntheticReplica, ...]:
    return tuple(generate_replica(parameters, replica) for replica in (1, 2, 3))
