"""Synthetic A1 replica bundles for analysis contract tests.

No DAO acquisition exists. These bundles are project-authored fabrications used
only to exercise the analyzer's decision rules, boundary derivation, freeze
discipline, and fail-closed paths.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "archive"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a1_model import ReplicaIndexes  # noqa: E402
from a1_spec import CHECKED_PLAN, load_checked_plan  # noqa: E402
from protocol_validation import sha256  # noqa: E402

# Every fixture constant below is read out of the checked plan document, so the
# synthetic bundles cannot silently mirror the implementation they exercise.
PLAN = load_checked_plan().document
PLAN_SHA256 = sha256(CHECKED_PLAN)
CHECKPOINT_IDS: tuple[str, ...] = tuple(PLAN["checkpoint_design"]["checkpoint_ids"])
ORDINAL = {name: index for index, name in enumerate(CHECKPOINT_IDS)}
PAGE_SIZE = PLAN["page_capture"]["page_size"]
PLAN_NO_OUTCOME_REASONS: tuple[str, ...] = tuple(PLAN["decision_rules"]["no_scientific_outcome"])
ROLE_BINDINGS = [
    {role: binding[role] for role in PLAN["tables"]["roles"]}
    for binding in PLAN["tables"]["role_bindings"]
]
LADDER: tuple[int, ...] = tuple(
    int(name.rsplit("_", 1)[1]) for name in CHECKPOINT_IDS if name.startswith("L_REL_")
)
EXTENDED_MAP_BITS = int(
    next(
        candidate
        for candidate in PLAN["hypotheses"]["extended_base_candidates"]
        if candidate.startswith("slot_relative_expected_")
    ).rsplit("_", 1)[1]
)

RECORD_BASE = 16
CONVERSION_CHECKPOINT = "L_REL_0512"
INLINE_ANCHOR = "L_REL_0064"
INLINE_CAPACITY_PAGES = 320
DROPPED_PAGES = range(10, 140)
EXTENDED_HEADER = b"\x05\x01\x00\x00"


def default_counts() -> dict[str, int]:
    counts = {"E0": 8, "E0R": 8, "D_GROW_0128": 140, "D_DROP": 140, "D_REGROW_0128": 140}
    for target in LADDER:
        counts[f"L_REL_{target:04d}"] = 142 + target + 2
    for name in ("L_DELETE_ALTERNATING", "L_REINSERT_SAME", "L_IDLE_REOPEN"):
        counts[name] = counts["L_REL_1280"]
    counts.update(
        {"P_ABS_04096": 4098, "P_ABS_08192": 8194, "P_ABS_12288": 12290, "P_ABS_16480": 16482}
    )
    for target in LADDER:
        counts[f"H_REL_{target:04d}"] = 16484 + target + 2
    counts["H_IDLE_REOPEN"] = counts["H_REL_1280"]
    return counts


@dataclass
class Spec:
    """One synthetic replica; every flag switches on a single fault path."""

    number: int
    low_reference: int = 600
    high_reference: int = 16400
    free_pages: tuple[int, int, int] = (5, 6, 7)
    convert: bool = True
    record_shift: int = 0
    second_used_pointer: bool = False
    static_free_pointer: bool = False
    stray_suffix: bool = False
    empty_inline_anchor: bool = False
    activate_high_slot: bool = True
    bitmap_shift: int = 0
    page_ceiling_breach: bool = False


def _free_page(spec: Spec, checkpoint: str) -> int:
    if spec.static_free_pointer:
        return spec.free_pages[0]
    ordinal = ORDINAL[checkpoint]
    if ordinal < ORDINAL["L_DELETE_ALTERNATING"]:
        return spec.free_pages[0]
    if ordinal < ORDINAL["L_REINSERT_SAME"]:
        return spec.free_pages[1]
    return spec.free_pages[2]


def _indirect(spec: Spec, checkpoint: str) -> bool:
    return spec.convert and ORDINAL[checkpoint] >= ORDINAL[CONVERSION_CHECKPOINT]


def _high_slot_active(spec: Spec, count: int) -> bool:
    return spec.activate_high_slot and count > EXTENDED_MAP_BITS


def record_page(spec: Spec, checkpoint: str, count: int) -> bytes:
    body = bytearray(PAGE_SIZE)
    body[0:8] = b"A1PAGE01"
    base = RECORD_BASE + spec.record_shift
    if spec.second_used_pointer:
        body[base - 4 : base - 1] = (count - 2).to_bytes(3, "little")
        body[base - 1] = 3
    body[base : base + 3] = (count - 1).to_bytes(3, "little")
    body[base + 3] = 3
    body[base + 4 : base + 7] = _free_page(spec, checkpoint).to_bytes(3, "little")
    body[base + 7] = 1
    type_offset = base + 8
    if _indirect(spec, checkpoint):
        body[type_offset] = 0x01
        body[type_offset + 1 : type_offset + 5] = spec.low_reference.to_bytes(4, "little")
        if _high_slot_active(spec, count):
            body[type_offset + 5 : type_offset + 9] = spec.high_reference.to_bytes(4, "little")
    elif not (spec.empty_inline_anchor and checkpoint == INLINE_ANCHOR):
        bitmap = type_offset + 5
        allocated = set(range(min(count, INLINE_CAPACITY_PAGES)))
        if checkpoint == "D_DROP":
            allocated -= set(DROPPED_PAGES)
        for page in allocated:
            body[bitmap + page // 8] |= 1 << (page % 8)
    if spec.stray_suffix and checkpoint in ("E0", "E0R"):
        body[base + 44] = 0xAA
    return bytes(body)


def extended_map_page(spec: Spec, slot: int, count: int) -> bytes:
    origin = slot * EXTENDED_MAP_BITS
    mapped = min(count, origin + EXTENDED_MAP_BITS) - origin
    bits = (((1 << mapped) - 1) << spec.bitmap_shift) & ((1 << EXTENDED_MAP_BITS) - 1)
    return EXTENDED_HEADER + bits.to_bytes(PAGE_SIZE - len(EXTENDED_HEADER), "little")


def build_replica(spec: Spec) -> tuple[ReplicaIndexes, dict[str, bytes]]:
    counts = default_counts()
    if spec.page_ceiling_breach:
        counts["H_REL_1280"] = 20481
        counts["H_IDLE_REOPEN"] = 20481
    fake = [f"{page:064x}" for page in range(max(counts.values()))]
    blobs: dict[str, bytes] = {}
    indexes: dict[str, dict[str, list[str]]] = {}
    for checkpoint in CHECKPOINT_IDS:
        count = counts[checkpoint]
        hashes = fake[:count]
        record = record_page(spec, checkpoint, count)
        digest = hashlib.sha256(record).hexdigest()
        blobs[digest] = record
        hashes[1] = digest
        if _indirect(spec, checkpoint):
            references = [(0, spec.low_reference)]
            if _high_slot_active(spec, count):
                references.append((1, spec.high_reference))
            for slot, reference in references:
                page = extended_map_page(spec, slot, count)
                page_digest = hashlib.sha256(page).hexdigest()
                blobs[page_digest] = page
                hashes[reference] = page_digest
        indexes[checkpoint] = {"ordered_page_sha256": hashes}
    observation = {
        "replica": spec.number,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": "0" * 40,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "run_id": "synthetic",
        "environment_sha256": "1" * 64,
        "provider_sha256": "2" * 64,
        "role_binding": ROLE_BINDINGS[spec.number - 1],
    }
    return ReplicaIndexes(observation=observation, indexes=indexes), blobs


def build_bundle(specs: list[Spec], page_store: Path) -> list[ReplicaIndexes]:
    page_store.mkdir(parents=True, exist_ok=True)
    replicas: list[ReplicaIndexes] = []
    for spec in specs:
        replica, blobs = build_replica(spec)
        for digest, payload in blobs.items():
            (page_store / f"{digest}.page").write_bytes(payload)
        replicas.append(replica)
    return replicas


def decisive_specs() -> list[Spec]:
    return [
        Spec(1),
        Spec(2, low_reference=604, free_pages=(5, 6, 8)),
        Spec(3, low_reference=608, high_reference=16404, free_pages=(9, 10, 11)),
    ]
