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
function New-Control([string]$Path) {
    $engine = $workspace = $db = $table = $id = $code = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $table = $db.CreateTableDef('Rows')
        $id = $table.CreateField('Id', 4)
        $code = $table.CreateField('Code', 10, 8)
        $table.Fields.Append($id)
        $table.Fields.Append($code)
        $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Rows', 2)
        foreach ($row in @(@{id=1; code='one'}, @{id=-2; code='two'}, @{id=$null; code=$null})) {
            $rs.AddNew()
            if ($null -ne $row.id) { $rs.Fields.Item('Id').Value = [int]$row.id }
            if ($null -ne $row.code) { $rs.Fields.Item('Code').Value = [string]$row.code }
            $rs.Update()
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $id; Release $code; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
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
        $snapshot.index_count = [int]$table.Indexes.Count
        $endpoint = 'rows'
        $rs = $db.OpenRecordset('Rows', 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            $id = $rs.Fields.Item('Id').Value
            $code = $rs.Fields.Item('Code').Value
            if ($null -eq $id -or [Convert]::IsDBNull($id)) { $id = $null } else { $id = [int]$id }
            if ($null -eq $code -or [Convert]::IsDBNull($code)) { $code = $null } else { $code = [string]$code }
            [void]$rows.Add(@{id=$id; code=$code})
            $rs.MoveNext()
        }
        $snapshot.rows = @($rows)
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
$planPath = Join-Path $env:JET3_WORK 'initial-rows.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/initial_rows.ps1') { throw 'Script pin mismatch' }
$candidate = Join-Path $env:JET3_WORK 'initial-rows.mdb'
foreach ($replica in 1..3) {
    $path = Join-Path $env:JET3_WORK "candidate-r$replica.mdb"
    Copy-Item -LiteralPath $candidate -Destination $path
    $identity = Identity $path
    if ($identity.size -ne $plan.candidate.size -or $identity.sha256 -cne $plan.candidate.sha256) { throw 'Candidate pin mismatch' }
}
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_initial_rows_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); error=$null
}
try {
    foreach ($replica in 1..3) {
        $control = Join-Path $env:JET3_WORK "control-r$replica.mdb"
        New-Control $control
        $result.replicas += @{replica=$replica; control=(Observe $control); candidate=(Observe (Join-Path $env:JET3_WORK "candidate-r$replica.mdb"))}
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
