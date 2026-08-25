"""Materialize worker-shaped A4 replica trees for hosted-lane tests."""

from __future__ import annotations

import shutil
import sys
import hashlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a4_spec import CHECKPOINT_IDS  # noqa: E402
from protocol_validation import canonical_json_bytes  # noqa: E402
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402

CAMPAIGN_ID = "a4-synthetic"
PRODUCER_COMMIT = _COMMIT


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_replica_tree(root: Path, replica: int, surface: object) -> None:
    """Write one exact replica-only artifact tree."""
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    environment_path = f"environment/replica-{replica:02d}.json"
    observation_path = f"observations/replica-{replica:02d}.json"
    manifest_path = f"replica-artifacts/replica-{replica:02d}-manifest.json"
    _write(root / environment_path, surface.environment_payload)
    _write(root / observation_path, canonical_json_bytes(surface.replica_observation))
    _write(root / manifest_path, canonical_json_bytes(surface.artifact_manifest))
    for ordinal, checkpoint in enumerate(CHECKPOINT_IDS):
        _write(
            root / f"page-indexes/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
            canonical_json_bytes(surface.page_indexes[checkpoint]),
        )
        _write(
            root / f"schema-snapshots/replica-{replica:02d}/{ordinal:02d}-{checkpoint}.json",
            canonical_json_bytes(surface.schema_snapshots[checkpoint]),
        )
        for digest in surface.source.ordered_page_sha256[checkpoint]:
            page = root / f"page-store/{digest}.page"
            if not page.exists():
                _write(page, surface.source.page_bytes(digest))


def write_replica_trees(root: Path) -> tuple[tuple[Path, ...], str, str]:
    """Write three deterministic replicas and return campaign bindings."""
    root.mkdir(parents=True, exist_ok=True)
    inputs = _inputs()
    surfaces = {1: inputs[1], 2: inputs[2]}
    frozen = b"{}"
    surfaces[3] = inputs.acquire_holdout(
        frozen, hashlib.sha256(frozen).hexdigest()
    )
    roots = tuple(root / f"replica-{replica:02d}" for replica in (1, 2, 3))
    for replica, replica_root in enumerate(roots, start=1):
        write_replica_tree(replica_root, replica, surfaces[replica])
    return roots, CAMPAIGN_ID, PRODUCER_COMMIT


def materialize_holdout(source: Path, destination: Path) -> None:
    """Copy replica 3 only when invoked by the post-freeze callback."""
    shutil.copytree(source, destination)
