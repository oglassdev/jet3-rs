from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "tools/generate_scenario_inventory.py"
SPEC = importlib.util.spec_from_file_location("generate_scenario_inventory", GENERATOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load scenario inventory generator")
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class ScenarioInventoryGenerationTests(unittest.TestCase):
    def test_checked_rust_table_recomputes_from_every_inventory_entry(self) -> None:
        inventory = json.loads(GENERATOR.INVENTORY.read_text(encoding="utf-8"))
        branch_registry = json.loads(
            GENERATOR.BRANCH_REGISTRY.read_text(encoding="utf-8")
        )
        _, entries = GENERATOR.load_entries()
        _, branch_ids = GENERATOR.load_branch_ids()

        self.assertEqual(
            [identifier for identifier, *_ in entries],
            [scenario["id"] for scenario in inventory["scenarios"]],
        )
        self.assertEqual(
            list(branch_ids),
            [branch["id"] for branch in branch_registry["branches"]],
        )
        self.assertEqual(
            GENERATOR.OUTPUT.read_text(encoding="utf-8"),
            GENERATOR.render(),
        )

    def test_overlapping_scenario_branches_are_rejected(self) -> None:
        inventory = json.loads(GENERATOR.INVENTORY.read_text(encoding="utf-8"))
        scenario = inventory["scenarios"][0]
        branch = scenario["required_branches"][0]
        scenario["boundary"] = {
            "dimension": "page_count",
            "position": "at",
            "forbidden_branches": [branch],
        }
        path = self._temporary_inventory(inventory)
        try:
            with self.assertRaisesRegex(ValueError, "cannot require and forbid"):
                GENERATOR.load_entries(path)
        finally:
            path.unlink()

    def test_unregistered_scenario_branch_is_rejected(self) -> None:
        inventory = json.loads(GENERATOR.INVENTORY.read_text(encoding="utf-8"))
        inventory["scenarios"][0]["required_branches"].append("rows.imaginary")
        inventory["scenarios"][0]["required_branches"].sort()
        path = self._temporary_inventory(inventory)
        try:
            with self.assertRaisesRegex(ValueError, "contains unregistered branches"):
                GENERATOR.load_entries(path)
        finally:
            path.unlink()

    def _temporary_inventory(self, inventory: object) -> Path:
        path = ROOT / "target" / "scenario-inventory-generator-test.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(inventory), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
