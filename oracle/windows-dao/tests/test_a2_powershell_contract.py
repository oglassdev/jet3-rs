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
A2 = SCRIPTS / "a2"
ENTRY = SCRIPTS / "run-a2-replica.ps1"
WORKER = A2 / "A2.Worker.ps1"
PAGE_STORE = A2 / "A2.PageStore.ps1"
PROGRESS = A2 / "A2.Progress.ps1"
A1_PAGE_STORE = SCRIPTS / "a1" / "A1.PageStore.ps1"
PLAN = ROOT / "experiments" / "a2" / "a2-allocation-maps.plan.json"
SPEC = SCRIPTS / "a2_spec.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_generator_schedule import build_schedule, checkpoint_document  # noqa: E402
from a2_spec import (  # noqa: E402
    CHECKPOINT_ORDINALS,
    EXPERIMENT_ID,
    PLAN_SHA256,
    ROLE_BINDINGS,
    ROLES,
    expected_reread_sha256,
    validate_replica_observation,
)
from protocol_validation import ValidationError  # noqa: E402


def function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    end = source.find("\nfunction ", start + 1)
    return source[start:] if end < 0 else source[start:end]


class A2PowerShellContractTests(unittest.TestCase):
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
            "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2",
        )
        self.assertIn(digest, self.entry)
        self.assertIn(digest, self.worker)
        checkpoints = self.plan["checkpoint_design"]["checkpoint_ids"]
        self.assertEqual(len(checkpoints), 25)
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertIn("checkpoint_design.checkpoint_ids", self.worker)
        self.assertIn("A2 checkpoint schedule differs", self.worker)
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
        schedule = function_source(self.worker, "Invoke-A2Schedule")
        create = schedule.index("Invoke-A2WithDatabase -Create")
        tables = schedule.index('foreach ($role in @("D", "L", "P", "H"))')
        checkpoints = schedule.index("checkpoint_design.checkpoint_ids")
        self.assertLess(create, tables)
        self.assertLess(tables, checkpoints)
        self.assertIn("Add-A2Table -Role $role", schedule)

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
        growth = function_source(self.worker, "Add-A2UntilTarget")
        schedule = function_source(self.worker, "Invoke-A2Schedule")
        self.assertLess(
            growth.index("Add-A2RowBatch -Role $Role"),
            growth.index("Assert-A2Quiescent"),
        )
        self.assertLess(
            growth.index("Assert-A2Quiescent"),
            growth.index("Get-A2ClosedPageCount"),
        )
        self.assertIn("} while ($pages -lt $ThresholdPages)", growth)
        self.assertIn('if ($id -ceq "D_RECREATE_EMPTY")', schedule)
        self.assertIn('Add-A2Table -Role "D"', schedule)
        self.assertIn("$baseline + 128", schedule)
        self.assertIn("-le", schedule)
        self.assertIn("regrowth is not strictly greater", schedule)
        self.assertNotIn("row-count replay", schedule.lower())

    def test_l_full_delete_and_exact_reinsert_are_id_ordered(self) -> None:
        delete = function_source(self.worker, "Remove-A2AllLRows")
        restore = function_source(self.worker, "Restore-A2AllLRows")
        self.assertIn('$script:A1Rows["L"] | Sort-Object', delete)
        self.assertIn("SELECT Id FROM [$name] ORDER BY Id", delete)
        self.assertIn("[int]$idField.Value -ne $expectedId", delete)
        self.assertIn("$recordset.Delete()", delete)
        self.assertIn('$script:A1Rows["L"].Clear()', delete)
        self.assertIn("[int[]]@($script:A2DeletedLIds)", restore)
        self.assertIn('Add-A2Ids -Role "L" -Ids $ids', restore)
        insert = function_source(self.worker, "Add-A2Ids")
        self.assertIn("Get-A2Payload -Role $Role -Id $id", insert)

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
        self.assertIn("Get-A2Payload -Role $Role -Id $Id", self.page_store)

    def test_every_checkpoint_rereads_all_extant_tables_in_one_session(self) -> None:
        checkpoint = function_source(self.worker, "Add-A2Checkpoint")
        semantic = checkpoint.index("Read-A2SemanticTables")
        snapshot = checkpoint.index("Read-A2PageSnapshot")
        self.assertLess(semantic, snapshot)
        self.assertGreaterEqual(checkpoint[:snapshot].count("Assert-A2Quiescent"), 2)
        all_tables = function_source(self.a1_page_store, "Read-A1SemanticTables")
        one_table = function_source(self.a1_page_store, "Read-A1SemanticTable")
        self.assertEqual(all_tables.count("Invoke-A1WithDatabase -Action"), 1)
        self.assertIn('@("D", "L", "P", "H")', all_tables)
        self.assertIn("ORDER BY Id", one_table)
        self.assertIn('$idField = $fields.Item("Id")', one_table)
        self.assertIn('$payloadField = $fields.Item("Payload")', one_table)

    def test_dirty_role_digest_cache_is_preserved_for_every_mutation(self) -> None:
        for name in ("Add-A2Table", "Remove-A2Table", "Add-A2Ids"):
            with self.subTest(function=name):
                self.assertIn(
                    "Set-A2ExpectedSemanticDirty -Role $Role",
                    function_source(self.worker, name),
                )
        self.assertIn(
            'Set-A2ExpectedSemanticDirty -Role "L"',
            function_source(self.worker, "Remove-A2AllLRows"),
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
        self.assertIn("$script:A2PriorPages = [byte[]]$snapshot.pages", self.worker)

    def test_page_indexes_are_ordered_content_addressed_and_reconstructable(self) -> None:
        checkpoint = function_source(self.worker, "Add-A2Checkpoint")
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
        verify = function_source(self.entry, "Assert-A2ReplicaOutput")
        self.assertIn('"page-store/$digest.page"', verify)
        self.assertIn("cannot be reconstructed", verify)
        self.assertIn("Get-M1FileSha256 -Path $path", verify)
        self.assertIn("GetFileNameWithoutExtension", verify)

    def test_idle_reopen_and_companion_contract_is_explicit(self) -> None:
        schedule = function_source(self.worker, "Invoke-A2Schedule")
        for checkpoint in ("E0", "E0R", "L_IDLE_REOPEN", "H_IDLE_REOPEN"):
            self.assertIn(f'"{checkpoint}"', schedule)
        quiescence = function_source(self.worker, "Assert-A2Quiescent")
        self.assertIn('ChangeExtension($script:A2DatabasePath, ".ldb")', quiescence)
        self.assertIn("64KB", quiescence)
        checkpoint = function_source(self.worker, "Add-A2Checkpoint")
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
        manifest = function_source(self.worker, "Write-A2ReplicaManifest")
        for field in (
            "inventory_closed = $true",
            "hashes_verified = $true",
            "paths_closed = $true",
            "checkpoint_count = 25",
            "files = @($records)",
        ):
            self.assertIn(field, manifest)
        verify = function_source(self.entry, "Assert-A2ReplicaOutput")
        self.assertIn("$files.Count -ne ($records.Count + 1)", verify)
        self.assertIn("unexpected directory", verify)
        self.assertIn("unmanifested artifact", verify)
        for source in (manifest, verify):
            self.assertNotIn("Assert-M1NoReparseComponents -Path $path", source)
            self.assertIn("[IO.FileAttributes]::ReparsePoint", source)

    def test_worker_rechecks_process_and_environment_identity(self) -> None:
        worker = function_source(self.worker, "Invoke-A2Worker")
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
        checkpoint = function_source(self.worker, "Add-A2Checkpoint")
        observation = function_source(self.worker, "Invoke-A2Worker")
        manifest = function_source(self.worker, "Write-A2ReplicaManifest")
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
        self.assertIn('$campaignId = "a2-run-$RunId"', self.entry)
        self.assertIn("matrix_job_id", self.worker)

    def test_preflight_probe_source_and_pushed_commit_gates_precede_worker(self) -> None:
        campaign = function_source(self.entry, "Invoke-A2ReplicaCampaign")
        probe = campaign.index("Invoke-A2ProviderProbe")
        preflight = campaign.index("Invoke-M1Preflight")
        pushed = campaign.index("Assert-A2ExactPushedCommit")
        worker = campaign.index('"A2 replica worker"')
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
        campaign = function_source(self.entry, "Invoke-A2ReplicaCampaign")
        self.assertIn("Invoke-BoundedChildProcess", campaign)
        self.assertIn("-TimeoutSeconds 1700", campaign)
        self.assertIn("-ReviewedTimeoutCeilingSeconds 1700", campaign)
        self.assertIn("-MaximumOutputBytes 1MB", campaign)
        self.assertNotIn("Start-Process", campaign)

    def test_all_documents_are_semantically_validated_before_success(self) -> None:
        verify = function_source(self.entry, "Assert-A2ReplicaOutput")
        self.assertIn("A2 page-index schema validation", verify)
        self.assertIn("A2 environment validation", verify)
        self.assertIn("A2 observation validation", verify)
        self.assertIn("A2 replica manifest validation", verify)
        self.assertIn("a2_spec.py", self.entry)
        self.assertLess(
            self.entry.index("Assert-A2ReplicaOutput -Context"),
            self.entry.index("PASS: retained A2 replica"),
        )

    def test_progress_is_durable_and_outside_output(self) -> None:
        self.assertIn('Join-Path $root "progress"', self.progress)
        self.assertIn('"replica-{0:D2}.jsonl"', self.progress)
        self.assertIn("$stream.Flush($true)", self.progress)
        self.assertIn("$script:A2ProgressMaximumBytes = 1MB", self.progress)
        self.assertIn("New-A2ProgressFile", self.entry)
        self.assertIn("Open-A2WorkerProgress", self.worker)
        self.assertNotIn("OutputRoot", self.progress)

    def test_failure_document_is_minimal_and_fail_closed(self) -> None:
        failure = function_source(self.entry, "Write-A2Failure")
        self.assertIn("[ordered]@{ stage = $Stage; message = $text }", failure)
        self.assertNotIn("document_type", failure)
        catch = self.entry[self.entry.rindex("\ncatch {\n    $failure") :]
        self.assertIn("Write-A2Failure", catch)
        self.assertIn("exit 1", catch)
        self.assertIn("exit 0", self.entry)

    def test_primary_failure_is_preserved_and_failed_mdb_is_retained(self) -> None:
        retain = function_source(self.entry, "Move-A2FailedDatabase")
        campaign = function_source(self.entry, "Invoke-A2ReplicaCampaign")
        self.assertIn('"failed-replica-{0:D2}.mdb"', retain)
        self.assertIn('"replica-{0:D2}"', retain)
        self.assertIn('"ACQUISITION.MDB"', retain)
        self.assertIn("$item.Length -gt 128MB", retain)
        self.assertIn("[IO.File]::Move", retain)
        self.assertIn("catch { $primary = $_ }", campaign)
        self.assertLess(
            campaign.index("Move-A2FailedDatabase"),
            campaign.index("Remove-A2PrivateWorkingRoot"),
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

    def test_worker_shaped_observation_passes_and_bad_reinsert_fails(self) -> None:
        schedule = build_schedule()
        checkpoints = []
        for row in schedule.checkpoints:
            reference = {
                "path": (
                    f"page-indexes/replica-01/{row.ordinal:02d}-"
                    f"{row.checkpoint_id}.json"
                ),
                "sha256": hashlib.sha256(row.checkpoint_id.encode()).hexdigest(),
                "size_bytes": 128,
            }
            checkpoint = checkpoint_document(row, reference)
            checkpoint["dao_reread"] = [
                {
                    "role": role,
                    "row_count": row.table_row_counts[role],
                    "rolling_sha256": expected_reread_sha256(
                        role, row.table_row_counts[role]
                    ),
                }
                for role in ROLES
                if not (row.checkpoint_id == "D_DROP" and role == "D")
            ]
            checkpoints.append(checkpoint)
        first = checkpoints[CHECKPOINT_ORDINALS["D_GROW_0128"]]
        recreated = checkpoints[CHECKPOINT_ORDINALS["D_RECREATE_EMPTY"]]
        regrown = checkpoints[CHECKPOINT_ORDINALS["D_REGROW_0128"]]
        observation = {
            "protocol_version": "1.0.0",
            "document_type": "dao_a2_replica_observation",
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256,
            "producer_commit": "1" * 40,
            "repository_url": "https://github.com/oglassdev/jet3-rs.git",
            "campaign_id": "a2-worker-contract",
            "matrix_job": {
                "job_id": "replica-01",
                "replica_only": True,
                "shared_mutable_state": False,
            },
            "environment_sha256": "2" * 64,
            "provider_sha256": "3" * 64,
            "replica": 1,
            "role_binding": dict(ROLE_BINDINGS[0]),
            "d_growth_observation": {
                "first_baseline_pages": first["target_baseline_pages"],
                "first_target_pages": first["target_threshold_pages"],
                "first_achieved_pages": first["actual_file_pages"],
                "first_rows": first["table_row_counts"]["D"],
                "regrowth_baseline_pages": recreated["actual_file_pages"],
                "regrowth_target_pages": regrown["target_threshold_pages"],
                "regrowth_achieved_pages": regrown["actual_file_pages"],
                "regrowth_rows": regrown["table_row_counts"]["D"],
            },
            "logical_checkpoint_read_bytes": sum(
                checkpoint["actual_size_bytes"] for checkpoint in checkpoints
            ),
            "inserted_rows_total": checkpoints[-1]["inserted_rows_total"],
            "changed_hash_entries": 25,
            "checkpoints": checkpoints,
        }
        self.assertEqual(len(checkpoints), 25)
        self.assertIs(validate_replica_observation(observation), observation)

        invalid = copy.deepcopy(observation)
        reinsert = invalid["checkpoints"][
            CHECKPOINT_ORDINALS["L_REINSERT_SAME"]
        ]
        reinsert["table_row_counts"]["L"] -= 1
        reread_l = next(row for row in reinsert["dao_reread"] if row["role"] == "L")
        reread_l["row_count"] -= 1
        reread_l["rolling_sha256"] = expected_reread_sha256(
            "L", reread_l["row_count"]
        )
        self.assertNotEqual(reinsert["table_row_counts"]["L"] % 32, 0)
        with self.assertRaises(ValidationError):
            validate_replica_observation(invalid)


if __name__ == "__main__":
    unittest.main()
