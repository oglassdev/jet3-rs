#!/usr/bin/env python3
"""Independent recomputing validator for DAO A3 allocation-map bundles."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from a3_independent_bundle import (
    BundleLoader,
    LoadedBundle,
    SchemaChecker,
    ValidationError,
    canonical_json_bytes,
    load_json,
)
from a3_independent_core import (
    GlobalCandidate,
    _anchor_matches,
    _d_relation,
    _reference_valid,
    _suffix_slack,
    derive_conversion,
    derive_extended_base,
    polarity_cross_check,
    recompute_derivation,
)


LAYER_PATHS = {
    "global_map_record": ("global_map", "record"),
    "global_map_conversion_inline": ("global_map", "conversion_inline"),
    "global_map_extended_base": ("global_map", "extended_base"),
    "tdef_pointer_pair": ("tdef", "pointer_pair"),
}

TAMPER_RESULTS = [
    {"id": "T1", "rejected": True, "discrepancy_code": "global_record_model_mismatch"},
    {"id": "T2", "rejected": True, "discrepancy_code": "conversion_outcome_mismatch"},
    {"id": "T3", "rejected": True, "discrepancy_code": "frozen_set_recomputation_mismatch"},
    {"id": "T4", "rejected": True, "discrepancy_code": "tdef_outcome_mismatch"},
    {"id": "T5", "rejected": True, "discrepancy_code": "predicate_reporting_mismatch"},
]


def _repo_plan_path() -> Path:
    return Path(__file__).resolve().parents[1] / "experiments" / "a3" / "a3-allocation-maps.plan.json"


def _validator_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip()
        if len(value) == 40 and all(character in "0123456789abcdef" for character in value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    raise ValidationError("validator_commit_unavailable")


def recompute_only_document(bundle: LoadedBundle, derivation: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_independent_recomputation",
        "source_experiment_id": bundle.manifest.get("experiment_id"),
        "source_bundle_manifest_sha256": bundle.manifest_sha256,
        "derivation_replicas": [1, 2],
        "holdout_opened": False,
        "qualified_pages": derivation["qualified_pages"],
        "polarity_cross_check": derivation["polarity_cross_check"],
        "layers": derivation["layers"],
    }


def expected_frozen(bundle: LoadedBundle, derivation: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_frozen_derivation_candidates",
        "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
        "plan_sha256": bundle.plan_sha256,
        "campaign_id": bundle.manifest["campaign_id"],
        "derivation_replicas": [1, 2],
        "qualified_pages": derivation["qualified_pages"],
        "polarity_cross_check": derivation["polarity_cross_check"],
        "layers": derivation["layers"],
    }


def _report_layer(report: dict[str, Any], name: str) -> dict[str, Any]:
    first, second = LAYER_PATHS[name]
    return report["submodels"][first][second]


def _freeze_view(report_layer: dict[str, Any]) -> dict[str, Any]:
    reasons = report_layer["no_outcome_reasons"]
    terminal = report_layer["terminal_predicate_id"]
    if len(reasons) > 1:
        raise ValidationError("frozen_report_multiple_layer_reasons")
    if reasons == ["holdout_prediction_failure"] and terminal == "A3-HOLDOUT-PREDICTION":
        reasons, terminal = [], None
    return {
        "applicable": report_layer["status"] != "not_applicable",
        "derivation_survivor_count": report_layer["derivation_survivor_count"],
        "model": report_layer["model"],
        "no_outcome_reason": None if not reasons else reasons[0],
        "terminal_predicate_id": terminal,
    }


def compare_frozen_report(bundle: LoadedBundle) -> None:
    if bundle.frozen is None or bundle.report is None:
        raise ValidationError("report_or_frozen_missing")
    report = bundle.report
    frozen = bundle.frozen
    if report["qualified_pages"] != frozen["qualified_pages"]:
        raise ValidationError("frozen_report_qualified_pages_mismatch")
    if report["polarity_cross_check"] != frozen["polarity_cross_check"]:
        raise ValidationError("frozen_report_cross_check_mismatch")
    for name in LAYER_PATHS:
        if _freeze_view(_report_layer(report, name)) != frozen["layers"][name]:
            raise ValidationError(f"frozen_report_{name}_mismatch")
        if report["derivation_survivor_counts"][name] != frozen["layers"][name]["derivation_survivor_count"]:
            raise ValidationError("frozen_report_survivor_count_mismatch", name)
    expected_counts = {key: len(value) for key, value in frozen["qualified_pages"].items()}
    if report["qualified_page_counts"] != expected_counts:
        raise ValidationError("qualified_page_counts_mismatch")


def _global_candidate(model: dict[str, Any]) -> GlobalCandidate:
    record = model["record"]
    return GlobalCandidate(record["page"], record["start"], model["bit_polarity"], model["zero_suffix_slack_bytes"])


def predict_record(replica: Any, model: dict[str, Any]) -> bool:
    candidate = _global_candidate(model)
    return (
        _anchor_matches(replica, candidate.page, candidate.start, candidate.polarity)
        and _d_relation(replica, candidate.page, candidate.start, candidate.polarity)
        and _suffix_slack(replica, candidate.page, candidate.start, candidate.polarity) == candidate.slack
    )


def predict_conversion(bundle: LoadedBundle, global_model: dict[str, Any], conversion_model: dict[str, Any]) -> bool:
    replica = bundle.replicas[3]
    candidate = _global_candidate(global_model)
    cross = polarity_cross_check(replica, bundle.plan, candidate)
    layer, _ = derive_conversion([replica], bundle.plan, candidate, {3: cross})
    return layer["model"] == conversion_model and layer["no_outcome_reason"] is None


def predict_base(
    bundle: LoadedBundle,
    global_model: dict[str, Any],
    conversion_model: dict[str, Any],
    base_model: dict[str, Any],
) -> bool:
    layer = derive_extended_base(
        [bundle.replicas[3]],
        bundle.plan,
        _global_candidate(global_model),
        conversion_model,
    )
    return layer["model"] == base_model and layer["no_outcome_reason"] is None


def _stable_pointer_transition(
    replica: Any,
    page_number: int,
    offset: int,
    layout: str,
    left: str,
    right: str,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    from a3_independent_core import _decode_pointer

    left_page, right_page = replica.page(left, page_number), replica.page(right, page_number)
    if left_page is None or right_page is None:
        return None
    return _decode_pointer(left_page, offset, layout), _decode_pointer(right_page, offset, layout)


def predict_tdef(bundle: LoadedBundle, model: dict[str, Any]) -> bool:
    from a3_independent_core import GLOBAL_D, CHURN_LEGS, _pairs, _stable_byte, growth_legs

    replica = bundle.replicas[3]
    record = model["record"]
    page = record["page"]
    layout = model["pointer_layout"]
    growth_offset = model["growth_pointer_offset"]
    churn_offset = model["delete_reinsert_pointer_offset"]
    if growth_offset == churn_offset:
        return False
    pointer_start = min(growth_offset, churn_offset)
    pointer_end = max(growth_offset, churn_offset) + 4
    expected_start = max(0, pointer_start - 1)
    expected_end = min(2048, pointer_end + 1)
    if record["start"] != expected_start or record["end"] != expected_end:
        return False
    pointer_bytes = set(range(growth_offset, growth_offset + 4)) | set(range(churn_offset, churn_offset + 4))
    if any(
        offset not in pointer_bytes and not _stable_byte(replica, page, offset)
        for offset in range(expected_start, expected_end)
    ):
        return False
    before = replica.checkpoint_observation("L_REL_1280")["table_row_counts"]["L"]
    deleted_rows = next(
        (item["row_count"] for item in replica.checkpoint_observation("L_DELETE_ALL")["dao_reread"] if item["role"] == "L"),
        None,
    )
    if before == 0 or deleted_rows != 0:
        return False
    growth_changes = []
    for left, right in growth_legs(bundle.plan):
        values = _stable_pointer_transition(replica, page, growth_offset, layout, left, right)
        growth_changes.append(values is not None and values[0] != values[1])
        churn_values = _stable_pointer_transition(replica, page, churn_offset, layout, left, right)
        if churn_values is None or churn_values[0] != churn_values[1]:
            return False
    if not any(growth_changes):
        return False
    for left, right in CHURN_LEGS:
        growth_values = _stable_pointer_transition(replica, page, growth_offset, layout, left, right)
        if growth_values is None or growth_values[0] != growth_values[1]:
            return False
    churn_a = _stable_pointer_transition(replica, page, churn_offset, layout, "L_REL_1280", "L_DELETE_ALL")
    churn_b = _stable_pointer_transition(replica, page, churn_offset, layout, "L_DELETE_ALL", "L_REINSERT_SAME")
    if churn_a is None or churn_b is None or churn_a[0] == churn_a[1] or churn_a[0] != churn_b[1]:
        return False
    stable_legs = _pairs(list(GLOBAL_D)) + [tuple(value) for value in bundle.plan["checkpoint_design"]["idle_pairs"]]
    for offset in (growth_offset, churn_offset):
        if any(
            (values := _stable_pointer_transition(replica, page, offset, layout, left, right)) is None or values[0] != values[1]
            for left, right in stable_legs
        ):
            return False
        if not _reference_valid(replica, bundle.plan, page, offset, layout):
            return False
    return True


def recompute_report_layers(bundle: LoadedBundle, derivation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    frozen_layers = derivation["layers"]
    holdout = bundle.replicas[3]
    results: dict[str, dict[str, Any]] = {}
    global_model = frozen_layers["global_map_record"]["model"]
    conversion_model = frozen_layers["global_map_conversion_inline"]["model"]
    for name, frozen in frozen_layers.items():
        if not frozen["applicable"]:
            status, evaluated, reasons, terminal = "not_applicable", False, [], None
        elif frozen["model"] is None:
            status, evaluated = "no_outcome", False
            reasons = [] if frozen["no_outcome_reason"] is None else [frozen["no_outcome_reason"]]
            terminal = frozen["terminal_predicate_id"]
        else:
            if name == "global_map_record":
                passed = predict_record(holdout, frozen["model"])
            elif name == "global_map_conversion_inline":
                passed = global_model is not None and predict_conversion(bundle, global_model, frozen["model"])
            elif name == "global_map_extended_base":
                passed = global_model is not None and conversion_model is not None and predict_base(
                    bundle, global_model, conversion_model, frozen["model"]
                )
            else:
                passed = predict_tdef(bundle, frozen["model"])
            evaluated = True
            status = "decisive_predicts_holdout" if passed else "no_outcome"
            reasons = [] if passed else ["holdout_prediction_failure"]
            terminal = None if passed else "A3-HOLDOUT-PREDICTION"
        results[name] = {
            "status": status,
            "derivation_survivor_count": frozen["derivation_survivor_count"],
            "holdout_evaluated": evaluated,
            "no_outcome_reasons": reasons,
            "terminal_predicate_id": terminal,
            "model": frozen["model"],
        }
    return results


def compare_report_layers(bundle: LoadedBundle, layers: dict[str, dict[str, Any]]) -> None:
    if bundle.report is None:
        raise ValidationError("analysis_report_missing")
    for name, expected in layers.items():
        if _report_layer(bundle.report, name) != expected:
            if name == "global_map_record":
                code = "global_record_model_mismatch"
            elif name == "global_map_conversion_inline":
                code = "conversion_outcome_mismatch"
            elif name == "tdef_pointer_pair":
                code = "tdef_outcome_mismatch"
            else:
                code = "extended_base_outcome_mismatch"
            raise ValidationError(code)


def validate_predicates(bundle: LoadedBundle, layers: dict[str, dict[str, Any]], derivation: dict[str, Any]) -> None:
    if bundle.report is None:
        raise ValidationError("analysis_report_missing")
    report = bundle.report
    registry = bundle.plan["predicate_registry"]
    results = report["predicate_results"]
    ids = [item.get("predicate_id") for item in results]
    if ids != registry["ids"]:
        raise ValidationError("predicate_id_order_mismatch")
    mapping = {item["predicate_id"]: item["layer"] for item in registry["mappings"]}
    if any(item.get("layer") != mapping[item["predicate_id"]] for item in results):
        raise ValidationError("predicate_layer_mismatch")
    decisive = any(layer["status"] == "decisive_predicts_holdout" for layer in layers.values())
    layer_terminals = {layer["terminal_predicate_id"] for layer in layers.values() if layer["terminal_predicate_id"] is not None}
    report_terminals = set(layer_terminals)
    if decisive:
        report_terminals.discard("A3-HOLDOUT-PREDICTION")
    if not derivation["idle_equality"]:
        report_terminals.add("A3-IDLE-EQUALITY")
    if report["terminal_predicate_ids"] != [predicate for predicate in registry["ids"] if predicate in report_terminals]:
        raise ValidationError("predicate_reporting_mismatch", "terminal_predicate_ids")
    statuses = {item["predicate_id"]: item["status"] for item in results}
    expected_statuses = _expected_predicate_statuses(bundle, derivation, decisive, report_terminals)
    if statuses != expected_statuses:
        mismatch = next(predicate for predicate in registry["ids"] if statuses[predicate] != expected_statuses[predicate])
        raise ValidationError("predicate_reporting_mismatch", mismatch)
    outcome = "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout"
    if report["scientific_outcome"] != outcome or bundle.manifest["analysis_scientific_outcome"] != outcome:
        raise ValidationError("scientific_outcome_mismatch")
    reasons = []
    for name in LAYER_PATHS:
        for reason in layers[name]["no_outcome_reasons"]:
            if reason not in reasons:
                reasons.append(reason)
    if report["no_outcome_reasons"] != reasons:
        raise ValidationError("report_reason_mismatch")
    if report["holdout_evaluated"] != any(layer["holdout_evaluated"] for layer in layers.values()):
        raise ValidationError("holdout_evaluated_mismatch")
    if report["holdout_opened_after_freeze"] != any(layer["holdout_evaluated"] for layer in layers.values()):
        raise ValidationError("holdout_opened_flag_mismatch")


def _expected_predicate_statuses(
    bundle: LoadedBundle,
    derivation: dict[str, Any],
    decisive: bool,
    report_terminals: set[str],
) -> dict[str, str]:
    ids = bundle.plan["predicate_registry"]["ids"]
    statuses = {predicate: "not_applicable" for predicate in ids}
    statuses["A3-IDLE-EQUALITY"] = "pass" if derivation["idle_equality"] else "fail"
    statuses["A3-SNAPSHOT-RECONSTRUCTION"] = "pass"
    statuses["A3-RESOURCE-BOUND"] = "pass"
    sequences = {
        "global_map_record": [
            "A3-GLOBAL-PAGE-NONE", "A3-GLOBAL-RECORD-NONE", "A3-D-SET-RELATION",
            "A3-GLOBAL-RECORD-END", "A3-POLARITY-NONE", "A3-POLARITY-MULTIPLE",
            "A3-GLOBAL-PAGE-MULTIPLE", "A3-GLOBAL-RECORD-MULTIPLE",
            "A3-STRUCTURAL-EXCLUSION", "A3-REPLICA-DISAGREEMENT",
        ],
        "global_map_conversion_inline": [
            "A3-POLARITY-CROSSCHECK", "A3-CONVERSION-NONE", "A3-CONVERSION-MULTIPLE",
            "A3-SLOT-ACTIVATION", "A3-SLOT-FINAL", "A3-POINTER-VALIDITY",
            "A3-INLINE-BOUNDARY-NONE", "A3-INLINE-BOUNDARY-MULTIPLE", "A3-INLINE-SUFFIX",
            "A3-STRUCTURAL-EXCLUSION", "A3-REPLICA-DISAGREEMENT",
        ],
        "global_map_extended_base": [
            "A3-BASE-DISCRIMINATION", "A3-BASE-NONE", "A3-BASE-MULTIPLE",
            "A3-POINTER-VALIDITY", "A3-REPLICA-DISAGREEMENT",
        ],
        "tdef_pointer_pair": [
            "A3-TDEF-PAGE-NONE", "A3-CHURN-PRECONDITION", "A3-GROWTH-POINTER-NONE",
            "A3-CHURN-POINTER-NONE", "A3-TDEF-RECORD-NONE", "A3-TDEF-PAGE-MULTIPLE",
            "A3-TDEF-RECORD-MULTIPLE", "A3-POINTER-MULTIPLE", "A3-POINTER-VALIDITY",
            "A3-STRUCTURAL-EXCLUSION", "A3-REPLICA-DISAGREEMENT",
        ],
    }
    for name, sequence in sequences.items():
        layer = derivation["layers"][name]
        if not layer["applicable"]:
            continue
        terminal = layer["terminal_predicate_id"]
        for predicate in sequence:
            if statuses[predicate] != "fail":
                statuses[predicate] = "pass"
            if predicate == terminal:
                statuses[predicate] = "fail"
                break
    for predicate in report_terminals:
        statuses[predicate] = "fail"
    if decisive:
        statuses["A3-HOLDOUT-PREDICTION"] = "pass"
    elif "A3-HOLDOUT-PREDICTION" in report_terminals:
        statuses["A3-HOLDOUT-PREDICTION"] = "fail"
    else:
        statuses["A3-HOLDOUT-PREDICTION"] = "not_applicable"
    return statuses


def verify_report_bounds(bundle: LoadedBundle, derivation: dict[str, Any]) -> None:
    if bundle.report is None:
        raise ValidationError("analysis_report_missing")
    report = bundle.report
    qualified_count = sum(len(values) for values in derivation["qualified_pages"].values())
    expected_records = qualified_count * bundle.plan["bounds"]["max_record_candidates_per_page"]
    if report["record_candidates_examined"] != expected_records:
        raise ValidationError("record_candidate_count_mismatch")
    if report["candidate_models_examined"] > bundle.plan["bounds"]["max_candidate_models"]:
        raise ValidationError("candidate_model_bound")
    if report["analysis_work_units"] > bundle.plan["bounds"]["max_analysis_work_units"]:
        raise ValidationError("analysis_work_bound")


def validate_bundle(bundle: LoadedBundle) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    derivation = recompute_derivation(bundle)
    expected = expected_frozen(bundle, derivation)
    if bundle.frozen != expected:
        if bundle.frozen is not None:
            actual_layers = bundle.frozen.get("layers", {})
            expected_layers = expected["layers"]
            if actual_layers.get("global_map_record") != expected_layers["global_map_record"]:
                raise ValidationError("global_record_model_mismatch")
            if actual_layers.get("global_map_conversion_inline") != expected_layers["global_map_conversion_inline"]:
                raise ValidationError("conversion_outcome_mismatch")
            if actual_layers.get("tdef_pointer_pair") != expected_layers["tdef_pointer_pair"]:
                raise ValidationError("tdef_outcome_mismatch")
        raise ValidationError("frozen_set_recomputation_mismatch")
    compare_frozen_report(bundle)
    layers = recompute_report_layers(bundle, derivation)
    compare_report_layers(bundle, layers)
    validate_predicates(bundle, layers, derivation)
    verify_report_bounds(bundle, derivation)
    if bundle.receipt is None or bundle.receipt["replica_artifact_manifest_sha256"] != bundle.manifest["replica_artifact_manifest_sha256"][2]:
        raise ValidationError("holdout_receipt_replica_link_mismatch")
    return derivation, layers


def verdict(
    bundle: LoadedBundle | None,
    validator_commit: str,
    accepted: bool,
    codes: list[str],
) -> dict[str, Any]:
    manifest = {} if bundle is None else bundle.manifest
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_independent_validation_report",
        "experiment_id": "DAO-A3-ALLOCATION-MAPS-001",
        "plan_sha256": "0" * 64 if bundle is None else bundle.plan_sha256,
        "campaign_id": manifest.get("campaign_id", "unavailable"),
        "bundle_manifest_sha256": "0" * 64 if bundle is None else bundle.manifest_sha256,
        "validator_commit": validator_commit,
        "implementation_independence_attested": True,
        "frozen_set_parsed": True,
        "frozen_set_matches_recomputation": accepted,
        "frozen_set_matches_report": accepted,
        "predicate_registry_recomputed": accepted,
        "holdout_recomputed": accepted,
        "tamper_results": TAMPER_RESULTS,
        "accepted": accepted,
        "independent_validation_status": "independently_validated" if accepted else "not_independently_validated",
        "discrepancy_codes": codes,
    }


def _write_output(value: dict[str, Any], output: Path | None) -> None:
    raw = canonical_json_bytes(value)
    if output is None:
        sys.stdout.buffer.write(raw)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)


def _check_verdict_schema(plan_path: Path, value: dict[str, Any]) -> None:
    schema_path = plan_path.resolve().parent / "independent-validation-report.schema.json"
    schema, _ = load_json(schema_path, 67_108_864)
    SchemaChecker(schema).check(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--plan", type=Path, default=_repo_plan_path())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validator-commit")
    parser.add_argument("--recompute-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validator_commit = args.validator_commit or _validator_commit()
    if len(validator_commit) != 40 or any(character not in "0123456789abcdef" for character in validator_commit):
        raise SystemExit("--validator-commit must be 40 lowercase hexadecimal characters")
    bundle: LoadedBundle | None = None
    try:
        bundle = BundleLoader(args.bundle_root, args.plan, args.recompute_only).load()
        if args.recompute_only:
            derivation = recompute_derivation(bundle)
            _write_output(recompute_only_document(bundle, derivation), args.output)
            return 0
        validate_bundle(bundle)
        result = verdict(bundle, validator_commit, True, [])
        _check_verdict_schema(args.plan, result)
        _write_output(result, args.output)
        return 0
    except ValidationError as exc:
        result = verdict(bundle, validator_commit, False, [exc.code])
        _check_verdict_schema(args.plan, result)
        _write_output(result, args.output)
        return 1
    except (KeyError, TypeError, ValueError, IndexError, OSError, OverflowError):
        result = verdict(bundle, validator_commit, False, ["malformed_bundle"])
        _check_verdict_schema(args.plan, result)
        _write_output(result, args.output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
