#!/usr/bin/env python3
"""Fail-closed checked contract for DAO-A3-ALLOCATION-MAPS-001.

A3 rule | implementation
--- | ---
Base + R2 + R3 hash binding | :func:`load_checked_plan`, :func:`load_checked_revisions`
R2 order with R3-G03 disagreement reach | :func:`project_predicate_results`
R3-G08 ordered reasons and holdout fields | :func:`validate_analysis_report`
R3-M05 campaign-terminal projection | :func:`project_predicate_results`
R3-G09 report-level holdout exception | :func:`validate_analysis_report`
Frozen-set field equality, including the holdout-only exception | :func:`compare_frozen_to_report`
Canonical frozen-set bytes | :func:`validate_frozen_candidates`
Ordered dry-run parameter and predicate coverage | :func:`validate_dry_run_report`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from protocol_validation import ProtocolSchemaSet, ValidationError

DAO_ROOT = Path(__file__).resolve().parents[1]
A3_ROOT = DAO_ROOT / "experiments" / "a3"
CHECKED_PLAN = A3_ROOT / "a3-allocation-maps.plan.json"
PLAN_SHA256 = "b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1"
CHECKED_R2_PLAN = A3_ROOT / "a3-allocation-maps-r2.plan.json"
R2_PLAN_SHA256 = "3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1"
CHECKED_R3_PLAN = A3_ROOT / "a3-allocation-maps-r3.plan.json"
R3_PLAN_SHA256 = "bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a"
PAIR_REVIEW = A3_ROOT / "design-inputs" / "fable-a3-pair-review.md"
PAIR_REVIEW_SHA256 = "70b9717d3b3387cbd2d4f1ceec3c8deff4f7706563af07eb2c5e77a6c05eab65"
REVISION_PLAN_SHA256 = R3_PLAN_SHA256
EXPERIMENT_ID = "DAO-A3-ALLOCATION-MAPS-001"

_SCHEMA_FILES = {
    "dao_a3_allocation_maps_plan": "plan.schema.json",
    "dao_a3_replica_observation": "replica-observation.schema.json",
    "dao_a3_page_index": "page-index.schema.json",
    "dao_a3_replica_artifact_manifest": "replica-artifact-manifest.schema.json",
    "dao_a3_bundle_manifest": "bundle-manifest.schema.json",
    "dao_a3_analysis_report": "analysis-report.schema.json",
    "dao_a3_analyzer_dry_run_report": "dry-run-report.schema.json",
    "dao_a3_holdout_structure_receipt": "holdout-structure-receipt.schema.json",
    "dao_a3_environment": "environment.schema.json",
    "dao_a3_frozen_derivation_candidates": "derivation-candidates.schema.json",
    "dao_a3_independent_validation_report": "independent-validation-report.schema.json",
}
SCHEMA_SHA256: Mapping[str, str] = MappingProxyType({
    "analysis-report.schema.json": "f15bf39ad703f77fb7749d93214fe43711a9b525376b128f93c898b531db6460",
    "bundle-manifest.schema.json": "9d049c910b4a53da5d3cd3ee71f02c5671fdbb75b94e33587999cf40a91e9727",
    "derivation-candidates.schema.json": "50a9f7a1208969475a89ac3782077cb2bc0e5d3f9635ec51d5a46e8afcacd5b2",
    "dry-run-report.schema.json": "e7b054543529f4b2ac38cda7ae15fac80cf20bd6745f4fcd43cec02eabc9f13d",
    "environment.schema.json": "6fb863f1c224698b466ba5fd5e10d9869a6b313b7480f02045e70c2e8eb49465",
    "holdout-structure-receipt.schema.json": "c2316f9bf84f7722c93160c354f671d7411c0089bf7f52124237b262f43c50fe",
    "independent-validation-report.schema.json": "2ad90d2b6ade15e815ad9819c09ca28d6b7e77ab6064e3a1139a9acf7e4c6d8c",
    "page-index.schema.json": "5e78e1a4b8d95ca1313c5d7e1df78f033f3791c959cb22a5b464aef581ddbdfd",
    "plan.schema.json": "177fdbdda54b0e0d90383578a9bbea4a398cbcbd74424d522997a8f304113f03",
    "replica-artifact-manifest.schema.json": "a60cf012c2ceb8dee55ffd55e4fa21b14759d0d258b0203e14fd583b0b08d197",
    "replica-observation.schema.json": "e0605f67cae502da3b0187c05f9c6ff83b1f7da42a1496af95310dc90d1a2bbf",
})
SCHEMAS = ProtocolSchemaSet(A3_ROOT, _SCHEMA_FILES)


def require_equal(actual: Any, expected: Any, location: str) -> None:
    if actual != expected:
        raise ValidationError(f"{location}: does not match the checked A3 contract")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_bounded_json(path: Path, maximum: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Load one regular, duplicate-key-free, bounded UTF-8 JSON object."""
    try:
        metadata = path.lstat()
        reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(f"{path}: JSON input must be a regular file")
        if metadata.st_size > maximum:
            raise ValidationError(f"{path}: exceeds {maximum}-byte JSON ceiling")
        payload = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect JSON: {exc}") from exc
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{path}: UTF-8 byte-order marks are forbidden")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: JSON root must be an object")
    return value


