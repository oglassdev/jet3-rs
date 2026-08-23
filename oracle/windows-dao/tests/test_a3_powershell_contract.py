from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
A3 = SCRIPTS / "a3"
ENTRY = SCRIPTS / "run-a3-replica.ps1"
WORKER = A3 / "A3.Worker.ps1"
PAGE_STORE = A3 / "A3.PageStore.ps1"
PROGRESS = A3 / "A3.Progress.ps1"
A1_PAGE_STORE = SCRIPTS / "a1" / "A1.PageStore.ps1"
PLAN = ROOT / "experiments" / "a3" / "a3-allocation-maps.plan.json"
SPEC = SCRIPTS / "a3_spec.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a3_spec import (  # noqa: E402
    EXPERIMENT_ID,
    PLAN_SHA256,
    R2_PLAN_SHA256,
    R3_PLAN_SHA256,
    R4_PLAN_SHA256,
    REVISION_PLAN_SHA256,
    validate_document,
)
from a3_test_bundle import replica_documents  # noqa: E402
from a3_generator import generate_synthetic_bundle  # noqa: E402
from protocol_validation import ValidationError  # noqa: E402

A2_SCRIPTS = SCRIPTS / "a2"
A2_PLAN_SHA256 = "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"


def function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    end = source.find("\nfunction ", start + 1)
    return source[start:] if end < 0 else source[start:end]


