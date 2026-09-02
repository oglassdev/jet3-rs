#!/usr/bin/env python3
"""Focused tests for the preregistered issue #152 analyzer."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("extended_names", SCRIPTS / "extended_names.py")
assert SPEC and SPEC.loader
names = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(names)
PLAN = "a" * 64


def attempts_for(checkpoint: str) -> list[dict]:
    specs = names.rejection_specs() if checkpoint == "reject" else names.batch_specs(int(checkpoint[1:]))
    result = []
    for spec in specs:
        created = checkpoint != "reject" or spec["role"] == "ascii_control"
        result.append(
            {
                "role": spec["role"],
                "name": spec["name"],
                "name_utf16le_hex": spec["name"].encode("utf-16le").hex(),
                "inserted_bytes": spec["inserted"],
                "created": created,
                "failure_operation": None if created else "tabledefs_append",
                "error": None if created else "DAO rejected the control",
            }
        )
    return result


def dao_for(attempts: list[dict]) -> dict:
    created = [attempt for attempt in attempts if attempt["created"]]
    return {
        "tabledefs": [
            {
                "ordinal": index,
                "name": attempt["name"],
                "fields": [{"ordinal": 0, "name": "Id", "type": 4, "size": 4}],
                "indexes": [],
            }
            for index, attempt in enumerate(created)
        ]
    }


def fake_keys(data: bytes) -> list[dict]:
    checkpoint = names.CHECKPOINT_NAMES[data[0]]
    specs = names.rejection_specs() if checkpoint == "reject" else names.batch_specs(int(checkpoint[1:]))
    result = []
    for spec in specs:
        if checkpoint == "reject" and spec["role"] != "ascii_control":
            continue
        contribution = bytes((value - 0x7E) & 0xFF for value in spec["inserted"])
        primary = names.ascii_primary(spec["prefix"]) + contribution + names.ascii_primary(spec["suffix"])
        result.append(
            {
                "id": len(result) + 1,
                "name": spec["name"],
                "key_hex": primary.hex() + "00",
                "parent_id": 0x80000000,
                "primary_hex": primary.hex(),
                "secondary_nibbles": [],
                "row_page": 2,
                "row_slot": len(result),
            }
        )
    return result


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.replicas = []
        for replica in range(1, 4):
            checkpoints = []
            empty = bytes(names.PAGE_BYTES)
            empty_sha = hashlib.sha256(empty).hexdigest()
            for index, checkpoint in enumerate(names.CHECKPOINT_NAMES):
                data = bytes([index]) + bytes(names.PAGE_BYTES - 1)
                if checkpoint == "empty":
                    attempts = []
                    metadata = {"tabledefs": []}
                    before = None
                else:
                    attempts = attempts_for(checkpoint)
                    metadata = dao_for(attempts)
                    before = {"size": len(empty), "sha256": empty_sha}
                filename = names.expected_database(replica, checkpoint)
                (root / filename).write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                checkpoints.append(
                    {
                        "name": checkpoint,
                        "database": filename,
                        "size": len(data),
                        "sha256": digest,
                        "size_after_metadata": len(data),
                        "sha256_after_metadata": digest,
                        "arm_before": before,
                        "attempts": attempts,
                        "dao": metadata,
                    }
                )
            self.replicas.append(
                {
                    "replica": replica,
                    "status": "pass",
                    "error": None,
                    "mutation_started": True,
                    "phase": "complete",
                    "checkpoints": checkpoints,
                    "recovery": [],
                }
            )
        self.document = {
            "document_type": names.DOCUMENT_TYPE,
            "development_only": True,
            "plan_sha256": PLAN,
            "run_id": "20260902T120000Z-dev-dao",
            "status": "pass",
            "replicas": self.replicas,
        }

    def write(self) -> Path:
        path = self.root / "extended-names-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class ExtendedNamesTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture):
        output = fixture.root / "extended-names-report.json"
        with mock.patch.object(names.schema, "catalog_name_keys", side_effect=fake_keys):
            report = names.evaluate(fixture.write(), PLAN, output)
        self.assertEqual(output.read_bytes(), names.canonical_bytes(report))
        return report

    def test_inventory_covers_every_defined_cp1252_byte_in_bounded_batches(self) -> None:
        self.assertEqual(len(names.DEFINED_BYTES), 123)
        self.assertEqual(len(names.BATCHES), 41)
        self.assertTrue(all(len(batch) == 3 for batch in names.BATCHES))
        self.assertEqual(set(range(0x80, 0x100)) - set(names.DEFINED_BYTES), set(names.UNDEFINED_SLOTS))
        self.assertEqual(names.names_for(0xFF)[-2]["inserted"], [0xFF, 0x80])

    def test_producer_isolates_working_files_and_names_cleanup_phases(self) -> None:
        producer = (SCRIPTS / "dev" / "ExtendedNames.DevJob.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $RunRoot "_working"', producer)
        self.assertIn('$state.phase = "cleanup_$activeName"', producer)
        self.assertIn('$state.phase = "cleanup_reject"', producer)
        self.assertIn('$state.phase = "cleanup_complete"', producer)
        self.assertIn("if ($recoveryEligible -and", producer)

    def test_accepts_complete_replicated_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(Fixture(Path(directory)))
            self.assertEqual(report["status"], "accepted")
            self.assertEqual(len(report["questions"]["coverage"]["bytes"]), 123)
            controls = report["questions"]["coverage"]["rejection_controls"]
            self.assertTrue(controls["ascii_control"]["created"])
            self.assertFalse(controls["boundary_7f"]["created"])
            self.assertEqual(controls["boundary_7f"]["failure_operation"], "tabledefs_append")

    def test_exact_pair_order_and_position_answers_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(Fixture(Path(directory)))
            byte_80 = report["questions"]["secondary_order"]["bytes"]["80"]
            self.assertTrue(byte_80["singleton_primary_position_independent"])
            self.assertTrue(byte_80["repeat_primary_is_two_singletons"])
            self.assertTrue(byte_80["forward_primary_is_ordered_singletons"])
            self.assertTrue(byte_80["reverse_primary_is_ordered_singletons"])

    def test_replica_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[2]["checkpoints"][1]["attempts"][1]["created"] = False
            fixture.replicas[2]["checkpoints"][1]["attempts"][1]["error"] = "rejected"
            fixture.replicas[2]["checkpoints"][1]["attempts"][1]["failure_operation"] = "tabledefs_append"
            fixture.replicas[2]["checkpoints"][1]["dao"] = dao_for(fixture.replicas[2]["checkpoints"][1]["attempts"])
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

    def test_failure_operation_is_retained_and_replica_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[2]["checkpoints"][-1]["attempts"][1]["failure_operation"] = "create_tabledef"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            retained = report["replicas"][2]["attempts"]["reject"][1]
            self.assertEqual(retained["failure_operation"], "create_tabledef")

    def test_decode_failure_and_changed_metadata_are_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            output = fixture.root / "extended-names-report.json"
            with mock.patch.object(
                names.schema,
                "catalog_name_keys",
                side_effect=names.schema.DecodeError("bad catalog key"),
            ):
                report = names.evaluate(fixture.write(), PLAN, output)
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("recorded grammar", report["questions"]["coverage"]["reason"])
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            checkpoint = fixture.replicas[0]["checkpoints"][1]
            checkpoint["sha256"] = "b" * 64
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("metadata access changed", report["questions"]["coverage"]["reason"])

    def test_ascii_context_mismatch_is_scientific_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))

            def bad_ascii(data: bytes):
                result = fake_keys(data)
                if data[0] == 1:
                    result[0]["primary_hex"] = "ff"
                return result

            output = fixture.root / "extended-names-report.json"
            with mock.patch.object(names.schema, "catalog_name_keys", side_effect=bad_ascii):
                report = names.evaluate(fixture.write(), PLAN, output)
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("recorded grammar", report["questions"]["coverage"]["reason"])

    def test_accepted_rejection_control_preserves_key_without_mapping_claim(self) -> None:
        attempts = attempts_for("reject")
        attempts[1].update(created=True, failure_operation=None, error=None)
        specs = names.rejection_specs()
        primary = names.ascii_primary(specs[1]["prefix"]) + b"\xaa" + names.ascii_primary(specs[1]["suffix"])
        keys = fake_keys(bytes([42]) + bytes(names.PAGE_BYTES - 1))
        keys.append(
            {
                "id": 99,
                "name": specs[1]["name"],
                "key_hex": "deadbeef",
                "parent_id": 7,
                "primary_hex": primary.hex(),
                "secondary_nibbles": [3, 5],
                "row_page": 9,
                "row_slot": 2,
            }
        )
        with mock.patch.object(names.schema, "catalog_name_keys", return_value=keys):
            arm = names.analyze_arm(bytes(names.PAGE_BYTES), "reject", attempts, dao_for(attempts))
        control = arm["forms"][1]
        self.assertEqual(control["key_hex"], "deadbeef")
        self.assertEqual(control["primary_hex"], primary.hex())
        self.assertEqual(control["secondary_nibbles"], [3, 5])
        self.assertNotIn("isolated_primary_hex", control)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                reject = replica["checkpoints"][-1]
                reject["attempts"][1].update(created=True, failure_operation=None, error=None)
                reject["dao"] = dao_for(reject["attempts"])

            def accepted_control_keys(data: bytes):
                result = fake_keys(data)
                if data[0] == 42:
                    result.append(keys[-1])
                return result

            output = fixture.root / "extended-names-report.json"
            with mock.patch.object(names.schema, "catalog_name_keys", side_effect=accepted_control_keys):
                report = names.evaluate(fixture.write(), PLAN, output)
            self.assertEqual(
                report["questions"]["coverage"]["rejection_controls"]["boundary_7f"]["key_hex"],
                "deadbeef",
            )

    def test_metadata_bounds_and_real_index_shapes_are_validated(self) -> None:
        system = {
            "tabledefs": [
                {
                    "ordinal": 0,
                    "name": "MSysObjects",
                    "fields": [{"ordinal": 0, "name": "Id", "type": 4, "size": 4}],
                    "indexes": [{"ordinal": 0, "name": "PrimaryKey", "primary": True, "unique": True}],
                }
            ]
        }
        self.assertEqual(names.user_tables(system), set())
        malformed = copy.deepcopy(system)
        malformed["tabledefs"][0]["indexes"][0]["ordinal"] = 1
        with self.assertRaisesRegex(names.ValidationError, "index metadata value"):
            names.user_tables(malformed)
        user = dao_for(attempts_for("b00"))
        user["tabledefs"][0]["indexes"] = [{"ordinal": 0, "name": "I", "primary": False, "unique": False}]
        with self.assertRaisesRegex(names.schema.DecodeError, "schema differs"):
            names.user_tables(user)

    def test_post_mutation_failed_prefix_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"
                replica["error"] = "bounded DAO failure"
                replica["phase"] = "append_b01"
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

    def test_capture_empty_recovery_and_cleanup_failures_are_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            for path in root.glob("*.mdb"):
                path.unlink()
            for replica in fixture.replicas:
                data = bytes(names.PAGE_BYTES)
                filename = names.expected_database(replica["replica"], "empty")
                (root / filename).write_bytes(data)
                replica.update(
                    status="fail",
                    error="capture failed after mutation",
                    mutation_started=True,
                    phase="capture_empty",
                    checkpoints=[],
                    recovery=[
                        {
                            "name": "empty",
                            "database": filename,
                            "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    ],
                )
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (root / checkpoint["database"]).unlink()
                replica.update(
                    status="fail",
                    error="working-file cleanup failed",
                    phase="cleanup_b00",
                    checkpoints=replica["checkpoints"][:2],
                )
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                replica.update(status="fail", error="final cleanup failed", phase="cleanup_complete")
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0].update(
                status="fail",
                error="final cleanup failed",
                phase="cleanup_complete",
                recovery=[
                    {
                        "name": "reject",
                        "database": names.expected_database(1, "reject"),
                        "size": names.PAGE_BYTES,
                        "sha256": "a" * 64,
                    }
                ],
            )
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(names.ValidationError, "complete replica"):
                self.evaluate(fixture)

    def test_failed_prefix_catalog_corruption_is_decode_error_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (root / checkpoint["database"]).unlink()
                replica.update(
                    status="fail",
                    error="stopped after retained b00",
                    phase="append_b01",
                    checkpoints=replica["checkpoints"][:2],
                )
            fixture.document["status"] = "fail"

            def corrupt_prefix(data: bytes):
                if data[0] == 1:
                    raise names.schema.DecodeError("corrupt retained prefix key")
                return fake_keys(data)

            output = root / "extended-names-report.json"
            with mock.patch.object(names.schema, "catalog_name_keys", side_effect=corrupt_prefix):
                report = names.evaluate(fixture.write(), PLAN, output)
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("corrupt retained prefix key", report["replicas"][0]["decode_error"])

    def test_failed_prefix_still_validates_attempts_metadata_and_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"; replica["error"] = "stopped"; replica["phase"] = "append_b01"
            fixture.document["status"] = "fail"
            fixture.replicas[0]["checkpoints"][1]["attempts"][0]["failure_operation"] = "open_database"
            with self.assertRaisesRegex(names.ValidationError, "successful attempt carries"):
                self.evaluate(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"; replica["error"] = "stopped"; replica["phase"] = "append_b01"
            fixture.document["status"] = "fail"
            fixture.replicas[0]["checkpoints"][1]["dao"]["tabledefs"][0]["ordinal"] = 2
            with self.assertRaisesRegex(names.ValidationError, "table identity"):
                self.evaluate(fixture)

    def test_rejects_pre_mutation_abort_and_bad_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.document["replicas"] = [{"replica": 1, "status": "fail", "error": "no provider", "mutation_started": False, "phase": "before_create_database", "checkpoints": [], "recovery": []}]
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(names.ValidationError, "before the first DAO mutation"):
                self.evaluate(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["status"] = "fail"; fixture.replicas[0]["error"] = "bad"; fixture.replicas[0]["phase"] = "append_b02"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(names.ValidationError, "checkpoint prefix"):
                self.evaluate(fixture)

    def test_rejects_non_integer_replica_identities(self) -> None:
        for invalid in (True, 1.0):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                fixture.replicas[0]["replica"] = invalid
                with self.assertRaisesRegex(names.ValidationError, "replica state"):
                    self.evaluate(fixture)

    def test_rejects_attempt_corruption_and_extra_mdb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["checkpoints"][1]["attempts"][1]["inserted_bytes"] = [0x81]
            with self.assertRaisesRegex(names.ValidationError, "generated inventory"):
                self.evaluate(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory)); (Path(directory) / "extra.mdb").write_bytes(bytes(names.PAGE_BYTES))
            with self.assertRaisesRegex(names.ValidationError, "retained MDB inventory"):
                self.evaluate(fixture)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            original = root / names.expected_database(1, "empty")
            (root / original.name.upper()).write_bytes(original.read_bytes())
            with self.assertRaisesRegex(names.ValidationError, "case-folded"):
                self.evaluate(fixture)

    def test_rejects_symlinked_job_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = Fixture(root)
            real = fixture.write()
            link = root / "linked-job-result.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(names.ValidationError, "regular non-link"):
                names.evaluate(link, PLAN, root / "report.json")


if __name__ == "__main__":
    unittest.main()
