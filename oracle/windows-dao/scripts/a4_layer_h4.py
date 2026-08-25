#!/usr/bin/env python3
"""Bounded, name-deferred A4 H4 catalog-root and field-model analysis."""
from __future__ import annotations
import itertools
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from a4_model import (A4AnalysisError, QualifiedPage, WorkLedger,
                      canonical_candidate_id, canonical_model_id, canonical_object_id)
from a4_layer_h4_fields import (
    bitmap_hex,
    bitmap_members,
    encoded_patterns,
    encoding_class_matches,
    expected_operation_name,
    identifier_assignment,
    identifier_assignment_exists,
    kind_mappings,
    ranges_are_disjoint,
    value_equivalence_key,
)
from a4_measurements import MeasurementRecorder, measure
from a4_spec import (
    BOUNDS,
    CANDIDATE_GRAMMARS,
    CHECKPOINT_IDS,
    LIFECYCLE_RANGES,
    PAGE_SIZE,
    PLAN,
    ROLE_BINDINGS,
    canonical_json_bytes,
    sha256_hex,
)
_GRAMMAR = CANDIDATE_GRAMMARS["h4"]
OPERATIONS = tuple(_GRAMMAR["operation_binding_order"])
ROOT_OPERATIONS = ("T1_CREATE_ID", "T2_CREATE", "T2_RECREATE", "T3_CREATE", "T4_CREATE")
ROOT_SIGNATURE = tuple(_GRAMMAR["catalog_root_selection_signatures"])[0]
KIND_START_DELTAS = tuple(int(value) for value in _GRAMMAR["kind_start_deltas"])
KIND_WIDTHS = tuple(int(value) for value in _GRAMMAR["kind_widths"])
IDENTIFIER_WIDTHS = tuple(int(value) for value in _GRAMMAR["identifier_widths"])
ENDIANNESS = tuple(_GRAMMAR["endianness"])
NAME_LENGTH_START_DELTAS = tuple(
    int(value) for value in _GRAMMAR["name_length_start_deltas"]
)
NAME_LENGTH_WIDTHS = tuple(int(value) for value in _GRAMMAR["name_length_widths"])
IDENTIFIER_LIFECYCLES = tuple(_GRAMMAR["identifier_lifecycle_relations"])
ENCODING_CLASSES = tuple(
    entry["id"] for entry in _GRAMMAR["name_length_equivalence_classes"]
)
MAX_CANDIDATES = int(BOUNDS["max_candidate_models"])
RAW_TUPLES_PER_OCCURRENCE = math.prod(map(len, (KIND_START_DELTAS, KIND_WIDTHS, IDENTIFIER_WIDTHS, ENDIANNESS, NAME_LENGTH_START_DELTAS, NAME_LENGTH_WIDTHS))) * 6 * len(IDENTIFIER_LIFECYCLES)
_OBJECT_KIND = dict.fromkeys(("T1_CREATE_ID", "T2_CREATE", "T2_RECREATE", "T3_CREATE", "T4_CREATE"), "table")
_OBJECT_KIND.update({"T1_ADD_TEXT": "field", "T1_ADD_INDEX": "index"})
_TABLE_ROLE = {operation: operation.split("_", 1)[0] for operation in ROOT_OPERATIONS}
_TABLE_INSTANCE_BY_CREATE = {value.first_checkpoint: key for key, value in LIFECYCLE_RANGES.items()}
_EXPECTED_SCHEMA = PLAN["tables"]["expected_schema_by_checkpoint"]
_FEATURE_BY_OPERATION = {"T1_ADD_TEXT": "payload", "T1_ADD_INDEX": "index"}
def applicable_operation_checkpoints(operation_id: str) -> tuple[str, ...]:
    """Return the exact plan-derived checkpoints where one record must persist."""
    if operation_id not in OPERATIONS:
        raise ValueError(f"unregistered H4 operation {operation_id!r}")
    instance_id = _TABLE_INSTANCE_BY_CREATE.get(operation_id)
    if instance_id is not None:
        lifecycle = LIFECYCLE_RANGES[instance_id]
        return CHECKPOINT_IDS[lifecycle.first_ordinal : lifecycle.last_ordinal + 1]
    feature = _FEATURE_BY_OPERATION.get(operation_id)
    if feature is None:
        raise ValueError(f"H4 operation {operation_id!r} has no applicability rule")
    checkpoints = tuple(checkpoint for checkpoint in CHECKPOINT_IDS if any(
            token.split(":", 1)[0] == "T1"
            and feature in token.rsplit(":", 1)[-1].split("+")
            for token in _EXPECTED_SCHEMA[checkpoint]
        ))
    if not checkpoints or checkpoints[0] != operation_id:
        raise ValueError(f"H4 operation {operation_id!r} has inconsistent plan coverage")
    return checkpoints
