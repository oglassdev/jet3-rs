from __future__ import annotations

import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
CONTROLLER = SCRIPTS / "a1" / "A1.Controller.ps1"
PROGRESS = SCRIPTS / "a1" / "A1.Progress.ps1"
BOUNDED = SCRIPTS / "shared" / "BoundedProcess.ps1"


class A1PostAcquisitionDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.controller = CONTROLLER.read_text(encoding="utf-8")
        cls.progress = PROGRESS.read_text(encoding="utf-8")
        cls.bounded = BOUNDED.read_text(encoding="utf-8")

    def test_only_post_worker_python_calls_bind_failure_diagnostics(self) -> None:
        self.assertEqual(self.controller.count("-DiagnosticsRoot $diagnostics"), 5)
        self.assertIn('$phase = "analysis"', self.controller)
        self.assertIn('$phase = "analysis-report-validation"', self.controller)
        self.assertIn('-Phase "complete-bundle-validation"', self.controller)
        for label in (
            "A1 preregistered analysis",
            "A1 analysis report validation",
            "A1 complete bundle validation",
        ):
            start = self.controller.index(f'-Label "{label}"')
            invocation = self.controller[start : start + 220]
            self.assertIn("-DiagnosticsRoot $diagnostics", invocation)

    def test_child_failure_record_is_bounded_and_structured(self) -> None:
        self.assertIn("-ReturnFailureRecord", self.controller)
        self.assertIn("Write-A1ChildFailureDiagnostic", self.controller)
        self.assertIn('document_type = "jet3_a1_child_failure"', self.progress)
        for field in (
            "label = $Label",
            "exit_code = $exitValue",
            "elapsed_seconds =",
            "stdout_tail =",
            "stderr_tail =",
            "error_message =",
        ):
            self.assertIn(field, self.progress)
        self.assertEqual(self.progress.count("-MaximumChars 32768"), 2)
        self.assertIn("$bytes.Length -gt $script:A1ProgressMaximumBytes", self.progress)
        self.assertIn("[IO.FileMode]::CreateNew", self.progress)

    def test_campaign_failure_is_recorded_before_private_cleanup(self) -> None:
        catch = self.controller.split("    catch {\n        $original = $_", 1)[1]
        record = catch.index("Write-A1CampaignFailureDiagnostic")
        cleanup = catch.index("Remove-A1PrivateStaging")
        self.assertLess(record, cleanup)
        self.assertIn('document_type = "jet3_a1_campaign_failure"', self.progress)
        self.assertIn('-FileName "campaign-error.json"', self.progress)
        self.assertIn("phase = $Phase", self.progress)
        self.assertIn("exception_type =", self.progress)

    def test_shared_launcher_default_nonzero_behavior_is_unchanged(self) -> None:
        opt_in = self.bounded.index("if ($ReturnFailureRecord)")
        default_failure = self.bounded.index("if ($launch.ExitCode -ne 0)")
        self.assertLess(opt_in, default_failure)
        self.assertIn('throw "$CallerLabel worker failed: $stderr"', self.bounded)


if __name__ == "__main__":
    unittest.main()
