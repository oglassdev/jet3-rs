#!/usr/bin/env python3
"""Bounded plan-derived result projections for the independent A3 validator."""

from __future__ import annotations

from typing import Any

from a3_independent_bundle import LoadedBundle, ValidationError


LAYER_NAMES = (
    "global_map_record",
    "global_map_conversion_inline",
    "global_map_extended_base",
    "tdef_pointer_pair",
)


def expected_predicate_statuses(
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
            cutoff = min(
                terminal_positions,
                default=sequence.index("A3-REPLICA-DISAGREEMENT"),
            )
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


def _predicate_status_rows(
    plan: dict[str, Any],
    statuses: dict[str, str],
) -> list[dict[str, str]]:
    predicate_ids = plan["predicate_registry"]["ids"]
    if (
        len(predicate_ids) != 34
        or len(set(predicate_ids)) != 34
        or set(statuses) != set(predicate_ids)
    ):
        raise ValidationError("predicate_revision_contract_mismatch")
    return [
        {"predicate_id": predicate_id, "status": statuses[predicate_id]}
        for predicate_id in predicate_ids
    ]


def independent_projection(
    bundle: LoadedBundle,
    derivation: dict[str, Any],
    layers: dict[str, dict[str, Any]],
    campaign_predicates: list[str],
    predicate_sequences: dict[str, list[str]],
) -> dict[str, Any]:
    decisive = any(
        layer["status"] == "decisive_predicts_holdout"
        for layer in layers.values()
    )
    report_terminals = {
        layer["terminal_predicate_id"]
        for layer in layers.values()
        if layer["terminal_predicate_id"] is not None
    }
    if decisive:
        report_terminals.discard("A3-HOLDOUT-PREDICTION")
    campaign_terminal = derivation["campaign_terminal_predicate_id"]
    if campaign_terminal is not None:
        report_terminals.add(campaign_terminal)
    statuses = expected_predicate_statuses(
        bundle,
        derivation,
        decisive,
        report_terminals,
        campaign_predicates,
        predicate_sequences,
    )
    projected_layers = {
        name: {
            "status": layers[name]["status"],
            "terminal_predicate_id": layers[name]["terminal_predicate_id"],
            "model": layers[name]["model"],
            "derivation_survivor_count": layers[name]["derivation_survivor_count"],
        }
        for name in LAYER_NAMES
    }
    return {
        "layers": projected_layers,
        "polarity_cross_check": derivation["polarity_cross_check"],
        "campaign_terminal_predicate_id": campaign_terminal,
        "predicate_statuses": _predicate_status_rows(bundle.plan, statuses),
    }


def bundle_rejection_projection(
    plan: dict[str, Any],
    campaign_predicates: list[str],
    predicate_sequences: dict[str, list[str]],
    rejection_code: str,
) -> dict[str, Any] | None:
    """Project a campaign stop from preregistered text without bundle bytes."""
    reason_by_rejection = {
        "snapshot_page_blob_missing": "unreconstructable_snapshot",
        "resource_bound_breach": "resource_bound_breach",
    }
    reason = reason_by_rejection.get(rejection_code)
    if reason is None:
        return None
    matching = [
        row["predicate_id"]
        for row in plan["predicate_registry"]["mappings"]
        if row.get("layer") == "campaign" and row.get("reason") == reason
    ]
    if len(matching) != 1 or matching[0] not in campaign_predicates:
        raise ValidationError("predicate_revision_contract_mismatch")
    if set(predicate_sequences) != set(LAYER_NAMES):
        raise ValidationError("predicate_revision_contract_mismatch")
    campaign_terminal = matching[0]
    statuses = {
        predicate_id: "not_applicable"
        for predicate_id in plan["predicate_registry"]["ids"]
    }
    for predicate_id in campaign_predicates:
        statuses[predicate_id] = (
            "fail" if predicate_id == campaign_terminal else "pass"
        )
        if predicate_id == campaign_terminal:
            break
    layers = {
        name: {
            "status": "not_applicable",
            "terminal_predicate_id": None,
            "model": None,
            "derivation_survivor_count": 0,
        }
        for name in LAYER_NAMES
    }
    return {
        "layers": layers,
        "polarity_cross_check": {
            "evaluated_legs": [],
            "first_violating_leg": None,
            "first_violating_page": None,
            "representation_change_stop": None,
        },
        "campaign_terminal_predicate_id": campaign_terminal,
        "predicate_statuses": _predicate_status_rows(plan, statuses),
    }


def pair_projection_document(
    bundle: LoadedBundle,
    derivation: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_independent_pair_projection",
        "source_experiment_id": bundle.manifest.get("experiment_id"),
        "source_bundle_manifest_sha256": bundle.manifest_sha256,
        "derivation_replicas": [1, 2],
        "holdout_opened": any(
            layer["model"] is not None for layer in derivation["layers"].values()
        ),
        "bundle_contract_rejection": None,
        "independent_projection": projection,
    }


def recompute_only_document(
    bundle: LoadedBundle,
    derivation: dict[str, Any],
) -> dict[str, Any]:
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


def rejected_pair_projection_document(
    rejection_code: str,
    projection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_independent_pair_projection",
        "derivation_replicas": [1, 2],
        "holdout_opened": False,
        "bundle_contract_rejection": rejection_code,
        "independent_projection": projection,
    }