_APPLICABLE_CHECKPOINTS = {operation: applicable_operation_checkpoints(operation) for operation in OPERATIONS}
def _check_replica(replica: int, *, holdout: bool = False) -> None:
    allowed = (1, 2, 3) if holdout else (1, 2)
    if isinstance(replica, bool) or replica not in allowed:
        raise ValueError(f"H4 replica must be one of {allowed}")
def _page(value: int, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value < int(BOUNDS["max_final_pages_per_replica"])
    ):
        raise ValueError(f"{label} is outside the registered page bound")
    return value
def _ordered_bindings(bindings: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result = tuple(dict(binding) for binding in sorted(bindings, key=lambda item: item["replica"]))
    replicas = tuple(binding["replica"] for binding in result)
    if replicas not in ((), (1,), (2,), (1, 2), (3,)):
        raise ValueError("H4 bindings must be unique and replica ordered")
    return result
@dataclass(frozen=True)
class CatalogRootObservation:
    """Name-blind system-TDEF traversal for one physical root candidate."""
    replica: int
    tdef_page: int
    locator_offsets: tuple[int, int]
    tag_at_empty: int
    traversal_valid_checkpoints: frozenset[str]
    stream_fingerprint_by_checkpoint: Mapping[str, str]
    admitted_pages_by_checkpoint: Mapping[str, frozenset[int]]
    def __post_init__(self) -> None:
        _check_replica(self.replica, holdout=True)
        _page(self.tdef_page, "H4 root TDEF page")
        offsets = tuple(sorted(self.locator_offsets))
        if (
            len(offsets) != 2
            or len(set(offsets)) != 2
            or any(not 0 <= offset <= PAGE_SIZE - 4 for offset in offsets)
        ):
            raise ValueError("H4 root requires two distinct ascending locator offsets")
        object.__setattr__(self, "locator_offsets", offsets)
        checkpoints = frozenset(self.traversal_valid_checkpoints)
        if not checkpoints <= frozenset(CHECKPOINT_IDS):
            raise ValueError("H4 root contains an unknown traversal checkpoint")
        object.__setattr__(self, "traversal_valid_checkpoints", checkpoints)
        if set(self.stream_fingerprint_by_checkpoint) != set(CHECKPOINT_IDS):
            raise ValueError("H4 root stream fingerprints must cover every checkpoint")
        if set(self.admitted_pages_by_checkpoint) != set(CHECKPOINT_IDS):
            raise ValueError("H4 root admitted sets must cover every checkpoint")
        admitted: dict[str, frozenset[int]] = {}
        for checkpoint_id in CHECKPOINT_IDS:
            pages = frozenset(self.admitted_pages_by_checkpoint[checkpoint_id])
            for page in pages:
                _page(page, "H4 admitted page")
            admitted[checkpoint_id] = pages
        object.__setattr__(self, "admitted_pages_by_checkpoint", admitted)
@dataclass(frozen=True)
class H4Candidate:
    model_type: str
    model: Mapping[str, Any]
    instance_bindings: tuple[Mapping[str, Any], ...] = ()
    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_bindings", _ordered_bindings(self.instance_bindings))
    @property
    def canonical_model_id(self) -> str:
        return canonical_model_id(self.model_type, self.model)
    @property
    def canonical_candidate_id(self) -> str:
        if self.model_type == "h4_operation_record":
            return canonical_object_id(
                {"model_type": self.model_type, "model": dict(self.model)}
            )
        return canonical_candidate_id(self.model_type, self.model, self.instance_bindings)
    def document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "model_type": self.model_type,
            "canonical_candidate_id": self.canonical_candidate_id,
            "model": dict(self.model),
        }
        if self.model_type != "h4_operation_record":
            document["canonical_model_id"] = self.canonical_model_id
            document["instance_bindings"] = [dict(binding) for binding in self.instance_bindings]
        return document
