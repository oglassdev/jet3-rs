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
function Get-RowValues($Recordset, $Columns) {
    $values = [ordered]@{}
    foreach ($column in $Columns) {
        $field = $null
        try { $field = $Recordset.Fields.Item([string]$column.name); $values[[string]$column.name] = Get-TypedValue -Field $field }
        finally { Release-ComObject $field }
    }
    return $values
}
function Get-SeekValue($Typed) {
    switch([string]$Typed.kind){
        'long' {return [int]$Typed.value}
        'double' {return [double]$Typed.value}
        'currency' {$value=[decimal]::Parse([string]$Typed.value,[Globalization.CultureInfo]::InvariantCulture);return [Runtime.InteropServices.CurrencyWrapper]::new($value)}
        default {throw 'Unsupported typed Seek component'}
    }
}
function Get-IndexObservations($Database, $Snapshot, $Scenario) {
    $observations = New-Object Collections.ArrayList
    foreach ($table in $Snapshot.tables) {
        foreach ($index in $table.indexes) {
            $rs = $null
            try {
                $rs = $Database.OpenRecordset([string]$table.name, 1); $rs.Index = [string]$index.name
                $rows = New-Object Collections.ArrayList; $seeks = New-Object Collections.ArrayList
                if (-not $rs.EOF) { $rs.MoveFirst() }
                while (-not $rs.EOF) { [void]$rows.Add((Get-RowValues $rs $table.columns)); $rs.MoveNext() }
                $seen = @{}
                foreach ($row in $rows) {
                    $values=@($index.fields | ForEach-Object { $row[[string]$_.name] })
                    if(@($values|Where-Object{$_.kind-eq 'null'}).Count){continue}
                    $query=[object[]]@($values|ForEach-Object{$_.value})
                    if($Scenario.recipe-ceq 'long-depth-three'-and $query[0]-notin @(-13900,-13899,-13701,-13700,-13699,-1,0,13899,13900)){continue}
                    $identity=ConvertTo-Json -InputObject $query -Compress
                    if($seen.ContainsKey($identity)){continue};$seen[$identity]=$true
                    $first=Get-SeekValue $values[0]
                    if($query.Count-eq 1){$rs.Seek('=',$first)}
                    elseif($query.Count-eq 2){$second=Get-SeekValue $values[1];$rs.Seek('=',$first,$second)}
                    else{throw 'Expected one/two numeric components'}
                    $found = if ($rs.NoMatch) { $null } else { Get-RowValues $rs $table.columns }
                    [void]$seeks.Add(@{query=$query; row=$found})
                }
                [void]$observations.Add(@{table=[string]$table.name; index=[string]$index.name; rows=@($rows); seeks=@($seeks)})
            } finally {
                if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release-ComObject $rs
            }
        }
    }
    return ,@($observations)
}
$inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
$environment = Get-Content -LiteralPath $EnvironmentPath -Raw | ConvertFrom-Json
$prepared = Get-Content -LiteralPath (Join-Path $OutputRoot 'preparation.json') -Raw | ConvertFrom-Json
$inventoryHash = Get-FileSha256 $InventoryPath
if ($inventory.document_type -cne 'dao_write_scenario_inventory' -or $prepared.inventory_sha256 -cne $inventoryHash) { throw 'Write inventory identity mismatch' }
if($environment.document_type-cne 'dao_environment'-or $environment.protocol_version-cne '1.2.0'-or $environment.status-cne 'ready'-or $environment.host.process_architecture-cne 'x86'-or $environment.accepted_provider.prog_id-cne 'DAO.DBEngine.36'-or $environment.accepted_provider.database_version-cne 'dbVersion30'){throw 'Expected ready stock x86 DAO3.0 environment'}
Copy-Item -LiteralPath $EnvironmentPath -Destination (Join-Path $OutputRoot 'environment.json')
if ($prepared.scenarios.Count -ne $inventory.scenarios.Count) { throw 'Incomplete Rust preparation' }
$manifest = [ordered]@{document_type='dao_write_manifest'; protocol_version='1.2.0'; inventory_sha256=$inventoryHash; source_revision=$SourceRevision; environment_sha256=(Get-FileSha256 $EnvironmentPath); scenarios=@()}
try {
    for ($n=0; $n -lt $inventory.scenarios.Count; $n++) {
        $scenario = $inventory.scenarios[$n]; $initial = $prepared.scenarios[$n]
        if ($scenario.operation.mode -cne 'dao_open_rust' -or $scenario.id -cne $initial.scenario_id -or $initial.status -cne 'prepared') { throw 'Invalid write preparation' }
        $root = Join-Path $OutputRoot ([string]$scenario.id); $path = Join-Path $root 'database.mdb'
        $before = Get-FileSha256 $path
        if ($before -cne $initial.database_sha256) { throw 'Prepared MDB identity mismatch' }
        $entry = [ordered]@{scenario_id=[string]$scenario.id; before=$before; after=$null; status='pass'; error=$null}
        $engine = $db = $null
        try {
            $engine = New-Object -ComObject ([string]$environment.accepted_provider.prog_id)
            $db = $engine.OpenDatabase($path, $false, $true)
            $snapshot = Get-DaoSnapshot -Database $db -Scenario $scenario.id -Revision $SourceRevision -DatabaseHash $before
            Write-JsonDocument -Path (Join-Path $root 'dao-snapshot.raw.json') -Document $snapshot
            $indexes = Get-IndexObservations $db $snapshot $scenario
            [IO.File]::WriteAllText((Join-Path $root 'dao-indexes.raw.json'), ((ConvertTo-Json -InputObject $indexes -Depth 32 -Compress) + "`n"), $Utf8)
        } catch { $entry.status='fail'; $entry.error=$_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
        finally {
            if ($null -ne $db) { try { $db.Close() } catch { $entry.status='fail'; $entry.error=$_.Exception.Message } }
            Release-ComObject $db; Release-ComObject $engine
            [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            $entry.after = Get-FileSha256 $path
            $manifest.scenarios += $entry
        }
    }
} finally { Write-JsonDocument -Path (Join-Path $OutputRoot 'dao-manifest.raw.json') -Document $manifest }
if (@($manifest.scenarios | Where-Object { $_.status -ne 'pass' -or $_.before -cne $_.after }).Count) { exit 1 }
