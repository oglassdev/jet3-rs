#!/usr/bin/env python3
"""Exact bounded M5R3 comparison recomputation against validated M4R2."""

from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from m4r1_bundle import validate_bundle as validate_m4_bundle
from m5_spec import (
    ANALYZED_BYTES,
    EXPECTED_BYTE_VISITS,
    EXPECTED_COMPARISONS,
    M4_MANIFEST_SHA256,
    M4_PRODUCER_COMMIT,
    M4_RUN_ID,
    PREFIX_BYTES,
    compile_checked_plan,
)
from m5_records import reject_alias_components
from protocol_validation import ValidationError


def canonical_analysis_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_m4_identity(m4_root: Path) -> tuple[Path, dict[str, Any]]:
    """Bound one direct M4 manifest without recursively discovering aliases."""
    lexical_root = reject_alias_components(m4_root, "supplied M4 bundle root")
    try:
        metadata = lexical_root.lstat()
    except OSError as exc:
        raise ValidationError(f"{m4_root}: cannot inspect M4 bundle root: {exc}") from exc
    from m4r1_snapshot import _is_reparse
    if lexical_root.is_symlink() or _is_reparse(metadata):
        raise ValidationError("supplied M4 bundle-root aliases and reparses are forbidden")
    m4_root = lexical_root.resolve(strict=True)
    manifest_path = m4_root / "bundle-manifest.json"
    if not manifest_path.is_file():
        raise ValidationError("M4 bundle root must contain bundle-manifest.json directly")
    from m1_bundle_validation import bounded_file_identity
    _, digest, retained = bounded_file_identity(manifest_path, 16 * 1024 * 1024, retain=True)
    assert retained is not None
    if digest != M4_MANIFEST_SHA256:
        raise ValidationError("M4 bundle manifest SHA-256 differs from immutable M5 binding")
    from m4r1_records import SCHEMA_SET as M4_SCHEMA_SET, parse_json_bytes
    manifest = parse_json_bytes(retained, str(manifest_path))
    if M4_SCHEMA_SET.validate(manifest) != "dao_m4_bundle_manifest":
        raise ValidationError("M4 bundle manifest document type differs")
    if manifest["producer_commit"] != M4_PRODUCER_COMMIT or manifest["run_id"] != M4_RUN_ID:
        raise ValidationError("M4 producer commit or campaign run differs from immutable M5 binding")
    return m4_root, manifest


def load_validated_m4(m4_root: Path) -> dict[str, Any]:
    """Independently validate and exactly bind the immutable M4 input."""
    m4_root, _ = validate_m4_identity(m4_root)
    validated = validate_m4_bundle(m4_root)
    manifest = validated["manifest"]
    if manifest["producer_commit"] != M4_PRODUCER_COMMIT or manifest["run_id"] != M4_RUN_ID:
        raise ValidationError("M4 producer commit or campaign run differs from immutable M5 binding")
    return validated


def _m4_projection(validated: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, list[int]]]:
    """Derive per-condition stable bytes and preregistered candidate offsets."""
    by_condition: dict[str, list[bytes]] = {}
    for record in validated["records"]:
        condition = record["condition_id"]
        for phase in ("creator", "reopen"):
            path = record["phases"][phase]["post_close_file_observations"]["prefix_path"]
            by_condition.setdefault(condition, []).append(validated["prefixes"][path])
    stable: dict[str, bytes] = {}
    for condition, prefixes in by_condition.items():
        if len(prefixes) != 12:
            raise ValidationError(f"M4 condition {condition}: observation inventory differs")
        values = bytearray()
        for offset in range(ANALYZED_BYTES):
            observed = {prefix[offset] for prefix in prefixes}
            if len(observed) != 1:
                raise ValidationError(
                    f"M4 condition {condition}: analyzed offset {offset} is unstable"
                )
            values.append(observed.pop())
        stable[condition] = bytes(values)
    candidates = {
        row["candidate_set_id"]: row["absolute_offsets"]
        for row in validated["analysis"]["candidate_sets"]
    }
    return stable, candidates


