from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
A1 = SCRIPTS / "a1"
ENTRY = SCRIPTS / "run-a1-controlled.ps1"
CONTROLLER = A1 / "A1.Controller.ps1"
WORKER = A1 / "A1.Worker.ps1"
PAGE_STORE = A1 / "A1.PageStore.ps1"
PLAN = ROOT / "experiments" / "a1" / "a1-allocation-maps.plan.json"


class A1PowerShellSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.page_store = PAGE_STORE.read_text(encoding="utf-8")
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.combined = "\n".join(
            (cls.entry, cls.controller, cls.worker, cls.page_store)
        )

    def test_exact_replica_and_checkpoint_schedule_is_plan_driven(self) -> None:
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 71)
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertEqual(self.plan["bounds"]["max_checkpoints_per_replica"], 72)
        self.assertIn("checkpoint_design.checkpoint_ids", self.worker)
        self.assertIn("checkpoint_design.count", self.worker)
        self.assertNotIn("$ids.Count -ne 64", self.combined)
        self.assertNotIn("$ids.Count -ne 67", self.combined)
        self.assertIn(
            "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80",
            self.controller,
        )
        self.assertIn("A1 plan bytes differ from the frozen", self.controller)

    def test_all_role_rotations_are_bound_by_the_plan(self) -> None:
        bindings = self.plan["tables"]["role_bindings"]
        self.assertEqual(len(bindings), 3)
        self.assertEqual(
            {tuple(row[role] for role in ("D", "L", "P", "H")) for row in bindings},
            {
                ("A1TAB_A", "A1TAB_B", "A1TAB_C", "A1TAB_D"),
                ("A1TAB_B", "A1TAB_C", "A1TAB_D", "A1TAB_A"),
                ("A1TAB_C", "A1TAB_D", "A1TAB_A", "A1TAB_B"),
            },
        )
        self.assertIn("Plan.tables.role_bindings", self.worker)
        self.assertIn("equal-length table design drifted", self.worker)

    def test_dao_schema_and_deterministic_rows_are_exact(self) -> None:
        for fragment in (
            '$script:A1DbLong = 4',
            '$script:A1DbText = 10',
            '$script:A1DbFixedField = 1',
            'CreateField("Id", $script:A1DbLong)',
            'CreateField("Payload", $script:A1DbText, 240)',
            '$payloadField.Attributes = $script:A1DbFixedField',
            'growth_batch_rows -ne 32',
            '"A1|$Role|$($Id.ToString(\'D10\'))|"',
            "GetBytes([int]$Id)",
            "GetBytes([uint16]$payloadBytes.Length)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.worker)
        self.assertIn("Id, Payload", self.worker)
        self.assertIn("ORDER BY Id", self.worker)

    def test_checkpoint_closes_rereads_and_requires_quiescence(self) -> None:
        checkpoint = self.worker.index("function Add-A1Checkpoint")
        semantic = self.worker.index("Read-A1SemanticTables", checkpoint)
        snapshot = self.worker.index("Read-A1PageSnapshot", semantic)
        self.assertLess(checkpoint, semantic)
        self.assertLess(semantic, snapshot)
        self.assertGreaterEqual(
            self.worker[checkpoint:snapshot].count("Assert-A1Quiescent"), 2
        )
        self.assertIn("A1 DAO lock companion remains after close", self.worker)
        self.assertIn("retained_for_physical_analysis = $false", self.worker)

    def test_growth_targets_are_crossed_only_by_fixed_batches(self) -> None:
        self.assertIn("do {", self.worker)
        self.assertIn("Add-A1RowBatch -Role $Role", self.worker)
        self.assertIn("} while ($pages -lt $ThresholdPages)", self.worker)
        self.assertIn("target_overshoot_pages", self.worker)
        self.assertIn("$baseline + 128", self.worker)
        self.assertIn('^([LH])_REL_([0-9]{4})$', self.worker)
        self.assertIn('^P_ABS_([0-9]{5})$', self.worker)
        for forbidden in ("retune", "adaptive checkpoint", "batch_rows +="):
            self.assertNotIn(forbidden, self.worker.lower())

    def test_page_store_is_exact_content_addressed_and_reconstructable(self) -> None:
        for fragment in (
            '$script:A1PageBytes = 2048L',
            '"page-store/$Sha256.page"',
            "[IO.FileMode]::CreateNew",
            "Assert-A1ExistingPageBlob",
            "page_sha256 = @($snapshot.hashes)",
            "ordered_page_sha256 = @($snapshot.hashes)",
            "changed_page_indices = @(",
            '"page-indexes/replica-{0:D2}/{1:D2}-{2}.json"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.combined)
        self.assertNotIn("page-store/sha256/", self.combined)

    def test_resource_and_process_ceilings_are_fail_closed(self) -> None:
        expected = {
            "20480": self.combined,
            "262144": self.combined,
            "512MB": self.combined,
            "768MB": self.combined,
            "200000": self.combined,
            "1500000": self.combined,
            "8GB": self.combined,
            "1800": self.controller,
            "7200": self.controller,
            "1MB": self.controller,
            "64KB": self.worker,
        }
        for literal, source in expected.items():
            with self.subTest(literal=literal):
                self.assertIn(literal, source)
        self.assertIn("Stop-BoundedProcessJob", self.controller)
        self.assertIn("StartSuspendedInJob", self.controller)
        self.assertIn("Get-A1CampaignAllowance", self.controller)

    def test_python_runtime_and_long_validation_timeouts_are_audited(self) -> None:
        self.assertIn("A1 requires Python 3.10 or newer", self.controller)
        self.assertIn("preflight-bound Python 3.13 runtime", self.controller)
        self.assertIn("python_version = $PythonVersion", self.controller)
        probe = self.controller.index(
            "$pythonVersion = Assert-A1PythonRuntime -Context $context"
        )
        staging = self.controller.index("New-M1PublicationSession", probe)
        self.assertLess(probe, staging)
        self.assertIn(
            "[ValidateRange(1, 1800)][int]$MaximumSeconds = 120",
            self.controller,
        )
        self.assertIn(
            "Get-A1CampaignAllowance -MaximumSeconds $MaximumSeconds",
            self.controller,
        )
        self.assertIn(
            ') -Label "A1 preregistered analysis" -MaximumSeconds 1800',
            self.controller,
        )
        self.assertIn(
            '-Label "A1 complete bundle validation" -MaximumSeconds 1800',
            self.controller,
        )
        # Two Python calls plus the independently frozen worker allowance.
        self.assertEqual(self.controller.count("-MaximumSeconds 1800"), 3)

    def test_clean_pushed_provider_and_source_identity_gates_precede_work(self) -> None:
        for fragment in (
            "status --porcelain=v1",
            "rev-parse --verify HEAD",
            "hash-object -- $path",
            'ls-remote", "--heads"',
            "Assert-M1RuntimeBinding",
            "Assert-M1CurrentRegistration",
            "provider binary differs from its environment digest",
            "x86 Windows PowerShell 5 Desktop",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.combined)
        self.assertIn(
            'Plan.execution_gate.status -cne "BLOCKED"', self.controller
        )
        self.assertIn("checked_windows_acquisition", self.controller)
        self.assertNotIn('execution_gate.status = "READY"', self.controller)

    def test_publication_is_collision_free_and_complete_only(self) -> None:
        manifest = self.controller.index("Write-A1Manifest")
        publish = self.controller.index("Publish-M1Stage", manifest)
        self.assertLess(manifest, publish)
        self.assertIn("validate-bundle", self.controller[manifest:publish])
        self.assertIn("Remove-A1PrivateStaging", self.controller)
        self.assertIn("-MaxEntries 263000", self.controller)
        self.assertIn("cleanup also", self.controller)
        self.assertIn("inventory_closed = $true", self.controller)
        self.assertIn("hashes_verified = $true", self.controller)
        self.assertIn("paths_closed = $true", self.controller)

    def test_acquisition_assigns_no_physical_meaning_to_bytes(self) -> None:
        forbidden = (
            "allocation map",
            "tdef",
            "usage bitmap",
            "record boundary",
            "page[0]",
            "page[1]",
            "bitmask",
        )
        lowered = "\n".join((self.worker, self.page_store)).lower()
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, lowered)

    def test_no_admin_or_destructive_file_command_is_used(self) -> None:
        for forbidden in (
            "Start-Process -Verb RunAs",
            "Remove-Item",
            "[IO.File]::Delete",
            "Format-Volume",
        ):
            with self.subTest(fragment=forbidden):
                self.assertNotIn(forbidden, self.combined)
        self.assertEqual(self.combined.count("[IO.Directory]::Delete"), 1)
        self.assertIn("Refusing cleanup outside", self.controller)

    def test_snapshot_shrink_and_dao_collections_are_explicit(self) -> None:
        self.assertIn(
            "$PriorHashes.Count -gt $pageCount", self.page_store
        )
        self.assertIn("page_index = [long]$index", self.page_store)
        self.assertIn("$fields = $Recordset.Fields", self.worker)
        self.assertIn("$tableDefinitions = $database.TableDefs", self.worker)
        self.assertIn("A1 table definitions release", self.worker)
        self.assertIn('Label "A1 table deletion"', self.worker)

    def test_production_scripts_stay_below_800_lines(self) -> None:
        for path in (ENTRY, CONTROLLER, WORKER, PAGE_STORE):
            with self.subTest(path=path.name):
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()), 800
                )


if __name__ == "__main__":
    unittest.main()
