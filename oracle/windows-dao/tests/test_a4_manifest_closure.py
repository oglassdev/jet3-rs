"""Focused closed-inventory contracts for A4 replica manifests."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_analysis_input import ReplicaAnalysisInput  # noqa: E402
from a4_campaign import check_campaign_replica  # noqa: E402
from a4_model import A4AnalysisError  # noqa: E402
from a4_spec import PLAN  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


_CAMPAIGN = "a4-synthetic"


def _canonical_surface() -> ReplicaAnalysisInput:
    original = _inputs()[1]
    manifest = copy.deepcopy(original.artifact_manifest)
    environment = next(
        entry for entry in manifest["files"] if entry["role"] == "environment"
    )
    environment["path"] = PLAN["artifacts"]["replica_environments"][0]
    return ReplicaAnalysisInput(
        original.source,
        original.table_row_counts,
        original.replica_observation,
        original.page_indexes,
        original.schema_snapshots,
        manifest,
        original.environment_payload,
    )


def _with_manifest(
    original: ReplicaAnalysisInput, manifest: dict[str, object]
) -> ReplicaAnalysisInput:
    return ReplicaAnalysisInput(
        original.source,
        original.table_row_counts,
        original.replica_observation,
        original.page_indexes,
        original.schema_snapshots,
        manifest,
        original.environment_payload,
    )


class A4ManifestClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.surface = _canonical_surface()

    def _reject(self, mutate: object) -> None:
        manifest = copy.deepcopy(self.surface.artifact_manifest)
        mutate(manifest)
        with self.assertRaises(A4AnalysisError) as raised:
            check_campaign_replica(
                1,
                _with_manifest(self.surface, manifest),
                _CAMPAIGN,
                _COMMIT,
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

    def test_exact_hosted_inventory_is_accepted(self) -> None:
        checked = check_campaign_replica(
            1, self.surface, _CAMPAIGN, _COMMIT
        )
        self.assertEqual(checked.view.replica, 1)

    def test_missing_or_extra_inventory_is_rejected(self) -> None:
        environment_path = PLAN["artifacts"]["replica_environments"][0]
        observation_path = PLAN["artifacts"]["replica_observations"][0]

        def remove(path: str):
            return lambda manifest: manifest["files"].__setitem__(
                slice(None),
                [entry for entry in manifest["files"] if entry["path"] != path],
            )

        for name, mutate in (
            ("environment", remove(environment_path)),
            ("observation", remove(observation_path)),
            (
                "acquisition_log",
                lambda manifest: manifest["files"].append(
                    {
                        "path": "logs/acquisition.txt",
                        "role": "acquisition_log",
                        "sha256": "f" * 64,
                        "size_bytes": 1,
                        "media_type": "text/plain",
                    }
                ),
            ),
        ):
            with self.subTest(name=name):
                self._reject(mutate)

    def test_wrong_roles_hashes_sizes_and_paths_are_rejected(self) -> None:
        environment_path = PLAN["artifacts"]["replica_environments"][0]
        observation_path = PLAN["artifacts"]["replica_observations"][0]

        def change(path: str, key: str, value: object):
            def mutate(manifest: dict[str, object]) -> None:
                entry = next(
                    row for row in manifest["files"] if row["path"] == path
                )
                entry[key] = value

            return mutate

        for name, mutate in (
            ("environment_role", change(environment_path, "role", "acquisition_log")),
            ("environment_hash", change(environment_path, "sha256", "f" * 64)),
            ("environment_size", change(environment_path, "size_bytes", 0)),
            ("environment_path", change(environment_path, "path", "environment.json")),
            ("observation_role", change(observation_path, "role", "acquisition_log")),
            ("observation_hash", change(observation_path, "sha256", "f" * 64)),
            ("observation_size", change(observation_path, "size_bytes", 1)),
            ("observation_path", change(observation_path, "path", "observations/fake.json")),
        ):
            with self.subTest(name=name):
                self._reject(mutate)

    def test_manifest_matrix_job_must_match_observation(self) -> None:
        self._reject(
            lambda manifest: manifest.__setitem__("matrix_job_id", "another-job")
        )

    def test_reconstruction_inventory_waits_for_schema_predicate(self) -> None:
        manifest = copy.deepcopy(self.surface.artifact_manifest)
        manifest["files"].remove(
            next(entry for entry in manifest["files"] if entry["role"] == "page_blob")
        )
        snapshots = copy.deepcopy(self.surface.schema_snapshots)
        snapshots["T1_CREATE_ID"]["ordinal"] = 4
        invalid_schema = ReplicaAnalysisInput(
            self.surface.source,
            self.surface.table_row_counts,
            self.surface.replica_observation,
            self.surface.page_indexes,
            snapshots,
            manifest,
            self.surface.environment_payload,
        )
        for surface, expected in (
            (invalid_schema, "A4-SCHEMA-SNAPSHOT"),
            (_with_manifest(self.surface, manifest), "A4-SNAPSHOT-RECONSTRUCTION"),
        ):
            with self.subTest(expected=expected), self.assertRaises(
                A4AnalysisError
            ) as raised:
                check_campaign_replica(1, surface, _CAMPAIGN, _COMMIT)
            self.assertEqual(raised.exception.predicate_id, expected)


if __name__ == "__main__":
    unittest.main()
