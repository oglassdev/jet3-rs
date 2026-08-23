"""Focused hash and design contracts for the frozen DAO A3 preregistration."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a3"
A2_EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a2"
PLAN = EXPERIMENT / "a3-allocation-maps.plan.json"
REVISION_PLAN = EXPERIMENT / "a3-allocation-maps-r2.plan.json"
README = EXPERIMENT / "README.md"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"
PLAN_SHA256 = "b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1"
REVISION_PLAN_SHA256 = "3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1"
R3_PLAN = EXPERIMENT / "a3-allocation-maps-r3.plan.json"
R3_PLAN_SHA256 = "bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a"
R4_PLAN = EXPERIMENT / "a3-allocation-maps-r4.plan.json"
R4_PLAN_SHA256 = "939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea"
DRY_RUN_SCHEMA_SHA256 = "e7b054543529f4b2ac38cda7ae15fac80cf20bd6745f4fcd43cec02eabc9f13d"
PAIR_REVIEW_SHA256 = "70b9717d3b3387cbd2d4f1ceec3c8deff4f7706563af07eb2c5e77a6c05eab65"
DESIGN_INPUT_HASHES = {
    "a2-preregistration-pointer.md": "8f16e79686620e254b0ba98de4d7cb21611f84a3e9b5c84d9fd6428987f51632",
    "a2-independent-review-pointer.md": "2e89bb60aa5ac99d8f384836c75ce54c078817564d579d5411acd3bba8daae3b",
    "exp-0042-bundle-pointer.md": "9bcb4b3c7ca2b43abd44a38200042312156d14552908c6d00ec9a25b24178349",
}
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protocol_validation import lint_schema, validate_schema_value  # noqa: E402


class A3PlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_bytes = PLAN.read_bytes()
        cls.plan = json.loads(cls.plan_bytes)
        cls.a2_plan = json.loads(
            (A2_EXPERIMENT / "a2-allocation-maps.plan.json").read_bytes()
        )

    def test_exact_plan_hash_is_frozen_in_readme_and_provenance(self) -> None:
        self.assertEqual(hashlib.sha256(self.plan_bytes).hexdigest(), PLAN_SHA256)
        provenance = PROVENANCE.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertIn("### EXP-0044", provenance)
        self.assertIn(PLAN_SHA256, provenance)
        self.assertIn(PLAN_SHA256, readme)

    def test_r2_predicate_sequence_is_hash_pinned_and_additive(self) -> None:
        revision_bytes = REVISION_PLAN.read_bytes()
        revision = json.loads(revision_bytes)
        self.assertEqual(
            hashlib.sha256(revision_bytes).hexdigest(), REVISION_PLAN_SHA256
        )
        preregistration = revision["preregistration"]
        self.assertEqual(preregistration["revision_of"], self.plan["experiment_id"])
        self.assertEqual(preregistration["original_plan"]["sha256"], PLAN_SHA256)
        self.assertFalse(preregistration["acquisition_started"])
        self.assertIn("permitted", preregistration["amendment_permitted"])

        reconciliation = revision["predicate_evaluation_sequence_reconciliation"]
        self.assertEqual(
            reconciliation["campaign_evaluated_before_any_layer"],
            [
                "A3-IDLE-EQUALITY",
                "A3-SNAPSHOT-RECONSTRUCTION",
                "A3-RESOURCE-BOUND",
            ],
        )
        self.assertEqual(
            reconciliation["per_layer_ordered_predicates"],
            {
                "global_map.record": [
                    "A3-GLOBAL-PAGE-NONE",
                    "A3-GLOBAL-RECORD-NONE",
                    "A3-D-SET-RELATION",
                    "A3-GLOBAL-RECORD-END",
                    "A3-POLARITY-NONE",
                    "A3-POLARITY-MULTIPLE",
                    "A3-GLOBAL-PAGE-MULTIPLE",
                    "A3-GLOBAL-RECORD-MULTIPLE",
                    "A3-STRUCTURAL-EXCLUSION",
                    "A3-REPLICA-DISAGREEMENT",
                ],
                "global_map.conversion_inline": [
                    "A3-POLARITY-CROSSCHECK",
                    "A3-CONVERSION-NONE",
                    "A3-CONVERSION-MULTIPLE",
                    "A3-SLOT-ACTIVATION",
                    "A3-SLOT-FINAL",
                    "A3-POINTER-VALIDITY",
                    "A3-INLINE-BOUNDARY-NONE",
                    "A3-INLINE-BOUNDARY-MULTIPLE",
                    "A3-INLINE-SUFFIX",
                    "A3-STRUCTURAL-EXCLUSION",
                    "A3-REPLICA-DISAGREEMENT",
                ],
                "global_map.extended_base": [
                    "A3-BASE-DISCRIMINATION",
                    "A3-BASE-NONE",
                    "A3-BASE-MULTIPLE",
                    "A3-POINTER-VALIDITY",
                    "A3-REPLICA-DISAGREEMENT",
                ],
                "tdef.pointer_pair": [
                    "A3-TDEF-PAGE-NONE",
                    "A3-CHURN-PRECONDITION",
                    "A3-GROWTH-POINTER-NONE",
                    "A3-CHURN-POINTER-NONE",
                    "A3-TDEF-RECORD-NONE",
                    "A3-TDEF-PAGE-MULTIPLE",
                    "A3-TDEF-RECORD-MULTIPLE",
                    "A3-POINTER-MULTIPLE",
                    "A3-POINTER-VALIDITY",
                    "A3-STRUCTURAL-EXCLUSION",
                    "A3-REPLICA-DISAGREEMENT",
                ],
            },
        )
        self.assertIn("stops", reconciliation["layer_evaluation_rule"])
        self.assertIn("reached", reconciliation["applicable_layer_status_rule"])
        self.assertIn("unreached", reconciliation["layer_specific_status_rule"])
        self.assertIn("A3-HOLDOUT-PREDICTION", reconciliation["holdout_exception"])
        self.assertEqual(
            [
                (row["layer"], row["predicate_id"], row["position"])
                for row in reconciliation["base_text_consistency_review"][
                    "flagged_positions"
                ]
            ],
            [
                ("global_map.record", "A3-GLOBAL-RECORD-END", 4),
                ("global_map.conversion_inline", "A3-POLARITY-CROSSCHECK", 1),
                ("global_map.conversion_inline", "A3-INLINE-SUFFIX", 9),
                ("global_map.extended_base", "A3-POINTER-VALIDITY", 4),
            ],
        )
        self.assertTrue(revision["execution_effect"]["original_plan_remains_immutable"])
        self.assertFalse(revision["execution_effect"]["operational_rules_changed"])

        provenance = PROVENANCE.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertIn("### EXP-0045", provenance)
        self.assertIn(REVISION_PLAN_SHA256, provenance)
        self.assertIn(REVISION_PLAN_SHA256, readme)

    def test_r3_layer_semantics_are_hash_pinned_and_additive(self) -> None:
        revision_bytes = R3_PLAN.read_bytes()
        revision = json.loads(revision_bytes)
        self.assertEqual(hashlib.sha256(revision_bytes).hexdigest(), R3_PLAN_SHA256)
        preregistration = revision["preregistration"]
        self.assertEqual(revision["revision_id"], "DAO-A3-ALLOCATION-MAPS-001-R3")
        self.assertEqual(preregistration["provenance_entry"], "EXP-0046")
        self.assertEqual(preregistration["revision_of"], self.plan["experiment_id"])
        self.assertEqual(preregistration["original_plan"]["sha256"], PLAN_SHA256)
        self.assertEqual(
            preregistration["prior_revision"]["sha256"], REVISION_PLAN_SHA256
        )
        self.assertFalse(preregistration["acquisition_started"])
        self.assertIn("permitted", preregistration["amendment_permitted"])
        self.assertIn("not opened", preregistration["derivation_basis"])
        review = preregistration["design_inputs"][0]
        self.assertEqual(review["sha256"], PAIR_REVIEW_SHA256)
        review_path = ROOT / review["path"]
        self.assertEqual(
            hashlib.sha256(review_path.read_bytes()).hexdigest(), PAIR_REVIEW_SHA256
        )

        gaps = revision["layer_semantics_reconciliation"]["gaps"]
        self.assertEqual(
            [gap["gap_id"] for gap in gaps], [f"R3-G{i:02d}" for i in range(1, 11)]
        )
        for gap in gaps:
            self.assertIn("rule", gap)
            self.assertIn("single_implementation", gap)
            self.assertIn("exp_0042_worked_example", gap)
        rules = {gap["gap_id"]: gap["rule"] for gap in gaps}
        base = rules["R3-G01"]
        for text in (
            "Bytes [4,2048) are the extended bitmap: 16352 bits",
            "least-significant-bit-first",
            "[P_ABS_16480, H_REL_0064]",
            "slot_relative_expected_0_16352 = 16352*k + i",
            "decodes in-use (the map page occupies itself)",
            "F(k,r,i) = page_count then bit i decodes not-in-use",
            "flips in both directions are evaluated and none is ignored",
            "applicable iff the conversion layer holds a model",
        ):
            self.assertIn(text, base)
        example = gaps[0]["exp_0042_worked_example"]
        for text in ("offset 1860 = 0xFE", "bits 14849 through 14855", "bit 129", "1036", "refuted"):
            self.assertIn(text, example)
        conversion = rules["R3-G02"]
        self.assertLess(
            conversion.index("A3-CONVERSION-NONE"), conversion.index("A3-CONVERSION-MULTIPLE")
        )
        self.assertIn("count is not exactly 1", conversion)
        self.assertIn("only the terminal predicate and the schema-shaped model", rules["R3-G03"])
        inline = rules["R3-G04"]
        self.assertIn("b* = max over inline-phase checkpoints", inline)
        self.assertLess(
            inline.index("A3-INLINE-BOUNDARY-NONE"), inline.index("A3-INLINE-SUFFIX (")
        )
        record = rules["R3-G05"]
        self.assertIn("end = 2048 only", record)
        self.assertIn("exactly those three anchors", record)
        for stage in range(1, 11):
            self.assertIn(f"Stage {stage} ", record)
        self.assertLess(
            record.index("A3-GLOBAL-RECORD-END"), record.index("A3-POLARITY-MULTIPLE")
        )
        self.assertLess(
            record.index("A3-GLOBAL-PAGE-MULTIPLE"), record.index("A3-GLOBAL-RECORD-MULTIPLE")
        )
        tdef = rules["R3-G06"]
        self.assertIn("four-byte ranges do not overlap", tdef)
        self.assertLess(tdef.index("Stages 4 and 5"), tdef.index("position 9 A3-POINTER-VALIDITY"))
        self.assertLess(
            tdef.index("position 9 A3-POINTER-VALIDITY"),
            tdef.index("Position 10 A3-STRUCTURAL-EXCLUSION"),
        )
        self.assertIn("whose tag is 1", rules["R3-G07"])
        self.assertIn("global_map_record, global_map_conversion_inline, global_map_extended_base, tdef_pointer_pair", rules["R3-G08"])
        self.assertIn("Replica 3 is opened iff at least one layer holds a frozen model", rules["R3-G08"])
        holdout = rules["R3-G09"]
        self.assertIn("never required to equal the holdout's measured slack", holdout)
        self.assertIn("exactly the frozen slot_reference_pages", holdout)
        self.assertIn("uniqueness is not re-established", holdout)
        self.assertIn("evaluates as failed for that candidate only", rules["R3-G10"])

        reachability = revision["predicate_reachability_reconciliation"]
        self.assertEqual(
            [row["predicate_id"] for row in reachability["unreachable_by_construction"]],
            ["A3-POLARITY-NONE", "A3-INLINE-BOUNDARY-MULTIPLE", "A3-INLINE-BOUNDARY-NONE"],
        )
        for row in reachability["unreachable_by_construction"]:
            self.assertEqual(row["status"], "unreachable_by_construction")
            self.assertIn(row["predicate_id"], self.plan["predicate_registry"]["ids"])
        structural = reachability["layer_unreachable_but_id_reachable"][0]
        self.assertEqual(structural["predicate_id"], "A3-STRUCTURAL-EXCLUSION")
        self.assertEqual(
            structural["unreachable_layers"],
            ["global_map.record", "global_map.conversion_inline"],
        )
        self.assertIn("31 ids", reachability["effective_reachability_rule"])

        dry_run = revision["dry_run_honesty_clause"]
        self.assertIn("executed fixture transcript", dry_run["reachability_by_transcript"])
        self.assertIn("each of the D, L, P, and H phases", dry_run["replica_3_independent_overshoot"])
        self.assertIn("full-sweep agreement", dry_run["pair_acceptance_gate"])
        self.assertIn("dry-run/a3-pair-agreement.json", dry_run["pair_acceptance_gate"])
        self.assertIn("constant rejected=true is a dry-run failure", dry_run["tamper_suite_execution"])

        effect = revision["execution_effect"]
        self.assertTrue(effect["original_plan_remains_immutable"])
        self.assertTrue(effect["original_schemas_remain_immutable"])
        self.assertTrue(effect["r2_sequences_remain_immutable"])
        self.assertFalse(effect["acquisition_authorized"])

        provenance = PROVENANCE.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertIn("### EXP-0046", provenance)
        for digest in (R3_PLAN_SHA256, PAIR_REVIEW_SHA256):
            self.assertIn(digest, provenance)
            self.assertIn(digest, readme)

    def test_r4_survivor_count_and_replay_blob_bound_are_hash_pinned(self) -> None:
        revision_bytes = R4_PLAN.read_bytes()
        revision = json.loads(revision_bytes)
        self.assertEqual(hashlib.sha256(revision_bytes).hexdigest(), R4_PLAN_SHA256)
        preregistration = revision["preregistration"]
        self.assertEqual(revision["revision_id"], "DAO-A3-ALLOCATION-MAPS-001-R4")
        self.assertEqual(preregistration["provenance_entry"], "EXP-0047")
        self.assertEqual(preregistration["revision_of"], self.plan["experiment_id"])
        self.assertEqual(preregistration["original_plan"]["sha256"], PLAN_SHA256)
        self.assertEqual(
            [row["sha256"] for row in preregistration["prior_revisions"]],
            [REVISION_PLAN_SHA256, R3_PLAN_SHA256],
        )
        self.assertFalse(preregistration["acquisition_started"])
        self.assertIn("permitted", preregistration["amendment_permitted"])
        self.assertIn("replica 3 was not opened", preregistration["derivation_basis"])

        survivor = revision["survivor_count_reconciliation"]
        self.assertEqual(survivor["gap_id"], "R4-S01")
        rule = survivor["rule"]
        for text in (
            "measured in derivation replica 1",
            "the multiplicity (at least 2) for every MULTIPLE terminal",
            "0 for every terminal that fires on an empty set",
            "1 for every terminal that fires on the single surviving candidate",
            "An inapplicable layer counts 0",
        ):
            self.assertIn(text, rule)
        sequences = json.loads(REVISION_PLAN.read_bytes())[
            "predicate_evaluation_sequence_reconciliation"
        ]["per_layer_ordered_predicates"]
        table = survivor["per_terminal_counts"]
        self.assertEqual(set(table), set(sequences))
        for layer, ordered in sequences.items():
            rows = table[layer]
            self.assertEqual([row["predicate_id"] for row in rows], ordered)
            for row in rows:
                if row["predicate_id"].endswith("-MULTIPLE"):
                    self.assertIn("at least 2", row["count"])
                elif row["predicate_id"].endswith("-NONE"):
                    self.assertTrue(row["count"].startswith("0"))
        self.assertIn("A3-POLARITY-MULTIPLE with derivation_survivor_count 2", survivor["pair_gate_worked_example"])

        gaps = {gap["gap_id"]: gap for gap in revision["replay_blob_bound_reconciliation"]["gaps"]}
        self.assertEqual(set(gaps), {"R4-B01", "R4-B02"})
        bound = gaps["R4-B01"]["rule"]
        self.assertIn("Its ceiling is 1800", bound)
        self.assertIn("2 derivation replicas x 25 planned checkpoints x (16 + 16 + 4)", bound)
        self.assertEqual(self.plan["bounds"]["max_qualified_pages_per_submodel"], 16)
        self.assertEqual(self.plan["bounds"]["planned_checkpoints_per_replica"], 25)
        self.assertEqual(2 * 25 * (16 + 16 + 4), 1800)
        example = gaps["R4-B01"]["exp_0042_worked_example"]
        for text in ("{0, 1, 20, 21}", "{0, 1, 23, 24}", "50 distinct blobs", "71", "exactly 81"):
            self.assertIn(text, example)
        self.assertIn("exactly 81 unique page blobs", gaps["R4-B01"]["exp_0042_candidate_bound_assertion"])
        self.assertEqual(
            self.plan["analyzer_dry_run_contract"]["historical_a1_input_not_required_by_a3"]["max_input_page_blobs"],
            55,
        )

        schema_bytes = (EXPERIMENT / "dry-run-report.schema.json").read_bytes()
        self.assertEqual(hashlib.sha256(schema_bytes).hexdigest(), DRY_RUN_SCHEMA_SHA256)
        self.assertEqual(
            json.loads(schema_bytes)["properties"]["input_page_blob_count"]["maximum"], 1800
        )
        schema_rule = gaps["R4-B02"]["rule"]
        self.assertIn(DRY_RUN_SCHEMA_SHA256, schema_rule)
        self.assertIn("non-evidential", schema_rule)

        candidates = revision["record_candidate_count_reconciliation"]
        self.assertEqual(candidates["gap_id"], "R4-C01")
        per_page = self.plan["bounds"]["max_record_candidates_per_page"]
        ceiling = self.plan["bounds"]["max_record_candidates"]
        self.assertEqual(32 * per_page, ceiling)
        self.assertEqual(
            self.plan["record_candidate_procedure"]["combined_record_candidate_bound"], ceiling
        )
        report_schema = json.loads((EXPERIMENT / "analysis-report.schema.json").read_bytes())
        self.assertEqual(
            report_schema["properties"]["record_candidates_examined"]["maximum"], ceiling
        )
        self.assertLess(8 * ceiling + 32 * 16 * 2049, self.plan["bounds"]["max_analysis_work_units"])
        for text in (
            "counted once however many derivation replicas enumerated it",
            "supersedes the record_candidates_examined sentence of R3-G08",
            "must additionally enforce bounds.max_record_candidates",
        ):
            self.assertIn(text, candidates["rule"])
        self.assertIn("67,141,632 = combined_record_candidate_bound", candidates["bound_consistency_derivation"])
        self.assertIn("16,785,408", candidates["exp_0042_worked_example"])

        defects = revision["analyzer_defects_not_resolved_here"]
        self.assertEqual([row["id"] for row in defects["items"]], ["tdef_u24_pointer_layout"])

        effect = revision["execution_effect"]
        self.assertTrue(effect["original_plan_remains_immutable"])
        self.assertTrue(effect["original_evidence_schemas_remain_immutable"])
        self.assertTrue(effect["dry_run_report_schema_changed"])
        self.assertFalse(effect["r3_rules_remain_immutable"])
        self.assertIn("R4-C01", effect["r3_rules_superseded"])
        self.assertFalse(effect["bounds_changed"])
        self.assertFalse(effect["acquisition_authorized"])

        provenance = PROVENANCE.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertIn("### EXP-0047", provenance)
        for digest in (R4_PLAN_SHA256, DRY_RUN_SCHEMA_SHA256):
            self.assertIn(digest, provenance)
            self.assertIn(digest, readme)

    def test_design_input_pointers_and_targets_are_hash_pinned(self) -> None:
        recorded = {
            Path(item["path"]).name: item["sha256"]
            for item in self.plan["preregistration"]["origin_disclosure"][
                "design_inputs"
            ]
        }
        self.assertEqual(recorded, DESIGN_INPUT_HASHES)
        provenance = PROVENANCE.read_text(encoding="utf-8")
        for name, expected in DESIGN_INPUT_HASHES.items():
            path = EXPERIMENT / "design-inputs" / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
            self.assertIn(expected, provenance)
        pointer = (EXPERIMENT / "design-inputs" / "exp-0042-bundle-pointer.md").read_text()
        self.assertIn("7e58dc5e3c8424110897053cdfeab703b0e1d15fde2dfd4d8235efd62da43dc7", pointer)
        self.assertIn("9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46", pointer)

    def test_a2_schedule_roles_rows_capture_and_bounds_are_unchanged(self) -> None:
        a2 = self.a2_plan
        a3 = self.plan
        self.assertEqual(a3["tables"], a2["tables"])
        self.assertEqual(a3["page_capture"], a2["page_capture"])
        self.assertEqual(a3["bounds"], a2["bounds"])
        self.assertEqual(a3["replicas"], a2["replicas"])
        self.assertEqual(a3["runtime_design"], a2["runtime_design"])
        for key in (
            "count",
            "adaptive_checkpoints_allowed",
            "all_checkpoints_closed_and_quiescent",
            "d_growth_rule",
            "d_regrowth_rule",
            "relative_growth_rule",
            "absolute_growth_rule",
            "checkpoint_ids",
            "idle_pairs",
        ):
            self.assertEqual(a3["checkpoint_design"][key], a2["checkpoint_design"][key])
        for key in a2["checkpoint_design"]["transition_coverage"]:
            self.assertEqual(
                a3["checkpoint_design"]["transition_coverage"][key],
                a2["checkpoint_design"]["transition_coverage"][key],
            )
        self.assertEqual(len(a3["checkpoint_design"]["checkpoint_ids"]), 25)

    def test_record_layout_and_start_resolution_are_operational(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        layout = procedure["global_record_layout"]
        indirect = procedure["global_record_indirect_layout"]
        start = procedure["global_record_start_resolution"]
        self.assertIn("byte start is the one-byte representation tag", layout)
        self.assertIn("[start+1,start+5)", layout)
        self.assertIn("least-significant-bit-first", layout)
        self.assertIn("base+i", layout)
        for checkpoint in ("E0", "D_GROW_0128", "D_REGROW_0128"):
            self.assertIn(checkpoint, start)
        self.assertIn("tag == 0", start)
        self.assertIn("base <= page_count < base+capacity", start)
        self.assertIn("page_count itself to be not-in-use", start)
        self.assertIn("both derivation replicas", start)
        self.assertIn("representation-anchoring predicate", start)
        self.assertEqual(
            indirect,
            "when byte start (the tag) is 1, bytes [start+1,start+5) are "
            "slot-0 and bytes [start+5,start+9) are slot-1, each one unsigned "
            "little-endian u32; a slot is active when its u32 is nonzero; the "
            "slot's reference is that u32 interpreted as a physical page number; "
            "bytes [start+9,end) are the indirect suffix and must decode to zero "
            "at every checkpoint at or after the conversion checkpoint. Any tag "
            "other than 0 or 1 at a checkpoint in the conversion window "
            "classifies that checkpoint as neither inline nor indirect.",
        )

    def test_exp_0042_record_start_example_is_numeric_and_non_evidential(self) -> None:
        example = self.plan["record_candidate_procedure"][
            "global_record_start_worked_example"
        ]
        for text in (
            "[1915,2048)",
            "[1916,1920)",
            "[1920,2048)",
            "capacity 1024",
            "29, 157, and 285",
            "1,935 starts 0 through 1934",
            "anchors start 1915",
            "cannot satisfy A3",
        ):
            self.assertIn(text, example)

    def test_no_equality_rule_is_amended_for_representation_anchor(self) -> None:
        predicate = self.plan["hypotheses"]["global_map_record_predicate"]
        self.assertIn("No byte, record, physical page, or page count", predicate)
        self.assertIn("within-snapshot tag/base/highwater", predicate)
        self.assertIn("not cross-checkpoint equality", predicate)

    def test_polarity_cross_check_has_frozen_legs_and_stop_rule(self) -> None:
        legs = self.plan["checkpoint_design"]["transition_coverage"][
            "polarity_cross_check_legs"
        ]
        self.assertEqual(legs[0], ["D_REGROW_0128", "L_REL_0064"])
        self.assertIn(["P_ABS_12288", "P_ABS_16480"], legs)
        self.assertEqual(legs[-1], ["H_REL_0896", "H_REL_0904"])
        rule = self.plan["hypotheses"]["polarity_cross_check"]
        self.assertIn("both record tags are 0 and equal", rule)
        self.assertIn("[page_count(left),page_count(right))", rule)
        self.assertIn("representable by both snapshots", rule)
        self.assertIn("passes vacuously", rule)
        self.assertIn("stop before interpreting", rule)
        self.assertIn("first violating leg", rule)
        self.assertIn("lowest violating page", rule)
        self.assertIn("including a violating leg", rule)
        self.assertIn("never contains a representation_change_stop leg", rule)
        self.assertIn("required page p >= 65536", rule)

    def test_exp_0042_cross_check_example_and_replay_are_exact(self) -> None:
        example = self.plan["hypotheses"]["polarity_cross_check_worked_example"]
        self.assertIn("violates at pages 1021, 1022, and 1023", example)
        self.assertIn(
            "first_violating_leg [L_REL_0512, L_REL_0768]", example
        )
        self.assertIn("first_violating_page 1021", example)
        self.assertIn("evaluated_legs of length 3", example)
        self.assertIn("representation_change_stop null", example)
        self.assertIn("tag change 0 to 1 is never reached", example)
        replay = self.plan["analyzer_dry_run_contract"][
            "retained_exp_0042_input"
        ]["required_assertions"][2]
        self.assertEqual(
            replay,
            "evaluate polarity_cross_check_legs in order, carry the exact "
            "evaluated-leg transcript, and stop at the first violating leg "
            "[L_REL_0512, L_REL_0768] with first_violating_page 1021 and "
            "representation_change_stop null in both derivation replicas; never "
            "reinterpret any later leg's tag or u32 bytes as bitmap bits",
        )
        consequence = (
            "the `global_map_conversion_inline` and "
            "`global_map_extended_base` layers are terminal at leg 3 by "
            "construction; only `global_map_record` and `tdef_pointer_pair` can "
            "reach holdout"
        )
        readme = " ".join(README.read_text(encoding="utf-8").split())
        provenance = " ".join(PROVENANCE.read_text(encoding="utf-8").split())
        self.assertIn(consequence, readme)
        self.assertIn(consequence, provenance)

    def test_indirect_layout_is_used_by_every_dependent_rule_and_schema(self) -> None:
        hypotheses = self.plan["hypotheses"]
        for name in (
            "type1_conversion_predicate",
            "type1_rule",
            "pointer_validity_rule",
            "extended_base_rule",
        ):
            self.assertIn("global_record_indirect_layout", hypotheses[name])
        survival = self.plan["inline_boundary_procedure"]["survival_rule"]
        self.assertIn("[start+1,start+5) as slot-0", survival)
        self.assertIn("[start+5,start+9) as slot-1", survival)
        self.assertIn("[start+9,end)", survival)
        self.assertIn("capacity 8*(b-(start+5))", survival)
        self.assertIn("raw 0xFF for set_means_not_in_use", survival)
        self.assertIn("0x00 for set_means_in_use", survival)
        self.assertIn("indirect suffix [start+9,end) must be raw 0x00", survival)
        disclosure = self.plan["preregistration"]["origin_disclosure"][
            "prediction_not_rediscovery_disclosure"
        ]
        self.assertIn("01 | 00 3A 00 00 | E0 3F 00 00", disclosure)
        self.assertIn("14848", disclosure)
        self.assertIn("16352", disclosure)
        for schema_name in (
            "analysis-report.schema.json",
            "derivation-candidates.schema.json",
        ):
            schema = json.loads((EXPERIMENT / schema_name).read_bytes())
            model = schema["$defs"]["conversionModel"]
            self.assertIn("indirect_tag", model["required"])
            self.assertEqual(model["properties"]["indirect_tag"]["const"], 1)
            slots = model["properties"]["slot_reference_pages"]
            self.assertEqual((slots["minItems"], slots["maxItems"]), (2, 2))
            self.assertEqual(slots["items"]["minimum"], 0)
        dry_run = json.loads((EXPERIMENT / "dry-run-report.schema.json").read_bytes())
        calibration = dry_run["properties"]["parameter_coverage"]["properties"][
            "exp_0042_calibration"
        ]["anyOf"][1]
        expected_values = {
            "indirect_tag": 1,
            "slot_0_reference_page": 14848,
            "slot_1_reference_page": 16352,
            "indirect_prefix_hex": "01003a0000e03f0000",
        }
        for field, expected in expected_values.items():
            self.assertIn(field, calibration["required"])
            self.assertEqual(calibration["properties"][field]["const"], expected)

    def test_reviewed_operational_rules_are_pinned(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        start = procedure["global_record_start_resolution"]
        self.assertIn("page index document's page_count field", start)
        self.assertIn("len(ordered_page_sha256)", start)
        self.assertIn("observation's actual_file_pages", start)
        self.assertIn("global_set_relation_not_satisfied is emitted only", start)
        self.assertIn("no_global_record_candidate is emitted when", start)
        self.assertIn("some end in (start+5, 2048]", start)
        self.assertIn("after end resolution", start)
        end = procedure["global_record_end_resolution"]
        for checkpoint in (
            "E0",
            "D_GROW_0128",
            "D_DROP",
            "D_RECREATE_EMPTY",
            "D_REGROW_0128",
        ):
            self.assertIn(checkpoint, end)
        self.assertIn("not identical across all five", end)
        churn = procedure["tdef_no_outcome_ordering"]
        self.assertIn("table_row_counts for role L is 0", churn)
        self.assertIn("dao_reread row_count for role L is not 0", churn)
        polarity = self.plan["hypotheses"]["bit_polarity_rule"]
        for relation in ("Gp ∩ X = ∅", "Gp ∩ Y = ∅", "Gp ⊆ R", "R \\ G ≠ ∅"):
            self.assertIn(relation, polarity)
        pointer = self.plan["hypotheses"]["pointer_validity_rule"]
        self.assertIn("TDEF layout", pointer)
        self.assertIn("u24 page field", pointer)
        self.assertIn("holdout replica alone", pointer)
        cross_check = self.plan["hypotheses"]["polarity_cross_check"]
        self.assertIn(
            "emit pointer_validity_failure (A3-POINTER-VALIDITY) for the "
            "global_map.conversion_inline layer",
            cross_check,
        )
        terminal = self.plan["decision_rules"]["terminal_disambiguation"]
        self.assertIn("RECORD-MULTIPLE", terminal)
        self.assertIn("PAGE-MULTIPLE", terminal)
        self.assertLess(
            terminal.index("PAGE-MULTIPLE"), terminal.index("page multiplicity")
        )

    def test_cross_check_transcript_is_required_by_both_schemas(self) -> None:
        report = json.loads((EXPERIMENT / "analysis-report.schema.json").read_bytes())
        frozen = json.loads((EXPERIMENT / "derivation-candidates.schema.json").read_bytes())
        self.assertIn("polarity_cross_check", report["required"])
        self.assertIn("polarity_cross_check", frozen["required"])
        for schema in (report, frozen):
            required = schema["$defs"]["crossCheck"]["required"]
            self.assertEqual(
                required,
                [
                    "evaluated_legs",
                    "representation_change_stop",
                    "first_violating_leg",
                    "first_violating_page",
                ],
            )

    def test_frozen_candidate_set_has_fixed_shape_and_report_comparison(self) -> None:
        schema = json.loads((EXPERIMENT / "derivation-candidates.schema.json").read_bytes())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["layers"]["required"]),
            {
                "global_map_record",
                "global_map_conversion_inline",
                "global_map_extended_base",
                "tdef_pointer_pair",
            },
        )
        freeze = self.plan["decision_rules"]["freeze_rule"]
        self.assertIn("parsed frozen values", freeze)
        self.assertIn("qualified_page_counts equals array lengths", freeze)
        self.assertIn("Hash equality alone is insufficient", freeze)
        report = json.loads((EXPERIMENT / "analysis-report.schema.json").read_bytes())
        self.assertIn("qualified_pages", report["required"])

    def test_predicate_reporting_is_total_and_handles_applicable_layers(self) -> None:
        registry = self.plan["predicate_registry"]
        ids = registry["ids"]
        mappings = registry["mappings"]
        self.assertEqual(len(ids), 34)
        self.assertEqual(len(set(ids)), 34)
        self.assertEqual({row["predicate_id"] for row in mappings}, set(ids))
        rule = registry["reporting_rule"]
        self.assertIn("exactly 34 entries", rule)
        self.assertIn("literal string applicable_layer", rule)
        self.assertIn("terminal_predicate_ids lists it once", rule)
        self.assertIn("A3-HOLDOUT-PREDICTION is pass iff", rule)
        self.assertIn("report-level predicate remains pass", rule)
        self.assertIn(
            "excludes A3-HOLDOUT-PREDICTION whenever any layer is decisive", rule
        )
        freeze = self.plan["decision_rules"]["freeze_rule"]
        self.assertIn("equals that layer's terminal predicate id", freeze)
        self.assertIn("report layer's derivation-time values", freeze)
        self.assertIn("compares as empty/null against the frozen layer", freeze)
        self.assertIn(
            "holdout_prediction_failure is the only reason permitted", freeze
        )
        report = json.loads((EXPERIMENT / "analysis-report.schema.json").read_bytes())
        predicates = report["properties"]["predicate_results"]
        self.assertEqual((predicates["minItems"], predicates["maxItems"]), (34, 34))
        for layer in (
            "globalRecordLayer",
            "conversionLayer",
            "baseLayer",
            "tdefLayer",
        ):
            self.assertIn("terminal_predicate_id", report["$defs"][layer]["required"])

    def test_tdef_no_outcome_order_is_exact(self) -> None:
        rule = self.plan["record_candidate_procedure"]["tdef_no_outcome_ordering"]
        stages = [
            "(1) churn precondition",
            "(2) growth windows",
            "(3) churn windows",
            "(4) records",
            "(5) multiplicity",
        ]
        offsets = [rule.index(stage) for stage in stages]
        self.assertEqual(offsets, sorted(offsets))
        for reason in (
            "legacy_churn_precondition_not_met",
            "no_growth_only_pointer_candidate",
            "no_delete_reinsert_only_pointer_candidate",
            "no_tdef_record_candidate",
            "multiple_tdef_record_boundaries_survive",
            "multiple_pointer_models_survive",
        ):
            self.assertIn(reason, rule)

    def test_pointer_validity_window_is_fully_ordered(self) -> None:
        rule = self.plan["hypotheses"]["pointer_validity_rule"]
        for text in (
            "earliest checkpoint in the complete 25-checkpoint order",
            "transition_coverage.pointer_validity_checkpoints",
            "at or after activation",
            "a zero reference is skipped",
            "1 <= p < that checkpoint's page_count",
            "candidate_page_space",
            "byte zero",
            "0x05",
            "schedule order, then pointer offset, then slot number",
        ):
            self.assertIn(text, rule)

    def test_worker_and_workflow_rebinding_is_narrow_and_fail_closed(self) -> None:
        binding = self.plan["implementation_rebinding"]
        self.assertEqual(binding["required_experiment_id"], "DAO-A3-ALLOCATION-MAPS-001")
        self.assertIn("may change only", binding["source_rule"])
        self.assertIn("reject unless experiment_id is exactly", binding["worker_fail_closed_rule"])
        self.assertIn("must reject", binding["workflow_fail_closed_rule"])
        self.assertNotIn("windows-dao-a2-", binding["allowed_artifact_name_changes"])
        self.assertEqual(binding["plan_lane_implementation_status"], "not_implemented")

    def test_execution_gate_is_blocked_on_named_implementations_and_disclosure(self) -> None:
        gate = self.plan["execution_gate"]
        self.assertEqual(gate["status"], "BLOCKED")
        requirements = set(gate["blocking_requirements"])
        self.assertLessEqual(
            {
                "checked_a3_analyzer_and_synthetic_generator",
                "independent_recomputing_a3_validator",
                "a2_worker_and_workflow_rebound_to_a3_with_fail_closed_experiment_id",
                "passing_a3_dry_runs_disclosed_in_an_additive_provenance_entry",
            },
            requirements,
        )
        self.assertEqual(
            self.plan["analyzer_dry_run_contract"]["dry_run_result_disclosure"],
            "not_run_preregistration_only",
        )

    def test_independent_validator_is_preregistered_and_rejects_t1_to_t5(self) -> None:
        contract = self.plan["independent_validator_contract"]
        self.assertIn("must not read, import, execute", contract["implementation_independence"])
        self.assertIn("Parse the frozen candidate set", contract["required_recomputation"])
        self.assertEqual(
            [case["id"] for case in contract["tamper_cases"]],
            ["T1", "T2", "T3", "T4", "T5"],
        )
        self.assertIn("moves independent_validation_status", contract["acceptance_rule"])
        self.assertEqual(contract["plan_lane_implementation_status"], "not_implemented")

    def test_exp_0042_is_disclosed_as_prediction_input_not_a3_evidence(self) -> None:
        origin = self.plan["preregistration"]["origin_disclosure"]
        self.assertIn("prediction test", origin["prediction_not_rediscovery_disclosure"])
        self.assertIn("three new replicas", origin["prediction_not_rediscovery_disclosure"])
        self.assertFalse(self.plan["claims"]["exp_0042_bundle_is_a3_evidence"])
        replay = self.plan["analyzer_dry_run_contract"]["retained_exp_0042_input"]
        self.assertFalse(replay["scientific_evidence"])
        self.assertIn("replica 3", replay["holdout_access"])

    def test_plan_lane_contains_no_implementation_code(self) -> None:
        suffixes = {path.suffix for path in EXPERIMENT.rglob("*") if path.is_file()}
        self.assertLessEqual(suffixes, {".json", ".md"})

    def test_all_a3_json_documents_parse(self) -> None:
        documents = sorted(EXPERIMENT.glob("*.json"))
        self.assertEqual(len(documents), 15)
        for document in documents:
            with self.subTest(document=document.name):
                json.loads(document.read_bytes())

    def test_schemas_lint_and_plan_validates(self) -> None:
        schemas = sorted(EXPERIMENT.glob("*.schema.json"))
        self.assertEqual(len(schemas), 11)
        for path in schemas:
            with self.subTest(schema=path.name):
                lint_schema(json.loads(path.read_bytes()))
        schema = json.loads((EXPERIMENT / "plan.schema.json").read_bytes())
        validate_schema_value(self.plan, schema, schema, "$")


if __name__ == "__main__":
    unittest.main()
