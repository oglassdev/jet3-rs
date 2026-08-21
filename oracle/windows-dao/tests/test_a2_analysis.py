"""Focused, inline A2 analyzer fixtures; no acquired data is used here."""

from __future__ import annotations

import ast
import hashlib
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_analysis import ReplicaInput, build_analysis  # noqa: E402
from a2_layers import derive_conversion, derive_tdef  # noqa: E402
from a2_model import (  # noqa: E402
    CHECKPOINT_IDS,
    MAX_CANDIDATE_MODELS,
    MAX_PAGE_BYTES,
    MAX_RECORD_CANDIDATES,
    MAX_WORK_UNITS,
    PAGE_SIZE,
    PER_PAGE_CANDIDATES,
    PREDICATES,
    Abort,
    View,
    WorkCounter,
    candidate_page_space,
    derive_global_record,
    qualify_global_pages,
    qualify_tdef_pages,
    require_one_page,
)

GLOBAL_PAGE = 1
LOW_MAP_PAGE = 2
HIGH_MAP_PAGE = 3
TDEF_PAGE = 4
RECORD_START = 2011
INLINE_BOUNDARY = RECORD_START + 7
CONVERSION = "P_ABS_04096"


def counts() -> dict[str, int]:
    values = dict.fromkeys(CHECKPOINT_IDS, 17)
    values.update(
        {
            "E0": 8,
            "E0R": 8,
            "D_GROW_0128": 10,
            "D_DROP": 10,
            "D_RECREATE_EMPTY": 10,
            "D_REGROW_0128": 12,
            "L_REL_0064": 13,
            "L_REL_0512": 14,
            "L_REL_0768": 15,
            "L_REL_0896": 16,
            "L_REL_0904": 17,
            "L_REL_1024": 17,
            "L_REL_1088": 17,
            "L_REL_1280": 17,
            "L_DELETE_ALL": 17,
            "L_REINSERT_SAME": 17,
            "L_IDLE_REOPEN": 17,
            "P_ABS_04096": 18,
            "P_ABS_08192": 19,
            "P_ABS_12288": 20,
            "P_ABS_16480": 21,
            "H_REL_0064": 22,
            "H_REL_0896": 23,
            "H_REL_0904": 24,
            "H_IDLE_REOPEN": 24,
        }
    )
    return values


def bitmap(count: int, polarity: str, width: int, first_page: int = 1) -> bytes:
    used_count = max(0, count - first_page)
    used = (1 << used_count) - 1 if used_count else 0
    if polarity == "set_means_not_in_use":
        used ^= (1 << (width * 8)) - 1
    return used.to_bytes(width, "little")


def global_page(checkpoint: str, polarity: str, *, bad_crosscheck: bool = False) -> bytes:
    body = bytearray([0xA6] * PAGE_SIZE)
    count = counts()[checkpoint]
    body[RECORD_START:] = bytes(PAGE_SIZE - RECORD_START)
    if checkpoint in {"E0", "E0R", "D_DROP", "D_RECREATE_EMPTY"}:
        represented = 8
    elif checkpoint == "D_GROW_0128":
        represented = 10
    elif checkpoint == "D_REGROW_0128":
        represented = 12
    else:
        represented = count
    body[RECORD_START + 1 : RECORD_START + 5] = (1).to_bytes(4, "little")
    if CHECKPOINT_IDS.index(checkpoint) < CHECKPOINT_IDS.index(CONVERSION):
        body[RECORD_START] = 0
        width = PAGE_SIZE - RECORD_START - 5 if checkpoint.startswith("D_") or checkpoint == "E0" or checkpoint == "E0R" else 2
        encoded = bitmap(represented, polarity, width)
        if bad_crosscheck and checkpoint == "L_REL_0512":
            encoded = bitmap(counts()["L_REL_0064"], polarity, width)
        body[RECORD_START + 5 : RECORD_START + 5 + width] = encoded
    else:
        body[RECORD_START] = 1
        body[RECORD_START + 1 : RECORD_START + 5] = LOW_MAP_PAGE.to_bytes(4, "little")
        if CHECKPOINT_IDS.index(checkpoint) >= CHECKPOINT_IDS.index("H_REL_0064"):
            body[RECORD_START + 5 : RECORD_START + 9] = HIGH_MAP_PAGE.to_bytes(4, "little")
    return bytes(body)


