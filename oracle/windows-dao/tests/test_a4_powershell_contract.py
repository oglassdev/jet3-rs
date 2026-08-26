"""Static contracts for the checked A4 PowerShell worker."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle/windows-dao/scripts"
PLAN_PATH = ROOT / "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json"
WORKER_PATH = SCRIPTS / "a4/A4.Worker.ps1"
POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a4_spec import CHECKPOINT_IDS, EXPERIMENT_ID, PLAN_SHA256  # noqa: E402


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class A4PowerShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN_PATH.read_bytes())
        cls.entry = (SCRIPTS / "run-a4-replica.ps1").read_text(encoding="utf-8")
        cls.worker = WORKER_PATH.read_text(encoding="utf-8")
        cls.store = (SCRIPTS / "a4/A4.PageStore.ps1").read_text(encoding="utf-8")
        cls.snapshot = (SCRIPTS / "a4/A4.SchemaSnapshot.ps1").read_text(
            encoding="utf-8"
        )
        cls.progress = (SCRIPTS / "a4/A4.Progress.ps1").read_text(encoding="utf-8")

    def test_identity_is_exact_and_contains_no_retired_a4_binding(self) -> None:
        for source in (self.entry, self.worker):
            self.assertIn(EXPERIMENT_ID, source)
            self.assertIn(PLAN_SHA256, source)
            self.assertIn("a4-row-anchored-maps.plan.json", source)
            self.assertNotIn("DAO-A4-ALLOCATION-MAPS-001", source)
            self.assertNotIn("a4-allocation-maps", source)
        self.assertEqual(PLAN_SHA256, hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest())

    def test_schedule_and_role_binding_are_plan_derived(self) -> None:
        self.assertNotIn("$Plan.preregistration.status", self.worker)
        self.assertIn(
            "$Plan.preregistration.acquisition_started -ne $false", self.worker
        )
        self.assertIn("$Plan.checkpoint_design.checkpoint_ids", self.worker)
        self.assertIn("$Plan.tables.logical_roles", self.worker)
        self.assertIn("$Plan.tables.role_bindings", self.worker)
        self.assertIn("checkpoint_operations.PSObject.Properties[$id]", self.worker)
        self.assertIn("foreach ($value in @($script:A4Plan.checkpoint_design.checkpoint_ids))", self.worker)
        self.assertEqual(tuple(self.plan["checkpoint_design"]["checkpoint_ids"]), CHECKPOINT_IDS)
        self.assertEqual(self.plan["tables"]["logical_roles"], ["T1", "T2", "T3", "T4"])
        for legacy in ('@("D", "L", "P", "H")', "d_growth_observation"):
            self.assertNotIn(legacy, self.worker)

    def test_dao_mutations_match_the_preregistered_call_shapes(self) -> None:
        for token in (
            ".CreateDatabase(",
            ".CreateTableDef(",
            ".CreateField(",
            ".CreateIndex(",
            ".AddNew()",
            ".Update()",
            ".Delete()",
            "OpenRecordset($name, 2, 0)",
        ):
            self.assertIn(token, self.worker)
        self.assertIn("$script:A4Locale = \";LANGID=0x0409;CP=1252;COUNTRY=0\"", self.worker)
        for forbidden in ("BeginTrans", "CommitTrans", "Rollback", "CompactDatabase"):
            self.assertNotIn(forbidden, self.worker)

    def test_every_checkpoint_gets_read_only_schema_and_physical_capture(self) -> None:
        capture = self.worker.index("function Add-A4Checkpoint")
        schema = self.worker.index("Read-A4SchemaSnapshot", capture)
        physical = self.worker.index("Read-A4PageSnapshot", schema)
        self.assertLess(schema, physical)
        self.assertIn("$script:A4Workspace.OpenDatabase(", self.snapshot)
        self.assertIn('$script:A4DatabasePath, $false, $true, ""', self.snapshot)
        self.assertIn("database_sha256_before_read", self.snapshot)
        self.assertIn("database_sha256_after_read", self.snapshot)
        self.assertIn("name_windows_1252_hex", self.snapshot)
        self.assertIn("name_utf8_hex", self.snapshot)
        self.assertIn("dao_schema_snapshot", self.worker)
        self.assertIn("$schemaSnapshots -ne 25", self.worker)

    def test_worker_bounds_match_the_frozen_plan(self) -> None:
        for token in (
            "max_final_pages_per_replica -ne 20480",
            "max_inserted_rows_per_replica -ne 200000",
            "max_unique_page_blobs -ne 65536",
            "max_retained_page_store_bytes -ne 128MB",
            "max_bundle_bytes -ne 768MB",
            "worker_timeout_seconds_per_replica -ne 1700",
        ):
            self.assertIn(token, self.worker)
        self.assertIn("$script:A1MaximumPagesPerReplica = 20480L", self.store)
        self.assertIn("$script:A1MaximumPageStoreBytes = 128MB", self.store)
        self.assertIn("-TimeoutSeconds 1700", self.entry)

    def test_controller_requires_x86_dao_cp1252_and_clean_pushed_source(self) -> None:
        for token in (
            "x86 Windows PowerShell 5 Desktop",
            'prog_id -cne "DAO.DBEngine.36"',
            "windows_ansi_code_page =",
            "GetACP()",
            "GetOEMCP()",
            "windows_ansi_code_page -ne 1252",
            "Assert-A4ExactPushedCommit",
            "status --porcelain=v1",
            "Assert-M1CurrentRegistration",
            "server_sha256",
        ):
            self.assertIn(token, self.entry + self.worker)

    def test_output_inventory_includes_exact_schema_snapshot_tree(self) -> None:
        self.assertIn('"schema-snapshots/$replicaId"', self.entry)
        self.assertIn('"schema-snapshots/replica-{0:D2}"', self.entry)
        self.assertIn("DAO schema-snapshot validation", self.entry)
        self.assertIn("unmanifested DAO schema snapshot", self.entry)
        self.assertIn("dao-schema-snapshot.schema.json", self.entry)
        self.assertIn("A4.SchemaSnapshot.ps1", self.entry)

    def test_progress_and_failure_artifacts_are_bounded(self) -> None:
        self.assertIn("Set-StrictMode -Version Latest", self.progress)
        self.assertIn("Write-A4Failure", self.entry)
        self.assertIn("$text.Length -gt 4000", self.entry)
        self.assertIn("MaximumOutputBytes 1MB", self.entry)
        self.assertIn("failed-replica-{0:D2}.mdb", self.entry)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class A4PowerShellWindowsNoComTests(unittest.TestCase):
    def test_plan_chain_accepts_the_actual_frozen_plan_under_strict_mode(self) -> None:
        command = (
            "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
            "$tokens=$null;$errors=$null;"
            "$ast=[Management.Automation.Language.Parser]::ParseFile("
            f"{ps_quote(WORKER_PATH)},[ref]$tokens,[ref]$errors);"
            "if($errors.Count-ne 0){throw 'A4 worker parse failed'};"
            "$functions=@($ast.FindAll({param($node)"
            "$node-is [Management.Automation.Language.FunctionDefinitionAst]"
            "-and $node.Name-ceq 'Assert-A4PlanChain'},$true));"
            "if($functions.Count-ne 1){throw 'A4 plan-chain function count drifted'};"
            "Invoke-Expression $functions[0].Extent.Text;"
            "$script:A4RequiredPlanPath="
            "'oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json';"
            f"$script:A4ExperimentId={ps_quote(EXPERIMENT_ID)};"
            f"$plan=Get-Content -Raw -LiteralPath {ps_quote(PLAN_PATH)}|ConvertFrom-Json;"
            "Assert-A4PlanChain -Plan $plan;"
            "$plan.preregistration.acquisition_started=$true;"
            "$rejected=$false;try{Assert-A4PlanChain -Plan $plan}catch{"
            "if($_.Exception.Message-cne "
            "'A4 base-plan chain or implementation binding drifted.'){throw};"
            "$rejected=$true};"
            "if(-not $rejected){throw 'A4 acquired plan was accepted'};"
            "[Console]::Write('accepted|rejected')"
        )
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "accepted|rejected")


if __name__ == "__main__":
    unittest.main()
