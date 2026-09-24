# Indexed insertion boundaries (EXP-0217/0218): capture each arm's original and Rust candidate,
# then insert natively (or provoke the duplicate refusal) on a copy of the original.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

# Currency is invariant four-decimal text.
function Row($Rs, [string]$Table) {
    $id = [int]$Rs.Fields.Item('Id').Value
    if ($Table -eq 'Notes') {
        $body = $Rs.Fields.Item('Body').Value
        return ,([object[]]@($id, $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    $price = $Rs.Fields.Item('Price').Value
    $text = if ($price -is [DBNull]) { $null } else { ([decimal]$price).ToString('0.0000', [Globalization.CultureInfo]::InvariantCulture) }
    return ,([object[]]@($id, [string]$Rs.Fields.Item('Name').Value, $text, [bool]$Rs.Fields.Item('Active').Value))
}

function Read-AllRows($Rs, [string]$Table) {
    $rows = New-Object Collections.ArrayList
    while (-not $Rs.EOF) { [void]$rows.Add((Row $Rs $Table)); $Rs.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

function Capture-Boundary([string]$Path) {
    $before = Identity $Path; $engine = $db = $rs = $null
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $snapshot.user_tables = @()
        foreach ($name in @('Items', 'Notes')) {
            $item = Read-Table $db $name
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-AllRows $rs $name
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item
        }
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if (-not $rs.EOF) { $rs.MoveFirst() }
        $snapshot.traversal = Read-AllRows $rs 'Items'
        $seeks = New-Object Collections.ArrayList
        foreach ($query in -1..201) {
            $rs.Seek('=', [int]$query)
            $row = if ($rs.NoMatch) { $null } else { Row $rs 'Items' }
            [void]$seeks.Add(@{ query = [int]$query; row = $row })
        }
        $snapshot.seek = [object[]]$seeks.ToArray()
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ before = $before; after = (Identity $Path); snapshot = $snapshot }
}

function Insert-Row([string]$Path, [int]$Id, [bool]$Duplicate) {
    $engine = $db = $rs = $null
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/insert/$Id"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false); $rs = $db.OpenRecordset('Items', 2)
        $script:mutationStarted = $true
        try {
            $rs.AddNew(); $rs.Fields.Item('Id').Value = [int]$Id; $rs.Fields.Item('Name').Value = [string]('x' * 80)
            $price = $rs.Fields.Item('Price')
            try {
                if ($Id % 2 -eq 0) { $price.Value = [DBNull]::Value }
                else { $price.Value = [Runtime.InteropServices.CurrencyWrapper]::new([decimal]::Parse('-12.3456', [Globalization.CultureInfo]::InvariantCulture)) }
            } finally { Release $price }
            $rs.Fields.Item('Active').Value = [bool]($Id % 2 -ne 0); $rs.Update()
        } catch {
            $numbers = @($engine.Errors | ForEach-Object { [int]$_.Number })
            if (-not $Duplicate -or $numbers -notcontains 3022) { throw }
            $rs.CancelUpdate()
            return @{ status = 'duplicate'; numbers = $numbers }
        }
        if ($Duplicate) { throw 'Duplicate accepted' }
        return @{ status = 'inserted' }
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
}

$manifest = Read-Manifest 'indexed-boundary.json'
$script:mutationStarted = $false
$result = New-Result 'dao_indexed_boundary_result' $manifest
$result.captures = @{}; $result.operations = @{}; $result.mutation_started = $false
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($arm in $manifest.arms) {
        foreach ($role in @('original', 'candidate')) {
            $name = "$($arm.name)-$role.mdb"
            $result.captures[$name] = Capture-Boundary (Join-Path $env:JET3_WORK $name)
        }
        $name = "$($arm.name)-control.mdb"; $path = Join-Path $env:JET3_WORK $name
        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($arm.name)-original.mdb") -Destination $path
        $result.operations[$arm.name] = Insert-Row $path ([int]$arm.id) ($arm.name -eq 'duplicate')
        $result.captures[$name] = Capture-Boundary $path
    }
} catch { $result.error = Failure $_ } finally {
    $result.mutation_started = $script:mutationStarted
    Save-Outputs $result 'result.json' @('.mdb')
}
if (-not (Test-Complete $result)) { exit 1 }
