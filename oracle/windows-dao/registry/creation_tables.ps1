# Creation layouts (EXP-0222/0241): for two replicas of each arm, capture the Rust candidate
# beside a DAO-created control, then insert natively into copies of both and capture again.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

function Row($Recordset, $Table) {
    $values = [object[]]::new($Table.columns.Count); $fields = $Recordset.Fields; $field = $null
    try {
        for ($i = 0; $i -lt $values.Length; $i++) {
            $field = $fields.Item([string]$Table.columns[$i]); $values[$i] = [int]$field.Value
            Release $field; $field = $null
        }
    } finally { Release $field; Release $fields }
    return ,$values
}

function All-Rows($Recordset, $Table) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Row $Recordset $Table)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

function Add-Row($Db, $Table, $Values) {
    $rs = $db.OpenRecordset([string]$Table.name, 2); $fields = $rs.Fields; $field = $null
    try {
        $rs.AddNew()
        for ($i = 0; $i -lt $Table.columns.Count; $i++) {
            $field = $fields.Item([string]$Table.columns[$i]); $field.Value = [int]$Values[$i]
            Release $field; $field = $null
        }
        $rs.Update()
    } finally { Release $field; Release $fields; Close-Com $rs $true }
}

function New-TablesControl([string]$Path, $Arm) {
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $field = $indexes = $index = $keys = $key = $null
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
            foreach ($row in $spec.rows) { Add-Row $db $spec $row }
        }
    } finally {
        Release $key; Release $keys; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        Close-Com $db $true; Release $workspace; Release $workspaces; Release $engine
    }
}

function Insert-Native([string]$Source, [string]$Destination, $Arm) {
    Copy-Item -LiteralPath $Source -Destination $Destination
    $engine = $db = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Destination, $false, $false)
        foreach ($operation in $Arm.native) {
            $script:endpoint = "native/$($operation.table)/insert"
            $spec = @($Arm.tables | Where-Object { $_.name -ceq $operation.table })[0]
            Add-Row $db $spec $operation.row
        }
    } finally { Close-Com $db $true; Release $engine }
}

function Capture-Tables([string]$Path, $Arm) {
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
            $observation.rows = All-Rows $rs $spec
            Close-Com $rs $true; $rs = $null
            $observation.traversals = @{}; $observation.seeks = @{}
            foreach ($requested in $spec.indexes) {
                $script:endpoint = "capture/$($spec.name)/index/$($requested.name)"
                $rs = $db.OpenRecordset([string]$spec.name, 1); $rs.Index = [string]$requested.name
                if (-not $rs.EOF) { $rs.MoveFirst() }
                $observation.traversals[$requested.name] = All-Rows $rs $spec
                $seeks = New-Object Collections.ArrayList
                foreach ($query in @(-1000, 0, 2, 16, 1000)) {
                    $rs.Seek('=', [int]$query)
                    $row = if ($rs.NoMatch) { $null } else { Row $rs $spec }
                    [void]$seeks.Add(@{ query = [int]$query; row = $row })
                }
                $observation.seeks[$requested.name] = [object[]]$seeks.ToArray()
                Close-Com $rs $true; $rs = $null
            }
            $snapshot.tables += ,$observation
            Release $table; $table = $null
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        Close-Com $rs $true; Release $table; Close-Com $db $true; Release $engine
    }
    @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

$manifest = Read-Manifest 'creation-tables.json'
$result = New-Result 'dao_creation_tables_result' $manifest
$result.pairs = @()
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($arm in $manifest.arms) {
        foreach ($replica in 1..2) {
            $prefix = "$($arm.name)-r$replica"
            $pair = @{ arm = [string]$arm.name; replica = $replica; captures = @{}; native = @{} }; $result.pairs += ,$pair
            Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($arm.name).mdb") -Destination (Join-Path $env:JET3_WORK "$prefix-candidate.mdb")
            New-TablesControl (Join-Path $env:JET3_WORK "$prefix-control.mdb") $arm
            foreach ($role in @('candidate', 'control')) {
                $pair.captures[$role] = Capture-Tables (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $arm
                if ($pair.captures[$role].status -ne 'pass') { throw 'Capture failed' }
                $native = Join-Path $env:JET3_WORK "$prefix-native-$role.mdb"
                Insert-Native (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $native $arm
                $pair.native[$role] = Capture-Tables $native $arm
                if ($pair.native[$role].status -ne 'pass') { throw 'Native successor capture failed' }
            }
        }
    }
} catch { $result.error = Failure $_ } finally {
    Save-Outputs $result 'result.json' @('.mdb')
}
if (-not (Test-Complete $result)) { exit 1 }
