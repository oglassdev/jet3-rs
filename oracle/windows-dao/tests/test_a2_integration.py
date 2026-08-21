"""Analyzer integration against the optional A2 schedule generator."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_analysis import LoadedReplicaSource, ReplicaInput, build_analysis  # noqa: E402
from a2_model import CHECKPOINT_IDS, PAGE_SIZE, PLAN  # noqa: E402

try:
    from a2_generator import (  # type: ignore[import-not-found] # noqa: E402
        SyntheticParameters,
        generate_synthetic_bundles,
        run12_calibration_parameters,
    )
    from a2_spec import validate_analysis_report  # type: ignore[import-not-found] # noqa: E402
except ImportError:
    SyntheticParameters = None
    generate_synthetic_bundles = None
    run12_calibration_parameters = None
    validate_analysis_report = None

_BASELINE = object()


@unittest.skipUnless(generate_synthetic_bundles is not None, "a2_generator is not present")
class A2GeneratorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert run12_calibration_parameters is not None
        cls.baseline = run12_calibration_parameters()
        cls.free = PLAN["analyzer_dry_run_contract"]["synthetic_input"][
            "free_parameters"
        ]

    def parameters(
        self,
        *,
        conversion: int | None | object = _BASELINE,
        slots: int | None = None,
        polarity: str | None = None,
        fill: str | None = None,
        slack: int | None = None,
    ) -> object:
        assert SyntheticParameters is not None
        return SyntheticParameters(
            self.baseline.conversion_ordinal if conversion is _BASELINE else conversion,
            self.baseline.slot_activation_at_conversion if slots is None else slots,
            self.baseline.bit_polarity if polarity is None else polarity,
            self.baseline.anchor_fill_state if fill is None else fill,
            self.baseline.record_end_uniform_slack_bytes if slack is None else slack,
            self.baseline.delete_page_delta,
        )

    def analyze(self, parameters: object) -> dict[str, object]:
        assert generate_synthetic_bundles is not None
        sources = []
        for bundle in generate_synthetic_bundles(parameters):
            observation = bundle.documents[
                f"observations/replica-{bundle.replica:02d}.json"
            ]
            before = bundle.schedule.checkpoint("L_REL_1280")
            deleted = bundle.schedule.checkpoint("L_DELETE_ALL")
            replica = ReplicaInput(
                bundle,
                bundle.replica,
                observation["campaign_id"],
                observation["producer_commit"],
                observation["provider_sha256"],
                before.table_row_counts["L"] > 0
                and deleted.table_row_counts["L"] == 0,
            )
            sources.append(LoadedReplicaSource(replica))
        with tempfile.TemporaryDirectory() as directory:
            report = build_analysis(
                sources,
                Path(directory) / "analysis" / "derivation-candidates.json",
                lambda digest: self.assertEqual(len(digest), 64),
            )
        assert validate_analysis_report is not None
        validate_analysis_report(report)
        return report

    def assert_reason(self, layer: dict[str, object], reason: str) -> None:
        self.assertEqual(layer["status"], "no_outcome")
        self.assertEqual(layer["no_outcome_reasons"], [reason])

    def test_every_a2_conversion_ordinal_plus_never(self) -> None:
        for ordinal in (*range(1, len(CHECKPOINT_IDS)), None):
            with self.subTest(ordinal=ordinal):
                report = self.analyze(self.parameters(conversion=ordinal))
                global_map = report["submodels"]["global_map"]
                if ordinal in {1, 16, 24}:
                    self.assert_reason(global_map["record"], "idle_volatility")
                elif ordinal == 2:
                    self.assert_reason(
                        global_map["record"],
                        "no_physical_page_satisfies_global_transition_predicates",
                    )
                elif ordinal in {3, 4, 5}:
                    self.assert_reason(global_map["record"], "no_unique_bit_polarity")
                else:
                    self.assertEqual(
                        global_map["record"]["status"], "decisive_predicts_holdout"
                    )
                    if ordinal in {6, None}:
                        self.assert_reason(
                            global_map["conversion_inline"],
                            "missing_inline_to_indirect_conversion",
                        )
                    else:
                        self.assertEqual(
                            global_map["conversion_inline"]["status"],
                            "decisive_predicts_holdout",
                        )
                        if ordinal in {22, 23}:
                            self.assert_reason(
                                global_map["extended_base"],
                                "insufficient_base_discrimination",
                            )
                        else:
                            self.assertEqual(
                                global_map["extended_base"]["status"],
                                "decisive_predicts_holdout",
                            )

    def test_both_polarities(self) -> None:
        for polarity in self.free["bit_polarity"]:
            with self.subTest(polarity=polarity):
                report = self.analyze(self.parameters(polarity=polarity))
                record = report["submodels"]["global_map"]["record"]
                self.assertEqual(record["status"], "decisive_predicts_holdout")
                self.assertEqual(record["model"]["bit_polarity"], polarity)

    def test_slot_counts_and_final_activation(self) -> None:
        for slots in self.free["slot_activation_at_conversion"]:
            with self.subTest(slots=slots):
                report = self.analyze(self.parameters(slots=slots))
                conversion = report["submodels"]["global_map"]["conversion_inline"]
                if slots == 0:
                    self.assert_reason(conversion, "no_active_slot_at_conversion")
                else:
                    self.assertEqual(conversion["status"], "decisive_predicts_holdout")
        report = self.analyze(self.parameters(conversion=23, slots=1))
        self.assert_reason(
            report["submodels"]["global_map"]["conversion_inline"],
            "final_slot_activation_not_two",
        )

    def test_anchor_fill_does_not_move_the_recovered_boundary(self) -> None:
        for polarity in self.free["bit_polarity"]:
            full = self.analyze(self.parameters(polarity=polarity, fill="full"))
            expected = full["submodels"]["global_map"]["conversion_inline"][
                "model"
            ]["inline_boundary"]
            for fill in self.free["anchor_fill_state"]:
                with self.subTest(polarity=polarity, fill=fill):
                    report = self.analyze(
                        self.parameters(polarity=polarity, fill=fill)
                    )
                    conversion = report["submodels"]["global_map"][
                        "conversion_inline"
                    ]
                    self.assertEqual(
                        conversion["status"], "decisive_predicts_holdout"
                    )
                    self.assertEqual(conversion["model"]["inline_boundary"], expected)

    def test_every_record_end_slack(self) -> None:
        for slack in self.free["record_end_uniform_slack_bytes"]:
            with self.subTest(slack=slack):
                report = self.analyze(self.parameters(slack=slack))
                record = report["submodels"]["global_map"]["record"]
                self.assertEqual(record["status"], "decisive_predicts_holdout")
                self.assertEqual(record["model"]["zero_suffix_slack_bytes"], slack)
                conversion = report["submodels"]["global_map"]["conversion_inline"]
                self.assertEqual(
                    conversion["model"]["inline_boundary"], PAGE_SIZE - slack
                )


if __name__ == "__main__":
    unittest.main()
