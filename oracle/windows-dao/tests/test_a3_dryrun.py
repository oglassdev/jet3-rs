"""Focused contracts for the rebuilt A3 dry-run harness (no full sweep here)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a3_dryrun import DEFAULT_RETAINED_ROOT, _produced_layers, assess_case, CaseResult  # noqa: E402
from a3_dryrun_cases import DECISIVE, all_cases, conversion_expectation, reachability_targets  # noqa: E402
from a3_dryrun_pair import run_fixture  # noqa: E402
from a3_dryrun_replay import RetainedDerivationReplica, run_replay  # noqa: E402
from a3_generator import FREE, calibration_parameters  # noqa: E402
from a3_spec import PREDICATE_IDS, UNREACHABLE_PREDICATE_IDS  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


class A3DryRunTests(unittest.TestCase):
    @unittest.skipUnless(DEFAULT_RETAINED_ROOT.is_dir(), "EXP-0042 design-input bundle is absent")
    def test_exp0042_replay_is_derivation_only_and_exact(self) -> None:
        result = run_replay(DEFAULT_RETAINED_ROOT)
        self.assertTrue(all(result.checks.values()), result.checks)
        self.assertEqual(result.document()["replica_3_opened"], False)

    def test_replica_three_cannot_be_named(self) -> None:
        with self.assertRaises(ValidationError):
            RetainedDerivationReplica(Path("."), 3, {}, None)  # type: ignore[arg-type]

    def test_case_catalog_covers_axes_and_designates_reachable_predicates(self) -> None:
        cases = all_cases()
        parameters = [case.parameters for case in cases]
        self.assertEqual({p.conversion_ordinal for p in parameters}, set(range(1, 25)) | {None})
        for key in ("slot_activation_at_conversion", "bit_polarity", "anchor_fill_state", "record_end_uniform_slack_bytes", "global_record_start", "global_record_base", "inline_tag_at_anchor"):
            self.assertTrue(set(FREE[key]) <= {getattr(p, key) for p in parameters}, key)
        targets = reachability_targets(cases)
        self.assertEqual(set(targets), set(PREDICATE_IDS) - UNREACHABLE_PREDICATE_IDS)

    def test_expectations_come_from_schedule_arithmetic(self) -> None:
        expectation = conversion_expectation(calibration_parameters())
        self.assertEqual(expectation.layers["global_map_conversion_inline"], DECISIVE)
        self.assertEqual(expectation.model_fields["global_map_conversion_inline"]["conversion_checkpoint_id"], "P_ABS_16480")

    def test_one_fixture_runs_pair_on_identical_bundle(self) -> None:
        case = next(case for case in all_cases() if case.case_id == "missing_page_blob")
        with tempfile.TemporaryDirectory() as workspace:
            run = run_fixture(case.case_id, case.parameters, Path(workspace), "0" * 40, "2026-08-22T00:00:00Z", expected_rejection=case.expected_validator_rejection)
        result = CaseResult(case, run.pair.analyzer.report, run.pair.analyzer.error, run.pair.document(), {}, None, run.report_sha256)
        assess_case(result)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.executed_terminals, ["A3-SNAPSHOT-RECONSTRUCTION"])
        self.assertEqual(_produced_layers(run.pair.analyzer.report)["global_map_record"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
