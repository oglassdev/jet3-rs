Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# A4 reuses the proven A1 capture primitives under A4's preregistered bounds.
. (Join-Path (Split-Path $PSScriptRoot -Parent) "a1/A1.PageStore.ps1")

$script:A1PageBytes = 2048L
$script:A1MaximumPagesPerReplica = 20480L
$script:A1MaximumUniqueBlobs = 65536L
$script:A1MaximumPageStoreBytes = 128MB
$script:A1MaximumChangedEntries = 65536L
$script:A1MaximumLogicalReadBytes = 2GB

function Get-A1Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$Id
    )
    return Get-A4Payload -Role $Role -Id $Id
}
function Invoke-A1WithDatabase {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [switch]$Create
    )
    return Invoke-A4WithDatabase -Action $Action -Create:$Create
}

function New-A4PageStore {
    param([Parameter(Mandatory = $true)][pscustomobject]$Session)
    return New-A1PageStore -Session $Session
}

function Set-A4ExpectedSemanticDirty {
    param([Parameter(Mandatory = $true)][string]$Role)
    Set-A1ExpectedSemanticDirty -Role $Role
}

function Read-A4SemanticTables {
    $expectedByRole = @{}
    foreach ($role in $script:A4Roles) {
        if ([bool]$script:A1Extant[$role]) {
            $expectedByRole[$role] = Get-A1ExpectedSemanticResult `
                -Role $role -Rows $script:A1Rows[$role]
        }
    }
    $documents = Invoke-A4WithDatabase -Action {
        param($database)
        foreach ($role in $script:A4Roles) {
            if ([bool]$script:A1Extant[$role]) {
                Read-A1SemanticTable -Database $database -Role $role `
                    -Expected $expectedByRole[$role]
            }
        }
    }
    return @($documents)
}

function Read-A4PageSnapshot {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Store,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [AllowNull()][string[]]$PriorHashes,
        [AllowNull()][byte[]]$PriorPages
    )
    return Read-A1PageSnapshot -Store $Store -DatabasePath $DatabasePath `
        -PriorHashes $PriorHashes -PriorPages $PriorPages
}

function Get-A4LowerSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    return Get-A1LowerSha256 -Bytes $Bytes
}

function Write-A4CreateNewBytes {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    Write-A1CreateNewBytes -Path $Path -Bytes $Bytes `
        -MaximumBytes $MaximumBytes
}
