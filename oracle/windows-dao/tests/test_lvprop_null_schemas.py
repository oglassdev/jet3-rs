#!/usr/bin/env python3
"""Focused tests for the preregistered issue #178 analyzer."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
ANALYZER_PATH = ROOT / "oracle" / "windows-dao" / "scripts" / "lvprop_null_schemas.py"
SPEC = importlib.util.spec_from_file_location("lvprop_null_schemas", ANALYZER_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)
PLAN = "a" * 64


def snapshot(schema_name: str) -> dict[str, object]:
    schema = ANALYZER.SCHEMAS[schema_name]
    tables = sorted((schema["table"], *ANALYZER.SYSTEM_TABLES))
    return {
        "tabledefs": tables,
        "fields": [{"name": name, "type": 4} for name in schema["fields"]],
        "indexes": [
            {
                **{key: value for key, value in entry.items() if key != "fields"},
                "fields": list(entry["fields"]),
            }
            for entry in schema["indexes"]
        ],
        "table_documents": tables,
    }


def endpoints(schema: str, *, passed: bool = True) -> dict[str, object]:
    return {
        "status": "pass" if passed else "fail",
        "completed": list(ANALYZER.ENDPOINTS) if passed else ["open_database"],
        "detail": "all endpoints passed" if passed else "DAO rejected the image",
        "snapshot": snapshot(schema) if passed else {},
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.candidates: dict[str, dict[str, object]] = {}
        self.control_sizes = {schema: (10 + index) * ANALYZER.PAGE_BYTES for index, schema in enumerate(ANALYZER.SCHEMA_ORDER)}
        for index, schema in enumerate(ANALYZER.SCHEMA_ORDER):
            raw = bytes([index + 1]) * ((20 + index) * ANALYZER.PAGE_BYTES)
            self.candidates[f"candidate_{schema}"] = {
                "filename": f"source-{schema}.mdb",
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "raw": raw,
            }
        self.replicas = []
        for replica in range(1, 4):
            images = []
            for role in ANALYZER.ROLE_ORDER:
                kind, schema = role.split("_", 1)
                if kind == "candidate":
                    raw = self.candidates[role]["raw"]
                else:
                    raw = bytes([replica + 10]) * self.control_sizes[schema]
                images.append(self.image(replica, role, raw))
            self.replicas.append(
                {"replica": replica, "status": "pass", "error": None, "images": images}
            )
        self.document = {
            "document_type": ANALYZER.DOCUMENT_TYPE,
            "development_only": True,
            "plan_sha256": PLAN,
            "run_id": "20260903T120000Z-lvprop-schemas",
            "status": "pass",
            "mutation_started": True,
            "replicas": self.replicas,
        }

    def image(self, replica: int, role: str, raw: bytes) -> dict[str, object]:
        schema = role.split("_", 1)[1]
        filename = ANALYZER.expected_filename(replica, role)
        path = self.root / filename
        path.write_bytes(raw)
        identity = hashlib.sha256(raw).hexdigest()
        return {
            "role": role,
            "database": filename,
            "size_before": len(raw),
            "sha256_before": identity,
            "endpoints": endpoints(schema),
            "size_after": len(raw),
            "sha256_after": identity,
        }

    def write(self) -> Path:
        path = self.root / "lvprop-null-schemas-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class LvPropNullSchemasTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture) -> dict[str, object]:
        pins = {
            role: {key: value for key, value in raw.items() if key != "raw"}
            for role, raw in fixture.candidates.items()
        }
        output = fixture.root / "report.json"
        with (
            mock.patch.object(ANALYZER, "CANDIDATES", pins),
            mock.patch.object(ANALYZER, "CONTROL_SIZES", fixture.control_sizes),
        ):
            report = ANALYZER.evaluate(fixture.write(), PLAN, output)
        self.assertEqual(output.read_bytes(), ANALYZER.canonical_bytes(report))
        return report

    def test_accepts_three_unchanged_candidate_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self.evaluate(Fixture(Path(temporary)))
        self.assertEqual(report["status"], "accepted")
        self.assertTrue(
            all(question["status"] == "observed_accepted" for question in report["questions"].values())
        )
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])

    def test_stable_target_rejection_is_an_accepted_negative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                replica["images"][1]["endpoints"] = endpoints("indexed", passed=False)
            report = self.evaluate(fixture)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["questions"]["indexed"]["status"], "not_observed_accepted")

    def test_stable_semantic_mismatch_is_an_accepted_negative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                fixture_snapshot = replica["images"][2]["endpoints"]["snapshot"]
                fixture_snapshot["fields"][-1]["name"] = "Unexpected"
            report = self.evaluate(fixture)
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["questions"]["wide"]["status"], "not_observed_accepted")

    def test_alpha_or_control_failure_gates_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                replica["images"][0]["endpoints"] = endpoints("alpha", passed=False)
            alpha = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                replica["images"][4]["endpoints"] = endpoints("indexed", passed=False)
            control = self.evaluate(fixture)
        self.assertEqual(alpha["status"], "no_outcome")
        self.assertEqual(control["questions"]["indexed"]["status"], "no_outcome")

    def test_replica_disagreement_and_change_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.replicas[0]["images"][1]["endpoints"] = endpoints("indexed", passed=False)
            disagreement = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            image = fixture.replicas[0]["images"][2]
            retained = bytearray((fixture.root / image["database"]).read_bytes())
            retained[-1] ^= 1
            (fixture.root / image["database"]).write_bytes(retained)
            image["sha256_after"] = hashlib.sha256(retained).hexdigest()
            changed = self.evaluate(fixture)
        self.assertEqual(disagreement["questions"]["indexed"]["status"], "no_outcome")
        self.assertEqual(changed["questions"]["wide"]["status"], "no_outcome")

    def test_incomplete_post_mutation_job_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            replica = fixture.replicas[2]
            for image in replica["images"]:
                (fixture.root / image["database"]).unlink()
            replica["images"] = []
            replica["status"] = "fail"
            replica["error"] = "DAO control creation failed"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
        self.assertEqual(report["status"], "no_outcome")

    def test_failed_job_with_complete_observations_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.replicas[2]["status"] = "fail"
            fixture.replicas[2]["error"] = "post-observation job failure"
            fixture.document["status"] = "fail"
            report = self.evaluate(fixture)
        self.assertEqual(report["status"], "no_outcome")

    def test_failed_endpoint_cannot_claim_all_endpoints_completed(self) -> None:
        failed = endpoints("indexed", passed=False)
        failed["completed"] = list(ANALYZER.ENDPOINTS)
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "claim the complete"):
            ANALYZER.read_endpoints(failed, "indexed")

    def test_pre_mutation_failure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.document["mutation_started"] = False
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "before its first DAO mutation"):
                self.evaluate(fixture)

    def test_candidate_pin_unique_json_and_inventory_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.replicas[0]["images"][0]["sha256_before"] = "0" * 64
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "preregistered identity"):
                self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            (fixture.root / "unexpected.MDB").write_bytes(bytes(ANALYZER.PAGE_BYTES))
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(fixture)

            duplicate = fixture.root / "duplicate.json"
            duplicate.write_text('{"status":"pass","status":"fail"}', encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "duplicate JSON field"):
                ANALYZER.load(duplicate)

    def test_file_bounds_are_checked_before_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "oversized.json"
            with oversized.open("wb") as stream:
                stream.truncate(ANALYZER.MAX_JSON_BYTES + 1)
            with mock.patch.object(
                Path, "read_bytes", side_effect=AssertionError("oversized JSON was read")
            ):
                with self.assertRaisesRegex(ANALYZER.AnalysisError, "JSON bound"):
                    ANALYZER.load(oversized)

            fixture = Fixture(root)
            image = copy.deepcopy(fixture.replicas[0]["images"][0])
            image["size_after"] -= ANALYZER.PAGE_BYTES
            pins = {
                role: {key: value for key, value in raw.items() if key != "raw"}
                for role, raw in fixture.candidates.items()
            }
            with (
                mock.patch.object(ANALYZER, "CANDIDATES", pins),
                mock.patch.object(
                    Path, "read_bytes", side_effect=AssertionError("oversized MDB was read")
                ),
            ):
                with self.assertRaisesRegex(ANALYZER.AnalysisError, "recorded identity"):
                    ANALYZER.read_image(root, image, 1, "candidate_alpha")

    def test_index_order_is_normalized_but_index_semantics_are_exact(self) -> None:
        observed = snapshot("indexed")
        observed["indexes"].reverse()
        _, matches = ANALYZER.normalize_snapshot(observed, "indexed")
        self.assertTrue(matches)
        observed = copy.deepcopy(observed)
        observed["indexes"][0]["unique"] = not observed["indexes"][0]["unique"]
        _, matches = ANALYZER.normalize_snapshot(observed, "indexed")
        self.assertFalse(matches)


if __name__ == "__main__":
    unittest.main()
