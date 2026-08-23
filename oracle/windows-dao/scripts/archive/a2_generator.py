#!/usr/bin/env python3
"""Schedule-derived, non-evidential A2 analyzer fixture generator.

``generate_synthetic_bundle`` returns the analyzer-facing in-memory form.
``write_synthetic_bundles`` materializes the acquisition-shaped directories.
Every schedule and parameter value originates in the checked A2 plan.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from a2_generator_pages import PageFixture, build_page_fixture
from a2_generator_schedule import Schedule, build_schedule, checkpoint_document
from a2_spec import (
    A2_CONVERSION_ORDINALS,
    BIT_POLARITIES,
    BOUNDS,
    CHECKPOINT_IDS,
    EXPERIMENT_ID,
    LEGACY_CONVERSION_ORDINALS,
    PAGE_SIZE,
    PLAN_SHA256,
    ROLE_BINDINGS,
    ROLES,
    RUN12_CALIBRATION,
    expected_reread_sha256,
    load_checked_plan,
    validate_environment,
    validate_page_index,
    validate_replica_artifact_manifest,
    validate_replica_observation,
)
from protocol_validation import ValidationError, canonical_json_bytes

_PLAN = load_checked_plan()
_SYNTHETIC = _PLAN.document["analyzer_dry_run_contract"]["synthetic_input"]
_FREE = _SYNTHETIC["free_parameters"]
_REPOSITORY_URL = _PLAN.document["repository_binding"]["canonical_https_url"]
_PRODUCER_COMMIT = PLAN_SHA256[:40]
_PROVIDER_SHA256 = hashlib.sha256(
    _PLAN.document["environment_binding"]["dao_prog_id"].encode("ascii")
).hexdigest()


@dataclass(frozen=True)
class SyntheticParameters:
    """One member of the plan's bounded synthetic parameter product."""

    conversion_ordinal: int | None
    slot_activation_at_conversion: int
    bit_polarity: str
    anchor_fill_state: str
    record_end_uniform_slack_bytes: int
    delete_page_delta: int


@dataclass(frozen=True)
class GlobalMapFixture:
    page: int
    record_start: int
    record_end: int
    inline_boundary: int
    bit_polarity: str
    conversion_ordinal: int | None
    inline_base: int
    extended_base_formula: str


@dataclass(frozen=True)
class TdefFixture:
    page: int
    record_start: int
    record_end: int
    pointer_layout: str
    growth_pointer_offset: int
    delete_reinsert_pointer_offset: int


@dataclass(frozen=True)
class SyntheticBundle:
    """Bounded analyzer view over one synthetic replica.

    ``checkpoint_ids`` is in frozen plan order. ``page_count`` and
    ``ordered_page_sha256`` are keyed by those ids, while ``page_bytes``
    resolves a content address without exposing the backing mapping.
    """

    checkpoint_ids: tuple[str, ...]
    page_count: Mapping[str, int]
    ordered_page_sha256: Mapping[str, tuple[str, ...]]
    replica: int
    parameters: SyntheticParameters
    schedule: Schedule
    global_map: GlobalMapFixture
    tdef: TdefFixture
    documents: Mapping[str, dict[str, Any]]
    _payloads: Mapping[str, bytes]

    def page_bytes(self, sha256: str) -> bytes:
        """Return one verified 2,048-byte page by lowercase SHA-256."""
        try:
            payload = self._payloads[sha256]
        except KeyError as exc:
            raise ValidationError(f"unknown synthetic page digest {sha256!r}") from exc
        if len(payload) != PAGE_SIZE or hashlib.sha256(payload).hexdigest() != sha256:
            raise ValidationError("synthetic page store failed its content address")
        return payload

    @property
    def page_counts(self) -> Mapping[str, int]:
        """Plural alias for callers that name checkpoint mappings collectively."""
        return self.page_count

    @property
    def ordered_page_hashes(self) -> Mapping[str, tuple[str, ...]]:
        return self.ordered_page_sha256


A2SyntheticBundle = SyntheticBundle
SyntheticBundleView = SyntheticBundle


