Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function New-Tables([string]$Path, $Arm) {
    $engine = $workspace = $db = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($name in @($Arm.parent, $Arm.child)) {
            $table = $field = $index = $key = $null
            try {
                $table = $db.CreateTableDef($name)
                $columns = if ($name -eq $Arm.parent) { @('Code2', 'Key1') } else { @('Label3', 'Account4') }
                foreach ($column in $columns) {
                    $field = if ($column -eq 'Label3') { $table.CreateField($column, 10, 8) } else { $table.CreateField($column, 4) }
                    $table.Fields.Append($field)
                    Release $field; $field = $null
                }
                if ($name -eq $Arm.parent) {
                    foreach ($definition in $Arm.expected_control.parent_indexes) {
                        $index = $table.CreateIndex([string]$definition[0])
                        $index.Unique = $true
                        $index.Primary = [bool]$definition[2]
                        $key = $index.CreateField([string]$definition[1])
                        $index.Fields.Append($key)
                        $table.Indexes.Append($index)
                        Release $key; $key = $null
                        Release $index; $index = $null
                    }
                }
                $db.TableDefs.Append($table)
            }
            finally { Release $key; Release $index; Release $field; Release $table }
        }
    }
    finally {
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Add-Relation([string]$Path, $Arm) {
    $engine = $db = $relation = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path)
        $relation = $db.CreateRelation($Arm.relation, $Arm.parent, $Arm.child, 0)
        $field = $relation.CreateField('Key1')
        $field.ForeignName = 'Account4'
        $relation.Fields.Append($field)
        $db.Relations.Append($relation)
    }
    finally {
        Release $field; Release $relation
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
    }
}
function Observe([string]$Path, $Arm) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $snapshot = [ordered]@{}
    $status = 'pass'; $endpoint = 'open_database'; $errorDetail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $endpoint = 'schema'
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.schema = @{}
        foreach ($name in @($Arm.parent, $Arm.child)) {
            $table = $db.TableDefs.Item($name)
            $fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes} })
            $indexes = @()
            foreach ($index in $table.Indexes) {
                try {
                    $keys = @($index.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes} })
                    $indexes += @{name=[string]$index.Name; primary=[bool]$index.Primary; unique=[bool]$index.Unique; foreign=[bool]$index.Foreign; required=[bool]$index.Required; ignore_nulls=[bool]$index.IgnoreNulls; fields=$keys}
                }
                finally { Release $index }
            }
            $snapshot.schema[$name] = @{attributes=[int]$table.Attributes; fields=$fields; indexes=$indexes; rows=@()}
            Release $table; $table = $null
        }
        $endpoint = 'relations'
        $snapshot.relations = @()
        foreach ($relation in $db.Relations) {
            try {
                $fields = @($relation.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })
                $snapshot.relations += @{name=[string]$relation.Name; table=[string]$relation.Table; foreign_table=[string]$relation.ForeignTable; attributes=[int]$relation.Attributes; fields=$fields}
            }
            finally { Release $relation }
        }
        $endpoint = 'rows'
        foreach ($name in @($Arm.parent, $Arm.child)) {
            $rs = $db.OpenRecordset($name, 4)
            while (-not $rs.EOF) {
                $values = @($rs.Fields | ForEach-Object {
                    $value = $_.Value
                    if ($null -eq $value -or [Convert]::IsDBNull($value)) { $null } else { $value }
                })
                $snapshot.schema[$name].rows += ,$values
                $rs.MoveNext()
            }
            $rs.Close(); Release $rs; $rs = $null
        }
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
$planPath = Join-Path $env:JET3_WORK 'parameterized-relationships.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/parameterized_relationships.ps1') { throw 'Script pin mismatch' }
foreach ($arm in $plan.arms) {
    $candidate = Join-Path $env:JET3_WORK $arm.filename
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "$($arm.name)-candidate-r$replica.mdb"
        Copy-Item -LiteralPath $candidate -Destination $path
        $identity = Identity $path
        if ($identity.size -ne $arm.candidate.size -or $identity.sha256 -cne $arm.candidate.sha256) { throw 'Candidate pin mismatch' }
    }
}
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_parameterized_relationships_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}
    replicas=@(); error=$null
}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $control = Join-Path $env:JET3_WORK "$($arm.name)-control-r$replica.mdb"
            New-Tables $control $arm
            Add-Relation $control $arm
            $result.replicas += @{arm=$arm.name; replica=$replica; control=(Observe $control $arm); candidate=(Observe (Join-Path $env:JET3_WORK "$($arm.name)-candidate-r$replica.mdb") $arm)}
        }
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
