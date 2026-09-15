Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$helper = Join-Path $env:JET3_WORK 'field_update.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($helper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Helper syntax' }
foreach ($name in @('Identity', 'Release', 'Write-Json')) {
    $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $false))
    if ($found.Count -ne 1) { throw 'Missing helper' }
    Invoke-Expression $found[0].Extent.Text
}
function Failure($Record) {
    return @{ endpoint = $script:endpoint; message = $Record.Exception.Message; hresult = $Record.Exception.HResult; stack = $Record.ScriptStackTrace }
}
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
function Read-Row($Recordset, [string]$Name) {
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
function Read-Rows($Recordset, [string]$Name) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Name)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function New-Control([string]$Path) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
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
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $engine
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
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ before = $before; after = (Identity $Path); count = @($Operations).Count }
}
function Capture([string]$Path) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = @{ version = [string]$db.Version; tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object);
            queries = @($db.QueryDefs | ForEach-Object { [string]$_.Name }); relations = @($db.Relations | ForEach-Object { [string]$_.Name }); user_tables = @() }
        foreach ($name in @('Items', 'Notes')) {
            $table = $db.TableDefs.Item($name)
            $item = @{ name = $name; attributes = [int]$table.Attributes }
            $item.fields = @($table.Fields | ForEach-Object {
                @{ name = [string]$_.Name; type = [int]$_.Type; size = [int]$_.Size; attributes = [int]$_.Attributes;
                   required = [bool]$_.Required; allow_zero_length = [bool]$_.AllowZeroLength; default_value = [string]$_.DefaultValue }
            })
            $item.indexes = @($table.Indexes | ForEach-Object {
                @{ name = [string]$_.Name; primary = [bool]$_.Primary; unique = [bool]$_.Unique; required = [bool]$_.Required; foreign = [bool]$_.Foreign; ignore_nulls = [bool]$_.IgnoreNulls;
                   fields = @($_.Fields | ForEach-Object { @{ name = [string]$_.Name; attributes = [int]$_.Attributes } }) }
            })
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-Rows $rs $name
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item; Release $table; $table = $null
        }
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if (-not $rs.EOF) { $rs.MoveFirst() }
        $snapshot.traversal = Read-Rows $rs 'Items'; $snapshot.seek = @()
        foreach ($query in $manifest.queries) {
            $rs.Seek('=', [int]$query)
            $row = if ($rs.NoMatch) { $null } else { Read-Row $rs 'Items' }
            $snapshot.seek += ,@{ query = [int]$query; row = $row }
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'practical-lifecycle.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_practical_lifecycle_result'; source_revision = $manifest.source_revision;
    manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/practical_lifecycle.ps1'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
        if ((Identity $pair[0]).sha256 -cne $manifest.inputs.($pair[1]).sha256) { throw 'Producer/helper identity differs' }
    }
    foreach ($property in $manifest.files.PSObject.Properties) {
        $actual = Identity (Join-Path $env:JET3_WORK $property.Name)
        if ($actual.sha256 -cne $property.Value.sha256 -or $actual.size -ne $property.Value.size) { throw "Input identity differs: $($property.Name)" }
    }
    $engine = New-Object -ComObject DAO.DBEngine.36
    try {
        $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
        if ($dll.Count -ne 1) { throw 'Loaded DAO module absent or ambiguous' }
        $result.environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; provider_version = [string]$engine.Version;
            os = [Environment]::OSVersion.VersionString; powershell = [string]$PSVersionTable.PSVersion; clr = [Environment]::Version.ToString();
            culture = [Globalization.CultureInfo]::CurrentCulture.Name; timezone = [TimeZoneInfo]::Local.Id;
            dll = @{ path = $dll[0].FileName; version = $dll[0].FileVersionInfo.FileVersion; sha256 = (Identity $dll[0].FileName).sha256 } }
    } finally { Release $engine }
    foreach ($case in $manifest.cases) {
        $outcome = @{ name = [string]$case.name; status = 'running'; created = $null; stages = @(); error = $null }; $result.cases += ,$outcome
        try {
            $control = Join-Path $env:JET3_WORK "$($case.name)-control-working.mdb"
            New-Control $control
            $createdFile = "$($case.name)-control-created.mdb"
            Copy-Item -LiteralPath $control -Destination (Join-Path $env:JET3_WORK $createdFile)
            $outcome.created = @{ file = $createdFile; image = (Identity $control) }
            foreach ($stage in $case.stages) {
                $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{}; control_mutation = $null }; $outcome.stages += ,$checkpoint
                $checkpoint.control_mutation = Mutate-Stage $control $stage.operations
                foreach ($role in @('candidate', 'control')) {
                    $file = "$($case.name)-$($stage.name)-$role.mdb"; $path = Join-Path $env:JET3_WORK $file
                    $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($case.name)-$($stage.name).mdb" } else { $control }
                    Copy-Item -LiteralPath $source -Destination $path
                    $checkpoint.roles[$role] = Capture $path
                    if ($checkpoint.roles[$role].status -ne 'pass') { throw 'Checkpoint capture failed' }
                }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json') }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
