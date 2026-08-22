"""Focused synthetic tests for the independent A2 validator.

Every page index and page blob below is hand-built from the preregistered plan.
No analyzer or generator fixture is imported or consulted.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a2_independent_core import (
    PAGE_SIZE,
    GlobalModel,
    ReplicaView,
    _minimal_tdef_record,
    derive_global_models,
    global_model_predicts,
    global_qualifying_pages,
    growth_polarity_violations,
)

CHECKPOINTS = (
    "E0",
    "E0R",
    "D_GROW_0128",
    "D_DROP",
    "D_RECREATE_EMPTY",
    "D_REGROW_0128",
    "L_REL_0064",
    "L_REL_0512",
    "L_REL_0768",
    "L_REL_0896",
    "L_REL_0904",
    "L_REL_1024",
    "L_REL_1088",
    "L_REL_1280",
    "L_DELETE_ALL",
    "L_REINSERT_SAME",
    "L_IDLE_REOPEN",
    "P_ABS_04096",
    "P_ABS_08192",
    "P_ABS_12288",
    "P_ABS_16480",
    "H_REL_0064",
    "H_REL_0896",
    "H_REL_0904",
    "H_IDLE_REOPEN",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _map_page(
    start: int, in_use: set[int], polarity: str = "set_means_not_in_use"
) -> bytes:
    free = 0xFF if polarity == "set_means_not_in_use" else 0x00
    page = bytearray((free,)) * PAGE_SIZE
    page[start : start + 5] = b"\0" * 5
    offset = start + 5
    for represented_page in in_use:
        byte, bit = divmod(represented_page, 8)
        if polarity == "set_means_not_in_use":
            page[offset + byte] &= ~(1 << bit)
        else:
            page[offset + byte] |= 1 << bit
    return bytes(page)


def _view(
    replica: int,
    checkpoint_pages: dict[str, list[bytes]],
) -> ReplicaView:
    blobs: dict[str, bytes] = {}
    hashes: dict[str, tuple[str, ...]] = {}
    for checkpoint in CHECKPOINTS:
        pages = checkpoint_pages[checkpoint]
        values = []
        for page in pages:
            digest = _digest(page)
            blobs[digest] = page
            values.append(digest)
        hashes[checkpoint] = tuple(values)
    return ReplicaView(
        replica,
        CHECKPOINTS,
        hashes,
        {checkpoint: len(checkpoint_pages[checkpoint]) for checkpoint in CHECKPOINTS},
        blobs.__getitem__,
    )


def _global_view(
    replica: int, *, bad_holdout: bool = False, opposite_growth: bool = False
) -> ReplicaView:
    start = 2000
    stable = bytes(PAGE_SIZE)
    states = {
        "E0": _map_page(start, set(range(8))),
        "E0R": _map_page(start, set(range(8))),
        "D_GROW_0128": _map_page(start, set(range(16))),
        "D_DROP": _map_page(start, set(range(8))),
        "D_RECREATE_EMPTY": _map_page(start, set(range(8))),
        "D_REGROW_0128": _map_page(start, set(range(16 if bad_holdout else 24))),
    }
    if bad_holdout:
        states["D_REGROW_0128"] = _map_page(start, set(range(16)))
    current = _map_page(start, set(range(32)))
    for checkpoint in CHECKPOINTS[6:]:
        states[checkpoint] = current
    if opposite_growth:
        states["L_REL_0512"] = _map_page(start, set(range(24)))
        for checkpoint in CHECKPOINTS[8:]:
            states[checkpoint] = states["L_REL_0512"]
    lengths = {
        "E0": 8,
        "E0R": 8,
        "D_GROW_0128": 16,
        "D_DROP": 16,
        "D_RECREATE_EMPTY": 16,
        "D_REGROW_0128": 24,
    }
    pages: dict[str, list[bytes]] = {}
    for checkpoint in CHECKPOINTS:
        length = lengths.get(checkpoint, 32)
        pages[checkpoint] = [states[checkpoint]] + [stable] * (length - 1)
    return _view(replica, pages)


class GlobalRecordTests(unittest.TestCase):
    def test_hash_qualification_precedes_unique_page_terminal_record(self) -> None:
        views = (_global_view(1), _global_view(2))
        qualified = global_qualifying_pages(views)
        self.assertEqual(qualified, (0,))
        self.assertEqual(
            derive_global_models(views, qualified),
            (
                GlobalModel(
                    page=0,
                    start=2000,
                    end=2048,
                    bit_polarity="set_means_not_in_use",
                    zero_suffix_slack_bytes=40,
                ),
            ),
        )

    def test_absence_is_an_explicit_hash_state_but_not_two_endpoint_differences(
        self,
    ) -> None:
        view = _global_view(1)
        self.assertEqual(global_qualifying_pages((view,)), (0,))
        self.assertIsNone(view.state("E0", 12))
        self.assertIsNotNone(view.state("D_GROW_0128", 12))
        self.assertEqual(view.state("D_GROW_0128", 12), view.state("D_DROP", 12))

    def test_frozen_model_rejects_holdout_without_additional_regrowth(self) -> None:
        derivation = (_global_view(1), _global_view(2))
        model = derive_global_models(derivation, global_qualifying_pages(derivation))[0]
        self.assertFalse(
            global_model_predicts(_global_view(3, bad_holdout=True), model)
        )

    def test_growth_polarity_crosscheck_names_the_opposite_leg(self) -> None:
        view = _global_view(1, opposite_growth=True)
        model = GlobalModel(0, 2000, 2048, "set_means_not_in_use", 40)
        self.assertEqual(
            growth_polarity_violations(view, model),
            (("L_REL_0064", "L_REL_0512"),),
        )


class TdefMinimalityTests(unittest.TestCase):
    def _tdef_view(self, noisy_between_windows: bool) -> ReplicaView:
        stable = bytes(PAGE_SIZE)
        pages: dict[str, list[bytes]] = {}
        for ordinal, checkpoint in enumerate(CHECKPOINTS):
            tdef = bytearray(PAGE_SIZE)
            if noisy_between_windows and ordinal == 7:
                tdef[15] = 1
            pages[checkpoint] = [stable, bytes(tdef)]
        return _view(1, pages)

    def test_inclusion_minimal_interval_adds_one_stable_byte_per_side(self) -> None:
        view = self._tdef_view(False)
        self.assertEqual(_minimal_tdef_record(view, 1, 10, 20), (9, 25))

    def test_change_between_pointer_signatures_rejects_interval(self) -> None:
        view = self._tdef_view(True)
        self.assertIsNone(_minimal_tdef_record(view, 1, 10, 20))


if __name__ == "__main__":
    unittest.main()
