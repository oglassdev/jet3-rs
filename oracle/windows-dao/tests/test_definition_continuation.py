#!/usr/bin/env python3
"""Focused tests for the preregistered issue #151 analyzer."""

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
SPEC = importlib.util.spec_from_file_location(
    "definition_continuation", SCRIPTS / "definition_continuation.py"
)
assert SPEC and SPEC.loader
continuation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(continuation)

PLAN = "a" * 64


def measurement(length: int) -> dict:
    divisible = length % continuation.PAGE_BYTES == 0
    pages = length // continuation.PAGE_BYTES if divisible else None
    failed = None
    if length < continuation.PAGE_BYTES:
        failed = "minimum_page_length"
    elif not divisible:
        failed = "page_alignment"
    elif pages > continuation.MAX_CHECKPOINT_PAGES:
        failed = "checkpoint_bound_exceeded"
    return {
        "raw_byte_length": length,
        "divisible_by_page_size": divisible,
        "page_count": pages,
        "failed_predicate": failed,
    }


def dao(scenario: str | None) -> dict:
    tables = []
    if scenario is not None:
        tables.append(
            {
                "ordinal": 0,
                "name": continuation.TABLE_NAMES[scenario],
                "fields": copy.deepcopy(continuation.expected_fields(scenario)),
            }
        )
    return {"tabledefs": tables}


def scenario_image(scenario: str) -> bytes:
    page_count = 28
    data = bytearray(page_count * continuation.PAGE_BYTES)
    data[1] = continuation.SCENARIOS.index(scenario) + 1
    pages = {
        "zero": [20],
        "one": [20, 24],
        "two": [20, 25, 23],
    }[scenario]
    for position, page in enumerate(pages):
        start = page * continuation.PAGE_BYTES
        data[start : start + 4] = b"\x02\x01VC"
        following = pages[position + 1] if position + 1 < len(pages) else 0
        data[start + 4 : start + 8] = following.to_bytes(4, "little")
    return bytes(data)


