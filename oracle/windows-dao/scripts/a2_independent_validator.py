#!/usr/bin/env python3
"""Independently recompute and validate an A2 analysis bundle.

The implementation was written from the A2 plan, R2 revision, README, adjacent
schemas, and provenance entries EXP-0040/EXP-0041 only.  It deliberately does
not import ``a2_spec.py`` (even for schema loading), and does not read or share
code with the A2 analyzer, layers, model, dry runs, generators, or their tests.

The sole positional argument is the complete bundle root.  Exactly one
canonical, compact JSON verdict is written to stdout.  Every structural,
recomputation, comparison, holdout, and named-no-outcome failure is a
discrepancy and produces a nonzero exit status.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from a2_independent_bundle import BundleError, Contract, verify_bundle
from a2_independent_core import (
    PAGE_SIZE,
    derive_global_models,
    derive_tdef_models,
    global_model_predicts,
    global_qualifying_pages,
    growth_polarity_violations,
    tdef_qualifying_pages,
)


def _discrepancy(code: str, message: str, layer: str = "campaign") -> dict[str, str]:
    return {"code": code, "layer": layer, "message": message}


def _predicate_status(report: dict[str, Any], predicate_id: str) -> str | None:
    matches = [
        item["status"]
        for item in report["predicate_results"]
        if item["predicate_id"] == predicate_id
    ]
    return matches[0] if len(matches) == 1 else None


def _reason_mapping(plan: dict[str, Any]) -> dict[str, str]:
    return {
        item["reason"]: item["predicate_id"]
        for item in plan["predicate_registry"]["mappings"]
    }


def recompute(bundle_root: Path) -> dict[str, Any]:
    discrepancies: list[dict[str, str]] = []
    layer_verdicts: dict[str, dict[str, Any]] = {
        "global_map.record": {"accepted": False},
        "global_map.conversion_inline": {"accepted": False},
        "global_map.extended_base": {"accepted": False},
        "tdef.pointer_pair": {"accepted": False},
    }
    try:
        contract = Contract.load(Path(__file__).resolve().parent)
        bundle = verify_bundle(bundle_root, contract)
    except (BundleError, OSError, KeyError, TypeError, ValueError) as exc:
        discrepancies.append(_discrepancy("bundle_validation_failed", str(exc)))
        return {
            "accepted": False,
            "layer_verdicts": layer_verdicts,
            "discrepancies": discrepancies,
        }

    plan = contract.plan
    report = bundle.report
    derivation = bundle.views((1, 2))
    holdout = bundle.views((3,))[0]
    bound = plan["bounds"]["max_qualified_pages_per_submodel"]

    for view in (*derivation, holdout):
        for left, right in plan["checkpoint_design"]["idle_pairs"]:
            if view.page_hashes[left] != view.page_hashes[right]:
                discrepancies.append(
                    _discrepancy(
                        "idle_volatility",
                        f"replica {view.replica} differs across {left}/{right}",
                    )
                )

    global_pages = global_qualifying_pages(derivation)
    tdef_pages = tdef_qualifying_pages(derivation)
    if len(global_pages) > bound or len(tdef_pages) > bound:
        discrepancies.append(
            _discrepancy(
                "resource_bound_breach",
                f"qualified pages global={len(global_pages)}, tdef={len(tdef_pages)}, bound={bound}",
            )
        )
    if report["qualified_page_counts"] != {
        "global_map": len(global_pages),
        "tdef": len(tdef_pages),
    }:
        discrepancies.append(
            _discrepancy(
                "qualified_page_count_mismatch",
                "report qualified-page counts differ from hash-only recomputation",
            )
        )
    candidates_per_page = PAGE_SIZE * (PAGE_SIZE + 1) // 2
    record_candidates = (len(global_pages) + len(tdef_pages)) * candidates_per_page
    if record_candidates > plan["bounds"]["max_record_candidates"]:
        discrepancies.append(
            _discrepancy(
                "resource_bound_breach", "recomputed interval count exceeds the plan"
            )
        )
    if report["record_candidates_examined"] != record_candidates:
        discrepancies.append(
            _discrepancy(
                "record_candidate_count_mismatch",
                f"report={report['record_candidates_examined']}, recomputed={record_candidates}",
            )
        )

    global_models = (
        derive_global_models(derivation, global_pages)
        if len(global_pages) <= bound
        else ()
    )
    record_layer = report["submodels"]["global_map"]["record"]
    record_discrepancies_before = len(discrepancies)
    layer_verdicts["global_map.record"].update(
        {
            "derived_survivor_count": len(global_models),
            "qualified_pages": list(global_pages),
            "report_status": record_layer["status"],
        }
    )
    if len(global_models) != 1:
        discrepancies.append(
            _discrepancy(
                "global_record_not_unique",
                f"independent derivation retained {len(global_models)} models",
                "global_map.record",
            )
        )
    else:
        derived = global_models[0]
        derived_value = derived.report_value()
        layer_verdicts["global_map.record"]["derived_model"] = derived_value
        if record_layer["status"] != "decisive_predicts_holdout":
            discrepancies.append(
                _discrepancy(
                    "global_record_status_mismatch",
                    "one independent model survived but the report is not decisive",
                    "global_map.record",
                )
            )
        if record_layer["model"] != derived_value:
            discrepancies.append(
                _discrepancy(
                    "global_record_model_mismatch",
                    "reported global_map.record differs from independent recomputation",
                    "global_map.record",
                )
            )
        if (
            report["derivation_survivor_counts"]["global_map_record"] != 1
            or record_layer["derivation_survivor_count"] != 1
        ):
            discrepancies.append(
                _discrepancy(
                    "global_record_survivor_count_mismatch",
                    "reported global record survivor count is not one",
                    "global_map.record",
                )
            )
        holdout_pass = global_model_predicts(holdout, derived)
        layer_verdicts["global_map.record"]["holdout_prediction"] = holdout_pass
        if not holdout_pass:
            discrepancies.append(
                _discrepancy(
                    "holdout_prediction_failure",
                    "the frozen derivation model does not predict replica 3",
                    "global_map.record",
                )
            )
        if not record_layer["holdout_evaluated"] or not report["holdout_evaluated"]:
            discrepancies.append(
                _discrepancy(
                    "holdout_reporting_mismatch",
                    "report does not record the independently required holdout evaluation",
                    "global_map.record",
                )
            )
    layer_verdicts["global_map.record"]["accepted"] = (
        len(discrepancies) == record_discrepancies_before
    )

    conversion_layer = report["submodels"]["global_map"]["conversion_inline"]
    conversion_before = len(discrepancies)
    layer_verdicts["global_map.conversion_inline"].update(
        {"report_status": conversion_layer["status"], "confirmed_reasons": []}
    )
    if conversion_layer["status"] == "no_outcome" and conversion_layer[
        "no_outcome_reasons"
    ] == ["growth_polarity_disagreement"]:
        if len(global_models) != 1:
            discrepancies.append(
                _discrepancy(
                    "polarity_crosscheck_unavailable",
                    "a frozen global record is required to recompute the cross-check",
                    "global_map.conversion_inline",
                )
            )
        else:
            violations = [
                growth_polarity_violations(view, global_models[0])
                for view in derivation
            ]
            layer_verdicts["global_map.conversion_inline"][
                "violating_growth_transitions"
            ] = [
                [list(pair) for pair in replica_violations]
                for replica_violations in violations
            ]
            if not violations[0] or violations[0] != violations[1]:
                discrepancies.append(
                    _discrepancy(
                        "growth_polarity_reason_not_confirmed",
                        "derivation replicas do not agree on a nonempty polarity violation set",
                        "global_map.conversion_inline",
                    )
                )
            else:
                layer_verdicts["global_map.conversion_inline"]["confirmed_reasons"] = [
                    "growth_polarity_disagreement"
                ]
    else:
        discrepancies.append(
            _discrepancy(
                "unsupported_conversion_outcome",
                "this independent validator requires the report's conversion no-outcome to be the recomputable growth polarity disagreement",
                "global_map.conversion_inline",
            )
        )
    layer_verdicts["global_map.conversion_inline"]["accepted"] = (
        len(discrepancies) == conversion_before
    )

    base_layer = report["submodels"]["global_map"]["extended_base"]
    base_before = len(discrepancies)
    layer_verdicts["global_map.extended_base"].update(
        {"report_status": base_layer["status"]}
    )
    if not (
        base_layer["status"] == "not_applicable"
        and base_layer["model"] is None
        and base_layer["no_outcome_reasons"] == []
        and conversion_layer["status"] == "no_outcome"
    ):
        discrepancies.append(
            _discrepancy(
                "extended_base_dependency_mismatch",
                "extended base must be not-applicable after conversion no-outcome",
                "global_map.extended_base",
            )
        )
    layer_verdicts["global_map.extended_base"]["accepted"] = (
        len(discrepancies) == base_before
    )

    tdef_layer = report["submodels"]["tdef"]["pointer_pair"]
    tdef_before = len(discrepancies)
    tdef_models = (
        derive_tdef_models(derivation, tdef_pages) if len(tdef_pages) <= bound else ()
    )
    layer_verdicts["tdef.pointer_pair"].update(
        {
            "derived_survivor_count": len(tdef_models),
            "qualified_pages": list(tdef_pages),
            "report_status": tdef_layer["status"],
            "confirmed_reasons": [],
        }
    )
    if not tdef_pages:
        derived_reason = "no_physical_page_satisfies_tdef_transition_predicates"
    elif not tdef_models:
        derived_reason = "no_tdef_record_candidate"
    elif len(tdef_models) > 1:
        derived_reason = "multiple_tdef_record_boundaries_survive"
    else:
        derived_reason = None
    if derived_reason is not None:
        if tdef_layer["status"] != "no_outcome" or tdef_layer["no_outcome_reasons"] != [
            derived_reason
        ]:
            discrepancies.append(
                _discrepancy(
                    "tdef_reason_mismatch",
                    f"report does not name independently derived reason {derived_reason}",
                    "tdef.pointer_pair",
                )
            )
        else:
            layer_verdicts["tdef.pointer_pair"]["confirmed_reasons"] = [derived_reason]
    else:
        discrepancies.append(
            _discrepancy(
                "unsupported_decisive_tdef",
                "a TDEF model survived; decisive TDEF holdout evaluation is not implemented",
                "tdef.pointer_pair",
            )
        )
    if report["derivation_survivor_counts"]["tdef_pointer_pair"] != len(
        tdef_models
    ) or tdef_layer["derivation_survivor_count"] != len(tdef_models):
        discrepancies.append(
            _discrepancy(
                "tdef_survivor_count_mismatch",
                "reported TDEF survivor count differs from recomputation",
                "tdef.pointer_pair",
            )
        )
    layer_verdicts["tdef.pointer_pair"]["accepted"] = len(discrepancies) == tdef_before

    expected_reasons = sorted(
        reason
        for layer in (
            record_layer,
            conversion_layer,
            base_layer,
            tdef_layer,
        )
        for reason in layer["no_outcome_reasons"]
    )
    if sorted(report["no_outcome_reasons"]) != expected_reasons:
        discrepancies.append(
            _discrepancy(
                "no_outcome_summary_mismatch",
                "top-level no-outcome reasons differ from layers",
            )
        )
    mapping = _reason_mapping(plan)
    expected_terminal = sorted(mapping[reason] for reason in expected_reasons)
    if sorted(report["terminal_predicate_ids"]) != expected_terminal:
        discrepancies.append(
            _discrepancy(
                "terminal_predicate_mismatch",
                "terminal predicate ids do not match layer reasons",
            )
        )
    for reason in expected_reasons:
        predicate = mapping[reason]
        if _predicate_status(report, predicate) != "fail":
            discrepancies.append(
                _discrepancy(
                    "predicate_result_mismatch",
                    f"terminal predicate {predicate} is not uniquely recorded as fail",
                )
            )
    scientific = any(
        layer["status"] == "decisive_predicts_holdout"
        for layer in (record_layer, conversion_layer, base_layer, tdef_layer)
    )
    expected_scientific = (
        "one_or_more_submodels_predict_holdout"
        if scientific
        else "no_submodel_predicts_holdout"
    )
    if report["scientific_outcome"] != expected_scientific:
        discrepancies.append(
            _discrepancy(
                "scientific_outcome_mismatch",
                "scientific outcome differs from layer statuses",
            )
        )

    discrepancies.sort(key=lambda item: (item["layer"], item["code"], item["message"]))
    return {
        "accepted": not discrepancies,
        "layer_verdicts": layer_verdicts,
        "discrepancies": discrepancies,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        verdict = {
            "accepted": False,
            "layer_verdicts": {},
            "discrepancies": [
                _discrepancy("usage", "usage: a2_independent_validator.py BUNDLE_ROOT")
            ],
        }
    else:
        try:
            verdict = recompute(Path(argv[1]))
        except Exception as exc:  # noqa: BLE001 - the CLI must fail closed on implementation faults.
            verdict = {
                "accepted": False,
                "layer_verdicts": {},
                "discrepancies": [
                    _discrepancy(
                        "internal_validation_error", f"{type(exc).__name__}: {exc}"
                    )
                ],
            }
    sys.stdout.write(json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if verdict["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
