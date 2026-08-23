#!/usr/bin/env python3
"""Serializable, bounded state for resuming A3 after the workflow holdout gate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a3_analysis_input import ReplicaInput
from a3_layers import (
    BaseModel, ConversionModel, CrossCheckTranscript, Leg, TdefModel,
)
from a3_model import Abort, GlobalRecordModel, PAGE_SIZE, Record, WorkCounter
from a3_spec import (
    BOUNDS, LAYER_KEYS, LAYER_PREDICATE_SEQUENCES, frozen_json_bytes,
    load_bounded_json, load_bounded_json_with_payload, validate_frozen_candidates,
)
from protocol_validation import ValidationError

MAX_JSON_BYTES = BOUNDS["max_json_bytes"]
CAMPAIGN_TERMINALS = {
    "A3-IDLE-EQUALITY",
    "A3-SNAPSHOT-RECONSTRUCTION",
    "A3-RESOURCE-BOUND",
}


@dataclass(frozen=True)
class LayerDraft:
    model: GlobalRecordModel | ConversionModel | BaseModel | TdefModel | None
    survivor_count: int
    abort: Abort | None
    applicable: bool = True
    reached: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FreezeResult:
    derivation: list[ReplicaInput]
    drafts: dict[str, LayerDraft]
    global_pages: tuple[int, ...]
    tdef_pages: tuple[int, ...]
    transcript: CrossCheckTranscript
    frozen: dict[str, Any]
    frozen_sha256: str
    work: WorkCounter
    campaign_abort: Abort | None
    observed_candidate_sha256: str | None = None


def _leg(document: dict[str, str] | None) -> Leg | None:
    if document is None:
        return None
    return Leg(document["left_checkpoint_id"], document["right_checkpoint_id"])


def _transcript(document: dict[str, Any]) -> CrossCheckTranscript:
    return CrossCheckTranscript(
        tuple(_leg(row) for row in document["evaluated_legs"] if row is not None),
        _leg(document["representation_change_stop"]),
        _leg(document["first_violating_leg"]),
        document["first_violating_page"],
    )


def _record(document: dict[str, int]) -> Record:
    return Record(document["page"], document["start"], document["end"])


def _frozen_model(layer: str, document: dict[str, Any] | None) -> Any:
    if document is None:
        return None
    if layer == "global_map_record":
        return GlobalRecordModel(
            _record(document["record"]), document["bit_polarity"],
            document["zero_suffix_slack_bytes"],
        )
    if layer == "global_map_conversion_inline":
        return ConversionModel(
            document["conversion_checkpoint_id"], document["conversion_ordinal"],
            document["indirect_tag"], document["active_slot_count_at_conversion"],
            document["active_slot_count_at_h_rel_0904"], document["inline_boundary"],
            tuple(document["slot_reference_pages"]),
        )
    if layer == "global_map_extended_base":
        return BaseModel(document["extended_base_formula"])
    if layer == "tdef_pointer_pair":
        return TdefModel(
            _record(document["record"]), document["pointer_layout"],
            document["growth_pointer_offset"], document["delete_reinsert_pointer_offset"],
        )
    raise ValidationError(f"unknown frozen A3 layer {layer}")


def load_freeze_state(
    path: Path,
    candidate_output: Path,
) -> tuple[FreezeResult, tuple[str, str, str], int]:
    state = load_bounded_json(path, MAX_JSON_BYTES)
    frozen, frozen_payload = load_bounded_json_with_payload(
        candidate_output, MAX_JSON_BYTES
    )
    validate_frozen_candidates(frozen)
    if frozen_json_bytes(frozen) != frozen_payload:
        raise ValidationError("A3 frozen candidate bytes are not canonical")
    frozen_sha = hashlib.sha256(frozen_payload).hexdigest()
    required = {
        "document_type", "campaign_id", "producer_commit", "provider_sha256",
        "derivation_candidate_set_sha256", "freeze_phase_completed",
        "replica_3_artifact_existed_before_freeze_phase_completed",
        "analyzer_replica_3_opens_before_receipt", "campaign_terminal_predicate_id",
        "reached_by_layer", "work",
    }
    if set(state) != required or state["document_type"] != "dao_a3_internal_freeze_phase":
        raise ValidationError("A3 freeze state shape mismatch")
    if (
        state["derivation_candidate_set_sha256"] != frozen_sha
        or state["campaign_id"] != frozen["campaign_id"]
        or state["freeze_phase_completed"] is not True
        or state["replica_3_artifact_existed_before_freeze_phase_completed"] is not False
    ):
        raise ValidationError("A3 freeze state binding mismatch")
    opens = state["analyzer_replica_3_opens_before_receipt"]
    if isinstance(opens, bool) or not isinstance(opens, int) or opens < 0:
        raise ValidationError("A3 freeze state analyzer-open count is invalid")
    reached = state["reached_by_layer"]
    if set(reached) != set(LAYER_KEYS):
        raise ValidationError("A3 freeze state layer shape mismatch")
    drafts: dict[str, LayerDraft] = {}
    for key in LAYER_KEYS:
        layer = frozen["layers"][key]
        predicates = reached[key]
        if not isinstance(predicates, list) or any(
            predicate not in LAYER_PREDICATE_SEQUENCES[key] for predicate in predicates
        ):
            raise ValidationError("A3 freeze state reached predicates are invalid")
        ordered = [
            predicate for predicate in LAYER_PREDICATE_SEQUENCES[key]
            if predicate in predicates
        ]
        if predicates != ordered:
            raise ValidationError("A3 freeze state reached predicates are not ordered")
        terminal = layer["terminal_predicate_id"]
        abort = None if terminal is None else Abort(terminal, layer["derivation_survivor_count"])
        drafts[key] = LayerDraft(
            _frozen_model(key, layer["model"]), layer["derivation_survivor_count"],
            abort, layer["applicable"], frozenset(predicates),
        )
    work_state = state["work"]
    if not isinstance(work_state, dict) or set(work_state) != {
        "value", "record_candidates", "candidate_models", "page_digests"
    }:
        raise ValidationError("A3 freeze work state shape mismatch")
    work = WorkCounter()
    for key in ("value", "record_candidates", "candidate_models"):
        value = work_state[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValidationError("A3 freeze work counter is invalid")
        setattr(work, key, value)
    if (
        work.value > BOUNDS["max_analysis_work_units"]
        or work.record_candidates > BOUNDS["max_record_candidates"]
        or work.candidate_models > BOUNDS["max_candidate_models"]
    ):
        raise ValidationError("A3 freeze work counter exceeds its bound")
    digests = work_state["page_digests"]
    if not isinstance(digests, list) or len(digests) != len(set(digests)) or any(
        not isinstance(digest, str) or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for digest in digests
    ):
        raise ValidationError("A3 freeze page-digest state is invalid")
    work.page_digests = set(digests)
    work.page_bytes_read = len(digests) * PAGE_SIZE
    if len(digests) > BOUNDS["max_unique_page_blobs"]:
        raise ValidationError("A3 freeze page-digest state exceeds its bound")
    campaign_terminal = state["campaign_terminal_predicate_id"]
    if campaign_terminal is not None and campaign_terminal not in CAMPAIGN_TERMINALS:
        raise ValidationError("A3 freeze campaign terminal is invalid")
    campaign_abort = None if campaign_terminal is None else Abort(campaign_terminal)
    result = FreezeResult(
        [], drafts, tuple(frozen["qualified_pages"]["global_map"]),
        tuple(frozen["qualified_pages"]["tdef"]), _transcript(frozen["polarity_cross_check"]),
        frozen, frozen_sha, work, campaign_abort, frozen_sha,
    )
    bindings = (state["campaign_id"], state["producer_commit"], state["provider_sha256"])
    return result, bindings, opens
