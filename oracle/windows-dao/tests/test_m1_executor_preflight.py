import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ORACLE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ORACLE_ROOT.parents[1]
MODULE = ORACLE_ROOT / "scripts" / "m1" / "M1.Preflight.ps1"
PROVIDER_MODULE = ORACLE_ROOT / "scripts" / "m1" / "M1.Provider.ps1"
MODULE_RELATIVE = "oracle/windows-dao/scripts/m1/M1.Preflight.ps1"
POWERSHELL = shutil.which("powershell.exe")


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class M1PreflightFunctionalTests(unittest.TestCase):
    def invoke_failure(
        self,
        *,
        repository: Path,
        environment: Path,
        output: Path,
        commit: str,
        run_id: str = "20260724T120000Z-dao-m1",
    ) -> dict[str, str]:
        command = f"""
. {ps_literal(MODULE)}
try {{
    $null = Invoke-M1Preflight `
        -RepositoryRoot {ps_literal(repository)} `
        -EnvironmentPath {ps_literal(environment)} `
        -OutputRoot {ps_literal(output)} `
        -GitCommit {ps_literal(commit)} `
        -RunId {ps_literal(run_id)} `
        -ExecutedRepoRelativeSourcePaths @({ps_literal(MODULE_RELATIVE)})
    throw "preflight unexpectedly succeeded"
}}
catch {{
    @{{
        category = [string]$_.Exception.Data["M1Category"]
        message = [string]$_.Exception.Message
    }} | ConvertTo-Json -Compress
}}
"""
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_invalid_commit_is_categorized_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.json"
            environment.write_text("{}\n", encoding="utf-8")
            output = root / "never-created"
            result = self.invoke_failure(
                repository=REPOSITORY_ROOT,
                environment=environment,
                output=output,
                commit="not-a-commit",
            )
            self.assertEqual(result["category"], "Invocation")
            self.assertIn("40 lowercase hexadecimal", result["message"])
            self.assertFalse(output.exists())

    def test_oversized_environment_is_rejected_before_json_or_com(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.json"
            environment.write_bytes(b" " * (1_048_576 + 1))
            output = root / "output"
            result = self.invoke_failure(
                repository=REPOSITORY_ROOT,
                environment=environment,
                output=output,
                commit="0" * 40,
            )
            self.assertEqual(result["category"], "Invocation")
            self.assertIn("exceeds the 1048576-byte limit", result["message"])
            self.assertFalse(output.exists())

    def test_output_inside_repository_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / "environment.json"
            environment.write_text("{}\n", encoding="utf-8")
            output = REPOSITORY_ROOT / "artifacts" / "forbidden-m1-preflight"
            self.assertFalse(output.exists())
            result = self.invoke_failure(
                repository=REPOSITORY_ROOT,
                environment=environment,
                output=output,
                commit="0" * 40,
            )
            self.assertEqual(result["category"], "Invocation")
            self.assertIn("outside the repository", result["message"])
            self.assertFalse(output.exists())

    def test_loaded_byte_digest_rejects_post_validation_swap(self) -> None:
        command = f"""
. {ps_literal(MODULE)}
$accepted = [Text.Encoding]::UTF8.GetBytes("accepted")
$expected = Get-M1ByteArraySha256 -Bytes $accepted
$swapped = [Text.Encoding]::UTF8.GetBytes("swapped")
try {{
    Assert-M1ByteArraySha256 -Bytes $swapped `
        -ExpectedSha256 $expected -Label "checked input"
    throw "digest check unexpectedly succeeded"
}}
catch {{
    @{{
        category = [string]$_.Exception.Data["M1Category"]
        message = [string]$_.Exception.Message
    }} | ConvertTo-Json -Compress
}}
"""
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["category"], "Blocked")
        self.assertIn("differ from the bound digest", result["message"])

    def test_environment_record_field_drift_is_blocked(self) -> None:
        command = f"""
. {ps_literal(MODULE)}
$recorded = [pscustomobject]@{{ os_build = "old-build" }}
$current = [ordered]@{{ os_build = "new-build" }}
try {{
    Assert-M1ExactRecordFields -Recorded $recorded -Current $current `
        -Fields @("os_build") -Label "Host environment"
    throw "record comparison unexpectedly succeeded"
}}
catch {{
    @{{
        category = [string]$_.Exception.Data["M1Category"]
        message = [string]$_.Exception.Message
    }} | ConvertTo-Json -Compress
}}
"""
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["category"], "Blocked")
        self.assertIn("os_build", result["message"])

    def test_python_discovery_skips_non_runnable_application_shims(self) -> None:
        command = f"""
. {ps_literal(MODULE)}
$selected = Get-M1Python3
[ordered]@{{
    executable = $selected.Executable
    version = $selected.Version
}} | ConvertTo-Json -Compress
"""
        environment = os.environ.copy()
        environment["PATH"] = (
            str(Path(sys.executable).parent)
            + os.pathsep
            + environment.get("PATH", "")
        )
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        selected = Path(result["executable"])
        self.assertTrue(selected.is_file())
        self.assertTrue(result["version"].startswith("3."))
        self.assertNotIn(
            "Microsoft/WindowsApps",
            selected.as_posix(),
        )


