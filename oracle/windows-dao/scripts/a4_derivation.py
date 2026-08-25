#!/usr/bin/env python3
"""Terminal-payload reconstruction helpers for A4 derivation orchestration."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import a4_layer_h4 as h4_primitive
from a4_analysis_input import CheckedAnalysisInput
from a4_layer_h3 import (
    BASE_FORMULAS,
    H3Candidate,
    TraversalObservation,
    admitted_pages,
    conversion_legs,
    formula_fits,
    reference_invalid,
)
from a4_layer_h4 import (
    OPERATIONS,
    CatalogRootObservation,
    H4Candidate,
    OperationRecord,
    StructuralDerivation,
    derive_catalog_root,
    operation_candidate,
)
from a4_layer_h4_fields import (
    bitmap_hex,
    bitmap_members,
    encoding_class_matches,
    identifier_assignment,
    identifier_assignment_exists,
    kind_mappings,
    value_equivalence_key,
)
from a4_model import A4AnalysisError, WorkLedger
from a4_layer_h1 import H1ReplicaCandidate
from a4_model import View
from a4_spec import (
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    EXPERIMENT_ID,
    PLAN,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    validate_schema,
)
from a4_terminal import not_applicable_result, terminal_result


def empty_layer_results() -> dict[str, Any]:
    return {
        "h1_tdef_to_map_row": not_applicable_result(),
        "h2_row_identity_map_role": not_applicable_result(),
        "h3_indirect_traversal": not_applicable_result(),
        "h4_catalog_bootstrap": {
            "root_result": not_applicable_result(),
            "structural_result": not_applicable_result(),
            "encoding_result": not_applicable_result(),
        },
    }


def caught_terminal(
    error: A4AnalysisError,
    ledger: WorkLedger,
    *,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    evidence: Mapping[str, Any] | None = None,
    per_replica_counts: Sequence[int] | None = None,
    candidate_stage: str | None = None,
) -> dict[str, Any]:
    return terminal_result(
        error,
        ledger,
        candidates=(getattr(error, "candidates", ()) if candidates is None else candidates),
        terminal_evidence=(
            getattr(error, "terminal_evidence", None) if evidence is None else evidence
        ),
        per_replica_counts=per_replica_counts,
        candidate_stage=candidate_stage,
    )


def h3_terminal_payload(
    error: A4AnalysisError,
    rows: Sequence[TraversalObservation],
    page_counts: Mapping[str, int],
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any] | None]:
    conversion_name = PLAN["candidate_grammars"]["h3"]["conversion_candidates"][0]
    conversion = H3Candidate("h3_conversion", {"conversion": conversion_name})
    if error.predicate_id == "A4-H3-CONVERSION-NONE":
        return (), None
    if error.predicate_id in (
        "A4-H3-INACTIVE-SLOT-NONE",
        "A4-H3-BASE-DISCRIMINATION",
    ):
        return (conversion.document(),), None
    if error.predicate_id == "A4-H3-REFERENCE-INVALID":
        for row in rows:
            if row.representation != "type_1":
                continue
            invalid = reference_invalid(row, page_counts[row.checkpoint_id])
            if invalid is None:
                continue
            reason = (
                "out_of_range"
                if invalid.reference >= page_counts[row.checkpoint_id]
                else "missing_page"
                if invalid.referenced_page_tag is None
                else "not_tag_05"
            )
            return (conversion.document(),), {
                "kind": "reference",
                "input_model_id": conversion.canonical_candidate_id,
                "observation": {
                    "replica": row.replica,
                    "checkpoint_id": row.checkpoint_id,
                    "page": row.map_page,
                    "slot_ordinal": invalid.slot_ordinal,
                    "referenced_page": invalid.reference,
                    "observed_tag_byte": invalid.referenced_page_tag,
                    "reason": reason,
                },
            }
        raise ValueError("H3 reference terminal has no invalid observation")
    if error.predicate_id in ("A4-H3-BASE-NONE", "A4-H3-BASE-MULTIPLE"):
        legs = conversion_legs(rows)
        candidates = tuple(
            H3Candidate(
                "h3_final_base_formula",
                {"conversion": conversion_name, "base_formula": formula},
            ).document()
            for formula in BASE_FORMULAS
            if formula_fits(formula, rows, legs)
        )
        return candidates, None
    raise error


def replica_pair(candidates: Sequence[Any]) -> dict[str, Any]:
    entries = []
    for replica, candidate in zip((1, 2), candidates):
        entries.append({
            "replica": replica,
            "canonical_model_id": candidate.canonical_model_id,
            "canonical_candidate_id": candidate.canonical_candidate_id,
            "complete_candidate": candidate.document(),
        })
    return {"kind": "replica_pair", "entries": entries}


def root_candidates(
    replica: int, observations: Sequence[CatalogRootObservation]
) -> tuple[H4Candidate, ...]:
    candidates = []
    for observation in observations:
        try:
            candidates.append(derive_catalog_root(replica, (observation,)))
        except A4AnalysisError as error:
            if error.predicate_id != "A4-H4-CATALOG-ROOT-NONE":
                raise
    return tuple(sorted(candidates, key=lambda row: row.canonical_candidate_id))


def operation_groups(
    root: H4Candidate, records: Sequence[OperationRecord]
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {operation: [] for operation in OPERATIONS}
    for record in records:
        grouped[record.operation_id].append(operation_candidate(root, record).document())
    for operation in OPERATIONS:
        grouped[operation].sort(key=lambda row: row["canonical_candidate_id"])
    candidates = tuple(
        sorted(
            (
                candidate
                for operation in OPERATIONS
                for candidate in grouped[operation]
            ),
            key=lambda row: row["canonical_candidate_id"],
        )
    )
    evidence = {
        "kind": "operation_groups",
        "groups": [
            {
                "operation_id": operation,
                "cardinality": len(grouped[operation]),
                "candidate_ids": [
                    candidate["canonical_candidate_id"] for candidate in grouped[operation]
                ],
            }
            for operation in OPERATIONS
        ],
    }
    return candidates, evidence


def outside_evidence(
    inputs: CheckedAnalysisInput,
    replica: int,
    root_candidate: H4Candidate,
    root: CatalogRootObservation,
    deltas: Mapping[str, frozenset[int]],
) -> dict[str, Any] | None:
    view = inputs.views[replica]
    for operation in OPERATIONS:
        outside = sorted(deltas[operation] - root.admitted_pages_by_checkpoint[operation])
        if not outside:
            continue
        page = outside[0]
        ordinal = CHECKPOINT_ORDINALS[operation]
        before = CHECKPOINT_IDS[ordinal - 1]
        return {
            "kind": "schema_delta_outside",
            "input_model_id": root_candidate.canonical_candidate_id,
            "observation": {
                "replica": replica,
                "operation_id": operation,
                "checkpoint_before": before,
                "checkpoint_after": operation,
                "page": page,
                "page_sha256_before": view.hash_at(before, page),
                "page_sha256_after": view.hash_at(operation, page),
            },
        }
    return None


def isolated_operation_deltas(
    view: View,
    h1: H1ReplicaCandidate,
    observations: Sequence[TraversalObservation],
    h3: H3Candidate,
) -> Mapping[str, frozenset[int]]:
    def changed_pages(checkpoint: str) -> frozenset[int]:
        ordinal = CHECKPOINT_ORDINALS[checkpoint]
        before = view.hashes(CHECKPOINT_IDS[ordinal - 1])
        after = view.hashes(checkpoint)
        maximum = max(len(before), len(after))
        return frozenset(
            page
            for page in range(maximum)
            if (before[page] if page < len(before) else None)
            != (after[page] if page < len(after) else None)
        )

    formula = str(h3.model["base_formula"])
    result = {}
    for operation in OPERATIONS:
        ordinal = CHECKPOINT_ORDINALS[operation]
        checkpoints = (CHECKPOINT_IDS[ordinal - 1], operation)
        excluded = {0, 1}
        for checkpoint in checkpoints:
            for binding in h1.bindings:
                if checkpoint in binding.checkpoints:
                    excluded.add(binding.tdef_page)
                    if binding.locator_targets is not None:
                        excluded.update(target.page for target in binding.locator_targets)
            for row in observations:
                if row.checkpoint_id == checkpoint:
                    excluded.update(
                        row.type0_owned
                        if row.representation == "type_0"
                        else admitted_pages(row, formula)
                    )
        result[operation] = changed_pages(operation) - excluded
    return result


def occurrence_evidence_document(
    campaign_id: str,
    root: H4Candidate,
    structural: Mapping[int, StructuralDerivation],
) -> dict[str, object]:
    groups = []
    for replica in (1, 2):
        groups.append({
            "replica": replica,
            "operation_bindings": [
                {
                    "operation_id": group.record.operation_id,
                    "canonical_record_locator": group.record.locator.document(),
                    "occurrences": [
                        {
                            "occurrence_index": occurrence.index,
                            "name_start": occurrence.start,
                            "matched_registered_pattern_id": (
                                f"{group.record.operation_id}_CP1252"
                                if "strict_windows_1252" in occurrence.encoding_ids
                                else f"{group.record.operation_id}_UTF8"
                            ),
                            "matched_bytes_hex": occurrence.encoded_hex,
                        }
                        for occurrence in group.occurrences
                    ],
                }
                for group in structural[replica].evidence
            ],
        })
    document: dict[str, object] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_h4_occurrence_evidence",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": campaign_id,
        "root_candidate_id": root.canonical_candidate_id,
        "replica_groups": groups,
    }
    validate_schema(document, "dao_a4_h4_occurrence_evidence")
    return document


def rebind_evidence(
    structural: StructuralDerivation, digest: str
) -> StructuralDerivation:
    candidate = structural.candidates[0]
    binding = {
        **dict(candidate.instance_bindings[0]),
        "occurrence_evidence_sha256": digest,
    }
    rebound = H4Candidate(candidate.model_type, candidate.model, (binding,))
    return StructuralDerivation(
        structural.replica, digest, structural.evidence, (rebound,)
    )


def structural_candidates(
    replica: int, records: Sequence[OperationRecord]
) -> StructuralDerivation:
    evidence = tuple(h4_primitive.scan_name_occurrences(record) for record in records)
    digest = h4_primitive._evidence_hash(evidence)
    equivalent: dict[
        tuple[Any, ...], tuple[dict[str, Any], dict[str, tuple[int, ...]], int]
    ] = {}
    shapes = itertools.product(
        h4_primitive.KIND_START_DELTAS,
        h4_primitive.KIND_WIDTHS,
        h4_primitive.IDENTIFIER_WIDTHS,
        h4_primitive.ENDIANNESS,
        h4_primitive.NAME_LENGTH_START_DELTAS,
        h4_primitive.NAME_LENGTH_WIDTHS,
    )
    for shape in shapes:
        decoded: dict[str, tuple[Any, ...]] = {}
        for group in evidence:
            rows = tuple(
                result
                for occurrence in group.occurrences
                if (result := h4_primitive._decode_shape(group, occurrence, shape))
                is not None
            )
            if not rows:
                break
            decoded[group.record.operation_id] = rows
        if len(decoded) != len(OPERATIONS):
            continue
        for mapping in kind_mappings(
            frozenset(row.kind for rows in decoded.values() for row in rows)
        ):
            filtered = {
                operation: tuple(
                    row
                    for row in decoded[operation]
                    if row.kind == mapping[h4_primitive._OBJECT_KIND[operation]]
                )
                for operation in OPERATIONS
            }
            if not all(filtered.values()):
                continue
            options = {
                operation: frozenset(row.identifier for row in filtered[operation])
                for operation in OPERATIONS
            }
            for lifecycle in h4_primitive.IDENTIFIER_LIFECYCLES:
                if not identifier_assignment_exists(OPERATIONS, options, lifecycle):
                    continue
                compatible: dict[str, tuple[int, ...]] = {}
                for operation in OPERATIONS:
                    indexes = tuple(
                        row.occurrence.index
                        for row in filtered[operation]
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
                    OPERATIONS, filtered, compatible, mapping, lifecycle
                )
                if key in equivalent:
                    first_model, first_compatible, count = equivalent[key]
                    equivalent[key] = (first_model, first_compatible, count + 1)
                else:
                    equivalent[key] = (model, compatible, 1)
    candidates = []
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
    return StructuralDerivation(
        replica,
        digest,
        evidence,
        tuple(sorted(candidates, key=lambda row: row.canonical_candidate_id)),
    )


def encoding_candidates(structural: StructuralDerivation) -> tuple[H4Candidate, ...]:
    candidate = structural.candidates[0]
    binding = candidate.instance_bindings[0]
    bitmap_rows = {
        row["operation_id"]: bitmap_members(row["compatible_occurrence_bitmap_hex"])
        for row in binding["compatible_occurrences_by_operation"]
    }
    finals = []
    for class_id in h4_primitive.ENCODING_CLASSES:
        matching_by_operation = {}
        for evidence in structural.evidence:
            matching = []
            for occurrence in evidence.occurrences:
                if occurrence.index not in bitmap_rows[evidence.record.operation_id]:
                    continue
                decoded = h4_primitive._decode_for_model(
                    evidence, occurrence, candidate.model
                )
                if decoded is not None and encoding_class_matches(
                    class_id,
                    evidence.expected_name,
                    bytes.fromhex(occurrence.encoded_hex),
                    decoded.stored_length,
                ):
                    matching.append(decoded)
            if not matching:
                break
            matching_by_operation[evidence.record.operation_id] = tuple(matching)
        if len(matching_by_operation) != len(OPERATIONS):
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
        selected = [
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
        finals.append(H4Candidate(
            "h4_final_encoded_field",
            {
                "structural_model_id": candidate.canonical_model_id,
                "encoding_length_equivalence_class": class_id,
            },
            ({
                "replica": structural.replica,
                "structural_candidate_id": candidate.canonical_candidate_id,
                "selected_operation_occurrences": selected,
            },),
        ))
    return tuple(finals)
