"""Focused campaign-predicate and transcript tests for the A4 validator."""

from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_independent_bundle import (  # noqa: E402
    BundleLoader,
    ValidationError,
    canonical_document_bytes,
)
from a4_independent_campaign import (  # noqa: E402
    recompute_campaign,
    require_campaign,
    verify_frozen_transcripts,
)
from a4_independent_contract import CONTRACT  # noqa: E402
import a4_layers  # noqa: E402
import test_a4_independent_bundle as fixture  # noqa: E402
from a4_layer_h3 import SlotObservation  # noqa: E402
from a4_layer_h4 import (  # noqa: E402
    CatalogRecordLocator,
    CheckpointRecordEvidence,
    OperationRecord,
)
from test_a4_independent_bundle import _build_bundle  # noqa: E402


class IndependentCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a4-independent-campaign-")
        cls.root = Path(cls.temporary.name)
        _build_bundle(cls.root)
        cls.bundle = BundleLoader(cls.root).load()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_normal_bundle_passes_campaign_and_exact_transcripts(self) -> None:
        result = require_campaign(self.bundle)
        self.assertTrue(result.passed)
        self.assertIsNone(result.first_failure)
        self.assertEqual(
            [row["predicate_id"] for row in result.predicate_rows],
            list(CONTRACT.campaign_predicates),
        )
        self.assertEqual([row["status"] for row in result.predicate_rows], ["pass"] * 4)
        self.assertEqual(
            result.resources["retained_page_store_bytes"],
            result.resources["unique_page_blobs"] * 2048,
        )
        rebuilt = verify_frozen_transcripts(self.bundle)
        self.assertEqual(
            {name: list(rows) for name, rows in rebuilt.items()},
            self.bundle.frozen["transcripts"],
        )

    def test_initial_index_has_no_changed_pages(self) -> None:
        for replica in self.bundle.replicas.values():
            self.assertEqual(replica.index("EMPTY")["changed_page_indices"], [])
        result = recompute_campaign(self.bundle)
        self.assertEqual(result.predicate_rows[2]["status"], "pass")

    def test_idle_difference_is_first_terminal_and_later_rows_are_na(self) -> None:
        replica = self.bundle.replicas[1]
        indexes = dict(replica.indexes)
        changed = dict(indexes["EMPTY_R"])
        hashes = list(changed["ordered_page_sha256"])
        hashes[0] = next(digest for digest in self.bundle.page_store.paths if digest != hashes[0])
        changed["ordered_page_sha256"] = hashes
        indexes["EMPTY_R"] = changed
        replicas = dict(self.bundle.replicas)
        replicas[1] = replace(replica, indexes=indexes)
        bundle = replace(self.bundle, replicas=replicas)

        result = recompute_campaign(bundle)
        self.assertEqual(result.first_failure.predicate_id, "A4-IDLE-EQUALITY")
        self.assertEqual(
            [row["status"] for row in result.predicate_rows],
            ["fail", "not_applicable", "not_applicable", "not_applicable"],
        )
        with self.assertRaises(ValidationError) as raised:
            require_campaign(bundle)
        self.assertEqual(raised.exception.code, "A4-IDLE-EQUALITY")

    def test_semantic_snapshot_mutation_with_coherent_entry_is_schema_terminal(self) -> None:
        checkpoint = "T1_CREATE_ID"
        ordinal = CONTRACT.checkpoint_ids.index(checkpoint)
        relative = f"schema-snapshots/replica-01/{ordinal:02d}-{checkpoint}.json"
        original_bytes = (self.root / relative).read_bytes()
        replica = self.bundle.replicas[1]
        snapshot = copy.deepcopy(replica.snapshots[checkpoint])
        snapshot["tables"][0]["attributes"] += 1
        encoded = canonical_document_bytes(snapshot)
        digest = hashlib.sha256(encoded).hexdigest()
        entries = dict(self.bundle.entries)
        entries[relative] = {
            **entries[relative], "sha256": digest, "size_bytes": len(encoded)
        }
        snapshots = dict(replica.snapshots)
        snapshots[checkpoint] = snapshot
        observation = copy.deepcopy(replica.observation)
        reference = observation["checkpoints"][ordinal]["dao_schema_snapshot"]
        reference.update({"sha256": digest, "size_bytes": len(encoded)})
        replicas = dict(self.bundle.replicas)
        replicas[1] = replace(replica, snapshots=snapshots, observation=observation)
        bundle = replace(self.bundle, entries=entries, replicas=replicas)
        try:
            (self.root / relative).write_bytes(encoded)
            result = recompute_campaign(bundle)
        finally:
            (self.root / relative).write_bytes(original_bytes)
        self.assertEqual(result.first_failure.predicate_id, "A4-SCHEMA-SNAPSHOT")
        self.assertEqual(
            [row["status"] for row in result.predicate_rows],
            ["pass", "fail", "not_applicable", "not_applicable"],
        )

    def test_corrupt_reconstructed_page_is_reconstruction_terminal(self) -> None:
        idle_digests = {
            digest
            for replica in self.bundle.replicas.values()
            for pair in CONTRACT.plan["checkpoint_design"]["idle_pairs"]
            for checkpoint in pair
            for digest in replica.index(checkpoint)["ordered_page_sha256"]
        }
        target = next(
            digest
            for replica in self.bundle.replicas.values()
            for checkpoint in CONTRACT.checkpoint_ids
            for digest in replica.index(checkpoint)["ordered_page_sha256"]
            if digest not in idle_digests
        )
        original = self.bundle.page_store._cache[target]
        try:
            self.bundle.page_store._cache[target] = b"\xff" * 2048
            result = recompute_campaign(self.bundle)
        finally:
            self.bundle.page_store._cache[target] = original
        self.assertEqual(
            result.first_failure.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION"
        )
        self.assertEqual(
            [row["status"] for row in result.predicate_rows],
            ["pass", "pass", "fail", "not_applicable"],
        )

    def test_claimed_resource_counter_difference_is_resource_terminal(self) -> None:
        replica = self.bundle.replicas[1]
        observation = dict(replica.observation)
        observation["changed_hash_entries"] += 1
        replicas = dict(self.bundle.replicas)
        replicas[1] = replace(replica, observation=observation)
        result = recompute_campaign(replace(self.bundle, replicas=replicas))
        self.assertEqual(result.first_failure.predicate_id, "A4-RESOURCE-BOUND")
        self.assertEqual(
            [row["status"] for row in result.predicate_rows],
            ["pass", "pass", "pass", "fail"],
        )

    def test_schema_valid_transcript_payload_and_order_tampering_are_rejected(self) -> None:
        frozen = copy.deepcopy(self.bundle.frozen)
        row = frozen["transcripts"]["locators"][0]
        row["detail_hex"] = "00" * (len(row["detail_hex"]) // 2)
        bundle = replace(self.bundle, frozen=frozen)
        with self.assertRaises(ValidationError) as raised:
            verify_frozen_transcripts(bundle)
        self.assertEqual(raised.exception.code, "frozen_set_recomputation_mismatch")

        frozen = copy.deepcopy(self.bundle.frozen)
        rows = frozen["transcripts"]["catalog_fields"]
        rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaises(ValidationError) as raised:
            verify_frozen_transcripts(replace(self.bundle, frozen=frozen))
        self.assertEqual(raised.exception.code, "frozen_set_recomputation_mismatch")

    def _assert_generated_terminal(self, patcher: object, expected: str) -> None:
        with tempfile.TemporaryDirectory(prefix="a4-campaign-terminal-") as temporary:
            root = Path(temporary)
            with patcher:
                fixture._build_bundle(root)
            bundle = BundleLoader(root).load()
            terminals = {
                result.get("terminal_predicate_id")
                for layer in bundle.frozen["layers"].values()
                for result in (
                    layer.values()
                    if "root_result" in layer
                    else (layer,)
                )
            }
            self.assertIn(expected, terminals)
            verify_frozen_transcripts(bundle)

    def test_early_h3_terminals_rebuild_exact_reached_transcripts(self) -> None:
        original = a4_layers.h3_observations

        def conversion_none(view: object, rows: object, work: object) -> object:
            return original(view, rows, work)[:1]

        self._assert_generated_terminal(
            mock.patch.object(
                a4_layers, "h3_observations", side_effect=conversion_none
            ),
            "A4-H3-CONVERSION-NONE",
        )

        def inactive_none(view: object, rows: object, work: object) -> object:
            observations = original(view, rows, work)
            active = next(
                slot
                for observation in observations
                for slot in observation.slots
                if slot.reference
            )
            return tuple(
                replace(
                    observation,
                    slots=tuple(
                        slot
                        if slot.reference
                        else SlotObservation(
                            slot.slot_ordinal,
                            active.reference,
                            active.referenced_page_tag,
                            active.set_bits,
                        )
                        for slot in observation.slots
                    ),
                )
                for observation in observations
            )

        self._assert_generated_terminal(
            mock.patch.object(
                a4_layers, "h3_observations", side_effect=inactive_none
            ),
            "A4-H3-INACTIVE-SLOT-NONE",
        )

    def test_early_h4_record_terminals_rebuild_exact_catalog_markers(self) -> None:
        original = a4_layers.operation_records
        self._assert_generated_terminal(
            mock.patch.object(a4_layers, "operation_records", return_value=()),
            "A4-H4-CATALOG-RECORD-NONE",
        )

        def multiple(*args: object, **kwargs: object) -> object:
            records = original(*args, **kwargs)
            base = records[0]
            evidence = tuple(
                CheckpointRecordEvidence(
                    row.checkpoint_id,
                    CatalogRecordLocator(
                        row.locator.page,
                        row.locator.row + 1,
                        row.locator.row_start,
                        row.locator.row_end,
                    ),
                    row.row_bytes,
                )
                for row in base.checkpoint_evidence
            )
            duplicate = OperationRecord(
                base.replica, base.operation_id, evidence
            )
            return tuple(records) + (duplicate,)

        self._assert_generated_terminal(
            mock.patch.object(
                a4_layers, "operation_records", side_effect=multiple
            ),
            "A4-H4-CATALOG-RECORD-MULTIPLE",
        )


if __name__ == "__main__":
    unittest.main()