def _checked_inputs(
    plan: dict[str, Any],
    records: Sequence[dict[str, Any]],
    prefixes: Mapping[str, bytes],
) -> tuple[Any, dict[str, dict[str, Any]], dict[tuple[str, str], bytes]]:
    checked = compile_checked_plan(plan)
    if len(records) != 108 or len(prefixes) != 324:
        raise ValidationError("M5 analysis requires 108 records and 324 retained prefixes")
    indexed: dict[str, dict[str, Any]] = {}
    observations: dict[tuple[str, str], bytes] = {}
    referenced: set[str] = set()
    for record in records:
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or sample_id in indexed:
            raise ValidationError("M5 sample record identity is missing or duplicated")
        indexed[sample_id] = record
    for sample in checked.samples:
        record = indexed.get(sample["sample_id"])
        if record is None:
            raise ValidationError(f"{sample['sample_id']}: sample record missing")
        for key in ("condition_id", "replica", "block", "position_in_block", "launch_ordinal"):
            if record.get(key) != sample[key]:
                raise ValidationError(f"{sample['sample_id']}: record {key} differs")
        for phase, role in (("source", "source_database"), ("compact", "compacted_database"), ("verify", "verify_database")):
            result = record["_results"][phase]
            matches = [row for row in result["database_observations"] if row["database_role"] == role]
            if len(matches) != 1 or matches[0]["prefix"] is None:
                raise ValidationError(f"{sample['sample_id']}: {phase} retained observation missing")
            ref = matches[0]["prefix"]
            payload = prefixes.get(ref["path"])
            if not isinstance(payload, bytes) or len(payload) != PREFIX_BYTES or _sha256(payload) != ref["sha256"]:
                raise ValidationError(f"{sample['sample_id']}: {phase} prefix differs")
            if ref["path"] in referenced:
                raise ValidationError(f"{ref['path']}: prefix path reused")
            referenced.add(ref["path"])
            observations[(sample["sample_id"], role)] = payload
    if set(prefixes) != referenced:
        raise ValidationError("M5 prefix inventory contains unreferenced paths")
    return checked, indexed, observations


def _ref(sample_id: str, role: str) -> dict[str, str]:
    return {"sample_id": sample_id, "database_role": role}


