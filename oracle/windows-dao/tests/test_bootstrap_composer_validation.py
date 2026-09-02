from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
ANALYZER_PATH = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "bootstrap_composer_validation.py"
)
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_composer_validation", ANALYZER_PATH
)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def endpoint_observation(role: str, *, passed: bool = True) -> dict[str, object]:
    expected = (
        ANALYZER.EMPTY_ENDPOINTS
        if role == "candidate_empty"
        else ANALYZER.ALPHA_ENDPOINTS
    )
    if not passed:
        return {"status": "fail", "completed": [], "detail": "rejected", "snapshot": {}}
    tables = ANALYZER.SYSTEM_TABLES if role == "candidate_empty" else ANALYZER.ALPHA_TABLES
    snapshot: dict[str, object] = {
        "tabledefs": tables,
        "table_documents": tables,
    }
    if role != "candidate_empty":
        snapshot.update(
            {
                "field": {"name": "Id", "type": 4},
                "table_properties": [{"name": "Name", "type": 10}],
                "field_properties": [{"name": "Required", "type": 1}],
                "field_required": False,
            }
        )
    return {
        "status": "pass",
        "completed": expected,
        "detail": "passed",
        "snapshot": snapshot,
    }


def image(
    root: Path,
    replica: int,
    role: str,
    raw: bytes,
    *,
    passed: bool = True,
    after: bytes | None = None,
) -> dict[str, object]:
    prefix = "candidate" if role.startswith("candidate_") else "control"
    suffix = "empty" if role == "candidate_empty" else "alpha"
    database = f"{prefix}-r{replica}-{suffix}.mdb"
    retained = raw if after is None else after
    (root / database).write_bytes(retained)
    return {
        "role": role,
        "database": database,
        "size_before": len(raw),
        "sha256_before": hashlib.sha256(raw).hexdigest(),
        "endpoints": endpoint_observation(role, passed=passed),
        "size_after": len(retained),
        "sha256_after": hashlib.sha256(retained).hexdigest(),
    }


def job_document(
    root: Path,
    *,
    alpha_passed: tuple[bool, bool, bool] = (True, True, True),
    repaired_role: str | None = None,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    empty = b"E" * (20 * ANALYZER.PAGE_BYTES)
    alpha = b"A" * (23 * ANALYZER.PAGE_BYTES)
    control = b"C" * (23 * ANALYZER.PAGE_BYTES)
    pins = {
        "candidate_empty": {"size": len(empty), "sha256": hashlib.sha256(empty).hexdigest()},
        "candidate_alpha": {"size": len(alpha), "sha256": hashlib.sha256(alpha).hexdigest()},
    }
    replicas = []
    for replica in range(1, 4):
        images = []
        for role, raw, passed in (
            ("candidate_empty", empty, True),
            ("candidate_alpha", alpha, alpha_passed[replica - 1]),
            ("control_alpha", control, True),
        ):
            after = None
            if repaired_role == role:
                after = raw[:-1] + bytes([raw[-1] ^ 1])
            images.append(
                image(root, replica, role, raw, passed=passed, after=after)
            )
        replicas.append(
            {"replica": replica, "status": "pass", "error": None, "images": images}
        )
    return (
        {
            "document_type": "dao_bootstrap_composer_validation_job_result",
            "development_only": True,
            "plan_sha256": "a" * 64,
            "run_id": "20260902T120000Z-composer-validation",
            "status": "pass",
            "replicas": replicas,
        },
        pins,
    )


class BootstrapComposerValidationTests(unittest.TestCase):
    def evaluate(self, root: Path, document: dict[str, object], pins: dict[str, object]):
        job = root / "job.json"
        job.write_text(json.dumps(document), encoding="utf-8")
        with mock.patch.object(ANALYZER, "CANDIDATES", pins):
            return ANALYZER.evaluate(job, "a" * 64, root / "report.json")

    def test_both_candidates_are_observed_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "accepted")
        self.assertEqual(
            report["questions"]["empty_candidate"]["status"], "observed_accepted"
        )
        self.assertEqual(
            report["questions"]["alpha_candidate"]["status"], "observed_accepted"
        )
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])

    def test_consistent_candidate_rejection_is_an_accepted_negative_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, alpha_passed=(False, False, False))
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "accepted")
        self.assertEqual(
            report["questions"]["alpha_candidate"]["status"],
            "not_observed_accepted",
        )

    def test_replica_disagreement_and_metadata_change_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, alpha_passed=(True, False, True))
            disagreement = self.evaluate(root, document, pins)
            document, pins = job_document(root, repaired_role="candidate_empty")
            repaired = self.evaluate(root, document, pins)

        self.assertEqual(disagreement["status"], "no_outcome")
        self.assertEqual(repaired["status"], "no_outcome")
        self.assertEqual(
            repaired["questions"]["empty_candidate"]["reason"],
            "DAO changed at least one candidate",
        )

    def test_endpoint_detail_participates_in_replica_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, alpha_passed=(False, False, False))
            document["replicas"][1]["images"][1]["endpoints"]["detail"] = "different"
            report = self.evaluate(root, document, pins)

        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(
            report["questions"]["alpha_candidate"]["reason"],
            "candidate endpoint observations disagree",
        )

    def test_control_change_and_incomplete_replica_are_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root, repaired_role="control_alpha")
            repaired = self.evaluate(root, document, pins)

            document, pins = job_document(root)
            document["replicas"][2] = {
                "replica": 3,
                "status": "fail",
                "error": "control creation failed",
                "images": [],
            }
            document["status"] = "fail"
            for name in (
                "candidate-r3-empty.mdb",
                "candidate-r3-alpha.mdb",
                "control-r3-alpha.mdb",
            ):
                (root / name).unlink()
            incomplete = self.evaluate(root, document, pins)

        self.assertEqual(repaired["status"], "no_outcome")
        self.assertEqual(incomplete["status"], "no_outcome")

    def test_candidate_pin_and_retained_artifact_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            document["replicas"][0]["images"][0]["sha256_before"] = "0" * 64
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "preregistered candidate"):
                self.evaluate(root, document, pins)

            document, pins = job_document(root)
            (root / "candidate-r1-empty.mdb").write_bytes(b"tampered")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "retained identity"):
                self.evaluate(root, document, pins)

    def test_order_status_and_unique_json_fields_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            document["replicas"][0]["images"].reverse()
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "out of order"):
                self.evaluate(root, document, pins)

            job = root / "duplicate.json"
            job.write_text('{"status":"pass","status":"fail"}', encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "duplicate JSON field"):
                ANALYZER.load_document(job)

    def test_unexpected_retained_mdb_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document, pins = job_document(root)
            (root / "unexpected.mdb").write_bytes(b"extra")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(root, document, pins)


if __name__ == "__main__":
    unittest.main()