def run12_calibration_parameters() -> SyntheticParameters:
    """Return the plan-declared non-evidential calibration parameter case."""
    anchor_states = _FREE["anchor_fill_state"]
    slack_values = _FREE["record_end_uniform_slack_bytes"]
    return SyntheticParameters(
        conversion_ordinal=RUN12_CALIBRATION["a2_conversion_ordinal"],
        slot_activation_at_conversion=RUN12_CALIBRATION["active_slot_count"],
        bit_polarity=RUN12_CALIBRATION["bit_polarity"],
        anchor_fill_state=anchor_states[-1],
        record_end_uniform_slack_bytes=slack_values[len(slack_values) // 2],
        delete_page_delta=RUN12_CALIBRATION["delete_page_delta"],
    )


def iter_parameter_combinations(
    *, legacy_projection: bool = False
) -> Iterator[SyntheticParameters]:
    """Enumerate every checked free-parameter combination, including never.

    Both ordinal ranges are parsed from the checked plan; neither projection
    inherits a hand-authored schedule bound.
    """
    ordinals = LEGACY_CONVERSION_ORDINALS if legacy_projection else A2_CONVERSION_ORDINALS
    conversion_values: tuple[int | None, ...] = (*ordinals, None)
    delete_delta = RUN12_CALIBRATION["delete_page_delta"]
    for conversion, slots, polarity, fill, slack in itertools.product(
        conversion_values,
        _FREE["slot_activation_at_conversion"],
        _FREE["bit_polarity"],
        _FREE["anchor_fill_state"],
        _FREE["record_end_uniform_slack_bytes"],
    ):
        yield SyntheticParameters(conversion, slots, polarity, fill, slack, delete_delta)


enumerate_parameter_combinations = iter_parameter_combinations


def _identity(replica: int, parameters: SyntheticParameters) -> tuple[str, str]:
    description = (
        f"a2-synthetic|{parameters.conversion_ordinal}|"
        f"{parameters.slot_activation_at_conversion}|{parameters.bit_polarity}|"
        f"{parameters.anchor_fill_state}|{parameters.record_end_uniform_slack_bytes}|"
        f"{parameters.delete_page_delta}"
    )
    digest = hashlib.sha256(description.encode("ascii")).hexdigest()
    return f"a2-synthetic-{digest[:16]}", f"replica-{replica:02d}"


def _environment(replica: int, campaign_id: str, matrix_job_id: str) -> dict[str, Any]:
    binding = _PLAN.document["environment_binding"]
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_environment",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": _PRODUCER_COMMIT,
        "repository_url": _REPOSITORY_URL,
        "campaign_id": campaign_id,
        "replica": replica,
        "matrix_job_id": matrix_job_id,
        "status": "ready",
        "host": {
            "windows_version": "synthetic-non-evidential",
            "process_architecture": binding["process_architecture"],
            "powershell_version": f"{binding['powershell_major']}.1",
            "python_version": "3.13.0",
            "runner_image": "synthetic-plan-derived",
        },
        "provider": {
            "prog_id": binding["dao_prog_id"],
            "clsid": (
                "{" + _PROVIDER_SHA256[:8] + "-" + _PROVIDER_SHA256[8:12] + "-"
                + _PROVIDER_SHA256[12:16] + "-" + _PROVIDER_SHA256[16:20] + "-"
                + _PROVIDER_SHA256[20:32] + "}"
            ),
            "provider_version": "synthetic",
            "server_path": "C:/synthetic/dao360.dll",
            "server_file_version": "synthetic",
            "server_sha256": _PROVIDER_SHA256,
        },
    }
    validate_environment(document)
    return document


def _artifact_ref(path: str, payload: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _page_indexes(
    replica: int,
    campaign_id: str,
    environment_sha256: str,
    schedule: Schedule,
    pages: PageFixture,
) -> tuple[dict[str, dict[str, Any]], int]:
    documents: dict[str, dict[str, Any]] = {}
    changed_total = 0
    prior: tuple[str, ...] = ()
    for row in schedule.checkpoints:
        hashes = pages.ordered_hashes[row.checkpoint_id]
        changed = [
            index
            for index in range(max(len(prior), len(hashes)))
            if index >= len(prior)
            or index >= len(hashes)
            or prior[index] != hashes[index]
        ]
        database = hashlib.sha256()
        for digest in hashes:
            database.update(pages.payloads[digest])
        path = (
            f"page-indexes/replica-{replica:02d}/"
            f"{row.ordinal:02d}-{row.checkpoint_id}.json"
        )
        document = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a2_page_index",
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256,
            "producer_commit": _PRODUCER_COMMIT,
            "campaign_id": campaign_id,
            "environment_sha256": environment_sha256,
            "provider_sha256": _PROVIDER_SHA256,
            "replica": replica,
            "checkpoint_id": row.checkpoint_id,
            "ordinal": row.ordinal,
            "predecessor_checkpoint_id": (
                None if row.ordinal == 0 else CHECKPOINT_IDS[row.ordinal - 1]
            ),
            "page_count": len(hashes),
            "file_size_bytes": len(hashes) * PAGE_SIZE,
            "database_sha256": database.hexdigest(),
            "ordered_page_sha256": list(hashes),
            "changed_page_indices": changed,
        }
        validate_page_index(document, prior_hashes=prior)
        documents[path] = document
        changed_total += len(changed)
        prior = hashes
    if changed_total > BOUNDS["max_changed_hash_entries_per_replica"]:
        raise ValidationError("synthetic changed-hash total exceeds the checked bound")
    return documents, changed_total


