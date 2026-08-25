Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# The A4 progress log is byte-for-byte the A2 implementation; only the
# callers' names are rebound.
. (Join-Path (Split-Path $PSScriptRoot -Parent) "a2/A2.Progress.ps1")

function New-A4ProgressFile {
    param(
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
    )
    return New-A2ProgressFile -DiagnosticsRoot $DiagnosticsRoot -Replica $Replica
}

function Open-A4WorkerProgress {
    param(
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
    )
    return Open-A2WorkerProgress -DiagnosticsRoot $DiagnosticsRoot `
        -Replica $Replica
}

function Add-A4ProgressRecord {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Progress,
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [Parameter(Mandatory = $true)][long]$PageCount
    )
    Add-A2ProgressRecord -Progress $Progress -CheckpointId $CheckpointId `
        -PageCount $PageCount
}
