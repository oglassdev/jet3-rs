"""Focused A4 adapter and lifecycle-boundary regressions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

import a4_analysis_holdout  # noqa: E402
import a4_layers  # noqa: E402
from a4_layer_h1 import _preserved_window  # noqa: E402
from a4_layer_h1 import H1Binding, H1ReplicaCandidate, LocatorTarget, _target_valid  # noqa: E402
from a4_layer_h2 import H2ReplicaCandidate  # noqa: E402
from a4_layer_h2_types import FrozenOwnedRow  # noqa: E402
from a4_layer_h3 import (  # noqa: E402
    H3Candidate,
    SlotObservation,
    TraversalObservation,
    admitted_pages,
)
from a4_layer_h4 import derive_catalog_root  # noqa: E402
from a4_layer_h4_fields import identifier_assignment_exists  # noqa: E402
from a4_layers import catalog_root_observations, h3_observations  # noqa: E402
from a4_model import A4AnalysisError, QualifiedPage, WorkLedger  # noqa: E402
from a4_spec import CHECKPOINT_IDS, PAGE_SIZE, PLAN  # noqa: E402


class _WindowView:
    def __init__(self, invalid_checkpoint: str) -> None:
        self.invalid_checkpoint = invalid_checkpoint
        self.visited: list[str] = []

    def page_optional(self, checkpoint: str, page: int) -> bytes:
        self.visited.append(checkpoint)
        payload = bytearray(2048)
        if checkpoint == self.invalid_checkpoint:
            payload[:3] = (20480).to_bytes(3, "little")
        return bytes(payload)


class _ReferenceView:
    replica = 1

    def __init__(self) -> None:
        self.opened: list[int] = []

    def page_optional(self, checkpoint: str, page: int) -> bytes:
        self.opened.append(page)
        if page != 30:
            raise AssertionError("reference traversal continued after invalid tag")
        return bytes((0x01,)) + bytes(2047)


class _ValidReferenceView:
    replica = 1

    def __init__(self) -> None:
        self.opened: list[int] = []

    def page_optional(self, _checkpoint: str, page: int) -> bytes:
        self.opened.append(page)
        return bytes((0x05,)) + bytes(PAGE_SIZE - 1)


class _TargetView:
    replica = 1

    @staticmethod
    def page_optional(_checkpoint: str, _page: int) -> bytes:
        payload = bytearray(PAGE_SIZE)
        payload[0] = 0x01
        payload[8:10] = (1).to_bytes(2, "little")
        return bytes(payload)


def _data_page(row: bytes) -> bytes:
    payload = bytearray(PAGE_SIZE)
    payload[0] = 0x01
    payload[8:10] = (1).to_bytes(2, "little")
    start = PAGE_SIZE - len(row)
    payload[10:12] = start.to_bytes(2, "little")
    payload[start:] = row
    return bytes(payload)


class _SystemView:
    def __init__(self, replica: int, reference_tag: int) -> None:
        self.replica = replica
        self.opened: list[tuple[str, int]] = []
        tdef = bytearray(PAGE_SIZE)
        tdef[0] = 0x02
        tdef[35:39] = bytes((0, 3, 0, 0))
        tdef[39:43] = bytes((0, 4, 0, 0))
        self.pages = {
            2: bytes(tdef),
            3: _data_page(b"\x01" + (5).to_bytes(4, "little")),
            4: _data_page(b"\x00" + bytes(5)),
            5: bytes((reference_tag,)) + bytes(PAGE_SIZE - 1),
        }

    def page_optional(self, checkpoint: str, page: int) -> bytes | None:
        self.opened.append((checkpoint, page))
        return self.pages.get(page)

    def page(self, checkpoint: str, page: int) -> bytes:
        self.opened.append((checkpoint, page))
        return self.pages[page]

    @staticmethod
    def page_count(_checkpoint: str) -> int:
        return 6

    def hash_at(self, _checkpoint: str, page: int) -> str | None:
        return f"{page:064x}" if page in self.pages else None


def _system_models(replica: int):
    h1 = H1ReplicaCandidate(
        replica,
        "u8_row_then_u24le_page",
        "test-signature",
        (35, 39),
        (),
    )
    h2 = H2ReplicaCandidate(0, 0x1FFF, "set_bit_owned_in_use", 0, 1)
    h3 = H3Candidate(
        "h3_final_base_formula",
        {
            "conversion": PLAN["candidate_grammars"]["h3"][
                "conversion_candidates"
            ][0],
            "base_formula": PLAN["candidate_grammars"]["h3"][
                "base_formulas"
            ][0],
        },
    )
    return h1, h2, h3


def _type1(reference: int) -> FrozenOwnedRow:
    return FrozenOwnedRow(
        1,
        "T1",
        "T1-v1",
        "owned_in_use",
        "T1_CREATE_ID",
        24,
        0,
        "type_1",
        None,
        (reference,),
        None,
    )


class A4LayerBoundaryTests(unittest.TestCase):
    def test_h1_window_must_decode_at_all_25_checkpoints(self) -> None:
        view = _WindowView("T4_IDLE_R")
        self.assertFalse(
            _preserved_window(view, 23, "u24le_page_then_u8_row", 0)
        )
        self.assertEqual(view.visited, list(CHECKPOINT_IDS))

    def test_h3_invalid_reference_stops_before_bitmap_or_later_reference(self) -> None:
        view = _ReferenceView()
        work = WorkLedger()
        rows = h3_observations(view, (_type1(30), _type1(31)), work)
        self.assertEqual(view.opened, [30])
        self.assertEqual(
            work.value("type_0_and_tag_05_bitmap_bits"), 0
        )
        self.assertEqual(rows[0].slots[0].referenced_page_tag, 0x01)
        self.assertIsNone(rows[1].slots[0].referenced_page_tag)
        self.assertIn(
            QualifiedPage(1, "T1_CREATE_ID", 30), work.qualified_pages()
        )

    def test_h3_stops_before_reading_seventeenth_qualified_reference(self) -> None:
        view = _ValidReferenceView()
        row = FrozenOwnedRow(
            1,
            "T1",
            "T1-v1",
            "owned_in_use",
            "T1_CREATE_ID",
            24,
            0,
            "type_1",
            None,
            tuple(range(30, 47)),
            None,
        )
        with self.assertRaisesRegex(A4AnalysisError, "A4-RESOURCE-BOUND"):
            h3_observations(view, (row,), WorkLedger())
        self.assertEqual(view.opened, list(range(30, 46)))

    def test_h3_stops_before_materializing_seventeenth_admitted_page(self) -> None:
        observation = TraversalObservation(
            1,
            "T1_CREATE_ID",
            24,
            "type_1",
            "T1-v1",
            slots=(SlotObservation(0, 30, 0x05, frozenset(range(17))),),
        )
        with self.assertRaisesRegex(A4AnalysisError, "A4-RESOURCE-BOUND"):
            admitted_pages(
                observation,
                PLAN["candidate_grammars"]["h3"]["base_formulas"][0],
                maximum=16,
            )

    def test_h1_target_validity_records_target_pages(self) -> None:
        binding = H1Binding(
            1,
            "T2",
            "T2-v1",
            7,
            (LocatorTarget(30, 0), LocatorTarget(31, 0)),
        )
        work = WorkLedger()
        self.assertTrue(
            _target_valid(
                _TargetView(), (binding,), "u8_row_then_u24le_page", work
            )
        )
        self.assertEqual(
            set(work.qualified_pages()),
            {
                QualifiedPage(1, "T2_CREATE", 30),
                QualifiedPage(1, "T2_CREATE", 31),
            },
        )

    def test_system_indirect_reference_validates_tag_before_bitmap(self) -> None:
        view = _SystemView(1, 0x01)
        h1, h2, h3 = _system_models(1)
        work = WorkLedger()
        observation = catalog_root_observations(
            view, (2,), h1, h2, h3, work
        )[0]
        self.assertEqual(observation.traversal_valid_checkpoints, frozenset())
        self.assertEqual(work.value("type_0_and_tag_05_bitmap_bits"), 0)
        self.assertEqual(work.value("type_1_slots"), 1)
        self.assertIn(QualifiedPage(1, "EMPTY", 5), work.qualified_pages())
        self.assertIn(QualifiedPage(1, "EMPTY", 3), work.qualified_pages())
        self.assertIn(QualifiedPage(1, "EMPTY", 4), work.qualified_pages())
        self.assertEqual(
            view.opened,
            [("EMPTY", 2), ("EMPTY", 2), ("EMPTY", 3), ("EMPTY", 4), ("EMPTY", 5)],
        )
        with self.assertRaisesRegex(A4AnalysisError, "A4-H4-CATALOG-ROOT-NONE"):
            derive_catalog_root(1, (observation,), work)
        self.assertEqual(work.value("catalog_root_signatures"), 1)

    def test_system_malformed_available_map_is_recorded_before_prefix_stops(self) -> None:
        view = _SystemView(1, 0x05)
        view.pages[4] = b""
        h1, h2, h3 = _system_models(1)
        work = WorkLedger()
        observation = catalog_root_observations(
            view, (2,), h1, h2, h3, work
        )[0]
        self.assertEqual(observation.traversal_valid_checkpoints, frozenset())
        self.assertIn(QualifiedPage(1, "EMPTY", 3), work.qualified_pages())
        self.assertIn(QualifiedPage(1, "EMPTY", 4), work.qualified_pages())
        self.assertNotIn(("EMPTY", 5), view.opened)

    def test_system_malformed_owned_map_stops_before_available_map(self) -> None:
        view = _SystemView(1, 0x05)
        view.pages[3] = b""
        h1, h2, h3 = _system_models(1)
        work = WorkLedger()
        observation = catalog_root_observations(
            view, (2,), h1, h2, h3, work
        )[0]
        self.assertEqual(observation.traversal_valid_checkpoints, frozenset())
        self.assertIn(QualifiedPage(1, "EMPTY", 3), work.qualified_pages())
        self.assertNotIn(QualifiedPage(1, "EMPTY", 4), work.qualified_pages())
        self.assertNotIn(("EMPTY", 4), view.opened)

    def test_system_type0_stops_before_seventeenth_admitted_page(self) -> None:
        view = _SystemView(1, 0x05)
        view.pages[3] = _data_page(b"\x00" + bytes(4) + b"\xff\xff\x01")
        h1, h2, h3 = _system_models(1)
        with self.assertRaisesRegex(A4AnalysisError, "A4-RESOURCE-BOUND"):
            catalog_root_observations(view, (2,), h1, h2, h3, WorkLedger())

    def test_system_type1_stops_before_seventeenth_reference_page(self) -> None:
        view = _SystemView(1, 0x05)
        references = tuple(range(5, 22))
        view.pages[3] = _data_page(
            b"\x01" + b"".join(page.to_bytes(4, "little") for page in references)
        )
        view.pages.update(
            {
                page: bytes((0x05,)) + bytes(PAGE_SIZE - 1)
                for page in references
            }
        )
        h1, h2, h3 = _system_models(1)
        work = WorkLedger()
        with self.assertRaisesRegex(A4AnalysisError, "A4-RESOURCE-BOUND"):
            catalog_root_observations(view, (2,), h1, h2, h3, work)
        self.assertNotIn(("EMPTY", 21), view.opened)
        self.assertEqual(
            {
                page.page_number
                for page in work.qualified_pages()
                if page.page_number in references
            },
            set(range(5, 21)),
        )

    def test_system_indirect_holdout_does_not_construct_qualified_page_3(self) -> None:
        view = _SystemView(3, 0x05)
        h1, h2, h3 = _system_models(3)
        observation = catalog_root_observations(
            view, (2,), h1, h2, h3, WorkLedger()
        )[0]
        self.assertEqual(
            observation.traversal_valid_checkpoints, frozenset(CHECKPOINT_IDS)
        )

    def test_identifier_reuse_depends_on_extant_interval(self) -> None:
        operations = (
            "T1_CREATE_ID", "T1_ADD_TEXT", "T1_ADD_INDEX", "T2_CREATE",
            "T2_RECREATE", "T3_CREATE", "T4_CREATE",
        )
        values = dict(zip(operations, (1, 2, 3, 4, 5, 4, 7)))
        options = {key: frozenset((value,)) for key, value in values.items()}
        self.assertTrue(identifier_assignment_exists(
            operations, options,
            "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2",
        ))
        values["T3_CREATE"] = 6
        values["T2_CREATE"] = 1
        simultaneous = {
            key: frozenset((value,)) for key, value in values.items()
        }
        self.assertFalse(identifier_assignment_exists(
            operations, simultaneous,
            "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2",
        ))
        values.update(T2_CREATE=4, T2_RECREATE=4)
        same_t2 = {key: frozenset((value,)) for key, value in values.items()}
        self.assertTrue(identifier_assignment_exists(
            operations, same_t2,
            "stable_for_same_physical_name_including_t2_v1_v2",
        ))

    def test_holdout_resource_terminal_is_not_a_prediction_failure(self) -> None:
        inputs = SimpleNamespace(
            view=object(),
            qualified_tdef_pages=(),
            replica=SimpleNamespace(table_row_counts={}),
        )
        frozen = SimpleNamespace(h1=object(), h2=object())
        with patch.object(
            a4_analysis_holdout, "predict_h1", return_value=object()
        ), patch.object(
            a4_layers, "predicts_h2", return_value=True
        ), patch.object(
            a4_analysis_holdout,
            "decode_frozen_owned_rows",
            side_effect=A4AnalysisError("A4-RESOURCE-BOUND"),
        ):
            with self.assertRaisesRegex(A4AnalysisError, "A4-RESOURCE-BOUND"):
                a4_analysis_holdout.evaluate_holdout(inputs, frozen, WorkLedger())


if __name__ == "__main__":
    unittest.main()
