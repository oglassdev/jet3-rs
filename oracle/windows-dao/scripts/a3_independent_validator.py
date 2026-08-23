#!/usr/bin/env python3
"""Independent recomputing validator for DAO A3 allocation-map bundles."""

from __future__ import annotations

import argparse
import copy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from a3_independent_bundle import (
    BundleLoader,
    LoadedBundle,
    SchemaChecker,
    ValidationError,
    canonical_json_bytes,
    load_json,
    sha256_bytes,
)
from a3_independent_base import base_formula_survives
from a3_independent_core import (
    GlobalCandidate,
    _anchor_matches,
    _d_relation,
    _reference_valid,
    _suffix_slack,
    derive_conversion,
    polarity_cross_check,
    recompute_derivation,
)


LAYER_PATHS = {
    "global_map_record": ("global_map", "record"),
    "global_map_conversion_inline": ("global_map", "conversion_inline"),
    "global_map_extended_base": ("global_map", "extended_base"),
    "tdef_pointer_pair": ("tdef", "pointer_pair"),
}

R2_SHA256 = "3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1"
R3_SHA256 = "bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a"
R2_LAYER_NAME_MAP = {
    "global_map.record": "global_map_record",
    "global_map.conversion_inline": "global_map_conversion_inline",
    "global_map.extended_base": "global_map_extended_base",
    "tdef.pointer_pair": "tdef_pointer_pair",
}


def _not_executed_tamper_results() -> list[dict[str, Any]]:
    return [
        {"id": tamper_id, "rejected": False, "discrepancy_code": "not_executed"}
        for tamper_id in ("T1", "T2", "T3", "T4", "T5")
    ]

def _repo_plan_path() -> Path:
    return Path(__file__).resolve().parents[1] / "experiments" / "a3" / "a3-allocation-maps.plan.json"


def _repo_revision_path() -> Path:
    return Path(__file__).resolve().parents[1] / "experiments" / "a3" / "a3-allocation-maps-r3.plan.json"


