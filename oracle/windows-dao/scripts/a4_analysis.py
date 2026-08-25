#!/usr/bin/env python3
"""Top-level A4 analyze/freeze/open-holdout/report pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from a4_analysis_input import (
    CheckedAnalysisInput,
    HoldoutProvider,
    ReplicaAnalysisInput,
    check_analysis_input,
    close_derivation,
    open_holdout,
    open_holdout_campaign,
)
from a4_analysis_state import FrozenDerivation, freeze_derivation, resume_derivation
from a4_analysis_holdout import HoldoutResults, evaluate_holdout
from a4_frozen_models import load_frozen_models
from a4_layers import DerivationLayers, derive_layers
from a4_model import WorkLedger
from a4_measurements import PredicateMeasurement
from a4_terminal import DerivationTerminal
from a4_spec import (
    EXPERIMENT_ID,
    PLAN,
    PLAN_SHA256,
    PREDICATE_CONTRACTS,
    PREDICATE_IDS,
    REVISION_PLAN_SHA256,
    validate_schema,
)


_HOLDOUT_IDS = {
    "h1": "A4-H1-HOLDOUT-PREDICTION",
    "h2": "A4-H2-HOLDOUT-PREDICTION",
    "h3": "A4-H3-HOLDOUT-PREDICTION",
    "h4_root": "A4-H4-HOLDOUT-ROOT",
    "h4_fields": "A4-H4-HOLDOUT-FIELDS",
}


@dataclass(frozen=True)
class AnalysisResult:
    frozen: FrozenDerivation
    occurrence_evidence: Mapping[str, object]
    report: Mapping[str, Any]


def _holdout_document(
    results: HoldoutResults | None,
) -> dict[str, dict[str, str | None]]:
    output: dict[str, dict[str, str | None]] = {}
    for name, predicate_id in _HOLDOUT_IDS.items():
        value = None if results is None else getattr(results, name)
        output[name] = {
            "status": "not_applicable" if value is None else "pass" if value else "fail",
            "terminal_predicate_id": predicate_id if value is False else None,
        }
    return output


def _predicate_rows(
    layers: Mapping[str, Any],
    holdout: HoldoutResults | None,
    measurements: tuple[PredicateMeasurement, ...],
) -> list[dict[str, Any]]:
    holdout_values = {
        predicate_id: None if holdout is None else getattr(holdout, name)
        for name, predicate_id in _HOLDOUT_IDS.items()
    }
    rows: list[dict[str, Any]] = []
    for predicate_id in PREDICATE_IDS:
        contract = PREDICATE_CONTRACTS[predicate_id]
        if contract["scope"] == "campaign":
            status = "pass"
        elif predicate_id in holdout_values:
            value = holdout_values[predicate_id]
            status = "not_applicable" if value is None else "pass" if value else "fail"
        else:
            events = tuple(
                event for event in measurements if event.predicate_id == predicate_id
            )
            failed = next((event for event in events if not event.passed), None)
            selected = failed if failed is not None else events[-1] if events else None
            status = (
                "not_applicable"
                if selected is None
                else "pass"
                if selected.passed
                else "fail"
            )
        if contract["scope"] == "campaign":
            measured = 0
        elif predicate_id in holdout_values:
            measured = 0 if status == "not_applicable" else 1
        else:
            measured = 0 if selected is None else selected.measured_count
        retains_derivation = (
            contract["scope"] != "campaign"
            and status != "not_applicable"
            and (status == "pass" or predicate_id in holdout_values)
        )
        rows.append({
            "predicate_id": predicate_id,
            "order": contract["order"],
            "scope": contract["scope"],
            "status": status,
            "terminal_predicate_id": predicate_id if status == "fail" else None,
            "predicate_measured_survivor_count": measured,
            "derivation_survivor_count": 1 if retains_derivation else 0,
            "reachability_fixture_id": contract["reachability_fixture_id"],
        })
    return rows


def _report(
    campaign_id: str,
    producer_commit: str,
    frozen: FrozenDerivation,
    holdout: HoldoutResults | None,
    logical_read_bytes: list[int],
    measurements: tuple[PredicateMeasurement, ...],
) -> dict[str, Any]:
    frozen_document = frozen.document
    report: dict[str, Any] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_analysis_report",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": campaign_id,
        "producer_commit": producer_commit,
        "derivation_replicas": [1, 2],
        "qualified_pages": frozen_document["qualified_pages"],
        "work_charges": frozen_document["work_charges"],
        "derivation_candidate_set_sha256": frozen.sha256,
        "holdout_replica": 3,
        "holdout_opened_after_freeze": True,
        "predicate_results": _predicate_rows(
            frozen_document["layers"], holdout, measurements
        ),
        "h4_occurrence_evidence": frozen_document["h4_occurrence_evidence"],
        "layers": frozen_document["layers"],
        "holdout_results": _holdout_document(holdout),
        "transcripts": frozen_document["transcripts"],
        "scientific_outcome": (
            "one_or_more_layers_predict_holdout"
            if holdout is not None and any(value is True for value in (
                holdout.h1,
                holdout.h2,
                holdout.h3,
                holdout.h4_root,
                holdout.h4_fields,
            ))
            else "no_layer_predicts_holdout"
        ),
        "claims": dict(PLAN["claims"]),
        "analyzer_logical_read_bytes_by_replica": logical_read_bytes,
    }
    validate_schema(report, "dao_a4_analysis_report")
    return report


def analyze(
    campaign_id: str,
    producer_commit: str,
    replicas: Mapping[int, ReplicaAnalysisInput],
    holdout_provider: HoldoutProvider | None = None,
) -> AnalysisResult:
    """Derive on 1/2, freeze+verify exact bytes, then first open replica 3."""
    provider = (
        holdout_provider
        if holdout_provider is not None
        else getattr(replicas, "acquire_holdout", None)
    )
    if not callable(provider):
        raise TypeError("A4 analysis requires a lazy holdout provider")
    derivation_input = check_analysis_input(campaign_id, producer_commit, replicas)
    ledger = WorkLedger()
    layers = derive_layers(derivation_input, ledger)
    frozen = freeze_derivation(derivation_input, layers, ledger)
    resumed = resume_derivation(
        frozen.canonical_bytes,
        frozen.sha256,
        frozen.occurrence_evidence_bytes,
    )
    if isinstance(layers, DerivationTerminal):
        measurements = layers.measurements
        occurrence_evidence = MappingProxyType(
            {} if layers.h4_occurrence_evidence is None else dict(layers.h4_occurrence_evidence)
        )
        derivation_reads = [
            derivation_input.views[replica].logical_read_bytes for replica in (1, 2)
        ]
        ticket = close_derivation(
            derivation_input,
            frozen.canonical_bytes,
            frozen.sha256,
            provider,
            frozen.occurrence_evidence_bytes,
        )
        del layers, derivation_input, ledger, resumed
        holdout_campaign = open_holdout_campaign(ticket, frozen.canonical_bytes)
        del ticket
        report = _report(
            campaign_id,
            producer_commit,
            frozen,
            None,
            [*derivation_reads, holdout_campaign.view.logical_read_bytes],
            measurements,
        )
        return AnalysisResult(frozen, occurrence_evidence, MappingProxyType(report))
    frozen_models = load_frozen_models(resumed)
    measurements = layers.measurements
    occurrence_evidence = MappingProxyType(dict(layers.h4_occurrence_evidence))
    derivation_reads = [
        derivation_input.views[replica].logical_read_bytes for replica in (1, 2)
    ]
    ticket = close_derivation(
        derivation_input,
        frozen.canonical_bytes,
        frozen.sha256,
        provider,
        frozen.occurrence_evidence_bytes,
    )
    prior_work_units = ledger.total_work_units
    del layers, derivation_input, resumed, ledger
    holdout_input = open_holdout(ticket, frozen.canonical_bytes)
    del ticket
    holdout_ledger = WorkLedger(prior_work_units)
    holdout = evaluate_holdout(holdout_input, frozen_models, holdout_ledger)
    report = _report(
        campaign_id,
        producer_commit,
        frozen,
        holdout,
        [*derivation_reads, holdout_input.view.logical_read_bytes],
        measurements,
    )
    return AnalysisResult(
        frozen,
        occurrence_evidence,
        MappingProxyType(report),
    )
