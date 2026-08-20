"""Focused fail-closed tests for bounded A1 analysis."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a1_analysis import ReplicaIndexes, build_analysis  # noqa: E402
from a1_spec import (  # noqa: E402
    CHECKPOINT_IDS,
    CLAIMS,
    PLAN_SHA256,
    ROLE_BINDINGS,
    load_checked_plan,
    validate_analysis_report,
)
from protocol_validation import ValidationError  # noqa: E402


class A1AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_checked_plan()

    @staticmethod
    def replica(number: int, digest: str = "0" * 64) -> ReplicaIndexes:
        observation = {
            "replica": number, "plan_sha256": PLAN_SHA256,
            "producer_commit": "0" * 40,
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "run_id": "synthetic", "environment_sha256": "1" * 64,
            "provider_sha256": "2" * 64, "role_binding": ROLE_BINDINGS[number - 1],
        }
        indexes = {
            checkpoint: {"ordered_page_sha256": [digest]}
            for checkpoint in CHECKPOINT_IDS
        }
        return ReplicaIndexes(observation=observation, indexes=indexes)

    def test_ambiguous_derivation_returns_no_outcome_without_reading_holdout_pages(self) -> None:
        replicas = [self.replica(1), self.replica(2), self.replica(3, "f" * 64)]
        with tempfile.TemporaryDirectory() as directory:
            report = build_analysis(self.plan, replicas, Path(directory))
        self.assertEqual(report["scientific_outcome"], "no_scientific_outcome")
        self.assertIn("ambiguous_record_boundary", report["no_outcome_reasons"])
        self.assertFalse(report["holdout_evaluated"])
        self.assertIsNone(report["surviving_model"])
        self.assertEqual(report["claims"], CLAIMS)

    def test_idle_volatility_is_terminal_before_candidate_search(self) -> None:
        replicas = [self.replica(1), self.replica(2), self.replica(3)]
        replicas[0].indexes["E0R"] = {"ordered_page_sha256": ["a" * 64]}
        with tempfile.TemporaryDirectory() as directory:
            report = build_analysis(self.plan, replicas, Path(directory))
        self.assertEqual(report["no_outcome_reasons"], ["idle_volatility"])
        self.assertEqual(report["candidate_models_examined"], 0)
        self.assertFalse(report["holdout_evaluated"])

    def test_replica_binding_disagreement_fails_closed(self) -> None:
        replicas = [self.replica(1), self.replica(2), self.replica(3)]
        replicas[2].observation["run_id"] = "different"
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValidationError, "run_id"):
            build_analysis(self.plan, replicas, Path(directory))

    def test_no_outcome_report_cannot_smuggle_a_model_or_claim(self) -> None:
        report = {
            "protocol_version": "1.0.0", "document_type": "dao_a1_analysis_report",
            "experiment_id": "DAO-A1-ALLOCATION-MAPS-001", "plan_sha256": PLAN_SHA256,
            "run_id": "synthetic", "producer_commit": "0" * 40,
            "derivation_replicas": [1, 2], "holdout_replica": 3,
            "input_checkpoint_count": 213, "candidate_models_examined": 0,
            "derivation_survivor_count": 0, "analysis_work_units": 0,
            "holdout_evaluated": False, "scientific_outcome": "no_scientific_outcome",
            "no_outcome_reasons": ["no_surviving_joint_model"],
            "surviving_model": None, "claims": CLAIMS,
        }
        validate_analysis_report(report)
        report["claims"] = {**CLAIMS, "rust_correctness": True}
        with self.assertRaises(ValidationError):
            validate_analysis_report(report)


if __name__ == "__main__":
    unittest.main()
