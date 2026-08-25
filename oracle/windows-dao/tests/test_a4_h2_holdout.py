"""Focused holdout checks for the frozen A4 H2 transition model."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

from a4_layer_h1 import H1Binding, H1ReplicaCandidate, LocatorTarget  # noqa: E402
from a4_layer_h2 import H2ReplicaCandidate  # noqa: E402
from a4_layers import predicts_h2  # noqa: E402
from a4_spec import CHECKPOINT_IDS, CHECKPOINT_ORDINALS, PAGE_SIZE  # noqa: E402


def _type_0(pages: set[int]) -> bytes:
    bitmap = sum(1 << page for page in pages).to_bytes(1, "little")
    return bytes((0,)) + (0).to_bytes(4, "little") + bitmap


def _map_page(owned: set[int], available: set[int]) -> bytes:
    payload = bytearray(PAGE_SIZE)
    payload[0] = 0x01
    payload[8:10] = (2).to_bytes(2, "little")
    cursor = PAGE_SIZE
    for ordinal, row in enumerate((_type_0(owned), _type_0(available))):
        cursor -= len(row)
        payload[cursor : cursor + len(row)] = row
        offset = 10 + 2 * ordinal
        payload[offset : offset + 2] = cursor.to_bytes(2, "little")
    return bytes(payload)


class _HoldoutView:
    replica = 3

    def __init__(self, *, gained_page_also_available: bool) -> None:
        self._pages: dict[tuple[str, int], bytes] = {}
        threshold = CHECKPOINT_ORDINALS["T1_REL_0512"]
        for role, instance, page in (("T1", "T1-v1", 5), ("T3", "T3-v1", 6), ("T4", "T4-v1", 7)):
            binding = H1Binding(3, role, instance, 20 + page)
            for checkpoint in binding.checkpoints:
                grown = role == "T1" and CHECKPOINT_ORDINALS[checkpoint] >= threshold
                owned = {1, 2} if grown else {1}
                available = {2} if grown and gained_page_also_available else set()
                self._pages[(checkpoint, page)] = _map_page(owned, available)

    def page(self, checkpoint: str, page: int) -> bytes:
        return self._pages[(checkpoint, page)]

    @staticmethod
    def page_count(_checkpoint: str) -> int:
        return 64


def _h1() -> H1ReplicaCandidate:
    bindings = tuple(
        H1Binding(
            3,
            role,
            instance,
            20 + page,
            (LocatorTarget(page, 0), LocatorTarget(page, 1)),
        )
        for role, instance, page in (("T1", "T1-v1", 5), ("T3", "T3-v1", 6), ("T4", "T4-v1", 7))
    )
    return H1ReplicaCandidate(3, "u24le_page_then_u8_row", "tag_02", (4, 8), bindings)


class A4H2HoldoutTests(unittest.TestCase):
    def test_holdout_rejects_page_newly_owned_and_available_on_growth_leg(self) -> None:
        model = H2ReplicaCandidate(0, 0x1FFF, "set_bit_owned_in_use", 0, 1)
        counts = {checkpoint: {role: 1 for role in ("T1", "T2", "T3", "T4")} for checkpoint in CHECKPOINT_IDS}

        self.assertTrue(predicts_h2(_HoldoutView(gained_page_also_available=False), _h1(), model, counts))
        self.assertFalse(predicts_h2(_HoldoutView(gained_page_also_available=True), _h1(), model, counts))


if __name__ == "__main__":
    unittest.main()