def fake_analysis(
    data: bytes,
    *,
    bad_count: bool = False,
    bad_length: bool = False,
    definition_free: bool = False,
    definition_free_unassigned: bool = False,
    free_attributed: bool = False,
    in_use_unassigned: bool = False,
    missing_role: bool = False,
    unassigned: bool = False,
) -> dict:
    marker = data[1]
    count = len(data) // continuation.PAGE_BYTES
    if marker == 0:
        return {
            "free_pages": [],
            "tables": {},
            "pages": [
                {"page": page, "role": "base", "owners": [], "tag": 0}
                for page in range(count)
            ],
        }
    scenario = continuation.SCENARIOS[marker - 1]
    pages = {
        "zero": [20],
        "one": [20, 24],
        "two": [20, 25, 23],
    }[scenario]
    if bad_count and scenario == "one":
        pages = [20]
    logical_length = {"zero": 66, "one": 2075, "two": 4105}[scenario]
    if bad_length and scenario == "one":
        logical_length += 1
    fields = continuation.expected_fields(scenario)
    definition = {
        "columns": [
            {"name": field["name"], "ordinal": field["ordinal"], "size": 4, "type": "Long"}
            for field in fields
        ],
        "logical_indexes": [],
        "logical_length": logical_length,
        "long_value_maps": [],
        "maps": {"owned": {"page": 21, "row": 0}, "available": {"page": 21, "row": 1}},
        "pages": pages,
        "physical_indexes": [],
        "root": 20,
        "row_count": 0,
    }
    owner = f"table 20 {continuation.TABLE_NAMES[scenario]}"
    output_pages = []
    for page in range(count):
        if page == 20:
            role = "definition_root"
        elif page in pages[1:]:
            role = "definition_continuation"
        elif page == 21:
            role = "map_rows"
        elif page >= 22:
            role = "long_value"
        else:
            role = "base"
        if (unassigned or in_use_unassigned) and scenario == "one" and page == 22:
            role = "unassigned"
        if definition_free_unassigned and scenario == "one" and page == 24:
            role = "unassigned"
        if missing_role and scenario == "one" and page == 22:
            continue
        output_pages.append(
            {
                "page": page,
                "role": role,
                "owners": [] if role == "unassigned" else [owner],
                "tag": 2 if page in pages else 1,
            }
        )
    return {
        "free_pages": (
            [24]
            if (definition_free or definition_free_unassigned) and scenario == "one"
            else [22]
            if (unassigned or free_attributed) and scenario == "one"
            else []
        ),
        "tables": {20: {"definition": definition, "flags": 0, "name": continuation.TABLE_NAMES[scenario]}},
        "pages": output_pages,
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.replicas = []
        for replica in range(1, 4):
            empty = bytes(20 * continuation.PAGE_BYTES)
            empty_digest = hashlib.sha256(empty).hexdigest()
            arm_baselines = [
                {
                    "name": scenario,
                    "size": len(empty),
                    "sha256": empty_digest,
                    "measurement": measurement(len(empty)),
                }
                for scenario in continuation.SCENARIOS
            ]
            checkpoints = [self.checkpoint(replica, "empty", empty, None, dao(None))]
            for scenario, baseline in zip(
                continuation.SCENARIOS, arm_baselines, strict=True
            ):
                checkpoints.append(
                    self.checkpoint(
                        replica,
                        scenario,
                        scenario_image(scenario),
                        {key: value for key, value in baseline.items() if key != "name"},
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
                    "arm_baselines": arm_baselines,
                    "failure_measurement": None,
                    "recovery": [],
                }
            )
        self.document = {
            "document_type": continuation.DOCUMENT_TYPE,
            "development_only": True,
            "plan_sha256": PLAN,
            "run_id": "20260902T120000Z-dev-dao",
            "status": "pass",
            "replicas": self.replicas,
        }

    def checkpoint(
        self,
        replica: int,
        name: str,
        data: bytes,
        arm_before: dict | None,
        metadata: dict,
    ) -> dict:
        filename = f"definition-continuation-r{replica}-{name}.mdb"
        (self.root / filename).write_bytes(data)
        digest_value = hashlib.sha256(data).hexdigest()
        return {
            "name": name,
            "database": filename,
            "size": len(data),
            "size_after_metadata": len(data),
            "sha256": digest_value,
            "sha256_after_metadata": digest_value,
            "measurement": measurement(len(data)),
            "measurement_after_metadata": measurement(len(data)),
            "arm_before": arm_before,
            "dao": metadata,
        }

    def write(self) -> Path:
        path = self.root / "definition-continuation-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class DefinitionContinuationTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture, analysis=fake_analysis, lvprop=None):
        output = fixture.root / "definition-continuation-report.json"
        if lvprop is None:
            lvprop = lambda *_args: {
                "appended_lval_pages": [],
                "declared_length": 1,
                "first_locator": None,
                "header_hex": "0" * 24,
                "inline_length": 13,
                "referenced_appended_lval_pages": [],
                "storage": "inline",
                "unreferenced_appended_lval_pages": [],
            }
        with (
            mock.patch.object(continuation.catalog, "analyze_checkpoint", side_effect=analysis),
            mock.patch.object(
                continuation.catalog,
                "_locator_pages",
                side_effect=lambda _data, _locator, _what: set(),
            ),
            mock.patch.object(
                continuation,
                "created_lvprop",
                side_effect=lvprop,
            ),
        ):
            report = continuation.evaluate(fixture.write(), PLAN, output)
        self.assertEqual(output.read_bytes(), continuation.canonical_bytes(report))
        return report

    def test_measurement_predicates_are_exact_and_ordered(self) -> None:
        limit = continuation.MAX_CHECKPOINT_PAGES * continuation.PAGE_BYTES
        self.assertEqual(
            continuation.validate_measurement(measurement(limit), "measurement"),
            measurement(limit),
        )
        self.assertEqual(measurement(0)["failed_predicate"], "minimum_page_length")
        self.assertEqual(measurement(limit + 1)["failed_predicate"], "page_alignment")
        self.assertIsNone(measurement(limit + 1)["page_count"])
        self.assertEqual(
            measurement(limit + continuation.PAGE_BYTES)["failed_predicate"],
            "checkpoint_bound_exceeded",
        )

    def test_accepts_exact_counts_and_nonconsecutive_chain_placement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(Fixture(Path(directory)))
            self.assertEqual(report["status"], "accepted")
            counts = report["questions"]["continuation_counts"]["scenarios"]
            self.assertEqual([counts[name]["continuation_count"] for name in continuation.SCENARIOS], [0, 1, 2])
            self.assertEqual(counts["two"]["definition_pages"], [20, 25, 23])
            chunks = report["questions"]["placement"]["scenarios"]["two"]["definition_chunks"]
            self.assertEqual([chunk["used"] for chunk in chunks], [2048, 2040, 17])
            counters = report["questions"]["counters"]["scenarios"]
            self.assertEqual(counters["zero"]["changed_offsets"], [1])

    def test_zero_control_is_exact_alpha_schema(self) -> None:
        self.assertEqual(continuation.TABLE_NAMES["zero"], "Alpha")
        self.assertEqual(
            continuation.expected_fields("zero"),
            [{"ordinal": 0, "name": "Id", "type": 4, "size": 4}],
        )
        self.assertEqual(continuation.EXPECTED_LOGICAL_LENGTHS["zero"], 66)

    def test_wrong_continuation_count_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, bad_count=True)
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("control requires", report["replicas"][0]["decode_error"])

    def test_replica_stable_unassigned_appended_page_is_answered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, unassigned=True)
            )
            self.assertEqual(report["status"], "accepted")
            appended = report["questions"]["placement"]["scenarios"]["one"][
                "appended_pages"
            ]
            self.assertEqual(
                [page for page in appended if page["role"] == "unassigned"],
                [
                    {
                        "delta_from_definition": 2,
                        "delta_from_empty": 2,
                        "globally_free": True,
                        "owners": [],
                        "page": 22,
                        "role": "unassigned",
                        "tag": 1,
                    }
                ],
            )

    def test_globally_free_attributed_page_is_answered_as_bounded_decoder_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def lvprop(_data, _analysis, table_name, _before_pages):
                unreferenced = [22] if table_name == "ContOneX" else []
                return {
                    "appended_lval_pages": unreferenced,
                    "declared_length": 1,
                    "first_locator": None,
                    "header_hex": "0" * 24,
                    "inline_length": 13,
                    "referenced_appended_lval_pages": [],
                    "storage": "inline",
                    "unreferenced_appended_lval_pages": unreferenced,
                }

            report = self.evaluate(
                Fixture(Path(directory)),
                lambda data: fake_analysis(data, free_attributed=True),
                lvprop,
            )
            self.assertEqual(report["status"], "accepted")
            appended = report["questions"]["placement"]["scenarios"]["one"][
                "appended_pages"
            ]
            self.assertEqual(
                next(page for page in appended if page["page"] == 22),
                {
                    "delta_from_definition": 2,
                    "delta_from_empty": 2,
                    "globally_free": True,
                    "owners": ["table 20 ContOneX"],
                    "page": 22,
                    "role": "long_value",
                    "tag": 1,
                },
            )
            self.assertEqual(
                report["questions"]["continuation_counts"]["scenarios"]["one"][
                    "lvprop"
                ]["unreferenced_appended_lval_pages"],
                [22],
            )

    def test_globally_free_referenced_lvprop_page_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def lvprop(_data, _analysis, table_name, _before_pages):
                referenced = [22] if table_name == "ContOneX" else []
                return {
                    "appended_lval_pages": referenced,
                    "declared_length": 1,
                    "first_locator": None,
                    "header_hex": "0" * 24,
                    "inline_length": 13,
                    "referenced_appended_lval_pages": referenced,
                    "storage": "inline",
                    "unreferenced_appended_lval_pages": [],
                }

            report = self.evaluate(
                Fixture(Path(directory)),
                lambda data: fake_analysis(data, free_attributed=True),
                lvprop,
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn(
                "LvProp references globally free appended LVAL page 22",
                report["replicas"][0]["decode_error"],
            )

    def test_missing_appended_page_role_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, missing_role=True)
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("lacks a page-role record", report["replicas"][0]["decode_error"])

    def test_in_use_unassigned_appended_page_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)),
                lambda data: fake_analysis(data, in_use_unassigned=True),
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("is not globally free", report["replicas"][0]["decode_error"])

    def test_globally_free_unassigned_definition_page_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)),
                lambda data: fake_analysis(data, definition_free_unassigned=True),
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("wrong role or owner", report["replicas"][0]["decode_error"])

    def test_attributed_definition_page_cannot_be_globally_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)),
                lambda data: fake_analysis(data, definition_free=True),
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("definition page 24 is globally free", report["replicas"][0]["decode_error"])

    def test_rejects_arm_identity_mismatch_and_extra_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["checkpoints"][1]["arm_before"]["sha256"] = "b" * 64
            with self.assertRaisesRegex(continuation.AnalysisError, "arm identity"):
                self.evaluate(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            (Path(directory) / "unexpected.mdb").write_bytes(bytes(2048))
            with self.assertRaisesRegex(continuation.AnalysisError, "retained MDB inventory"):
                self.evaluate(fixture)

    def test_ephemeral_subdirectory_does_not_change_retained_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            working = Path(directory) / "working-definition-continuation"
            working.mkdir()
            (working / "working-continuation-zero-r1.mdb").write_bytes(bytes(2048))
            self.assertEqual(self.evaluate(fixture)["status"], "accepted")

    def test_arm_baselines_are_exact_ordered_and_allow_a_partial_copy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["arm_baselines"][0]["name"] = "one"
            with self.assertRaisesRegex(continuation.AnalysisError, "ordered scenario prefix"):
                self.evaluate(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][1:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["arm_baselines"] = replica["arm_baselines"][:1]
                replica["status"] = "fail"
                replica["error"] = "copy of the one arm failed"
                replica["phase"] = "copy_arms"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(
                [entry["name"] for entry in report["replicas"][0]["arm_baselines"]],
                ["zero"],
            )

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[0]
            for checkpoint in replica["checkpoints"][1:]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = replica["checkpoints"][:1]
            replica["arm_baselines"] = replica["arm_baselines"][:2]
            replica["status"] = "fail"
            replica["error"] = "failure while capturing zero"
            replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(continuation.AnalysisError, "lacks all arm baselines"):
                self.evaluate(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            baseline = fixture.replicas[0]["arm_baselines"][0]
            baseline["size"] = 257 * continuation.PAGE_BYTES
            baseline["measurement"] = measurement(baseline["size"])
            with self.assertRaisesRegex(continuation.AnalysisError, "arm baseline size"):
                self.evaluate(fixture)

    def test_failed_prefix_and_recovery_are_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                recovery_source = replica["checkpoints"][2]
                replica["recovery"] = [
                    {
                        "name": "one",
                        "database": recovery_source["database"],
                        "size": recovery_source["size"],
                        "sha256": recovery_source["sha256"],
                        "measurement": recovery_source["measurement"],
                        "reason": "post_mutation_failure",
                        "interpreted": False,
                    }
                ]
                for checkpoint in replica["checkpoints"][3:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:2]
                replica["status"] = "fail"
                replica["error"] = "bounded DAO failure"
                replica["phase"] = "append_one"
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

    def test_failed_phase_must_match_checkpoint_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["status"] = "fail"
            fixture.replicas[0]["error"] = "impossible state"
            fixture.replicas[0]["phase"] = "append_one"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(continuation.AnalysisError, "checkpoint prefix"):
                self.evaluate(fixture)

    def test_later_pre_mutation_failure_after_global_mutation_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[1]
            for checkpoint in replica["checkpoints"]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = []
            replica["arm_baselines"] = []
            replica["status"] = "fail"
            replica["error"] = "CreateDatabase was not entered"
            replica["mutation_started"] = False
            replica["phase"] = "before_create_database"
            fixture.document["status"] = "fail"
            self.assertEqual(self.evaluate(fixture)["status"], "no_outcome")

    def test_before_create_database_rejects_started_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[1]
            for checkpoint in replica["checkpoints"]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = []
            replica["arm_baselines"] = []
            replica["status"] = "fail"
            replica["error"] = "impossible mutation state"
            replica["phase"] = "before_create_database"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(
                continuation.AnalysisError, "cannot have a started DAO mutation"
            ):
                self.evaluate(fixture)

    def test_failure_measurement_must_be_reachable_from_the_producer_phase(self) -> None:
        for phase, mutation_started, complete_inventory in (
            ("before_create_database", False, False),
            ("create_database", False, False),
            ("complete", True, True),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                replica = fixture.replicas[1]
                if not complete_inventory:
                    for checkpoint in replica["checkpoints"]:
                        (Path(directory) / checkpoint["database"]).unlink()
                    replica["checkpoints"] = []
                    replica["arm_baselines"] = []
                replica["status"] = "fail"
                replica["error"] = "fabricated measurement state"
                replica["mutation_started"] = mutation_started
                replica["phase"] = phase
                replica["failure_measurement"] = measurement(
                    257 * continuation.PAGE_BYTES
                )
                fixture.document["status"] = "fail"
                with self.assertRaisesRegex(
                    continuation.AnalysisError,
                    "failure_measurement is inconsistent with producer phase",
                ):
                    self.evaluate(fixture)

    def test_repeatable_final_arm_failure_remains_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            empty = bytes(20 * continuation.PAGE_BYTES)
            empty_digest = hashlib.sha256(empty).hexdigest()
            for replica in fixture.replicas:
                recovery = replica["checkpoints"][3]
                (Path(directory) / recovery["database"]).write_bytes(empty)
                replica["recovery"] = [
                    {
                        "name": "two",
                        "database": recovery["database"],
                        "size": len(empty),
                        "sha256": empty_digest,
                        "measurement": measurement(len(empty)),
                        "reason": "post_mutation_failure",
                        "interpreted": False,
                    }
                ]
                replica["checkpoints"] = replica["checkpoints"][:3]
                replica["status"] = "fail"
                replica["error"] = "DAO rejected the exact schema"
                replica["phase"] = "append_two"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(report["questions"]["producer_outcome"]["status"], "no_outcome")

    def test_checkpoint_bound_failure_retains_exact_512_page_uninterpreted_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            recovery_data = bytes(512 * continuation.PAGE_BYTES)
            recovery_digest = hashlib.sha256(recovery_data).hexdigest()
            observed = measurement(len(recovery_data))
            for replica in fixture.replicas:
                recovery = replica["checkpoints"][1]
                (Path(directory) / recovery["database"]).write_bytes(recovery_data)
                replica["recovery"] = [
                    {
                        "name": "zero",
                        "database": recovery["database"],
                        "size": len(recovery_data),
                        "sha256": recovery_digest,
                        "measurement": observed,
                        "reason": "checkpoint_bound_exceeded",
                        "interpreted": False,
                    }
                ]
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["failure_measurement"] = observed
                replica["status"] = "fail"
                replica["error"] = "checkpoint_bound_exceeded"
                replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            retained = report["replicas"][0]["files"][1]
            self.assertTrue(retained["recovery"])
            self.assertFalse(retained["interpreted"])
            self.assertEqual(retained["reason"], "checkpoint_bound_exceeded")
            self.assertEqual(retained["measurement"]["page_count"], 512)
            self.assertEqual(
                [entry["name"] for entry in report["replicas"][0]["arm_baselines"]],
                list(continuation.SCENARIOS),
            )

    def test_bound_recovery_requires_the_exact_failed_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            recovery_data = bytes(257 * continuation.PAGE_BYTES)
            observed = measurement(len(recovery_data))
            replica = fixture.replicas[0]
            recovery = replica["checkpoints"][1]
            (Path(directory) / recovery["database"]).write_bytes(recovery_data)
            replica["recovery"] = [
                {
                    "name": "zero",
                    "database": recovery["database"],
                    "size": len(recovery_data),
                    "sha256": hashlib.sha256(recovery_data).hexdigest(),
                    "measurement": observed,
                    "reason": "checkpoint_bound_exceeded",
                    "interpreted": False,
                }
            ]
            for checkpoint in replica["checkpoints"][2:]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = replica["checkpoints"][:1]
            replica["failure_measurement"] = measurement(
                258 * continuation.PAGE_BYTES
            )
            replica["status"] = "fail"
            replica["error"] = "checkpoint_bound_exceeded"
            replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(continuation.AnalysisError, "exact failed"):
                self.evaluate(fixture)

    def test_over_512_page_failure_records_measurement_without_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            observed = measurement(513 * continuation.PAGE_BYTES)
            for replica in fixture.replicas:
                for checkpoint in replica["checkpoints"][1:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["failure_measurement"] = observed
                replica["status"] = "fail"
                replica["error"] = "recovery bound exceeded"
                replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(
                report["replicas"][0]["failure_measurement"], observed
            )
            self.assertEqual(len(report["replicas"][0]["files"]), 1)

    def test_rejects_retained_513_page_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[0]
            recovery_data = bytes(513 * continuation.PAGE_BYTES)
            observed = measurement(len(recovery_data))
            recovery = replica["checkpoints"][1]
            (Path(directory) / recovery["database"]).write_bytes(recovery_data)
            replica["recovery"] = [
                {
                    "name": "zero",
                    "database": recovery["database"],
                    "size": len(recovery_data),
                    "sha256": hashlib.sha256(recovery_data).hexdigest(),
                    "measurement": observed,
                    "reason": "checkpoint_bound_exceeded",
                    "interpreted": False,
                }
            ]
            for checkpoint in replica["checkpoints"][2:]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = replica["checkpoints"][:1]
            replica["failure_measurement"] = observed
            replica["status"] = "fail"
            replica["error"] = "recovery bound exceeded"
            replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(continuation.AnalysisError, "recovery size"):
                self.evaluate(fixture)

    def test_rejects_undersized_and_unaligned_retained_recovery(self) -> None:
        cases = ((bytes(1024), "recovery size"), (bytes(continuation.PAGE_BYTES + 1), "exact sequence"))
        for recovery_data, message in cases:
            with self.subTest(size=len(recovery_data)), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                replica = fixture.replicas[0]
                recovery = replica["checkpoints"][1]
                (Path(directory) / recovery["database"]).write_bytes(recovery_data)
                replica["recovery"] = [
                    {
                        "name": "zero",
                        "database": recovery["database"],
                        "size": len(recovery_data),
                        "sha256": hashlib.sha256(recovery_data).hexdigest(),
                        "measurement": measurement(len(recovery_data)),
                        "reason": "post_mutation_failure",
                        "interpreted": False,
                    }
                ]
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["failure_measurement"] = measurement(len(recovery_data))
                replica["status"] = "fail"
                replica["error"] = "invalid recovery geometry"
                replica["phase"] = "capture_zero"
                fixture.document["status"] = "fail"
                with self.assertRaisesRegex(continuation.AnalysisError, message):
                    self.evaluate(fixture)

    def test_rejects_recovery_reason_mismatches(self) -> None:
        cases = (
            (20, "checkpoint_bound_exceeded", None, "in-bound recovery"),
            (257, "post_mutation_failure", "failed", "omits its checkpoint"),
        )
        for pages, reason, failure_kind, message in cases:
            with self.subTest(pages=pages, reason=reason), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory))
                replica = fixture.replicas[0]
                recovery_data = bytes(pages * continuation.PAGE_BYTES)
                observed = measurement(len(recovery_data))
                recovery = replica["checkpoints"][1]
                (Path(directory) / recovery["database"]).write_bytes(recovery_data)
                replica["recovery"] = [
                    {
                        "name": "zero",
                        "database": recovery["database"],
                        "size": len(recovery_data),
                        "sha256": hashlib.sha256(recovery_data).hexdigest(),
                        "measurement": observed,
                        "reason": reason,
                        "interpreted": False,
                    }
                ]
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["failure_measurement"] = observed if failure_kind else None
                replica["status"] = "fail"
                replica["error"] = "reason mismatch"
                replica["phase"] = "capture_zero"
                fixture.document["status"] = "fail"
                with self.assertRaisesRegex(continuation.AnalysisError, message):
                    self.evaluate(fixture)

    def test_rejects_post_metadata_measurement_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            checkpoint = fixture.replicas[0]["checkpoints"][0]
            checkpoint["measurement_after_metadata"] = measurement(
                checkpoint["size_after_metadata"] + continuation.PAGE_BYTES
            )
            with self.assertRaisesRegex(continuation.AnalysisError, "raw measurement"):
                self.evaluate(fixture)

    def test_recovery_bytes_are_never_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for replica in fixture.replicas:
                recovery = replica["checkpoints"][1]
                replica["recovery"] = [
                    {
                        "name": "zero",
                        "database": recovery["database"],
                        "size": recovery["size"],
                        "sha256": recovery["sha256"],
                        "measurement": recovery["measurement"],
                        "reason": "post_mutation_failure",
                        "interpreted": False,
                    }
                ]
                for checkpoint in replica["checkpoints"][2:]:
                    (Path(directory) / checkpoint["database"]).unlink()
                replica["checkpoints"] = replica["checkpoints"][:1]
                replica["status"] = "fail"
                replica["error"] = "post-mutation failure"
                replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            output = fixture.root / "definition-continuation-report.json"
            with mock.patch.object(
                continuation, "analyze_scenario", side_effect=AssertionError("decoded recovery")
            ) as analyze:
                report = continuation.evaluate(fixture.write(), PLAN, output)
            self.assertEqual(report["status"], "no_outcome")
            analyze.assert_not_called()

    def test_rejects_inconsistent_measurement_and_interpreted_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.replicas[0]["checkpoints"][0]["measurement"]["page_count"] += 1
            with self.assertRaisesRegex(continuation.AnalysisError, "page_count"):
                self.evaluate(fixture)

        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            replica = fixture.replicas[0]
            recovery = replica["checkpoints"][1]
            replica["recovery"] = [
                {
                    "name": "zero",
                    "database": recovery["database"],
                    "size": recovery["size"],
                    "sha256": recovery["sha256"],
                    "measurement": recovery["measurement"],
                    "reason": "post_mutation_failure",
                    "interpreted": True,
                }
            ]
            for checkpoint in replica["checkpoints"][2:]:
                (Path(directory) / checkpoint["database"]).unlink()
            replica["checkpoints"] = replica["checkpoints"][:1]
            replica["status"] = "fail"
            replica["error"] = "bounded DAO failure"
            replica["phase"] = "capture_zero"
            fixture.document["status"] = "fail"
            with self.assertRaisesRegex(continuation.AnalysisError, "uninterpreted"):
                self.evaluate(fixture)

    def test_wrong_logical_length_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, bad_length=True)
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("logical definition length", report["replicas"][0]["decode_error"])

    def test_definition_chunks_rejects_pointer_corruption(self) -> None:
        data = bytearray(3 * continuation.PAGE_BYTES)
        data[4:8] = (2).to_bytes(4, "little")
        with self.assertRaisesRegex(continuation.DecodeError, "pointer"):
            continuation.definition_chunks(bytes(data), [0, 1], 2050)

    def test_correlates_chained_lvprop_first_locator_without_assuming_placement(self) -> None:
        header = (16).to_bytes(4, "little") + bytes([0]) + (2).to_bytes(3, "little") + bytes(4)
        page = bytearray(continuation.PAGE_BYTES)
        page[0] = 1
        page[4:8] = b"LVAL"
        with (
            mock.patch.object(
                continuation.catalog,
                "_discover_catalog",
                return_value=({}, [], [{"values": ["ContTwoX", {"inline_length": 12, "long_value_header_hex": header.hex()}]}]),
            ),
            mock.patch.object(
                continuation.catalog,
                "_ordinal",
                side_effect=lambda _definition, name: {"Name": 0, "LvProp": 1}[name],
            ),
            mock.patch.object(continuation.catalog, "_page", return_value=bytes(page)),
            mock.patch.object(
                continuation.catalog,
                "_row_directory",
                return_value=[{"hidden": False, "overflow": False, "start": 10, "end": 30}],
            ),
        ):
            value = continuation.created_lvprop(
                bytes(4 * continuation.PAGE_BYTES),
                {
                    "pages": [
                        {"page": 2, "role": "long_value"},
                        {"page": 3, "role": "long_value"},
                    ]
                },
                "ContTwoX",
                2,
            )
        self.assertEqual(value["storage"], "chained")
        self.assertEqual(value["first_locator"], {"row": 0, "page": 2, "appended": True, "row_length": 20})
        self.assertEqual(value["appended_lval_pages"], [2, 3])
        self.assertEqual(value["referenced_appended_lval_pages"], [2])
        self.assertEqual(value["unreferenced_appended_lval_pages"], [3])

    def test_lvprop_allows_null_with_unreferenced_appended_lval_page(self) -> None:
        with (
            mock.patch.object(
                continuation.catalog,
                "_discover_catalog",
                return_value=({}, [], [{"values": ["Alpha", None]}]),
            ),
            mock.patch.object(
                continuation.catalog,
                "_ordinal",
                side_effect=lambda _definition, name: {"Name": 0, "LvProp": 1}[name],
            ),
        ):
            value = continuation.created_lvprop(
                bytes(21 * continuation.PAGE_BYTES),
                {
                    "pages": [
                        {"page": page, "role": "long_value" if page == 20 else "base"}
                        for page in range(21)
                    ]
                },
                "Alpha",
                20,
            )
        self.assertEqual(
            value,
            {
                "appended_lval_pages": [20],
                "referenced_appended_lval_pages": [],
                "storage": "null",
                "unreferenced_appended_lval_pages": [20],
            },
        )

    def test_lvprop_rejects_malformed_external_inline_length(self) -> None:
        header = (0x40000001).to_bytes(4, "little") + bytes([0]) + (2).to_bytes(3, "little") + bytes(4)
        with (
            mock.patch.object(
                continuation.catalog,
                "_discover_catalog",
                return_value=({}, [], [{"values": ["ContOneX", {"inline_length": 13, "long_value_header_hex": header.hex()}]}]),
            ),
            mock.patch.object(
                continuation.catalog,
                "_ordinal",
                side_effect=lambda _definition, name: {"Name": 0, "LvProp": 1}[name],
            ),
        ):
            with self.assertRaisesRegex(continuation.DecodeError, "external framing"):
                continuation.created_lvprop(
                    bytes(3 * continuation.PAGE_BYTES),
                    {"pages": [{"page": 2, "role": "long_value"}]},
                    "ContOneX",
                    2,
                )


if __name__ == "__main__":
    unittest.main()
