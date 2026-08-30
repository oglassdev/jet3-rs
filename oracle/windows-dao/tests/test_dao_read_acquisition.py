from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dao_read_diff  # noqa: E402


PLAN = ROOT / "oracle" / "windows-dao" / "acquisition" / "read-v1_2.plan.json"
SYNTHETIC = (
    ROOT / "oracle" / "windows-dao" / "acquisition" / "read-v1_2.synthetic.json"
)
PRODUCER = SCRIPTS / "Invoke-DaoReadV12.ps1"
PROVENANCE = ROOT / "docs" / "PROVENANCE.md"
INVENTORY = ROOT / "oracle" / "windows-dao" / "protocol" / "v1_2" / "scenarios.json"
SUPPORT_MATRIX = ROOT / "docs" / "validation" / "support-matrix.json"
APPROVED_PLAN_SHA256 = (
    "b4a05fc381efdaf56011205063c07232a77d23f99837e021242ee199cda48570"
)
ACCEPTED_REPORT_SHA256 = (
    "d5593d9a66962b478e68bf8e764cb606911db6d8b04e41390e81b0f46cc6eea4"
)
ACCEPTED_SOURCE_REVISION = "e6a7b2c24afa2ef386031a2e70cdedb120180a3e"
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
EXPECTED_DAO_DIFFERENTIAL_CAPABILITIES = {
    "database.open",
    "format.header_and_version",
    "format.pages_allocation_usage",
    "indexes.composite_ascending_descending",
    "indexes.primary_unique_non_unique",
    "rows.streaming_read",
    "schema.catalog_and_table_definitions",
    "values.all_dao_jet3_table_types",
    "values.code_pages_lossless_raw",
    "values.date_currency_binary_guid_replication",
    "values.memo_ole_multi_page",
    "values.null_fixed_variable",
}


class DaoReadAcquisitionTests(unittest.TestCase):
    def test_consumed_plan_and_accepted_result_remain_bound(self) -> None:
        self.assertEqual(
            hashlib.sha256(PLAN.read_bytes()).hexdigest(), APPROVED_PLAN_SHA256
        )
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.assertEqual(plan["document_type"], "dao_read_acquisition_plan")
        self.assertEqual(plan["protocol_version"], "1.2.0")
        self.assertEqual(set(plan["inputs"]), EXPECTED_EXECUTION_INPUTS)
        self.assertEqual(set(plan["source_trees"]), EXPECTED_SOURCE_TREES)
        self.assertEqual(
            plan["inputs"]["docs/validation/support-matrix.json"],
            "3cfb49712acbe715fa87516c9861953d31ecd495530be1ab4dfc410e209f3715",
        )
        self.assertEqual(plan["execution"]["attempts"], 1)
        self.assertEqual(plan["execution"]["scenario_count"], 98)
        self.assertFalse(plan["publication"]["mdb_bytes_committed"])
        self.assertFalse(plan["synthetic_dry_run"]["compatibility_claim"])

        provenance = PROVENANCE.read_text(encoding="utf-8")
        result_entry = provenance.split("### EXP-0064", maxsplit=1)[1].split(
            "\n## ", maxsplit=1
        )[0]
        self.assertIn(APPROVED_PLAN_SHA256, result_entry)
        self.assertIn(ACCEPTED_REPORT_SHA256, result_entry)
        self.assertIn(ACCEPTED_SOURCE_REVISION, result_entry)

        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        covered_capabilities = {
            capability
            for scenario in inventory["scenarios"]
            for capability in scenario["capability_ids"]
        }
        self.assertEqual(
            covered_capabilities, EXPECTED_DAO_DIFFERENTIAL_CAPABILITIES
        )
        matrix = json.loads(SUPPORT_MATRIX.read_text(encoding="utf-8"))
        differential_capabilities = {
            capability["id"]
            for capability in matrix["capabilities"]
            if capability["verification"] == "dao_differential"
        }
        self.assertLessEqual(
            EXPECTED_DAO_DIFFERENTIAL_CAPABILITIES, differential_capabilities
        )
        for capability in matrix["capabilities"]:
            if capability["id"] in EXPECTED_DAO_DIFFERENTIAL_CAPABILITIES:
                self.assertIn("docs/PROVENANCE.md", capability["evidence"])

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
