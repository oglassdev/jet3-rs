Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}
function Identity([string]$Path) {
    return @{ size = (Get-Item -LiteralPath $Path).Length; sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Failure($Record) { return @{ endpoint = $script:endpoint; message = $Record.Exception.Message; hresult = $Record.Exception.HResult } }
function Read-Row($Recordset, $Table) {
    $values = [object[]]::new($Table.columns.Count)
    for ($i = 0; $i -lt $values.Length; $i++) { $values[$i] = [int]$Recordset.Fields.Item([string]$Table.columns[$i]).Value }
    return ,$values
}
function Read-Rows($Recordset, $Table) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Table)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function New-Control([string]$Path, $Arm) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:endpoint = 'create_database'
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($spec in $Arm.tables) {
            $script:endpoint = "create_table/$($spec.name)"
            $table = $db.CreateTableDef([string]$spec.name)
            foreach ($name in $spec.columns) {
                $field = $table.CreateField([string]$name, 4, 4)
                $table.Fields.Append($field); Release $field; $field = $null
            }
            foreach ($requested in $spec.indexes) {
                $index = $table.CreateIndex([string]$requested.name)
                $index.Primary = [bool]$requested.primary
                $index.Unique = [bool]$requested.unique
                $index.Required = [bool]$requested.primary
                $index.IgnoreNulls = $false
                $key = $index.CreateField([string]$spec.columns[[int]$requested.column])
                if ($requested.descending) { $key.Attributes = 1 }
                $index.Fields.Append($key); Release $key; $key = $null
                $table.Indexes.Append($index); Release $index; $index = $null
            }
            $db.TableDefs.Append($table); Release $table; $table = $null
            $rs = $db.OpenRecordset([string]$spec.name, 2)
            foreach ($row in $spec.rows) {
                $rs.AddNew()
                for ($i = 0; $i -lt $spec.columns.Count; $i++) { $rs.Fields.Item([string]$spec.columns[$i]).Value = [int]$row[$i] }
                $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $engine
    }
}
function Capture([string]$Path, $Arm) {
    $before = Identity $Path
    $snapshot = @{ tables = @() }
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null
    try {
        $script:endpoint = "capture/$([IO.Path]::GetFileName($Path))/open"
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.inventory = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { [string]$_.Name })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { [string]$_.Name })
        foreach ($spec in $Arm.tables) {
            $script:endpoint = "capture/$($spec.name)/metadata"
            $table = $db.TableDefs.Item([string]$spec.name)
            $observation = @{ name = [string]$table.Name; attributes = [int]$table.Attributes }
            $observation.columns = @($table.Fields | ForEach-Object {
                @{ name = [string]$_.Name; type = [int]$_.Type; size = [int]$_.Size; attributes = [int]$_.Attributes; required = [bool]$_.Required; default_value = [string]$_.DefaultValue }
            })
            $observation.indexes = @($table.Indexes | ForEach-Object {
                @{ name = [string]$_.Name; primary = [bool]$_.Primary; unique = [bool]$_.Unique; required = [bool]$_.Required; ignore_nulls = [bool]$_.IgnoreNulls; foreign = [bool]$_.Foreign;
                   fields = @($_.Fields | ForEach-Object { @{ name = [string]$_.Name; attributes = [int]$_.Attributes } }) }
            })
            $script:endpoint = "capture/$($spec.name)/rows"
            $rs = $db.OpenRecordset([string]$spec.name, 4)
            $observation.rows = Read-Rows $rs $spec
            $rs.Close(); Release $rs; $rs = $null
            $observation.traversals = @{}; $observation.seeks = @{}
            foreach ($requested in $spec.indexes) {
                $script:endpoint = "capture/$($spec.name)/index/$($requested.name)"
                $rs = $db.OpenRecordset([string]$spec.name, 1); $rs.Index = [string]$requested.name
                if (-not $rs.EOF) { $rs.MoveFirst() }
                $observation.traversals[$requested.name] = Read-Rows $rs $spec
                $seeks = New-Object Collections.ArrayList
                foreach ($query in @(-1000, 0, 2, 16, 1000)) {
                    $rs.Seek('=', [int]$query)
                    $row = if ($rs.NoMatch) { $null } else { Read-Row $rs $spec }
                    [void]$seeks.Add(@{ query = [int]$query; row = $row })
                }
                $observation.seeks[$requested.name] = [object[]]$seeks.ToArray()
                $rs.Close(); Release $rs; $rs = $null
            }
            $snapshot.tables += ,$observation
            Release $table; $table = $null
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'creation-tables.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_creation_tables_result'; source_revision = $manifest.source_revision; manifest_sha256 = (Identity $manifestPath).sha256;
    environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; os = [Environment]::OSVersion.VersionString };
    pairs = @(); error = $null; retention_failures = @() }
try {
    if ((Identity $PSCommandPath).sha256 -cne $manifest.producer_sha256) { throw 'Producer identity differs' }
    foreach ($arm in $manifest.arms) {
        foreach ($replica in 1..2) {
            $prefix = "$($arm.name)-r$replica"
            $pair = @{ arm = [string]$arm.name; replica = $replica; captures = @{} }; $result.pairs += ,$pair
            $source = Join-Path $env:JET3_WORK "$($arm.name).mdb"
            $actual = Identity $source
            if ($actual.sha256 -cne $arm.image.sha256 -or $actual.size -ne $arm.image.size) { throw 'Candidate identity differs' }
            $candidate = Join-Path $env:JET3_WORK "$prefix-candidate.mdb"
            Copy-Item -LiteralPath $source -Destination $candidate
            New-Control (Join-Path $env:JET3_WORK "$prefix-control.mdb") $arm
            foreach ($role in @('candidate', 'control')) {
                $pair.captures[$role] = Capture (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $arm
                if ($pair.captures[$role].status -ne 'pass') { throw 'Capture failed' }
            }
        }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb') {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += $_.Exception.Message }
    }
    $json = ConvertTo-Json -InputObject $result -Depth 100
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), $json + "`n", [Text.UTF8Encoding]::new($false))
}
if ($null -ne $result.error -or $result.retention_failures.Count) { exit 1 }
