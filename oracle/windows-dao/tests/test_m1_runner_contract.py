import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-m1-controlled.ps1"
DAO = ROOT / "scripts" / "m1" / "M1.Dao.ps1"
VALUES = ROOT / "scripts" / "m1" / "M1.DaoValues.ps1"
BUNDLE = ROOT / "scripts" / "m1" / "M1.Bundle.ps1"
PUBLICATION = ROOT / "scripts" / "m1" / "M1.Publication.ps1"
POWERSHELL = shutil.which("powershell.exe")


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class M1RunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = RUNNER.read_text(encoding="utf-8")
        cls.dao = DAO.read_text(encoding="utf-8")
        cls.values = VALUES.read_text(encoding="utf-8")
        cls.bundle = BUNDLE.read_text(encoding="utf-8")

    def test_runner_has_pure_preflight_and_two_identity_rechecks(self) -> None:
        self.assertIn("Invoke-M1Preflight", self.runner)
        self.assertGreaterEqual(self.runner.count("Assert-M1RuntimeBinding"), 2)
        first_stage = self.runner.index("New-M1PublicationSession")
        first_com = self.runner.index("Invoke-M1DaoScenario")
        self.assertLess(first_stage, first_com)
        self.assertIn("Close-M1PreflightContext", self.runner)

    def test_loaded_bytes_are_digest_bound_before_output_or_com(self) -> None:
        inventory_check = self.runner.index(
            'Assert-M1ByteArraySha256 -Bytes $inventoryInput.bytes'
        )
        example_check = self.runner.index(
            'Assert-M1ByteArraySha256 -Bytes $loaded.bytes'
        )
        first_stage = self.runner.index("New-M1PublicationSession")
        first_com = self.runner.index("Invoke-M1DaoScenario")
        self.assertLess(inventory_check, first_stage)
        self.assertLess(example_check, first_stage)
        self.assertLess(example_check, first_com)
        self.assertIn("[string]$entry.sha256", self.runner)

    def test_every_executed_transitive_source_is_git_bound(self) -> None:
        required = {
            "M1.Preflight.ps1",
            "M1.Provider.ps1",
            "M1.Publication.ps1",
            "M1.PublicationPaths.ps1",
            "M1.Dao.ps1",
            "M1.DaoValues.ps1",
            "M1.Bundle.ps1",
            "m1_pair_compare.py",
            "m1_bundle_validation.py",
        }
        for name in required:
            self.assertIn(name, self.runner)

    def test_runner_consumes_inventory_and_never_accepts_source_mdb(self) -> None:
        self.assertIn("$context.Inventory.files", self.runner)
        self.assertIn('"dao_scenario"', self.runner)
        self.assertIn('"dao_pair"', self.runner)
        self.assertNotRegex(
            self.runner,
            re.compile(r"(SourceDatabase|InputDatabase|donated)", re.IGNORECASE),
        )
        self.assertIn("-WorkingRoot $session.WorkingPath", self.runner)

    def test_exact_binary_marshalling_is_enforced(self) -> None:
        self.assertIn("$field.Value = $value", self.dao)
        self.assertIn("$field.AppendChunk($value)", self.dao)
        self.assertIn("$value.GetType() -ne [byte[]]", self.dao)
        self.assertIn("return ,$bytes", self.values)
        self.assertNotIn("$field.Value = (,$value)", self.dao)

    def test_com_failures_are_structured_and_all_scenarios_continue(self) -> None:
        self.assertIn("Get-M1ExceptionRecord", self.dao)
        self.assertIn("exception_type", self.values)
        self.assertIn("hresult", self.values)
        self.assertIn("cleanup_errors", self.values)
        self.assertRegex(
            self.runner,
            r"foreach \(\$plan in \$scenarioPlans\)[\s\S]+"
            r"\[void\]\$scenarioResults\.Add",
        )

    def test_report_is_complete_inventory_and_manifest_is_sorted(self) -> None:
        self.assertIn("scenario_counts", self.bundle)
        self.assertIn("pair_counts", self.bundle)
        self.assertIn("inventory = $inventoryReference", self.bundle)
        self.assertIn("Sort-Object -Property path", self.bundle)
        self.assertIn('"bundle-manifest.json"', self.bundle)

    @unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
    def test_identical_databases_share_one_content_addressed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            output = root / "evidence"
            source_a = root / "a.mdb"
            source_b = root / "b.mdb"
            repository.mkdir()
            source_a.write_bytes(b"identical controlled database")
            source_b.write_bytes(source_a.read_bytes())
            command = f"""
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. {ps_literal(PUBLICATION)}
. {ps_literal(BUNDLE)}
$session = New-M1PublicationSession `
    -RepositoryRoot {ps_literal(repository)} `
    -OutputRoot {ps_literal(output)} `
    -GitCommit "{'1' * 40}" `
    -RunId "20260724T120000Z-dedup-test"
try {{
    $manifest = New-Object Collections.ArrayList
    $hash = Get-M1FileSha256 -Path {ps_literal(source_a)}
    $references = New-Object Collections.ArrayList
    foreach ($case in @(
        @{{ id = "DAO-GEN-EMPTY-REPEAT-A"; path = {ps_literal(source_a)} }},
        @{{ id = "DAO-GEN-EMPTY-REPEAT-B"; path = {ps_literal(source_b)} }}
    )) {{
        $inputPath = "scenarios/$($case.id)/input.json"
        Write-M1DurableUtf8 -Session $session -RelativePath $inputPath `
            -Text "{{}}`n"
        $scenario = [pscustomobject]@{{
            scenario_id = $case.id
            recipe = "empty_repeat"
        }}
        $execution = [pscustomobject]@{{
            database_path = $case.path
            operation_log = [ordered]@{{ scenario_id = $case.id }}
            reason = "pass"
            snapshot = [ordered]@{{ database_sha256 = $hash }}
            status = "pass"
        }}
        $state = @{{}}
        $result = Write-M1ScenarioArtifacts -Session $session `
            -Scenario $scenario -Execution $execution `
            -ManifestInputs $manifest -ScenarioState $state
        [void]$references.Add([string]$result.output_database.path)
    }}
    $failedId = "DAO-GEN-FAILED-RETAINED"
    Write-M1DurableUtf8 -Session $session `
        -RelativePath "scenarios/$failedId/input.json" -Text "{{}}`n"
    $failed = Write-M1ScenarioArtifacts -Session $session `
        -Scenario ([pscustomobject]@{{
            scenario_id = $failedId
            recipe = "synthetic_failure"
        }}) `
        -Execution ([pscustomobject]@{{
            database_path = {ps_literal(source_a)}
            operation_log = [ordered]@{{ scenario_id = $failedId }}
            reason = "controlled failure"
            snapshot = $null
            status = "fail"
        }}) `
        -ManifestInputs $manifest -ScenarioState @{{}}
    [ordered]@{{
        database_entries = @(
            $manifest | Where-Object {{ $_.role -eq "output_database" }}
        ).Count
        failed_database_retained = ($null -ne $failed.output_database)
        reference_count = @($references | Select-Object -Unique).Count
    }} | ConvertTo-Json -Compress
}}
finally {{
    Remove-M1PublicationStaging -Session $session
}}
"""
            completed = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=ROOT.parents[1],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"database_entries":1', completed.stdout)
            self.assertIn('"failed_database_retained":true', completed.stdout)
            self.assertIn('"reference_count":1', completed.stdout)


if __name__ == "__main__":
    unittest.main()
