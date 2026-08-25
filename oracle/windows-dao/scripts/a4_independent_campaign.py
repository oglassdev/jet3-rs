#!/usr/bin/env python3
"""Independent A4 campaign predicates and frozen transcript projection."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from a4_independent_bundle import (
    LoadedBundle,
    ValidationError,
    canonical_document_bytes,
    canonical_json_bytes,
)
from a4_independent_contract import (
    CONTRACT,
    ContractError,
    validate_canonical_snapshot,
    validate_snapshot_schedule,
)


_CAMPAIGN_IDS = tuple(CONTRACT.campaign_predicates)
_TRANSCRIPT_NAMES = (
    "row_directories",
    "locators",
    "map_transitions",
    "reference_bitmaps",
    "catalog_roots",
    "catalog_fields",
)
_TRANSCRIPT_KINDS = {
    "row_directories": "row_directory",
    "locators": "locator",
    "map_transitions": "map_transition",
    "reference_bitmaps": "reference_bitmap",
    "catalog_roots": "catalog_root",
    "catalog_fields": "catalog_field",
}
_CATEGORY_CODES = {
    name: code for name, code in zip(
        ("locators", "row_directories", "map_transitions", "reference_bitmaps",
         "catalog_roots", "catalog_fields"),
        range(1, 7),
        strict=True,
    )
}
_PAGE_MARKER = hashlib.sha256(b"dao-a4-qualified-page-transcript-v1").digest()[:15]


@dataclass(frozen=True)
class CampaignFailure:
    """The first preregistered campaign terminal reached by the bundle."""

    predicate_id: str
    detail: str


@dataclass(frozen=True)
class CampaignEvaluation:
    """Ordered campaign rows plus independently measured resource counters."""

    predicate_rows: tuple[Mapping[str, Any], ...]
    first_failure: CampaignFailure | None
    resources: Mapping[str, int]

    @property
    def passed(self) -> bool:
        return self.first_failure is None


class _Mismatch(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _entry_bytes(bundle: LoadedBundle, relative: str, value: Mapping[str, Any]) -> bytes:
    """Rebuild canonical JSON bytes and bind them to the retained inventory."""
    entry = bundle.entries.get(relative)
    encoded = canonical_document_bytes(value)
    if entry is None or entry.get("media_type") != "application/json":
        raise _Mismatch(f"missing JSON inventory entry {relative}")
    if entry.get("size_bytes") != len(encoded):
        raise _Mismatch(f"size differs for {relative}")
    if entry.get("sha256") != hashlib.sha256(encoded).hexdigest():
        raise _Mismatch(f"hash differs for {relative}")
    try:
        actual = (Path(bundle.root) / relative).read_bytes()
    except OSError as exc:
        raise _Mismatch(f"unreadable artifact {relative}") from exc
    if actual != encoded:
        raise _Mismatch(f"canonical bytes differ for {relative}")
    return encoded


def _checkpoint_rows(bundle: LoadedBundle, replica: int) -> Mapping[str, Mapping[str, Any]]:
    rows = bundle.replicas[replica].observation["checkpoints"]
    return {row["checkpoint_id"]: row for row in rows}


def _check_idle(bundle: LoadedBundle) -> None:
    idle_pairs = CONTRACT.plan["checkpoint_design"]["idle_pairs"]
    for replica_number in (1, 2, 3):
        replica = bundle.replicas[replica_number]
        for left, right in idle_pairs:
            left_index, right_index = replica.index(left), replica.index(right)
            left_hashes = tuple(left_index["ordered_page_sha256"])
            right_hashes = tuple(right_index["ordered_page_sha256"])
            if left_hashes != right_hashes:
                raise _Mismatch(f"replica {replica_number} index {left}/{right}")
            if left_index["database_sha256"] != right_index["database_sha256"]:
                raise _Mismatch(f"replica {replica_number} MDB digest {left}/{right}")
            for page_number in range(len(left_hashes)):
                if replica.page(left, page_number) != replica.page(right, page_number):
                    raise _Mismatch(
                        f"replica {replica_number} MDB byte {left}/{right} page {page_number}"
                    )
            if replica.snapshots[left]["tables"] != replica.snapshots[right]["tables"]:
                raise _Mismatch(f"replica {replica_number} snapshot tables {left}/{right}")


def _common_snapshot_binding(
    bundle: LoadedBundle,
    replica_number: int,
    checkpoint_id: str,
    ordinal: int,
    snapshot: Mapping[str, Any],
) -> None:
    replica = bundle.replicas[replica_number]
    manifest = bundle.manifest
    expected = {
        "experiment_id": manifest["experiment_id"],
        "plan_sha256": manifest["plan_sha256"],
        "revision_plan_sha256": manifest["revision_plan_sha256"],
        "producer_commit": manifest["producer_commit"],
        "campaign_id": manifest["campaign_id"],
        "environment_sha256": bundle.entries[
            f"environment/replica-{replica_number:02d}.json"
        ]["sha256"],
        "provider_sha256": manifest["provider_sha256"],
        "replica": replica_number,
        "checkpoint_id": checkpoint_id,
        "ordinal": ordinal,
    }
    for key, value in expected.items():
        if snapshot.get(key) != value:
            raise _Mismatch(f"snapshot binding r{replica_number}:{checkpoint_id}:{key}")
    index = replica.index(checkpoint_id)
    if not (
        snapshot.get("database_sha256_before_read")
        == snapshot.get("database_sha256_after_read")
        == index.get("database_sha256")
    ):
        raise _Mismatch(f"snapshot before/after r{replica_number}:{checkpoint_id}")


def _check_schema(bundle: LoadedBundle) -> None:
    for replica_number in (1, 2, 3):
        replica = bundle.replicas[replica_number]
        observations = list(replica.observation["checkpoints"])
        if len(replica.snapshots) != 25 or len(observations) != 25:
            raise _Mismatch(f"snapshot cardinality replica {replica_number}")
        for ordinal, checkpoint_id in enumerate(CONTRACT.checkpoint_ids):
            try:
                snapshot = replica.snapshots[checkpoint_id]
                CONTRACT.validate_document(snapshot, "dao_a4_schema_snapshot")
                validate_canonical_snapshot(snapshot, checkpoint_id)
                validate_snapshot_schedule(
                    snapshot, CONTRACT.plan, replica_number, checkpoint_id
                )
            except (KeyError, ContractError) as exc:
                detail = getattr(exc, "detail", str(exc))
                raise _Mismatch(
                    f"snapshot contract r{replica_number}:{checkpoint_id}: {detail}"
                ) from exc
            row = observations[ordinal]
            if row.get("checkpoint_id") != checkpoint_id or row.get("ordinal") != ordinal:
                raise _Mismatch(f"snapshot ordinal r{replica_number}:{checkpoint_id}")
            _common_snapshot_binding(
                bundle, replica_number, checkpoint_id, ordinal, snapshot
            )
            relative = (
                f"schema-snapshots/replica-{replica_number:02d}/"
                f"{ordinal:02d}-{checkpoint_id}.json"
            )
            reference = row.get("dao_schema_snapshot", {})
            entry = bundle.entries.get(relative)
            if entry is None or reference != {
                "path": relative,
                "sha256": entry["sha256"],
                "size_bytes": entry["size_bytes"],
            }:
                raise _Mismatch(f"snapshot reference r{replica_number}:{checkpoint_id}")
            _entry_bytes(bundle, relative, snapshot)
            tables = snapshot["tables"]
            counts = row["table_row_counts"]
            actual_counts = {table["logical_role"]: table["row_count"] for table in tables}
            if any(counts[role] != actual_counts.get(role, 0) for role in counts):
                raise _Mismatch(f"snapshot row counts r{replica_number}:{checkpoint_id}")
            reread = [
                {
                    "role": table["logical_role"],
                    "row_count": table["row_count"],
                    "rolling_sha256": table["rolling_row_sha256"],
                }
                for table in tables
            ]
            if row["dao_reread"] != reread:
                raise _Mismatch(f"snapshot reread r{replica_number}:{checkpoint_id}")


def _changed_indices(previous: Sequence[str], current: Sequence[str]) -> list[int]:
    return [
        page
        for page in range(max(len(previous), len(current)))
        if (previous[page] if page < len(previous) else None)
        != (current[page] if page < len(current) else None)
    ]


def _check_reconstruction(bundle: LoadedBundle) -> None:
    for replica_number in (1, 2, 3):
        replica = bundle.replicas[replica_number]
        observations = _checkpoint_rows(bundle, replica_number)
        previous: Sequence[str] = ()
        if len(replica.indexes) != 25:
            raise _Mismatch(f"page-index cardinality replica {replica_number}")
        for ordinal, checkpoint_id in enumerate(CONTRACT.checkpoint_ids):
            try:
                index = replica.index(checkpoint_id)
                CONTRACT.validate_document(index, "dao_a4_page_index")
            except (KeyError, ContractError) as exc:
                raise _Mismatch(f"page index contract r{replica_number}:{checkpoint_id}") from exc
            hashes = index["ordered_page_sha256"]
            expected_previous = None if ordinal == 0 else CONTRACT.checkpoint_ids[ordinal - 1]
            if (
                index["replica"] != replica_number
                or index["checkpoint_id"] != checkpoint_id
                or index["ordinal"] != ordinal
                or index["predecessor_checkpoint_id"] != expected_previous
                or index["page_count"] != len(hashes)
                or index["file_size_bytes"] != len(hashes) * 2048
                or index["changed_page_indices"]
                != ([] if ordinal == 0 else _changed_indices(previous, hashes))
            ):
                raise _Mismatch(f"page index structure r{replica_number}:{checkpoint_id}")
            row = observations[checkpoint_id]
            if (
                row["actual_file_pages"] != len(hashes)
                or row["actual_size_bytes"] != len(hashes) * 2048
            ):
                raise _Mismatch(f"observation size r{replica_number}:{checkpoint_id}")
            digest = hashlib.sha256()
            for page_number, expected_hash in enumerate(hashes):
                payload = replica.page(checkpoint_id, page_number)
                if payload is None or len(payload) != 2048:
                    raise _Mismatch(f"missing page r{replica_number}:{checkpoint_id}:{page_number}")
                actual_hash = hashlib.sha256(payload).hexdigest()
                if actual_hash != expected_hash:
                    raise _Mismatch(f"ordered page hash r{replica_number}:{checkpoint_id}:{page_number}")
                digest.update(payload)
            if digest.hexdigest() != index["database_sha256"]:
                raise _Mismatch(f"database hash r{replica_number}:{checkpoint_id}")
            previous = hashes


def _resource_measurements(bundle: LoadedBundle) -> dict[str, int]:
    bounds = CONTRACT.bounds
    result: dict[str, int] = {
        "unique_page_blobs": len(bundle.page_store.paths),
        "retained_page_store_bytes": len(bundle.page_store.paths) * 2048,
        "bundle_bytes": sum(int(entry["size_bytes"]) for entry in bundle.entries.values()),
    }
    union = {
        digest
        for replica in bundle.replicas.values()
        for index in replica.indexes.values()
        for digest in index["ordered_page_sha256"]
    }
    if union != set(bundle.page_store.paths):
        raise _Mismatch("page-store digest union")
    roles = tuple(CONTRACT.plan["tables"]["logical_roles"])
    for replica_number in (1, 2, 3):
        replica = bundle.replicas[replica_number]
        previous_hashes: Sequence[str] = ()
        previous_counts = {role: 0 for role in roles}
        changed = inserted = logical = maximum_pages = maximum_companion = 0
        for ordinal, checkpoint_id in enumerate(CONTRACT.checkpoint_ids):
            index = replica.index(checkpoint_id)
            hashes = index["ordered_page_sha256"]
            if ordinal:
                changed += len(_changed_indices(previous_hashes, hashes))
            logical += len(hashes) * 2048
            maximum_pages = max(maximum_pages, len(hashes))
            snapshot_counts = {
                table["logical_role"]: table["row_count"]
                for table in replica.snapshots[checkpoint_id]["tables"]
            }
            counts = {role: snapshot_counts.get(role, 0) for role in roles}
            inserted += sum(max(0, counts[role] - previous_counts[role]) for role in roles)
            previous_counts, previous_hashes = counts, hashes
            checkpoint = replica.checkpoint_observation(checkpoint_id)
            if checkpoint["inserted_rows_total"] != inserted:
                raise _Mismatch(
                    f"retained cumulative inserts replica {replica_number}:{checkpoint_id}"
                )
            companion = checkpoint["post_close_companion"]
            maximum_companion = max(maximum_companion, companion["observed_size_bytes"])
        prefix = f"replica_{replica_number}_"
        result.update({
            prefix + "checkpoints": len(replica.indexes),
            prefix + "final_pages": maximum_pages,
            prefix + "logical_checkpoint_read_bytes": logical,
            prefix + "inserted_rows": inserted,
            prefix + "changed_hash_entries": changed,
            prefix + "max_companion_bytes": maximum_companion,
        })
        observation = replica.observation
        if (
            observation["logical_checkpoint_read_bytes"] != logical
            or observation["inserted_rows_total"] != inserted
            or observation["changed_hash_entries"] != changed
        ):
            raise _Mismatch(f"retained resource counter replica {replica_number}")
    if bundle.manifest["page_blob_count"] != result["unique_page_blobs"]:
        raise _Mismatch("manifest page blob count")
    if bundle.manifest["bundle_size_bytes_excluding_manifest"] != result["bundle_bytes"]:
        raise _Mismatch("manifest bundle size")
    comparisons = {
        "unique_page_blobs": "max_unique_page_blobs",
        "retained_page_store_bytes": "max_retained_page_store_bytes",
        "bundle_bytes": "max_bundle_bytes",
    }
    for replica_number in (1, 2, 3):
        prefix = f"replica_{replica_number}_"
        comparisons.update({
            prefix + "checkpoints": "max_checkpoints_per_replica",
            prefix + "final_pages": "max_final_pages_per_replica",
            prefix + "logical_checkpoint_read_bytes":
                "max_logical_checkpoint_read_bytes_per_replica",
            prefix + "inserted_rows": "max_inserted_rows_per_replica",
            prefix + "changed_hash_entries": "max_changed_hash_entries_per_replica",
            prefix + "max_companion_bytes": "max_companion_bytes_per_checkpoint",
        })
    for measurement, bound in comparisons.items():
        if result[measurement] > int(bounds[bound]):
            raise _Mismatch(f"{measurement}={result[measurement]} exceeds {bound}={bounds[bound]}")
    return result


def _campaign_row(predicate_id: str, status: str) -> dict[str, Any]:
    contract = next(
        row for row in CONTRACT.plan["predicate_registry"]["predicate_contracts"]
        if row["predicate_id"] == predicate_id
    )
    return {
        "predicate_id": predicate_id,
        "order": contract["order"],
        "scope": "campaign",
        "status": status,
        "terminal_predicate_id": predicate_id if status == "fail" else None,
        "predicate_measured_survivor_count": 0,
        "derivation_survivor_count": 0,
        "reachability_fixture_id": contract["reachability_fixture_id"],
    }


def recompute_campaign(bundle: LoadedBundle) -> CampaignEvaluation:
    """Execute the four campaign predicates in their registered terminal order."""
    checks = (_check_idle, _check_schema, _check_reconstruction)
    rows: list[Mapping[str, Any]] = []
    failure: CampaignFailure | None = None
    resources: Mapping[str, int] = {}
    for index, predicate_id in enumerate(_CAMPAIGN_IDS):
        if failure is not None:
            rows.append(_campaign_row(predicate_id, "not_applicable"))
            continue
        try:
            if index < len(checks):
                checks[index](bundle)
            else:
                resources = _resource_measurements(bundle)
        except _Mismatch as exc:
            failure = CampaignFailure(predicate_id, exc.detail)
            rows.append(_campaign_row(predicate_id, "fail"))
        except (ContractError, ValidationError, KeyError, TypeError, ValueError, IndexError) as exc:
            failure = CampaignFailure(predicate_id, f"invalid campaign input: {exc}")
            rows.append(_campaign_row(predicate_id, "fail"))
        else:
            rows.append(_campaign_row(predicate_id, "pass"))
    return CampaignEvaluation(tuple(rows), failure, resources)


def require_campaign(bundle: LoadedBundle) -> CampaignEvaluation:
    """Return a passing evaluation or raise the reached predicate id as its code."""
    result = recompute_campaign(bundle)
    if result.first_failure is not None:
        raise ValidationError(
            result.first_failure.predicate_id, result.first_failure.detail
        )
    return result


def _all_results(frozen: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    layers = frozen["layers"]
    h4 = layers["h4_catalog_bootstrap"]
    return (
        layers["h1_tdef_to_map_row"],
        layers["h2_row_identity_map_role"],
        layers["h3_indirect_traversal"],
        h4["root_result"], h4["structural_result"], h4["encoding_result"],
    )


def _all_candidates(frozen: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    output: list[Mapping[str, Any]] = []
    for result in _all_results(frozen):
        output.extend(result["candidates"])
        evidence = result.get("terminal_evidence")
        if isinstance(evidence, Mapping) and evidence.get("kind") == "replica_pair":
            output.extend(row["complete_candidate"] for row in evidence["entries"])
    return tuple(output)


def _interval(start: str, end: str) -> tuple[str, ...]:
    first = CONTRACT.checkpoint_ids.index(start)
    last = CONTRACT.checkpoint_ids.index(end)
    if first > last:
        raise ValidationError("frozen_set_recomputation_mismatch", "reversed checkpoint interval")
    return CONTRACT.checkpoint_ids[first:last + 1]


def _append(
    output: dict[str, list[dict[str, Any]]],
    category: str,
    checkpoint: str,
    page: int,
    detail: bytes,
) -> None:
    output[category].append({
        "kind": _TRANSCRIPT_KINDS[category],
        "checkpoint_id": checkpoint,
        "page": page,
        "detail_hex": detail.hex(),
    })


def _h1_projection(
    bundle: LoadedBundle,
    output: dict[str, list[dict[str, Any]]],
    *,
    h2_reached: bool,
    h3_reached: bool,
) -> tuple[
    set[tuple[int, str, int]],
    set[tuple[int, str, int]],
    set[tuple[int, str, int]],
]:
    tdefs: set[tuple[int, str, int]] = set()
    targets: set[tuple[int, str, int]] = set()
    references: set[tuple[int, str, int]] = set()
    qualified = {
        (row["replica"], row["checkpoint_id"], row["page_number"])
        for row in bundle.frozen["qualified_pages"]
    }
    terminal_projection = any(
        result.get("terminal_predicate_id") is not None
        for result in _all_results(bundle.frozen)
    )
    h1_candidates = tuple(
        row for row in _all_candidates(bundle.frozen)
        if row["model_type"] == "h1_locator_pair"
    )
    h2 = next((row for row in _all_candidates(bundle.frozen)
               if row["model_type"] == "h2_final_role"), None)
    if not h1_candidates:
        return tdefs, targets, references
    row_mask = int(h2["model"]["row_mask"]) if h2 is not None else 0x1FFF
    seen: dict[str, set[tuple[int, str, int, bytes]]] = {
        "locators": set(), "row_directories": set()
    }
    for h1 in h1_candidates:
        offsets = tuple(h1["model"]["locator_offsets"])
        for binding in h1.get("instance_bindings", ()):
            replica_number = binding["replica"]
            replica = bundle.replicas[replica_number]
            checkpoints = _interval(
                binding["applicable_checkpoint_range"]["start"],
                binding["applicable_checkpoint_range"]["end"],
            )
            for checkpoint in checkpoints:
                tdef_page = binding["tdef_page"]
                payload = replica.page(checkpoint, tdef_page)
                if payload is None:
                    raise ValidationError("frozen_set_recomputation_mismatch", "absent TDEF page")
                tdefs.add((replica_number, checkpoint, tdef_page))
                detail = b"".join(payload[offset:offset + 4] for offset in offsets)
                key = (replica_number, checkpoint, tdef_page, detail)
                if key not in seen["locators"]:
                    seen["locators"].add(key)
                    _append(output, "locators", checkpoint, tdef_page, detail)
                if not h2_reached:
                    continue
                for target in binding.get("locator_targets", ()):
                    page_number = target["page"]
                    page = replica.page(checkpoint, page_number)
                    if page is None or len(page) != 2048:
                        raise ValidationError("frozen_set_recomputation_mismatch", "absent map page")
                    targets.add((replica_number, checkpoint, page_number))
                    detail = page[8:14]
                    key = (replica_number, checkpoint, page_number, detail)
                    if not terminal_projection or key not in seen["row_directories"]:
                        seen["row_directories"].add(key)
                        _append(output, "row_directories", checkpoint, page_number, detail)
    if not h3_reached:
        return tdefs, targets, references
    # H3's registered observation order is replica, lifecycle binding, locator
    # ordinal, then checkpoint; it is distinct from H1's checkpoint-major rows.
    h1 = h1_candidates[0]
    terminal_h3 = terminal_projection
    h3_result = bundle.frozen["layers"]["h3_indirect_traversal"]
    h3_evidence = h3_result.get("terminal_evidence")
    invalid_reference_replica = (
        h3_evidence["observation"]["replica"]
        if h3_result.get("terminal_predicate_id") == "A4-H3-REFERENCE-INVALID"
        and isinstance(h3_evidence, Mapping)
        and isinstance(h3_evidence.get("observation"), Mapping)
        else None
    )
    seen_h3: dict[str, set[tuple[int, str, int, bytes]]] = {
        "map_transitions": set(), "reference_bitmaps": set()
    }
    for binding in h1.get("instance_bindings", ()):
        replica_number = binding["replica"]
        replica = bundle.replicas[replica_number]
        checkpoints = _interval(
            binding["applicable_checkpoint_range"]["start"],
            binding["applicable_checkpoint_range"]["end"],
        )
        for target in binding.get("locator_targets", ()):
            page_number, row = target["page"], target["row"]
            for checkpoint in checkpoints:
                page = replica.page(checkpoint, page_number)
                if page is None or len(page) != 2048:
                    raise ValidationError("frozen_set_recomputation_mismatch", "absent map page")
                count = int.from_bytes(page[8:10], "little")
                if not 0 <= row < count or 10 + 2 * count > 2048:
                    raise ValidationError("frozen_set_recomputation_mismatch", "invalid map row")
                starts = [
                    int.from_bytes(page[10 + 2 * item:12 + 2 * item], "little") & row_mask
                    for item in range(count)
                ]
                start = starts[row]
                end = 2048 if row == 0 else starts[row - 1]
                if not 10 + 2 * count <= start < end <= 2048:
                    raise ValidationError("frozen_set_recomputation_mismatch", "invalid row bound")
                record = page[start:end]
                if not record or record[0] not in (0, 1):
                    raise ValidationError("frozen_set_recomputation_mismatch", "unsupported map row")
                representation = b"type_0" if record[0] == 0 else b"type_1"
                map_detail = page[:1] if terminal_h3 else representation
                map_key = (replica_number, checkpoint, page_number, map_detail)
                if not terminal_h3 or map_key not in seen_h3["map_transitions"]:
                    seen_h3["map_transitions"].add(map_key)
                    _append(output, "map_transitions", checkpoint, page_number, map_detail)
                if record[0] == 1:
                    if (len(record) - 1) % 4:
                        raise ValidationError("frozen_set_recomputation_mismatch", "invalid type-1 row")
                    for offset in range(1, len(record), 4):
                        reference = int.from_bytes(record[offset:offset + 4], "little")
                        if reference:
                            identity = (replica_number, checkpoint, reference)
                            if replica_number == invalid_reference_replica:
                                references.add(identity)
                                continue
                            if identity not in qualified:
                                continue
                            detail = reference.to_bytes(4, "little")
                            key = (*identity, detail)
                            if not terminal_h3 or key not in seen_h3["reference_bitmaps"]:
                                seen_h3["reference_bitmaps"].add(key)
                                _append(
                                    output, "reference_bitmaps", checkpoint, reference,
                                    detail,
                                )
                            references.add(identity)
    return tdefs, targets, references


def _h4_projection(
    bundle: LoadedBundle,
    output: dict[str, list[dict[str, Any]]],
) -> tuple[set[int], set[tuple[int, str, int]]]:
    catalog_pages: set[tuple[int, int]] = set()
    raw_identities: set[tuple[int, str, int]] = set()
    for candidate in _all_candidates(bundle.frozen):
        if candidate["model_type"] == "h4_catalog_root":
            offsets = bytes(candidate["model"]["locator_offsets"])
            for binding in candidate["instance_bindings"]:
                catalog_pages.add((binding["replica"], binding["tdef_page"]))
                _append(output, "catalog_roots", "EMPTY", binding["tdef_page"], offsets)
        elif candidate["model_type"] == "h4_operation_record":
            model = candidate["model"]
            catalog_pages.add(
                (model["replica"], model["canonical_record_locator"]["page"])
            )
    evidence = bundle.occurrence_evidence
    if evidence is None:
        return catalog_pages, raw_identities
    for group in evidence["replica_groups"]:
        replica_number = group["replica"]
        replica = bundle.replicas[replica_number]
        for operation in group["operation_bindings"]:
            checkpoint = operation["operation_id"]
            locator = operation["canonical_record_locator"]
            page_number = locator["page"]
            page = replica.page(checkpoint, page_number)
            if page is None:
                raise ValidationError("frozen_set_recomputation_mismatch", "absent catalog page")
            raw_identities.add((replica_number, checkpoint, page_number))
            catalog_pages.add((replica_number, page_number))
            _append(
                output, "catalog_fields", checkpoint, page_number,
                page[locator["row_start"]:locator["row_end"]][:64],
            )
    return catalog_pages, raw_identities


def _marker_category(
    identity: tuple[int, str, int],
    payload: bytes,
    tdefs: set[tuple[int, str, int]],
    targets: set[tuple[int, str, int]],
    references: set[tuple[int, str, int]],
    root_pages: set[tuple[int, str, int]],
    system_maps: set[tuple[int, str, int]],
    catalog_pages: set[tuple[int, int]],
    *,
    h2_reached: bool,
    h3_reached: bool,
    h4_reached: bool,
) -> str:
    if not h2_reached:
        return "locators"
    if not h3_reached:
        return "row_directories"
    physical = (identity[0], identity[2])
    if identity in references or payload[:1] == b"\x05":
        return "reference_bitmaps"
    if h4_reached and identity in root_pages:
        return "catalog_roots"
    if h4_reached and identity in system_maps:
        return "row_directories"
    if h4_reached and physical in catalog_pages and identity not in tdefs:
        return "catalog_fields"
    if h4_reached and payload[:1] == b"\x01" and identity not in targets:
        return "catalog_fields"
    return "map_transitions"


def recompute_frozen_transcripts(bundle: LoadedBundle) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Rebuild raw and coverage transcripts from frozen identities and bundle pages."""
    output: dict[str, list[dict[str, Any]]] = {name: [] for name in _TRANSCRIPT_NAMES}
    layers = bundle.frozen["layers"]
    h2_reached = layers["h2_row_identity_map_role"]["status"] != "not_applicable"
    h3_reached = layers["h3_indirect_traversal"]["status"] != "not_applicable"
    h4_reached = (
        layers["h4_catalog_bootstrap"]["root_result"]["status"] != "not_applicable"
    )
    tdefs, targets, references = _h1_projection(
        bundle, output, h2_reached=h2_reached, h3_reached=h3_reached
    )
    if h4_reached:
        catalog_pages, raw_catalog = _h4_projection(bundle, output)
    else:
        catalog_pages, raw_catalog = set(), set()
    catalog_pages.update((replica, page) for replica, _, page in tdefs)
    root_pages: set[tuple[int, str, int]] = set()
    system_maps: set[tuple[int, str, int]] = set()
    root_offsets = next(
        (
            tuple(candidate["model"]["locator_offsets"])
            for candidate in _all_candidates(bundle.frozen)
            if candidate["model_type"] == "h4_catalog_root"
        ),
        (),
    )
    if not root_offsets:
        root_offsets = next(
            (
                tuple(candidate["model"]["locator_offsets"])
                for candidate in _all_candidates(bundle.frozen)
                if candidate["model_type"] == "h1_locator_pair"
            ),
            (),
        )
    root_result = bundle.frozen["layers"]["h4_catalog_bootstrap"]["root_result"]
    root_replicas = (
        (1,)
        if root_result.get("terminal_predicate_id") == "A4-H4-CATALOG-ROOT-NONE"
        else (1, 2)
    )
    for replica_number in root_replicas:
        replica = bundle.replicas[replica_number]
        for row in bundle.frozen["qualified_pages"]:
            if row["replica"] != replica_number or row["checkpoint_id"] != "EMPTY":
                continue
            page_number = row["page_number"]
            payload = replica.page("EMPTY", page_number) or b""
            if payload[:1] != b"\x02" or len(root_offsets) != 2:
                continue
            root_physical = page_number
            for candidate_row in bundle.frozen["qualified_pages"]:
                if (
                    candidate_row["replica"] != replica_number
                    or candidate_row["page_number"] != root_physical
                ):
                    continue
                checkpoint = candidate_row["checkpoint_id"]
                checkpoint_payload = replica.page(checkpoint, root_physical) or b""
                root_pages.add((replica_number, checkpoint, root_physical))
                for offset in root_offsets:
                    locator = checkpoint_payload[offset:offset + 4]
                    if len(locator) == 4:
                        system_maps.add((
                            replica_number,
                            checkpoint,
                            int.from_bytes(locator[1:4], "little"),
                        ))
    ordinal = {checkpoint: index for index, checkpoint in enumerate(CONTRACT.checkpoint_ids)}
    qualified = bundle.frozen["qualified_pages"]
    keys = [(row["replica"], row["checkpoint_id"], row["page_number"]) for row in qualified]
    if keys != sorted(set(keys), key=lambda row: (row[0], ordinal[row[1]], row[2])):
        raise ValidationError("frozen_set_recomputation_mismatch", "qualified page order")
    for identity in keys:
        if identity in raw_catalog:
            continue
        replica_number, checkpoint, page_number = identity
        payload = bundle.replicas[replica_number].page(checkpoint, page_number) or b""
        category = _marker_category(
            identity, payload, tdefs, targets, references,
            root_pages, system_maps, catalog_pages,
            h2_reached=h2_reached, h3_reached=h3_reached, h4_reached=h4_reached,
        )
        detail = (
            _PAGE_MARKER
            + bytes((replica_number, _CATEGORY_CODES[category]))
            + hashlib.sha256(payload).digest()[:15]
        )
        _append(output, category, checkpoint, page_number, detail)
    return {name: tuple(output[name]) for name in _TRANSCRIPT_NAMES}


