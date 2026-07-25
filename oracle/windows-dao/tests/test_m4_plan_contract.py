from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ORACLE = TESTS.parent
REPOSITORY = ORACLE.parent.parent
M4 = ORACLE / "experiments" / "m4"
sys.path.insert(0, str(ORACLE / "scripts"))

from protocol_validation import (  # noqa: E402
    ValidationError,
    lint_schema,
    load_json,
    validate_schema_value,
)

SHA = "0" * 64
COMMIT = "1" * 40
TIME = "2026-07-25T12:00:00+00:00"
SAMPLE = "M4-V20-U-01"
PLAN_SHA256 = "28048c300d1a056020d437c635c84a5308260f92c6bc0a61ba404a22415f1321"


def artifact(name: str) -> dict[str, str]:
    return {"path": f"evidence/{SAMPLE}/{name}.json", "sha256": SHA}


def validate_invocation_relations(value: dict[str, object]) -> None:
    phase = value["phase_id"]
    expected_ordinal = 1 if phase == "creator" else 2
    expected_suffix = "-CREATOR" if phase == "creator" else "-REOPEN"
    contract = value["phase_contract"]
    if (
        value["phase_ordinal"] != expected_ordinal
        or not str(value["worker_run_id"]).endswith(expected_suffix)
        or not isinstance(contract, dict)
        or contract.get("kind") != phase
    ):
        raise ValidationError("invocation phase relationship differs")


def validate_operation_sequence(value: dict[str, object]) -> None:
    phase = value["phase_id"]
    expected = (
        (
            "bindings_verified",
            "com_activated",
            "database_created",
            "version_read",
            "empty_schema_read",
            "database_closed",
            "ldb_absence_verified",
            "prefix_observed",
        )
        if phase == "creator"
        else (
            "bindings_verified",
            "clone_verified",
            "com_activated",
            "database_opened",
            "version_read",
            "empty_schema_read",
            "database_closed",
            "ldb_absence_verified",
            "prefix_observed",
        )
    )
    entries = value["entries"]
    if not isinstance(entries, list) or tuple(item["action"] for item in entries) != expected:
        raise ValidationError("operation sequence differs")
    if [item["sequence"] for item in entries] != list(range(1, len(entries) + 1)):
        raise ValidationError("operation sequence numbers differ")


def validate_manifest_roles(value: dict[str, object]) -> None:
    expected = {
        "plan": 1,
        "environment": 1,
        "analysis_report": 1,
        "sample_record": 36,
        "phase_invocation": 72,
        "phase_worker_result": 72,
        "operation_log": 72,
        "semantic_snapshot": 72,
        "clone_log": 36,
        "database": 72,
        "prefix": 72,
    }
    files = value["files"]
    actual = {
        role: sum(item["role"] == role for item in files) for role in expected
    }
    if actual != expected or len({item["path"] for item in files}) != 507:
        raise ValidationError("bundle role projection differs")


