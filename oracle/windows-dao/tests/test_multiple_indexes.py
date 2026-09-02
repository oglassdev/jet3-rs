#!/usr/bin/env python3
"""Focused tests for the preregistered issue #150 analyzer."""

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
SPEC = importlib.util.spec_from_file_location("multiple_indexes", SCRIPTS / "multiple_indexes.py")
assert SPEC and SPEC.loader
multiple = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(multiple)


PLAN = "a" * 64


def dao(scenario: str | None) -> dict:
    tables = []
    if scenario is not None:
        expected = multiple.EXPECTED[scenario]
        tables.append(
            {
                "ordinal": 0,
                "name": expected["table"],
                "fields": copy.deepcopy(multiple.FIELDS),
                "indexes": copy.deepcopy(expected["indexes"]),
            }
        )
    return {"tabledefs": tables}


def fake_analysis(data: bytes, root_shift: int = 0) -> dict:
    marker = data[0]
    if marker == 0:
        return {
            "tables": {},
            "pages": [
                {"page": page, "role": "base", "owners": [], "tag": 0}
                for page in range(len(data) // 2048)
            ],
        }
    scenario = multiple.SCENARIOS[marker - 1]
    expected = multiple.EXPECTED[scenario]
    logical = []
    for name in sorted(entry["name"] for entry in expected["indexes"]):
        physical = next(position for position, entry in enumerate(expected["indexes"]) if entry["name"] == name)
        logical.append(
            {
                "class": 1 if expected["indexes"][physical]["primary"] else 0,
                "name": name,
                "physical_index": physical,
            }
        )
    physical = []
    for ordinal, entry in enumerate(expected["indexes"]):
        keys = []
        for field in entry["fields"]:
            column = next(position for position, value in enumerate(multiple.FIELDS) if value["name"] == field["name"])
            keys.append({"column": column, "direction": 0 if field["descending"] else 1})
        physical.append(
            {
                "entry_count": 0,
                "flags": (1 if entry["unique"] else 0) | (8 if entry["required"] else 0),
                "index": ordinal,
                "keys": keys,
                "map": {"page": 3, "row": 2 + ordinal},
                "root": 4 + ordinal + root_shift,
            }
        )
    definition = {
        "columns": [{"name": field["name"], "type": "Long"} for field in multiple.FIELDS],
        "logical_indexes": logical,
        "maps": {"owned": {"page": 3, "row": 0}, "available": {"page": 3, "row": 1}},
        "pages": [2],
        "physical_indexes": physical,
        "root": 2,
        "row_count": 0,
    }
    count = len(data) // 2048
    pages = []
    for page in range(count):
        if page < 2:
            role = "base"
        elif page == 2:
            role = "definition_root"
        elif page == 3:
            role = "map_rows"
        elif page == count - 1:
            role = "long_value"
        else:
            role = "index_root"
        tag = 4 if role == "index_root" else 2 if role == "definition_root" else 1
        pages.append(
            {"page": page, "role": role, "owners": [expected["table"]], "tag": tag}
        )
    return {
        "tables": {2: {"definition": definition, "flags": 0, "name": expected["table"]}},
        "pages": pages,
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.replicas = []
        for replica in range(1, 4):
            checkpoints = []
            empty = bytes(2 * 2048)
            empty_digest = hashlib.sha256(empty).hexdigest()
            checkpoints.append(self.checkpoint(replica, "empty", empty, None, dao(None)))
            for marker, scenario in enumerate(multiple.SCENARIOS, 1):
                pages = 12
                image = bytes([marker]) + bytes(pages * 2048 - 1)
                checkpoints.append(
                    self.checkpoint(
                        replica,
                        scenario,
                        image,
                        {"size": len(empty), "sha256": empty_digest},
                        dao(scenario),
                    )
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
            "document_type": multiple.DOCUMENT_TYPE,
            "development_only": True,
            "plan_sha256": PLAN,
            "run_id": "20260902T120000Z-dev-dao",
            "status": "pass",
            "replicas": self.replicas,
        }

    def checkpoint(self, replica: int, name: str, data: bytes, arm_before: dict | None, metadata: dict) -> dict:
        filename = f"multiple-indexes-r{replica}-{name}.mdb"
        (self.root / filename).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return {
            "name": name,
            "database": filename,
            "size": len(data),
            "size_after_metadata": len(data),
            "sha256": digest,
            "sha256_after_metadata": digest,
            "arm_before": arm_before,
            "dao": metadata,
        }

    def write(self) -> Path:
        path = self.root / "multiple-indexes-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class MultipleIndexesTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture, side_effect=fake_analysis):
        result = fixture.write()
        output = fixture.root / "multiple-indexes-report.json"
        with (
            mock.patch.object(multiple.catalog, "analyze_checkpoint", side_effect=side_effect),
            mock.patch.object(
                multiple.catalog,
                "_locator_pages",
                side_effect=lambda _data, locator, _what: {locator["page"]},
            ),
            mock.patch.object(
                multiple,
                "created_lvprop",
                side_effect=lambda _data, _table: {
                    "header_hex": "",
                    "length": 1,
                    "page": 11,
                    "payload_sha256": "0" * 64,
                    "row": 0,
                },
            ),
            mock.patch.object(multiple, "validate_empty_leaf"),
        ):
            report = multiple.evaluate(result, PLAN, output)
        self.assertEqual(output.read_bytes(), multiple.canonical_bytes(report))
        return report

    def test_accepts_exact_replicated_layout_and_reports_both_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "accepted")
            layout = report["questions"]["index_layout"]["scenarios"]["three"]
            self.assertEqual(layout["physical_ordinal_order"], ["ZPrimary", "MUniqueX", "ASecondx"])
            self.assertEqual(layout["logical_name_sorted_order"], ["ASecondx", "MUniqueX", "ZPrimary"])
            self.assertEqual(layout["physical_indexes"][0]["mapped_pages"], [3])
            self.assertEqual(len(report["replicas"][0]["files"]), 5)
            self.assertEqual(
                report["questions"]["page_assignment"]["scenarios"]["one"][
                    "lvprop"
                ]["page"],
                11,
            )

    def test_fully_decoded_unanticipated_root_layout_remains_answered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            report = self.evaluate(fixture, lambda data: fake_analysis(data, root_shift=2))
            self.assertEqual(report["status"], "accepted")
            roots = report["questions"]["index_layout"]["scenarios"]["two"]["physical_indexes"]
            self.assertEqual([entry["root"] for entry in roots], [6, 7])

    def test_definition_continuation_is_outside_the_experiment_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))

            def with_continuation(data: bytes) -> dict:
                analysis = fake_analysis(data)
                if data[0]:
                    analysis["tables"][2]["definition"]["pages"] = [2, 10]
                return analysis

            report = self.evaluate(fixture, with_continuation)
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn(
                "continuation page",
                report["replicas"][0]["decode_error"],
            )

    def test_replica_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            calls = 0

            def differing(data: bytes) -> dict:
                nonlocal calls
                calls += 1
                return fake_analysis(data, root_shift=1 if calls > 8 else 0)

            report = self.evaluate(fixture, differing)
            self.assertEqual(report["status"], "no_outcome")
            self.assertTrue(all(question["status"] == "no_outcome" for question in report["questions"].values()))

    def test_rejects_arm_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["checkpoints"][1]["arm_before"]["sha256"] = "b" * 64
            with self.assertRaisesRegex(multiple.AnalysisError, "arm identity"):
                self.evaluate(fixture)

    def test_rejects_extra_retained_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            (Path(directory) / "unexpected.mdb").write_bytes(bytes(2048))
            with self.assertRaisesRegex(multiple.AnalysisError, "retained MDB inventory"):
                self.evaluate(fixture)

    def test_failed_partial_prefix_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"
                replica["error"] = "bounded DAO failure"
                replica["phase"] = "two"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")

    def test_failed_replica_may_retain_one_bounded_recovery_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                recovery_source = replica["checkpoints"][2]
                replica["recovery"] = [
                    {
                        "name": "two",
                        "database": recovery_source["database"],
                        "size": recovery_source["size"],
                        "sha256": recovery_source["sha256"],
                    }
                ]
                for checkpoint in replica["checkpoints"][3:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"
                replica["error"] = "bounded DAO failure"
                replica["phase"] = "two"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")

    def test_failed_empty_capture_may_retain_empty_recovery_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                recovery_source = replica["checkpoints"][0]
                replica["recovery"] = [
                    {
                        "name": "empty",
                        "database": recovery_source["database"],
                        "size": recovery_source["size"],
                        "sha256": recovery_source["sha256"],
                    }
                ]
                for checkpoint in replica["checkpoints"][1:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = []
                replica["status"] = "fail"
                replica["error"] = "bounded checkpoint failure"
                replica["phase"] = "capture_empty"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")

    def test_failed_replica_phase_must_match_checkpoint_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["status"] = "fail"
            fixture.replicas[0]["error"] = "impossible producer state"
            fixture.replicas[0]["phase"] = "two"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(multiple.AnalysisError, "checkpoint prefix"):
                self.evaluate(fixture)

    def test_failed_replica_phase_must_match_mutation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for checkpoint in fixture.replicas[0]["checkpoints"][1:]:
                (Path(directory) / checkpoint["database"]).unlink()
            fixture.replicas[0]["checkpoints"] = fixture.replicas[0]["checkpoints"][:1]
            fixture.replicas[0]["status"] = "fail"
            fixture.replicas[0]["error"] = "impossible producer state"
            fixture.replicas[0]["mutation_started"] = False
            fixture.replicas[0]["phase"] = "copy_arms"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(multiple.AnalysisError, "started DAO mutation"):
                self.evaluate(fixture)

    def test_recovery_image_must_be_the_next_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[0]
            recovery_source = replica["checkpoints"][4]
            replica["recovery"] = [
                {
                    "name": "composite",
                    "database": recovery_source["database"],
                    "size": recovery_source["size"],
                    "sha256": recovery_source["sha256"],
                }
            ]
            for checkpoint in replica["checkpoints"][2:4]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = replica["checkpoints"][:2]
            replica["status"] = "fail"
            replica["error"] = "bounded DAO failure"
            replica["phase"] = "two"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(multiple.AnalysisError, "recovery artifact"):
                self.evaluate(fixture)

    def test_empty_leaf_validation_rejects_a_branched_root(self) -> None:
        page = bytearray(2048)
        page[0] = 4
        page[1] = 1
        page[2:4] = multiple.INDEX_ENTRY_AREA_LENGTH.to_bytes(2, "little")
        page[4:8] = (2).to_bytes(4, "little")
        multiple.validate_empty_leaf(bytes(page), 0, 2, "root")
        page[21] = 1
        with self.assertRaisesRegex(multiple.DecodeError, "branch marker"):
            multiple.validate_empty_leaf(bytes(page), 0, 2, "root")

    def test_created_lvprop_correlates_the_external_row(self) -> None:
        payload = b"abc"
        header = (0x40000003).to_bytes(4, "little") + bytes([0]) + (2).to_bytes(
            3, "little"
        ) + bytes(4)
        row = {
            "values": [
                "IdxOne",
                {"inline_length": 12, "long_value_header_hex": header.hex()},
            ]
        }
        page = bytearray(2048)
        page[0] = 1
        page[4:8] = b"LVAL"
        page[10:13] = payload
        with (
            mock.patch.object(multiple.catalog, "_discover_catalog", return_value=({}, [], [row])),
            mock.patch.object(
                multiple.catalog,
                "_ordinal",
                side_effect=lambda _definition, name: {"Name": 0, "LvProp": 1}[name],
            ),
            mock.patch.object(multiple.catalog, "_page", return_value=bytes(page)),
            mock.patch.object(
                multiple.catalog,
                "_row_directory",
                return_value=[{"hidden": False, "overflow": False, "start": 10, "end": 13}],
            ),
        ):
            observed = multiple.created_lvprop(bytes(3 * 2048), "IdxOne")
        self.assertEqual(observed["page"], 2)
        self.assertEqual(observed["payload_sha256"], hashlib.sha256(payload).hexdigest())

    def test_pre_mutation_abort_is_rejected_without_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for checkpoint in fixture.replicas[0]["checkpoints"]:
                (Path(directory) / checkpoint["database"]).unlink()
            fixture.document["replicas"] = [
                {
                    "replica": 1,
                    "status": "fail",
                    "error": "provider unavailable",
                    "mutation_started": False,
                    "phase": "create_database",
                    "checkpoints": [],
                    "recovery": [],
                }
            ]
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(multiple.AnalysisError, "first DAO mutation"):
                self.evaluate(fixture)

    def test_dao_direction_mismatch_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                replica["checkpoints"][3]["dao"]["tabledefs"][0]["indexes"][1]["fields"][0][
                    "descending"
                ] = False
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")

    def test_metadata_digest_change_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                replica["checkpoints"][1]["sha256"] = "b" * 64
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")


if __name__ == "__main__":
    unittest.main()
