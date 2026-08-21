"""Plan-derived schedule arithmetic for the A2 synthetic generator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from a2_spec import BOUNDS, CHECKPOINT_IDS, PAGE_SIZE, ROLES, CheckedPlan, load_checked_plan
from protocol_validation import ValidationError


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

    def checkpoint(self, checkpoint_id: str) -> ScheduledCheckpoint:
        return self.checkpoints[CHECKPOINT_IDS.index(checkpoint_id)]


def _derived_storage_units(plan: CheckedPlan) -> tuple[int, int, int]:
    fields = plan.document["tables"]["definition"]["fields"]
    row_bytes = sum(field["size"] for field in fields)
    rows_per_page = PAGE_SIZE // row_bytes
    batch_rows = plan.document["tables"]["row_algorithm"]["growth_batch_rows"]
    if rows_per_page < 1 or batch_rows < 1:
        raise ValidationError("plan fields cannot produce bounded synthetic rows")
    pages_per_batch = math.ceil(batch_rows / rows_per_page)
    table_count = len(plan.document["tables"]["physical_names"])
    initial_pages = table_count * (len(fields) + int(not plan.document["tables"]["definition"]["indexed"]))
    return rows_per_page, pages_per_batch, initial_pages


def build_schedule(*, delete_page_delta: int | None = None, plan: CheckedPlan | None = None) -> Schedule:
    """Execute the frozen target rules using one derived 32-row storage batch."""
    checked = load_checked_plan() if plan is None else plan
    rows_per_page, pages_per_batch, pages = _derived_storage_units(checked)
    batch_rows = checked.document["tables"]["row_algorithm"]["growth_batch_rows"]
    if delete_page_delta is None:
        delete_page_delta = int("delete every row" in checked.document["tables"]["row_algorithm"]["delete_rule"])
    if isinstance(delete_page_delta, bool) or delete_page_delta < 0:
        raise ValidationError("delete_page_delta must be a non-negative integer")

    counts = {role: 0 for role in ROLES}
    inserted_total = 0
    rows: list[ScheduledCheckpoint] = []
    baselines: dict[str, int] = {}
    retained_l_rows = 0

    def capture(checkpoint_id: str, baseline: int | None = None, threshold: int | None = None) -> None:
        overshoot = None if threshold is None else pages - threshold
        rows.append(
            ScheduledCheckpoint(
                checkpoint_id=checkpoint_id,
                ordinal=len(rows),
                actual_file_pages=pages,
                target_baseline_pages=baseline,
                target_threshold_pages=threshold,
                target_overshoot_pages=overshoot,
                inserted_rows_total=inserted_total,
                table_row_counts=dict(counts),
            )
        )

    def grow(role: str, threshold: int) -> None:
        nonlocal pages, inserted_total
        while pages < threshold:
            counts[role] += batch_rows
            inserted_total += batch_rows
            pages += pages_per_batch
            if (
                pages > BOUNDS["max_final_pages_per_replica"]
                or inserted_total > BOUNDS["max_inserted_rows_per_replica"]
            ):
                raise ValidationError("synthetic schedule exceeds the checked A2 bounds")

    for checkpoint_id in CHECKPOINT_IDS:
        if checkpoint_id in ("E0", "E0R"):
            capture(checkpoint_id)
        elif checkpoint_id == "D_GROW_0128":
            baselines["D_FIRST"] = pages
            threshold = pages + int(checkpoint_id.rsplit("_", 1)[1])
            grow("D", threshold)
            capture(checkpoint_id, baselines["D_FIRST"], threshold)
        elif checkpoint_id == "D_DROP":
            counts["D"] = 0
            capture(checkpoint_id)
        elif checkpoint_id == "D_RECREATE_EMPTY":
            baselines["D_REGROW"] = pages
            capture(checkpoint_id)
        elif checkpoint_id == "D_REGROW_0128":
            threshold = baselines["D_REGROW"] + int(checkpoint_id.rsplit("_", 1)[1])
            grow("D", threshold)
            capture(checkpoint_id, baselines["D_REGROW"], threshold)
        elif checkpoint_id.startswith("L_REL_"):
            baselines.setdefault("L", pages)
            threshold = baselines["L"] + int(checkpoint_id.rsplit("_", 1)[1])
            grow("L", threshold)
            capture(checkpoint_id, baselines["L"], threshold)
            retained_l_rows = counts["L"]
        elif checkpoint_id == "L_DELETE_ALL":
            counts["L"] = 0
            pages += delete_page_delta
            capture(checkpoint_id)
        elif checkpoint_id == "L_REINSERT_SAME":
            counts["L"] = retained_l_rows
            inserted_total += retained_l_rows
            capture(checkpoint_id)
        elif checkpoint_id == "L_IDLE_REOPEN":
            capture(checkpoint_id)
        elif checkpoint_id.startswith("P_ABS_"):
            threshold = int(checkpoint_id.rsplit("_", 1)[1])
            grow("P", threshold)
            capture(checkpoint_id, None, threshold)
        elif checkpoint_id.startswith("H_REL_"):
            baselines.setdefault("H", pages)
            threshold = baselines["H"] + int(checkpoint_id.rsplit("_", 1)[1])
            grow("H", threshold)
            capture(checkpoint_id, baselines["H"], threshold)
        elif checkpoint_id == "H_IDLE_REOPEN":
            capture(checkpoint_id)
        else:
            raise ValidationError(f"unimplemented checked A2 checkpoint {checkpoint_id!r}")
    if tuple(row.checkpoint_id for row in rows) != CHECKPOINT_IDS:
        raise ValidationError("synthetic schedule did not execute the checked plan order")
    first_growth = rows[CHECKPOINT_IDS.index("D_GROW_0128")]
    regrowth = rows[CHECKPOINT_IDS.index("D_REGROW_0128")]
    if regrowth.actual_file_pages <= first_growth.actual_file_pages:
        raise ValidationError("synthetic D regrowth is not strictly larger than first growth")
    return Schedule(
        tuple(rows),
        batch_rows,
        rows_per_page,
        pages_per_batch,
        rows[0].actual_file_pages,
        delete_page_delta,
    )


def checkpoint_document(row: ScheduledCheckpoint, page_index_ref: dict[str, Any]) -> dict[str, Any]:
    """Render the schedule portion of one schema-shaped observation checkpoint."""
    return {
        "checkpoint_id": row.checkpoint_id,
        "ordinal": row.ordinal,
        "actual_file_pages": row.actual_file_pages,
        "actual_size_bytes": row.actual_file_pages * PAGE_SIZE,
        "target_baseline_pages": row.target_baseline_pages,
        "target_threshold_pages": row.target_threshold_pages,
        "target_overshoot_pages": row.target_overshoot_pages,
        "inserted_rows_total": row.inserted_rows_total,
        "table_row_counts": dict(row.table_row_counts),
        "dao_reread": [],
        "quiescent": True,
        "post_close_companion": {
            "present_after_close": False,
            "observed_size_bytes": 0,
            "retained_for_physical_analysis": False,
        },
        "page_index": page_index_ref,
    }
