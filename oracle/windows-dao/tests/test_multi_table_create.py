#!/usr/bin/env python3
"""Focused tests for the preregistered multi-table create analyzer."""

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
ANALYZER_PATH = ROOT / "oracle" / "windows-dao" / "scripts" / "multi_table_create.py"
SPEC = importlib.util.spec_from_file_location("multi_table_create", ANALYZER_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)
PLAN = "a" * 64


def snapshot() -> dict[str, object]:
    tables = ANALYZER.expected_tables()
    return {
        "tabledefs": tables,
        "tables": ANALYZER.expected_table_shapes(),
        "table_documents": tables,
    }


def endpoints(*, passed: bool = True) -> dict[str, object]:
    return {
        "status": "pass" if passed else "fail",
        "completed": list(ANALYZER.ENDPOINTS) if passed else ["open_database"],
        "detail": "all endpoints passed" if passed else "DAO rejected the image",
        "snapshot": snapshot() if passed else {},
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        raw = bytes([1]) * (30 * ANALYZER.PAGE_BYTES)
        self.candidate = {
            "filename": "source-quad.mdb",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.control_size = 31 * ANALYZER.PAGE_BYTES
        self.replicas = []
        for replica in range(1, 4):
            images = [
                self.image(replica, "candidate_quad", raw),
                self.image(replica, "control_quad", bytes([replica + 10]) * self.control_size),
            ]
            self.replicas.append(
                {"replica": replica, "status": "pass", "error": None, "images": images}
            )
        self.document = {
            "document_type": ANALYZER.DOCUMENT_TYPE,
            "development_only": True,
            "plan_sha256": PLAN,
            "run_id": "20260903T120000Z-multi-table",
            "status": "pass",
            "mutation_started": True,
            "replicas": self.replicas,
        }

    def image(self, replica: int, role: str, raw: bytes) -> dict[str, object]:
        filename = ANALYZER.expected_filename(replica, role)
        (self.root / filename).write_bytes(raw)
        identity = hashlib.sha256(raw).hexdigest()
        return {
            "role": role,
            "database": filename,
            "size_before": len(raw),
            "sha256_before": identity,
            "endpoints": endpoints(),
            "size_after": len(raw),
            "sha256_after": identity,
        }

    def write(self) -> Path:
        path = self.root / "multi-table-create-job-result.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path


class MultiTableCreateTests(unittest.TestCase):
    def evaluate(self, fixture: Fixture) -> dict[str, object]:
        output = fixture.root / "report.json"
        with (
            mock.patch.object(ANALYZER, "CANDIDATE", fixture.candidate),
            mock.patch.object(ANALYZER, "CONTROL_SIZE", fixture.control_size),
        ):
            report = ANALYZER.evaluate(fixture.write(), PLAN, output)
        self.assertEqual(output.read_bytes(), ANALYZER.canonical_bytes(report))
        return report

    def test_accepts_an_unchanged_candidate_with_control_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self.evaluate(Fixture(Path(temporary)))
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["questions"]["quad"]["status"], "observed_accepted")
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])

    def test_stable_rejection_and_semantic_mismatch_are_accepted_negatives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                replica["images"][0]["endpoints"] = endpoints(passed=False)
            rejected = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                tables = replica["images"][0]["endpoints"]["snapshot"]["tables"]
                tables[1]["fields"][1]["size"] = 51
            mismatch = self.evaluate(fixture)
        self.assertEqual(rejected["status"], "accepted")
        self.assertEqual(rejected["questions"]["quad"]["status"], "not_observed_accepted")
        self.assertEqual(mismatch["status"], "accepted")
        self.assertEqual(mismatch["questions"]["quad"]["status"], "not_observed_accepted")

    def test_control_failure_disagreement_and_change_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            for replica in fixture.replicas:
                replica["images"][1]["endpoints"] = endpoints(passed=False)
            control = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            fixture.replicas[0]["images"][0]["endpoints"] = endpoints(passed=False)
            disagreement = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            image = fixture.replicas[0]["images"][0]
            retained = bytearray((fixture.root / image["database"]).read_bytes())
            retained[-1] ^= 1
            (fixture.root / image["database"]).write_bytes(retained)
            image["sha256_after"] = hashlib.sha256(retained).hexdigest()
            changed = self.evaluate(fixture)
        for report in (control, disagreement, changed):
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(report["questions"]["quad"]["status"], "no_outcome")

    def test_incomplete_or_failed_post_mutation_job_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            replica = fixture.replicas[2]
            for image in replica["images"]:
                (fixture.root / image["database"]).unlink()
            replica["images"] = []
            replica["status"] = "fail"
            replica["error"] = "DAO control creation failed"
            fixture.document["status"] = "fail"
            incomplete = self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            fixture.replicas[2]["status"] = "fail"
            fixture.replicas[2]["error"] = "post-observation job failure"
            fixture.document["status"] = "fail"
            failed = self.evaluate(fixture)
        self.assertEqual(incomplete["status"], "no_outcome")
        self.assertEqual(failed["status"], "no_outcome")

    def test_pre_mutation_failure_and_bad_identities_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.document["mutation_started"] = False
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "before its first DAO mutation"):
                self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            fixture.replicas[0]["images"][0]["sha256_before"] = "0" * 64
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "preregistered identity"):
                self.evaluate(fixture)

            fixture = Fixture(Path(temporary))
            (fixture.root / "unexpected.MDB").write_bytes(bytes(ANALYZER.PAGE_BYTES))
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(fixture)

            failed = endpoints(passed=False)
            failed["completed"] = list(ANALYZER.ENDPOINTS)
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "claim the complete"):
                ANALYZER.read_endpoints(failed)

    def test_index_order_is_normalized_but_table_order_and_semantics_are_exact(self) -> None:
        observed = snapshot()
        observed["tables"][2]["indexes"].append(
            {
                "name": "AExtra",
                "primary": False,
                "unique": False,
                "required": False,
                "fields": [{"name": "Id", "descending": False}],
            }
        )
        observed["tables"][2]["indexes"].reverse()
        normalized, matches = ANALYZER.normalize_snapshot(observed)
        self.assertFalse(matches)
        self.assertEqual(normalized["tables"][2]["indexes"][0]["name"], "AExtra")

        observed = snapshot()
        observed["tables"].reverse()
        _, matches = ANALYZER.normalize_snapshot(observed)
        self.assertFalse(matches)

        observed = copy.deepcopy(snapshot())
        observed["tables"][2]["indexes"][0]["required"] = False
        _, matches = ANALYZER.normalize_snapshot(observed)
        self.assertFalse(matches)
        _, matches = ANALYZER.normalize_snapshot(snapshot())
        self.assertTrue(matches)


if __name__ == "__main__":
    unittest.main()
