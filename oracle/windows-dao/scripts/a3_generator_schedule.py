"""Plan-derived schedule arithmetic for the non-evidential A3 generator.

A3 rule | implementation
--- | ---
Unchanged 25-checkpoint order | :func:`build_schedule`
Fixed 32-row batches and first-reaching target | :func:`build_schedule`
Strictly larger D regrowth | :func:`build_schedule`
Full-delete/reinsert row semantics | :func:`build_schedule`
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from protocol_validation import ValidationError
from a3_spec import BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, PLAN

ROLES = tuple(PLAN.document["tables"]["roles"])


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


@dataclass(frozen=True)
class Schedule:
    checkpoints: tuple[ScheduledCheckpoint, ...]
    batch_rows: int
    rows_per_page: int
    pages_per_batch: int
    initial_pages: int
    delete_page_delta: int

    def checkpoint(self, name: str) -> ScheduledCheckpoint:
        return self.checkpoints[CHECKPOINT_IDS.index(name)]


def build_schedule(*, delete_page_delta: int = 1) -> Schedule:
    fields = PLAN.document["tables"]["definition"]["fields"]
    row_bytes = sum(field["size"] for field in fields)
    rows_per_page = PAGE_SIZE // row_bytes
    batch_rows = PLAN.document["tables"]["row_algorithm"]["growth_batch_rows"]
    pages_per_batch = math.ceil(batch_rows / rows_per_page)
    pages = len(PLAN.document["tables"]["physical_names"]) * (len(fields) + int(not PLAN.document["tables"]["definition"]["indexed"]))
    if rows_per_page < 1 or delete_page_delta < 0:
        raise ValidationError("A3 synthetic storage arithmetic is invalid")
    initial_pages = pages
    counts = {role: 0 for role in ROLES}
    inserted = retained_l = 0
    baselines: dict[str, int] = {}
    rows: list[ScheduledCheckpoint] = []

    def capture(name: str, baseline: int | None = None, threshold: int | None = None) -> None:
        rows.append(ScheduledCheckpoint(name, len(rows), pages, baseline, threshold, None if threshold is None else pages - threshold, inserted, dict(counts)))

    def grow(role: str, threshold: int) -> None:
        nonlocal pages, inserted
        while pages < threshold:
            counts[role] += batch_rows
            inserted += batch_rows
            pages += pages_per_batch
            if pages > BOUNDS["max_final_pages_per_replica"] or inserted > BOUNDS["max_inserted_rows_per_replica"]:
                raise ValidationError("A3 synthetic schedule exceeds plan bounds")

    for name in CHECKPOINT_IDS:
        if name in {"E0", "E0R"}:
            capture(name)
        elif name == "D_GROW_0128":
            baselines["D_FIRST"] = pages
            target = pages + 128
            grow("D", target)
            capture(name, baselines["D_FIRST"], target)
        elif name == "D_DROP":
            counts["D"] = 0
            capture(name)
        elif name == "D_RECREATE_EMPTY":
            baselines["D_REGROW"] = pages
            capture(name)
        elif name == "D_REGROW_0128":
            target = baselines["D_REGROW"] + 128
            grow("D", target)
            capture(name, baselines["D_REGROW"], target)
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
    return Schedule(tuple(rows), batch_rows, rows_per_page, pages_per_batch, initial_pages, delete_page_delta)
