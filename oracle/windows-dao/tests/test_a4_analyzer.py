"""A4 derivation, freeze boundary, holdout, and schema contracts."""

from __future__ import annotations

import hashlib
import json
import copy
import sys
import unittest
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

from a4_analysis import analyze  # noqa: E402
import a4_derivation  # noqa: E402
import a4_analysis_input as input_module  # noqa: E402
import a4_layer_h4  # noqa: E402
from a4_analysis_input import (  # noqa: E402
    HoldoutTicket,
    ReplicaAnalysisInput,
    check_analysis_input,
    close_derivation,
    open_holdout,
)
from a4_analysis_state import freeze_derivation, resume_derivation  # noqa: E402
from a4_campaign import (  # noqa: E402
    CampaignResourceTotals,
    changed_page_indices,
    expected_snapshot_tables,
    require_resource_bounds,
)
from a4_generator import SyntheticParameters, generate_replica  # noqa: E402
from a4_generator_pages import data_page  # noqa: E402
from a4_layers import derive_layers  # noqa: E402
from a4_model import A4AnalysisError, WorkLedger  # noqa: E402
from a4_terminal import DerivationTerminal  # noqa: E402
from a4_spec import (  # noqa: E402
    CHECKPOINT_IDS,
    EXPERIMENT_ID,
    PAGE_SIZE,
    PLAN,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    ROLE_BINDINGS,
    canonical_json_bytes as canonical_model_bytes,
    validate_schema,
)
from protocol_validation import canonical_json_bytes  # noqa: E402

_COMMIT = "0" * 40
_PROVIDER = "2" * 64
_PROVIDER_CLSID = "{00000100-0000-0010-8000-00AA006D2EA4}"
_CANONICALIZATION = json.loads(
    (ROOT / "oracle/windows-dao/experiments/a4/dao-schema-snapshot.schema.json").read_text()
)["properties"]["canonicalization"]["const"]


def _ref(document: dict[str, object], path: str) -> dict[str, object]:
    payload = canonical_json_bytes(document)
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _common(
    replica: int, campaign_id: str, environment_sha256: str
) -> dict[str, object]:
    return {
        "protocol_version": "1.0.0",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": _COMMIT,
        "campaign_id": campaign_id,
        "environment_sha256": environment_sha256,
        "provider_sha256": _PROVIDER,
        "replica": replica,
    }


def _environment(replica: int, campaign_id: str) -> bytes:
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_environment",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": _COMMIT,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "campaign_id": campaign_id,
        "replica": replica,
        "matrix_job_id": f"synthetic-replica-{replica}",
        "status": "ready",
        "host": {
            "windows_version": f"10.0.20348.{replica}",
            "process_architecture": "x86",
            "powershell_version": "5.1.20348.1",
            "python_version": f"3.13.{replica}",
            "runner_image": "windows-2022",
            "windows_ansi_code_page": 1252,
            "windows_oem_code_page": 437,
            "locale_name": "en-US",
        },
        "provider": {
            "prog_id": "DAO.DBEngine.36",
            "clsid": _PROVIDER_CLSID,
            "provider_version": "3.60",
            "server_path": "C:/Program Files (x86)/Common Files/System/dao/dao360.dll",
            "server_file_version": "3.60.8618.0",
            "server_sha256": _PROVIDER,
        },
    }
    return canonical_json_bytes(document)


def _manifest(
    source: object,
    environment_payload: bytes,
    observation: dict[str, object],
    indexes: dict[str, dict[str, object]],
    snapshots: dict[str, dict[str, object]],
    campaign_id: str,
) -> dict[str, object]:
    replica = source.replica
    files = [
        {
            "path": PLAN["artifacts"]["replica_environments"][replica - 1],
            "role": "environment",
            "sha256": hashlib.sha256(environment_payload).hexdigest(),
            "size_bytes": len(environment_payload),
            "media_type": "application/json",
        },
        {
            **_ref(observation, f"observations/replica-{replica:02d}.json"),
            "role": "replica_observation",
            "media_type": "application/json",
        },
    ]
    for checkpoint in CHECKPOINT_IDS:
        files.extend(
            (
                {
                    **observation["checkpoints"][CHECKPOINT_IDS.index(checkpoint)]["page_index"],
                    "role": "page_index",
                    "media_type": "application/json",
                },
                {
                    **observation["checkpoints"][CHECKPOINT_IDS.index(checkpoint)]["dao_schema_snapshot"],
                    "role": "dao_schema_snapshot",
                    "media_type": "application/json",
                },
            )
        )
    for digest in sorted(
        {
            value
            for checkpoint in CHECKPOINT_IDS
            for value in source.ordered_page_sha256[checkpoint]
        }
    ):
        files.append(
            {
                "path": f"page-store/{digest}.page",
                "role": "page_blob",
                "sha256": digest,
                "size_bytes": PAGE_SIZE,
                "media_type": "application/octet-stream",
            }
        )
    return {
        **_common(
            replica,
            campaign_id,
            hashlib.sha256(environment_payload).hexdigest(),
        ),
        "document_type": "dao_a4_replica_artifact_manifest",
        "matrix_job_id": f"synthetic-replica-{replica}",
        "checkpoint_count": len(CHECKPOINT_IDS),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "files": files,
    }