class A3PowerShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.page_store = PAGE_STORE.read_text(encoding="utf-8")
        cls.progress = PROGRESS.read_text(encoding="utf-8")
        cls.a1_page_store = A1_PAGE_STORE.read_text(encoding="utf-8")
        cls.plan_bytes = PLAN.read_bytes()
        cls.plan = json.loads(cls.plan_bytes)
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.combined = "\n".join(
            (cls.entry, cls.worker, cls.page_store, cls.progress)
        )

    def test_entrypoint_implements_the_exact_matrix_job_interface(self) -> None:
        for parameter in (
            "RepositoryRoot",
            "OutputRoot",
            "DiagnosticsRoot",
            "GitCommit",
            "RunId",
            "Replica",
            "MatrixJobId",
        ):
            with self.subTest(parameter=parameter):
                self.assertRegex(
                    self.entry,
                    rf"\[Parameter\(Mandatory = \$true\)\].*\${parameter}",
                )
        self.assertIn("[ValidateRange(1, 3)][int]$Replica", self.entry)
        self.assertNotIn("EnvironmentPath", self.entry.split(")\nSet-StrictMode", 1)[0])

    def test_frozen_plan_hash_schedule_and_bounds_are_exact(self) -> None:
        digest = hashlib.sha256(self.plan_bytes).hexdigest()
        self.assertEqual(
            digest,
            "b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1",
        )
        self.assertIn(digest, self.entry)
        self.assertIn(digest, self.worker)
        self.assertEqual(self.plan["experiment_id"], EXPERIMENT_ID)
        self.assertNotIn(A2_PLAN_SHA256, self.combined)
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 25)
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertIn("checkpoint_design.checkpoint_ids", self.worker)
        self.assertIn("A3 checkpoint schedule differs", self.worker)
        for literal in (
            "65536",
            "2GB",
            "524288",
            "512MB",
            "768MB",
            "64MB",
            "64KB",
            "1700",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.combined)

    def test_all_four_empty_tables_exist_before_e0(self) -> None:
        schedule = function_source(self.worker, "Invoke-A3Schedule")
        create = schedule.index("Invoke-A3WithDatabase -Create")
        tables = schedule.index('foreach ($role in @("D", "L", "P", "H"))')
        checkpoints = schedule.index("checkpoint_design.checkpoint_ids")
        self.assertLess(create, tables)
        self.assertLess(tables, checkpoints)
        self.assertIn("Add-A3Table -Role $role", schedule)

    def test_role_rotations_are_selected_from_the_plan(self) -> None:
        expected = {
            ("A2TAB_A", "A2TAB_B", "A2TAB_C", "A2TAB_D"),
            ("A2TAB_B", "A2TAB_C", "A2TAB_D", "A2TAB_A"),
            ("A2TAB_C", "A2TAB_D", "A2TAB_A", "A2TAB_B"),
        }
        bindings = self.plan["tables"]["role_bindings"]
        self.assertEqual(
            {tuple(row[role] for role in ("D", "L", "P", "H")) for row in bindings},
            expected,
        )
        self.assertIn("Plan.tables.role_bindings", self.worker)
        self.assertIn("$roleBinding.$role", self.worker)

    def test_d_uses_closed_fixed_batches_recreate_and_strict_regrowth(self) -> None:
        growth = function_source(self.worker, "Add-A3UntilTarget")
        schedule = function_source(self.worker, "Invoke-A3Schedule")
        self.assertLess(
            growth.index("Add-A3RowBatch -Role $Role"),
            growth.index("Assert-A3Quiescent"),
        )
        self.assertLess(
            growth.index("Assert-A3Quiescent"),
            growth.index("Get-A3ClosedPageCount"),
        )
        self.assertIn("} while ($pages -lt $ThresholdPages)", growth)
        self.assertIn('if ($id -ceq "D_RECREATE_EMPTY")', schedule)
        self.assertIn('Add-A3Table -Role "D"', schedule)
        self.assertIn("$baseline + 128", schedule)
        self.assertIn("-le", schedule)
        self.assertIn("regrowth is not strictly greater", schedule)
        self.assertNotIn("row-count replay", schedule.lower())

    def test_l_full_delete_and_exact_reinsert_are_id_ordered(self) -> None:
        delete = function_source(self.worker, "Remove-A3AllLRows")
        restore = function_source(self.worker, "Restore-A3AllLRows")
        self.assertIn('$script:A1Rows["L"] | Sort-Object', delete)
        self.assertIn("SELECT Id FROM [$name] ORDER BY Id", delete)
        self.assertIn("[int]$idField.Value -ne $expectedId", delete)
        self.assertIn("$recordset.Delete()", delete)
        self.assertIn('$script:A1Rows["L"].Clear()', delete)
        self.assertIn("[int[]]@($script:A3DeletedLIds)", restore)
        self.assertIn('Add-A3Ids -Role "L" -Ids $ids', restore)
        insert = function_source(self.worker, "Add-A3Ids")
        self.assertIn("Get-A3Payload -Role $Role -Id $id", insert)

    def test_payload_schema_and_rolling_digest_are_plan_exact(self) -> None:
        for fragment in (
            '$script:A1DbLong = 4',
            '$script:A1DbText = 10',
            '$script:A1DbFixedField = 1',
            'CreateField("Id", $script:A1DbLong)',
            'CreateField("Payload", $script:A1DbText, 240)',
            '"A2|$Role|$($Id.ToString(\'D10\'))|"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.worker)
        for fragment in (
            "GetBytes([int]$Id)",
            "GetBytes([uint16]$payloadBytes.Length)",
            "$Hash.TransformBlock",
            "$Hash.TransformFinalBlock",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.a1_page_store)
        self.assertIn("Get-A3Payload -Role $Role -Id $Id", self.page_store)

    def test_every_checkpoint_rereads_all_extant_tables_in_one_session(self) -> None:
        checkpoint = function_source(self.worker, "Add-A3Checkpoint")
        semantic = checkpoint.index("Read-A3SemanticTables")
        snapshot = checkpoint.index("Read-A3PageSnapshot")
        self.assertLess(semantic, snapshot)
        self.assertGreaterEqual(checkpoint[:snapshot].count("Assert-A3Quiescent"), 2)
        all_tables = function_source(self.a1_page_store, "Read-A1SemanticTables")
        one_table = function_source(self.a1_page_store, "Read-A1SemanticTable")
        self.assertEqual(all_tables.count("Invoke-A1WithDatabase -Action"), 1)
        self.assertIn('@("D", "L", "P", "H")', all_tables)
        self.assertIn("ORDER BY Id", one_table)
        self.assertIn('$idField = $fields.Item("Id")', one_table)
        self.assertIn('$payloadField = $fields.Item("Payload")', one_table)

    def test_dirty_role_digest_cache_is_preserved_for_every_mutation(self) -> None:
        for name in ("Add-A3Table", "Remove-A3Table", "Add-A3Ids"):
            with self.subTest(function=name):
                self.assertIn(
                    "Set-A3ExpectedSemanticDirty -Role $Role",
                    function_source(self.worker, name),
                )
        self.assertIn(
            'Set-A3ExpectedSemanticDirty -Role "L"',
            function_source(self.worker, "Remove-A3AllLRows"),
        )
        self.assertIn("ContainsKey($Role)", self.a1_page_store)
        self.assertIn("New-A1SemanticSha256", self.a1_page_store)

    def test_fast_page_capture_and_exact_reuse_are_preserved(self) -> None:
        self.assertIn("a1/A1.PageStore.ps1", self.page_store)
        for fragment in (
            "[Jet3A1PageSnapshotNative]::Capture",
            "PageEquals(",
            "hash.ComputeHash(bytes)",
            "$capture.ChangedIndices",
            "Add-A1PageBlob",
            "Assert-A1ExistingPageBlob",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.a1_page_store)
        self.assertIn("$script:A1MaximumPagesPerReplica = 65536L", self.page_store)
        self.assertIn("$script:A1MaximumChangedEntries = 65536L", self.page_store)
        self.assertIn("$script:A1MaximumLogicalReadBytes = 2GB", self.page_store)
        self.assertIn("$script:A3PriorPages = [byte[]]$snapshot.pages", self.worker)

    def test_page_indexes_are_ordered_content_addressed_and_reconstructable(self) -> None:
        checkpoint = function_source(self.worker, "Add-A3Checkpoint")
        for field in (
            "ordered_page_sha256",
            "changed_page_indices",
            "database_sha256",
            "predecessor_checkpoint_id",
            "environment_sha256",
            "provider_sha256",
        ):
            with self.subTest(field=field):
                self.assertIn(field, checkpoint)
        verify = function_source(self.entry, "Assert-A3ReplicaOutput")
        self.assertIn('"page-store/$digest.page"', verify)
        self.assertIn("cannot be reconstructed", verify)
        self.assertIn("Get-M1FileSha256 -Path $path", verify)
        self.assertIn("GetFileNameWithoutExtension", verify)

    def test_idle_reopen_and_companion_contract_is_explicit(self) -> None:
        schedule = function_source(self.worker, "Invoke-A3Schedule")
        for checkpoint in ("E0", "E0R", "L_IDLE_REOPEN", "H_IDLE_REOPEN"):
            self.assertIn(f'"{checkpoint}"', schedule)
        quiescence = function_source(self.worker, "Assert-A3Quiescent")
        self.assertIn('ChangeExtension($script:A3DatabasePath, ".ldb")', quiescence)
        self.assertIn("64KB", quiescence)
        checkpoint = function_source(self.worker, "Add-A3Checkpoint")
        self.assertIn("present_after_close = $false", checkpoint)
        self.assertIn("retained_for_physical_analysis = $false", checkpoint)

    def test_output_tree_and_replica_manifest_are_closed(self) -> None:
        for path in (
            '"environment/replica-{0:D2}.json"',
            '"observations/replica-{0:D2}.json"',
            '"replica-artifacts/replica-{0:D2}-manifest.json"',
            '"page-indexes/replica-{0:D2}/{1:D2}-{2}.json"',
            '"^page-store/[0-9a-f]{64}\\.page$"',
        ):
            with self.subTest(path=path):
                self.assertIn(path, self.combined)
        manifest = function_source(self.worker, "Write-A3ReplicaManifest")
        for field in (
            "inventory_closed = $true",
            "hashes_verified = $true",
            "paths_closed = $true",
            "checkpoint_count = 25",
            "files = @($records)",
        ):
            self.assertIn(field, manifest)
        verify = function_source(self.entry, "Assert-A3ReplicaOutput")
        self.assertIn("$files.Count -ne ($records.Count + 1)", verify)
        self.assertIn("unexpected directory", verify)
        self.assertIn("unmanifested artifact", verify)
        for source in (manifest, verify):
            self.assertNotIn("Assert-M1NoReparseComponents -Path $path", source)
            self.assertIn("[IO.FileAttributes]::ReparsePoint", source)

    def test_worker_rechecks_process_and_environment_identity(self) -> None:
        worker = function_source(self.worker, "Invoke-A3Worker")
        for fragment in (
            "[IntPtr]::Size -ne 4",
            "$PSVersionTable.PSVersion.Major -ne 5",
            "$environment.experiment_id",
            "$environment.repository_url",
            '$environment.status -cne "ready"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, worker)

    def test_every_output_document_has_all_five_runtime_bindings(self) -> None:
        checkpoint = function_source(self.worker, "Add-A3Checkpoint")
        observation = function_source(self.worker, "Invoke-A3Worker")
        manifest = function_source(self.worker, "Write-A3ReplicaManifest")
        for source in (checkpoint, observation, manifest):
            for field in (
                "plan_sha256",
                "producer_commit",
                "campaign_id",
                "environment_sha256",
                "provider_sha256",
            ):
                with self.subTest(field=field):
                    self.assertIn(field, source)
        self.assertIn('$campaignId = "a3-run-$RunId"', self.entry)
        self.assertIn("matrix_job_id", self.worker)

    def test_preflight_probe_source_and_pushed_commit_gates_precede_worker(self) -> None:
        campaign = function_source(self.entry, "Invoke-A3ReplicaCampaign")
        probe = campaign.index("Invoke-A3ProviderProbe")
        preflight = campaign.index("Invoke-M1Preflight")
        pushed = campaign.index("Assert-A3ExactPushedCommit")
        worker = campaign.index('"A3 replica worker"')
        self.assertLess(probe, preflight)
        self.assertLess(preflight, pushed)
        self.assertLess(pushed, worker)
        for fragment in (
            "status --porcelain=v1",
            "rev-parse --verify HEAD",
            "hash-object -- $path",
            '"ls-remote", "--heads"',
            "Assert-M1RuntimeBinding",
            "DAO.DBEngine.36",
            "x86 Windows PowerShell 5 Desktop",
        ):
            self.assertIn(fragment, self.entry)

    def test_worker_timeout_is_exact_and_job_bounded(self) -> None:
        campaign = function_source(self.entry, "Invoke-A3ReplicaCampaign")
        self.assertIn("Invoke-BoundedChildProcess", campaign)
        self.assertIn("-TimeoutSeconds 1700", campaign)
        self.assertIn("-ReviewedTimeoutCeilingSeconds 1700", campaign)
        self.assertIn("-MaximumOutputBytes 1MB", campaign)
        self.assertNotIn("Start-Process", campaign)

    def test_all_documents_are_semantically_validated_before_success(self) -> None:
        verify = function_source(self.entry, "Assert-A3ReplicaOutput")
        self.assertIn("A3 page-index schema validation", verify)
        self.assertIn("A3 environment validation", verify)
        self.assertIn("A3 observation validation", verify)
        self.assertIn("A3 replica manifest validation", verify)
        self.assertIn("a3_spec.py", self.entry)
        self.assertLess(
            self.entry.index("Assert-A3ReplicaOutput -Context"),
            self.entry.index("PASS: retained A3 replica"),
        )

    def test_progress_is_durable_and_outside_output(self) -> None:
        a2_progress = (A2_SCRIPTS / "A2.Progress.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $root "progress"', a2_progress)
        self.assertIn('"replica-{0:D2}.jsonl"', a2_progress)
        self.assertIn("$stream.Flush($true)", a2_progress)
        self.assertIn("$script:A2ProgressMaximumBytes = 1MB", a2_progress)
        self.assertIn("New-A3ProgressFile", self.entry)
        self.assertIn("Open-A3WorkerProgress", self.worker)
        self.assertNotIn("OutputRoot", self.progress)

    def test_failure_document_is_minimal_and_fail_closed(self) -> None:
        failure = function_source(self.entry, "Write-A3Failure")
        self.assertIn("[ordered]@{ stage = $Stage; message = $text }", failure)
        self.assertNotIn("document_type", failure)
        catch = self.entry[self.entry.rindex("\ncatch {\n    $failure") :]
        self.assertIn("Write-A3Failure", catch)
        self.assertIn("exit 1", catch)
        self.assertIn("exit 0", self.entry)

    def test_primary_failure_is_preserved_and_failed_mdb_is_retained(self) -> None:
        retain = function_source(self.entry, "Move-A3FailedDatabase")
        campaign = function_source(self.entry, "Invoke-A3ReplicaCampaign")
        self.assertIn('"failed-replica-{0:D2}.mdb"', retain)
        self.assertIn('"replica-{0:D2}"', retain)
        self.assertIn('"ACQUISITION.MDB"', retain)
        self.assertIn("$item.Length -gt 128MB", retain)
        self.assertIn("[IO.File]::Move", retain)
        self.assertIn("catch { $primary = $_ }", campaign)
        self.assertLess(
            campaign.index("Move-A3FailedDatabase"),
            campaign.index("Remove-A3PrivateWorkingRoot"),
        )
        self.assertIn("$primary.Exception.Message", campaign)
        self.assertIn("throw $primary", campaign)

    def test_powershell_51_strictmode_pitfalls_are_guarded(self) -> None:
        self.assertNotRegex(self.combined, r"\[int\]\$[A-Za-z0-9_.]+\.exit_code")
        self.assertNotIn(".ExitCode", self.combined)
        self.assertIn("@($manifest.files)", self.entry)
        self.assertIn("@($observation.checkpoints)", self.entry)
        self.assertIn("@($indexDocument.ordered_page_sha256)", self.entry)
        self.assertIn("@($indexDocument.changed_page_indices).Count", self.entry)
        self.assertIn("[Convert]::ToString($Message)", self.entry)
        self.assertIn("[Convert]::ToString($result.stdout)", self.entry)
        self.assertNotIn("Trim('\"')", self.entry)
        self.assertIn("server_path -match '\"'", self.entry)

    def test_command_conditions_parenthesize_calls_before_logical_operators(
        self,
    ) -> None:
        ambiguous = re.compile(
            r"(?m)^\s*(?:if|while)\s*\(\s*(?:"
            r"-not\s+[A-Za-z][A-Za-z0-9]*-[A-Za-z][A-Za-z0-9]*\b|"
            r"[A-Za-z][A-Za-z0-9]*-[A-Za-z][A-Za-z0-9]*\b"
            r"[^\r\n]*\s-(?:or|and|not)\b)"
        )
        self.assertRegex(
            "if (Test-One -Path $value -or Test-Two -Path $value) {",
            ambiguous,
        )
        for path in (ENTRY, WORKER, PAGE_STORE, PROGRESS):
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), ambiguous)

    def test_acquisition_assigns_no_physical_format_meaning(self) -> None:
        lowered = "\n".join((self.worker, self.page_store)).lower()
        for forbidden in (
            "usage bitmap",
            "record boundary",
            "page[0]",
            "page[1]",
            "bitmask",
            "mdbtools",
            "jackcess",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_production_powershell_files_stay_below_800_lines(self) -> None:
        for path in (ENTRY, WORKER, PAGE_STORE, PROGRESS):
            with self.subTest(path=path.name):
                self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 800)

    def test_worker_is_rebound_to_a3_and_fails_closed_on_plan_identity(self) -> None:
        for document_type in (
            "dao_a3_allocation_maps_plan",
            "dao_a3_environment",
            "dao_a3_page_index",
            "dao_a3_replica_observation",
            "dao_a3_replica_artifact_manifest",
        ):
            with self.subTest(document_type=document_type):
                self.assertIn(f'"{document_type}"', self.combined)
        self.assertNotIn("dao_a2_", self.combined)
        self.assertNotIn("experiments/a2/", self.combined)
        self.assertNotIn("DAO-A2-ALLOCATION-MAPS-001", self.combined)
        self.assertNotIn("a2_spec.py", self.combined)
        self.assertEqual(self.combined.count('"DAO-A3-ALLOCATION-MAPS-001"'), 2)
        required = "oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json"
        self.assertEqual(self.entry.count(f'"{required}"'), 2)
        self.assertIn(f'"{required}"', self.worker)
        self.assertEqual(
            self.plan["implementation_rebinding"]["required_plan_path"], required
        )
        identity = function_source(self.entry, "Assert-A3PlanIdentity")
        self.assertIn("$Plan.experiment_id -cne $script:A3ExperimentId", identity)
        self.assertIn("required_experiment_id", identity)
        self.assertIn("required_plan_path", identity)
        campaign = function_source(self.entry, "Invoke-A3ReplicaCampaign")
        self.assertLess(
            campaign.index("Assert-A3PlanIdentity -Plan"),
            campaign.index("Invoke-A3ProviderProbe"),
        )
        worker = function_source(self.worker, "Invoke-A3Worker")
        self.assertLess(
            worker.index("$script:A3RequiredPlanPath"),
            worker.index("Read-A1CheckedJson -Path $PlanPath"),
        )
        self.assertIn("$PlanSha256 -cne $script:A3FrozenPlanSha256", worker)
        self.assertLess(
            worker.index("Assert-A3WorkerPlan -Plan"),
            worker.index("[Activator]::CreateInstance"),
        )
        self.assertIn("$Plan.experiment_id -cne $script:A3ExperimentId",
                      function_source(self.worker, "Assert-A3WorkerPlan"))
        gate = function_source(self.entry, "Assert-A3RuntimeGate")
        for requirement in self.plan["execution_gate"]["blocking_requirements"]:
            self.assertIn(f'"{requirement}"', gate)

    def test_worker_pins_the_r2_to_r4_revision_chain(self) -> None:
        self.assertIn(f'"{PLAN_SHA256}"', self.worker)
        self.assertEqual(REVISION_PLAN_SHA256, R4_PLAN_SHA256)
        self.assertIn(f'"{R4_PLAN_SHA256}"', self.worker)
        chain = function_source(self.worker, "Assert-A3RevisionChain")
        self.assertIn('"dao_a3_allocation_maps_plan_revision"', chain)
        self.assertIn("original_plan.sha256 -cne", chain)
        self.assertIn("$script:A3FrozenPlanSha256", chain)
        worker = function_source(self.worker, "Invoke-A3Worker")
        self.assertLess(
            worker.index("Assert-A3WorkerPlan -Plan"),
            worker.index("Assert-A3RevisionChain -RepositoryRoot"),
        )
        self.assertLess(
            worker.index("Assert-A3RevisionChain -RepositoryRoot"),
            worker.index("[Activator]::CreateInstance"),
        )
        bootstrap = self.entry[self.entry.index("$sources = @("):]
        for revision, digest in (("r2", R2_PLAN_SHA256), ("r3", R3_PLAN_SHA256),
                                 ("r4", R4_PLAN_SHA256)):
            path = f"oracle/windows-dao/experiments/a3/a3-allocation-maps-{revision}.plan.json"
            with self.subTest(revision=revision):
                self.assertIn(f'"{path}"', self.worker)
                self.assertIn(f'"{digest}"', self.worker)
                self.assertIn(f'"{path}"', bootstrap)
                self.assertEqual(hashlib.sha256((ROOT.parents[1] / path).read_bytes()).hexdigest(), digest)

    def test_identical_capture_and_progress_code_is_dot_sourced_not_copied(self) -> None:
        self.assertIn('"a1/A1.PageStore.ps1"', self.page_store)
        self.assertIn('"a2/A2.Progress.ps1"', self.progress)
        for function in ("New-A2ProgressFile", "Open-A2WorkerProgress", "Add-A2ProgressRecord"):
            self.assertNotIn(f"function {function}", self.progress)
            self.assertIn(function, self.progress)
        bootstrap = self.entry[self.entry.index("$sources = @("):]
        for source in (
            "oracle/windows-dao/scripts/a3/A3.Worker.ps1",
            "oracle/windows-dao/scripts/a3/A3.PageStore.ps1",
            "oracle/windows-dao/scripts/a3/A3.Progress.ps1",
            "oracle/windows-dao/scripts/a2/A2.Progress.ps1",
            "oracle/windows-dao/scripts/a1/A1.PageStore.ps1",
            "oracle/windows-dao/scripts/a3_spec.py",
            "oracle/windows-dao/scripts/protocol_validation.py",
        ):
            with self.subTest(source=source):
                self.assertIn(f'"{source}"', bootstrap)
        for relative in re.findall(r'"(oracle/windows-dao/[A-Za-z0-9._/-]+)"', bootstrap):
            with self.subTest(path=relative):
                self.assertTrue((ROOT.parents[1] / relative).is_file())
        self.assertIn(
            "The A3 plan inherits A2's row algorithm verbatim",
            function_source(self.worker, "Get-A3Payload"),
        )
        self.assertEqual(
            self.plan["tables"]["row_algorithm"]["payload"].split(" ")[4],
            "A2|<role>|<Id",
        )

    def test_worker_shaped_observation_passes_the_a3_schema(self) -> None:
        artifacts = replica_documents(generate_synthetic_bundle(replica=1))
        observation = json.loads(artifacts["observations/replica-01.json"])
        self.assertEqual(observation["document_type"], "dao_a3_replica_observation")
        self.assertEqual(observation["experiment_id"], EXPERIMENT_ID)
        self.assertEqual(len(observation["checkpoints"]), 25)
        self.assertEqual(
            [row["checkpoint_id"] for row in observation["checkpoints"]],
            self.plan["checkpoint_design"]["checkpoint_ids"],
        )
        self.assertIs(validate_document(observation), observation)
        invalid = copy.deepcopy(observation)
        invalid["experiment_id"] = "DAO-A2-ALLOCATION-MAPS-001"
        with self.assertRaises(ValidationError):
            validate_document(invalid)

if __name__ == "__main__":
    unittest.main()
