from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ENTRY = SCRIPTS / "run-m4-controlled.ps1"
CONTROLLER = SCRIPTS / "m4" / "M4.Controller.ps1"
RUNTIME = SCRIPTS / "m4" / "M4.ControllerRuntime.ps1"
BUNDLE = SCRIPTS / "m4" / "M4.Bundle.ps1"
PUBLISHER = SCRIPTS / "m1" / "M1.Publication.ps1"
POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
X86_POWERSHELL = next(
    (
        candidate
        for candidate in (
            Path(os.environ.get("WINDIR", r"C:\Windows"))
            / "SysWOW64"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe",
            POWERSHELL,
        )
        if candidate.is_file()
    ),
    None,
)


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M4ControllerSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = ENTRY.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.runtime = RUNTIME.read_text(encoding="utf-8")
        cls.bundle = BUNDLE.read_text(encoding="utf-8")
        cls.publisher = PUBLISHER.read_text(encoding="utf-8")

    def test_controller_never_activates_com(self) -> None:
        combined = self.entry + self.controller + self.runtime + self.bundle
        for prohibited in (
            "[Activator]::CreateInstance",
            "[Type]::GetTypeFromProgID",
            ".OpenDatabase(",
            "$workspace.CreateDatabase(",
            "New-Object -ComObject",
        ):
            self.assertNotIn(prohibited, combined)

    def test_controller_binds_clean_pushed_private_source(self) -> None:
        for fragment in (
            "Invoke-M1Preflight",
            "remote get-url origin",
            '"ls-remote", "--heads"',
            "Assert-M1RuntimeBinding",
            "Assert-M4ExactRemoteCommit",
            "ExecutedRepoRelativeSourcePaths",
        ):
            self.assertIn(fragment, self.controller + self.runtime)
        self.assertIn("M4 requires the exact clean pushed commit", self.runtime)
        self.assertIn(
            '"oracle/windows-dao/scripts/m4/M4.Artifacts.ps1"',
            self.controller,
        )
        self.assertIn(
            '"oracle/windows-dao/scripts/m4_campaign.py"',
            self.entry,
        )
        self.assertIn(
            '"oracle/windows-dao/scripts/m4_campaign.py"',
            self.controller,
        )
        self.assertIn('"oracle/windows-dao/scripts/m4_spec.py"', self.entry)
        self.assertIn('"oracle/windows-dao/scripts/m4_spec.py"', self.controller)
        self.assertIn('"oracle/windows-dao/scripts/m4_phase.py"', self.entry)
        self.assertIn('"oracle/windows-dao/scripts/m4_phase.py"', self.controller)
        self.assertIn('"oracle/windows-dao/scripts/m4_snapshot.py"', self.entry)
        self.assertIn('"oracle/windows-dao/scripts/m4_snapshot.py"', self.controller)
        native = '"oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs"'
        self.assertIn(native, self.entry)
        self.assertIn(native, self.controller)
        bootstrap = self.entry.index("$bootstrapGit = Assert-M4BootstrapSource")
        first_source = self.entry.index(". (Join-Path")
        self.assertLess(bootstrap, first_source)
        self.assertIn("status --porcelain=v1", self.entry)
        self.assertIn('"ls-remote", "--heads"', self.entry)
        self.assertIn("Invoke-BoundedChildProcess", self.entry)
        self.assertIn("GIT_TERMINAL_PROMPT", self.entry)
        self.assertIn("GCM_INTERACTIVE", self.entry)
        source_identity = self.entry.index("hash-object -- $sourcePath")
        self.assertLess(source_identity, first_source)
        self.assertIn("[IO.FileShare]::Read", self.entry)
        self.assertIn("M4.Controller.ps1", self.entry)

    def test_controller_runs_exact_two_phase_schedule(self) -> None:
        self.assertIn("foreach ($sample in $plan.samples)", self.controller)
        self.assertIn("$creatorOrdinal = (2 *", self.controller)
        self.assertIn("$reopenOrdinal = 2 *", self.controller)
        self.assertIn("Get-M4DeterministicNonce", self.bundle)
        self.assertNotIn("$env:WINDIR", self.runtime)
        self.assertIn("[Diagnostics.Process]::GetCurrentProcess()", self.runtime)
        self.assertNotIn("[Environment]::SystemDirectory", self.runtime)
        self.assertIn('$PSVersionTable.PSEdition -cne "Desktop"', self.runtime)
        self.assertIn("Get-M1StreamSha256 -Stream $Binding.Stream", self.runtime)
        self.assertEqual(
            (self.controller + self.runtime).count("Invoke-M4CheckedPhase"),
            3,
        )
        self.assertEqual(self.controller.count("Invoke-M4PhaseWorker"), 0)
        self.assertEqual(self.runtime.count("Invoke-M4PhaseWorker"), 2)
        self.assertIn("-PriorResult $creatorResult", self.controller)
        self.assertIn("creator and reopen did not use distinct", self.controller)

    def test_clone_is_between_fresh_creator_and_reopen_launches(self) -> None:
        creator = self.controller.index(
            "$creatorResult = Invoke-M4CheckedPhase"
        )
        clone = self.controller.index("$clone = Invoke-M4BoundedClone")
        reopen = self.controller.index(
            "$reopenResult = Invoke-M4CheckedPhase",
            creator + 1,
        )
        self.assertLess(creator, clone)
        self.assertLess(clone, reopen)
        self.assertIn("completed_before_reopen_com = $true", self.controller)

    def test_every_boundary_uses_checked_validation(self) -> None:
        combined = self.controller + self.runtime
        self.assertNotIn("output_root", combined + self.bundle)
        for command in (
            '"validate-plan"',
            '"validate-invocation"',
            '"validate-result"',
            '"validate-sample"',
            '"build-analysis"',
            '"validate-bundle"',
        ):
            self.assertIn(command, combined)
        self.assertEqual(self.runtime.count('"validate-invocation"'), 1)
        self.assertEqual(self.runtime.count('"validate-result"'), 1)
        self.assertEqual(
            (self.controller + self.runtime).count("Invoke-M4CheckedPhase"),
            3,
        )
        self.assertGreaterEqual(
            self.controller.count('"validate-bundle"'), 2
        )

    def test_analysis_is_built_off_stage_then_durably_retained(self) -> None:
        self.assertIn(
            '$scratchPath = Join-Path $Session.WorkingPath "analysis-report.json"',
            self.controller,
        )
        build = self.controller.index('"build-analysis"')
        durable = self.controller.index(
            "Write-M1DurableBytes -Session $Session",
            build,
        )
        manifest = self.controller.index(
            "Add-M4ManifestEntry -Entries $Entries",
            durable,
        )
        delete = self.controller.index("[IO.File]::Delete($scratchPath)")
        self.assertLess(build, durable)
        self.assertLess(durable, manifest)
        self.assertLess(manifest, delete)
        self.assertNotIn("AnalysisScratchRoot", self.controller)
        self.assertNotIn(
            '"build-analysis", "--bundle-root",\n'
            "                $session.StagingBundle, \"--output\", "
            "$analysisOutput",
            self.controller,
        )

    def test_bundle_manifest_is_exact_and_ordinal_sorted(self) -> None:
        self.assertIn("$script:M4ExpectedPayloadFiles = 507", self.bundle)
        self.assertIn("[Array]::Sort($paths, [StringComparer]::Ordinal)", self.bundle)
        self.assertIn("sample_count = 36", self.bundle)
        self.assertIn("worker_count = 72", self.bundle)
        self.assertIn("file_count = $script:M4ExpectedPayloadFiles", self.bundle)
        self.assertIn("$entries.Count -ne 506", self.controller)
        # 3 campaign files + 36 * (2 * 6 phase files + clone + record).
        self.assertEqual(3 + 36 * (2 * 6 + 1 + 1), 507)

    def test_publication_is_collision_refusing_and_failure_is_bounded(self) -> None:
        run_id_check = self.entry.index(
            '^[0-9]{8}T[0-9]{6}Z-m4-[a-z0-9-]{1,24}$'
        )
        bootstrap = self.entry.index("$bootstrapGit = Assert-M4BootstrapSource")
        publication = self.controller.index("New-M1PublicationSession")
        self.assertLess(run_id_check, bootstrap)
        campaign_run_id_check = self.controller.index(
            '^[0-9]{8}T[0-9]{6}Z-m4-[a-z0-9-]{1,24}$'
        )
        preflight = self.controller.index("$context = Invoke-M1Preflight")
        self.assertLess(campaign_run_id_check, preflight)
        self.assertGreater(publication, 0)
        self.assertIn("New-M1PublicationSession", self.controller)
        self.assertIn("Publish-M1Stage", self.controller)
        self.assertIn("[IO.Directory]::Move(", self.publisher)
        self.assertIn("$Session.StagingBundle", self.publisher)
        self.assertIn("$Session.FinalDirectory", self.publisher)
        self.assertIn(
            "Assert-M1LocalFixedVolume -Path $Session.OutputRoot",
            self.publisher,
        )
        self.assertIn("Remove-M1PublicationStaging", self.controller)
        self.assertIn(".m4-quarantine-", self.controller)
        self.assertIn("-MaxTotalBytes 128MB", self.controller)
        self.assertIn("-MaxFileBytes 16MB", self.controller)
        for prohibited in ("Remove-Item", "[IO.Directory]::Delete($stage, $true)"):
            self.assertNotIn(prohibited, self.controller)

    def test_all_owned_files_remain_below_repository_limit(self) -> None:
        for path, source in (
            (ENTRY, self.entry),
            (CONTROLLER, self.controller),
            (RUNTIME, self.runtime),
            (BUNDLE, self.bundle),
        ):
            self.assertLess(
                len(source.splitlines()),
                800,
                f"{path.name} exceeds 800 lines",
            )


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows required")
class M4ControllerWindowsNoComTests(unittest.TestCase):
    def run_ps(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "$ErrorActionPreference='Stop';"
                "Set-StrictMode -Version Latest;"
                f". {ps_quote(BUNDLE)};"
                + body,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )

    def test_json_builder_is_bom_free_and_bounded_without_com(self) -> None:
        result = self.run_ps(
            "$bytes=ConvertTo-M4BundleJsonBytes "
            "-Document ([ordered]@{z=1;a='x'}) -MaximumBytes 128;"
            "[Console]::Write([BitConverter]::ToString($bytes))"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stdout.startswith("EF-BB-BF"))

    def test_json_builder_rejects_output_ceiling_without_com(self) -> None:
        result = self.run_ps(
            "try{ConvertTo-M4BundleJsonBytes "
            "-Document ([ordered]@{value=('x'*512)}) "
            "-MaximumBytes 32;exit 9}"
            "catch{[Console]::Error.Write($_.Exception.Message);exit 7}"
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("byte ceiling", result.stderr)

    def test_analysis_is_retained_only_after_scratch_output_completes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m4-analysis-retain-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            output = root / "evidence"
            repository.mkdir()
            result = self.run_ps(
                f". {ps_quote(SCRIPTS / 'm1' / 'M1.Publication.ps1')};"
                f". {ps_quote(CONTROLLER)};"
                "function Invoke-M4ContractCommand{"
                "param($Context,$ContractPath,$Arguments,$Label);"
                "$encoding=New-Object Text.UTF8Encoding($false,$true);"
                "[IO.File]::WriteAllText("
                "$Arguments[$Arguments.Count-1],'{\"ok\":true}`n',$encoding)};"
                f"$session=New-M1PublicationSession "
                f"-RepositoryRoot {ps_quote(repository)} "
                f"-OutputRoot {ps_quote(output)} "
                f"-GitCommit '{'2' * 40}' "
                "-RunId '20260725T120000Z-m4-analysis-test' "
                "-MaxFileBytes 16MB -MaxTotalBytes 32MB;"
                "$entries=New-Object Collections.ArrayList;"
                "[void]$entries.Add([pscustomobject]@{role='prior_payload'});"
                "try{Write-M4RetainedAnalysis "
                "-Context ([pscustomobject]@{}) -Session $session "
                "-Entries $entries -ContractPath 'unused.py';"
                "$retained=Get-M1PayloadPath -Session $session "
                "-RelativePath 'analysis/report.json';"
                "$observed=[ordered]@{"
                "retained=(Test-Path -LiteralPath $retained -PathType Leaf);"
                "scratch=(Test-Path -LiteralPath "
                "(Join-Path $session.WorkingPath 'analysis-report.json'));"
                "entries=$entries.Count;"
                "text=[IO.File]::ReadAllText($retained)};"
                "$observed|ConvertTo-Json -Compress"
                "}finally{"
                "Remove-M1PublicationStaging -Session $session"
                "}"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(observed["retained"])
            self.assertFalse(observed["scratch"])
            self.assertEqual(observed["entries"], 2)
            self.assertEqual(observed["text"], '{"ok":true}\n')

    def test_real_publication_fault_cleans_stage_and_creates_no_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m4-publish-fault-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            output = root / "evidence"
            repository.mkdir()
            commit = "1" * 40
            run_id = "20260725T120000Z-m4-fault-test"
            result = self.run_ps(
                f". {ps_quote(SCRIPTS / 'm1' / 'M1.Publication.ps1')};"
                f"$session=New-M1PublicationSession "
                f"-RepositoryRoot {ps_quote(repository)} "
                f"-OutputRoot {ps_quote(output)} "
                f"-GitCommit '{commit}' -RunId '{run_id}' "
                "-MaxFileBytes 4096 -MaxTotalBytes 8192;"
                "Write-M1DurableUtf8 -Session $session "
                "-RelativePath 'bundle-manifest.json' -Text '{}';"
                "$recheck={param($stage)$true};"
                "$validate={param($bundle)"
                "if(-not (Test-Path -LiteralPath "
                "(Join-Path $bundle 'bundle-manifest.json') -PathType Leaf))"
                "{throw 'manifest absent'}};"
                "$fault={param($phase,$stage)"
                "if($phase -ceq 'before_move'){throw 'synthetic M4 fault'}};"
                "try{Publish-M1Stage -Stage $session "
                "-RecheckScriptBlock $recheck "
                "-ValidationScriptBlock $validate "
                "-FaultInjector $fault;exit 9}"
                "catch{[Console]::Error.Write($_.Exception.Message);exit 7}"
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn("synthetic M4 fault", result.stderr)
            self.assertFalse((output / commit / run_id).exists())
            self.assertEqual(list(output.glob(".m1-stage-*")), [])

    def test_invalid_run_id_is_rejected_before_any_output_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m4-invalid-run-") as temporary:
            root = Path(temporary)
            output = root / "never-created"
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ENTRY),
                    "-RepositoryRoot",
                    str(root),
                    "-EnvironmentPath",
                    str(root / "missing-environment.json"),
                    "-OutputRoot",
                    str(output),
                    "-GitCommit",
                    "0" * 40,
                    "-RunId",
                    "not-an-m4-run",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("M4 RunId", result.stderr)
            self.assertFalse(output.exists())

            trailing_newline = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ENTRY),
                    "-RepositoryRoot",
                    str(root),
                    "-EnvironmentPath",
                    str(root / "missing-environment.json"),
                    "-OutputRoot",
                    str(output),
                    "-GitCommit",
                    "0" * 40,
                    "-RunId",
                    "20260725T120000Z-m4-valid-looking\n",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertNotEqual(trailing_newline.returncode, 0)
            self.assertIn("M4 RunId", trailing_newline.stderr)
            self.assertFalse(output.exists())


@unittest.skipUnless(
    os.name == "nt" and X86_POWERSHELL is not None,
    "x86 Windows PowerShell required",
)
class M4ControllerWindowsX86BindingTests(unittest.TestCase):
    def test_current_x86_powershell_binding_is_accepted_and_retained(self) -> None:
        assert X86_POWERSHELL is not None
        preflight = SCRIPTS / "m1" / "M1.Preflight.ps1"
        publication_paths = SCRIPTS / "m1" / "M1.PublicationPaths.ps1"
        command = (
            "$ErrorActionPreference='Stop';"
            "Set-StrictMode -Version Latest;"
            f". {ps_quote(preflight)};"
            f". {ps_quote(publication_paths)};"
            f". {ps_quote(RUNTIME)};"
            f". {ps_quote(CONTROLLER)};"
            "$binding=Get-M4WorkerPowerShellBinding;"
            "try{"
            "Assert-M4WorkerPowerShellBinding -Binding $binding;"
            "$current=[IO.Path]::GetFullPath("
            "[Diagnostics.Process]::GetCurrentProcess().MainModule.FileName);"
            "if(-not $binding.Path.Equals("
            "$current,[StringComparison]::OrdinalIgnoreCase)){"
            "throw 'binding path differs'};"
            "[Console]::Write($binding.Path)"
            "}finally{$binding.Stream.Dispose()}"
        )
        result = subprocess.run(
            [
                str(X86_POWERSHELL),
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
        self.assertEqual(
            Path(result.stdout).name.lower(),
            "powershell.exe",
        )


if __name__ == "__main__":
    unittest.main()
