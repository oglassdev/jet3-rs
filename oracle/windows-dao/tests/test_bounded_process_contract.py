from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
SHARED = SCRIPTS / "shared/BoundedProcess.ps1"


def windows_powershell() -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windir
        / "SysWOW64"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe",
        windir
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def quoted(path: Path) -> str:
    return str(path).replace("'", "''")


class BoundedProcessSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SHARED.read_text(encoding="utf-8")

    def test_module_is_neutral_and_owns_the_complete_process_lifecycle(self) -> None:
        for function in (
            "ConvertTo-BoundedProcessCommandLineArgument",
            "Assert-BoundedProcessLimits",
            "Stop-BoundedProcessTree",
            "Read-BoundedProcessOutput",
            "Invoke-BoundedChildProcess",
        ):
            self.assertIn(f"function {function}", self.source)
        self.assertNotIn("M3", self.source)
        self.assertNotIn("M4", self.source)
        self.assertIn("[Diagnostics.Process]::Start", self.source)
        self.assertIn("$process.ExitCode -ne 0", self.source)
        self.assertIn("$process.Dispose()", self.source)

    def test_limits_are_positive_caller_labeled_and_hard_capped(self) -> None:
        self.assertIn("$TimeoutSeconds -lt 1", self.source)
        self.assertIn("$TimeoutSeconds -gt 120", self.source)
        self.assertIn("$MaximumOutputBytes -lt 1", self.source)
        self.assertIn("$MaximumOutputBytes -gt 1MB", self.source)
        self.assertIn("[string]::IsNullOrWhiteSpace($CallerLabel)", self.source)
        self.assertIn(
            '"$CallerLabel worker exceeded its wall-clock ceiling."', self.source
        )
        self.assertIn(
            '"$CallerLabel worker output exceeded its byte ceiling."', self.source
        )
        self.assertIn('"$CallerLabel worker failed: $stderr"', self.source)

    def test_stdout_and_stderr_are_drained_concurrently_under_one_byte_cap(self) -> None:
        self.assertIn("$Process.StandardOutput.BaseStream.ReadAsync(", self.source)
        self.assertIn("$Process.StandardError.BaseStream.ReadAsync(", self.source)
        self.assertIn("[Threading.Tasks.Task]::WaitAny(", self.source)
        self.assertGreaterEqual(
            self.source.count("$stdout.Length + $stderr.Length + $read"), 2
        )
        self.assertIn(
            "$outDone -and $errDone -and $Process.HasExited", self.source
        )

    def test_termination_targets_the_windows_process_tree_with_safe_fallback(self) -> None:
        self.assertIn('"System32/taskkill.exe"', self.source)
        self.assertIn("/PID $Process.Id /T /F", self.source)
        self.assertIn("$Process.Kill()", self.source)
        self.assertNotIn("Kill($true)", self.source)
        self.assertNotIn(".ArgumentList", self.source)


class BoundedProcessWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.powershell = windows_powershell()
        if self.powershell is None:
            self.skipTest("Windows PowerShell is unavailable")

    def _run(
        self, command: str, *, timeout: int = 20
    ) -> subprocess.CompletedProcess[str]:
        assert self.powershell is not None
        return subprocess.run(
            [
                str(self.powershell),
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
            timeout=timeout,
        )

    def test_limit_rejections_precede_launch_and_retain_the_caller_label(self) -> None:
        command = (
            f". '{quoted(SHARED)}';"
            "$messages=@();"
            "try{Invoke-BoundedChildProcess -Executable 'not-used.exe' "
            "-Arguments @() -CallerLabel 'M4' -TimeoutSeconds 121 "
            "-MaximumOutputBytes 1MB|Out-Null}catch{$messages+=$_.Exception.Message};"
            "try{Invoke-BoundedChildProcess -Executable 'not-used.exe' "
            "-Arguments @() -CallerLabel 'M4' -TimeoutSeconds 1 "
            "-MaximumOutputBytes 1048577|Out-Null}catch{$messages+=$_.Exception.Message};"
            "try{Invoke-BoundedChildProcess -Executable 'not-used.exe' "
            "-Arguments @() -CallerLabel '' -TimeoutSeconds 1 "
            "-MaximumOutputBytes 1MB|Out-Null}catch{$messages+=$_.Exception.Message};"
            "[Console]::Write(($messages|ConvertTo-Json -Compress))"
        )
        result = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [
                "M4 child timeout is outside the reviewed ceiling.",
                "M4 child output limit is outside the reviewed ceiling.",
                "Bounded process caller label is invalid.",
            ],
        )

    def test_timeout_terminates_a_descendant_before_it_can_write(self) -> None:
        assert self.powershell is not None
        with tempfile.TemporaryDirectory(prefix="bounded-tree-") as temporary:
            root = Path(temporary)
            started = root / "descendant-started.txt"
            completed = root / "descendant-completed.txt"
            child_command = (
                f"Set-Content -LiteralPath '{quoted(started)}' -Value 'started';"
                "Start-Sleep -Seconds 4;"
                f"Set-Content -LiteralPath '{quoted(completed)}' -Value 'unexpected'"
            )
            encoded_child = base64.b64encode(
                child_command.encode("utf-16-le")
            ).decode("ascii")
            parent = root / "parent.ps1"
            parent.write_text(
                "param([string]$PowerShell,[string]$ChildCommand);"
                "Start-Process -FilePath $PowerShell -ArgumentList @("
                "'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',"
                "'-EncodedCommand',$ChildCommand);"
                "Start-Sleep -Seconds 30",
                encoding="utf-8",
            )
            command = (
                f". '{quoted(SHARED)}';"
                "try{"
                f"Invoke-BoundedChildProcess -Executable '{quoted(self.powershell)}' "
                "-Arguments @('-NoProfile','-NonInteractive','-ExecutionPolicy',"
                f"'Bypass','-File','{quoted(parent)}','-PowerShell',"
                f"'{quoted(self.powershell)}','-ChildCommand','{encoded_child}') "
                "-CallerLabel 'probe' "
                "-TimeoutSeconds 1 -MaximumOutputBytes 1MB|Out-Null;exit 9"
                "}catch{$message=$_.Exception.Message};"
                "Start-Sleep -Seconds 5;"
                f"[Console]::Write($message+'|'+"
                f"(Test-Path -LiteralPath '{quoted(started)}')+'|'+"
                f"(Test-Path -LiteralPath '{quoted(completed)}'))"
            )
            result = self._run(command, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "probe worker exceeded its wall-clock ceiling.|True|False",
            )


if __name__ == "__main__":
    unittest.main()
