from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import run_acceptance as runner  # noqa: E402


class AcceptanceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.commit = "a" * 40
        self.recorded: list[dict[str, object]] = []

    def _record(self, **arguments: object) -> Path:
        self.recorded.append(arguments)
        return self.repo / "record.json"

    def _summary(self, **arguments: object) -> Path:
        run_id = str(arguments["run_id"])
        statuses = [str(item["status"]) for item in self.recorded]
        counts = {
            status: statuses.count(status)
            for status in ("PASS", "FAIL", "BLOCKED")
        }
        status = "FAIL" if counts["FAIL"] else "BLOCKED" if counts["BLOCKED"] else "PASS"
        path = (
            self.repo
            / "artifacts"
            / "acceptance"
            / self.commit
            / run_id
            / "summary.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"status": status, "counts": counts}),
            encoding="utf-8",
        )
        return path

    def _run_with(
        self,
        results: list[subprocess.CompletedProcess[str]],
        *,
        dirty: bool = False,
    ) -> int:
        with (
            mock.patch.object(
                runner.acceptance_report,
                "repository_root",
                return_value=self.repo,
            ),
            mock.patch.object(
                runner.acceptance_report,
                "git_state",
                return_value=(self.commit, dirty),
            ),
            mock.patch.object(
                runner.acceptance_report,
                "record_gate",
                side_effect=self._record,
            ),
            mock.patch.object(
                runner.acceptance_report,
                "summarize",
                side_effect=self._summary,
            ),
            mock.patch.object(runner.subprocess, "run", side_effect=results),
            redirect_stdout(StringIO()),
        ):
            return runner.run(repo_root=self.repo, run_id="test-run")

    def test_runs_and_records_every_gate_in_canonical_order(self) -> None:
        results = [
            subprocess.CompletedProcess(
                ["gate", gate],
                3,
                stdout=f"{gate} output\n",
                stderr=f"BLOCKED: {gate} precise blocker\n",
            )
            for gate in runner.acceptance_report.GATES
        ]
        self.assertEqual(self._run_with(results), 3)
        self.assertEqual(
            [item["gate"] for item in self.recorded],
            list(runner.acceptance_report.GATES),
        )
        self.assertTrue(all(item["status"] == "BLOCKED" for item in self.recorded))
        self.assertEqual(self.recorded[0]["reason"], "G0 precise blocker")
        self.assertEqual(len(self.recorded), 9)

    def test_failed_check_makes_result_fail_without_skipping_later_gates(self) -> None:
        results = [
            subprocess.CompletedProcess(["gate", "G0"], 1, stdout="", stderr="bad\n"),
            *[
                subprocess.CompletedProcess(
                    ["gate", gate],
                    3,
                    stdout="",
                    stderr=f"BLOCKED: {gate} unavailable\n",
                )
                for gate in runner.acceptance_report.GATES[1:]
            ],
        ]
        self.assertEqual(self._run_with(results), 1)
        self.assertEqual(self.recorded[0]["status"], "FAIL")
        self.assertEqual(len(self.recorded), 9)

    def test_pass_requires_zero_and_all_pass_returns_zero(self) -> None:
        results = [
            subprocess.CompletedProcess(["gate", gate], 0, stdout="", stderr="")
            for gate in runner.acceptance_report.GATES
        ]
        self.assertEqual(self._run_with(results), 0)
        self.assertTrue(all(item["status"] == "PASS" for item in self.recorded))

    def test_dirty_tree_forces_g8_blocked_and_nonzero_result(self) -> None:
        results = [
            subprocess.CompletedProcess(["gate", gate], 0, stdout="", stderr="")
            for gate in runner.acceptance_report.GATES
        ]
        self.assertEqual(self._run_with(results, dirty=True), 3)
        self.assertTrue(all(item["status"] == "PASS" for item in self.recorded[:-1]))
        self.assertEqual(self.recorded[-1]["status"], "BLOCKED")
        self.assertEqual(self.recorded[-1]["exit_code"], 3)
        self.assertIn("dirty", str(self.recorded[-1]["reason"]))

    def test_rejects_unsafe_run_id_before_creating_logs(self) -> None:
        with mock.patch.object(
            runner.acceptance_report,
            "repository_root",
            return_value=self.repo,
        ):
            with self.assertRaisesRegex(
                runner.acceptance_report.ReportError, "invalid acceptance run ID"
            ):
                runner.run(repo_root=self.repo, run_id="../escape")
        self.assertFalse((self.repo / "artifacts").exists())

    def test_immutable_log_write_rejects_existing_path(self) -> None:
        path = self.repo / "log.txt"
        path.write_text("first\n", encoding="utf-8")
        with self.assertRaisesRegex(
            runner.acceptance_report.ReportError, "refusing to overwrite"
        ):
            runner._write_new(path, "second\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "first\n")

    def test_blocked_reason_uses_last_explicit_marker(self) -> None:
        stderr = "BLOCKED: first condition\ncontext\nBLOCKED: final condition\n"
        self.assertEqual(
            runner._blocked_reason("G5", stderr),
            "final condition",
        )

    def test_command_launch_error_is_a_recordable_failure(self) -> None:
        with mock.patch.object(
            runner.subprocess,
            "run",
            side_effect=FileNotFoundError("missing helper"),
        ):
            result = runner._execute(["missing-helper", "G0"], self.repo)
        self.assertEqual(result.returncode, 127)
        self.assertIn("cannot execute", result.stderr)

    def test_windows_default_invokes_shell_script_through_bash(self) -> None:
        with mock.patch.object(runner.os, "name", "nt"):
            self.assertEqual(
                runner._default_gate_command(),
                ("bash", "./scripts/run-acceptance-gate.sh"),
            )

    def test_signal_exit_is_normalized_to_nonnegative_shell_status(self) -> None:
        interrupted = subprocess.CompletedProcess(
            ["gate", "G0"],
            -9,
            stdout="partial\n",
            stderr="",
        )
        with mock.patch.object(runner.subprocess, "run", return_value=interrupted):
            result = runner._execute(["gate", "G0"], self.repo)
        self.assertEqual(result.returncode, 137)
        self.assertIn("signal 9", result.stderr)


if __name__ == "__main__":
    unittest.main()