def _changed_at(
    fingerprints: Mapping[str, str], checkpoint_id: str
) -> bool:
    ordinal = CHECKPOINT_IDS.index(checkpoint_id)
    if ordinal == 0:
        return False
    return fingerprints[CHECKPOINT_IDS[ordinal - 1]] != fingerprints[checkpoint_id]
def derive_catalog_root(
    replica: int,
    observations: Sequence[CatalogRootObservation],
    ledger: WorkLedger | None = None,
    measurements: MeasurementRecorder | None = None,
) -> H4Candidate:
    """Evaluate CATALOG-ROOT-NONE/MULTIPLE without inspecting name bytes."""
    _check_replica(replica)
    candidates: list[H4Candidate] = []
    for observation in observations:
        if observation.replica != replica:
            raise ValueError("H4 root observations must be replica-local")
        if ledger is not None:
            for checkpoint_id in CHECKPOINT_IDS:
                ledger.charge_qualified(
                    "catalog_root_signatures",
                    QualifiedPage(replica, checkpoint_id, observation.tdef_page),
                    discriminator=observation.locator_offsets,
                )
                if checkpoint_id not in observation.traversal_valid_checkpoints:
                    break
        if (
            observation.tag_at_empty != 0x02
            or observation.traversal_valid_checkpoints != frozenset(CHECKPOINT_IDS)
            or not all(
                _changed_at(observation.stream_fingerprint_by_checkpoint, operation)
                for operation in ROOT_OPERATIONS
            )
        ):
            continue
        model = {
            "root_selection_signature": ROOT_SIGNATURE,
            "locator_offsets": list(observation.locator_offsets),
        }
        candidates.append(
            H4Candidate(
                "h4_catalog_root",
                model,
                ({"replica": replica, "tdef_page": observation.tdef_page},),
            )
        )
    candidates.sort(key=lambda candidate: candidate.canonical_candidate_id)
    measure(measurements, "A4-H4-CATALOG-ROOT-NONE", len(candidates), bool(candidates), replica=replica)
    if not candidates:
        raise A4AnalysisError("A4-H4-CATALOG-ROOT-NONE")
    measure(measurements, "A4-H4-CATALOG-ROOT-MULTIPLE", len(candidates), len(candidates) == 1, replica=replica)
    if len(candidates) > 1:
        raise A4AnalysisError("A4-H4-CATALOG-ROOT-MULTIPLE", len(candidates))
    return candidates[0]
def merge_catalog_roots(left: H4Candidate, right: H4Candidate,
                        measurements: MeasurementRecorder | None = None) -> H4Candidate:
    """Bind the same invariant root model to both derivation replicas."""
    if left.model_type != "h4_catalog_root" or right.model_type != "h4_catalog_root":
        raise ValueError("H4 root merge requires root candidates")
    model_count = len({left.canonical_model_id, right.canonical_model_id})
    if model_count != 1:
        measure(measurements, "A4-H4-CATALOG-ROOT-MULTIPLE", model_count, False)
        raise A4AnalysisError("A4-H4-CATALOG-ROOT-MULTIPLE", 2)
    return H4Candidate(
        "h4_catalog_root", left.model, left.instance_bindings + right.instance_bindings
    )
def validate_isolated_deltas(
    replica: int,
    root: CatalogRootObservation,
    isolated_delta_pages: Mapping[str, frozenset[int]],
    measurements: MeasurementRecorder | None = None,
) -> None:
    """Apply SCHEMA-DELTA-OUTSIDE-OWNED before record restriction."""
    _check_replica(replica)
    if root.replica != replica:
        raise ValueError("H4 delta validation cannot cross replicas")
    if set(isolated_delta_pages) != set(OPERATIONS):
        raise ValueError("H4 isolated deltas must cover all seven operations")
    root_count = len((root,))
    for operation in OPERATIONS:
        delta = frozenset(isolated_delta_pages[operation])
        outside = sorted(delta - root.admitted_pages_by_checkpoint[operation])
        if outside:
            measure(measurements, "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED", root_count, False, replica=replica)
            raise A4AnalysisError(
                "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED",
                1,
                detail=f"replica {replica} {operation} page {outside[0]}",
            )
    measure(measurements, "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED", root_count, True, replica=replica)