def _observation(
    replica: int,
    campaign_id: str,
    matrix_job_id: str,
    environment_sha256: str,
    schedule: Schedule,
    indexes: Mapping[str, dict[str, Any]],
    changed_total: int,
) -> dict[str, Any]:
    checkpoint_documents = []
    for row in schedule.checkpoints:
        path = (
            f"page-indexes/replica-{replica:02d}/"
            f"{row.ordinal:02d}-{row.checkpoint_id}.json"
        )
        retained = canonical_json_bytes(indexes[path])
        checkpoint = checkpoint_document(row, _artifact_ref(path, retained))
        reread_roles = tuple(
            role
            for role in ROLES
            if not (row.checkpoint_id == "D_DROP" and role == "D")
        )
        checkpoint["dao_reread"] = [
            {
                "role": role,
                "row_count": row.table_row_counts[role],
                "rolling_sha256": expected_reread_sha256(
                    role, row.table_row_counts[role]
                ),
            }
            for role in reread_roles
        ]
        checkpoint_documents.append(checkpoint)
    first = schedule.checkpoint("D_GROW_0128")
    regrow = schedule.checkpoint("D_REGROW_0128")
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_replica_observation",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": _PRODUCER_COMMIT,
        "repository_url": _REPOSITORY_URL,
        "campaign_id": campaign_id,
        "matrix_job": {
            "job_id": matrix_job_id,
            "replica_only": True,
            "shared_mutable_state": False,
        },
        "environment_sha256": environment_sha256,
        "provider_sha256": _PROVIDER_SHA256,
        "replica": replica,
        "role_binding": dict(ROLE_BINDINGS[replica - 1]),
        "d_growth_observation": {
            "first_baseline_pages": first.target_baseline_pages,
            "first_target_pages": first.target_threshold_pages,
            "first_achieved_pages": first.actual_file_pages,
            "first_rows": first.table_row_counts["D"],
            "regrowth_baseline_pages": schedule.checkpoint(
                "D_RECREATE_EMPTY"
            ).actual_file_pages,
            "regrowth_target_pages": regrow.target_threshold_pages,
            "regrowth_achieved_pages": regrow.actual_file_pages,
            "regrowth_rows": regrow.table_row_counts["D"],
        },
        "logical_checkpoint_read_bytes": sum(
            row.actual_file_pages * PAGE_SIZE for row in schedule.checkpoints
        ),
        "inserted_rows_total": max(
            row.inserted_rows_total for row in schedule.checkpoints
        ),
        "changed_hash_entries": changed_total,
        "checkpoints": checkpoint_documents,
    }
    validate_replica_observation(document)
    return document


def _manifest_entry(
    path: str, role: str, payload: bytes
) -> dict[str, Any]:
    media = "application/octet-stream" if role == "page_blob" else "application/json"
    return {
        "path": path,
        "role": role,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "media_type": media,
    }


def _replica_manifest(
    replica: int,
    campaign_id: str,
    matrix_job_id: str,
    environment_sha256: str,
    environment: dict[str, Any],
    observation: dict[str, Any],
    indexes: Mapping[str, dict[str, Any]],
    pages: PageFixture,
) -> dict[str, Any]:
    files = [
        _manifest_entry(
            f"environment/replica-{replica:02d}.json",
            "environment",
            canonical_json_bytes(environment),
        ),
        _manifest_entry(
            f"observations/replica-{replica:02d}.json",
            "replica_observation",
            canonical_json_bytes(observation),
        ),
    ]
    files.extend(
        _manifest_entry(path, "page_index", canonical_json_bytes(document))
        for path, document in indexes.items()
    )
    referenced = sorted(
        {digest for hashes in pages.ordered_hashes.values() for digest in hashes}
    )
    files.extend(
        _manifest_entry(
            f"page-store/{digest}.page", "page_blob", pages.payloads[digest]
        )
        for digest in referenced
    )
    document = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a2_replica_artifact_manifest",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": _PRODUCER_COMMIT,
        "campaign_id": campaign_id,
        "matrix_job_id": matrix_job_id,
        "replica": replica,
        "environment_sha256": environment_sha256,
        "provider_sha256": _PROVIDER_SHA256,
        "checkpoint_count": len(CHECKPOINT_IDS),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "files": files,
    }
    validate_replica_artifact_manifest(document)
    return document


