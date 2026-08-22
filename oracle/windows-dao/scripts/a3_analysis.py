#!/usr/bin/env python3
"""Layered, freeze-before-holdout analyzer for DAO-A3-ALLOCATION-MAPS-001.

A3 rule | implementation
--- | ---
Replica 1/2 freeze before any holdout open | :func:`build_analysis`
Qualified-page then record/page disambiguation | :func:`derive_layers`
Per-leg transcript frozen with all layers | :func:`candidate_document`
Four independent layered outcomes | :func:`build_analysis`
Frozen model-only holdout prediction | :func:`build_analysis`
Exactly-once reporting and holdout exception | :func:`predicate_results`
Field-for-field frozen/report agreement | :func:`build_analysis`
CLI compatible with the A2 analyzer shape | :func:`main`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from protocol_validation import ValidationError, canonical_json_bytes
from a3_layers import (
    BaseModel, ConversionModel, CrossCheckTranscript, TdefModel, derive_base,
    derive_conversion, derive_tdef_candidates, global_structural_valid,
    polarity_cross_check,
    predicts_base, predicts_conversion, predicts_global, predicts_tdef,
)
from a3_model import (
    CHECKPOINT_IDS, MAX_QUALIFIED_PAGES, PAGE_SIZE, Abort, GlobalRecordModel,
    ReplicaData, View, WorkCounter, candidate_page_space, global_start_candidates,
    qualify_global_pages, qualify_tdef_pages,
)
from a3_spec import (
    BOUNDS, EXPERIMENT_ID, LAYER_KEYS, PLAN, PLAN_SHA256, PREDICATES,
    PREDICATE_IDS, compare_frozen_to_report, load_bounded_json,
    frozen_json_bytes, validate_analysis_report, validate_document,
    validate_frozen_candidates,
)

MAX_JSON_BYTES = BOUNDS["max_json_bytes"]
CLAIMS = {key: PLAN.document["claims"][key] for key in (
    "descriptive_provider_observation_only", "general_tdef_catalog_row_index_or_lval_layout",
    "unobserved_slot_or_base_behavior", "compaction_encryption_or_version_behavior",
    "rust_correctness", "dao_compatibility_or_support",
)}
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts or "\\" in relative:
        raise ValidationError(f"unsafe A3 artifact path {relative!r}")
    resolved_root, resolved = root.resolve(), (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValidationError(f"A3 artifact escapes bundle root: {relative!r}")
    return resolved


@dataclass(frozen=True)
class ReplicaInput:
    data: ReplicaData
    replica: int
    campaign_id: str
    producer_commit: str
    provider_sha256: str
    churn_precondition_met: bool


class ReplicaSource(Protocol):
    def open(self) -> ReplicaInput: ...


@dataclass(frozen=True)
class LoadedReplicaSource:
    replica: ReplicaInput
    def open(self) -> ReplicaInput:
        return self.replica


class BundleReplicaData:
    def __init__(self, root: Path, indexes: dict[str, dict[str, Any]], checkpoint_ids: tuple[str, ...] = CHECKPOINT_IDS) -> None:
        self.root, self.indexes, self._checkpoint_ids = root, indexes, checkpoint_ids
        self._cache: dict[str, bytes] = {}

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return self._checkpoint_ids

    @property
    def page_count(self) -> dict[str, int]:
        return {name: int(index["page_count"]) for name, index in self.indexes.items()}

    @property
    def ordered_page_sha256(self) -> dict[str, tuple[str, ...]]:
        return {name: tuple(index["ordered_page_sha256"]) for name, index in self.indexes.items()}

    def page_bytes(self, digest: str) -> bytes:
        if digest in self._cache:
            return self._cache[digest]
        path = self.root / "page-store" / f"{digest}.page"
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != PAGE_SIZE:
            raise OSError(f"unsafe A3 page blob {path}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(f"A3 page blob hash mismatch {path}")
        self._cache[digest] = payload
        return payload


@dataclass(frozen=True)
class BundleReplicaSource:
    observation_path: Path
    bundle_root: Path

    def open(self) -> ReplicaInput:
        observation = load_bounded_json(self.observation_path, MAX_JSON_BYTES)
        validate_document(observation)
        if observation["plan_sha256"] != PLAN_SHA256:
            raise ValidationError("replica observation is not bound to the A3 plan")
        checkpoints = observation["checkpoints"]
        observed_ids = tuple(row["checkpoint_id"] for row in checkpoints)
        indexes: dict[str, dict[str, Any]] = {}
        prior: list[str] = []
        changed_total = 0
        for ordinal, checkpoint in enumerate(checkpoints):
            reference = checkpoint["page_index"]
            path = _safe_path(self.bundle_root, reference["path"])
            if path.stat().st_size != reference["size_bytes"] or _sha256(path) != reference["sha256"]:
                raise ValidationError(f"{path}: page-index binding failed")
            index = load_bounded_json(path, MAX_JSON_BYTES)
            validate_document(index)
            expected_predecessor = CHECKPOINT_IDS[ordinal - 1] if ordinal else None
            bindings = {
                "plan_sha256": PLAN_SHA256, "producer_commit": observation["producer_commit"],
                "campaign_id": observation["campaign_id"], "environment_sha256": observation["environment_sha256"],
                "provider_sha256": observation["provider_sha256"], "replica": observation["replica"],
                "checkpoint_id": checkpoint["checkpoint_id"], "ordinal": ordinal,
                "predecessor_checkpoint_id": expected_predecessor,
                "page_count": checkpoint["actual_file_pages"],
            }
            if any(index[key] != value for key, value in bindings.items()):
                raise ValidationError(f"{path}: page-index metadata binding mismatch")
            hashes = index["ordered_page_sha256"]
            expected_changed = []
            for page in range(max(len(prior), len(hashes))):
                prior_hash = prior[page] if page < len(prior) else None
                current_hash = hashes[page] if page < len(hashes) else None
                if prior_hash != current_hash:
                    expected_changed.append(page)
            if index["changed_page_indices"] != expected_changed:
                raise ValidationError(f"{path}: changed-page reconstruction failed")
            changed_total += len(expected_changed)
            prior, indexes[checkpoint["checkpoint_id"]] = hashes, index
        if changed_total != observation["changed_hash_entries"]:
            raise ValidationError("replica changed-hash total mismatch")
        by_id = {row["checkpoint_id"]: row for row in checkpoints}
        before, deleted = by_id["L_REL_1280"], by_id["L_DELETE_ALL"]
        reread = next((row["row_count"] for row in deleted["dao_reread"] if row["role"] == "L"), None)
        churn = before["table_row_counts"]["L"] != 0 and reread == 0
        return ReplicaInput(
            BundleReplicaData(self.bundle_root, indexes, observed_ids), observation["replica"],
            observation["campaign_id"], observation["producer_commit"], observation["provider_sha256"], churn,
        )


@dataclass(frozen=True)
class LayerDraft:
    model: GlobalRecordModel | ConversionModel | BaseModel | TdefModel | None
    survivor_count: int
    abort: Abort | None
    applicable: bool = True


def _not_applicable() -> LayerDraft:
    return LayerDraft(None, 0, None, False)


def _validate_inputs(replicas: list[ReplicaInput]) -> None:
    if [row.replica for row in replicas] != list(range(1, len(replicas) + 1)):
        raise Abort("A3-REPLICA-DISAGREEMENT")
    for attribute in ("campaign_id", "producer_commit", "provider_sha256"):
        if len({getattr(row, attribute) for row in replicas}) != 1:
            raise Abort("A3-REPLICA-DISAGREEMENT")


def _pair(function: Callable[[View, int], Any], views: tuple[View, View]) -> LayerDraft:
    outcomes: list[Any | Abort] = []
    for index, view in enumerate(views):
        try:
            outcomes.append(function(view, index))
        except Abort as exc:
            outcomes.append(exc)
    if all(isinstance(row, Abort) for row in outcomes):
        first, second = outcomes
        assert isinstance(first, Abort) and isinstance(second, Abort)
        return LayerDraft(None, 0, first if first.predicate_id == second.predicate_id else Abort("A3-REPLICA-DISAGREEMENT"))
    if any(isinstance(row, Abort) for row in outcomes) or outcomes[0] != outcomes[1]:
        return LayerDraft(None, 0, Abort("A3-REPLICA-DISAGREEMENT"))
    return LayerDraft(outcomes[0], 1, None)


def _qualify(function: Callable[[View, range], tuple[int, ...]], views: tuple[View, View], pages: range) -> tuple[tuple[int, ...], Abort | None]:
    outcomes: list[tuple[int, ...] | Abort] = []
    for view in views:
        try:
            outcomes.append(function(view, pages))
        except Abort as exc:
            outcomes.append(exc)
    if all(isinstance(row, Abort) for row in outcomes):
        first, second = outcomes
        assert isinstance(first, Abort) and isinstance(second, Abort)
        return (), first if first.predicate_id == second.predicate_id else Abort("A3-REPLICA-DISAGREEMENT")
    if any(isinstance(row, Abort) for row in outcomes) or outcomes[0] != outcomes[1]:
        return (), Abort("A3-REPLICA-DISAGREEMENT")
    assert isinstance(outcomes[0], tuple)
    return outcomes[0], None


def _global_draft(views: tuple[View, View], pages: tuple[int, ...]) -> LayerDraft:
    if not pages:
        return LayerDraft(None, 0, Abort("A3-GLOBAL-PAGE-NONE"))
    replicas: list[dict[int, tuple[list[GlobalRecordModel], dict[str, bool]]]] = []
    for replica_index, view in enumerate(views):
        page_rows = {page: global_start_candidates(view, page, enumerate_candidates=replica_index == 0) for page in pages}
        replicas.append(page_rows)
    if replicas[0] != replicas[1]:
        return LayerDraft(None, 0, Abort("A3-REPLICA-DISAGREEMENT"))
    rows = replicas[0]
    nonempty = {page: models for page, (models, _evidence) in rows.items() if models}
    if len(nonempty) > 1:
        return LayerDraft(None, sum(len(models) for models in nonempty.values()), Abort("A3-GLOBAL-PAGE-MULTIPLE"))
    if not nonempty:
        evidence = {key: any(row[1][key] for row in rows.values()) for key in ("anchor", "relation", "suffix")}
        if not evidence["anchor"]:
            predicate = "A3-GLOBAL-RECORD-NONE"
        elif not evidence["relation"]:
            predicate = "A3-D-SET-RELATION"
        else:
            predicate = "A3-GLOBAL-RECORD-END"
        return LayerDraft(None, 0, Abort(predicate))
    models = next(iter(nonempty.values()))
    polarities = {model.bit_polarity for model in models}
    if not polarities:
        return LayerDraft(None, 0, Abort("A3-POLARITY-NONE"))
    if len(polarities) > 1:
        return LayerDraft(None, len(models), Abort("A3-POLARITY-MULTIPLE"))
    starts = {model.record.start for model in models}
    if len(starts) > 1:
        return LayerDraft(None, len(starts), Abort("A3-GLOBAL-RECORD-MULTIPLE"))
    return LayerDraft(models[0], 1, None)


def derive_layers(derivation: list[ReplicaInput], work: WorkCounter) -> tuple[dict[str, LayerDraft], tuple[int, ...], tuple[int, ...], CrossCheckTranscript]:
    _validate_inputs(derivation)
    views = (View(derivation[0].data, work), View(derivation[1].data, work))
    empty_transcript = CrossCheckTranscript()
    if not all(view.idle_pairs_identical() for view in views):
        abort = Abort("A3-IDLE-EQUALITY")
        return {key: LayerDraft(None, 0, abort) for key in LAYER_KEYS}, (), (), empty_transcript
    pages = candidate_page_space(views)
    global_pages, global_abort = _qualify(qualify_global_pages, views, pages)
    global_draft = LayerDraft(None, 0, global_abort) if global_abort else _global_draft(views, global_pages)
    drafts: dict[str, LayerDraft] = {"global_map_record": global_draft}
    transcript = empty_transcript
    if not isinstance(global_draft.model, GlobalRecordModel):
        drafts["global_map_conversion_inline"] = _not_applicable()
        drafts["global_map_extended_base"] = _not_applicable()
    else:
        structural = tuple(global_structural_valid(view, global_draft.model) for view in views)
        transcripts = tuple(polarity_cross_check(view, global_draft.model) for view in views)
        if structural[0] != structural[1] or transcripts[0] != transcripts[1]:
            conversion = LayerDraft(None, 0, Abort("A3-REPLICA-DISAGREEMENT"))
        elif not structural[0]:
            conversion = LayerDraft(None, 0, Abort("A3-STRUCTURAL-EXCLUSION"))
        else:
            transcript = transcripts[0]
            if transcript.first_violating_leg is not None:
                conversion = LayerDraft(None, 0, Abort("A3-POLARITY-CROSSCHECK"))
            else:
                conversion = _pair(lambda view, _index: derive_conversion(view, global_draft.model)[0], views)
        drafts["global_map_conversion_inline"] = conversion
        if isinstance(conversion.model, ConversionModel):
            drafts["global_map_extended_base"] = _pair(lambda view, _index: derive_base(view, global_draft.model, conversion.model), views)
        else:
            drafts["global_map_extended_base"] = _not_applicable()
    tdef_pages, tdef_abort = _qualify(qualify_tdef_pages, views, pages)
    if tdef_abort:
        drafts["tdef_pointer_pair"] = LayerDraft(None, 0, tdef_abort)
    elif not tdef_pages:
        drafts["tdef_pointer_pair"] = LayerDraft(None, 0, Abort("A3-TDEF-PAGE-NONE"))
    else:
        drafts["tdef_pointer_pair"] = _pair(
            lambda view, index: derive_tdef_candidates(view, tdef_pages, derivation[index].churn_precondition_met, enumerate_candidates=index == 0)[0], views,
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


def _layer_result(draft: LayerDraft, holdout_match: bool | None, campaign_abort: Abort | None) -> dict[str, Any]:
    if not draft.applicable:
        return {"status": "not_applicable", "derivation_survivor_count": 0, "holdout_evaluated": False, "no_outcome_reasons": [], "terminal_predicate_id": None, "model": None}
    abort = campaign_abort or draft.abort
    if abort:
        return {"status": "no_outcome", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": False, "no_outcome_reasons": [abort.reason], "terminal_predicate_id": abort.predicate_id, "model": None if draft.model is None else draft.model.document()}
    if holdout_match:
        return {"status": "decisive_predicts_holdout", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": True, "no_outcome_reasons": [], "terminal_predicate_id": None, "model": draft.model.document() if draft.model else None}
    return {"status": "no_outcome", "derivation_survivor_count": draft.survivor_count, "holdout_evaluated": True, "no_outcome_reasons": ["holdout_prediction_failure"], "terminal_predicate_id": "A3-HOLDOUT-PREDICTION", "model": draft.model.document() if draft.model else None}


def predicate_results(layer_results: dict[str, dict[str, Any]], idle_evaluated: bool) -> tuple[list[dict[str, str]], list[str]]:
    terminals = {row["terminal_predicate_id"] for row in layer_results.values() if row["terminal_predicate_id"]}
    decisive = {key for key, row in layer_results.items() if row["status"] == "decisive_predicts_holdout"}
    if decisive:
        terminals.discard("A3-HOLDOUT-PREDICTION")
    stage_groups = {
        "global_map_record": (
            ("A3-GLOBAL-PAGE-NONE", "A3-GLOBAL-PAGE-MULTIPLE"),
            ("A3-GLOBAL-RECORD-NONE", "A3-D-SET-RELATION"),
            ("A3-GLOBAL-RECORD-END",),
            ("A3-POLARITY-NONE", "A3-POLARITY-MULTIPLE"),
            ("A3-GLOBAL-RECORD-MULTIPLE",),
        ),
        "global_map_conversion_inline": (
            ("A3-POLARITY-CROSSCHECK",),
            ("A3-CONVERSION-NONE", "A3-CONVERSION-MULTIPLE"),
            ("A3-SLOT-ACTIVATION", "A3-SLOT-FINAL"),
            ("A3-INLINE-BOUNDARY-NONE", "A3-INLINE-BOUNDARY-MULTIPLE", "A3-INLINE-SUFFIX"),
        ),
        "global_map_extended_base": (
            ("A3-BASE-DISCRIMINATION",),
            ("A3-BASE-NONE", "A3-BASE-MULTIPLE"),
        ),
        "tdef_pointer_pair": (
            ("A3-TDEF-PAGE-NONE", "A3-TDEF-PAGE-MULTIPLE"),
            ("A3-CHURN-PRECONDITION",),
            ("A3-GROWTH-POINTER-NONE",),
            ("A3-CHURN-POINTER-NONE",),
            ("A3-TDEF-RECORD-NONE",),
            ("A3-TDEF-RECORD-MULTIPLE", "A3-POINTER-MULTIPLE"),
        ),
    }
    evaluated: set[str] = set()
    for key, groups in stage_groups.items():
        row = layer_results[key]
        if row["status"] == "not_applicable":
            continue
        terminal = row["terminal_predicate_id"]
        stop = next((index for index, group in enumerate(groups) if terminal in group), len(groups) - 1)
        evaluated.update(predicate for group in groups[:stop + 1] for predicate in group)
    if idle_evaluated:
        evaluated.update(("A3-IDLE-EQUALITY", "A3-SNAPSHOT-RECONSTRUCTION", "A3-RESOURCE-BOUND"))
    if any(row["status"] != "not_applicable" for row in layer_results.values()):
        evaluated.update(("A3-STRUCTURAL-EXCLUSION", "A3-POINTER-VALIDITY", "A3-REPLICA-DISAGREEMENT"))
    if decisive or any(row["holdout_evaluated"] for row in layer_results.values()):
        evaluated.add("A3-HOLDOUT-PREDICTION")
    results: list[dict[str, str]] = []
    for predicate_id in PREDICATE_IDS:
        _reason, registered_layer = PREDICATES[predicate_id]
        if predicate_id in terminals:
            status = "fail"
        elif predicate_id == "A3-HOLDOUT-PREDICTION" and decisive:
            status = "pass"
        elif predicate_id in evaluated:
            status = "pass"
        else:
            status = "not_applicable"
        results.append({"predicate_id": predicate_id, "status": status, "layer": registered_layer})
    return results, sorted(terminals)


def build_analysis(sources: list[ReplicaSource], candidate_output: Path, validate_holdout_after_freeze: Callable[[str], None]) -> dict[str, Any]:
    if len(sources) != 3:
        raise ValidationError("A3 analysis requires exactly three replica sources")
    derivation = [sources[0].open(), sources[1].open()]
    work, campaign_abort, idle_evaluated = WorkCounter(), None, False
    try:
        drafts, global_pages, tdef_pages, transcript = derive_layers(derivation, work)
        idle_evaluated = True
    except Abort as exc:
        campaign_abort = exc
        drafts = {key: LayerDraft(None, 0, exc) for key in LAYER_KEYS}
        global_pages, tdef_pages, transcript = (), (), CrossCheckTranscript()
    frozen = candidate_document(derivation[0].campaign_id, global_pages, tdef_pages, transcript, drafts)
    frozen_sha = write_frozen(candidate_output, frozen)
    validate_holdout_after_freeze(frozen_sha)
    holdout_opened = False
    matches: dict[str, bool | None] = {key: None for key in LAYER_KEYS}
    if campaign_abort is None:
        try:
            holdout_input = sources[2].open()
            holdout_opened = True
            _validate_inputs(derivation + [holdout_input])
            holdout = View(holdout_input.data, work)
            global_draft = drafts["global_map_record"]
            if isinstance(global_draft.model, GlobalRecordModel):
                matches["global_map_record"] = predicts_global(holdout, global_draft.model)
                conversion = drafts["global_map_conversion_inline"]
                if isinstance(conversion.model, ConversionModel):
                    matches["global_map_conversion_inline"] = predicts_conversion(holdout, global_draft.model, conversion.model)
                    base = drafts["global_map_extended_base"]
                    if isinstance(base.model, BaseModel):
                        matches["global_map_extended_base"] = predicts_base(holdout, global_draft.model, conversion.model, base.model)
            tdef = drafts["tdef_pointer_pair"]
            if isinstance(tdef.model, TdefModel):
                matches["tdef_pointer_pair"] = predicts_tdef(holdout, tdef.model, holdout_input.churn_precondition_met)
        except Abort as exc:
            campaign_abort = exc
    layers = {key: _layer_result(drafts[key], matches[key], campaign_abort) for key in LAYER_KEYS}
    predicates, terminal_ids = predicate_results(layers, idle_evaluated)
    decisive = any(row["status"] == "decisive_predicts_holdout" for row in layers.values())
    report = {
        "protocol_version": "1.0.0", "document_type": "dao_a3_analysis_report", "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256, "campaign_id": derivation[0].campaign_id, "producer_commit": derivation[0].producer_commit,
        "derivation_replicas": [1, 2], "holdout_replica": 3, "input_checkpoint_count": len(CHECKPOINT_IDS) * 3,
        "qualified_page_counts": {"global_map": min(len(global_pages), MAX_QUALIFIED_PAGES), "tdef": min(len(tdef_pages), MAX_QUALIFIED_PAGES)},
        "qualified_pages": {"global_map": list(global_pages), "tdef": list(tdef_pages)},
        "record_candidates_examined": work.record_candidates, "candidate_models_examined": work.candidate_models,
        "derivation_survivor_counts": {key: drafts[key].survivor_count for key in LAYER_KEYS},
        "derivation_candidate_set_sha256": frozen_sha, "polarity_cross_check": transcript.document(),
        "analysis_work_units": work.value, "holdout_structurally_validated_after_freeze": True,
        "holdout_opened_after_freeze": holdout_opened,
        "holdout_evaluated": any(row["holdout_evaluated"] for row in layers.values()),
        "predicate_results": predicates, "terminal_predicate_ids": terminal_ids,
        "scientific_outcome": "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout",
        "no_outcome_reasons": sorted({reason for row in layers.values() for reason in row["no_outcome_reasons"]}),
        "submodels": {"global_map": {"record": layers["global_map_record"], "conversion_inline": layers["global_map_conversion_inline"], "extended_base": layers["global_map_extended_base"]}, "tdef": {"pointer_pair": layers["tdef_pointer_pair"]}},
        "claims": CLAIMS,
    }
    validate_analysis_report(report, frozen)
    compare_frozen_to_report(frozen, report)
    return report


def _receipt_validator(path: Path, campaign: str, producer: str) -> Callable[[str], None]:
    def validate(frozen_sha: str) -> None:
        receipt = load_bounded_json(path, MAX_JSON_BYTES)
        validate_document(receipt)
        expected = {"campaign_id": campaign, "producer_commit": producer, "derivation_candidate_set_sha256": frozen_sha}
        if any(receipt[key] != value for key, value in expected.items()):
            raise ValidationError("A3 holdout receipt binding mismatch")
    return validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--replica", action="append", type=Path)
    parser.add_argument("--candidate-output", type=Path)
    parser.add_argument("--holdout-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    root, artifacts = arguments.bundle_root, PLAN.document["artifacts"]
    replicas = arguments.replica or [root / relative for relative in artifacts["replica_observations"]]
    candidate = arguments.candidate_output or root / artifacts["frozen_candidate_set"]
    receipt = arguments.holdout_receipt or root / artifacts["holdout_structure_receipt"]
    output = arguments.output or root / artifacts["analysis_report"]
    try:
        if len(replicas) != 3:
            raise ValidationError("exactly three A3 replica observations are required")
        sources = [BundleReplicaSource(path, root) for path in replicas]
        first = sources[0].open()
        report = build_analysis(sources, candidate, _receipt_validator(receipt, first.campaign_id, first.producer_commit))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(canonical_json_bytes(report))
    except (Abort, OSError, ValidationError) as exc:
        print(f"A3 analysis failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(output), "scientific_outcome": report["scientific_outcome"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