@dataclass(frozen=True, order=True)
class CatalogRecordLocator:
    page: int
    row: int
    row_start: int
    row_end: int
    def __post_init__(self) -> None:
        _page(self.page, "H4 catalog-record page")
        if isinstance(self.row, bool) or not isinstance(self.row, int) or not 0 <= self.row <= 255:
            raise ValueError("H4 catalog row ordinal is outside the schema bound")
        if not 10 <= self.row_start < self.row_end <= PAGE_SIZE:
            raise ValueError("H4 catalog record is not a complete in-page row")
    def document(self) -> dict[str, int]:
        return {
            "page": self.page,
            "row": self.row,
            "row_start": self.row_start,
            "row_end": self.row_end,
        }
@dataclass(frozen=True)
class CheckpointRecordEvidence:
    """One complete row and locator at one applicable checkpoint."""
    checkpoint_id: str
    locator: CatalogRecordLocator
    row_bytes: bytes
    def __post_init__(self) -> None:
        if self.checkpoint_id not in CHECKPOINT_IDS:
            raise ValueError(f"unknown H4 checkpoint {self.checkpoint_id!r}")
        if not isinstance(self.row_bytes, bytes) or not self.row_bytes:
            raise ValueError("H4 checkpoint evidence must carry nonempty row bytes")
        if len(self.row_bytes) != self.locator.row_end - self.locator.row_start:
            raise ValueError("H4 checkpoint row length does not match its locator")
        if len(self.row_bytes) > PAGE_SIZE - 12:
            raise ValueError("H4 checkpoint row exceeds the complete-row bound")
@dataclass(frozen=True)
class OperationRecord:
    """One name-blind record tracked across every applicable checkpoint."""
    replica: int
    operation_id: str
    checkpoint_evidence: tuple[CheckpointRecordEvidence, ...]
    def __post_init__(self) -> None:
        _check_replica(self.replica, holdout=True)
        if self.operation_id not in OPERATIONS:
            raise ValueError(f"unregistered H4 operation {self.operation_id!r}")
        if any(not isinstance(row, CheckpointRecordEvidence) for row in self.checkpoint_evidence):
            raise ValueError("H4 checkpoint evidence has an invalid entry")
        expected = _APPLICABLE_CHECKPOINTS[self.operation_id]
        actual = tuple(row.checkpoint_id for row in self.checkpoint_evidence)
        if actual != expected:
            raise ValueError("H4 checkpoint evidence must cover every applicable checkpoint exactly once in plan order")
    @classmethod
    def from_checkpoint_rows(cls, replica: int, operation_id: str,
                             rows: Mapping[str, tuple[CatalogRecordLocator, bytes]]) -> OperationRecord:
        """Build checked evidence from a checkpoint-keyed orchestration mapping."""
        expected = applicable_operation_checkpoints(operation_id)
        if set(rows) != set(expected):
            raise ValueError("H4 checkpoint rows must contain the exact applicable checkpoint set")
        evidence = tuple(CheckpointRecordEvidence(checkpoint, *rows[checkpoint]) for checkpoint in expected)
        return cls(replica, operation_id, evidence)
    @property
    def locator(self) -> CatalogRecordLocator:
        return self.checkpoint_evidence[0].locator
    @property
    def row_bytes(self) -> bytes:
        return self.checkpoint_evidence[0].row_bytes
