"""Focused hash and structural contracts for the frozen DAO A4 base plan."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "oracle" / "windows-dao" / "experiments" / "a4"
PLAN = EXPERIMENT / "a4-row-anchored-maps.plan.json"
PLAN_SCHEMA = EXPERIMENT / "plan.schema.json"
SCHEMA_SNAPSHOT = EXPERIMENT / "dao-schema-snapshot.schema.json"
BRIEF = EXPERIMENT / "design-inputs" / "a4-scope-approved.md"
README = EXPERIMENT / "README.md"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"

PLAN_SHA256 = "6604b4866b26e3077f351909f7cf85839da7ff75a11600320b21d67d2e98c21c"
BRIEF_SHA256 = "ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7"
CHECKPOINTS = [
    "EMPTY",
    "EMPTY_R",
    "T1_CREATE_ID",
    "T1_ADD_TEXT",
    "T1_ADD_INDEX",
    "T2_CREATE",
    "T2_DROP",
    "T2_RECREATE",
    "T3_CREATE",
    "T4_CREATE",
    "T1_REL_0064",
    "T1_REL_0512",
    "T1_REL_0768",
    "T1_REL_1280",
    "T1_DELETE_ALL",
    "T1_REINSERT_SAME",
    "T1_IDLE_R",
    "T3_ABS_04096",
    "T3_ABS_08192",
    "T3_ABS_12288",
    "T3_ABS_16480",
    "T4_REL_0064",
    "T4_REL_0896",
    "T4_REL_0904",
    "T4_IDLE_R",
]

SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protocol_validation import lint_schema, validate_schema_value  # noqa: E402


class A4PlanContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_bytes = PLAN.read_bytes()
        cls.plan = json.loads(cls.plan_bytes)
        cls.schema = json.loads(PLAN_SCHEMA.read_bytes())

    def test_plan_hash_is_frozen_in_readme_and_exp_0052(self) -> None:
        self.assertEqual(hashlib.sha256(self.plan_bytes).hexdigest(), PLAN_SHA256)
        readme = README.read_text(encoding="utf-8")
        provenance = PROVENANCE.read_text(encoding="utf-8")
        self.assertIn(PLAN_SHA256, readme)
        self.assertIn("### EXP-0052", provenance)
        self.assertIn(PLAN_SHA256, provenance)
        ids = [int(value) for value in re.findall(r"^### EXP-(\d{4})", provenance, re.M)]
        self.assertEqual(max(ids), 52)
        self.assertEqual(len(ids), len(set(ids)))

    def test_approved_brief_is_byte_exact_and_hash_bound(self) -> None:
        self.assertEqual(hashlib.sha256(BRIEF.read_bytes()).hexdigest(), BRIEF_SHA256)
        design_input = self.plan["preregistration"]["origin_disclosure"][
            "design_inputs"
        ][0]
        self.assertEqual(design_input["sha256"], BRIEF_SHA256)
        self.assertEqual(
            design_input["path"],
            "oracle/windows-dao/experiments/a4/design-inputs/a4-scope-approved.md",
        )

    def test_plan_and_all_document_schemas_lint(self) -> None:
        for path in sorted(EXPERIMENT.glob("*.schema.json")):
            with self.subTest(schema=path.name):
                lint_schema(json.loads(path.read_bytes()))
        validate_schema_value(self.plan, self.schema, self.schema, "$")

    def test_experiment_identity_and_acquisition_gate_are_fail_closed(self) -> None:
        self.assertEqual(
            self.plan["experiment_id"], "DAO-A4-ROW-ANCHORED-MAPS-001"
        )
        self.assertEqual(self.plan["preregistration"]["provenance_entry"], "EXP-0052")
        self.assertFalse(self.plan["preregistration"]["acquisition_started"])
        self.assertEqual(self.plan["execution_gate"]["status"], "BLOCKED")
        self.assertEqual(
            self.plan["implementation_rebinding"]["plan_lane_implementation_status"],
            "not_implemented",
        )

    def test_exact_schedule_and_one_at_a_time_schema_operations_are_frozen(self) -> None:
        design = self.plan["checkpoint_design"]
        self.assertEqual(design["count"], 25)
        self.assertEqual(design["checkpoint_ids"], CHECKPOINTS)
        self.assertFalse(design["adaptive_checkpoints_allowed"])
        self.assertTrue(design["all_checkpoints_closed_and_quiescent"])
        operations = self.plan["tables"]["checkpoint_operations"]
        self.assertEqual(list(operations), CHECKPOINTS)
        for checkpoint in (
            "T1_CREATE_ID",
            "T1_ADD_TEXT",
            "T1_ADD_INDEX",
            "T2_CREATE",
            "T2_DROP",
            "T2_RECREATE",
            "T3_CREATE",
            "T4_CREATE",
        ):
            self.assertIn("One ", operations[checkpoint])

    def test_role_rotation_code_page_and_index_perturbation_are_exact(self) -> None:
        tables = self.plan["tables"]
        names = tables["physical_names"]
        self.assertEqual(names, ["A4TAB_A1", "A4TAB_B2", "A4TAB_C3", "A4TAB_É4"])
        self.assertEqual({len(name) for name in names}, {8})
        self.assertEqual("A4TAB_É4".encode("cp1252").hex(), "41345441425fc934")
        self.assertEqual(
            self.plan["environment_binding"]["windows_ansi_code_page"], 1252
        )
        self.assertEqual(
            [binding["replica"] for binding in tables["role_bindings"]], [1, 2, 3]
        )
        self.assertEqual(tables["definition"]["index"]["name"], "A4IX_ID")
        self.assertFalse(tables["definition"]["index"]["unique"])
        self.assertEqual(
            tables["definition"]["index"]["role"],
            "catalog_object_kind_perturbation_only",
        )

    def test_four_layer_sequences_terminals_and_reachability_reconcile(self) -> None:
        registry = self.plan["predicate_registry"]
        sequences = registry["per_layer_ordered_predicates"]
        self.assertEqual(
            list(sequences),
            [
                "h1_tdef_to_map_row",
                "h2_row_identity_map_role",
                "h3_indirect_traversal",
                "h4_catalog_bootstrap",
            ],
        )
        flattened = registry["campaign_evaluated_before_any_layer"] + [
            predicate for sequence in sequences.values() for predicate in sequence
        ]
        self.assertEqual(registry["ids"], flattened)
        self.assertEqual(len(flattened), 40)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(registry["no_outcome_terminals"], sequences)
        reachability = registry["predicate_reachability_reconciliation"]["rows"]
        self.assertEqual([row["predicate_id"] for row in reachability], flattened)
        self.assertEqual(len({row["predicate_id"] for row in reachability}), 40)

    def test_survivor_count_table_covers_every_layer_predicate(self) -> None:
        sequences = self.plan["predicate_registry"]["per_layer_ordered_predicates"]
        survivor = self.plan["decision_rules"]["survivor_count_reconciliation"]
        self.assertEqual(survivor["style"], "R4-S01")
        self.assertEqual(set(survivor["per_layer_counts"]), set(sequences))
        for layer, sequence in sequences.items():
            rows = survivor["per_layer_counts"][layer]
            self.assertEqual([row["predicate_id"] for row in rows], sequence)
            for row in rows:
                if row["predicate_id"].endswith("-MULTIPLE"):
                    self.assertIn("at least 2", row["count"])
                if row["predicate_id"].endswith("-NONE"):
                    self.assertTrue(row["count"].startswith("0"))

    def test_row_anchoring_and_real_a3_calibration_examples_are_explicit(self) -> None:
        procedure = self.plan["record_candidate_procedure"]
        self.assertIn("SRC-0020", procedure["row_directory_source"])
        self.assertIn("0x1fff", procedure["row_directory_source"])
        self.assertIn("0x0fff", procedure["row_directory_source"])
        self.assertIn("Moving starts", procedure["row_boundary_rule"])
        examples = {row["id"]: row["observation"] for row in procedure["a3_calibration_worked_examples"]}
        moving = examples["A4-W01-MOVING-ROW"]
        for value in ("1915", "1911", "1895", "1847", "1843", "1021-1023"):
            self.assertIn(value, moving)
        type1 = examples["A4-W02-TYPE1"]
        for value in ("14848", "16352", "01 26 06 00 00 e1 3f 00 00"):
            self.assertIn(value, type1)
        self.assertIn(
            "slot_ordinal_times_16352_plus_bit_index",
            self.plan["hypotheses"]["A4-H3"]["base_candidates"],
        )

    def test_freeze_holdout_binding_charging_and_hard_timeout_are_exact(self) -> None:
        self.assertIn("before replica 3", self.plan["decision_rules"]["freeze_rule"])
        self.assertIn("without refit", self.plan["decision_rules"]["holdout_rule"])
        binding = self.plan["implementation_rebinding"]["revision_binding_rule"]
        self.assertEqual(binding["style"], "R5-V01")
        charging = self.plan["record_candidate_procedure"]["union_once_charging"]
        self.assertEqual(charging["style"], "R4-C01")
        self.assertIn("once", charging["rule"])
        runtime = self.plan["runtime_design"]
        self.assertEqual(self.plan["bounds"]["campaign_timeout_seconds"], 2700)
        timeout_contract = (
            runtime["campaign_headroom"] + " " + runtime["bounds_sanity_basis"]
        )
        for value in ("2700", "2701", "before manifest creation"):
            self.assertIn(value, timeout_contract)

    def test_schema_snapshot_is_required_at_all_75_checkpoints(self) -> None:
        schema = json.loads(SCHEMA_SNAPSHOT.read_bytes())
        self.assertEqual(
            schema["properties"]["document_type"]["const"],
            "dao_a4_schema_snapshot",
        )
        self.assertEqual(schema["properties"]["windows_ansi_code_page"]["const"], 1252)
        self.assertEqual(schema["$defs"]["table"]["properties"]["row_count"]["maximum"], 200000)
        observation = json.loads((EXPERIMENT / "replica-observation.schema.json").read_bytes())
        self.assertIn(
            "dao_schema_snapshot", observation["$defs"]["checkpoint"]["required"]
        )
        manifest = json.loads((EXPERIMENT / "bundle-manifest.schema.json").read_bytes())
        self.assertIn("dao_schema_snapshot", manifest["$defs"]["file"]["properties"]["role"]["enum"])
        self.assertEqual(
            self.plan["checkpoint_design"]["transition_coverage"][
                "schema_snapshot_every_checkpoint"
            ],
            CHECKPOINTS,
        )

    def test_dry_run_honesty_and_claims_are_fail_closed(self) -> None:
        honesty = self.plan["analyzer_dry_run_contract"]["dry_run_honesty_clause"]
        for case in (
            "moving_row",
            "deleted_row",
            "wrong_locator_target",
            "zero_slot",
            "nonzero_slot",
            "base_ambiguity",
            "catalog_multiplicity",
            "encoding_ambiguity",
            "replica_disagreement",
            "holdout_failure",
            "resource_one_over",
            "campaign_2701_seconds",
        ):
            self.assertIn(case, honesty["required_cases"])
        claims = self.plan["claims"]
        self.assertTrue(claims["descriptive_provider_observation_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in claims.items()
                if key != "descriptive_provider_observation_only"
            )
        )


if __name__ == "__main__":
    unittest.main()
