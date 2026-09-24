# Practical Items/Notes lifecycle (EXP-0229/0242): replay each stage on a DAO control and
# capture the Rust candidate and the control after it.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

function Set-ItemRow($Recordset, $Values) {
    $Recordset.Fields.Item('Id').Value = [int]$Values[0]
    $Recordset.Fields.Item('Name').Value = [string]$Values[1]
    $price = $Recordset.Fields.Item('Price')
    try {
        if ($null -eq $Values[2]) { $price.Value = [DBNull]::Value }
        else { $price.Value = [Runtime.InteropServices.CurrencyWrapper]::new(([decimal]$Values[2] / [decimal]10000)) }
    } finally { Release $price }
    $Recordset.Fields.Item('Active').Value = [bool]$Values[3]
}

function Read-ItemRow($Recordset, [string]$Name) {
    $id = [int]$Recordset.Fields.Item('Id').Value
    if ($Name -eq 'Notes') {
        $body = $Recordset.Fields.Item('Body').Value
        return ,([object[]]@($id, $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    $price = $Recordset.Fields.Item('Price').Value
    return ,([object[]]@($id, [string]$Recordset.Fields.Item('Name').Value,
        $(if ($price -is [DBNull]) { $null } else { [long]([decimal]$price * [decimal]10000) }),
        [bool]$Recordset.Fields.Item('Active').Value))
}

function Read-ItemRows($Recordset, [string]$Name) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-ItemRow $Recordset $Name)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

function New-ItemControl([string]$Path) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, (Locale-String 'general'), 32)
        foreach ($name in @('Items', 'Notes')) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/create/$name"
            $table = $db.CreateTableDef($name)
            $specs = if ($name -eq 'Items') { @(@('Id', 4, 4), @('Name', 10, 80), @('Price', 5, 8), @('Active', 1, 1)) }
                     else { @(@('Id', 4, 4), @('Body', 12, 0)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                $table.Fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                $index = $table.CreateIndex('ById')
                $index.Primary = $true; $index.Unique = $true; $index.Required = $true; $index.IgnoreNulls = $false
                $key = $index.CreateField('Id'); $index.Fields.Append($key); Release $key; $key = $null
                $table.Indexes.Append($index); Release $index; $index = $null
            }
            $db.TableDefs.Append($table); Release $table; $table = $null
        }
        $rs = $db.OpenRecordset('Notes', 2)
        $rs.AddNew(); $rs.Fields.Item('Id').Value = 7; $rs.Fields.Item('Body').Value = [string]('n' * 4096); $rs.Update()
        $rs.AddNew(); $rs.Fields.Item('Id').Value = 8; $rs.Fields.Item('Body').Value = [DBNull]::Value; $rs.Update()
    } finally {
        Close-Com $rs $true
        Release $key; Release $index; Release $field; Release $table
        Close-Com $db $true; Release $workspace; Release $engine
    }
}

function Mutate-Stage([string]$Path, $Operations) {
    $before = Identity $Path
    $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        foreach ($operation in $Operations) {
            $id = if ($operation.kind -eq 'insert') { $operation.row[0] } else { $operation.id }
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($operation.kind)/$id"
            if ($operation.kind -eq 'insert') { $rs.AddNew(); Set-ItemRow $rs $operation.row; $rs.Update() }
            else {
                $rs.Seek('=', [int]$operation.id); if ($rs.NoMatch) { throw 'Mutation key absent' }
                switch ($operation.kind) {
                    'replace' { $rs.Edit(); Set-ItemRow $rs $operation.row; $rs.Update() }
                    'delete' { $rs.Delete() }
                    default { throw 'Unknown operation' }
                }
            }
        }
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ before = $before; after = (Identity $Path); count = @($Operations).Count }
}

function Capture-Items([string]$Path) {
    $before = Identity $Path
    $engine = $db = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $snapshot.user_tables = @()
        foreach ($name in @('Items', 'Notes')) {
            $item = Read-Table $db $name
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-ItemRows $rs $name
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item
        }
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if (-not $rs.EOF) { $rs.MoveFirst() }
        $snapshot.traversal = Read-ItemRows $rs 'Items'; $snapshot.seek = @()
        foreach ($query in $manifest.queries) {
            $rs.Seek('=', [int]$query)
            $row = if ($rs.NoMatch) { $null } else { Read-ItemRow $rs 'Items' }
            $snapshot.seek += ,@{ query = [int]$query; row = $row }
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

$manifest = Read-Manifest 'practical-lifecycle.json'
$result = New-Result 'dao_practical_lifecycle_result' $manifest
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($case in $manifest.cases) {
        $outcome = @{ name = [string]$case.name; status = 'running'; created = $null; stages = @(); error = $null }; $result.cases += ,$outcome
        try {
            $control = Join-Path $env:JET3_WORK "$($case.name)-control-working.mdb"
            New-ItemControl $control
            $createdFile = "$($case.name)-control-created.mdb"
            Copy-Item -LiteralPath $control -Destination (Join-Path $env:JET3_WORK $createdFile)
            $outcome.created = @{ file = $createdFile; image = (Identity $control) }
            foreach ($stage in $case.stages) {
                $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{}; control_mutation = $null }; $outcome.stages += ,$checkpoint
                $checkpoint.control_mutation = Mutate-Stage $control $stage.operations
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($case.name)-$($stage.name)-$role.mdb"
                    $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($case.name)-$($stage.name).mdb" } else { $control }
                    Copy-Item -LiteralPath $source -Destination $path
                    $checkpoint.roles[$role] = Capture-Items $path
                    if ($checkpoint.roles[$role].status -ne 'pass') { throw 'Checkpoint capture failed' }
                }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
} catch { $result.error = Failure $_ } finally { Save-Outputs $result 'result.json' }
if (-not (Test-Complete $result)) { exit 1 }
