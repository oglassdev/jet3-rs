from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_baseline.py"
sys.path.insert(0, str(SCRIPT.parent))
import suite_identity

SPEC = importlib.util.spec_from_file_location("compare_baseline", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load comparison module")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def document() -> dict:
    return {
        "schema_version": 1,
        "suite_id": "BENCH-FORMAT-FOUNDATION-V1",
        "metadata": {
            "git_commit": "0" * 40,
            "dirty": False,
            "captured_at_utc": "2026-01-01T00:00:00Z",
            "os": "test-os",
            "architecture": "test-arch",
            "cpu": "test-cpu",
            "logical_cpus": 4,
            "memory_bytes": 1024,
            "rustc": "rustc test",
            "cargo": "cargo test",
            "benchmark_manifest_sha256": "1" * 64,
            "benchmark_lockfile_sha256": "2" * 64,
            "suite_digest_sha256": "3" * 64,
        },
        "raw_measurement_artifacts": [
            {
                "path": "artifacts/benchmarks/raw.json",
                "sha256": "4" * 64,
            }
        ],
        "measurements": [
            {
                "id": "BENCH-CASE-001",
                "metrics": {
                    "median_latency_ns": 100.0,
                    "throughput_bytes_per_second": 100.0,
                    "peak_rss_bytes": 100.0,
                    "output_size_bytes": 100.0,
                },
            }
        ],
    }


class CompareBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = mock.patch.object(
            MODULE,
            "_verify_commit_binding",
            side_effect=lambda document, _label, _root, _measurements: {
                "suite_digest_sha256": document["metadata"]["suite_digest_sha256"],
                "raw_measurement_artifacts": document[
                    "raw_measurement_artifacts"
                ],
            },
        )
        self.binding.start()

    def tearDown(self) -> None:
        self.binding.stop()

    def test_identical_measurements_pass(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["metadata"]["git_commit"] = "f" * 40
        report = MODULE.compare(baseline, candidate)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["regressions"], [])
        self.assertEqual(report["baseline_git_commit"], "0" * 40)
        self.assertEqual(report["candidate_git_commit"], "f" * 40)

    def test_exactly_fifteen_percent_is_not_a_regression(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        metrics = candidate["measurements"][0]["metrics"]
        metrics["median_latency_ns"] = 115.0
        metrics["throughput_bytes_per_second"] = 85.0
        metrics["peak_rss_bytes"] = 115.0
        metrics["output_size_bytes"] = 115.0
        self.assertEqual(MODULE.compare(baseline, candidate)["status"], "PASS")

    def test_latency_above_threshold_fails(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["measurements"][0]["metrics"]["median_latency_ns"] = 115.01
        report = MODULE.compare(baseline, candidate)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["regressions"][0]["metric"], "median_latency_ns")

    def test_throughput_below_threshold_fails(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["measurements"][0]["metrics"][
            "throughput_bytes_per_second"
        ] = 84.99
        report = MODULE.compare(baseline, candidate)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            report["regressions"][0]["metric"], "throughput_bytes_per_second"
        )

    def test_metric_set_mismatch_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        del candidate["measurements"][0]["metrics"]["peak_rss_bytes"]
        with self.assertRaisesRegex(MODULE.ComparisonError, "four required metrics"):
            MODULE.compare(baseline, candidate)

    def test_environment_mismatch_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["metadata"]["cpu"] = "different-cpu"
        with self.assertRaisesRegex(MODULE.ComparisonError, "metadata field differs"):
            MODULE.compare(baseline, candidate)

    def test_dirty_candidate_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["metadata"]["dirty"] = True
        with self.assertRaisesRegex(MODULE.ComparisonError, "candidate metadata"):
            MODULE.compare(baseline, candidate)

    def test_nonpositive_measurement_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["measurements"][0]["metrics"]["output_size_bytes"] = 0
        with self.assertRaisesRegex(MODULE.ComparisonError, "greater than zero"):
            MODULE.compare(baseline, candidate)

    def test_threshold_cannot_weaken_contract(self) -> None:
        baseline = document()
        with self.assertRaisesRegex(MODULE.ComparisonError, r"\[0, 0.15\]"):
            MODULE.compare(baseline, copy.deepcopy(baseline), threshold=0.16)

    def test_missing_commit_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        del candidate["metadata"]["git_commit"]
        with self.assertRaisesRegex(MODULE.ComparisonError, "git_commit"):
            MODULE.compare(baseline, candidate)

    def test_malformed_timestamp_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["metadata"]["captured_at_utc"] = "not-a-timestamp"
        with self.assertRaisesRegex(MODULE.ComparisonError, "ISO 8601"):
            MODULE.compare(baseline, candidate)

    def test_missing_timestamp_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        del candidate["metadata"]["captured_at_utc"]
        with self.assertRaisesRegex(MODULE.ComparisonError, "captured_at_utc"):
            MODULE.compare(baseline, candidate)

    def test_boolean_memory_value_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["metadata"]["memory_bytes"] = True
        with self.assertRaisesRegex(MODULE.ComparisonError, "positive integer"):
            MODULE.compare(baseline, candidate)

    def test_huge_integer_float_conversion_is_blocked(self) -> None:
        baseline = document()
        candidate = copy.deepcopy(baseline)
        candidate["measurements"][0]["metrics"]["median_latency_ns"] = 10**10_000
        with self.assertRaisesRegex(MODULE.ComparisonError, "finite float"):
            MODULE.compare(baseline, candidate)

    def test_cli_uses_exit_two_for_corruption_and_one_for_regression(self) -> None:
        baseline = document()
        regression = copy.deepcopy(baseline)
        regression["measurements"][0]["metrics"]["median_latency_ns"] = 116.0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_path = root / "baseline.json"
            regression_path = root / "regression.json"
            corruption_path = root / "corruption.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            regression_path.write_text(json.dumps(regression), encoding="utf-8")
            corruption_path.write_text(
                json.dumps(baseline).replace("100.0", "1e10000", 1),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    MODULE.main([str(baseline_path), str(regression_path)]), 1
                )
                self.assertEqual(
                    MODULE.main([str(baseline_path), str(corruption_path)]), 2
                )


class CommitBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.repository_root = Path(cls.temporary.name)
        cls._git("init", "-q")
        cls._git("config", "user.name", "Benchmark Test")
        cls._git("config", "user.email", "benchmark@example.invalid")

        for path in suite_identity.SUITE_PATHS:
            destination = cls.repository_root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(f"retained suite source: {path}\n", encoding="utf-8")
        cls._write_raw("artifacts/benchmarks/baseline-raw.json", document()["measurements"])
        cls._git("add", ".")
        cls._git("commit", "-qm", "baseline suite")
        cls.baseline_commit = cls._git("rev-parse", "HEAD").strip()

        cls._write_raw("artifacts/benchmarks/candidate-raw.json", document()["measurements"])
        cls._git("add", ".")
        cls._git("commit", "-qm", "candidate measurements")
        cls.candidate_commit = cls._git("rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @classmethod
    def _git(cls, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(cls.repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    @classmethod
    def _write_raw(cls, path: str, measurements: list[dict]) -> None:
        destination = cls.repository_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"measurements": measurements}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def _bound_document(cls, commit: str, raw_path: str) -> dict:
        bound = document()
        bound["metadata"]["git_commit"] = commit
        bound["metadata"]["suite_digest_sha256"] = suite_identity.digest_for_commit(
            cls.repository_root, commit
        )
        manifest = suite_identity.retained_blob(
            cls.repository_root, commit, "benches/manifest.json"
        )
        lockfile = suite_identity.retained_blob(
            cls.repository_root, commit, "benches/Cargo.lock"
        )
        raw = suite_identity.retained_blob(cls.repository_root, commit, raw_path)
        bound["metadata"]["benchmark_manifest_sha256"] = hashlib.sha256(
            manifest
        ).hexdigest()
        bound["metadata"]["benchmark_lockfile_sha256"] = hashlib.sha256(
            lockfile
        ).hexdigest()
        bound["raw_measurement_artifacts"] = [
            {"path": raw_path, "sha256": hashlib.sha256(raw).hexdigest()}
        ]
        return bound

    def test_retained_commits_and_raw_artifacts_can_pass(self) -> None:
        baseline = self._bound_document(
            self.baseline_commit, "artifacts/benchmarks/baseline-raw.json"
        )
        candidate = self._bound_document(
            self.candidate_commit, "artifacts/benchmarks/candidate-raw.json"
        )
        report = MODULE.compare(
            baseline, candidate, repository_root=self.repository_root
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["baseline_git_commit"], self.baseline_commit)
        self.assertEqual(report["candidate_git_commit"], self.candidate_commit)
        self.assertEqual(
            report["suite_digest_sha256"],
            suite_identity.digest_for_commit(
                self.repository_root, self.baseline_commit
            ),
        )
        self.assertEqual(
            report["baseline_raw_measurement_artifacts"],
            baseline["raw_measurement_artifacts"],
        )

    def test_invented_commit_is_blocked(self) -> None:
        baseline = self._bound_document(
            self.baseline_commit, "artifacts/benchmarks/baseline-raw.json"
        )
        candidate = self._bound_document(
            self.candidate_commit, "artifacts/benchmarks/candidate-raw.json"
        )
        candidate["metadata"]["git_commit"] = "0" * 40
        with self.assertRaisesRegex(MODULE.ComparisonError, "commit binding failed"):
            MODULE.compare(
                baseline, candidate, repository_root=self.repository_root
            )

    def test_invented_artifact_hash_is_blocked(self) -> None:
        baseline = self._bound_document(
            self.baseline_commit, "artifacts/benchmarks/baseline-raw.json"
        )
        candidate = self._bound_document(
            self.candidate_commit, "artifacts/benchmarks/candidate-raw.json"
        )
        candidate["raw_measurement_artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ComparisonError, "hash does not match"):
            MODULE.compare(
                baseline, candidate, repository_root=self.repository_root
            )

    def test_measurements_not_present_in_retained_raw_are_blocked(self) -> None:
        baseline = self._bound_document(
            self.baseline_commit, "artifacts/benchmarks/baseline-raw.json"
        )
        candidate = self._bound_document(
            self.candidate_commit, "artifacts/benchmarks/candidate-raw.json"
        )
        candidate["measurements"][0]["metrics"]["median_latency_ns"] = 101.0
        with self.assertRaisesRegex(MODULE.ComparisonError, "differ from retained raw"):
            MODULE.compare(
                baseline, candidate, repository_root=self.repository_root
            )

    def test_changed_retained_suite_is_blocked(self) -> None:
        baseline = self._bound_document(
            self.baseline_commit, "artifacts/benchmarks/baseline-raw.json"
        )
        manifest = self.repository_root / "benches/manifest.json"
        manifest.write_text("changed retained suite\n", encoding="utf-8")
        self._git("add", "benches/manifest.json")
        self._git("commit", "-qm", "change suite")
        changed_commit = self._git("rev-parse", "HEAD").strip()
        candidate = self._bound_document(
            changed_commit, "artifacts/benchmarks/candidate-raw.json"
        )
        with self.assertRaisesRegex(MODULE.ComparisonError, "retained benchmark suites differ"):
            MODULE.compare(
                baseline, candidate, repository_root=self.repository_root
            )


if __name__ == "__main__":
    unittest.main()
