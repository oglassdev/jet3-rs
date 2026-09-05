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
function Capture([string]$Path, [int]$Replica, [string]$Checkpoint) {
    $before = Identity $Path
    $engine = $db = $null
    $relations = @()
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        foreach ($relation in $db.Relations) {
            try {
                $fields = @()
                foreach ($field in $relation.Fields) {
                    try { $fields += @{name=[string]$field.Name; foreign_name=[string]$field.ForeignName} }
                    finally { Release $field }
                }
                $relations += @{name=[string]$relation.Name; table=[string]$relation.Table; foreign_table=[string]$relation.ForeignTable; attributes=[int]$relation.Attributes; fields=$fields}
            }
            finally { Release $relation }
        }
    }
    finally {
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    $filename = "relationship-r$Replica-$Checkpoint.mdb"
    Copy-Item -LiteralPath $Path -Destination (Join-Path $env:JET3_OUTBOX $filename)
    return @{replica=$Replica; checkpoint=$Checkpoint; file=$filename; before=$before; after=(Identity $Path); relations=$relations}
}
$planPath = Join-Path $env:JET3_WORK 'relationship-create.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/relationship_create.ps1') { throw 'Script pin mismatch' }
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_relationship_create_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}
    checkpoints=@(); error=$null
}
try {
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "working-r$replica.mdb"
        New-Tables $path
        $result.checkpoints += Capture $path $replica 'base'
        Add-Relation $path 'ParentChild' 'Id' 'ParentId'
        $result.checkpoints += Capture $path $replica 'first'
        Add-Relation $path 'AlternateLink' 'Alternate' 'Alternate'
        $result.checkpoints += Capture $path $replica 'second'
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter 'working-*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