def _surface(source: object, campaign_id: str) -> ReplicaAnalysisInput:
    replica = source.replica
    environment_payload = _environment(replica, campaign_id)
    common = _common(
        replica,
        campaign_id,
        hashlib.sha256(environment_payload).hexdigest(),
    )
    indexes: dict[str, dict[str, object]] = {}
    snapshots: dict[str, dict[str, object]] = {}
    predecessor: tuple[str, ...] | None = None
    running_inserted = 0
    previous_rows = {role: 0 for role in PLAN["tables"]["logical_roles"]}
    checkpoint_rows = []
    growth_observations = []
    changed_total = 0
    logical_reads = 0
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        sequence = tuple(source.ordered_page_sha256[checkpoint])
        database = hashlib.sha256()
        for digest in sequence:
            database.update(source.page_bytes(digest))
        database_sha256 = database.hexdigest()
        changed = changed_page_indices(predecessor, sequence)
        changed_total += len(changed)
        logical_reads += len(sequence) * PAGE_SIZE
        index = {
            **common,
            "document_type": "dao_a4_page_index",
            "checkpoint_id": checkpoint,
            "ordinal": ordinal,
            "predecessor_checkpoint_id": None if ordinal == 0 else CHECKPOINT_IDS[ordinal - 1],
            "page_count": len(sequence),
            "file_size_bytes": len(sequence) * PAGE_SIZE,
            "database_sha256": database_sha256,
            "ordered_page_sha256": list(sequence),
            "changed_page_indices": list(changed),
        }
        indexes[checkpoint] = index
        rows = dict(source.row_counts[checkpoint])
        running_inserted += sum(
            max(0, rows[role] - previous_rows[role]) for role in previous_rows
        )
        tables = expected_snapshot_tables(replica, checkpoint, rows)
        snapshot = {
            **common,
            "document_type": "dao_a4_schema_snapshot",
            "checkpoint_id": checkpoint,
            "ordinal": ordinal,
            "windows_ansi_code_page": 1252,
            "database_sha256_before_read": database_sha256,
            "database_sha256_after_read": database_sha256,
            "database_unchanged_by_read": True,
            "dao_identifier_observable": False,
            "identity_oracle": "listed_operation_instance_equality_only",
            "canonicalization": _CANONICALIZATION,
            "tables": tables,
        }
        snapshots[checkpoint] = snapshot
        checkpoint_row = {
                "checkpoint_id": checkpoint,
                "ordinal": ordinal,
                "actual_file_pages": len(sequence),
                "actual_size_bytes": len(sequence) * PAGE_SIZE,
                "target_baseline_pages": None,
                "target_threshold_pages": None,
                "target_overshoot_pages": None,
                "inserted_rows_total": running_inserted,
                "table_row_counts": rows,
                "dao_reread": [
                    {
                        "role": table["logical_role"],
                        "row_count": table["row_count"],
                        "rolling_sha256": table["rolling_row_sha256"],
                    }
                    for table in tables
                ],
                "quiescent": True,
                "post_close_companion": {
                    "present_after_close": False,
                    "observed_size_bytes": 0,
                    "retained_for_physical_analysis": False,
                },
                "page_index": _ref(index, f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"),
                "dao_schema_snapshot": _ref(snapshot, f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json"),
        }
        if "_REL_" in checkpoint or "_ABS_" in checkpoint:
            role = checkpoint.split("_", 1)[0]
            suffix = int(checkpoint.rsplit("_", 1)[1])
            if role == "T1":
                baseline = len(source.ordered_page_sha256["T4_CREATE"])
            elif role == "T4":
                baseline = len(source.ordered_page_sha256["T3_ABS_16480"])
            else:
                baseline = None
            target = suffix if baseline is None else baseline + suffix
            overshoot = len(sequence) - target
            inserted = rows[role] - previous_rows[role]
            checkpoint_row.update(
                target_baseline_pages=baseline,
                target_threshold_pages=target,
                target_overshoot_pages=overshoot,
            )
            growth_observations.append(
                {
                    "checkpoint_id": checkpoint,
                    "baseline_pages": baseline,
                    "target_pages": target,
                    "achieved_pages": len(sequence),
                    "overshoot_pages": overshoot,
                    "rows": inserted,
                }
            )
        checkpoint_rows.append(checkpoint_row)
        previous_rows = rows
        predecessor = sequence
    observation = {
        **common,
        "document_type": "dao_a4_replica_observation",
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "matrix_job": {
            "job_id": f"synthetic-replica-{replica}",
            "replica_only": True,
            "shared_mutable_state": False,
        },
        "role_binding": dict(ROLE_BINDINGS[replica]),
        "growth_observations": growth_observations,
        "logical_checkpoint_read_bytes": logical_reads,
        "inserted_rows_total": running_inserted,
        "changed_hash_entries": changed_total,
        "checkpoints": checkpoint_rows,
    }
    manifest = _manifest(
        source,
        environment_payload,
        observation,
        indexes,
        snapshots,
        campaign_id,
    )
    return ReplicaAnalysisInput(
        source,
        source.row_counts,
        observation,
        indexes,
        snapshots,
        manifest,
        environment_payload,
    )


class _LazyInputs(Mapping[int, ReplicaAnalysisInput]):
    def __init__(self, parameters: SyntheticParameters | None = None) -> None:
        self.parameters = parameters or SyntheticParameters()
        self.derivation = {
            replica: _surface(
                generate_replica(self.parameters, replica), "a4-synthetic"
            )
            for replica in (1, 2)
        }
        self.holdout_calls = 0
        self.last_holdout_sha256: str | None = None

    def __getitem__(self, key: int) -> ReplicaAnalysisInput:
        return self.derivation[key]

    def __iter__(self) -> Iterator[int]:
        return iter(self.derivation)

    def __len__(self) -> int:
        return len(self.derivation)

    def acquire_holdout(
        self, frozen_payload: bytes, frozen_sha256: str
    ) -> ReplicaAnalysisInput:
        self.assert_frozen(frozen_payload, frozen_sha256)
        self.holdout_calls += 1
        self.last_holdout_sha256 = frozen_sha256
        return _surface(
            generate_replica(self.parameters, 3), "a4-synthetic"
        )

    @staticmethod
    def assert_frozen(frozen_payload: bytes, frozen_sha256: str) -> None:
        if hashlib.sha256(frozen_payload).hexdigest() != frozen_sha256:
            raise AssertionError("holdout provider received unfrozen bytes")


def _inputs(parameters: SyntheticParameters | None = None) -> _LazyInputs:
    return _LazyInputs(parameters)


def _close_after_freeze(checked, provider):
    ledger = WorkLedger()
    layers = derive_layers(checked, ledger)
    frozen = freeze_derivation(checked, layers, ledger)
    return (
        close_derivation(
            checked,
            frozen.canonical_bytes,
            frozen.sha256,
            provider,
            frozen.occurrence_evidence_bytes,
        ),
        frozen,
    )


class A4CampaignInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = _inputs()

    def _changed(
        self,
        replica: int,
        *,
        observation: dict[str, object] | None = None,
        indexes: dict[str, dict[str, object]] | None = None,
        snapshots: dict[str, dict[str, object]] | None = None,
        manifest: dict[str, object] | None = None,
        environment_payload: bytes | None = None,
    ) -> dict[int, ReplicaAnalysisInput]:
        inputs = dict(self.inputs)
        original = inputs[replica]
        inputs[replica] = ReplicaAnalysisInput(
            original.source,
            original.table_row_counts,
            original.replica_observation if observation is None else observation,
            original.page_indexes if indexes is None else indexes,
            original.schema_snapshots if snapshots is None else snapshots,
            original.artifact_manifest if manifest is None else manifest,
            (
                original.environment_payload
                if environment_payload is None
                else environment_payload
            ),
        )
        return inputs

    def _changed_entry_adversary(self) -> ReplicaAnalysisInput:
        original = self.inputs[1]
        observation = copy.deepcopy(original.replica_observation)
        indexes = copy.deepcopy(original.page_indexes)
        snapshots = copy.deepcopy(original.schema_snapshots)
        payloads = (bytes(PAGE_SIZE), bytes([1]) * PAGE_SIZE)
        digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
        blobs = dict(zip(digests, payloads))
        ordered: dict[str, tuple[str, ...]] = {}
        selected = 0
        idle_right = {right for _left, right in PLAN["checkpoint_design"]["idle_pairs"]}
        predecessor: tuple[str, ...] | None = None
        changed_total = 0
        for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
            if ordinal and checkpoint not in idle_right:
                selected ^= 1
            page_count = original.source.page_count[checkpoint]
            sequence = (digests[selected],) * page_count
            ordered[checkpoint] = sequence
            changed = changed_page_indices(predecessor, sequence)
            changed_total += len(changed)
            database_sha256 = hashlib.sha256(
                payloads[selected] * page_count
            ).hexdigest()
            index = indexes[checkpoint]
            index.update(
                page_count=page_count,
                file_size_bytes=page_count * PAGE_SIZE,
                database_sha256=database_sha256,
                ordered_page_sha256=list(sequence),
                changed_page_indices=list(changed),
            )
            snapshot = snapshots[checkpoint]
            snapshot["database_sha256_before_read"] = database_sha256
            snapshot["database_sha256_after_read"] = database_sha256
            checkpoint_row = observation["checkpoints"][ordinal]
            checkpoint_row["actual_file_pages"] = page_count
            checkpoint_row["actual_size_bytes"] = page_count * PAGE_SIZE
            checkpoint_row["page_index"] = _ref(
                index,
                f"page-indexes/replica-01/{ordinal:02d}-{checkpoint}.json",
            )
            checkpoint_row["dao_schema_snapshot"] = _ref(
                snapshot,
                f"schema-snapshots/replica-01/{ordinal:02d}-{checkpoint}.json",
            )
            predecessor = sequence
        self.assertGreater(
            changed_total,
            PLAN["bounds"]["max_changed_hash_entries_per_replica"],
        )
        observation["changed_hash_entries"] = int(
            PLAN["bounds"]["max_changed_hash_entries_per_replica"]
        )
        observation["logical_checkpoint_read_bytes"] = (
            sum(len(sequence) for sequence in ordered.values()) * PAGE_SIZE
        )

        class Source:
            checkpoint_ids = CHECKPOINT_IDS
            page_count = {
                checkpoint: len(sequence)
                for checkpoint, sequence in ordered.items()
            }
            ordered_page_sha256 = ordered

            @staticmethod
            def page_bytes(digest: str) -> bytes:
                return blobs[digest]

        source = Source()
        source.replica = 1
        manifest = _manifest(
            source,
            original.environment_payload,
            observation,
            indexes,
            snapshots,
            "a4-synthetic",
        )
        return ReplicaAnalysisInput(
            source,
            original.table_row_counts,
            observation,
            indexes,
            snapshots,
            manifest,
            original.environment_payload,
        )

    def test_all_75_surfaces_cross_bind_around_holdout_boundary(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, self.inputs)
        self.assertEqual(tuple(checked.views), (1, 2))
        self.assertEqual(tuple(checked.campaign_resources), (1, 2))
        with self.assertRaises(TypeError):
            close_derivation(checked)
        ticket, frozen = _close_after_freeze(
            checked, self.inputs.acquire_holdout
        )
        opened = open_holdout(ticket, frozen.canonical_bytes)
        self.assertEqual(opened.view.replica, 3)
        self.assertFalse(hasattr(opened, "views"))
        self.assertGreater(opened.campaign_resources.logical_checkpoint_read_bytes, 0)
        tampered = frozen.canonical_bytes[:-1] + bytes(
            [frozen.canonical_bytes[-1] ^ 1]
        )
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            open_holdout(ticket, tampered)

    def test_replica_3_surfaces_are_not_opened_during_derivation_checks(self) -> None:
        inputs = _inputs()
        checked = check_analysis_input("a4-synthetic", _COMMIT, inputs)
        self.assertEqual(tuple(checked.views), (1, 2))
        self.assertEqual(inputs.holdout_calls, 0)
        ticket, frozen = _close_after_freeze(checked, inputs.acquire_holdout)
        self.assertEqual(inputs.holdout_calls, 0)
        open_holdout(ticket, frozen.canonical_bytes)
        self.assertEqual(inputs.holdout_calls, 1)

    def test_idle_index_difference_is_the_first_terminal(self) -> None:
        indexes = copy.deepcopy(self.inputs[1].page_indexes)
        indexes["EMPTY_R"]["ordered_page_sha256"][0] = "f" * 64
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT, self._changed(1, indexes=indexes)
            )
        self.assertEqual(raised.exception.predicate_id, "A4-IDLE-EQUALITY")

    def test_schema_ordinal_and_row_hash_are_recomputed(self) -> None:
        snapshots = copy.deepcopy(self.inputs[1].schema_snapshots)
        snapshots["T1_CREATE_ID"]["ordinal"] = 4
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT, self._changed(1, snapshots=snapshots)
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")
        snapshots = copy.deepcopy(self.inputs[1].schema_snapshots)
        snapshots["T1_CREATE_ID"]["tables"][0]["rolling_row_sha256"] = "f" * 64
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT, self._changed(1, snapshots=snapshots)
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")

    def test_page_sequence_is_independently_reconstructed(self) -> None:
        indexes = copy.deepcopy(self.inputs[1].page_indexes)
        indexes["T1_CREATE_ID"]["ordered_page_sha256"][0] = "f" * 64
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT, self._changed(1, indexes=indexes)
            )
        self.assertEqual(
            raised.exception.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION"
        )

    def test_artifact_reference_path_must_match_manifest_and_checkpoint(self) -> None:
        observation = copy.deepcopy(self.inputs[1].replica_observation)
        observation["checkpoints"][0]["page_index"]["path"] = observation[
            "checkpoints"
        ][1]["page_index"]["path"]
        manifest = copy.deepcopy(self.inputs[1].artifact_manifest)
        observation_entry = next(
            entry for entry in manifest["files"]
            if entry["role"] == "replica_observation"
        )
        observation_payload = canonical_json_bytes(observation)
        observation_entry.update(
            sha256=hashlib.sha256(observation_payload).hexdigest(),
            size_bytes=len(observation_payload),
        )
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT,
                self._changed(1, observation=observation, manifest=manifest),
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION")

    def test_page_blob_inventory_rejects_noncanonical_extra_entry(self) -> None:
        manifest = copy.deepcopy(self.inputs[1].artifact_manifest)
        blob = next(entry for entry in manifest["files"] if entry["role"] == "page_blob")
        manifest["files"].append({**blob, "path": "page-store/extra.page"})
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input(
                "a4-synthetic", _COMMIT, self._changed(1, manifest=manifest)
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SNAPSHOT-RECONSTRUCTION")

    def test_changed_entry_equality_passes_and_67200_fixture_fails(self) -> None:
        maximum = int(PLAN["bounds"]["max_changed_hash_entries_per_replica"])
        require_resource_bounds(CampaignResourceTotals(0, maximum, 1, PAGE_SIZE, 0, 0))
        with self.assertRaises(A4AnalysisError) as raised:
            require_resource_bounds(
                CampaignResourceTotals(0, 21 * 3_200, 2, 2 * PAGE_SIZE, 0, 0)
            )
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")

    def test_schema_valid_67200_changed_entry_campaign_reaches_resource(self) -> None:
        inputs = dict(self.inputs)
        inputs[1] = self._changed_entry_adversary()
        with self.assertRaises(A4AnalysisError) as raised:
            check_analysis_input("a4-synthetic", _COMMIT, inputs)
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")
        self.assertIn("changed", str(raised.exception))

    def test_prefreeze_campaign_resource_abort_never_opens_holdout(self) -> None:
        inputs = _inputs()
        inputs.derivation[1] = self._changed_entry_adversary()
        with self.assertRaises(A4AnalysisError) as raised:
            analyze("a4-synthetic", _COMMIT, inputs)
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")
        self.assertEqual(inputs.holdout_calls, 0)

    def test_aggregate_derivation_store_rejects_before_view_or_page_access(
        self,
    ) -> None:
        maximum = int(PLAN["bounds"]["max_unique_page_blobs"])
        per_checkpoint_maximum = int(
            PLAN["bounds"]["max_final_pages_per_replica"]
        )
        all_digests = tuple(
            hashlib.sha256(f"aggregate-{index}".encode()).hexdigest()
            for index in range(maximum + 1)
        )
        page_calls: list[str] = []

        def replica_input(
            replica: int, digests: tuple[str, ...]
        ) -> ReplicaAnalysisInput:
            ordered = {
                checkpoint: (digests[0],) for checkpoint in CHECKPOINT_IDS
            }
            ordered["EMPTY"] = digests[:per_checkpoint_maximum]
            ordered["EMPTY_R"] = digests[per_checkpoint_maximum:]

            class Source:
                checkpoint_ids = CHECKPOINT_IDS
                page_count = {
                    checkpoint: len(sequence)
                    for checkpoint, sequence in ordered.items()
                }
                ordered_page_sha256 = ordered

                @staticmethod
                def page_bytes(digest: str) -> bytes:
                    page_calls.append(digest)
                    raise AssertionError("page bytes accessed before aggregate preflight")

            source = Source()
            source.replica = replica
            return ReplicaAnalysisInput(source, {}, None, None, None, None, None)

        replicas = {
            1: replica_input(1, all_digests[: maximum // 2 + 1]),
            2: replica_input(2, all_digests[maximum // 2 + 1 :]),
        }
        with (
            mock.patch.object(input_module, "check_campaign_replica") as campaign,
            self.assertRaises(A4AnalysisError) as raised,
        ):
            check_analysis_input("a4-synthetic", _COMMIT, replicas)
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")
        campaign.assert_not_called()
        self.assertEqual(page_calls, [])

    def test_aggregate_holdout_store_rejects_before_view_or_page_access(
        self,
    ) -> None:
        maximum = int(PLAN["bounds"]["max_unique_page_blobs"])
        derivation_digests = frozenset(
            hashlib.sha256(f"retained-{index}".encode()).hexdigest()
            for index in range(maximum)
        )
        extra_digest = hashlib.sha256(b"holdout-extra").hexdigest()
        page_calls: list[str] = []

        class Source:
            replica = 3
            checkpoint_ids = CHECKPOINT_IDS
            page_count = {checkpoint: 1 for checkpoint in CHECKPOINT_IDS}
            ordered_page_sha256 = {
                checkpoint: (extra_digest,) for checkpoint in CHECKPOINT_IDS
            }

            @staticmethod
            def page_bytes(digest: str) -> bytes:
                page_calls.append(digest)
                raise AssertionError("page bytes accessed before aggregate preflight")

        holdout = ReplicaAnalysisInput(Source(), {}, None, None, None, None, None)

        def provider(_payload: bytes, _sha256: str) -> ReplicaAnalysisInput:
            return holdout

        ticket = HoldoutTicket(
            "a4-synthetic",
            _COMMIT,
            provider,
            derivation_digests,
            "a" * 64,
            None,
            (),
            frozenset(),
        )
        with (
            mock.patch(
                "a4_analysis_state.resume_derivation",
                return_value={"campaign_id": "a4-synthetic"},
            ),
            mock.patch.object(input_module, "check_campaign_replica") as campaign,
            self.assertRaises(A4AnalysisError) as raised,
        ):
            open_holdout(ticket, b"frozen")
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")
        campaign.assert_not_called()
        self.assertEqual(page_calls, [])


class A4AnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = analyze("a4-synthetic", _COMMIT, _inputs())

    def test_decisive_report_is_schema_valid_and_keeps_claims_narrow(self) -> None:
        report = dict(self.result.report)
        validate_schema(report, "dao_a4_analysis_report")
        self.assertEqual(len(report["predicate_results"]), 40)
        self.assertTrue(all(row["status"] == "pass" for row in report["predicate_results"]))
        self.assertEqual(
            [
                row["predicate_measured_survivor_count"]
                for row in report["predicate_results"][:4]
            ],
            [0, 0, 0, 0],
        )
        self.assertEqual(report["claims"], PLAN["claims"])
        self.assertFalse(report["claims"]["dao_compatibility_or_support"])
        self.assertFalse(report["claims"]["synthetic_dry_run_is_a4_evidence"])

    def test_frozen_document_round_trips_exact_bytes(self) -> None:
        frozen = self.result.frozen
        resumed = resume_derivation(
            frozen.canonical_bytes,
            frozen.sha256,
            frozen.occurrence_evidence_bytes,
        )
        self.assertEqual(resumed["document_type"], "dao_a4_frozen_derivation_candidates")
        tampered = frozen.canonical_bytes[:-1] + bytes([frozen.canonical_bytes[-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            resume_derivation(tampered, frozen.sha256)
        document = copy.deepcopy(dict(resumed))
        document["layers"]["h1_tdef_to_map_row"][
            "canonical_candidates_sha256"
        ] = "f" * 64
        with self.assertRaisesRegex(ValueError, "candidate-array hash"):
            payload = canonical_model_bytes(document)
            resume_derivation(
                payload,
                hashlib.sha256(payload).hexdigest(),
                frozen.occurrence_evidence_bytes,
            )

    def test_holdout_is_not_opened_until_after_frozen_bytes_exist(self) -> None:
        inputs = _inputs()
        checked = check_analysis_input("a4-synthetic", _COMMIT, inputs)
        self.assertEqual(tuple(checked.views), (1, 2))
        self.assertEqual(inputs.holdout_calls, 0)
        ledger = WorkLedger()
        layers = derive_layers(checked, ledger)
        frozen = freeze_derivation(checked, layers, ledger)
        self.assertGreater(len(frozen.canonical_bytes), 0)
        ticket = close_derivation(
            checked,
            frozen.canonical_bytes,
            frozen.sha256,
            inputs.acquire_holdout,
            frozen.occurrence_evidence_bytes,
        )
        self.assertEqual(inputs.holdout_calls, 0)
        tampered = frozen.canonical_bytes[:-1] + bytes(
            [frozen.canonical_bytes[-1] ^ 1]
        )
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            open_holdout(ticket, tampered)
        self.assertEqual(inputs.holdout_calls, 0)
        open_holdout(ticket, frozen.canonical_bytes)
        self.assertEqual(inputs.holdout_calls, 1)

    def _freeze_encoding_terminal(self, *, coherent_identifiers: bool):
        inputs = _inputs()
        if coherent_identifiers:
            for replica in (1, 2):
                source = generate_replica(replica=replica)
                catalog_page = int(source.metadata["system_pages"]["catalog"])
                payloads = dict(source.payloads)
                transformed = {}
                sequences = {}
                for checkpoint, sequence in source.ordered_page_sha256.items():
                    digest = sequence[catalog_page]
                    replacement = transformed.get(digest)
                    if replacement is None:
                        page = source.page_bytes(digest)
                        count = int.from_bytes(page[8:10], "little")
                        starts = [
                            int.from_bytes(
                                page[10 + 2 * row : 12 + 2 * row], "little"
                            )
                            & 0x0FFF
                            for row in range(count)
                        ]
                        ends = [2048, *starts[:-1]] if starts else []
                        rows = []
                        for start, end in zip(starts, ends, strict=True):
                            row = page[start:end]
                            name = row[3 : 3 + row[2]]
                            utf8 = name.decode("cp1252").encode("utf-8")
                            if utf8 != name:
                                row += row[:2] + bytes([len(utf8)]) + utf8
                            rows.append(row)
                        rebuilt = data_page(rows)
                        replacement = hashlib.sha256(rebuilt).hexdigest()
                        transformed[digest] = replacement
                        payloads[replacement] = rebuilt
                    updated = list(sequence)
                    updated[catalog_page] = replacement
                    sequences[checkpoint] = tuple(updated)
                source = replace(
                    source,
                    ordered_page_sha256=sequences,
                    payloads=payloads,
                )
                inputs.derivation[replica] = _surface(source, "a4-synthetic")
        checked = check_analysis_input("a4-synthetic", _COMMIT, inputs)
        ledger = WorkLedger()
        original_decode = a4_layer_h4._decode_for_model
        first_class = a4_layer_h4.ENCODING_CLASSES[0]
        original_class_match = a4_layer_h4.encoding_class_matches

        def selected_identifier(group, occurrence, model):
            decoded = original_decode(group, occurrence, model)
            if decoded is None:
                return None
            operation = group.record.operation_id
            operation_index = a4_layer_h4.OPERATIONS.index(operation)
            if (
                operation == "T2_RECREATE"
                and model["identifier_lifecycle"]
                == "stable_for_same_physical_name_including_t2_v1_v2"
            ):
                operation_index = a4_layer_h4.OPERATIONS.index("T2_CREATE")
            identifier = (
                10 + operation_index
                if coherent_identifiers
                else 1
            )
            stored_length = decoded.stored_length
            if coherent_identifiers:
                payload = bytes.fromhex(occurrence.encoded_hex)
                cp1252 = group.expected_name.encode("cp1252", errors="strict")
                utf8 = group.expected_name.encode("utf-8", errors="strict")
                stored_length = len(cp1252) if payload == cp1252 else len(utf8)
            return replace(
                decoded,
                identifier=identifier,
                stored_length=stored_length,
            )

        def selected_classes(class_id, _name, _payload, _stored_length):
            if coherent_identifiers:
                return original_class_match(
                    class_id, _name, _payload, _stored_length
                )
            return class_id == first_class

        with mock.patch.object(
            a4_layer_h4, "_decode_for_model", side_effect=selected_identifier
        ), mock.patch.object(
            a4_layer_h4,
            "encoding_class_matches",
            side_effect=selected_classes,
        ), mock.patch.object(
            a4_derivation,
            "encoding_class_matches",
            side_effect=selected_classes,
        ):
            layers = derive_layers(checked, ledger)

        self.assertIsInstance(layers, DerivationTerminal)
        self.assertEqual(layers.predicate_id, "A4-H4-ENCODING-AMBIGUOUS")
        frozen = freeze_derivation(checked, layers, ledger)
        resumed = resume_derivation(
            frozen.canonical_bytes,
            frozen.sha256,
            frozen.occurrence_evidence_bytes,
        )
        self.assertEqual(
            resumed["layers"]["h4_catalog_bootstrap"]["encoding_result"][
                "terminal_predicate_id"
            ],
            "A4-H4-ENCODING-AMBIGUOUS",
        )
        return layers, resumed

    def test_zero_survivor_encoding_terminal_freezes(self) -> None:
        layers, _resumed = self._freeze_encoding_terminal(
            coherent_identifiers=False
        )
        encoding = layers.layers["h4_catalog_bootstrap"]["encoding_result"]
        self.assertEqual(encoding["predicate_measured_survivor_count"], 0)
        self.assertEqual(encoding["candidates"], [])

    def test_multiple_survivor_encoding_terminal_freezes(self) -> None:
        layers, resumed = self._freeze_encoding_terminal(
            coherent_identifiers=True
        )
        structural = resumed["layers"]["h4_catalog_bootstrap"][
            "structural_result"
        ]["candidates"][0]
        self.assertEqual(
            [binding["replica"] for binding in structural["instance_bindings"]],
            [1, 2],
        )
        encoding = layers.layers["h4_catalog_bootstrap"]["encoding_result"]
        self.assertEqual(encoding["predicate_measured_survivor_count"], 2)
        self.assertEqual(len(encoding["candidates"]), 2)
        for candidate in encoding["candidates"]:
            self.assertEqual(
                [
                    binding["replica"]
                    for binding in candidate["instance_bindings"]
                ],
                [1],
            )
            self.assertEqual(
                candidate["instance_bindings"][0]["structural_candidate_id"],
                structural["canonical_candidate_id"],
            )

    def test_h1_accepts_lifecycle_pages_reserved_before_create(self) -> None:
        h1 = self.result.report["layers"]["h1_tdef_to_map_row"]
        self.assertEqual(h1["status"], "model")
        self.assertEqual(h1["candidates"][0]["model"]["layout"], "u8_row_then_u24le_page")
        self.assertEqual(len(h1["candidates"][0]["instance_bindings"]), 10)

    def test_value_equivalent_raw_tuples_are_one_structural_model(self) -> None:
        structural = self.result.report["layers"]["h4_catalog_bootstrap"]["structural_result"]
        self.assertEqual(structural["status"], "model")
        candidate = structural["candidates"][0]
        self.assertEqual(candidate["model"]["kind_width"], 1)
        self.assertEqual(candidate["model"]["name_length_width"], 1)
        self.assertEqual(
            [binding["value_equivalent_tuple_count"] for binding in candidate["instance_bindings"]],
            [2, 2],
        )

    def test_occurrence_evidence_hash_binds_both_replicas(self) -> None:
        reference = self.result.report["h4_occurrence_evidence"]
        bindings = self.result.report["layers"]["h4_catalog_bootstrap"]["structural_result"]["candidates"][0]["instance_bindings"]
        self.assertEqual({binding["occurrence_evidence_sha256"] for binding in bindings}, {reference["sha256"]})
        validate_schema(dict(self.result.occurrence_evidence), "dao_a4_h4_occurrence_evidence")

    def test_holdout_layout_change_fails_h1_and_stops_downstream(self) -> None:
        layouts = PLAN["candidate_grammars"]["h1"]["locator_layouts"]
        result = analyze(
            "a4-synthetic",
            _COMMIT,
            _inputs(SyntheticParameters(layout_by_replica={3: layouts[0]})),
        )
        holdout = result.report["holdout_results"]
        self.assertEqual(holdout["h1"]["status"], "fail")
        failed = next(
            row
            for row in result.report["predicate_results"]
            if row["predicate_id"] == "A4-H1-HOLDOUT-PREDICTION"
        )
        self.assertEqual(failed["derivation_survivor_count"], 1)
        self.assertTrue(all(holdout[name]["status"] == "not_applicable" for name in ("h2", "h3", "h4_root", "h4_fields")))
        for layer in ("h1_tdef_to_map_row", "h2_row_identity_map_role", "h3_indirect_traversal"):
            self.assertEqual(
                result.report["layers"][layer]["candidates"][0]["model"],
                self.result.report["layers"][layer]["candidates"][0]["model"],
            )
        for result_key in ("root_result", "structural_result", "encoding_result"):
            self.assertEqual(
                result.report["layers"]["h4_catalog_bootstrap"][result_key]["candidates"][0]["model"],
                self.result.report["layers"]["h4_catalog_bootstrap"][result_key]["candidates"][0]["model"],
            )

    def test_derivation_terminal_campaign_check_opens_after_freeze(self) -> None:
        signature = PLAN["candidate_grammars"]["h1"][
            "pair_multiple_reachability_signature"
        ]
        inputs = _inputs(
            SyntheticParameters(
                signature_id=signature["signature_id"],
                locator_offsets=tuple(
                    interval[0] for interval in signature["locator_holes"]
                ),
            )
        )
        result = analyze("a4-synthetic", _COMMIT, inputs)
        self.assertEqual(inputs.holdout_calls, 1)
        self.assertEqual(inputs.last_holdout_sha256, result.frozen.sha256)
        self.assertTrue(result.report["holdout_opened_after_freeze"])
        self.assertGreater(result.report["analyzer_logical_read_bytes_by_replica"][2], 0)
        self.assertTrue(
            all(
                row["status"] == "pass"
                for row in result.report["predicate_results"][:4]
            )
        )
        self.assertTrue(
            all(
                row["status"] == "not_applicable"
                for row in result.report["holdout_results"].values()
            )
        )
        self.assertEqual(
            result.report["layers"]["h1_tdef_to_map_row"][
                "terminal_predicate_id"
            ],
            "A4-H1-LOCATOR-PAIR-MULTIPLE",
        )

    def test_derivation_terminal_replica3_campaign_failure_emits_no_report(self) -> None:
        signature = PLAN["candidate_grammars"]["h1"][
            "pair_multiple_reachability_signature"
        ]
        inputs = _inputs(
            SyntheticParameters(
                signature_id=signature["signature_id"],
                locator_offsets=tuple(
                    interval[0] for interval in signature["locator_holes"]
                ),
            )
        )

        def invalid_holdout(
            frozen_payload: bytes, frozen_sha256: str
        ) -> ReplicaAnalysisInput:
            original = inputs.acquire_holdout(frozen_payload, frozen_sha256)
            return ReplicaAnalysisInput(
                original.source,
                original.table_row_counts,
                original.replica_observation,
                original.page_indexes,
                original.schema_snapshots,
                original.artifact_manifest,
                b"{}",
            )

        with self.assertRaises(A4AnalysisError) as raised:
            analyze(
                "a4-synthetic", _COMMIT, inputs, holdout_provider=invalid_holdout
            )
        self.assertEqual(raised.exception.predicate_id, "A4-SCHEMA-SNAPSHOT")
        self.assertEqual(inputs.holdout_calls, 1)
        self.assertIsNotNone(inputs.last_holdout_sha256)


if __name__ == "__main__":
    unittest.main()
