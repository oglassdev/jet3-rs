"""Decisive-report retention case for the A2 dry-run harness."""

from __future__ import annotations

from typing import Any

from a2_model import CHECKPOINT_IDS, PAGE_SIZE, PLAN, PLAN_SHA256
from a2_spec import BOUNDS, validate_analysis_report, validate_bundle_manifest


def _manifest_files() -> list[dict[str, Any]]:
    replicas = BOUNDS["replicas"]
    roles = (
        ("plan", 1),
        ("environment", replicas),
        ("replica_artifact_manifest", replicas),
        ("replica_observation", replicas),
        ("page_index", replicas * len(CHECKPOINT_IDS)),
        ("frozen_candidate_set", 1),
        ("analysis_report", 1),
        ("holdout_structure_receipt", 1),
    )
    files: list[dict[str, Any]] = []
    counter = 1
    for role, count in roles:
        for _ in range(count):
            digest = f"{counter:064x}"
            files.append(
                {
                    "path": f"synthetic/{role}-{counter}.json",
                    "role": role,
                    "sha256": digest,
                    "size_bytes": 1,
                    "media_type": "application/json",
                }
            )
            counter += 1
    digest = f"{counter:064x}"
    files.append(
        {
            "path": f"page-store/{digest}.page",
            "role": "page_blob",
            "sha256": digest,
            "size_bytes": PAGE_SIZE,
            "media_type": "application/octet-stream",
        }
    )
    return files


def validate_decisive_handling(report: dict[str, Any]) -> dict[str, str]:
    validate_analysis_report(report)
    files = _manifest_files()
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_bundle_manifest",
        "experiment_id": PLAN["experiment_id"],
        "campaign_id": report["campaign_id"],
        "producer_commit": report["producer_commit"],
        "repository_url": PLAN["repository_binding"]["canonical_https_url"],
        "created_utc": "2026-08-21T00:00:00Z",
        "plan_sha256": PLAN_SHA256,
        "replica_environment_sha256": [f"{index + 100:064x}" for index in range(3)],
        "provider_sha256": f"{200:064x}",
        "replica_count": BOUNDS["replicas"],
        "replica_artifact_manifest_sha256": [
            f"{index + 300:064x}" for index in range(3)
        ],
        "checkpoint_count": BOUNDS["replicas"] * len(CHECKPOINT_IDS),
        "page_blob_count": 1,
        "bundle_size_bytes_excluding_manifest": sum(row["size_bytes"] for row in files),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "execution_status": "analysis_complete",
        "campaign_failed": False,
        "holdout_structure_receipt_sha256": f"{400:064x}",
        "analysis_report_retained": True,
        "analysis_scientific_outcome": report["scientific_outcome"],
        "bundle_status": PLAN["decisive_report_handling"]["bundle_status"],
        "independent_validation_status": "not_independently_validated",
        "files": files,
    }
    validate_bundle_manifest(manifest)
    return {
        "analysis_report": "validate_document_pass",
        "bundle_manifest": "validate_document_pass",
        "bundle_status": manifest["bundle_status"],
    }
