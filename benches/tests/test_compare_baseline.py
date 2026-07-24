from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_baseline.py"
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
        },
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


if __name__ == "__main__":
    unittest.main()
