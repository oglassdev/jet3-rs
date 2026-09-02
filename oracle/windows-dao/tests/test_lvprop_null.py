from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
ANALYZER_PATH = ROOT / "oracle" / "windows-dao" / "scripts" / "lvprop_null.py"
SPEC = importlib.util.spec_from_file_location("lvprop_null", ANALYZER_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def endpoint_observation(
    *, passed: bool = True, semantic_mismatch: bool = False
) -> dict[str, object]:
    if not passed:
        return {
            "status": "fail",
            "completed": [],
            "detail": "rejected",
            "snapshot": {},
        }
    table_properties = [{"name": "Name", "type": 10}]
    if semantic_mismatch:
        table_properties = [{"name": "Name", "type": 12}]
    return {
        "status": "pass",
        "completed": ANALYZER.ALPHA_ENDPOINTS,
        "detail": "passed",
        "snapshot": {
            "tabledefs": ANALYZER.ALPHA_TABLES,
            "table_documents": ANALYZER.ALPHA_TABLES,
            "field": {"name": "Id", "type": 4},
            "table_properties": table_properties,
            "field_properties": [{"name": "Required", "type": 1}],
            "field_required": False,
        },
    }


def image(
    root: Path,
    replica: int,
    role: str,
    raw: bytes,
    *,
    passed: bool = True,
    semantic_mismatch: bool = False,
    after: bytes | None = None,
) -> dict[str, object]:
    database = ANALYZER.DATABASE_NAMES[role].format(replica=replica)
    retained = raw if after is None else after
    (root / database).write_bytes(retained)
    return {
        "role": role,
        "database": database,
        "size_before": len(raw),
        "sha256_before": hashlib.sha256(raw).hexdigest(),
        "endpoints": endpoint_observation(
            passed=passed, semantic_mismatch=semantic_mismatch
        ),
        "size_after": len(retained),
        "sha256_after": hashlib.sha256(retained).hexdigest(),
    }


def job_document(
    root: Path,
    *,
    fixed_passed: tuple[bool, bool, bool] = (True, True, True),
    null_passed: tuple[bool, bool, bool] = (True, True, True),
    control_passed: tuple[bool, bool, bool] = (True, True, True),
    null_semantic_mismatch: bool = False,
    repaired_role: str | None = None,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    fixed = b"F" * (23 * ANALYZER.PAGE_BYTES)
    null = b"N" * (23 * ANALYZER.PAGE_BYTES)
    control = b"C" * (23 * ANALYZER.PAGE_BYTES)
    pins = {
        "candidate_fixed": {
            "size": len(fixed),
            "sha256": hashlib.sha256(fixed).hexdigest(),
        },
        "candidate_null": {
            "size": len(null),
            "sha256": hashlib.sha256(null).hexdigest(),
        },
    }
    replicas = []
    for replica in range(1, 4):
        images = []
        for role, raw, passed, mismatch in (
            ("candidate_fixed", fixed, fixed_passed[replica - 1], False),
            (
                "candidate_null",
                null,
                null_passed[replica - 1],
                null_semantic_mismatch,
            ),
            ("control_alpha", control, control_passed[replica - 1], False),
        ):
            after = None
            if repaired_role == role:
                after = raw[:-1] + bytes([raw[-1] ^ 1])
            images.append(
                image(
                    root,
                    replica,
                    role,
                    raw,
                    passed=passed,
                    semantic_mismatch=mismatch,
                    after=after,
                )
            )
        replicas.append(
            {"replica": replica, "status": "pass", "error": None, "images": images}
        )
    return (
        {
            "document_type": "dao_lvprop_null_job_result",
            "development_only": True,
            "plan_sha256": "a" * 64,
            "run_id": "20260902T120000Z-lvprop-null",
            "status": "pass",
            "replicas": replicas,
        },
        pins,
    )


class LvPropNullTests(unittest.TestCase):
    def evaluate(
        self,
        root: Path,
        document: dict[str, object],
        pins: dict[str, object],
    ) -> dict[str, object]:
        job = root / "job.json"
        job.write_text(json.dumps(document), encoding="utf-8")
        with mock.patch.object(ANALYZER, "CANDIDATES", pins):
            return ANALYZER.evaluate(job, "a" * 64, root / "report.json")

    def test_null_candidate_is_observed_accepted_after_both_controls_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "accepted")
        self.assertEqual(
            report["questions"]["fixed_candidate"]["status"], "observed_accepted"
        )
        self.assertEqual(
            report["questions"]["null_candidate"]["status"], "observed_accepted"
        )
        self.assertEqual(report["document_type"], "lvprop_null_report")
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])

    def test_stable_null_rejection_is_an_accepted_negative_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(
                root, null_passed=(False, False, False)
            )
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "accepted")
        self.assertEqual(
            report["questions"]["null_candidate"]["status"],
            "not_observed_accepted",
        )

    def test_stable_semantic_mismatch_is_an_accepted_negative_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, null_semantic_mismatch=True)
            report = self.evaluate(root, document, pins)

        question = report["questions"]["null_candidate"]
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(question["status"], "not_observed_accepted")
        self.assertIn("semantic snapshot differs", question["reason"])

    def test_fixed_positive_control_failure_gates_the_null_question(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(
                root, fixed_passed=(False, False, False)
            )
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(
            report["questions"]["fixed_candidate"]["status"], "no_outcome"
        )
        self.assertEqual(
            report["questions"]["null_candidate"]["status"], "no_outcome"
        )

    def test_replica_disagreement_and_candidate_change_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(
                root, null_passed=(True, False, True)
            )
            disagreement = self.evaluate(root, document, pins)

            document, pins = job_document(root, repaired_role="candidate_null")
            repaired = self.evaluate(root, document, pins)

        self.assertEqual(disagreement["status"], "no_outcome")
        self.assertEqual(repaired["status"], "no_outcome")
        self.assertEqual(
            repaired["questions"]["null_candidate"]["reason"],
            "DAO changed at least one candidate",
        )

    def test_fresh_control_failure_and_change_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(
                root, control_passed=(False, False, False)
            )
            failed = self.evaluate(root, document, pins)

            document, pins = job_document(root, repaired_role="control_alpha")
            repaired = self.evaluate(root, document, pins)

        self.assertEqual(failed["status"], "no_outcome")
        self.assertEqual(repaired["status"], "no_outcome")

    def test_incomplete_replica_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            document["replicas"][2] = {
                "replica": 3,
                "status": "fail",
                "error": "control creation failed",
                "images": [],
            }
            document["status"] = "fail"
            for name in (
                "candidate-r3-fixed.mdb",
                "candidate-r3-null.mdb",
                "control-r3-alpha.mdb",
            ):
                (root / name).unlink()
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")

    def test_partial_control_after_mutation_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            replica = document["replicas"][0]
            replica["status"] = "fail"
            replica["error"] = "control construction failed after CreateDatabase"
            control = replica["images"][2]
            partial = b"P" * (20 * ANALYZER.PAGE_BYTES)
            (root / control["database"]).write_bytes(partial)
            control["size_before"] = len(partial)
            control["size_after"] = len(partial)
            control["sha256_before"] = hashlib.sha256(partial).hexdigest()
            control["sha256_after"] = hashlib.sha256(partial).hexdigest()
            control["endpoints"] = endpoint_observation(passed=False)
            document["status"] = "fail"
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(
            report["questions"]["null_candidate"]["status"], "no_outcome"
        )

    def test_failed_recovery_may_retain_an_unobserved_expected_mdb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            replica = document["replicas"][0]
            replica["status"] = "fail"
            replica["error"] = "control identity recovery failed"
            replica["images"].pop()
            document["status"] = "fail"
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")

    def test_unobserved_recovery_mdb_remains_size_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            replica = document["replicas"][0]
            replica["status"] = "fail"
            replica["error"] = "control identity recovery failed"
            control = replica["images"].pop()
            (root / control["database"]).write_bytes(
                b"X" * (65 * ANALYZER.PAGE_BYTES)
            )
            document["status"] = "fail"
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "byte bound"):
                self.evaluate(root, document, pins)

    def test_recovered_candidate_change_is_a_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, repaired_role="candidate_null")
            document["replicas"][0]["status"] = "fail"
            document["replicas"][0]["error"] = "post-access recovery"
            document["status"] = "fail"
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")

    def test_candidate_pin_and_retained_artifact_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            document["replicas"][0]["images"][0]["sha256_before"] = "0" * 64
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError, "preregistered candidate"
            ):
                self.evaluate(root, document, pins)
            self.assertFalse((root / "report.json").exists())

            document, pins = job_document(root)
            (root / "candidate-r1-null.mdb").write_bytes(b"tampered")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "retained identity"):
                self.evaluate(root, document, pins)

    def test_property_shape_accepts_sorted_values_but_rejects_duplicates(self) -> None:
        properties = [
            {"name": "Name", "type": 10},
            {"name": "RecordCount", "type": 4},
            {"name": "ValidationRule", "type": 10},
        ]
        self.assertEqual(ANALYZER.property_shape(properties, "properties"), properties)
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "not sorted"):
            ANALYZER.property_shape(list(reversed(properties)), "properties")
        properties.append({"name": "Name", "type": 10})
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "nonempty and unique"):
            ANALYZER.property_shape(properties, "properties")

    def test_order_status_unique_json_and_inventory_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            document["replicas"][0]["images"].reverse()
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "out of order"):
                self.evaluate(root, document, pins)

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"status":"pass","status":"fail"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError, "duplicate JSON field"
            ):
                ANALYZER.load_document(duplicate)

            document, pins = job_document(root)
            (root / "unexpected.mdb").write_bytes(b"extra")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(root, document, pins)
            (root / "unexpected.mdb").unlink()
            (root / "unexpected.MDB").write_bytes(b"extra")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(root, document, pins)


if __name__ == "__main__":
    unittest.main()
