"""A4 plan compiler and synthetic byte-generator contracts."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

import a4_generator_schedule as schedule_module  # noqa: E402
from a4_generator import (  # noqa: E402
    SyntheticParameters,
    generate_campaign,
    generate_replica,
    overshoots,
)
from a4_generator_pages import (  # noqa: E402
    TAG_TDEF,
    data_page,
    encode_locator,
    masked_tdef_page,
    type_0_row,
)
from a4_generator_schedule import EVENTS, PROFILES, SCHEDULE, EventKind  # noqa: E402
from a4_model import A4AnalysisError, View, WorkLedger, WORK_TERM_LIMITS  # noqa: E402
from a4_spec import (  # noqa: E402
    CHECKPOINT_IDS,
    PAGE_SIZE,
    PLAN,
    PREDICATE_IDS,
    SCHEMA_SHA256,
)


class A4GeneratorTests(unittest.TestCase):
    def test_checked_contract_is_exact_and_complete(self) -> None:
        self.assertEqual(len(CHECKPOINT_IDS), PLAN["checkpoint_design"]["count"])
        self.assertEqual(len(PREDICATE_IDS), len(PLAN["predicate_registry"]["ids"]))
        self.assertEqual(len(SCHEMA_SHA256), 14)
        self.assertEqual(tuple(event.checkpoint_id for event in EVENTS), CHECKPOINT_IDS)

    def test_schedule_derives_batch_arithmetic_from_the_document(self) -> None:
        changed = copy.deepcopy(PLAN)
        changed["tables"]["row_algorithm"]["growth_batch_rows"] = 40
        with mock.patch.object(schedule_module, "PLAN", changed):
            compiled = schedule_module.build_schedule()
        self.assertEqual(compiled.batch_rows, 40)
        self.assertEqual([compiled.profile(replica).rows_per_page for replica in (1, 2, 3)], [10, 9, 8])

    def test_lifecycles_and_growth_modes_are_compiled(self) -> None:
        self.assertEqual([instance.instance_id for instance in SCHEDULE.instances], [
            "T1-v1", "T2-v1", "T2-v2", "T3-v1", "T4-v1",
        ])
        growth = [event for event in EVENTS if event.kind is EventKind.GROW]
        self.assertEqual(len(growth), 11)
        self.assertEqual(SCHEDULE.event("T1_REL_0064").baseline_checkpoint_id, "T4_CREATE")
        self.assertIsNone(SCHEDULE.event("T3_ABS_04096").baseline_checkpoint_id)

    def test_page_encoders_are_exact_and_duplicate_signature_is_byte_derived(self) -> None:
        layout = PLAN["candidate_grammars"]["h1"]["locator_layouts"][1]
        duplicate = PLAN["candidate_grammars"]["h1"]["pair_multiple_reachability_signature"]
        first = encode_locator(layout, 24, 0)
        second = encode_locator(layout, 24, 1)
        page = masked_tdef_page(
            duplicate["signature_id"],
            {35: first, 39: second, 43: second},
        )
        self.assertEqual(len(page), PAGE_SIZE)
        self.assertEqual(page[0], TAG_TDEF)
        self.assertEqual(page[39:43], page[43:47])
        self.assertNotEqual(page[43:47], bytes.fromhex("04000000"))
        row_page = data_page((type_0_row(3, {3, 5}),), raw_flags={0: 0x1000})
        self.assertEqual(len(row_page), PAGE_SIZE)
        self.assertEqual(int.from_bytes(row_page[8:10], "little"), 1)

    def test_replicas_follow_exact_first_reaching_profiles(self) -> None:
        replicas = [generate_replica(replica=replica) for replica in (1, 2, 3)]
        for replica in replicas:
            self.assertEqual(tuple(replica.ordered_page_sha256), CHECKPOINT_IDS)
            measured = overshoots(replica)
            self.assertTrue(all(value >= 0 for value in measured.values()))
            self.assertLessEqual(max(replica.page_count.values()), PLAN["bounds"]["max_final_pages_per_replica"])
            View(replica.replica, replica)
        signatures = [tuple(overshoots(replica).values()) for replica in replicas]
        self.assertEqual(len(set(signatures)), 3)

    def test_lifecycle_locator_pages_exist_for_every_amb01_checkpoint(self) -> None:
        for replica_ordinal in (1, 2, 3):
            replica = generate_replica(replica=replica_ordinal)
            for lifecycle in replica.metadata["lifecycle_pages"].values():
                for page in (lifecycle["tdef_page"], lifecycle["map_page"]):
                    self.assertTrue(all(
                        page < replica.page_count[checkpoint]
                        for checkpoint in CHECKPOINT_IDS
                    ))

    def test_growth_uses_complete_batches_with_unique_monotonic_ids(self) -> None:
        for replica_ordinal in (1, 2, 3):
            replica = generate_replica(replica=replica_ordinal)
            profile = PROFILES[replica_ordinal]
            measured = overshoots(replica)
            for event in (event for event in EVENTS if event.kind is EventKind.GROW):
                checkpoint = event.checkpoint_id
                instance = event.lifecycle_instance
                self.assertIsNotNone(instance)
                lifecycle = replica.metadata["lifecycle_pages"][instance]
                captured = [
                    page
                    for page in lifecycle["data_pages"]
                    if page < replica.page_count[checkpoint]
                ]
                self.assertEqual(len(captured) % profile.pages_per_batch, 0)

                row_ids: list[int] = []
                page_row_counts: list[int] = []
                for page_number in captured:
                    digest = replica.ordered_page_sha256[checkpoint][page_number]
                    payload = replica.page_bytes(digest)
                    row_count = int.from_bytes(payload[8:10], "little")
                    page_row_counts.append(row_count)
                    starts = [
                        int.from_bytes(
                            payload[10 + 2 * ordinal : 12 + 2 * ordinal], "little"
                        )
                        & 0x0FFF
                        for ordinal in range(row_count)
                    ]
                    ends = [PAGE_SIZE, *starts[:-1]]
                    row_ids.extend(
                        int.from_bytes(payload[start:end][:4], "little", signed=True)
                        for start, end in zip(starts, ends)
                    )
                expected_page_rows = [profile.rows_per_page] * profile.pages_per_batch
                expected_page_rows[-1] = (
                    profile.batch_rows
                    - profile.rows_per_page * (profile.pages_per_batch - 1)
                )
                for start in range(0, len(page_row_counts), profile.pages_per_batch):
                    self.assertEqual(
                        page_row_counts[start : start + profile.pages_per_batch],
                        expected_page_rows,
                    )
                self.assertEqual(row_ids, list(range(1, len(row_ids) + 1)))
                self.assertEqual(
                    len(row_ids), replica.row_counts[checkpoint][event.role]
                )
                self.assertEqual(len(row_ids) % profile.batch_rows, 0)

                baseline = (
                    replica.metadata["baselines"][event.baseline_checkpoint_id]
                    if event.baseline_checkpoint_id is not None
                    else None
                )
                target = event.target_pages(baseline)
                self.assertIsNotNone(target)
                previous = EVENTS[event.ordinal - 1].checkpoint_id
                self.assertLess(replica.page_count[previous], target)
                self.assertEqual(
                    measured[checkpoint], replica.page_count[checkpoint] - target
                )

    def test_view_separates_checkpoint_reads_from_retained_blob_bytes(self) -> None:
        replica = generate_replica(replica=1)
        view = View(1, replica)
        view.page("EMPTY", 0)
        self.assertEqual(view.checkpoint_read_bytes, PAGE_SIZE)
        self.assertEqual(view.logical_read_bytes, PAGE_SIZE)
        view.page("EMPTY", 0)
        self.assertEqual(view.checkpoint_read_bytes, PAGE_SIZE)
        view.page("EMPTY_R", 0)
        self.assertEqual(view.checkpoint_read_bytes, PAGE_SIZE * 2)
        self.assertEqual(view.logical_read_bytes, PAGE_SIZE)

    def test_campaign_shares_only_content_addressed_bytes(self) -> None:
        campaign = generate_campaign()
        self.assertEqual(tuple(campaign.replicas), (1, 2, 3))
        for replica in campaign.replicas.values():
            for digest, payload in replica.payloads.items():
                self.assertEqual(campaign.payloads[digest], payload)
        fixture_text = json.dumps({
            "replicas": {
                str(replica): data.metadata["system_pages"]
                for replica, data in campaign.replicas.items()
            }
        }, default=dict)
        for forbidden in ("accepted", "valid", "reachable", "passed"):
            self.assertNotIn(f'"{forbidden}"', fixture_text)

    def test_generator_parameters_fail_closed(self) -> None:
        with self.assertRaisesRegex(Exception, "layout"):
            generate_replica(SyntheticParameters(layout_by_replica={1: "invented"}))
        with self.assertRaisesRegex(Exception, "offsets"):
            generate_replica(SyntheticParameters(locator_offsets=(35, 43)))

    def test_work_ledger_accepts_exact_limits_and_rejects_one_over(self) -> None:
        term = next(iter(WORK_TERM_LIMITS))
        ledger = WorkLedger()
        ledger.charge(term, WORK_TERM_LIMITS[term])
        self.assertEqual(ledger.value(term), WORK_TERM_LIMITS[term])
        with self.assertRaises(A4AnalysisError) as raised:
            ledger.charge(term)
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")

    def test_work_paths_are_mutually_exclusive(self) -> None:
        ledger = WorkLedger()
        ledger.charge("invalid_path_row_directory_entries")
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            ledger.charge("valid_path_row_directory_entries")


if __name__ == "__main__":
    unittest.main()
