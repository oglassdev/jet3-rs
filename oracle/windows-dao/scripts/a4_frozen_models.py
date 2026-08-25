#!/usr/bin/env python3
"""Reconstruct holdout-only A4 models from validated frozen JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from a4_layer_h1 import H1Binding, H1ReplicaCandidate, LocatorTarget
from a4_layer_h2 import H2ReplicaCandidate
from a4_layer_h3 import H3Candidate
from a4_layer_h4 import EncodedDerivation, H4Candidate
from a4_spec import canonical_json_bytes, sha256_hex


@dataclass(frozen=True)
class FrozenModelPrefix:
    """The dependency-complete model prefix permitted across the boundary."""

    h1: H1ReplicaCandidate | None
    h2: H2ReplicaCandidate | None
    h3: H3Candidate | None
    h4_root: H4Candidate | None
    h4: EncodedDerivation | None


@dataclass(frozen=True)
class FrozenModels:
    """Every replica-invariant model from a decisive derivation."""

    h1: H1ReplicaCandidate
    h2: H2ReplicaCandidate
    h3: H3Candidate
    h4_root: H4Candidate
    h4: EncodedDerivation


def _only_model(result: Mapping[str, Any], stage: str) -> Mapping[str, Any]:
    if (
        result.get("status") != "model"
        or result.get("terminal_predicate_id") is not None
        or result.get("derivation_survivor_count") != 1
    ):
        raise ValueError(f"A4 frozen {stage} is not a decisive model")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError(f"A4 frozen {stage} does not contain exactly one candidate")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ValueError(f"A4 frozen {stage} candidate is not an object")
    if result.get("canonical_candidates_sha256") != sha256_hex(
        canonical_json_bytes(candidates)
    ):
        raise ValueError(f"A4 frozen {stage} candidate-array hash does not recompute")
    return candidate


def _same_document(actual: Mapping[str, Any], expected: Mapping[str, Any], stage: str) -> None:
    if canonical_json_bytes(actual) != canonical_json_bytes(expected):
        raise ValueError(f"A4 frozen {stage} candidate identity does not recompute")


def _h1(candidate: Mapping[str, Any]) -> H1ReplicaCandidate:
    model = candidate["model"]
    bindings = tuple(
        H1Binding(
            int(row["replica"]),
            str(row["logical_role"]),
            str(row["lifecycle_instance"]),
            int(row["tdef_page"]),
            tuple(
                LocatorTarget(int(target["page"]), int(target["row"]))
                for target in row["locator_targets"]
            ),
        )
        for row in candidate["instance_bindings"]
    )
    result = H1ReplicaCandidate(
        0,
        str(model["layout"]),
        str(model["table_signature_id"]),
        tuple(int(value) for value in model["locator_offsets"]),
        bindings,
    )
    _same_document(candidate, result.document(), "H1")
    return result


def _h2(candidate: Mapping[str, Any]) -> H2ReplicaCandidate:
    model = candidate["model"]
    result = H2ReplicaCandidate(
        0,
        int(model["row_mask"]),
        str(model["polarity"]),
        int(model["owned_in_use_locator_ordinal"]),
        int(model["available_locator_ordinal"]),
    )
    _same_document(candidate, result.document(), "H2")
    return result


def _plain(candidate: Mapping[str, Any], stage: str) -> H3Candidate:
    result = H3Candidate(str(candidate["model_type"]), dict(candidate["model"]))
    _same_document(candidate, result.document(), stage)
    return result


def _h4(candidate: Mapping[str, Any], stage: str) -> H4Candidate:
    bindings = tuple(dict(row) for row in candidate.get("instance_bindings", ()))
    result = H4Candidate(str(candidate["model_type"]), dict(candidate["model"]), bindings)
    _same_document(candidate, result.document(), stage)
    return result


def load_frozen_model_prefix(document: Mapping[str, Any]) -> FrozenModelPrefix:
    """Recompute the dependency-complete decisive prefix of frozen models."""
    layers = document["layers"]
    h4 = layers["h4_catalog_bootstrap"]

    def present(result: Mapping[str, Any]) -> bool:
        return result.get("status") == "model"

    h1 = (
        _h1(_only_model(layers["h1_tdef_to_map_row"], "H1"))
        if present(layers["h1_tdef_to_map_row"])
        else None
    )
    h2 = (
        _h2(_only_model(layers["h2_row_identity_map_role"], "H2"))
        if present(layers["h2_row_identity_map_role"])
        else None
    )
    h3 = (
        _plain(_only_model(layers["h3_indirect_traversal"], "H3"), "H3")
        if present(layers["h3_indirect_traversal"])
        else None
    )
    root = (
        _h4(_only_model(h4["root_result"], "H4 root"), "H4 root")
        if present(h4["root_result"])
        else None
    )
    structural = (
        _h4(
            _only_model(h4["structural_result"], "H4 structural"),
            "H4 structural",
        )
        if present(h4["structural_result"])
        else None
    )
    final = (
        _h4(_only_model(h4["encoding_result"], "H4 encoding"), "H4 encoding")
        if present(h4["encoding_result"])
        else None
    )
    ordered = (h1, h2, h3, root)
    if any(value is not None for value in ordered[1:]) and h1 is None:
        raise ValueError("A4 frozen model prefix omits H1")
    if any(value is not None for value in ordered[2:]) and h2 is None:
        raise ValueError("A4 frozen model prefix omits H2")
    if root is not None and h3 is None:
        raise ValueError("A4 frozen model prefix omits H3")
    if (structural is not None or final is not None) and root is None:
        raise ValueError("A4 frozen model prefix omits H4 root")
    if final is not None and structural is None:
        raise ValueError("A4 frozen model prefix omits H4 structural model")
    encoded = (
        EncodedDerivation(structural, final)
        if structural is not None and final is not None
        else None
    )
    return FrozenModelPrefix(h1, h2, h3, root, encoded)


def load_frozen_models(document: Mapping[str, Any]) -> FrozenModels:
    """Require and recompute every model from a decisive frozen document."""
    models = load_frozen_model_prefix(document)
    h1, h2, h3 = models.h1, models.h2, models.h3
    root, h4 = models.h4_root, models.h4
    if h1 is None or h2 is None or h3 is None or root is None or h4 is None:
        raise ValueError("A4 frozen derivation does not contain every model")
    return FrozenModels(h1, h2, h3, root, h4)
