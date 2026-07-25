#!/usr/bin/env python3
"""Focused corruption and projection tests for the checked M4 validator."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from m4_records import (  # noqa: E402
    CHECKED_PLAN,
    ValidationError,
    load_bounded_json,
    load_checked_plan,
    resolve_bundle_path,
    validate_invocation_document,
    validate_plan_document,
)
from m4_bundle import (  # noqa: E402
    _validate_databases_and_prefixes,
    discover_bundle,
)


class StrictJsonTests(unittest.TestCase):
    def _load(self, payload: bytes) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_bytes(payload)
            load_bounded_json(path, 1024)

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "duplicate JSON object key"):
            self._load(b'{"same":1,"same":2}')

    def test_bom_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "byte-order marks"):
            self._load(b"\xef\xbb\xbf{}")

    def test_nonfinite_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "non-finite"):
            self._load(b'{"number":NaN}')


class CheckedPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan, cls.digest = load_checked_plan()

    def test_checked_plan_passes(self) -> None:
        validate_plan_document(copy.deepcopy(self.plan))

    def test_partial_cyclic_schedule_is_rejected(self) -> None:
        altered = copy.deepcopy(self.plan)
        altered["samples"][6]["condition_id"] = "V20-U"
        with self.assertRaises(ValidationError):
            validate_plan_document(altered)

    def test_duplicate_plan_path_is_rejected(self) -> None:
        altered = copy.deepcopy(self.plan)
        altered["samples"][1]["record_path"] = altered["samples"][0]["record_path"]
        with self.assertRaises(ValidationError):
            validate_plan_document(altered)

    def test_byte_identity_rejects_schema_valid_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(self.plan), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "bytes differ"):
                load_checked_plan(path)


class InvocationProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.bundle = base / "stage"
        self.repository = base / "repository"
        self.output = base / "output"
        for path in (self.bundle, self.repository, self.output):
            path.mkdir()
        (self.bundle / "plan").mkdir()
        (self.bundle / "bindings").mkdir()
        (self.bundle / "evidence" / "samples" / "M4-V20-U-01").mkdir(
            parents=True
        )
        self.plan_path = self.bundle / "plan" / "checked-plan.json"
        self.plan_path.write_bytes(CHECKED_PLAN.read_bytes())
        self.environment_path = self.bundle / "bindings" / "environment.json"
        self.environment_path.write_bytes(b'{"binding":"test"}\n')
        self.plan, self.plan_hash = load_checked_plan()
        env_hash = hashlib.sha256(self.environment_path.read_bytes()).hexdigest()
        condition = self.plan["conditions"][0]
        self.invocation = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_invocation",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": "M4-V20-U-01",
            "condition_id": "V20-U",
            "phase_id": "creator",
            "phase_ordinal": 1,
            "worker_run_id": "M4-V20-U-01-CREATOR",
            "worker_ordinal": 1,
            "nonce": "0123456789abcdef0123456789abcdef",
            "campaign_run_id": "20260725T120000Z-m4-test",
            "producer_commit": "0" * 40,
            "repository_url": self.plan["repository_url"],
            "remote_ref": self.plan["remote_ref"],
            "repository_root": str(self.repository.resolve()),
            "plan_path": "plan/checked-plan.json",
            "plan_sha256": self.plan_hash,
            "environment_path": "bindings/environment.json",
            "environment_sha256": env_hash,
            "provider_sha256": "1" * 64,
            "stage_root": str(self.bundle.resolve()),
            "output_root": str(self.output.resolve()),
            "database_path": "evidence/samples/M4-V20-U-01/creator.mdb",
            "result_path": "evidence/samples/M4-V20-U-01/creator-result.json",
            "phase_contract": {
                "kind": "creator",
                "method": self.plan["design"]["creation_method"],
                "locale": self.plan["design"]["locale"],
                "version_option": condition["version_option"],
                "version_api_value": condition["version_api_value"],
                "encryption_option": condition["encryption_option"],
                "encryption_api_value": condition["encryption_api_value"],
                "create_option_value": condition["create_option_value"],
                "compact_database_used": False,
                "expected_dao_version": condition["expected_dao_version"],
            },
            "created_at_utc": "2026-07-25T12:00:00-04:00",
            "bindings_verified_before_com": True,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _reopen_invocation(
        self, *, destination_sha: str | None = None, completed: str = "2026-07-25T15:59:00Z"
    ) -> dict[str, object]:
        payload = b"R" * 2048
        database = (
            self.bundle / "evidence" / "samples" / "M4-V20-U-01" / "reopen.mdb"
        )
        database.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        clone = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_clone_log",
            "experiment_id": "DAO-M4-HEADER-DISCRIMINATOR-001",
            "sample_id": "M4-V20-U-01",
            "started_at_utc": "2026-07-25T15:58:00Z",
            "completed_at_utc": completed,
            "source_path": "evidence/samples/M4-V20-U-01/creator.mdb",
            "destination_path": "evidence/samples/M4-V20-U-01/reopen.mdb",
            "source_bytes": 2048,
            "destination_bytes": 2048,
            "source_sha256_before_clone": digest,
            "source_sha256_after_clone": digest,
            "destination_sha256": destination_sha or digest,
            "source_file_identity": {
                "volume_serial_number": "12345678",
                "file_index": "0000000000000001",
                "link_count": 1,
            },
            "destination_file_identity": {
                "volume_serial_number": "12345678",
                "file_index": "0000000000000002",
                "link_count": 1,
            },
            "all_hashes_equal": True,
            "same_volume": True,
            "distinct_file_identity": True,
            "no_hardlink": True,
            "reparse_free": True,
            "completed_before_reopen_com": True,
            "status": "pass",
        }
        clone_path = (
            self.bundle / "evidence" / "samples" / "M4-V20-U-01" / "clone.json"
        )
        clone_path.write_text(json.dumps(clone), encoding="utf-8")
        clone_hash = hashlib.sha256(clone_path.read_bytes()).hexdigest()
        invocation = copy.deepcopy(self.invocation)
        invocation.update(
            {
                "phase_id": "reopen",
                "phase_ordinal": 2,
                "worker_run_id": "M4-V20-U-01-REOPEN",
                "worker_ordinal": 2,
                "nonce": "fedcba9876543210fedcba9876543210",
                "database_path": "evidence/samples/M4-V20-U-01/reopen.mdb",
                "result_path": "evidence/samples/M4-V20-U-01/reopen-result.json",
                "phase_contract": {
                    "kind": "reopen",
                    "expected_dao_version": "2.0",
                    "pre_com_database_bytes": 2048,
                    "pre_com_database_sha256": digest,
                    "clone_log": {
                        "path": "evidence/samples/M4-V20-U-01/clone.json",
                        "sha256": clone_hash,
                    },
                },
                "created_at_utc": "2026-07-25T16:00:00Z",
            }
        )
        return invocation

    def test_valid_creator_preflight(self) -> None:
        validate_invocation_document(
            self.invocation,
            self.plan,
            self.plan_hash,
            self.bundle,
            preflight=True,
        )

    def test_worker_ordinal_projection_is_enforced(self) -> None:
        self.invocation["worker_ordinal"] = 2
        with self.assertRaisesRegex(ValidationError, "worker_ordinal"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_reopen_clone_destination_binding_is_enforced(self) -> None:
        invocation = self._reopen_invocation(destination_sha="f" * 64)
        with self.assertRaisesRegex(ValidationError, "pre_com_database_sha256"):
            validate_invocation_document(
                invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_reopen_clone_must_precede_invocation_creation(self) -> None:
        invocation = self._reopen_invocation(completed="2026-07-25T16:01:00Z")
        with self.assertRaisesRegex(ValidationError, "completed after"):
            validate_invocation_document(
                invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_creator_refuses_preexisting_database(self) -> None:
        database = resolve_bundle_path(
            self.bundle, self.invocation["database_path"]
        )
        database.write_bytes(b"x")
        with self.assertRaisesRegex(ValidationError, "must not preexist"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_bundle_locator_cannot_escape(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unsafe path"):
            resolve_bundle_path(self.bundle, "evidence/../outside.json")

    def test_output_root_cannot_be_inside_stage(self) -> None:
        self.invocation["output_root"] = str((self.bundle / "publish").resolve())
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_noncanonical_absolute_path_is_rejected(self) -> None:
        self.invocation["repository_root"] = (
            f"{self.repository}/../{self.repository.name}"
        )
        with self.assertRaisesRegex(ValidationError, "noncanonical"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_stage_cannot_overlap_repository(self) -> None:
        self.invocation["repository_root"] = str(self.bundle.parent.resolve())
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )

    def test_output_ancestor_cannot_contain_repository(self) -> None:
        self.invocation["output_root"] = str(self.bundle.parent.resolve())
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_invocation_document(
                self.invocation,
                self.plan,
                self.plan_hash,
                self.bundle,
                preflight=True,
            )


class BundleTopologyTests(unittest.TestCase):
    def test_hard_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.json"
            second = root / "second.json"
            first.write_bytes(b"{}\n")
            try:
                second.hardlink_to(first)
            except OSError:
                self.skipTest("hard links unavailable")
            with self.assertRaisesRegex(ValidationError, "hard links"):
                discover_bundle(root)

    def test_each_reopen_uses_its_own_clone_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b"D" * 2048
            digest = hashlib.sha256(payload).hexdigest()
            prefix_digest = digest
            records = []
            index = {}
            for record_number in (1, 2):
                phases = {}
                for phase in ("creator", "reopen"):
                    database_path = f"db/r{record_number}-{phase}.mdb"
                    prefix_path = f"prefix/r{record_number}-{phase}.bin"
                    database = root / database_path
                    prefix = root / prefix_path
                    database.parent.mkdir(exist_ok=True)
                    prefix.parent.mkdir(exist_ok=True)
                    database.write_bytes(payload)
                    prefix.write_bytes(payload)
                    post = {
                        "database_path": database_path,
                        "database_bytes": 2048,
                        "database_sha256": digest,
                        "prefix_path": prefix_path,
                        "prefix_sha256": prefix_digest,
                    }
                    phase_row = {
                        "phase_id": phase,
                        "post_close_file_observations": post,
                    }
                    if phase == "reopen":
                        phase_row["pre_com_file_binding"] = {
                            "database_path": database_path,
                            "database_bytes": 2048,
                            "database_sha256": "a" * 64,
                        }
                    phases[phase] = phase_row
                    index[database_path] = {
                        "role": "database",
                        "size_bytes": 2048,
                        "sha256": digest,
                    }
                    index[prefix_path] = {"role": "prefix"}
                records.append(
                    {
                        "phases": phases,
                        "controller_clone": {
                            "destination_path": phases["reopen"][
                                "post_close_file_observations"
                            ]["database_path"],
                            "destination_bytes": 2048,
                            "destination_sha256": (
                                "b" * 64 if record_number == 1 else "a" * 64
                            ),
                        },
                    }
                )
            plan = {
                "bounds": {
                    "max_database_artifacts": 4,
                    "max_database_bytes": 2048,
                    "max_validator_database_reads_per_run": 4,
                    "max_validator_database_read_bytes_per_run": 8192,
                    "max_prefix_artifacts": 4,
                    "max_total_prefix_bytes": 8192,
                }
            }
            with self.assertRaisesRegex(ValidationError, "pre-COM clone hash"):
                _validate_databases_and_prefixes(root, plan, index, records)


if __name__ == "__main__":
    unittest.main()
