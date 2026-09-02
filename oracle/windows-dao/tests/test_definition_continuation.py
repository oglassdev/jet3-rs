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
    unattributed: bool = False,
) -> dict:
    marker = data[1]
    count = len(data) // continuation.PAGE_BYTES
    if marker == 0:
        return {
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
    logical_length = {"zero": 2046, "one": 2075, "two": 4105}[scenario]
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
        if unattributed and scenario == "two" and page == count - 1:
            role = "unassigned"
        output_pages.append(
            {"page": page, "role": role, "owners": [owner], "tag": 2 if page in pages else 1}
        )
    return {
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
            checkpoints = [self.checkpoint(replica, "empty", empty, None, dao(None))]
            for scenario in continuation.SCENARIOS:
                checkpoints.append(
                    self.checkpoint(
                        replica,
                        scenario,
                        scenario_image(scenario),
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
            "arm_before": arm_before,
            "dao": metadata,
        }

    def write(self) -> Path:
        path = self.root / "definition-continuation-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class DefinitionContinuationTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture, analysis=fake_analysis):
        output = fixture.root / "definition-continuation-report.json"
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
                side_effect=lambda *_args: {
                    "declared_length": 1,
                    "first_locator": None,
                    "header_hex": "0" * 24,
                    "inline_length": 13,
                    "storage": "inline",
                },
            ),
        ):
            report = continuation.evaluate(fixture.write(), PLAN, output)
        self.assertEqual(output.read_bytes(), continuation.canonical_bytes(report))
        return report

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

    def test_wrong_continuation_count_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, bad_count=True)
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("control requires", report["replicas"][0]["decode_error"])

    def test_unattributed_appended_page_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(
                Fixture(Path(directory)), lambda data: fake_analysis(data, unattributed=True)
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertIn("unattributed", report["replicas"][0]["decode_error"])

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
                return_value=({}, [], [{"values": ["ContZero", None]}]),
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
                "ContZero",
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
