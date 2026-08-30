from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dao_allocation_a9 as a9  # noqa: E402


ACQUISITION = ROOT / "oracle" / "windows-dao" / "acquisition"
PLAN = ACQUISITION / "a9-allocation.plan.json"
SYNTHETIC = ACQUISITION / "a9-allocation.synthetic.json"
GENERATOR = SCRIPTS / "Invoke-DaoAllocationA9.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "windows-dao-allocation-a9.yml"


class A9PlanTests(unittest.TestCase):
    def test_plan_pins_every_execution_input(self) -> None:
        a9.validate_plan(PLAN, ROOT)
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.assertEqual(plan["issue"], 99)
        self.assertEqual(plan["execution"]["attempts"], 1)
        self.assertEqual(plan["execution"]["replicas"], 3)
        self.assertFalse(plan["publication"]["mdb_bytes_committed"])
        for relative in (
            ".github/workflows/windows-dao-allocation-a9.yml",
            "oracle/windows-dao/scripts/Invoke-DaoAllocationA9.ps1",
            "oracle/windows-dao/scripts/dao_allocation_a9.py",
            "oracle/windows-dao/scripts/probe-provider.ps1",
        ):
            self.assertIn(relative, plan["inputs"])
        broken = copy.deepcopy(plan)
        broken["inputs"]["oracle/windows-dao/scripts/dao_allocation_a9.py"] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(a9.EvaluationError, "digest differs"):
                a9.validate_plan(path, ROOT)

    def test_generator_is_bounded_and_covers_every_question(self) -> None:
        generator = GENERATOR.read_text(encoding="utf-8")
        for name in ("$MaximumRows", "$MaximumPages", "$MaximumTaggedPages", "Assert-Budget"):
            self.assertIn(name, generator)
        for question in a9.QUESTIONS:
            self.assertIn(f'-Question "{question}"', generator)
        self.assertIn("dao_allocation_a9_manifest", generator)
        self.assertNotIn("Invoke-Expression", generator)
        self.assertNotIn("CompactDatabase", generator)


class A9EvaluatorTests(unittest.TestCase):
    def test_synthetic_report_is_reproducible_and_non_evidentiary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "synthetic.json"
            a9.synthetic_dry_run(generated)
            self.assertEqual(generated.read_bytes(), SYNTHETIC.read_bytes())
        report = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
        self.assertFalse(report["compatibility_claim"])
        self.assertTrue(report["consistent_input_accepted"])
        self.assertTrue(report["inconsistent_input_rejected"])

    def test_replica_disagreement_is_no_outcome_not_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            entry = next(
                item
                for item in a9.load_json(manifest_path)["checkpoints"]
                if item["replica"] == 3 and item["question"] == "Q3" and item["name"] == "04-reinsert-4"
            )
            document = a9.load_json(root / entry["path"])
            # Replica 3 appends a page instead of reusing one: verdicts differ.
            document["page_count"] += 1
            document["pages"].append(
                {"page": 30, "sha256": a9.sha256(bytes([1]) + bytes(2047)), "hex": (bytes([1]) + bytes(2047)).hex()}
            )
            raw = a9.canonical_bytes(document)
            (root / entry["path"]).write_bytes(raw)
            manifest = a9.load_json(manifest_path)
            for item in manifest["checkpoints"]:
                if item["path"] == entry["path"]:
                    item["sha256"], item["page_count"] = a9.sha256(raw), document["page_count"]
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q3"]["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q1"]["status"], "answered")

    def test_uncaptured_indirect_reference_is_no_outcome(self) -> None:
        with self.assertRaisesRegex(a9.NoOutcome, "reference 1001 was not captured"):
            a9.usage_record(a9.SyntheticDatabase.type1([1001]), {})

    def test_q4_requires_owned_map_reference_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = a9.write_synthetic_artifact(root)
            manifest = a9.load_json(manifest_path)
            for replica in range(1, a9.REPLICAS + 1):
                before_entry = next(
                    item
                    for item in manifest["checkpoints"]
                    if item["replica"] == replica
                    and item["question"] == "Q4"
                    and item["name"] == "01-before-first-type05"
                )
                after_entry = next(
                    item
                    for item in manifest["checkpoints"]
                    if item["replica"] == replica
                    and item["question"] == "Q4"
                    and item["name"] == "02-after-first-type05"
                )
                before = a9.load_json(root / before_entry["path"])
                after = a9.load_json(root / after_entry["path"])
                before_map = next(page for page in before["pages"] if page["page"] == 21)
                after["pages"] = [
                    before_map if page["page"] == 21 else page for page in after["pages"]
                ]
                raw = a9.canonical_bytes(after)
                (root / after_entry["path"]).write_bytes(raw)
                after_entry["sha256"] = a9.sha256(raw)
            a9.write_canonical(manifest_path, manifest)
            report = a9.evaluate(manifest_path, root, root / "report.json")
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["Q4"]["status"], "no_outcome")
        self.assertIn("did not extend", report["questions"]["Q4"]["reason"])

    def test_row_directory_rejects_deleted_and_overflow_rows(self) -> None:
        page = bytearray(a9.PAGE_SIZE)
        page[0] = 0x01
        page[8:10] = (1).to_bytes(2, "little")
        page[10:12] = (0x8000 | 2040).to_bytes(2, "little")
        self.assertIsNone(a9.row_bytes(bytes(page), 0))
        page[10:12] = (2040).to_bytes(2, "little")
        self.assertEqual(len(a9.row_bytes(bytes(page), 0)), 8)


class A9WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_only_with_read_only_permissions(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertRegex(self.workflow, r"permissions:\n  contents: read\n")
        self.assertIn("windows-2022", self.workflow)

    def test_gating_matches_the_read_differential(self) -> None:
        for token in (
            "accept_microsoft_access_runtime_license:",
            "run_acquisition:",
            "approve_acquisition:",
            "plan_sha256:",
            "a9-allocation.plan.json",
            "dao_allocation_a9.py plan",
            "Get-AuthenticodeSignature",
            "Invoke-DaoAllocationA9.ps1",
            "dao_allocation_a9.py evaluate",
            "if-no-files-found: error",
        ):
            self.assertIn(token, self.workflow)
        self.assertLess(
            self.workflow.index("Verify preregistration and human approval"),
            self.workflow.index("Probe the untouched runner image"),
        )
        self.assertNotIn("cargo", self.workflow)
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
