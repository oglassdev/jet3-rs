Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) }
}
function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, ((ConvertTo-Json -InputObject $Value -Depth 30) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function Create-Original([string]$Path, $Plan) {
    $engine = $workspace = $db = $table = $field = $rs = $query = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($spec in $Plan.tables) {
            $table = $db.CreateTableDef([string]$spec.name)
            foreach ($column in $Plan.fields) {
                $field = $table.CreateField([string]$column.name, [int]$column.type, [int]$column.size)
                $table.Fields.Append($field); Release $field; $field = $null
            }
            $db.TableDefs.Append($table)
            $rs = $db.OpenRecordset([string]$spec.name, 2)
            foreach ($row in $spec.rows) {
                $rs.AddNew()
                $rs.Fields.Item('Id').Value = [int]$row[0]
                $rs.Fields.Item('Value').Value = [int]$row[1]
                $rs.Fields.Item('Payload').Value = [string]$row[2]
                $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null; Release $table; $table = $null
        }
        $query = $db.CreateQueryDef([string]$Plan.query.name, [string]$Plan.query.sql)
    } finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $query; Release $field; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $workspace; Release $engine
    }
}
function Observe([string]$Path) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $endpoint = 'open_database'; $snapshot = [ordered]@{}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { @{name=[string]$_.Name; table=[string]$_.Table; foreign_table=[string]$_.ForeignTable; attributes=[int]$_.Attributes; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })} })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { @{name=[string]$_.Name; sql=[string]$_.SQL; type=[int]$_.Type} } | Sort-Object { $_.name })
        $snapshot.user_tables = @()
        foreach ($name in @($snapshot.tables | Where-Object { -not $_.StartsWith('MSys') })) {
            $endpoint = 'schema'; $table = $db.TableDefs.Item([string]$name)
            $item = [ordered]@{name=[string]$table.Name; attributes=[int]$table.Attributes}
            $item.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes; required=[bool]$_.Required; allow_zero_length=[bool]$_.AllowZeroLength; default_value=[string]$_.DefaultValue} })
            $item.indexes = @($table.Indexes | ForEach-Object { @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes} })} })
            $endpoint = 'rows'; $rs = $db.OpenRecordset([string]$name, 4)
            $rows = New-Object Collections.ArrayList
            while (-not $rs.EOF) {
                [void]$rows.Add(@([int]$rs.Fields.Item('Id').Value, [int]$rs.Fields.Item('Value').Value, [string]$rs.Fields.Item('Payload').Value))
                $rs.MoveNext()
            }
            $item.rows = @($rows); $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += $item; Release $table; $table = $null
        }
    } catch { $status = 'error'; $errorDetail = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
    finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $engine
    }
    return @{file=[IO.Path]::GetFileName($Path); before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'field-update.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/field_update.ps1') { throw 'Script pin mismatch' }
$phase = (Get-Content -LiteralPath (Join-Path $env:JET3_WORK 'phase.txt') -Raw).Trim()
if ($phase -notin @('create', 'observe')) { throw 'Unknown phase' }
$mutationStarted = $false
$result = [ordered]@{document_type='dao_field_update_phase'; phase=$phase; plan_sha256=(Identity $planPath).sha256; environment=@{process_bits=32; provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}; mutation_started=$false; observations=@(); error=$null; endpoint='start'}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $roles = if ($phase -eq 'create') { @('original') } else { @('original', 'updated') }
            foreach ($role in $roles) {
                $path = Join-Path $env:JET3_WORK "$($arm.name)-r$replica-$role.mdb"
                $result.endpoint = "$($arm.name)/$replica/$role"
                if ($phase -eq 'create') { Create-Original $path $plan }
                $observation = Observe $path
                $result.observations += @{arm=[string]$arm.name; replica=$replica; role=$role; observation=$observation}
                if ($observation.status -ne 'pass') { throw 'Read-only observation failed' }
            }
        }
    }
} catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
