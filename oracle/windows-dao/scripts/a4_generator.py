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
    force_conversion_checkpoint_by_role: Mapping[str, str] = field(default_factory=dict)
    initial_filler_delta: int = 0
    post_reservation_filler_delta: int = 0
    omit_blob_digest: str | None = None
    user_tdef_tag: int = TAG_TDEF
    tdef_style: str = "signature"
    locator_target: str = "map"
    locator_target_page: int | None = None
    decoy_tdef_pages: Mapping[str, int] = field(default_factory=dict)
    available_equals_owned: bool = False
    conversion: str = "capacity"
    never_convert_roles: frozenset[str] = frozenset()
    type_1_exact_slots: bool = False
    type_1_slot_count: int | None = None
    omit_pages_at_or_above: int | None = None
    omit_pages_below_by_role: Mapping[str, int] = field(default_factory=dict)
    tag_05_mode_by_replica: Mapping[int, str] = field(default_factory=dict)
    tag_05_bit_shift: int = 0
    tag_05_bit_shift_by_replica: Mapping[int, int] = field(default_factory=dict)
    synthetic_boundary_bits: bool = False
    catalog_page_default: str = "catalog"
    catalog_page_override: Mapping[str, str] = field(default_factory=dict)
    catalog_page_override_by_replica: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    decoy_follows_operations: bool = False
    spare_noise_at: frozenset[str] = frozenset()
    catalog_header_noise_at: frozenset[str] = frozenset()
    skip_catalog_record_at: frozenset[str] = frozenset()
    duplicate_catalog_record_at: frozenset[str] = frozenset()
    catalog_kind_override: Mapping[str, int] = field(default_factory=dict)
    catalog_record_layout: str = "single"
    e_acute_length_override: int | None = None
    e_acute_double_occurrence: bool = False
    t2_identifier_relation_by_replica: Mapping[int, str] = field(default_factory=dict)
    holdout_name_corruption: bool = False

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
        if any(value not in CHECKPOINT_IDS for value in self.force_conversion_checkpoint_by_role.values()):
            raise ValidationError("A4 synthetic role conversion checkpoint is unknown")
        if self.initial_filler_delta < 0 or self.initial_filler_delta > 4096:
            raise ValidationError("A4 synthetic filler delta is outside its local bound")
        if not 0 <= self.post_reservation_filler_delta <= 4096:
            raise ValidationError("A4 synthetic post-reservation filler is outside its bound")
        if self.omit_blob_digest is not None and (
            len(self.omit_blob_digest) != 64
            or any(value not in "0123456789abcdef" for value in self.omit_blob_digest)
        ):
            raise ValidationError("A4 omitted blob identity is not canonical SHA-256")
        if self.user_tdef_tag < 0 or self.user_tdef_tag > 255:
            raise ValidationError("A4 synthetic TDEF tag is outside u8")
        if self.tdef_style not in {"signature", "opaque", "broken_signature"}:
            raise ValidationError("A4 synthetic TDEF style is unknown")
        if self.locator_target not in {"map", "tdef"}:
            raise ValidationError("A4 synthetic locator target is unknown")
        if self.locator_target_page is not None and not 0 <= self.locator_target_page <= 20479:
            raise ValidationError("A4 synthetic locator target page is outside its bound")
        if self.conversion not in {"capacity", "never"}:
            raise ValidationError("A4 synthetic conversion policy is unknown")
        if self.type_1_slot_count is not None and not 1 <= self.type_1_slot_count <= 64:
            raise ValidationError("A4 synthetic type-1 slot count is outside its bound")
        if any(value not in {"slot", "equals_slot", "referenced"} for value in self.tag_05_mode_by_replica.values()):
            raise ValidationError("A4 synthetic tag-05 mode is unknown")
        if self.catalog_page_default not in {"catalog", "spare"}:
            raise ValidationError("A4 synthetic catalog page is unknown")
        if self.catalog_record_layout not in {"single", "double"}:
            raise ValidationError("A4 synthetic catalog layout is unknown")
        if self.omit_pages_at_or_above is not None and self.omit_pages_at_or_above < 0:
            raise ValidationError("A4 synthetic visible-page limit is negative")


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
    page_slot: str = "catalog"


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
        self.instance_object_ids: dict[str, int] = {}
        self.system: dict[str, int] = {}
        self.reserved: dict[str, tuple[int, int]] = {}
        self.baselines: dict[str, int] = {}
        self.inserted_rows = 0
        self.current_checkpoint = ""

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
        self.system["spare"] = 1
        for _ in range(self.profile.initial_filler_pages + self.parameters.initial_filler_delta):
            self._append(empty_page())
        for instance in SCHEDULE.instances:
            self.reserved[instance.instance_id] = (
                self._append(empty_page()),
                self._append(empty_page()),
            )
        for _ in range(self.parameters.post_reservation_filler_delta):
            self._append(data_page((b"\x00", b"\x01")))
        if (
            self.parameters.catalog_page_default == "spare"
            or self.parameters.decoy_follows_operations
            or self.parameters.spare_noise_at
            or "spare" in self.parameters.catalog_page_override.values()
            or any(
                "spare" in overrides.values()
                for overrides in self.parameters.catalog_page_override_by_replica.values()
            )
        ):
            self.system["spare"] = self._append(empty_page())
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
        decoy_owned = (
            {decoy_map, self.system["spare"]}
            if self.parameters.decoy_follows_operations
            else {decoy_map}
        )
        decoy_available = type_0_row(
            decoy_map, (), polarity=self.parameters.type_0_polarity
        )
        self.pages[decoy_map] = data_page(
            (type_0_row(decoy_map, decoy_owned), decoy_available), raw_flags=flags
        )
        for slot, page_number in (
            ("catalog", catalog),
            ("spare", self.system["spare"]),
        ):
            rows = tuple(
                self._catalog_row(record)
                for record in self.catalog_records
                if record.page_slot == slot
            )
            if slot == "spare" and self.parameters.post_reservation_filler_delta:
                rows += (b"\x00", b"\x01")
            if slot == "spare":
                rows += tuple(
                    b"\x7f" + record.checkpoint_id.encode("ascii")
                    for record in self.catalog_records
                    if record.checkpoint_id in self.parameters.spare_noise_at
                )
            if slot == "spare" and self.parameters.decoy_follows_operations:
                rows += (b"\x01" + len(self.catalog_records).to_bytes(2, "little"),)
            rendered = data_page(rows)
            if (
                slot == "catalog"
                and self.current_checkpoint in self.parameters.catalog_header_noise_at
            ):
                changed = bytearray(rendered)
                changed[2] = 1
                rendered = bytes(changed)
            self.pages[page_number] = rendered

    def _catalog_row(self, record: CatalogRecord) -> bytes:
        kind = self.parameters.catalog_kind_override.get(
            record.checkpoint_id, CATALOG_KINDS[record.object_kind]
        )
        name = _strict_name_bytes(record.name)
        if len(name) > 0xFFFF:
            raise ValidationError("A4 synthetic catalog name is too long")
        if (
            self.parameters.holdout_name_corruption
            and self.replica == 3
            and b"\xc9" in name
        ):
            name = name[:-1] + b"5"
        stored_length = len(name)
        if (
            name != record.name.encode("utf-8")
            and self.parameters.e_acute_length_override is not None
        ):
            stored_length = self.parameters.e_acute_length_override
        prefix = bytes([record.object_id, kind])
        if self.parameters.catalog_record_layout == "double":
            prefix += bytes([record.object_id ^ 0x40, kind])
        row = prefix + bytes([stored_length]) + name
        if (
            self.parameters.e_acute_double_occurrence
            and name != record.name.encode("utf-8")
        ):
            utf8 = record.name.encode("utf-8")
            row += bytes([record.object_id, kind, len(utf8)]) + utf8
        return row

    def _name_for(self, checkpoint_id: str, role: str) -> str:
        binding = next(item for item in PLAN["tables"]["role_bindings"] if item["replica"] == self.replica)
        if checkpoint_id == "T1_ADD_TEXT":
            return "Payload"
        if checkpoint_id == "T1_ADD_INDEX":
            return "A4IX_ID"
        return str(binding[role])

    def _record(self, checkpoint_id: str, instance_id: str, kind: str) -> None:
        if checkpoint_id in self.parameters.skip_catalog_record_at:
            return
        role = SCHEDULE.instance(instance_id).role
        ordinal = len(self.catalog_records)
        object_id = 0x21 + ordinal
        relation = self.parameters.t2_identifier_relation_by_replica.get(
            self.replica
        )
        if instance_id == "T2-v2" and relation == "same":
            object_id = self.instance_object_ids["T2-v1"]
        elif instance_id == "T2-v2" and relation == "distinct":
            object_id = 0x70
        if kind == "table":
            self.instance_object_ids[instance_id] = object_id
        overrides = dict(self.parameters.catalog_page_override)
        overrides.update(
            self.parameters.catalog_page_override_by_replica.get(self.replica, {})
        )
        record = CatalogRecord(
            checkpoint_id,
            instance_id,
            kind,
            object_id,
            self._name_for(checkpoint_id, role),
            overrides.get(checkpoint_id, self.parameters.catalog_page_default),
        )
        self.catalog_records.append(record)
        if checkpoint_id in self.parameters.duplicate_catalog_record_at:
            self.catalog_records.append(record)

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

    def _tdef_page(self, tdef_page: int, map_page: int, version: int) -> bytes:
        target = (
            self.parameters.locator_target_page
            if self.parameters.locator_target_page is not None
            else tdef_page
            if self.parameters.locator_target == "tdef"
            else map_page
        )
        if self.parameters.tdef_style == "opaque":
            page = bytearray([0xA5] * PAGE_SIZE)
            page[0] = self.parameters.user_tdef_tag
            return bytes(page)
        page = bytearray(
            masked_tdef_page(
                self.parameters.signature_id,
                self._locators(target),
                version=version,
            )
        )
        page[0] = self.parameters.user_tdef_tag
        if self.parameters.tdef_style == "broken_signature":
            page[2] ^= 1
        return bytes(page)

    def _create(self, checkpoint_id: str, instance_id: str) -> None:
        role = SCHEDULE.instance(instance_id).role
        tdef_page, map_page = self.reserved[instance_id]
        state = TableState(instance_id, role, tdef_page, map_page, {map_page})
        self.tables[instance_id] = state
        self.pages[tdef_page] = self._tdef_page(
            tdef_page, map_page, state.version
        )
        for _ in range(self.parameters.decoy_tdef_pages.get(checkpoint_id, 0)):
            self._append(self._tdef_page(tdef_page, map_page, state.version))
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
        forced_checkpoint = self.parameters.force_conversion_checkpoint_by_role.get(
            state.role,
            self.parameters.force_t3_conversion_checkpoint
            if state.role == "T3"
            else "",
        )
        force_t3 = bool(forced_checkpoint) and CHECKPOINT_IDS.index(
            checkpoint_id
        ) >= CHECKPOINT_IDS.index(forced_checkpoint)
        if (
            self.parameters.conversion != "never"
            and state.role not in self.parameters.never_convert_roles
            and (
            state.converted or force_t3 or projected_row_bytes > PAGE_SIZE
            )
        ):
            state.converted = True
        first_batch = state.row_count == 0
        while len(self.pages) < target or first_batch:
            first_batch = False
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
        if state.converted:
            self._prepare_tag05_slots(state, len(self.pages))
        state.retained_row_count = state.row_count

    def _prepare_tag05_slots(self, state: TableState, achieved_pages: int) -> None:
        """Allocate required map pages as part of the current complete batch."""
        while True:
            highest = max((*state.owned_pages, achieved_pages - 1), default=0)
            required = set(range(highest // TAG05_BITS + 1))
            if self.parameters.synthetic_boundary_bits and required:
                required.add(max(required) + 1)
            missing = sorted(required - set(state.tag05_pages))
            if not missing:
                return
            for slot in missing:
                state.tag05_pages[slot] = self._append(tag_05_page(()))

    def _should_convert(self, checkpoint_id: str, state: TableState) -> bool:
        if state.role in self.parameters.never_convert_roles:
            return False
        forced_checkpoint = self.parameters.force_conversion_checkpoint_by_role.get(
            state.role,
            self.parameters.force_t3_conversion_checkpoint
            if state.role == "T3"
            else "",
        )
        if forced_checkpoint and CHECKPOINT_IDS.index(checkpoint_id) >= CHECKPOINT_IDS.index(forced_checkpoint):
            return True
        owned_bits = max(state.owned_pages, default=state.map_page) - state.map_page + 1
        available_bits = max(state.available_pages, default=state.map_page) - state.map_page + 1
        serialized = 10 + math.ceil(owned_bits / 8) + math.ceil(available_bits / 8)
        return 14 + serialized > PAGE_SIZE

    def _render_tag05(self, state: TableState) -> tuple[int, ...]:
        visible_owned = self._visible(state, state.owned_pages)
        slots = sorted({page // TAG05_BITS for page in visible_owned})
        if self.parameters.synthetic_boundary_bits:
            slots = sorted(set(slots) | set(state.tag05_pages))
        if not slots:
            return ()
        for slot in slots:
            if slot not in state.tag05_pages:
                raise ValidationError("A4 synthetic tag-05 page was not allocated during growth")
        references = [0] * (max(slots) + 1)
        mode = self.parameters.tag_05_mode_by_replica.get(self.replica, "slot")
        for slot in slots:
            reference = state.tag05_pages[slot]
            if mode == "equals_slot":
                reference = slot
            elif mode == "referenced":
                reference = slot + 1
            references[slot] = reference
            base = reference if mode == "referenced" else slot
            bits = {
                page
                - base * TAG05_BITS
                + self.parameters.tag_05_bit_shift_by_replica.get(
                    self.replica, self.parameters.tag_05_bit_shift
                )
                for page in visible_owned
                if page // TAG05_BITS == slot
            }
            if self.parameters.synthetic_boundary_bits:
                bits.update({0, TAG05_BITS - 1})
            if not 0 <= reference < len(self.pages):
                raise ValidationError("A4 synthetic tag-05 reference is outside the file")
            self.pages[reference] = tag_05_page(
                bit for bit in bits if 0 <= bit < TAG05_BITS
            )
        return tuple(references)

    def _visible(self, state: TableState, pages: set[int]) -> set[int]:
        limit = self.parameters.omit_pages_at_or_above
        floor = self.parameters.omit_pages_below_by_role.get(state.role, 0)
        return {
            page
            for page in pages
            if page >= floor and (limit is None or page < limit)
        }

    def _render_table(self, checkpoint_id: str, state: TableState) -> None:
        if state.dropped:
            return
        state.converted = (
            self.parameters.conversion != "never"
            and state.role not in self.parameters.never_convert_roles
            and (state.converted or self._should_convert(checkpoint_id, state))
        )
        visible_owned = self._visible(state, state.owned_pages)
        visible_available = self._visible(state, state.available_pages)
        if state.converted:
            self._prepare_tag05_slots(state, len(self.pages))
            references = self._render_tag05(state)
            owned_row = type_1_row(
                references,
                slot_count=(
                    len(references)
                    if self.parameters.type_1_exact_slots
                    else self.parameters.type_1_slot_count or 3
                ),
            )
        else:
            capacity_limit = ((PAGE_SIZE - 24) // 2) * 8
            visible_owned = {
                page
                for page in visible_owned
                if page - state.map_page < capacity_limit
            }
            visible_available = {
                page
                for page in visible_available
                if page - state.map_page < capacity_limit
            }
            capacity = (
                max(visible_owned | visible_available, default=state.map_page)
                - state.map_page
                + 1
            )
            owned_row = type_0_row(
                state.map_page,
                visible_owned,
                polarity=self.parameters.type_0_polarity,
                capacity_bits=capacity,
            )
        available_row = type_0_row(
            state.map_page,
            visible_available,
            polarity=self.parameters.type_0_polarity,
            capacity_bits=(
                max(visible_available, default=state.map_page)
                - state.map_page
                + 1
            ),
        )
        if self.parameters.available_equals_owned:
            available_row = owned_row
        rows = (owned_row, available_row)
        if self.parameters.owned_ordinal(self.replica) == 1:
            rows = tuple(reversed(rows))
        flags = {0: self.parameters.row_flag, 1: self.parameters.row_flag}
        self.pages[state.map_page] = data_page(rows, raw_flags=flags)
        self.pages[state.tdef_page] = self._tdef_page(
            state.tdef_page, state.map_page, state.version
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
            self.current_checkpoint = checkpoint_id
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
            if self.parameters.tag_05_mode_by_replica.get(self.replica, "slot") == "slot":
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
