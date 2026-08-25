#!/usr/bin/env python3
"""Deterministic, non-evidential byte generator for the A4 protocol.

The generator compiles its checkpoint walk from :mod:`a4_generator_schedule`
and encodes pages through :mod:`a4_generator_pages`.  It does not import an
analyzer and it never stores predicate outcomes in a fixture.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from protocol_validation import ValidationError
from a4_generator_pages import (
    TAG_DATA,
    TAG_TDEF,
    data_page,
    empty_page,
    encode_locator,
    masked_tdef_page,
    tag_05_page,
    type_0_row,
    type_1_row,
)
from a4_generator_schedule import EVENTS, PROFILES, EventKind, Profile, SCHEDULE
from a4_spec import BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, PLAN


GRAMMAR = PLAN["candidate_grammars"]
H1 = GRAMMAR["h1"]
H4 = GRAMMAR["h4"]
STANDARD_SIGNATURE = H1["table_record_signature"]["signature_id"]
DUPLICATE_SIGNATURE = H1["pair_multiple_reachability_signature"]["signature_id"]
DEFAULT_LAYOUT = H1["locator_layouts"][1]
DEFAULT_OFFSETS = tuple(interval[0] for interval in H1["table_record_signature"]["locator_holes"])
TAG05_BITS = (PAGE_SIZE - 4) * 8
SYSTEM_PAGE_NAMES = (
    "root_tdef",
    "root_map",
    "catalog",
    "decoy_tdef",
    "decoy_map",
)
CATALOG_KINDS = MappingProxyType({"table": 0x11, "field": 0x22, "index": 0x33})


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _header_page(replica: int) -> bytes:
    page = bytearray(PAGE_SIZE)
    marker = b"Jet3-A4-SYN"
    page[: len(marker)] = marker
    page[12] = replica
    return bytes(page)


def _system_tdef(layout: str, map_page: int) -> bytes:
    page = bytearray(PAGE_SIZE)
    page[:8] = bytes([TAG_TDEF, 1]) + b"A4SYS0"
    page[35:39] = encode_locator(layout, map_page, 0)
    page[39:43] = encode_locator(layout, map_page, 1)
    return bytes(page)


def _payload(role: str, row_id: int) -> bytes:
    seed = f"A4|{role}|{row_id:010d}|".encode("ascii")
    return (seed * (240 // len(seed) + 1))[:240]


def _strict_name_bytes(name: str) -> bytes:
    try:
        return name.encode("cp1252", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValidationError("A4 synthetic name is not strict Windows-1252") from exc


@dataclass(frozen=True)
class SyntheticParameters:
    """Bounded byte-level choices; none is a scientific result label."""

    layout_by_replica: Mapping[int, str] = field(default_factory=dict)
    owned_ordinal_by_replica: Mapping[int, int] = field(default_factory=dict)
    signature_id: str = STANDARD_SIGNATURE
    locator_offsets: tuple[int, ...] = DEFAULT_OFFSETS
    type_0_polarity: str = "set_bit_owned_in_use"
    row_flag: int = 0x1000
    force_t3_conversion_checkpoint: str = "T3_ABS_08192"
    initial_filler_delta: int = 0
    omit_blob_digest: str | None = None

    def layout(self, replica: int) -> str:
        return self.layout_by_replica.get(replica, DEFAULT_LAYOUT)

    def owned_ordinal(self, replica: int) -> int:
        return self.owned_ordinal_by_replica.get(replica, 0)

    def validate(self) -> None:
        layouts = tuple(H1["locator_layouts"])
        if any(replica not in PROFILES for replica in self.layout_by_replica):
            raise ValidationError("A4 synthetic layout names an unknown replica")
        if any(layout not in layouts for layout in self.layout_by_replica.values()):
            raise ValidationError("A4 synthetic locator layout is not registered")
        if any(value not in (0, 1) for value in self.owned_ordinal_by_replica.values()):
            raise ValidationError("A4 synthetic owned ordinal must be zero or one")
        signatures = {STANDARD_SIGNATURE, DUPLICATE_SIGNATURE}
        if self.signature_id not in signatures:
            raise ValidationError("A4 synthetic TDEF signature is not registered")
        signature = (
            H1["table_record_signature"]
            if self.signature_id == STANDARD_SIGNATURE
            else H1["pair_multiple_reachability_signature"]
        )
        required = tuple(interval[0] for interval in signature["locator_holes"])
        if self.locator_offsets != required:
            raise ValidationError("A4 synthetic locator offsets differ from the signature")
        if self.type_0_polarity not in H1.get("type_0_polarities", ()) and self.type_0_polarity not in GRAMMAR["h2"]["type_0_polarities"]:
            raise ValidationError("A4 synthetic type-0 polarity is not registered")
        if self.row_flag < 0 or self.row_flag > 0xF000 or self.row_flag & 0x0FFF:
            raise ValidationError("A4 synthetic row flag overlaps the offset bits")
        if self.force_t3_conversion_checkpoint not in CHECKPOINT_IDS:
            raise ValidationError("A4 synthetic conversion checkpoint is unknown")
        if self.initial_filler_delta < 0 or self.initial_filler_delta > 32:
            raise ValidationError("A4 synthetic filler delta is outside its local bound")
        if self.omit_blob_digest is not None and (
            len(self.omit_blob_digest) != 64
            or any(value not in "0123456789abcdef" for value in self.omit_blob_digest)
        ):
            raise ValidationError("A4 omitted blob identity is not canonical SHA-256")


@dataclass
class TableState:
    instance_id: str
    role: str
    tdef_page: int
    map_page: int
    owned_pages: set[int]
    available_pages: set[int] = field(default_factory=set)
    data_pages: list[int] = field(default_factory=list)
    tag05_pages: dict[int, int] = field(default_factory=dict)
    row_count: int = 0
    retained_row_count: int = 0
    version: int = 0
    converted: bool = False
    dropped: bool = False


@dataclass(frozen=True)
class CatalogRecord:
    checkpoint_id: str
    instance_id: str
    object_kind: str
    object_id: int
    name: str


@dataclass(frozen=True)
class SyntheticReplica:
    """The exact in-memory page-store surface consumed by ``a4_model.View``."""

    replica: int
    ordered_page_sha256: Mapping[str, tuple[str, ...]]
    payloads: Mapping[str, bytes]
    row_counts: Mapping[str, Mapping[str, int]]
    metadata: Mapping[str, object]
    omitted_digests: frozenset[str] = frozenset()

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return CHECKPOINT_IDS

    @property
    def page_count(self) -> Mapping[str, int]:
        return {checkpoint: len(hashes) for checkpoint, hashes in self.ordered_page_sha256.items()}

    def page_bytes(self, digest: str) -> bytes:
        if digest in self.omitted_digests:
            raise KeyError(digest)
        return self.payloads[digest]


@dataclass(frozen=True)
class SyntheticCampaign:
    replicas: Mapping[int, SyntheticReplica]
    payloads: Mapping[str, bytes]


class ReplicaBuilder:
    def __init__(self, parameters: SyntheticParameters, replica: int) -> None:
        parameters.validate()
        if replica not in PROFILES:
            raise ValidationError("A4 synthetic replica ordinal is outside the plan")
        self.parameters = parameters
        self.replica = replica
        self.profile: Profile = PROFILES[replica]
        self.pages: list[bytes] = []
        self.payloads: dict[str, bytes] = {}
        self.ordered: dict[str, tuple[str, ...]] = {}
        self.row_counts: dict[str, Mapping[str, int]] = {}
        self.tables: dict[str, TableState] = {}
        self.catalog_records: list[CatalogRecord] = []
        self.system: dict[str, int] = {}
        self.reserved: dict[str, tuple[int, int]] = {}
        self.baselines: dict[str, int] = {}
        self.inserted_rows = 0

    def _append(self, payload: bytes) -> int:
        if len(payload) != PAGE_SIZE:
            raise ValidationError("A4 synthetic page is not exactly 2,048 bytes")
        self.pages.append(payload)
        return len(self.pages) - 1

    def _store(self, payload: bytes) -> str:
        digest = _digest(payload)
        self.payloads.setdefault(digest, payload)
        if len(self.payloads) > int(BOUNDS["max_unique_page_blobs"]):
            raise ValidationError("A4 synthetic unique-page bound exceeded")
        return digest

    def _build_empty(self) -> None:
        self._append(_header_page(self.replica))
        self._append(empty_page())
        for name in SYSTEM_PAGE_NAMES:
            self.system[name] = self._append(empty_page())
        for _ in range(self.profile.initial_filler_pages + self.parameters.initial_filler_delta):
            self._append(empty_page())
        for instance in SCHEDULE.instances:
            self.reserved[instance.instance_id] = (
                self._append(empty_page()),
                self._append(empty_page()),
            )
        layout = self.parameters.layout(self.replica)
        self.pages[self.system["root_tdef"]] = _system_tdef(layout, self.system["root_map"])
        self.pages[self.system["decoy_tdef"]] = _system_tdef(layout, self.system["decoy_map"])
        self._render_system()

    def _render_system(self) -> None:
        root_map = self.system["root_map"]
        catalog = self.system["catalog"]
        decoy_map = self.system["decoy_map"]
        owned = type_0_row(root_map, {root_map, catalog}, polarity=self.parameters.type_0_polarity)
        available = type_0_row(root_map, (), polarity=self.parameters.type_0_polarity)
        flags = {0: self.parameters.row_flag, 1: self.parameters.row_flag}
        self.pages[root_map] = data_page((owned, available), raw_flags=flags)
        decoy_available = type_0_row(decoy_map, (), polarity=self.parameters.type_0_polarity)
        self.pages[decoy_map] = data_page((type_0_row(decoy_map, {decoy_map}), decoy_available), raw_flags=flags)
        rows = tuple(self._catalog_row(record) for record in self.catalog_records)
        self.pages[catalog] = data_page(rows)

    def _catalog_row(self, record: CatalogRecord) -> bytes:
        kind = CATALOG_KINDS[record.object_kind]
        name = _strict_name_bytes(record.name)
        if len(name) > 0xFFFF:
            raise ValidationError("A4 synthetic catalog name is too long")
        return bytes([record.object_id, kind, len(name)]) + name

    def _name_for(self, checkpoint_id: str, role: str) -> str:
        binding = next(item for item in PLAN["tables"]["role_bindings"] if item["replica"] == self.replica)
        if checkpoint_id == "T1_ADD_TEXT":
            return "Payload"
        if checkpoint_id == "T1_ADD_INDEX":
            return "A4IX_ID"
        return str(binding[role])

    def _record(self, checkpoint_id: str, instance_id: str, kind: str) -> None:
        role = SCHEDULE.instance(instance_id).role
        ordinal = len(self.catalog_records)
        object_id = 0x21 + ordinal
        self.catalog_records.append(CatalogRecord(
            checkpoint_id,
            instance_id,
            kind,
            object_id,
            self._name_for(checkpoint_id, role),
        ))

    def _locators(self, map_page: int) -> Mapping[int, bytes]:
        layout = self.parameters.layout(self.replica)
        offsets = self.parameters.locator_offsets
        locators = {
            offsets[0]: encode_locator(layout, map_page, 0),
            offsets[1]: encode_locator(layout, map_page, 1),
        }
        if len(offsets) == 3:
            locators[offsets[2]] = locators[offsets[1]]
        return locators

    def _create(self, checkpoint_id: str, instance_id: str) -> None:
        role = SCHEDULE.instance(instance_id).role
        tdef_page, map_page = self.reserved[instance_id]
        state = TableState(instance_id, role, tdef_page, map_page, {map_page})
        self.tables[instance_id] = state
        self.pages[tdef_page] = masked_tdef_page(
            self.parameters.signature_id,
            self._locators(map_page),
            version=state.version,
        )
        self._record(checkpoint_id, instance_id, "table")

    def _drop(self, instance_id: str) -> None:
        state = self.tables[instance_id]
        state.dropped = True
        state.owned_pages.clear()
        state.available_pages.clear()
        self.catalog_records = [
            record for record in self.catalog_records if record.instance_id != instance_id
        ]
        self.pages[state.tdef_page] = empty_page()
        self.pages[state.map_page] = empty_page()

    def _grow(self, checkpoint_id: str, instance_id: str) -> None:
        state = self.tables[instance_id]
        event = SCHEDULE.event(checkpoint_id)
        baseline = None
        if event.baseline_checkpoint_id is not None:
            baseline = self.baselines.get(event.baseline_checkpoint_id)
            if baseline is None:
                raise ValidationError(f"missing A4 relative baseline {event.baseline_checkpoint_id}")
        target = event.target_pages(baseline)
        if target is None:
            raise ValidationError("A4 growth event lacks a target")
        projected_bits = max(1, target - state.map_page)
        projected_row_bytes = 15 + math.ceil(projected_bits / 8)
        force_t3 = state.role == "T3" and CHECKPOINT_IDS.index(checkpoint_id) >= CHECKPOINT_IDS.index(self.parameters.force_t3_conversion_checkpoint)
        if state.converted or force_t3 or projected_row_bytes > PAGE_SIZE:
            state.converted = True
        while len(self.pages) < target:
            if self.inserted_rows + self.profile.batch_rows > SCHEDULE.max_inserted_rows_per_replica:
                raise ValidationError("A4 synthetic inserted-row bound exceeded")
            first_id = state.row_count + 1
            batch = tuple(range(first_id, first_id + self.profile.batch_rows))
            for start in range(0, len(batch), self.profile.rows_per_page):
                row_ids = batch[start : start + self.profile.rows_per_page]
                rows = tuple(
                    row_id.to_bytes(4, "little", signed=True)
                    + _payload(state.role, row_id)
                    for row_id in row_ids
                )
                page_number = self._append(data_page(rows))
                state.data_pages.append(page_number)
                state.owned_pages.add(page_number)
            if state.converted:
                self._prepare_tag05_slots(state, len(self.pages))
            state.row_count += self.profile.batch_rows
            self.inserted_rows += self.profile.batch_rows
        state.retained_row_count = state.row_count

    def _prepare_tag05_slots(self, state: TableState, achieved_pages: int) -> None:
        """Allocate required map pages as part of the current complete batch."""
        while True:
            highest = max((*state.owned_pages, achieved_pages - 1), default=0)
            required = set(range(highest // TAG05_BITS + 1))
            missing = sorted(required - set(state.tag05_pages))
            if not missing:
                return
            for slot in missing:
                state.tag05_pages[slot] = self._append(tag_05_page(()))

    def _should_convert(self, checkpoint_id: str, state: TableState) -> bool:
        if state.role == "T3" and CHECKPOINT_IDS.index(checkpoint_id) >= CHECKPOINT_IDS.index(self.parameters.force_t3_conversion_checkpoint):
            return True
        owned_bits = max(state.owned_pages, default=state.map_page) - state.map_page + 1
        available_bits = max(state.available_pages, default=state.map_page) - state.map_page + 1
        serialized = 10 + math.ceil(owned_bits / 8) + math.ceil(available_bits / 8)
        return 14 + serialized > PAGE_SIZE

    def _render_tag05(self, state: TableState) -> tuple[int, ...]:
        slots = sorted({page // TAG05_BITS for page in state.owned_pages})
        if not slots:
            return ()
        for slot in slots:
            if slot not in state.tag05_pages:
                raise ValidationError("A4 synthetic tag-05 page was not allocated during growth")
        references = [0] * (max(slots) + 1)
        for slot in slots:
            reference = state.tag05_pages[slot]
            references[slot] = reference
            bits = {page - slot * TAG05_BITS for page in state.owned_pages if page // TAG05_BITS == slot}
            self.pages[reference] = tag_05_page(bits)
        return tuple(references)

    def _render_table(self, checkpoint_id: str, state: TableState) -> None:
        if state.dropped:
            return
        state.converted = state.converted or self._should_convert(checkpoint_id, state)
        if state.converted:
            owned_row = type_1_row(self._render_tag05(state), slot_count=3)
        else:
            capacity = max(state.owned_pages | state.available_pages, default=state.map_page) - state.map_page + 1
            owned_row = type_0_row(
                state.map_page,
                state.owned_pages,
                polarity=self.parameters.type_0_polarity,
                capacity_bits=capacity,
            )
        available_row = type_0_row(
            state.map_page,
            state.available_pages,
            polarity=self.parameters.type_0_polarity,
            capacity_bits=max(state.available_pages, default=state.map_page) - state.map_page + 1,
        )
        rows = (owned_row, available_row)
        if self.parameters.owned_ordinal(self.replica) == 1:
            rows = tuple(reversed(rows))
        flags = {0: self.parameters.row_flag, 1: self.parameters.row_flag}
        self.pages[state.map_page] = data_page(rows, raw_flags=flags)
        self.pages[state.tdef_page] = masked_tdef_page(
            self.parameters.signature_id,
            self._locators(state.map_page),
            version=state.version,
        )

    def _capture(self, checkpoint_id: str) -> None:
        if len(self.pages) > int(BOUNDS["max_final_pages_per_replica"]):
            raise ValidationError("A4 synthetic final-page bound exceeded")
        hashes = tuple(self._store(page) for page in self.pages)
        self.ordered[checkpoint_id] = hashes
        counts = {role: 0 for role in PLAN["tables"]["logical_roles"]}
        counts.update({
            state.role: state.row_count
            for state in self.tables.values()
            if not state.dropped
        })
        self.row_counts[checkpoint_id] = MappingProxyType(counts)

    def build(self) -> SyntheticReplica:
        for event in EVENTS:
            checkpoint_id = event.checkpoint_id
            if event.kind is EventKind.EMPTY:
                self._build_empty()
            elif event.kind is EventKind.CREATE:
                self._create(checkpoint_id, event.lifecycle_instance or "")
            elif event.kind is EventKind.ADD_FIELD:
                state = self.tables[event.lifecycle_instance or ""]
                state.version += 1
                self._record(checkpoint_id, state.instance_id, "field")
            elif event.kind is EventKind.ADD_INDEX:
                state = self.tables[event.lifecycle_instance or ""]
                state.version += 1
                self._record(checkpoint_id, state.instance_id, "index")
            elif event.kind is EventKind.DROP:
                self._drop(event.lifecycle_instance or "")
            elif event.kind is EventKind.GROW:
                self._grow(checkpoint_id, event.lifecycle_instance or "")
            elif event.kind is EventKind.DELETE_ALL:
                state = self.tables[event.lifecycle_instance or ""]
                state.available_pages = set(state.data_pages)
                state.row_count = 0
            elif event.kind is EventKind.REINSERT:
                state = self.tables[event.lifecycle_instance or ""]
                state.available_pages.clear()
                state.row_count = state.retained_row_count
                self.inserted_rows += state.row_count
            self._render_system()
            for state in self.tables.values():
                self._render_table(checkpoint_id, state)
            self._render_system()
            self._capture(checkpoint_id)
            if checkpoint_id in {event.baseline_checkpoint_id for event in EVENTS if event.baseline_checkpoint_id}:
                self.baselines[checkpoint_id] = len(self.pages)
        if tuple(self.ordered) != CHECKPOINT_IDS:
            raise ValidationError("A4 synthetic checkpoint walk departed from the plan")
        omitted = frozenset(
            {self.parameters.omit_blob_digest}
            if self.parameters.omit_blob_digest in self.payloads
            else set()
        )
        metadata = MappingProxyType({
            "system_pages": MappingProxyType(dict(self.system)),
            "lifecycle_pages": MappingProxyType({
                instance_id: MappingProxyType({
                    "tdef_page": state.tdef_page,
                    "map_page": state.map_page,
                    "tag05_pages": MappingProxyType(dict(state.tag05_pages)),
                    "data_pages": tuple(state.data_pages),
                })
                for instance_id, state in self.tables.items()
            }),
            "baselines": MappingProxyType(dict(self.baselines)),
            "profile": self.profile,
        })
        return SyntheticReplica(
            self.replica,
            MappingProxyType(dict(self.ordered)),
            MappingProxyType(dict(self.payloads)),
            MappingProxyType(dict(self.row_counts)),
            metadata,
            omitted,
        )


def generate_replica(parameters: SyntheticParameters | None = None, replica: int = 1) -> SyntheticReplica:
    return ReplicaBuilder(parameters or SyntheticParameters(), replica).build()


def generate_campaign(parameters: SyntheticParameters | None = None) -> SyntheticCampaign:
    selected = parameters or SyntheticParameters()
    replicas = {replica: generate_replica(selected, replica) for replica in sorted(PROFILES)}
    payloads: dict[str, bytes] = {}
    for replica in replicas.values():
        for digest, payload in replica.payloads.items():
            previous = payloads.setdefault(digest, payload)
            if previous != payload:
                raise ValidationError("A4 synthetic digest collision")
    return SyntheticCampaign(MappingProxyType(replicas), MappingProxyType(payloads))


def overshoots(replica: SyntheticReplica) -> Mapping[str, int]:
    output: dict[str, int] = {}
    for event in EVENTS:
        if event.kind is not EventKind.GROW:
            continue
        baseline = None
        if event.baseline_checkpoint_id is not None:
            baseline = int(replica.metadata["baselines"][event.baseline_checkpoint_id])  # type: ignore[index]
        target = event.target_pages(baseline)
        if target is None:
            raise ValidationError("A4 growth target unexpectedly absent")
        output[event.checkpoint_id] = replica.page_count[event.checkpoint_id] - target
    return MappingProxyType(output)