def verify_frozen_transcripts(bundle: LoadedBundle) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Reject any missing, extra, category-moved, or byte-unbound transcript."""
    expected = recompute_frozen_transcripts(bundle)
    actual = bundle.frozen.get("transcripts")
    if not isinstance(actual, Mapping) or set(actual) != set(_TRANSCRIPT_NAMES):
        raise ValidationError("frozen_set_recomputation_mismatch", "transcript categories")
    terminal_projection = any(
        result.get("terminal_predicate_id") is not None
        for result in _all_results(bundle.frozen)
    )
    for name in _TRANSCRIPT_NAMES:
        rows = tuple(actual[name])
        if terminal_projection:
            split = lambda values: (
                tuple(row for row in values if not bytes.fromhex(row["detail_hex"]).startswith(_PAGE_MARKER)),
                tuple(row for row in values if bytes.fromhex(row["detail_hex"]).startswith(_PAGE_MARKER)),
            )
            raw, markers = split(rows)
            expected_raw, expected_markers = split(expected[name])
            matches = (
                Counter(canonical_json_bytes(row) for row in raw)
                == Counter(canonical_json_bytes(row) for row in expected_raw)
                and markers == expected_markers
                and rows == raw + markers
            )
        else:
            matches = rows == expected[name]
        if not matches:
            raise ValidationError(
                "frozen_set_recomputation_mismatch", f"transcript {name}"
            )
    return expected
