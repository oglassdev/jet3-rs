#!/usr/bin/env python3
"""Fail-closed campaign inputs and the A4 freeze-before-holdout boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from a4_campaign import CampaignResourceTotals, check_campaign_replica
from a4_model import A4AnalysisError, ReplicaData, View
from a4_spec import BOUNDS, CHECKPOINT_IDS

_MAX_QUALIFIED = int(BOUNDS["max_qualified_pages_per_submodel"])


@dataclass(frozen=True)
class ReplicaAnalysisInput:
    """All independently checked surfaces for one physical replica."""

    source: ReplicaData
    table_row_counts: Mapping[str, Mapping[str, int]]
    replica_observation: Mapping[str, Any] | None = None
    page_indexes: Mapping[str, Mapping[str, Any]] | None = None
    schema_snapshots: Mapping[str, Mapping[str, Any]] | None = None
    artifact_manifest: Mapping[str, Any] | None = None
    environment_payload: bytes | None = None


HoldoutProvider = Callable[[bytes, str], ReplicaAnalysisInput]


@dataclass(frozen=True)
class _BoundedSource:
    source: ReplicaData
    checkpoint_ids: tuple[str, ...]
    page_count: Mapping[str, int]
    ordered_page_sha256: Mapping[str, tuple[str, ...]]

    def page_bytes(self, sha256: str) -> bytes:
        return self.source.page_bytes(sha256)


def _bounded_sequence(value: Any, expected: int, label: str) -> tuple[Any, ...]:
    try:
        if len(value) != expected:
            raise ValueError(f"{label} length differs")
        result = tuple(value[index] for index in range(expected))
        try:
            value[expected]
        except IndexError:
            return result
    except (IndexError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise A4AnalysisError(
            "A4-SNAPSHOT-RECONSTRUCTION", detail=f"invalid {label}: {exc}"
        ) from exc
    raise A4AnalysisError(
        "A4-SNAPSHOT-RECONSTRUCTION", detail=f"{label} exceeds its declared length"
    )


def _bounded_replica(
    replica: int, value: ReplicaAnalysisInput
) -> tuple[ReplicaAnalysisInput, frozenset[str]]:
    checkpoints = _bounded_sequence(
        value.source.checkpoint_ids, len(CHECKPOINT_IDS), "checkpoint sequence"
    )
    if checkpoints != CHECKPOINT_IDS:
        raise A4AnalysisError(
            "A4-SNAPSHOT-RECONSTRUCTION",
            detail=f"replica {replica}: checkpoint order differs",
        )
    counts: dict[str, int] = {}
    hashes: dict[str, tuple[str, ...]] = {}
    for checkpoint in CHECKPOINT_IDS:
        try:
            count = value.source.page_count[checkpoint]
            sequence = value.source.ordered_page_sha256[checkpoint]
        except (KeyError, TypeError) as exc:
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail=f"replica {replica}: missing {checkpoint} page index",
            ) from exc
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= int(BOUNDS["max_final_pages_per_replica"])
        ):
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail=f"replica {replica}: invalid {checkpoint} page count",
            )
        bounded = _bounded_sequence(sequence, count, f"{checkpoint} page hashes")
        if any(
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in bounded
        ):
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail=f"replica {replica}: invalid {checkpoint} page digest",
            )
        counts[checkpoint], hashes[checkpoint] = count, bounded
    source = _BoundedSource(
        value.source,
        CHECKPOINT_IDS,
        MappingProxyType(counts),
        MappingProxyType(hashes),
    )
    bounded_input = ReplicaAnalysisInput(
        source,
        value.table_row_counts,
        value.replica_observation,
        value.page_indexes,
        value.schema_snapshots,
        value.artifact_manifest,
        value.environment_payload,
    )
    return bounded_input, frozenset(
        digest for sequence in hashes.values() for digest in sequence
    )


@dataclass(frozen=True)
class CheckedAnalysisInput:
    campaign_id: str
    producer_commit: str
    replicas: Mapping[int, ReplicaAnalysisInput]
    views: Mapping[int, View]
    qualified_tdef_pages: Mapping[int, tuple[int, ...]]
    campaign_resources: Mapping[int, CampaignResourceTotals]
    environment_exact_fields: tuple[object, ...]
    derivation_matrix_job_ids: frozenset[str]


@dataclass(frozen=True)
class HoldoutAnalysisInput:
    """Replica-3-only input surface created after exact frozen-state resume."""

    campaign_id: str
    producer_commit: str
    replica: ReplicaAnalysisInput
    view: View
    qualified_tdef_pages: tuple[int, ...]
    campaign_resources: CampaignResourceTotals


@dataclass(frozen=True)
class HoldoutCampaignInput:
    """Structurally validated replica 3 before optional scientific evaluation."""

    replica: ReplicaAnalysisInput
    view: View
    campaign_resources: CampaignResourceTotals


@dataclass(frozen=True)
class HoldoutTicket:
    """The only capability retained after derivation state is closed."""

    campaign_id: str
    producer_commit: str
    provider: HoldoutProvider
    derivation_page_digests: frozenset[str]
    frozen_derivation_sha256: str
    occurrence_evidence_payload: bytes | None
    environment_exact_fields: tuple[object, ...]
    derivation_matrix_job_ids: frozenset[str]


def _checked_identity(campaign_id: str, producer_commit: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if (
        not 1 <= len(campaign_id) <= 128
        or campaign_id[0] not in allowed
        or set(campaign_id) - allowed
    ):
        raise ValueError("A4 campaign id is not canonical")
    if len(producer_commit) != 40 or any(
        value not in "0123456789abcdef" for value in producer_commit
    ):
        raise ValueError("A4 producer commit is not a full lowercase Git OID")


def _qualified_tdefs(replica: int, view: View) -> tuple[int, ...]:
    candidates: set[int] = set()
    for checkpoint in CHECKPOINT_IDS:
        for page in range(view.page_count(checkpoint)):
            payload = view.page_optional(checkpoint, page)
            if payload is not None and payload[0] == 0x02:
                candidates.add(page)
                if len(candidates) > _MAX_QUALIFIED:
                    raise A4AnalysisError(
                        "A4-RESOURCE-BOUND",
                        detail=f"replica {replica}: qualified TDEF pages exceed the bound",
                    )
    return tuple(sorted(candidates))


def check_analysis_input(
    campaign_id: str,
    producer_commit: str,
    replicas: Mapping[int, ReplicaAnalysisInput],
) -> CheckedAnalysisInput:
    """Check replicas 1/2 completely without reading any replica-3 surface."""
    _checked_identity(campaign_id, producer_commit)
    expected_replicas = (1, 2)
    if tuple(sorted(replicas)) != expected_replicas:
        raise A4AnalysisError(
            "A4-SNAPSHOT-RECONSTRUCTION",
            detail="A4 derivation requires exact replicas 1 and 2",
        )
    bounded: dict[int, ReplicaAnalysisInput] = {}
    derivation_digests: set[str] = set()
    for replica in (1, 2):
        bounded[replica], digests = _bounded_replica(replica, replicas[replica])
        derivation_digests.update(digests)
    _check_retained_store(derivation_digests)
    checked = {
        replica: check_campaign_replica(
            replica, bounded[replica], campaign_id, producer_commit
        )
        for replica in (1, 2)
    }
    exact_fields = {
        checked[replica].environment_exact_fields for replica in (1, 2)
    }
    job_ids = frozenset(
        checked[replica].matrix_job_id for replica in (1, 2)
    )
    if len(exact_fields) != 1 or len(job_ids) != 2:
        raise A4AnalysisError(
            "A4-SCHEMA-SNAPSHOT",
            detail="derivation environment or matrix-job identity differs",
        )
    views = {replica: checked[replica].view for replica in (1, 2)}
    qualified = {
        replica: _qualified_tdefs(replica, views[replica]) for replica in (1, 2)
    }
    derivation_digests = {
        digest for view in views.values() for checkpoint in CHECKPOINT_IDS
        for digest in view.hashes(checkpoint)
    }
    _check_retained_store(derivation_digests)
    return CheckedAnalysisInput(
        campaign_id,
        producer_commit,
        MappingProxyType(bounded),
        MappingProxyType(views),
        MappingProxyType(qualified),
        MappingProxyType(
            {replica: checked[replica].resources for replica in (1, 2)}
        ),
        next(iter(exact_fields)),
        job_ids,
    )


def close_derivation(
    inputs: CheckedAnalysisInput,
    frozen_payload: bytes,
    frozen_sha256: str,
    holdout_provider: HoldoutProvider,
    occurrence_evidence_payload: bytes | None = None,
) -> HoldoutTicket:
    """Mint a holdout capability only from verified frozen derivation bytes."""
    if not callable(holdout_provider):
        raise TypeError("A4 analysis requires a lazy holdout provider")
    # Delayed to keep the input/freeze modules acyclic at import time.
    from a4_analysis_state import resume_derivation

    frozen = resume_derivation(
        frozen_payload, frozen_sha256, occurrence_evidence_payload
    )
    if (
        frozen["campaign_id"] != inputs.campaign_id
        or frozen["derivation_replicas"] != [1, 2]
    ):
        raise ValueError("A4 frozen derivation does not bind this campaign")
    digests = frozenset(
        digest
        for replica in (1, 2)
        for checkpoint in CHECKPOINT_IDS
        for digest in inputs.views[replica].hashes(checkpoint)
    )
    return HoldoutTicket(
        inputs.campaign_id,
        inputs.producer_commit,
        holdout_provider,
        digests,
        frozen_sha256,
        occurrence_evidence_payload,
        inputs.environment_exact_fields,
        inputs.derivation_matrix_job_ids,
    )


def _check_retained_store(digests: set[str] | frozenset[str]) -> None:
    if (
        len(digests) > int(BOUNDS["max_unique_page_blobs"])
        or len(digests) * int(BOUNDS["page_size"])
        > int(BOUNDS["max_retained_page_store_bytes"])
    ):
        raise A4AnalysisError(
            "A4-RESOURCE-BOUND",
            detail="aggregate retained page store exceeds the campaign bound",
        )


def open_holdout_campaign(
    inputs: HoldoutTicket, frozen_payload: bytes
) -> HoldoutCampaignInput:
    """Acquire and campaign-check replica 3 only after exact freeze resume."""
    from a4_analysis_state import resume_derivation

    frozen = resume_derivation(
        frozen_payload,
        inputs.frozen_derivation_sha256,
        inputs.occurrence_evidence_payload,
    )
    if frozen["campaign_id"] != inputs.campaign_id:
        raise ValueError("A4 frozen derivation does not bind this holdout ticket")
    replica = inputs.provider(frozen_payload, inputs.frozen_derivation_sha256)
    if not isinstance(replica, ReplicaAnalysisInput):
        raise TypeError("A4 holdout provider did not return a replica input")
    replica, holdout_digests = _bounded_replica(3, replica)
    _check_retained_store(inputs.derivation_page_digests | holdout_digests)
    checked = check_campaign_replica(
        3, replica, inputs.campaign_id, inputs.producer_commit
    )
    if (
        checked.environment_exact_fields != inputs.environment_exact_fields
        or checked.matrix_job_id in inputs.derivation_matrix_job_ids
    ):
        raise A4AnalysisError(
            "A4-SCHEMA-SNAPSHOT",
            detail="holdout environment or matrix-job identity differs",
        )
    holdout_digests = {
        digest
        for checkpoint in CHECKPOINT_IDS
        for digest in checked.view.hashes(checkpoint)
    }
    _check_retained_store(inputs.derivation_page_digests | holdout_digests)
    return HoldoutCampaignInput(replica, checked.view, checked.resources)


def open_holdout(inputs: HoldoutTicket, frozen_payload: bytes) -> HoldoutAnalysisInput:
    """Open replica 3 for scientific evaluation after its campaign checks pass."""
    campaign = open_holdout_campaign(inputs, frozen_payload)
    qualified = _qualified_tdefs(3, campaign.view)
    return HoldoutAnalysisInput(
        inputs.campaign_id,
        inputs.producer_commit,
        campaign.replica,
        campaign.view,
        qualified,
        campaign.campaign_resources,
    )
