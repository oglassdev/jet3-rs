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
    if ($Arm -ceq 'primary') { return ,@([int](27800 - $Position), $Position) }
    if ($Arm -ceq 'composite') { return ,@([int][Math]::Floor($Position / 400), [int]([Math]::Floor($Position / 800) - 9), $Position) }
    if ($Name -ceq 'Parents') { return ,@([int]($Position - 100), $Position) }
    return ,@([int]($Position % 3 - 1), $Position)
}
function Add-Table($Database, $Spec) {
    $table = $index = $field = $null
    try {
        $table = $Database.CreateTableDef([string]$Spec.name)
        foreach ($column in $Spec.fields) {
            $field = $table.CreateField([string]$column.name, 4)
            $table.Fields.Append($field); Release $field; $field = $null
        }
        foreach ($definition in $Spec.indexes) {
            if ($definition.foreign) { continue }
            $index = $table.CreateIndex([string]$definition.name)
            $index.Primary = [bool]$definition.primary; $index.Unique = [bool]$definition.unique
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
                for ($column = 0; $column -lt $values.Count; $column++) { $rs.Fields.Item($column).Value = [int]$values[$column] }
                $rs.Update()
            }
            $workspace.CommitTrans()
            $rs.Close(); Release $rs; $rs = $null
        }
        if ($Arm -ceq 'relationship') {
            $relation = $db.CreateRelation('ParentChildren', 'Parents', 'Children', 0)
            $field = $relation.CreateField('Id'); $field.ForeignName = 'Id'
            $relation.Fields.Append($field); $db.Relations.Append($relation)
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
    $values = @()
    for ($column = 0; $column -lt $Count; $column++) {
        $value = $Recordset.Fields.Item($column).Value
        if ($null -eq $value -or [Convert]::IsDBNull($value)) { $values += $null }
        else { $values += [int]$value }
    }
    return ,$values
}
function Read-Table($Database, $Spec) {
    $table = $rs = $null
    try {
        $table = $Database.TableDefs.Item([string]$Spec.name)
        $snapshot = [ordered]@{name=[string]$Spec.name}
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size} })
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
            $rs.MoveFirst()
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
$planPath = Join-Path $env:JET3_WORK 'multi-level-index.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/multi_level_index.ps1') { throw 'Script pin mismatch' }
$arms = @('primary', 'composite', 'relationship')
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
    document_type='dao_multi_level_index_result'; development_only=$true
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
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
