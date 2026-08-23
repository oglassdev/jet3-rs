"""Revision-chain and predicate-sequence contract for A3 validation.

This module is independent of the analyzer and uses only the Python standard
library plus the bounded primitives shared by the independent validator.
"""

from __future__ import annotations

from pathlib import Path

from a3_independent_bundle import (
    GOVERNING_REVISION_SHA256,
    ValidationError,
    load_json,
    sha256_bytes,
)


R2_SHA256 = "3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1"
R3_SHA256 = "bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a"
R4_SHA256 = "939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea"
R5_SHA256 = GOVERNING_REVISION_SHA256
REVISION_PATHS = {
    "DAO-A3-ALLOCATION-MAPS-001-R2": "oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json",
    "DAO-A3-ALLOCATION-MAPS-001-R3": "oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json",
    "DAO-A3-ALLOCATION-MAPS-001-R4": "oracle/windows-dao/experiments/a3/a3-allocation-maps-r4.plan.json",
}
R2_LAYER_NAME_MAP = {
    "global_map.record": "global_map_record",
    "global_map.conversion_inline": "global_map_conversion_inline",
    "global_map.extended_base": "global_map_extended_base",
    "tdef.pointer_pair": "tdef_pointer_pair",
}


def load_predicate_sequences(
    revision_path: Path,
    plan_sha256: str,
    predicate_ids: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    governing, governing_raw = load_json(revision_path, 67_108_864)
    if sha256_bytes(governing_raw) != R5_SHA256:
        raise ValidationError("predicate_revision_hash_mismatch")
    original_path = "oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json"
    try:
        original = governing["preregistration"]["original_plan"]
        r5_priors = {row["revision_id"]: row for row in governing["preregistration"]["prior_revisions"]}
        r4_row = r5_priors["DAO-A3-ALLOCATION-MAPS-001-R4"]
        revision, raw = load_json(revision_path.with_name(Path(r4_row["path"]).name), 67_108_864)
        priors = {row["revision_id"]: row for row in revision["preregistration"]["prior_revisions"]}
        r3_row = priors["DAO-A3-ALLOCATION-MAPS-001-R3"]
        r2_row = priors["DAO-A3-ALLOCATION-MAPS-001-R2"]
        r3, r3_raw = load_json(revision_path.with_name(Path(r3_row["path"]).name), 67_108_864)
        r2, r2_raw = load_json(revision_path.with_name(Path(r2_row["path"]).name), 67_108_864)
        r3_prior = r3["preregistration"]["prior_revision"]
        reconciliation = r2["predicate_evaluation_sequence_reconciliation"]
        campaign = reconciliation["campaign_evaluated_before_any_layer"]
        published_layers = reconciliation["per_layer_ordered_predicates"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("predicate_revision_contract_mismatch") from exc
    if (
        governing.get("document_type") != "dao_a3_allocation_maps_plan_revision"
        or governing.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R5"
        or len(r5_priors) != 3
        or r4_row.get("path") != REVISION_PATHS["DAO-A3-ALLOCATION-MAPS-001-R4"]
        or r4_row.get("sha256") != R4_SHA256
        or sha256_bytes(raw) != R4_SHA256
        or r5_priors["DAO-A3-ALLOCATION-MAPS-001-R3"].get("sha256") != R3_SHA256
        or r5_priors["DAO-A3-ALLOCATION-MAPS-001-R2"].get("sha256") != R2_SHA256
        or revision.get("document_type") != "dao_a3_allocation_maps_plan_revision"
        or revision.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R4"
        or revision["preregistration"].get("original_plan") != original
        or original.get("path") != original_path
        or original.get("sha256") != plan_sha256
        or len(priors) != 2
        or r3_row.get("path") != REVISION_PATHS["DAO-A3-ALLOCATION-MAPS-001-R3"]
        or r3_row.get("sha256") != R3_SHA256
        or sha256_bytes(r3_raw) != R3_SHA256
        or r3.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R3"
        or r3["preregistration"].get("original_plan") != original
        or r3_prior.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R2"
        or r3_prior.get("sha256") != R2_SHA256
        or r2_row.get("path") != REVISION_PATHS["DAO-A3-ALLOCATION-MAPS-001-R2"]
        or r2_row.get("sha256") != R2_SHA256
        or sha256_bytes(r2_raw) != R2_SHA256
        or r2.get("revision_id") != "DAO-A3-ALLOCATION-MAPS-001-R2"
        or r2["preregistration"].get("original_plan") != original
        or not isinstance(campaign, list)
        or not all(isinstance(predicate, str) for predicate in campaign)
        or len(campaign) != len(set(campaign))
        or not isinstance(published_layers, dict)
        or set(published_layers) != set(R2_LAYER_NAME_MAP)
    ):
        raise ValidationError("predicate_revision_contract_mismatch")
    sequences: dict[str, list[str]] = {}
    for published_name, internal_name in R2_LAYER_NAME_MAP.items():
        sequence = published_layers[published_name]
        if (
            not isinstance(sequence, list)
            or not all(isinstance(predicate, str) for predicate in sequence)
            or len(sequence) != len(set(sequence))
        ):
            raise ValidationError("predicate_revision_contract_mismatch")
        sequences[internal_name] = sequence
    known = set(predicate_ids)
    if any(predicate not in known for predicate in campaign) or any(
        predicate not in known for sequence in sequences.values() for predicate in sequence
    ):
        raise ValidationError("predicate_revision_contract_mismatch")
    return campaign, sequences
