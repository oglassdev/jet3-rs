from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
SHARED = SCRIPTS / "shared/BoundedProcess.ps1"
NATIVE = SCRIPTS / "shared/BoundedProcess.Native.cs"


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
        self.native = NATIVE.read_text(encoding="utf-8")

    def test_module_is_neutral_and_owns_the_complete_process_lifecycle(self) -> None:
        for function in (
            "ConvertTo-BoundedProcessCommandLineArgument",
            "Assert-BoundedProcessLimits",
            "Initialize-BoundedProcessJobNative",
            "Stop-BoundedProcessJob",
            "Read-BoundedProcessOutput",
            "Invoke-BoundedChildProcess",
        ):
            self.assertIn(f"function {function}", self.source)
        self.assertNotIn("M3", self.source)
        self.assertNotIn("M4", self.source)
        self.assertNotIn("M3", self.native)
        self.assertNotIn("M4", self.native)
        self.assertIn("StartSuspendedInJob", self.native)
        self.assertIn("CreateSuspended |", self.native)
        self.assertIn("JobObjectLimitKillOnJobClose", self.native)
        self.assertIn("AssignProcessToJobObject", self.native)
        self.assertIn("TerminateJobObject", self.native)
        self.assertIn("QueryInformationJobObject", self.native)
        self.assertLess(
            self.native.index("AssignProcess(job, process);"),
            self.native.index("ResumeThread(thread);"),
        )
        self.assertIn("SafeWaitHandle", self.native)
        self.assertIn("$launch.ExitCode -ne 0", self.source)
        self.assertIn("$launch.Dispose()", self.source)
        self.assertIn('Add-Type -Path $nativeSource', self.source)

    def test_native_and_orchestration_files_stay_below_reviewed_limits(self) -> None:
        self.assertLessEqual(len(self.source.splitlines()), 400)
        self.assertLessEqual(len(self.native.splitlines()), 800)

    def test_only_the_three_standard_stream_handles_are_inherited(self) -> None:
        self.assertIn("ProcThreadAttributeHandleList", self.native)
        self.assertIn("InitializeProcThreadAttributeList", self.native)
        self.assertIn("UpdateProcThreadAttribute(", self.native)
        self.assertIn("ExtendedStartupInfoPresent", self.native)
        self.assertIn(
            "CreateRestrictedHandleList(\n"
            "                stdinRead,\n"
            "                stdoutWrite,\n"
            "                stderrWrite",
            self.native,
        )
        self.assertEqual(self.native.count("Marshal.WriteIntPtr(handles,"), 3)

    def test_limits_are_positive_caller_labeled_and_hard_capped(self) -> None:
        self.assertIn("$TimeoutSeconds -lt 1", self.source)
        self.assertGreaterEqual(
            self.source.count("[int]$ReviewedTimeoutCeilingSeconds = 120"), 3
        )
        self.assertIn("$ReviewedTimeoutCeilingSeconds -gt 1800", self.source)
        self.assertIn(
            "$TimeoutSeconds -gt $ReviewedTimeoutCeilingSeconds", self.source
        )
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
        self.assertIn("$Launch.StandardOutput.ReadAsync(", self.source)
        self.assertIn("$Launch.StandardError.ReadAsync(", self.source)
        self.assertIn("[Threading.Tasks.Task]::WaitAny(", self.source)
        self.assertGreaterEqual(
            self.source.count("$stdout.Length + $stderr.Length + $read"), 2
        )
        self.assertIn(
            "$outDone -and $errDone -and $Launch.HasExited", self.source
        )
        self.assertIn(
            "$Launch.HasExited -and\n"
            "                -not ($outDone -and $errDone)",
            self.source,
        )

    def test_launch_has_no_pid_based_or_start_then_assign_fallback(self) -> None:
        combined = self.source + self.native
        self.assertNotIn("taskkill.exe", combined)
        self.assertNotIn("Stop-BoundedProcessTree", combined)
        self.assertNotIn("[Diagnostics.Process]::Start", combined)
        self.assertNotIn("$Process.Id", combined)
        self.assertNotIn(".WaitForExit()", combined)
        self.assertNotIn(".ArgumentList", combined)


class BoundedProcessWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.powershell = windows_powershell()
        if self.powershell is None:
            self.skipTest("Windows PowerShell is unavailable")

    # Process-launch tests pass explicit harness bounds for slow hosted Windows runners.
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
            release = root / "release-descendant.txt"
            completed = root / "descendant-completed.txt"
            child_command = (
                f"Set-Content -LiteralPath '{quoted(started)}' -Value 'started';"
                f"while(-not (Test-Path -LiteralPath '{quoted(release)}')){{"
                "Start-Sleep -Milliseconds 25};"
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
                "$deadline=[DateTime]::UtcNow.AddSeconds(15);"
                f"while(-not (Test-Path -LiteralPath '{quoted(started)}')){{"
                "if([DateTime]::UtcNow -ge $deadline){throw 'child did not start'};"
                "Start-Sleep -Milliseconds 25};"
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
                "-TimeoutSeconds 20 -MaximumOutputBytes 1MB|Out-Null;exit 9"
                "}catch{$message=$_.Exception.Message};"
                f"Set-Content -LiteralPath '{quoted(release)}' -Value 'release';"
                "Start-Sleep -Seconds 3;"
                f"[Console]::Write($message+'|'+"
                f"(Test-Path -LiteralPath '{quoted(started)}')+'|'+"
                f"(Test-Path -LiteralPath '{quoted(completed)}'))"
            )
            started_at = time.monotonic()
            result = self._run(command, timeout=120)
            elapsed = time.monotonic() - started_at
            # Catch late ceiling enforcement below the 120 s harness cap while
            # retaining ample headroom for slow Add-Type compilation on hosted runners.
            self.assertLess(
                elapsed,
                90,
                f"20-second wall-clock ceiling took {elapsed:.1f} seconds to fire",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "probe worker exceeded its wall-clock ceiling.|True|False",
            )

    def test_successful_root_exit_still_terminates_its_owned_descendant(self) -> None:
        assert self.powershell is not None
        with tempfile.TemporaryDirectory(prefix="bounded-job-") as temporary:
            root = Path(temporary)
            started = root / "descendant-started.txt"
            release = root / "release-descendant.txt"
            completed = root / "descendant-completed.txt"
            child_command = (
                f"Set-Content -LiteralPath '{quoted(started)}' -Value 'started';"
                f"while(-not (Test-Path -LiteralPath '{quoted(release)}')){{"
                "Start-Sleep -Milliseconds 25};"
                f"Set-Content -LiteralPath '{quoted(completed)}' -Value 'unexpected'"
            )
            encoded_child = base64.b64encode(
                child_command.encode("utf-16-le")
            ).decode("ascii")
            parent = root / "exiting-parent.ps1"
            parent.write_text(
                "param([string]$PowerShell,[string]$ChildCommand,"
                "[string]$Started);"
                "Start-Process -FilePath $PowerShell -ArgumentList @("
                "'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',"
                "'-EncodedCommand',$ChildCommand);"
                "$deadline=[DateTime]::UtcNow.AddSeconds(15);"
                "while(-not (Test-Path -LiteralPath $Started)){"
                "if([DateTime]::UtcNow -ge $deadline){throw 'child did not start'};"
                "Start-Sleep -Milliseconds 25}",
                encoding="utf-8",
            )
            command = (
                f". '{quoted(SHARED)}';"
                f"Invoke-BoundedChildProcess -Executable '{quoted(self.powershell)}' "
                "-Arguments @('-NoProfile','-NonInteractive','-ExecutionPolicy',"
                f"'Bypass','-File','{quoted(parent)}','-PowerShell',"
                f"'{quoted(self.powershell)}','-ChildCommand','{encoded_child}',"
                f"'-Started','{quoted(started)}') "
                "-CallerLabel 'root-exit-probe' "
                "-TimeoutSeconds 20 -MaximumOutputBytes 1MB|Out-Null;"
                f"Set-Content -LiteralPath '{quoted(release)}' -Value 'release';"
                "Start-Sleep -Seconds 3;"
                "[Console]::Write(('{0}|{1}' -f "
                f"(Test-Path -LiteralPath '{quoted(started)}'),"
                f"(Test-Path -LiteralPath '{quoted(completed)}')))"
            )
            result = self._run(command, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "True|False")

    def test_immediate_native_spawn_and_exit_cannot_escape_assignment(self) -> None:
        assert self.powershell is not None
        with tempfile.TemporaryDirectory(prefix="bounded-atomic-") as temporary:
            root = Path(temporary)
            source = root / "immediate-spawner.cs"
            executable = root / "immediate-spawner.exe"
            completed = root / "escaped-descendant.txt"
            source.write_text(
                "using System.Diagnostics;"
                "public static class ImmediateSpawner {"
                "public static int Main(string[] args) {"
                "ProcessStartInfo start = new ProcessStartInfo();"
                "start.FileName = args[0];"
                "start.Arguments = \"-NoProfile -NonInteractive "
                "-ExecutionPolicy Bypass -EncodedCommand \" + args[1];"
                "start.UseShellExecute = false;"
                "start.CreateNoWindow = true;"
                "Process.Start(start);"
                "return 0;"
                "}}",
                encoding="utf-8",
            )
            child_command = (
                "Start-Sleep -Seconds 2;"
                f"Set-Content -LiteralPath '{quoted(completed)}' "
                "-Value 'escaped'"
            )
            encoded_child = base64.b64encode(
                child_command.encode("utf-16-le")
            ).decode("ascii")
            command = (
                "$source=[IO.File]::ReadAllText("
                f"'{quoted(source)}');"
                "Add-Type -TypeDefinition $source "
                f"-OutputAssembly '{quoted(executable)}' "
                "-OutputType ConsoleApplication;"
                f". '{quoted(SHARED)}';"
                "1..24|ForEach-Object{"
                f"Invoke-BoundedChildProcess -Executable '{quoted(executable)}' "
                f"-Arguments @('{quoted(self.powershell)}','{encoded_child}') "
                "-CallerLabel 'atomic-spawn-probe' "
                "-TimeoutSeconds 10 -MaximumOutputBytes 1MB|Out-Null};"
                "Start-Sleep -Seconds 4;"
                f"[Console]::Write((Test-Path -LiteralPath '{quoted(completed)}'))"
            )
            result = self._run(command, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "False")


if __name__ == "__main__":
    unittest.main()
