# Indexed row insertion/deletion (EXP-0215/0216/0219): three replicas per arm capture the
# original, the Rust candidate, a native control, and next-row and duplicate follow-ups.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

function Record-Failure($Record) {
    $detail = Failure $Record
    $detail.type = $Record.Exception.GetType().FullName
    if ($null -eq $script:failure) { $script:failure = $detail }
    return $detail
}

function Close($Object, [bool]$Database) {
    if ($null -eq $Object) { return }
    try { if ($Database) { $Object.Close() } } catch { $null = Record-Failure $_ } finally { Release $Object }
}

function Set-Long($Rs, [string]$Name, [int]$Value) {
    $field = $null; $prior = $script:endpoint; $script:endpoint = "$prior/field/$Name"
    try { $field = $Rs.Fields.Item($Name); $field.Value = [int]$Value }
    catch { $null = Record-Failure $_; throw }
    finally { Release $field; $script:endpoint = $prior }
}

function Append-Row($Rs, $Values) {
    $Rs.AddNew(); Set-Long $Rs 'Id' ([int]$Values[0]); Set-Long $Rs 'Value' ([int]$Values[1]); $Rs.Update()
}

function Row($Rs) { return ,([object[]]@([int]$Rs.Fields.Item('Id').Value, [int]$Rs.Fields.Item('Value').Value)) }

function Read-AllRows($Rs) {
    $rows = New-Object Collections.ArrayList
    while (-not $Rs.EOF) { [void]$rows.Add((Row $Rs)); $Rs.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

function Create-Control([string]$Path, $Arm) {
    $engine = $workspace = $db = $table = $index = $field = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $script:endpoint = 'control/create'; $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, (Locale-String 'general'), 32); $table = $db.CreateTableDef('Items')
        foreach ($name in @('Id', 'Value')) { $field = $table.CreateField($name, 4); $table.Fields.Append($field); Release $field; $field = $null }
        $index = $table.CreateIndex('ByKey'); $index.Primary = [bool]$Arm.primary; $index.Unique = $true; $index.Required = [bool]$Arm.primary
        $field = $index.CreateField('Id'); $field.Attributes = [int]$Arm.descending; $index.Fields.Append($field); Release $field; $field = $null
        $table.Indexes.Append($index); $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Items', 2)
        foreach ($row in $Arm.rows) { $script:endpoint = 'control/seed'; Append-Row $rs $row }
    } catch { $null = Record-Failure $_; throw }
    finally { Close $rs $true; Close $field $false; Close $index $false; Close $table $false; Close $db $true; Close $workspace $false; Close $engine $false }
}

# Modes: control (the arm's insert or deletions), next (follow row), duplicate (must fail with 3022).
function Mutate-Arm([string]$Path, $Arm, [string]$Mode) {
    $engine = $db = $rs = $null; $operation = @{ status = 'complete' }
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ByKey'; $script:endpoint = "mutate/$Mode"
        if ($Mode -eq 'duplicate') {
            $accepted = $false; $errorDetail = $null; $numbers = @()
            try { Append-Row $rs $Arm.duplicate; $accepted = $true }
            catch { $errorDetail = Failure $_; $errorDetail.type = $_.Exception.GetType().FullName; $numbers = @($engine.Errors | ForEach-Object { [int]$_.Number }) }
            $operation = @{ accepted = $accepted; error = $errorDetail; numbers = $numbers }
            if (-not $accepted) { $rs.CancelUpdate() }
            if ($accepted -or $numbers -notcontains 3022) { throw 'Expected duplicate-key rejection' }
        } elseif ($Mode -eq 'next') { Append-Row $rs $Arm.follow }
        elseif ($Arm.kind -eq 'insert') { Append-Row $rs $Arm.insert }
        else {
            foreach ($id in $Arm.delete) { $rs.Seek('=', [int]$id); if ($rs.NoMatch) { throw 'Deleted key absent' }; $rs.Delete() }
        }
    } catch { $null = Record-Failure $_; throw }
    finally { Close $rs $true; Close $db $true; Close $engine $false }
    return $operation
}

function Capture-Arm([string]$Path, $Arm) {
    $before = Identity $Path; $engine = $db = $rs = $null; $snapshot = @{}; $errorDetail = $null
    try {
        $script:endpoint = 'capture/open'
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $item = Read-Table $db 'Items'
        $script:endpoint = 'capture/rows'
        $rs = $db.OpenRecordset('Items', 4); $item.rows = Read-AllRows $rs; $snapshot.user_tables = @($item)
        Close $rs $true; $rs = $null
        $script:endpoint = 'capture/index'
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ByKey'
        if (-not $rs.EOF) { $rs.MoveFirst() }
        $snapshot.traversal = Read-AllRows $rs
        $seeks = New-Object Collections.ArrayList
        foreach ($query in $Arm.queries) {
            $rs.Seek('=', [int]$query)
            $row = if ($rs.NoMatch) { $null } else { Row $rs }
            [void]$seeks.Add(@{ query = [int]$query; row = $row })
        }
        $snapshot.seek = [object[]]$seeks.ToArray()
    } catch { $errorDetail = Record-Failure $_ }
    finally { Close $rs $true; Close $db $true; Close $engine $false }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path)
              status = $(if ($null -eq $errorDetail) { 'pass' } else { 'error' }); error = $errorDetail; snapshot = $snapshot }
}

$manifest = Read-Manifest 'indexed-rows.json'
$script:failure = $null; $script:mutationStarted = $false
$result = New-Result 'dao_indexed_row_result' $manifest
$result.pairs = @(); $result.mutation_started = $false
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($arm in $manifest.arms) {
        foreach ($replica in 1..3) {
            $prefix = "$($arm.name)-r$replica"
            $pair = @{ arm = $arm.name; replica = $replica; captures = @{}; operations = @{} }; $result.pairs += ,$pair
            foreach ($role in @('original', 'candidate')) {
                Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($arm.name)-$role.mdb") -Destination (Join-Path $env:JET3_WORK "$prefix-$role.mdb")
            }
            $control = Join-Path $env:JET3_WORK "$prefix-control-original.mdb"; Create-Control $control $arm
            foreach ($role in @('original', 'candidate', 'control-original')) {
                $pair.captures[$role] = Capture-Arm (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $arm
                if ($pair.captures[$role].status -ne 'pass' -or $null -ne $script:failure) { throw 'Baseline capture failed' }
            }
            $path = Join-Path $env:JET3_WORK "$prefix-control.mdb"; Copy-Item -LiteralPath $control -Destination $path
            $pair.operations.control = Mutate-Arm $path $arm 'control'; $pair.captures.control = Capture-Arm $path $arm
            if ($pair.captures.control.status -ne 'pass' -or $null -ne $script:failure) { throw 'Control mutation/capture failed' }
            foreach ($mode in @('next', 'duplicate')) {
                foreach ($role in @('candidate', 'control')) {
                    $name = "$role-$mode"; $path = Join-Path $env:JET3_WORK "$prefix-$name.mdb"
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$prefix-$role.mdb") -Destination $path
                    $pair.operations[$name] = Mutate-Arm $path $arm $mode; $pair.captures[$name] = Capture-Arm $path $arm
                    if ($pair.captures[$name].status -ne 'pass' -or $null -ne $script:failure) { throw 'Continuation mutation/capture failed' }
                }
            }
        }
    }
} catch { $null = Record-Failure $_ } finally {
    $result.error = $script:failure; $result.mutation_started = $script:mutationStarted
    Save-Outputs $result 'result.json' @('.mdb')
}
if (-not (Test-Complete $result)) { exit 1 }
