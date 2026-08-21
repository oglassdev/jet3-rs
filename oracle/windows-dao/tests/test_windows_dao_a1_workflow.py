from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-dao-a1.yml"


class WindowsDaoA1WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.contract, cls.campaign = cls.workflow.split("  a1-campaign:", 1)
        cls.parse_step = cls.contract.split(
            "- name: Parse checked A1 PowerShell sources without execution", 1
        )[1].split("- name: Validate the frozen plan and workflow contract", 1)[0]
        cls.probe_step = cls.campaign.split(
            "      - name: Probe and bind the proven stock x86 provider", 1
        )[1].split("      - name: Run the bounded checked A1 campaign", 1)[0]

    def test_push_validates_only_and_acquisition_is_explicit_manual(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("execute_a1_campaign:", self.workflow)
        self.assertIn("default: false", self.workflow)
        self.assertIn("codex/windows-dao-a1", self.workflow)
        self.assertNotIn("Start-Process", self.contract)
        self.assertNotIn("probe-provider.ps1", self.contract)
        self.assertRegex(
            self.campaign,
            r"github\.event_name == 'workflow_dispatch' &&\s+"
            r"github\.ref == 'refs/heads/main' &&\s+"
            r"inputs\.execute_a1_campaign",
        )
        self.assertNotIn("pull_request:", self.workflow)

    def test_contract_ast_parses_all_checked_powershell_without_execution(self) -> None:
        self.assertIn("shell: powershell", self.parse_step)
        self.assertIn("$PSVersionTable.PSEdition -cne \"Desktop\"", self.parse_step)
        self.assertIn("$PSVersionTable.PSVersion.Major -ne 5", self.parse_step)
        self.assertIn("$item.Length -gt 2MB", self.parse_step)
        self.assertIn(
            "[System.Management.Automation.Language.Parser]::ParseFile",
            self.parse_step,
        )
        for path in (
            "oracle/windows-dao/scripts/run-a1-controlled.ps1",
            "oracle/windows-dao/scripts/a1/A1.Controller.ps1",
            "oracle/windows-dao/scripts/a1/A1.Worker.ps1",
            "oracle/windows-dao/scripts/a1/A1.PageStore.ps1",
        ):
            self.assertEqual(self.parse_step.count(path), 1)
        for forbidden in ("Start-Process", "Invoke-A1", "Invoke-Jet3", "& "):
            self.assertNotIn(forbidden, self.parse_step)

    def test_uses_only_pinned_windows_2022_and_read_permissions(self) -> None:
        self.assertEqual(self.workflow.count("runs-on: windows-2022"), 2)
        self.assertNotIn("windows-latest", self.workflow)
        self.assertNotIn("windows-2025", self.workflow)
        self.assertRegex(self.workflow, r"permissions:\n  contents: read\n")
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertEqual(len(uses), 7)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertEqual(self.workflow.count("persist-credentials: false"), 2)
        self.assertEqual(
            self.workflow.count(
                "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
            ),
            2,
        )
        self.assertEqual(self.workflow.count('python-version: "3.13.7"'), 2)
        self.assertNotIn('python-version: "3.13"', self.workflow)

    def test_exact_clean_pushed_checkout_is_controller_compatible(self) -> None:
        repository_check = self.workflow.index(
            '$env:GITHUB_REPOSITORY -cne "oglassdev/jet3-rs"'
        )
        set_url = self.workflow.index(
            "git remote set-url origin https://github.com/oglassdev/jet3-rs.git"
        )
        self.assertLess(repository_check, set_url)
        self.assertIn(
            '$origin -cne "https://github.com/oglassdev/jet3-rs.git"',
            self.workflow,
        )
        self.assertIn("$head -cne $env:GITHUB_SHA", self.workflow)
        self.assertGreaterEqual(
            self.workflow.count("status --porcelain=v1 --untracked-files=all"), 3
        )
        self.assertIn("fetch-depth: 0", self.campaign)

    def test_binds_proven_stock_x86_dao_without_installing_runtime(self) -> None:
        self.assertIn('PROVEN_PROVIDER_RUN_ID: "32327232241"', self.workflow)
        self.assertIn("EXPECTED_IMAGE_OS: win22", self.workflow)
        self.assertIn('EXPECTED_IMAGE_VERSION: "20260802.262.1"', self.workflow)
        self.assertIn(
            "$env:ImageOS -ceq $env:EXPECTED_IMAGE_OS", self.workflow
        )
        self.assertIn(
            "$env:ImageVersion -ceq $env:EXPECTED_IMAGE_VERSION", self.workflow
        )
        self.assertIn(
            "The hosted image differs from run $env:PROVEN_PROVIDER_RUN_ID.",
            self.workflow,
        )
        probe_checks = self.probe_step.split(
            '$ErrorActionPreference = "Stop"', 1
        )[1].lstrip()
        self.assertTrue(
            probe_checks.startswith(
                "if (-not ($env:ImageOS -ceq $env:EXPECTED_IMAGE_OS)"
            )
        )
        self.assertIn("EXPECTED_PROVIDER_PROG_ID: DAO.DBEngine.36", self.workflow)
        self.assertIn("03.60.9765.0", self.workflow)
        self.assertIn(
            "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac",
            self.workflow,
        )
        self.assertIn(
            '"SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe"',
            self.workflow,
        )
        self.assertIn('registry_view = "x86"', self.workflow)
        self.assertIn('registration_scope = "machine"', self.workflow)
        self.assertIn(
            "$pythonVersion = (& python --version 2>&1).Trim()", self.workflow
        )
        self.assertIn("python_version = $pythonVersion", self.workflow)
        for forbidden in ("AccessRuntime", "AcceptEULA", "ODT_URL", "Invoke-WebRequest"):
            self.assertNotIn(forbidden, self.workflow)

    def test_probe_campaign_and_retained_output_are_bounded(self) -> None:
        self.assertIn("-TimeoutSeconds 120 -MaximumOutputBytes 1MB", self.workflow)
        self.assertIn('FROZEN_CAMPAIGN_TIMEOUT_SECONDS: "7200"', self.workflow)
        self.assertIn('HOSTED_CAMPAIGN_TIMEOUT_SECONDS: "7500"', self.workflow)
        self.assertIn("controller_timeout_seconds", self.workflow)
        self.assertIn("hosted_timeout_seconds", self.workflow)
        self.assertIn('CAMPAIGN_MAXIMUM_OUTPUT_BYTES: "1048576"', self.workflow)
        self.assertIn("Stop-Jet3BootstrapProcessTree", self.workflow)
        self.assertIn("timeout-minutes: 210", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)
        self.assertIn("compression-level: 0", self.workflow)
        self.assertEqual(self.workflow.count("retention-days: 90"), 2)
        self.assertEqual(self.workflow.count("retention-days: 14"), 1)
        self.assertNotIn("${{ runner.temp }}\\jet3-a1-output\n", self.workflow)
        self.assertIn("Stop-Jet3BootstrapProcessTree -Process $process", self.workflow)
        finally_block = self.workflow.index("          finally {")
        cleanup = self.workflow.index(
            "Stop-Jet3BootstrapProcessTree -Process $process", finally_block
        )
        dispose = self.workflow.index("$process.Dispose()", cleanup)
        self.assertLess(finally_block, cleanup)
        self.assertLess(cleanup, dispose)
        monitored = self.workflow[
            self.workflow.index("while (-not $process.WaitForExit(1000))"):finally_block
        ]
        self.assertNotIn("Stop-Jet3BootstrapProcessTree", monitored)
        self.assertIn("cleanup also failed", self.workflow)

    def test_campaign_and_independent_bundle_validator_are_exactly_bound(self) -> None:
        self.assertEqual(self.workflow.count("run-a1-controlled.ps1"), 2)
        self.assertIn("from a1_bundle import validate_bundle", self.workflow)
        self.assertIn('manifest["producer_commit"] != sys.argv[2]', self.workflow)
        self.assertIn('manifest["run_id"] != sys.argv[3]', self.workflow)
        self.assertIn('manifest["provider_sha256"] != sys.argv[4]', self.workflow)
        self.assertIn(
            'manifest["repository_url"] != "https://github.com/oglassdev/jet3-rs.git"',
            self.workflow,
        )
        self.assertIn("$env:GITHUB_SHA", self.workflow)
        self.assertIn("$env:EXPECTED_PROVIDER_SHA256", self.workflow)
        self.assertIn("id: bundle_validation", self.workflow)
        self.assertIn(
            "if: success() && steps.bundle_validation.outcome == 'success'",
            self.workflow,
        )
        self.assertIn("Upload independently validated A1 evidence", self.workflow)
        self.assertIn("Upload independently validated A1 attestation", self.workflow)
        self.assertIn("Upload bounded A1 diagnostics", self.workflow)
        self.assertIn("jet3_windows_dao_a1_validation_receipt", self.workflow)
        self.assertIn('"status": "independently_validated"', self.workflow)
        self.assertIn("bundle_manifest_sha256", self.workflow)
        self.assertIn("if len(receipt_bytes) > 4096", self.workflow)
        self.assertIn('status = "not_independently_validated"', self.workflow)
        self.assertIn(
            '$campaignRecord.status = "independently_validated"', self.workflow
        )
        evidence = self.workflow.split(
            "      - name: Upload independently validated A1 evidence", 1
        )[1].split(
            "      - name: Upload independently validated A1 attestation", 1
        )[0]
        attestation = self.workflow.split(
            "      - name: Upload independently validated A1 attestation", 1
        )[1].split("      - name: Upload bounded A1 diagnostics", 1)[0]
        diagnostics = self.workflow.split(
            "      - name: Upload bounded A1 diagnostics", 1
        )[1]
        self.assertIn("${{ steps.campaign.outputs.bundle_path }}", evidence)
        self.assertNotIn("${{ runner.temp }}\\jet3-a1-diagnostics", evidence)
        self.assertIn("retention-days: 90", evidence)
        self.assertIn(
            "windows-dao-a1-attestation-${{ github.sha }}-${{ github.run_id }}",
            attestation,
        )
        self.assertIn("${{ runner.temp }}\\jet3-a1-attestation", attestation)
        self.assertIn("retention-days: 90", attestation)
        self.assertIn(
            "if: success() && steps.bundle_validation.outcome == 'success'",
            attestation,
        )
        attestation_copy = self.workflow.split(
            '$attestation = Join-Path $env:RUNNER_TEMP "jet3-a1-attestation"', 1
        )[1].split(
            '          $campaignRecord.status = "independently_validated"', 1
        )[0]
        self.assertEqual(
            re.findall(r'^\s+"([^\"]+\.json)",?$', attestation_copy, re.MULTILINE),
            [
                "provider-binding.json",
                "validation-receipt.json",
                "campaign.json",
            ],
        )
        self.assertEqual(attestation_copy.count("Copy-Item"), 1)
        self.assertIn("-Destination (Join-Path $attestation $name)", attestation_copy)
        validation_step = self.workflow.split("        id: bundle_validation", 1)[1].split(
            "      - name: Upload independently validated A1 evidence", 1
        )[0]
        clean_check = validation_step.index(
            "Independent validation changed the exact checkout."
        )
        prepare_attestation = validation_step.index("jet3-a1-attestation")
        validated_status = validation_step.index(
            '$campaignRecord.status = "independently_validated"'
        )
        final_diagnostics_write = validation_step.rindex("$campaignPath")
        self.assertLess(clean_check, prepare_attestation)
        self.assertLess(prepare_attestation, validated_status)
        self.assertLess(validated_status, final_diagnostics_write)
        self.assertIn("if: always()", diagnostics)
        self.assertIn("${{ runner.temp }}\\jet3-a1-diagnostics", diagnostics)
        self.assertIn("retention-days: 14", diagnostics)
        self.assertNotIn("${{ steps.campaign.outputs.bundle_path }}", diagnostics)


if __name__ == "__main__":
    unittest.main()
