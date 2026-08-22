"""Focused A3 analyzer, generator, freeze, and reporting contracts."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol_validation import ValidationError  # noqa: E402
from a3_analysis import LoadedReplicaSource, ReplicaInput, build_analysis  # noqa: E402
from a3_generator import (  # noqa: E402
    calibration_parameters, generate_synthetic_bundle, generate_synthetic_bundles,
)
from a3_layers import derive_tdef_candidates, polarity_cross_check  # noqa: E402
from a3_model import (  # noqa: E402
    Abort, View, WorkCounter, decode_inline, global_start_candidates,
)
from a3_spec import (  # noqa: E402
    PLAN_SHA256, PREDICATES, PREDICATE_IDS, compare_frozen_to_report,
    load_bounded_json, validate_analysis_report, validate_predicate_reporting,
)


def source(bundle):
    return LoadedReplicaSource(ReplicaInput(
        bundle, bundle.replica, bundle.campaign_id, bundle.producer_commit,
        bundle.provider_sha256, bundle.churn_precondition_met,
    ))


class A3AnalyzerTests(unittest.TestCase):
    def analyze(self):
        bundles = generate_synthetic_bundles(calibration_parameters())
        temporary = TemporaryDirectory(prefix="a3-test-")
        self.addCleanup(temporary.cleanup)
        frozen_path = Path(temporary.name) / "derivation-candidates.json"
        report = build_analysis([source(bundle) for bundle in bundles], frozen_path, lambda _digest: None)
        return report, load_bounded_json(frozen_path), bundles

    def test_all_four_layers_are_decisive_and_frozen_field_for_field(self) -> None:
        report, frozen, _ = self.analyze()
        statuses = (
            report["submodels"]["global_map"]["record"]["status"],
            report["submodels"]["global_map"]["conversion_inline"]["status"],
            report["submodels"]["global_map"]["extended_base"]["status"],
            report["submodels"]["tdef"]["pointer_pair"]["status"],
        )
        self.assertEqual(statuses, ("decisive_predicts_holdout",) * 4)
        compare_frozen_to_report(frozen, report)
        validate_analysis_report(report, frozen)

    def test_global_start_uses_tag_base_highwater_and_sentinel(self) -> None:
        bundle = generate_synthetic_bundle()
        view = View(bundle, WorkCounter())
        models, evidence = global_start_candidates(view, bundle.global_page)
        self.assertEqual(len(models), 1)
        model = models[0]
        self.assertEqual(model.record.start, 0)
        self.assertEqual(model.bit_polarity, "set_means_not_in_use")
        self.assertTrue(evidence["anchor"])
        for checkpoint in ("E0", "D_GROW_0128", "D_REGROW_0128"):
            state = decode_inline(
                view.page(checkpoint, model.record.page), model.record.start,
                model.record.end, model.bit_polarity,
            )
            self.assertIsNotNone(state)
            count = view.page_count(checkpoint)
            self.assertNotIn(count, state.in_use)
            self.assertTrue(set(range(state.base, count)) <= state.in_use)

    def test_cross_check_stops_before_tag_change(self) -> None:
        report, _frozen, bundle = self.analyze()
        model_document = report["submodels"]["global_map"]["record"]["model"]
        view = View(bundle[0], WorkCounter())
        models, _ = global_start_candidates(view, model_document["record"]["page"])
        transcript = polarity_cross_check(view, models[0])
        self.assertEqual(
            transcript.representation_change_stop.document(),
            {"left_checkpoint_id": "P_ABS_12288", "right_checkpoint_id": "P_ABS_16480"},
        )
        self.assertIsNone(transcript.first_violating_leg)

    def test_tdef_precondition_is_first_terminal(self) -> None:
        bundle = generate_synthetic_bundle()
        view = View(bundle, WorkCounter())
        with self.assertRaises(Abort) as caught:
            derive_tdef_candidates(view, (bundle.tdef_page,), False)
        self.assertEqual(caught.exception.predicate_id, "A3-CHURN-PRECONDITION")

    def test_holdout_reporting_exception_and_t5_rejection(self) -> None:
        rows = [
            {"predicate_id": predicate_id, "status": "pass", "layer": PREDICATES[predicate_id][1]}
            for predicate_id in PREDICATE_IDS
        ]
        validate_predicate_reporting(rows, [], any_decisive=True, any_holdout_failure=True)
        tampered = copy.deepcopy(rows)
        tampered[0]["status"] = "fail"
        with self.assertRaises(ValidationError):
            validate_predicate_reporting(tampered, [], any_decisive=True, any_holdout_failure=True)

    def test_plan_hash_is_the_frozen_a3_identity(self) -> None:
        self.assertEqual(
            PLAN_SHA256,
            "b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1",
        )


if __name__ == "__main__":
    unittest.main()
