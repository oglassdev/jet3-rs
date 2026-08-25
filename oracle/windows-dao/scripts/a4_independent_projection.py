#!/usr/bin/env python3
"""Plan-derived output projections for the independent A4 validator."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from a4_independent_bundle import LoadedBundle, ValidationError
from a4_independent_contract import REVISION_PLAN_SHA256


LAYER_NAMES = (
    "h1_tdef_to_map_row",
    "h2_row_identity_map_role",
    "h3_indirect_traversal",
    "h4_catalog_bootstrap",
)
HOLDOUT_NAMES = ("h1", "h2", "h3", "h4_root", "h4_fields")


def compare_frozen_report(bundle: LoadedBundle) -> None:
    """Require every report field copied from the freeze to be byte-equivalent."""
    frozen = bundle.frozen
    report = bundle.report
    pairs = (
        ("qualified_pages", "qualified_pages"),
        ("work_charges", "work_charges"),
        ("h4_occurrence_evidence", "h4_occurrence_evidence"),
        ("layers", "layers"),
        ("transcripts", "transcripts"),
    )
    if any(frozen[left] != report[right] for left, right in pairs):
        raise ValidationError("analysis_report_mismatch")
    frozen_sha256 = hashlib.sha256(bundle.frozen_raw).hexdigest()
    if report.get("derivation_candidate_set_sha256") != frozen_sha256:
        raise ValidationError("frozen_file_hash_mismatch")
    if report.get("campaign_id") != frozen.get("campaign_id"):
        raise ValidationError("analysis_report_mismatch")


def compare_recomputation(
    bundle: LoadedBundle,
    recomputed: Mapping[str, Any],
) -> None:
    """Compare independently recomputed physical and predicate projections."""
    frozen = bundle.frozen
    report = bundle.report
    for field in ("layers", "qualified_pages", "work_charges", "transcripts"):
        if recomputed.get(field) != frozen.get(field):
            raise ValidationError("frozen_set_recomputation_mismatch")
    if recomputed.get("predicate_results") != report.get("predicate_results"):
        raise ValidationError("predicate_layer_projection_mismatch")
    if recomputed.get("holdout_results") != report.get("holdout_results"):
        raise ValidationError("holdout_projection_mismatch")
    if recomputed.get("scientific_outcome") != report.get("scientific_outcome"):
        raise ValidationError("holdout_projection_mismatch")


def validate_claims(bundle: LoadedBundle) -> None:
    claims = bundle.report.get("claims")
    if not isinstance(claims, Mapping) or set(claims) != set(bundle.plan["claims"]):
        raise ValidationError("analysis_report_mismatch")
    if claims != bundle.plan["claims"]:
        raise ValidationError("analysis_report_mismatch")
    if claims.get("descriptive_provider_observation_only") is not True:
        raise ValidationError("analysis_report_mismatch")
    if any(
        value is not False
        for name, value in claims.items()
        if name != "descriptive_provider_observation_only"
    ):
        raise ValidationError("analysis_report_mismatch")


def logical_read_projection(
    bundle: LoadedBundle,
) -> list[dict[str, int]]:
    analyzer = bundle.report.get("analyzer_logical_read_bytes_by_replica")
    if not isinstance(analyzer, list) or len(analyzer) != 3:
        raise ValidationError("analysis_report_mismatch")
    output: list[dict[str, int]] = []
    for index, replica in enumerate((1, 2, 3)):
        source = bundle.replicas[replica]
        producer = sum(row["page_count"] for row in source.indexes.values()) * 2048
        independent = bundle.page_store.logical_read_bytes(replica)
        analyzer_value = analyzer[index]
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (producer, analyzer_value, independent)
        ):
            raise ValidationError("logical_read_accounting_mismatch")
        total = producer + analyzer_value + independent
        if total > 1_317_011_456:
            raise ValidationError("resource_bound_breach")
        output.append({
            "replica": replica,
            "producer": producer,
            "analyzer": analyzer_value,
            "independent_validator": independent,
            "total": total,
        })
    return output


def recompute_only_document(
    bundle: LoadedBundle,
    recomputed: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_independent_recomputation",
        "source_experiment_id": bundle.manifest["experiment_id"],
        "source_bundle_manifest_sha256": bundle.manifest_sha256,
        "derivation_replicas": [1, 2],
        "holdout_opened": False,
        "qualified_pages": recomputed["qualified_pages"],
        "work_charges": recomputed["work_charges"],
        "layers": recomputed["layers"],
    }


def pair_projection_document(
    bundle: LoadedBundle,
    recomputed: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_independent_pair_projection",
        "source_experiment_id": bundle.manifest["experiment_id"],
        "source_bundle_manifest_sha256": bundle.manifest_sha256,
        "derivation_replicas": [1, 2],
        "holdout_opened": True,
        "bundle_contract_rejection": None,
        "independent_projection": {
            "layers": recomputed["layers"],
            "predicate_results": recomputed["predicate_results"],
            "holdout_results": recomputed["holdout_results"],
            "scientific_outcome": recomputed["scientific_outcome"],
        },
    }


def verdict(
    bundle: LoadedBundle | None,
    validator_commit: str,
    *,
    accepted: bool,
    discrepancy_codes: Sequence[str],
    tamper_results: Sequence[Mapping[str, Any]],
    logical_reads: Sequence[Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    manifest: Mapping[str, Any] = {} if bundle is None else bundle.manifest
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_independent_validation_report",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": "0" * 64 if bundle is None else bundle.plan_sha256,
        "revision_plan_sha256": (
            "0" * 64 if bundle is None else REVISION_PLAN_SHA256
        ),
        "campaign_id": manifest.get("campaign_id", "unavailable"),
        "bundle_manifest_sha256": (
            "0" * 64 if bundle is None else bundle.manifest_sha256
        ),
        "validator_commit": validator_commit,
        "implementation_independence_attested": True,
        "frozen_set_parsed": True,
        "frozen_set_matches_recomputation": accepted,
        "frozen_set_matches_report": accepted,
        "predicate_registry_recomputed": accepted,
        "holdout_recomputed": accepted,
        "tamper_results": [dict(row) for row in tamper_results],
        "accepted": accepted,
        "independent_validation_status": (
            "independently_validated" if accepted else "not_independently_validated"
        ),
        "discrepancy_codes": list(discrepancy_codes),
        "logical_read_bytes_by_replica": (
            [dict(row) for row in logical_reads]
            if logical_reads is not None
            else [
                {
                    "replica": replica,
                    "producer": 0,
                    "analyzer": 0,
                    "independent_validator": 0,
                    "total": 0,
                }
                for replica in (1, 2, 3)
            ]
        ),
    }


def failure_document(
    bundle: LoadedBundle | None,
    validator_commit: str,
    discrepancy_code: str,
    tamper_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a truthful diagnostic, not a schema-valid acceptance report."""
    return {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_independent_validation_failure",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "validator_commit": validator_commit,
        "bundle_manifest_sha256": (
            None if bundle is None else bundle.manifest_sha256
        ),
        "frozen_set_parsed": bundle is not None,
        "tamper_results_executed": [dict(row) for row in tamper_results],
        "accepted": False,
        "independent_validation_status": "not_independently_validated",
        "discrepancy_codes": [discrepancy_code],
    }
