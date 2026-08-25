"""Focused tests for the independent A4 contract and bundle trust boundary."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a4_independent_bundle import (  # noqa: E402
    BundleLoader,
    PageStore,
    ValidationError,
    canonical_document_bytes,
)
from a4_independent_contract import (  # noqa: E402
    CONTRACT,
    EXPECTED_TAMPERS,
    PLAN_PATH,
    PLAN_SHA256,
    ContractError,
    load_contract,
    validate_canonical_snapshot,
)
from test_a4_analyzer import _COMMIT, _inputs  # noqa: E402
from a4_analysis import analyze  # noqa: E402


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _entry(path: str, role: str, payload: bytes, media: str = "application/json") -> dict[str, object]:
    return {
        "path": path,
        "role": role,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "media_type": media,
    }


def _build_bundle(root: Path) -> None:
    inputs = _inputs()
    result = analyze("a4-synthetic", _COMMIT, inputs)
    surfaces = {1: inputs[1], 2: inputs[2], 3: inputs.acquire_holdout(result.frozen.canonical_bytes, result.frozen.sha256)}
    outer: dict[str, dict[str, object]] = {}
    plan_path = "plan/a4-row-anchored-maps.plan.json"
    plan_raw = PLAN_PATH.read_bytes()
    _write(root / plan_path, plan_raw)
    outer[plan_path] = _entry(plan_path, "plan", plan_raw)
    artifact_hashes: list[str] = []
    environment_hashes: list[str] = []
    for number, surface in surfaces.items():
        environment_path = f"environment/replica-{number:02d}.json"
        observation_path = f"observations/replica-{number:02d}.json"
        artifact_path = f"replica-artifacts/replica-{number:02d}-manifest.json"
        documents = {
            environment_path: (surface.environment_payload, "environment"),
            observation_path: (canonical_document_bytes(surface.replica_observation), "replica_observation"),
            artifact_path: (canonical_document_bytes(surface.artifact_manifest), "replica_artifact_manifest"),
        }
        for checkpoint, index in surface.page_indexes.items():
            ordinal = CONTRACT.checkpoint_ids.index(checkpoint)
            path = f"page-indexes/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
            documents[path] = (canonical_document_bytes(index), "page_index")
        for checkpoint, snapshot in surface.schema_snapshots.items():
            ordinal = CONTRACT.checkpoint_ids.index(checkpoint)
            path = f"schema-snapshots/replica-{number:02d}/{ordinal:02d}-{checkpoint}.json"
            documents[path] = (canonical_document_bytes(snapshot), "dao_schema_snapshot")
        for path, (payload, role) in documents.items():
            assert isinstance(payload, bytes)
            _write(root / path, payload)
            outer[path] = _entry(path, role, payload)
        environment_hashes.append(outer[environment_path]["sha256"])
        artifact_hashes.append(outer[artifact_path]["sha256"])
        for checkpoint in CONTRACT.checkpoint_ids:
            for digest in surface.source.ordered_page_sha256[checkpoint]:
                path = f"page-store/{digest}.page"
                if path not in outer:
                    payload = surface.source.page_bytes(digest)
                    _write(root / path, payload)
                    outer[path] = _entry(path, "page_blob", payload, "application/octet-stream")
    frozen_path = "analysis/derivation-candidates.json"
    report_path = "analysis/analysis-report.json"
    evidence_path = "analysis/h4-occurrence-evidence.json"
    receipt_path = "analysis/holdout-structure-receipt.json"
    report_raw = canonical_document_bytes(dict(result.report))
    _write(root / frozen_path, result.frozen.canonical_bytes)
    _write(root / report_path, report_raw)
    outer[frozen_path] = _entry(frozen_path, "frozen_candidate_set", result.frozen.canonical_bytes)
    outer[report_path] = _entry(report_path, "analysis_report", report_raw)
    if result.frozen.occurrence_evidence_bytes is not None:
        _write(root / evidence_path, result.frozen.occurrence_evidence_bytes)
        outer[evidence_path] = _entry(
            evidence_path, "h4_occurrence_evidence", result.frozen.occurrence_evidence_bytes
        )
    receipt = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_holdout_structure_receipt",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": PLAN_SHA256,
        "producer_commit": _COMMIT,
        "campaign_id": "a4-synthetic",
        "derivation_candidate_set_sha256": result.frozen.sha256,
        "replica": 3,
        "replica_artifact_manifest_sha256": artifact_hashes[2],
        "validated_after_candidate_freeze": True,
        "page_bytes_exposed_to_analyzer": False,
        "result": "pass",
    }
    receipt_raw = canonical_document_bytes(receipt)
    _write(root / receipt_path, receipt_raw)
    outer[receipt_path] = _entry(receipt_path, "holdout_structure_receipt", receipt_raw)
    files = [outer[path] for path in sorted(outer)]
    scientific = {
        "one_or_more_layers_predict_holdout": "one_or_more_submodels_predict_holdout",
        "no_layer_predicts_holdout": "no_submodel_predicts_holdout",
    }[result.report["scientific_outcome"]]
    manifest = {
        "protocol_version": "1.0.0",
        "document_type": "dao_a4_bundle_manifest",
        "experiment_id": "DAO-A4-ROW-ANCHORED-MAPS-001",
        "campaign_id": "a4-synthetic",
        "producer_commit": _COMMIT,
        "repository_url": "https://github.com/oglassdev/jet3-rs.git",
        "created_utc": "2026-08-25T00:10:00Z",
        "campaign_started_utc": "2026-08-25T00:00:00Z",
        "campaign_elapsed_seconds": 600,
        "plan_sha256": PLAN_SHA256,
        "revision_plan_sha256": PLAN_SHA256,
        "replica_environment_sha256": environment_hashes,
        "provider_sha256": "2" * 64,
        "replica_count": 3,
        "replica_artifact_manifest_sha256": artifact_hashes,
        "checkpoint_count": 75,
        "page_blob_count": sum(row["role"] == "page_blob" for row in files),
        "bundle_size_bytes_excluding_manifest": sum(row["size_bytes"] for row in files),
        "inventory_closed": True,
        "hashes_verified": True,
        "paths_closed": True,
        "execution_status": "analysis_complete",
        "campaign_failed": False,
        "holdout_structure_receipt_sha256": outer[receipt_path]["sha256"],
        "analysis_report_retained": True,
        "analysis_scientific_outcome": scientific,
        "bundle_status": (
            "decisive_pending_independent_validation"
            if scientific == "one_or_more_submodels_predict_holdout"
            else "complete_no_scientific_outcome"
        ),
        "independent_validation_status": "not_independently_validated",
        "files": files,
    }
    _write(root / "bundle-manifest.json", canonical_document_bytes(manifest))


class IndependentContractTests(unittest.TestCase):
    def test_contract_pins_plan_schemas_predicates_and_nine_tampers(self) -> None:
        self.assertEqual(hashlib.sha256(CONTRACT.plan_raw).hexdigest(), PLAN_SHA256)
        self.assertEqual(len(CONTRACT.schemas), 14)
        self.assertEqual(len(CONTRACT.checkpoint_ids), 25)
        self.assertEqual(len(CONTRACT.predicate_ids), 40)
        flattened = (
            CONTRACT.campaign_predicates
            + tuple(item for rows in CONTRACT.layer_predicates.values() for item in rows)
            + CONTRACT.holdout_predicates
        )
        self.assertEqual(flattened, CONTRACT.predicate_ids)
        self.assertEqual(
            tuple((row["id"], row["required_rejection"]) for row in CONTRACT.tamper_cases),
            EXPECTED_TAMPERS,
        )

    def test_contract_rejects_replacement_plan_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            replacement = Path(directory) / PLAN_PATH.name
            replacement.write_bytes(PLAN_PATH.read_bytes() + b" ")
            with self.assertRaisesRegex(ContractError, "plan_binding_mismatch"):
                load_contract(replacement)

    def test_independent_modules_have_closed_imports(self) -> None:
        forbidden = (
            "a4_analysis",
            "a4_model",
            "a4_layer",
            "a4_generator",
            "a4_spec",
        )
        for path in SCRIPTS.glob("a4_independent_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = [
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
            ]
            self.assertFalse(
                [name for name in imports if name.startswith(forbidden)],
                path.name,
            )


class IndependentBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="a4-independent-bundle-")
        cls.bundle = Path(cls.temporary.name) / "bundle"
        _build_bundle(cls.bundle)
        cls.loaded = BundleLoader(cls.bundle).load()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _copy(self, directory: str) -> Path:
        target = Path(directory) / "bundle"
        shutil.copytree(self.bundle, target, copy_function=os.link)
        manifest = target / "bundle-manifest.json"
        payload = manifest.read_bytes()
        manifest.unlink()
        manifest.write_bytes(payload)
        return target

    @staticmethod
    def _replace(root: Path, relative: str, payload: bytes) -> None:
        path = root / relative
        path.unlink()
        path.write_bytes(payload)

    def _relink_replica_json(
        self, root: Path, manifest: dict[str, object], number: int, relative: str, value: dict[str, object]
    ) -> None:
        payload = canonical_document_bytes(value)
        self._replace(root, relative, payload)
        entry = next(row for row in manifest["files"] if row["path"] == relative)
        entry.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        observation_path = f"observations/replica-{number:02d}.json"
        if relative.startswith("schema-snapshots/") or relative.startswith("page-indexes/"):
            observation = json.loads((root / observation_path).read_text(encoding="utf-8"))
            key = "dao_schema_snapshot" if relative.startswith("schema-snapshots/") else "page_index"
            reference = next(row for row in observation["checkpoints"] if row[key]["path"] == relative)[key]
            reference.update(sha256=entry["sha256"], size_bytes=entry["size_bytes"])
            self._relink_replica_json(root, manifest, number, observation_path, observation)
            return
        artifact_path = f"replica-artifacts/replica-{number:02d}-manifest.json"
        if relative == artifact_path:
            manifest["replica_artifact_manifest_sha256"][number - 1] = entry["sha256"]
            return
        artifact = json.loads((root / artifact_path).read_text(encoding="utf-8"))
        artifact_entry = next(row for row in artifact["files"] if row["path"] == relative)
        artifact_entry.clear()
        artifact_entry.update(entry)
        self._relink_replica_json(root, manifest, number, artifact_path, artifact)

    @staticmethod
    def _finish_manifest(root: Path, manifest: dict[str, object]) -> None:
        manifest["bundle_size_bytes_excluding_manifest"] = sum(
            row["size_bytes"] for row in manifest["files"]
        )
        (root / "bundle-manifest.json").write_bytes(canonical_document_bytes(manifest))

    def test_loads_complete_bundle_and_reads_each_blob_once(self) -> None:
        bundle = self.loaded
        page_count = bundle.manifest["page_blob_count"]
        self.assertEqual(bundle.page_store.physical_read_count, page_count)
        self.assertEqual(set(bundle.replicas), {1, 2, 3})
        self.assertEqual(sum(len(replica.indexes) for replica in bundle.replicas.values()), 75)
        self.assertEqual(sum(len(replica.snapshots) for replica in bundle.replicas.values()), 75)
        first = bundle.replicas[1].page("EMPTY", 0)
        self.assertIs(first, bundle.replicas[1].page("EMPTY", 0))
        self.assertEqual(bundle.page_store.physical_read_count, page_count)

    def test_rejects_duplicate_snapshot_name_and_matrix_job(self) -> None:
        snapshot = copy.deepcopy(self.loaded.replicas[1].snapshots["T1_ADD_TEXT"])
        fields = next(table for table in snapshot["tables"] if table["logical_role"] == "T1")["fields"]
        for key in ("name", "name_utf16_code_units", "name_windows_1252_hex", "name_utf8_hex"):
            fields[1][key] = fields[0][key]
        with self.assertRaisesRegex(ContractError, "schema_snapshot_mismatch"):
            validate_canonical_snapshot(snapshot, "tampered")
        replicas = dict(self.loaded.replicas)
        environment = copy.deepcopy(replicas[3].environment)
        environment["matrix_job_id"] = replicas[2].environment["matrix_job_id"]
        replicas[3] = replace(replicas[3], environment=environment)
        loader = BundleLoader(self.bundle)
        with self.assertRaisesRegex(ValidationError, "cross_replica_environment_mismatch"):
            loader._environment_closure(self.loaded.manifest, replicas, self.loaded.entries)

    def test_aggregate_preflight_happens_before_page_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy(directory)
            manifest_path = root / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["page_blob_count"] += 1
            manifest_path.write_bytes(canonical_document_bytes(manifest))
            with mock.patch.object(PageStore, "read", side_effect=AssertionError("page opened")):
                with self.assertRaisesRegex(ValidationError, "aggregate page store"):
                    BundleLoader(root).load()

    def test_coherently_relinked_t1_t3_t5_reach_registered_failures(self) -> None:
        def t1(root: Path, manifest: dict[str, object]) -> None:
            relative = "plan/a4-row-anchored-maps.plan.json"
            plan = json.loads((root / relative).read_text(encoding="utf-8"))
            plan["question"] += " "
            payload = canonical_document_bytes(plan)
            self._replace(root, relative, payload)
            entry = next(row for row in manifest["files"] if row["path"] == relative)
            entry.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))

        def t3(root: Path, manifest: dict[str, object]) -> None:
            relative = "schema-snapshots/replica-01/03-T1_ADD_TEXT.json"
            snapshot = json.loads((root / relative).read_text(encoding="utf-8"))
            table = next(row for row in snapshot["tables"] if row["logical_role"] == "T1")
            table["fields"][1]["attributes"] = 0
            self._relink_replica_json(root, manifest, 1, relative, snapshot)

        def t5(_root: Path, manifest: dict[str, object]) -> None:
            manifest["campaign_elapsed_seconds"] = 2701
            manifest["created_utc"] = "2026-08-25T00:45:01Z"

        for name, mutate, expected in (
            ("T1", t1, "plan_binding_mismatch"),
            ("T3", t3, "schema_snapshot_mismatch"),
            ("T5", t5, "campaign_timeout_exceeded"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = self._copy(directory)
                manifest = json.loads(
                    (root / "bundle-manifest.json").read_text(encoding="utf-8")
                )
                mutate(root, manifest)
                self._finish_manifest(root, manifest)
                with self.assertRaisesRegex(ValidationError, expected):
                    BundleLoader(root).load()

    def test_rejects_uninventoried_file_and_symlink(self) -> None:
        for name, mutate, expected in (
            ("extra", lambda root: _write(root / "extra.txt", b"x"), "manifest_inventory_not_closed"),
            (
                "symlink",
                lambda root: (root / "linked").symlink_to(root / "analysis", target_is_directory=True),
                "bundle_symlink",
            ),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = self._copy(directory)
                mutate(root)
                with self.assertRaisesRegex(ValidationError, expected):
                    BundleLoader(root).load()

    def test_rejects_noncanonical_frozen_even_when_inventory_is_relinked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy(directory)
            frozen_path = root / "analysis/derivation-candidates.json"
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
            frozen_raw = json.dumps(frozen, ensure_ascii=False, indent=2).encode() + b"\n"
            frozen_path.unlink()
            frozen_path.write_bytes(frozen_raw)
            manifest_path = root / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(row for row in manifest["files"] if row["path"] == "analysis/derivation-candidates.json")
            manifest["bundle_size_bytes_excluding_manifest"] += len(frozen_raw) - entry["size_bytes"]
            entry["size_bytes"] = len(frozen_raw)
            entry["sha256"] = hashlib.sha256(frozen_raw).hexdigest()
            manifest_path.write_bytes(canonical_document_bytes(manifest))
            with self.assertRaisesRegex(ValidationError, "frozen_set_not_canonical"):
                BundleLoader(root).load()


if __name__ == "__main__":
    unittest.main()
