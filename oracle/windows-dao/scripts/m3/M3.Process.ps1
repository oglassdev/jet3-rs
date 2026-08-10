Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$shared = Join-Path (Split-Path -Parent $PSScriptRoot) `
    "shared/BoundedProcess.ps1"
. $shared

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
