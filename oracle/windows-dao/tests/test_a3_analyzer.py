"""Focused A3 analyzer, generator, freeze, and reporting contracts."""

from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol_validation import ValidationError  # noqa: E402
from a3_analysis import (  # noqa: E402
    LoadedReplicaSource, ReplicaInput, ReplicaLayer, _combine_replicas,
    _qualified_union, _same_model, build_analysis, recompute_only,
)
from a3_generator import (  # noqa: E402
    calibration_parameters, generate_synthetic_bundle, generate_synthetic_bundles,
)
from a3_layers import (  # noqa: E402
    ConversionModel, conversion_index, derive_tdef_candidates,
    pointer_windows, polarity_cross_check,
)
from a3_model import (  # noqa: E402
    CHECKPOINT_IDS, MAX_QUALIFIED_PAGES, MAX_RECORD_CANDIDATES, PAGE_SIZE,
    PER_PAGE_CANDIDATES, Abort, GlobalRecordModel, Record, View, WorkCounter,
    decode_inline, global_start_candidates,
)
from a3_spec import (  # noqa: E402
    LAYER_PREDICATE_SEQUENCES, PLAN_SHA256, PREDICATES, PREDICATE_IDS,
    R2_PLAN_SHA256, R3_PLAN_SHA256, R4_PLAN_SHA256, REVISION_PLAN_SHA256,
    compare_frozen_to_report, load_bounded_json,
    project_predicate_results, validate_analysis_report,
    validate_predicate_reporting,
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

    def test_synthetic_report_is_frozen_field_for_field(self) -> None:
        report, frozen, _ = self.analyze()
        compare_frozen_to_report(frozen, report)
        validate_analysis_report(report, frozen)
        union_pages = sum(report["qualified_page_counts"].values())
        self.assertEqual(
            report["record_candidates_examined"],
            union_pages * PER_PAGE_CANDIDATES,
        )
        self.assertEqual(
            report["analysis_work_units"],
            8 * report["record_candidates_examined"]
            + (PAGE_SIZE + 1) * report["qualified_page_counts"]["tdef"],
        )
        self.assertLessEqual(
            report["analysis_work_units"],
            8 * report["record_candidates_examined"]
            + 16 * (PAGE_SIZE + 1) * union_pages,
        )

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

    def test_tdef_signatures_compare_the_layout_page_field(self) -> None:
        bundle = generate_synthetic_bundle()
        windows = pointer_windows(View(bundle, WorkCounter()), bundle.tdef_page)
        self.assertEqual(
            windows.growth,
            {
                "u24le_page_then_u8_slot": (0,),
                "u8_slot_then_u24le_page": (),
            },
        )

    def test_tdef_slot_change_is_structural_exclusion(self) -> None:
        bundle = generate_synthetic_bundle()
        payloads = dict(bundle._payloads)
        ordered = {checkpoint: list(hashes) for checkpoint, hashes in bundle.ordered_page_sha256.items()}
        for checkpoint in CHECKPOINT_IDS:
            payload = bytearray(bundle.page_bytes(ordered[checkpoint][bundle.tdef_page]))
            churn_page = 24 + ((1 << 16) if checkpoint == "L_DELETE_ALL" else 0)
            payload[2044:2047] = churn_page.to_bytes(3, "little")
            if checkpoint == "D_DROP":
                payload[3] += 1
            encoded = bytes(payload)
            digest = hashlib.sha256(encoded).hexdigest()
            payloads[digest] = encoded
            ordered[checkpoint][bundle.tdef_page] = digest
        modified = replace(
            bundle,
            ordered_page_sha256=MappingProxyType({
                checkpoint: tuple(hashes) for checkpoint, hashes in ordered.items()
            }),
            _payloads=MappingProxyType(payloads),
        )
        view = View(modified, WorkCounter())
        windows = pointer_windows(view, modified.tdef_page)
        layout = "u24le_page_then_u8_slot"
        self.assertIn(0, windows.growth[layout])
        self.assertIn(2044, windows.churn[layout])
        with self.assertRaises(Abort) as caught:
            derive_tdef_candidates(view, (modified.tdef_page,), True)
        self.assertEqual(caught.exception.predicate_id, "A3-STRUCTURAL-EXCLUSION")
        self.assertEqual(caught.exception.survivor_count, 1)

    def test_r4_candidate_charging_accepts_exact_ceiling_and_rejects_one_over(self) -> None:
        work = WorkCounter()
        work.enumerate_pages(MAX_QUALIFIED_PAGES)
        work.enumerate_pages(MAX_QUALIFIED_PAGES, prefix_arrays_per_page=1)
        self.assertEqual(work.record_candidates, MAX_RECORD_CANDIDATES)
        self.assertEqual(
            work.value,
            MAX_RECORD_CANDIDATES * 8 + MAX_QUALIFIED_PAGES * (PAGE_SIZE + 1),
        )
        with self.assertRaises(Abort) as candidate_bound:
            work.enumerate_pages(1)
        self.assertEqual(candidate_bound.exception.predicate_id, "A3-RESOURCE-BOUND")
        with self.assertRaises(Abort) as qualified_bound:
            pages = tuple(range(MAX_QUALIFIED_PAGES + 1))
            _qualified_union((pages, pages))
        self.assertEqual(qualified_bound.exception.predicate_id, "A3-RESOURCE-BOUND")

    def test_r4_tdef_union_is_charged_when_either_precondition_passes(self) -> None:
        bundles = generate_synthetic_bundles(calibration_parameters())[:2]

        def inputs(preconditions: tuple[bool, bool]) -> list[ReplicaInput]:
            return [
                ReplicaInput(
                    bundle,
                    bundle.replica,
                    bundle.campaign_id,
                    bundle.producer_commit,
                    bundle.provider_sha256,
                    precondition,
                )
                for bundle, precondition in zip(bundles, preconditions)
            ]

        neither = recompute_only(inputs((False, False)))
        either = recompute_only(inputs((True, False)))
        self.assertEqual(
            neither["record_candidates_examined"],
            len(neither["qualified_pages"]["global_map"]) * PER_PAGE_CANDIDATES,
        )
        self.assertEqual(
            either["record_candidates_examined"],
            sum(len(pages) for pages in either["qualified_pages"].values())
            * PER_PAGE_CANDIDATES,
        )

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

    def test_r2_r3_r4_hashes_and_tdef_sequence_are_bound(self) -> None:
        self.assertEqual(
            R2_PLAN_SHA256,
            "3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1",
        )
        self.assertEqual(
            R3_PLAN_SHA256,
            "bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a",
        )
        self.assertEqual(
            R4_PLAN_SHA256,
            "939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea",
        )
        self.assertEqual(REVISION_PLAN_SHA256, R4_PLAN_SHA256)
        sequence = LAYER_PREDICATE_SEQUENCES["tdef_pointer_pair"]
        self.assertLess(
            sequence.index("A3-TDEF-RECORD-NONE"),
            sequence.index("A3-TDEF-PAGE-MULTIPLE"),
        )

    def test_r2_stops_projection_at_each_layer_terminal(self) -> None:
        layers = {
            "global_map_record": {
                "status": "decisive_predicts_holdout",
                "terminal_predicate_id": None,
            },
            "global_map_conversion_inline": {
                "status": "no_outcome",
                "terminal_predicate_id": "A3-POLARITY-CROSSCHECK",
            },
            "global_map_extended_base": {
                "status": "not_applicable",
                "terminal_predicate_id": None,
            },
            "tdef_pointer_pair": {
                "status": "no_outcome",
                "terminal_predicate_id": "A3-TDEF-RECORD-NONE",
            },
        }
        rows, terminals = project_predicate_results(layers)
        statuses = {row["predicate_id"]: row["status"] for row in rows}
        self.assertEqual(statuses["A3-TDEF-RECORD-NONE"], "fail")
        self.assertEqual(statuses["A3-TDEF-PAGE-MULTIPLE"], "not_applicable")
        self.assertEqual(statuses["A3-POINTER-VALIDITY"], "not_applicable")
        self.assertEqual(statuses["A3-STRUCTURAL-EXCLUSION"], "pass")
        self.assertEqual(
            terminals,
            ["A3-POLARITY-CROSSCHECK", "A3-TDEF-RECORD-NONE"],
        )

    def test_r3_global_replica_comparison_uses_minimum_slack(self) -> None:
        record = Record(1, 1915, 2048)
        left = GlobalRecordModel(record, "set_means_not_in_use", 92)
        right = GlobalRecordModel(record, "set_means_not_in_use", 93)
        self.assertEqual(_same_model(left, right).zero_suffix_slack_bytes, 92)

    def test_r3_g02_conversion_attribution(self) -> None:
        self.assertEqual(conversion_index(("inline", "inline", "indirect")), 2)
        with self.assertRaises(Abort) as missing:
            conversion_index(("indirect", "indirect"))
        self.assertEqual(missing.exception.predicate_id, "A3-CONVERSION-NONE")
        with self.assertRaises(Abort) as multiple:
            conversion_index(("inline", "neither", "indirect"))
        self.assertEqual(multiple.exception.predicate_id, "A3-CONVERSION-MULTIPLE")
        self.assertEqual(multiple.exception.survivor_count, 2)

    def test_r4_survivor_count_uses_replica_one_stopping_set(self) -> None:
        same_terminal = _combine_replicas(
            "global_map_record",
            (
                ReplicaLayer(None, 3, Abort("A3-GLOBAL-RECORD-MULTIPLE", 3)),
                ReplicaLayer(None, 2, Abort("A3-GLOBAL-RECORD-MULTIPLE", 2)),
            ),
        )
        self.assertEqual(same_terminal.survivor_count, 3)
        model = ConversionModel("P_ABS_16480", 20, 1, 2, 2, 2020, (14848, 16352))
        disagreement = _combine_replicas(
            "global_map_conversion_inline",
            (
                ReplicaLayer(model, 1, None),
                ReplicaLayer(None, 0, Abort("A3-CONVERSION-NONE")),
            ),
        )
        self.assertEqual(disagreement.abort.predicate_id, "A3-REPLICA-DISAGREEMENT")
        self.assertEqual(disagreement.survivor_count, 1)

    def test_r3_m1_m2_disagreement_statuses_stop_before_earliest_terminal(self) -> None:
        model = ConversionModel("P_ABS_16480", 20, 1, 2, 2, 2020, (14848, 16352))
        cases = (
            (ReplicaLayer(None, 0, Abort("A3-CONVERSION-NONE")), ReplicaLayer(model, 1, None)),
            (
                ReplicaLayer(None, 0, Abort("A3-CONVERSION-NONE")),
                ReplicaLayer(None, 2, Abort("A3-CONVERSION-MULTIPLE", 2)),
            ),
        )
        for outcomes in cases:
            with self.subTest(outcomes=outcomes):
                draft = _combine_replicas("global_map_conversion_inline", outcomes)
                layers = {
                    "global_map_record": {"status": "not_applicable", "terminal_predicate_id": None},
                    "global_map_conversion_inline": {
                        "status": "no_outcome",
                        "terminal_predicate_id": "A3-REPLICA-DISAGREEMENT",
                    },
                    "global_map_extended_base": {"status": "not_applicable", "terminal_predicate_id": None},
                    "tdef_pointer_pair": {"status": "not_applicable", "terminal_predicate_id": None},
                }
                reached = {key: frozenset() for key in layers}
                reached["global_map_conversion_inline"] = draft.reached
                rows, _ = project_predicate_results(layers, reached_by_layer=reached)
                statuses = {row["predicate_id"]: row["status"] for row in rows}
                self.assertEqual(statuses["A3-POLARITY-CROSSCHECK"], "pass")
                self.assertEqual(statuses["A3-CONVERSION-NONE"], "not_applicable")
                self.assertEqual(statuses["A3-REPLICA-DISAGREEMENT"], "fail")

    def test_r3_absent_page_is_not_snapshot_abort(self) -> None:
        bundle = generate_synthetic_bundle()
        view = View(bundle, WorkCounter())
        self.assertIsNone(view.page_optional("E0", view.page_count("E0")))

    def test_r3_m05_campaign_terminal_preempts_every_layer(self) -> None:
        layers = {
            key: {"status": "not_applicable", "terminal_predicate_id": None}
            for key in (
                "global_map_record",
                "global_map_conversion_inline",
                "global_map_extended_base",
                "tdef_pointer_pair",
            )
        }
        rows, terminals = project_predicate_results(
            layers,
            campaign_terminal="A3-SNAPSHOT-RECONSTRUCTION",
        )
        statuses = {row["predicate_id"]: row["status"] for row in rows}
        self.assertEqual(terminals, ["A3-SNAPSHOT-RECONSTRUCTION"])
        self.assertEqual(statuses["A3-IDLE-EQUALITY"], "pass")
        self.assertEqual(statuses["A3-SNAPSHOT-RECONSTRUCTION"], "fail")
        self.assertEqual(statuses["A3-RESOURCE-BOUND"], "not_applicable")
        self.assertEqual(statuses["A3-GLOBAL-PAGE-NONE"], "not_applicable")

    def test_r3_m06_holdout_abort_keeps_derivation_terminals(self) -> None:
        parameters = replace(
            calibration_parameters(),
            slot_activation_at_conversion=0,
        )
        bundles = generate_synthetic_bundles(parameters)
        bad_holdout = LoadedReplicaSource(ReplicaInput(
            bundles[2],
            3,
            "different-campaign",
            bundles[2].producer_commit,
            bundles[2].provider_sha256,
            bundles[2].churn_precondition_met,
        ))
        temporary = TemporaryDirectory(prefix="a3-holdout-abort-")
        self.addCleanup(temporary.cleanup)
        report = build_analysis(
            [source(bundles[0]), source(bundles[1]), bad_holdout],
            Path(temporary.name) / "derivation-candidates.json",
            lambda _digest: None,
        )
        record = report["submodels"]["global_map"]["record"]
        conversion = report["submodels"]["global_map"]["conversion_inline"]
        self.assertEqual(record["terminal_predicate_id"], "A3-HOLDOUT-PREDICTION")
        self.assertEqual(conversion["terminal_predicate_id"], "A3-SLOT-ACTIVATION")
        self.assertEqual(
            report["submodels"]["global_map"]["extended_base"]["status"],
            "not_applicable",
        )


if __name__ == "__main__":
    unittest.main()
