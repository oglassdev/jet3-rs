from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NORMALIZER = load_module("normalize_criterion")
VALIDATOR = load_module("validate_benchmark_manifest")


class CriterionNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.criterion = self.root / "criterion"
        self.manifest = self.root / "manifest.json"
        self.resources = self.root / "resources.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "benchmarks": [
                        {
                            "id": "BENCH-UNIT-001",
                            "criterion_group": "unit_group",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self._write_case()
        self._write_resources()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_case(self, full_id: str = "unit_group/read/64") -> None:
        result = self.criterion / "unit_group" / "read" / "64" / "new"
        result.mkdir(parents=True, exist_ok=True)
        (result / "benchmark.json").write_text(
            json.dumps(
                {
                    "group_id": "unit_group",
                    "full_id": full_id,
                    "throughput": {"Bytes": 64},
                }
            ),
            encoding="utf-8",
        )
        (result / "estimates.json").write_text(
            json.dumps({"median": {"point_estimate": 32.0}}), encoding="utf-8"
        )
        (result / "sample.json").write_text(
            json.dumps({"iters": [1.0, 2.0], "times": [32.0, 64.0]}),
            encoding="utf-8",
        )

    def _write_resources(self, measurements: list[dict] | None = None) -> None:
        if measurements is None:
            measurements = [
                {
                    "criterion_id": "unit_group/read/64",
                    "peak_rss_bytes": 4096,
                    "output_size_bytes": 128,
                }
            ]
        self.resources.write_text(
            json.dumps({"schema_version": 1, "measurements": measurements}),
            encoding="utf-8",
        )

    def test_normalization_is_deterministic_and_unit_explicit(self) -> None:
        first = NORMALIZER.normalize(self.criterion, self.manifest, self.resources)
        second = NORMALIZER.normalize(self.criterion, self.manifest, self.resources)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["throughput_unit"], "bytes")
        self.assertEqual(
            first[0]["metrics"]["throughput_per_second"], 2_000_000_000.0
        )
        self.assertNotIn(
            "throughput_bytes_per_second", first[0]["metrics"]
        )

    def test_missing_resource_metric_is_blocked(self) -> None:
        self._write_resources([])
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "inventory must be non-empty"
        ):
            NORMALIZER.normalize(self.criterion, self.manifest, self.resources)

    def test_missing_peak_rss_field_is_blocked(self) -> None:
        self._write_resources(
            [{"criterion_id": "unit_group/read/64", "output_size_bytes": 128}]
        )
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "requires criterion_id"
        ):
            NORMALIZER.normalize(self.criterion, self.manifest, self.resources)

    def test_empty_criterion_inventory_is_blocked(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "inventory is empty"):
            NORMALIZER.normalize(empty, self.manifest, self.resources)

    def test_manifest_group_omission_is_blocked(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["benchmarks"].append(
            {"id": "BENCH-UNIT-002", "criterion_group": "missing_group"}
        )
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "omit manifest groups"
        ):
            NORMALIZER.normalize(self.criterion, self.manifest, self.resources)


def baseline(artifact_path: str, digest: str) -> dict:
    return {
        "id": "BENCH-VALIDATION-001",
        "traceability_ids": ["PERF-01"],
        "operation": "adversarial_parse",
        "scenario_id": "validator-test",
        "row_count": 0,
        "artifacts": [{"path": artifact_path, "sha256": digest}],
        "hardware": "test hardware",
        "os": "test os",
        "toolchain": "test toolchain",
        "sample_count": 2,
        "median_latency_ns": 10,
        "latency_percentiles_ns": {"p50": 10, "p90": 12, "p99": 15},
        "throughput_per_second": 100.0,
        "peak_rss_bytes": 4096,
        "output_size_bytes": 128,
    }


class BindingManifestValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        artifact = self.root / "evidence.json"
        artifact.write_text("{}\n", encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.document = {
            "schema_version": 1,
            "baselines": [baseline("evidence.json", digest)],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_manifest_and_artifact_pass(self) -> None:
        schema = json.loads(
            (
                Path(__file__).parents[2]
                / "docs/validation/schema/benchmark-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        VALIDATOR.validate_contract(schema)
        VALIDATOR.validate(self.document, self.root)

    def test_contract_drift_is_blocked(self) -> None:
        schema = json.loads(
            (
                Path(__file__).parents[2]
                / "docs/validation/schema/benchmark-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        schema["$defs"]["baseline"]["required"].remove("peak_rss_bytes")
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "fields differ"):
            VALIDATOR.validate_contract(schema)

    def test_empty_baseline_inventory_is_blocked(self) -> None:
        self.document["baselines"] = []
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "non-empty"):
            VALIDATOR.validate(self.document, self.root)

    def test_missing_metric_is_blocked(self) -> None:
        del self.document["baselines"][0]["peak_rss_bytes"]
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "binding baseline fields"):
            VALIDATOR.validate(self.document, self.root)

    def test_tampered_artifact_is_blocked(self) -> None:
        (self.root / "evidence.json").write_text('{"tampered":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "hash does not match"):
            VALIDATOR.validate(self.document, self.root)

    def test_symlink_artifact_is_blocked(self) -> None:
        target = self.root / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        artifact = self.root / "evidence.json"
        artifact.unlink()
        artifact.symlink_to(target)
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "not a regular repository file"):
            VALIDATOR.validate(self.document, self.root)

    def test_duplicate_baseline_id_is_blocked(self) -> None:
        self.document["baselines"].append(copy.deepcopy(self.document["baselines"][0]))
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "duplicate baseline id"):
            VALIDATOR.validate(self.document, self.root)


if __name__ == "__main__":
    unittest.main()
