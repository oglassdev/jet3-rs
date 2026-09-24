# Index-tree mutations (EXP-0223/0225): build the DAO control per case, replay each stage,
# capture candidate and control, then apply native successor operations to both.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

function Base-Row([int]$Id, [bool]$Deep) {
    if ($Deep) { return ,([object[]]@($Id, ($Id * 17 + 3))) }
    return ,([object[]]@($Id, ('x' * 80), ('11' * 8)))
}

function Row($Rs, [string]$Name, [bool]$Deep) {
    $id = [int]$Rs.Fields.Item('Id').Value
    if ($Name -eq 'Notes') {
        $body = $Rs.Fields.Item('Body').Value
        return ,([object[]]@($id, $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    if ($Deep) { return ,([object[]]@($id, [int]$Rs.Fields.Item('Value').Value)) }
    return ,([object[]]@($id, [string]$Rs.Fields.Item('Text').Value, (Hex ([byte[]]$Rs.Fields.Item('Bytes').Value))))
}

function Set-TreeRow($Rs, $Value, [bool]$Deep) {
    $Rs.Fields.Item('Id').Value = [int]$Value[0]
    if ($Deep) { $Rs.Fields.Item('Value').Value = [int]$Value[1] }
    else { $Rs.Fields.Item('Text').Value = [string]$Value[1]; $Rs.Fields.Item('Bytes').Value = From-Hex ([string]$Value[2]) }
}

# Rows go to a sidecar file; the capture keeps its identity.
function Save-Rows($Rs, [string]$Name, [bool]$Deep, [string]$File) {
    $rows = New-Object Collections.ArrayList
    while (-not $Rs.EOF) { [void]$rows.Add((Row $Rs $Name $Deep)); $Rs.MoveNext() }
    $path = Join-Path $env:JET3_WORK $File
    Write-Json ([object[]]$rows.ToArray()) $path
    $saved = Identity $path; $saved.file = $File
    return $saved
}

function New-TreeControl([string]$Path, $Case) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $script:endpoint = "$($Case.name)/create_database"
        $db = $workspace.CreateDatabase($Path, (Locale-String 'general'), 32)
        foreach ($name in @('Items', 'Notes')) {
            $table = $db.CreateTableDef($name)
            $specs = if ($name -eq 'Notes') { @(@('Id', 4, 4), @('Body', 12, 0)) }
                     elseif ($Case.deep) { @(@('Id', 4, 4), @('Value', 4, 4)) }
                     else { @(@('Id', 4, 4), @('Text', 10, 80), @('Bytes', 9, 80)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                $table.Fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                $index = $table.CreateIndex('ById'); $index.Primary = -not [bool]$Case.descending
                $index.Unique = $true; $index.Required = -not [bool]$Case.descending; $index.IgnoreNulls = $false
                $key = $index.CreateField('Id'); if ($Case.descending) { $key.Attributes = 1 }
                $index.Fields.Append($key); Release $key; $key = $null
                $table.Indexes.Append($index); Release $index; $index = $null
            }
            $db.TableDefs.Append($table); Release $table; $table = $null
            $rs = $db.OpenRecordset($name, 2)
            if ($name -eq 'Items') {
                for ($id = 0; $id -lt $Case.initial_count; $id++) {
                    $script:endpoint = "$($Case.name)/initial_row/$id"
                    $rs.AddNew(); Set-TreeRow $rs (Base-Row $id $Case.deep) $Case.deep; $rs.Update()
                }
            } else {
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 7; $rs.Fields.Item('Body').Value = [string]('n' * 4096); $rs.Update()
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 8; $rs.Fields.Item('Body').Value = [DBNull]::Value; $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        Close-Com $rs $true
        Release $key; Release $index; Release $field; Release $table
        Close-Com $db $true; Release $workspace; Release $engine
    }
}

# Operations: insert {row}, delete {id}, key {id, next_id}, row {id, row}.
function Mutate-Tree([string]$Path, $Case, $Operation) {
    $engine = $db = $rs = $null
    $before = Identity $Path
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($Operation.kind)"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if ($Operation.kind -eq 'insert') { $rs.AddNew(); Set-TreeRow $rs $Operation.row $Case.deep; $rs.Update() }
        else {
            $rs.Seek('=', [int]$Operation.id); if ($rs.NoMatch) { throw 'Mutation key absent' }
            switch ($Operation.kind) {
                'delete' { $rs.Delete() }
                'key' { $rs.Edit(); $rs.Fields.Item('Id').Value = [int]$Operation.next_id; $rs.Update() }
                'row' { $rs.Edit(); Set-TreeRow $rs $Operation.row $Case.deep; $rs.Update() }
                default { throw 'Unknown operation' }
            }
        }
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ request = $Operation; status = 'pass'; before = $before; after = (Identity $Path) }
}

function Capture-Tree([string]$Path, $Case) {
    $before = Identity $Path
    $engine = $db = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    $stem = [IO.Path]::GetFileNameWithoutExtension($Path)
    try {
        $script:endpoint = "$stem/capture/open"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $snapshot.user_tables = @()
        foreach ($name in @('Items', 'Notes')) {
            $script:endpoint = "$stem/capture/$name"
            $item = Read-Table $db $name
            $rs = $db.OpenRecordset($name, 4)
            $item.rows = Save-Rows $rs $name $Case.deep "$stem-$name.rows.json"
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item
        }
        $script:endpoint = "$stem/capture/index"
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if (-not $rs.EOF) { $rs.MoveFirst() }
        $snapshot.traversal = Save-Rows $rs 'Items' $Case.deep "$stem.traversal.json"
        $snapshot.seek = @()
        foreach ($query in $manifest.queries) {
            $rs.Seek('=', [int]$query)
            $row = if ($rs.NoMatch) { $null } else { Row $rs 'Items' $Case.deep }
            $snapshot.seek += ,@{ query = [int]$query; row = $row }
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

function Invoke-Continuation($Case) {
    $outcome = @{ name = [string]$Case.name; status = 'running'; roles = @{}; operation = $null; error = $null }
    try {
        foreach ($role in @('candidate', 'control')) {
            $source = Join-Path $env:JET3_WORK $(if ($role -eq 'candidate') { [string]$Case.candidate_file } else { [string]$Case.source_file })
            $path = Join-Path $env:JET3_WORK "$($Case.name)-continued-$role.mdb"
            Copy-Item -LiteralPath $source -Destination $path
            if ($role -eq 'control') { $outcome.operation = Mutate-Tree $path $Case $Case.operation }
            $outcome.roles[$role] = Capture-Tree $path $Case
            if ($outcome.roles[$role].status -ne 'pass') { throw 'Continuation capture failed' }
        }
        $outcome.status = 'pass'
    } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    return $outcome
}

function Invoke-Mutations($Case) {
    $outcome = @{ name = [string]$Case.name; status = 'running'; stages = @(); native = @{}; error = $null }
    try {
        $control = Join-Path $env:JET3_WORK "$($Case.name)-control-working.mdb"
        New-TreeControl $control $Case
        foreach ($stage in $Case.stages) {
            $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{} }; $outcome.stages += ,$checkpoint
            foreach ($operation in $stage.operations) { $null = Mutate-Tree $control $Case $operation }
            foreach ($role in @('candidate', 'control')) {
                $file = "$($Case.name)-$($stage.name)-$role.mdb"; $path = Join-Path $env:JET3_WORK $file
                $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($Case.name)-$($stage.name).mdb" } else { $control }
                Copy-Item -LiteralPath $source -Destination $path
                $record = @{ file = $file; image = (Identity $path); capture = $null }; $checkpoint.roles[$role] = $record
                if ($stage.capture) {
                    $record.capture = Capture-Tree $path $Case
                    if ($record.capture.status -ne 'pass') { throw 'Checkpoint capture failed' }
                }
            }
        }
        foreach ($role in @('candidate', 'control')) {
            $path = Join-Path $env:JET3_WORK "$($Case.name)-native-$role.mdb"
            Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($Case.name)-regrown-$role.mdb") -Destination $path
            $native = @{ operations = @(); capture = $null }; $outcome.native[$role] = $native
            $step = 0
            foreach ($operation in $Case.native) {
                $receipt = Mutate-Tree $path $Case $operation; $step++
                $receipt.file = "$($Case.name)-native-$role-step$step.mdb"
                Copy-Item -LiteralPath $path -Destination (Join-Path $env:JET3_WORK $receipt.file)
                $native.operations += ,$receipt
            }
            $native.capture = Capture-Tree $path $Case
            if ($native.capture.status -ne 'pass') { throw 'Native successor capture failed' }
        }
        $outcome.status = 'pass'
    } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    return $outcome
}

$manifest = Read-Manifest 'index-tree-mutation.json'
$result = New-Result 'dao_index_tree_mutation_result' $manifest
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($case in $manifest.cases) {
        $result.cases += ,$(if ($manifest.round -eq 'continuation') { Invoke-Continuation $case } else { Invoke-Mutations $case })
    }
} catch { $result.error = Failure $_ } finally { Save-Outputs $result 'result.json' }
if (-not (Test-Complete $result)) { exit 1 }
