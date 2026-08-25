#!/usr/bin/env python3
"""Plan-registered A4 byte fixtures with no stored verdict fields."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping

from a4_generator import SyntheticParameters, SyntheticReplica
from a4_generator_pages import data_page, tag_05_page
from a4_spec import CHECKPOINT_IDS, PLAN, PREDICATE_CONTRACTS
from protocol_validation import canonical_json_bytes


Patch = Callable[[SyntheticReplica], SyntheticReplica]


def _rows(page: bytes) -> tuple[list[bytes], dict[int, int]]:
    count = int.from_bytes(page[8:10], "little")
    starts = [
        int.from_bytes(page[10 + 2 * slot : 12 + 2 * slot], "little") & 0x0FFF
        for slot in range(count)
    ]
    ends = [len(page), *starts[:-1]]
    return (
        [page[start:end] for start, end in zip(starts, ends, strict=True)],
        {
            slot: int.from_bytes(
                page[10 + 2 * slot : 12 + 2 * slot], "little"
            )
            & 0xF000
            for slot in range(count)
        },
    )


def _rebuild(
    page: bytes,
    transform: Callable[[list[bytes]], list[bytes]],
    flags: Mapping[int, int] | None = None,
) -> bytes:
    rows, existing = _rows(page)
    return data_page(transform(rows), raw_flags=flags or existing)


def _patch_from(
    source: SyntheticReplica,
    first_checkpoint: str,
    page_number: int,
    transform: Callable[[bytes], bytes],
) -> SyntheticReplica:
    ordered = dict(source.ordered_page_sha256)
    payloads = dict(source.payloads)
    transformed: dict[str, str] = {}
    first = CHECKPOINT_IDS.index(first_checkpoint)
    for checkpoint in CHECKPOINT_IDS[first:]:
        sequence = list(ordered[checkpoint])
        if page_number >= len(sequence):
            continue
        digest = sequence[page_number]
        replacement = transformed.get(digest)
        if replacement is None:
            payload = transform(source.page_bytes(digest))
            if len(payload) != 2048:
                raise ValueError("A4 dry-run mutation changed the page size")
            replacement = hashlib.sha256(payload).hexdigest()
            transformed[digest] = replacement
            payloads[replacement] = payload
        sequence[page_number] = replacement
        ordered[checkpoint] = tuple(sequence)
    return replace(
        source,
        ordered_page_sha256=MappingProxyType(ordered),
        payloads=MappingProxyType(payloads),
    )


def _instance(source: SyntheticReplica, name: str) -> Mapping[str, Any]:
    return source.metadata["lifecycle_pages"][name]  # type: ignore[index,return-value]


def idle_byte(source: SyntheticReplica) -> SyntheticReplica:
    digest = source.ordered_page_sha256["EMPTY_R"][1]
    payload = bytearray(source.page_bytes(digest))
    payload[100] ^= 1
    ordered = dict(source.ordered_page_sha256)
    payloads = dict(source.payloads)
    replacement = hashlib.sha256(payload).hexdigest()
    payloads[replacement] = bytes(payload)
    sequence = list(ordered["EMPTY_R"])
    sequence[1] = replacement
    ordered["EMPTY_R"] = tuple(sequence)
    return replace(
        source,
        ordered_page_sha256=MappingProxyType(ordered),
        payloads=MappingProxyType(payloads),
    )


def directory_overlap(source: SyntheticReplica) -> SyntheticReplica:
    page = int(_instance(source, "T3-v1")["map_page"])

    def mutate(payload: bytes) -> bytes:
        value = bytearray(payload)
        value[12:14] = (12).to_bytes(2, "little")
        return bytes(value)

    return _patch_from(source, "T3_CREATE", page, mutate)


def deleted_row(source: SyntheticReplica) -> SyntheticReplica:
    page = int(_instance(source, "T3-v1")["map_page"])
    return _patch_from(
        source,
        "T3_CREATE",
        page,
        lambda payload: _rebuild(
            payload, lambda rows: rows, {0: 0x8000}
        ),
    )


def unsupported_map_tag(source: SyntheticReplica) -> SyntheticReplica:
    page = int(_instance(source, "T3-v1")["map_page"])

    def mutate(payload: bytes) -> bytes:
        return _rebuild(
            payload,
            lambda rows: [b"\x02" + rows[0][1:], *rows[1:]],
        )

    return _patch_from(source, "T3_CREATE", page, mutate)


def _set_type_0_bit(row: bytes, page_number: int, value: bool) -> bytes:
    base = int.from_bytes(row[1:5], "little")
    bit = page_number - base
    body = bytearray(row[5:])
    while len(body) * 8 <= bit:
        body.append(0)
    mask = 1 << (bit & 7)
    if value:
        body[bit >> 3] |= mask
    else:
        body[bit >> 3] &= ~mask & 0xFF
    return row[:5] + bytes(body)


def role_none(source: SyntheticReplica) -> SyntheticReplica:
    instance = _instance(source, "T1-v1")
    map_page = int(instance["map_page"])
    foreign = map_page + 1

    def mutate(payload: bytes) -> bytes:
        return _rebuild(
            payload,
            lambda rows: [rows[0], _set_type_0_bit(rows[1], foreign, True)],
        )

    return _patch_from(source, "T1_CREATE_ID", map_page, mutate)


def transition_drop(source: SyntheticReplica) -> SyntheticReplica:
    instance = _instance(source, "T1-v1")
    map_page = int(instance["map_page"])
    dropped = int(instance["data_pages"][0])

    def mutate(payload: bytes) -> bytes:
        def rows_transform(rows: list[bytes]) -> list[bytes]:
            return [
                _set_type_0_bit(rows[0], dropped, False),
                _set_type_0_bit(rows[1], dropped, False),
            ]

        return _rebuild(payload, rows_transform)

    return _patch_from(source, "T1_REL_0512", map_page, mutate)


def reference_to_data(source: SyntheticReplica) -> SyntheticReplica:
    instance = _instance(source, "T3-v1")
    map_page = int(instance["map_page"])
    data_page_number = int(instance["data_pages"][0])

    def mutate(payload: bytes) -> bytes:
        def rows_transform(rows: list[bytes]) -> list[bytes]:
            row = bytearray(rows[0])
            if row and row[0] == 1:
                row[1:5] = data_page_number.to_bytes(4, "little")
            return [bytes(row), *rows[1:]]

        return _rebuild(payload, rows_transform)

    return _patch_from(source, "T3_ABS_08192", map_page, mutate)


def holdout_bitmap_clear(source: SyntheticReplica) -> SyntheticReplica:
    instance = _instance(source, "T3-v1")
    map_page = int(instance["map_page"])
    references = instance["tag05_pages"]
    reference = int(next(iter(references.values())))

    def mutate(payload: bytes) -> bytes:
        bits = {
            index
            for index in range((len(payload) - 4) * 8)
            if payload[4 + index // 8] & (1 << (index & 7))
        }
        bits.discard(map_page)
        return tag_05_page(bits)

    return _patch_from(source, "T3_ABS_08192", reference, mutate)


PATCHES: Mapping[str, Patch] = MappingProxyType(
    {
        function.__name__: function
        for function in (
            idle_byte,
            directory_overlap,
            deleted_row,
            unsupported_map_tag,
            role_none,
            transition_drop,
            reference_to_data,
            holdout_bitmap_clear,
        )
    }
)


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    parameters: SyntheticParameters = field(default_factory=SyntheticParameters)
    patches_by_replica: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    surface_patches_by_replica: Mapping[int, tuple[str, ...]] = field(default_factory=dict)

    def mutation_document(self) -> dict[str, Any]:
        return {
            "generator_parameters": _jsonable(asdict(self.parameters)),
            "patches_by_replica": {
                str(replica): list(names)
                for replica, names in sorted(self.patches_by_replica.items())
            },
            "surface_patches_by_replica": {
                str(replica): list(names)
                for replica, names in sorted(self.surface_patches_by_replica.items())
            },
        }

    def mutation_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.mutation_document())).hexdigest()

    def source(self, replica: int, generated: SyntheticReplica) -> SyntheticReplica:
        value = generated
        for name in self.patches_by_replica.get(replica, ()):
            value = PATCHES[name](value)
        return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def fixture_id(predicate_id: str) -> str:
    return str(PREDICATE_CONTRACTS[predicate_id]["reachability_fixture_id"])


_H1 = PLAN["candidate_grammars"]["h1"]
_DUPLICATE = _H1["pair_multiple_reachability_signature"]


FIXTURES: Mapping[str, Fixture] = MappingProxyType(
    {
        "A4-IDLE-EQUALITY": Fixture(fixture_id("A4-IDLE-EQUALITY"), patches_by_replica={1: ("idle_byte",)}),
        "A4-SCHEMA-SNAPSHOT": Fixture(fixture_id("A4-SCHEMA-SNAPSHOT"), surface_patches_by_replica={1: ("schema_ordinal",)}),
        "A4-SNAPSHOT-RECONSTRUCTION": Fixture(fixture_id("A4-SNAPSHOT-RECONSTRUCTION"), surface_patches_by_replica={1: ("page_index_digest",)}),
        "A4-RESOURCE-BOUND": Fixture(fixture_id("A4-RESOURCE-BOUND"), surface_patches_by_replica={1: ("changed_entries_one_over",)}),
        "A4-H1-TDEF-NONE": Fixture(fixture_id("A4-H1-TDEF-NONE"), SyntheticParameters(user_tdef_tag=3)),
        "A4-H1-TDEF-MULTIPLE": Fixture(fixture_id("A4-H1-TDEF-MULTIPLE"), SyntheticParameters(decoy_tdef_pages={"T3_CREATE": 1})),
        "A4-H1-LOCATOR-LAYOUT-NONE": Fixture(fixture_id("A4-H1-LOCATOR-LAYOUT-NONE"), SyntheticParameters(tdef_style="opaque")),
        "A4-H1-LOCATOR-PAIR-NONE": Fixture(fixture_id("A4-H1-LOCATOR-PAIR-NONE"), SyntheticParameters(tdef_style="broken_signature")),
        "A4-H1-TARGET-ROW-INVALID": Fixture(fixture_id("A4-H1-TARGET-ROW-INVALID"), SyntheticParameters(locator_target="tdef")),
        "A4-H1-LOCATOR-LAYOUT-MULTIPLE": Fixture(fixture_id("A4-H1-LOCATOR-LAYOUT-MULTIPLE"), SyntheticParameters(post_reservation_filler_delta=300, locator_target_page=1)),
        "A4-H1-LOCATOR-PAIR-MULTIPLE": Fixture(
            fixture_id("A4-H1-LOCATOR-PAIR-MULTIPLE"),
            SyntheticParameters(
                signature_id=_DUPLICATE["signature_id"],
                locator_offsets=tuple(interval[0] for interval in _DUPLICATE["locator_holes"]),
            ),
        ),
        "A4-H1-REPLICA-DISAGREEMENT": Fixture(fixture_id("A4-H1-REPLICA-DISAGREEMENT"), SyntheticParameters(layout_by_replica={2: _H1["locator_layouts"][0]})),
        "A4-H2-ROW-DIRECTORY-INVALID": Fixture(fixture_id("A4-H2-ROW-DIRECTORY-INVALID"), patches_by_replica={1: ("directory_overlap",)}),
        "A4-H2-ROW-FLAGS-INVALID": Fixture(fixture_id("A4-H2-ROW-FLAGS-INVALID"), patches_by_replica={1: ("deleted_row",)}),
        "A4-H2-MAP-TAG-UNSUPPORTED": Fixture(fixture_id("A4-H2-MAP-TAG-UNSUPPORTED"), patches_by_replica={1: ("unsupported_map_tag",)}),
        "A4-H2-ROLE-NONE": Fixture(fixture_id("A4-H2-ROLE-NONE"), patches_by_replica={1: ("role_none",)}),
        "A4-H2-ROLE-MULTIPLE": Fixture(fixture_id("A4-H2-ROLE-MULTIPLE"), SyntheticParameters(available_equals_owned=True)),
        "A4-H2-TRANSITION-UNEXPLAINED": Fixture(fixture_id("A4-H2-TRANSITION-UNEXPLAINED"), patches_by_replica={1: ("transition_drop",)}),
        "A4-H2-REPLICA-DISAGREEMENT": Fixture(fixture_id("A4-H2-REPLICA-DISAGREEMENT"), SyntheticParameters(owned_ordinal_by_replica={2: 1})),
        "A4-H3-CONVERSION-NONE": Fixture(fixture_id("A4-H3-CONVERSION-NONE"), SyntheticParameters(conversion="never")),
        "A4-H3-INACTIVE-SLOT-NONE": Fixture(fixture_id("A4-H3-INACTIVE-SLOT-NONE"), SyntheticParameters(type_1_exact_slots=True)),
        "A4-H3-REFERENCE-INVALID": Fixture(fixture_id("A4-H3-REFERENCE-INVALID"), patches_by_replica={1: ("reference_to_data",)}),
        "A4-H3-BASE-DISCRIMINATION": Fixture(fixture_id("A4-H3-BASE-DISCRIMINATION"), SyntheticParameters(omit_pages_at_or_above=16352)),
        "A4-H3-BASE-NONE": Fixture(fixture_id("A4-H3-BASE-NONE"), SyntheticParameters(tag_05_bit_shift=2, synthetic_boundary_bits=True)),
        "A4-H3-BASE-MULTIPLE": Fixture(
            fixture_id("A4-H3-BASE-MULTIPLE"),
            SyntheticParameters(
                tag_05_mode_by_replica={1: "equals_slot", 2: "equals_slot", 3: "equals_slot"},
                force_conversion_checkpoint_by_role={"T4": "T4_REL_0064"},
                never_convert_roles=frozenset({"T3"}),
                omit_pages_below_by_role={"T4": 16352},
                synthetic_boundary_bits=True,
            ),
        ),
        "A4-H3-REPLICA-DISAGREEMENT": Fixture(fixture_id("A4-H3-REPLICA-DISAGREEMENT"), SyntheticParameters(tag_05_bit_shift_by_replica={2: 1}, synthetic_boundary_bits=True)),
        "A4-H4-CATALOG-ROOT-NONE": Fixture(fixture_id("A4-H4-CATALOG-ROOT-NONE"), SyntheticParameters(catalog_page_default="spare")),
        "A4-H4-CATALOG-ROOT-MULTIPLE": Fixture(fixture_id("A4-H4-CATALOG-ROOT-MULTIPLE"), SyntheticParameters(decoy_follows_operations=True)),
        "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED": Fixture(fixture_id("A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED"), SyntheticParameters(spare_noise_at=frozenset({"T3_CREATE"}))),
        "A4-H4-CATALOG-RECORD-NONE": Fixture(fixture_id("A4-H4-CATALOG-RECORD-NONE"), SyntheticParameters(skip_catalog_record_at=frozenset({"T3_CREATE"}), catalog_header_noise_at=frozenset({"T3_CREATE"}))),
        "A4-H4-CATALOG-RECORD-MULTIPLE": Fixture(fixture_id("A4-H4-CATALOG-RECORD-MULTIPLE"), SyntheticParameters(duplicate_catalog_record_at=frozenset({"T3_CREATE"}))),
        "A4-H4-FIELD-MODEL-NONE": Fixture(fixture_id("A4-H4-FIELD-MODEL-NONE"), SyntheticParameters(catalog_kind_override={"T3_CREATE": 0x44})),
        "A4-H4-FIELD-MODEL-MULTIPLE": Fixture(fixture_id("A4-H4-FIELD-MODEL-MULTIPLE"), SyntheticParameters(catalog_record_layout="double")),
        "A4-H4-ENCODING-AMBIGUOUS": Fixture(fixture_id("A4-H4-ENCODING-AMBIGUOUS"), SyntheticParameters(e_acute_length_override=9)),
        "A4-H4-REPLICA-DISAGREEMENT": Fixture(fixture_id("A4-H4-REPLICA-DISAGREEMENT"), SyntheticParameters(t2_identifier_relation_by_replica={2: "distinct"})),
        "A4-H1-HOLDOUT-PREDICTION": Fixture(fixture_id("A4-H1-HOLDOUT-PREDICTION"), SyntheticParameters(layout_by_replica={3: _H1["locator_layouts"][0]})),
        "A4-H2-HOLDOUT-PREDICTION": Fixture(fixture_id("A4-H2-HOLDOUT-PREDICTION"), SyntheticParameters(owned_ordinal_by_replica={3: 1})),
        "A4-H3-HOLDOUT-PREDICTION": Fixture(fixture_id("A4-H3-HOLDOUT-PREDICTION"), patches_by_replica={3: ("holdout_bitmap_clear",)}),
        "A4-H4-HOLDOUT-ROOT": Fixture(fixture_id("A4-H4-HOLDOUT-ROOT"), SyntheticParameters(catalog_page_override_by_replica={3: {"T3_CREATE": "spare"}})),
        "A4-H4-HOLDOUT-FIELDS": Fixture(fixture_id("A4-H4-HOLDOUT-FIELDS"), SyntheticParameters(holdout_name_corruption=True)),
    }
)


def reject_verdict_keys(document: Any) -> None:
    if isinstance(document, dict):
        forbidden = {"accepted", "valid", "reachable", "passed"} & set(document)
        if forbidden:
            raise ValueError(f"A4 fixture contains forbidden verdict keys: {sorted(forbidden)}")
        for value in document.values():
            reject_verdict_keys(value)
    elif isinstance(document, list):
        for value in document:
            reject_verdict_keys(value)
