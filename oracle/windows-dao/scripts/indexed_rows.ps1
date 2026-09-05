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
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $table = $id = $payload = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $table = $db.CreateTableDef('Rows')
        $id = $table.CreateField('Id', 4)
        $table.Fields.Append($id)
        $payload = $table.CreateField('Payload', 10, 255)
        $table.Fields.Append($payload)
        $index = $table.CreateIndex('ById')
        $index.Primary = ($Arm -ceq 'primary')
        $index.Unique = ($Arm -cne 'ordinary')
        $key = $index.CreateField('Id')
        $index.Fields.Append($key)
        $table.Indexes.Append($index)
        $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Rows', 2)
        foreach ($position in 0..19) {
            $value = if ($Arm -ceq 'ordinary') { 9 - ($position % 10) } else { 9 - $position }
            $rs.AddNew()
            $rs.Fields.Item('Id').Value = [int]$value
            $rs.Fields.Item('Payload').Value = ([string][char](97 + $position)) * 255
            $rs.Update()
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $key; Release $index; Release $payload; Release $id; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset) {
    $id = $Recordset.Fields.Item('Id').Value
    $payload = $Recordset.Fields.Item('Payload').Value
    if ($null -eq $id -or [Convert]::IsDBNull($id)) { $id = $null } else { $id = [int]$id }
    if ($null -eq $payload -or [Convert]::IsDBNull($payload)) { $payload = $null } else { $payload = [string]$payload }
    return @{id=$id; payload=$payload}
}
function Observe([string]$Path, [string]$Arm) {
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
        $rs.Index = 'ById'
        $rs.MoveFirst()
        $traversal = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            [void]$traversal.Add((Read-Row $rs))
            $rs.MoveNext()
        }
        $snapshot.traversal = @($traversal)
        $endpoint = 'seek'
        $seeks = New-Object Collections.ArrayList
        $first = if ($Arm -ceq 'ordinary') { 0 } else { -10 }
        foreach ($value in $first..9) {
            $rs.Seek('=', [int]$value)
            $row = if ($rs.NoMatch) { $null } else { Read-Row $rs }
            [void]$seeks.Add(@{query=$value; row=$row})
        }
        $snapshot.seek = @($seeks)
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
$planPath = Join-Path $env:JET3_WORK 'indexed-rows.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/indexed_rows.ps1') { throw 'Script pin mismatch' }
$arms = @('primary', 'unique', 'ordinary')
foreach ($arm in $arms) {
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "$arm-candidate-r$replica.mdb"
        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$arm.mdb") -Destination $path
        $identity = Identity $path
        $expected = $plan.candidates.$arm
        if ($identity.size -ne $expected.size -or $identity.sha256 -cne $expected.sha256) { throw 'Candidate pin mismatch' }
    }
}
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_indexed_rows_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); error=$null
}
try {
    foreach ($arm in $arms) {
        foreach ($replica in 1..3) {
            $control = Join-Path $env:JET3_WORK "$arm-control-r$replica.mdb"
            New-Control $control $arm
            $candidate = Join-Path $env:JET3_WORK "$arm-candidate-r$replica.mdb"
            $result.replicas += @{arm=$arm; replica=$replica; control=(Observe $control $arm); candidate=(Observe $candidate $arm)}
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