def extended_page(checkpoint: str, polarity: str, slot: int, *, no_discriminator: bool) -> bytes:
    body = bytearray(PAGE_SIZE)
    body[0:4] = b"\x05\x01\x00\x00"
    count = counts()[checkpoint]
    if checkpoint in {"E0", "E0R", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128"}:
        count = 8
    if no_discriminator and checkpoint == "H_REL_0064":
        count = counts()["P_ABS_16480"]
    used = (1 << count) - 1 if slot == 0 else 0
    if no_discriminator and slot == 1 and CHECKPOINT_IDS.index(checkpoint) >= CHECKPOINT_IDS.index("H_REL_0064"):
        used = 1
    if polarity == "set_means_not_in_use":
        used ^= (1 << ((PAGE_SIZE - 4) * 8)) - 1
    body[4:] = used.to_bytes(PAGE_SIZE - 4, "little")
    return bytes(body)


def pointer(page: int, slot: int) -> bytes:
    return page.to_bytes(3, "little") + bytes([slot])


def tdef_page(checkpoint: str, *, invalid_pointer: bool = False) -> bytes:
    body = bytearray(PAGE_SIZE)
    growth_page = LOW_MAP_PAGE
    growth_slot = 200
    if CHECKPOINT_IDS.index(checkpoint) >= CHECKPOINT_IDS.index("L_REL_0512"):
        growth_page = HIGH_MAP_PAGE
        growth_slot = 201
    if checkpoint in {"D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128"}:
        growth_page = LOW_MAP_PAGE
        growth_slot = 200
    if invalid_pointer and checkpoint in {"P_ABS_16480", "H_REL_0064", "H_REL_0896", "H_REL_0904", "H_IDLE_REOPEN"}:
        growth_page = 999
    churned = checkpoint == "L_DELETE_ALL"
    churn_page = HIGH_MAP_PAGE if churned else LOW_MAP_PAGE
    body[100:104] = pointer(growth_page, growth_slot)
    body[110:114] = pointer(churn_page, 201 if churned else 200)
    return bytes(body)


@dataclass
class MemoryReplica:
    polarity: str
    no_discriminator: bool = False
    invalid_pointer: bool = False
    bad_crosscheck: bool = False
    extra_qualified_global: bool = False

    def __post_init__(self) -> None:
        self.reads: list[str] = []
        self.blobs: dict[str, bytes] = {}
        self.indexes: dict[str, tuple[str, ...]] = {}
        for checkpoint in CHECKPOINT_IDS:
            hashes: list[str] = []
            for page in range(counts()[checkpoint]):
                if page == GLOBAL_PAGE:
                    payload = global_page(checkpoint, self.polarity, bad_crosscheck=self.bad_crosscheck)
                elif page == LOW_MAP_PAGE:
                    payload = extended_page(
                        checkpoint, self.polarity, 0, no_discriminator=self.no_discriminator
                    )
                elif page == HIGH_MAP_PAGE:
                    payload = extended_page(
                        checkpoint, self.polarity, 1, no_discriminator=self.no_discriminator
                    )
                elif page == TDEF_PAGE:
                    payload = tdef_page(checkpoint, invalid_pointer=self.invalid_pointer)
                elif self.extra_qualified_global and page == 5 and checkpoint in {
                    "D_GROW_0128",
                    "D_DROP",
                }:
                    payload = bytes([0x91 if checkpoint == "D_GROW_0128" else 0x92]) * PAGE_SIZE
                else:
                    payload = bytes([page & 0xFF]) * PAGE_SIZE
                digest = hashlib.sha256(payload).hexdigest()
                self.blobs[digest] = payload
                hashes.append(digest)
            self.indexes[checkpoint] = tuple(hashes)

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return CHECKPOINT_IDS

    @property
    def page_count(self) -> dict[str, int]:
        return {checkpoint: len(hashes) for checkpoint, hashes in self.indexes.items()}

    @property
    def ordered_page_sha256(self) -> dict[str, tuple[str, ...]]:
        return self.indexes

    def page_bytes(self, sha256: str) -> bytes:
        self.reads.append(sha256)
        return self.blobs[sha256]


class OrderedSource:
    def __init__(self, replica: ReplicaInput, events: list[int], frozen: Path) -> None:
        self.replica = replica
        self.events = events
        self.frozen = frozen

    def open(self) -> ReplicaInput:
        if self.replica.replica == 3:
            if not self.frozen.is_file():
                raise AssertionError("holdout opened before candidate freeze")
            self.events.append(3)
        else:
            self.events.append(self.replica.replica)
        return self.replica


def input_for(number: int, replica: MemoryReplica) -> ReplicaInput:
    return ReplicaInput(replica, number, "synthetic-a2", "0" * 40, "1" * 64, True)


class A2PredicateTests(unittest.TestCase):
    def view(self, polarity: str = "set_means_in_use", **kwargs: bool) -> tuple[MemoryReplica, View]:
        replica = MemoryReplica(polarity, **kwargs)
        return replica, View(replica, WorkCounter())

    def test_page_qualification_precedes_every_blob_read(self) -> None:
        replica, view = self.view()
        pages = candidate_page_space((view,))
        self.assertEqual(qualify_global_pages(view, pages), (GLOBAL_PAGE,))
        self.assertEqual(qualify_tdef_pages(view, pages), (TDEF_PAGE,))
        self.assertEqual(replica.reads, [])
        derive_global_record(view, GLOBAL_PAGE)
        self.assertNotEqual(replica.reads, [])

    def test_d_relation_selects_each_polarity_and_uses_terminal_slack(self) -> None:
        for polarity in ("set_means_in_use", "set_means_not_in_use"):
            with self.subTest(polarity=polarity):
                _, view = self.view(polarity)
                model = derive_global_record(view, GLOBAL_PAGE)
                self.assertEqual(model.bit_polarity, polarity)
                self.assertEqual(model.record.end, PAGE_SIZE)
                self.assertGreaterEqual(model.zero_suffix_slack_bytes, 16)

    def test_conversion_uses_frozen_global_record_and_both_polarities(self) -> None:
        for polarity in ("set_means_in_use", "set_means_not_in_use"):
            with self.subTest(polarity=polarity):
                _, view = self.view(polarity)
                global_model = derive_global_record(view, GLOBAL_PAGE)
                conversion = derive_conversion(view, global_model)
                self.assertEqual(conversion.conversion_checkpoint_id, CONVERSION)
                self.assertEqual(conversion.inline_boundary, INLINE_BOUNDARY)
                self.assertEqual(conversion.active_slot_count_at_conversion, 1)
                self.assertEqual(conversion.active_slot_count_at_h_rel_0904, 2)

    def test_tdef_contains_only_two_transition_selective_pointers(self) -> None:
        _, view = self.view()
        model = derive_tdef(view, TDEF_PAGE, True)
        self.assertEqual(model.pointer_layout, "u24le_page_then_u8_slot")
        self.assertEqual(model.growth_pointer_offset, 100)
        self.assertEqual(model.delete_reinsert_pointer_offset, 110)
        self.assertEqual((model.record.start, model.record.end), (99, 115))

    def test_lph_crosscheck_cannot_change_the_d_selected_polarity(self) -> None:
        _, view = self.view(bad_crosscheck=True)
        global_model = derive_global_record(view, GLOBAL_PAGE)
        self.assertEqual(global_model.bit_polarity, "set_means_in_use")
        with self.assertRaises(Abort) as caught:
            derive_conversion(view, global_model)
        self.assertEqual(caught.exception.predicate_id, "A2-POLARITY-CROSSCHECK")

    def test_named_predicate_units_are_fail_closed(self) -> None:
        with self.assertRaises(Abort) as none:
            require_one_page((), "A2-GLOBAL-PAGE-NONE", "A2-GLOBAL-PAGE-MULTIPLE")
        self.assertEqual(none.exception.reason, "no_physical_page_satisfies_global_transition_predicates")
        with self.assertRaises(Abort) as multiple:
            require_one_page((1, 2), "A2-GLOBAL-PAGE-NONE", "A2-GLOBAL-PAGE-MULTIPLE")
        self.assertEqual(multiple.exception.predicate_id, "A2-GLOBAL-PAGE-MULTIPLE")
        with self.assertRaises(Abort) as bounded:
            WorkCounter().charge(600_000_001)
        self.assertEqual(bounded.exception.predicate_id, "A2-RESOURCE-BOUND")

    def test_work_candidate_model_and_blob_bounds_accept_exact_and_reject_over(self) -> None:
        work = WorkCounter()
        work.value = MAX_WORK_UNITS - 1
        work.charge(1)
        with self.assertRaises(Abort):
            work.charge(1)

        work = WorkCounter()
        work.record_candidates = MAX_RECORD_CANDIDATES - PER_PAGE_CANDIDATES
        work.enumerate_intervals()
        self.assertEqual(work.record_candidates, MAX_RECORD_CANDIDATES)
        with self.assertRaises(Abort):
            work.enumerate_intervals()

        work = WorkCounter()
        work.candidate_models = MAX_CANDIDATE_MODELS - 1
        work.examine_models()
        with self.assertRaises(Abort):
            work.examine_models()

        work = WorkCounter()
        work.page_bytes_read = MAX_PAGE_BYTES - PAGE_SIZE
        work.opened("0" * 64)
        self.assertEqual(work.page_bytes_read, MAX_PAGE_BYTES)
        with self.assertRaises(Abort):
            work.opened("1" * 64)

    def test_every_registered_abort_has_one_literal_site_and_one_mapping(self) -> None:
        site_ids: set[str] = set()
        for name in ("a2_model.py", "a2_layers.py", "a2_analysis.py"):
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("A2-"):
                    site_ids.add(node.value)
        self.assertEqual(site_ids, set(PREDICATES))
        for predicate_id, (reason, _) in PREDICATES.items():
            reached = Abort(predicate_id)
            self.assertEqual((reached.predicate_id, reached.reason), (predicate_id, reason))


class A2LayeredAnalysisTests(unittest.TestCase):
    def analyze(self, replicas: list[MemoryReplica]) -> tuple[dict[str, object], list[int]]:
        with tempfile.TemporaryDirectory() as directory:
            frozen = Path(directory) / "analysis" / "derivation-candidates.json"
            events: list[int] = []
            sources = [
                OrderedSource(input_for(index, replica), events, frozen)
                for index, replica in enumerate(replicas, 1)
            ]
            report = build_analysis(sources, frozen, lambda digest: self.assertEqual(len(digest), 64))
            self.assertTrue(frozen.is_file())
            self.assertEqual(
                hashlib.sha256(frozen.read_bytes()).hexdigest(),
                report["derivation_candidate_set_sha256"],
            )
            return report, events

    def test_all_layers_decisive_and_holdout_opens_after_freeze(self) -> None:
        report, events = self.analyze([MemoryReplica("set_means_in_use") for _ in range(3)])
        self.assertEqual(events, [1, 2, 3])
        self.assertEqual(report["scientific_outcome"], "one_or_more_submodels_predict_holdout")
        self.assertEqual(report["terminal_predicate_ids"], [])
        self.assertEqual(report["submodels"]["global_map"]["record"]["status"], "decisive_predicts_holdout")
        self.assertEqual(report["submodels"]["global_map"]["conversion_inline"]["status"], "decisive_predicts_holdout")
        self.assertEqual(report["submodels"]["global_map"]["extended_base"]["status"], "decisive_predicts_holdout")
        self.assertEqual(report["submodels"]["tdef"]["pointer_pair"]["status"], "decisive_predicts_holdout")

    def test_base_no_outcome_does_not_erase_other_decisive_layers(self) -> None:
        report, _ = self.analyze(
            [MemoryReplica("set_means_in_use", no_discriminator=True) for _ in range(3)]
        )
        global_map = report["submodels"]["global_map"]
        self.assertEqual(global_map["record"]["status"], "decisive_predicts_holdout")
        self.assertEqual(global_map["conversion_inline"]["status"], "decisive_predicts_holdout")
        self.assertEqual(global_map["extended_base"]["status"], "no_outcome")
        self.assertIn("insufficient_base_discrimination", report["no_outcome_reasons"])
        self.assertEqual(report["scientific_outcome"], "one_or_more_submodels_predict_holdout")

    def test_multiple_qualified_pages_are_enumerated_before_one_page_survives(self) -> None:
        report, _ = self.analyze(
            [MemoryReplica("set_means_in_use", extra_qualified_global=True) for _ in range(3)]
        )
        self.assertEqual(report["qualified_page_counts"]["global_map"], 2)
        self.assertEqual(
            report["submodels"]["global_map"]["record"]["status"],
            "decisive_predicts_holdout",
        )
        self.assertGreaterEqual(report["record_candidates_examined"], 3 * 2_098_176)

    def test_holdout_is_pure_prediction_and_cannot_replace_frozen_pointer(self) -> None:
        replicas = [MemoryReplica("set_means_in_use") for _ in range(2)]
        replicas.append(MemoryReplica("set_means_in_use", invalid_pointer=True))
        report, _ = self.analyze(replicas)
        self.assertEqual(report["submodels"]["tdef"]["pointer_pair"]["status"], "no_outcome")
        self.assertIn("holdout_prediction_failure", report["no_outcome_reasons"])
        self.assertEqual(report["submodels"]["global_map"]["record"]["status"], "decisive_predicts_holdout")


if __name__ == "__main__":
    unittest.main()
