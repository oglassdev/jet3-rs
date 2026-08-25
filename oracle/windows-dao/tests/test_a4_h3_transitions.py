"""Focused A4 H3 tests for the frozen H2 transition signature."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

from a4_layer_h3 import (  # noqa: E402
    SlotObservation,
    TraversalObservation,
    conversion_legs,
    formula_fits,
    transition_signature_fits,
)


FORMULA = "slot_ordinal_times_16352_plus_bit_index"
SPAN = (0, 16)


def _type_0(
    checkpoint: str,
    pages: set[int],
    *,
    instance: str,
    allocation_role: str,
    ordinal: int,
    span: tuple[int, int] = SPAN,
) -> TraversalObservation:
    return TraversalObservation(
        1,
        checkpoint,
        24,
        "type_0",
        instance,
        allocation_role=allocation_role,
        type0_owned=frozenset(pages),
        locator_ordinal=ordinal,
        allocation_span=span,
    )


def _type_1(
    checkpoint: str,
    pages: set[int],
    *,
    instance: str,
    allocation_role: str,
    ordinal: int,
    span: tuple[int, int] = SPAN,
) -> TraversalObservation:
    return TraversalObservation(
        1,
        checkpoint,
        24,
        "type_1",
        instance,
        allocation_role=allocation_role,
        slots=(
            SlotObservation(0, 30, 0x05, frozenset(pages)),
            SlotObservation(1, 0, None),
        ),
        locator_ordinal=ordinal,
        allocation_span=span,
    )


def _t3_growth(
    owned: dict[str, set[int]], available: dict[str, set[int]]
) -> tuple[TraversalObservation, ...]:
    checkpoints = (
        "T3_CREATE",
        "T3_ABS_04096",
        "T3_ABS_08192",
        "T3_ABS_12288",
        "T3_ABS_16480",
    )
    rows: list[TraversalObservation] = []
    for index, checkpoint in enumerate(checkpoints):
        owned_builder = _type_0 if index == 0 else _type_1
        rows.append(
            owned_builder(
                checkpoint,
                owned[checkpoint],
                instance="T3-v1",
                allocation_role="owned_in_use",
                ordinal=0,
            )
        )
        rows.append(
            _type_0(
                checkpoint,
                available[checkpoint],
                instance="T3-v1",
                allocation_role="available",
                ordinal=1,
            )
        )
    return tuple(rows)


class A4H3TransitionTests(unittest.TestCase):
    def test_later_type_1_growth_may_not_lose_owned_pages(self) -> None:
        checkpoints = (
            "T3_CREATE",
            "T3_ABS_04096",
            "T3_ABS_08192",
            "T3_ABS_12288",
            "T3_ABS_16480",
        )
        owned = {
            checkpoints[0]: {0},
            checkpoints[1]: {0, 1},
            checkpoints[2]: {0, 1, 2},
            checkpoints[3]: {0, 1},
            checkpoints[4]: {0, 1, 3},
        }
        rows = _t3_growth(owned, {checkpoint: set() for checkpoint in checkpoints})
        self.assertFalse(formula_fits(FORMULA, rows, conversion_legs(rows)))

    def test_growth_rejects_page_newly_owned_and_newly_available(self) -> None:
        checkpoints = (
            "T3_CREATE",
            "T3_ABS_04096",
            "T3_ABS_08192",
            "T3_ABS_12288",
            "T3_ABS_16480",
        )
        owned = {
            checkpoints[0]: {0},
            checkpoints[1]: {0, 1},
            checkpoints[2]: {0, 1, 2},
            checkpoints[3]: {0, 1, 2, 3},
            checkpoints[4]: {0, 1, 2, 3, 4},
        }
        available = {checkpoint: set() for checkpoint in checkpoints}
        available[checkpoints[2]] = {2}
        rows = _t3_growth(owned, available)
        self.assertFalse(formula_fits(FORMULA, rows, conversion_legs(rows)))

    def test_complete_monotone_growth_signature_passes(self) -> None:
        checkpoints = (
            "T3_CREATE",
            "T3_ABS_04096",
            "T3_ABS_08192",
            "T3_ABS_12288",
            "T3_ABS_16480",
        )
        owned = {
            checkpoint: set(range(index + 1))
            for index, checkpoint in enumerate(checkpoints)
        }
        rows = _t3_growth(owned, {checkpoint: set() for checkpoint in checkpoints})
        self.assertTrue(formula_fits(FORMULA, rows, conversion_legs(rows)))

    def test_churn_and_idle_use_both_allocation_roles_and_span(self) -> None:
        checkpoints = (
            "T4_CREATE",
            "T1_REL_0064",
            "T1_REL_0512",
            "T1_REL_0768",
            "T1_REL_1280",
            "T1_DELETE_ALL",
            "T1_REINSERT_SAME",
            "T1_IDLE_R",
        )
        owned = (
            {0},
            {0, 1},
            {0, 1, 2},
            {0, 1, 2, 3},
            {0, 1, 2, 3, 4},
            {0},
            {0, 1, 2, 3, 4},
            {0, 1, 2, 3, 4},
        )
        available = (set(), set(), set(), set(), set(), {1, 2, 3, 4}, set(), set())
        rows = tuple(
            row
            for checkpoint, owned_pages, available_pages in zip(
                checkpoints, owned, available
            )
            for row in (
                _type_0(
                    checkpoint,
                    owned_pages,
                    instance="T1-v1",
                    allocation_role="owned_in_use",
                    ordinal=0,
                ),
                _type_0(
                    checkpoint,
                    available_pages,
                    instance="T1-v1",
                    allocation_role="available",
                    ordinal=1,
                ),
            )
        )
        self.assertTrue(transition_signature_fits(FORMULA, rows))

        outside = list(rows)
        delete_index = next(
            index
            for index, row in enumerate(outside)
            if row.checkpoint_id == "T1_REL_1280"
            and row.allocation_role == "owned_in_use"
        )
        outside[delete_index] = _type_0(
            "T1_REL_1280",
            {0, 1, 2, 3, 4, 20},
            instance="T1-v1",
            allocation_role="owned_in_use",
            ordinal=0,
        )
        self.assertFalse(transition_signature_fits(FORMULA, outside))

        idle_mismatch = list(rows)
        idle_index = next(
            index
            for index, row in enumerate(idle_mismatch)
            if row.checkpoint_id == "T1_IDLE_R"
            and row.allocation_role == "available"
        )
        idle_mismatch[idle_index] = _type_0(
            "T1_IDLE_R",
            {7},
            instance="T1-v1",
            allocation_role="available",
            ordinal=1,
        )
        self.assertFalse(transition_signature_fits(FORMULA, idle_mismatch))


if __name__ == "__main__":
    unittest.main()
