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
function Table-Names([string]$Arm) {
    if ($Arm -ceq 'mixed') { return @('Numbers', 'Keys', 'Notes', 'Empty') }
    return @('Empty', 'Binary')
}
function Add-Table($Database, [string]$Name) {
    $table = $field = $index = $key = $null
    try {
        $table = $Database.CreateTableDef($Name)
        $type = if ($Name -ceq 'Notes') { 12 } elseif ($Name -ceq 'Binary') { 11 } else { 4 }
        $fieldName = if ($type -eq 4) { 'Id' } else { 'Payload' }
        $field = $table.CreateField($fieldName, $type)
        $table.Fields.Append($field)
        if ($Name -ceq 'Keys') {
            $index = $table.CreateIndex('ById')
            $index.Primary = $true
            $index.Unique = $true
            $key = $index.CreateField('Id')
            $index.Fields.Append($key)
            $table.Indexes.Append($index)
        }
        $Database.TableDefs.Append($table)
    }
    finally { Release $key; Release $index; Release $field; Release $table }
}
function Add-Rows($Database, [string]$Name) {
    if ($Name -ceq 'Empty') { return }
    $rs = $field = $null
    try {
        $rs = $Database.OpenRecordset($Name, 2)
        if ($Name -ceq 'Numbers' -or $Name -ceq 'Keys') {
            $values = if ($Name -ceq 'Numbers') { -254..254 } else { @(3, -1, 2) }
            foreach ($value in $values) {
                $rs.AddNew()
                $rs.Fields.Item('Id').Value = [int]$value
                $rs.Update()
            }
        }
        else {
            $field = $rs.Fields.Item('Payload')
            $rs.AddNew()
            if ($Name -ceq 'Notes') {
                $text = -join (0..4095 | ForEach-Object { [char](65 + ($_ % 26)) })
                $field.AppendChunk([string]$text)
            }
            else {
                $bytes = [byte[]](0..2047 | ForEach-Object { [byte]($_ % 256) })
                $field.AppendChunk([byte[]]$bytes)
            }
            $rs.Update()
            if ($Name -ceq 'Notes') {
                $rs.AddNew()
                $field.Value = [DBNull]::Value
                $rs.Update()
            }
        }
    }
    finally {
        Release $field
        if ($null -ne $rs) { $rs.Close() }
        Release $rs
    }
}
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($name in (Table-Names $Arm)) {
            Add-Table $db $name
            Add-Rows $db $name
        }
    }
    finally {
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset, [string]$Name) {
    if ($Name -ceq 'Notes' -or $Name -ceq 'Binary') {
        $value = $Recordset.Fields.Item('Payload').Value
        if ($null -eq $value -or [Convert]::IsDBNull($value)) { $value = $null }
        elseif ($Name -ceq 'Notes') { $value = [string]$value }
        else { $value = [BitConverter]::ToString([byte[]]$value).Replace('-', '').ToLowerInvariant() }
        return @{payload=$value}
    }
    $value = $Recordset.Fields.Item('Id').Value
    if ($null -eq $value -or [Convert]::IsDBNull($value)) { $value = $null } else { $value = [int]$value }
    return @{id=$value}
}
function Read-Table($Database, [string]$Name) {
    $table = $rs = $null
    try {
        $table = $Database.TableDefs.Item($Name)
        $snapshot = [ordered]@{name=$Name}
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object {
            @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required;
              foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls;
              fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; descending=(([int]$_.Attributes -band 1) -ne 0); attributes=[int]$_.Attributes} })}
        })
        $rs = $Database.OpenRecordset($Name, 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) { [void]$rows.Add((Read-Row $rs $Name)); $rs.MoveNext() }
        $snapshot.rows = @($rows)
        if ($Name -ceq 'Keys') {
            $rs.Close(); Release $rs; $rs = $null
            $rs = $Database.OpenRecordset($Name, 1)
            $rs.Index = 'ById'
            $rs.MoveFirst()
            $traversal = New-Object Collections.ArrayList
            while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs $Name)); $rs.MoveNext() }
            $snapshot.traversal = @($traversal)
            $seeks = New-Object Collections.ArrayList
            foreach ($value in @(-1, 2, 3)) {
                $rs.Seek('=', [int]$value)
                $row = if ($rs.NoMatch) { $null } else { Read-Row $rs $Name }
                [void]$seeks.Add(@{query=$value; row=$row})
            }
            $snapshot.seek = @($seeks)
        }
        return $snapshot
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $table
    }
}
function Observe([string]$Path, [string]$Arm) {
    $before = Identity $Path
    $engine = $db = $null
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
        $endpoint = 'user_tables'
        $snapshot.user_tables = @(foreach ($name in (Table-Names $Arm)) { Read-Table $db $name })
        $endpoint = 'complete'
    }
    catch {
        $status = 'fail'
        $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>')
    }
    finally {
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'multi-table-rows.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/multi_table_rows.ps1') { throw 'Script pin mismatch' }
$arms = @('mixed', 'empty-first')
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
    document_type='dao_multi_table_rows_result'; development_only=$true
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