@dataclass(frozen=True)
class CheckedPlan:
    document: dict[str, Any]
    checkpoint_ids: tuple[str, ...]
    checkpoint_ordinals: Mapping[str, int]
    predicate_ids: tuple[str, ...]
    predicate_rows: Mapping[str, tuple[str, str]]
    reason_predicates: Mapping[str, str]
    bounds: Mapping[str, int]


def _compile_plan(document: dict[str, Any]) -> CheckedPlan:
    SCHEMAS.validate(document)
    require_equal(document["experiment_id"], EXPERIMENT_ID, "$.experiment_id")
    checkpoints = tuple(document["checkpoint_design"]["checkpoint_ids"])
    require_equal(len(checkpoints), document["checkpoint_design"]["count"], "checkpoint count")
    require_equal(len(checkpoints), len(set(checkpoints)), "checkpoint uniqueness")
    registry = document["predicate_registry"]
    ids = tuple(registry["ids"])
    rows = registry["mappings"]
    require_equal([row["predicate_id"] for row in rows], list(ids), "predicate order")
    reasons = [row["reason"] for row in rows]
    require_equal(len(reasons), len(set(reasons)), "predicate reason uniqueness")
    require_equal(set(reasons), set(document["decision_rules"]["no_scientific_outcome_identifiers"]), "reason registry")
    bounds = document["bounds"]
    require_equal(bounds["page_size"], document["page_capture"]["page_size"], "page size")
    require_equal(bounds["max_record_candidates_per_page"], document["record_candidate_procedure"]["per_page_candidate_bound"], "per-page candidates")
    return CheckedPlan(
        document, checkpoints, MappingProxyType({name: i for i, name in enumerate(checkpoints)}),
        ids, MappingProxyType({row["predicate_id"]: (row["reason"], row["layer"]) for row in rows}),
        MappingProxyType({row["reason"]: row["predicate_id"] for row in rows}),
        MappingProxyType(dict(bounds)),
    )


def load_checked_plan(path: Path = CHECKED_PLAN) -> CheckedPlan:
    require_equal(set(SCHEMA_SHA256), set(_SCHEMA_FILES.values()), "schema pin set")
    for name, expected in SCHEMA_SHA256.items():
        require_equal(_sha256(A3_ROOT / name), expected, name)
    SCHEMAS.lint()
    require_equal(_sha256(path), PLAN_SHA256, "preregistered plan sha256")
    return _compile_plan(load_bounded_json(path))


PLAN = load_checked_plan()