def select_operation_records(
    replica: int,
    root_candidate: H4Candidate,
    candidates: Sequence[OperationRecord],
    ledger: WorkLedger | None = None,
    measurements: MeasurementRecorder | None = None,
) -> tuple[OperationRecord, ...]:
    """Apply grouped NONE then MULTIPLE, still without a name anchor."""
    _check_replica(replica)
    if root_candidate.model_type != "h4_catalog_root":
        raise ValueError("H4 operation records require a root candidate")
    if len(candidates) > MAX_CANDIDATES:
        raise A4AnalysisError("A4-RESOURCE-BOUND", detail="constructed H4 operation-record candidate 4097")
    grouped = {operation: [] for operation in OPERATIONS}
    for candidate in candidates:
        if candidate.replica != replica:
            raise ValueError("H4 operation-record candidates must be replica-local")
        grouped[candidate.operation_id].append(candidate)
    counts = tuple(len(grouped[operation]) for operation in OPERATIONS)
    measure(measurements, "A4-H4-CATALOG-RECORD-NONE", min(counts), min(counts) >= 1, replica=replica)
    missing = [operation for operation in OPERATIONS if not grouped[operation]]
    if missing:
        raise A4AnalysisError(
            "A4-H4-CATALOG-RECORD-NONE",
            0,
            detail=f"missing {missing[0]}",
        )
    measure(measurements, "A4-H4-CATALOG-RECORD-MULTIPLE", max(counts), max(counts) <= 1, replica=replica)
    multiple = [operation for operation in OPERATIONS if len(grouped[operation]) > 1]
    if multiple:
        raise A4AnalysisError(
            "A4-H4-CATALOG-RECORD-MULTIPLE",
            max(len(grouped[operation]) for operation in multiple),
            detail=f"multiple {multiple[0]}",
        )
    return tuple(grouped[operation][0] for operation in OPERATIONS)
def operation_candidate(root: H4Candidate, record: OperationRecord) -> H4Candidate:
    """Serialize one physical, replica-qualified operation record."""
    model = {
        "replica": record.replica,
        "root_candidate_id": root.canonical_candidate_id,
        "operation_id": record.operation_id,
        "canonical_record_locator": record.locator.document(),
    }
    return H4Candidate("h4_operation_record", model)
@dataclass(frozen=True, order=True)
class NameOccurrence:
    index: int
    start: int
    encoded_hex: str
    encoding_ids: tuple[str, ...]
@dataclass(frozen=True)
class OperationEvidence:
    record: OperationRecord
    expected_name: str
    occurrences: tuple[NameOccurrence, ...]
def scan_name_occurrences(
    record: OperationRecord, ledger: WorkLedger | None = None
) -> OperationEvidence:
    """Perform the deferred encoding-neutral union scan for one record."""
    name = expected_operation_name(
        record.replica, record.operation_id, ROLE_BINDINGS, _TABLE_ROLE
    )
    occurrences: list[NameOccurrence] = []
    for pattern, encoding_ids in encoded_patterns(name):
        if ledger is not None:
            ledger.charge("encoding_union_anchor_bytes", len(record.row_bytes))
        start = 0
        while True:
            at = record.row_bytes.find(pattern, start)
            if at < 0:
                break
            occurrences.append(NameOccurrence(0, at, pattern.hex(), encoding_ids))
            start = at + len(pattern)
    unique = sorted(
        {(row.start, row.encoded_hex, row.encoding_ids) for row in occurrences},
        key=lambda row: (row[0], row[1], row[2]),
    )
    maximum = 290 if record.operation_id in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
    if len(unique) > maximum:
        raise A4AnalysisError("A4-RESOURCE-BOUND", detail="H4 occurrence identity bound exceeded")
    indexed = tuple(
        NameOccurrence(index, at, encoded_hex, encoding_ids)
        for index, (at, encoded_hex, encoding_ids) in enumerate(unique)
    )
    return OperationEvidence(record, name, indexed)
@dataclass(frozen=True)
class _DecodedOccurrence:
    occurrence: NameOccurrence
    kind: int
    identifier: int
    stored_length: int
def _decode_row_shape(
    row: bytes,
    expected_name: str,
    occurrence: NameOccurrence,
    shape: tuple[int, int, int, str, int, int],
) -> tuple[int, int, int] | None:
    kind_delta, kind_width, identifier_width, endian, length_delta, length_width = shape
    name_start = occurrence.start
    name_end = name_start + len(bytes.fromhex(occurrence.encoded_hex))
    kind_start = name_start - kind_delta
    identifier_start = kind_start - identifier_width
    length_start = name_start - length_delta
    ranges = ((identifier_start, kind_start), (kind_start, kind_start + kind_width),
              (length_start, length_start + length_width), (name_start, name_end))
    if any(start < 0 or end > len(row) for start, end in ranges):
        return None
    if not ranges_are_disjoint(ranges):
        return None
    kind = int.from_bytes(row[kind_start : kind_start + kind_width], endian)
    identifier = int.from_bytes(row[identifier_start:kind_start], endian)
    stored_length = int.from_bytes(row[length_start : length_start + length_width], endian)
    plausible = {len(expected_name.encode(encoding, errors="strict")) for encoding in ("cp1252", "utf-8")}
    plausible.add(len(expected_name))
    if stored_length not in plausible:
        return None
    return kind, identifier, stored_length
