#!/usr/bin/env python3
"""Plan-driven synthetic A4 campaign generator (non-evidential; page bytes only).

Every replica walks the 25 checkpoints of ``checkpoint_design`` with the
listed operation, fixed 32-row batches, relative/absolute thresholds with
per-replica overshoot, T2 drop/recreate without page reuse, and one DAO
snapshot per checkpoint. Physical layout: user tables get one tag-02 TDEF page
carrying the registered 92-byte record signature with two table-relative
locators, one tag-01 map page with an owned/in-use type-0 row and an available
type-0 row (rows move as bitmaps grow), and type-0 to type-1 conversion with
tag-05 extended pages once the type-0 row no longer fits. A system catalog
(root TDEF, map page, record page) records one operation-delta row per listed
operation; a second system table is a static decoy.

Generator and environment: deterministic pure Python (stdlib only); identity is
the SHA-256 of this module recorded in the transcript. Nothing produced here is
A4 evidence.

A4 rule | implementation
--- | ---
Relative/absolute growth, baseline capture, overshoot disclosure | :meth:`ReplicaBuilder._grow`
Independent replica 3 overshoot | :data:`DEFAULT_PROFILES`
TDEF signature with exact locator holes [35,39)/[39,43) | :func:`tdef_page`
Moving row starts as the owned bitmap grows | :meth:`ReplicaBuilder._render_map`
Type-0 -> type-1 conversion with zero (inactive) slots | :meth:`ReplicaBuilder._render_map`
One catalog record per operation instance, non-name fields first | :meth:`ReplicaBuilder._record`
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from a4_campaign import Campaign, ReplicaData
from a4_pages import (
    TAG_DATA, TAG_TDEF, data_page, encode_locator, tag05_page, type0_row, type1_row,
)
from a4_spec import (
    CHECKPOINTS, EVENT_BY_CHECKPOINT, EVENTS, INSTANCE_BY_ID, LOCATOR_HOLES, MAX_ROWS, ORDINAL, PAGE_SIZE,
    ROLE_BINDINGS, ROW_BATCH, SIGNATURE_VALUE, TAG05_BITS, expected_name, name_bytes, row_payload,
)

KIND_BYTES = {"table": 0x11, "field": 0x22, "index": 0x33}
TYPE1_SLOTS = 33
STAMP0 = 0x10
FIRST_ID = 0x21
DIRECTORY_RESERVE = 14  # header + two directory entries


@dataclass(frozen=True)
class Profile:
    rows_per_page: int
    empty_filler: int


DEFAULT_PROFILES = {1: Profile(8, 0), 2: Profile(7, 2), 3: Profile(6, 1)}


@dataclass
class Params:
    """Every knob is a synthetic free choice; none encodes an expected predicate status."""

    profiles: dict[int, Profile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))
    layout_by_replica: dict[int, str] = field(default_factory=dict)
    locator_offsets_by_replica: dict[int, tuple[int, int]] = field(default_factory=dict)
    owned_ordinal_by_replica: dict[int, int] = field(default_factory=dict)
    user_tdef_tag: int = TAG_TDEF
    tdef_style: str = "signature"  # signature | opaque | broken_signature
    locator_target: str = "map"  # map | tdef
    decoy_tdef_pages: dict[str, int] = field(default_factory=dict)
    extra_empty_filler: int = 0
    conversion: str = "capacity"  # capacity | never
    force_conversion_at: dict[str, str] = field(default_factory=dict)
    type1_exact_slots: bool = False
    omit_pages_at_or_above: int | None = None
    tag05_mode_by_replica: dict[int, str] = field(default_factory=dict)  # slot | equals_slot | referenced
    extra_tag05_bits: dict[int, set[int]] = field(default_factory=dict)
    tag05_bit_shift: int = 0
    record_page_default: str = "root"  # root | spare
    record_page_override: dict[str, str] = field(default_factory=dict)
    record_page_override_by_replica: dict[int, dict[str, str]] = field(default_factory=dict)
    decoy_follows_ops: bool = False
    skip_record_at: set[str] = field(default_factory=set)
    duplicate_record_at: set[str] = field(default_factory=set)
    kind_override: dict[str, int] = field(default_factory=dict)
    record_layout: str = "single"  # single | double
    e_acute_length_override: int | None = None
    e_acute_double_occurrence: bool = False
    t2_id_relation_by_replica: dict[int, str] = field(default_factory=dict)  # distinct | same
    holdout_name_corruption: bool = False
    preallocate_user_pages: bool = False  # reserve low page numbers at EMPTY for user TDEF/map pages
    available_equals_owned: bool = False
    synthetic_boundary_bits: bool = False  # activate the next slot and set bit 16351 / bit 0 around one boundary

    def layout(self, replica: int) -> str:
        return self.layout_by_replica.get(replica, "u8_row_then_u24le_page")

    def offsets(self, replica: int) -> tuple[int, int]:
        return self.locator_offsets_by_replica.get(replica, (LOCATOR_HOLES[0][0], LOCATOR_HOLES[1][0]))

    def owned_ordinal(self, replica: int) -> int:
        return self.owned_ordinal_by_replica.get(replica, 0)

    def tag05_mode(self, replica: int) -> str:
        return self.tag05_mode_by_replica.get(replica, "slot")


def tdef_page(style: str, tag: int, locators: tuple[tuple[int, bytes], ...], version: int) -> bytes:
    if style == "opaque":
        page = bytearray([0xA5] * PAGE_SIZE)
        page[0] = tag
        return bytes(page)
    page = bytearray(PAGE_SIZE)
    page[: len(SIGNATURE_VALUE)] = SIGNATURE_VALUE
    page[0] = tag
    page[12:14] = version.to_bytes(2, "little")
    if style == "broken_signature":
        page[2] ^= 0x01
    for offset, window in locators:
        page[offset: offset + 4] = window
    return bytes(page)


def system_tdef_page(locators: tuple[tuple[int, bytes], ...]) -> bytes:
    page = bytearray(PAGE_SIZE)
    page[0:4] = bytes([TAG_TDEF, 0x01, ord("S"), ord("Y")])
    for offset, window in locators:
        page[offset: offset + 4] = window
    return bytes(page)


def header_page() -> bytes:
    page = bytearray(PAGE_SIZE)
    page[0:8] = b"\x00\x01Jet3A4\x00"[:8]
    return bytes(page)


def empty_data_page(rows: int = 0) -> bytes:
    return data_page([b"\x01\x00\x00\x00filler" ] * rows)


@dataclass
class TableState:
    instance: str
    role: str
    tdef: int
    map: int
    owned: set[int]
    available: set[int] = field(default_factory=set)
    data_pages: list[int] = field(default_factory=list)
    rows: int = 0
    version: int = 0
    converted: bool = False
    tag05: dict[int, int] = field(default_factory=dict)
    dropped: bool = False


@dataclass
class Record:
    checkpoint: str
    instance: str
    kind: str
    object_id: int
    stamp: int = STAMP0
    deleted: bool = False
    page_slot: str = "root"


class ReplicaBuilder:
    def __init__(self, campaign: Campaign, params: Params, replica: int) -> None:
        self.campaign, self.params, self.replica = campaign, params, replica
        self.profile = params.profiles[replica]
        self.file: list[bytes] = []
        self.tables: dict[str, TableState] = {}
        self.records: list[Record] = []
        self.next_id = FIRST_ID
        self.baselines: dict[str, int] = {}
        self.data = ReplicaData(replica)
        self.mode = params.tag05_mode(replica)
        self.reserved_tag05: tuple[int, ...] = ()
        self.system: dict[str, int] = {}
        self.op_counter = 0
        self.reserved_user: list[int] = []
        self.rows_per_batch_pages = math.ceil(ROW_BATCH / self.profile.rows_per_page)

    # ------------------------------------------------------------------ helpers
    def _append(self, page: bytes) -> int:
        self.file.append(page)
        return len(self.file) - 1

    def _locators(self, page: int, ordinal_pages: tuple[int, int]) -> tuple[tuple[int, bytes], ...]:
        layout = self.params.layout(self.replica)
        a, b = self.params.offsets(self.replica)
        return ((a, encode_locator(layout, ordinal_pages[0], 0)), (b, encode_locator(layout, ordinal_pages[1], 1)))

    def _data_page(self, role: str) -> bytes:
        row = (1).to_bytes(4, "little") + row_payload(role, 1)
        return data_page([row] * self.profile.rows_per_page)

    # ------------------------------------------------------------------ EMPTY layout
    def _build_empty(self) -> None:
        self._append(header_page())
        if self.mode != "slot":
            self.reserved_tag05 = (self._append(tag05_page(set())), self._append(tag05_page(set())))
        else:
            self._append(empty_data_page())  # page 1: static global page, not used by A4
        root_tdef = self._append(b"")
        root_map = self._append(b"")
        record = self._append(b"")
        decoy_tdef = self._append(b"")
        decoy_map = self._append(b"")
        spare = self._append(b"")
        self.system = {"root_tdef": root_tdef, "root_map": root_map, "record": record,
                       "decoy_tdef": decoy_tdef, "decoy_map": decoy_map, "spare": spare}
        self.file[root_tdef] = system_tdef_page(self._locators(root_tdef, (root_map, root_map)))
        self.file[decoy_tdef] = system_tdef_page(self._locators(decoy_tdef, (decoy_map, decoy_map)))
        if self.params.preallocate_user_pages:
            self.reserved_user = [self._append(empty_data_page()) for _ in range(2 * len(INSTANCE_BY_ID))]
        for _ in range(self.profile.empty_filler + self.params.extra_empty_filler):
            self._append(empty_data_page(1))
        self._render_system()

    def _render_system(self) -> None:
        root_map, record, decoy_map, spare = (self.system[k] for k in ("root_map", "record", "decoy_map", "spare"))
        self.file[root_map] = data_page([type0_row(root_map, {root_map, record}), type0_row(root_map, set())])
        decoy_owned = {decoy_map, spare} if self.params.decoy_follows_ops else {decoy_map}
        self.file[decoy_map] = data_page([type0_row(decoy_map, decoy_owned), type0_row(decoy_map, set())])
        self._render_records()

    def _render_records(self) -> None:
        for slot_name, page in (("root", self.system["record"]), ("spare", self.system["spare"])):
            rows, flags = [], {}
            for record in [r for r in self.records if r.page_slot == slot_name]:
                if record.deleted:
                    flags[len(rows)] = 0x8000
                rows.append(self._record_bytes(record))
            if slot_name == "spare" and self.params.decoy_follows_ops:
                rows.append(b"\x01" + self.op_counter.to_bytes(2, "little"))
            self.file[page] = data_page(rows, flags)

    def _record_bytes(self, record: Record) -> bytes:
        name = expected_name(self.replica, record.checkpoint)
        encodings = name_bytes(name)
        kind = self.params.kind_override.get(record.checkpoint, KIND_BYTES[record.kind])
        primary = encodings["strict_windows_1252"]
        is_e_acute = primary != encodings["utf_8"]
        if is_e_acute and self.params.holdout_name_corruption and self.replica == 3:
            primary = primary[:-1] + b"5"
        length = len(primary)
        if is_e_acute and self.params.e_acute_length_override is not None:
            length = self.params.e_acute_length_override
        prefix = bytes([record.stamp, record.object_id & 0xFF, kind])
        if self.params.record_layout == "double":
            prefix += bytes([record.stamp, (record.object_id ^ 0x40) & 0xFF, kind])
        row = prefix + bytes([length]) + primary
        if is_e_acute and self.params.e_acute_double_occurrence:
            utf8 = encodings["utf_8"]
            row += bytes([record.stamp, record.object_id & 0xFF, kind, len(utf8)]) + utf8
        return row

    # ------------------------------------------------------------------ user tables
    def _create(self, checkpoint: str, instance_id: str) -> None:
        inst = INSTANCE_BY_ID[instance_id]
        if self.reserved_user:
            tdef, map_page = self.reserved_user.pop(0), self.reserved_user.pop(0)
        else:
            tdef, map_page = self._append(b""), self._append(b"")
        target = tdef if self.params.locator_target == "tdef" else map_page
        self.file[tdef] = tdef_page(self.params.tdef_style, self.params.user_tdef_tag, self._locators(tdef, (target, target)), 0)
        for _ in range(self.params.decoy_tdef_pages.get(checkpoint, 0)):
            self._append(tdef_page(self.params.tdef_style, self.params.user_tdef_tag, (), 0))
        state = TableState(instance_id, inst.role, tdef, map_page, {map_page})
        self.tables[instance_id] = state
        self.file[map_page] = b""
        self._record(checkpoint, instance_id, "table", self._allocate_id(instance_id))

    def _allocate_id(self, instance_id: str) -> int:
        relation = self.params.t2_id_relation_by_replica.get(self.replica, "distinct")
        if instance_id == "T2-v2" and relation == "same":
            return next(r.object_id for r in self.records if r.instance == "T2-v1" and r.kind == "table")
        value, self.next_id = self.next_id, self.next_id + 1
        return value

    def _record(self, checkpoint: str, instance_id: str, kind: str, object_id: int) -> None:
        if checkpoint in self.params.skip_record_at:
            return
        override = dict(self.params.record_page_override)
        override.update(self.params.record_page_override_by_replica.get(self.replica, {}))
        slot = override.get(checkpoint, self.params.record_page_default)
        stamp = STAMP0 + len(self.records)
        self.records.append(Record(checkpoint, instance_id, kind, object_id, stamp=stamp, page_slot=slot))
        if checkpoint in self.params.duplicate_record_at:
            self.records.append(Record(checkpoint, instance_id, kind, object_id, stamp=stamp, page_slot=slot))

    def _table_record(self, instance_id: str) -> Record | None:
        for record in self.records:
            if record.instance == instance_id and record.kind == "table" and not record.deleted:
                return record
        return None

    def _grow(self, checkpoint: str, instance_id: str) -> None:
        state = self.tables[instance_id]
        suffix = int(checkpoint.rsplit("_", 1)[1])
        target = suffix if "_ABS_" in checkpoint else self.baselines[state.role] + suffix
        while len(self.file) < target:
            if state.rows + ROW_BATCH > MAX_ROWS:
                raise RuntimeError("row cap reached in synthetic growth")
            for _ in range(self.rows_per_batch_pages):
                page = self._append(self._data_page(state.role))
                state.data_pages.append(page)
                state.owned.add(page)
            state.rows += ROW_BATCH
        self._stamp(instance_id)

    def _stamp(self, instance_id: str) -> None:
        record = self._table_record(instance_id)
        if record is not None:
            record.stamp += 1

    # ------------------------------------------------------------------ map rendering
    def _visible(self, pages: set[int]) -> set[int]:
        limit = self.params.omit_pages_at_or_above
        return {p for p in pages if limit is None or p < limit}

    def _type0_fits(self, state: TableState) -> bool:
        owned, available = self._visible(state.owned), self._visible(state.available)
        if self.params.available_equals_owned:
            available = set(owned)
        len0 = 5 + math.ceil((max(owned) - state.map + 1) / 8) if owned else 5
        len1 = 5 + math.ceil((max(available) - state.map + 1) / 8) if available else 5
        return DIRECTORY_RESERVE + len0 + len1 <= PAGE_SIZE

    def _slot_of(self, page: int) -> tuple[int, int]:
        """(slot ordinal, referenced page) for an owned page under this replica's tag-05 mode."""
        block = page // TAG05_BITS
        if self.mode == "slot":
            return block, -1
        if self.mode == "equals_slot":
            return block, block
        return block - 1, block  # referenced: slot k references page k+1

    def _reference(self, state: TableState, slot: int) -> int:
        if slot not in state.tag05:
            if self.mode == "slot":
                state.tag05[slot] = self._append(tag05_page(set()))
            elif self.mode == "equals_slot":
                state.tag05[slot] = slot
            else:
                state.tag05[slot] = slot + 1
        return state.tag05[slot]

    def _activate(self, state: TableState, pages: set[int]) -> list[int]:
        """Return the u32 slot values for a converted table, allocating references as needed."""
        for page in sorted(pages):
            self._reference(state, self._slot_of(page)[0])
        if self.params.synthetic_boundary_bits and state.tag05:
            self._reference(state, min(state.tag05) + 1)
        return [state.tag05.get(i, 0) for i in range(max(state.tag05) + 1)] if state.tag05 else []

    def _render_map(self, checkpoint: str, state: TableState) -> None:
        owned, available = self._visible(state.owned), self._visible(state.available)
        forced = self.params.force_conversion_at.get(state.instance)
        convert = self.params.conversion != "never" and (
            state.converted or not self._type0_fits(state) or (forced is not None and ORDINAL[checkpoint] >= ORDINAL[forced]))
        if self.params.available_equals_owned:
            available = set() if convert else set(owned)
        if convert:
            state.converted = True
            references = self._activate(state, owned)
            count = len(references) if self.params.type1_exact_slots else TYPE1_SLOTS
            owned_row = type1_row(references, count)
        else:
            if self.params.conversion == "never":
                capacity = (PAGE_SIZE - DIRECTORY_RESERVE - 10) * 8
                owned = {p for p in owned if p - state.map < capacity}
            owned_row = type0_row(state.map, owned)
        available_row = owned_row if self.params.available_equals_owned else type0_row(state.map, available)
        rows = [owned_row, available_row] if self.params.owned_ordinal(self.replica) == 0 else [available_row, owned_row]
        self.file[state.map] = data_page(rows)

    def _render_tag05(self) -> None:
        bits: dict[int, set[int]] = {}
        for state in self.tables.values():
            if not state.converted or state.dropped:
                continue
            for page in self._visible(state.owned):
                slot, _ = self._slot_of(page)
                reference = state.tag05[slot]
                base = reference if self.mode == "referenced" else slot
                bits.setdefault(reference, set()).add(page - base * TAG05_BITS + self.params.tag05_bit_shift)
            if self.params.synthetic_boundary_bits and state.tag05:
                low = min(state.tag05)
                bits.setdefault(state.tag05[low], set()).add(TAG05_BITS - 1)
                bits.setdefault(state.tag05[low + 1], set()).add(0)
        for page, extra in self.params.extra_tag05_bits.items():
            bits.setdefault(page, set()).update(extra)
        for page, values in bits.items():
            self.file[page] = tag05_page({b for b in values if 0 <= b < TAG05_BITS})

    # ------------------------------------------------------------------ checkpoint walk
    def build(self) -> ReplicaData:
        for event in EVENTS:
            cp = event.checkpoint
            if event.kind == "empty":
                self._build_empty()
            elif event.kind == "create":
                self._create(cp, event.instance or "")
            elif event.kind == "add_field":
                self.tables[event.instance or ""].version += 1
                self._record(cp, event.instance or "", "field", self._allocate_id("field"))
            elif event.kind == "add_index":
                self.tables[event.instance or ""].version += 1
                self._record(cp, event.instance or "", "index", self._allocate_id("index"))
            elif event.kind == "drop":
                self._drop("T2-v1")
            elif event.kind == "grow":
                self._grow(cp, event.instance or "")
            elif event.kind == "delete_all":
                state = self.tables[event.instance or ""]
                state.available = set(state.data_pages)
                state.rows = 0
                self._stamp(state.instance)
            elif event.kind == "reinsert":
                state = self.tables[event.instance or ""]
                state.available = set()
                state.rows = self._reinsert_rows(state)
                self._stamp(state.instance)
            if event.kind in ("create", "add_field", "add_index", "drop"):
                self.op_counter += 1
            self._render(cp)
            self._capture(cp)
            if cp == "T4_CREATE":
                self.baselines["T1"] = len(self.file)
            if cp == "T3_ABS_16480":
                self.baselines["T4"] = len(self.file)
        return self.data

    def _reinsert_rows(self, state: TableState) -> int:
        return self.data.row_counts["T1_REL_1280"][state.role]

    def _drop(self, instance_id: str) -> None:
        state = self.tables[instance_id]
        state.dropped = True
        self.file[state.tdef] = empty_data_page()
        self.file[state.map] = empty_data_page()
        for record in self.records:
            if record.instance == instance_id:
                record.deleted = True

    def _render(self, checkpoint: str) -> None:
        for state in self.tables.values():
            if not state.dropped:
                self.file[state.tdef] = self.file[state.tdef][:12] + state.version.to_bytes(2, "little") + self.file[state.tdef][14:] \
                    if self.params.tdef_style != "opaque" else self.file[state.tdef]
                self._render_map(checkpoint, state)
        self._render_tag05()
        self._render_system()

    def _capture(self, checkpoint: str) -> None:
        self.data.pages[checkpoint] = [self.campaign.store(page) for page in self.file]
        self.data.row_counts[checkpoint] = {s.role: s.rows for s in self.tables.values() if not s.dropped}
        self.data.meta = {
            "system": dict(self.system),
            "instances": {k: {"tdef": s.tdef, "map": s.map, "data_pages": list(s.data_pages), "tag05": dict(s.tag05),
                              "converted": s.converted} for k, s in self.tables.items()},
            "baselines": dict(self.baselines),
            "role_binding": dict(ROLE_BINDINGS[self.replica]),
        }


def generate(params: Params | None = None, replicas: tuple[int, ...] = (1, 2, 3)) -> Campaign:
    params = params or Params()
    campaign = Campaign()
    for replica in replicas:
        campaign.replicas[replica] = ReplicaBuilder(campaign, params, replica).build()
        campaign.refresh(replica)
    return campaign


def overshoots(campaign: Campaign, replica: int) -> dict[str, int]:
    """Achieved minus threshold for every growth checkpoint (relative_growth_rule / absolute_growth_rule)."""
    data = campaign.replicas[replica]
    out = {}
    for cp in CHECKPOINTS:
        event = EVENT_BY_CHECKPOINT[cp]
        if event.kind != "grow":
            continue
        suffix = int(cp.rsplit("_", 1)[1])
        baseline = 0 if "_ABS_" in cp else data.meta["baselines"][event.role or ""]
        out[cp] = len(data.pages[cp]) - (baseline + suffix)
    return out
