"""Focused schema and contract tests for A4 derivation terminals."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_analysis import analyze  # noqa: E402
from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_generator import SyntheticParameters  # noqa: E402
from a4_layer_h1 import (  # noqa: E402
    H1Binding,
    H1ReplicaCandidate,
    LocatorTarget,
    agree_h1_replicas,
    derive_h1_replica,
)
from a4_layer_h2 import H2ReplicaCandidate  # noqa: E402
from a4_layer_h3 import H3Candidate  # noqa: E402
from a4_layer_h4 import H4Candidate, OPERATIONS  # noqa: E402
from a4_model import A4AnalysisError, WorkLedger  # noqa: E402
from a4_spec import PLAN  # noqa: E402
from a4_analysis_state import freeze_derivation  # noqa: E402
from a4_frozen_validation import _validate_groups  # noqa: E402
from a4_terminal import (  # noqa: E402
    DerivationTerminal,
    decisive_result,
    not_applicable_result,
    terminal_result,
)
from protocol_validation import validate_schema_value  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


SCHEMA = json.loads(
    (ROOT / "oracle/windows-dao/experiments/a4/derivation-candidates.schema.json").read_text()
)


class A4TerminalTests(unittest.TestCase):
    def _validate(self, definition: str, result: dict[str, object]) -> None:
        validate_schema_value(result, SCHEMA["$defs"][definition], SCHEMA, "$")

    def test_exact_h1_pair_multiple_retains_both_counted_pairs(self) -> None:
        bindings = tuple(
            H1Binding(
                1,
                role,
                instance,
                23 + index,
                (LocatorTarget(40 + index, 0), LocatorTarget(40 + index, 1)),
            )
            for index, (role, instance) in enumerate((
                ("T1", "T1-v1"),
                ("T2", "T2-v1"),
                ("T2", "T2-v2"),
                ("T3", "T3-v1"),
                ("T4", "T4-v1"),
            ))
        )
        candidates = tuple(
            H1ReplicaCandidate(
                1,
                "u8_row_then_u24le_page",
                "a4_pair_multiple_duplicate_locator_0_92",
                offsets,
                bindings,
            ).document()
            for offsets in ((35, 39), (35, 43))
        )
        result = terminal_result(
            A4AnalysisError("A4-H1-LOCATOR-PAIR-MULTIPLE", 2),
            WorkLedger(),
            candidates=candidates,
        )
        self._validate("h1Result", result)
        self.assertEqual(result["predicate_measured_survivor_count"], 2)
        self.assertEqual(result["terminal_candidate_stage"], "h1_locator_pair")
        self.assertEqual(len(result["candidates"]), 2)

    def test_exact_h1_pair_multiple_freezes_and_reports_without_holdout_refit(self) -> None:
        signature = PLAN["candidate_grammars"]["h1"][
            "pair_multiple_reachability_signature"
        ]
        parameters = SyntheticParameters(
            signature_id=signature["signature_id"],
            locator_offsets=tuple(
                interval[0] for interval in signature["locator_holes"]
            ),
        )
        analysis = analyze("a4-synthetic", _COMMIT, _inputs(parameters))
        layers = analysis.frozen.document["layers"]
        h1 = layers["h1_tdef_to_map_row"]
        self.assertEqual(h1["status"], "no_outcome")
        self.assertEqual(h1["terminal_predicate_id"], "A4-H1-LOCATOR-PAIR-MULTIPLE")
        self.assertEqual(h1["predicate_measured_survivor_count"], 2)
        self.assertEqual(layers["h2_row_identity_map_role"]["status"], "not_applicable")
        rows = {row["predicate_id"]: row for row in analysis.report["predicate_results"]}
        self.assertEqual(rows["A4-H1-LOCATOR-LAYOUT-NONE"]["predicate_measured_survivor_count"], 2)
        self.assertEqual(rows["A4-H1-LOCATOR-PAIR-MULTIPLE"]["status"], "fail")
        qualified = analysis.frozen.document["qualified_pages"]
        transcripts = analysis.frozen.document["transcripts"]
        first_binding = h1["candidates"][0]["instance_bindings"][0]
        first_checkpoint = first_binding["applicable_checkpoint_range"]["start"]
        self.assertIn(
            {
                "replica": 1,
                "checkpoint_id": first_checkpoint,
                "page_number": first_binding["tdef_page"],
            },
            qualified,
        )
        self.assertIn(
            {
                "replica": 1,
                "checkpoint_id": first_checkpoint,
                "page_number": first_binding["locator_targets"][0]["page"],
            },
            qualified,
        )
        self.assertTrue(transcripts["locators"])
        self.assertFalse(transcripts["row_directories"])
        self.assertEqual(analysis.report["qualified_pages"], qualified)
        self.assertEqual(analysis.report["transcripts"], transcripts)
        self.assertTrue(
            all(row["status"] == "not_applicable" for row in analysis.report["holdout_results"].values())
        )

    def test_h2_transition_retains_its_one_static_model(self) -> None:
        candidate = H2ReplicaCandidate(
            1, 0x1FFF, "set_bit_owned_in_use", 0, 1
        ).document()
        result = terminal_result(
            A4AnalysisError("A4-H2-TRANSITION-UNEXPLAINED", 1),
            WorkLedger(),
            candidates=(candidate,),
        )
        self._validate("h2Result", result)
        self.assertEqual(result["derivation_survivor_count"], 0)

    def test_replica_pair_candidates_are_size_bounded_and_charged(self) -> None:
        candidates = (
            H2ReplicaCandidate(1, 0x1FFF, "set_bit_owned_in_use", 0, 1),
            H2ReplicaCandidate(2, 0x0FFF, "set_bit_owned_in_use", 0, 1),
        )
        evidence = {
            "kind": "replica_pair",
            "entries": [
                {
                    "replica": candidate.replica,
                    "canonical_model_id": candidate.canonical_model_id,
                    "canonical_candidate_id": candidate.canonical_candidate_id,
                    "complete_candidate": candidate.document(),
                }
                for candidate in candidates
            ],
        }
        ledger = WorkLedger()
        result = terminal_result(
            A4AnalysisError("A4-H2-REPLICA-DISAGREEMENT", 2),
            ledger,
            terminal_evidence=evidence,
            per_replica_counts=(1, 1),
        )
        self._validate("h2Result", result)
        self.assertEqual(ledger.value("candidate_serializations"), 2)

    def test_h3_invalid_reference_retains_conversion_and_observation(self) -> None:
        conversion = H3Candidate(
            "h3_conversion",
            {"conversion": "structural_type_0_to_type_1_with_nonzero_u32_slots"},
        ).document()
        evidence = {
            "kind": "reference",
            "input_model_id": conversion["canonical_candidate_id"],
            "observation": {
                "replica": 1,
                "checkpoint_id": "T1_REL_0512",
                "page": 24,
                "slot_ordinal": 0,
                "referenced_page": 25,
                "observed_tag_byte": 1,
                "reason": "not_tag_05",
            },
        }
        result = terminal_result(
            A4AnalysisError("A4-H3-REFERENCE-INVALID", 1),
            WorkLedger(),
            candidates=(conversion,),
            terminal_evidence=evidence,
        )
        self._validate("h3Result", result)
        self.assertEqual(result["terminal_payload_kind"], "invalid_observation")

    def test_h4_record_none_uses_group_minimum_and_union(self) -> None:
        root_id = "a" * 64
        candidates = []
        groups = []
        for index, operation in enumerate(OPERATIONS):
            operation_candidates = [] if index == 0 else [
                H4Candidate(
                    "h4_operation_record",
                    {
                        "replica": 1,
                        "root_candidate_id": root_id,
                        "operation_id": operation,
                        "canonical_record_locator": {
                            "page": 30 + index,
                            "row": 0,
                            "row_start": 100,
                            "row_end": 120,
                        },
                    },
                ).document()
            ]
            candidates.extend(operation_candidates)
            groups.append({
                "operation_id": operation,
                "cardinality": len(operation_candidates),
                "candidate_ids": [
                    item["canonical_candidate_id"] for item in operation_candidates
                ],
            })
        result = terminal_result(
            A4AnalysisError("A4-H4-CATALOG-RECORD-NONE", 0),
            WorkLedger(),
            candidates=candidates,
            terminal_evidence={"kind": "operation_groups", "groups": groups},
        )
        self._validate("h4StructuralResult", result)
        self.assertEqual(result["predicate_measured_survivor_count"], 0)
        self.assertEqual(len(result["candidates"]), 6)

        tampered = json.loads(json.dumps(result))
        first = tampered["terminal_evidence"]["groups"][1]["candidate_ids"]
        second = tampered["terminal_evidence"]["groups"][2]["candidate_ids"]
        first[0], second[0] = second[0], first[0]
        with self.assertRaisesRegex(ValueError, "membership differs by operation"):
            _validate_groups(
                "A4-H4-CATALOG-RECORD-NONE",
                tampered,
                tampered["terminal_evidence"],
                None,
            )

    def test_campaign_resource_terminal_is_not_converted(self) -> None:
        error = A4AnalysisError("A4-RESOURCE-BOUND", 0)
        with self.assertRaises(A4AnalysisError) as raised:
            terminal_result(error, WorkLedger())
        self.assertIs(raised.exception, error)

    def test_freeze_preserves_upstream_model_and_gates_downstream_slots(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        ledger = WorkLedger()
        h1_by = {
            replica: derive_h1_replica(
                checked.views[replica],
                checked.qualified_tdef_pages[replica],
                ledger,
            )
            for replica in (1, 2)
        }
        h1 = agree_h1_replicas(h1_by[1], h1_by[2]).document()
        h2 = terminal_result(
            A4AnalysisError("A4-H2-ROLE-NONE", 0), ledger, candidates=()
        )
        layers = {
            "h1_tdef_to_map_row": decisive_result(h1, ledger),
            "h2_row_identity_map_role": h2,
            "h3_indirect_traversal": not_applicable_result(),
            "h4_catalog_bootstrap": {
                "root_result": not_applicable_result(),
                "structural_result": not_applicable_result(),
                "encoding_result": not_applicable_result(),
            },
        }
        frozen = freeze_derivation(
            checked,
            DerivationTerminal("A4-H2-ROLE-NONE", layers),
            ledger,
        )
        self.assertEqual(
            frozen.document["layers"]["h1_tdef_to_map_row"]["status"], "model"
        )
        self.assertEqual(
            frozen.document["layers"]["h3_indirect_traversal"]["status"],
            "not_applicable",
        )


if __name__ == "__main__":
    unittest.main()
