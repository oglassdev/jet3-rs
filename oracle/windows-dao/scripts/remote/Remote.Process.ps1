Set-StrictMode -Version Latest

function ConvertTo-Jet3ProcessArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $result = '"'
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            $result += ('\' * (($slashes * 2) + 1)) + '"'
        }
        else {
            $result += ('\' * $slashes) + $character
        }
        $slashes = 0
    }
    return $result + ('\' * ($slashes * 2)) + '"'
}

function Assert-Jet3ProcessLimits {
    param(
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

    if ($TimeoutSeconds -lt 10 -or $TimeoutSeconds -gt 120) {
        throw "Process timeout must be between 10 and 120 seconds."
    }
    if ($MaximumOutputBytes -lt 4096 -or $MaximumOutputBytes -gt 1MB) {
        throw "Process output limit must be between 4096 bytes and 1 MiB."
    }
}

function Read-Jet3ProcessOutput {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes,
        [string]$Label
    )

    $stdout = New-Object Text.StringBuilder
    $stderr = New-Object Text.StringBuilder
    $stdoutBuffer = New-Object char[] 4096
    $stderrBuffer = New-Object char[] 4096
    $stdoutTask = $Process.StandardOutput.ReadAsync(
        $stdoutBuffer,
        0,
        $stdoutBuffer.Length
    )
    $stderrTask = $Process.StandardError.ReadAsync(
        $stderrBuffer,
        0,
        $stderrBuffer.Length
    )
    $stdoutDone = $false
    $stderrDone = $false
    $characterCount = [long]0
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not ($stdoutDone -and $stderrDone -and $Process.HasExited)) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                throw "$Label exceeded its wall-clock ceiling."
            }
            $tasks = New-Object Collections.ArrayList
            $labels = New-Object Collections.ArrayList
            if (-not $stdoutDone) {
                [void]$tasks.Add($stdoutTask)
                [void]$labels.Add("stdout")
            }
            if (-not $stderrDone) {
                [void]$tasks.Add($stderrTask)
                [void]$labels.Add("stderr")
            }
            if ($tasks.Count -eq 0) {
                Start-Sleep -Milliseconds 10
                continue
            }
            $index = [Threading.Tasks.Task]::WaitAny(
                [Threading.Tasks.Task[]]$tasks,
                100
            )
            if ($index -lt 0) { continue }
            $label = [string]$labels[$index]
            if ($label -ceq "stdout") {
                $read = $stdoutTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stdoutDone = $true
                }
                else {
                    $characterCount += $read
                    if (($characterCount * 4) -gt $MaximumOutputBytes) {
                        throw "$Label exceeded its output ceiling."
                    }
                    [void]$stdout.Append($stdoutBuffer, 0, $read)
                    $stdoutTask = $Process.StandardOutput.ReadAsync(
                        $stdoutBuffer,
                        0,
                        $stdoutBuffer.Length
                    )
                }
            }
            else {
                $read = $stderrTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stderrDone = $true
                }
                else {
                    $characterCount += $read
                    if (($characterCount * 4) -gt $MaximumOutputBytes) {
                        throw "$Label exceeded its output ceiling."
                    }
                    [void]$stderr.Append($stderrBuffer, 0, $read)
                    $stderrTask = $Process.StandardError.ReadAsync(
                        $stderrBuffer,
                        0,
                        $stderrBuffer.Length
                    )
                }
            }
        }
        return [ordered]@{
            exit_code = $Process.ExitCode
            stderr = $stderr.ToString()
            stdout = $stdout.ToString()
        }
    }
    finally {
        $clock.Stop()
    }
}

function Stop-Jet3BootstrapProcessTree {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [string]$Label
    )

    if ($Process.HasExited) { return }
    $taskkillPath = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $taskkillPath
    $start.Arguments = "/PID $($Process.Id) /T /F"
    $start.CreateNoWindow = $true
    $start.UseShellExecute = $false
    $terminator = New-Object Diagnostics.Process
    $terminator.StartInfo = $start
    try {
        if (-not $terminator.Start()) {
            throw "$Label process-tree termination could not start."
        }
        if (-not $terminator.WaitForExit(5000)) {
            $terminator.Kill()
            throw "$Label process-tree termination exceeded five seconds."
        }
        if ($terminator.ExitCode -ne 0 -and -not $Process.HasExited) {
            throw "$Label process-tree termination failed."
        }
        [void]$Process.WaitForExit(5000)
    }
    finally {
        $terminator.Dispose()
        if (-not $Process.HasExited) {
            $Process.Kill()
        }
    }
}

function Invoke-Jet3BootstrapProcess {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Label,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

    Assert-Jet3ProcessLimits -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = @(
        $Arguments | ForEach-Object {
            ConvertTo-Jet3ProcessArgument -Value $_
        }
    ) -join " "
    $start.CreateNoWindow = $true
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) {
            throw "$Label could not be started."
        }
        try {
            return Read-Jet3ProcessOutput -Process $process `
                -TimeoutSeconds $TimeoutSeconds `
                -MaximumOutputBytes $MaximumOutputBytes -Label $Label
        }
        catch {
            Stop-Jet3BootstrapProcessTree -Process $process -Label $Label
            throw
        }
    }
    finally {
        $process.Dispose()
    }
}
