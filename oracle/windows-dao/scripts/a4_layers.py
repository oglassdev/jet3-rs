#!/usr/bin/env python3
"""Dependency-ordered orchestration for the four A4 analyzer layers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from a4_analysis_input import CheckedAnalysisInput
from a4_catalog_inventory import operation_records, row_bounds
from a4_catalog_root import catalog_root_observations
import a4_layer_h4 as h4_primitive
from a4_derivation import (
    caught_terminal,
    empty_layer_results,
    encoding_candidates,
    h3_terminal_payload,
    isolated_operation_deltas,
    occurrence_evidence_document,
    operation_groups,
    outside_evidence,
    rebind_evidence,
    replica_pair,
    root_candidates as reconstruct_root_candidates,
)
from a4_layer_decoding import decode_locator, set_bits, type0_owned
from a4_layer_h1 import H1ReplicaCandidate, agree_h1_replicas, derive_h1_replica
from a4_layer_h2 import (
    H2ReplicaCandidate,
    _transitions_fit as h2_transitions_fit,
    agree_h2_replicas,
    decode_frozen_owned_rows,
    derive_h2_replica,
)
from a4_layer_h2_types import FrozenOwnedRow, MapRow
from a4_layer_h3 import (
    BITMAP_BITS,
    H3Candidate,
    H3Derivation,
    MAX_ADMITTED_PAGES,
    SlotObservation,
    TraversalObservation,
    agree_h3,
    derive_h3,
)
from a4_layer_h4 import (
    OPERATIONS,
    CatalogRootObservation,
    EncodedDerivation,
    H4Candidate,
    OperationRecord,
    StructuralDerivation,
    derive_catalog_root,
    derive_encoding,
    derive_structural_fields,
    merge_catalog_roots,
    merge_encoded_derivations,
    select_operation_records,
    validate_isolated_deltas,
)
from a4_measurements import MeasurementRecorder, PredicateMeasurement
from a4_model import A4AnalysisError, QualifiedPage, View, WorkLedger
from a4_predicate_major import evaluate_replica_predicates
from a4_terminal import DerivationTerminal, decisive_result
from a4_spec import (
    BOUNDS,
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    LAYER_PREDICATE_IDS,
    PAGE_SIZE,
    ROLE_BINDINGS,
    canonical_json_bytes,
    sha256_hex,
)


@dataclass(frozen=True)
class DerivationLayers:
    h1_by_replica: Mapping[int, H1ReplicaCandidate]
    h1: H1ReplicaCandidate
    h2_by_replica: Mapping[int, H2ReplicaCandidate]
    h2: H2ReplicaCandidate
    h3_observations: Mapping[int, tuple[TraversalObservation, ...]]
    h3_by_replica: Mapping[int, H3Derivation]
    h3: H3Candidate
    h4_root_observations: Mapping[int, CatalogRootObservation]
    h4_root: H4Candidate
    h4_records: Mapping[int, tuple[OperationRecord, ...]]
    h4_by_replica: Mapping[int, EncodedDerivation]
    h4: EncodedDerivation
    h4_occurrence_evidence: Mapping[str, object]
    measurements: tuple[PredicateMeasurement, ...]


def h3_observations(
    view: View,
    rows: Sequence[FrozenOwnedRow],
    ledger: WorkLedger,
) -> tuple[TraversalObservation, ...]:
    """Read only frozen-H2 references and create formula-neutral H3 inputs."""
    most_recent_span: dict[tuple[str, str], tuple[int, int]] = {}
    output: list[TraversalObservation] = []
    reference_payloads: dict[tuple[str, int], bytes] = {}
    referenced_pages: dict[str, set[int]] = {}
    invalid_reference_seen = False
    for row in rows:
        if row.replica != view.replica:
            raise ValueError("H3 adapter cannot cross replicas")
        identity = (row.lifecycle_instance, row.allocation_role)
        if row.representation == "type_0":
            owned = frozenset(row.owned_pages or ())
            if row.type_0_span is not None:
                most_recent_span[identity] = row.type_0_span
            output.append(TraversalObservation(
                row.replica,
                row.checkpoint_id,
                row.map_page,
                "type_0",
                row.lifecycle_instance,
                allocation_role=row.allocation_role,
                type0_owned=owned,
                locator_ordinal=row.map_row,
                allocation_span=row.type_0_span,
            ))
            continue
        references = row.type_1_references
        if references is None:
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail="frozen H2 type-1 row lacks complete references",
            )
        slots: list[SlotObservation] = []
        for ordinal, reference in enumerate(references):
            if reference == 0:
                slots.append(SlotObservation(ordinal, 0, None))
                continue
            checkpoint_references = referenced_pages.setdefault(
                row.checkpoint_id, set()
            )
            if (
                not invalid_reference_seen
                and reference not in checkpoint_references
                and len(checkpoint_references) == MAX_ADMITTED_PAGES
            ):
                raise A4AnalysisError(
                    "A4-RESOURCE-BOUND",
                    detail="H3 references more than 16 qualified pages",
                )
            if not invalid_reference_seen:
                checkpoint_references.add(reference)
            payload = (
                None
                if invalid_reference_seen
                else view.page_optional(row.checkpoint_id, reference)
            )
            if (
                not invalid_reference_seen
                and view.replica in (1, 2)
                and 0 <= reference < int(BOUNDS["max_final_pages_per_replica"])
            ):
                ledger.record_qualified_page(
                    QualifiedPage(view.replica, row.checkpoint_id, reference),
                    discriminator=("h3_reference_tag", row.map_page, ordinal),
                )
            tag = None if payload is None else payload[0]
            slots.append(SlotObservation(ordinal, reference, tag))
            if payload is None or tag != 0x05:
                invalid_reference_seen = True
            else:
                reference_payloads[(row.checkpoint_id, reference)] = payload
        output.append(TraversalObservation(
            row.replica,
            row.checkpoint_id,
            row.map_page,
            "type_1",
            row.lifecycle_instance,
            allocation_role=row.allocation_role,
            slots=tuple(slots),
            locator_ordinal=row.map_row,
            allocation_span=most_recent_span.get(identity),
        ))
    if invalid_reference_seen:
        return tuple(output)
    hydrated: list[TraversalObservation] = []
    for row in output:
        slots: list[SlotObservation] = []
        for slot in row.slots:
            if slot.reference == 0:
                slots.append(slot)
                continue
            payload = reference_payloads[(row.checkpoint_id, slot.reference)]
            if row.replica in (1, 2):
                ledger.charge_qualified(
                    "type_0_and_tag_05_bitmap_bits",
                    QualifiedPage(row.replica, row.checkpoint_id, slot.reference),
                    BITMAP_BITS,
                )
            slots.append(
                SlotObservation(
                    slot.slot_ordinal,
                    slot.reference,
                    slot.referenced_page_tag,
                    set_bits(payload[4:]),
                )
            )
        hydrated.append(replace(row, slots=tuple(slots)))
    return tuple(hydrated)


def _frozen_rows(
    view: View,
    binding: object,
    checkpoint: str,
    h2: H2ReplicaCandidate,
) -> tuple[tuple[bytes, frozenset[int] | None], ...]:
    targets = getattr(binding, "locator_targets")
    if targets is None:
        raise ValueError("A4 frozen H1 binding lacks locator targets")
    result: list[tuple[bytes, frozenset[int] | None]] = []
    for target in targets:
        payload = view.page(checkpoint, target.page)
        start, end = row_bounds(payload, h2.row_mask)[target.row]
        row = payload[start:end]
        if not row or row[0] not in (0, 1) or (row[0] == 1 and (len(row) - 1) % 4):
            raise ValueError("A4 holdout map row contradicts the frozen H2 grammar")
        admitted = type0_owned(row, h2.polarity) if row[0] == 0 else None
        result.append((row, admitted))
    return tuple(result)


def predicts_h2(
    view: View,
    h1: H1ReplicaCandidate,
    h2: H2ReplicaCandidate,
    row_counts: Mapping[str, Mapping[str, int]],
) -> bool:
    """Apply the complete frozen H2 model without refitting."""
    try:
        transition_rows: dict[tuple[str, str, int], MapRow] = {}
        for binding in h1.bindings:
            for checkpoint in binding.checkpoints:
                rows = _frozen_rows(view, binding, checkpoint, h2)
                for ordinal, (payload, _admitted) in enumerate(rows):
                    type_0 = payload[0] == 0
                    transition_rows[(binding.lifecycle_instance, checkpoint, ordinal)] = MapRow(
                        payload[0],
                        payload,
                        int.from_bytes(payload[1:5], "little") if type_0 else None,
                        payload[5:] if type_0 else None,
                    )
                owned = rows[h2.owned_in_use_locator_ordinal][1]
                available = rows[h2.available_locator_ordinal][1]
                if owned is not None:
                    if any(page >= view.page_count(checkpoint) for page in owned):
                        return False
                    if row_counts[checkpoint][binding.logical_role] > 0 and not owned:
                        return False
                if owned is not None and available is not None and not available <= owned:
                    return False
        return h2_transitions_fit(
            h1, transition_rows, h2.polarity,
            h2.owned_in_use_locator_ordinal, h2.available_locator_ordinal,
        )
    except A4AnalysisError:
        raise
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def derive_layers(
    inputs: CheckedAnalysisInput, ledger: WorkLedger | None = None
) -> DerivationLayers | DerivationTerminal:
    """Run H1→H4 on replicas 1/2, freezing each model before proceeding."""
    work = ledger or WorkLedger()
    measurements = MeasurementRecorder()
    results = empty_layer_results()

    def terminal(
        error: A4AnalysisError,
        occurrence: Mapping[str, object] | None = None,
    ) -> DerivationTerminal:
        return DerivationTerminal(
            error.predicate_id, results, occurrence, measurements.events
        )
    h1_predicates = LAYER_PREDICATE_IDS["h1_tdef_to_map_row"]
    h1_outcome = evaluate_replica_predicates(
        h1_predicates[:-1],
        lambda replica, stage_work, stage_measurements: derive_h1_replica(
            inputs.views[replica],
            inputs.qualified_tdef_pages[replica],
            stage_work,
            stage_measurements,
        ),
        work,
        measurements,
    )
    if h1_outcome.failure is not None:
        error = h1_outcome.failure.error
        results["h1_tdef_to_map_row"] = caught_terminal(error, work)
        return terminal(error)
    h1_by = dict(h1_outcome.values)
    try:
        frozen_h1 = agree_h1_replicas(h1_by[1], h1_by[2], measurements)
    except A4AnalysisError as error:
        results["h1_tdef_to_map_row"] = caught_terminal(
            error, work, per_replica_counts=(1, 1)
        )
        return terminal(error)
    results["h1_tdef_to_map_row"] = decisive_result(frozen_h1.document(), work)

    h2_predicates = LAYER_PREDICATE_IDS["h2_row_identity_map_role"]
    h2_outcome = evaluate_replica_predicates(
        h2_predicates[:-1],
        lambda replica, stage_work, stage_measurements: derive_h2_replica(
            inputs.views[replica],
            h1_by[replica],
            inputs.replicas[replica].table_row_counts,
            stage_work,
            stage_measurements,
        ),
        work,
        measurements,
    )
    if h2_outcome.failure is not None:
        error = h2_outcome.failure.error
        evidence = getattr(error, "terminal_evidence", None)
        if error.predicate_id in {
            "A4-H2-ROW-DIRECTORY-INVALID",
            "A4-H2-ROW-FLAGS-INVALID",
            "A4-H2-MAP-TAG-UNSUPPORTED",
        }:
            evidence = {
                **dict(evidence),
                "input_model_id": frozen_h1.canonical_candidate_id,
            }
        results["h2_row_identity_map_role"] = caught_terminal(
            error, work, evidence=evidence
        )
        return terminal(error)
    h2_by = dict(h2_outcome.values)
    try:
        frozen_h2 = agree_h2_replicas(h2_by[1], h2_by[2], measurements)
    except A4AnalysisError as error:
        results["h2_row_identity_map_role"] = caught_terminal(
            error, work, per_replica_counts=(1, 1)
        )
        return terminal(error)
    results["h2_row_identity_map_role"] = decisive_result(frozen_h2.document(), work)
    rows_by: dict[int, tuple[TraversalObservation, ...]] = {}

    def run_h3(
        replica: int, stage_work: WorkLedger, stage_measurements: object
    ) -> H3Derivation:
        frozen_rows = decode_frozen_owned_rows(
            inputs.views[replica], h1_by[replica], frozen_h2, stage_work
        )
        rows = h3_observations(inputs.views[replica], frozen_rows, stage_work)
        rows_by[replica] = rows
        return derive_h3(
            replica,
            rows,
            inputs.replicas[replica].source.page_count,
            stage_work,
            stage_measurements,
        )

    h3_predicates = LAYER_PREDICATE_IDS["h3_indirect_traversal"]
    h3_outcome = evaluate_replica_predicates(
        h3_predicates[:-1],
        run_h3,
        work,
        measurements,
    )
    if h3_outcome.failure is not None:
        error = h3_outcome.failure.error
        replica = h3_outcome.failure.replica
        candidates, evidence = h3_terminal_payload(
            error,
            rows_by[replica],
            inputs.replicas[replica].source.page_count,
        )
        results["h3_indirect_traversal"] = caught_terminal(
            error,
            work,
            candidates=candidates,
            evidence=evidence,
        )
        return terminal(error)
    h3_by = dict(h3_outcome.values)
    try:
        frozen_h3 = agree_h3(h3_by[1], h3_by[2], measurements)
    except A4AnalysisError as error:
        evidence = replica_pair((h3_by[1].final, h3_by[2].final))
        results["h3_indirect_traversal"] = caught_terminal(
            error,
            work,
            candidates=(),
            evidence=evidence,
            per_replica_counts=(1, 1),
        )
        return terminal(error)
    results["h3_indirect_traversal"] = decisive_result(frozen_h3.document(), work)

    root_observation: dict[int, CatalogRootObservation] = {}
    records_by: dict[int, tuple[OperationRecord, ...]] = {}
    observations_by: dict[int, tuple[CatalogRootObservation, ...]] = {}

    def run_root(
        replica: int, stage_work: WorkLedger, stage_measurements: object
    ) -> H4Candidate:
        observations = catalog_root_observations(
            inputs.views[replica],
            inputs.qualified_tdef_pages[replica],
            h1_by[replica],
            frozen_h2,
            frozen_h3,
            stage_work,
        )
        observations_by[replica] = observations
        return derive_catalog_root(
            replica, observations, stage_work, stage_measurements
        )

    h4_predicates = LAYER_PREDICATE_IDS["h4_catalog_bootstrap"]
    root_outcome = evaluate_replica_predicates(
        h4_predicates[:2],
        run_root,
        work,
        measurements,
    )
    if root_outcome.failure is not None:
        error = root_outcome.failure.error
        replica = root_outcome.failure.replica
        candidates = reconstruct_root_candidates(replica, observations_by[replica])
        results["h4_catalog_bootstrap"]["root_result"] = caught_terminal(
            error,
            work,
            candidates=tuple(candidate.document() for candidate in candidates),
        )
        return terminal(error)
    root_candidates = dict(root_outcome.values)
    for replica in (1, 2):
        tdef_page = root_candidates[replica].instance_bindings[0]["tdef_page"]
        root_observation[replica] = next(
            row for row in observations_by[replica] if row.tdef_page == tdef_page
        )
    try:
        frozen_root = merge_catalog_roots(
            root_candidates[1], root_candidates[2], measurements
        )
    except A4AnalysisError as error:
        root_documents = tuple(
            root_candidates[replica].document() for replica in (1, 2)
        )
        results["h4_catalog_bootstrap"]["root_result"] = caught_terminal(
            error, work, candidates=root_documents
        )
        return terminal(error)
    results["h4_catalog_bootstrap"]["root_result"] = decisive_result(
        frozen_root.document(), work
    )
    deltas_by: dict[int, Mapping[str, frozenset[int]]] = {}

    def run_delta(
        replica: int, _stage_work: WorkLedger, stage_measurements: object
    ) -> None:
        deltas = isolated_operation_deltas(
            inputs.views[replica], h1_by[replica], rows_by[replica], frozen_h3
        )
        deltas_by[replica] = deltas
        return validate_isolated_deltas(
            replica,
            root_observation[replica],
            deltas,
            stage_measurements,
        )

    delta_outcome = evaluate_replica_predicates(
        h4_predicates[2:3],
        run_delta,
        work,
        measurements,
    )
    if delta_outcome.failure is not None:
        error = delta_outcome.failure.error
        replica = delta_outcome.failure.replica
        outside = outside_evidence(
            inputs,
            replica,
            root_candidates[replica],
            root_observation[replica],
            deltas_by[replica],
        )
        if outside is not None:
            outside = {
                **outside,
                "input_model_id": frozen_root.canonical_candidate_id,
            }
        results["h4_catalog_bootstrap"]["structural_result"] = caught_terminal(
            error, work, evidence=outside
        )
        return terminal(error)
    raw_records_by: dict[int, tuple[OperationRecord, ...]] = {}

    def run_records(
        replica: int, stage_work: WorkLedger, stage_measurements: object
    ) -> tuple[OperationRecord, ...]:
        raw_records = operation_records(
            inputs.views[replica],
            root_observation[replica],
            deltas_by[replica],
            frozen_h2,
            stage_work,
        )
        raw_records_by[replica] = raw_records
        return select_operation_records(
            replica,
            root_candidates[replica],
            raw_records,
            stage_work,
            stage_measurements,
        )

    record_outcome = evaluate_replica_predicates(
        h4_predicates[3:5],
        run_records,
        work,
        measurements,
    )
    if record_outcome.failure is not None:
        error = record_outcome.failure.error
        replica = record_outcome.failure.replica
        grouped_candidates, grouped_evidence = operation_groups(
            root_candidates[replica], raw_records_by[replica]
        )
        results["h4_catalog_bootstrap"]["structural_result"] = caught_terminal(
            error,
            work,
            candidates=grouped_candidates,
            evidence=grouped_evidence,
        )
        return terminal(error)
    records_by = dict(record_outcome.values)
    evidence_by: dict[int, StructuralDerivation] = {}

    def run_structural(
        replica: int, stage_work: WorkLedger, stage_measurements: object
    ) -> StructuralDerivation:
        groups = tuple(
            h4_primitive.scan_name_occurrences(record, stage_work)
            for record in records_by[replica]
        )
        evidence_by[replica] = StructuralDerivation(
            replica, h4_primitive._evidence_hash(groups), groups, ()
        )
        return derive_structural_fields(
            replica,
            records_by[replica],
            stage_work,
            stage_measurements,
            groups,
        )

    structural_outcome = evaluate_replica_predicates(
        h4_predicates[5:7],
        run_structural,
        work,
        measurements,
    )
    # Unreached replicas retain locator bindings but no name-byte observations.
    reached_evidence = {
        replica: evidence_by.get(replica)
        or StructuralDerivation(
            replica,
            h4_primitive._evidence_hash(groups),
            groups,
            (),
        )
        for replica in (1, 2)
        for groups in (
            tuple(
                h4_primitive.OperationEvidence(record, "", ())
                for record in records_by[replica]
            ),
        )
    }
    evidence = occurrence_evidence_document(
        inputs.campaign_id, frozen_root, reached_evidence
    )
    evidence_digest = sha256_hex(canonical_json_bytes(evidence))
    if structural_outcome.failure is not None:
        error = structural_outcome.failure.error
        rebound = tuple(
            H4Candidate(
                candidate["model_type"],
                candidate["model"],
                ({
                    **dict(candidate["instance_bindings"][0]),
                    "occurrence_evidence_sha256": evidence_digest,
                },),
            )
            for candidate in getattr(error, "candidates", ())
        )
        results["h4_catalog_bootstrap"]["structural_result"] = caught_terminal(
            error,
            work,
            candidates=tuple(candidate.document() for candidate in rebound),
        )
        return terminal(error, evidence)
    structural_by = {
        replica: rebind_evidence(structural_outcome.values[replica], evidence_digest)
        for replica in (1, 2)
    }
    encoding_outcome = evaluate_replica_predicates(
        h4_predicates[7:8],
        lambda replica, stage_work, stage_measurements: derive_encoding(
            structural_by[replica], stage_work, stage_measurements
        ),
        work,
        measurements,
    )
    if encoding_outcome.failure is not None:
        error = encoding_outcome.failure.error
        replica = encoding_outcome.failure.replica
        structural_candidates = tuple(
            structural_by[current].candidates[0] for current in (1, 2)
        )
        if len(
            {candidate.canonical_model_id for candidate in structural_candidates}
        ) != 1:
            raise ValueError(
                "A4 encoding terminal reached with unequal structural models"
            )
        structural_candidate = H4Candidate(
            "h4_structural_field",
            structural_candidates[0].model,
            tuple(
                binding
                for candidate in structural_candidates
                for binding in candidate.instance_bindings
            ),
        )
        results["h4_catalog_bootstrap"]["structural_result"] = decisive_result(
            structural_candidate.document(), work
        )
        finals = tuple(
            sorted(
                (
                    H4Candidate(
                        candidate.model_type,
                        {
                            **dict(candidate.model),
                            "structural_model_id": (
                                structural_candidate.canonical_model_id
                            ),
                        },
                        tuple(
                            {
                                **dict(binding),
                                "structural_candidate_id": (
                                    structural_candidate.canonical_candidate_id
                                ),
                            }
                            for binding in candidate.instance_bindings
                        ),
                    )
                    for candidate in encoding_candidates(structural_by[replica])
                ),
                key=lambda candidate: candidate.canonical_candidate_id,
            )
        )
        results["h4_catalog_bootstrap"]["encoding_result"] = caught_terminal(
            error,
            work,
            candidates=tuple(candidate.document() for candidate in finals),
        )
        return terminal(error, evidence)
    encoded_by = dict(encoding_outcome.values)
    try:
        frozen_fields = merge_encoded_derivations(
            encoded_by[1], encoded_by[2], measurements
        )
    except A4AnalysisError as error:
        results["h4_catalog_bootstrap"]["structural_result"] = caught_terminal(
            error,
            work,
            candidates=(),
            evidence=replica_pair(
                (encoded_by[1].structural, encoded_by[2].structural)
            ),
            per_replica_counts=(1, 1),
            candidate_stage="h4_structural_field",
        )
        results["h4_catalog_bootstrap"]["encoding_result"] = caught_terminal(
            error,
            work,
            candidates=(),
            evidence=replica_pair((encoded_by[1].final, encoded_by[2].final)),
            per_replica_counts=(1, 1),
        )
        return terminal(error, evidence)
    results["h4_catalog_bootstrap"]["structural_result"] = decisive_result(
        frozen_fields.structural.document(), work
    )
    results["h4_catalog_bootstrap"]["encoding_result"] = decisive_result(
        frozen_fields.final.document(), work
    )
    return DerivationLayers(
        h1_by,
        frozen_h1,
        h2_by,
        frozen_h2,
        rows_by,
        h3_by,
        frozen_h3,
        root_observation,
        frozen_root,
        records_by,
        encoded_by,
        frozen_fields,
        evidence,
        measurements.events,
    )