def generate_synthetic_bundle(
    parameters: SyntheticParameters | None = None,
    *,
    replica: int = 1,
) -> SyntheticBundle:
    """Generate one validated in-memory A2 bundle view."""
    if not 1 <= replica <= BOUNDS["replicas"]:
        raise ValidationError("replica lies outside the checked A2 bounds")
    selected = run12_calibration_parameters() if parameters is None else parameters
    schedule = build_schedule(
        delete_page_delta=selected.delete_page_delta, plan=_PLAN
    )
    pages = build_page_fixture(
        schedule,
        conversion_ordinal=selected.conversion_ordinal,
        slot_activation_at_conversion=selected.slot_activation_at_conversion,
        bit_polarity=selected.bit_polarity,
        anchor_fill_state=selected.anchor_fill_state,
        record_end_uniform_slack_bytes=selected.record_end_uniform_slack_bytes,
    )
    campaign_id, matrix_job_id = _identity(replica, selected)
    environment = _environment(replica, campaign_id, matrix_job_id)
    environment_sha256 = hashlib.sha256(
        canonical_json_bytes(environment)
    ).hexdigest()
    indexes, changed_total = _page_indexes(
        replica, campaign_id, environment_sha256, schedule, pages
    )
    observation = _observation(
        replica,
        campaign_id,
        matrix_job_id,
        environment_sha256,
        schedule,
        indexes,
        changed_total,
    )
    manifest = _replica_manifest(
        replica,
        campaign_id,
        matrix_job_id,
        environment_sha256,
        environment,
        observation,
        indexes,
        pages,
    )
    documents: dict[str, dict[str, Any]] = {
        f"environment/replica-{replica:02d}.json": environment,
        f"observations/replica-{replica:02d}.json": observation,
        f"replica-artifacts/replica-{replica:02d}-manifest.json": manifest,
        **indexes,
    }
    return SyntheticBundle(
        checkpoint_ids=CHECKPOINT_IDS,
        page_count=MappingProxyType(
            {
                row.checkpoint_id: row.actual_file_pages
                for row in schedule.checkpoints
            }
        ),
        ordered_page_sha256=MappingProxyType(dict(pages.ordered_hashes)),
        replica=replica,
        parameters=selected,
        schedule=schedule,
        global_map=GlobalMapFixture(
            pages.global_page,
            *pages.global_record,
            pages.inline_boundary,
            selected.bit_polarity,
            selected.conversion_ordinal,
            pages.inline_base,
            pages.extended_base_formula,
        ),
        tdef=TdefFixture(
            pages.tdef_page,
            *pages.tdef_record,
            pages.pointer_layout,
            pages.growth_pointer_offset,
            pages.delete_reinsert_pointer_offset,
        ),
        documents=MappingProxyType(documents),
        _payloads=MappingProxyType(dict(pages.payloads)),
    )


generate_fixture = generate_synthetic_bundle
generate_bundle = generate_synthetic_bundle


def run12_calibration_case(*, replica: int = 1) -> SyntheticBundle:
    """Generate the plan's named non-evidential run-12 calibration case."""
    return generate_synthetic_bundle(
        run12_calibration_parameters(), replica=replica
    )


def generate_synthetic_bundles(
    parameters: SyntheticParameters | None = None,
) -> tuple[SyntheticBundle, ...]:
    """Generate the three independent replica views declared by the plan."""
    return tuple(
        generate_synthetic_bundle(parameters, replica=replica)
        for replica in range(1, BOUNDS["replicas"] + 1)
    )


def _write_unchanged_or_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValidationError(
                f"refusing to overwrite differing synthetic artifact {path}"
            )
        return
    path.write_bytes(payload)


def write_synthetic_bundle(root: Path, bundle: SyntheticBundle) -> None:
    """Materialize one view without overwriting any differing existing file."""
    root = root.resolve()
    for path, document in bundle.documents.items():
        _write_unchanged_or_new(root / path, canonical_json_bytes(document))
    referenced = {
        digest
        for hashes in bundle.ordered_page_sha256.values()
        for digest in hashes
    }
    for digest in referenced:
        _write_unchanged_or_new(
            root / f"page-store/{digest}.page", bundle.page_bytes(digest)
        )


def write_synthetic_bundles(
    root: Path, parameters: SyntheticParameters | None = None
) -> tuple[SyntheticBundle, ...]:
    """Generate and materialize all plan-declared independent replicas."""
    bundles = generate_synthetic_bundles(parameters)
    for bundle in bundles:
        write_synthetic_bundle(root, bundle)
    return bundles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        bundles = write_synthetic_bundles(args.output)
    except (OSError, ValidationError) as exc:
        parser.exit(1, f"A2 synthetic generation failed: {exc}\n")
    unique_pages = len(
        {digest for bundle in bundles for digest in bundle._payloads}
    )
    print(
        f"wrote {len(bundles)} A2 synthetic replicas with "
        f"{unique_pages} unique pages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
