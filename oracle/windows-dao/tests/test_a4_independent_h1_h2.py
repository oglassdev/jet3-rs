"""Focused fake-replica tests for fresh A4 H1/H2 recomputation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
TESTS = ROOT / "oracle" / "windows-dao" / "tests"
sys.path[:0] = [str(SCRIPTS), str(TESTS)]

from a4_independent_contract import CONTRACT  # noqa: E402
from a4_independent_h1 import (  # noqa: E402
    _ReplicaState,
    _sha as h1_sha,
    _structural_stage,
    predict_h1_holdout,
    recompute_h1,
)
from a4_independent_h2 import (  # noqa: E402
    ValidationError as H2ValidationError,
    _candidate as h2_candidate,
    predict_h2_holdout,
    recompute_h2,
)


class FakeReplica:
    def __init__(self, number: int, pages: dict[tuple[str, int], bytes] | None = None):
        self.number = number
        self.checkpoint_ids = tuple(CONTRACT.checkpoint_ids)
        self.pages = {} if pages is None else dict(pages)
        self.page_calls = 0

    def state(self, checkpoint: str, page: int) -> str | None:
        value = self.pages.get((checkpoint, page))
        return None if value is None else __import__("hashlib").sha256(value).hexdigest()

    def page(self, checkpoint: str, page: int) -> bytes | None:
        self.page_calls += 1
        return self.pages.get((checkpoint, page))

    def index(self, checkpoint: str) -> dict[str, int]:
        return {"page_count": 32}


class ViewReplica:
    def __init__(self, view: object):
        self.view = view
        self.number = view.replica
        self.checkpoint_ids = tuple(CONTRACT.checkpoint_ids)

    def state(self, checkpoint: str, page: int) -> str | None:
        return self.view.hash_at(checkpoint, page)

    def page(self, checkpoint: str, page: int) -> bytes | None:
        return self.view.page_optional(checkpoint, page)

    def index(self, checkpoint: str) -> dict[str, int]:
        return {"page_count": self.view.page_count(checkpoint)}


def _contracts() -> list[dict[str, object]]:
    return [dict(row) for row in CONTRACT.plan["predicate_registry"]["predicate_contracts"]]


def _invalid_directory_page() -> bytes:
    page = bytearray(2048)
    page[0] = 1
    page[8:10] = (1).to_bytes(2, "little")
    page[10:12] = (10).to_bytes(2, "little")
    return bytes(page)


def _valid_directory_page() -> bytes:
    page = bytearray(2048)
    page[0] = 1
    page[8:10] = (1).to_bytes(2, "little")
    page[10:12] = (2040).to_bytes(2, "little")
    page[2040:] = bytes([0, 0, 0, 0, 0, 0, 0, 0])
    return bytes(page)


def _frozen_h1() -> dict[str, object]:
    bindings = []
    for replica in (1, 2):
        for role, instance in (
            ("T1", "T1-v1"),
            ("T2", "T2-v1"),
            ("T2", "T2-v2"),
            ("T3", "T3-v1"),
            ("T4", "T4-v1"),
        ):
            bindings.append({
                "replica": replica,
                "logical_role": role,
                "lifecycle_instance": instance,
                "tdef_page": 23,
                "locator_targets": [{"page": 5, "row": 0}, {"page": 5, "row": 0}],
                "applicable_checkpoint_range": {"start": "T1_CREATE_ID", "end": "T1_CREATE_ID"},
            })
    candidate = {
        "model_type": "h1_locator_pair",
        "canonical_model_id": "1" * 64,
        "canonical_candidate_id": "2" * 64,
        "model": {
            "layout": "u8_row_then_u24le_page",
            "table_signature_id": "a3_page23_masked_record_0_92",
            "locator_offsets": [35, 39],
        },
        "instance_bindings": bindings,
    }
    return {"status": "model", "candidates": [candidate]}


class IndependentH1H2Tests(unittest.TestCase):
    def test_candidate_hash_vector_has_no_trailing_newline(self) -> None:
        model = {
            "row_mask": 8191,
            "polarity": "set_bit_owned_in_use",
            "owned_in_use_locator_ordinal": 0,
            "available_locator_ordinal": 1,
        }
        self.assertEqual(
            h2_candidate(model)["canonical_candidate_id"],
            "a4f9d35e77e42704310d90264128698a8581855df4460bd7b499dcb015538def",
        )
        self.assertEqual(
            h1_sha({"model_type": "h2_final_role", "model": model}),
            "a4f9d35e77e42704310d90264128698a8581855df4460bd7b499dcb015538def",
        )

    def test_h1_tdef_none_stops_before_replica_two(self) -> None:
        replicas = {1: FakeReplica(1), 2: FakeReplica(2)}
        result = recompute_h1(
            replicas, plan=CONTRACT.plan, predicate_contracts=_contracts()
        )
        self.assertEqual(result.layer["terminal_predicate_id"], "A4-H1-TDEF-NONE")
        self.assertEqual(result.layer["predicate_measured_survivor_count"], 0)
        self.assertEqual(result.work_charges["tdef_lifecycle_signatures"], 0)
        self.assertEqual(replicas[2].page_calls, 0)

    def test_duplicate_signature_is_derived_from_pinned_base_mask(self) -> None:
        grammar = CONTRACT.plan["candidate_grammars"]["h1"]
        page = bytearray(2048)
        value = bytes.fromhex(grammar["table_record_signature"]["value_hex"])
        page[: len(value)] = value
        page[43:47] = page[39:43]
        pages = {(checkpoint, 23): bytes(page) for checkpoint in CONTRACT.checkpoint_ids}
        state = _ReplicaState(FakeReplica(1, pages), 1, CONTRACT.checkpoint_ids, 2048)
        binding = {
            "replica": 1,
            "logical_role": "T1",
            "lifecycle_instance": "T1-v1",
            "tdef_page": 23,
            "applicable_checkpoint_range": {"start": "T1_CREATE_ID", "end": "T4_IDLE_R"},
        }
        tdef = {"instance_bindings": [binding]}
        windows = {
            layout: {23: (35, 39, 43)} for layout in grammar["locator_layouts"]
        }
        candidates = _structural_stage(state, tdef, windows, CONTRACT.plan)
        self.assertEqual(len(candidates), 6)
        self.assertEqual(
            {row["model"]["table_signature_id"] for row in candidates},
            {"a4_pair_multiple_duplicate_locator_0_92"},
        )

    def test_h2_invalid_directory_is_structured_and_cut_off(self) -> None:
        pages = {("T1_CREATE_ID", 5): _invalid_directory_page()}
        replicas = {1: FakeReplica(1, pages), 2: FakeReplica(2, pages)}
        result = recompute_h2(
            replicas,
            _frozen_h1(),
            plan=CONTRACT.plan,
            predicate_contracts=_contracts(),
            snapshot_row_counts={},
        )
        self.assertEqual(
            result.layer["terminal_predicate_id"], "A4-H2-ROW-DIRECTORY-INVALID"
        )
        self.assertEqual(result.layer["terminal_evidence"]["kind"], "row_directory")
        self.assertEqual(result.work_charges["invalid_path_row_directory_entries"], 1)
        self.assertEqual(result.work_charges["valid_path_row_directory_entries"], 0)
        self.assertEqual(replicas[2].page_calls, 0)

    def test_h2_replica_two_invalid_reclassifies_prior_valid_work(self) -> None:
        replicas = {
            1: FakeReplica(1, {("T1_CREATE_ID", 5): _valid_directory_page()}),
            2: FakeReplica(2, {("T1_CREATE_ID", 5): _invalid_directory_page()}),
        }
        result = recompute_h2(
            replicas, _frozen_h1(), plan=CONTRACT.plan,
            predicate_contracts=_contracts(), snapshot_row_counts={},
        )
        self.assertEqual(result.layer["terminal_predicate_id"], "A4-H2-ROW-DIRECTORY-INVALID")
        self.assertEqual(result.work_charges["valid_path_row_directory_entries"], 0)
        self.assertEqual(result.work_charges["invalid_path_row_directory_entries"], 2)

    def test_holdout_predictors_fail_closed_without_bindings(self) -> None:
        replica = FakeReplica(3)
        self.assertFalse(
            predict_h1_holdout(replica, _frozen_h1(), plan=CONTRACT.plan)
        )
        with self.assertRaises(H2ValidationError) as raised:
            predict_h2_holdout(
                replica,
                _frozen_h1(),
                {"status": "model", "candidates": [h2_candidate({
                    "row_mask": 8191,
                    "polarity": "set_bit_owned_in_use",
                    "owned_in_use_locator_ordinal": 0,
                    "available_locator_ordinal": 1,
                })]},
                plan=CONTRACT.plan,
                snapshot_row_counts={},
            )
        self.assertEqual(raised.exception.code, "independent_h2_holdout_h1_binding")

    def test_decisive_synthetic_matches_h1_h2_oracle_projection(self) -> None:
        from test_a4_analyzer import _COMMIT, _inputs
        from a4_analysis_input import check_analysis_input
        from a4_layer_h1 import agree_h1_replicas, derive_h1_replica
        from a4_layer_h2 import agree_h2_replicas, derive_h2_replica
        from a4_model import WorkLedger

        inputs = _inputs()
        checked = check_analysis_input("a4-synthetic", _COMMIT, inputs)
        replicas = {
            number: ViewReplica(checked.views[number]) for number in (1, 2)
        }
        contracts = _contracts()
        independent_h1 = recompute_h1(
            replicas, plan=CONTRACT.plan, predicate_contracts=contracts
        )
        independent_h2 = recompute_h2(
            replicas,
            independent_h1,
            plan=CONTRACT.plan,
            predicate_contracts=contracts,
            snapshot_row_counts={
                number: checked.replicas[number].table_row_counts
                for number in (1, 2)
            },
        )

        ledger = WorkLedger()
        oracle_h1_by = {
            number: derive_h1_replica(
                checked.views[number], checked.qualified_tdef_pages[number], ledger
            )
            for number in (1, 2)
        }
        oracle_h1 = agree_h1_replicas(oracle_h1_by[1], oracle_h1_by[2])
        oracle_h2_by = {
            number: derive_h2_replica(
                checked.views[number],
                oracle_h1_by[number],
                checked.replicas[number].table_row_counts,
                ledger,
            )
            for number in (1, 2)
        }
        oracle_h2 = agree_h2_replicas(oracle_h2_by[1], oracle_h2_by[2])

        self.assertEqual(independent_h1.layer["candidates"], [oracle_h1.document()])
        self.assertEqual(independent_h2.layer["candidates"], [oracle_h2.document()])
        oracle_work = ledger.document()
        for name, value in independent_h1.work_charges.items():
            self.assertEqual(value, oracle_work[name], name)
        for name, value in independent_h2.work_charges.items():
            self.assertEqual(value, oracle_work[name], name)
        independent_pages = {
            (row["replica"], row["checkpoint_id"], row["page_number"])
            for row in (*independent_h1.qualified_pages, *independent_h2.qualified_pages)
        }
        oracle_pages = {
            (page.replica, page.checkpoint_id, page.page_number)
            for page in ledger.qualified_pages()
        }
        self.assertEqual(independent_pages, oracle_pages)


if __name__ == "__main__":
    unittest.main()