def build_analysis(
    plan: dict[str, Any],
    records: Sequence[dict[str, Any]],
    prefixes: Mapping[str, bytes],
    validated_m4: dict[str, Any],
) -> dict[str, Any]:
    """Recompute all 648 comparisons and three preregistered predicates."""
    checked, indexed, observations = _checked_inputs(plan, records, prefixes)
    m4_stable, m4_candidates = _m4_projection(validated_m4)
    comparisons: list[dict[str, Any]] = []
    byte_visits = 0

    def add(kind: str, left_ref: dict[str, str], right_ref: dict[str, str], left: bytes, right: bytes) -> None:
        nonlocal byte_visits
        if len(comparisons) >= EXPECTED_COMPARISONS:
            raise ValidationError("M5 comparison count exceeded its ceiling")
        byte_visits += 2 * ANALYZED_BYTES
        if byte_visits > EXPECTED_BYTE_VISITS:
            raise ValidationError("M5 comparison byte visits exceeded their ceiling")
        comparisons.append({
            "comparison_id": f"M5-CMP-{len(comparisons) + 1:03d}",
            "kind": kind,
            "left": left_ref,
            "right": right_ref,
            "differing_offsets": [offset for offset in range(ANALYZED_BYTES) if left[offset] != right[offset]],
        })

    for sample in checked.samples:
        sid = sample["sample_id"]
        add("paired_phase", _ref(sid, "compacted_database"), _ref(sid, "verify_database"), observations[(sid, "compacted_database")], observations[(sid, "verify_database")])
    samples_by_condition = {
        condition["condition_id"]: sorted(
            [sample for sample in checked.samples if sample["condition_id"] == condition["condition_id"]],
            key=lambda row: row["replica"],
        )
        for condition in checked.conditions
    }
    for condition in checked.conditions:
        rows = samples_by_condition[condition["condition_id"]]
        for role in ("source_database", "compacted_database", "verify_database"):
            for left_sample, right_sample in combinations(rows, 2):
                left_id, right_id = left_sample["sample_id"], right_sample["sample_id"]
                add("within_condition", _ref(left_id, role), _ref(right_id, role), observations[(left_id, role)], observations[(right_id, role)])
    for sample in checked.samples:
        sid = sample["sample_id"]
        matched = checked.conditions_by_id[sample["condition_id"]]["matched_m4_condition_id"]
        add("compact_versus_created_matched", _ref(sid, "compacted_database"), _ref(matched, "m4_created_condition"), observations[(sid, "compacted_database")], m4_stable[matched])
    for sample in checked.samples:
        sid = sample["sample_id"]
        add("source_versus_compacted_within_sample", _ref(sid, "source_database"), _ref(sid, "compacted_database"), observations[(sid, "source_database")], observations[(sid, "compacted_database")])
    if len(comparisons) != EXPECTED_COMPARISONS or byte_visits != EXPECTED_BYTE_VISITS:
        raise ValidationError("M5 comparison topology is incomplete")

    version_offsets = list(m4_candidates.get("M4-CANDIDATE-VERSION-PAIRED", []))
    encryption_offsets = list(m4_candidates.get("M4-CANDIDATE-V30-ENCRYPTION", []))
    version_holds = bool(version_offsets)
    encryption_holds = bool(encryption_offsets)
    for sample in checked.samples:
        condition = checked.conditions_by_id[sample["condition_id"]]
        matched = condition["matched_m4_condition_id"]
        compacted = observations[(sample["sample_id"], "compacted_database")]
        if any(compacted[offset] != m4_stable[matched][offset] for offset in version_offsets):
            version_holds = False
        if condition["destination_version_option"] == "dbVersion30" and any(compacted[offset] != m4_stable[matched][offset] for offset in encryption_offsets):
            encryption_holds = False
    divergence: list[int] = []
    unstable = False
    for condition in checked.conditions:
        for role in ("source_database", "compacted_database", "verify_database"):
            values_by_replica = {
                observations[(sample["sample_id"], role)][:ANALYZED_BYTES]
                for sample in samples_by_condition[condition["condition_id"]]
            }
            if len(values_by_replica) != 1:
                unstable = True
    for offset in range(ANALYZED_BYTES):
        all_differ = True
        for condition in checked.conditions:
            values = {
                observations[(sample["sample_id"], role)][offset]
                for sample in samples_by_condition[condition["condition_id"]]
                for role in ("compacted_database", "verify_database")
            }
            if len(values) != 1:
                unstable = True
                all_differ = False
                continue
            value = next(iter(values))
            if value == m4_stable[condition["matched_m4_condition_id"]][offset]:
                all_differ = False
        if all_differ:
            divergence.append(offset)
    candidates = [
        {"candidate_set_id": "M5-CONFIRM-VERSION-AGREEMENT", "factor": "version", "absolute_offsets": version_offsets if version_holds else [], "bound_offsets": version_offsets, "predicate_holds": version_holds},
        {"candidate_set_id": "M5-CONFIRM-ENCRYPTION-AGREEMENT", "factor": "encryption", "absolute_offsets": encryption_offsets if encryption_holds else [], "bound_offsets": encryption_offsets, "predicate_holds": encryption_holds},
        {"candidate_set_id": "M5-COMPACT-ONLY-DIVERGENCE", "factor": "generation_method", "absolute_offsets": divergence, "bound_offsets": list(range(ANALYZED_BYTES)), "predicate_holds": bool(divergence)},
    ]
    if unstable or not version_offsets or not encryption_offsets:
        outcome = "inconclusive"
    elif divergence:
        outcome = "compact_diverges"
    elif version_holds and encryption_holds:
        outcome = "compact_matches_created"
    else:
        outcome = "inconclusive"
    result = {
        "bounds": {"retained_prefix_range": {"start": 0, "end": PREFIX_BYTES}, "analyzed_ranges": [{"start": 0, "end": ANALYZED_BYTES}], "excluded_ranges": [{"start": ANALYZED_BYTES, "end": PREFIX_BYTES}], "max_analyzed_offsets": ANALYZED_BYTES, "max_comparisons": EXPECTED_COMPARISONS},
        "comparisons": comparisons,
        "candidate_sets": candidates,
        "excluded_region_analyzed": False,
        "companion_bytes_analyzed": False,
        "physical_meaning_assigned": False,
        "compatibility_claimed": False,
        "execution_status": "pass",
        "scientific_outcome": outcome,
    }
    if len(canonical_analysis_bytes(result)) > checked.bounds["max_analysis_report_bytes"]:
        raise ValidationError("M5 analysis exceeded its byte ceiling")
    return result
