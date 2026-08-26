"""Contracts for deterministic DAO test routing in CI."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = ROOT / "oracle" / "windows-dao" / "scripts" / "run_contract_tests.py"
SPEC = importlib.util.spec_from_file_location("run_contract_tests", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ContractTestRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = RUNNER.discover_cases()
        cls.modules = RUNNER.discovered_modules(cls.cases)

    def test_four_portable_shards_partition_every_discovered_test(self) -> None:
        routed_ids: list[str] = []
        for shard_index in range(RUNNER.FOUR_SHARD_COUNT):
            selected = RUNNER.selected_cases(
                self.cases,
                lane="portable-shard",
                shard_index=shard_index,
                shard_count=RUNNER.FOUR_SHARD_COUNT,
            )
            self.assertTrue(selected)
            routed_ids.extend(case.id() for case in selected)
        self.assertEqual(len(routed_ids), len(self.cases))
        self.assertEqual(len(set(routed_ids)), len(self.cases))

    def test_curated_inventories_name_discovered_modules(self) -> None:
        self.assertLessEqual(set(RUNNER.FOUR_SHARD_OVERRIDES), self.modules)
        self.assertLessEqual(set(RUNNER.WINDOWS_PR_MODULES), self.modules)

    def test_windows_inventory_covers_every_platform_sensitive_module(self) -> None:
        markers = ("POWERSHELL", "PowerShell", "os.name", "sys.platform")
        marked_modules = {
            path.stem
            for path in RUNNER.TEST_ROOT.glob("test_*.py")
            if path != Path(__file__)
            if any(marker in path.read_text(encoding="utf-8") for marker in markers)
        }
        self.assertLessEqual(marked_modules, RUNNER.WINDOWS_PR_MODULES)

    def test_windows_lane_includes_platform_contracts_not_heavy_portable_work(self) -> None:
        selected = RUNNER.selected_cases(
            self.cases,
            lane="windows-pr",
            shard_index=None,
            shard_count=None,
        )
        modules = RUNNER.discovered_modules(selected)
        self.assertEqual(modules, RUNNER.WINDOWS_PR_MODULES)
        self.assertIn("test_bounded_process_contract", modules)
        self.assertNotIn("test_a4_independent_campaign", modules)

    def test_nonstandard_shard_counts_use_stable_hash_routing(self) -> None:
        module = "test_future_contract"
        self.assertEqual(
            RUNNER.shard_for_module(module, 3),
            RUNNER.shard_for_module(module, 3),
        )
        with self.assertRaises(ValueError):
            RUNNER.shard_for_module(module, 0)


if __name__ == "__main__":
    unittest.main()
