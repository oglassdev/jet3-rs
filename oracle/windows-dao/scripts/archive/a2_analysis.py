#!/usr/bin/env python3
"""Layered, holdout-safe analyzer for DAO-A2-ALLOCATION-MAPS-001."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from a2_layers import (
    BaseModel,
    ConversionModel,
    TdefModel,
    derive_base,
    derive_conversion,
    derive_tdef,
    predicts_base,
    predicts_conversion,
    predicts_global,
    predicts_tdef,
)
from a2_model import (
    CHECKPOINT_IDS,
    EXPERIMENT_DIR,
    MAX_QUALIFIED_PAGES,
    PAGE_SIZE,
    PLAN,
    PLAN_SHA256,
    PREDICATES,
    Abort,
    GlobalRecordModel,
    ReplicaData,
    View,
    WorkCounter,
    candidate_page_space,
    derive_global_record,
    qualify_global_pages,
    qualify_tdef_pages,
)
from protocol_validation import (
    ValidationError,
    canonical_json_bytes,
    load_json,
    validate_schema_value,
)

MAX_JSON_BYTES = int(PLAN["bounds"]["max_json_bytes"])
EXPERIMENT_ID = PLAN["experiment_id"]
CLAIMS = {
    key: value
    for key, value in PLAN["claims"].items()
    if key
    in {
        "descriptive_provider_observation_only",
        "general_tdef_catalog_row_index_or_lval_layout",
        "unobserved_slot_or_base_behavior",
        "compaction_encryption_or_version_behavior",
        "rust_correctness",
        "dao_compatibility_or_support",
    }
}
LAYER_KEYS = (
    "global_map_record",
    "global_map_conversion_inline",
    "global_map_extended_base",
    "tdef_pointer_pair",
)
REPORT_LAYERS = {
    "global_map_record": "global_map.record",
    "global_map_conversion_inline": "global_map.conversion_inline",
    "global_map_extended_base": "global_map.extended_base",
    "tdef_pointer_pair": "tdef.pointer_pair",
}


def _schema(name: str) -> dict[str, Any]:
    document = load_json(EXPERIMENT_DIR / name)
    if not isinstance(document, dict):
        raise ValidationError(f"{name}: schema must be an object")
    return document


def validate_document(document: dict[str, Any], schema_name: str) -> None:
    """Validate directly against a checked A2 JSON schema."""
    schema = _schema(schema_name)
    validate_schema_value(document, schema, schema, "$")


def _bounded_json(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValidationError(f"{path}: cannot inspect JSON: {exc}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > MAX_JSON_BYTES
    ):
        raise ValidationError(f"{path}: unsafe or oversized JSON artifact")
    try:
        payload = path.read_bytes()
        if payload.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 byte-order marks are forbidden")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate object key {key!r}")
                result[key] = value
            return result

        def reject_nonfinite(value: str) -> None:
            raise ValueError(f"non-finite JSON number {value}")

        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{path}: invalid bounded JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{path}: expected a JSON object")
    return document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError(f"{path}: cannot hash artifact: {exc}") from exc
    return digest.hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValidationError(f"unsafe A2 artifact path {relative!r}")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValidationError(f"A2 artifact escapes bundle root: {relative!r}")
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
    """Schema-checked indexes plus lazy, content-addressed bundle pages."""

    def __init__(
        self,
        bundle_root: Path,
        observation: dict[str, Any],
        indexes: dict[str, dict[str, Any]],
        checkpoint_ids: tuple[str, ...] = CHECKPOINT_IDS,
    ) -> None:
        self.bundle_root = bundle_root
        self.observation = observation
        self.indexes = indexes
        self._checkpoint_ids = checkpoint_ids
        self._cache: dict[str, bytes] = {}

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return self._checkpoint_ids

    @property
    def page_count(self) -> dict[str, int]:
        return {
            checkpoint: int(index["page_count"])
            for checkpoint, index in self.indexes.items()
        }

    @property
    def ordered_page_sha256(self) -> dict[str, tuple[str, ...]]:
        return {
            checkpoint: tuple(index["ordered_page_sha256"])
            for checkpoint, index in self.indexes.items()
        }

    def page_bytes(self, digest: str) -> bytes:
        retained = self._cache.get(digest)
        if retained is not None:
            return retained
        path = self.bundle_root / "page-store" / f"{digest}.page"
        try:
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != PAGE_SIZE
            ):
                raise OSError("unsafe page blob")
            retained = path.read_bytes()
        except OSError as exc:
            raise OSError(f"{path}: cannot read checked page blob") from exc
        if hashlib.sha256(retained).hexdigest() != digest:
            raise ValueError(f"{path}: content hash mismatch")
        self._cache[digest] = retained
        return retained


@dataclass(frozen=True)
class BundleReplicaSource:
    observation_path: Path
    bundle_root: Path

    def open(self) -> ReplicaInput:
        observation = _bounded_json(self.observation_path)
        validate_document(observation, "replica-observation.schema.json")
        if observation["plan_sha256"] != PLAN_SHA256:
            raise ValidationError("replica observation is not bound to the checked A2 plan")
        checkpoints = observation["checkpoints"]
        observed_ids = tuple(item["checkpoint_id"] for item in checkpoints)
        if observed_ids != CHECKPOINT_IDS:
            data = BundleReplicaData(self.bundle_root, observation, {}, observed_ids)
            return ReplicaInput(
                data,
                observation["replica"],
                observation["campaign_id"],
                observation["producer_commit"],
                observation["provider_sha256"],
                False,
            )
        indexes: dict[str, dict[str, Any]] = {}
        prior: list[str] = []
        changed_total = 0
        for ordinal, checkpoint in enumerate(checkpoints):
            reference = checkpoint["page_index"]
            path = _safe_path(self.bundle_root, reference["path"])
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise ValidationError(f"{path}: cannot inspect page index") from exc
            if size != reference["size_bytes"] or _sha256(path) != reference["sha256"]:
                raise ValidationError(f"{path}: page-index artifact binding failed")
            index = _bounded_json(path)
            validate_document(index, "page-index.schema.json")
            expected_predecessor = CHECKPOINT_IDS[ordinal - 1] if ordinal else None
            bindings = {
                "plan_sha256": PLAN_SHA256,
                "producer_commit": observation["producer_commit"],
                "campaign_id": observation["campaign_id"],
                "environment_sha256": observation["environment_sha256"],
                "provider_sha256": observation["provider_sha256"],
                "replica": observation["replica"],
                "checkpoint_id": checkpoint["checkpoint_id"],
                "ordinal": ordinal,
                "predecessor_checkpoint_id": expected_predecessor,
                "page_count": checkpoint["actual_file_pages"],
            }
            for key, expected in bindings.items():
                if index[key] != expected:
                    raise ValidationError(f"{path}: {key} binding mismatch")
            hashes = index["ordered_page_sha256"]
            if len(hashes) != index["page_count"]:
                raise ValidationError(f"{path}: page count/hash list mismatch")
            expected_changed = [
                page
                for page in range(max(len(prior), len(hashes)))
                if (prior[page] if page < len(prior) else None)
                != (hashes[page] if page < len(hashes) else None)
            ]
            if index["changed_page_indices"] != expected_changed:
                raise ValidationError(f"{path}: changed page indexes are not reconstructable")
            changed_total += len(expected_changed)
            prior = hashes
            indexes[checkpoint["checkpoint_id"]] = index
        if changed_total != observation["changed_hash_entries"]:
            raise ValidationError("replica changed-hash total mismatch")
        by_checkpoint = {item["checkpoint_id"]: item for item in checkpoints}
        before = by_checkpoint["L_REL_1280"]
        deleted = by_checkpoint["L_DELETE_ALL"]
        deleted_reread = next(
            (item["row_count"] for item in deleted["dao_reread"] if item["role"] == "L"), None
        )
        churn_precondition = before["table_row_counts"]["L"] > 0 and (
            deleted["table_row_counts"]["L"] == 0 and deleted_reread == 0
        )
        data = BundleReplicaData(self.bundle_root, observation, indexes)
        return ReplicaInput(
            data,
            observation["replica"],
            observation["campaign_id"],
            observation["producer_commit"],
            observation["provider_sha256"],
            churn_precondition,
        )


@dataclass(frozen=True)
class LayerDraft:
    model: GlobalRecordModel | ConversionModel | BaseModel | TdefModel | None
    survivor_count: int
    abort: Abort | None
    applicable: bool = True


def _derive_pair(function: Callable[[View], Any], views: tuple[View, View]) -> LayerDraft:
    outcomes: list[Any | Abort] = []
    for view in views:
        try:
            outcomes.append(function(view))
        except Abort as exc:
            outcomes.append(exc)
    if all(isinstance(item, Abort) for item in outcomes):
        first, second = outcomes
        assert isinstance(first, Abort) and isinstance(second, Abort)
        if first.predicate_id == second.predicate_id:
            return LayerDraft(None, 0, first)
        return LayerDraft(None, 0, Abort("A2-REPLICA-DISAGREEMENT"))
    if any(isinstance(item, Abort) for item in outcomes) or outcomes[0] != outcomes[1]:
        return LayerDraft(None, 0, Abort("A2-REPLICA-DISAGREEMENT"))
    return LayerDraft(outcomes[0], 1, None)


def _not_applicable() -> LayerDraft:
    return LayerDraft(None, 0, None, False)


def _qualify_pair(
    function: Callable[[View, range], tuple[int, ...]],
    views: tuple[View, View],
    pages: range,
) -> tuple[tuple[int, ...], Abort | None]:
    results: list[tuple[int, ...] | Abort] = []
    for view in views:
        try:
            results.append(function(view, pages))
        except Abort as exc:
            results.append(exc)
    if all(isinstance(item, Abort) for item in results):
        first, second = results
        assert isinstance(first, Abort) and isinstance(second, Abort)
        return (), first if first.predicate_id == second.predicate_id else Abort(
            "A2-REPLICA-DISAGREEMENT"
        )
    if any(isinstance(item, Abort) for item in results) or results[0] != results[1]:
        return (), Abort("A2-REPLICA-DISAGREEMENT")
    assert isinstance(results[0], tuple)
    return results[0], None


def _derive_pages(
    pages: tuple[int, ...],
    views: tuple[View, View],
    function: Callable[[View, int, int, bool], Any],
    none_page_id: str,
    multiple_page_id: str,
    none_record_id: str,
) -> LayerDraft:
    """Evaluate each qualified coordinate once across both derivation replicas."""
    if not pages:
        return LayerDraft(None, 0, Abort(none_page_id))
    survivors: list[Any] = []
    local_aborts: list[Abort] = []
    for page in pages:
        outcomes: list[Any | Abort] = []
        for replica_index, view in enumerate(views):
            try:
                outcomes.append(function(view, page, replica_index, replica_index == 0))
            except Abort as exc:
                outcomes.append(exc)
        if all(isinstance(item, Abort) for item in outcomes):
            first, second = outcomes
            assert isinstance(first, Abort) and isinstance(second, Abort)
            if first.predicate_id != second.predicate_id:
                return LayerDraft(None, 0, Abort("A2-REPLICA-DISAGREEMENT"))
            local_aborts.append(first)
            continue
        if any(isinstance(item, Abort) for item in outcomes) or outcomes[0] != outcomes[1]:
            return LayerDraft(None, 0, Abort("A2-REPLICA-DISAGREEMENT"))
        survivors.append(outcomes[0])
    if len(survivors) > 1:
        return LayerDraft(None, len(survivors), Abort(multiple_page_id))
    if len(survivors) == 1:
        return LayerDraft(survivors[0], 1, None)
    if local_aborts and len({item.predicate_id for item in local_aborts}) == 1:
        return LayerDraft(None, 0, local_aborts[0])
    return LayerDraft(None, 0, Abort(none_record_id))


def _candidate_document(
    campaign_id: str,
    global_pages: tuple[int, ...],
    tdef_pages: tuple[int, ...],
    drafts: dict[str, LayerDraft],
) -> dict[str, Any]:
    def layer(draft: LayerDraft) -> dict[str, Any]:
        return {
            "applicable": draft.applicable,
            "derivation_survivor_count": draft.survivor_count,
            "terminal_predicate_id": draft.abort.predicate_id if draft.abort else None,
            "no_outcome_reason": draft.abort.reason if draft.abort else None,
            "model": draft.model.document() if draft.model is not None else None,
        }

    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_frozen_derivation_candidates",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "campaign_id": campaign_id,
        "derivation_replicas": [1, 2],
        "qualified_pages": {"global_map": list(global_pages), "tdef": list(tdef_pages)},
        "layers": {key: layer(drafts[key]) for key in LAYER_KEYS},
    }


def _write_frozen(path: Path, document: dict[str, Any]) -> str:
    payload = canonical_json_bytes(document)
    if len(payload) > MAX_JSON_BYTES:
        raise ValidationError("A2 frozen candidate set exceeds JSON ceiling")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return hashlib.sha256(payload).hexdigest()


def _validate_inputs(replicas: list[ReplicaInput]) -> None:
    if [item.replica for item in replicas] != list(range(1, len(replicas) + 1)):
        raise Abort("A2-REPLICA-DISAGREEMENT")
    for attribute in ("campaign_id", "producer_commit", "provider_sha256"):
        if len({getattr(item, attribute) for item in replicas}) != 1:
            raise Abort("A2-REPLICA-DISAGREEMENT")


def _layer_result(
    draft: LayerDraft, holdout_match: bool | None, campaign_abort: Abort | None = None
) -> dict[str, Any]:
    if not draft.applicable:
        return {
            "status": "not_applicable",
            "derivation_survivor_count": 0,
            "holdout_evaluated": False,
            "no_outcome_reasons": [],
            "model": None,
        }
    abort = campaign_abort or draft.abort
    if abort is not None:
        reasons = [abort.reason]
        model = None
        status = "no_outcome"
        evaluated = False
    elif holdout_match:
        reasons = []
        model = draft.model.document() if draft.model is not None else None
        status = "decisive_predicts_holdout"
        evaluated = True
    else:
        reasons = [PREDICATES["A2-HOLDOUT-PREDICTION"][0]]
        model = None
        status = "no_outcome"
        evaluated = True
    return {
        "status": status,
        "derivation_survivor_count": draft.survivor_count,
        "holdout_evaluated": evaluated,
        "no_outcome_reasons": reasons,
        "model": model,
    }


def _predicate_results(
    drafts: dict[str, LayerDraft],
    layer_results: dict[str, dict[str, Any]],
    campaign_abort: Abort | None,
    idle_evaluated: bool,
) -> list[dict[str, str]]:
    failed = {
        draft.abort.predicate_id
        for draft in drafts.values()
        if draft.abort is not None
    }
    if campaign_abort is not None:
        failed.add(campaign_abort.predicate_id)
    for key, result in layer_results.items():
        if result["holdout_evaluated"] and result["status"] == "no_outcome":
            failed.add("A2-HOLDOUT-PREDICTION")
    decisive_layers = {
        REPORT_LAYERS[key]
        for key, result in layer_results.items()
        if result["status"] == "decisive_predicts_holdout"
    }
    results = []
    for predicate_id, (_, registered_layer) in PREDICATES.items():
        if predicate_id in failed:
            status = "fail"
        elif registered_layer in decisive_layers or (
            registered_layer == "applicable_layer" and decisive_layers
        ):
            status = "pass"
        elif predicate_id == "A2-IDLE-EQUALITY" and idle_evaluated:
            status = "pass"
        else:
            status = "not_applicable"
        results.append({"predicate_id": predicate_id, "status": status, "layer": registered_layer})
    return results


def _campaign_drafts(abort: Abort) -> dict[str, LayerDraft]:
    return {key: LayerDraft(None, 0, abort) for key in LAYER_KEYS}


def _derive_layers(
    derivation: list[ReplicaInput], work: WorkCounter
) -> tuple[dict[str, LayerDraft], tuple[int, ...], tuple[int, ...]]:
    _validate_inputs(derivation)
    views = (View(derivation[0].data, work), View(derivation[1].data, work))
    drafts: dict[str, LayerDraft] = {}
    global_pages: tuple[int, ...] = ()
    tdef_pages: tuple[int, ...] = ()

    if not all(view.idle_pairs_identical() for view in views):
        return _campaign_drafts(Abort("A2-IDLE-EQUALITY")), global_pages, tdef_pages

    pages = candidate_page_space(views)
    global_pages, global_qualification_abort = _qualify_pair(
        qualify_global_pages, views, pages
    )
    if global_qualification_abort is None:
        drafts["global_map_record"] = _derive_pages(
            global_pages,
            views,
            lambda view, page, _replica_index, charge: derive_global_record(
                view, page, enumerate_candidates=charge
            ),
            "A2-GLOBAL-PAGE-NONE",
            "A2-GLOBAL-PAGE-MULTIPLE",
            "A2-GLOBAL-RECORD-NONE",
        )
    else:
        drafts["global_map_record"] = LayerDraft(None, 0, global_qualification_abort)

    global_draft = drafts["global_map_record"]
    if global_draft.model is None:
        drafts["global_map_conversion_inline"] = _not_applicable()
        drafts["global_map_extended_base"] = _not_applicable()
    else:
        global_model = global_draft.model
        assert isinstance(global_model, GlobalRecordModel)
        conversion_draft = _derive_pair(
            lambda view: derive_conversion(view, global_model), views
        )
        drafts["global_map_conversion_inline"] = conversion_draft
        if conversion_draft.model is None:
            drafts["global_map_extended_base"] = _not_applicable()
        else:
            conversion = conversion_draft.model
            assert isinstance(conversion, ConversionModel)
            drafts["global_map_extended_base"] = _derive_pair(
                lambda view: derive_base(view, global_model, conversion), views
            )

    tdef_pages, tdef_qualification_abort = _qualify_pair(
        qualify_tdef_pages, views, pages
    )
    if tdef_qualification_abort is None:
        drafts["tdef_pointer_pair"] = _derive_pages(
            tdef_pages,
            views,
            lambda view, page, replica_index, charge: derive_tdef(
                view,
                page,
                derivation[replica_index].churn_precondition_met,
                enumerate_candidates=charge,
            ),
            "A2-TDEF-PAGE-NONE",
            "A2-TDEF-PAGE-MULTIPLE",
            "A2-TDEF-RECORD-NONE",
        )
    else:
        drafts["tdef_pointer_pair"] = LayerDraft(None, 0, tdef_qualification_abort)
    return drafts, global_pages, tdef_pages


def build_analysis(
    sources: list[ReplicaSource],
    candidate_output: Path,
    validate_holdout_after_freeze: Callable[[str], None],
) -> dict[str, Any]:
    """Derive on replicas 1+2, persist the freeze, then purely predict replica 3."""
    if len(sources) != 3:
        raise ValidationError("A2 analysis requires exactly three replica sources")
    derivation = [sources[0].open(), sources[1].open()]
    work = WorkCounter()
    campaign_abort: Abort | None = None
    idle_evaluated = False
    global_pages: tuple[int, ...] = ()
    tdef_pages: tuple[int, ...] = ()
    try:
        drafts, global_pages, tdef_pages = _derive_layers(derivation, work)
        idle_evaluated = True
    except Abort as exc:
        campaign_abort = exc
        drafts = _campaign_drafts(exc)

    frozen = _candidate_document(
        derivation[0].campaign_id, global_pages, tdef_pages, drafts
    )
    frozen_sha256 = _write_frozen(candidate_output, frozen)
    validate_holdout_after_freeze(frozen_sha256)
    # No replica-3 source operation occurs above this line.
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
                conversion_draft = drafts["global_map_conversion_inline"]
                if isinstance(conversion_draft.model, ConversionModel):
                    matches["global_map_conversion_inline"] = predicts_conversion(
                        holdout, global_draft.model, conversion_draft.model
                    )
                    base_draft = drafts["global_map_extended_base"]
                    if isinstance(base_draft.model, BaseModel):
                        matches["global_map_extended_base"] = predicts_base(
                            holdout,
                            global_draft.model,
                            conversion_draft.model,
                            base_draft.model,
                        )
            tdef_draft = drafts["tdef_pointer_pair"]
            if isinstance(tdef_draft.model, TdefModel):
                matches["tdef_pointer_pair"] = predicts_tdef(
                    holdout, tdef_draft.model, holdout_input.churn_precondition_met
                )
        except Abort as exc:
            campaign_abort = exc

    layer_results = {
        key: _layer_result(drafts[key], matches[key], campaign_abort)
        for key in LAYER_KEYS
    }
    terminal_ids = {
        draft.abort.predicate_id
        for draft in drafts.values()
        if draft.abort is not None
    }
    if campaign_abort is not None:
        terminal_ids.add(campaign_abort.predicate_id)
    terminal_ids.update(
        "A2-HOLDOUT-PREDICTION"
        for result in layer_results.values()
        if result["holdout_evaluated"] and result["status"] == "no_outcome"
    )
    reasons = sorted(
        {reason for result in layer_results.values() for reason in result["no_outcome_reasons"]}
    )
    decisive = any(
        result["status"] == "decisive_predicts_holdout"
        for result in layer_results.values()
    )
    report = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_analysis_report",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "campaign_id": derivation[0].campaign_id,
        "producer_commit": derivation[0].producer_commit,
        "derivation_replicas": [1, 2],
        "holdout_replica": 3,
        "input_checkpoint_count": len(CHECKPOINT_IDS) * int(PLAN["replicas"]["count"]),
        "qualified_page_counts": {
            "global_map": min(len(global_pages), MAX_QUALIFIED_PAGES),
            "tdef": min(len(tdef_pages), MAX_QUALIFIED_PAGES),
        },
        "record_candidates_examined": work.record_candidates,
        "candidate_models_examined": work.candidate_models,
        "derivation_survivor_counts": {
            key: drafts[key].survivor_count for key in LAYER_KEYS
        },
        "derivation_candidate_set_sha256": frozen_sha256,
        "analysis_work_units": work.value,
        "holdout_structurally_validated_after_freeze": True,
        "holdout_opened_after_freeze": holdout_opened,
        "holdout_evaluated": any(result["holdout_evaluated"] for result in layer_results.values()),
        "predicate_results": _predicate_results(
            drafts, layer_results, campaign_abort, idle_evaluated
        ),
        "terminal_predicate_ids": sorted(terminal_ids),
        "scientific_outcome": (
            "one_or_more_submodels_predict_holdout" if decisive else "no_submodel_predicts_holdout"
        ),
        "no_outcome_reasons": reasons,
        "submodels": {
            "global_map": {
                "record": layer_results["global_map_record"],
                "conversion_inline": layer_results["global_map_conversion_inline"],
                "extended_base": layer_results["global_map_extended_base"],
            },
            "tdef": {"pointer_pair": layer_results["tdef_pointer_pair"]},
        },
        "claims": CLAIMS,
    }
    validate_document(report, "analysis-report.schema.json")
    if len(canonical_json_bytes(report)) > MAX_JSON_BYTES:
        raise ValidationError("A2 analysis report exceeds JSON ceiling")
    return report


def _receipt_validator(path: Path, bundle_root: Path, candidate_path: Path,
                       replica: ReplicaInput) -> Callable[[str], None]:
    def validate(frozen_sha256: str) -> None:
        from a2_holdout import run_holdout_process

        run_holdout_process(bundle_root, candidate_path, frozen_sha256,
                            replica.campaign_id, replica.producer_commit, path)
        receipt = _bounded_json(path)
        validate_document(receipt, "holdout-structure-receipt.schema.json")
        expected = {
            "plan_sha256": PLAN_SHA256,
            "campaign_id": replica.campaign_id,
            "producer_commit": replica.producer_commit,
            "derivation_candidate_set_sha256": frozen_sha256,
        }
        for key, value in expected.items():
            if receipt[key] != value:
                raise ValidationError(f"holdout receipt {key} binding mismatch")
        manifest_path = bundle_root / "replica-artifacts" / "replica-03-manifest.json"
        if receipt["replica_artifact_manifest_sha256"] != _sha256(manifest_path):
            raise ValidationError("holdout receipt replica manifest binding mismatch")
    return validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--replica", action="append", type=Path)
    parser.add_argument("--candidate-output", type=Path)
    parser.add_argument("--holdout-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    root = arguments.bundle_root
    artifacts = PLAN["artifacts"]
    replicas = arguments.replica or [
        root / relative for relative in artifacts["replica_observations"]
    ]
    candidate_output = arguments.candidate_output or root / artifacts["frozen_candidate_set"]
    output = arguments.output or root / artifacts["analysis_report"]
    receipt_path = arguments.holdout_receipt or root / artifacts["holdout_structure_receipt"]
    try:
        if len(replicas) != 3:
            raise ValidationError("exactly three A2 replica observations are required")
        sources = [BundleReplicaSource(path, root) for path in replicas]
        first = sources[0].open()
        validator = _receipt_validator(receipt_path, root, candidate_output, first)
        # Re-opening replica 1 below is metadata-only and still precedes the freeze;
        # replica 3 remains unopened until build_analysis persists candidates.
        report = build_analysis(sources, candidate_output, validator)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(canonical_json_bytes(report))
    except (Abort, OSError, ValidationError) as exc:
        print(f"A2 analysis failed: {exc}", file=sys.stderr)
        return 1
    summary = {"output": str(output), "scientific_outcome": report["scientific_outcome"]}
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
