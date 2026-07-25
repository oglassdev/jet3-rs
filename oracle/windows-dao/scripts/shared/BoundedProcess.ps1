Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-BoundedProcessCommandLineArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value -cnotmatch '[\s"]' -and $Value.Length -gt 0) {
        return $Value
    }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
        }
        else {
            if ($slashes -gt 0) {
                [void]$builder.Append(('\' * $slashes))
            }
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Assert-BoundedProcessLimits {
    param(
        [string]$CallerLabel,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

    if (
        [string]::IsNullOrWhiteSpace($CallerLabel) -or
        $CallerLabel.Length -gt 64 -or
        $CallerLabel -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._ -]*$'
    ) {
        throw "Bounded process caller label is invalid."
    }
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 120) {
        throw "$CallerLabel child timeout is outside the reviewed ceiling."
    }
    if ($MaximumOutputBytes -lt 1 -or $MaximumOutputBytes -gt 1MB) {
        throw "$CallerLabel child output limit is outside the reviewed ceiling."
    }
}

function Initialize-BoundedProcessJobNative {
    if ("Jet3BoundedProcessJobNative" -as [type]) {
        return
    }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class Jet3BoundedProcessJobNative
{
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectBasicAccountingInformation = 1;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInformation
    {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicAccountingInformation
    {
        public Int64 TotalUserTime;
        public Int64 TotalKernelTime;
        public Int64 ThisPeriodTotalUserTime;
        public Int64 ThisPeriodTotalKernelTime;
        public UInt32 TotalPageFaultCount;
        public UInt32 TotalProcesses;
        public UInt32 ActiveProcesses;
        public UInt32 TotalTerminatedProcesses;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(
        IntPtr jobAttributes,
        string name
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        UInt32 informationClass,
        ref ExtendedLimitInformation information,
        UInt32 informationLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(
        IntPtr job,
        IntPtr process
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(
        IntPtr job,
        UInt32 exitCode
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(
        IntPtr job,
        UInt32 informationClass,
        out BasicAccountingInformation information,
        UInt32 informationLength,
        IntPtr returnLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private static void ThrowLastError(string operation)
    {
        throw new Win32Exception(
            Marshal.GetLastWin32Error(),
            operation + " failed"
        );
    }

    public static IntPtr CreateKillOnCloseJob()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            ThrowLastError("CreateJobObject");
        }
        ExtendedLimitInformation information =
            new ExtendedLimitInformation();
        information.BasicLimitInformation.LimitFlags =
            JobObjectLimitKillOnJobClose;
        if (!SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ref information,
            (UInt32)Marshal.SizeOf(typeof(ExtendedLimitInformation))
        ))
        {
            int error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new Win32Exception(
                error,
                "SetInformationJobObject failed"
            );
        }
        return job;
    }

    public static void AssignProcess(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
        {
            ThrowLastError("AssignProcessToJobObject");
        }
    }

    public static void Terminate(IntPtr job)
    {
        if (!TerminateJobObject(job, 1))
        {
            ThrowLastError("TerminateJobObject");
        }
    }

    public static UInt32 ActiveProcessCount(IntPtr job)
    {
        BasicAccountingInformation information =
            new BasicAccountingInformation();
        if (!QueryInformationJobObject(
            job,
            JobObjectBasicAccountingInformation,
            out information,
            (UInt32)Marshal.SizeOf(typeof(BasicAccountingInformation)),
            IntPtr.Zero
        ))
        {
            ThrowLastError("QueryInformationJobObject");
        }
        return information.ActiveProcesses;
    }

    public static void CloseJob(IntPtr job)
    {
        if (job != IntPtr.Zero && !CloseHandle(job))
        {
            ThrowLastError("CloseHandle");
        }
    }
}
'@
}

function New-BoundedProcessJob {
    param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)

    Initialize-BoundedProcessJobNative
    $handle = [IntPtr]::Zero
    try {
        $handle = [Jet3BoundedProcessJobNative]::CreateKillOnCloseJob()
        [Jet3BoundedProcessJobNative]::AssignProcess(
            $handle,
            $Process.Handle
        )
        return $handle
    }
    catch {
        if ($handle -ne [IntPtr]::Zero) {
            try {
                [Jet3BoundedProcessJobNative]::CloseJob($handle)
            }
            catch {}
        }
        throw
    }
}

function Stop-BoundedProcessJob {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$Handle,
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process
    )

    [Jet3BoundedProcessJobNative]::Terminate($Handle)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (
            [Jet3BoundedProcessJobNative]::ActiveProcessCount($Handle) -ne 0 -and
            $clock.ElapsedMilliseconds -lt 5000
        ) {
            Start-Sleep -Milliseconds 10
        }
        if (
            [Jet3BoundedProcessJobNative]::ActiveProcessCount($Handle) -ne 0
        ) {
            throw "Owned process job did not terminate within its ceiling."
        }
        if (-not $Process.WaitForExit(1000)) {
            throw "Owned process exit could not be observed within its ceiling."
        }
    }
    finally {
        $clock.Stop()
    }
}

function Get-BoundedTaskkillPath {
    $path = [IO.Path]::GetFullPath(
        (Join-Path ([Environment]::SystemDirectory) "taskkill.exe")
    )
    if (
        $path.StartsWith("\\", [StringComparison]::Ordinal) -or
        $path.Substring(2).Contains(":")
    ) {
        throw "Process-tree termination helper is not on a local path."
    }
    $cursor = $path
    $leaf = $true
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($leaf -and $item.PSIsContainer) -or
            (-not $leaf -and -not $item.PSIsContainer)
        ) {
            throw "Process-tree termination helper identity is unsafe."
        }
        $parent = [IO.Path]::GetDirectoryName($item.FullName)
        if (
            [string]::IsNullOrWhiteSpace($parent) -or
            $parent.Equals($cursor, [StringComparison]::OrdinalIgnoreCase)
        ) {
            break
        }
        $cursor = $parent
        $leaf = $false
    }
    return $path
}