def _load_revision(path: Path, sha256: str, revision_id: str) -> dict[str, Any]:
    require_equal(_sha256(path), sha256, f"{revision_id} plan sha256")
    document = load_bounded_json(path)
    require_equal(
        document.get("document_type"),
        "dao_a3_allocation_maps_plan_revision",
        f"{revision_id} document type",
    )
    require_equal(document.get("revision_id"), revision_id, f"{revision_id} id")
    original = document["preregistration"]["original_plan"]
    require_equal(
        original["path"],
        "oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json",
        f"{revision_id} original plan path",
    )
    require_equal(original["sha256"], PLAN_SHA256, f"{revision_id} original plan sha256")
    return document


def load_checked_revisions(
    plan: CheckedPlan = PLAN,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the exact R2 order and R3 operational reconciliation."""
    r2 = _load_revision(CHECKED_R2_PLAN, R2_PLAN_SHA256, "DAO-A3-ALLOCATION-MAPS-001-R2")
    registry_ids = set(plan.predicate_ids)
    reconciliation = r2["predicate_evaluation_sequence_reconciliation"]
    campaign = reconciliation["campaign_evaluated_before_any_layer"]
    require_equal(len(campaign), len(set(campaign)), "R2 campaign sequence uniqueness")
    require_equal(set(campaign) <= registry_ids, True, "R2 campaign predicate registry")
    sequences = reconciliation["per_layer_ordered_predicates"]
    require_equal(
        set(sequences),
        {
            "global_map.record",
            "global_map.conversion_inline",
            "global_map.extended_base",
            "tdef.pointer_pair",
        },
        "R2 layer sequence set",
    )
    for layer, sequence in sequences.items():
        require_equal(len(sequence), len(set(sequence)), f"R2 {layer} sequence uniqueness")
        require_equal(set(sequence) <= registry_ids, True, f"R2 {layer} predicate registry")
    r3 = _load_revision(CHECKED_R3_PLAN, R3_PLAN_SHA256, "DAO-A3-ALLOCATION-MAPS-001-R3")
    prior = r3["preregistration"]["prior_revision"]
    require_equal(prior["revision_id"], r2["revision_id"], "R3 prior revision id")
    require_equal(prior["sha256"], R2_PLAN_SHA256, "R3 prior revision sha256")
    design_input = r3["preregistration"]["design_inputs"]
    require_equal(len(design_input), 1, "R3 design-input count")
    require_equal(design_input[0]["sha256"], PAIR_REVIEW_SHA256, "R3 pair-review sha256")
    require_equal(_sha256(PAIR_REVIEW), PAIR_REVIEW_SHA256, "pair-review file sha256")
    gaps = r3["layer_semantics_reconciliation"]["gaps"]
    require_equal([row["gap_id"] for row in gaps], [f"R3-G{i:02d}" for i in range(1, 11)], "R3 gap ids")
    return r2, r3


R2_PLAN, R3_PLAN = load_checked_revisions()
REVISION_PLAN = R3_PLAN
CHECKPOINT_IDS = PLAN.checkpoint_ids
CHECKPOINT_ORDINALS = PLAN.checkpoint_ordinals
PREDICATE_IDS = PLAN.predicate_ids
PREDICATES = PLAN.predicate_rows
REASON_PREDICATES = PLAN.reason_predicates
BOUNDS = PLAN.bounds
PAGE_SIZE = BOUNDS["page_size"]
POLARITIES = tuple(PLAN.document["hypotheses"]["bit_polarity_candidates"])
POINTER_LAYOUTS = tuple(PLAN.document["hypotheses"]["tdef_pointer_layouts"])
BASE_FORMULAS = tuple(PLAN.document["hypotheses"]["extended_base_candidates"])
LAYER_KEYS = (
    "global_map_record", "global_map_conversion_inline",
    "global_map_extended_base", "tdef_pointer_pair",
)
_REVISION_LAYER_NAMES = MappingProxyType({
    "global_map_record": "global_map.record",
    "global_map_conversion_inline": "global_map.conversion_inline",
    "global_map_extended_base": "global_map.extended_base",
    "tdef_pointer_pair": "tdef.pointer_pair",
})
_INTERNAL_LAYER_NAMES = MappingProxyType({value: key for key, value in _REVISION_LAYER_NAMES.items()})
_SEQUENCE_CONTRACT = R2_PLAN["predicate_evaluation_sequence_reconciliation"]
CAMPAIGN_PREDICATE_SEQUENCE = tuple(_SEQUENCE_CONTRACT["campaign_evaluated_before_any_layer"])
LAYER_PREDICATE_SEQUENCES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    key: tuple(_SEQUENCE_CONTRACT["per_layer_ordered_predicates"][revision_key])
    for key, revision_key in _REVISION_LAYER_NAMES.items()
})
UNREACHABLE_PREDICATE_IDS = frozenset(
    row["predicate_id"]
    for row in R3_PLAN["predicate_reachability_reconciliation"]["unreachable_by_construction"]
)


def _schema(document: dict[str, Any], expected: str) -> None:
    require_equal(SCHEMAS.validate(document), expected, "$.document_type")
    if expected != "dao_a3_allocation_maps_plan":
        require_equal(document["plan_sha256"], PLAN_SHA256, "$.plan_sha256")


def _layer_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "global_map_record": report["submodels"]["global_map"]["record"],
        "global_map_conversion_inline": report["submodels"]["global_map"]["conversion_inline"],
        "global_map_extended_base": report["submodels"]["global_map"]["extended_base"],
        "tdef_pointer_pair": report["submodels"]["tdef"]["pointer_pair"],
    }


def _reached_predicates(
    sequence: tuple[str, ...],
    terminal: str | None,
    *,
    applicable: bool,
) -> set[str]:
    if not applicable:
        return set()
    if terminal in sequence:
        return set(sequence[: sequence.index(terminal) + 1])
    if terminal is None or terminal == "A3-HOLDOUT-PREDICTION":
        return set(sequence)
    return set()


def project_predicate_results(
    layer_results: Mapping[str, Mapping[str, Any]],
    *,
    campaign_terminal: str | None = None,
    reached_by_layer: Mapping[str, set[str] | frozenset[str]] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Project statuses with R2 order and R3 campaign/disagreement stops."""
    terminals = {
        row["terminal_predicate_id"]
        for row in layer_results.values()
        if row["terminal_predicate_id"] is not None
    }
    if campaign_terminal is not None:
        terminals.add(campaign_terminal)
    decisive = any(
        row["status"] == "decisive_predicts_holdout"
        for row in layer_results.values()
    )
    if decisive:
        terminals.discard("A3-HOLDOUT-PREDICTION")

    campaign_reached = _reached_predicates(
        CAMPAIGN_PREDICATE_SEQUENCE,
        campaign_terminal,
        applicable=True,
    )
    if reached_by_layer is None:
        reached = {
            key: _reached_predicates(
                LAYER_PREDICATE_SEQUENCES[key],
                row["terminal_predicate_id"],
                applicable=campaign_terminal is None
                and row["status"] != "not_applicable",
            )
            for key, row in layer_results.items()
        }
    else:
        reached = {key: set(reached_by_layer[key]) for key in LAYER_KEYS}
    reached_in_any_layer = set().union(*reached.values())

    results: list[dict[str, str]] = []
    for predicate_id in PREDICATE_IDS:
        _reason, registered_layer = PREDICATES[predicate_id]
        if predicate_id == "A3-HOLDOUT-PREDICTION" and decisive:
            status = "pass"
        elif predicate_id in terminals:
            status = "fail"
        elif registered_layer == "campaign":
            status = "pass" if predicate_id in campaign_reached else "not_applicable"
        elif registered_layer == "applicable_layer":
            status = "pass" if predicate_id in reached_in_any_layer else "not_applicable"
        else:
            internal_layer = _INTERNAL_LAYER_NAMES[registered_layer]
            status = (
                "pass"
                if predicate_id in reached[internal_layer]
                else "not_applicable"
            )
        results.append({
            "predicate_id": predicate_id,
            "status": status,
            "layer": registered_layer,
        })
    return results, sorted(terminals)


def frozen_json_bytes(document: dict[str, Any]) -> bytes:
    """Encode the frozen document in schema property order with one trailing LF."""
    def record(value: dict[str, Any]) -> dict[str, Any]:
        return {key: value[key] for key in ("page", "start", "end")}

    def model(name: str, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if name == "global_map_record":
            return {
                "record": record(value["record"]), "bit_polarity": value["bit_polarity"],
                "zero_suffix_slack_bytes": value["zero_suffix_slack_bytes"],
            }
        if name == "global_map_conversion_inline":
            keys = (
                "conversion_checkpoint_id", "conversion_ordinal", "indirect_tag",
                "active_slot_count_at_conversion", "active_slot_count_at_h_rel_0904",
                "inline_boundary", "slot_reference_pages",
            )
            return {key: value[key] for key in keys}
        if name == "global_map_extended_base":
            return {"extended_base_formula": value["extended_base_formula"]}
        return {
            "record": record(value["record"]), "pointer_layout": value["pointer_layout"],
            "growth_pointer_offset": value["growth_pointer_offset"],
            "delete_reinsert_pointer_offset": value["delete_reinsert_pointer_offset"],
        }

    def leg(value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else {
            "left_checkpoint_id": value["left_checkpoint_id"],
            "right_checkpoint_id": value["right_checkpoint_id"],
        }

    cross = document["polarity_cross_check"]
    ordered = {
        "protocol_version": document["protocol_version"],
        "document_type": document["document_type"],
        "experiment_id": document["experiment_id"],
        "plan_sha256": document["plan_sha256"],
        "campaign_id": document["campaign_id"],
        "derivation_replicas": document["derivation_replicas"],
        "qualified_pages": {
            "global_map": document["qualified_pages"]["global_map"],
            "tdef": document["qualified_pages"]["tdef"],
        },
        "polarity_cross_check": {
            "evaluated_legs": [leg(value) for value in cross["evaluated_legs"]],
            "representation_change_stop": leg(cross["representation_change_stop"]),
            "first_violating_leg": leg(cross["first_violating_leg"]),
            "first_violating_page": cross["first_violating_page"],
        },
        "layers": {},
    }
    for name in LAYER_KEYS:
        value = document["layers"][name]
        ordered["layers"][name] = {
            "applicable": value["applicable"],
            "derivation_survivor_count": value["derivation_survivor_count"],
            "model": model(name, value["model"]),
            "no_outcome_reason": value["no_outcome_reason"],
            "terminal_predicate_id": value["terminal_predicate_id"],
        }
    return (json.dumps(ordered, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def validate_frozen_candidates(document: dict[str, Any], payload: bytes | None = None) -> dict[str, Any]:
    _schema(document, "dao_a3_frozen_derivation_candidates")
    for name in ("global_map", "tdef"):
        pages = document["qualified_pages"][name]
        require_equal(pages, sorted(set(pages)), f"$.qualified_pages.{name}")
    if payload is not None:
        require_equal(payload, frozen_json_bytes(document), "frozen canonical bytes")
    return document


def compare_frozen_to_report(frozen: dict[str, Any], report: dict[str, Any]) -> None:
    """Enforce the parsed, field-for-field freeze rule; a matching hash is insufficient."""
    require_equal(report["qualified_pages"], frozen["qualified_pages"], "frozen qualified pages")
    require_equal(report["polarity_cross_check"], frozen["polarity_cross_check"], "frozen cross-check")
    rows = _layer_rows(report)
    for name in LAYER_KEYS:
        retained = frozen["layers"][name]
        current = rows[name]
        require_equal(current["model"], retained["model"], f"{name}.model")
        require_equal(current["derivation_survivor_count"], retained["derivation_survivor_count"], f"{name}.count")
        require_equal(retained["applicable"], current["status"] != "not_applicable", f"{name}.applicable")
        reasons = current["no_outcome_reasons"]
        terminal = current["terminal_predicate_id"]
        if reasons == ["holdout_prediction_failure"] and terminal == "A3-HOLDOUT-PREDICTION":
            reasons, terminal = [], None
        require_equal(reasons, [] if retained["no_outcome_reason"] is None else [retained["no_outcome_reason"]], f"{name}.reason")
        require_equal(terminal, retained["terminal_predicate_id"], f"{name}.terminal")


def validate_predicate_reporting(
    results: list[dict[str, str]], terminal_ids: list[str], *,
    any_decisive: bool, any_holdout_failure: bool,
) -> None:
    """Validate the registry order, literal layers, and report holdout exception."""
    require_equal([row["predicate_id"] for row in results], list(PREDICATE_IDS), "predicate_results order")
    result_map = {row["predicate_id"]: row for row in results}
    require_equal(len(result_map), len(PREDICATE_IDS), "predicate_results uniqueness")
    for predicate_id, (_reason, layer) in PREDICATES.items():
        require_equal(result_map[predicate_id]["layer"], layer, f"{predicate_id}.layer")
    terminals = set(terminal_ids)
    require_equal(len(terminals), len(terminal_ids), "terminal predicate uniqueness")
    for predicate_id in PREDICATE_IDS:
        status = result_map[predicate_id]["status"]
        if predicate_id in terminals:
            require_equal(status, "fail", f"{predicate_id}.status")
        elif status == "fail":
            raise ValidationError(f"{predicate_id}: nonterminal predicate cannot fail")
    if any_decisive:
        expected_holdout_status = "pass"
    elif any_holdout_failure:
        expected_holdout_status = "fail"
    else:
        expected_holdout_status = "not_applicable"
    holdout_status = result_map["A3-HOLDOUT-PREDICTION"]["status"]
    require_equal(
        holdout_status,
        expected_holdout_status,
        "holdout reporting exception",
    )


def validate_analysis_report(
    document: dict[str, Any],
    frozen: dict[str, Any] | None = None,
    *,
    reached_by_layer: Mapping[str, set[str] | frozenset[str]] | None = None,
) -> dict[str, Any]:
    _schema(document, "dao_a3_analysis_report")
    results = document["predicate_results"]
    layers = _layer_rows(document)
    decisive = {name for name, row in layers.items() if row["status"] == "decisive_predicts_holdout"}
    holdout_fail = any(row["terminal_predicate_id"] == "A3-HOLDOUT-PREDICTION" for row in layers.values())
    report_terminals = set(document["terminal_predicate_ids"])
    layer_terminals = {
        row["terminal_predicate_id"] for row in layers.values()
        if row["terminal_predicate_id"] is not None
    }
    campaign_terminals = report_terminals & set(CAMPAIGN_PREDICATE_SEQUENCE)
    require_equal(len(campaign_terminals) <= 1, True, "campaign terminal count")
    campaign_terminal = next(iter(campaign_terminals), None)
    expected_terminals = set(layer_terminals)
    if campaign_terminal is not None:
        expected_terminals.add(campaign_terminal)
    if decisive:
        expected_terminals.discard("A3-HOLDOUT-PREDICTION")
    require_equal(report_terminals, expected_terminals, "report terminal ids")
    validate_predicate_reporting(
        results, document["terminal_predicate_ids"], any_decisive=bool(decisive),
        any_holdout_failure=holdout_fail,
    )
    expected_results, _ = project_predicate_results(
        layers,
        campaign_terminal=campaign_terminal,
        reached_by_layer=reached_by_layer,
    )
    if reached_by_layer is not None or not any(
        row["terminal_predicate_id"] == "A3-REPLICA-DISAGREEMENT"
        for row in layers.values()
    ):
        require_equal(results, expected_results, "R2/R3 predicate status projection")
    require_equal(document["scientific_outcome"], "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout", "scientific outcome")
    require_equal(
        document["qualified_page_counts"],
        {name: len(document["qualified_pages"][name]) for name in ("global_map", "tdef")},
        "qualified page counts",
    )
    frozen_model_exists = any(row["model"] is not None for row in layers.values())
    require_equal(document["holdout_evaluated"], frozen_model_exists, "R3-G08 holdout evaluated")
    require_equal(
        document["holdout_opened_after_freeze"],
        frozen_model_exists,
        "R3-G08 holdout opened",
    )
    ordered_reasons: list[str] = []
    if campaign_terminal is not None:
        ordered_reasons.append(PREDICATES[campaign_terminal][0])
    for name in LAYER_KEYS:
        require_equal(
            len(layers[name]["no_outcome_reasons"]) <= 1,
            True,
            f"{name}.reason count",
        )
        ordered_reasons.extend(layers[name]["no_outcome_reasons"])
    ordered_reasons = list(dict.fromkeys(ordered_reasons))
    require_equal(document["no_outcome_reasons"], ordered_reasons, "R3-G08 reason order")
    if campaign_terminal is not None:
        for name, row in layers.items():
            if row["status"] != "not_applicable":
                raise ValidationError(f"{name}: campaign terminal must preempt every layer")
    for name, row in layers.items():
        require_equal(row["derivation_survivor_count"], document["derivation_survivor_counts"][name], f"{name}.count")
        require_equal(
            row["holdout_evaluated"],
            row["model"] is not None,
            f"{name}.R3-G08 holdout evaluated",
        )
        if row["status"] == "decisive_predicts_holdout":
            if row["model"] is None or row["derivation_survivor_count"] != 1 or not row["holdout_evaluated"] or row["no_outcome_reasons"]:
                raise ValidationError(f"{name}: malformed decisive layer")
        elif row["status"] == "not_applicable" and any((row["model"], row["derivation_survivor_count"], row["holdout_evaluated"], row["no_outcome_reasons"], row["terminal_predicate_id"])):
            raise ValidationError(f"{name}: malformed not-applicable layer")
    if frozen is not None:
        compare_frozen_to_report(frozen, document)
    return document


def validate_dry_run_report(document: dict[str, Any]) -> dict[str, Any]:
    _schema(document, "dao_a3_analyzer_dry_run_report")
    if document["result"] != "pass":
        return document
    if document["holdout_opened"]:
        raise ValidationError("dry run must never open a holdout")
    if document["source_kind"] == "a3_schedule_synthetic":
        free = PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
        coverage = document["parameter_coverage"]
        require_equal(coverage["conversion_ordinals"], list(range(1, len(CHECKPOINT_IDS))), "conversion coverage")
        require_equal(coverage["conversion_never"], True, "conversion never")
        for report_key, plan_key in (("slot_activation_counts", "slot_activation_at_conversion"), ("bit_polarities", "bit_polarity"), ("anchor_fill_states", "anchor_fill_state"), ("record_end_uniform_slack_bytes", "record_end_uniform_slack_bytes")):
            require_equal(coverage[report_key], free[plan_key], report_key)
        excluded_reasons = {PREDICATES[predicate_id][0] for predicate_id in UNREACHABLE_PREDICATE_IDS}
        required_cases = set(
            PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]["required_cases"]
        ) - excluded_reasons
        require_equal(set(document["predicted_terminal_states"]), required_cases, "R3 required cases")
        require_equal(
            set(document["terminal_predicate_ids"]),
            set(PREDICATE_IDS) - UNREACHABLE_PREDICATE_IDS,
            "R3 reachable predicates",
        )
        if document["source_identity"]["generator_sha256"] is None:
            raise ValidationError("synthetic dry run requires generator hash")
    else:
        require_equal(document["source_identity"]["generator_sha256"], None, "replay generator hash")
    return document


def validate_document(document: dict[str, Any]) -> Any:
    kind = document.get("document_type")
    if kind == "dao_a3_allocation_maps_plan":
        require_equal(document, PLAN.document, "checked plan")
        return document
    if kind == "dao_a3_analysis_report":
        return validate_analysis_report(document)
    if kind == "dao_a3_analyzer_dry_run_report":
        return validate_dry_run_report(document)
    if kind == "dao_a3_frozen_derivation_candidates":
        return validate_frozen_candidates(document)
    _schema(document, str(kind))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=CHECKED_PLAN)
    arguments = parser.parse_args(argv)
    try:
        validate_document(load_bounded_json(arguments.path, BOUNDS["max_json_bytes"]))
    except ValidationError as exc:
        print(f"A3 validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"A3 validation passed: {arguments.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
