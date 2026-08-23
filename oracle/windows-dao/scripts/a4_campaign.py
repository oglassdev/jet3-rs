#!/usr/bin/env python3
"""Shared synthetic A4 campaign container: blobs, page indexes, DAO snapshots, lifecycle events.

A campaign is exactly what the dry-run honesty clause permits a fixture to be:
content-addressed 2,048-byte page blobs, one ordered page index per
replica/checkpoint, one canonical DAO schema snapshot per replica/checkpoint,
and the plan-derived checkpoint events. It carries no verdict booleans.
Fixtures mutate a campaign through :meth:`Campaign.patch_page` and the snapshot
/ page-index documents, after which :meth:`Campaign.refresh` re-derives every
dependent field (changed indices, database SHA-256, snapshot before/after) so a
mutation stays a consistent campaign unless the fixture deliberately breaks it.

A4 rule | implementation
--- | ---
Content-addressed page store, duplicates stored once | :attr:`Campaign.blobs`
Ordered page index with changed_page_indices per checkpoint | :meth:`Campaign.page_index`
Canonical snapshot ordinals after exact scheduled-name filtering | :func:`snapshot_document`
Rolling row hash from the row algorithm | :func:`a4_spec.rolling_sha256`
Fixture identity hashes for the transcript | :meth:`Campaign.inventory`
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from a4_spec import (
    CHECKPOINTS, EVENTS, EXPECTED_SCHEMA, EXPERIMENT_ID, FIELD_DEFS, INDEX_DEF, INSTANCE_BY_ID, ORDINAL, PAGE_SIZE,
    PLAN_SHA256, REVISION_PLAN_SHA256, ROLE_BINDINGS, canonical_json_bytes, rolling_sha256, sha256_hex,
)

ZERO_COMMIT = "0" * 40
PROVIDER_SHA256 = sha256_hex(b"A4 synthetic DAO provider; no binary exists")
ENVIRONMENT_SHA256 = sha256_hex(b"A4 synthetic environment; Windows x86 PowerShell 5 code page 1252 declared, not observed")
CAMPAIGN_ID = "a4-dryrun-synthetic"


def _utf16(name: str) -> list[int]:
    return [int.from_bytes(name.encode("utf-16-le")[i: i + 2], "little") for i in range(0, 2 * len(name), 2)]


def _named(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "name_utf16_code_units": _utf16(name),
        "name_windows_1252_hex": name.encode("cp1252").hex(),
        "name_utf8_hex": name.encode("utf-8").hex(),
    }


def _field(ordinal: int, definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "ordinal_source": "Fields zero-based position after Refresh and the all-fields filter",
        **_named(definition["name"]),
        "type": definition["dao_type_numeric"],
        "size": definition["size"],
        "attributes": definition["attributes_numeric"],
        "required": definition["required"],
        "allow_zero_length": definition["allow_zero_length"],
    }


def _index() -> dict[str, Any]:
    return {
        "ordinal": 0,
        "ordinal_source": "Indexes zero-based position after Refresh and exact A4IX_ID scheduled-name filtering",
        **_named(INDEX_DEF["name"]),
        "attributes": 0,
        "primary": INDEX_DEF["primary"],
        "unique": INDEX_DEF["unique"],
        "required": INDEX_DEF["required"],
        "ignore_nulls": INDEX_DEF["ignore_nulls"],
        "fields": [{
            "ordinal": 0,
            "ordinal_source": "Index.Fields zero-based position after Refresh and the all-fields filter",
            **_named(INDEX_DEF["fields"][0]),
            "descending": INDEX_DEF["descending"],
        }],
    }


def snapshot_tables(replica: int, checkpoint: str, row_counts: dict[str, int]) -> list[dict[str, Any]]:
    """Canonical tables array: extant scheduled tables, ordinals after exact-name filtering."""
    rows = []
    for token in EXPECTED_SCHEMA[checkpoint]:
        parts = token.split(":")
        role = parts[0]
        version = parts[1] if parts[1].startswith("v") else "v1"
        shape = parts[-1]
        name = ROLE_BINDINGS[replica][role]
        fields = [_field(0, FIELD_DEFS[0])]
        if "payload" in shape:
            fields.append(_field(1, FIELD_DEFS[1]))
        count = row_counts.get(role, 0)
        rows.append({
            "logical_role": role,
            "lifecycle_instance": f"{role}-{version}",
            **_named(name),
            "attributes": 0,
            "row_count": count,
            "rolling_row_sha256": rolling_sha256(role, count),
            "fields": fields,
            "indexes": [_index()] if "index" in shape else [],
        })
    rows.sort(key=lambda r: bytes.fromhex(r["name_windows_1252_hex"]))
    for ordinal, row in enumerate(rows):
        row["ordinal"] = ordinal
        row["ordinal_source"] = "TableDefs zero-based position after Refresh and exact extant scheduled-name filtering"
    return rows


def _binding(replica: int, checkpoint: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": REVISION_PLAN_SHA256,
        "producer_commit": ZERO_COMMIT,
        "campaign_id": CAMPAIGN_ID,
        "environment_sha256": ENVIRONMENT_SHA256,
        "provider_sha256": PROVIDER_SHA256,
        "replica": replica,
        "checkpoint_id": checkpoint,
        "ordinal": ORDINAL[checkpoint],
    }


def snapshot_document(replica: int, checkpoint: str, database_sha256: str, row_counts: dict[str, int]) -> dict[str, Any]:
    return {
        "document_type": "dao_a4_schema_snapshot",
        **_binding(replica, checkpoint),
        "windows_ansi_code_page": 1252,
        "database_sha256_before_read": database_sha256,
        "database_sha256_after_read": database_sha256,
        "database_unchanged_by_read": True,
        "dao_identifier_observable": False,
        "identity_oracle": "listed_operation_instance_equality_only",
        "canonicalization": "synthetic dry-run snapshot canonicalized per dao-schema-snapshot.schema.json",
        "tables": snapshot_tables(replica, checkpoint, row_counts),
    }


@dataclass
class ReplicaData:
    number: int
    pages: dict[str, list[str]] = field(default_factory=dict)  # checkpoint -> ordered page hashes
    page_indexes: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    row_counts: dict[str, dict[str, int]] = field(default_factory=dict)  # checkpoint -> role -> rows
    meta: dict[str, Any] = field(default_factory=dict)  # generator bookkeeping (instance pages); not evidence


@dataclass
class Campaign:
    blobs: dict[str, bytes] = field(default_factory=dict)
    replicas: dict[int, ReplicaData] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=lambda: [e.__dict__ for e in EVENTS])
    _db_cache: dict[tuple[str, ...], str] = field(default_factory=dict, repr=False)

    # ----------------------------------------------------------------- construction
    def store(self, page: bytes) -> str:
        digest = sha256_hex(page)
        self.blobs.setdefault(digest, page)
        return digest

    def page(self, replica: int, checkpoint: str, number: int) -> bytes | None:
        hashes = self.replicas[replica].pages[checkpoint]
        if number < 0 or number >= len(hashes):
            return None
        return self.blobs.get(hashes[number])

    def page_count(self, replica: int, checkpoint: str) -> int:
        return len(self.replicas[replica].pages[checkpoint])

    def database_sha256(self, hashes: list[str]) -> str:
        key = tuple(hashes)
        if key not in self._db_cache:
            digest = hashlib.sha256()
            for h in key:
                digest.update(self.blobs.get(h, b""))
            self._db_cache[key] = digest.hexdigest()
        return self._db_cache[key]

    def page_index(self, replica: int, checkpoint: str) -> dict[str, Any]:
        data = self.replicas[replica]
        hashes = data.pages[checkpoint]
        ordinal = ORDINAL[checkpoint]
        predecessor = CHECKPOINTS[ordinal - 1] if ordinal else None
        previous = data.pages.get(predecessor, []) if predecessor else []
        changed = [i for i, h in enumerate(hashes) if i >= len(previous) or previous[i] != h]
        return {
            "document_type": "dao_a4_page_index",
            **_binding(replica, checkpoint),
            "predecessor_checkpoint_id": predecessor,
            "page_count": len(hashes),
            "file_size_bytes": len(hashes) * PAGE_SIZE,
            "database_sha256": self.database_sha256(hashes),
            "ordered_page_sha256": list(hashes),
            "changed_page_indices": changed,
        }

    def refresh(self, replica: int) -> None:
        """Re-derive page indexes and snapshot hashes from the current page sequences."""
        data = self.replicas[replica]
        for checkpoint in CHECKPOINTS:
            if checkpoint not in data.pages:
                continue
            index = self.page_index(replica, checkpoint)
            data.page_indexes[checkpoint] = index
            snapshot = snapshot_document(replica, checkpoint, index["database_sha256"], data.row_counts.get(checkpoint, {}))
            data.snapshots[checkpoint] = snapshot

    def patch_page(self, replica: int, checkpoint: str, number: int, page: bytes) -> None:
        hashes = self.replicas[replica].pages[checkpoint]
        if number >= len(hashes):
            raise IndexError(f"page {number} beyond checkpoint {checkpoint} ({len(hashes)} pages)")
        hashes[number] = self.store(page)

    def patch_from(self, replica: int, first_checkpoint: str, number: int, transform) -> None:
        """Apply ``transform(bytes) -> bytes`` to ``number`` at every checkpoint from ``first_checkpoint`` on."""
        for checkpoint in CHECKPOINTS[ORDINAL[first_checkpoint]:]:
            current = self.page(replica, checkpoint, number)
            if current is not None:
                self.patch_page(replica, checkpoint, number, transform(current))

    # ----------------------------------------------------------------- identity
    def inventory(self) -> dict[str, Any]:
        """Hashes that identify this exact campaign for the transcript."""
        index_digest = hashlib.sha256()
        snapshot_digest = hashlib.sha256()
        for replica in sorted(self.replicas):
            data = self.replicas[replica]
            for checkpoint in CHECKPOINTS:
                if checkpoint in data.page_indexes:
                    index_digest.update(canonical_json_bytes(data.page_indexes[checkpoint]))
                    snapshot_digest.update(canonical_json_bytes(data.snapshots[checkpoint]))
        blob_digest = hashlib.sha256()
        for h in sorted(self.blobs):
            blob_digest.update(bytes.fromhex(h))
        events_sha = sha256_hex(canonical_json_bytes(self.events))
        combined = sha256_hex((index_digest.hexdigest() + snapshot_digest.hexdigest() + blob_digest.hexdigest() + events_sha).encode())
        return {
            "campaign_sha256": combined,
            "page_index_inventory_sha256": index_digest.hexdigest(),
            "schema_snapshot_inventory_sha256": snapshot_digest.hexdigest(),
            "page_blob_inventory_sha256": blob_digest.hexdigest(),
            "events_sha256": events_sha,
            "unique_page_blob_count": len(self.blobs),
            "replica_page_counts": {
                str(r): {cp: len(d.pages[cp]) for cp in CHECKPOINTS if cp in d.pages} for r, d in sorted(self.replicas.items())
            },
        }

    def malformed_blobs(self) -> list[str]:
        return sorted(h for h, b in self.blobs.items() if len(b) != PAGE_SIZE or sha256_hex(b) != h)

    def instance_pages(self, replica: int, instance_id: str) -> dict[str, Any]:
        """Generator bookkeeping for fixture authoring; never read by the evaluator."""
        INSTANCE_BY_ID[instance_id]
        return self.replicas[replica].meta["instances"][instance_id]