def _matching_checkpoint_values(
    row: bytes,
    expected_name: str,
    shape: tuple[int, int, int, str, int, int],
) -> frozenset[tuple[int, int, int]]:
    values: set[tuple[int, int, int]] = set()
    for pattern, encoding_ids in encoded_patterns(expected_name):
        start = 0
        while True:
            at = row.find(pattern, start)
            if at < 0:
                break
            decoded = _decode_row_shape(row, expected_name, NameOccurrence(0, at, pattern.hex(), encoding_ids), shape)
            if decoded is not None:
                values.add(decoded)
            start = at + len(pattern)
    return frozenset(values)
def _decode_shape(
    evidence: OperationEvidence,
    occurrence: NameOccurrence,
    shape: tuple[int, int, int, str, int, int],
) -> _DecodedOccurrence | None:
    decoded = _decode_row_shape(evidence.record.row_bytes, evidence.expected_name, occurrence, shape)
    if decoded is None:
        return None
    for checkpoint in evidence.record.checkpoint_evidence[1:]:
        if decoded not in _matching_checkpoint_values(checkpoint.row_bytes, evidence.expected_name, shape):
            return None
    kind, identifier, stored_length = decoded
    return _DecodedOccurrence(occurrence, kind, identifier, stored_length)
@dataclass(frozen=True)
class StructuralDerivation:
    replica: int
    evidence_sha256: str
    evidence: tuple[OperationEvidence, ...]
    candidates: tuple[H4Candidate, ...]
def _evidence_hash(evidence: Sequence[OperationEvidence]) -> str:
    document = {
        "replica": evidence[0].record.replica,
        "operations": [
            {
                "operation_id": group.record.operation_id,
                "locator": group.record.locator.document(),
                "occurrences": [
                    {
                        "occurrence_index": occurrence.index,
                        "start": occurrence.start,
                        "encoded_hex": occurrence.encoded_hex,
                        "encoding_ids": list(occurrence.encoding_ids),
                    }
                    for occurrence in group.occurrences
                ],
            }
            for group in evidence
        ],
    }
    return sha256_hex(canonical_json_bytes(document))
