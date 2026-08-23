"""Plan-derived schedule arithmetic for the non-evidential A3 generator.

A3 rule | implementation
--- | ---
Unchanged 25-checkpoint order | :func:`build_schedule`
Fixed 32-row batches and first-reaching target | :func:`build_schedule`
Recorded, untuned overshoot per replica walk | :class:`OvershootProfile`
Strictly larger D regrowth | :func:`build_schedule`
Full-delete/reinsert row semantics | :func:`build_schedule`
Row-algorithm rolling SHA-256 per role | :class:`RollingHashes`
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from protocol_validation import ValidationError
from a3_spec import BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, PLAN

TABLES = PLAN.document["tables"]
ROLES = tuple(TABLES["roles"])
ROW_ALGORITHM = TABLES["row_algorithm"]
BATCH_ROWS = int(ROW_ALGORITHM["growth_batch_rows"])
PAYLOAD_SIZE = next(field["size"] for field in TABLES["definition"]["fields"] if field["name"] == "Payload")
D_GROWTH_TARGET = 128
# One tdef page plus one data page per declared field of every physical table.
INITIAL_PAGES = len(TABLES["physical_names"]) * (
    len(TABLES["definition"]["fields"]) + int(not TABLES["definition"]["indexed"])
)
# E0 occupancy variants of the D anchor bitmap, in D growth units above the
# arithmetic minimum; the inline boundary must not depend on them.
ANCHOR_FILL_UNITS = {"empty": 0, "partial": 1, "full": 8}


@dataclass(frozen=True)
class OvershootProfile:
    """A synthetic engine that spends one extra page every ``period`` batches."""

    extra_page_period: int = 0

    def batch_pages(self, batch_index: int, pages_per_batch: int) -> int:
        if self.extra_page_period and batch_index % self.extra_page_period == 0:
            return pages_per_batch + 1
        return pages_per_batch


# Derivation replicas and the holdout walk with distinct, fixed profiles.
REPLICA_PROFILES = {1: OvershootProfile(0), 2: OvershootProfile(7), 3: OvershootProfile(5)}


@dataclass(frozen=True)
class ScheduledCheckpoint:
    checkpoint_id: str
    ordinal: int
    actual_file_pages: int
    target_baseline_pages: int | None
    target_threshold_pages: int | None
    target_overshoot_pages: int | None
    inserted_rows_total: int
    table_row_counts: dict[str, int]
    extant_roles: tuple[str, ...]


@dataclass(frozen=True)
class Schedule:
    checkpoints: tuple[ScheduledCheckpoint, ...]
    batch_rows: int
    rows_per_page: int
    pages_per_batch: int
    initial_pages: int
    delete_page_delta: int
    retained_l_rows: int
    profile: OvershootProfile

    def checkpoint(self, name: str) -> ScheduledCheckpoint:
        return self.checkpoints[CHECKPOINT_IDS.index(name)]

    def page_count(self, name: str) -> int:
        return self.checkpoint(name).actual_file_pages

    def d_growth_observation(self) -> dict[str, int]:
        first, regrow = self.checkpoint("D_GROW_0128"), self.checkpoint("D_REGROW_0128")
        assert first.target_baseline_pages is not None and regrow.target_baseline_pages is not None
        return {
            "first_baseline_pages": first.target_baseline_pages,
            "first_target_pages": first.target_baseline_pages + D_GROWTH_TARGET,
            "first_achieved_pages": first.actual_file_pages,
            "first_rows": first.table_row_counts["D"],
            "regrowth_baseline_pages": regrow.target_baseline_pages,
            "regrowth_target_pages": regrow.target_baseline_pages + D_GROWTH_TARGET,
            "regrowth_achieved_pages": regrow.actual_file_pages,
            "regrowth_rows": regrow.table_row_counts["D"],
        }


def e0_baseline_pages(anchor_fill_state: str) -> int:
    if anchor_fill_state not in ANCHOR_FILL_UNITS:
        raise ValidationError(f"unknown A3 anchor fill state {anchor_fill_state!r}")
    return INITIAL_PAGES + ANCHOR_FILL_UNITS[anchor_fill_state] * D_GROWTH_TARGET


def build_schedule(
    *,
    delete_page_delta: int = 1,
    profile: OvershootProfile = OvershootProfile(0),
    initial_pages: int = INITIAL_PAGES,
) -> Schedule:
    row_bytes = sum(field["size"] for field in TABLES["definition"]["fields"])
    rows_per_page = PAGE_SIZE // row_bytes
    pages_per_batch = math.ceil(BATCH_ROWS / rows_per_page)
    if rows_per_page < 1 or delete_page_delta < 0 or initial_pages < INITIAL_PAGES:
        raise ValidationError("A3 synthetic storage arithmetic is invalid")
    pages = initial_pages
    counts = {role: 0 for role in ROLES}
    extant = set(ROLES)
    inserted = retained_l = batches = 0
    baselines: dict[str, int] = {}
    rows: list[ScheduledCheckpoint] = []

    def capture(name: str, baseline: int | None = None, threshold: int | None = None) -> None:
        overshoot = None if threshold is None else pages - threshold
        rows.append(ScheduledCheckpoint(
            name, len(rows), pages, baseline, threshold, overshoot, inserted, dict(counts),
            tuple(role for role in ROLES if role in extant),
        ))

    def grow(role: str, threshold: int) -> None:
        nonlocal pages, inserted, batches
        while pages < threshold:
            batches += 1
            counts[role] += BATCH_ROWS
            inserted += BATCH_ROWS
            pages += profile.batch_pages(batches, pages_per_batch)
            if pages > BOUNDS["max_final_pages_per_replica"] or inserted > BOUNDS["max_inserted_rows_per_replica"]:
                raise ValidationError("A3 synthetic schedule exceeds plan bounds")

    for name in CHECKPOINT_IDS:
        if name in {"E0", "E0R"}:
            capture(name)
        elif name == "D_GROW_0128":
            baselines["D_FIRST"] = pages
            grow("D", pages + D_GROWTH_TARGET)
            capture(name, baselines["D_FIRST"], baselines["D_FIRST"] + D_GROWTH_TARGET)
        elif name == "D_DROP":
            counts["D"] = 0
            extant.discard("D")
            capture(name)
        elif name == "D_RECREATE_EMPTY":
            extant.add("D")
            baselines["D_REGROW"] = pages
            capture(name)
        elif name == "D_REGROW_0128":
            grow("D", baselines["D_REGROW"] + D_GROWTH_TARGET)
            capture(name, baselines["D_REGROW"], baselines["D_REGROW"] + D_GROWTH_TARGET)
        elif name.startswith("L_REL_"):
            baselines.setdefault("L", pages)
            target = baselines["L"] + int(name.rsplit("_", 1)[1])
            grow("L", target)
            capture(name, baselines["L"], target)
            retained_l = counts["L"]
        elif name == "L_DELETE_ALL":
            counts["L"] = 0
            pages += delete_page_delta
            capture(name)
        elif name == "L_REINSERT_SAME":
            counts["L"] = retained_l
            inserted += retained_l
            capture(name)
        elif name == "L_IDLE_REOPEN":
            capture(name)
        elif name.startswith("P_ABS_"):
            target = int(name.rsplit("_", 1)[1])
            grow("P", target)
            capture(name, None, target)
        elif name.startswith("H_REL_"):
            baselines.setdefault("H", pages)
            target = baselines["H"] + int(name.rsplit("_", 1)[1])
            grow("H", target)
            capture(name, baselines["H"], target)
        elif name == "H_IDLE_REOPEN":
            capture(name)
        else:
            raise ValidationError(f"unimplemented A3 checkpoint {name}")
    if tuple(row.checkpoint_id for row in rows) != CHECKPOINT_IDS:
        raise ValidationError("A3 generator departed from plan checkpoint order")
    if rows[5].actual_file_pages <= rows[2].actual_file_pages:
        raise ValidationError("A3 D regrowth is not strictly larger")
    return Schedule(
        tuple(rows), BATCH_ROWS, rows_per_page, pages_per_batch, initial_pages,
        delete_page_delta, retained_l, profile,
    )


def row_bytes(role: str, row_id: int) -> bytes:
    seed = f"A2|{role}|{row_id:010d}|".encode("ascii")
    payload = (seed * (PAYLOAD_SIZE // len(seed) + 1))[:PAYLOAD_SIZE]
    return row_id.to_bytes(4, "little", signed=True) + len(payload).to_bytes(2, "little") + payload


class RollingHashes:
    """Rolling SHA-256 over Id-ascending rows; reinserted rows hash identically."""

    def __init__(self) -> None:
        self._hashers = {role: hashlib.sha256() for role in ROLES}
        self._counts = {role: 0 for role in ROLES}
        self._snapshots: dict[tuple[str, int], str] = {(role, 0): hashlib.sha256().hexdigest() for role in ROLES}

    def digest(self, role: str, row_count: int) -> str:
        if (role, row_count) in self._snapshots:
            return self._snapshots[(role, row_count)]
        if row_count < self._counts[role]:
            hasher = hashlib.sha256()
            for row_id in range(1, row_count + 1):
                hasher.update(row_bytes(role, row_id))
            digest = hasher.hexdigest()
            self._snapshots[(role, row_count)] = digest
            return digest
        hasher = self._hashers[role]
        for row_id in range(self._counts[role] + 1, row_count + 1):
            hasher.update(row_bytes(role, row_id))
        self._counts[role] = row_count
        digest = hasher.hexdigest()
        self._snapshots[(role, row_count)] = digest
        return digest
