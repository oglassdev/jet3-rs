Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-M3CommandLineArgument {
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

function Stop-M3ProcessTree {
    param([Diagnostics.Process]$Process)

    if ($Process.HasExited) { return }
    $taskkill = Join-Path $env:SystemRoot "System32/taskkill.exe"
    if (Test-Path -LiteralPath $taskkill -PathType Leaf) {
        & $taskkill /PID $Process.Id /T /F 2>&1 | Out-Null
    }
    if (-not $Process.HasExited) {
        $Process.Kill()
    }
    $Process.WaitForExit()
}

function Read-M3BoundedProcessOutput {
    param(
        [Diagnostics.Process]$Process,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

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
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not $outDone -or -not $errDone) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Stop-M3ProcessTree -Process $Process
                throw "M3 worker exceeded its wall-clock ceiling."
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
                        Stop-M3ProcessTree -Process $Process
                        throw "M3 worker output exceeded its byte ceiling."
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
                        Stop-M3ProcessTree -Process $Process
                        throw "M3 worker output exceeded its byte ceiling."
                    }
                    $stderr.Write($errBuffer, 0, $read)
                    $errTask = $Process.StandardError.BaseStream.ReadAsync(
                        $errBuffer, 0, $errBuffer.Length
                    )
                }
            }
        }
        $Process.WaitForExit()
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

function Invoke-M3ChildProcess {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes = 1MB
    )

    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 120) {
        throw "M3 child timeout is outside the reviewed ceiling."
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = [IO.Path]::GetFullPath($Executable)
    $start.Arguments = (
        @($Arguments | ForEach-Object {
            ConvertTo-M3CommandLineArgument -Value $_
        }) -join " "
    )
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($start)
    try {
        $captured = Read-M3BoundedProcessOutput -Process $process `
            -TimeoutSeconds $TimeoutSeconds `
            -MaximumOutputBytes $MaximumOutputBytes
        if ($process.ExitCode -ne 0) {
            $stderr = [string]$captured.stderr
            if ($stderr.Length -gt 2000) {
                $stderr = $stderr.Substring(0, 2000)
            }
            throw "M3 worker failed: $stderr"
        }
        return $captured
    }
    finally {
        $process.Dispose()
    }
}
