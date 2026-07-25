Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$shared = Join-Path (Split-Path -Parent $PSScriptRoot) `
    "shared/BoundedProcess.ps1"
. $shared

function ConvertTo-M3CommandLineArgument {
    param([AllowEmptyString()][string]$Value)

    return ConvertTo-BoundedProcessCommandLineArgument -Value $Value
}

function Stop-M3ProcessTree {
    param([Diagnostics.Process]$Process)

    Stop-BoundedProcessTree -Process $Process
}

function Read-M3BoundedProcessOutput {
    param(
        [Diagnostics.Process]$Process,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes
    )

    return Read-BoundedProcessOutput -Process $Process `
        -CallerLabel "M3" `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
}

function Invoke-M3ChildProcess {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [long]$MaximumOutputBytes = 1MB
    )

    return Invoke-BoundedChildProcess -Executable $Executable `
        -Arguments $Arguments `
        -CallerLabel "M3" `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
}
