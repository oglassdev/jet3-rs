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
REVISION_PLAN = EXPERIMENT / "a2-allocation-maps-r2.plan.json"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"
PLAN_SHA256 = "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"
REVISION_PLAN_SHA256 = "977d352b6b7c042cf4d0f0cab793086842b3ad2b7da13b9c217020f00c5193c4"
DESIGN_INPUT_HASHES = {
    "a1-run12-ambiguity-diagnosis.md": "17d5ee28983ffc126feec63e7a7d8c7ffbc369e5f025193c9cd0d8404edf430d",
    "fable-review-findings.md": "ef77b917e2c7da6c8fc7a7c262352cf9ec208783bb4b71c63c2f3bb058a2950a",
    "fable-analyzer-schedule-audit.md": "c9f10f07b8b4b21da524de90819149d68fa387736dda4cb0cbcccfcb4f8ab603",
    "fable-a2-plan-review.md": "342e6cd56963de476639768368b5d187ecc95fb4eccd7b390ec4df5091c8e876",
    "fable-a2-plan-review-2.md": "620aad56198446be88ceeab3b0185e0e24eef1df6b94f365c230ae7305cb764d",
}
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

    def test_r2_reachability_revision_is_hash_pinned_and_additive(self) -> None:
        revision_bytes = REVISION_PLAN.read_bytes()
        revision = json.loads(revision_bytes)
        self.assertEqual(hashlib.sha256(revision_bytes).hexdigest(), REVISION_PLAN_SHA256)
        preregistration = revision["preregistration"]
        self.assertEqual(preregistration["revision_of"], self.plan["experiment_id"])
        self.assertEqual(preregistration["original_plan"]["sha256"], PLAN_SHA256)
        self.assertFalse(preregistration["acquisition_started"])
        reconciliation = revision["analyzer_dry_run_reconciliation"]
        exclusions = reconciliation["unreachable_by_construction"]
        self.assertEqual(
            [(row["predicate_id"], row["status"]) for row in exclusions],
            [("A2-INLINE-BOUNDARY-MULTIPLE", "unreachable_by_construction")],
        )
        self.assertTrue(revision["execution_effect"]["original_plan_remains_immutable"])
        self.assertFalse(revision["execution_effect"]["inline_suffix_rule_weakened"])
        provenance = PROVENANCE.read_text(encoding="utf-8")
        self.assertIn("### EXP-0041", provenance)
        self.assertIn(REVISION_PLAN_SHA256, provenance)

    def test_committed_design_inputs_are_hash_pinned(self) -> None:
        recorded = {
            Path(item["path"]).name: item["sha256"]
            for item in self.plan["preregistration"]["origin_disclosure"]["design_inputs"]
        }
        self.assertEqual(recorded, DESIGN_INPUT_HASHES)
        provenance = PROVENANCE.read_text(encoding="utf-8")
        for name, expected in DESIGN_INPUT_HASHES.items():
            path = EXPERIMENT / "design-inputs" / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
            self.assertIn(expected, provenance)

    def test_checkpoint_schedule_and_parallel_bounds_are_frozen(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 25)
        self.assertEqual(len(set(checkpoints)), 25)
        self.assertEqual(self.plan["bounds"]["planned_checkpoints_per_replica"], 25)
        self.assertEqual(self.plan["runtime_design"]["matrix_jobs"], 3)
        self.assertEqual(self.plan["bounds"]["worker_timeout_seconds_per_replica"], 1700)
        self.assertEqual(self.plan["bounds"]["fan_in_timeout_seconds"], 900)
        self.assertEqual(self.plan["runtime_design"]["per_replica_projection_seconds"], 725)
        self.assertEqual(self.plan["runtime_design"]["estimated_complete_wall_clock_seconds"], 1625)
        self.assertEqual(self.plan["runtime_design"]["hosted_wall_clock_target_seconds"], 1800)
        self.assertGreaterEqual(1700 / 725, 2)
        setup = self.plan["runtime_design"]["setup_and_dispatch_allowance_seconds"]
        self.assertEqual(1700 + 900 + setup, 2700)
        self.assertGreaterEqual(2700 / 1625, 1.5)
        self.assertEqual(len(self.plan["runtime_design"]["post_33_timing_inputs"]), 6)

    def test_d_relation_and_d_only_polarity_are_record_level(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(
            checkpoints[2:6],
            ["D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY", "D_REGROW_0128"],
        )
        self.assertIn("strictly greater", self.plan["checkpoint_design"]["d_regrowth_rule"])
        hypotheses = self.plan["hypotheses"]
        self.assertIn("record-level models", self.plan["question"])
        self.assertIn("no byte, record, or page equality", hypotheses["global_map_record_predicate"])
        self.assertIn("D-delimited global_map record only", hypotheses["bit_polarity_rule"])
        self.assertIn("after D alone freezes polarity", hypotheses["polarity_cross_check"])

    def test_page_qualification_and_record_enumeration_are_bounded(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        self.assertIn("hashes only", procedure["global_page_qualification"])
        self.assertIn("present at E0", procedure["tdef_page_qualification"])
        self.assertIn("absence at both endpoints is equality", procedure["global_page_qualification"])
        self.assertIn("{0,1,...,2048}", procedure["boundary_source"])
        self.assertIn("never inferred from the minimum or maximum", procedure["boundary_source"])
        self.assertIn("union of every physical page index", procedure["candidate_page_space"])
        self.assertIn("2049-entry prefix sums", procedure["prefix_sum_work_model"])
        self.assertIn("O(1)", procedure["prefix_sum_work_model"])
        self.assertIn("538,182,144", procedure["prefix_sum_work_model"])
        self.assertEqual(procedure["per_page_candidate_bound"], 2_098_176)
        self.assertEqual(procedure["max_qualified_pages_per_submodel"], 16)
        self.assertEqual(procedure["combined_record_candidate_bound"], 67_141_632)
        self.assertEqual(self.plan["bounds"]["max_record_candidates"], 67_141_632)
        self.assertEqual(self.plan["bounds"]["max_analysis_work_units"], 600_000_000)

    def test_global_record_end_has_byte_property_and_generator_slack(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        self.assertIn("ending at 2048", procedure["global_record_end_resolution"])
        self.assertIn("decodes entirely to not-in-use", procedure["global_record_end_resolution"])
        self.assertIn("raw 0xFF", procedure["global_record_end_resolution"])
        self.assertIn("raw 0x00", procedure["global_record_end_resolution"])
        self.assertIn("at least 16 bytes", procedure["global_record_end_resolution"])
        synthetic = self.plan["analyzer_dry_run_contract"]["synthetic_input"]
        self.assertEqual(synthetic["free_parameters"]["record_end_uniform_slack_bytes"], [16, 32, 64])
        self.assertIn("at least 32", synthetic["record_uniqueness_rule"])
        self.assertIn("shorter equivalent endpoints", synthetic["record_uniqueness_rule"])

    def test_models_have_separate_ownership_and_layered_outcomes(self) -> None:
        hypotheses = self.plan["hypotheses"]
        self.assertIn("use only E0", hypotheses["global_map_search"])
        self.assertIn("L/P/H", hypotheses["global_map_search"])
        self.assertIn("only for the growth-only and churn-only", hypotheses["tdef_record_search"])
        self.assertIn("no conversion", hypotheses["tdef_record_search"])
        layers = self.plan["decision_rules"]["layered_outcomes"]
        self.assertEqual(
            set(layers),
            {
                "global_map_record",
                "global_map_conversion_inline",
                "global_map_extended_base",
                "tdef_pointer_pair",
            },
        )
        self.assertIn("at least one layer is decisive", self.plan["decision_rules"]["scientific_outcome"])

    def test_conversion_slots_churn_inline_and_base_are_satisfiable(self) -> None:
        hypotheses = self.plan["hypotheses"]
        window = self.plan["checkpoint_design"]["transition_coverage"]["inline_to_indirect_conversion_window"]
        self.assertEqual(window[0], "L_REL_0064")
        self.assertIn("P_ABS_16480", window)
        self.assertEqual(window[-1], "H_REL_0904")
        self.assertIn("earliest valid indirect checkpoint", hypotheses["type1_conversion_predicate"])
        self.assertIn("one or two at conversion are valid", hypotheses["type1_rule"])
        self.assertIn("exactly two active references by H_REL_0904", hypotheses["type1_rule"])
        self.assertIn("DAO-rereads zero rows", hypotheses["delete_reinsert_only_pointer_predicate"])
        self.assertIn("only for the extended_base layer", hypotheses["extended_base_rule"])
        boundary = self.plan["inline_boundary_procedure"]
        self.assertIn("D-delimited global_map record", boundary["subject"])
        self.assertIn("enumerate every byte boundary", boundary["candidate_source"])
        self.assertIn("fill level", boundary["candidate_source"])

    def test_structural_exclusion_has_no_page_or_offset_blacklist(self) -> None:
        rule = self.plan["record_candidate_procedure"]["structural_exclusion_rule"]
        self.assertIn("identically on every page", rule)
        self.assertIn("never classified as headers by page number or offset", rule)
        self.assertIn("L_REL_1280 to L_DELETE_ALL to L_REINSERT_SAME", rule)
        self.assertIn("idle pair itself is byte-identical", rule)
        self.assertIn("No page number or byte offset", rule)
        self.assertNotIn("header_exclusion_source", self.plan["record_candidate_procedure"])

    def test_holdout_freeze_and_environment_identity_are_explicit(self) -> None:
        replicas = self.plan["replicas"]
        self.assertIn("first validates only replica 1 and 2", replicas["fan_in_rule"])
        self.assertIn("Only after that freeze", replicas["fan_in_rule"])
        self.assertIn("bounded pass/fail receipt", replicas["fan_in_rule"])
        self.assertEqual(len(self.plan["artifacts"]["replica_environments"]), 3)
        self.assertIn("provider CLSID and binary SHA-256", replicas["environment_identity_rule"])
        self.assertIn("may differ", replicas["environment_identity_rule"])

    def test_legacy_projection_is_explicit_and_non_applicable_rows_are_named(self) -> None:
        retained = self.plan["analyzer_dry_run_contract"]["retained_a1_input"]
        projection = retained["checkpoint_projection"]
        self.assertEqual(len(projection), 25)
        self.assertEqual(
            [row["a2_checkpoint"] for row in projection],
            self.plan["checkpoint_design"]["checkpoint_ids"],
        )
        by_a2 = {row["a2_checkpoint"]: row for row in projection}
        self.assertIsNone(by_a2["D_RECREATE_EMPTY"]["a1_checkpoint"])
        self.assertEqual(by_a2["L_DELETE_ALL"]["a1_checkpoint"], "L_DELETE_ALTERNATING")
        self.assertEqual(
            set(retained["not_applicable_predicates"]),
            {"A2-CHURN-PRECONDITION", "A2-CHURN-POINTER-NONE"},
        )

    def test_run12_and_synthetic_dry_run_acceptance_is_closed(self) -> None:
        contract = self.plan["analyzer_dry_run_contract"]
        retained = contract["retained_a1_input"]
        self.assertTrue(contract["must_complete_before_acquisition"])
        self.assertEqual(retained["max_input_page_blobs"], 55)
        self.assertIn("13 global qualifying pages in replica 1 and 13 in replica 2", retained["candidate_bound_assertion"])
        self.assertIn("67,141,632", retained["candidate_bound_assertion"])
        self.assertIn("600,000,000", retained["candidate_bound_assertion"])
        self.assertIn("last D-flipped byte offset 1954", retained["record_end_assertion"])
        self.assertIn("93 following bytes", retained["record_end_assertion"])
        self.assertIn("exactly one", retained["record_end_assertion"])
        self.assertIn("zero changes", retained["record_end_assertion"])
        synthetic = contract["synthetic_input"]
        self.assertIn("parse checkpoint_design", synthetic["generation_rule"])
        self.assertIn("every analyzer checkpoint equality", synthetic["arithmetic_rule"])
        self.assertEqual(synthetic["free_parameters"]["slot_activation_at_conversion"], [0, 1, 2])
        self.assertEqual(
            synthetic["free_parameters"]["bit_polarity"],
            ["set_means_in_use", "set_means_not_in_use"],
        )
        self.assertIn("before any A2 matrix job", contract["dispatch_gate"])
        self.assertIn("decisive-report validator case", contract["dispatch_gate"])
        self.assertFalse(self.plan["claims"]["a1_exploratory_input_is_a2_evidence"])
        self.assertFalse(self.plan["claims"]["synthetic_dry_run_is_a2_evidence"])

    def test_predicate_reason_mapping_is_bijective_and_cardinality_is_distinct(self) -> None:
        registry = self.plan["predicate_registry"]
        mappings = registry["mappings"]
        self.assertEqual(len(registry["ids"]), 34)
        self.assertEqual({item["predicate_id"] for item in mappings}, set(registry["ids"]))
        reasons = [item["reason"] for item in mappings]
        self.assertEqual(len(reasons), len(set(reasons)))
        required = {
            "no_physical_page_satisfies_global_transition_predicates",
            "multiple_physical_pages_satisfy_global_transition_predicates",
            "no_global_record_candidate",
            "multiple_global_record_boundaries_survive",
            "no_physical_page_satisfies_tdef_transition_predicates",
            "multiple_physical_pages_satisfy_tdef_transition_predicates",
            "no_tdef_record_candidate",
            "multiple_tdef_record_boundaries_survive",
        }
        self.assertLessEqual(required, set(reasons))

    def test_decisive_report_is_retained_and_validator_is_a_blocker(self) -> None:
        handling = self.plan["decisive_report_handling"]
        self.assertEqual(handling["analysis_report_artifact"], "retained_in_bundle")
        self.assertEqual(handling["bundle_status"], "decisive_pending_independent_validation")
        self.assertIn("completes the campaign successfully", handling["campaign_behavior"])
        self.assertIn(
            "a2_contract_validator_accepts_decisive_reports",
            self.plan["execution_gate"]["blocking_requirements"],
        )

    def test_bounds_sanity_is_preregistered(self) -> None:
        runtime = self.plan["runtime_design"]
        self.assertIn("20,701", runtime["bounds_sanity_basis"])
        self.assertIn("254 MiB", runtime["bounds_sanity_basis"])
        self.assertIn("150,000", runtime["bounds_sanity_basis"])
        self.assertIn("accept each exact ceiling and reject one over", runtime["bounds_sanity_basis"])

    def test_all_a2_json_documents_parse(self) -> None:
        documents = sorted(EXPERIMENT.glob("*.json"))
        self.assertEqual(len(documents), 11)
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
