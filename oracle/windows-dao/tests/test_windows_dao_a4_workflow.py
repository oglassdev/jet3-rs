"""Fail-closed source contract for the manual DAO A4 workflow."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/windows-dao-a4.yml"
SCRIPTS = ROOT / "oracle/windows-dao/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a4_spec import BOUNDS, EXPERIMENT_ID, PLAN_SHA256  # noqa: E402


class WindowsDaoA4WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.contract, remainder = cls.workflow.split("  a4-replica:", 1)
        cls.replica, cls.fan_in = remainder.split("  fan-in:", 1)

    def test_dispatch_is_manual_read_only_and_default_denied(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("execute_a4_campaign:", self.workflow)
        self.assertIn("type: boolean", self.workflow)
        self.assertIn("default: false", self.workflow)
        for trigger in ("push:", "pull_request:", "schedule:"):
            self.assertNotIn(trigger, self.workflow)
        permissions = self.workflow.split("permissions:", 1)[1].split(
            "concurrency:", 1
        )[0]
        self.assertEqual(
            {line.strip() for line in permissions.splitlines() if line.strip()},
            {"actions: read", "contents: read"},
        )
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_jobs_and_timeouts_match_the_frozen_plan(self) -> None:
        self.assertEqual(self.workflow.count("runs-on: windows-2022"), 3)
        self.assertIn("timeout-minutes: 15", self.contract)
        self.assertIn("timeout-minutes: 37", self.replica)
        self.assertEqual(
            int(re.search(r"timeout-minutes: (\d+)", self.fan_in).group(1)) * 60,
            BOUNDS["fan_in_timeout_seconds"],
        )
        self.assertIn("replica: [1, 2, 3]", self.replica)
        self.assertIn("max-parallel: 3", self.replica)
        self.assertIn("fail-fast: false", self.replica)
        for section in (self.replica, self.fan_in):
            self.assertIn(f'FROZEN_PLAN_SHA256: "{PLAN_SHA256}"', section)
            self.assertIn(f'FROZEN_REVISION_PLAN_SHA256: "{PLAN_SHA256}"', section)
        self.assertIn('FROZEN_CAMPAIGN_TIMEOUT_SECONDS: "2700"', self.workflow)

    def test_contract_and_worker_are_bound_to_row_anchored_a4(self) -> None:
        self.assertEqual(
            self.workflow.count(
                "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json"
            ),
            2,
        )
        self.assertIn(f'experiment_id -cne "{EXPERIMENT_ID}"', self.contract)
        self.assertNotIn("a4-allocation-maps", self.workflow)
        self.assertNotIn("DAO-A4-ALLOCATION-MAPS", self.workflow)
        self.assertIn("test_a4_powershell_contract.py", self.contract)
        self.assertIn("test_windows_dao_a4_workflow.py", self.contract)
        self.assertIn("ParseFile", self.contract)
        self.assertIn('"SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe"', self.replica)
        self.assertIn("WaitForExit(1000)", self.replica)
        self.assertIn("Stop-Jet3BootstrapProcessTree", self.replica)

    def test_exact_clean_pushed_main_commit_is_required(self) -> None:
        self.assertIn('$env:GITHUB_REPOSITORY -cne "oglassdev/jet3-rs"', self.replica)
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", self.replica)
        self.assertIn("$head -cne $env:GITHUB_SHA", self.replica)
        self.assertGreaterEqual(
            self.workflow.count("status --porcelain=v1 --untracked-files=all"), 2
        )
        gate = (
            r"github\.event_name == 'workflow_dispatch' &&\s+"
            r"github\.ref == 'refs/heads/main' &&\s+"
            r"inputs\.execute_a4_campaign"
        )
        self.assertRegex(self.replica, gate)
        self.assertRegex(self.fan_in, gate)

    def test_fan_in_materializes_holdout_inside_lazy_analyzer_callback(self) -> None:
        self.assertEqual(self.fan_in.count("Download-A4Artifact.ps1"), 3)
        assemble = self.fan_in.index("a4_bundle.py assemble")
        analyze = self.fan_in.index("a4_bundle.py analyze", assemble)
        finalize = self.fan_in.index("a4_bundle.py finalize", analyze)
        validate = self.fan_in.index("a4_bundle.py validate", finalize)
        independent = self.fan_in.index("a4_independent_validator.py", validate)
        self.assertLess(assemble, analyze)
        self.assertLess(analyze, finalize)
        self.assertLess(finalize, validate)
        self.assertLess(validate, independent)
        self.assertEqual(self.fan_in.count("--replica-root"), 2)
        self.assertIn("--holdout-command-executable", self.fan_in)
        self.assertIn("--holdout-command-argument=windows-dao-a4-replica-3", self.fan_in)
        self.assertNotIn("--freeze-only", self.workflow)
        self.assertNotIn("--resume", self.workflow)
        bundle_source = (SCRIPTS / "a4_bundle.py").read_text(encoding="utf-8")
        freeze = bundle_source.index('with candidate_path.open("xb")')
        materialize = bundle_source.index("subprocess.run(", freeze)
        provider = bundle_source.index("run_holdout_process(", materialize)
        self.assertLess(freeze, materialize)
        self.assertLess(materialize, provider)

    def test_independent_validator_is_a_separate_process_and_report(self) -> None:
        step = self.fan_in.split(
            "      - name: Independently recompute the retained A4 bundle", 1
        )[1].split("      - name: Record retained A4 fan-in status", 1)[0]
        self.assertIn("id: independent", step)
        self.assertIn("--validator-commit $env:GITHUB_SHA", step)
        self.assertIn("--output $report", step)
        self.assertNotIn("--plan", step)
        self.assertNotIn("--revision", step)
        self.assertNotIn("jet3-a4-bundle\\validation", step)

    def test_retention_and_campaign_timing_are_fail_closed(self) -> None:
        self.assertIn("campaign-start.json", self.fan_in)
        self.assertIn("campaign_elapsed_seconds", self.fan_in)
        self.assertIn("if (-not $withinPlan)", self.fan_in)
        self.assertIn("fan-in-status.json", self.fan_in)
        self.assertIn("if: success()", self.fan_in.split(
            "Upload retained A4 bundle", 1
        )[1].split("Upload bounded A4 fan-in diagnostics", 1)[0])
        diagnostics = self.fan_in.split(
            "Upload bounded A4 fan-in diagnostics", 1
        )[1]
        self.assertIn("if: always()", diagnostics)
        self.assertIn("retention-days: 14", diagnostics)

    def test_all_actions_are_commit_pinned(self) -> None:
        actions = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertEqual(len(actions), 10)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertEqual(self.workflow.count("persist-credentials: false"), 3)


if __name__ == "__main__":
    unittest.main()
