"""Materialize worker-shaped A3 replica trees from in-memory synthetic bundles.

The A3 generator yields page views only; fan-in tests need the on-disk
environment, observation, page-index, manifest, and page-store artifacts that
the rebound worker emits. Every document here is schema-checked through
``a3_spec`` before it is written.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a3_generator import SyntheticBundle, generate_synthetic_bundles  # noqa: E402
from a3_spec import CHECKPOINT_IDS, EXPERIMENT_ID, PAGE_SIZE, PLAN, PLAN_SHA256, validate_document  # noqa: E402
from protocol_validation import canonical_json_bytes  # noqa: E402

ROLES = ("D", "L", "P", "H")
REPOSITORY_URL = PLAN.document["repository_binding"]["canonical_https_url"]
ROLE_BINDINGS = {
    row["replica"]: {role: row[role] for role in ROLES}
    for row in PLAN.document["tables"]["role_bindings"]
}


def reread_sha256(role: str, row_count: int) -> str:
    """The plan's row-algorithm rolling digest over Ids 1..row_count."""
    digest = hashlib.sha256()
    for row_id in range(1, row_count + 1):
        seed = f"A2|{role}|{row_id:010d}|".encode("ascii")
        payload = (seed * ((240 + len(seed) - 1) // len(seed)))[:240]
        digest.update(row_id.to_bytes(4, "little", signed=True))
        digest.update(len(payload).to_bytes(2, "little"))
        digest.update(payload)
    return digest.hexdigest()


def _entry(path: str, role: str, payload: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "role": role,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "media_type": "application/octet-stream" if role == "page_blob" else "application/json",
    }


def _checked(document: dict[str, Any]) -> bytes:
    validate_document(document)
    return canonical_json_bytes(document)


def replica_documents(bundle: SyntheticBundle) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Return (artifact path -> bytes, page digest -> bytes) for one replica."""
    replica = bundle.replica
    matrix_job_id = f"a3-replica-{replica}"
    binding = PLAN.document["environment_binding"]
    provider = bundle.provider_sha256
    environment = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_environment",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": bundle.producer_commit,
        "repository_url": REPOSITORY_URL,
        "campaign_id": bundle.campaign_id,
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
            "clsid": "{" + "-".join((provider[:8], provider[8:12], provider[12:16],
                                     provider[16:20], provider[20:32])) + "}",
            "provider_version": "synthetic",
            "server_path": "C:/synthetic/dao360.dll",
            "server_file_version": "synthetic",
            "server_sha256": provider,
        },
    }
    environment_bytes = _checked(environment)
    environment_sha256 = hashlib.sha256(environment_bytes).hexdigest()
    artifacts: dict[str, bytes] = {f"environment/replica-{replica:02d}.json": environment_bytes}
    pages: dict[str, bytes] = {}
    checkpoints = []
    prior: tuple[str, ...] = ()
    changed_total = 0
    for row in bundle.schedule.checkpoints:
        hashes = bundle.ordered_page_sha256[row.checkpoint_id]
        changed = [
            page for page in range(max(len(prior), len(hashes)))
            if page >= len(prior) or page >= len(hashes) or prior[page] != hashes[page]
        ]
        database = hashlib.sha256()
        for digest in hashes:
            if digest not in pages:
                pages[digest] = bundle.page_bytes(digest)
            database.update(pages[digest])
        path = f"page-indexes/replica-{replica:02d}/{row.ordinal:02d}-{row.checkpoint_id}.json"
        index = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a3_page_index",
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256,
            "producer_commit": bundle.producer_commit,
            "campaign_id": bundle.campaign_id,
            "environment_sha256": environment_sha256,
            "provider_sha256": provider,
            "replica": replica,
            "checkpoint_id": row.checkpoint_id,
            "ordinal": row.ordinal,
            "predecessor_checkpoint_id": None if row.ordinal == 0 else CHECKPOINT_IDS[row.ordinal - 1],
            "page_count": len(hashes),
            "file_size_bytes": len(hashes) * PAGE_SIZE,
            "database_sha256": database.hexdigest(),
            "ordered_page_sha256": list(hashes),
            "changed_page_indices": changed,
        }
        index_bytes = _checked(index)
        artifacts[path] = index_bytes
        changed_total += len(changed)
        prior = hashes
        checkpoints.append({
            "checkpoint_id": row.checkpoint_id,
            "ordinal": row.ordinal,
            "actual_file_pages": row.actual_file_pages,
            "actual_size_bytes": row.actual_file_pages * PAGE_SIZE,
            "target_baseline_pages": row.target_baseline_pages,
            "target_threshold_pages": row.target_threshold_pages,
            "target_overshoot_pages": row.target_overshoot_pages,
            "inserted_rows_total": row.inserted_rows_total,
            "table_row_counts": dict(row.table_row_counts),
            "dao_reread": [
                {
                    "role": role,
                    "row_count": row.table_row_counts[role],
                    "rolling_sha256": reread_sha256(role, row.table_row_counts[role]),
                }
                for role in ROLES
                if not (row.checkpoint_id == "D_DROP" and role == "D")
            ],
            "quiescent": True,
            "post_close_companion": {
                "present_after_close": False,
                "observed_size_bytes": 0,
                "retained_for_physical_analysis": False,
            },
            "page_index": {
                "path": path,
                "sha256": hashlib.sha256(index_bytes).hexdigest(),
                "size_bytes": len(index_bytes),
            },
        })
    by_id = {row.checkpoint_id: row for row in bundle.schedule.checkpoints}
    first, recreated, regrown = by_id["D_GROW_0128"], by_id["D_RECREATE_EMPTY"], by_id["D_REGROW_0128"]
    observation = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_replica_observation",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": bundle.producer_commit,
        "repository_url": REPOSITORY_URL,
        "campaign_id": bundle.campaign_id,
        "matrix_job": {"job_id": matrix_job_id, "replica_only": True, "shared_mutable_state": False},
        "environment_sha256": environment_sha256,
        "provider_sha256": provider,
        "replica": replica,
        "role_binding": dict(ROLE_BINDINGS[replica]),
        "d_growth_observation": {
            "first_baseline_pages": first.target_baseline_pages,
            "first_target_pages": first.target_threshold_pages,
            "first_achieved_pages": first.actual_file_pages,
            "first_rows": first.table_row_counts["D"],
            "regrowth_baseline_pages": recreated.actual_file_pages,
            "regrowth_target_pages": regrown.target_threshold_pages,
            "regrowth_achieved_pages": regrown.actual_file_pages,
            "regrowth_rows": regrown.table_row_counts["D"],
        },
        "logical_checkpoint_read_bytes": sum(
            row.actual_file_pages * PAGE_SIZE for row in bundle.schedule.checkpoints
        ),
        "inserted_rows_total": max(row.inserted_rows_total for row in bundle.schedule.checkpoints),
        "changed_hash_entries": changed_total,
        "checkpoints": checkpoints,
    }
    artifacts[f"observations/replica-{replica:02d}.json"] = _checked(observation)
    files = [
        _entry(path, "environment" if path.startswith("environment/")
               else "replica_observation" if path.startswith("observations/")
               else "page_index", payload)
        for path, payload in artifacts.items()
    ]
    files.extend(
        _entry(f"page-store/{digest}.page", "page_blob", pages[digest]) for digest in sorted(pages)
    )
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a3_replica_artifact_manifest",
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": PLAN_SHA256,
        "producer_commit": bundle.producer_commit,
        "campaign_id": bundle.campaign_id,
        "matrix_job_id": matrix_job_id,
        "replica": replica,
        "environment_sha256": environment_sha256,
        "provider_sha256": provider,
        "checkpoint_count": len(CHECKPOINT_IDS),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "files": files,
    }
    artifacts[f"replica-artifacts/replica-{replica:02d}-manifest.json"] = _checked(manifest)
    return artifacts, pages


def write_replica_tree(root: Path, bundle: SyntheticBundle) -> None:
    artifacts, pages = replica_documents(bundle)
    for path, payload in artifacts.items():
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    store = root / "page-store"
    store.mkdir(parents=True, exist_ok=True)
    for digest, payload in pages.items():
        (store / f"{digest}.page").write_bytes(payload)


def write_replica_trees(root: Path) -> tuple[tuple[Path, ...], str, str]:
    """Write three calibration replicas; return (roots, campaign_id, producer_commit)."""
    bundles = generate_synthetic_bundles()
    roots = tuple(root / f"replica-{bundle.replica:02d}" for bundle in bundles)
    for replica_root, bundle in zip(roots, bundles, strict=True):
        write_replica_tree(replica_root, bundle)
    return roots, bundles[0].campaign_id, bundles[0].producer_commit
