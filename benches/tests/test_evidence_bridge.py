from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def symlink_or_skip(
    case: unittest.TestCase, link: Path, target: Path
) -> None:
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            case.skipTest("Windows symlink privilege is unavailable")
        raise


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

    @unittest.skipIf(os.name == "nt", "directory fsync is unavailable on Windows")
    def test_raw_mode_publishes_one_complete_bundle(self) -> None:
        bundle_output = self.root / "bundle"
        result = NORMALIZER.main(
            [
                "--criterion-root",
                str(self.criterion),
                "--resources",
                str(self.resources),
                "--manifest",
                str(self.manifest),
                "--bundle-output",
                str(bundle_output),
            ]
        )
        self.assertEqual(result, 0)
        self.assertEqual(
            {path.name for path in bundle_output.iterdir()},
            {NORMALIZER.RAW_MEASUREMENTS_FILE},
        )
        document = json.loads(
            (bundle_output / NORMALIZER.RAW_MEASUREMENTS_FILE).read_text(
                encoding="utf-8"
            )
        )
        NORMALIZER._validate_raw_document(document)

    def test_invalid_bound_document_does_not_publish_bundle(self) -> None:
        bundle_output = self.root / "bundle"
        with mock.patch.object(NORMALIZER, "_bound_document", return_value={}):
            with contextlib.redirect_stderr(io.StringIO()):
                result = NORMALIZER.main(
                    [
                        "--criterion-root",
                        str(self.criterion),
                        "--resources",
                        str(self.resources),
                        "--manifest",
                        str(self.manifest),
                        "--bundle-output",
                        str(bundle_output),
                        "--metadata",
                        str(self.root / "metadata.json"),
                        "--raw-artifact-path",
                        "artifacts/benchmarks/raw.json",
                    ]
                )
        self.assertEqual(result, 2)
        self.assertFalse(bundle_output.exists())

    @unittest.skipIf(os.name == "nt", "directory fsync is unavailable on Windows")
    def test_bound_bundle_uses_one_publication_rename(self) -> None:
        output = self.root / "bundle"
        documents = {
            NORMALIZER.RAW_MEASUREMENTS_FILE: {"measurements": ["raw"]},
            NORMALIZER.COMPARISON_INPUT_FILE: {"measurements": ["bound"]},
        }
        replace = NORMALIZER.os.replace
        with mock.patch.object(NORMALIZER.os, "replace", wraps=replace) as publication:
            NORMALIZER._publish_bundle(output, documents)
        self.assertEqual(publication.call_count, 1)
        self.assertEqual(
            json.loads(
                (output / NORMALIZER.RAW_MEASUREMENTS_FILE).read_text(
                    encoding="utf-8"
                )
            ),
            documents[NORMALIZER.RAW_MEASUREMENTS_FILE],
        )
        self.assertEqual(
            json.loads(
                (output / NORMALIZER.COMPARISON_INPUT_FILE).read_text(
                    encoding="utf-8"
                )
            ),
            documents[NORMALIZER.COMPARISON_INPUT_FILE],
        )

    def test_bundle_stage_failure_has_no_publication_point(self) -> None:
        output = self.root / "bundle"
        documents = {
            NORMALIZER.RAW_MEASUREMENTS_FILE: {"measurements": ["raw"]},
            NORMALIZER.COMPARISON_INPUT_FILE: {"measurements": ["bound"]},
        }
        write_document = NORMALIZER._write_bundle_document
        calls = 0

        def fail_second_write(path: Path, value: dict) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise NORMALIZER.NormalizationError("injected staging failure")
            write_document(path, value)

        with mock.patch.object(
            NORMALIZER, "_write_bundle_document", side_effect=fail_second_write
        ):
            with self.assertRaisesRegex(
                NORMALIZER.NormalizationError, "injected staging failure"
            ):
                NORMALIZER._publish_bundle(output, documents)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".bundle.tmp-*")), [])

    def test_bundle_refuses_to_replace_existing_evidence(self) -> None:
        output = self.root / "bundle"
        output.mkdir()
        marker = output / "marker"
        marker.write_text("retained\n", encoding="utf-8")
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "refusing to replace"
        ):
            NORMALIZER._publish_bundle(
                output,
                {NORMALIZER.RAW_MEASUREMENTS_FILE: {"measurements": ["raw"]}},
            )
        self.assertEqual(marker.read_text(encoding="utf-8"), "retained\n")

    def test_bundle_refuses_broken_symlink_destination(self) -> None:
        output = self.root / "bundle"
        symlink_or_skip(self, output, self.root / "missing-target")
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "refusing to replace"
        ):
            NORMALIZER._publish_bundle(
                output,
                {NORMALIZER.RAW_MEASUREMENTS_FILE: {"measurements": ["raw"]}},
            )
        self.assertTrue(output.is_symlink())
        self.assertFalse((self.root / "missing-target").exists())

    def test_raw_document_validation_rejects_noncanonical_measurements(self) -> None:
        measurements = NORMALIZER.normalize(
            self.criterion, self.manifest, self.resources
        )
        duplicate = [measurements[0], copy.deepcopy(measurements[0])]
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "duplicate measurement ids"
        ):
            NORMALIZER._raw_document(duplicate)


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
        symlink_or_skip(self, artifact, target)
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "not a regular repository file"):
            VALIDATOR.validate(self.document, self.root)

    def test_duplicate_baseline_id_is_blocked(self) -> None:
        self.document["baselines"].append(copy.deepcopy(self.document["baselines"][0]))
        with self.assertRaisesRegex(VALIDATOR.ManifestError, "duplicate baseline id"):
            VALIDATOR.validate(self.document, self.root)


if __name__ == "__main__":
    unittest.main()
