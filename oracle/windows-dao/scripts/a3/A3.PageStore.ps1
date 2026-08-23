Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# A3 reuses the proven A1 capture implementation under the A2-identical
# preregistered resource ceilings. Only the experiment binding differs.
. (Join-Path (Split-Path $PSScriptRoot -Parent) "a1/A1.PageStore.ps1")

$script:A1PageBytes = 2048L
$script:A1MaximumPagesPerReplica = 65536L
$script:A1MaximumUniqueBlobs = 65536L
$script:A1MaximumPageStoreBytes = 512MB
$script:A1MaximumChangedEntries = 65536L
$script:A1MaximumLogicalReadBytes = 2GB

function Get-A1Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$Id
    )
    return Get-A3Payload -Role $Role -Id $Id
}
function Invoke-A1WithDatabase {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [switch]$Create
    )
    return Invoke-A3WithDatabase -Action $Action -Create:$Create
}

function New-A3PageStore {
    param([Parameter(Mandatory = $true)][pscustomobject]$Session)
    return New-A1PageStore -Session $Session
}

function Set-A3ExpectedSemanticDirty {
    param([Parameter(Mandatory = $true)][string]$Role)
    Set-A1ExpectedSemanticDirty -Role $Role
}

function Read-A3SemanticTables {
    return @(Read-A1SemanticTables)
}

function Read-A3PageSnapshot {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Store,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [AllowNull()][string[]]$PriorHashes,
        [AllowNull()][byte[]]$PriorPages
    )
    return Read-A1PageSnapshot -Store $Store -DatabasePath $DatabasePath `
        -PriorHashes $PriorHashes -PriorPages $PriorPages
}

function Get-A3LowerSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    return Get-A1LowerSha256 -Bytes $Bytes
}

function Write-A3CreateNewBytes {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    Write-A1CreateNewBytes -Path $Path -Bytes $Bytes `
        -MaximumBytes $MaximumBytes
}
