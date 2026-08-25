#!/usr/bin/env python3
"""Freeze/resume primitives for the A4 derivation boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from a4_analysis_input import CheckedAnalysisInput
from a4_frozen_validation import _verify_physical_projection, validate_frozen_layers
from a4_layers import DerivationLayers
from a4_model import QualifiedPage, WORK_TERM_LIMITS, WorkLedger
from a4_terminal import DerivationTerminal
from a4_spec import (
    BOUNDS,
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    EXPERIMENT_ID,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    canonical_json_bytes,
    sha256_hex,
    validate_schema,
)


_LAYER_KEYS = (
    "h1_tdef_to_map_row",
    "h2_row_identity_map_role",
    "h3_indirect_traversal",
    "h4_catalog_bootstrap",
)
_QUALIFIED_PAGE_MARKER = hashlib.sha256(
    b"dao-a4-qualified-page-transcript-v1"
).digest()[:15]
_TRANSCRIPT_CATEGORY_CODES = dict(zip(
    ("locators", "row_directories", "map_transitions", "reference_bitmaps",
     "catalog_roots", "catalog_fields"), range(1, 7), strict=True,
))


def _candidate_hash(candidates: Sequence[Mapping[str, Any]]) -> str:
    return sha256_hex(canonical_json_bytes(list(candidates)))


def _decisive(candidate: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [dict(candidate)]
    return {
        "status": "model",
        "predicate_measured_survivor_count": 1,
        "derivation_survivor_count": 1,
        "terminal_predicate_id": None,
        "terminal_payload_kind": None,
        "terminal_candidate_stage": None,
        "candidates": candidates,
        "terminal_evidence": None,
        "canonical_candidates_sha256": _candidate_hash(candidates),
    }


def _charge_frozen_candidates(layers: Mapping[str, Any], ledger: WorkLedger) -> None:
    results = (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        layers["h4_catalog_bootstrap"]["root_result"],
        layers["h4_catalog_bootstrap"]["structural_result"],
        layers["h4_catalog_bootstrap"]["encoding_result"],
    )
    for result in results:
        ledger.charge_candidate_documents(result["candidates"])


def _verify_candidate_hashes(layers: Mapping[str, Any]) -> None:
    """Recompute every frozen candidate-array hash for every outcome shape."""
    results = (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        layers["h4_catalog_bootstrap"]["root_result"],
        layers["h4_catalog_bootstrap"]["structural_result"],
        layers["h4_catalog_bootstrap"]["encoding_result"],
    )
    for result in results:
        if result["canonical_candidates_sha256"] != _candidate_hash(
            result["candidates"]
        ):
            raise ValueError("A4 frozen candidate-array hash mismatch")


def _verify_envelope(document: Mapping[str, Any]) -> None:
    """Recheck canonical inventory ordering and the registered work sum."""
    pages = document["qualified_pages"]
    page_keys = [
        (row["replica"], CHECKPOINT_ORDINALS[row["checkpoint_id"]], row["page_number"])
        for row in pages
    ]
    if page_keys != sorted(set(page_keys)):
        raise ValueError("A4 frozen qualified-page inventory is not canonical and unique")
    charges = document["work_charges"]
    terms = [value for key, value in charges.items() if key != "total_work_units"]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in terms):
        raise ValueError("A4 frozen work charges contain an invalid term")
    if charges["total_work_units"] != sum(terms) or sum(terms) > int(BOUNDS["max_analysis_work_units"]):
        raise ValueError("A4 frozen work total differs from its bounded term sum")
    for term, maximum in WORK_TERM_LIMITS.items():
        if charges[term] > maximum:
            raise ValueError(f"A4 frozen work term {term} exceeds its registered maximum")
    candidate_payloads = {
        canonical_json_bytes(dict(candidate))
        for candidate in _all_frozen_candidates(document["layers"])
    }
    if len(candidate_payloads) > int(BOUNDS["max_candidate_models"]):
        raise ValueError("A4 frozen candidates exceed the registered global maximum")
    if any(len(payload) > 4096 for payload in candidate_payloads):
        raise ValueError("A4 frozen candidate exceeds 4,096 encoded bytes")
    if charges["candidate_serializations"] < len(candidate_payloads):
        raise ValueError("A4 frozen candidate serialization charge omits retained candidates")
    if (charges["invalid_path_row_directory_entries"]
            and any(charges[term] for term in (
                "valid_path_row_directory_entries",
                "type_0_and_tag_05_bitmap_bits",
                "type_1_slots",
                "role_transition_evaluations",
                "base_formula_evaluations",
                "catalog_root_signatures",
                "catalog_raw_rows",
                "encoding_union_anchor_bytes",
                "h4_name_length_structural_tuples",
                "encoding_length_equivalence_candidates",
            ))):
        raise ValueError("A4 frozen work document mixes invalid and valid directory paths")


def _all_frozen_candidates(layers: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    candidates: list[Mapping[str, Any]] = []
    for result in (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        layers["h4_catalog_bootstrap"]["root_result"],
        layers["h4_catalog_bootstrap"]["structural_result"],
        layers["h4_catalog_bootstrap"]["encoding_result"],
    ):
        candidates.extend(result["candidates"])
        evidence = result.get("terminal_evidence")
        if isinstance(evidence, Mapping) and evidence.get("kind") == "replica_pair":
            candidates.extend(entry["complete_candidate"] for entry in evidence["entries"])
    return tuple(candidates)


def _coverage_category(
    ledger: WorkLedger,
    page: QualifiedPage,
    payload: bytes,
    *,
    h2_reached: bool,
    h3_reached: bool,
    h4_reached: bool,
) -> tuple[str, str]:
    """Classify a reached page by its registered read source and reached stage."""
    discriminators = ledger.reached_page_discriminators(page)
    prefixes = {value[0] for value in discriminators
                if isinstance(value, tuple) and value and isinstance(value[0], str)}
    if "catalog_page" in discriminators:
        return "catalog_fields", "catalog_field"
    if "system_map_page" in prefixes:
        return "row_directories", "row_directory"
    if prefixes & {"h3_reference_tag", "system_reference_tag"}:
        return "reference_bitmaps", "reference_bitmap"
    terms = set(ledger.qualified_page_terms(page))
    if "catalog_raw_rows" in terms:
        return "catalog_fields", "catalog_field"
    if "catalog_root_signatures" in terms:
        return "catalog_roots", "catalog_root"
    if not h2_reached:
        return "locators", "locator"
    if not h3_reached:
        return "row_directories", "row_directory"
    if payload[:1] == b"\x05":
        return "reference_bitmaps", "reference_bitmap"
    map_terms = {"type_1_slots", "type_0_and_tag_05_bitmap_bits",
                 "role_transition_evaluations", "base_formula_evaluations"}
    if terms & map_terms:
        return "map_transitions", "map_transition"
    if h4_reached and payload[:1] == b"\x01":
        return "catalog_fields", "catalog_field"
    return "map_transitions", "map_transition"


def _qualified_pages(ledger: WorkLedger) -> list[dict[str, int | str]]:
    ordered = (
        (page.replica, page.checkpoint_id, page.page_number)
        for page in ledger.qualified_pages()
    )
    return [
        {"replica": replica, "checkpoint_id": checkpoint, "page_number": page}
        for replica, checkpoint, page in ordered
    ]


def _transcripts(
    inputs: CheckedAnalysisInput, layers: DerivationLayers
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {
        "row_directories": [],
        "locators": [],
        "map_transitions": [],
        "reference_bitmaps": [],
        "catalog_roots": [],
        "catalog_fields": [],
    }
    for replica in (1, 2):
        view = inputs.views[replica]
        for binding in layers.h1_by_replica[replica].bindings:
            for checkpoint in binding.checkpoints:
                page = view.page(checkpoint, binding.tdef_page)
                locator_bytes = b"".join(
                    page[offset : offset + 4]
                    for offset in layers.h1_by_replica[replica].locator_offsets
                )
                output["locators"].append({
                    "kind": "locator",
                    "checkpoint_id": checkpoint,
                    "page": binding.tdef_page,
                    "detail_hex": locator_bytes.hex(),
                })
                if binding.locator_targets is not None:
                    for target in binding.locator_targets:
                        data = view.page(checkpoint, target.page)
                        output["row_directories"].append({
                            "kind": "row_directory",
                            "checkpoint_id": checkpoint,
                            "page": target.page,
                            "detail_hex": data[8:14].hex(),
                        })
        for row in layers.h3_observations[replica]:
            output["map_transitions"].append({
                "kind": "map_transition",
                "checkpoint_id": row.checkpoint_id,
                "page": row.map_page,
                "detail_hex": row.representation.encode("ascii").hex(),
            })
            for slot in row.slots:
                if slot.reference:
                    output["reference_bitmaps"].append({
                        "kind": "reference_bitmap",
                        "checkpoint_id": row.checkpoint_id,
                        "page": slot.reference,
                        "detail_hex": slot.reference.to_bytes(4, "little").hex(),
                    })
        root = layers.h4_root_observations[replica]
        output["catalog_roots"].append({
            "kind": "catalog_root",
            "checkpoint_id": "EMPTY",
            "page": root.tdef_page,
            "detail_hex": bytes(root.locator_offsets).hex(),
        })
        output["catalog_fields"].extend(
            {
                "kind": "catalog_field",
                "checkpoint_id": record.operation_id,
                "page": record.locator.page,
                "detail_hex": record.row_bytes[:64].hex(),
            }
            for record in layers.h4_records[replica]
        )
    return output


def _empty_transcripts() -> dict[str, list[dict[str, Any]]]:
    return {
        "row_directories": [],
        "locators": [],
        "map_transitions": [],
        "reference_bitmaps": [],
        "catalog_roots": [],
        "catalog_fields": [],
    }


def _complete_transcript_coverage(
    inputs: CheckedAnalysisInput,
    ledger: WorkLedger,
    transcripts: dict[str, list[dict[str, Any]]],
    *,
    h2_reached: bool,
    h3_reached: bool,
    h4_reached: bool,
    markerless_catalog_pages: set[tuple[int, str, int]],
) -> None:
    """Retain one byte-derived transcript for every reached qualified page."""
    views = getattr(inputs, "views", {})
    for page in ledger.qualified_pages():
        identity = (page.replica, page.checkpoint_id, page.page_number)
        if identity in markerless_catalog_pages:
            continue
        view = views[page.replica]
        if page.page_number >= view.page_count(page.checkpoint_id):
            payload = b""
        else:
            payload = view.page(page.checkpoint_id, page.page_number)
        category, kind = _coverage_category(
            ledger,
            page,
            payload,
            h2_reached=h2_reached,
            h3_reached=h3_reached,
            h4_reached=h4_reached,
        )
        detail = (
            _QUALIFIED_PAGE_MARKER
            + bytes((page.replica,))
            + bytes((_TRANSCRIPT_CATEGORY_CODES[category],))
            + hashlib.sha256(payload).digest()[:15]
        )
        transcripts[category].append({
            "kind": kind,
            "checkpoint_id": page.checkpoint_id,
            "page": page.page_number,
            "detail_hex": detail.hex(),
        })


def _terminal_candidates(layers: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    results = (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        layers["h4_catalog_bootstrap"]["root_result"],
        layers["h4_catalog_bootstrap"]["structural_result"],
        layers["h4_catalog_bootstrap"]["encoding_result"],
    )
    candidates: list[Mapping[str, Any]] = []
    for result in results:
        candidates.extend(result["candidates"])
        evidence = result["terminal_evidence"]
        if evidence is not None and evidence.get("kind") == "replica_pair":
            candidates.extend(
                entry["complete_candidate"] for entry in evidence["entries"]
            )
    return tuple(candidates)


def _raw_catalog_identities(
    occurrence: Mapping[str, Any] | None,
) -> set[tuple[int, str, int]]:
    if occurrence is None:
        return set()
    return {
        (
            group["replica"],
            operation["operation_id"],
            operation["canonical_record_locator"]["page"],
        )
        for group in occurrence["replica_groups"]
        for operation in group["operation_bindings"]
    }


def _binding_checkpoints(binding: Mapping[str, Any]) -> tuple[str, ...]:
    interval = binding["applicable_checkpoint_range"]
    first = CHECKPOINT_ORDINALS[interval["start"]]
    last = CHECKPOINT_ORDINALS[interval["end"]]
    return tuple(CHECKPOINT_IDS[first : last + 1])


def _terminal_evidence(
    inputs: CheckedAnalysisInput,
    terminal: DerivationTerminal,
    ledger: WorkLedger,
) -> tuple[list[dict[str, int | str]], dict[str, list[dict[str, Any]]]]:
    """Project only reached, replica-qualified terminal evidence."""
    identities: set[tuple[int, str, int]] = {
        (page.replica, page.checkpoint_id, page.page_number)
        for page in ledger.qualified_pages()
    }
    transcripts = _empty_transcripts()
    views = getattr(inputs, "views", None)
    if not isinstance(views, Mapping):
        return ([], transcripts)

    seen: dict[str, set[tuple[int, str, int, str, object | None]]] = {
        key: set() for key in transcripts
    }
    h2_reached = (
        terminal.layers["h2_row_identity_map_role"]["status"]
        != "not_applicable"
    )
    h3_reached = (
        terminal.layers["h3_indirect_traversal"]["status"]
        != "not_applicable"
    )

    def append(
        category: str,
        kind: str,
        replica: int,
        checkpoint: str,
        page: int,
        detail: bytes,
        *,
        discriminator: object | None = None,
    ) -> None:
        detail_hex = detail.hex()
        key = (replica, checkpoint, page, detail_hex, discriminator)
        if key in seen[category]:
            return
        seen[category].add(key)
        transcripts[category].append(
            {
                "kind": kind,
                "checkpoint_id": checkpoint,
                "page": page,
                "detail_hex": detail_hex,
            }
        )

    for candidate in _terminal_candidates(terminal.layers):
        model_type = candidate["model_type"]
        model = candidate["model"]
        if model_type.startswith("h1_"):
            offsets = tuple(model.get("locator_offsets", ()))
            for binding in candidate.get("instance_bindings", ()):
                replica = binding["replica"]
                checkpoints = _binding_checkpoints(binding)
                tdef_page = binding["tdef_page"]
                identities.update(
                    (replica, checkpoint, tdef_page) for checkpoint in checkpoints
                )
                targets = binding.get("locator_targets", ())
                identities.update(
                    (replica, checkpoint, target["page"])
                    for checkpoint in checkpoints
                    for target in targets
                )
                for checkpoint in checkpoints:
                    if len(offsets) == 2:
                        payload = views[replica].page(checkpoint, tdef_page)
                        append(
                            "locators",
                            "locator",
                            replica,
                            checkpoint,
                            tdef_page,
                            b"".join(
                                payload[offset : offset + 4]
                                for offset in offsets
                            ),
                        )
                    if h2_reached:
                        for page in sorted({target["page"] for target in targets}):
                            payload = views[replica].page(checkpoint, page)
                            append(
                                "row_directories",
                                "row_directory",
                                replica,
                                checkpoint,
                                page,
                                payload[8:14],
                            )
        elif model_type == "h4_catalog_root":
            offsets = tuple(model["locator_offsets"])
            for binding in candidate["instance_bindings"]:
                replica = binding["replica"]
                page = binding["tdef_page"]
                identities.update(
                    (replica, checkpoint, page) for checkpoint in CHECKPOINT_IDS
                )
                append(
                    "catalog_roots",
                    "catalog_root",
                    replica,
                    "EMPTY",
                    page,
                    bytes(offsets),
                )
        elif model_type == "h4_operation_record":
            replica = model["replica"]
            checkpoint = model["operation_id"]
            page = model["canonical_record_locator"]["page"]
            identities.add((replica, checkpoint, page))

    if terminal.h4_occurrence_evidence is not None:
        for group in terminal.h4_occurrence_evidence["replica_groups"]:
            replica = group["replica"]
            for operation in group["operation_bindings"]:
                checkpoint = operation["operation_id"]
                locator = operation["canonical_record_locator"]
                page = locator["page"]
                payload = views[replica].page(checkpoint, page)
                append(
                    "catalog_fields",
                    "catalog_field",
                    replica,
                    checkpoint,
                    page,
                    payload[locator["row_start"] : locator["row_end"]][:64],
                    discriminator=(
                        "catalog_record",
                        locator["row"],
                        locator["row_start"],
                        locator["row_end"],
                    ),
                )

    if h3_reached:
        for term in (
            "type_1_slots",
            "type_0_and_tag_05_bitmap_bits",
            "base_formula_evaluations",
        ):
            for identity in ledger.identities(term):
                page = (
                    identity
                    if isinstance(identity, QualifiedPage)
                    else identity[0]
                    if isinstance(identity, tuple)
                    and identity
                    and isinstance(identity[0], QualifiedPage)
                    else None
                )
                if page is None:
                    continue
                key = (page.replica, page.checkpoint_id, page.page_number)
                identities.add(key)
                payload = views[page.replica].page(
                    page.checkpoint_id, page.page_number
                )
                if payload[0] == 0x05:
                    append(
                        "reference_bitmaps",
                        "reference_bitmap",
                        *key,
                        page.page_number.to_bytes(4, "little"),
                    )
                else:
                    append(
                        "map_transitions",
                        "map_transition",
                        *key,
                        payload[:1],
                    )

    identities = {
        (page.replica, page.checkpoint_id, page.page_number)
        for page in ledger.qualified_pages()
    }
    ordered = sorted(
        identities,
        key=lambda row: (row[0], CHECKPOINT_ORDINALS[row[1]], row[2]),
    )
    return (
        [
            {"replica": replica, "checkpoint_id": checkpoint, "page_number": page}
            for replica, checkpoint, page in ordered
        ],
        transcripts,
    )


@dataclass(frozen=True)
class FrozenDerivation:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    occurrence_evidence_bytes: bytes | None


def freeze_derivation(
    inputs: CheckedAnalysisInput,
    layers: DerivationLayers | DerivationTerminal,
    ledger: WorkLedger,
) -> FrozenDerivation:
    """Serialize and schema-check the complete derivation state before holdout."""
    if isinstance(layers, DerivationTerminal):
        _charge_frozen_candidates(layers.layers, ledger)
        qualified_pages, transcripts = _terminal_evidence(inputs, layers, ledger)
        occurrence = layers.h4_occurrence_evidence
        _complete_transcript_coverage(
            inputs,
            ledger,
            transcripts,
            h2_reached=(
                layers.layers["h2_row_identity_map_role"]["status"]
                != "not_applicable"
            ),
            h3_reached=(
                layers.layers["h3_indirect_traversal"]["status"]
                != "not_applicable"
            ),
            h4_reached=(
                layers.layers["h4_catalog_bootstrap"]["root_result"]["status"]
                != "not_applicable"
            ),
            markerless_catalog_pages=_raw_catalog_identities(occurrence),
        )
        occurrence_bytes = None
        evidence_reference = None
        if occurrence is not None:
            occurrence_bytes = canonical_json_bytes(occurrence)
            evidence_reference = {
                "path": "analysis/h4-occurrence-evidence.json",
                "sha256": sha256_hex(occurrence_bytes),
                "size_bytes": len(occurrence_bytes),
            }
        document: dict[str, Any] = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a4_frozen_derivation_candidates",
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256,
            "revision_plan_sha256": REVISION_PLAN_SHA256,
            "campaign_id": inputs.campaign_id,
            "derivation_replicas": [1, 2],
            "qualified_pages": qualified_pages,
            "work_charges": ledger.document(),
            "h4_occurrence_evidence": evidence_reference,
            "layers": dict(layers.layers),
            "transcripts": transcripts,
        }
        validate_schema(document, "dao_a4_frozen_derivation_candidates")
        _verify_envelope(document)
        _verify_physical_projection(document, occurrence)
        _verify_candidate_hashes(document["layers"])
        validate_frozen_layers(
            document["layers"], document["h4_occurrence_evidence"], occurrence,
            document["transcripts"], document["qualified_pages"],
        )
        encoded = canonical_json_bytes(document)
        return FrozenDerivation(
            MappingProxyType(document), encoded, sha256_hex(encoded), occurrence_bytes
        )
    h4 = {
        "root_result": _decisive(layers.h4_root.document()),
        "structural_result": _decisive(layers.h4.structural.document()),
        "encoding_result": _decisive(layers.h4.final.document()),
    }
    frozen_layers = {
        "h1_tdef_to_map_row": _decisive(layers.h1.document()),
        "h2_row_identity_map_role": _decisive(layers.h2.document()),
        "h3_indirect_traversal": _decisive(layers.h3.document()),
        "h4_catalog_bootstrap": h4,
    }
    _charge_frozen_candidates(frozen_layers, ledger)
    evidence_bytes = canonical_json_bytes(layers.h4_occurrence_evidence)
    transcripts = _transcripts(inputs, layers)
    _complete_transcript_coverage(
        inputs,
        ledger,
        transcripts,
        h2_reached=True,
        h3_reached=True,
        h4_reached=True,
        markerless_catalog_pages=_raw_catalog_identities(
            layers.h4_occurrence_evidence
        ),
    )
    document: dict[str, Any] = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_frozen_derivation_candidates",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "campaign_id": inputs.campaign_id,
        "derivation_replicas": [1, 2],
        "qualified_pages": _qualified_pages(ledger),
        "work_charges": ledger.document(),
        "h4_occurrence_evidence": {
            "path": "analysis/h4-occurrence-evidence.json",
            "sha256": sha256_hex(evidence_bytes),
            "size_bytes": len(evidence_bytes),
        },
        "layers": frozen_layers,
        "transcripts": transcripts,
    }
    validate_schema(document, "dao_a4_frozen_derivation_candidates")
    _verify_envelope(document)
    _verify_physical_projection(document, layers.h4_occurrence_evidence)
    _verify_candidate_hashes(document["layers"])
    validate_frozen_layers(
        document["layers"], document["h4_occurrence_evidence"],
        layers.h4_occurrence_evidence,
        document["transcripts"], document["qualified_pages"],
    )
    encoded = canonical_json_bytes(document)
    return FrozenDerivation(
        MappingProxyType(document), encoded, sha256_hex(encoded), evidence_bytes
    )


def resume_derivation(
    payload: bytes,
    expected_sha256: str,
    occurrence_evidence_payload: bytes | None = None,
) -> Mapping[str, Any]:
    """Verify frozen bytes against an externally trusted SHA, without holdout."""
    import json

    if not isinstance(payload, bytes):
        raise ValueError("A4 frozen derivation payload must be bytes")
    if len(payload) > int(BOUNDS["max_json_bytes"]):
        raise ValueError("A4 frozen derivation exceeds the registered JSON byte bound")
    if sha256_hex(payload) != expected_sha256:
        raise ValueError("A4 frozen derivation hash mismatch")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("A4 frozen derivation is not canonical JSON") from exc
    try:
        if canonical_json_bytes(document) != payload:
            raise ValueError("A4 frozen derivation bytes are not canonical")
        validate_schema(document, "dao_a4_frozen_derivation_candidates")
        _verify_envelope(document)
        _verify_candidate_hashes(document["layers"])
        reference = document["h4_occurrence_evidence"]
        occurrence_evidence = None
        if reference is None:
            if occurrence_evidence_payload is not None:
                raise ValueError("A4 frozen derivation has unexpected occurrence evidence bytes")
        else:
            if not isinstance(occurrence_evidence_payload, bytes):
                raise ValueError("A4 frozen derivation requires occurrence evidence bytes")
            if len(occurrence_evidence_payload) > int(BOUNDS["max_h4_occurrence_evidence_bytes"]):
                raise ValueError("A4 H4 occurrence evidence exceeds its registered byte bound")
            if (len(occurrence_evidence_payload) != reference["size_bytes"]
                    or sha256_hex(occurrence_evidence_payload) != reference["sha256"]):
                raise ValueError("A4 H4 occurrence evidence reference mismatch")
            occurrence_evidence = json.loads(occurrence_evidence_payload.decode("utf-8"))
            if canonical_json_bytes(occurrence_evidence) != occurrence_evidence_payload:
                raise ValueError("A4 H4 occurrence evidence bytes are not canonical")
            validate_schema(occurrence_evidence, "dao_a4_h4_occurrence_evidence")
            if occurrence_evidence.get("campaign_id") != document["campaign_id"]:
                raise ValueError("A4 H4 occurrence evidence campaign differs from the frozen derivation")
        _verify_physical_projection(document, occurrence_evidence)
        validate_frozen_layers(
            document["layers"], reference, occurrence_evidence,
            document["transcripts"], document["qualified_pages"],
        )
    except (RecursionError, TypeError) as exc:
        raise ValueError("A4 frozen derivation is malformed") from exc
    return MappingProxyType(document)
