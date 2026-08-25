"""Adversarial frozen-terminal validation and evidence projection tests."""

from __future__ import annotations

import copy
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

from a4_analysis import analyze  # noqa: E402
from a4_analysis_input import check_analysis_input  # noqa: E402
from a4_analysis_state import (  # noqa: E402
    _QUALIFIED_PAGE_MARKER,
    _TRANSCRIPT_CATEGORY_CODES,
    freeze_derivation,
    resume_derivation,
)
from a4_generator import SyntheticParameters  # noqa: E402
from a4_frozen_validation import (  # noqa: E402
    _candidate_identity,
    _validate_groups,
    validate_frozen_layers,
)
from a4_layer_h1 import (  # noqa: E402
    H1Binding,
    H1ReplicaCandidate,
    LocatorTarget,
    agree_h1_replicas,
    derive_h1_replica,
)
from a4_layer_h2 import H2ReplicaCandidate  # noqa: E402
from a4_layer_h3 import H3Candidate  # noqa: E402
from a4_layer_h4 import H4Candidate  # noqa: E402
import a4_layers  # noqa: E402
from a4_layers import derive_layers  # noqa: E402
from a4_model import A4AnalysisError, QualifiedPage, WorkLedger  # noqa: E402
from a4_spec import (  # noqa: E402
    BOUNDS,
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    EXPERIMENT_ID,
    PLAN,
    PLAN_SHA256,
    REVISION_PLAN_SHA256,
    canonical_candidate_id,
    canonical_json_bytes,
    canonical_model_id,
    sha256_hex,
)
from a4_terminal import (  # noqa: E402
    DerivationTerminal,
    decisive_result,
    not_applicable_result,
    terminal_result,
)
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402


@lru_cache(maxsize=1)
def _pair_multiple_analysis():
    signature = PLAN["candidate_grammars"]["h1"][
        "pair_multiple_reachability_signature"
    ]
    parameters = SyntheticParameters(
        signature_id=signature["signature_id"],
        locator_offsets=tuple(interval[0] for interval in signature["locator_holes"]),
    )
    return analyze("a4-synthetic", _COMMIT, _inputs(parameters))


@lru_cache(maxsize=1)
def _default_analysis():
    return analyze("a4-synthetic", _COMMIT, _inputs())


def _rehash(document: dict[str, object]) -> tuple[bytes, str]:
    payload = canonical_json_bytes(document)
    return payload, hashlib.sha256(payload).hexdigest()


def _h2_terminal_layers():
    bindings = tuple(
        H1Binding(
            replica,
            role,
            instance,
            20 + replica * 10 + index,
            (LocatorTarget(50 + replica * 10 + index, 0), LocatorTarget(50 + replica * 10 + index, 1)),
        )
        for replica in (1, 2)
        for index, (role, instance) in enumerate((
            ("T1", "T1-v1"), ("T2", "T2-v1"), ("T2", "T2-v2"),
            ("T3", "T3-v1"), ("T4", "T4-v1"),
        ))
    )
    ledger = WorkLedger()
    h1 = H1ReplicaCandidate(
        0, "u8_row_then_u24le_page", "a4_pair_multiple_duplicate_locator_0_92",
        (35, 39), bindings,
    ).document()
    layers = {
        "h1_tdef_to_map_row": decisive_result(h1, ledger),
        "h2_row_identity_map_role": terminal_result(A4AnalysisError("A4-H2-ROLE-NONE", 0), ledger),
        "h3_indirect_traversal": not_applicable_result(),
        "h4_catalog_bootstrap": {
            "root_result": not_applicable_result(),
            "structural_result": not_applicable_result(),
            "encoding_result": not_applicable_result(),
        },
    }
    return layers


