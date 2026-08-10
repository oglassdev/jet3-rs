from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run-m4-phase.ps1"
MODULE = ROOT / "scripts" / "m4" / "M4.Dao.ps1"
WORKER_MODULE = ROOT / "scripts" / "m4" / "M4.Worker.ps1"
ARTIFACTS_MODULE = ROOT / "scripts" / "m4" / "M4.Artifacts.ps1"
DAO_VALUES = ROOT / "scripts" / "m1" / "M1.DaoValues.ps1"
WINDOWS_ROOT = Path(os.environ.get("WINDIR", r"C:\Windows"))
POWERSHELL_CANDIDATES = (
    WINDOWS_ROOT
    / "SysWOW64"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe",
    WINDOWS_ROOT
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe",
)
POWERSHELL = next(
    (candidate for candidate in POWERSHELL_CANDIDATES if candidate.is_file()),
    POWERSHELL_CANDIDATES[0],
)


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M4PhaseSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = WORKER.read_text(encoding="utf-8")
        cls.worker_module = WORKER_MODULE.read_text(encoding="utf-8")
        cls.artifacts = ARTIFACTS_MODULE.read_text(encoding="utf-8")
        cls.worker = cls.entry + "\n" + cls.worker_module + "\n" + cls.artifacts
        cls.module = MODULE.read_text(encoding="utf-8")

    def test_worker_has_exact_bundle_validator_hook_before_com(self) -> None:
        for fragment in (
            "[string]$BundleRoot",
            "[string]$InvocationPath",
            '"validate-invocation" "--bundle-root" $bundle',
            '"--invocation" $invocationFile',
            "Resolve-M4BundleLocator",
            "Test-M4WorkerPathWithin",
        ):
            self.assertIn(fragment, self.worker)
        self.assertLess(
            self.worker.index('"validate-invocation"'),
            self.worker.index("Invoke-M4DaoPhase"),
        )
        self.assertNotIn("Get-Location", self.worker)
        self.assertNotIn("Set-Location", self.worker)
        self.assertNotIn("output_root", self.worker)
        for fragment in (
            "-Path ([string]$Invocation.repository_root)",
            "-Path ([string]$Invocation.stage_root)",
            "BundleRoot differs from the invocation stage_root binding.",
            "Executing repository differs from the invocation binding.",
            "M4.Worker.ps1",
            "M4.Artifacts.ps1",
        ):
            self.assertIn(fragment, self.worker)

    def test_bootstrap_binds_sources_before_loading_any_helper(self) -> None:
        for fragment in (
            "$bootstrapHead -cne $bootstrapCommit",
            "status --porcelain=v1",
            "rev-parse",
            "hash-object",
            "$actualObject -cne $expectedObject",
        ):
            self.assertIn(fragment, self.entry)
        first_load = self.entry.index('. (Join-Path $m1Root "M1.Preflight.ps1")')
        self.assertLess(self.entry.index("hash-object"), first_load)
        self.assertLess(first_load, self.entry.index('"validate-invocation"'))

    def test_worker_rebinds_commit_environment_remote_and_provider(self) -> None:
        for fragment in (
            "Assert-M1GitState",
            "Assert-M1GitBoundPath",
            '"ls-remote", "--heads"',
            "Invoke-BoundedChildProcess",
            "GIT_TERMINAL_PROMPT",
            "-TimeoutSeconds 30",
            "Assert-M1ProviderEnvironment",
            "Assert-M1CurrentRegistration",
            "Get-M1StreamSha256",
            "$providerStream",
            "$EnvironmentInput.Stream",
            "$InvocationInput.Stream",
        ):
            self.assertIn(fragment, self.worker)
        self.assertLess(
            self.worker.index("Assert-M1CurrentRegistration"),
            self.worker.index("Invoke-M4DaoPhase"),
        )

    def test_checked_validator_transitive_sources_are_commit_bound(self) -> None:
        for relative in (
            "scripts/m4_contract.py",
            "scripts/m4/M4.Worker.ps1",
            "scripts/m4/M4.Artifacts.ps1",
            "scripts/shared/BoundedProcess.ps1",
            "scripts/m4_records.py",
            "scripts/m4_bundle.py",
            "scripts/m4_campaign.py",
            "scripts/m4_spec.py",
            "scripts/m4_phase.py",
            "scripts/m4_snapshot.py",
            "scripts/m4_analysis.py",
            "scripts/shared/BoundedProcess.Native.cs",
            "scripts/m1_bundle_validation.py",
            "scripts/protocol_validation.py",
            "scripts/protocol_cli.py",
            "scripts/validate_m1_protocol.py",
            "experiments/m4/m4-header-discriminator.plan.json",
            "experiments/m4/plan.schema.json",
            "experiments/m4/invocation.schema.json",
            "experiments/m4/worker-result.schema.json",
            "experiments/m4/operation-log.schema.json",
            "experiments/m4/snapshot.schema.json",
            "experiments/m4/clone-log.schema.json",
            "experiments/m4/sample-record.schema.json",
            "experiments/m4/analysis-report.schema.json",
            "experiments/m4/bundle-manifest.schema.json",
            "protocol/v1_1/environment.schema.json",
        ):
            self.assertIn(f"oracle/windows-dao/{relative}", self.worker)

    def test_dao_activation_has_no_hidden_collection_rcw(self) -> None:
        for fragment in (
            "[Type]::GetTypeFromProgID(",
            "[string]$AcceptedProvider.prog_id",
            "$false",
            "if ($null -eq $providerType)",
            "$actualClsid",
            "[Activator]::CreateInstance($providerType)",
            "$engineVersion = [string]$engine.Version",
            "[string]$AcceptedProvider.provider_version",
            "$workspaces = $engine.Workspaces",
            "$workspace = $workspaces.Item([int]0)",
        ):
            self.assertIn(fragment, self.module)
        self.assertNotIn("$engine.Workspaces.Item", self.module)
        release_order = [
            'Label "DAO Database release"',
            'Label "DAO Workspace release"',
            'Label "DAO Workspaces release"',
            'Label "DAO DBEngine release"',
        ]
        positions = [self.module.index(item) for item in release_order]
        self.assertEqual(positions, sorted(positions))

    def test_creator_uses_exact_direct_create_call_and_no_compaction(self) -> None:
        create_call = (
            "$database = $workspace.CreateDatabase(\n"
            "                $DatabasePath,\n"
            "                [string]$PhaseContract.locale,\n"
            "                [int]$PhaseContract.create_option_value\n"
            "            )"
        )
        self.assertIn(create_call, self.module)
        for source in (self.worker, self.module):
            self.assertNotIn("CompactDatabase", source)
            self.assertNotIn("dbVersion30", source)
        self.assertIn(
            "$database = $workspace.OpenDatabase($DatabasePath)",
            self.module,
        )

    def test_version_and_empty_schema_are_observed_while_open(self) -> None:
        for fragment in (
            "$version = [string]$database.Version",
            "$version -cne",
            "$tableDefinitions = $Database.TableDefs",
            "$tableDefinitions.Refresh()",
            "$count = [int]$tableDefinitions.Count",
            "$script:M4MaximumTableDefinitions = 32",
            "for ($index = 0; $index -lt $count; $index++)",
            "$tableDefinitions.Item([int]$index)",
            "$script:M4SystemTableMask = -2147483646",
            "if ($userTableCount -ne 0)",
        ):
            self.assertIn(fragment, self.module)
        snapshot = self.module.index("$snapshot = [ordered]@{")
        self.assertLess(self.module.index('Action "empty_schema_read"'), snapshot)
        self.assertLess(snapshot, self.module.index('Action "database_closed"'))

    def test_all_dao_rcws_are_released_deterministically(self) -> None:
        for label in (
            "DAO TableDef release",
            "DAO TableDefs release",
            "DAO Database release",
            "DAO Workspace release",
            "DAO Workspaces release",
            "DAO DBEngine release",
        ):
            self.assertIn(label, self.module)
        self.assertIn("FinalReleaseComObject", DAO_VALUES.read_text(encoding="utf-8"))
        self.assertEqual(self.module.count("[GC]::Collect()"), 1)
        self.assertEqual(self.module.count("[GC]::WaitForPendingFinalizers()"), 1)

    def test_reopen_has_exact_pre_com_file_and_clone_binding(self) -> None:
        for fragment in (
            "$cloneInput.Sha256 -cne",
            "phase_contract.clone_log.sha256",
            "$preCom = Get-M4ClosedFileObservation",
            "phase_contract.pre_com_database_bytes",
            "phase_contract.pre_com_database_sha256",
            "Assert-M4ReopenCloneBinding",
            "source_file_identity",
            "destination_file_identity",
            "live destination identity",
            'Action "clone_verified"',
        ):
            self.assertIn(fragment, self.worker)
        self.assertLess(
            self.worker.index('Action "clone_verified"'),
            self.worker.index("Invoke-M4DaoPhase"),
        )

    def test_closed_file_observation_is_exclusive_bounded_and_one_pass(self) -> None:
        for fragment in (
            "$script:M4MaximumDatabaseBytes = 1MB",
            "$script:M4PrefixBytes = 2048",
            "[IO.FileShare]::None",
            "[IO.FileOptions]::SequentialScan",
            "$hash.TransformBlock(",
            "$hash.TransformFinalBlock(",
            "$prefixOffset -ne $script:M4PrefixBytes",
        ):
            self.assertIn(fragment, self.module)
        self.assertNotIn("ReadAllBytes", self.module)
        self.assertNotIn("MemoryStream", self.module)

    def test_lock_file_is_only_asserted_absent_and_never_removed(self) -> None:
        self.assertIn(
            "[IO.Path]::ChangeExtension($DatabasePath, \".ldb\")",
            self.module,
        )
        self.assertGreaterEqual(
            self.worker.count("Assert-M4LockFileAbsent -DatabasePath"),
            2,
        )
        lock_function = self.module[
            self.module.index("function Assert-M4LockFileAbsent") :
            self.module.index("function Read-M4EmptyUserSchema")
        ]
        self.assertNotIn("Delete", lock_function)
        self.assertNotIn("Remove-Item", lock_function)

    def test_artifacts_are_bounded_create_new_and_schema_shaped(self) -> None:
        for fragment in (
            "[IO.FileMode]::CreateNew",
            "[IO.FileOptions]::WriteThrough",
            "$stream.Flush($true)",
            "dao_m4_worker_result",
            "dao_m4_operation_log",
            "dao_m4_empty_schema_version_snapshot",
            "pre_com_file_binding",
            "post_close_file_observations",
            "prefix_bytes = 2048",
            "lock_file_absent_after_close",
            "invocation_sha256",
            "execution_status = \"pass\"",
        ):
            self.assertIn(fragment, self.worker + self.module)
        self.assertNotIn("Remove-M4CreatedArtifacts", self.worker)
        for action in (
            "bindings_verified",
            "clone_verified",
            "com_activated",
            "database_created",
            "database_opened",
            "version_read",
            "empty_schema_read",
            "database_closed",
            "ldb_absence_verified",
            "prefix_observed",
        ):
            self.assertIn(action, self.worker + self.module)
        for suffix in (
            '"-worker-result.json"',
            '".prefix.bin"',
            '"-operation-log.json"',
            '"-snapshot.json"',
        ):
            self.assertIn(suffix, self.worker)

    def test_failure_is_structured_and_never_uses_m1_scenario_mutators(self) -> None:
        self.assertIn('"dao_m4_worker_error"', self.worker)
        self.assertIn("[Console]::Error.WriteLine(", self.worker)
        self.assertIn("Get-M1ExceptionRecord", self.worker)
        self.assertIn("-CleanupErrors $CleanupErrors", self.worker)
        for prohibited in (
            "Invoke-M1DaoScenario",
            "Flush-M1DaoDatabase",
            "Sync-M1DurableFile",
            "Publish-M1",
            "M1.Dao.ps1",
        ):
            self.assertNotIn(prohibited, self.worker)
            self.assertNotIn(prohibited, self.module)

    def test_assigned_sources_stay_below_repository_file_limit(self) -> None:
        self.assertLess(len(self.entry.splitlines()), 550)
        self.assertLess(len(self.worker_module.splitlines()), 650)
        self.assertLess(len(self.module.splitlines()), 800)
        self.assertLess(len(self.artifacts.splitlines()), 300)

    def test_result_is_last_commit_marker_and_failure_is_tombstoned(self) -> None:
        writes = [
            self.entry.index("Write-M4CreateNewBytes -Path $prefixPath"),
            self.entry.index("Write-M4CreateNewBytes -Path $snapshotPath"),
            self.entry.index("Write-M4CreateNewBytes -Path $logPath"),
            self.entry.index("Write-M4CreateNewBytes -Path $resultPath"),
        ]
        self.assertEqual(writes, sorted(writes))
        self.assertIn("-worker-failure.json", self.worker_module)
        self.assertIn("Write-M4FailureTombstone", self.artifacts)
        self.assertIn("[IO.FileMode]::CreateNew", self.module)
        self.assertNotIn("[IO.FileMode]::Create,", self.module)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class M4PhaseWindowsHelperTests(unittest.TestCase):
    def run_ps(self, body: str) -> subprocess.CompletedProcess[str]:
        command = (
            "$ErrorActionPreference='Stop';"
            "Set-StrictMode -Version Latest;"
            f". {ps_quote(DAO_VALUES)};"
            f". {ps_quote(MODULE)};"
            + body
        )
        return subprocess.run(
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
        )

    def test_all_worker_sources_parse_in_windows_powershell_51(self) -> None:
        paths = (WORKER, MODULE, WORKER_MODULE, ARTIFACTS_MODULE)
        statements = []
        for path in paths:
            statements.append(
                "$tokens=$null;$errors=$null;"
                "[Management.Automation.Language.Parser]::ParseFile("
                f"{ps_quote(path)},[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count-ne 0){"
                "[Console]::Error.WriteLine(($errors|Out-String));exit 7}"
            )
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "".join(statements),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_operation_entry_accepts_empty_collection(self) -> None:
        result = self.run_ps(
            "$entries=New-Object Collections.ArrayList;"
            "Add-M4OperationEntry -Entries $entries -Action 'bindings_verified';"
            "[ordered]@{count=$entries.Count;sequence=$entries[0].sequence;"
            "action=$entries[0].action;status=$entries[0].status}|"
            "ConvertTo-Json -Compress"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.strip().splitlines()[-1]),
            {
                "count": 1,
                "sequence": 1,
                "action": "bindings_verified",
                "status": "pass",
            },
        )

    def test_closed_file_observation_returns_exact_prefix_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "closed.mdb"
            payload = bytes(range(256)) * 16
            database.write_bytes(payload)
            result = self.run_ps(
                "$result=Get-M4ClosedFileObservation "
                f"-DatabasePath {ps_quote(database)} -MaximumBytes 4096;"
                "[ordered]@{bytes=$result.bytes;sha256=$result.sha256;"
                "prefix_sha256=$result.prefix_sha256;"
                "prefix_length=$result.prefix.Length}|"
                "ConvertTo-Json -Compress"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(observed["bytes"], len(payload))
            self.assertEqual(
                observed["sha256"], hashlib.sha256(payload).hexdigest()
            )
            self.assertEqual(observed["prefix_length"], 2048)
            self.assertEqual(
                observed["prefix_sha256"],
                hashlib.sha256(payload[:2048]).hexdigest(),
            )

    def test_create_new_writer_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.bin"
            destination.write_bytes(b"retained")
            result = self.run_ps(
                "try{Write-M4CreateNewBytes "
                f"-Path {ps_quote(destination)} "
                "-Bytes ([byte[]](1,2,3));exit 9}catch{exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(destination.read_bytes(), b"retained")


FUNCTIONAL_BUNDLE = os.environ.get("JET3_M4_FUNCTIONAL_BUNDLE_ROOT")
FUNCTIONAL_INVOCATION = os.environ.get("JET3_M4_FUNCTIONAL_INVOCATION")


@unittest.skipUnless(
    os.name == "nt"
    and POWERSHELL.is_file()
    and FUNCTIONAL_BUNDLE
    and FUNCTIONAL_INVOCATION,
    "checked Windows DAO M4 host and private fixture required",
)
class M4PhaseWindowsDaoFunctionalTests(unittest.TestCase):
    def test_checked_phase_worker_passes(self) -> None:
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WORKER),
                "-BundleRoot",
                str(FUNCTIONAL_BUNDLE),
                "-InvocationPath",
                str(FUNCTIONAL_INVOCATION),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
