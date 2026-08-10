from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ENTRY = SCRIPTS / "run-m4r1-controlled.ps1"
WORKER = SCRIPTS / "run-m4r1-phase.ps1"
MODULES = SCRIPTS / "m4r1"
CONTROLLER = MODULES / "M4R1.Controller.ps1"
RUNTIME = MODULES / "M4R1.ControllerRuntime.ps1"
BUNDLE = MODULES / "M4R1.Bundle.ps1"
QUIESCENCE = MODULES / "M4R1.Quiescence.ps1"
ARTIFACTS = MODULES / "M4R1.Artifacts.ps1"
DAO = SCRIPTS / "m4" / "M4.Dao.ps1"
WORKER_HELPERS = SCRIPTS / "m4" / "M4.Worker.ps1"
PLAN = ROOT / "experiments" / "m4r2" / "m4-header-discriminator-r2.plan.json"
POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M4R1PowerShellSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.runtime = RUNTIME.read_text(encoding="utf-8")
        cls.bundle = BUNDLE.read_text(encoding="utf-8")
        cls.quiescence = QUIESCENCE.read_text(encoding="utf-8")
        cls.artifacts = ARTIFACTS.read_text(encoding="utf-8")

    def test_revision_identity_and_paths_are_additive(self) -> None:
        combined = "\n".join(
            (self.entry, self.worker, self.controller, self.bundle, self.artifacts)
        )
        self.assertIn("DAO-M4-HEADER-DISCRIMINATOR-003", combined)
        self.assertIn("experiments/m4r2/", combined)
        self.assertIn("m4r1_contract.py", combined)
        self.assertIn("refs/heads/codex/m4r2-canonical-paths", combined)
        self.assertNotIn("refs/heads/codex/jet3-v1-foundations", combined)
        self.assertNotIn("refs/heads/codex/m4r1-companion-aware", combined)
        self.assertIn("run-m4r1-phase.ps1", self.controller)

    def test_companion_locator_preserves_uppercase_database_basename(self) -> None:
        self.assertIn('$artifactBase = $PhaseId.ToUpperInvariant()', self.bundle)
        for suffix in (
            "-worker-result.json",
            "-operation-log.json",
            "-snapshot.json",
            ".prefix.bin",
            ".ldb",
        ):
            self.assertIn(f'$root/$artifactBase{suffix}', self.bundle)

    def test_worker_requires_only_pre_com_companion_absence(self) -> None:
        self.assertEqual(
            self.worker.count("Assert-M4LockFileAbsent -DatabasePath"), 1
        )
        lock_check = self.worker.index("Assert-M4LockFileAbsent -DatabasePath")
        dao_phase = self.worker.index("Invoke-M4DaoPhase")
        observation = self.worker.index("Get-M4ClosedFileObservation", dao_phase)
        self.assertLess(lock_check, dao_phase)
        self.assertLess(dao_phase, observation)
        self.assertNotIn("ldb_absence_verified", self.worker)
        self.assertNotIn("lock_file_absent_after_close", self.artifacts)

    def test_controller_owns_post_worker_quiescence(self) -> None:
        phase_launch = self.runtime.index("Invoke-M4PhaseWorker")
        result_validation = self.runtime.index('"validate-result"')
        observation = self.runtime.index("New-M4R1PostWorkerQuiescence")
        quiescence_validation = self.runtime.index('"validate-quiescence"')
        self.assertLess(phase_launch, result_validation)
        self.assertLess(result_validation, observation)
        self.assertLess(observation, quiescence_validation)
        self.assertIn("worker_exit_wait_completed = $true", self.quiescence)
        self.assertIn("matches_worker_post_close_observation = $true", self.quiescence)

    def test_companion_is_bounded_exclusive_and_never_mutated(self) -> None:
        for fragment in (
            "$script:M4R1MaximumCompanionBytes = 65536",
            "[IO.FileShare]::None",
            "[IO.FileAttributes]::ReparsePoint",
            "NumberOfLinks",
            'state = "absent"',
            'state = "present"',
            '-Role "companion"',
        ):
            self.assertIn(fragment, self.quiescence)
        for prohibited in (
            "Remove-Item",
            "Delete(",
            "Move(",
            "FileMode]::Create",
            "FileMode]::Truncate",
            "FileAccess]::Write",
        ):
            self.assertNotIn(prohibited, self.quiescence)

    def test_database_is_independently_reopened_and_exactly_matched(self) -> None:
        self.assertIn("Get-M4ClosedFileObservation", self.quiescence)
        self.assertIn("$database.bytes -ne", self.quiescence)
        self.assertIn("$database.sha256 -cne", self.quiescence)
        self.assertIn("$database.prefix_sha256 -cne", self.quiescence)
        self.assertIn("database drifted after the worker", self.quiescence)

    def test_variable_manifest_topology_is_checked(self) -> None:
        self.assertIn("$script:M4MinimumPayloadFiles = 579", self.bundle)
        self.assertIn("$script:M4MaximumPayloadFiles = 651", self.bundle)
        self.assertIn("file_count = [int]$Entries.Count", self.bundle)
        self.assertIn("$entries.Count -lt 578", self.controller)
        self.assertIn("$entries.Count -gt 650", self.controller)

    def test_manifest_registrar_accepts_revision_roles(self) -> None:
        self.assertIn('"post_worker_quiescence", "companion"', self.bundle)

    def test_new_production_files_remain_below_limit(self) -> None:
        for path in (ENTRY, WORKER, *MODULES.glob("*.ps1")):
            with self.subTest(path=path.name):
                self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 800)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class M4R1PowerShellWindowsNoComTests(unittest.TestCase):
    def run_ps(self, body: str) -> subprocess.CompletedProcess[str]:
        command = (
            "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"
            "function Assert-M1NoReparseComponents{param($Path);"
            "$item=Get-Item -LiteralPath $Path -Force;"
            "if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint)-ne 0)"
            "{throw 'reparse'}};"
            f". {ps_quote(DAO)};. {ps_quote(BUNDLE)};"
            f". {ps_quote(QUIESCENCE)};. {ps_quote(WORKER_HELPERS)};"
            "$script:M4RepositoryUrl='https://github.com/oglassdev/jet3-rs.git';"
            "$script:M4RemoteRef='refs/heads/codex/m4r2-canonical-paths';"
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
            timeout=20,
        )

    def test_exclusive_observation_hashes_empty_companion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m4r1-companion-") as temporary:
            companion = Path(temporary) / "creator.ldb"
            companion.write_bytes(b"")
            result = self.run_ps(
                f"$o=Get-M4R1ExclusiveFileObservation -Path {ps_quote(companion)} "
                "-MaximumBytes 65536 -Label 'test companion';"
                "[Console]::Write(('{0}|{1}|{2}' -f "
                "$o.bytes,$o.sha256,$o.file_identity.link_count))"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            size, digest, links = result.stdout.split("|")
            self.assertEqual(size, "0")
            self.assertEqual(
                digest,
                "e3b0c44298fc1c149afbf4c8996fb924"
                "27ae41e4649b934ca495991b7852b855",
            )
            self.assertEqual(links, "1")

    def test_uppercase_paths_pass_worker_plan_projection(self) -> None:
        result = self.run_ps(
            f"$plan=Get-Content -LiteralPath {ps_quote(PLAN)} -Raw|ConvertFrom-Json;"
            "$sample=$plan.samples[0];$condition=$plan.conditions[0];"
            "$paths=Get-M4PhasePaths -SampleId $sample.sample_id -PhaseId creator;"
            "$invocation=[pscustomobject]@{condition_id=$sample.condition_id;"
            "sample_id=$sample.sample_id;phase_id='creator';worker_ordinal=1;"
            "database_path=$sample.creator_database_path;result_path=$paths.result;"
            "phase_contract=[pscustomobject]@{kind='creator';"
            "method='DBEngine.CreateDatabase';locale=$plan.design.locale;"
            "version_option=$condition.version_option;"
            "version_api_value=$condition.version_api_value;"
            "encryption_option=$condition.encryption_option;"
            "encryption_api_value=$condition.encryption_api_value;"
            "create_option_value=$condition.create_option_value;"
            "expected_dao_version=$condition.expected_dao_version;"
            "compact_database_used=$false}};"
            "Assert-M4PlanProjection -Invocation $invocation -Plan $plan;"
            "[Console]::Write($paths.result)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith("/CREATOR-worker-result.json"))

    def test_exclusive_observation_rejects_oversized_companion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m4r1-oversize-") as temporary:
            companion = Path(temporary) / "creator.ldb"
            companion.write_bytes(b"x" * 65537)
            result = self.run_ps(
                f"try{{Get-M4R1ExclusiveFileObservation -Path {ps_quote(companion)} "
                "-MaximumBytes 65536 -Label 'test companion';exit 9}"
                "catch{[Console]::Error.Write($_.Exception.Message);exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn("byte bound", result.stderr)


if __name__ == "__main__":
    unittest.main()