def _load_predicate_sequences(
    revision_path: Path,
    plan_sha256: str,
    predicate_ids: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    revision, raw = load_json(revision_path, 67_108_864)
    if sha256_bytes(raw) != R3_SHA256:
        raise ValidationError("predicate_revision_hash_mismatch")
    try:
        original = revision["preregistration"]["original_plan"]
        prior = revision["preregistration"]["prior_revision"]
        prior_path = revision_path.with_name(Path(prior["path"]).name)
        r2, r2_raw = load_json(prior_path, 67_108_864)
        r2_original = r2["preregistration"]["original_plan"]
        reconciliation = r2["predicate_evaluation_sequence_reconciliation"]
        campaign = reconciliation["campaign_evaluated_before_any_layer"]
        published_layers = reconciliation["per_layer_ordered_predicates"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("predicate_revision_contract_mismatch") from exc
    if (
        revision.get("document_type") != "dao_a3_allocation_maps_plan_revision"
        or revision.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R3"
        or original.get("path") != "oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json"
        or original.get("sha256") != plan_sha256
        or prior.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R2"
        or prior.get("sha256") != R2_SHA256
        or sha256_bytes(r2_raw) != R2_SHA256
        or r2.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R2"
        or r2_original != original
        or not isinstance(campaign, list)
        or not all(isinstance(predicate, str) for predicate in campaign)
        or len(campaign) != len(set(campaign))
        or not isinstance(published_layers, dict)
        or set(published_layers) != set(R2_LAYER_NAME_MAP)
    ):
        raise ValidationError("predicate_revision_contract_mismatch")
    sequences: dict[str, list[str]] = {}
    for published_name, internal_name in R2_LAYER_NAME_MAP.items():
        sequence = published_layers[published_name]
        if (
            not isinstance(sequence, list)
            or not all(isinstance(predicate, str) for predicate in sequence)
            or len(sequence) != len(set(sequence))
        ):
            raise ValidationError("predicate_revision_contract_mismatch")
        sequences[internal_name] = sequence
    known = set(predicate_ids)
    if any(predicate not in known for predicate in campaign) or any(
        predicate not in known for sequence in sequences.values() for predicate in sequence
    ):
        raise ValidationError("predicate_revision_contract_mismatch")
    return campaign, sequences


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
        and _suffix_slack(replica, candidate.page, candidate.start, candidate.polarity) is not None
    )


def predict_conversion(bundle: LoadedBundle, global_model: dict[str, Any], conversion_model: dict[str, Any]) -> bool:
    replica = bundle.replicas[3]
    candidate = _global_candidate(global_model)
    cross = polarity_cross_check(replica, bundle.plan, candidate)
    layer, _, _ = derive_conversion([replica], bundle.plan, candidate, {3: cross})
    return layer["model"] == conversion_model and layer["no_outcome_reason"] is None


def predict_base(
    bundle: LoadedBundle,
    global_model: dict[str, Any],
    conversion_model: dict[str, Any],
    base_model: dict[str, Any],
) -> bool:
    return base_formula_survives(
        bundle.replicas[3],
        bundle.plan,
        _global_candidate(global_model),
        conversion_model,
        base_model["extended_base_formula"],
        False,
    )


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
    if not (growth_offset + 4 <= churn_offset or churn_offset + 4 <= growth_offset):
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
        growth_changes.append(values is not None and values[0][0] != values[1][0])
        churn_values = _stable_pointer_transition(replica, page, churn_offset, layout, left, right)
        if churn_values is None or churn_values[0][0] != churn_values[1][0]:
            return False
    if not any(growth_changes):
        return False
    for left, right in CHURN_LEGS:
        growth_values = _stable_pointer_transition(replica, page, growth_offset, layout, left, right)
        if growth_values is None or growth_values[0][0] != growth_values[1][0]:
            return False
    churn_a = _stable_pointer_transition(replica, page, churn_offset, layout, "L_REL_1280", "L_DELETE_ALL")
    churn_b = _stable_pointer_transition(replica, page, churn_offset, layout, "L_DELETE_ALL", "L_REINSERT_SAME")
    if (
        churn_a is None
        or churn_b is None
        or churn_a[0][0] == churn_a[1][0]
        or churn_a[0][0] != churn_b[1][0]
    ):
        return False
    schedule = bundle.plan["checkpoint_design"]["checkpoint_ids"]
    d_legs = _pairs(schedule[:6])
    idle_legs = [tuple(value) for value in bundle.plan["checkpoint_design"]["idle_pairs"]]
    p_legs = [
        ("L_IDLE_REOPEN", "P_ABS_04096"),
        ("P_ABS_04096", "P_ABS_08192"),
        ("P_ABS_08192", "P_ABS_12288"),
        ("P_ABS_12288", "P_ABS_16480"),
    ]
    structural_legs = {
        growth_offset: d_legs + idle_legs,
        churn_offset: d_legs + growth_legs(bundle.plan) + p_legs + idle_legs,
    }
    for offset, legs in structural_legs.items():
        if any(
            (values := _stable_pointer_transition(replica, page, offset, layout, left, right)) is None or values[0] != values[1]
            for left, right in legs
        ) or not _reference_valid(replica, bundle.plan, page, offset, layout):
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
            try:
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
            except ValidationError:
                passed = False
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


def _validate_terminal_predicate_ids(reported: list[str], expected: set[str]) -> None:
    reported_set = set(reported)
    if len(reported) != len(reported_set) or reported_set != expected:
        raise ValidationError("predicate_reporting_mismatch", "terminal_predicate_ids")


def validate_predicates(
    bundle: LoadedBundle,
    layers: dict[str, dict[str, Any]],
    derivation: dict[str, Any],
    campaign_predicates: list[str],
    predicate_sequences: dict[str, list[str]],
) -> None:
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
    campaign_terminal = derivation["campaign_terminal_predicate_id"]
    if campaign_terminal is not None:
        report_terminals.add(campaign_terminal)
    _validate_terminal_predicate_ids(report["terminal_predicate_ids"], report_terminals)
    statuses = {item["predicate_id"]: item["status"] for item in results}
    expected_statuses = _expected_predicate_statuses(
        bundle,
        derivation,
        decisive,
        report_terminals,
        campaign_predicates,
        predicate_sequences,
    )
    if statuses != expected_statuses:
        mismatch = next(predicate for predicate in registry["ids"] if statuses[predicate] != expected_statuses[predicate])
        raise ValidationError("predicate_reporting_mismatch", mismatch)
    outcome = "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout"
    if report["scientific_outcome"] != outcome or bundle.manifest["analysis_scientific_outcome"] != outcome:
        raise ValidationError("scientific_outcome_mismatch")
    reasons = []
    if campaign_terminal is not None:
        reason_by_predicate = {item["predicate_id"]: item["reason"] for item in registry["mappings"]}
        reasons.append(reason_by_predicate[campaign_terminal])
    for name in LAYER_PATHS:
        for reason in layers[name]["no_outcome_reasons"]:
            if reason not in reasons:
                reasons.append(reason)
    if report["no_outcome_reasons"] != reasons:
        raise ValidationError("report_reason_mismatch")
    holdout_opened = any(layer["model"] is not None for layer in derivation["layers"].values())
    if report["holdout_evaluated"] != holdout_opened:
        raise ValidationError("holdout_evaluated_mismatch")
    if report["holdout_opened_after_freeze"] != holdout_opened:
        raise ValidationError("holdout_opened_flag_mismatch")


def _expected_predicate_statuses(
    bundle: LoadedBundle,
    derivation: dict[str, Any],
    decisive: bool,
    report_terminals: set[str],
    campaign_predicates: list[str],
    sequences: dict[str, list[str]],
) -> dict[str, str]:
    ids = bundle.plan["predicate_registry"]["ids"]
    statuses = {predicate: "not_applicable" for predicate in ids}
    if set(campaign_predicates) != {
        "A3-IDLE-EQUALITY",
        "A3-SNAPSHOT-RECONSTRUCTION",
        "A3-RESOURCE-BOUND",
    }:
        raise ValidationError("predicate_revision_contract_mismatch")
    campaign_terminal = derivation["campaign_terminal_predicate_id"]
    for predicate in campaign_predicates:
        statuses[predicate] = "fail" if predicate == campaign_terminal else "pass"
        if predicate == campaign_terminal:
            break
    for name, sequence in sequences.items():
        layer = derivation["layers"][name]
        if not layer["applicable"]:
            continue
        terminal = layer["terminal_predicate_id"]
        if terminal == "A3-REPLICA-DISAGREEMENT":
            replica_terminals = derivation["replica_terminals"][name]
            terminal_positions = [
                sequence.index(predicate)
                for predicate in replica_terminals
                if predicate is not None
            ]
            cutoff = min(terminal_positions, default=sequence.index("A3-REPLICA-DISAGREEMENT"))
            for predicate in sequence[:cutoff]:
                if statuses[predicate] != "fail":
                    statuses[predicate] = "pass"
            statuses[terminal] = "fail"
            continue
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
    expected_records = (
        derivation["record_candidate_enumerations"]
        * bundle.plan["bounds"]["max_record_candidates_per_page"]
    )
    if report["record_candidates_examined"] != expected_records:
        raise ValidationError("record_candidate_count_mismatch")
    if report["candidate_models_examined"] > bundle.plan["bounds"]["max_candidate_models"]:
        raise ValidationError("candidate_model_bound")
    if report["analysis_work_units"] > bundle.plan["bounds"]["max_analysis_work_units"]:
        raise ValidationError("analysis_work_bound")


def validate_bundle(
    bundle: LoadedBundle,
    campaign_predicates: list[str],
    predicate_sequences: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
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
    validate_predicates(bundle, layers, derivation, campaign_predicates, predicate_sequences)
    verify_report_bounds(bundle, derivation)
    if bundle.receipt is None or bundle.receipt["replica_artifact_manifest_sha256"] != bundle.manifest["replica_artifact_manifest_sha256"][2]:
        raise ValidationError("holdout_receipt_replica_link_mismatch")
    return derivation, layers


def _tamper_bundle(bundle: LoadedBundle) -> LoadedBundle:
    return replace(
        bundle,
        manifest=copy.deepcopy(bundle.manifest),
        report=copy.deepcopy(bundle.report),
        frozen=copy.deepcopy(bundle.frozen),
        receipt=copy.deepcopy(bundle.receipt),
    )


def _relink_tamper(bundle: LoadedBundle) -> None:
    if bundle.frozen is None or bundle.report is None or bundle.receipt is None:
        raise ValidationError("tamper_suite_not_executable")
    bundle.frozen_raw = canonical_json_bytes(bundle.frozen)
    digest = sha256_bytes(bundle.frozen_raw)
    bundle.report["derivation_candidate_set_sha256"] = digest
    bundle.receipt["derivation_candidate_set_sha256"] = digest


def _set_tampered_layer(
    bundle: LoadedBundle,
    name: str,
    reason: str,
    predicate: str,
) -> None:
    if bundle.frozen is None or bundle.report is None:
        raise ValidationError("tamper_suite_not_executable")
    frozen = bundle.frozen["layers"][name]
    frozen.update(
        {
            "applicable": True,
            "derivation_survivor_count": 0,
            "model": None,
            "no_outcome_reason": reason,
            "terminal_predicate_id": predicate,
        }
    )
    report_layer = _report_layer(bundle.report, name)
    report_layer.update(
        {
            "status": "no_outcome",
            "derivation_survivor_count": 0,
            "holdout_evaluated": False,
            "no_outcome_reasons": [reason],
            "terminal_predicate_id": predicate,
            "model": None,
        }
    )
    bundle.report["derivation_survivor_counts"][name] = 0


def _execute_tamper_suite(
    bundle: LoadedBundle,
    campaign_predicates: list[str],
    predicate_sequences: dict[str, list[str]],
) -> list[dict[str, Any]]:
    if bundle.frozen is None or bundle.report is None:
        raise ValidationError("tamper_suite_not_executable")
    variants: list[tuple[str, LoadedBundle]] = []

    t1 = _tamper_bundle(bundle)
    global_model = t1.frozen["layers"]["global_map_record"]["model"]
    if global_model is None:
        raise ValidationError("tamper_suite_not_executable", "T1")
    opposite = {
        "set_means_in_use": "set_means_not_in_use",
        "set_means_not_in_use": "set_means_in_use",
    }[global_model["bit_polarity"]]
    global_model["bit_polarity"] = opposite
    _report_layer(t1.report, "global_map_record")["model"]["bit_polarity"] = opposite
    _relink_tamper(t1)
    variants.append(("T1", t1))

    t2 = _tamper_bundle(bundle)
    current_conversion = t2.frozen["layers"]["global_map_conversion_inline"]["terminal_predicate_id"]
    conversion_predicate = "A3-CONVERSION-MULTIPLE" if current_conversion == "A3-CONVERSION-NONE" else "A3-CONVERSION-NONE"
    conversion_reason = {
        "A3-CONVERSION-NONE": "missing_inline_to_indirect_conversion",
        "A3-CONVERSION-MULTIPLE": "multiple_inline_to_indirect_conversions",
    }[conversion_predicate]
    _set_tampered_layer(t2, "global_map_conversion_inline", conversion_reason, conversion_predicate)
    _relink_tamper(t2)
    variants.append(("T2", t2))

    t3 = _tamper_bundle(bundle)
    pages = t3.frozen["qualified_pages"]["global_map"]
    t3.frozen["qualified_pages"]["global_map"] = [page for page in pages if page != 0] if 0 in pages else [0, *pages]
    t3.report["qualified_pages"] = copy.deepcopy(t3.frozen["qualified_pages"])
    t3.report["qualified_page_counts"]["global_map"] = len(t3.frozen["qualified_pages"]["global_map"])
    _relink_tamper(t3)
    variants.append(("T3", t3))

    t4 = _tamper_bundle(bundle)
    current_tdef = t4.frozen["layers"]["tdef_pointer_pair"]["terminal_predicate_id"]
    tdef_predicate = "A3-CHURN-POINTER-NONE" if current_tdef == "A3-GROWTH-POINTER-NONE" else "A3-GROWTH-POINTER-NONE"
    tdef_reason = {
        "A3-GROWTH-POINTER-NONE": "no_growth_only_pointer_candidate",
        "A3-CHURN-POINTER-NONE": "no_delete_reinsert_only_pointer_candidate",
    }[tdef_predicate]
    _set_tampered_layer(t4, "tdef_pointer_pair", tdef_reason, tdef_predicate)
    _relink_tamper(t4)
    variants.append(("T4", t4))

    t5 = _tamper_bundle(bundle)
    holdout = next(
        item for item in t5.report["predicate_results"] if item["predicate_id"] == "A3-HOLDOUT-PREDICTION"
    )
    holdout["status"] = "fail"
    if "A3-HOLDOUT-PREDICTION" not in t5.report["terminal_predicate_ids"]:
        t5.report["terminal_predicate_ids"].append("A3-HOLDOUT-PREDICTION")
    variants.append(("T5", t5))

    results = []
    for tamper_id, variant in variants:
        try:
            validate_bundle(variant, campaign_predicates, predicate_sequences)
        except ValidationError as exc:
            results.append({"id": tamper_id, "rejected": True, "discrepancy_code": exc.code})
        else:
            raise ValidationError("tamper_variant_accepted", tamper_id)
    return results


def verdict(
    bundle: LoadedBundle | None,
    validator_commit: str,
    accepted: bool,
    codes: list[str],
    tamper_results: list[dict[str, Any]],
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
        "tamper_results": tamper_results,
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
    parser.add_argument("--revision", type=Path, default=_repo_revision_path())
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
    tamper_results = _not_executed_tamper_results()
    try:
        loader = BundleLoader(args.bundle_root, args.plan, args.recompute_only)
        campaign_predicates, predicate_sequences = _load_predicate_sequences(
            args.revision,
            sha256_bytes(loader.plan_raw),
            loader.plan["predicate_registry"]["ids"],
        )
        bundle = loader.load()
        if args.recompute_only:
            derivation = recompute_derivation(bundle)
            _write_output(recompute_only_document(bundle, derivation), args.output)
            return 0
        validate_bundle(bundle, campaign_predicates, predicate_sequences)
        tamper_results = _execute_tamper_suite(bundle, campaign_predicates, predicate_sequences)
        result = verdict(bundle, validator_commit, True, [], tamper_results)
        _check_verdict_schema(args.plan, result)
        _write_output(result, args.output)
        return 0
    except ValidationError as exc:
        result = verdict(bundle, validator_commit, False, [exc.code], tamper_results)
        _write_output(result, args.output)
        return 1
    except (KeyError, TypeError, ValueError, IndexError, OSError, OverflowError):
        result = verdict(bundle, validator_commit, False, ["malformed_bundle"], tamper_results)
        _write_output(result, args.output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
