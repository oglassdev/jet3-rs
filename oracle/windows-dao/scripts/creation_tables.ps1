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
    $values = [object[]]::new($Table.columns.Count); $fields = $Recordset.Fields; $field = $null
    try {
        for ($i = 0; $i -lt $values.Length; $i++) {
            $field = $fields.Item([string]$Table.columns[$i]); $values[$i] = [int]$field.Value
            Release $field; $field = $null
        }
    } finally { Release $field; Release $fields }
    return ,$values
}

function Read-Rows($Recordset, $Table) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Table)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function Read-Names($Collection) {
    $item = $null; $names = @()
    try {
        for ($i = 0; $i -lt $Collection.Count; $i++) {
            $item = $Collection.Item($i); $names += [string]$item.Name
            Release $item; $item = $null
        }
    } finally { Release $item; Release $Collection }
    return $names
}
function Read-Fields($Table) {
    $fields = $Table.Fields; $field = $null; $items = @()
    try {
        for ($i = 0; $i -lt $fields.Count; $i++) {
            $field = $fields.Item($i)
            $items += @{ name = [string]$field.Name; type = [int]$field.Type; size = [int]$field.Size; attributes = [int]$field.Attributes;
                         required = [bool]$field.Required; default_value = [string]$field.DefaultValue }
            Release $field; $field = $null
        }
    } finally { Release $field; Release $fields }
    return $items
}
function Read-Indexes($Table) {
    $indexes = $Table.Indexes; $index = $fields = $field = $null; $items = @()
    try {
        for ($i = 0; $i -lt $indexes.Count; $i++) {
            $index = $indexes.Item($i); $fields = $index.Fields; $keys = @()
            for ($j = 0; $j -lt $fields.Count; $j++) {
                $field = $fields.Item($j)
                $keys += @{name = [string]$field.Name; attributes = [int]$field.Attributes}
                Release $field; $field = $null
            }
            Release $fields; $fields = $null
            $items += @{ name = [string]$index.Name; primary = [bool]$index.Primary; unique = [bool]$index.Unique; required = [bool]$index.Required;
                         foreign = [bool]$index.Foreign; ignore_nulls = [bool]$index.IgnoreNulls; fields = $keys }
            Release $index; $index = $null
        }
    } finally { Release $field; Release $fields; Release $index; Release $indexes }
    return $items
}

function New-Control([string]$Path, $Arm) {
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $field = $indexes = $index = $keys = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspaces = $engine.Workspaces; $workspace = $workspaces.Item(0)
        $script:endpoint = 'create_database'
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32); $tables = $db.TableDefs
        foreach ($spec in $Arm.tables) {
            $script:endpoint = "create_table/$($spec.name)"
            $table = $db.CreateTableDef([string]$spec.name); $fields = $table.Fields; $indexes = $table.Indexes
            foreach ($name in $spec.columns) {
                $field = $table.CreateField([string]$name, 4, 4)
                $fields.Append($field); Release $field; $field = $null
            }
            foreach ($requested in $spec.indexes) {
                $index = $table.CreateIndex([string]$requested.name); $keys = $index.Fields
                $index.Primary = [bool]$requested.primary
                $index.Unique = [bool]$requested.unique
                $index.Required = [bool]$requested.primary
                $index.IgnoreNulls = $false
                $key = $index.CreateField([string]$spec.columns[[int]$requested.column])
                if ($requested.descending) { $key.Attributes = 1 }
                $keys.Append($key); Release $key; $key = $null
                $indexes.Append($index); Release $keys; $keys = $null; Release $index; $index = $null
            }
            $tables.Append($table); Release $indexes; $indexes = $null; Release $fields; $fields = $null; Release $table; $table = $null
            $rs = $db.OpenRecordset([string]$spec.name, 2); $fields = $rs.Fields
            foreach ($row in $spec.rows) {
                $rs.AddNew()
                for ($i = 0; $i -lt $spec.columns.Count; $i++) {
                    $field = $fields.Item([string]$spec.columns[$i]); $field.Value = [int]$row[$i]; Release $field; $field = $null
                }
                $rs.Update()
            }
            Release $fields; $fields = $null; $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $keys; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $workspaces; Release $engine
    }
}
function Continue-File([string]$Source, [string]$Destination, $Arm) {
    Copy-Item -LiteralPath $Source -Destination $Destination
    $engine = $db = $rs = $fields = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Destination, $false, $false)
        foreach ($operation in $Arm.native) {
            $script:endpoint = "native/$($operation.table)/insert"
            $spec = @($Arm.tables | Where-Object { $_.name -ceq $operation.table })[0]
            $rs = $db.OpenRecordset([string]$operation.table, 2); $fields = $rs.Fields
            $rs.AddNew()
            for ($i = 0; $i -lt $spec.columns.Count; $i++) {
                $field = $fields.Item([string]$spec.columns[$i]); $field.Value = [int]$operation.row[$i]
                Release $field; $field = $null
            }
            $rs.Update(); Release $fields; $fields = $null; $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        Release $field; Release $fields
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
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
        $snapshot.inventory = @(Read-Names $db.TableDefs | Sort-Object)
        $snapshot.relations = @(Read-Names $db.Relations)
        $snapshot.queries = @(Read-Names $db.QueryDefs)
        foreach ($spec in $Arm.tables) {
            $script:endpoint = "capture/$($spec.name)/metadata"
            $tables = $db.TableDefs
            try { $table = $tables.Item([string]$spec.name) } finally { Release $tables }
            $observation = @{ name = [string]$table.Name; attributes = [int]$table.Attributes }
            $observation.columns = @(Read-Fields $table)
            $observation.indexes = @(Read-Indexes $table)
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
            $pair = @{ arm = [string]$arm.name; replica = $replica; captures = @{}; native = @{} }; $result.pairs += ,$pair
            $source = Join-Path $env:JET3_WORK "$($arm.name).mdb"
            $actual = Identity $source
            if ($actual.sha256 -cne $arm.image.sha256 -or $actual.size -ne $arm.image.size) { throw 'Candidate identity differs' }
            $candidate = Join-Path $env:JET3_WORK "$prefix-candidate.mdb"
            Copy-Item -LiteralPath $source -Destination $candidate
            New-Control (Join-Path $env:JET3_WORK "$prefix-control.mdb") $arm
            foreach ($role in @('candidate', 'control')) {
                $pair.captures[$role] = Capture (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $arm
                if ($pair.captures[$role].status -ne 'pass') { throw 'Capture failed' }
                $native = Join-Path $env:JET3_WORK "$prefix-native-$role.mdb"
                Continue-File (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $native $arm
                $pair.native[$role] = Capture $native $arm
                if ($pair.native[$role].status -ne 'pass') { throw 'Native successor capture failed' }
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
