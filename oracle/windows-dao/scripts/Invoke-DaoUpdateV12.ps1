[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InventoryPath,
    [Parameter(Mandatory=$true)][string]$EnvironmentPath,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$SourceRevision
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
# Reuse only named pure snapshot helpers; never execute the read acquisition.
$helperPath = Join-Path $PSScriptRoot 'Invoke-DaoReadV12.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($helperPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Snapshot helper parse failure' }
foreach ($name in @('Release-ComObject','Write-JsonDocument','Convert-ToLowerHex','Get-FileSha256','Get-DateText','Get-TypedValue','Get-NormalizedSize','Get-DaoSnapshot')) {
    $definitions = @($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name}, $false))
    if ($definitions.Count -ne 1) { throw "Missing snapshot helper $name" }
    Invoke-Expression $definitions[0].Extent.Text
}
$DaoTypeNames = @{1='dbBoolean';2='dbByte';3='dbInteger';4='dbLong';5='dbCurrency';6='dbSingle';7='dbDouble';8='dbDate';9='dbBinary';10='dbText';11='dbLongBinary';12='dbMemo';15='dbGUID'}
$DbAutoIncrField = 16; $DbSystemObject = -2147483646; $DbDescending = 1
$Utf8 = New-Object Text.UTF8Encoding($false)
$CodePage = [Text.Encoding]::GetEncoding(1252, (New-Object Text.EncoderExceptionFallback), (New-Object Text.DecoderExceptionFallback))
$inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
$environment = Get-Content -LiteralPath $EnvironmentPath -Raw | ConvertFrom-Json
$prepared = Get-Content -LiteralPath (Join-Path $OutputRoot 'preparation.json') -Raw | ConvertFrom-Json
$inventoryHash = Get-FileSha256 $InventoryPath
if ($inventory.document_type -cne 'dao_update_scenario_inventory' -or $prepared.inventory_sha256 -cne $inventoryHash) { throw 'Update inventory identity mismatch' }
if ($environment.status -cne 'ready' -or $null -eq $environment.accepted_provider) { throw 'No accepted DAO provider' }
if ($prepared.source_revision -cne $SourceRevision -or $prepared.producer_os -cne 'Linux') { throw 'Prepared source/platform mismatch' }
if ($prepared.scenarios.Count -ne $inventory.scenarios.Count) { throw 'Incomplete Rust preparation' }
$manifest = [ordered]@{document_type='dao_update_manifest'; protocol_version='1.2.0'; inventory_sha256=$inventoryHash; source_revision=$SourceRevision; scenarios=@()}
try {
    for ($n=0; $n -lt $inventory.scenarios.Count; $n++) {
        $scenario = $inventory.scenarios[$n]; $initial = $prepared.scenarios[$n]
        if ($scenario.operation.mode -cne 'dao_open_rust_update' -or $scenario.id -cne $initial.scenario_id -or $initial.status -cne 'prepared') { throw 'Invalid update preparation' }
        foreach ($role in @('before','after')) {
            $root = Join-Path (Join-Path $OutputRoot ([string]$scenario.id)) $role; $path = Join-Path $root 'database.mdb'
            $before = Get-FileSha256 $path
            if ($before -cne $initial.verification.($role + '_sha256')) { throw 'Prepared MDB identity mismatch' }
            $entry = [ordered]@{scenario_id=[string]$scenario.id; role=$role; before=$before; after=$null; status='pass'; error=$null}
            $engine = $db = $null
            try {
                $engine = New-Object -ComObject ([string]$environment.accepted_provider.prog_id)
                $db = $engine.OpenDatabase($path, $false, $true)
                $snapshot = Get-DaoSnapshot -Database $db -Scenario $scenario.id -Revision $SourceRevision -DatabaseHash $before
                Write-JsonDocument -Path (Join-Path $root 'dao-snapshot.raw.json') -Document $snapshot
            } catch { $entry.status='fail'; $entry.error=$_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
            finally {
                if ($null -ne $db) { try { $db.Close() } catch { $entry.status='fail'; $entry.error=$_.Exception.Message } }
                Release-ComObject $db; Release-ComObject $engine
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
                $entry.after = Get-FileSha256 $path
                $manifest.scenarios += $entry
            }
        }
    }
} finally { Write-JsonDocument -Path (Join-Path $OutputRoot 'dao-manifest.raw.json') -Document $manifest }
if (@($manifest.scenarios | Where-Object { $_.status -ne 'pass' -or $_.before -cne $_.after }).Count) { exit 1 }
