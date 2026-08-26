#!/usr/bin/env python3
"""Semantic validation for every frozen A4 derivation outcome."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Mapping

from a4_spec import (
    BOUNDS, CHECKPOINT_IDS, CHECKPOINT_ORDINALS, EXPERIMENT_ID,
    LIFECYCLE_RANGES, PLAN, PLAN_SHA256,
    PREDICATE_CONTRACTS, REVISION_PLAN_SHA256,
    canonical_candidate_id, canonical_model_id, validate_failure_count,
)

_SLOTS = ("h1_tdef_to_map_row", "h2_row_identity_map_role", "h3_indirect_traversal",
          "root_result", "structural_result", "encoding_result")
_DECISIVE_TYPES = {
    "h1_tdef_to_map_row": "h1_locator_pair", "h2_row_identity_map_role": "h2_final_role",
    "h3_indirect_traversal": "h3_final_base_formula", "root_result": "h4_catalog_root",
    "structural_result": "h4_structural_field", "encoding_result": "h4_final_encoded_field",
}
_BOUND_TYPES = {"h1_tdef", "h1_target_valid_layout", "h1_locator_pair", "h4_catalog_root",
                "h4_structural_field", "h4_final_encoded_field"}
_OPERATIONS = tuple(PLAN["candidate_grammars"]["h4"]["operation_binding_order"])
_H1_INSTANCES = tuple(LIFECYCLE_RANGES)
_H1, _H2, _H3, _H4 = (PLAN["candidate_grammars"][key] for key in ("h1", "h2", "h3", "h4"))
_OCCURRENCE_LIMIT = {op: 290 if op in ("T1_ADD_TEXT", "T1_ADD_INDEX") else 254 for op in _OPERATIONS}
_ROLE_BINDINGS = {
    row["replica"]: {key: value for key, value in row.items() if key != "replica"}
    for row in PLAN["tables"]["role_bindings"]
}
_TABLE_ROLE = {
    operation: operation.split("_", 1)[0]
    for operation in _OPERATIONS
    if operation not in ("T1_ADD_TEXT", "T1_ADD_INDEX")
}
_QUALIFIED_PAGE_MARKER = hashlib.sha256(
    b"dao-a4-qualified-page-transcript-v1"
).digest()[:15]
_TRANSCRIPT_CATEGORY_CODES = dict(zip(
    ("locators", "row_directories", "map_transitions", "reference_bitmaps",
     "catalog_roots", "catalog_fields"), range(1, 7), strict=True,
))


def _expected_name(replica: int, operation: str) -> str:
    if operation == "T1_ADD_TEXT":
        return "Payload"
    if operation == "T1_ADD_INDEX":
        return "A4IX_ID"
    return _ROLE_BINDINGS[replica][_TABLE_ROLE[operation]]


def _registered_patterns(replica: int, operation: str) -> Mapping[str, bytes]:
    name = _expected_name(replica, operation)
    cp1252 = name.encode("cp1252", errors="strict")
    utf8 = name.encode("utf-8", errors="strict")
    patterns = {f"{operation}_CP1252": cp1252}
    if utf8 != cp1252:
        patterns[f"{operation}_UTF8"] = utf8
    return patterns


def _results(layers: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    h4 = layers["h4_catalog_bootstrap"]
    return (("h1_tdef_to_map_row", layers["h1_tdef_to_map_row"]),
            ("h2_row_identity_map_role", layers["h2_row_identity_map_role"]),
            ("h3_indirect_traversal", layers["h3_indirect_traversal"]),
            ("root_result", h4["root_result"]), ("structural_result", h4["structural_result"]),
            ("encoding_result", h4["encoding_result"]))


def _binding_checkpoints(binding: Mapping[str, Any]) -> tuple[str, ...]:
    interval = binding["applicable_checkpoint_range"]
    first = CHECKPOINT_ORDINALS[interval["start"]]
    last = CHECKPOINT_ORDINALS[interval["end"]]
    return tuple(CHECKPOINT_IDS[first:last + 1])


def _decode_frozen_locator(raw: bytes, layout: str) -> dict[str, int]:
    if len(raw) != 4:
        raise ValueError("A4 frozen locator transcript is incomplete")
    if layout == "u24le_page_then_u8_row":
        return {"page": int.from_bytes(raw[:3], "little"), "row": raw[3]}
    if layout == "u8_row_then_u24le_page":
        return {"page": int.from_bytes(raw[1:], "little"), "row": raw[0]}
    raise ValueError("A4 frozen locator transcript has an unknown layout")


def _all_frozen_candidates(layers: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    candidates: list[Mapping[str, Any]] = []
    for _, result in _results(layers):
        candidates.extend(result["candidates"])
        evidence = result.get("terminal_evidence")
        if isinstance(evidence, Mapping) and evidence.get("kind") == "replica_pair":
            candidates.extend(entry["complete_candidate"] for entry in evidence["entries"])
    return tuple(candidates)


def _verify_physical_projection(
    document: Mapping[str, Any],
    occurrence_evidence: Mapping[str, Any] | None,
) -> None:
    """Cross-bind every candidate-derived physical identity to frozen evidence."""
    qualified = {
        (row["replica"], row["checkpoint_id"], row["page_number"])
        for row in document["qualified_pages"]
    }
    locators: dict[tuple[str, int], list[bytes]] = {}
    retained: dict[str, dict[tuple[str, int], list[bytes]]] = {
        category: {} for category in _TRANSCRIPT_CATEGORY_CODES
    }
    coverage: dict[tuple[int, str, int], str] = {}
    h4 = document["layers"]["h4_catalog_bootstrap"]
    allowed_marker_categories = {"locators"}
    if document["layers"]["h2_row_identity_map_role"]["status"] != "not_applicable":
        allowed_marker_categories.add("row_directories")
    if document["layers"]["h3_indirect_traversal"]["status"] != "not_applicable":
        allowed_marker_categories.update(("map_transitions", "reference_bitmaps"))
    if h4["root_result"]["status"] != "not_applicable":
        allowed_marker_categories.update(("catalog_roots", "catalog_fields"))
    for category, rows in document["transcripts"].items():
        for row in rows:
            raw = bytes.fromhex(row["detail_hex"])
            if raw.startswith(_QUALIFIED_PAGE_MARKER):
                replica_index = len(_QUALIFIED_PAGE_MARKER)
                if (
                    len(raw) != replica_index + 17
                    or raw[replica_index] not in (1, 2)
                    or raw[replica_index + 1] != _TRANSCRIPT_CATEGORY_CODES[category]
                    or category not in allowed_marker_categories
                ):
                    raise ValueError("A4 frozen qualified-page transcript marker is malformed")
                identity = (raw[replica_index], row["checkpoint_id"], row["page"])
                if identity in coverage:
                    raise ValueError(
                        "A4 frozen qualified-page inventory has a duplicated transcript marker"
                    )
                coverage[identity] = category
            else:
                pair = (row["checkpoint_id"], row["page"])
                retained[category].setdefault(pair, []).append(raw)
                if category == "locators":
                    locators.setdefault(pair, []).append(raw)
    if not set(coverage) <= qualified:
        raise ValueError("A4 frozen qualified-page inventory differs from transcript evidence")

    required: set[tuple[int, str, int]] = set()
    required_categories: dict[tuple[int, str, int], str] = {}
    raw_evidenced: set[tuple[int, str, int]] = set()
    operation_locators: dict[tuple[int, str], set[tuple[int, int, int, int]]] = {}
    occurrence_locators: dict[tuple[int, str], set[tuple[int, int, int, int]]] = {}

    def require(identity: tuple[int, str, int], category: str) -> None:
        previous = required_categories.setdefault(identity, category)
        if previous != category:
            raise ValueError("A4 frozen physical evidence has conflicting stage roles")
        required.add(identity)

    for candidate in _all_frozen_candidates(document["layers"]):
        model_type = candidate["model_type"]
        if model_type.startswith("h1_"):
            offsets = candidate["model"].get("locator_offsets")
            for binding in candidate["instance_bindings"]:
                for checkpoint in _binding_checkpoints(binding):
                    required.add((binding["replica"], checkpoint, binding["tdef_page"]))
                    targets = binding.get("locator_targets", ())
                    required.update(
                        (binding["replica"], checkpoint, target["page"])
                        for target in targets
                    )
                    if offsets is not None and not any(
                        len(raw) == 8
                        and [_decode_frozen_locator(
                            raw[index:index + 4], candidate["model"]["layout"]
                        ) for index in (0, 4)] == list(targets)
                        for raw in locators.get((checkpoint, binding["tdef_page"]), ())
                    ):
                        raise ValueError(
                            "A4 frozen H1 target binding differs from its locator transcript"
                        )
        elif model_type == "h4_catalog_root":
            expected = bytes(candidate["model"]["locator_offsets"])
            for binding in candidate["instance_bindings"]:
                for checkpoint in CHECKPOINT_IDS:
                    require(
                        (binding["replica"], checkpoint, binding["tdef_page"]),
                        "catalog_roots",
                    )
                if expected not in retained["catalog_roots"].get(
                    ("EMPTY", binding["tdef_page"]), ()
                ):
                    raise ValueError("A4 frozen H4 root lacks its retained transcript")
        elif model_type == "h4_operation_record":
            model = candidate["model"]
            locator = model["canonical_record_locator"]
            pair = (model["operation_id"], locator["page"])
            identity = (model["replica"], *pair)
            require(identity, "catalog_fields")
            operation_locators.setdefault(
                (model["replica"], model["operation_id"]), set()
            ).add((locator["page"], locator["row"], locator["row_start"], locator["row_end"]))

    if occurrence_evidence is not None:
        for group in occurrence_evidence["replica_groups"]:
            for operation in group["operation_bindings"]:
                locator = operation["canonical_record_locator"]
                pair = (operation["operation_id"], locator["page"])
                identity = (group["replica"], *pair)
                require(identity, "catalog_fields")
                raw_evidenced.add(identity)
                occurrence_locators.setdefault(
                    (group["replica"], operation["operation_id"]), set()
                ).add((locator["page"], locator["row"], locator["row_start"], locator["row_end"]))

    for scope in operation_locators.keys() & occurrence_locators.keys():
        if operation_locators[scope] != occurrence_locators[scope]:
            raise ValueError("A4 H4 occurrence locator differs from its retained record")
    required_lengths: dict[tuple[str, int], Counter[int]] = {}
    for replica, operation in occurrence_locators:
        rows = occurrence_locators[(replica, operation)]
        for page in {row[0] for row in rows}:
            lengths = required_lengths.setdefault((operation, page), Counter())
            lengths.update(
                min(row[3] - row[2], 64) for row in rows if row[0] == page
            )
    if any(
        any(Counter(map(len, retained["catalog_fields"].get(pair, ())))[length] < count
            for length, count in lengths.items())
        for pair, lengths in required_lengths.items()
    ):
        raise ValueError("A4 frozen H4 retained record transcript cardinality differs")

    if not qualified - set(coverage) <= raw_evidenced:
        raise ValueError("A4 frozen qualified-page inventory differs from transcript evidence")
    if not required <= qualified:
        raise ValueError("A4 frozen qualified-page inventory omits retained physical evidence")
    if any(identity in coverage and coverage[identity] != category
           for identity, category in required_categories.items()):
        raise ValueError("A4 frozen qualified-page marker differs from candidate stage")
    transcript_pairs = {
        (row["checkpoint_id"], row["page"])
        for rows in document["transcripts"].values()
        for row in rows
    }
    if transcript_pairs != {(identity[1], identity[2]) for identity in qualified}:
        raise ValueError("A4 frozen qualified-page inventory differs from transcript evidence")


def _binding_replicas(candidate: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(binding["replica"] for binding in candidate.get("instance_bindings", ()))


def _validate_h1(candidate: Mapping[str, Any]) -> None:
    model_type, model = candidate["model_type"], candidate["model"]
    bindings = candidate["instance_bindings"]
    replicas = _binding_replicas(candidate)
    if not replicas or replicas not in ((1,) * 5, (2,) * 5, (1,) * 5 + (2,) * 5):
        raise ValueError("A4 frozen H1 bindings have an invalid replica order")
    for start in range(0, len(bindings), 5):
        group = bindings[start:start + 5]
        if tuple(row["lifecycle_instance"] for row in group) != _H1_INSTANCES:
            raise ValueError("A4 frozen H1 lifecycle bindings are incomplete or reordered")
        for row in group:
            lifecycle = LIFECYCLE_RANGES[row["lifecycle_instance"]]
            expected_range = {"start": lifecycle.first_checkpoint, "end": lifecycle.last_checkpoint}
            if row["logical_role"] != lifecycle.logical_role or row["applicable_checkpoint_range"] != expected_range:
                raise ValueError("A4 frozen H1 lifecycle role/range differs from the plan")
            if isinstance(row["tdef_page"], bool) or not 0 <= row["tdef_page"] < int(BOUNDS["max_final_pages_per_replica"]):
                raise ValueError("A4 frozen H1 TDEF page is outside the plan bound")
            targets = row.get("locator_targets")
            if model_type == "h1_tdef":
                if targets is not None:
                    raise ValueError("A4 frozen H1 TDEF candidate has downstream targets")
            elif not isinstance(targets, list) or len(targets) != 2 or targets[0] == targets[1]:
                raise ValueError("A4 frozen H1 locator binding lacks two distinct targets")
            elif any(isinstance(t.get("page"), bool) or not 0 <= t.get("page", -1) < int(BOUNDS["max_final_pages_per_replica"])
                     or isinstance(t.get("row"), bool) or not 0 <= t.get("row", -1) <= 255 for t in targets):
                raise ValueError("A4 frozen H1 locator target is outside its registered range")
    if model_type == "h1_tdef":
        if set(model) != {"tdef_lifecycle_signature"} or model["tdef_lifecycle_signature"] not in _H1["tdef_lifecycle_signatures"]:
            raise ValueError("A4 frozen H1 TDEF model is outside the grammar")
        return
    signatures = {_H1["table_record_signature"]["signature_id"],
                  _H1["pair_multiple_reachability_signature"]["signature_id"]}
    if model.get("layout") not in _H1["locator_layouts"] or model.get("table_signature_id") not in signatures:
        raise ValueError("A4 frozen H1 locator model is outside the grammar")
    if model_type == "h1_target_valid_layout":
        if set(model) != {"layout", "table_signature_id"}:
            raise ValueError("A4 frozen H1 layout model has downstream fields")
        return
    signature = (_H1["table_record_signature"] if model["table_signature_id"] == _H1["table_record_signature"]["signature_id"]
                 else _H1["pair_multiple_reachability_signature"])
    holes = signature["locator_holes"]
    allowed = {tuple(sorted((left[0], right[0]))) for i, left in enumerate(holes) for right in holes[i + 1:]}
    offsets = model.get("locator_offsets")
    if set(model) != {"layout", "table_signature_id", "locator_offsets"} or not isinstance(offsets, list) or tuple(offsets) not in allowed:
        raise ValueError("A4 frozen H1 locator offsets are not derived from the signature holes")
    multiple = _H1["pair_multiple_reachability_signature"]
    if model["table_signature_id"] == multiple["signature_id"]:
        equality = multiple["equal_byte_intervals"][0]
        if tuple(offsets) == (equality["left"][0], equality["right"][0]):
            raise ValueError("A4 frozen H1 target-valid candidate uses duplicate locator holes")


def _validate_h2(model: Mapping[str, Any]) -> None:
    ordinals = (model.get("owned_in_use_locator_ordinal"), model.get("available_locator_ordinal"))
    if (set(model) != {"row_mask", "polarity", "owned_in_use_locator_ordinal", "available_locator_ordinal"}
            or model.get("row_mask") not in _H2["row_masks"] or model.get("polarity") not in _H2["type_0_polarities"]
            or set(ordinals) != {0, 1}):
        raise ValueError("A4 frozen H2 model is outside the registered grammar")


def _validate_h3(model_type: str, model: Mapping[str, Any]) -> None:
    expected = {"conversion": _H3["conversion_candidates"][0]}
    if model_type == "h3_final_base_formula":
        if model.get("base_formula") not in _H3["base_formulas"]:
            raise ValueError("A4 frozen H3 base formula is outside the grammar")
        expected["base_formula"] = model["base_formula"]
    if dict(model) != expected:
        raise ValueError("A4 frozen H3 model is outside the registered grammar")


def _bitmap_members(raw_hex: Any, maximum: int) -> frozenset[int]:
    if not isinstance(raw_hex, str):
        raise ValueError("A4 frozen H4 occurrence bitmap is not hexadecimal")
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise ValueError("A4 frozen H4 occurrence bitmap is not hexadecimal") from exc
    if len(raw) != (maximum + 7) // 8:
        raise ValueError("A4 frozen H4 occurrence bitmap has the wrong width")
    members = frozenset(i for i in range(len(raw) * 8) if raw[i // 8] & (1 << (i % 8)))
    if any(i >= maximum for i in members):
        raise ValueError("A4 frozen H4 occurrence bitmap sets an out-of-range bit")
    return members


def _validate_h4(candidate: Mapping[str, Any], occurrence_sha256: str | None) -> None:
    model_type, model = candidate["model_type"], candidate["model"]
    bindings, replicas = candidate.get("instance_bindings", ()), _binding_replicas(candidate)
    if model_type == "h4_operation_record":
        locator = model.get("canonical_record_locator")
        if (set(model) != {"replica", "root_candidate_id", "operation_id", "canonical_record_locator"}
                or model.get("replica") not in (1, 2) or model.get("operation_id") not in _OPERATIONS
                or not isinstance(locator, Mapping) or set(locator) != {"page", "row", "row_start", "row_end"}
                or not 0 <= locator["row_start"] < locator["row_end"] <= int(BOUNDS["page_size"])):
            raise ValueError("A4 frozen H4 operation record is malformed")
        return
    if replicas not in ((1,), (2,), (1, 2)):
        raise ValueError("A4 frozen H4 bindings are missing, duplicated, or reordered")
    if model_type == "h4_catalog_root":
        if set(model) != {"root_selection_signature", "locator_offsets"} or model.get("root_selection_signature") not in _H4["catalog_root_selection_signatures"]:
            raise ValueError("A4 frozen H4 root model is outside the grammar")
        offsets = model.get("locator_offsets")
        if not isinstance(offsets, list) or len(offsets) != 2 or offsets != sorted(offsets) or len(set(offsets)) != 2:
            raise ValueError("A4 frozen H4 root locator offsets are malformed")
        return
    if model_type == "h4_structural_field":
        fields = {"kind_start_delta", "kind_width", "identifier_width", "endianness", "name_length_start_delta", "name_length_width", "kind_mapping", "identifier_lifecycle"}
        if (set(model) != fields or model["kind_start_delta"] not in _H4["kind_start_deltas"]
                or model["kind_width"] not in _H4["kind_widths"] or model["identifier_width"] not in _H4["identifier_widths"]
                or model["endianness"] not in _H4["endianness"] or model["name_length_start_delta"] not in _H4["name_length_start_deltas"]
                or model["name_length_width"] not in _H4["name_length_widths"] or model["identifier_lifecycle"] not in _H4["identifier_lifecycle_relations"]):
            raise ValueError("A4 frozen H4 structural model is outside the grammar")
        mapping = model["kind_mapping"]
        if (set(mapping) != {"table", "field", "index"} or len(set(mapping.values())) != 3
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or not 0 <= value < 1 << (8 * model["kind_width"])
                       for value in mapping.values())):
            raise ValueError("A4 frozen H4 kind mapping is not a three-value bijection")
        if occurrence_sha256 is None:
            raise ValueError("A4 frozen H4 structural candidate lacks occurrence evidence")
        for binding in bindings:
            if binding.get("occurrence_evidence_sha256") != occurrence_sha256:
                raise ValueError("A4 frozen H4 structural candidate is bound to different occurrence evidence")
            count = binding.get("value_equivalent_tuple_count")
            if isinstance(count, bool) or not 1 <= count <= int(BOUNDS["max_h4_value_equivalent_tuples"]):
                raise ValueError("A4 frozen H4 value-equivalent count is outside its bound")
            rows = binding.get("compatible_occurrences_by_operation")
            if not isinstance(rows, list) or [row.get("operation_id") for row in rows] != list(_OPERATIONS):
                raise ValueError("A4 frozen H4 compatible occurrences are incomplete or reordered")
            for row in rows:
                members = _bitmap_members(row.get("compatible_occurrence_bitmap_hex"), _OCCURRENCE_LIMIT[row["operation_id"]])
                if row.get("compatible_occurrence_count") != len(members) or not members:
                    raise ValueError("A4 frozen H4 bitmap popcount differs from its reported count")
        return
    if model_type == "h4_final_encoded_field":
        classes = {row["id"] for row in _H4["name_length_equivalence_classes"]}
        if set(model) != {"structural_model_id", "encoding_length_equivalence_class"} or model["encoding_length_equivalence_class"] not in classes:
            raise ValueError("A4 frozen H4 final model is outside the grammar")
        for binding in bindings:
            selected = binding.get("selected_operation_occurrences")
            if not isinstance(selected, list) or [row.get("operation_id") for row in selected] != list(_OPERATIONS):
                raise ValueError("A4 frozen H4 final selections are incomplete or reordered")
            if any(isinstance(row.get("occurrence_index"), bool) or not 0 <= row.get("occurrence_index", -1) < _OCCURRENCE_LIMIT[row["operation_id"]] for row in selected):
                raise ValueError("A4 frozen H4 final selection is outside the occurrence range")
        return
    raise ValueError("A4 frozen candidate has an unregistered model type")


def _candidate_identity(candidate: Mapping[str, Any], occurrence_sha256: str | None = None) -> tuple[str, str]:
    model_type, model = candidate["model_type"], candidate["model"]
    model_id = canonical_model_id(model_type, model)
    bindings = candidate.get("instance_bindings") if model_type in _BOUND_TYPES else None
    candidate_id = canonical_candidate_id(model_type, model, bindings)
    if candidate["canonical_candidate_id"] != candidate_id:
        raise ValueError("A4 frozen candidate identity does not recompute")
    if model_type in _BOUND_TYPES:
        if candidate.get("canonical_model_id") != model_id:
            raise ValueError("A4 frozen model identity does not recompute")
    elif "canonical_model_id" in candidate:
        raise ValueError("A4 frozen candidate has an unregistered model identity")
    if model_type.startswith("h1_"): _validate_h1(candidate)
    elif model_type == "h2_final_role": _validate_h2(model)
    elif model_type.startswith("h3_"): _validate_h3(model_type, model)
    elif model_type.startswith("h4_"): _validate_h4(candidate, occurrence_sha256)
    else: raise ValueError("A4 frozen candidate has an unregistered model type")
    return model_id, candidate_id


def _validate_replica_pair(predicate_id: str, slot: str, result: Mapping[str, Any], evidence: Mapping[str, Any], occurrence_sha256: str | None) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    entries = evidence.get("entries")
    if evidence.get("kind") != "replica_pair" or not isinstance(entries, list) or len(entries) != 2 or [e.get("replica") for e in entries] != [1, 2]:
        raise ValueError(f"{predicate_id}: invalid or reordered frozen replica pair")
    models, candidates = [], []
    for entry in entries:
        candidate = entry.get("complete_candidate")
        if not isinstance(candidate, Mapping):
            raise ValueError(f"{predicate_id}: replica pair lacks a complete candidate")
        model_id, candidate_id = _candidate_identity(candidate, occurrence_sha256)
        expected_type = "h4_structural_field" if slot == "structural_result" else result["terminal_candidate_stage"]
        if candidate["model_type"] != expected_type:
            raise ValueError(f"{predicate_id}: replica-pair candidate has the wrong stage")
        replicas = _binding_replicas(candidate)
        if replicas and set(replicas) != {entry["replica"]}:
            raise ValueError(f"{predicate_id}: replica-pair candidate is bound to another replica")
        if candidate["model_type"] == "h4_operation_record" and candidate["model"]["replica"] != entry["replica"]:
            raise ValueError(f"{predicate_id}: replica-pair operation record is cross-replica")
        if entry.get("canonical_model_id") != model_id or entry.get("canonical_candidate_id") != candidate_id:
            raise ValueError(f"{predicate_id}: replica-pair identity does not recompute")
        models.append(model_id); candidates.append(candidate)
    if result["predicate_measured_survivor_count"] != 2:
        raise ValueError(f"{predicate_id}: replica pair has the wrong measured count")
    if predicate_id != "A4-H4-REPLICA-DISAGREEMENT" and len(set(models)) != 2:
        raise ValueError(f"{predicate_id}: replica pair is not a measured disagreement")
    return candidates[0], candidates[1]


def _validate_groups(predicate_id: str, result: Mapping[str, Any], evidence: Mapping[str, Any], occurrence_sha256: str | None) -> None:
    groups = evidence.get("groups")
    if evidence.get("kind") != "operation_groups" or not isinstance(groups, list) or [g.get("operation_id") for g in groups] != list(_OPERATIONS):
        raise ValueError(f"{predicate_id}: invalid or reordered operation groups")
    candidates = result["candidates"]
    by_id: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        _candidate_identity(candidate, occurrence_sha256)
        by_id[candidate["canonical_candidate_id"]] = candidate
    if len(by_id) != len(candidates):
        raise ValueError(f"{predicate_id}: duplicate retained operation candidate")
    grouped, counts = set(), []
    for group in groups:
        ids, count = group.get("candidate_ids"), group.get("cardinality")
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or count != len(ids):
            raise ValueError(f"{predicate_id}: operation-group cardinality differs")
        expected_ids = sorted(i for i, candidate in by_id.items() if candidate["model"]["operation_id"] == group["operation_id"])
        if ids != expected_ids:
            raise ValueError(f"{predicate_id}: operation-group membership differs by operation")
        grouped.update(ids); counts.append(count)
    measured = result["predicate_measured_survivor_count"]
    expected = min(counts) if predicate_id.endswith("RECORD-NONE") else max(counts)
    if set(by_id) != grouped or measured != expected:
        raise ValueError(f"{predicate_id}: grouped terminal union differs")
    replicas = {candidate["model"]["replica"] for candidate in candidates}
    if len(replicas) > 1:
        raise ValueError(f"{predicate_id}: grouped candidate set mixes first-violating replicas")


def _validate_terminal_payload(slot: str, result: Mapping[str, Any], immediate_input_id: str | None, occurrence_sha256: str | None) -> None:
    predicate_id = result["terminal_predicate_id"]
    contract = PREDICATE_CONTRACTS.get(predicate_id)
    if contract is None or slot not in contract["result_slots"]:
        raise ValueError(f"{predicate_id}: terminal occupies an unregistered result slot")
    expected_stage = (
        "h4_structural_field"
        if predicate_id == "A4-H4-REPLICA-DISAGREEMENT"
        and slot == "structural_result"
        else contract["candidate_stage"]
    )
    if result["terminal_payload_kind"] != contract["terminal_payload_schema"] or result["terminal_candidate_stage"] != expected_stage or result["derivation_survivor_count"] != 0:
        raise ValueError(f"{predicate_id}: terminal projection differs from its contract")
    payload, measured = contract["terminal_payload_schema"], result["predicate_measured_survivor_count"]
    validate_failure_count(predicate_id, measured, per_replica_counts=(1, 1) if payload == "replica_pair" else None)
    candidates, evidence = result["candidates"], result["terminal_evidence"]
    if payload == "candidate_set":
        if len(candidates) != measured or evidence is not None: raise ValueError(f"{predicate_id}: candidate-set payload differs from its count")
        scopes: set[int] = set()
        for candidate in candidates:
            replicas = set(_binding_replicas(candidate))
            if candidate["model_type"] == "h4_operation_record":
                replicas = {candidate["model"]["replica"]}
            if replicas:
                if len(replicas) != 1:
                    raise ValueError(f"{predicate_id}: candidate-set entry is not replica-local")
                scopes.update(replicas)
        if len(scopes) > 1:
            raise ValueError(f"{predicate_id}: candidate set mixes first-violating replicas")
    elif payload == "replica_pair":
        if candidates or not isinstance(evidence, Mapping): raise ValueError(f"{predicate_id}: replica-pair payload is incomplete")
        _validate_replica_pair(predicate_id, slot, result, evidence, occurrence_sha256)
    elif payload == "grouped_candidate_set":
        if not isinstance(evidence, Mapping): raise ValueError(f"{predicate_id}: grouped payload is incomplete")
        _validate_groups(predicate_id, result, evidence, occurrence_sha256)
    elif payload == "invalid_observation":
        if not isinstance(evidence, Mapping) or len(candidates) > 1: raise ValueError(f"{predicate_id}: invalid-observation payload is incomplete")
        expected = candidates[0]["canonical_candidate_id"] if candidates else immediate_input_id
        if expected is None or evidence.get("input_model_id") != expected: raise ValueError(f"{predicate_id}: invalid observation is not bound to its immediate input")
        if evidence.get("kind") in ("replica_pair", "operation_groups"):
            raise ValueError(f"{predicate_id}: invalid observation has a foreign evidence kind")
        expected_kinds = {
            "A4-H2-ROW-DIRECTORY-INVALID": "row_directory",
            "A4-H2-ROW-FLAGS-INVALID": "row_flags",
            "A4-H2-MAP-TAG-UNSUPPORTED": "map_tag",
            "A4-H3-REFERENCE-INVALID": "reference",
            "A4-H4-SCHEMA-DELTA-OUTSIDE-OWNED": "schema_delta_outside",
        }
        if evidence.get("kind") != expected_kinds.get(predicate_id):
            raise ValueError(f"{predicate_id}: invalid observation kind differs from the predicate")
        if not isinstance(evidence.get("observation"), Mapping): raise ValueError(f"{predicate_id}: invalid observation is absent")
    else: raise ValueError(f"{predicate_id}: unknown frozen terminal payload")


def _validate_occurrence_evidence(
    evidence: Mapping[str, Any],
    transcripts: Mapping[str, Any] | None,
    qualified_pages: list[Mapping[str, Any]] | None,
) -> tuple[
    dict[tuple[int, str], frozenset[int]],
    dict[tuple[int, str, int], Mapping[str, Any]],
]:
    """Validate the complete plan-derived meaning of each retained occurrence."""
    if (
        evidence.get("experiment_id") != EXPERIMENT_ID
        or evidence.get("plan_sha256") != PLAN_SHA256
        or evidence.get("revision_plan_sha256") != REVISION_PLAN_SHA256
    ):
        raise ValueError("A4 H4 occurrence evidence metadata differs from the plan")
    groups = evidence.get("replica_groups")
    if not isinstance(groups, list) or [group.get("replica") for group in groups] != [1, 2]:
        raise ValueError("A4 H4 occurrence evidence replica groups are reordered")
    qualified = None if qualified_pages is None else {
        (row["replica"], row["checkpoint_id"], row["page_number"])
        for row in qualified_pages
    }
    retained_rows: dict[tuple[str, int], list[bytes]] = {}
    if isinstance(transcripts, Mapping):
        for row in transcripts.get("catalog_fields", ()):
            raw = bytes.fromhex(row["detail_hex"])
            if not raw.startswith(_QUALIFIED_PAGE_MARKER):
                retained_rows.setdefault(
                    (row["checkpoint_id"], row["page"]), []
                ).append(raw)
    indexes_by_group: dict[tuple[int, str], frozenset[int]] = {}
    occurrences_by_key: dict[tuple[int, str, int], Mapping[str, Any]] = {}
    total = 0
    for group in groups:
        replica = group["replica"]
        operations = group.get("operation_bindings")
        if (
            not isinstance(operations, list)
            or [row.get("operation_id") for row in operations] != list(_OPERATIONS)
        ):
            raise ValueError("A4 H4 occurrence operations are incomplete or reordered")
        for operation in operations:
            operation_id = operation["operation_id"]
            locator = operation.get("canonical_record_locator")
            if (
                not isinstance(locator, Mapping)
                or set(locator) != {"page", "row", "row_start", "row_end"}
                or isinstance(locator["page"], bool)
                or not 0 <= locator["page"] < int(BOUNDS["max_final_pages_per_replica"])
                or isinstance(locator["row"], bool)
                or not 0 <= locator["row"] <= 255
                or not 10 <= locator["row_start"] < locator["row_end"] <= int(BOUNDS["page_size"])
            ):
                raise ValueError("A4 H4 occurrence record locator is malformed")
            if qualified is not None and (
                replica, operation_id, locator["page"]
            ) not in qualified:
                raise ValueError("A4 H4 occurrence record page is absent from qualified evidence")
            patterns = _registered_patterns(replica, operation_id)
            rows = operation.get("occurrences")
            if not isinstance(rows, list):
                raise ValueError("A4 H4 occurrence rows are malformed")
            indexes = tuple(row.get("occurrence_index") for row in rows)
            if indexes != tuple(range(len(rows))):
                raise ValueError("A4 H4 occurrence evidence indexes are not canonical and contiguous")
            if len(rows) > _OCCURRENCE_LIMIT[operation_id]:
                raise ValueError("A4 H4 operation occurrence count exceeds its bound")
            identities: list[tuple[int, str, str]] = []
            row_length = locator["row_end"] - locator["row_start"]
            for row in rows:
                pattern_id = row.get("matched_registered_pattern_id")
                try:
                    matched = bytes.fromhex(row.get("matched_bytes_hex", ""))
                except (TypeError, ValueError) as exc:
                    raise ValueError("A4 H4 occurrence bytes are malformed") from exc
                if pattern_id not in patterns or matched != patterns[pattern_id]:
                    raise ValueError("A4 H4 occurrence bytes differ from the registered operation name")
                name_start = row.get("name_start")
                if (
                    isinstance(name_start, bool)
                    or not isinstance(name_start, int)
                    or not 0 <= name_start
                    or name_start + len(matched) > row_length
                ):
                    raise ValueError("A4 H4 occurrence name range is outside its record")
                identity = (name_start, pattern_id, matched.hex())
                identities.append(identity)
                retained = [
                    payload
                    for payload in retained_rows.get((operation_id, locator["page"]), ())
                    if len(payload) >= name_start + len(matched)
                ]
                if isinstance(transcripts, Mapping) and (
                    not retained or not any(
                        payload[name_start:name_start + len(matched)] == matched
                        for payload in retained
                    )
                ):
                    raise ValueError("A4 H4 occurrence bytes differ from retained record evidence")
                occurrences_by_key[(replica, operation_id, row["occurrence_index"])] = row
            if identities != sorted(set(identities)):
                raise ValueError("A4 H4 occurrence identities are duplicated or reordered")
            indexes_by_group[(replica, operation_id)] = frozenset(indexes)
            total += len(rows)
    if total > int(BOUNDS["max_h4_occurrence_identities"]):
        raise ValueError("A4 H4 occurrence identity total exceeds its bound")
    return indexes_by_group, occurrences_by_key


def _h4_cross_stage(
    rows: Mapping[str, Mapping[str, Any]],
    occurrence_sha256: str | None,
    occurrence_evidence: Mapping[str, Any] | None,
    transcripts: Mapping[str, Any] | None,
    qualified_pages: list[Mapping[str, Any]] | None,
) -> None:
    sr, fr = rows["structural_result"], rows["encoding_result"]
    root = rows["root_result"]
    root_ids = {candidate["canonical_candidate_id"] for candidate in root["candidates"]}
    for candidate in root["candidates"]:
        for binding in candidate.get("instance_bindings", ()):
            root_ids.add(canonical_candidate_id(candidate["model_type"], candidate["model"], [binding]))
    root_evidence = root.get("terminal_evidence")
    if isinstance(root_evidence, Mapping) and root_evidence.get("kind") == "replica_pair":
        root_ids.update(entry["canonical_candidate_id"] for entry in root_evidence["entries"])
    for candidate in sr["candidates"]:
        if candidate["model_type"] == "h4_operation_record" and candidate["model"]["root_candidate_id"] not in root_ids:
            raise ValueError("A4 frozen H4 operation record is orphaned from its root candidate")
    structural, final = list(sr["candidates"]), list(fr["candidates"])
    if isinstance(sr.get("terminal_evidence"), Mapping) and sr["terminal_evidence"].get("kind") == "replica_pair":
        structural = [e["complete_candidate"] for e in sr["terminal_evidence"]["entries"]]
    if isinstance(fr.get("terminal_evidence"), Mapping) and fr["terminal_evidence"].get("kind") == "replica_pair":
        final = [e["complete_candidate"] for e in fr["terminal_evidence"]["entries"]]
    began = (any(c["model_type"] == "h4_structural_field" for c in structural)
             or sr.get("terminal_candidate_stage") == "h4_structural_field"
             or sr.get("terminal_payload_kind") == "replica_pair")
    if began != (occurrence_sha256 is not None):
        raise ValueError("A4 frozen H4 occurrence evidence presence differs from reached stages")
    if began and occurrence_evidence is None:
        raise ValueError("A4 frozen H4 candidates lack canonical occurrence evidence bytes")
    evidence_indexes: dict[tuple[int, str], frozenset[int]] = {}
    evidence_occurrences: dict[tuple[int, str, int], Mapping[str, Any]] = {}
    if occurrence_evidence is not None:
        root_candidates = root["candidates"]
        if len(root_candidates) == 1 and occurrence_evidence.get("root_candidate_id") != root_candidates[0]["canonical_candidate_id"]:
            raise ValueError("A4 H4 occurrence evidence is bound to another root candidate")
        evidence_indexes, evidence_occurrences = _validate_occurrence_evidence(
            occurrence_evidence, transcripts, qualified_pages
        )
        for candidate in structural:
            if candidate["model_type"] != "h4_structural_field":
                continue
            for binding in candidate["instance_bindings"]:
                for row in binding["compatible_occurrences_by_operation"]:
                    members = _bitmap_members(
                        row["compatible_occurrence_bitmap_hex"],
                        _OCCURRENCE_LIMIT[row["operation_id"]],
                    )
                    if not members <= evidence_indexes[(binding["replica"], row["operation_id"])]:
                        raise ValueError("A4 frozen H4 bitmap selects an absent evidence occurrence")
    if not final: return
    classes: set[tuple[tuple[int, ...], str]] = set()
    for candidate in final:
        scope = _binding_replicas(candidate)
        linked_candidates = [
            structural_candidate
            for structural_candidate in structural
            if structural_candidate["model_type"] == "h4_structural_field"
            and set(scope) <= set(_binding_replicas(structural_candidate))
            and candidate["model"].get("structural_model_id")
            == structural_candidate["canonical_model_id"]
        ]
        if len(linked_candidates) != 1:
            raise ValueError("A4 frozen H4 final model is orphaned from its structural model")
        linked = linked_candidates[0]
        by_replica = {b["replica"]: b for b in linked["instance_bindings"]}
        for binding in candidate["instance_bindings"]:
            structural_binding = by_replica[binding["replica"]]
            if binding.get("structural_candidate_id") != linked["canonical_candidate_id"]:
                raise ValueError("A4 frozen H4 final binding is orphaned from its structural candidate")
            bitmaps = {r["operation_id"]: _bitmap_members(r["compatible_occurrence_bitmap_hex"], _OCCURRENCE_LIMIT[r["operation_id"]]) for r in structural_binding["compatible_occurrences_by_operation"]}
            if any(r["occurrence_index"] not in bitmaps[r["operation_id"]] for r in binding["selected_operation_occurrences"]):
                raise ValueError("A4 frozen H4 final occurrence is absent from the structural bitmap")
            class_id = candidate["model"]["encoding_length_equivalence_class"]
            for selected in binding["selected_operation_occurrences"]:
                occurrence = evidence_occurrences[
                    (binding["replica"], selected["operation_id"], selected["occurrence_index"])
                ]
                payload = bytes.fromhex(occurrence["matched_bytes_hex"])
                name = _expected_name(binding["replica"], selected["operation_id"])
                expected = (
                    name.encode("cp1252", errors="strict")
                    if class_id == "cp1252_single_byte_per_scalar"
                    else name.encode("utf-8", errors="strict")
                )
                if payload != expected:
                    raise ValueError("A4 frozen H4 final occurrence differs from its encoding class")
        class_key = (scope, candidate["model"]["encoding_length_equivalence_class"])
        if class_key in classes:
            raise ValueError("A4 frozen H4 final candidates duplicate an equivalence class")
        classes.add(class_key)
    if fr.get("terminal_predicate_id") == "A4-H4-REPLICA-DISAGREEMENT" and len({c["canonical_model_id"] for c in final}) != 2:
        raise ValueError("A4-H4-REPLICA-DISAGREEMENT: final replica models are equal")


def validate_frozen_layers(
    layers: Mapping[str, Any],
    occurrence_reference: Mapping[str, Any] | None = None,
    occurrence_evidence: Mapping[str, Any] | None = None,
    transcripts: Mapping[str, Any] | None = None,
    qualified_pages: list[Mapping[str, Any]] | None = None,
) -> None:
    """Recompute candidate semantics and project the unique terminal path."""
    occurrence_sha256 = None if occurrence_reference is None else occurrence_reference["sha256"]
    rows = _results(layers); by_slot = dict(rows)
    h1_result = by_slot["h1_tdef_to_map_row"]
    root_result = by_slot["root_result"]
    if h1_result["status"] == "model" and root_result["status"] != "not_applicable":
        roots = list(root_result["candidates"])
        root_evidence = root_result.get("terminal_evidence")
        if isinstance(root_evidence, Mapping) and root_evidence.get("kind") == "replica_pair":
            roots.extend(entry["complete_candidate"] for entry in root_evidence["entries"])
        expected_offsets = h1_result["candidates"][0]["model"]["locator_offsets"]
        if any(candidate["model"]["locator_offsets"] != expected_offsets for candidate in roots):
            raise ValueError("A4 frozen H4 root locator offsets differ from the frozen H1 model")
    terminal_ids = {r["terminal_predicate_id"] for _, r in rows if r["status"] == "no_outcome"}
    if len(terminal_ids) > 1: raise ValueError("A4 frozen derivation contains multiple terminals")
    terminal_id = next(iter(terminal_ids), None)
    expected_slots = set(PREDICATE_CONTRACTS[terminal_id]["result_slots"]) if terminal_id else set()
    actual_slots = {slot for slot, result in rows if result["status"] == "no_outcome"}
    if actual_slots != expected_slots: raise ValueError("A4 frozen terminal result slots differ from the contract")
    first = min((_SLOTS.index(s) for s in expected_slots), default=len(_SLOTS)); last = max((_SLOTS.index(s) for s in expected_slots), default=-1)
    immediate_input_id: str | None = None
    for index, (slot, result) in enumerate(rows):
        expected = "model" if terminal_id is None or index < first else "no_outcome" if first <= index <= last and slot in expected_slots else "not_applicable"
        if result["status"] != expected: raise ValueError("A4 frozen layer status does not follow terminal order")
        for candidate in result["candidates"]:
            _candidate_identity(candidate, occurrence_sha256)
            if result["terminal_candidate_stage"] is not None and candidate["model_type"] != result["terminal_candidate_stage"]:
                raise ValueError("A4 frozen terminal candidate has the wrong stage")
        candidate_ids = [candidate["canonical_candidate_id"] for candidate in result["candidates"]]
        if candidate_ids != sorted(set(candidate_ids)):
            raise ValueError("A4 frozen candidate array is not canonical and unique")
        if expected == "model":
            if (len(result["candidates"]) != 1 or result["candidates"][0]["model_type"] != _DECISIVE_TYPES[slot]
                    or result["predicate_measured_survivor_count"] != 1 or result["derivation_survivor_count"] != 1
                    or result["terminal_predicate_id"] is not None or result["terminal_evidence"] is not None):
                raise ValueError("A4 frozen decisive result is inconsistent")
            candidate = result["candidates"][0]
            if candidate["model_type"] in _BOUND_TYPES:
                exact = (1,) * 5 + (2,) * 5 if candidate["model_type"].startswith("h1_") else (1, 2)
                if _binding_replicas(candidate) != exact: raise ValueError("A4 frozen decisive candidate does not bind exact replicas 1 and 2")
            immediate_input_id = candidate["canonical_candidate_id"]
        elif expected == "not_applicable":
            if result["candidates"] or result["predicate_measured_survivor_count"] != 0 or result["derivation_survivor_count"] != 0 or result["terminal_predicate_id"] is not None or result["terminal_evidence"] is not None:
                raise ValueError("A4 frozen not-applicable result is inconsistent")
        else:
            _validate_terminal_payload(slot, result, immediate_input_id, occurrence_sha256)
    _h4_cross_stage(
        by_slot, occurrence_sha256, occurrence_evidence, transcripts, qualified_pages
    )
