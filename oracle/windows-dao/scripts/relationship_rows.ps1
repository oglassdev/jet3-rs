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
function Add-Table($Database, [bool]$Parent) {
    $table = $first = $second = $index = $key = $null
    try {
        if ($Parent) {
            $table = $Database.CreateTableDef('Accounts7')
            $first = $table.CreateField('Code2', 4)
            $second = $table.CreateField('Key1', 4)
        }
        else {
            $table = $Database.CreateTableDef('Events9')
            $first = $table.CreateField('Label3', 10, 255)
            $second = $table.CreateField('Account4', 4)
        }
        $table.Fields.Append($first); $table.Fields.Append($second)
        if ($Parent) {
            $index = $table.CreateIndex('Primary9')
            $index.Primary = $true; $index.Unique = $true
            $key = $index.CreateField('Key1')
            $index.Fields.Append($key)
            $table.Indexes.Append($index)
        }
        $Database.TableDefs.Append($table)
    }
    finally { Release $key; Release $index; Release $second; Release $first; Release $table }
}
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $rs = $relation = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        Add-Table $db $true
        Add-Table $db $false
        $rs = $db.OpenRecordset('Accounts7', 2)
        foreach ($position in 0..2) {
            $rs.AddNew()
            $rs.Fields.Item('Code2').Value = [int](9 - $position)
            $rs.Fields.Item('Key1').Value = [int]($position + 1)
            $rs.Update()
        }
        $rs.Close(); Release $rs; $rs = $null
        $rs = $db.OpenRecordset('Events9', 2)
        foreach ($position in 0..19) {
            $rs.AddNew()
            $rs.Fields.Item('Label3').Value = ([string][char](97 + $position)) * 255
            $rs.Fields.Item('Account4').Value = [int](1 + $position % 3)
            $rs.Update()
        }
        $rs.Close(); Release $rs; $rs = $null
        $relation = $db.CreateRelation('Account7Events9', 'Accounts7', 'Events9', 0)
        $field = $relation.CreateField('Key1')
        $field.ForeignName = 'Account4'
        $relation.Fields.Append($field)
        $db.Relations.Append($relation)
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $field; Release $relation
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset, [string]$Name) {
    $first = $Recordset.Fields.Item(0).Value
    $second = $Recordset.Fields.Item(1).Value
    if ($null -eq $first -or [Convert]::IsDBNull($first)) { $first = $null }
    elseif ($Name -ceq 'Accounts7') { $first = [int]$first } else { $first = [string]$first }
    if ($null -eq $second -or [Convert]::IsDBNull($second)) { $second = $null } else { $second = [int]$second }
    if ($Name -ceq 'Accounts7') { return @{code2=$first; key1=$second} }
    return @{label3=$first; account4=$second}
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
        if ($Name -ceq 'Accounts7' -or $Name -ceq 'Events9') {
            $rs.Close(); Release $rs; $rs = $null
            $rs = $Database.OpenRecordset($Name, 1)
            $rs.Index = $(if ($Name -ceq 'Accounts7') { 'Primary9' } else { 'Account7Events9' })
            $rs.MoveFirst()
            $traversal = New-Object Collections.ArrayList
            while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs $Name)); $rs.MoveNext() }
            $snapshot.traversal = @($traversal)
            $seeks = New-Object Collections.ArrayList
            foreach ($value in @(1, 2, 3)) {
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
        $snapshot.relations = @()
        foreach ($relation in $db.Relations) {
            try {
                $fields = @($relation.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })
                $snapshot.relations += @{name=[string]$relation.Name; table=[string]$relation.Table; foreign_table=[string]$relation.ForeignTable; attributes=[int]$relation.Attributes; fields=$fields}
            }
            finally { Release $relation }
        }
        $endpoint = 'user_tables'
        $snapshot.user_tables = @(foreach ($name in @('Accounts7', 'Events9')) { Read-Table $db $name })
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
function Probe([string]$Source, [string]$Path, [string]$Kind) {
    Copy-Item -LiteralPath $Source -Destination $Path
    $before = Identity $Path
    $sourceIdentity = Identity $Source
    if ($before.sha256 -cne $sourceIdentity.sha256 -or $before.size -ne $sourceIdentity.size) { throw 'Writable copy mismatch' }
    $engine = $db = $rs = $null
    $operation = @{status='updated'; endpoint='update'; native_codes=@(); hresult=$null; error=$null}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $false)
        $table = if ($Kind -ceq 'duplicate_parent') { 'Accounts7' } else { 'Events9' }
        $rs = $db.OpenRecordset($table, 2)
        $rs.AddNew()
        if ($Kind -ceq 'duplicate_parent') {
            $rs.Fields.Item('Code2').Value = [int]6
            $rs.Fields.Item('Key1').Value = [int]1
        }
        else {
            $label = if ($Kind -ceq 'valid_child') { 'valid' } else { 'orphan' }
            $key = if ($Kind -ceq 'valid_child') { 2 } else { 999 }
            $rs.Fields.Item('Label3').Value = [string]$label
            $rs.Fields.Item('Account4').Value = [int]$key
        }
        try { $rs.Update() }
        catch {
            $exception = $_.Exception
            while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
            $operation.status = 'rejected'
            $operation.hresult = [int]$exception.HResult
            $operation.error = ($exception.GetType().FullName + ': ' + $exception.Message).Replace($Path, '<DATABASE>')
            $operation.native_codes = @($engine.Errors | ForEach-Object { [int]$_.Number })
            if ([int]$rs.EditMode -ne 0) { $rs.CancelUpdate() }
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; operation=$operation; observation=(Observe $Path 'populated')}
}
$planPath = Join-Path $env:JET3_WORK 'relationship-rows.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$scriptHash = (Identity $PSCommandPath).sha256
if ($scriptHash -cne $plan.inputs.'oracle/windows-dao/scripts/relationship_rows.ps1') { throw 'Script pin mismatch' }
$arms = @('populated')
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
    document_type='dao_relationship_rows_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); probes=@(); error=$null
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
    foreach ($replica in 1..3) {
        foreach ($probe in @('valid_child', 'orphan_child', 'duplicate_parent')) {
            $control = Join-Path $env:JET3_WORK "populated-control-r$replica.mdb"
            $candidate = Join-Path $env:JET3_WORK "populated-candidate-r$replica.mdb"
            $controlCopy = Join-Path $env:JET3_WORK "populated-control-$probe-r$replica.mdb"
            $candidateCopy = Join-Path $env:JET3_WORK "populated-candidate-$probe-r$replica.mdb"
            $result.probes += @{replica=$replica; probe=$probe; control=(Probe $control $controlCopy $probe); candidate=(Probe $candidate $candidateCopy $probe)}
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