function Stop-BoundedProcessTree {
    param([Diagnostics.Process]$Process)

    if ($Process.HasExited) {
        if (-not $Process.WaitForExit(1000)) {
            throw "Process exit could not be observed within its ceiling."
        }
        return
    }

    $terminationFailure = $null
    $taskkill = $null
    try {
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = Get-BoundedTaskkillPath
        $start.Arguments = "/PID $($Process.Id) /T /F"
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $false
        $start.RedirectStandardError = $false
        $taskkill = [Diagnostics.Process]::Start($start)
        if (-not $taskkill.WaitForExit(5000)) {
            try { $taskkill.Kill() } catch {}
            [void]$taskkill.WaitForExit(1000)
            $terminationFailure = (
                "Process-tree termination helper exceeded its ceiling."
            )
        }
        elseif ($taskkill.ExitCode -ne 0) {
            $terminationFailure = (
                "Process-tree termination helper could not prove termination."
            )
        }
    }
    catch {
        $terminationFailure = (
            "Process-tree termination helper failed: " + $_.Exception.Message
        )
    }
    finally {
        if ($null -ne $taskkill) {
            if (-not $taskkill.HasExited) {
                try { $taskkill.Kill() } catch {}
                [void]$taskkill.WaitForExit(1000)
            }
            $taskkill.Dispose()
        }
    }

    if (-not $Process.HasExited) {
        try { $Process.Kill() } catch {}
    }
    if (-not $Process.WaitForExit(5000)) {
        throw "Process-tree termination could not be observed within its ceiling."
    }
    if ($null -ne $terminationFailure) {
        throw $terminationFailure
    }
}

