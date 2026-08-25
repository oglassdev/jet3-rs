"""Frozen H1-H3 adapter for A4 catalog-root observations."""

from __future__ import annotations

import hashlib
from typing import Sequence

from a4_catalog_inventory import row_bounds
from a4_layer_decoding import decode_locator, set_bits, type0_owned
from a4_layer_h1 import H1ReplicaCandidate
from a4_layer_h2 import H2ReplicaCandidate
from a4_layer_h3 import (
    BITMAP_BITS,
    H3Candidate,
    MAX_ADMITTED_PAGES,
    SlotObservation,
    TraversalObservation,
    admitted_pages,
)
from a4_layer_h4 import CatalogRootObservation
from a4_model import A4AnalysisError, QualifiedPage, View, WorkLedger
from a4_spec import CHECKPOINT_IDS


def _stream_fingerprint(
    view: View, checkpoint: str, pages: frozenset[int]
) -> str:
    hasher = hashlib.sha256()
    for page in sorted(pages):
        digest = view.hash_at(checkpoint, page)
        if digest is None:
            raise ValueError("A4 admitted stream names an absent page")
        hasher.update(page.to_bytes(4, "little"))
        hasher.update(bytes.fromhex(digest))
    return hasher.hexdigest()


def catalog_root_observations(
    view: View,
    qualified_tdef_pages: Sequence[int],
    h1: H1ReplicaCandidate,
    h2: H2ReplicaCandidate,
    h3: H3Candidate,
    ledger: WorkLedger,
) -> tuple[CatalogRootObservation, ...]:
    """Traverse EMPTY-extant tag-02 pages with only the frozen H1-H3 model."""
    output: list[CatalogRootObservation] = []
    for tdef_page in qualified_tdef_pages:
        empty = view.page_optional("EMPTY", tdef_page)
        if empty is None or empty[0] != 0x02:
            continue
        locators = tuple(
            decode_locator(empty[offset : offset + 4], h1.layout)
            for offset in h1.locator_offsets
        )
        admitted_by_checkpoint: dict[str, frozenset[int]] = {}
        fingerprints: dict[str, str] = {}
        valid: set[str] = set()
        invalid_prefix = False
        for checkpoint in CHECKPOINT_IDS:
            if invalid_prefix:
                admitted_by_checkpoint[checkpoint] = frozenset()
                fingerprints[checkpoint] = hashlib.sha256(b"").hexdigest()
                continue
            try:
                payload = view.page(checkpoint, tdef_page)
                current = tuple(
                    decode_locator(payload[offset : offset + 4], h1.layout)
                    for offset in h1.locator_offsets
                )
                if current != locators:
                    raise ValueError("system locator identity changed")
                rows: list[bytes] = []
                for locator_ordinal, (page, row_ordinal) in enumerate(current):
                    map_payload = view.page(checkpoint, page)
                    if view.replica in (1, 2):
                        ledger.record_qualified_page(
                            QualifiedPage(view.replica, checkpoint, page),
                            discriminator=(
                                "system_map_page",
                                tdef_page,
                                locator_ordinal,
                            ),
                        )
                    bounds = row_bounds(map_payload, h2.row_mask)
                    start, end = bounds[row_ordinal]
                    rows.append(map_payload[start:end])
                owned_row = rows[h2.owned_in_use_locator_ordinal]
                if owned_row[0] == 0:
                    owned = type0_owned(
                        owned_row,
                        h2.polarity,
                        maximum=MAX_ADMITTED_PAGES,
                    )
                elif owned_row[0] == 1 and (len(owned_row) - 1) % 4 == 0:
                    references = tuple(
                        int.from_bytes(owned_row[start : start + 4], "little")
                        for start in range(1, len(owned_row), 4)
                    )
                    if view.replica in (1, 2):
                        ledger.charge_qualified(
                            "type_1_slots",
                            QualifiedPage(
                                view.replica,
                                checkpoint,
                                current[h2.owned_in_use_locator_ordinal][0],
                            ),
                            len(references),
                            discriminator=(
                                "system",
                                h2.owned_in_use_locator_ordinal,
                            ),
                        )
                    slots: list[SlotObservation] = []
                    referenced_pages: set[int] = set()
                    for slot_ordinal, reference in enumerate(references):
                        if reference == 0:
                            slots.append(SlotObservation(slot_ordinal, 0, None))
                            continue
                        if (
                            reference not in referenced_pages
                            and len(referenced_pages) == MAX_ADMITTED_PAGES
                        ):
                            raise A4AnalysisError(
                                "A4-RESOURCE-BOUND",
                                detail=(
                                    "system traversal references more than "
                                    "16 qualified pages"
                                ),
                            )
                        referenced_pages.add(reference)
                        bitmap = view.page_optional(checkpoint, reference)
                        if view.replica in (1, 2) and bitmap is not None:
                            ledger.record_qualified_page(
                                QualifiedPage(view.replica, checkpoint, reference),
                                discriminator=(
                                    "system_reference_tag",
                                    current[h2.owned_in_use_locator_ordinal][0],
                                    slot_ordinal,
                                ),
                            )
                        if bitmap is None or bitmap[0] != 0x05:
                            raise ValueError(
                                "system allocation reference is absent or not tag 05"
                            )
                        if view.replica in (1, 2):
                            ledger.charge_qualified(
                                "type_0_and_tag_05_bitmap_bits",
                                QualifiedPage(view.replica, checkpoint, reference),
                                BITMAP_BITS,
                            )
                        slots.append(
                            SlotObservation(
                                slot_ordinal,
                                reference,
                                bitmap[0],
                                set_bits(bitmap[4:]),
                            )
                        )
                    traversal = TraversalObservation(
                        view.replica,
                        checkpoint,
                        current[h2.owned_in_use_locator_ordinal][0],
                        "type_1",
                        "system-catalog",
                        allocation_role="owned_in_use",
                        slots=tuple(slots),
                        locator_ordinal=h2.owned_in_use_locator_ordinal,
                    )
                    owned = admitted_pages(
                        traversal,
                        str(h3.model["base_formula"]),
                        maximum=MAX_ADMITTED_PAGES,
                    )
                else:
                    raise ValueError(
                        "system allocation row has an unsupported representation"
                    )
                if any(page >= view.page_count(checkpoint) for page in owned):
                    raise ValueError("system traversal admits an absent page")
                admitted_by_checkpoint[checkpoint] = owned
                fingerprints[checkpoint] = _stream_fingerprint(
                    view, checkpoint, owned
                )
                valid.add(checkpoint)
            except A4AnalysisError:
                raise
            except (IndexError, KeyError, ValueError):
                admitted_by_checkpoint[checkpoint] = frozenset()
                fingerprints[checkpoint] = hashlib.sha256(b"").hexdigest()
                invalid_prefix = True
        output.append(
            CatalogRootObservation(
                view.replica,
                tdef_page,
                h1.locator_offsets,
                empty[0],
                frozenset(valid),
                fingerprints,
                admitted_by_checkpoint,
            )
        )
    return tuple(output)
