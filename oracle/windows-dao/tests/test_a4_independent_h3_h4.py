"""Focused fake-page tests for fresh independent A4 H3/H4 recomputation."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import a4_independent_h3 as h3  # noqa: E402
import a4_independent_h4 as h4  # noqa: E402
import a4_independent_h4_contract as h4_contract  # noqa: E402
from a4_independent_bundle import BundleLoader  # noqa: E402
from a4_independent_h1 import apply_h1_holdout, recompute_h1  # noqa: E402
from a4_independent_h2 import recompute_h2  # noqa: E402
from a4_independent_validator import _snapshot_row_counts  # noqa: E402
from test_a4_independent_bundle import _build_bundle  # noqa: E402


H3_IDS = [
    "A4-H3-CONVERSION-NONE",
    "A4-H3-INACTIVE-SLOT-NONE",
    "A4-H3-REFERENCE-INVALID",
    "A4-H3-BASE-DISCRIMINATION",
    "A4-H3-BASE-NONE",
    "A4-H3-BASE-MULTIPLE",
    "A4-H3-REPLICA-DISAGREEMENT",
]


def _plan_h3() -> dict[str, object]:
    return {
        "bounds": {"max_candidate_models": 4096, "max_checkpoints_per_replica": 2},
        "checkpoint_design": {"checkpoint_ids": ["C0", "C1"], "idle_pairs": [],
                              "transition_coverage": {"t1_growth": ["C0", "C1"],
                              "t3_absolute": ["C0", "C1"], "t4_relative": ["C0", "C1"],
                              "t1_churn": ["X0", "X1", "X2", "X3"]}},
        "candidate_grammars": {"h3": {
            "conversion_candidates": ["structural_type_0_to_type_1_with_nonzero_u32_slots"],
            "base_formulas": [
                "slot_ordinal_times_16352_plus_bit_index",
                "referenced_page_times_16352_plus_bit_index",
                "slot_ordinal_times_16352_plus_bit_index_minus_one",
                "slot_ordinal_times_16352_plus_bit_index_plus_one",
            ],
        }},
        "predicate_registry": {"predicate_contracts": [
            {"predicate_id": identifier, "order": 20 + index, "scope": "h3_indirect_traversal",
             "reachability_fixture_id": f"A4-R{22 + index:02d}-TEST"}
            for index, identifier in enumerate(H3_IDS)
        ]},
    }


def _data_page(rows: list[bytes]) -> bytes:
    page = bytearray(2048)
    page[0] = 1
    page[8:10] = len(rows).to_bytes(2, "little")
    cursor = 2048
    starts = []
    for raw in rows:
        cursor -= len(raw)
        page[cursor : cursor + len(raw)] = raw
        starts.append(cursor)
    for index, start in enumerate(starts):
        page[10 + 2 * index : 12 + 2 * index] = start.to_bytes(2, "little")
    return bytes(page)


def _tag05(*bits: int) -> bytes:
    page = bytearray(2048)
    page[:4] = b"\x05\x01\x00\x00"
    for bit in bits:
        page[4 + bit // 8] |= 1 << (bit % 8)
    return bytes(page)


def _h3_fixture() -> tuple[dict[int, object], dict[int, object], dict[int, object]]:
    type0_owned = bytes([0]) + (0).to_bytes(4, "little") + bytes([1])
    type0_available = bytes([0]) + (0).to_bytes(4, "little")
    type1_owned = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in (3, 4, 0))
    type1_available = bytes([1]) + (0).to_bytes(4, "little")
    c0 = [bytes(2048), _data_page([type0_owned, type0_available]), bytes(2048),
          _tag05(0, 7, 16351), _tag05(0)]
    c1 = [bytes(2048), _data_page([type1_owned, type1_available]), bytes(2048),
          _tag05(0, 7, 16351), _tag05(0)]
    replicas = {number: {"checkpoint_ids": ["C0", "C1"], "checkpoints": {
        "C0": {"pages": c0}, "C1": {"pages": c1},
    }} for number in (1, 2)}
    h1_models = {number: {"model": {}, "instance_bindings": [{
        "replica": number, "logical_role": "T1", "lifecycle_instance": "T1-v1",
        "tdef_page": 2, "locator_targets": [{"page": 1, "row": 0}, {"page": 1, "row": 1}],
        "applicable_checkpoint_range": {"start": "C0", "end": "C1"},
    }]} for number in (1, 2)}
    h2_models = {number: {"model": {"row_mask": 8191, "polarity": "set_bit_owned_in_use",
        "owned_in_use_locator_ordinal": 0, "available_locator_ordinal": 1}}
        for number in (1, 2)}
    return replicas, h1_models, h2_models


class A4IndependentH3H4Tests(unittest.TestCase):
    def test_canonical_known_vector_has_sorted_keys_and_no_newline(self) -> None:
        value = {"z": 1, "a": [3, "é"]}
        expected = bytes.fromhex("7b2261223a5b332c22c3a9225d2c227a223a317d")
        self.assertEqual(h3._canonical(value), expected)
        self.assertEqual(h4._canonical(value), expected)
        self.assertEqual(h3._digest(value), "7f99f4654f8d09362fad7519a2670728e1e7e420cb8af180a0dca86fb1610822")

    def test_h3_terminal_candidate_hash_uses_canonical_id_order(self) -> None:
        candidates = [
            {"canonical_candidate_id": "b", "model": {"value": 2}},
            {"canonical_candidate_id": "a", "model": {"value": 1}},
        ]
        result = h3._make_result(
            candidates, "A4-H3-BASE-MULTIPLE", 2, "h3_final_base_formula"
        )
        expected = sorted(candidates, key=lambda row: row["canonical_candidate_id"])
        self.assertEqual(result["candidates"], expected)
        self.assertEqual(result["canonical_candidates_sha256"], h3._digest(expected))

    def test_h4_terminal_candidate_hash_uses_canonical_id_order(self) -> None:
        candidates = [
            {"canonical_candidate_id": "b", "model": {"value": 2}},
            {"canonical_candidate_id": "a", "model": {"value": 1}},
        ]
        result = h4._slot(
            candidates, "A4-H4-ENCODING-AMBIGUOUS", 2,
            "candidate_set", "h4_final_encoded_field",
        )
        expected = sorted(candidates, key=lambda row: row["canonical_candidate_id"])
        self.assertEqual(result["candidates"], expected)
        self.assertEqual(result["canonical_candidates_sha256"], h4._digest(expected))

    def test_h3_recomputes_unique_slot_formula_from_pages(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        result = h3.recompute_h3(replicas, h1_models, h2_models, _plan_h3())
        self.assertEqual(result["result"]["status"], "model")
        self.assertEqual(result["result"]["candidates"][0]["model"]["base_formula"],
                         "slot_ordinal_times_16352_plus_bit_index")
        self.assertIn(0, result["admitted_pages"][1]["C1"])
        self.assertEqual([row["predicate_id"] for row in result["predicates"]], H3_IDS)
        self.assertEqual([row["predicate_measured_survivor_count"]
                          for row in result["predicates"]], [1] * len(H3_IDS))

    def test_h3_reference_tag_tamper_is_the_registered_terminal(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        bad = bytearray(replicas[1]["checkpoints"]["C1"]["pages"][3])
        bad[0] = 1
        replicas[1]["checkpoints"]["C1"]["pages"][3] = bytes(bad)
        result = h3.recompute_h3(replicas, h1_models, h2_models, _plan_h3())
        self.assertEqual(result["result"]["terminal_predicate_id"], "A4-H3-REFERENCE-INVALID")
        self.assertEqual(result["result"]["terminal_evidence"]["observation"]["observed_tag_byte"], 1)
        self.assertEqual([row["predicate_measured_survivor_count"]
                          for row in result["predicates"]], [1, 1, 1, 0, 0, 0, 0])

    def test_h3_conflicting_replica_terminals_use_predicate_major_cutoff(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        invalid_reference = bytearray(replicas[1]["checkpoints"]["C1"]["pages"][3])
        invalid_reference[0] = 1
        replicas[1]["checkpoints"]["C1"]["pages"][3] = bytes(invalid_reference)
        replicas[2]["checkpoints"]["C1"]["pages"][1] = replicas[2]["checkpoints"]["C0"]["pages"][1]
        result = h3.recompute_h3(replicas, h1_models, h2_models, _plan_h3())
        self.assertEqual(result["result"]["terminal_predicate_id"], "A4-H3-CONVERSION-NONE")
        self.assertNotIn(
            {"replica": 1, "checkpoint_id": "C1", "page_number": 3},
            result["qualified_pages"],
        )
        self.assertEqual(result["work_charges"], {
            "type_0_and_tag_05_bitmap_bits": 0, "base_formula_evaluations": 0,
        })

    def test_h3_reference_bound_deduplicates_and_spans_both_map_rows(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        duplicate = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in ([3] * 17 + [0]))
        replicas[1]["checkpoints"]["C1"]["pages"][1] = _data_page([duplicate, duplicate])
        replicas[2]["checkpoints"]["C1"]["pages"][1] = _data_page([duplicate, duplicate])
        h3.recompute_h3(replicas, h1_models, h2_models, _plan_h3())

        distinct = list(range(3, 20))
        owned = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in (distinct[:9] + [0]))
        available = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in (distinct[9:] + [0]))
        for number in (1, 2):
            pages = replicas[number]["checkpoints"]["C1"]["pages"]
            pages[1] = _data_page([owned, available])
            pages.extend(_tag05() for _ in range(20 - len(pages)))
        with self.assertRaises(h3.H3ValidationError) as raised:
            h3.recompute_h3(replicas, h1_models, h2_models, _plan_h3())
        self.assertEqual(raised.exception.code, "resource_bound_breach")

    def test_h3_holdout_propagates_resource_terminal(self) -> None:
        with patch.object(h3, "_checkpoints", side_effect=h3.H3ValidationError(
                "resource_bound_breach")):
            with self.assertRaises(h3.H3ValidationError):
                h3.predict_h3_holdout({}, {}, {}, {}, _plan_h3())

    def test_h3_holdout_applies_frozen_formula_without_refit(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        binding = dict(h1_models[1]["instance_bindings"][0])
        binding["replica"] = 3
        h1_candidate = {"model": {}, "instance_bindings": [binding]}
        h3_candidate = {"model": {"base_formula":
            "slot_ordinal_times_16352_plus_bit_index"}}
        self.assertTrue(h3.predict_h3_holdout(
            replicas[1], h1_candidate, h2_models[1], h3_candidate, _plan_h3()
        ))
        page = bytearray(replicas[1]["checkpoints"]["C1"]["pages"][3])
        page[4 + 16351 // 8] &= ~(1 << (16351 % 8))
        replicas[1]["checkpoints"]["C1"]["pages"][3] = bytes(page)
        self.assertFalse(h3.predict_h3_holdout(
            replicas[1], h1_candidate, h2_models[1], h3_candidate, _plan_h3()
        ))

    def test_h4_recomputes_root_none_from_empty_pages(self) -> None:
        plan = json.loads(
            (ROOT / "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json")
            .read_text(encoding="utf-8")
        )
        checkpoints = plan["checkpoint_design"]["checkpoint_ids"]
        replicas = {number: {"checkpoints": {
            checkpoint: {"pages": [bytes(2048)]} for checkpoint in checkpoints
        }} for number in (1, 2)}
        h1_models = {number: {"model": {"layout": "u8_row_then_u24le_page",
            "locator_offsets": [35, 39]}, "instance_bindings": [{"replica": number}]}
            for number in (1, 2)}
        h2_models = {number: {"model": {"row_mask": 8191, "polarity": "set_bit_owned_in_use",
            "owned_in_use_locator_ordinal": 0, "available_locator_ordinal": 1}}
            for number in (1, 2)}
        h3_models = {number: {"model": {"base_formula":
            "slot_ordinal_times_16352_plus_bit_index"}} for number in (1, 2)}
        result = h4.recompute_h4({}, replicas, h1_models, h2_models, h3_models, plan)
        self.assertEqual(result["result"]["root_result"]["terminal_predicate_id"],
                         "A4-H4-CATALOG-ROOT-NONE")
        self.assertEqual(result["predicates"][0]["status"], "fail")
        self.assertEqual([row["predicate_measured_survivor_count"]
                          for row in result["predicates"]], [0] * 9)
        frozen = {"root_result": result["result"]["root_result"],
                  "structural_result": result["result"]["structural_result"],
                  "encoding_result": result["result"]["encoding_result"]}
        self.assertFalse(h4.predict_h4_root_holdout(
            replicas[1], h1_models[1], h2_models[1], h3_models[1], frozen, plan
        ))
        self.assertFalse(h4.predict_h4_fields_holdout(
            replicas[1], h1_models[1], h2_models[1], h3_models[1], frozen, plan
        ))

    def test_h4_conflicting_root_counts_choose_none_before_multiple(self) -> None:
        plan = json.loads(
            (ROOT / "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json")
            .read_text(encoding="utf-8")
        )
        checkpoints = plan["checkpoint_design"]["checkpoint_ids"]
        replicas = {number: {"number": number} for number in (1, 2)}
        h1_models = {number: {"model": {"locator_offsets": [35, 39]},
            "instance_bindings": [{"replica": number}]} for number in (1, 2)}
        h2_models = {number: {"model": {"row_mask": 8191}} for number in (1, 2)}
        h3_models = {number: {"model": {"base_formula":
            "slot_ordinal_times_16352_plus_bit_index"}} for number in (1, 2)}

        def page_count(replica: dict[str, int], checkpoint: str) -> int:
            return 2 if replica["number"] == 1 and checkpoint == "EMPTY" else 0

        def page(_replica: object, checkpoint: str, _number: int) -> bytes:
            return bytes([2 if checkpoint == "EMPTY" else 1]) + bytes(2047)

        def traverse(_replica: object, _number: int, checkpoint: str, *_args: object):
            admitted = {10 + checkpoints.index(checkpoint)}
            return ((1, 0), (1, 1)), admitted

        with patch.object(h4, "_page_count", side_effect=page_count), \
             patch.object(h4, "_page", side_effect=page), \
             patch.object(h4, "_traverse", side_effect=traverse), \
             patch.object(h4, "_state_digest", side_effect=lambda _r, c, p: f"{c}:{p}"):
            result = h4.recompute_h4({}, replicas, h1_models, h2_models, h3_models, plan)
        root = result["result"]["root_result"]
        self.assertEqual(root["terminal_predicate_id"], "A4-H4-CATALOG-ROOT-NONE")
        self.assertEqual(root["predicate_measured_survivor_count"], 0)

    def test_h4_root_attempt_bound_precedes_seventeenth_traversal(self) -> None:
        plan = json.loads(
            (ROOT / "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json")
            .read_text(encoding="utf-8")
        )
        replicas = {number: {"number": number} for number in (1, 2)}
        h1_models = {number: {"model": {"locator_offsets": [35, 39]},
            "instance_bindings": [{"replica": number}]} for number in (1, 2)}
        h2_models = {number: {"model": {"row_mask": 8191}} for number in (1, 2)}
        h3_models = {number: {"model": {"base_formula":
            "slot_ordinal_times_16352_plus_bit_index"}} for number in (1, 2)}
        page_count = lambda replica, checkpoint: 17 if replica["number"] == 1 and checkpoint == "EMPTY" else 0
        page = bytes([2]) + bytes(2047)
        with patch.object(h4, "_page_count", side_effect=page_count), \
             patch.object(h4, "_page", return_value=page), \
             patch.object(h4, "_traverse", side_effect=h4.H4ValidationError("system_tdef_invalid")) as traverse:
            with self.assertRaises(h4.H4ValidationError) as raised:
                h4.recompute_h4({}, replicas, h1_models, h2_models, h3_models, plan)
        self.assertEqual(raised.exception.code, "resource_bound_breach")
        self.assertEqual(traverse.call_count, 16)
        one_root = lambda replica, checkpoint: 1 if replica["number"] == 1 and checkpoint == "EMPTY" else 0
        with patch.object(h4, "_page_count", side_effect=one_root), \
             patch.object(h4, "_page", return_value=page), \
             patch.object(h4, "_traverse", side_effect=h4.H4ValidationError("resource_bound_breach")):
            with self.assertRaises(h4.H4ValidationError) as propagated:
                h4.recompute_h4({}, replicas, h1_models, h2_models, h3_models, plan)
        self.assertEqual(propagated.exception.code, "resource_bound_breach")

    def test_h4_system_reference_bound_is_distinct_across_both_rows(self) -> None:
        tdef = bytearray(2048)
        tdef[0] = 2
        tdef[35:39] = bytes([0, 1, 0, 0])
        tdef[39:43] = bytes([1, 1, 0, 0])
        duplicate = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in ([2] * 17 + [0]))
        replica = {"checkpoints": {"C": {"pages": [bytes(tdef),
            _data_page([duplicate, duplicate]), _tag05()]}}}
        h1_model = {"locator_offsets": [35, 39], "layout": "u8_row_then_u24le_page"}
        h2_model = {"row_mask": 8191, "polarity": "set_bit_owned_in_use",
                    "owned_in_use_locator_ordinal": 0, "available_locator_ordinal": 1}
        h3_model = {"base_formula": "slot_ordinal_times_16352_plus_bit_index"}
        h4._traverse(replica, 1, "C", 0, h1_model, h2_model, h3_model, set(), {})

        references = list(range(2, 19))
        owned = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in references[:9])
        available = bytes([1]) + b"".join(value.to_bytes(4, "little") for value in references[9:])
        replica["checkpoints"]["C"]["pages"] = [bytes(tdef), _data_page([owned, available])]
        replica["checkpoints"]["C"]["pages"].extend(_tag05() for _ in references)
        with self.assertRaises(h4.H4ValidationError) as raised:
            h4._traverse(replica, 1, "C", 0, h1_model, h2_model, h3_model, set(), {})
        self.assertEqual(raised.exception.code, "resource_bound_breach")

    def test_h4_resource_errors_propagate_and_catalog_rows_charge_once(self) -> None:
        resource = h4.H4ValidationError("resource_bound_breach")
        with patch.object(h4, "_holdout_root_context", side_effect=resource):
            with self.assertRaises(h4.H4ValidationError):
                h4.predict_h4_root_holdout({}, {}, {}, {}, {}, {})
            with self.assertRaises(h4.H4ValidationError):
                h4.predict_h4_fields_holdout({}, {}, {}, {}, {}, {})
        page = _data_page([bytes([1]), bytes([1])])
        work = {"catalog_raw_rows": 0}
        charged: set[tuple[int, str, int, int]] = set()
        h4_contract.catalog_rows(page, 8191, (1, "C1", 4), work, charged)
        h4_contract.catalog_rows(page, 8191, (1, "C1", 4), work, charged)
        self.assertEqual(work["catalog_raw_rows"], 2)

    def test_plan_predicate_reordering_fails_closed(self) -> None:
        replicas, h1_models, h2_models = _h3_fixture()
        plan = _plan_h3()
        plan["predicate_registry"]["predicate_contracts"][0]["order"] = 99
        with self.assertRaisesRegex(h3.H3ValidationError, "predicate_registry_invalid"):
            h3.recompute_h3(replicas, h1_models, h2_models, plan)

    def test_structural_scan_uses_registered_occurrence_rows(self) -> None:
        plan = json.loads(
            (ROOT / "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json")
            .read_text(encoding="utf-8")
        )
        grammar = plan["candidate_grammars"]["h4"]
        operations = grammar["operation_binding_order"]
        rows = {}
        identifiers = [10, 11, 12, 13, 14, 15, 16]
        for operation, identifier in zip(operations, identifiers):
            patterns = h4._operation_patterns(plan, 1, operation)
            pattern_id, pattern = patterns[0]
            raw = bytearray(40)
            raw[16], raw[17] = max(len(value) for _, value in patterns), identifier
            raw[18] = 2 if operation == "T1_ADD_TEXT" else 3 if operation == "T1_ADD_INDEX" else 1
            raw[20 : 20 + len(pattern)] = pattern
            occurrence = {"occurrence_index": 0, "name_start": 20,
                          "matched_registered_pattern_id": pattern_id,
                          "matched_bytes_hex": pattern.hex()}
            rows[operation] = ({"page": 5, "row": 0, "row_start": 2000, "row_end": 2040},
                               bytes(raw), [occurrence])
        work = {"h4_name_length_structural_tuples": 0}
        candidates = h4._structural_candidates(
            1, rows, "0" * 64, grammar, plan, work
        )
        self.assertTrue(any(candidate[0]["model"]["kind_start_delta"] == 2 for candidate in candidates))
        self.assertEqual(work["h4_name_length_structural_tuples"], 7 * 165888)

    def test_independent_modules_do_not_import_producer_symbols(self) -> None:
        forbidden = ("a4_analysis", "a4_model", "a4_layer", "a4_generator", "a4_spec")
        for path in (SCRIPTS / "a4_independent_h3.py", SCRIPTS / "a4_independent_h4.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
            self.assertFalse([name for name in names if name.startswith(forbidden)])

    def test_synthetic_bundle_recomputes_exact_frozen_h3_h4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            _build_bundle(root)
            bundle = BundleLoader(root).load()
            plan = bundle.plan
            contracts = plan["predicate_registry"]["predicate_contracts"]
            first = recompute_h1(bundle.replicas, plan=plan, predicate_contracts=contracts)
            second = recompute_h2(
                bundle.replicas, first, plan=plan, predicate_contracts=contracts,
                snapshot_row_counts=_snapshot_row_counts(bundle, (1, 2)),
            )
            third = h3.recompute_h3(bundle.replicas, first, second, plan)
            metadata = {
                "protocol_version": bundle.manifest["protocol_version"],
                "plan_sha256": bundle.plan_sha256,
                "revision_plan_sha256": bundle.manifest["revision_plan_sha256"],
                "campaign_id": bundle.manifest["campaign_id"],
            }
            fourth = h4.recompute_h4(metadata, bundle.replicas, first, second, third, plan)
            frozen = json.loads(
                (root / "analysis/derivation-candidates.json").read_text(
                    encoding="utf-8"
                )
            )
            occurrence = json.loads(
                (root / "analysis/h4-occurrence-evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(third["result"], frozen["layers"]["h3_indirect_traversal"])
            self.assertEqual(fourth["result"], frozen["layers"]["h4_catalog_bootstrap"])
            self.assertEqual(fourth["occurrence_evidence"], occurrence)
            self.assertEqual(third["work_charges"]["base_formula_evaluations"], 88)
            self.assertEqual(fourth["work_charges"], {
                "catalog_root_signatures": 100, "catalog_raw_rows": 236,
                "encoding_union_anchor_bytes": 172,
                "h4_name_length_structural_tuples": 2322432,
                "encoding_length_equivalence_candidates": 40,
            })
            qualified = {(row["replica"], row["checkpoint_id"], row["page_number"])
                         for row in fourth["qualified_pages"]}
            self.assertIn((1, "EMPTY_R", 4), qualified)
            self.assertIn((2, "EMPTY_R", 4), qualified)
            self.assertNotIn((1, "EMPTY", 4), qualified)
            self.assertNotIn((2, "EMPTY", 4), qualified)
            all_qualified = {
                (row["replica"], row["checkpoint_id"], row["page_number"])
                for rows in (first.qualified_pages, second.qualified_pages,
                             third["qualified_pages"], fourth["qualified_pages"])
                for row in rows
            }
            frozen_qualified = {(row["replica"], row["checkpoint_id"], row["page_number"])
                                for row in frozen["qualified_pages"]}
            self.assertEqual(all_qualified, frozen_qualified)
            holdout_h1 = apply_h1_holdout(bundle.replicas[3], first.layer, plan=plan)
            self.assertIsNotNone(holdout_h1)
            args = (bundle.replicas[3], holdout_h1, second.layer, third, fourth["result"], plan)
            self.assertTrue(h4.predict_h4_root_holdout(*args))
            self.assertTrue(h4.predict_h4_fields_holdout(*args))


if __name__ == "__main__":
    unittest.main()
