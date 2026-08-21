"""Focused fail-closed tests for bounded A1 analysis.

Every bundle here is synthetic. No DAO acquisition, page capture, or scientific
result exists; these tests exercise decision rules, not MDB facts.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TESTS = Path(__file__).resolve().parent
for location in (SCRIPTS, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from a1_analysis import build_analysis  # noqa: E402
from a1_model import Abort, ReplicaIndexes, WorkCounter  # noqa: E402
from a1_spec import (  # noqa: E402
    CHECKPOINT_IDS,
    CLAIMS,
    PLAN_SHA256,
    ROLE_BINDINGS,
    load_checked_plan,
    validate_analysis_report,
)
from a1_test_bundle import Spec, build_bundle, decisive_specs  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


class A1AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_checked_plan()

    @staticmethod
    def replica(number: int, digest: str = "0" * 64) -> ReplicaIndexes:
        observation = {
            "replica": number,
            "plan_sha256": PLAN_SHA256,
            "producer_commit": "0" * 40,
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "run_id": "synthetic",
            "environment_sha256": "1" * 64,
            "provider_sha256": "2" * 64,
            "role_binding": ROLE_BINDINGS[number - 1],
        }
        indexes = {
            checkpoint: {"ordered_page_sha256": [digest]} for checkpoint in CHECKPOINT_IDS
        }
        return ReplicaIndexes(observation=observation, indexes=indexes)

    def analyze(self, specs: list[Spec], holdout: ReplicaIndexes | None = None) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "page-store"
            replicas = build_bundle(specs, store)
            if holdout is not None:
                replicas[2] = holdout
            return build_analysis(self.plan, replicas, store)

    def assert_no_outcome(self, report: dict[str, Any], reason: str) -> None:
        self.assertEqual(report["scientific_outcome"], "no_scientific_outcome")
        self.assertEqual(report["no_outcome_reasons"], [reason])
        self.assertIsNone(report["surviving_model"])
        self.assertEqual(report["claims"], CLAIMS)

    def test_one_joint_model_survives_derivation_and_predicts_the_holdout(self) -> None:
        report = self.analyze(decisive_specs())
        self.assertEqual(report["scientific_outcome"], "one_joint_model_predicts_holdout")
        self.assertEqual(report["no_outcome_reasons"], [])
        self.assertTrue(report["holdout_evaluated"])
        self.assertEqual(report["derivation_survivor_count"], 1)
        self.assertEqual(report["candidate_models_examined"], 6)
        self.assertEqual(
            report["surviving_model"],
            {
                "metadata_page": 1,
                "record_start": 16,
                "record_end": 55,
                "pointer_layout": "u24le_page_then_u8_slot",
                "used_pointer_offset": 16,
                "free_pointer_offset": 20,
                "inline_boundary": 55,
                "low_type1_slot": 0,
                "high_type1_slot": 1,
                "low_reference_page": 600,
                "high_reference_page": 16400,
                "extended_base_formula": "slot_relative_expected_0_16352",
            },
        )
        validate_analysis_report(report)

    def test_holdout_cannot_widen_or_change_the_frozen_candidate_set(self) -> None:
        decisive = self.analyze(decisive_specs())
        frozen = self.analyze(decisive_specs(), holdout=self.replica(3, "e" * 64))
        self.assertEqual(
            frozen["candidate_models_examined"], decisive["candidate_models_examined"]
        )
        self.assertEqual(
            frozen["derivation_survivor_count"], decisive["derivation_survivor_count"]
        )
        self.assertTrue(frozen["holdout_evaluated"])
        self.assert_no_outcome(frozen, "holdout_prediction_failure")

    def test_holdout_base_formula_mismatch_is_not_refitted(self) -> None:
        specs = decisive_specs()
        specs[2].bitmap_shift = 1
        report = self.analyze(specs)
        self.assertEqual(report["derivation_survivor_count"], 1)
        self.assert_no_outcome(report, "holdout_prediction_failure")

    def test_derivation_replicas_must_agree_on_the_record_boundary(self) -> None:
        specs = decisive_specs()
        specs[1].record_shift = 4
        self.assert_no_outcome(self.analyze(specs), "replica_disagreement")

    def test_absent_inline_to_indirect_conversion_is_terminal(self) -> None:
        specs = decisive_specs()
        for spec in specs:
            spec.convert = False
        self.assert_no_outcome(self.analyze(specs), "missing_inline_to_indirect_conversion")

    def test_two_step_offsets_leave_the_record_boundary_ambiguous(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.low_reference = 344
        self.assert_no_outcome(self.analyze(specs), "ambiguous_record_boundary")

    def test_empty_final_inline_extent_is_ambiguous(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.empty_inline_anchor = True
        self.assert_no_outcome(self.analyze(specs), "ambiguous_inline_boundary")

    def test_nonzero_bytes_beyond_the_inline_boundary_are_unexplained(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.stray_suffix = True
        self.assert_no_outcome(self.analyze(specs), "unexplained_nonzero_inline_suffix")

    def test_absent_free_pointer_transition_leaves_no_surviving_model(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.static_free_pointer = True
        report = self.analyze(specs)
        self.assertEqual(report["candidate_models_examined"], 0)
        self.assertEqual(report["derivation_survivor_count"], 0)
        self.assertFalse(report["holdout_evaluated"])
        self.assert_no_outcome(report, "no_surviving_joint_model")

    def test_second_used_pointer_leaves_more_than_one_surviving_model(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.second_used_pointer = True
        report = self.analyze(specs)
        self.assertEqual(report["candidate_models_examined"], 18)
        self.assertEqual(report["derivation_survivor_count"], 3)
        self.assertFalse(report["holdout_evaluated"])
        self.assert_no_outcome(report, "multiple_surviving_joint_models")

    def test_unobserved_high_type1_slot_is_incomplete_evidence(self) -> None:
        specs = decisive_specs()
        for spec in specs[:2]:
            spec.activate_high_slot = False
        self.assert_no_outcome(self.analyze(specs), "incomplete_transition_evidence")

    def test_final_page_ceiling_breach_fails_closed(self) -> None:
        specs = decisive_specs()
        specs[0].page_ceiling_breach = True
        self.assert_no_outcome(self.analyze(specs), "resource_bound_breach")

    def test_missing_page_store_blob_is_unreconstructable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "page-store"
            replicas = build_bundle(decisive_specs(), store)
            (store / f"{replicas[0].indexes['E0']['ordered_page_sha256'][1]}.page").unlink()
            report = build_analysis(self.plan, replicas, store)
        self.assert_no_outcome(report, "unreconstructable_snapshot")

    def test_work_ceiling_is_charged_and_fails_closed(self) -> None:
        work = WorkCounter()
        work.charge(1)
        with self.assertRaises(Abort) as caught:
            work.charge(10**12)
        self.assertEqual(caught.exception.reason, "resource_bound_breach")
        self.assertEqual(work.value, 1)

    def test_ambiguous_derivation_returns_no_outcome_without_reading_holdout_pages(self) -> None:
        replicas = [self.replica(1), self.replica(2), self.replica(3, "f" * 64)]
        with tempfile.TemporaryDirectory() as directory:
            report = build_analysis(self.plan, replicas, Path(directory))
        self.assertIn("ambiguous_record_boundary", report["no_outcome_reasons"])
        self.assertFalse(report["holdout_evaluated"])
        self.assertIsNone(report["surviving_model"])

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
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValidationError, "run_id"
        ):
            build_analysis(self.plan, replicas, Path(directory))

    def test_no_outcome_report_cannot_smuggle_a_model_or_claim(self) -> None:
        report = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a1_analysis_report",
            "experiment_id": "DAO-A1-ALLOCATION-MAPS-001",
            "plan_sha256": PLAN_SHA256,
            "run_id": "synthetic",
            "producer_commit": "0" * 40,
            "derivation_replicas": [1, 2],
            "holdout_replica": 3,
            "input_checkpoint_count": 213,
            "candidate_models_examined": 0,
            "derivation_survivor_count": 0,
            "analysis_work_units": 0,
            "holdout_evaluated": False,
            "scientific_outcome": "no_scientific_outcome",
            "no_outcome_reasons": ["no_surviving_joint_model"],
            "surviving_model": None,
            "claims": CLAIMS,
        }
        validate_analysis_report(report)
        report["claims"] = {**CLAIMS, "rust_correctness": True}
        with self.assertRaises(ValidationError):
            validate_analysis_report(report)


if __name__ == "__main__":
    unittest.main()
