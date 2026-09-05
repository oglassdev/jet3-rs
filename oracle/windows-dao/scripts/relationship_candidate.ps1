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
function New-Tables([string]$Path) {
    $engine = $workspace = $db = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($name in @('Parent', 'Child')) {
            $table = $field = $index = $key = $null
            try {
                $table = $db.CreateTableDef($name)
                $first = if ($name -eq 'Parent') { 'Id' } else { 'ParentId' }
                foreach ($column in @($first, 'Alternate')) {
                    $field = $table.CreateField($column, 4)
                    $table.Fields.Append($field)
                    Release $field; $field = $null
                }
                if ($name -eq 'Parent') {
                    foreach ($column in @('Id', 'Alternate')) {
                        $index = $table.CreateIndex(('By' + $column))
                        $index.Unique = $true
                        $index.Primary = ($column -eq 'Id')
                        $key = $index.CreateField($column)
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
function Add-Relation([string]$Path, [string]$Name, [string]$ParentColumn, [string]$ChildColumn) {
    $engine = $db = $relation = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path)
        $relation = $db.CreateRelation($Name, 'Parent', 'Child', 0)
        $field = $relation.CreateField($ParentColumn)
        $field.ForeignName = $ChildColumn
        $relation.Fields.Append($field)
        $db.Relations.Append($relation)
    }
    finally {
        Release $field; Release $relation
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
    }
}
function Observe([string]$Path) {
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
        foreach ($name in @('Parent', 'Child')) {
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
        foreach ($name in @('Parent', 'Child')) {
            $rs = $db.OpenRecordset($name, 4)
            while (-not $rs.EOF) {
                $values = @($rs.Fields | ForEach-Object {
                    $value = $_.Value
                    if ($null -eq $value -or [Convert]::IsDBNull($value)) { $null } else { [int]$value }
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
$planPath = Join-Path $env:JET3_WORK 'relationship-candidate.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/relationship_candidate.ps1') { throw 'Script pin mismatch' }
$candidate = Join-Path $env:JET3_WORK 'relationship-candidate.mdb'
foreach ($replica in 1..3) {
    $path = Join-Path $env:JET3_WORK "candidate-r$replica.mdb"
    Copy-Item -LiteralPath $candidate -Destination $path
    $identity = Identity $path
    if ($identity.size -ne $plan.candidate.size -or $identity.sha256 -cne $plan.candidate.sha256) { throw 'Candidate pin mismatch' }
}
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_relationship_candidate_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}
    replicas=@(); error=$null
}
try {
    foreach ($replica in 1..3) {
        $control = Join-Path $env:JET3_WORK "control-r$replica.mdb"
        New-Tables $control
        Add-Relation $control 'ParentChild' 'Id' 'ParentId'
        $result.replicas += @{replica=$replica; control=(Observe $control); candidate=(Observe (Join-Path $env:JET3_WORK "candidate-r$replica.mdb"))}
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
