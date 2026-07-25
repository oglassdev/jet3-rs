#!/usr/bin/env python3
"""Campaign-wide runtime-binding and launch chronology checks for DAO M4."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from m4_records import (
    ArtifactSource,
    ValidationError,
    load_artifact_document,
    parse_timestamp,
    require_equal,
)


def validate_campaign_bindings_and_chronology(
    root: Path,
    plan: dict[str, Any],
    records: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
    source: ArtifactSource | None = None,
) -> None:
    """Require one runtime-root tuple and serial checked launch chronology."""
    records_by_id = {record["sample_id"]: record for record in records}
    root_bindings: set[tuple[str, str]] = set()
    campaign_run_ids: set[str] = set()
    previous_finished = None
    final_finished = None
    for sample in sorted(plan["samples"], key=lambda row: row["launch_ordinal"]):
        record = records_by_id[sample["sample_id"]]
        phase_documents: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for phase in ("creator", "reopen"):
            artifacts = record["phases"][phase]["artifacts"]
            invocation, _, _ = load_artifact_document(
                root,
                artifacts["invocation"]["path"],
                65536,
                "dao_m4_invocation",
                source,
            )
            result, _, _ = load_artifact_document(
                root,
                artifacts["worker_result"]["path"],
                65536,
                "dao_m4_worker_result",
                source,
            )
            root_bindings.add(
                (
                    invocation["repository_root"],
                    invocation["stage_root"],
                )
            )
            campaign_run_ids.add(invocation["campaign_run_id"])
            phase_documents[phase] = (invocation, result)
        creator_invocation, creator_result = phase_documents["creator"]
        reopen_invocation, reopen_result = phase_documents["reopen"]
        chronology = (
            parse_timestamp(
                creator_invocation["created_at_utc"], "$.creator.created_at_utc"
            ),
            parse_timestamp(
                creator_result["started_at_utc"], "$.creator.started_at_utc"
            ),
            parse_timestamp(
                creator_result["finished_at_utc"], "$.creator.finished_at_utc"
            ),
            parse_timestamp(
                reopen_invocation["created_at_utc"], "$.reopen.created_at_utc"
            ),
            parse_timestamp(
                reopen_result["started_at_utc"], "$.reopen.started_at_utc"
            ),
            parse_timestamp(
                reopen_result["finished_at_utc"], "$.reopen.finished_at_utc"
            ),
        )
        if previous_finished is not None and previous_finished > chronology[0]:
            raise ValidationError(
                f"{sample['sample_id']}: launch begins before the previous "
                "launch ordinal completed"
            )
        if tuple(sorted(chronology)) != chronology:
            raise ValidationError(
                f"{sample['sample_id']}: creator/reopen launch chronology differs"
            )
        previous_finished = chronology[-1]
        final_finished = chronology[-1]
    if len(root_bindings) != 1:
        raise ValidationError("M4 invocations do not share one exact runtime-root binding")
    if len(campaign_run_ids) != 1:
        raise ValidationError("M4 invocations do not share one campaign run ID")
    if manifest is not None:
        require_equal(campaign_run_ids, {manifest["run_id"]}, "$.run_id")
        assert final_finished is not None
        if parse_timestamp(manifest["created_at_utc"], "$.created_at_utc") < final_finished:
            raise ValidationError("$.created_at_utc: manifest predates worker completion")
