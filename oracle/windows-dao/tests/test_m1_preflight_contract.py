import re
import unittest
from pathlib import Path


ORACLE_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ORACLE_ROOT / "scripts" / "preflight-m1-controlled.ps1"
PROBE = ORACLE_ROOT / "scripts" / "probe-provider.ps1"
README = ORACLE_ROOT / "README.md"
PROTOCOL_README = ORACLE_ROOT / "protocol" / "v1_1" / "README.md"

EXPECTED_EXAMPLES = {
    "DAO-GEN-BINARY-MARKER-001.scenario.json",
    "DAO-GEN-EMPTY-REPEAT-A.scenario.json",
    "DAO-GEN-EMPTY-REPEAT-B.scenario.json",
    "DAO-GEN-LONGBINARY-LADDER-001.scenario.json",
    "DAO-GEN-MEMO-LADDER-001.scenario.json",
    "DAO-GEN-TEXT8-BASELINE-001.scenario.json",
    "DAO-GEN-TEXT8-INDEXED-001.scenario.json",
    "DAO-PAIR-EMPTY-REPEAT-001.pair.json",
    "DAO-PAIR-TEXT8-INDEX-001.pair.json",
}


class M1PreflightSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PREFLIGHT.read_text(encoding="utf-8")

    def test_preflight_is_pinned_to_protocol_1_1(self):
        self.assertIn('$ProtocolVersion = "1.1.0"', self.source)
        for parameter in (
            "RepositoryRoot",
            "EnvironmentPath",
            "OutputRoot",
            "GitCommit",
            "RunId",
        ):
            self.assertRegex(self.source, rf"\[string\]\${parameter}\b")

    def test_preflight_selects_the_complete_controlled_inventory(self):
        assignment = self.source.split(
            "$ExpectedExampleNames = @(", maxsplit=1
        )[1].split(")", maxsplit=1)[0]
        actual = set(re.findall(r'"([^"]+\.(?:scenario|pair)\.json)"', assignment))
        self.assertEqual(actual, EXPECTED_EXAMPLES)
        self.assertIn("$inventory.files.Count -eq $ExpectedExampleNames.Count", self.source)
        self.assertIn("$ExpectedExampleNames -cnotcontains $name", self.source)
        self.assertIn("Get-LowerSha256 -Path $source", self.source)

    def test_preflight_binds_clean_git_inputs(self):
        required_fragments = (
            "rev-parse HEAD",
            "status --porcelain=v1",
            "--untracked-files=all",
            "Assert-GitBoundFile",
            "$RunnerRelativePath",
            "$InventoryRelativePath",
            "$ValidatorRelativePath",
            "-RelativePath $relativePath",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, self.source)

    def test_preflight_uses_the_checked_validator_for_external_documents(self):
        self.assertIn("Invoke-M1DocumentValidator", self.source)
        self.assertIn("-DocumentPath $inventorySource", self.source)
        self.assertIn("-DocumentPath $environmentSource", self.source)
        self.assertIn(
            "Python 3 is required for fail-closed protocol 1.1 validation.",
            self.source,
        )
        self.assertIn('@{ Name = "python3"; Prefix = @("-B") }', self.source)
        self.assertIn('@{ Name = "py"; Prefix = @("-3", "-B") }', self.source)

    def test_preflight_binds_provider_host_bitness_and_binary(self):
        required_fragments = (
            '$environment.status -eq "ready"',
            "$environment.host.is_windows -eq $true",
            '$environment.accepted_provider.database_version -eq "dbVersion30"',
            '$_.activation -eq "succeeded"',
            '$_.dbversion30_test.status -eq "pass"',
            "$environment.host.computer_name -ine [Environment]::MachineName",
            "$environment.host.process_architecture -cne (Get-ProcessArchitecture)",
            "$accepted.registry_view -cne (Get-ProcessArchitecture)",
            "Get-LowerSha256 -Path $accepted.server_path",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, self.source)

    def test_preflight_cannot_activate_com_or_mutate_output(self):
        prohibited_fragments = (
            "[Activator]::CreateInstance",
            ".CreateDatabase(",
            ".OpenDatabase(",
            ".AppendChunk(",
            ".AddNew(",
            "[IO.Directory]::CreateDirectory",
            "[IO.Directory]::Move",
            "[IO.File]::Move",
            "[IO.File]::Copy",
        )
        for fragment in prohibited_fragments:
            self.assertNotIn(fragment, self.source)

    def test_preflight_ends_in_the_explicit_marshalling_blocker(self):
        self.assertIn("SRC-0012", self.source)
        self.assertIn(
            "deterministic PowerShell COM Variant/AppendChunk", self.source
        )
        self.assertIn("No database or evidence bundle was created.", self.source)
        self.assertTrue(self.source.rstrip().endswith(")"))

    def test_probe_can_emit_a_separate_1_1_environment_record(self):
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('[ValidateSet("1.0.0", "1.1.0")]', source)
        self.assertIn('[string]$ProtocolVersion = "1.0.0"', source)
        self.assertIn("protocol_version = $ProtocolVersion", source)

    def test_documentation_never_describes_preflight_as_evidence(self):
        readme = README.read_text(encoding="utf-8")
        protocol = PROTOCOL_README.read_text(encoding="utf-8")
        for document in (readme, protocol):
            self.assertIn("preflight-m1-controlled.ps1", document)
            self.assertIn("BLOCKED", document)
        self.assertIn("publishes no evidence bundle", readme)
        self.assertIn("before COM activation or output", protocol)
        self.assertIn("mutation. The checked", protocol)


if __name__ == "__main__":
    unittest.main()
