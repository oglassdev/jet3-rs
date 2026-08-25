"""Focused cutoff and union-accounting tests for A4 catalog preparation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import a4_layers as layer_module  # noqa: E402
from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_catalog_inventory import CatalogInventory, operation_records  # noqa: E402
from a4_layer_h2 import H2ReplicaCandidate  # noqa: E402
from a4_layer_h4 import OPERATIONS, CatalogRootObservation  # noqa: E402
from a4_layers import DerivationLayers, derive_layers  # noqa: E402
from a4_model import A4AnalysisError, QualifiedPage, WorkLedger  # noqa: E402
from a4_spec import CHECKPOINT_IDS  # noqa: E402
from a4_terminal import DerivationTerminal  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


class _OnePageView:
    replica = 1

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def page(self, checkpoint: str, page: int) -> bytes:
        if checkpoint != "EMPTY" or page != 4:
            raise AssertionError("unexpected test page")
        return self._payload


class _GrowingCatalogView:
    replica = 1

    @staticmethod
    def page(checkpoint: str, page: int) -> bytes:
        if page != 4:
            raise AssertionError("unexpected test page")
        return _row_page(256 if checkpoint == "EMPTY" else 257)

    @staticmethod
    def page_count(_checkpoint: str) -> int:
        return 5


def _three_row_page() -> bytes:
    payload = bytearray(2048)
    payload[0] = 0x01
    payload[8:10] = (3).to_bytes(2, "little")
    for ordinal, start in enumerate((2045, 2044, 2043)):
        offset = 10 + 2 * ordinal
        payload[offset : offset + 2] = start.to_bytes(2, "little")
    payload[2043:] = b"abcde"
    return bytes(payload)


def _row_page(count: int) -> bytes:
    payload = bytearray(2048)
    payload[0] = 0x01
    payload[8:10] = count.to_bytes(2, "little")
    for ordinal in range(count):
        start = 2048 - ordinal - 1
        offset = 10 + 2 * ordinal
        payload[offset : offset + 2] = start.to_bytes(2, "little")
    return bytes(payload)


class A4CatalogAccountingTests(unittest.TestCase):
    def test_page_is_recorded_for_empty_or_failed_inventory(self) -> None:
        for payload, fails in ((_row_page(0), False), (_row_page(680), True)):
            work = WorkLedger()
            inventory = CatalogInventory(_OnePageView(payload), 0x1FFF, work)
            if fails:
                with self.assertRaises(ValueError):
                    inventory.all_rows("EMPTY", 4)
            else:
                self.assertEqual(inventory.all_rows("EMPTY", 4), ())
            self.assertIn(QualifiedPage(1, "EMPTY", 4), work.qualified_pages())

    def test_rows_above_locator_ordinal_255_are_charged_but_not_candidates(self) -> None:
        work = WorkLedger()
        rows = CatalogInventory(
            _OnePageView(_row_page(257)), 0x1FFF, work
        ).all_rows("EMPTY", 4)
        self.assertEqual(len(rows), 257)
        self.assertIsNone(rows[-1][0])
        self.assertEqual(work.value("catalog_raw_rows"), 257)

        root = CatalogRootObservation(
            1,
            2,
            (35, 39),
            0x02,
            frozenset(CHECKPOINT_IDS),
            {checkpoint: "0" * 64 for checkpoint in CHECKPOINT_IDS},
            {checkpoint: frozenset({4}) for checkpoint in CHECKPOINT_IDS},
        )
        deltas = {
            operation: frozenset({4}) if operation == OPERATIONS[0] else frozenset()
            for operation in OPERATIONS
        }
        records = operation_records(
            _GrowingCatalogView(),
            root,
            deltas,
            H2ReplicaCandidate(1, 0x1FFF, "set_bit_owned_in_use", 0, 1),
            WorkLedger(),
        )
        self.assertEqual(records, ())

    def test_inventory_charges_every_row_once_with_qualified_identity(self) -> None:
        work = WorkLedger()
        inventory = CatalogInventory(_OnePageView(_three_row_page()), 0x1FFF, work)

        self.assertEqual(len(inventory.all_rows("EMPTY", 4)), 3)
        self.assertEqual(len(inventory.all_rows("EMPTY", 4)), 3)
        self.assertIsNotNone(inventory.row_at("EMPTY", 4, 1))
        self.assertIsNone(inventory.row_at("EMPTY", 4, 3))

        identities = work._identities["catalog_raw_rows"]
        self.assertEqual(work.value("catalog_raw_rows"), 3)
        self.assertEqual(
            identities,
            {
                (QualifiedPage(1, "EMPTY", 4), ordinal)
                for ordinal in range(3)
            },
        )

    def test_success_charges_initial_and_continuation_rows_union_once(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        work = WorkLedger()
        result = derive_layers(checked, work)
        self.assertIsInstance(result, DerivationLayers)
        assert isinstance(result, DerivationLayers)

        evidence_rows = {
            (
                QualifiedPage(
                    record.replica,
                    evidence.checkpoint_id,
                    evidence.locator.page,
                ),
                evidence.locator.row,
            )
            for records in result.h4_records.values()
            for record in records
            for evidence in record.checkpoint_evidence
        }
        identities = work._identities["catalog_raw_rows"]
        self.assertEqual(len(evidence_rows), 236)
        self.assertTrue(evidence_rows <= identities)
        self.assertEqual(work.value("catalog_raw_rows"), len(identities))

    def test_h3_replica_one_terminal_prevents_replica_two_adapter_work(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        original = layer_module.h3_observations
        reached: list[int] = []

        def guarded(view: object, rows: object, work: WorkLedger) -> object:
            replica = view.replica
            reached.append(replica)
            if replica == 2:
                raise A4AnalysisError(
                    "A4-RESOURCE-BOUND", detail="replica 2 must remain unreachable"
                )
            return original(view, rows, work)[:1]

        with patch.object(layer_module, "h3_observations", side_effect=guarded):
            result = derive_layers(checked)
        self.assertIsInstance(result, DerivationTerminal)
        assert isinstance(result, DerivationTerminal)
        self.assertEqual(result.predicate_id, "A4-H3-CONVERSION-NONE")
        self.assertTrue(reached)
        self.assertEqual(set(reached), {1})

    def test_h4_replica_one_record_none_prevents_replica_two_resource(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        reached: list[int] = []

        def guarded(
            view: object,
            root: object,
            deltas: object,
            h2: object,
            work: WorkLedger,
        ) -> tuple[()]:
            del root, deltas, h2, work
            reached.append(view.replica)
            if view.replica == 2:
                raise A4AnalysisError(
                    "A4-RESOURCE-BOUND", detail="replica 2 must remain unreachable"
                )
            return ()

        with patch.object(layer_module, "operation_records", side_effect=guarded):
            result = derive_layers(checked)
        self.assertIsInstance(result, DerivationTerminal)
        assert isinstance(result, DerivationTerminal)
        self.assertEqual(result.predicate_id, "A4-H4-CATALOG-RECORD-NONE")
        self.assertTrue(reached)
        self.assertEqual(set(reached), {1})


if __name__ == "__main__":
    unittest.main()
