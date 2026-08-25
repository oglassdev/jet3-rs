#!/usr/bin/env python3
"""Unchanged-model holdout evaluation for the A4 analyzer."""

from __future__ import annotations

from dataclasses import dataclass

from a4_analysis_input import HoldoutAnalysisInput
from a4_derivation import isolated_operation_deltas
from a4_frozen_models import FrozenModelPrefix, FrozenModels
from a4_layer_h1 import predict_h1
from a4_layer_h2 import decode_frozen_owned_rows
from a4_layer_h3 import predicts_h3
from a4_layer_h4 import OPERATIONS
from a4_layer_h4_holdout import predicts_fields, predicts_root
from a4_model import A4AnalysisError, WorkLedger


@dataclass(frozen=True)
class HoldoutResults:
    h1: bool | None
    h2: bool | None
    h3: bool | None
    h4_root: bool | None
    h4_fields: bool | None


def evaluate_holdout(
    inputs: HoldoutAnalysisInput,
    frozen: FrozenModelPrefix | FrozenModels,
    ledger: WorkLedger | None = None,
) -> HoldoutResults:
    """Open replica 3 only after derivation freeze and apply unchanged models."""
    from a4_layers import (
        catalog_root_observations,
        h3_observations,
        operation_records,
        predicts_h2,
    )

    work = ledger or WorkLedger()
    view = inputs.view
    if frozen.h1 is None:
        return HoldoutResults(None, None, None, None, None)
    holdout_h1 = predict_h1(
        view, inputs.qualified_tdef_pages, frozen.h1, work
    )
    if holdout_h1 is None:
        return HoldoutResults(False, None, None, None, None)
    if frozen.h2 is None:
        return HoldoutResults(True, None, None, None, None)
    h2_ok = predicts_h2(
        view,
        holdout_h1,
        frozen.h2,
        inputs.replica.table_row_counts,
    )
    if not h2_ok:
        return HoldoutResults(True, False, None, None, None)
    if frozen.h3 is None:
        return HoldoutResults(True, True, None, None, None)
    try:
        frozen_rows = decode_frozen_owned_rows(
            view, holdout_h1, frozen.h2, work
        )
        rows = h3_observations(view, frozen_rows, work)
    except A4AnalysisError:
        raise
    except ValueError:
        return HoldoutResults(True, True, False, None, None)
    h3_ok = predicts_h3(
        frozen.h3, rows, inputs.replica.source.page_count
    )
    if not h3_ok:
        return HoldoutResults(True, True, False, None, None)
    if frozen.h4_root is None:
        return HoldoutResults(True, True, True, None, None)
    roots = catalog_root_observations(
        view,
        inputs.qualified_tdef_pages,
        holdout_h1,
        frozen.h2,
        frozen.h3,
        work,
    )
    matching_roots = tuple(
        root for root in roots if predicts_root(frozen.h4_root, root)
    )
    if len(matching_roots) != 1:
        return HoldoutResults(True, True, True, False, None)
    if frozen.h4 is None:
        return HoldoutResults(True, True, True, True, None)
    root = matching_roots[0]
    try:
        deltas = isolated_operation_deltas(view, holdout_h1, rows, frozen.h3)
        for operation in OPERATIONS:
            if deltas[operation] - root.admitted_pages_by_checkpoint[operation]:
                return HoldoutResults(True, True, True, True, False)
        candidates = operation_records(
            view, root, deltas, frozen.h2, work
        )
        grouped = {operation: [] for operation in OPERATIONS}
        for record in candidates:
            grouped[record.operation_id].append(record)
        if any(len(grouped[operation]) != 1 for operation in OPERATIONS):
            return HoldoutResults(True, True, True, True, False)
        records = tuple(grouped[operation][0] for operation in OPERATIONS)
        fields_ok = predicts_fields(
            frozen.h4.structural, frozen.h4.final, records
        )
    except A4AnalysisError:
        raise
    except (KeyError, TypeError, ValueError):
        fields_ok = False
    return HoldoutResults(True, True, True, True, fields_ok)
