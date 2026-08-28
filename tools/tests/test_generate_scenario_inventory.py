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
        _, entries = GENERATOR.load_entries()

        self.assertEqual(
            [identifier for identifier, _ in entries],
            [scenario["id"] for scenario in inventory["scenarios"]],
        )
        self.assertEqual(
            GENERATOR.OUTPUT.read_text(encoding="utf-8"),
            GENERATOR.render(),
        )


if __name__ == "__main__":
    unittest.main()