class M4PlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            path.name: load_json(path) for path in sorted(M4.glob("*.schema.json"))
        }
        cls.plan = load_json(M4 / "m4-header-discriminator.plan.json")

    def validate(self, value: object, schema_name: str) -> None:
        schema = self.schemas[schema_name]
        validate_schema_value(value, schema, schema, "$")

    def assert_rejected(self, value: object, schema_name: str) -> None:
        with self.assertRaises(ValidationError):
            self.validate(value, schema_name)

    def test_all_schemas_lint_and_plan_is_blocked(self) -> None:
        for schema in self.schemas.values():
            lint_schema(schema)
        self.validate(self.plan, "plan.schema.json")
        self.assertEqual(
            hashlib.sha256(
                (M4 / "m4-header-discriminator.plan.json").read_bytes()
            ).hexdigest(),
            PLAN_SHA256,
        )
        self.assertEqual(
            self.plan["execution_gate"],
            {
                "status": "BLOCKED",
                "reason": "runner_and_analysis_not_implemented",
            },
        )

    def test_exact_schedule_paths_and_arithmetic(self) -> None:
        samples = self.plan["samples"]
        conditions = [item["condition_id"] for item in self.plan["conditions"]]
        self.assertEqual(len(samples), 36)
        self.assertEqual(
            sorted(item["launch_ordinal"] for item in samples), list(range(1, 37))
        )
        paths = [
            item[key]
            for item in samples
            for key in ("creator_database_path", "reopen_database_path", "record_path")
        ]
        self.assertEqual(len(paths), len(set(paths)))
        for block in range(1, 7):
            rows = sorted(
                (item for item in samples if item["block"] == block),
                key=lambda item: item["position_in_block"],
            )
            self.assertEqual(
                [item["condition_id"] for item in rows],
                conditions[block - 1 :] + conditions[: block - 1],
            )
        bounds = self.plan["bounds"]
        self.assertEqual(bounds["max_worker_processes"], 2 * len(samples))
        self.assertEqual(bounds["max_database_artifacts"], 2 * len(samples))
        self.assertEqual(bounds["max_acquisition_database_reads"], 5 * len(samples))
        self.assertEqual(
            bounds["max_acquisition_database_read_bytes"],
            bounds["max_acquisition_database_reads"]
            * bounds["max_database_bytes"],
        )

    def invocation(self) -> dict[str, object]:
        return {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_invocation",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": SAMPLE,
            "condition_id": "V20-U",
            "phase_id": "creator",
            "phase_ordinal": 1,
            "worker_run_id": f"{SAMPLE}-CREATOR",
            "worker_ordinal": 1,
            "nonce": "a" * 32,
            "campaign_run_id": "20260725T120000Z-m4-test",
            "producer_commit": COMMIT,
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "remote_ref": "refs/heads/codex/jet3-v1-foundations",
            "repository_root": "control/repository/root",
            "plan_path": "control/contracts/plan.json",
            "plan_sha256": SHA,
            "environment_path": "control/environment/environment.json",
            "environment_sha256": SHA,
            "provider_sha256": SHA,
            "stage_root": "private/stage/root",
            "output_root": "private/output/root",
            "database_path": f"evidence/{SAMPLE}/creator.mdb",
            "result_path": f"evidence/{SAMPLE}/creator-result.json",
            "phase_contract": {
                "kind": "creator",
                "method": "DBEngine.CreateDatabase",
                "locale": ";LANGID=0x0409;CP=1252;COUNTRY=0",
                "version_option": "dbVersion20",
                "version_api_value": 16,
                "encryption_option": "omitted",
                "encryption_api_value": 0,
                "create_option_value": 16,
                "compact_database_used": False,
                "expected_dao_version": "2.0",
            },
            "created_at_utc": TIME,
            "bindings_verified_before_com": True,
        }

    def test_invocation_worker_log_snapshot_clone_and_manifest_shapes(self) -> None:
        invocation = self.invocation()
        self.validate(invocation, "invocation.schema.json")
        validate_invocation_relations(invocation)
        log = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_operation_log",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": SAMPLE,
            "phase_id": "creator",
            "phase_ordinal": 1,
            "worker_run_id": f"{SAMPLE}-CREATOR",
            "entries": [
                {
                    "sequence": index,
                    "timestamp_utc": TIME,
                    "action": action,
                    "status": "pass",
                }
                for index, action in enumerate(
                    (
                        "bindings_verified",
                        "com_activated",
                        "database_created",
                        "version_read",
                        "empty_schema_read",
                        "database_closed",
                        "ldb_absence_verified",
                        "prefix_observed",
                    ),
                    1,
                )
            ],
            "final_status": "pass",
        }
        self.validate(log, "operation-log.schema.json")
        validate_operation_sequence(log)
        snapshot = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_empty_schema_version_snapshot",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": SAMPLE,
            "phase_id": "creator",
            "phase_ordinal": 1,
            "captured_while_database_open": True,
            "captured_at_utc": TIME,
            "dao_version": "2.0",
            "empty_user_schema": True,
            "user_table_count": 0,
        }
        self.validate(snapshot, "snapshot.schema.json")
        result = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_worker_result",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": SAMPLE,
            "phase_id": "creator",
            "phase_ordinal": 1,
            "worker_run_id": f"{SAMPLE}-CREATOR",
            "worker_ordinal": 1,
            "nonce": "a" * 32,
            "process_id": 100,
            "architecture": "x86",
            "provider": {
                "powershell_version": "5.1.22621.6133",
                "prog_id": "DAO.DBEngine.36",
                "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                "server_sha256": SHA,
            },
            "started_at_utc": TIME,
            "finished_at_utc": TIME,
            "bindings_verified_before_com": True,
            "invocation_sha256": SHA,
            "operation_log": artifact("creator-log"),
            "snapshot": artifact("creator-snapshot"),
            "pre_com_file_binding": None,
            "post_close_file_observations": {
                "database_path": f"evidence/{SAMPLE}/creator.mdb",
                "database_bytes": 2048,
                "database_sha256": SHA,
                "prefix": artifact("creator-prefix"),
                "prefix_bytes": 2048,
                "lock_file_absent_after_close": True,
            },
            "execution_status": "pass",
        }
        self.validate(result, "worker-result.schema.json")
        identity = {
            "volume_serial_number": "0" * 8,
            "file_index": "1" * 16,
            "link_count": 1,
        }
        clone = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_clone_log",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": SAMPLE,
            "started_at_utc": TIME,
            "completed_at_utc": TIME,
            "source_path": f"evidence/{SAMPLE}/creator.mdb",
            "destination_path": f"evidence/{SAMPLE}/reopen.mdb",
            "source_bytes": 2048,
            "destination_bytes": 2048,
            "source_sha256_before_clone": SHA,
            "source_sha256_after_clone": SHA,
            "destination_sha256": SHA,
            "source_file_identity": identity,
            "destination_file_identity": {**identity, "file_index": "2" * 16},
            "all_hashes_equal": True,
            "same_volume": True,
            "distinct_file_identity": True,
            "no_hardlink": True,
            "reparse_free": True,
            "completed_before_reopen_com": True,
            "status": "pass",
        }
        self.validate(clone, "clone-log.schema.json")
        worker = {
            "process_id": 100,
            "started_at_utc": TIME,
            "worker_run_id": f"{SAMPLE}-CREATOR",
            "worker_ordinal": 1,
            "nonce": "a" * 32,
            "architecture": "x86",
            "provider": {
                "powershell_version": "5.1.22621.6133",
                "prog_id": "DAO.DBEngine.36",
                "clsid": "{00000100-0000-0010-8000-00AA006D2EA4}",
                "server_sha256": SHA,
            },
            "fresh_process": True,
            "bindings_verified_before_com": True,
        }
        phase_artifacts = {
            key: artifact(f"creator-{key}")
            for key in ("invocation", "operation_log", "snapshot", "worker_result")
        }
        dao = {
            "captured_while_database_open": True,
            "dao_version": "2.0",
            "empty_user_schema": True,
            "user_table_count": 0,
        }
        post = {
            "database_path": f"evidence/{SAMPLE}/creator.mdb",
            "database_bytes": 2048,
            "database_sha256": SHA,
            "prefix_path": f"evidence/{SAMPLE}/creator.prefix.bin",
            "prefix_bytes": 2048,
            "prefix_sha256": SHA,
            "database_closed": True,
            "lock_file_absent_after_close": True,
        }
        reopen_worker = {
            **worker,
            "process_id": 101,
            "worker_run_id": f"{SAMPLE}-REOPEN",
            "worker_ordinal": 2,
            "nonce": "b" * 32,
        }
        record = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_sample_record",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "plan_sha256": SHA,
            "producer_commit": COMMIT,
            "environment_sha256": SHA,
            "provider_sha256": SHA,
            "sample_id": SAMPLE,
            "condition_id": "V20-U",
            "replica": 1,
            "block": 1,
            "position_in_block": 1,
            "launch_ordinal": 1,
            "creation": {
                "method": "DBEngine.CreateDatabase",
                "version_option": "dbVersion20",
                "version_api_value": 16,
                "encryption_option": "omitted",
                "encryption_api_value": 0,
                "create_option_value": 16,
                "compact_database_used": False,
            },
            "phases": {
                "creator": {
                    "phase_id": "creator",
                    "phase_ordinal": 1,
                    "worker": worker,
                    "artifacts": phase_artifacts,
                    "dao_observations_while_open": dao,
                    "post_close_file_observations": post,
                    "status": "pass",
                },
                "reopen": {
                    "phase_id": "reopen",
                    "phase_ordinal": 2,
                    "worker": reopen_worker,
                    "artifacts": {
                        key: artifact(f"reopen-{key}")
                        for key in (
                            "invocation",
                            "operation_log",
                            "snapshot",
                            "worker_result",
                        )
                    },
                    "pre_com_file_binding": {
                        "database_path": f"evidence/{SAMPLE}/reopen.mdb",
                        "database_bytes": 2048,
                        "database_sha256": SHA,
                        "verified_before_com": True,
                    },
                    "dao_observations_while_open": dao,
                    "post_close_file_observations": {
                        **post,
                        "database_path": f"evidence/{SAMPLE}/reopen.mdb",
                        "prefix_path": f"evidence/{SAMPLE}/reopen.prefix.bin",
                    },
                    "status": "pass",
                },
            },
            "controller_clone": {
                "owner": "controller",
                "clone_log": artifact("clone-log"),
                "started_at_utc": TIME,
                "completed_at_utc": TIME,
                **{
                    key: value
                    for key, value in clone.items()
                    if key
                    not in {
                        "protocol_version",
                        "document_type",
                        "experiment_id",
                        "sample_id",
                        "started_at_utc",
                        "completed_at_utc",
                        "reparse_free",
                    }
                },
                "creator_closed_before_clone": True,
                "source_immutable_during_clone": True,
                "source_unchanged_after_clone": True,
                "exact_byte_clone": True,
                "source_reparse_free": True,
                "destination_reparse_free": True,
                "identities_preserved_by_same_volume_publish_rename": True,
                "reopen_bindings_verified_before_com": True,
            },
            "execution_status": "pass",
        }
        self.validate(record, "sample-record.schema.json")
        manifest = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_bundle_manifest",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "run_id": "20260725T120000Z-m4-test",
            "producer_commit": COMMIT,
            "created_at_utc": TIME,
            "sample_count": 36,
            "worker_count": 72,
            "file_count": 507,
            "bundle_tree_complete": True,
            "unexpected_files_present": False,
            "symlinks_or_reparses_present": False,
            "execution_status": "pass",
            "files": [],
        }
        roles = (
            ["plan", "environment", "analysis_report"]
            + ["sample_record"] * 36
            + ["phase_invocation"] * 72
            + ["phase_worker_result"] * 72
            + ["operation_log"] * 72
            + ["semantic_snapshot"] * 72
            + ["clone_log"] * 36
            + ["database"] * 72
            + ["prefix"] * 72
        )
        manifest["files"] = [
            {
                "path": f"payload/files/file-{index:03d}.bin",
                "role": role,
                "sha256": f"{index:064x}",
                "size_bytes": 2,
                "media_type": "application/octet-stream",
            }
            for index, role in enumerate(roles)
        ]
        self.validate(manifest, "bundle-manifest.schema.json")
        validate_manifest_roles(manifest)

    def test_unknown_traversal_and_phase_drift_fail_closed(self) -> None:
        value = self.invocation()
        unknown = copy.deepcopy(value)
        unknown["unexpected"] = True
        self.assert_rejected(unknown, "invocation.schema.json")
        traversal = copy.deepcopy(value)
        traversal["database_path"] = "../escape.mdb"
        self.assert_rejected(traversal, "invocation.schema.json")
        drift = copy.deepcopy(value)
        drift["phase_id"] = "reopen"
        drift["phase_ordinal"] = 1
        self.validate(drift, "invocation.schema.json")
        with self.assertRaises(ValidationError):
            validate_invocation_relations(drift)

    def test_provenance_contains_every_checked_contract_hash(self) -> None:
        provenance = (REPOSITORY / "docs" / "PROVENANCE.md").read_text("utf-8")
        paths = [M4 / "m4-header-discriminator.plan.json", *M4.glob("*.schema.json")]
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertIn(digest, provenance, path.name)


if __name__ == "__main__":
    unittest.main()
