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
function Values([string]$Arm, [string]$Name, [int]$Position) {
    $values = New-Object 'object[]' 3
    if ($Arm -ceq 'required') { $values[0] = [int]($Position % 3); $values[1] = -$Position }
    elseif ($Arm -in @('composite', 'composite-ignore', 'auto')) {
        switch ($Position % 4) {
            0 { $values[0] = $null; $values[1] = $null }
            1 { $values[0] = $null; $values[1] = 1 }
            2 { $values[0] = 1; $values[1] = $null }
            3 { $values[0] = $Position; $values[1] = -$Position }
        }
    }
    else {
        $values[0] = if ($Position % 3 -eq 0) { $null } else { [int]($Position - 6) }
        $values[1] = -$Position
    }
    if ($Arm -ceq 'auto') { $values[1] = [int]($Position + 1) }
    $values[2] = $Position
    return ,$values
}
function Add-Table($Database, $Spec) {
    $table = $index = $field = $null
    try {
        $table = $Database.CreateTableDef([string]$Spec.name)
        foreach ($column in $Spec.fields) {
            $field = $table.CreateField([string]$column.name, 4)
            if ($column.auto_increment) { $field.Attributes = 17 }
            $table.Fields.Append($field); Release $field; $field = $null
        }
        foreach ($definition in $Spec.indexes) {
            if ($definition.foreign) { continue }
            $index = $table.CreateIndex([string]$definition.name)
            $index.Primary = [bool]$definition.primary; $index.Unique = [bool]$definition.unique
            $index.Required = [bool]$definition.required; $index.IgnoreNulls = [bool]$definition.ignore_nulls
            foreach ($key in $definition.fields) {
                $field = $index.CreateField([string]$key.name)
                $field.Attributes = [int]$key.attributes
                $index.Fields.Append($field); Release $field; $field = $null
            }
            $table.Indexes.Append($index); Release $index; $index = $null
        }
        $Database.TableDefs.Append($table)
    }
    finally { Release $field; Release $index; Release $table }
}
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $rs = $relation = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($table in $plan.arms.$Arm) { Add-Table $db $table }
        foreach ($table in $plan.arms.$Arm) {
            $rs = $db.OpenRecordset([string]$table.name, 2)
            $workspace.BeginTrans()
            for ($position = 0; $position -lt [int]$table.row_count; $position++) {
                $values = Values $Arm ([string]$table.name) $position
                $rs.AddNew()
                for ($column = 0; $column -lt $values.Count; $column++) {
                    if ($table.fields[$column].auto_increment) { continue }
                    $rs.Fields.Item($column).Value = if ($null -eq $values[$column]) { [DBNull]::Value } else { [int]$values[$column] }
                }
                $rs.Update()
            }
            $workspace.CommitTrans()
            $rs.Close(); Release $rs; $rs = $null
        }

    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $field; Release $relation
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset, [int]$Count) {
    $values = New-Object 'object[]' $Count
    for ($column = 0; $column -lt $Count; $column++) {
        $value = $Recordset.Fields.Item($column).Value
        if ($null -eq $value -or [Convert]::IsDBNull($value)) { $values[$column] = $null }
        else { $values[$column] = [int]$value }
    }
    return ,$values
}
function Read-Table($Database, $Spec) {
    $table = $rs = $null
    try {
        $table = $Database.TableDefs.Item([string]$Spec.name)
        $snapshot = [ordered]@{name=[string]$Spec.name}
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; auto_increment=(([int]$_.Attributes -band 16) -ne 0)} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object {
            @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required;
              foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls;
              fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; descending=(([int]$_.Attributes -band 1) -ne 0); attributes=[int]$_.Attributes} })}
        })
        $rs = $Database.OpenRecordset([string]$Spec.name, 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) { [void]$rows.Add((Read-Row $rs $Spec.fields.Count)); $rs.MoveNext() }
        $snapshot.rows = @($rows)
        if ($Spec.indexes.Count -gt 0) {
            $rs.Close(); Release $rs; $rs = $null
            $rs = $Database.OpenRecordset([string]$Spec.name, 1)
            $rs.Index = [string]$Spec.indexes[0].name
            if (-not ($rs.BOF -and $rs.EOF)) { $rs.MoveFirst() }
            $traversal = New-Object Collections.ArrayList
            while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs $Spec.fields.Count)); $rs.MoveNext() }
            $snapshot.traversal = @($traversal)
            $seeks = New-Object Collections.ArrayList
            foreach ($query in $Spec.queries) {
                if ($query.Count -eq 1) { $rs.Seek('=', [int]$query[0]) }
                else { $rs.Seek('=', [int]$query[0], [int]$query[1]) }
                $row = if ($rs.NoMatch) { $null } else { Read-Row $rs $Spec.fields.Count }
                [void]$seeks.Add(@{query=@($query); row=$row})
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
    $endpoint = 'open_database'; $snapshot = [ordered]@{}; $status = 'pass'; $errorDetail = $null
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
        $snapshot.user_tables = @(foreach ($spec in $plan.arms.$Arm) { Read-Table $db $spec })
        $endpoint = 'complete'
    }
    catch { $status = 'fail'; $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>') }
    finally {
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
function Reject-Probe([string]$Original, [string]$Path, [string]$Arm) {
    Copy-Item -LiteralPath $Original -Destination $Path
    $originalIdentity = Identity $Path
    $engine = $db = $rs = $null
    $operation = @{status='updated'; endpoint='update'; native_codes=@(); hresult=$null; error=$null}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Rows', 2)
        $rs.AddNew()
        $values = $plan.rejection_probes.$Arm
        for ($column = 0; $column -lt $values.Count; $column++) {
            $rs.Fields.Item($column).Value = if ($null -eq $values[$column]) { [DBNull]::Value } else { [int]$values[$column] }
        }
        try { $rs.Update() }
        catch {
            $operation.status = 'rejected'
            $operation.native_codes = @($engine.Errors | ForEach-Object { [int]$_.Number })
            $exception = $_.Exception
            while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
            $operation.hresult = [int]$exception.HResult
            $operation.error = ($exception.GetType().FullName + ': ' + $exception.Message).Replace($Path, '<DATABASE>')
            $rs.CancelUpdate()
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
    }
    return @{original=$originalIdentity; operation=$operation; observation=(Observe $Path $Arm)}
}
$planPath = Join-Path $env:JET3_WORK 'nullable-index.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/nullable_index.ps1') { throw 'Script pin mismatch' }
$arms = @('unique', 'ignore', 'required', 'composite', 'composite-ignore', 'auto')
foreach ($arm in $arms) {
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "$arm-candidate-r$replica.mdb"
        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$arm.mdb") -Destination $path
        $identity = Identity $path; $expected = $plan.candidates.$arm
        if ($identity.size -ne $expected.size -or $identity.sha256 -cne $expected.sha256) { throw 'Candidate pin mismatch' }
    }
}
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_nullable_index_result'; development_only=$true
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
            $pair = @{arm=$arm; replica=$replica; control=(Observe $control $arm); candidate=(Observe $candidate $arm)}
            if ($plan.rejection_probes.PSObject.Properties.Name -contains $arm) {
                $pair.probes = @{
                    control=(Reject-Probe $control (Join-Path $env:JET3_WORK "$arm-control-probe-r$replica.mdb") $arm)
                    candidate=(Reject-Probe $candidate (Join-Path $env:JET3_WORK "$arm-candidate-probe-r$replica.mdb") $arm)
                }
            }
            $result.replicas += $pair
        }
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
