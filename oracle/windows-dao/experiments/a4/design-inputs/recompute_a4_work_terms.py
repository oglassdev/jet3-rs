#!/usr/bin/env python3
"""Recompute the preregistered A4 work table from the plan JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--assert-plan-total", action="store_true")
    parser.add_argument("--expect-ceiling", type=int)
    parser.add_argument("--reject-ceiling", type=int)
    return parser.parse_args()


def require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemExit(f"{label} must be a non-negative integer")
    return value


def require_int_map(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be an object")
    return {key: require_int(item, f"{label}.{key}") for key, item in value.items()}


def recompute_terms(plan: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    bounds = plan["bounds"]
    grammar = plan["candidate_grammars"]
    raw_inputs = plan["work_model"]["recomputation_inputs"]
    if not isinstance(raw_inputs, dict):
        raise SystemExit("work_model.recomputation_inputs must be an object")
    inputs = require_int_map(
        {key: value for key, value in raw_inputs.items() if key != "operation_name_bytes"},
        "work_model.recomputation_inputs",
    )
    name_bytes = require_int_map(
        raw_inputs["operation_name_bytes"],
        "work_model.recomputation_inputs.operation_name_bytes",
    )
    checkpoints = require_int(plan["checkpoint_design"]["count"], "checkpoint count")
    qualified_pages = require_int(
        bounds["max_qualified_pages_per_submodel"], "qualified pages"
    )
    derivation_replicas = len(plan["replicas"]["derivation"])
    operations = grammar["h4"]["required_operations"]
    if set(name_bytes) != set(operations):
        raise SystemExit("operation_name_bytes must cover exactly the required operations")

    page_size = require_int(bounds["page_size"], "page size")
    complete_row_bytes = (
        page_size
        - inputs["page_header_bytes"]
        - inputs["row_directory_entry_bytes"]
    )
    locator_starts = page_size - inputs["locator_bytes"] + 1
    raw_locator_windows_per_page = len(grammar["h1"]["locator_layouts"]) * locator_starts
    pair_span = locator_starts - inputs["locator_bytes"]
    locator_pairs_one_layout = pair_span * (pair_span + 1) // 2
    locator_pairs_per_page = len(grammar["h1"]["locator_layouts"]) * locator_pairs_one_layout
    valid_rows = (page_size - inputs["page_header_bytes"]) // (
        inputs["row_directory_entry_bytes"] + inputs["minimum_row_bytes"]
    )
    invalid_directory_entries = (
        page_size - inputs["page_header_bytes"]
    ) // inputs["row_directory_entry_bytes"]
    type_1_slots = (
        complete_row_bytes - inputs["type_1_header_bytes"]
    ) // inputs["integer_slot_bytes"]
    type_0_bits = (complete_row_bytes - inputs["type_0_header_bytes"]) * 8
    tag_05_bits = (page_size - inputs["tag_05_header_bytes"]) * 8
    occurrence_ceiling = sum(complete_row_bytes // name_bytes[op] for op in operations)
    h4_inner_grammar = (
        len(grammar["h4"]["kind_start_deltas"])
        * len(grammar["h4"]["kind_widths"])
        * len(grammar["h4"]["identifier_widths"])
        * len(grammar["h4"]["endianness"])
        * len(grammar["h4"]["name_length_start_deltas"])
        * len(grammar["h4"]["name_length_widths"])
        * inputs["kind_mapping_permutations"]
        * len(grammar["h4"]["identifier_lifecycle_relations"])
    )

    terms = {
        "tdef_lifecycle_signatures": qualified_pages
        * checkpoints
        * len(grammar["h1"]["tdef_lifecycle_signatures"]),
        "raw_locator_windows": qualified_pages * raw_locator_windows_per_page,
        "raw_locator_pairs": qualified_pages * locator_pairs_per_page,
        "h1_target_validity_checks": qualified_pages
        * len(grammar["h1"]["locator_layouts"])
        * len(grammar["h1"]["table_record_signature"]["locator_holes"])
        * checkpoints,
        "valid_path_row_directory_entries": qualified_pages * checkpoints * valid_rows,
        "type_1_slots": qualified_pages * checkpoints * 2 * type_1_slots,
        "type_0_and_tag_05_bitmap_bits": qualified_pages
        * checkpoints
        * (type_0_bits + tag_05_bits),
        "role_transition_evaluations": len(grammar["h2"]["row_masks"])
        * len(grammar["h2"]["type_0_polarities"])
        * len(grammar["h2"]["locator_role_assignments"])
        * inputs["transition_legs"]
        * len(plan["tables"]["logical_roles"])
        * checkpoints,
        "base_formula_evaluations": len(grammar["h3"]["base_formulas"])
        * qualified_pages
        * checkpoints,
        "catalog_root_signatures": qualified_pages
        * checkpoints
        * derivation_replicas
        * len(grammar["h4"]["catalog_root_selection_signatures"]),
        "catalog_raw_rows": len(operations) * qualified_pages * valid_rows,
        "encoding_union_anchor_bytes": derivation_replicas
        * inputs["distinct_name_pattern_scans"]
        * complete_row_bytes,
        "h4_name_length_structural_tuples": derivation_replicas
        * occurrence_ceiling
        * h4_inner_grammar,
        "encoding_length_equivalence_candidates": derivation_replicas
        * len(operations)
        * len(grammar["h4"]["name_length_equivalence_classes"]),
        "candidate_serializations": require_int(
            bounds["max_candidate_models"], "maximum candidate models"
        ),
    }
    alternatives = {
        "invalid_path_row_directory_entries": qualified_pages
        * checkpoints
        * invalid_directory_entries
    }

    expected_bounds = {
        "max_locator_pairs_per_tdef_page": locator_pairs_per_page,
        "max_locator_pairs": qualified_pages * locator_pairs_per_page,
        "max_h4_occurrence_identities": derivation_replicas * occurrence_ceiling,
        "max_h4_value_equivalent_tuples": h4_inner_grammar,
    }
    for name, expected in expected_bounds.items():
        actual = require_int(bounds[name], f"bounds.{name}")
        if actual != expected:
            raise SystemExit(f"bounds.{name} {actual} != recomputed {expected}")
    return terms, alternatives


def main() -> None:
    args = parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    registered_terms = plan["work_model"]["terms"]
    if not isinstance(registered_terms, dict) or not registered_terms:
        raise SystemExit("work_model.terms must be a non-empty object")
    recomputed_terms, recomputed_alternatives = recompute_terms(plan)
    if set(registered_terms) != set(recomputed_terms):
        raise SystemExit("registered and recomputed work-term names differ")

    recomputed_total = 0
    for name, term in registered_terms.items():
        if not isinstance(term, dict):
            raise SystemExit(f"work_model.terms.{name} must be an object")
        registered_units = require_int(
            term.get("units"), f"work_model.terms.{name}.units"
        )
        recomputed_units = recomputed_terms[name]
        if registered_units != recomputed_units:
            raise SystemExit(
                f"work_model.terms.{name}.units {registered_units} "
                f"!= recomputed {recomputed_units}"
            )
        recomputed_total += recomputed_units
        print(f"{name}={recomputed_units}")

    registered_alternatives = plan["work_model"]["terminal_path_maxima"][
        "alternative_terms"
    ]
    for name, recomputed_units in recomputed_alternatives.items():
        registered_units = require_int(
            registered_alternatives[name]["units"],
            f"work_model.terminal_path_maxima.alternative_terms.{name}.units",
        )
        if registered_units != recomputed_units:
            raise SystemExit(
                f"alternative term {name} {registered_units} "
                f"!= recomputed {recomputed_units}"
            )

    all_terms = {**recomputed_terms, **recomputed_alternatives}
    paths = plan["work_model"]["terminal_path_maxima"]
    recomputed_paths = {
        name: sum(all_terms[term] for term in term_names)
        for name, term_names in paths["term_table"].items()
    }
    registered_paths = paths["computed_units"]
    if recomputed_paths != registered_paths:
        raise SystemExit(
            f"registered terminal paths {registered_paths} "
            f"!= recomputed {recomputed_paths}"
        )

    plan_total = require_int(
        plan["work_model"]["terminal_path_maxima"]["computed_units"][
            "h4_latest_derivation_terminal"
        ],
        "work_model.terminal_path_maxima.computed_units.h4_latest_derivation_terminal",
    )
    ceiling = require_int(
        plan["bounds"]["max_analysis_work_units"],
        "bounds.max_analysis_work_units",
    )
    print(f"recomputed_total={recomputed_total}")
    print(f"plan_total={plan_total}")
    print(f"max_analysis_work_units={ceiling}")

    if args.assert_plan_total and recomputed_total != plan_total:
        raise SystemExit(
            f"recomputed total {recomputed_total} != registered plan total {plan_total}"
        )
    if recomputed_total > ceiling:
        raise SystemExit(f"recomputed total {recomputed_total} exceeds ceiling {ceiling}")
    if args.expect_ceiling is not None and args.expect_ceiling != ceiling:
        raise SystemExit(
            f"expected ceiling {args.expect_ceiling} != registered ceiling {ceiling}"
        )
    if args.reject_ceiling is not None:
        if args.reject_ceiling != ceiling + 1:
            raise SystemExit(
                f"reject ceiling {args.reject_ceiling} must equal registered ceiling + 1"
            )
        if args.reject_ceiling <= ceiling:
            raise SystemExit(f"one-over value {args.reject_ceiling} was not rejected")
        print(f"one_over_rejected={args.reject_ceiling}")


if __name__ == "__main__":
    main()
