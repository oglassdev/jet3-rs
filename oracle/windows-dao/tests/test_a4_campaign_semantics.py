"""Adversarial A4 campaign schedule and environment contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import a4_campaign as campaign_module  # noqa: E402
from a4_analysis_input import ReplicaAnalysisInput, check_analysis_input  # noqa: E402
from a4_campaign import check_campaign_replica  # noqa: E402
from a4_generator import generate_replica  # noqa: E402
from a4_model import A4AnalysisError, View  # noqa: E402
from a4_spec import BOUNDS  # noqa: E402
from protocol_validation import canonical_json_bytes  # noqa: E402
import test_a4_analyzer as fixtures  # noqa: E402


def _with_observation(
    original: ReplicaAnalysisInput, observation: dict[str, object]
) -> ReplicaAnalysisInput:
    manifest = copy.deepcopy(original.artifact_manifest)
    payload = canonical_json_bytes(observation)
    entry = next(
        row
        for row in manifest["files"]
        if row["role"] == "replica_observation"
    )
    entry["sha256"] = hashlib.sha256(payload).hexdigest()
    entry["size_bytes"] = len(payload)
    return ReplicaAnalysisInput(
        original.source,
        original.table_row_counts,
        observation,
        original.page_indexes,
        original.schema_snapshots,
        manifest,
        original.environment_payload,
    )


def _with_snapshot(
    original: ReplicaAnalysisInput,
    checkpoint: str,
    snapshots: dict[str, dict[str, object]],
) -> ReplicaAnalysisInput:
    observation = copy.deepcopy(original.replica_observation)
    manifest = copy.deepcopy(original.artifact_manifest)
    ordinal = fixtures.CHECKPOINT_IDS.index(checkpoint)
    payload = canonical_json_bytes(snapshots[checkpoint])
    reference = observation["checkpoints"][ordinal]["dao_schema_snapshot"]
    reference["sha256"] = hashlib.sha256(payload).hexdigest()
    reference["size_bytes"] = len(payload)
    entry = next(row for row in manifest["files"] if row["path"] == reference["path"])
    entry.update(reference)
    observation_payload = canonical_json_bytes(observation)
    observation_entry = next(
        row
        for row in manifest["files"]
        if row["role"] == "replica_observation"
    )
    observation_entry["sha256"] = hashlib.sha256(observation_payload).hexdigest()
    observation_entry["size_bytes"] = len(observation_payload)
    return ReplicaAnalysisInput(
        original.source,
        original.table_row_counts,
        observation,
        original.page_indexes,
        snapshots,
        manifest,
        original.environment_payload,
    )


class A4CampaignSemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = fixtures._inputs()

    def test_growth_inventory_and_arithmetic_are_required(self) -> None:
        original = self.inputs[1]
        observation = copy.deepcopy(original.replica_observation)
        observation["growth_observations"] = []
        with self.assertRaises(A4AnalysisError) as raised:
            check_campaign_replica(
                1,
                _with_observation(original, observation),
                "a4-synthetic",
                fixtures._COMMIT,
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

        observation = copy.deepcopy(original.replica_observation)
        growth = observation["growth_observations"][0]
        growth["overshoot_pages"] += 1
        with self.assertRaises(A4AnalysisError):
            check_campaign_replica(
                1,
                _with_observation(original, observation),
                "a4-synthetic",
                fixtures._COMMIT,
            )

    def test_environment_bytes_and_cross_replica_identity_are_checked(self) -> None:
        original = self.inputs[1]
        broken = ReplicaAnalysisInput(
            original.source,
            original.table_row_counts,
            original.replica_observation,
            original.page_indexes,
            original.schema_snapshots,
            original.artifact_manifest,
            original.environment_payload[:-1] + b" ",
        )
        with self.assertRaises(A4AnalysisError):
            check_campaign_replica(
                1, broken, "a4-synthetic", fixtures._COMMIT
            )

        original_environment = fixtures._environment

        def changed_environment(replica: int, campaign_id: str) -> bytes:
            payload = original_environment(replica, campaign_id)
            document = json.loads(payload)
            document["provider"]["clsid"] = (
                "{00000101-0000-0010-8000-00AA006D2EA4}"
            )
            return canonical_json_bytes(document)

        with mock.patch.object(fixtures, "_environment", changed_environment):
            mismatched = fixtures._surface(
                generate_replica(replica=2), "a4-synthetic"
            )
        derivation = {1: self.inputs[1], 2: mismatched}
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", fixtures._COMMIT, derivation
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

        def duplicate_job_environment(replica: int, campaign_id: str) -> bytes:
            payload = original_environment(replica, campaign_id)
            document = json.loads(payload)
            document["matrix_job_id"] = "synthetic-job-1"
            return canonical_json_bytes(document)

        with mock.patch.object(
            fixtures, "_environment", duplicate_job_environment
        ):
            duplicate_job = fixtures._surface(
                generate_replica(replica=2), "a4-synthetic"
            )
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic",
                fixtures._COMMIT,
                {1: self.inputs[1], 2: duplicate_job},
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

    def test_environment_size_is_rejected_before_json_parsing(self) -> None:
        original = self.inputs[1]
        reduced = {**BOUNDS, "max_json_bytes": len(original.environment_payload) - 1}
        json_mock = mock.Mock(wraps=json)
        with (
            mock.patch.object(campaign_module, "BOUNDS", reduced),
            mock.patch.object(campaign_module, "json", json_mock),
            self.assertRaises(A4AnalysisError) as raised,
        ):
            check_campaign_replica(
                1, original, "a4-synthetic", fixtures._COMMIT
            )
        json_mock.loads.assert_not_called()
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

    def test_idle_preflight_does_not_materialize_an_invalid_hash_sequence(self) -> None:
        original = self.inputs[1]
        page_calls: list[str] = []

        class TrapSequence:
            iterated = False

            def __len__(self) -> int:
                return int(BOUNDS["max_final_pages_per_replica"]) + 1

            def __iter__(self):
                self.iterated = True
                raise AssertionError("invalid page hashes were materialized")

        class Source:
            checkpoint_ids = original.source.checkpoint_ids
            page_count = {**original.source.page_count, "EMPTY": 1}
            trap = TrapSequence()
            ordered_page_sha256 = {
                **original.source.ordered_page_sha256,
                "EMPTY": trap,
            }
            def page_bytes(self, sha256: str) -> bytes:
                page_calls.append(sha256)
                return original.source.page_bytes(sha256)

        broken = ReplicaAnalysisInput(
            Source(),
            original.table_row_counts,
            original.replica_observation,
            original.page_indexes,
            original.schema_snapshots,
            original.artifact_manifest,
            original.environment_payload,
        )
        with self.assertRaises(A4AnalysisError) as raised:
            check_campaign_replica(
                1, broken, "a4-synthetic", fixtures._COMMIT
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION")
        self.assertFalse(Source.trap.iterated)
        self.assertEqual(page_calls, [])

    def test_view_boundedly_rejects_dishonest_and_infinite_hash_sequences(self) -> None:
        original = self.inputs[1]
        digest = original.source.ordered_page_sha256["EMPTY"][0]

        class DishonestSequence:
            def __len__(self) -> int:
                return 1

            def __iter__(self):
                yield digest
                yield digest

        class InfiniteSequence:
            yielded = 0

            def __len__(self) -> int:
                return 1

            def __iter__(self):
                while True:
                    self.yielded += 1
                    yield digest

        infinite = InfiniteSequence()
        for sequence in (DishonestSequence(), infinite):
            source = mock.Mock(wraps=original.source)
            source.checkpoint_ids = original.source.checkpoint_ids
            source.page_count = {**original.source.page_count, "EMPTY": 1}
            source.ordered_page_sha256 = {
                **original.source.ordered_page_sha256,
                "EMPTY": sequence,
            }
            with self.assertRaises(A4AnalysisError) as raised:
                View(1, source)
            self.assertEqual(
                raised.exception.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION"
            )
        self.assertEqual(
            infinite.yielded,
            int(BOUNDS["max_final_pages_per_replica"]) + 1,
        )

    def test_idle_resource_and_programming_errors_are_not_reclassified(self) -> None:
        original = self.inputs[1]
        resource = A4AnalysisError("A4-RESOURCE-BOUND", detail="test bound")
        with (
            mock.patch.object(campaign_module.View, "page", side_effect=resource),
            self.assertRaises(A4AnalysisError) as raised,
        ):
            check_campaign_replica(
                1, original, "a4-synthetic", fixtures._COMMIT
            )
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")
        with (
            mock.patch.object(
                campaign_module.View, "page", side_effect=RuntimeError("test bug")
            ),
            self.assertRaisesRegex(RuntimeError, "test bug"),
        ):
            check_campaign_replica(
                1, original, "a4-synthetic", fixtures._COMMIT
            )

    def test_index_attributes_cannot_self_authorize(self) -> None:
        original = self.inputs[1]
        snapshots = copy.deepcopy(original.schema_snapshots)
        snapshots["T1_ADD_INDEX"]["tables"][0]["indexes"][0][
            "attributes"
        ] = 123456
        changed = _with_snapshot(original, "T1_ADD_INDEX", snapshots)
        with self.assertRaises(A4AnalysisError) as raised:
            check_campaign_replica(
                1, changed, "a4-synthetic", fixtures._COMMIT
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

    def test_growth_cannot_mutate_an_unrelated_role(self) -> None:
        original = self.inputs[1]
        observation = copy.deepcopy(original.replica_observation)
        growth = next(
            row
            for row in observation["checkpoints"]
            if row["checkpoint_id"] == "T1_REL_0064"
        )
        growth["table_row_counts"]["T4"] = 32
        with self.assertRaises(A4AnalysisError) as raised:
            check_campaign_replica(
                1,
                _with_observation(original, observation),
                "a4-synthetic",
                fixtures._COMMIT,
            )
        self.assertIn("unrelated rows differ", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
