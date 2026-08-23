"""Materialize worker-shaped A3 replica trees for fan-in tests.

Each tree is what one hosted matrix job uploads: the replica's environment,
observation, page indexes, page store, and artifact manifest — and nothing else.
The documents come from the checked dry-run bundle writer, so they carry the
same schema-validated shape the analyzer and validator already exercise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a3_dryrun_bundle import write_bundle  # noqa: E402
from a3_generator import SyntheticReplica, calibration_parameters, generate_replicas  # noqa: E402
from a3_spec import PLAN  # noqa: E402

CAMPAIGN_ID = "a3-synthetic-fan-in"
PRODUCER_COMMIT = "1" * 40


def write_replica_tree(root: Path, replica: SyntheticReplica) -> None:
    """Write one replica-only tree (no plan copy) under ``root``."""
    staging = Path(tempfile.mkdtemp(prefix=".a3-replica-", dir=root.parent))
    try:
        bundle = staging / "bundle"
        write_bundle(bundle, (replica,), CAMPAIGN_ID, PRODUCER_COMMIT)
        shutil.rmtree(bundle / Path(PLAN.document["artifacts"]["plan"]).parts[0])
        shutil.move(str(bundle), str(root))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def replica_documents(replica: SyntheticReplica) -> dict[str, bytes]:
    """Return artifact path -> bytes for one worker-shaped replica tree."""
    with tempfile.TemporaryDirectory(prefix="a3-replica-documents-") as directory:
        root = Path(directory) / "replica"
        write_replica_tree(root, replica)
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


def write_replica_trees(root: Path) -> tuple[tuple[Path, ...], str, str]:
    """Write three calibration replicas; return (roots, campaign_id, producer_commit)."""
    root.mkdir(parents=True, exist_ok=True)
    replicas = generate_replicas(calibration_parameters())
    roots = tuple(root / f"replica-{replica.replica:02d}" for replica in replicas)
    for replica_root, replica in zip(roots, replicas, strict=True):
        write_replica_tree(replica_root, replica)
    return roots, CAMPAIGN_ID, PRODUCER_COMMIT
