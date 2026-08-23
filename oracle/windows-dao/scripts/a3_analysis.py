#!/usr/bin/env python3
"""Layered, freeze-before-holdout analyzer for DAO-A3-ALLOCATION-MAPS-001.

A3 rule | implementation
--- | ---
R3-G03 per-replica outcomes and agreement | :func:`_combine_replicas`, :func:`derive_layers`
R3-G03 union-qualified pages | :func:`_qualified_union`
R3-G05 ordered global terminals | :func:`_global_replica`
R3-G08 report ordering/open fields | :func:`build_analysis`
R3-G09 frozen-model-only holdout | :func:`_evaluate_holdout`
R3-G10/M05/M06 abort isolation | :func:`derive_layers`, :func:`build_analysis`
R2+R3 predicate projection | :func:`predicate_results`
Field-for-field frozen/report agreement | :func:`build_analysis`
Freeze/recompute/resume phase driver | :func:`main`
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from protocol_validation import ValidationError, canonical_json_bytes
from a3_analysis_input import (
    BundleReplicaSource, LoadedReplicaSource, ReplicaInput, ReplicaSource,
    sha256_file,
)
from a3_analysis_state import FreezeResult, LayerDraft, load_freeze_state
from a3_layers import (
    BaseModel, ConversionModel, CrossCheckTranscript, TdefModel, derive_base,
    derive_conversion, derive_tdef_candidates, polarity_cross_check,
    predicts_base, predicts_conversion, predicts_global, predicts_tdef,
)
from a3_model import (
    CHECKPOINT_IDS, MAX_QUALIFIED_PAGES, PAGE_SIZE, Abort, GlobalRecordModel,
    View, WorkCounter, candidate_page_space, global_start_candidates,
    qualify_global_pages, qualify_tdef_pages,
)
from a3_spec import (
    BOUNDS, EXPERIMENT_ID, LAYER_KEYS, LAYER_PREDICATE_SEQUENCES, PLAN,
    PLAN_SHA256, REVISION_PLAN_SHA256,
    compare_frozen_to_report, frozen_json_bytes, load_bounded_json,
    project_predicate_results, validate_analysis_report, validate_document,
    validate_frozen_candidates,
)

MAX_JSON_BYTES = BOUNDS["max_json_bytes"]
CLAIMS = {key: PLAN.document["claims"][key] for key in (
    "descriptive_provider_observation_only", "general_tdef_catalog_row_index_or_lval_layout",
    "unobserved_slot_or_base_behavior", "compaction_encryption_or_version_behavior",
    "rust_correctness", "dao_compatibility_or_support",
)}
@dataclass
class CountingReplicaSource:
    source: ReplicaSource
    open_count: int = 0

    def open(self) -> ReplicaInput:
        self.open_count += 1
        return self.source.open()


@dataclass(frozen=True)
class ReplicaLayer:
    model: GlobalRecordModel | ConversionModel | BaseModel | TdefModel | None
    survivor_count: int
    abort: Abort | None
    transcript: CrossCheckTranscript = CrossCheckTranscript()


def _not_applicable() -> LayerDraft:
    return LayerDraft(None, 0, None, False, frozenset())


def _validate_inputs(replicas: list[ReplicaInput]) -> None:
    if [row.replica for row in replicas] != list(range(1, len(replicas) + 1)):
        raise ValidationError("A3 replica ordinals are not consecutive")
    for attribute in ("campaign_id", "producer_commit", "provider_sha256"):
        if len({getattr(row, attribute) for row in replicas}) != 1:
            raise ValidationError(f"A3 replica {attribute} binding mismatch")


def _is_campaign_abort(abort: Abort) -> bool:
    return abort.predicate_id in {
        "A3-IDLE-EQUALITY",
        "A3-SNAPSHOT-RECONSTRUCTION",
        "A3-RESOURCE-BOUND",
    }


def _reached(layer: str, terminal: str | None) -> frozenset[str]:
    sequence = LAYER_PREDICATE_SEQUENCES[layer]
    if terminal is None:
        return frozenset(sequence)
    return frozenset(sequence[: sequence.index(terminal) + 1])


def _replica_call(
    function: Callable[[], GlobalRecordModel | ConversionModel | BaseModel | TdefModel],
    *,
    transcript: CrossCheckTranscript = CrossCheckTranscript(),
) -> ReplicaLayer:
    try:
        return ReplicaLayer(function(), 1, None, transcript)
    except Abort as exc:
        if _is_campaign_abort(exc):
            raise
        return ReplicaLayer(None, exc.survivor_count, exc, transcript)


def _same_model(
    left: GlobalRecordModel | ConversionModel | BaseModel | TdefModel,
    right: GlobalRecordModel | ConversionModel | BaseModel | TdefModel,
) -> GlobalRecordModel | ConversionModel | BaseModel | TdefModel | None:
    if isinstance(left, GlobalRecordModel) and isinstance(right, GlobalRecordModel):
        if left.record != right.record or left.bit_polarity != right.bit_polarity:
            return None
        return GlobalRecordModel(
            left.record,
            left.bit_polarity,
            min(left.zero_suffix_slack_bytes, right.zero_suffix_slack_bytes),
        )
    return left if left == right else None


def _combine_replicas(
    layer: str,
    outcomes: tuple[ReplicaLayer, ReplicaLayer],
    *,
    compare_transcripts: bool = False,
) -> LayerDraft:
    first, second = outcomes
    terminals = [
        outcome.abort.predicate_id
        for outcome in outcomes
        if outcome.abort is not None
    ]
    if len(terminals) == 2 and terminals[0] == terminals[1]:
        terminal = terminals[0]
        return LayerDraft(
            None,
            first.survivor_count,
            Abort(terminal, first.survivor_count),
            True,
            _reached(layer, terminal),
        )
    if first.model is not None and second.model is not None:
        model = _same_model(first.model, second.model)
        transcripts_agree = not compare_transcripts or first.transcript == second.transcript
        if model is not None and transcripts_agree:
            return LayerDraft(model, 1, None, True, _reached(layer, None))

    sequence = LAYER_PREDICATE_SEQUENCES[layer]
    cutoff = min(
        (sequence.index(terminal) for terminal in terminals),
        default=sequence.index("A3-REPLICA-DISAGREEMENT"),
    )
    reached = frozenset((*sequence[:cutoff], "A3-REPLICA-DISAGREEMENT"))
    return LayerDraft(
        None,
        first.survivor_count,
        Abort("A3-REPLICA-DISAGREEMENT", first.survivor_count),
        True,
        reached,
    )


def _global_replica(view: View, pages: tuple[int, ...]) -> ReplicaLayer:
    if not pages:
        return ReplicaLayer(None, 0, Abort("A3-GLOBAL-PAGE-NONE"))
    rows = {
        page: global_start_candidates(view, page, enumerate_candidates=False)
        for page in pages
    }
    nonempty = {page: models for page, (models, _evidence) in rows.items() if models}
    if not nonempty:
        evidence = {key: any(row[1][key] for row in rows.values()) for key in ("anchor", "relation", "suffix")}
        if not evidence["anchor"]:
            predicate = "A3-GLOBAL-RECORD-NONE"
        elif not evidence["relation"]:
            predicate = "A3-D-SET-RELATION"
        else:
            predicate = "A3-GLOBAL-RECORD-END"
        return ReplicaLayer(None, 0, Abort(predicate))
    models = [model for values in nonempty.values() for model in values]
    polarities = {model.bit_polarity for model in models}
    if len(polarities) > 1:
        return ReplicaLayer(None, len(models), Abort("A3-POLARITY-MULTIPLE"))
    if len(nonempty) > 1:
        return ReplicaLayer(None, len(models), Abort("A3-GLOBAL-PAGE-MULTIPLE"))
    if len(models) > 1:
        return ReplicaLayer(None, len(models), Abort("A3-GLOBAL-RECORD-MULTIPLE"))
    return ReplicaLayer(models[0], 1, None)


def _conversion_replica(view: View, model: GlobalRecordModel) -> ReplicaLayer:
    try:
        transcript = polarity_cross_check(view, model)
    except Abort as exc:
        if _is_campaign_abort(exc):
            raise
        return ReplicaLayer(None, exc.survivor_count, exc)
    if transcript.first_violating_leg is not None:
        return ReplicaLayer(None, 0, Abort("A3-POLARITY-CROSSCHECK"), transcript)
    return _replica_call(
        lambda: derive_conversion(view, model, transcript)[0], transcript=transcript
    )


def _qualified_union(per_replica: tuple[tuple[int, ...], tuple[int, ...]]) -> tuple[int, ...]:
    pages = tuple(sorted(set(per_replica[0]) | set(per_replica[1])))
    if len(pages) > MAX_QUALIFIED_PAGES:
        raise Abort("A3-RESOURCE-BOUND")
    return pages


def _tdef_replica(
    view: View,
    pages: tuple[int, ...],
    churn_precondition_met: bool,
) -> ReplicaLayer:
    if not pages:
        return ReplicaLayer(None, 0, Abort("A3-TDEF-PAGE-NONE"))
    return _replica_call(
        lambda: derive_tdef_candidates(
            view,
            pages,
            churn_precondition_met,
            enumerate_candidates=False,
        )[0]
    )


def derive_layers(derivation: list[ReplicaInput], work: WorkCounter) -> tuple[dict[str, LayerDraft], tuple[int, ...], tuple[int, ...], CrossCheckTranscript]:
    _validate_inputs(derivation)
    views = (View(derivation[0].data, work), View(derivation[1].data, work))
    empty_transcript = CrossCheckTranscript()
    if not all(view.idle_pairs_identical() for view in views):
        raise Abort("A3-IDLE-EQUALITY")
    pages = candidate_page_space(views)
    global_by_replica = tuple(qualify_global_pages(view, pages) for view in views)
    global_pages = _qualified_union((global_by_replica[0], global_by_replica[1]))
    work.enumerate_pages(len(global_pages))
    global_outcomes = tuple(
        _global_replica(view, global_by_replica[index])
        for index, view in enumerate(views)
    )
    global_draft = _combine_replicas(
        "global_map_record", (global_outcomes[0], global_outcomes[1])
    )
    drafts: dict[str, LayerDraft] = {"global_map_record": global_draft}
    transcript = empty_transcript
    if not isinstance(global_draft.model, GlobalRecordModel):
        drafts["global_map_conversion_inline"] = _not_applicable()
        drafts["global_map_extended_base"] = _not_applicable()
    else:
        conversion_outcomes = tuple(
            _conversion_replica(view, global_draft.model) for view in views
        )
        transcript = conversion_outcomes[0].transcript
        conversion = _combine_replicas(
            "global_map_conversion_inline",
            (conversion_outcomes[0], conversion_outcomes[1]),
            compare_transcripts=True,
        )
        drafts["global_map_conversion_inline"] = conversion
        if isinstance(conversion.model, ConversionModel):
            base_outcomes = tuple(
                _replica_call(
                    lambda view=view: derive_base(
                        view, global_draft.model, conversion.model
                    )
                )
                for view in views
            )
            drafts["global_map_extended_base"] = _combine_replicas(
                "global_map_extended_base", (base_outcomes[0], base_outcomes[1])
            )
        else:
            drafts["global_map_extended_base"] = _not_applicable()
    tdef_by_replica = tuple(qualify_tdef_pages(view, pages) for view in views)
    tdef_pages = _qualified_union((tdef_by_replica[0], tdef_by_replica[1]))
    if any(row.churn_precondition_met for row in derivation):
        work.enumerate_pages(len(tdef_pages), prefix_arrays_per_page=1)
    tdef_outcomes = tuple(
        _tdef_replica(
            view,
            tdef_by_replica[index],
            derivation[index].churn_precondition_met,
        )
        for index, view in enumerate(views)
    )
    drafts["tdef_pointer_pair"] = _combine_replicas(
        "tdef_pointer_pair", (tdef_outcomes[0], tdef_outcomes[1])
    )
    return drafts, global_pages, tdef_pages, transcript


def candidate_document(campaign_id: str, global_pages: tuple[int, ...], tdef_pages: tuple[int, ...], transcript: CrossCheckTranscript, drafts: dict[str, LayerDraft]) -> dict[str, Any]:
    def layer(draft: LayerDraft) -> dict[str, Any]:
        return {
            "applicable": draft.applicable, "derivation_survivor_count": draft.survivor_count,
            "model": None if draft.model is None else draft.model.document(),
            "no_outcome_reason": None if draft.abort is None else draft.abort.reason,
            "terminal_predicate_id": None if draft.abort is None else draft.abort.predicate_id,
        }
    return {
        "protocol_version": "1.0.0", "document_type": "dao_a3_frozen_derivation_candidates",
        "experiment_id": EXPERIMENT_ID, "plan_sha256": PLAN_SHA256, "campaign_id": campaign_id,
        "derivation_replicas": [1, 2],
        "qualified_pages": {"global_map": list(global_pages), "tdef": list(tdef_pages)},
        "polarity_cross_check": transcript.document(),
        "layers": {key: layer(drafts[key]) for key in LAYER_KEYS},
    }


def write_frozen(path: Path, document: dict[str, Any]) -> str:
    validate_frozen_candidates(document)
    payload = frozen_json_bytes(document)
    if len(payload) > MAX_JSON_BYTES:
        raise ValidationError("A3 frozen candidate set exceeds JSON ceiling")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return hashlib.sha256(payload).hexdigest()


def _layer_result(draft: LayerDraft, holdout_match: bool | None) -> dict[str, Any]:
    if not draft.applicable:
        return {"status": "not_applicable", "derivation_survivor_count": 0, "holdout_evaluated": False, "no_outcome_reasons": [], "terminal_predicate_id": None, "model": None}
    if draft.abort:
        return {"status": "no_outcome", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": False, "no_outcome_reasons": [draft.abort.reason], "terminal_predicate_id": draft.abort.predicate_id, "model": None}
    if holdout_match:
        return {"status": "decisive_predicts_holdout", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": True, "no_outcome_reasons": [], "terminal_predicate_id": None, "model": draft.model.document() if draft.model else None}
    return {"status": "no_outcome", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": True, "no_outcome_reasons": ["holdout_prediction_failure"], "terminal_predicate_id": "A3-HOLDOUT-PREDICTION", "model": draft.model.document() if draft.model else None}


def predicate_results(
    layer_results: dict[str, dict[str, Any]],
    drafts: dict[str, LayerDraft],
    campaign_abort: Abort | None,
) -> tuple[list[dict[str, str]], list[str]]:
    return project_predicate_results(
        layer_results,
        campaign_terminal=None if campaign_abort is None else campaign_abort.predicate_id,
        reached_by_layer={key: drafts[key].reached for key in LAYER_KEYS},
    )


def _evaluate_holdout(
    source: ReplicaSource,
    expected_bindings: tuple[str, str, str],
    drafts: dict[str, LayerDraft],
    work: WorkCounter,
) -> dict[str, bool | None]:
    matches: dict[str, bool | None] = {key: None for key in LAYER_KEYS}
    model_keys = [key for key in LAYER_KEYS if drafts[key].model is not None]
    if not model_keys:
        return matches
    try:
        holdout_input = source.open()
        if holdout_input.replica != 3 or (
            holdout_input.campaign_id,
            holdout_input.producer_commit,
            holdout_input.provider_sha256,
        ) != expected_bindings:
            raise ValidationError("A3 holdout replica binding mismatch")
        holdout = View(holdout_input.data, work)
    except (Abort, OSError, ValidationError):
        return {key: (False if key in model_keys else None) for key in LAYER_KEYS}

    global_draft = drafts["global_map_record"]
    conversion_draft = drafts["global_map_conversion_inline"]
    checks: dict[str, Callable[[], bool]] = {}
    if isinstance(global_draft.model, GlobalRecordModel):
        checks["global_map_record"] = lambda: predicts_global(holdout, global_draft.model)
    if (
        isinstance(global_draft.model, GlobalRecordModel)
        and isinstance(conversion_draft.model, ConversionModel)
    ):
        checks["global_map_conversion_inline"] = lambda: predicts_conversion(
            holdout, global_draft.model, conversion_draft.model
        )
        base = drafts["global_map_extended_base"]
        if isinstance(base.model, BaseModel):
            checks["global_map_extended_base"] = lambda: predicts_base(
                holdout, global_draft.model, conversion_draft.model, base.model
            )
    tdef = drafts["tdef_pointer_pair"]
    if isinstance(tdef.model, TdefModel):
        checks["tdef_pointer_pair"] = lambda: predicts_tdef(
            holdout, tdef.model, holdout_input.churn_precondition_met
        )
    for key, check in checks.items():
        try:
            matches[key] = check()
        except (Abort, IndexError, OSError, ValidationError):
            matches[key] = False
    return matches


def _ordered_no_outcome_reasons(
    layers: dict[str, dict[str, Any]],
    campaign_abort: Abort | None,
) -> list[str]:
    reasons = [] if campaign_abort is None else [campaign_abort.reason]
    for key in LAYER_KEYS:
        reasons.extend(layers[key]["no_outcome_reasons"])
    return list(dict.fromkeys(reasons))


def recompute_only(derivation: list[ReplicaInput]) -> dict[str, Any]:
    """Recompute derivation replicas only; this function has no holdout source."""
    if len(derivation) != 2:
        raise ValidationError("A3 recompute-only requires replicas 1 and 2")
    work, campaign_abort = WorkCounter(), None
    try:
        drafts, global_pages, tdef_pages, transcript = derive_layers(derivation, work)
    except Abort as exc:
        if not _is_campaign_abort(exc):
            raise ValidationError(f"layer abort escaped isolation: {exc}") from exc
        campaign_abort = exc
        drafts = {key: _not_applicable() for key in LAYER_KEYS}
        global_pages, tdef_pages, transcript = (), (), CrossCheckTranscript()

    def outcome(draft: LayerDraft) -> dict[str, Any]:
        if not draft.applicable:
            status = "not_applicable"
        elif draft.abort is not None:
            status = "no_outcome"
        else:
            status = "derivation_model"
        return {
            "status": status,
            "terminal_predicate_id": None if draft.abort is None else draft.abort.predicate_id,
            "model": None if draft.model is None else draft.model.document(),
        }

    return {
        "campaign_terminal_predicate_id": (
            None if campaign_abort is None else campaign_abort.predicate_id
        ),
        "qualified_pages": {
            "global_map": list(global_pages),
            "tdef": list(tdef_pages),
        },
        "polarity_cross_check": transcript.document(),
        "layers": {key: outcome(drafts[key]) for key in LAYER_KEYS},
        "record_candidates_examined": work.record_candidates,
        "candidate_models_examined": work.candidate_models,
        "analysis_work_units": work.value,
    }


def freeze_analysis(sources: list[ReplicaSource], candidate_output: Path) -> FreezeResult:
    if len(sources) != 2:
        raise ValidationError("A3 freeze requires exactly two derivation replica sources")
    derivation = [source.open() for source in sources]
    work, campaign_abort = WorkCounter(), None
    try:
        drafts, global_pages, tdef_pages, transcript = derive_layers(derivation, work)
    except Abort as exc:
        if not _is_campaign_abort(exc):
            raise ValidationError(f"layer abort escaped isolation: {exc}") from exc
        campaign_abort = exc
        drafts = {key: _not_applicable() for key in LAYER_KEYS}
        global_pages, tdef_pages, transcript = (), (), CrossCheckTranscript()
    frozen = candidate_document(derivation[0].campaign_id, global_pages, tdef_pages, transcript, drafts)
    frozen_sha = write_frozen(candidate_output, frozen)
    return FreezeResult(
        derivation, drafts, global_pages, tdef_pages, transcript, frozen,
        frozen_sha, work, campaign_abort,
    )


def _freeze_state(result: FreezeResult, holdout_artifact: Path) -> dict[str, Any]:
    if holdout_artifact.exists() or holdout_artifact.is_symlink():
        raise ValidationError("replica-3 artifact existed before freeze phase completed")
    first = result.derivation[0]
    return {
        "document_type": "dao_a3_internal_freeze_phase",
        "campaign_id": first.campaign_id,
        "producer_commit": first.producer_commit,
        "provider_sha256": first.provider_sha256,
        "derivation_candidate_set_sha256": result.frozen_sha256,
        "freeze_phase_completed": True,
        "replica_3_artifact_existed_before_freeze_phase_completed": False,
        "analyzer_replica_3_opens_before_receipt": 0,
        "campaign_terminal_predicate_id": (
            None if result.campaign_abort is None else result.campaign_abort.predicate_id
        ),
        "reached_by_layer": {
            key: [predicate for predicate in LAYER_PREDICATE_SEQUENCES[key]
                  if predicate in result.drafts[key].reached]
            for key in LAYER_KEYS
        },
        "work": {
            "value": result.work.value,
            "record_candidates": result.work.record_candidates,
            "candidate_models": result.work.candidate_models,
            "page_digests": sorted(result.work.page_digests),
        },
    }


def write_freeze_state(path: Path, result: FreezeResult, holdout_artifact: Path) -> dict[str, Any]:
    state = _freeze_state(result, holdout_artifact)
    payload = canonical_json_bytes(state)
    if len(payload) > MAX_JSON_BYTES:
        raise ValidationError("A3 freeze state exceeds JSON ceiling")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return state


_load_freeze_state = load_freeze_state


def resume_analysis(
    frozen_result: FreezeResult,
    holdout_source: ReplicaSource,
    candidate_output: Path,
    receipt: dict[str, Any] | None,
    expected_bindings: tuple[str, str, str],
    analyzer_holdout_opens_before_receipt: int = 0,
) -> dict[str, Any]:
    drafts, work, campaign_abort = (
        frozen_result.drafts, frozen_result.work, frozen_result.campaign_abort
    )
    frozen, frozen_sha = frozen_result.frozen, frozen_result.frozen_sha256
    if receipt is not None:
        validate_document(receipt)
        expected_receipt = {
            "campaign_id": expected_bindings[0],
            "producer_commit": expected_bindings[1],
            "derivation_candidate_set_sha256": frozen_sha,
        }
        if any(receipt.get(key) != value for key, value in expected_receipt.items()):
            raise ValidationError("A3 holdout receipt binding mismatch")
    holdout_validated_after_freeze = _holdout_validated_after_freeze(
        candidate_output, frozen_sha, receipt, analyzer_holdout_opens_before_receipt
    )
    if not holdout_validated_after_freeze:
        raise ValidationError("holdout structural validation is not bound to the frozen candidate set")
    holdout_opened = campaign_abort is None and any(
        draft.model is not None for draft in drafts.values()
    )
    matches = (
        _evaluate_holdout(holdout_source, expected_bindings, drafts, work)
        if holdout_opened
        else {key: None for key in LAYER_KEYS}
    )
    layers = {key: _layer_result(drafts[key], matches[key]) for key in LAYER_KEYS}
    predicates, terminal_ids = predicate_results(layers, drafts, campaign_abort)
    decisive = any(row["status"] == "decisive_predicts_holdout" for row in layers.values())
    report = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_analysis_report", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": expected_bindings[0], "producer_commit": expected_bindings[1],
        "derivation_replicas": [1, 2], "holdout_replica": 3, "input_checkpoint_count": len(CHECKPOINT_IDS) * 3,
        "qualified_page_counts": {"global_map": min(len(frozen_result.global_pages), MAX_QUALIFIED_PAGES), "tdef": min(len(frozen_result.tdef_pages), MAX_QUALIFIED_PAGES)},
        "qualified_pages": {"global_map": list(frozen_result.global_pages), "tdef": list(frozen_result.tdef_pages)},
        "record_candidates_examined": work.record_candidates, "candidate_models_examined": work.candidate_models,
        "derivation_survivor_counts": {key: drafts[key].survivor_count for key in LAYER_KEYS},
        "derivation_candidate_set_sha256": frozen_sha, "polarity_cross_check": frozen_result.transcript.document(),
        "analysis_work_units": work.value, "holdout_structurally_validated_after_freeze": holdout_validated_after_freeze,
        "holdout_opened_after_freeze": holdout_opened,
        "holdout_evaluated": any(row["holdout_evaluated"] for row in layers.values()),
        "predicate_results": predicates, "terminal_predicate_ids": terminal_ids,
        "scientific_outcome": "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout",
        "no_outcome_reasons": _ordered_no_outcome_reasons(layers, campaign_abort),
        "submodels": {"global_map": {"record": layers["global_map_record"], "conversion_inline": layers["global_map_conversion_inline"], "extended_base": layers["global_map_extended_base"]}, "tdef": {"pointer_pair": layers["tdef_pointer_pair"]}},
        "claims": CLAIMS,
    }
    validate_analysis_report(
        report,
        frozen,
        reached_by_layer={key: drafts[key].reached for key in LAYER_KEYS},
    )
    compare_frozen_to_report(frozen, report)
    return report


def build_analysis(sources: list[ReplicaSource], candidate_output: Path, validate_holdout_after_freeze: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    if len(sources) != 3:
        raise ValidationError("A3 analysis requires exactly three replica sources")
    frozen_result = freeze_analysis(sources[:2], candidate_output)
    counted_holdout = CountingReplicaSource(sources[2])
    receipt = validate_holdout_after_freeze(frozen_result.frozen_sha256)
    first = frozen_result.derivation[0]
    return resume_analysis(
        frozen_result, counted_holdout, candidate_output, receipt,
        (first.campaign_id, first.producer_commit, first.provider_sha256),
        counted_holdout.open_count,
    )


def _holdout_validated_after_freeze(
    candidate_output: Path,
    frozen_sha: str,
    receipt: dict[str, Any] | None,
    analyzer_holdout_opens_before_receipt: int = 0,
) -> bool:
    """Derive the report flag from observables: the frozen bytes on disk and the receipt's own bindings."""
    if sha256_file(candidate_output) != frozen_sha:
        return False
    if receipt is None:
        return False
    return (
        receipt.get("derivation_candidate_set_sha256") == frozen_sha
        and receipt.get("validated_after_candidate_freeze") is True
        and receipt.get("page_bytes_exposed_to_analyzer")
        is (analyzer_holdout_opens_before_receipt > 0)
        and analyzer_holdout_opens_before_receipt == 0
    )


def main(argv: list[str] | None = None) -> int:
    from a3_analysis_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
