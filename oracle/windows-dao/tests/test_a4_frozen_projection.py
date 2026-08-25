"""Adversarial closure checks for A4 frozen physical evidence."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import a4_layers  # noqa: E402
from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_analysis_state import (  # noqa: E402
    _QUALIFIED_PAGE_MARKER,
    _TRANSCRIPT_CATEGORY_CODES,
    freeze_derivation,
    resume_derivation,
)
from a4_layer_h4 import (  # noqa: E402
    CatalogRecordLocator,
    CheckpointRecordEvidence,
    OperationRecord,
)
from a4_layers import derive_layers  # noqa: E402
from a4_model import WorkLedger  # noqa: E402
from a4_spec import PLAN, canonical_json_bytes  # noqa: E402
from a4_terminal import DerivationTerminal  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402
from test_a4_frozen_terminal import _default_analysis  # noqa: E402


def _rehash(document: dict[str, object]) -> tuple[bytes, str]:
    payload = canonical_json_bytes(document)
    return payload, hashlib.sha256(payload).hexdigest()


def _remove_marker(
    document: dict[str, object], identity: tuple[int, str, int]
) -> None:
    replica, checkpoint, page = identity
    for rows in document["transcripts"].values():
        rows[:] = [
            row
            for row in rows
            if not (
                row["checkpoint_id"] == checkpoint
                and row["page"] == page
                and (raw := bytes.fromhex(row["detail_hex"])).startswith(
                    _QUALIFIED_PAGE_MARKER
                )
                and raw[len(_QUALIFIED_PAGE_MARKER)] == replica
            )
        ]


@lru_cache(maxsize=2)
def _record_multiple_frozen(*, identical_rows: bool):
    checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
    ledger = WorkLedger()
    original = a4_layers.operation_records

    def duplicate_first(view, root, deltas, h2, work):
        candidates = original(view, root, deltas, h2, work)
        base = candidates[0]
        evidence = []
        for row in base.checkpoint_evidence:
            locator = row.locator
            if identical_rows:
                alternate = CatalogRecordLocator(
                    locator.page,
                    locator.row + 1,
                    locator.row_start,
                    locator.row_end,
                )
                row_bytes = row.row_bytes
            else:
                alternate = CatalogRecordLocator(
                    locator.page,
                    locator.row,
                    locator.row_start + 1,
                    locator.row_end,
                )
                row_bytes = row.row_bytes[1:]
            evidence.append(
                CheckpointRecordEvidence(row.checkpoint_id, alternate, row_bytes)
            )
        extra = OperationRecord(base.replica, base.operation_id, tuple(evidence))
        return tuple(candidates) + (extra,)

    with patch.object(
        a4_layers, "operation_records", side_effect=duplicate_first
    ):
        terminal = derive_layers(checked, ledger)
    if not isinstance(terminal, DerivationTerminal):
        raise AssertionError("record-multiple fixture did not reach its terminal")
    return freeze_derivation(checked, terminal, ledger)


class A4FrozenProjectionTests(unittest.TestCase):
    def test_resume_rejects_removed_h4_root_qualified_page(self) -> None:
        frozen = _default_analysis().frozen
        document = json.loads(frozen.canonical_bytes)
        root = document["layers"]["h4_catalog_bootstrap"]["root_result"][
            "candidates"
        ][0]
        binding = root["instance_bindings"][0]
        identity = (binding["replica"], "T1_ADD_TEXT", binding["tdef_page"])
        document["qualified_pages"] = [
            row
            for row in document["qualified_pages"]
            if (row["replica"], row["checkpoint_id"], row["page_number"])
            != identity
        ]
        _remove_marker(document, identity)
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "omits retained physical evidence"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_resume_rejects_removed_h4_terminal_record_evidence(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        ledger = WorkLedger()
        original = a4_layers.operation_records
        first = PLAN["candidate_grammars"]["h4"]["operation_binding_order"][0]

        def omit_first_operation(view, root, deltas, h2, work):
            return tuple(
                candidate
                for candidate in original(view, root, deltas, h2, work)
                if candidate.operation_id != first
            )

        with patch.object(
            a4_layers, "operation_records", side_effect=omit_first_operation
        ):
            terminal = derive_layers(checked, ledger)
        frozen = freeze_derivation(checked, terminal, ledger)
        document = json.loads(frozen.canonical_bytes)
        candidate = document["layers"]["h4_catalog_bootstrap"][
            "structural_result"
        ]["candidates"][0]
        model = candidate["model"]
        locator = model["canonical_record_locator"]
        identity = (model["replica"], model["operation_id"], locator["page"])
        document["qualified_pages"] = [
            row
            for row in document["qualified_pages"]
            if (row["replica"], row["checkpoint_id"], row["page_number"])
            != identity
        ]
        for rows in document["transcripts"].values():
            rows[:] = [
                row
                for row in rows
                if (row["checkpoint_id"], row["page"]) != identity[1:]
            ]
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "omits retained physical evidence"):
            resume_derivation(payload, digest)

    def test_resume_rejects_h4_root_marker_moved_to_locator_stage(self) -> None:
        frozen = _default_analysis().frozen
        document = json.loads(frozen.canonical_bytes)
        root = document["layers"]["h4_catalog_bootstrap"]["root_result"][
            "candidates"
        ][0]
        binding = root["instance_bindings"][0]
        identity = (binding["replica"], "T1_ADD_TEXT", binding["tdef_page"])
        rows = document["transcripts"]["catalog_roots"]
        marker = next(
            row
            for row in rows
            if row["checkpoint_id"] == identity[1]
            and row["page"] == identity[2]
            and bytes.fromhex(row["detail_hex"]).startswith(_QUALIFIED_PAGE_MARKER)
            and bytes.fromhex(row["detail_hex"])[len(_QUALIFIED_PAGE_MARKER)]
            == identity[0]
        )
        rows.remove(marker)
        raw = bytearray.fromhex(marker["detail_hex"])
        raw[len(_QUALIFIED_PAGE_MARKER) + 1] = _TRANSCRIPT_CATEGORY_CODES["locators"]
        marker["detail_hex"] = raw.hex()
        marker["kind"] = "locator"
        document["transcripts"]["locators"].append(marker)
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "marker differs from candidate stage"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_resume_rejects_removed_occurrence_record_transcript(self) -> None:
        frozen = _default_analysis().frozen
        document = json.loads(frozen.canonical_bytes)
        occurrence = json.loads(frozen.occurrence_evidence_bytes)
        operation = occurrence["replica_groups"][0]["operation_bindings"][0]
        pair = (
            operation["operation_id"],
            operation["canonical_record_locator"]["page"],
        )
        rows = document["transcripts"]["catalog_fields"]
        rows[:] = [
            row
            for row in rows
            if not (
                (row["checkpoint_id"], row["page"]) == pair
                and not bytes.fromhex(row["detail_hex"]).startswith(
                    _QUALIFIED_PAGE_MARKER
                )
            )
        ]
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "transcript cardinality differs"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_resume_rejects_wrong_record_locator_length_multiset(self) -> None:
        frozen = _default_analysis().frozen
        document = json.loads(frozen.canonical_bytes)
        occurrence = json.loads(frozen.occurrence_evidence_bytes)
        operation = occurrence["replica_groups"][0]["operation_bindings"][0]
        pair = (
            operation["operation_id"],
            operation["canonical_record_locator"]["page"],
        )
        rows = document["transcripts"]["catalog_fields"]
        matching = next(
            row
            for row in rows
            if (row["checkpoint_id"], row["page"]) == pair
            and not bytes.fromhex(row["detail_hex"]).startswith(_QUALIFIED_PAGE_MARKER)
        )
        matching["detail_hex"] = matching["detail_hex"][:-2]
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "transcript cardinality differs"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_record_multiple_uses_bounded_page_markers_not_raw_rows(self) -> None:
        frozen = _record_multiple_frozen(identical_rows=True)
        resumed = resume_derivation(frozen.canonical_bytes, frozen.sha256)
        result = resumed["layers"]["h4_catalog_bootstrap"]["structural_result"]
        pairs: dict[tuple[str, int], int] = {}
        for candidate in result["candidates"]:
            model = candidate["model"]
            locator = model["canonical_record_locator"]
            pair = (model["operation_id"], locator["page"])
            pairs[pair] = pairs.get(pair, 0) + 1
        pair = next(pair for pair, count in pairs.items() if count == 2)
        replica = next(
            candidate["model"]["replica"]
            for candidate in result["candidates"]
            if (
                candidate["model"]["operation_id"],
                candidate["model"]["canonical_record_locator"]["page"],
            ) == pair
        )
        matching = [
            bytes.fromhex(row["detail_hex"])
            for row in resumed["transcripts"]["catalog_fields"]
            if (row["checkpoint_id"], row["page"]) == pair
        ]
        self.assertTrue(all(raw.startswith(_QUALIFIED_PAGE_MARKER) for raw in matching))
        self.assertTrue(any(
            raw[len(_QUALIFIED_PAGE_MARKER)] == replica for raw in matching
        ))


if __name__ == "__main__":
    unittest.main()