class M1PreflightSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MODULE.read_text(encoding="utf-8")
        cls.provider_source = PROVIDER_MODULE.read_text(encoding="utf-8")
        cls.all_source = cls.source + "\n" + cls.provider_source

    def test_module_has_pure_preflight_recheck_and_close_api(self) -> None:
        self.assertIn("function Invoke-M1Preflight", self.source)
        self.assertIn("function Assert-M1PreflightCurrent", self.source)
        self.assertIn("function Assert-M1RuntimeBinding", self.source)
        self.assertIn("function Close-M1PreflightContext", self.source)
        self.assertNotIn("exit ", self.source)

    def test_context_exposes_runner_inputs(self) -> None:
        for property_name in (
            "Repository",
            "EnvironmentPath",
            "EnvironmentBytes",
            "OutputRoot",
            "FinalDirectory",
            "InventoryPath",
            "InventoryBytes",
            "InventorySha256",
            "Inventory",
            "ValidatorPath",
            "PythonPath",
            "AcceptedProvider",
            "GitCommit",
            "RunId",
        ):
            self.assertIn(f"{property_name} =", self.source)

    def test_module_cannot_activate_com_or_mutate_output(self) -> None:
        prohibited = (
            "[Activator]::CreateInstance",
            "New-Object -ComObject",
            ".CreateDatabase(",
            ".OpenDatabase(",
            "[IO.Directory]::CreateDirectory",
            "[IO.Directory]::Move",
            "[IO.File]::Move",
            "[IO.File]::Copy",
            "New-Item",
            "Remove-Item",
        )
        for fragment in prohibited:
            self.assertNotIn(fragment, self.all_source)

    def test_external_json_is_bounded_and_locked(self) -> None:
        self.assertIn("$script:M1MaximumJsonBytes = 1048576L", self.source)
        self.assertIn("[IO.FileShare]::Read", self.source)
        self.assertIn("Read-M1StreamBytes", self.source)
        self.assertIn("Get-M1StreamSha256", self.source)

    def test_all_checked_protocol_inputs_are_git_bound(self) -> None:
        self.assertIn("Assert-M1GitBoundPath", self.source)
        self.assertIn("protocol_cli.py", self.source)
        self.assertIn("protocol_validation.py", self.source)
        self.assertIn("m1_bundle_validation.py", self.source)
        self.assertIn("validate_m1_protocol.py", self.source)
        self.assertIn("M1.Provider.ps1", self.source)
        self.assertIn('(Join-Path $PSScriptRoot "M1.Provider.ps1")', self.source)
        self.assertEqual(self.source.count(".schema.json\""), 8)
        expected_examples = (
            "DAO-GEN-BINARY-MARKER-001.scenario.json",
            "DAO-GEN-EMPTY-REPEAT-A.scenario.json",
            "DAO-GEN-EMPTY-REPEAT-B.scenario.json",
            "DAO-GEN-LONGBINARY-LADDER-001.scenario.json",
            "DAO-GEN-MEMO-LADDER-001.scenario.json",
            "DAO-GEN-TEXT8-BASELINE-001.scenario.json",
            "DAO-GEN-TEXT8-INDEXED-001.scenario.json",
            "DAO-PAIR-EMPTY-REPEAT-001.pair.json",
            "DAO-PAIR-TEXT8-INDEX-001.pair.json",
        )
        for name in expected_examples:
            self.assertIn(name, self.source)

    def test_provider_policy_is_exact_x86_exp_0006_identity(self) -> None:
        required = (
            "EXP-0006",
            '"DAO.DBEngine.36"',
            '"{00000100-0000-0010-8000-00AA006D2EA4}"',
            '"03.60.9765.0"',
            "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac",
            "[IntPtr]::Size -ne 4",
            "Get-M1RegistryRegistration",
            "A user registration shadows",
        )
        for fragment in required:
            self.assertIn(fragment, self.all_source)
        for field in (
            "os_caption",
            "os_version",
            "os_build",
            "os_architecture",
            "utc_offset",
        ):
            self.assertIn(f'"{field}"', self.provider_source)

    def test_paths_reject_reparse_unc_ads_alias_and_repo_output(self) -> None:
        required = (
            "Assert-M1NoReparseAncestors",
            "[IO.FileAttributes]::ReparsePoint",
            "cannot be a UNC path",
            "cannot name an alternate stream",
            "EnvironmentPath cannot alias OutputRoot",
            "OutputRoot must remain outside the repository",
        )
        for fragment in required:
            self.assertIn(fragment, self.source)


if __name__ == "__main__":
    unittest.main()
