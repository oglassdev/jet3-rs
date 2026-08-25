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


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be an object")
    return value


def require_interval(
    value: Any, label: str, *, record_start: int, record_end: int, page_size: int
) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise SystemExit(f"{label} must be one [start,end) interval")
    start = require_int(value[0], f"{label}[0]")
    end = require_int(value[1], f"{label}[1]")
    if end - start != 4:
        raise SystemExit(f"{label} must be exactly four bytes")
    if start < record_start or end > record_end or end > page_size:
        raise SystemExit(f"{label} is outside its record/page domain")
    return start, end


def require_hex(value: Any, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise SystemExit(f"{label} must be lowercase hexadecimal")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as error:
        raise SystemExit(f"{label} must be lowercase hexadecimal") from error
    if value != decoded.hex() or len(decoded) != expected_bytes:
        raise SystemExit(f"{label} must encode exactly {expected_bytes} bytes")
    return decoded


def validate_holes(
    signature: dict[str, Any], label: str, page_size: int
) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    bounds = signature.get("record_bounds")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise SystemExit(f"{label}.record_bounds must be [start,end)")
    record_start = require_int(bounds[0], f"{label}.record_bounds[0]")
    record_end = require_int(bounds[1], f"{label}.record_bounds[1]")
    if record_start >= record_end or record_end > page_size:
        raise SystemExit(f"{label}.record_bounds is invalid")
    raw_holes = signature.get("locator_holes")
    if not isinstance(raw_holes, list) or not raw_holes:
        raise SystemExit(f"{label}.locator_holes must be a non-empty array")
    holes = [
        require_interval(
            hole,
            f"{label}.locator_holes[{index}]",
            record_start=record_start,
            record_end=record_end,
            page_size=page_size,
        )
        for index, hole in enumerate(raw_holes)
    ]
    if holes != sorted(holes) or len(set(holes)) != len(holes):
        raise SystemExit(f"{label}.locator_holes must be distinct and ascending")
    if any(left[1] > right[0] for left, right in zip(holes, holes[1:])):
        raise SystemExit(f"{label}.locator_holes must not overlap")
    return holes, (record_start, record_end)


def derive_pair_multiple_identity_classes(
    standard: dict[str, Any], multiple: dict[str, Any], page_size: int
) -> list[list[int]]:
    standard_holes, standard_bounds = validate_holes(
        standard, "candidate_grammars.h1.table_record_signature", page_size
    )
    multiple_holes, multiple_bounds = validate_holes(
        multiple,
        "candidate_grammars.h1.pair_multiple_reachability_signature",
        page_size,
    )
    if standard_bounds != multiple_bounds:
        raise SystemExit("pair-multiple record bounds differ from the base signature")
    additional = require_interval(
        multiple.get("additional_locator_hole"),
        "pair_multiple_reachability_signature.additional_locator_hole",
        record_start=multiple_bounds[0],
        record_end=multiple_bounds[1],
        page_size=page_size,
    )
    if multiple_holes != [*standard_holes, additional]:
        raise SystemExit(
            "pair-multiple locator holes must be exactly the base holes plus one additional hole"
        )

    base_id = standard.get("signature_id")
    if not isinstance(base_id, str) or multiple.get("base_signature_id") != base_id:
        raise SystemExit("pair-multiple base signature id mismatch")

    relationships = multiple.get("equal_byte_intervals")
    if not isinstance(relationships, list) or len(relationships) != 1:
        raise SystemExit("pair-multiple requires exactly one registered equality")
    equality = require_object(relationships[0], "equal_byte_intervals[0]")
    if set(equality) != {"left", "right", "relation"} or equality["relation"] != "equal":
        raise SystemExit("pair-multiple equality is removed, altered, or unregistered")
    left = require_interval(
        equality["left"],
        "equal_byte_intervals[0].left",
        record_start=multiple_bounds[0],
        record_end=multiple_bounds[1],
        page_size=page_size,
    )
    right = require_interval(
        equality["right"],
        "equal_byte_intervals[0].right",
        record_start=multiple_bounds[0],
        record_end=multiple_bounds[1],
        page_size=page_size,
    )
    if left != standard_holes[-1] or right != additional:
        raise SystemExit(
            "registered equality must join the immediately preceding base hole to the additional hole"
        )

    starts = [hole[0] for hole in multiple_holes]
    parents = {start: start for start in starts}

    def find(start: int) -> int:
        while parents[start] != start:
            parents[start] = parents[parents[start]]
            start = parents[start]
        return start

    left_root, right_root = find(left[0]), find(right[0])
    parents[right_root] = left_root
    groups: dict[int, list[int]] = {}
    for start in starts:
        groups.setdefault(find(start), []).append(start)
    derived_classes = sorted((sorted(group) for group in groups.values()), key=lambda group: group[0])
    if multiple.get("derived_locator_identity_classes") != derived_classes:
        raise SystemExit(
            "pair-multiple derived_locator_identity_classes conflicts with structured equality"
        )

    record_bytes = multiple_bounds[1] - multiple_bounds[0]
    base_value = require_hex(standard.get("value_hex"), "base signature value_hex", record_bytes)
    base_mask = require_hex(standard.get("mask_hex"), "base signature mask_hex", record_bytes)
    derivation = require_object(multiple.get("mask_derivation"), "pair-multiple mask_derivation")
    overrides = derivation.get("overrides")
    if derivation.get("base_signature_id") != base_id or not isinstance(overrides, list) or len(overrides) != 1:
        raise SystemExit("pair-multiple mask derivation must contain exactly the registered override")
    override = require_object(overrides[0], "mask_derivation.overrides[0]")
    override_interval = require_interval(
        override.get("interval"),
        "mask_derivation.overrides[0].interval",
        record_start=multiple_bounds[0],
        record_end=multiple_bounds[1],
        page_size=page_size,
    )
    if override_interval != additional or require_hex(
        override.get("mask_hex"), "mask_derivation.overrides[0].mask_hex", 4
    ) != b"\x00" * 4:
        raise SystemExit("pair-multiple mask override differs from the additional locator hole")

    inequality = require_object(
        multiple.get("mutual_exclusion_inequality"),
        "pair-multiple mutual_exclusion_inequality",
    )
    if set(inequality) != {"left", "relation", "right"} or inequality["relation"] != "not_equal":
        raise SystemExit("pair-multiple mutual-exclusion inequality is missing or changed")
    inequality_left = require_interval(
        inequality["left"],
        "mutual_exclusion_inequality.left",
        record_start=multiple_bounds[0],
        record_end=multiple_bounds[1],
        page_size=page_size,
    )
    inequality_right = require_object(
        inequality["right"], "mutual_exclusion_inequality.right"
    )
    if set(inequality_right) != {
        "signature_id",
        "interval",
        "fixed_value_hex",
        "fixed_mask_hex",
    }:
        raise SystemExit("pair-multiple mutual-exclusion right operand is not closed")
    base_interval = require_interval(
        inequality_right["interval"],
        "mutual_exclusion_inequality.right.interval",
        record_start=standard_bounds[0],
        record_end=standard_bounds[1],
        page_size=page_size,
    )
    start, end = additional
    relative_start, relative_end = start - standard_bounds[0], end - standard_bounds[0]
    fixed_value = base_value[relative_start:relative_end]
    fixed_mask = base_mask[relative_start:relative_end]
    if (
        inequality_left != additional
        or base_interval != additional
        or inequality_right["signature_id"] != base_id
        or require_hex(
            inequality_right["fixed_value_hex"],
            "mutual_exclusion_inequality.right.fixed_value_hex",
            4,
        )
        != fixed_value
        or require_hex(
            inequality_right["fixed_mask_hex"],
            "mutual_exclusion_inequality.right.fixed_mask_hex",
            4,
        )
        != fixed_mask
        or fixed_mask != b"\xff" * 4
    ):
        raise SystemExit("pair-multiple base signature mask/value mismatch")
    return derived_classes


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
    standard_signature = grammar["h1"]["table_record_signature"]
    standard_holes, _ = validate_holes(
        standard_signature, "candidate_grammars.h1.table_record_signature", page_size
    )
    standard_locator_identities = len(standard_holes)
    multiple_signature = grammar["h1"]["pair_multiple_reachability_signature"]
    identity_classes = derive_pair_multiple_identity_classes(
        standard_signature, multiple_signature, page_size
    )
    multiple_locator_identities = len(identity_classes)
    registered_multiple_bound = require_int(
        multiple_signature["maximum_distinct_target_identities_per_layout"],
        "pair-multiple distinct target identities",
    )
    if registered_multiple_bound != multiple_locator_identities:
        raise SystemExit(
            "pair-multiple distinct target identity bound "
            f"{registered_multiple_bound} != recomputed {multiple_locator_identities}"
        )
    maximum_locator_identities = max(
        standard_locator_identities, multiple_locator_identities
    )
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
        "tdef_lifecycle_signatures": derivation_replicas
        * qualified_pages
        * checkpoints
        * len(grammar["h1"]["tdef_lifecycle_signatures"]),
        "raw_locator_windows": derivation_replicas
        * qualified_pages
        * raw_locator_windows_per_page,
        "raw_locator_pairs": derivation_replicas
        * qualified_pages
        * locator_pairs_per_page,
        "h1_target_validity_checks": derivation_replicas
        * qualified_pages
        * len(grammar["h1"]["locator_layouts"])
        * maximum_locator_identities
        * checkpoints,
        "valid_path_row_directory_entries": derivation_replicas
        * qualified_pages
        * checkpoints
        * valid_rows,
        "type_1_slots": derivation_replicas
        * qualified_pages
        * checkpoints
        * 2
        * type_1_slots,
        "type_0_and_tag_05_bitmap_bits": derivation_replicas
        * qualified_pages
        * checkpoints
        * (type_0_bits + tag_05_bits),
        "role_transition_evaluations": derivation_replicas
        * len(grammar["h2"]["row_masks"])
        * len(grammar["h2"]["type_0_polarities"])
        * len(grammar["h2"]["locator_role_assignments"])
        * inputs["transition_legs"]
        * len(plan["tables"]["logical_roles"])
        * checkpoints,
        "base_formula_evaluations": derivation_replicas
        * len(grammar["h3"]["base_formulas"])
        * qualified_pages
        * checkpoints,
        "catalog_root_signatures": qualified_pages
        * checkpoints
        * derivation_replicas
        * len(grammar["h4"]["catalog_root_selection_signatures"]),
        "catalog_raw_rows": derivation_replicas
        * len(operations)
        * qualified_pages
        * valid_rows,
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
        "invalid_path_row_directory_entries": derivation_replicas
        * qualified_pages
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