def derive_structural_fields(
    replica: int,
    records: Sequence[OperationRecord],
    ledger: WorkLedger | None = None,
    measurements: MeasurementRecorder | None = None,
    scanned_evidence: Sequence[OperationEvidence] | None = None,
) -> StructuralDerivation:
    _check_replica(replica)
    if tuple(record.operation_id for record in records) != OPERATIONS:
        raise ValueError("H4 records must be in registered operation order")
    evidence = (tuple(scanned_evidence) if scanned_evidence is not None else
                tuple(scan_name_occurrences(record, ledger) for record in records))
    occurrence_count = sum(len(group.occurrences) for group in evidence)
    if occurrence_count > int(BOUNDS["max_h4_occurrence_identities"]) // 2:
        raise A4AnalysisError("A4-RESOURCE-BOUND", detail="H4 replica occurrence bound exceeded")
    if ledger is not None:
        ledger.charge(
            "h4_name_length_structural_tuples",
            occurrence_count * RAW_TUPLES_PER_OCCURRENCE,
        )
    digest = _evidence_hash(evidence)
    equivalent: dict[
        tuple[Any, ...], tuple[dict[str, Any], dict[str, tuple[int, ...]], int]
    ] = {}
    shapes = itertools.product(
        KIND_START_DELTAS,
        KIND_WIDTHS,
        IDENTIFIER_WIDTHS,
        ENDIANNESS,
        NAME_LENGTH_START_DELTAS,
        NAME_LENGTH_WIDTHS,
    )
    for shape in shapes:
        decoded: dict[str, tuple[_DecodedOccurrence, ...]] = {}
        for group in evidence:
            rows = tuple(
                result
                for occurrence in group.occurrences
                if (result := _decode_shape(group, occurrence, shape)) is not None
            )
            if not rows:
                break
            decoded[group.record.operation_id] = rows
        if len(decoded) != len(OPERATIONS):
            continue
        mappings = kind_mappings(
            frozenset(row.kind for rows in decoded.values() for row in rows)
        )
        for mapping in mappings:
            kind_filtered = {
                operation: tuple(
                    row for row in decoded[operation] if row.kind == mapping[_OBJECT_KIND[operation]]
                )
                for operation in OPERATIONS
            }
            if not all(kind_filtered.values()):
                continue
            options = {
                operation: frozenset(row.identifier for row in kind_filtered[operation])
                for operation in OPERATIONS
            }
            for lifecycle in IDENTIFIER_LIFECYCLES:
                if not identifier_assignment_exists(OPERATIONS, options, lifecycle):
                    continue
                compatible: dict[str, tuple[int, ...]] = {}
                for operation in OPERATIONS:
                    indexes = tuple(
                        row.occurrence.index
                        for row in kind_filtered[operation]
                        if identifier_assignment_exists(
                            OPERATIONS, options, lifecycle, (operation, row.identifier)
                        )
                    )
                    if not indexes:
                        break
                    compatible[operation] = tuple(sorted(set(indexes)))
                if len(compatible) != len(OPERATIONS):
                    continue
                model = {
                    "kind_start_delta": shape[0],
                    "kind_width": shape[1],
                    "identifier_width": shape[2],
                    "endianness": shape[3],
                    "name_length_start_delta": shape[4],
                    "name_length_width": shape[5],
                    "kind_mapping": mapping,
                    "identifier_lifecycle": lifecycle,
                }
                key = value_equivalence_key(
                    OPERATIONS, kind_filtered, compatible, mapping, lifecycle
                )
                if key in equivalent:
                    first_model, first_compatible, count = equivalent[key]
                    equivalent[key] = (first_model, first_compatible, count + 1)
                else:
                    equivalent[key] = (model, compatible, 1)
                if len(equivalent) > MAX_CANDIDATES:
                    raise A4AnalysisError(
                        "A4-RESOURCE-BOUND", detail="constructed H4 candidate 4097"
                    )
    candidates: list[H4Candidate] = []
    for model, compatible, count in equivalent.values():
        binding = {
            "replica": replica,
            "occurrence_evidence_sha256": digest,
            "value_equivalent_tuple_count": count,
            "compatible_occurrences_by_operation": [
                {
                    "operation_id": operation,
                    "compatible_occurrence_count": len(compatible[operation]),
                    "compatible_occurrence_bitmap_hex": bitmap_hex(
                        compatible[operation],
                        290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254,
                    ),
                }
                for operation in OPERATIONS
            ],
        }
        candidates.append(H4Candidate("h4_structural_field", model, (binding,)))
    ordered = tuple(sorted(candidates, key=lambda row: row.canonical_candidate_id))
    measure(measurements, "A4-H4-FIELD-MODEL-NONE", len(ordered), bool(ordered), replica=replica)
    if not ordered:
        error = A4AnalysisError("A4-H4-FIELD-MODEL-NONE")
        error.candidates = ()
        raise error
    measure(measurements, "A4-H4-FIELD-MODEL-MULTIPLE", len(ordered), len(ordered) == 1, replica=replica)
    if len(ordered) > 1:
        error = A4AnalysisError("A4-H4-FIELD-MODEL-MULTIPLE", len(ordered))
        error.candidates = tuple(candidate.document() for candidate in ordered)
        raise error
    return StructuralDerivation(replica, digest, evidence, ordered)
def _decode_for_model(
    evidence: OperationEvidence, occurrence: NameOccurrence, model: Mapping[str, Any]
) -> _DecodedOccurrence | None:
    shape = (
        model["kind_start_delta"],
        model["kind_width"],
        model["identifier_width"],
        model["endianness"],
        model["name_length_start_delta"],
        model["name_length_width"],
    )
    return _decode_shape(evidence, occurrence, shape)
@dataclass(frozen=True)
class EncodedDerivation:
    structural: H4Candidate
    final: H4Candidate