function Read-BoundedProcessOutput {
    param(
        [Diagnostics.Process]$Process,
        [IntPtr]$JobHandle,
        [string]$CallerLabel,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

    Assert-BoundedProcessLimits -CallerLabel $CallerLabel `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    $stdout = New-Object IO.MemoryStream
    $stderr = New-Object IO.MemoryStream
    $outBuffer = New-Object byte[] 4096
    $errBuffer = New-Object byte[] 4096
    $outTask = $Process.StandardOutput.BaseStream.ReadAsync(
        $outBuffer, 0, $outBuffer.Length
    )
    $errTask = $Process.StandardError.BaseStream.ReadAsync(
        $errBuffer, 0, $errBuffer.Length
    )
    $outDone = $false
    $errDone = $false
    $jobQuiesced = $false
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not ($outDone -and $errDone -and $Process.HasExited)) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Stop-BoundedProcessJob -Handle $JobHandle -Process $Process
                throw "$CallerLabel worker exceeded its wall-clock ceiling."
            }
            if (
                -not $jobQuiesced -and
                $Process.HasExited -and
                -not ($outDone -and $errDone)
            ) {
                # A descendant can inherit redirected handles after its root
                # exits. Terminate the owned job so those pipes close now.
                Stop-BoundedProcessJob -Handle $JobHandle -Process $Process
                $jobQuiesced = $true
            }
            $tasks = New-Object Collections.ArrayList
            $labels = New-Object Collections.ArrayList
            if (-not $outDone) {
                [void]$tasks.Add($outTask)
                [void]$labels.Add("stdout")
            }
            if (-not $errDone) {
                [void]$tasks.Add($errTask)
                [void]$labels.Add("stderr")
            }
            if ($tasks.Count -eq 0) {
                Start-Sleep -Milliseconds 10
                continue
            }
            $index = [Threading.Tasks.Task]::WaitAny(
                [Threading.Tasks.Task[]]$tasks, 100
            )
            if ($index -lt 0) { continue }
            $label = [string]$labels[$index]
            if ($label -ceq "stdout") {
                $read = $outTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $outDone = $true
                }
                else {
                    if (
                        $stdout.Length + $stderr.Length + $read -gt
                            $MaximumOutputBytes
                    ) {
                        Stop-BoundedProcessJob `
                            -Handle $JobHandle -Process $Process
                        throw "$CallerLabel worker output exceeded its byte ceiling."
                    }
                    $stdout.Write($outBuffer, 0, $read)
                    $outTask = $Process.StandardOutput.BaseStream.ReadAsync(
                        $outBuffer, 0, $outBuffer.Length
                    )
                }
            }
            else {
                $read = $errTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $errDone = $true
                }
                else {
                    if (
                        $stdout.Length + $stderr.Length + $read -gt
                            $MaximumOutputBytes
                    ) {
                        Stop-BoundedProcessJob `
                            -Handle $JobHandle -Process $Process
                        throw "$CallerLabel worker output exceeded its byte ceiling."
                    }
                    $stderr.Write($errBuffer, 0, $read)
                    $errTask = $Process.StandardError.BaseStream.ReadAsync(
                        $errBuffer, 0, $errBuffer.Length
                    )
                }
            }
        }
        if (-not $Process.WaitForExit(1000)) {
            throw "$CallerLabel worker exit could not be observed."
        }
        $encoding = New-Object Text.UTF8Encoding($false, $false)
        return [ordered]@{
            stdout = $encoding.GetString($stdout.ToArray())
            stderr = $encoding.GetString($stderr.ToArray())
        }
    }
    finally {
        $clock.Stop()
        $stdout.Dispose()
        $stderr.Dispose()
    }
}

function Invoke-BoundedChildProcess {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$CallerLabel,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes = 1MB
    )

    Assert-BoundedProcessLimits -CallerLabel $CallerLabel `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = [IO.Path]::GetFullPath($Executable)
    $start.Arguments = (
        @($Arguments | ForEach-Object {
            ConvertTo-BoundedProcessCommandLineArgument -Value $_
        }) -join " "
    )
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    Initialize-BoundedProcessJobNative
    $process = [Diagnostics.Process]::Start($start)
    $jobHandle = [IntPtr]::Zero
    try {
        try {
            $jobHandle = New-BoundedProcessJob -Process $process
        }
        catch {
            Stop-BoundedProcessTree -Process $process
            throw
        }
        $captured = Read-BoundedProcessOutput -Process $process `
            -JobHandle $jobHandle `
            -CallerLabel $CallerLabel `
            -TimeoutSeconds $TimeoutSeconds `
            -MaximumOutputBytes $MaximumOutputBytes
        if ($process.ExitCode -ne 0) {
            $stderr = [string]$captured.stderr
            if ($stderr.Length -gt 2000) {
                $stderr = $stderr.Substring(0, 2000)
            }
            throw "$CallerLabel worker failed: $stderr"
        }
        return $captured
    }
    finally {
        try {
            if ($jobHandle -ne [IntPtr]::Zero) {
                Stop-BoundedProcessJob `
                    -Handle $jobHandle -Process $process
            }
            elseif (-not $process.HasExited) {
                Stop-BoundedProcessTree -Process $process
            }
        }
        finally {
            if ($jobHandle -ne [IntPtr]::Zero) {
                try {
                    [Jet3BoundedProcessJobNative]::CloseJob($jobHandle)
                }
                catch {}
            }
            $process.Dispose()
        }
    }
}
