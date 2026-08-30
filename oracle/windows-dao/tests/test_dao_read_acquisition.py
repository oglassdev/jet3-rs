from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dao_read_diff  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402


PLAN = ROOT / "oracle" / "windows-dao" / "acquisition" / "read-v1_2.plan.json"
SYNTHETIC = (
    ROOT / "oracle" / "windows-dao" / "acquisition" / "read-v1_2.synthetic.json"
)
PRODUCER = SCRIPTS / "Invoke-DaoReadV12.ps1"
EXPECTED_EXECUTION_INPUTS = {
    ".github/workflows/windows-dao-hosted.yml",
    "Cargo.lock",
    "Cargo.toml",
    "docs/validation/support-matrix.json",
    "oracle/windows-dao/protocol/v1_2/branch-registry.json",
    "oracle/windows-dao/protocol/v1_2/branch-registry.schema.json",
    "oracle/windows-dao/protocol/v1_2/canonical-semantic-snapshot.schema.json",
    "oracle/windows-dao/protocol/v1_2/coverage-receipt.schema.json",
    "oracle/windows-dao/protocol/v1_2/scenarios.json",
    "oracle/windows-dao/protocol/v1_2/scenarios.schema.json",
    "oracle/windows-dao/scripts/Invoke-DaoReadV12.ps1",
    "oracle/windows-dao/scripts/build_v1_2_inventory.py",
    "oracle/windows-dao/scripts/dao_read_diff.py",
    "oracle/windows-dao/scripts/probe-provider.ps1",
    "oracle/windows-dao/scripts/protocol_validation.py",
    "oracle/windows-dao/scripts/remote/Remote.Process.ps1",
    "oracle/windows-dao/scripts/validate_protocol_v1_2.py",
    "rust-toolchain.toml",
}
EXPECTED_SOURCE_TREES = {
    "crates/jet3",
    "crates/jet3-cli",
    "crates/jet3-testkit",
}


class DaoReadAcquisitionTests(unittest.TestCase):
    def test_preregistered_plan_pins_every_execution_input(self) -> None:
        dao_read_diff.validate_plan(PLAN, ROOT)
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.assertEqual(set(plan["inputs"]), EXPECTED_EXECUTION_INPUTS)
        self.assertEqual(set(plan["source_trees"]), EXPECTED_SOURCE_TREES)
        self.assertEqual(plan["execution"]["attempts"], 1)
        self.assertEqual(plan["execution"]["scenario_count"], 98)
        self.assertFalse(plan["publication"]["mdb_bytes_committed"])
        self.assertFalse(plan["synthetic_dry_run"]["compatibility_claim"])

        broken = copy.deepcopy(plan)
        first = next(iter(broken["inputs"]))
        broken["inputs"][first] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "digest differs"):
                dao_read_diff.validate_plan(path, ROOT)

    def test_dao_producer_covers_the_closed_recipe_grammar(self) -> None:
        producer = PRODUCER.read_text(encoding="utf-8")
        for action in (
            "create_database",
            "create_table",
            "create_relationship",
            "insert_rows",
            "insert_until_page_count",
            "grow_rows",
            "delete_rows",
            "drop_table",
            "reopen",
            "close_database",
        ):
            self.assertIn(f'"{action}"', producer)
        for encoding in (
            "null",
            "boolean",
            "integer",
            "invariant_decimal",
            "ieee_bits_hex",
            "invariant_datetime",
            "lowercase_hex",
            "unicode_string",
            "repeat_byte",
            "repeat_ascii",
            "guid",
        ):
            self.assertIn(f'"{encoding}"', producer)
        self.assertIn("$MaximumGeneratedRows", producer)
        self.assertIn("dao-snapshot.raw.json", producer)
        self.assertIn("dao-manifest.raw.json", producer)
        self.assertNotIn("Invoke-Expression", producer)
        self.assertNotIn("CompactDatabase", producer)

    def test_synthetic_report_is_reproducible_and_non_evidentiary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "synthetic.json"
            dao_read_diff.synthetic_dry_run(generated)
            self.assertEqual(generated.read_bytes(), SYNTHETIC.read_bytes())
        report = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
        self.assertFalse(report["compatibility_claim"])


if __name__ == "__main__":
    unittest.main()
