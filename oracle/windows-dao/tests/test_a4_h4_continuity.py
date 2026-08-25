"""Focused AMB-12 checkpoint-continuity contracts for A4 H4."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "oracle" / "windows-dao" / "scripts"))

import a4_layer_h4  # noqa: E402
from a4_layer_h4 import (  # noqa: E402
    OPERATIONS,
    CatalogRecordLocator,
    CheckpointRecordEvidence,
    H4Candidate,
    OperationRecord,
    StructuralDerivation,
    applicable_operation_checkpoints,
    derive_encoding,
    derive_structural_fields,
    select_operation_records,
)
from a4_layers import operation_records  # noqa: E402
from a4_layer_h4_fields import expected_operation_name  # noqa: E402
import a4_layer_h4_holdout  # noqa: E402
from a4_model import A4AnalysisError  # noqa: E402
from a4_spec import CHECKPOINT_IDS, ROLE_BINDINGS  # noqa: E402


_ROOT_OPERATIONS = {
    "T1_CREATE_ID",
    "T2_CREATE",
    "T2_RECREATE",
    "T3_CREATE",
    "T4_CREATE",
}
_TABLE_ROLES = {
    operation: operation.split("_", 1)[0]
    for operation in _ROOT_OPERATIONS
}
_KINDS = {"table": 0x11, "field": 0x22, "index": 0x33}


def _kind(operation: str) -> int:
    if operation == "T1_ADD_TEXT":
        return _KINDS["field"]
    if operation == "T1_ADD_INDEX":
        return _KINDS["index"]
    return _KINDS["table"]


def _record_row(replica: int, operation: str, identifier: int) -> bytes:
    name = expected_operation_name(
        replica, operation, ROLE_BINDINGS, _TABLE_ROLES
    ).encode("cp1252", errors="strict")
    return bytes((identifier, _kind(operation), len(name))) + name


def _locator(page: int, row: bytes) -> CatalogRecordLocator:
    return CatalogRecordLocator(page, 0, 2048 - len(row), 2048)


def _records(
    mutation: tuple[str, str, int, int] | None = None,
) -> tuple[OperationRecord, ...]:
    records: list[OperationRecord] = []
    for ordinal, operation in enumerate(OPERATIONS):
        base = _record_row(1, operation, 0x21 + ordinal)
        rows: dict[str, tuple[CatalogRecordLocator, bytes]] = {}
        for checkpoint in applicable_operation_checkpoints(operation):
            row = bytearray(base)
            if mutation is not None:
                selected_operation, selected_checkpoint, offset, value = mutation
                if (operation, checkpoint) == (
                    selected_operation,
                    selected_checkpoint,
                ):
                    row[offset] = value
            payload = bytes(row)
            rows[checkpoint] = (_locator(10 + ordinal, payload), payload)
        records.append(OperationRecord.from_checkpoint_rows(1, operation, rows))
    return tuple(records)


class A4H4ContinuityTests(unittest.TestCase):
    def test_encoding_requires_one_coherent_identifier_assignment(self) -> None:
        evidence = tuple(
            SimpleNamespace(
                record=SimpleNamespace(operation_id=operation),
                expected_name="x",
                occurrences=(
                    SimpleNamespace(index=0, encoded_hex="67"),
                    SimpleNamespace(index=1, encoded_hex="62"),
                ),
            )
            for operation in OPERATIONS
        )
        model = {
            "identifier_lifecycle": (
                "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2"
            ),
        }
        candidate = H4Candidate(
            "h4_structural_field",
            model,
            (
                {
                    "replica": 1,
                    "compatible_occurrences_by_operation": [
                        {
                            "operation_id": operation,
                            "compatible_occurrence_bitmap_hex": "03",
                        }
                        for operation in OPERATIONS
                    ],
                },
            ),
        )
        structural = StructuralDerivation(1, "digest", evidence, (candidate,))

        def decode(group, occurrence, _model):
            operation = group.record.operation_id
            return SimpleNamespace(
                occurrence=occurrence,
                identifier=(
                    1
                    if occurrence.index == 0
                    else 10 + OPERATIONS.index(operation)
                ),
                stored_length=1,
            )

        with patch.object(
            a4_layer_h4, "ENCODING_CLASSES", ("test-class",)
        ), patch.object(
            a4_layer_h4, "_decode_for_model", side_effect=decode
        ), patch.object(
            a4_layer_h4,
            "encoding_class_matches",
            side_effect=lambda _class, _name, payload, _length: payload == b"g",
        ):
            with self.assertRaises(A4AnalysisError) as raised:
                derive_encoding(structural)
        self.assertEqual(
            raised.exception.predicate_id, "A4-H4-ENCODING-AMBIGUOUS"
        )
        self.assertEqual(raised.exception.survivor_count, 0)

    def test_holdout_requires_one_coherent_structural_and_encoding_selection(self) -> None:
        records = tuple(
            SimpleNamespace(replica=3, operation_id=operation)
            for operation in OPERATIONS
        )
        groups = {}
        for index, record in enumerate(records):
            groups[record.operation_id] = SimpleNamespace(
                record=record,
                expected_name="x",
                occurrences=(
                    SimpleNamespace(index=0, encoded_hex="67"),
                    SimpleNamespace(index=1, encoded_hex="62"),
                ),
            )
        model = {
            "kind_mapping": _KINDS,
            "identifier_lifecycle": (
                "stable_for_same_operation_instance_and_distinct_for_t2_v1_v2"
            ),
        }
        structural = H4Candidate("h4_structural_field", model)
        final = H4Candidate(
            "h4_final_encoded_field",
            {
                "structural_model_id": structural.canonical_model_id,
                "encoding_length_equivalence_class": "test-class",
            },
        )

        def decode(group, occurrence, _model):
            operation = group.record.operation_id
            kind = _kind(operation)
            identifier = 1 if occurrence.index == 0 else 10 + OPERATIONS.index(operation)
            return SimpleNamespace(
                occurrence=occurrence,
                kind=kind,
                identifier=identifier,
                stored_length=1,
            )

        with patch.object(
            a4_layer_h4_holdout,
            "scan_name_occurrences",
            side_effect=lambda record: groups[record.operation_id],
        ), patch.object(
            a4_layer_h4_holdout, "_decode_for_model", side_effect=decode
        ), patch.object(
            a4_layer_h4_holdout,
            "encoding_class_matches",
            side_effect=lambda _class, _name, payload, _length: payload == b"g",
        ):
            self.assertFalse(
                a4_layer_h4_holdout.predicts_fields(structural, final, records)
            )

    def test_record_enumeration_is_name_blind_and_bounded(self) -> None:
        self.assertNotIn("expected_operation_name", operation_records.__code__.co_names)
        self.assertNotIn("encoded_patterns", operation_records.__code__.co_names)
        records = _records()
        overflowing = (records[0],) * 4_097 + records[1:]
        with self.assertRaises(A4AnalysisError) as raised:
            select_operation_records(
                1, H4Candidate("h4_catalog_root", {}), overflowing
            )
        self.assertEqual(raised.exception.predicate_id, "A4-RESOURCE-BOUND")

    def test_applicability_is_exact_and_plan_ordered(self) -> None:
        for operation in OPERATIONS:
            checkpoints = applicable_operation_checkpoints(operation)
            self.assertEqual(checkpoints[0], operation)
            self.assertEqual(len(checkpoints), len(set(checkpoints)))
            self.assertEqual(
                checkpoints,
                tuple(
                    checkpoint
                    for checkpoint in CHECKPOINT_IDS
                    if checkpoint in checkpoints
                ),
            )
        self.assertEqual(
            applicable_operation_checkpoints("T2_CREATE"), ("T2_CREATE",)
        )

    def test_constructor_rejects_missing_extra_duplicate_or_reordered_evidence(self) -> None:
        operation = "T4_CREATE"
        expected = applicable_operation_checkpoints(operation)
        row = _record_row(1, operation, 0x27)
        locator = _locator(16, row)
        complete = {checkpoint: (locator, row) for checkpoint in expected}
        for changed in (
            {checkpoint: complete[checkpoint] for checkpoint in expected[:-1]},
            {**complete, "EMPTY": (locator, row)},
        ):
            with self.assertRaisesRegex(ValueError, "exact applicable checkpoint"):
                OperationRecord.from_checkpoint_rows(1, operation, changed)

        evidence = tuple(
            CheckpointRecordEvidence(checkpoint, locator, row)
            for checkpoint in expected
        )
        with self.assertRaisesRegex(ValueError, "exactly once in plan order"):
            OperationRecord(1, operation, tuple(reversed(evidence)))
        with self.assertRaisesRegex(ValueError, "exactly once in plan order"):
            OperationRecord(1, operation, evidence[:-1] + (evidence[-2],))

    def test_structural_fit_accepts_value_equivalence_at_every_checkpoint(self) -> None:
        result = derive_structural_fields(1, _records())
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].instance_bindings[0][
                "value_equivalent_tuple_count"
            ],
            2,
        )

    def test_structural_fit_rejects_changed_kind_identifier_or_length(self) -> None:
        operation = "T4_CREATE"
        checkpoint = applicable_operation_checkpoints(operation)[-1]
        for label, offset, value in (
            ("kind", 1, 0x12),
            ("identifier", 0, 0x55),
            ("stored length", 2, 9),
        ):
            with self.subTest(field=label):
                with self.assertRaises(A4AnalysisError) as raised:
                    derive_structural_fields(
                        1, _records((operation, checkpoint, offset, value))
                    )
                self.assertEqual(
                    raised.exception.predicate_id, "A4-H4-FIELD-MODEL-NONE"
                )


if __name__ == "__main__":
    unittest.main()
