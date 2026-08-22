"""Fail-closed source contract for the manual DAO A3 workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-dao-a3.yml"


class WindowsDaoA3WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.contract, remainder = cls.workflow.split("  a3-replica:", 1)
        cls.replica, cls.fan_in = remainder.split("  fan-in:", 1)
        cls.parse_step = cls.contract.split(
            "- name: Parse checked A3 PowerShell sources without execution", 1
        )[1].split("- name: Run the checked A3 Python contract tests", 1)[0]
        cls.run_step = cls.replica.split(
            "      - name: Run bounded A3 replica", 1
        )[1].split("      - name: Upload retained A3 replica tree", 1)[0]

    def test_dispatch_is_manual_only_and_explicitly_gated(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("execute_a3_campaign:", self.workflow)
        self.assertIn("type: boolean", self.workflow)
        self.assertIn("default: false", self.workflow)
        for trigger in ("push:", "pull_request:", "schedule:"):
            self.assertNotIn(trigger, self.workflow)
        gate = (
            r"github\.event_name == 'workflow_dispatch' &&\s+"
            r"github\.ref == 'refs/heads/main' &&\s+"
            r"inputs\.execute_a3_campaign"
        )
        self.assertRegex(self.replica, gate)
        self.assertRegex(self.fan_in, gate)
        self.assertNotIn("Start-Process", self.contract)

    def test_contract_parses_a3_powershell_without_execution(self) -> None:
        self.assertIn("shell: powershell", self.parse_step)
        self.assertIn('$PSVersionTable.PSEdition -cne "Desktop"', self.parse_step)
        self.assertIn("$PSVersionTable.PSVersion.Major -ne 5", self.parse_step)
        self.assertIn("$item.Length -gt 2MB", self.parse_step)
        self.assertIn(
            "[System.Management.Automation.Language.Parser]::ParseFile",
            self.parse_step,
        )
        self.assertEqual(
            self.parse_step.count(
                "oracle/windows-dao/scripts/run-a3-replica.ps1"
            ),
            1,
        )
        self.assertEqual(
            self.parse_step.count("oracle/windows-dao/scripts/a3"), 1
        )
        for forbidden in ("Start-Process", "Invoke-A3", "& python"):
            self.assertNotIn(forbidden, self.parse_step)
        for test in (
            "test_a3_plan_contract.py",
            "test_a3_powershell_contract.py",
            "test_windows_dao_a3_workflow.py",
        ):
            self.assertEqual(self.contract.count(test), 1)
        # EXP-0042 lesson: the contract job timed out once on heavy suites. Bundle,
        # analyzer, dry-run, and validator suites run in regular CI only.
        for heavy in (
            "test_a3_bundle.py",
            "test_a3_analyzer.py",
            "test_a3_dryrun.py",
            "test_a3_independent_validator.py",
        ):
            self.assertNotIn(heavy, self.contract)
        self.assertIn("a3_spec.py", self.contract)
        self.assertIn('experiment_id -cne "DAO-A3-ALLOCATION-MAPS-001"', self.contract)

    def test_workflow_is_bound_to_a3_only(self) -> None:
        self.assertNotIn("windows-dao-a2", self.workflow)
        self.assertNotIn("execute_a2_campaign", self.workflow)
        self.assertNotIn("experiments/a2/", self.workflow)
        self.assertNotIn("a2_", self.workflow)
        self.assertNotIn("A2.", self.workflow)
        self.assertEqual(
            self.workflow.count(
                "oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json"
            ),
            3,
        )
        for name in (
            "windows-dao-a3-replica-${{ matrix.replica }}",
            "windows-dao-a3-bundle-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}",
            "windows-dao-a3-fanin-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}",
        ):
            self.assertIn(name, self.workflow)
        self.assertIn("jet3_windows_dao_a3_hosted_replica", self.workflow)
        self.assertIn("jet3_windows_dao_a3_fan_in_status", self.workflow)

    def test_three_job_matrix_and_bounds_are_frozen(self) -> None:
        self.assertIn("replica: [1, 2, 3]", self.replica)
        self.assertIn("max-parallel: 3", self.replica)
        self.assertIn("fail-fast: false", self.replica)
        # EXP-0006 pins the dao360.dll binary shipped by the Server 2022 image.
        self.assertEqual(self.workflow.count("runs-on: windows-2022"), 3)
        self.assertNotIn("windows-latest", self.workflow)
        self.assertIn("timeout-minutes: 37", self.replica)
        self.assertIn('FROZEN_WORKER_TIMEOUT_SECONDS: "1700"', self.replica)
        self.assertIn('HOSTED_REPLICA_TIMEOUT_SECONDS: "2120"', self.replica)
        self.assertIn('FROZEN_CAMPAIGN_TIMEOUT_SECONDS: "2700"', self.replica)
        self.assertIn('REPLICA_MAXIMUM_OUTPUT_BYTES: "1048576"', self.replica)
        self.assertIn("timeout-minutes: 20", self.fan_in)
        self.assertEqual(1_700 + 120 + 300, 2_120)

    def test_replica_launch_is_x86_waited_bounded_and_null_safe(self) -> None:
        self.assertIn(
            '"SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe"',
            self.run_step,
        )
        launch = self.run_step.index(
            "$process = Start-Process -FilePath $x86PowerShell -PassThru"
        )
        handle = self.run_step.index("$null = $process.Handle", launch)
        clock = self.run_step.index(
            "$clock = [Diagnostics.Stopwatch]::StartNew()", handle
        )
        monitored_wait = self.run_step.index(
            "while (-not $process.WaitForExit(1000))", clock
        )
        completed_wait = self.run_step.index("$process.WaitForExit()", monitored_wait)
        refresh = self.run_step.index("$process.Refresh()", completed_wait)
        null_guard = self.run_step.index("if ($null -eq $exitCode)", refresh)
        assignment = self.run_step.index("$replicaExit = [int]$exitCode", null_guard)
        comparison = self.run_step.index("if ($replicaExit -ne 0)", assignment)
        self.assertLess(launch, handle)
        self.assertLess(handle, clock)
        self.assertLess(clock, monitored_wait)
        self.assertLess(completed_wait, refresh)
        self.assertLess(refresh, null_guard)
        self.assertLess(null_guard, assignment)
        self.assertLess(assignment, comparison)
        self.assertIn("Get-A3LogBytes", self.run_step)
        self.assertIn("REPLICA_MAXIMUM_OUTPUT_BYTES", self.run_step)
        self.assertIn("HOSTED_REPLICA_TIMEOUT_SECONDS", self.run_step)
        self.assertIn("Stop-Jet3BootstrapProcessTree -Process $process", self.run_step)
        self.assertIn('started_utc = [DateTimeOffset]::UtcNow.ToString("o")', self.run_step)
        self.assertIn('completed_utc = [DateTimeOffset]::UtcNow.ToString("o")', self.run_step)
        self.assertIn("elapsed_seconds = [Math]::Round($elapsedSeconds, 3)", self.run_step)
        for argument in (
            '"-RepositoryRoot", $repository',
            '"-OutputRoot", $outputRoot',
            '"-DiagnosticsRoot", $diagnostics',
            '"-GitCommit", $env:GITHUB_SHA',
            '"-RunId", $env:GITHUB_RUN_ID',
            '"-Replica", $replica',
            '"-MatrixJobId", "a3-replica-$replica"',
        ):
            self.assertIn(argument, self.run_step)

    def test_exact_clean_pushed_checkout_is_required(self) -> None:
        self.assertIn('$env:GITHUB_REPOSITORY -cne "oglassdev/jet3-rs"', self.replica)
        self.assertIn("git remote set-url origin https://github.com/oglassdev/jet3-rs.git", self.replica)
        self.assertIn("$head -cne $env:GITHUB_SHA", self.replica)
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", self.replica)
        self.assertGreaterEqual(
            self.workflow.count("status --porcelain=v1 --untracked-files=all"),
            2,
        )
        self.assertIn("fetch-depth: 1", self.replica)
        self.assertIn("needs.contract.result == 'success'", self.replica)

    def test_replica_and_diagnostic_uploads_are_always_retained(self) -> None:
        retained = self.replica.split(
            "      - name: Upload retained A3 replica tree", 1
        )[1].split("      - name: Upload bounded A3 replica diagnostics", 1)[0]
        diagnostics = self.replica.split(
            "      - name: Upload bounded A3 replica diagnostics", 1
        )[1]
        self.assertIn("if: always()", retained)
        self.assertIn("if: always()", diagnostics)
        self.assertIn("if-no-files-found: warn", retained)
        self.assertIn("if-no-files-found: error", diagnostics)
        self.assertIn("compression-level: 0", retained)
        self.assertIn("compression-level: 0", diagnostics)
        self.assertIn("retention-days: 90", retained)
        self.assertIn("retention-days: 14", diagnostics)

    def test_fan_in_downloads_exactly_three_and_runs_contract_order(self) -> None:
        self.assertEqual(self.fan_in.count("actions/download-artifact@"), 4)
        for replica in (1, 2, 3):
            self.assertIn(f"name: windows-dao-a3-replica-{replica}", self.fan_in)
            self.assertIn(f"replica-0{replica}", self.fan_in)
        assemble = self.fan_in.index("a3_bundle.py assemble")
        analysis = self.fan_in.index("a3_analysis.py", assemble)
        finalize = self.fan_in.index("a3_bundle.py finalize", analysis)
        validate = self.fan_in.index("a3_bundle.py validate", finalize)
        independent = self.fan_in.index("a3_independent_validator.py", validate)
        self.assertLess(assemble, analysis)
        self.assertLess(analysis, finalize)
        self.assertLess(finalize, validate)
        self.assertLess(validate, independent)
        self.assertEqual(self.fan_in.count("--replica-root"), 3)
        self.assertEqual(self.fan_in.count("--replica (Join-Path"), 3)
        self.assertIn('$campaignId = "a3-run-$env:GITHUB_RUN_ID"', self.fan_in)
        self.assertIn("--holdout-receipt", self.fan_in)
        self.assertIn('--campaign-id "a3-run-$env:GITHUB_RUN_ID"', self.fan_in)
        self.assertEqual(
            self.fan_in.count("--producer-commit $env:GITHUB_SHA"), 3
        )
        upload = self.fan_in.split("      - name: Upload retained A3 bundle", 1)[1]
        self.assertIn("if: always()", upload)
        self.assertIn("fan-in-status.json", upload)
        self.assertIn("validation\\independent-validation-report.json", upload)
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("compression-level: 0", upload)
        self.assertIn("retention-days: 90", upload)

    def test_independent_validator_is_a_separate_recorded_step(self) -> None:
        step = self.fan_in.split(
            "      - name: Independently recompute the retained A3 bundle", 1
        )[1].split("      - name: Record retained A3 fan-in status", 1)[0]
        self.assertIn("id: independent", step)
        self.assertIn("--validator-commit $env:GITHUB_SHA", step)
        self.assertIn(
            "--plan oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json",
            step,
        )
        self.assertIn("--output $report", step)
        self.assertIn("rejected the bundle", step)
        # The report lives outside the closed bundle tree.
        self.assertNotIn("jet3-a3-bundle\\validation", step)
        self.assertNotIn("continue-on-error", self.fan_in)

    def test_fan_in_status_is_bounded_timed_and_always_retained(self) -> None:
        for step_id in ("assemble", "analyze", "finalize", "validate", "independent"):
            self.assertIn(f"id: {step_id}", self.fan_in)
            self.assertIn(f"steps.{step_id}.outcome", self.fan_in)
        status = self.fan_in.split(
            "      - name: Record retained A3 fan-in status", 1
        )[1].split("      - name: Upload retained A3 bundle", 1)[0]
        self.assertIn("if: always()", status)
        for field in (
            "producer_contract_passed",
            "bundle_status",
            "independent_validation_accepted",
            "independent_validation_status",
            "independent_validation_discrepancy_codes",
            "campaign_elapsed_seconds",
            "within_plan_campaign_timeout",
            "timing_records_complete",
        ):
            self.assertIn(field, status)
        self.assertIn('"dao_a3_independent_validation_report"', status)
        self.assertIn(
            'if ($env:INDEPENDENT_OUTCOME -cne "success") { $independentAccepted = $false }',
            status,
        )
        self.assertIn("$statusBytes.Length -gt 4096", status)
        self.assertIn("hosted-replica.json", self.fan_in)
        self.assertIn("started_utc", self.fan_in)
        diagnostics = self.fan_in.split(
            "      - name: Upload bounded A3 fan-in diagnostics", 1
        )[1]
        self.assertIn("if: always()", diagnostics)
        self.assertIn("if-no-files-found: error", diagnostics)
        self.assertIn("retention-days: 14", diagnostics)

    def test_all_actions_are_commit_pinned(self) -> None:
        actions = re.findall(
            r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE
        )
        self.assertEqual(len(actions), 14)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertEqual(self.workflow.count("persist-credentials: false"), 3)


if __name__ == "__main__":
    unittest.main()
