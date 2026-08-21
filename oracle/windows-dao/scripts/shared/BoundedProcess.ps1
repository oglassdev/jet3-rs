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
        [long]$MaximumOutputBytes,
        [int]$ReviewedTimeoutCeilingSeconds = 120
    )

    if (
        [string]::IsNullOrWhiteSpace($CallerLabel) -or
        $CallerLabel.Length -gt 64 -or
        $CallerLabel -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._ -]*$'
    ) {
        throw "Bounded process caller label is invalid."
    }
    if (
        $ReviewedTimeoutCeilingSeconds -lt 1 -or
        $ReviewedTimeoutCeilingSeconds -gt 1800
    ) {
        throw "$CallerLabel reviewed timeout ceiling is invalid."
    }
    if (
        $TimeoutSeconds -lt 1 -or
        $TimeoutSeconds -gt $ReviewedTimeoutCeilingSeconds
    ) {
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
    $nativeSource = Join-Path $PSScriptRoot "BoundedProcess.Native.cs"
    Add-Type -Path $nativeSource
}

function Stop-BoundedProcessJob {
    param([Parameter(Mandatory = $true)][Jet3BoundedProcessLaunch]$Launch)

    $Launch.TerminateOwnedJob()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (
            $Launch.ActiveProcessCount() -ne 0 -and
            $clock.ElapsedMilliseconds -lt 5000
        ) {
            Start-Sleep -Milliseconds 10
        }
        if ($Launch.ActiveProcessCount() -ne 0) {
            throw "Owned process job did not terminate within its ceiling."
        }
        if (-not $Launch.WaitForExit(1000)) {
            throw "Owned process exit could not be observed within its ceiling."
        }
    }
    finally {
        $clock.Stop()
    }
}

function Read-BoundedProcessOutput {
    param(
        [Parameter(Mandatory = $true)]
        [Jet3BoundedProcessLaunch]$Launch,
        [string]$CallerLabel,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes,
        [int]$ReviewedTimeoutCeilingSeconds = 120
    )

    Assert-BoundedProcessLimits -CallerLabel $CallerLabel `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes `
        -ReviewedTimeoutCeilingSeconds $ReviewedTimeoutCeilingSeconds
    $stdout = New-Object IO.MemoryStream
    $stderr = New-Object IO.MemoryStream
    $outBuffer = New-Object byte[] 4096
    $errBuffer = New-Object byte[] 4096
    $outTask = $Launch.StandardOutput.ReadAsync(
        $outBuffer, 0, $outBuffer.Length
    )
    $errTask = $Launch.StandardError.ReadAsync(
        $errBuffer, 0, $errBuffer.Length
    )
    $outDone = $false
    $errDone = $false
    $jobQuiesced = $false
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not ($outDone -and $errDone -and $Launch.HasExited)) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Stop-BoundedProcessJob -Launch $Launch
                throw "$CallerLabel worker exceeded its wall-clock ceiling."
            }
            if (
                -not $jobQuiesced -and
                $Launch.HasExited -and
                -not ($outDone -and $errDone)
            ) {
                # A descendant can inherit redirected handles after its root
                # exits. Terminate the owned job so those pipes close now.
                Stop-BoundedProcessJob -Launch $Launch
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
                        Stop-BoundedProcessJob -Launch $Launch
                        throw "$CallerLabel worker output exceeded its byte ceiling."
                    }
                    $stdout.Write($outBuffer, 0, $read)
                    $outTask = $Launch.StandardOutput.ReadAsync(
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
                        Stop-BoundedProcessJob -Launch $Launch
                        throw "$CallerLabel worker output exceeded its byte ceiling."
                    }
                    $stderr.Write($errBuffer, 0, $read)
                    $errTask = $Launch.StandardError.ReadAsync(
                        $errBuffer, 0, $errBuffer.Length
                    )
                }
            }
        }
        if (-not $Launch.WaitForExit(1000)) {
            throw "$CallerLabel worker exit could not be observed."
        }
        $encoding = New-Object Text.UTF8Encoding($false, $false)
        return [ordered]@{
            stdout = $encoding.GetString($stdout.ToArray())
            stderr = $encoding.GetString($stderr.ToArray())
        }
    }
    catch {
        $failure = $_
        $encoding = New-Object Text.UTF8Encoding($false, $false)
        $failure.Exception.Data["BoundedProcess.Stdout"] =
            $encoding.GetString($stdout.ToArray())
        $failure.Exception.Data["BoundedProcess.Stderr"] =
            $encoding.GetString($stderr.ToArray())
        try {
            if ($Launch.HasExited) {
                $failure.Exception.Data["BoundedProcess.ExitCode"] =
                    [int]$Launch.ExitCode
            }
        }
        catch { }
        throw $failure
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
        [long]$MaximumOutputBytes = 1MB,
        [int]$ReviewedTimeoutCeilingSeconds = 120,
        [switch]$ReturnFailureRecord
    )

    Assert-BoundedProcessLimits -CallerLabel $CallerLabel `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes `
        -ReviewedTimeoutCeilingSeconds $ReviewedTimeoutCeilingSeconds
    $executablePath = [IO.Path]::GetFullPath($Executable)
    $argumentText = (
        @($Arguments | ForEach-Object {
            ConvertTo-BoundedProcessCommandLineArgument -Value $_
        }) -join " "
    )
    $commandLine = ConvertTo-BoundedProcessCommandLineArgument `
        -Value $executablePath
    if ($argumentText.Length -gt 0) {
        $commandLine += " " + $argumentText
    }
    if ($commandLine.Length -gt 32766) {
        throw "$CallerLabel worker command line exceeds the Windows ceiling."
    }
    Initialize-BoundedProcessJobNative
    $launch = [Jet3BoundedProcessJobNative]::StartSuspendedInJob(
        $executablePath,
        $commandLine
    )
    try {
        $captured = Read-BoundedProcessOutput -Launch $launch `
            -CallerLabel $CallerLabel `
            -TimeoutSeconds $TimeoutSeconds `
            -MaximumOutputBytes $MaximumOutputBytes `
            -ReviewedTimeoutCeilingSeconds $ReviewedTimeoutCeilingSeconds
        if ($ReturnFailureRecord) {
            return [pscustomobject]@{
                exit_code = [int]$launch.ExitCode
                stdout = [Convert]::ToString($captured.stdout)
                stderr = [Convert]::ToString($captured.stderr)
            }
        }
        if ($launch.ExitCode -ne 0) {
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
            Stop-BoundedProcessJob -Launch $launch
        }
        finally {
            $launch.Dispose()
        }
    }
}