class A4FrozenTerminalTests(unittest.TestCase):
    def test_reached_page_recording_is_zero_cost_and_page_exact(self) -> None:
        ledger = WorkLedger()
        page = QualifiedPage(1, "EMPTY", 7)
        self.assertTrue(ledger.record_qualified_page(page))
        self.assertFalse(ledger.record_qualified_page(page))
        self.assertTrue(ledger.record_qualified_page(page, discriminator="second-read"))
        self.assertEqual(ledger.total_work_units, 0)
        self.assertEqual(ledger.qualified_pages(), (page,))

    def test_resume_rejects_oversized_json_before_decode(self) -> None:
        payload = b"[" * (int(BOUNDS["max_json_bytes"]) + 1)
        with self.assertRaisesRegex(ValueError, "registered JSON byte bound"):
            resume_derivation(payload, hashlib.sha256(payload).hexdigest())

    def test_resume_rejects_recursion_bomb_as_malformed_json(self) -> None:
        payload = b"[" * 10_000 + b"]" * 10_000
        with self.assertRaisesRegex(ValueError, "canonical JSON"):
            resume_derivation(payload, hashlib.sha256(payload).hexdigest())

    def test_resume_rejects_rehashed_h1_lifecycle_role_tamper(self) -> None:
        document = copy.deepcopy(dict(_pair_multiple_analysis().frozen.document))
        result = document["layers"]["h1_tdef_to_map_row"]
        candidate = result["candidates"][0]
        candidate["instance_bindings"][0]["logical_role"] = "T2"
        candidate["canonical_model_id"] = canonical_model_id(candidate["model_type"], candidate["model"])
        candidate["canonical_candidate_id"] = canonical_candidate_id(
            candidate["model_type"], candidate["model"], candidate["instance_bindings"]
        )
        result["canonical_candidates_sha256"] = sha256_hex(canonical_json_bytes(result["candidates"]))
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "lifecycle role/range"):
            resume_derivation(payload, digest)

    def test_resume_rejects_rehashed_h1_target_replacement(self) -> None:
        document = copy.deepcopy(dict(_pair_multiple_analysis().frozen.document))
        result = document["layers"]["h1_tdef_to_map_row"]
        candidate = result["candidates"][0]
        targets = candidate["instance_bindings"][0]["locator_targets"]
        target = targets[0]
        target["row"] = next(
            row for row in range(256)
            if row != target["row"]
            and {"page": target["page"], "row": row} != targets[1]
        )
        candidate["canonical_candidate_id"] = canonical_candidate_id(
            candidate["model_type"], candidate["model"], candidate["instance_bindings"]
        )
        result["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(result["candidates"])
        )
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "target binding differs"):
            resume_derivation(payload, digest)

    def test_resume_rejects_rehashed_h1_tdef_replacement(self) -> None:
        document = copy.deepcopy(dict(_pair_multiple_analysis().frozen.document))
        result = document["layers"]["h1_tdef_to_map_row"]
        candidate = result["candidates"][0]
        candidate["instance_bindings"][0]["tdef_page"] += 100
        candidate["canonical_candidate_id"] = canonical_candidate_id(
            candidate["model_type"], candidate["model"], candidate["instance_bindings"]
        )
        result["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(result["candidates"])
        )
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "physical evidence|target binding"):
            resume_derivation(payload, digest)

    def test_resume_rejects_added_or_removed_qualified_page(self) -> None:
        original = _pair_multiple_analysis().frozen.document
        added = copy.deepcopy(dict(original))
        added["qualified_pages"].append({
            "replica": 2,
            "checkpoint_id": "T4_IDLE_R",
            "page_number": int(BOUNDS["max_final_pages_per_replica"]) - 1,
        })
        added["qualified_pages"].sort(
            key=lambda row: (
                row["replica"],
                CHECKPOINT_ORDINALS[row["checkpoint_id"]],
                row["page_number"],
            )
        )
        payload, digest = _rehash(added)
        with self.assertRaisesRegex(ValueError, "qualified-page inventory"):
            resume_derivation(payload, digest)

        removed = copy.deepcopy(dict(original))
        binding = removed["layers"]["h1_tdef_to_map_row"]["candidates"][0][
            "instance_bindings"
        ][0]
        identity = (
            binding["replica"],
            binding["applicable_checkpoint_range"]["start"],
            binding["tdef_page"],
        )
        removed["qualified_pages"] = [
            row for row in removed["qualified_pages"]
            if (row["replica"], row["checkpoint_id"], row["page_number"]) != identity
        ]
        payload, digest = _rehash(removed)
        with self.assertRaisesRegex(ValueError, "qualified-page inventory"):
            resume_derivation(payload, digest)

        decisive = _default_analysis().frozen
        one_replica_removed = copy.deepcopy(dict(decisive.document))
        identities = {
            (row["replica"], row["checkpoint_id"], row["page_number"])
            for row in one_replica_removed["qualified_pages"]
        }
        identity = next(
            row for row in sorted(identities)
            if (3 - row[0], row[1], row[2]) in identities
        )
        one_replica_removed["qualified_pages"] = [
            row for row in one_replica_removed["qualified_pages"]
            if (row["replica"], row["checkpoint_id"], row["page_number"])
            != identity
        ]
        payload, digest = _rehash(one_replica_removed)
        with self.assertRaisesRegex(ValueError, "qualified-page inventory"):
            resume_derivation(payload, digest, decisive.occurrence_evidence_bytes)

    def test_resume_rejects_a_marker_with_a_foreign_stage_discriminator(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        for category, rows in document["transcripts"].items():
            marker = next(
                (row for row in rows if bytes.fromhex(row["detail_hex"]).startswith(
                    _QUALIFIED_PAGE_MARKER
                )),
                None,
            )
            if marker is not None:
                raw = bytearray.fromhex(marker["detail_hex"])
                raw[len(_QUALIFIED_PAGE_MARKER) + 1] = next(
                    code for name, code in _TRANSCRIPT_CATEGORY_CODES.items()
                    if name != category
                )
                marker["detail_hex"] = raw.hex()
                break
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "transcript marker is malformed"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_resume_expected_sha_is_the_external_empirical_trust_anchor(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        marker = next(
            row
            for rows in document["transcripts"].values()
            for row in rows
            if bytes.fromhex(row["detail_hex"]).startswith(_QUALIFIED_PAGE_MARKER)
        )
        raw = bytearray.fromhex(marker["detail_hex"])
        raw[-1] ^= 1
        marker["detail_hex"] = raw.hex()
        payload, replacement_sha = _rehash(document)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            resume_derivation(payload, frozen.sha256, frozen.occurrence_evidence_bytes)
        resume_derivation(payload, replacement_sha, frozen.occurrence_evidence_bytes)

    def test_resume_rejects_mixed_terminal_candidate_replicas(self) -> None:
        document = copy.deepcopy(dict(_pair_multiple_analysis().frozen.document))
        result = document["layers"]["h1_tdef_to_map_row"]
        candidate = result["candidates"][1]
        added_identities: set[tuple[int, str, int]] = set()
        for binding in candidate["instance_bindings"]:
            binding["replica"] = 2
            first = CHECKPOINT_ORDINALS[
                binding["applicable_checkpoint_range"]["start"]
            ]
            last = CHECKPOINT_ORDINALS[
                binding["applicable_checkpoint_range"]["end"]
            ]
            for checkpoint in CHECKPOINT_IDS[first:last + 1]:
                for page in (
                    binding["tdef_page"],
                    *(target["page"] for target in binding["locator_targets"]),
                ):
                    document["qualified_pages"].append({
                        "replica": 2,
                        "checkpoint_id": checkpoint,
                        "page_number": page,
                    })
                    added_identities.add((2, checkpoint, page))
        candidate["canonical_candidate_id"] = canonical_candidate_id(
            candidate["model_type"], candidate["model"], candidate["instance_bindings"]
        )
        result["candidates"].sort(key=lambda row: row["canonical_candidate_id"])
        result["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(result["candidates"])
        )
        unique = {
            (row["replica"], row["checkpoint_id"], row["page_number"]): row
            for row in document["qualified_pages"]
        }
        document["qualified_pages"] = sorted(
            unique.values(),
            key=lambda row: (
                row["replica"], CHECKPOINT_ORDINALS[row["checkpoint_id"]], row["page_number"]
            ),
        )
        for _, checkpoint, page in sorted(added_identities):
            for rows in document["transcripts"].values():
                source = next(
                    (
                        row for row in rows
                        if row["checkpoint_id"] == checkpoint
                        and row["page"] == page
                        and bytes.fromhex(row["detail_hex"]).startswith(
                            _QUALIFIED_PAGE_MARKER
                        )
                    ),
                    None,
                )
                if source is None:
                    continue
                duplicate = copy.deepcopy(source)
                raw = bytearray.fromhex(duplicate["detail_hex"])
                raw[len(_QUALIFIED_PAGE_MARKER)] = 2
                duplicate["detail_hex"] = raw.hex()
                rows.append(duplicate)
                break
        with self.assertRaisesRegex(ValueError, "mixes first-violating replicas"):
            validate_frozen_layers(document["layers"])
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(
            ValueError,
            "qualified-page inventory|mixes first-violating replicas",
        ):
            resume_derivation(payload, digest)

    def test_resume_requires_exact_occurrence_evidence_bytes(self) -> None:
        frozen = _default_analysis().frozen
        self.assertIsNotNone(frozen.occurrence_evidence_bytes)
        with self.assertRaisesRegex(ValueError, "requires occurrence evidence bytes"):
            resume_derivation(frozen.canonical_bytes, frozen.sha256)
        malformed = b"{}"
        with self.assertRaisesRegex(ValueError, "reference mismatch"):
            resume_derivation(frozen.canonical_bytes, frozen.sha256, malformed)

    def test_resume_rejects_coherently_rehashed_occurrence_name_bytes(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        evidence = json.loads(frozen.occurrence_evidence_bytes)
        occurrence = evidence["replica_groups"][0]["operation_bindings"][0][
            "occurrences"
        ][0]
        occurrence["matched_bytes_hex"] = "00" * (
            len(occurrence["matched_bytes_hex"]) // 2
        )
        evidence_bytes = canonical_json_bytes(evidence)
        evidence_digest = sha256_hex(evidence_bytes)
        document["h4_occurrence_evidence"].update({
            "sha256": evidence_digest,
            "size_bytes": len(evidence_bytes),
        })
        h4 = document["layers"]["h4_catalog_bootstrap"]
        structural = h4["structural_result"]["candidates"][0]
        for binding in structural["instance_bindings"]:
            binding["occurrence_evidence_sha256"] = evidence_digest
        structural["canonical_candidate_id"] = canonical_candidate_id(
            structural["model_type"], structural["model"], structural["instance_bindings"]
        )
        h4["structural_result"]["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(h4["structural_result"]["candidates"])
        )
        final = h4["encoding_result"]["candidates"][0]
        for binding in final["instance_bindings"]:
            binding["structural_candidate_id"] = structural["canonical_candidate_id"]
        final["canonical_candidate_id"] = canonical_candidate_id(
            final["model_type"], final["model"], final["instance_bindings"]
        )
        h4["encoding_result"]["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(h4["encoding_result"]["candidates"])
        )
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "registered operation name"):
            resume_derivation(payload, digest, evidence_bytes)

    def test_resume_rejects_rehashed_bitmap_index_absent_from_occurrences(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        h4 = document["layers"]["h4_catalog_bootstrap"]
        structural = h4["structural_result"]["candidates"][0]
        final = h4["encoding_result"]["candidates"][0]
        for binding in structural["instance_bindings"]:
            row = binding["compatible_occurrences_by_operation"][0]
            raw = bytearray.fromhex(row["compatible_occurrence_bitmap_hex"])
            raw[0] = 0b10
            row["compatible_occurrence_bitmap_hex"] = raw.hex()
        structural["canonical_candidate_id"] = canonical_candidate_id(
            structural["model_type"], structural["model"], structural["instance_bindings"]
        )
        h4["structural_result"]["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(h4["structural_result"]["candidates"])
        )
        for binding in final["instance_bindings"]:
            binding["structural_candidate_id"] = structural["canonical_candidate_id"]
            binding["selected_operation_occurrences"][0]["occurrence_index"] = 1
        final["canonical_candidate_id"] = canonical_candidate_id(
            final["model_type"], final["model"], final["instance_bindings"]
        )
        h4["encoding_result"]["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(h4["encoding_result"]["candidates"])
        )
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "absent evidence occurrence"):
            resume_derivation(
                payload,
                digest,
                frozen.occurrence_evidence_bytes,
            )

    def test_resume_rejects_mixed_work_paths_after_rehash(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        charges = document["work_charges"]
        charges["invalid_path_row_directory_entries"] = 1
        charges["total_work_units"] += 1
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "mixes invalid and valid"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_resume_rejects_an_underreported_candidate_serialization_charge(self) -> None:
        frozen = _default_analysis().frozen
        document = copy.deepcopy(dict(frozen.document))
        charges = document["work_charges"]
        charges["total_work_units"] -= charges["candidate_serializations"]
        charges["candidate_serializations"] = 0
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "serialization charge omits"):
            resume_derivation(payload, digest, frozen.occurrence_evidence_bytes)

    def test_rehashed_h4_bitmap_count_tamper_is_rejected(self) -> None:
        digest = "a" * 64
        model = {
            "kind_start_delta": 1,
            "kind_width": 1,
            "identifier_width": 1,
            "endianness": "little",
            "name_length_start_delta": 1,
            "name_length_width": 1,
            "kind_mapping": {"table": 1, "field": 2, "index": 3},
            "identifier_lifecycle": PLAN["candidate_grammars"]["h4"]["identifier_lifecycle_relations"][0],
        }
        rows = []
        for operation in PLAN["candidate_grammars"]["h4"]["operation_binding_order"]:
            maximum = 290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
            rows.append({
                "operation_id": operation,
                "compatible_occurrence_count": 1,
                "compatible_occurrence_bitmap_hex": (b"\x01" + bytes((maximum + 7) // 8 - 1)).hex(),
            })
        candidate = H4Candidate("h4_structural_field", model, ({
            "replica": 1,
            "occurrence_evidence_sha256": digest,
            "value_equivalent_tuple_count": 1,
            "compatible_occurrences_by_operation": rows,
        },)).document()
        candidate["instance_bindings"][0]["compatible_occurrences_by_operation"][0]["compatible_occurrence_count"] = 2
        candidate["canonical_candidate_id"] = canonical_candidate_id(
            candidate["model_type"], candidate["model"], candidate["instance_bindings"]
        )
        with self.assertRaisesRegex(ValueError, "bitmap popcount"):
            _candidate_identity(candidate, digest)

    def test_grouped_candidate_set_rejects_mixed_first_violating_replicas(self) -> None:
        operations = PLAN["candidate_grammars"]["h4"]["operation_binding_order"]
        candidates = []
        groups = []
        for index, operation in enumerate(operations):
            operation_candidates = [] if index == 0 else [
                H4Candidate("h4_operation_record", {
                    "replica": 1 if index % 2 else 2,
                    "root_candidate_id": "a" * 64,
                    "operation_id": operation,
                    "canonical_record_locator": {
                        "page": 10 + index, "row": 0,
                        "row_start": 10, "row_end": 20,
                    },
                }).document()
            ]
            candidates.extend(operation_candidates)
            groups.append({
                "operation_id": operation,
                "cardinality": len(operation_candidates),
                "candidate_ids": [
                    candidate["canonical_candidate_id"]
                    for candidate in operation_candidates
                ],
            })
        candidates.sort(key=lambda row: row["canonical_candidate_id"])
        result = {"candidates": candidates, "predicate_measured_survivor_count": 0}
        with self.assertRaisesRegex(ValueError, "mixes first-violating replicas"):
            _validate_groups(
                "A4-H4-CATALOG-RECORD-NONE",
                result,
                {"kind": "operation_groups", "groups": groups},
                None,
            )

    def test_h4_disagreement_allows_equal_structural_models_only(self) -> None:
        digest = "b" * 64
        h1 = _h2_terminal_layers()["h1_tdef_to_map_row"]
        h2_candidate = H2ReplicaCandidate(1, 0x1FFF, "set_bit_owned_in_use", 0, 1).document()
        h3_candidate = H3Candidate("h3_final_base_formula", {
            "conversion": PLAN["candidate_grammars"]["h3"]["conversion_candidates"][0],
            "base_formula": PLAN["candidate_grammars"]["h3"]["base_formulas"][0],
        }).document()
        root = H4Candidate("h4_catalog_root", {
            "root_selection_signature": PLAN["candidate_grammars"]["h4"]["catalog_root_selection_signatures"][0],
            "locator_offsets": [35, 39],
        }, ({"replica": 1, "tdef_page": 2}, {"replica": 2, "tdef_page": 2})).document()
        structural_model = {
            "kind_start_delta": 1, "kind_width": 1, "identifier_width": 1,
            "endianness": "little", "name_length_start_delta": 1, "name_length_width": 1,
            "kind_mapping": {"table": 1, "field": 2, "index": 3},
            "identifier_lifecycle": PLAN["candidate_grammars"]["h4"]["identifier_lifecycle_relations"][0],
        }
        structures = []
        finals = []
        classes = ("cp1252_single_byte_per_scalar", "utf8_encoded_byte_count")
        for replica in (1, 2):
            compatible = []
            for operation in PLAN["candidate_grammars"]["h4"]["operation_binding_order"]:
                maximum = 290 if operation in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254
                compatible.append({"operation_id": operation, "compatible_occurrence_count": 1,
                                   "compatible_occurrence_bitmap_hex": (b"\x01" + bytes((maximum + 7) // 8 - 1)).hex()})
            structural = H4Candidate("h4_structural_field", structural_model, ({
                "replica": replica, "occurrence_evidence_sha256": digest,
                "value_equivalent_tuple_count": 1,
                "compatible_occurrences_by_operation": compatible,
            },))
            final = H4Candidate("h4_final_encoded_field", {
                "structural_model_id": structural.canonical_model_id,
                "encoding_length_equivalence_class": classes[replica - 1],
            }, ({"replica": replica, "structural_candidate_id": structural.canonical_candidate_id,
                 "selected_operation_occurrences": [{"operation_id": operation, "occurrence_index": 0}
                                                     for operation in PLAN["candidate_grammars"]["h4"]["operation_binding_order"]]},))
            structures.append(structural.document()); finals.append(final.document())
        def pair(candidates):
            return {"kind": "replica_pair", "entries": [
                {"replica": index + 1, "canonical_model_id": candidate["canonical_model_id"],
                 "canonical_candidate_id": candidate["canonical_candidate_id"], "complete_candidate": candidate}
                for index, candidate in enumerate(candidates)
            ]}
        ledger = WorkLedger()
        error = A4AnalysisError("A4-H4-REPLICA-DISAGREEMENT", 2)
        layers = {
            "h1_tdef_to_map_row": h1,
            "h2_row_identity_map_role": decisive_result(h2_candidate, ledger),
            "h3_indirect_traversal": decisive_result(h3_candidate, ledger),
            "h4_catalog_bootstrap": {
                "root_result": decisive_result(root, ledger),
                "structural_result": terminal_result(
                    error,
                    ledger,
                    terminal_evidence=pair(structures),
                    per_replica_counts=(1, 1),
                    candidate_stage="h4_structural_field",
                ),
                "encoding_result": terminal_result(error, ledger, terminal_evidence=pair(finals), per_replica_counts=(1, 1)),
            },
        }
        role_bindings = {
            row["replica"]: row for row in PLAN["tables"]["role_bindings"]
        }
        table_role = {
            operation: operation.split("_", 1)[0]
            for operation in PLAN["candidate_grammars"]["h4"]["operation_binding_order"]
        }
        occurrence_evidence = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a4_h4_occurrence_evidence",
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256,
            "revision_plan_sha256": REVISION_PLAN_SHA256,
            "campaign_id": "semantic-unit",
            "root_candidate_id": root["canonical_candidate_id"],
            "replica_groups": [
                {
                    "replica": replica,
                    "operation_bindings": [
                        {
                            "operation_id": operation,
                            "canonical_record_locator": {
                                "page": 10,
                                "row": 0,
                                "row_start": 10,
                                "row_end": 100,
                            },
                            "occurrences": [{
                                "occurrence_index": 0,
                                "name_start": 20,
                                "matched_registered_pattern_id": (
                                    f"{operation}_UTF8"
                                    if replica == 2 and "É" in (
                                        role_bindings[replica][table_role[operation]]
                                        if operation not in ("T1_ADD_TEXT", "T1_ADD_INDEX")
                                        else "Payload" if operation == "T1_ADD_TEXT" else "A4IX_ID"
                                    )
                                    else f"{operation}_CP1252"
                                ),
                                "matched_bytes_hex": (
                                    role_bindings[replica][table_role[operation]]
                                    if operation not in ("T1_ADD_TEXT", "T1_ADD_INDEX")
                                    else "Payload" if operation == "T1_ADD_TEXT" else "A4IX_ID"
                                ).encode("utf-8" if replica == 2 else "cp1252").hex(),
                            }],
                        }
                        for operation in PLAN["candidate_grammars"]["h4"]["operation_binding_order"]
                    ],
                }
                for replica in (1, 2)
            ],
        }
        validate_frozen_layers(layers, {"sha256": digest}, occurrence_evidence)

    def test_h4_record_terminal_projects_every_reached_catalog_row_page(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        ledger = WorkLedger()
        original = a4_layers.operation_records

        def omit_first_operation(view, root, deltas, h2, work):
            candidates = original(view, root, deltas, h2, work)
            filtered = tuple(
                candidate
                for candidate in candidates
                if candidate.operation_id
                != PLAN["candidate_grammars"]["h4"]["operation_binding_order"][0]
            )
            return filtered

        with patch.object(
            a4_layers,
            "operation_records",
            side_effect=omit_first_operation,
        ):
            terminal = derive_layers(checked, ledger)
        self.assertIsInstance(terminal, DerivationTerminal)
        frozen = freeze_derivation(checked, terminal, ledger)
        raw_pages = {
            (identity[0].replica, identity[0].checkpoint_id, identity[0].page_number)
            for identity in ledger.identities("catalog_raw_rows")
        }
        self.assertTrue(raw_pages)
        qualified = {
            (row["replica"], row["checkpoint_id"], row["page_number"])
            for row in frozen.document["qualified_pages"]
        }
        self.assertTrue(raw_pages <= qualified)
        transcript_pages = {
            (row["checkpoint_id"], row["page"])
            for row in frozen.document["transcripts"]["catalog_fields"]
        }
        self.assertTrue({(checkpoint, page) for _, checkpoint, page in raw_pages} <= transcript_pages)

    def test_h1_terminal_inventory_is_exact_and_does_not_emit_h2_transcript(self) -> None:
        frozen = _pair_multiple_analysis().frozen.document
        h1 = frozen["layers"]["h1_tdef_to_map_row"]
        expected: set[tuple[int, str, int]] = set()
        for candidate in h1["candidates"]:
            for binding in candidate["instance_bindings"]:
                interval = binding["applicable_checkpoint_range"]
                checkpoints = CHECKPOINT_IDS[
                    CHECKPOINT_ORDINALS[interval["start"]] :
                    CHECKPOINT_ORDINALS[interval["end"]] + 1
                ]
                for checkpoint in checkpoints:
                    expected.add(
                        (binding["replica"], checkpoint, binding["tdef_page"])
                    )
                    expected.update(
                        (binding["replica"], checkpoint, target["page"])
                        for target in binding["locator_targets"]
                    )
        actual = {
            (row["replica"], row["checkpoint_id"], row["page_number"])
            for row in frozen["qualified_pages"]
        }
        self.assertTrue(expected <= actual)
        self.assertTrue(frozen["transcripts"]["locators"])
        self.assertEqual(frozen["transcripts"]["row_directories"], [])

    def test_resume_rejects_stale_terminal_candidate_identity(self) -> None:
        frozen = _pair_multiple_analysis().frozen.document
        document = copy.deepcopy(dict(frozen))
        result = document["layers"]["h1_tdef_to_map_row"]
        result["candidates"][0]["model"]["locator_offsets"] = [39, 43]
        result["canonical_candidates_sha256"] = sha256_hex(
            canonical_json_bytes(result["candidates"])
        )
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "identity does not recompute"):
            resume_derivation(payload, digest)

    def test_resume_rejects_stale_embedded_replica_pair_identity(self) -> None:
        checked = check_analysis_input("a4-synthetic", _COMMIT, _inputs())
        ledger = WorkLedger()
        h1_by = {
            replica: derive_h1_replica(
                checked.views[replica], checked.qualified_tdef_pages[replica], ledger
            )
            for replica in (1, 2)
        }
        h1 = agree_h1_replicas(h1_by[1], h1_by[2])
        pair = (
            H2ReplicaCandidate(1, 0x1FFF, "set_bit_owned_in_use", 0, 1),
            H2ReplicaCandidate(2, 0x1FFF, "clear_bit_owned_in_use", 0, 1),
        )
        evidence = {
            "kind": "replica_pair",
            "entries": [
                {
                    "replica": candidate.replica,
                    "canonical_model_id": candidate.canonical_model_id,
                    "canonical_candidate_id": candidate.canonical_candidate_id,
                    "complete_candidate": candidate.document(),
                }
                for candidate in pair
            ],
        }
        h2 = terminal_result(
            A4AnalysisError("A4-H2-REPLICA-DISAGREEMENT", 2),
            ledger,
            terminal_evidence=evidence,
            per_replica_counts=(1, 1),
        )
        layers = {
            "h1_tdef_to_map_row": decisive_result(h1.document(), ledger),
            "h2_row_identity_map_role": h2,
            "h3_indirect_traversal": not_applicable_result(),
            "h4_catalog_bootstrap": {
                "root_result": not_applicable_result(),
                "structural_result": not_applicable_result(),
                "encoding_result": not_applicable_result(),
            },
        }
        frozen = freeze_derivation(
            checked,
            DerivationTerminal("A4-H2-REPLICA-DISAGREEMENT", layers),
            ledger,
        )
        resume_derivation(frozen.canonical_bytes, frozen.sha256)
        document = copy.deepcopy(dict(frozen.document))
        embedded = document["layers"]["h2_row_identity_map_role"][
            "terminal_evidence"
        ]["entries"][0]["complete_candidate"]
        embedded["model"]["row_mask"] = 0x0FFF
        payload, digest = _rehash(document)
        with self.assertRaisesRegex(ValueError, "identity does not recompute"):
            resume_derivation(payload, digest)


if __name__ == "__main__":
    unittest.main()
