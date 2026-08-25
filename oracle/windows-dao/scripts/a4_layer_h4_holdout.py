#!/usr/bin/env python3
"""Unchanged-model H4 root and field prediction for replica 3."""

from __future__ import annotations

from typing import Sequence

from a4_layer_h4 import (
    OPERATIONS,
    ROOT_OPERATIONS,
    ROOT_SIGNATURE,
    CatalogRootObservation,
    H4Candidate,
    OperationRecord,
    _changed_at,
    _decode_for_model,
    _OBJECT_KIND,
    scan_name_occurrences,
)
from a4_layer_h4_fields import encoding_class_matches, identifier_assignment_exists
from a4_model import canonical_model_id
from a4_spec import CHECKPOINT_IDS


def predicts_root(frozen: H4Candidate, observation: CatalogRootObservation) -> bool:
    """Check one unchanged H4 root model on holdout."""
    if frozen.model_type != "h4_catalog_root" or observation.replica != 3:
        return False
    model = {
        "root_selection_signature": ROOT_SIGNATURE,
        "locator_offsets": list(observation.locator_offsets),
    }
    return (
        observation.tag_at_empty == 0x02
        and observation.traversal_valid_checkpoints == frozenset(CHECKPOINT_IDS)
        and all(
            _changed_at(observation.stream_fingerprint_by_checkpoint, operation)
            for operation in ROOT_OPERATIONS
        )
        and canonical_model_id("h4_catalog_root", model)
        == frozen.canonical_model_id
    )


def predicts_fields(
    frozen_structural: H4Candidate,
    frozen_final: H4Candidate,
    records: Sequence[OperationRecord],
) -> bool:
    """Select one holdout occurrence set satisfying both frozen field stages."""
    if (
        frozen_structural.model_type != "h4_structural_field"
        or frozen_final.model_type != "h4_final_encoded_field"
        or tuple(record.operation_id for record in records) != OPERATIONS
        or any(record.replica != 3 for record in records)
        or frozen_final.model.get("structural_model_id")
        != frozen_structural.canonical_model_id
    ):
        return False
    try:
        evidence = tuple(scan_name_occurrences(record) for record in records)
        mapping = frozen_structural.model["kind_mapping"]
        class_id = frozen_final.model["encoding_length_equivalence_class"]
        compatible = {}
        for group in evidence:
            rows = tuple(
                decoded
                for occurrence in group.occurrences
                if (
                    decoded := _decode_for_model(
                        group, occurrence, frozen_structural.model
                    )
                )
                is not None
                and decoded.kind == mapping[_OBJECT_KIND[group.record.operation_id]]
                and encoding_class_matches(
                    class_id,
                    group.expected_name,
                    bytes.fromhex(decoded.occurrence.encoded_hex),
                    decoded.stored_length,
                )
            )
            if not rows:
                return False
            compatible[group.record.operation_id] = rows
        options = {
            operation: frozenset(row.identifier for row in compatible[operation])
            for operation in OPERATIONS
        }
        return identifier_assignment_exists(
            OPERATIONS,
            options,
            frozen_structural.model["identifier_lifecycle"],
        )
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False
