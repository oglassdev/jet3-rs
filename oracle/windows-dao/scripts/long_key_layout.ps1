Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}
function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function New-Control([string]$Path, $Arm) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $table = $db.CreateTableDef('Rows')
        foreach ($name in @('A', 'B', 'Tag')) {
            $field = $table.CreateField($name, 4)
            $table.Fields.Append($field)
            Release $field; $field = $null
        }
        $index = $table.CreateIndex('ByKey')
        $index.Primary = $false
        $index.Unique = [bool]$Arm.unique
        foreach ($definition in $Arm.fields) {
            $key = $index.CreateField([string]$definition.name)
            if ($definition.descending) { $key.Attributes = 1 }
            $index.Fields.Append($key)
            Release $key; $key = $null
        }
        $table.Indexes.Append($index)
        $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Rows', 2)
        foreach ($row in $Arm.rows) {
            $rs.AddNew()
            $rs.Fields.Item('A').Value = [int]$row[0]
            $rs.Fields.Item('B').Value = [int]$row[1]
            $rs.Fields.Item('Tag').Value = [int]$row[2]
            $rs.Update()
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset) {
    return ,@([int]$Recordset.Fields.Item('A').Value, [int]$Recordset.Fields.Item('B').Value, [int]$Recordset.Fields.Item('Tag').Value)
}
function Observe([string]$Path) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $endpoint = 'open_database'
    $snapshot = [ordered]@{}
    $status = 'pass'
    $errorDetail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $endpoint = 'schema'
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $table = $db.TableDefs.Item('Rows')
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object {
            @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required;
              fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; descending=(([int]$_.Attributes -band 1) -ne 0)} })}
        })
        $endpoint = 'rows'
        $rs = $db.OpenRecordset('Rows', 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            [void]$rows.Add((Read-Row $rs))
            $rs.MoveNext()
        }
        $snapshot.rows = @($rows)
        $rs.Close(); Release $rs; $rs = $null
        $endpoint = 'index_traversal'
        $rs = $db.OpenRecordset('Rows', 1)
        $rs.Index = 'ByKey'
        $rs.MoveFirst()
        $traversal = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            [void]$traversal.Add((Read-Row $rs))
            $rs.MoveNext()
        }
        $snapshot.traversal = @($traversal)
        $endpoint = 'complete'
    }
    catch {
        $status = 'fail'
        $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>')
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'long-key-layout.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/long_key_layout.ps1') { throw 'Script pin mismatch' }
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_long_key_layout_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); error=$null
}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $name = "$($arm.name)-r$replica.mdb"
            $control = Join-Path $env:JET3_WORK $name
            New-Control $control $arm
            $observation = Observe $control
            $observation.arm = $arm.name
            $observation.replica = $replica
            $observation.file = $name
            $result.replicas += $observation
        }
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
