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
    $engine = $workspace = $db = $table = $id = $payload = $field = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $table = $db.CreateTableDef('Rows')
        $id = $table.CreateField('Id', 4)
        $table.Fields.Append($id)
        $type = if ($Arm -ceq 'memo') { 12 } else { 11 }
        $payload = $table.CreateField('Payload', $type)
        $table.Fields.Append($payload)
        $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Rows', 2)
        $lengths = @(1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096)
        foreach ($position in 0..9) {
            $rs.AddNew()
            $rs.Fields.Item('Id').Value = [int]($position + 1)
            if ($position -lt 9) {
                $field = $rs.Fields.Item('Payload')
                $length = $lengths[$position]
                if ($Arm -ceq 'memo') {
                    $text = -join (0..($length - 1) | ForEach-Object { [char](65 + (($_ + $position) % 26)) })
                    $field.AppendChunk([string]$text)
                }
                else {
                    $bytes = [byte[]](0..($length - 1) | ForEach-Object { [byte](($_ + $position) % 256) })
                    $field.AppendChunk([byte[]]$bytes)
                }
                Release $field; $field = $null
            }
            $rs.Update()
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $field; Release $rs; Release $payload; Release $id; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset, [string]$Arm) {
    $id = $Recordset.Fields.Item('Id').Value
    $payload = $Recordset.Fields.Item('Payload').Value
    if ($null -eq $id -or [Convert]::IsDBNull($id)) { $id = $null } else { $id = [int]$id }
    if ($null -eq $payload -or [Convert]::IsDBNull($payload)) { $payload = $null }
    elseif ($Arm -ceq 'memo') { $payload = [string]$payload }
    else { $payload = [BitConverter]::ToString([byte[]]$payload).Replace('-', '').ToLowerInvariant() }
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
            [void]$rows.Add((Read-Row $rs $Arm))
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
$planPath = Join-Path $env:JET3_WORK 'long-value-rows.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/long_value_rows.ps1') { throw 'Script pin mismatch' }
$arms = @('memo', 'ole')
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
    document_type='dao_long_value_rows_result'; development_only=$true
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
