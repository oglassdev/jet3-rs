from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-dao-hosted.yml"
REMOTE_PROCESS = (
    ROOT / "oracle" / "windows-dao" / "scripts" / "remote" / "Remote.Process.ps1"
)


class WindowsDaoHostedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_or_exact_probe_branch_only(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("codex/windows-dao-hosted-probe", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)

    def test_uses_explicit_windows_images_and_read_only_permissions(self) -> None:
        self.assertIn("windows-2025", self.workflow)
        self.assertIn("windows-2022", self.workflow)
        self.assertRegex(self.workflow, r"permissions:\n  contents: read\n")
        self.assertIn("timeout-minutes: 40", self.workflow)

    def test_third_party_actions_are_commit_pinned(self) -> None:
        uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 2)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", self.workflow)

    def test_installer_is_hash_and_signature_checked_and_bounded(self) -> None:
        self.assertRegex(self.workflow, r"ODT_SHA256: [0-9a-f]{64}\n")
        self.assertIn("download.microsoft.com", self.workflow)
        self.assertIn("Get-AuthenticodeSignature", self.workflow)
        self.assertIn("O=Microsoft Corporation", self.workflow)
        self.assertIn("-TimeoutSeconds 120", self.workflow)
        self.assertIn("-TimeoutSeconds 1200", self.workflow)
        self.assertIn("Stop-Jet3BootstrapProcessTree", self.workflow)
        self.assertIn("OfficeClientEdition=\"32\"", self.workflow)
        self.assertIn("Product ID=\"AccessRuntimeRetail\"", self.workflow)

    def test_runtime_install_requires_an_operator_license_decision(self) -> None:
        self.assertIn("accept_microsoft_access_runtime_license:", self.workflow)
        self.assertIn("[accept-access-runtime-eula]", self.workflow)
        self.assertIn('AcceptEULA="TRUE"', self.workflow)

    def test_probes_stock_and_installed_x86_protocol_1_1(self) -> None:
        self.assertIn("environment-stock.json", self.workflow)
        self.assertIn('"SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe"', self.workflow)
        self.assertEqual(
            self.workflow.count('"-ProtocolVersion", "1.1.0"'),
            2,
        )
        self.assertEqual(self.workflow.count("probe-provider.ps1"), 2)
        self.assertEqual(self.workflow.count("Invoke-Jet3BootstrapProcess"), 2)
        self.assertEqual(self.workflow.count("-MaximumOutputBytes 1MB"), 2)
        self.assertGreaterEqual(self.workflow.count("-Encoding utf8 -Append"), 3)

    def test_diagnostics_upload_even_when_probe_is_blocked(self) -> None:
        self.assertIn("if: always()", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)
        self.assertIn("runner.json", self.workflow)
        self.assertIn("runtime-install.json", self.workflow)
        self.assertIn("environment.json", self.workflow)

    def test_retained_process_helper_is_bootstrap_only(self) -> None:
        process = REMOTE_PROCESS.read_text(encoding="utf-8")
        self.assertIn("function Invoke-Jet3BootstrapProcess", process)
        self.assertIn("function Stop-Jet3BootstrapProcessTree", process)
        self.assertNotIn("Invoke-Jet3CheckedChildProcess", process)
        self.assertNotIn("BoundedProcess", process)


if __name__ == "__main__":
    unittest.main()