def derive_encoding(
    structural: StructuralDerivation, ledger: WorkLedger | None = None,
    measurements: MeasurementRecorder | None = None,
) -> EncodedDerivation:
    """Evaluate the three equivalence classes after structural uniqueness."""
    candidate = structural.candidates[0]
    binding = candidate.instance_bindings[0]
    bitmap_rows = {
        row["operation_id"]: bitmap_members(row["compatible_occurrence_bitmap_hex"])
        for row in binding["compatible_occurrences_by_operation"]
    }
    finals: list[H4Candidate] = []
    for class_id in ENCODING_CLASSES:
        matching_by_operation: dict[str, tuple[_DecodedOccurrence, ...]] = {}
        fits = True
        for evidence in structural.evidence:
            if ledger is not None:
                ledger.charge_once(
                    "encoding_length_equivalence_candidates",
                    (structural.replica, evidence.record.operation_id, class_id),
                )
            matching: list[_DecodedOccurrence] = []
            for occurrence in evidence.occurrences:
                if occurrence.index not in bitmap_rows[evidence.record.operation_id]:
                    continue
                decoded = _decode_for_model(evidence, occurrence, candidate.model)
                if decoded is not None and encoding_class_matches(
                    class_id,
                    evidence.expected_name,
                    bytes.fromhex(occurrence.encoded_hex),
                    decoded.stored_length,
                ):
                    matching.append(decoded)
            if not matching:
                fits = False
                break
            matching_by_operation[evidence.record.operation_id] = tuple(matching)
        if not fits:
            continue
        assignment = identifier_assignment(
            OPERATIONS,
            {
                operation: frozenset(
                    row.identifier for row in matching_by_operation[operation]
                )
                for operation in OPERATIONS
            },
            str(candidate.model["identifier_lifecycle"]),
        )
        if assignment is None:
            continue
        selected: list[dict[str, int | str]] = [
            {
                "operation_id": operation,
                "occurrence_index": min(
                    row.occurrence.index
                    for row in matching_by_operation[operation]
                    if row.identifier == assignment[operation]
                ),
            }
            for operation in OPERATIONS
        ]
        model = {
            "structural_model_id": candidate.canonical_model_id,
            "encoding_length_equivalence_class": class_id,
        }
        final_binding = {
            "replica": structural.replica,
            "structural_candidate_id": candidate.canonical_candidate_id,
            "selected_operation_occurrences": selected,
        }
        finals.append(H4Candidate("h4_final_encoded_field", model, (final_binding,)))
    measure(measurements, "A4-H4-ENCODING-AMBIGUOUS", len(finals), len(finals) == 1, replica=structural.replica)
    if len(finals) != 1:
        raise A4AnalysisError("A4-H4-ENCODING-AMBIGUOUS", len(finals))
    return EncodedDerivation(candidate, finals[0])
def merge_encoded_derivations(
    left: EncodedDerivation, right: EncodedDerivation,
    measurements: MeasurementRecorder | None = None,
) -> EncodedDerivation:
    """Apply H4 replica agreement and bind both physical instances."""
    left_replica = left.structural.instance_bindings[0]["replica"]
    right_replica = right.structural.instance_bindings[0]["replica"]
    if (left_replica, right_replica) != (1, 2):
        raise ValueError("H4 agreement requires replicas 1 then 2")
    model_count = len({
        (left.structural.canonical_model_id, left.final.canonical_model_id),
        (right.structural.canonical_model_id, right.final.canonical_model_id),
    })
    disagrees = model_count != 1
    measure(measurements, "A4-H4-REPLICA-DISAGREEMENT", model_count, not disagrees)
    if disagrees:
        raise A4AnalysisError("A4-H4-REPLICA-DISAGREEMENT", 2)
    structural = H4Candidate(
        "h4_structural_field",
        left.structural.model,
        left.structural.instance_bindings + right.structural.instance_bindings,
    )
    # Final bindings must link to the merged structural candidate, not either
    # per-replica candidate id.
    final_bindings = tuple(
        {
            **dict(binding),
            "structural_candidate_id": structural.canonical_candidate_id,
        }
        for binding in left.final.instance_bindings + right.final.instance_bindings
    )
    final = H4Candidate("h4_final_encoded_field", left.final.model, final_bindings)
    return EncodedDerivation(structural, final)
def evidence_document_sha256(evidence: Sequence[OperationEvidence]) -> str:
    """Expose the canonical occurrence-table hash for orchestration/tests."""
    if not evidence:
        raise ValueError("H4 occurrence evidence cannot be empty")
    return _evidence_hash(evidence)
