#!/usr/bin/env python3
"""Bounded, union-accounted catalog-row inventory for A4 H4."""

from __future__ import annotations

from collections import Counter
from typing import Mapping

from a4_layer_h2 import H2ReplicaCandidate
from a4_layer_h4 import (
    OPERATIONS,
    CatalogRecordLocator,
    CatalogRootObservation,
    OperationRecord,
    applicable_operation_checkpoints,
)
from a4_model import A4AnalysisError, QualifiedPage, View, WorkLedger
from a4_spec import BOUNDS, CHECKPOINT_IDS, CHECKPOINT_ORDINALS, PAGE_SIZE


MAX_CANDIDATES = int(BOUNDS["max_candidate_models"])
MAX_QUALIFIED_PAGES = int(BOUNDS["max_qualified_pages_per_submodel"])
MAX_CACHE_PAGES = MAX_QUALIFIED_PAGES * (
    2 * len(OPERATIONS)
    + sum(len(applicable_operation_checkpoints(operation)) - 1 for operation in OPERATIONS)
)


def row_bounds(payload: bytes, mask: int) -> tuple[tuple[int, int], ...]:
    """Decode one complete tag-01 row directory under a frozen H2 mask."""
    if len(payload) != PAGE_SIZE or payload[0] != 0x01:
        raise ValueError("A4 frozen row page is invalid")
    count = int.from_bytes(payload[8:10], "little")
    directory_end = 10 + 2 * count
    if directory_end > PAGE_SIZE:
        raise ValueError("A4 row directory exceeds the page")
    result: list[tuple[int, int]] = []
    end = PAGE_SIZE
    for ordinal in range(count):
        raw = int.from_bytes(
            payload[10 + 2 * ordinal : 12 + 2 * ordinal], "little"
        )
        if raw & 0xC000:
            raise ValueError("A4 frozen row carries deleted/overflow flags")
        start = raw & mask
        if not directory_end <= start < end <= PAGE_SIZE:
            raise ValueError("A4 frozen row directory is inconsistent")
        result.append((start, end))
        end = start
    return tuple(result)


class CatalogInventory:
    """Cache bounded page inventories and charge each inspected row once."""

    def __init__(self, view: View, mask: int, ledger: WorkLedger) -> None:
        self._view = view
        self._mask = mask
        self._ledger = ledger
        self._pages: dict[
            tuple[str, int], tuple[bytes, tuple[tuple[int, int], ...]]
        ] = {}
        self._inventories: dict[
            tuple[str, int], tuple[tuple[CatalogRecordLocator | None, bytes], ...]
        ] = {}

    def _page(
        self, checkpoint: str, page: int
    ) -> tuple[bytes, tuple[tuple[int, int], ...]]:
        key = (checkpoint, page)
        cached = self._pages.get(key)
        if cached is not None:
            return cached
        if len(self._pages) >= MAX_CACHE_PAGES:
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND", detail="H4 catalog inventory page bound exceeded"
            )
        payload = self._view.page(checkpoint, page)
        if self._view.replica in (1, 2):
            self._ledger.record_qualified_page(
                QualifiedPage(self._view.replica, checkpoint, page),
                discriminator="catalog_page",
            )
        cached = payload, row_bounds(payload, self._mask)
        self._pages[key] = cached
        return cached

    def _row(
        self,
        checkpoint: str,
        page: int,
        ordinal: int,
        payload: bytes,
        bounds: tuple[tuple[int, int], ...],
    ) -> tuple[CatalogRecordLocator | None, bytes]:
        start, end = bounds[ordinal]
        identity = (
            (QualifiedPage(self._view.replica, checkpoint, page), ordinal)
            if self._view.replica in (1, 2)
            else ("holdout", self._view.replica, checkpoint, page, ordinal)
        )
        self._ledger.charge_once(
            "catalog_raw_rows",
            identity,
        )
        locator = (
            CatalogRecordLocator(page, ordinal, start, end)
            if ordinal <= 255
            else None
        )
        return locator, payload[start:end]

    def all_rows(
        self, checkpoint: str, page: int
    ) -> tuple[tuple[CatalogRecordLocator | None, bytes], ...]:
        key = (checkpoint, page)
        cached = self._inventories.get(key)
        if cached is not None:
            return cached
        payload, bounds = self._page(checkpoint, page)
        cached = tuple(
            self._row(checkpoint, page, ordinal, payload, bounds)
            for ordinal in range(len(bounds))
        )
        self._inventories[key] = cached
        return cached

    def row_at(
        self, checkpoint: str, page: int, ordinal: int
    ) -> tuple[CatalogRecordLocator | None, bytes] | None:
        payload, bounds = self._page(checkpoint, page)
        if not 0 <= ordinal < len(bounds):
            return None
        return self._row(checkpoint, page, ordinal, payload, bounds)


def operation_records(
    view: View,
    root: CatalogRootObservation,
    deltas: Mapping[str, frozenset[int]],
    h2: H2ReplicaCandidate,
    ledger: WorkLedger,
) -> tuple[OperationRecord, ...]:
    """Build complete same-locator operation records with bounded row scans."""
    for checkpoint in CHECKPOINT_IDS:
        if len(root.admitted_pages_by_checkpoint[checkpoint]) > MAX_QUALIFIED_PAGES:
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND",
                detail="H4 catalog admitted-page bound exceeded",
            )
    inventory = CatalogInventory(view, h2.row_mask, ledger)
    candidates: list[OperationRecord] = []
    for operation in OPERATIONS:
        ordinal = CHECKPOINT_ORDINALS[operation]
        before_checkpoint = CHECKPOINT_IDS[ordinal - 1]
        admitted = root.admitted_pages_by_checkpoint[operation]
        for page in sorted(deltas[operation] & admitted):
            before = (
                inventory.all_rows(before_checkpoint, page)
                if page < view.page_count(before_checkpoint)
                else ()
            )
            after = inventory.all_rows(operation, page)
            remaining = Counter(row for _locator, row in before)
            for locator, row in after:
                if remaining[row]:
                    remaining[row] -= 1
                    continue
                if locator is None:
                    continue
                checkpoint_rows = {operation: (locator, row)}
                for later in applicable_operation_checkpoints(operation)[1:]:
                    if locator.page not in root.admitted_pages_by_checkpoint[later]:
                        break
                    match = inventory.row_at(later, locator.page, locator.row)
                    if match is None or match[0] is None:
                        break
                    checkpoint_rows[later] = match
                else:
                    if len(candidates) >= MAX_CANDIDATES:
                        raise A4AnalysisError(
                            "A4-RESOURCE-BOUND",
                            detail="constructed H4 operation-record candidate 4097",
                        )
                    candidates.append(
                        OperationRecord.from_checkpoint_rows(
                            view.replica, operation, checkpoint_rows
                        )
                    )
    return tuple(candidates)
