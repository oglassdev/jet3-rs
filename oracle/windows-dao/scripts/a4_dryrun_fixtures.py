#!/usr/bin/env python3
"""Byte-level reachability fixtures for every registered A4 predicate.

Each fixture is the all-pass baseline campaign plus one disclosed mutation
(generator parameters and/or a post-generation page patch). No fixture carries
an expected status beyond the predicate it is registered against; the harness
measures the first failure from bytes and rejects a fixture whose first
failure is not its target.

A4 rule | implementation
--- | ---
One shared synthetic campaign per fixture, mutation from the baseline | :class:`Fixture`
Plan reachability_fixture_id per predicate | :data:`REGISTRY_FIXTURES`
Adversarial evaluator cases (MULTIPLE 2/3/4, encoding 0/2, unregistered id, malformed page, earlier-invalidating mutation) | :data:`ADVERSARIAL`
Unreachable terminal asserted by enumeration with an attempt fixture | :data:`UNREACHABLE`
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from a4_campaign import Campaign
from a4_generator import Params, generate
from a4_pages import DELETED_FLAG, data_page, decode_directory, tag05_bits, tag05_page
from a4_spec import PAGE_SIZE, PREDICATE_CONTRACTS, TAG05_BITS, canonical_json_bytes, sha256_hex

Patch = Callable[[Campaign], None]


def _rows(page: bytes) -> tuple[list[bytes], dict[int, int]]:
    slots = decode_directory(page, 0x1FFF)
    return [page[s.start: s.end] for s in slots], {s.ordinal: s.raw & 0xC000 for s in slots}


def rebuild(page: bytes, transform: Callable[[list[bytes]], list[bytes]], flags: dict[int, int] | None = None) -> bytes:
    rows, existing = _rows(page)
    return data_page(transform(rows), flags if flags is not None else existing)


def _set_bit(row: bytes, page: int) -> bytes:
    base = int.from_bytes(row[1:5], "little")
    bit = page - base
    body = bytearray(row[5:])
    while len(body) * 8 <= bit:
        body.append(0)
    body[bit >> 3] |= 1 << (bit & 7)
    return row[:5] + bytes(body)


def _clear_bit(row: bytes, page: int) -> bytes:
    base = int.from_bytes(row[1:5], "little")
    bit = page - base
    body = bytearray(row[5:])
    body[bit >> 3] &= ~(1 << (bit & 7)) & 0xFF
    return row[:5] + bytes(body)


# ----------------------------------------------------------------------------- patches

def flip_empty_r_byte(c: Campaign) -> None:
    page = bytearray(c.page(1, "EMPTY_R", 1) or b"")
    page[100] ^= 0x01
    c.patch_page(1, "EMPTY_R", 1, bytes(page))
    c.refresh(1)


def snapshot_duplicate_ordinal(c: Campaign) -> None:
    tables = c.replicas[1].snapshots["T2_CREATE"]["tables"]
    tables[1]["ordinal"] = tables[0]["ordinal"]


def page_index_hash_swap(c: Campaign) -> None:
    index = c.replicas[1].page_indexes["T1_REL_0064"]
    index["ordered_page_sha256"][5] = index["ordered_page_sha256"][4]


def _map_patch(c: Campaign, replica: int, instance: str, first: str, transform: Callable[[bytes], bytes]) -> None:
    meta = c.instance_pages(replica, instance)
    c.patch_from(replica, first, meta["map"], transform)
    c.refresh(replica)


def t3_directory_overlap(c: Campaign) -> None:
    def patch(page: bytes) -> bytes:
        out = bytearray(page)
        out[12:14] = (12).to_bytes(2, "little")  # slot 1 start below 10+2*row_count
        return bytes(out)
    _map_patch(c, 1, "T3-v1", "T3_CREATE", patch)


def t3_owned_deleted_flag(c: Campaign) -> None:
    _map_patch(c, 1, "T3-v1", "T3_CREATE", lambda p: rebuild(p, lambda rows: rows, {0: DELETED_FLAG}))


def t3_owned_tag_02(c: Campaign) -> None:
    _map_patch(c, 1, "T3-v1", "T3_CREATE", lambda p: rebuild(p, lambda rows: [b"\x02" + rows[0][1:]] + rows[1:]))


def t1_available_outside_owned(c: Campaign) -> None:
    foreign = c.instance_pages(1, "T1-v1")["map"] + 1  # next page: never owned by T1
    _map_patch(c, 1, "T1-v1", "T1_CREATE_ID", lambda p: rebuild(p, lambda rows: [rows[0], _set_bit(rows[1], foreign)]))


def t1_owned_drops_page_at_0512(c: Campaign) -> None:
    dropped = c.instance_pages(1, "T1-v1")["data_pages"][0]

    def transform(rows: list[bytes]) -> list[bytes]:
        available = rows[1]
        if len(available) * 8 > (dropped - int.from_bytes(available[1:5], "little")) + 40:
            available = _clear_bit(available, dropped)
        return [_clear_bit(rows[0], dropped), available]
    _map_patch(c, 1, "T1-v1", "T1_REL_0512", lambda p: rebuild(p, transform))


def t3_reference_to_tag01(c: Campaign) -> None:
    data_page_number = c.instance_pages(1, "T3-v1")["data_pages"][0]

    def patch(page: bytes) -> bytes:
        def rows_transform(rows: list[bytes]) -> list[bytes]:
            row = bytearray(rows[0])
            if row[0] == 0x01:
                row[5:9] = data_page_number.to_bytes(4, "little")  # slot 1 now references a tag-01 page
            return [bytes(row)] + rows[1:]
        return rebuild(page, rows_transform)
    _map_patch(c, 1, "T3-v1", "T3_ABS_16480", patch)


def holdout_t3_tag05_clears_map_bit(c: Campaign) -> None:
    meta = c.instance_pages(3, "T3-v1")
    reference, map_page = meta["tag05"][0], meta["map"]
    c.patch_from(3, "T3_ABS_16480", reference, lambda p: tag05_page(set(tag05_bits(p)) - {map_page}))
    c.refresh(3)


def second_locator_pair_copy(c: Campaign) -> None:
    """Copy the locator pair to [100,108) on every user TDEF: preserved and target-valid, but not at the holes."""
    for replica in (1, 2, 3):
        for instance, meta in c.replicas[replica].meta["instances"].items():
            first = next(cp for cp in c.replicas[replica].pages if (c.page(replica, cp, meta["tdef"]) or b"\x00")[0] == 0x02)

            def patch(page: bytes) -> bytes:
                return page[:100] + page[35:43] + page[108:] if page[0] == 0x02 else page
            c.patch_from(replica, first, meta["tdef"], patch)
        c.refresh(replica)


def malformed_page(c: Campaign) -> None:
    digest = c.replicas[1].pages["EMPTY"][1]
    c.blobs[digest] = c.blobs[digest][: PAGE_SIZE - 1]


def flags_and_directory(c: Campaign) -> None:
    """Targets ROW-FLAGS but also breaks the directory: the earlier predicate must win."""
    t3_owned_deleted_flag(c)
    t3_directory_overlap(c)


PATCHES: dict[str, Patch] = {f.__name__: f for f in (
    flip_empty_r_byte, snapshot_duplicate_ordinal, page_index_hash_swap, t3_directory_overlap, t3_owned_deleted_flag,
    t3_owned_tag_02, t1_available_outside_owned, t1_owned_drops_page_at_0512, t3_reference_to_tag01,
    holdout_t3_tag05_clears_map_bit, second_locator_pair_copy, malformed_page, flags_and_directory,
)}


# ----------------------------------------------------------------------------- fixtures

@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    predicate_id: str | None  # expected first failure; None means all-pass
    description: str
    params: Params = field(default_factory=Params)
    patches: tuple[str, ...] = ()
    grammar_selection: dict[str, list[Any]] | None = None
    expect_rejection: str | None = None  # harness must reject this fixture for the stated reason
    legitimate_count: int | None = None  # asserted measured count where the plan names one

    def mutation(self) -> dict[str, Any]:
        raw = asdict(self.params)
        return {"generator_params": _jsonable(raw), "patches": list(self.patches), "grammar_selection": self.grammar_selection}

    def mutation_sha256(self) -> str:
        return sha256_hex(canonical_json_bytes(self.mutation()))

    def build(self) -> Campaign:
        campaign = generate(self.params)
        for name in self.patches:
            PATCHES[name](campaign)
        return campaign


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


BLOAT = 16352 - 9  # first user page at or above the 16,352-page boundary (nine system pages with two reserved tag-05 pages)


def _fixture_for(predicate_id: str, description: str, **kwargs: Any) -> Fixture:
    return Fixture(PREDICATE_CONTRACTS[predicate_id]["reachability_fixture_id"], predicate_id, description, **kwargs)


BASELINE = Fixture("A4-R00-BASELINE", None, "all-pass synthetic campaign; every derivation layer decisive and every holdout predicted")

REGISTRY_FIXTURES: tuple[Fixture, ...] = (
    _fixture_for("A4-IDLE-EQUALITY", "one byte differs in replica 1 EMPTY_R page 1", patches=("flip_empty_r_byte",)),
    _fixture_for("A4-SCHEMA-SNAPSHOT", "replica 1 T2_CREATE snapshot carries a duplicate table ordinal", patches=("snapshot_duplicate_ordinal",)),
    _fixture_for("A4-SNAPSHOT-RECONSTRUCTION", "replica 1 T1_REL_0064 page index lists another blob's hash at page 5", patches=("page_index_hash_swap",)),
    _fixture_for("A4-RESOURCE-BOUND", "twelve extra tag-02 pages appear at T4_CREATE: 17 qualified pages in the union, one over 16",
                 params=Params(decoy_tdef_pages={"T4_CREATE": 12})),
    _fixture_for("A4-H1-TDEF-NONE", "user TDEF pages carry tag 03, so no tag-02 page matches either lifecycle signature", params=Params(user_tdef_tag=0x03)),
    _fixture_for("A4-H1-TDEF-MULTIPLE", "a second new tag-02 page appears at T3_CREATE: two lifecycle-matching TDEF candidates",
                 params=Params(decoy_tdef_pages={"T3_CREATE": 1}), legitimate_count=2),
    _fixture_for("A4-H1-LOCATOR-LAYOUT-NONE", "user TDEF bytes after the tag are all 0xA5: no window decodes under either layout", params=Params(tdef_style="opaque")),
    _fixture_for("A4-H1-LOCATOR-PAIR-NONE", "signature byte 2 differs on every user TDEF: windows preserved, no masked pair", params=Params(tdef_style="broken_signature")),
    _fixture_for("A4-H1-TARGET-ROW-INVALID", "locators point at the TDEF page itself (tag 02) under both layouts", params=Params(locator_target="tdef")),
    _fixture_for("A4-H1-LOCATOR-LAYOUT-MULTIPLE", "user pages preallocated below 18 and EMPTY padded past 256*17 so page-then-row targets are extant tag 01 too",
                 params=Params(preallocate_user_pages=True, extra_empty_filler=256 * 18 + 2), legitimate_count=2),
    _fixture_for("A4-H1-LOCATOR-PAIR-MULTIPLE", "attempt: the locator pair is copied to [100,108); exact-hole filtering keeps one structural pair (asserted unreachable)",
                 patches=("second_locator_pair_copy",)),
    _fixture_for("A4-H1-REPLICA-DISAGREEMENT", "replica 2 encodes its locators page-then-row", params=Params(layout_by_replica={2: "u24le_page_then_u8_row"})),
    _fixture_for("A4-H2-ROW-DIRECTORY-INVALID", "replica 1 T3 map page slot 1 starts at 12, inside the directory bytes", patches=("t3_directory_overlap",)),
    _fixture_for("A4-H2-ROW-FLAGS-INVALID", "replica 1 T3 owned row has the deleted flag set", patches=("t3_owned_deleted_flag",)),
    _fixture_for("A4-H2-MAP-TAG-UNSUPPORTED", "replica 1 T3 owned row begins 02", patches=("t3_owned_tag_02",)),
    _fixture_for("A4-H2-ROLE-NONE", "replica 1 T1 available row admits a page the owned row never admits", patches=("t1_available_outside_owned",)),
    _fixture_for("A4-H2-ROLE-MULTIPLE", "available rows equal owned rows everywhere: both role assignments fit", params=Params(available_equals_owned=True), legitimate_count=2),
    _fixture_for("A4-H2-TRANSITION-UNEXPLAINED", "replica 1 T1 owned row drops its first data page on the T1_REL_0064->T1_REL_0512 grow leg (AMB-06)",
                 patches=("t1_owned_drops_page_at_0512",)),
    _fixture_for("A4-H2-REPLICA-DISAGREEMENT", "replica 2 stores the owned row at locator ordinal 1", params=Params(owned_ordinal_by_replica={2: 1})),
    _fixture_for("A4-H3-CONVERSION-NONE", "owned rows never convert to type 1", params=Params(conversion="never")),
    _fixture_for("A4-H3-INACTIVE-SLOT-NONE", "type-1 rows carry exactly their nonzero slots", params=Params(type1_exact_slots=True)),
    _fixture_for("A4-H3-REFERENCE-INVALID", "replica 1 T3 slot 1 references a tag-01 data page from T3_ABS_16480", patches=("t3_reference_to_tag01",)),
    _fixture_for("A4-H3-BASE-DISCRIMINATION", "owned sets stop below page 16352: no boundary input region", params=Params(omit_pages_at_or_above=16352)),
    _fixture_for("A4-H3-BASE-NONE", "every owned tag-05 bit is shifted by two (synthetic boundary bits keep every input region exercised): no formula contains the pre-conversion set",
                 params=Params(tag05_bit_shift=2, synthetic_boundary_bits=True)),
    _fixture_for("A4-H3-BASE-MULTIPLE", "reference == slot for every active slot (user pages beyond 16352, forced T3 conversion): slot and referenced-page formulas both fit",
                 params=Params(extra_empty_filler=BLOAT, tag05_mode_by_replica={1: "equals_slot", 2: "equals_slot", 3: "equals_slot"},
                               force_conversion_at={"T3-v1": "T3_ABS_04096"}, synthetic_boundary_bits=True), legitimate_count=2),
    _fixture_for("A4-H3-REPLICA-DISAGREEMENT", "replica 2 references page k+1 from slot k (referenced-page formula) while replica 1 keeps the slot formula",
                 params=Params(extra_empty_filler=BLOAT, tag05_mode_by_replica={2: "referenced"}, force_conversion_at={"T3-v1": "T3_ABS_04096"},
                               synthetic_boundary_bits=True)),
    _fixture_for("A4-H3-HOLDOUT-PREDICTION", "replica 3's slot-0 tag-05 page clears the bit of the T3 map page after conversion", patches=("holdout_t3_tag05_clears_map_bit",)),
    _fixture_for("A4-H4-CATALOG-ROOT-NONE", "catalog records are written to a page no system stream admits", params=Params(record_page_default="spare")),
    _fixture_for("A4-H4-CATALOG-ROOT-MULTIPLE", "the decoy system table owns the spare page and changes it at every operation", params=Params(decoy_follows_ops=True), legitimate_count=2),
    _fixture_for("A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED", "the T3_CREATE record is written to the unadmitted spare page", params=Params(record_page_override={"T3_CREATE": "spare"})),
    _fixture_for("A4-H4-CATALOG-RECORD-NONE", "no record is written at T3_CREATE", params=Params(skip_record_at={"T3_CREATE"})),
    _fixture_for("A4-H4-CATALOG-RECORD-MULTIPLE", "two records are written at T3_CREATE", params=Params(duplicate_record_at={"T3_CREATE"}), legitimate_count=2),
    _fixture_for("A4-H4-FIELD-MODEL-NONE", "the T3_CREATE record stores kind 0x44 while other table records store 0x11", params=Params(kind_override={"T3_CREATE": 0x44})),
    _fixture_for("A4-H4-FIELD-MODEL-MULTIPLE", "records carry two stamp/id/kind prefixes with distinct identifiers before one length byte", params=Params(record_layout="double"), legitimate_count=2),
    _fixture_for("A4-H4-ENCODING-AMBIGUOUS", "the É record stores Windows-1252 bytes with stored length 9: zero fitting classes", params=Params(e_acute_length_override=9), legitimate_count=0),
    _fixture_for("A4-H4-REPLICA-DISAGREEMENT", "replica 2 reuses the T2-v1 identifier for T2-v2", params=Params(t2_id_relation_by_replica={2: "same"})),
    _fixture_for("A4-H1-HOLDOUT-PREDICTION", "replica 3 stores its locators at offsets 36/40", params=Params(locator_offsets_by_replica={3: (36, 40)})),
    _fixture_for("A4-H2-HOLDOUT-PREDICTION", "replica 3 stores the owned row at locator ordinal 1", params=Params(owned_ordinal_by_replica={3: 1})),
    _fixture_for("A4-H4-HOLDOUT-ROOT", "replica 3 writes the T3_CREATE record to the unadmitted spare page", params=Params(record_page_override_by_replica={3: {"T3_CREATE": "spare"}})),
    _fixture_for("A4-H4-HOLDOUT-FIELDS", "replica 3's É record name ends in '5'", params=Params(holdout_name_corruption=True)),
)

UNREACHABLE: tuple[str, ...] = ("A4-H1-LOCATOR-PAIR-MULTIPLE",)

ADVERSARIAL: tuple[Fixture, ...] = (
    Fixture("A4-ADV-TDEF-MULTIPLE-3", "A4-H1-TDEF-MULTIPLE", "three lifecycle-matching TDEF candidates (legitimate minimum-2 outcome)",
            params=Params(decoy_tdef_pages={"T3_CREATE": 2}), legitimate_count=3),
    Fixture("A4-ADV-TDEF-MULTIPLE-4", "A4-H1-TDEF-MULTIPLE", "four lifecycle-matching TDEF candidates (legitimate minimum-2 outcome)",
            params=Params(decoy_tdef_pages={"T3_CREATE": 3}), legitimate_count=4),
    Fixture("A4-ADV-ENCODING-2", "A4-H4-ENCODING-AMBIGUOUS", "the É record carries both registered byte occurrences at compatible anchors: two fitting classes",
            params=Params(e_acute_double_occurrence=True), legitimate_count=2),
    Fixture("A4-ADV-UNREGISTERED-ID", None, "fixture names an unregistered base formula", grammar_selection={"base_formula": ["not-a-registered-formula"]},
            expect_rejection="unregistered_candidate_id"),
    Fixture("A4-ADV-MALFORMED-PAGE", None, "one page blob is 2,047 bytes", patches=("malformed_page",), expect_rejection="malformed_page"),
    Fixture("A4-ADV-EARLIER-PREDICATE", "A4-H2-ROW-FLAGS-INVALID", "claims ROW-FLAGS but the same mutation breaks the directory first",
            patches=("flags_and_directory",), expect_rejection="first_failure_is_not_target"),
)


def all_fixtures() -> tuple[Fixture, ...]:
    return (BASELINE,) + REGISTRY_FIXTURES + ADVERSARIAL
