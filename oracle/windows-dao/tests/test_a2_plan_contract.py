"""Focused hash and design contracts for the frozen DAO A2 preregistration."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a2"
PLAN = EXPERIMENT / "a2-allocation-maps.plan.json"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"
PLAN_SHA256 = "e303c76633078740ebc07ec507e251b01d7e64e69bfb83d765b88babdd48aebf"
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protocol_validation import lint_schema, validate_schema_value  # noqa: E402


class A2PlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_bytes = PLAN.read_bytes()
        cls.plan = json.loads(cls.plan_bytes)

    def test_exact_plan_hash_is_frozen_and_provenanced(self) -> None:
        self.assertEqual(hashlib.sha256(self.plan_bytes).hexdigest(), PLAN_SHA256)
        provenance = PROVENANCE.read_text(encoding="utf-8")
        self.assertIn("### EXP-0040", provenance)
        self.assertIn(PLAN_SHA256, provenance)

    def test_checkpoint_schedule_and_parallel_bounds_are_frozen(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 25)
        self.assertEqual(len(set(checkpoints)), 25)
        self.assertEqual(self.plan["bounds"]["planned_checkpoints_per_replica"], 25)
        self.assertIn("H_REL_0064", checkpoints)
        self.assertEqual(self.plan["runtime_design"]["matrix_jobs"], 3)
        self.assertEqual(self.plan["bounds"]["worker_timeout_seconds_per_replica"], 1800)
        self.assertEqual(self.plan["bounds"]["fan_in_timeout_seconds"], 900)
        self.assertEqual(self.plan["runtime_design"]["hosted_wall_clock_target_seconds"], 1800)

    def test_d_schedule_and_predicate_are_record_level_abac(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(
            checkpoints[2:6],
            ["D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128"],
        )
        regrowth = self.plan["checkpoint_design"]["d_regrowth_rule"]
        self.assertIn("post-recreate baseline", regrowth)
        self.assertIn("strictly greater", regrowth)
        predicate = self.plan["hypotheses"]["global_map_record_predicate"]
        self.assertIn("Let G be", predicate)
        self.assertIn("set", predicate)
        self.assertIn("no byte, record, or page equality", predicate)

    def test_record_candidates_are_finite_and_do_not_use_change_extrema(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        self.assertEqual(procedure["interval_ceiling_per_page"], 2_098_176)
        self.assertIn("{0,1,...,2048}", procedure["boundary_source"])
        self.assertIn("never inferred as the minimum or maximum", procedure["boundary_source"])
        self.assertIn("neither endpoint is required to change", procedure["stable_endpoint_rule"])
        self.assertIn("overlapping intervals", procedure["multiple_records_on_one_page"])
        self.assertIn("union of every physical page index", procedure["candidate_page_space"])
        self.assertIn("header_exclusion_source offset", procedure["negative_page_rule"])

    def test_global_and_tdef_searches_and_terminal_identifiers_are_distinct(self) -> None:
        self.assertIn("only from the D record-set", self.plan["hypotheses"]["global_map_search"])
        self.assertIn("separately", self.plan["hypotheses"]["tdef_record_search"])
        reasons = self.plan["decision_rules"]["no_scientific_outcome_identifiers"]
        self.assertIn("no_physical_page_satisfies_global_transition_predicates", reasons)
        self.assertIn("multiple_global_record_boundaries_survive", reasons)
        self.assertIn("multiple_physical_pages_satisfy_global_transition_predicates", reasons)
        self.assertIn("no_global_record_candidate", reasons)
        self.assertIn("no_physical_page_satisfies_tdef_transition_predicates", reasons)
        self.assertIn("no_tdef_record_candidate", reasons)
        self.assertIn("multiple_tdef_record_boundaries_survive", reasons)

    def test_conversion_churn_and_inline_boundary_sources_are_satisfiable(self) -> None:
        coverage = self.plan["checkpoint_design"]["transition_coverage"]
        window = coverage["inline_to_indirect_conversion_window"]
        self.assertIn("L_REL_0064", window)
        self.assertIn("P_ABS_04096", window)
        self.assertIn("P_ABS_16480", window)
        self.assertIn("H_REL_0904", window)
        self.assertIn("H_REL_0064", window)
        self.assertNotIn("L_DELETE_ALL", window)
        self.assertIn("earliest preregistered checkpoint", self.plan["hypotheses"]["type1_conversion_predicate"])
        self.assertIn("every row from L", self.plan["tables"]["row_algorithm"]["delete_rule"])
        arithmetic = self.plan["analyzer_dry_run_contract"]["synthetic_input"]["arithmetic_rule"]
        self.assertIn("growth-only pointer transition", arithmetic)
        self.assertIn("churn-only pointer transition", arithmetic)
        self.assertIn("Every equality between checkpoints", arithmetic)
        self.assertIn("fixed set", self.plan["inline_boundary_procedure"]["candidate_source"])
        self.assertIn("do not derive", self.plan["inline_boundary_procedure"]["candidate_source"])

    def test_decisive_report_is_retained_without_campaign_failure(self) -> None:
        handling = self.plan["decisive_report_handling"]
        self.assertEqual(handling["analysis_report_artifact"], "retained_in_bundle")
        self.assertEqual(handling["bundle_status"], "decisive_pending_independent_validation")
        self.assertIn("completes the campaign successfully", handling["campaign_behavior"])

    def test_dry_runs_are_plan_derived_and_non_evidential(self) -> None:
        contract = self.plan["analyzer_dry_run_contract"]
        self.assertTrue(contract["must_complete_before_acquisition"])
        self.assertIn("retained A1 page blobs", contract["retained_a1_input"]["required_behavior"])
        self.assertEqual(contract["retained_a1_input"]["max_input_page_blobs"], 55)
        self.assertIn("parse checkpoint_design", contract["synthetic_input"]["generation_rule"])
        self.assertIn("hand-typed", contract["synthetic_input"]["generation_rule"])
        self.assertIn("Every equality between checkpoints", contract["synthetic_input"]["arithmetic_rule"])
        self.assertIn("before any A2 matrix job", contract["dispatch_gate"])
        self.assertFalse(self.plan["claims"]["a1_exploratory_input_is_a2_evidence"])
        self.assertFalse(self.plan["claims"]["synthetic_dry_run_is_a2_evidence"])

    def test_analyzer_parameters_and_abort_contract_are_preregistered(self) -> None:
        synthetic = self.plan["analyzer_dry_run_contract"]["synthetic_input"]
        parameters = synthetic["free_parameters"]
        self.assertIn("every noninitial checkpoint ordinal plus never", parameters["conversion_ordinal"])
        self.assertEqual(parameters["slot_activation_at_conversion"], [0, 1, 2])
        self.assertEqual(parameters["bit_polarity"], ["set_means_in_use", "set_means_not_in_use"])
        self.assertEqual(parameters["anchor_fill_state"], ["empty", "partial", "full"])
        self.assertIn("source_conversion_ordinal 40", synthetic["run12_calibration_case"])
        self.assertIn("single named perturbation", synthetic["abort_reachability_rule"])
        self.assertEqual(len(self.plan["predicate_registry"]["ids"]), 29)

    def test_pointer_polarity_boundary_and_base_rules_are_closed(self) -> None:
        hypotheses = self.plan["hypotheses"]
        self.assertEqual(hypotheses["bit_polarity_candidates"], ["set_means_in_use", "set_means_not_in_use"])
        self.assertIn("D release/reallocation", hypotheses["bit_polarity_rule"])
        self.assertIn("L_DELETE_ALL DAO-rereads exactly zero rows", hypotheses["delete_reinsert_only_pointer_predicate"])
        self.assertIn("only at transition_coverage.pointer_validity_checkpoints", hypotheses["pointer_validity_rule"])
        self.assertIn("H_REL_0064", hypotheses["extended_base_rule"])
        self.assertIn("anchor-fill", self.plan["analyzer_dry_run_contract"]["synthetic_input"]["inline_boundary_rule"])

    def test_all_a2_json_documents_parse(self) -> None:
        documents = sorted(EXPERIMENT.glob("*.json"))
        self.assertEqual(len(documents), 9)
        for document in documents:
            with self.subTest(document=document.name):
                json.loads(document.read_bytes())

    def test_schemas_lint_and_plan_validates(self) -> None:
        for path in sorted(EXPERIMENT.glob("*.schema.json")):
            with self.subTest(schema=path.name):
                lint_schema(json.loads(path.read_bytes()))
        schema = json.loads((EXPERIMENT / "plan.schema.json").read_bytes())
        validate_schema_value(self.plan, schema, schema, "$")


if __name__ == "__main__":
    unittest.main()
