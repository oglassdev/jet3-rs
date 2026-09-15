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

function Set-Cell($Recordset, $Case, [int]$Column, $Value) {
    if ($Column -eq 0 -and $Case.generated) { return }
    $spec = $Case.fields[$Column]; $field = $Recordset.Fields.Item([string]$spec[0])
    try {
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        if ($null -eq $Value) { $field.Value = [DBNull]::Value; return }
        switch ([int]$spec[1]) {
            4 { $field.Value = [int]$Value }
            12 { $field.Value = [string]$Value }
            11 {
                $text = [string]$Value; $bytes = [byte[]]::new($text.Length / 2)
                for ($n = 0; $n -lt $bytes.Length; $n++) { $bytes[$n] = [Convert]::ToByte($text.Substring(2 * $n, 2), 16) }
                $field.Value = [DBNull]::Value; $field.AppendChunk([byte[]]$bytes)
            }
            default { throw 'Unknown field type' }
        }
    } finally { Release $field }
}
function Set-Row($Recordset, $Case, $Values) {
    for ($i = 0; $i -lt $Case.fields.Count; $i++) { Set-Cell $Recordset $Case $i $Values[$i] }
}
function Read-Row($Recordset, [string]$Name, $Case) {
    if ($Name -eq 'Notes') {
        $body = $Recordset.Fields.Item('Body').Value
        return ,([object[]]@([int]$Recordset.Fields.Item('Id').Value, $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    $values = [object[]]::new($Case.fields.Count)
    for ($i = 0; $i -lt $values.Length; $i++) {
        $value = $Recordset.Fields.Item([string]$Case.fields[$i][0]).Value
        $values[$i] = if ($value -is [DBNull]) { $null }
            elseif ([int]$Case.fields[$i][1] -eq 11) { [BitConverter]::ToString([byte[]]$value).Replace('-', '').ToLowerInvariant() }
            elseif ([int]$Case.fields[$i][1] -eq 12) { [string]$value }
            else { [int]$value }
    }
    return ,$values
}
function Read-Rows($Recordset, [string]$Name, $Case) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Name $Case)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function New-Control([string]$Path, $Case) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $order = if ($Case.later) { @('Notes', 'Items') } else { @('Items', 'Notes') }
        foreach ($name in $order) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/create/$name"
            $table = $db.CreateTableDef($name)
            $specs = if ($name -eq 'Items') { $Case.fields } else { @(@('Id', 4, 4, $false), @('Body', 12, 0, $false)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                if ([bool]$spec[3]) { $field.Attributes = 16 }
                $table.Fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                foreach ($spec in $Case.indexes) {
                    $index = $table.CreateIndex([string]$spec.name)
                    $index.Primary = [bool]$spec.primary; $index.Unique = [bool]$spec.unique
                    $index.Required = [bool]$spec.required; $index.IgnoreNulls = [bool]$spec.ignore
                    foreach ($component in $spec.fields) {
                        $key = $index.CreateField([string]$Case.fields[[int]$component[0]][0])
                        if ([bool]$component[1]) { $key.Attributes = 1 }
                        $index.Fields.Append($key); Release $key; $key = $null
                    }
                    $table.Indexes.Append($index); Release $index; $index = $null
                }
            }
            $db.TableDefs.Append($table); Release $table; $table = $null
            $rs = $db.OpenRecordset($name, 2)
            if ($name -eq 'Notes') {
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 7; $rs.Fields.Item('Body').Value = [string]('n' * 4096); $rs.Update()
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 8; $rs.Fields.Item('Body').Value = [DBNull]::Value; $rs.Update()
            } else {
                foreach ($row in $Case.initial_rows) {
                    $rs.AddNew(); Set-Row $rs $Case $row; $rs.Update(); $rs.Bookmark = $rs.LastModified
                    if ([int]$rs.Fields.Item('Id').Value -ne [int]$row[0]) { throw 'Generated/control initial Id differs' }
                }
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $engine
    }
}
function Mutate([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 2)
        foreach ($operation in $Case.native) {
            $id = if ($operation.kind -eq 'insert') { $operation.row[0] } else { $operation.id }
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($operation.kind)/$id"
            if ($operation.kind -eq 'insert') { $rs.AddNew(); Set-Row $rs $Case $operation.row; $rs.Update() }
            else {
                $rs.FindFirst('[Id] = ' + [string][int]$operation.id); if ($rs.NoMatch) { throw 'Mutation Id absent' }
                switch ($operation.kind) {
                    'replace' { $rs.Edit(); Set-Row $rs $Case $operation.row; $rs.Update() }
                    'delete' { $rs.Delete() }
                    default { throw 'Unknown operation' }
                }
            }
            if ($operation.kind -ne 'delete') {
                $rs.Bookmark = $rs.LastModified
                if ([int]$rs.Fields.Item('Id').Value -ne [int]$operation.row[0]) { throw 'Native generated/mutated Id differs' }
            }
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ before = $before; after = (Identity $Path); operations = $Case.native }
}
function Capture([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = @{ version = [string]$db.Version; tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object);
            queries = @($db.QueryDefs | ForEach-Object { [string]$_.Name }); relations = @($db.Relations | ForEach-Object { [string]$_.Name }); user_tables = @(); index_reads = @{} }
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
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-Rows $rs $name $Case
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item; Release $table; $table = $null
        }
        $rs = $db.OpenRecordset('Items', 1)
        foreach ($index in $Case.indexes) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/index/$($index.name)"
            $rs.Index = [string]$index.name
            if (-not ($rs.BOF -and $rs.EOF)) { $rs.MoveFirst() }
            $read = @{ traversal = (Read-Rows $rs 'Items' $Case); seek = @() }
            foreach ($query in $index.queries) {
                $script:endpoint = "$([IO.Path]::GetFileName($Path))/index/$($index.name)/seek/$($query -join '/')"
                $firstColumn = [int]$index.fields[0][0]
                $first = [int]$query[0]
                if ($index.fields.Count -eq 1) { $rs.Seek('=', $first) }
                else {
                    $secondColumn = [int]$index.fields[1][0]
                    $second = [int]$query[1]
                    $rs.Seek('=', $first, $second)
                }
                $row = if ($rs.NoMatch) { $null } else { Read-Row $rs 'Items' $Case }
                $read.seek += ,@{ query = $query; row = $row }
            }
            $snapshot.index_reads[[string]$index.name] = $read
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'multiple-long-value-creation.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_multiple_long_value_creation_result'; source_revision = $manifest.source_revision;
    manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/multiple_long_value_creation.ps1'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
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
        $outcome = @{ name = [string]$case.name; status = 'running'; original = @{}; native = @{}; mutations = @{}; error = $null }; $result.cases += ,$outcome
        try {
            $control = Join-Path $env:JET3_WORK "$($case.name)-original-control.mdb"; New-Control $control $case
            foreach ($role in @('candidate', 'control')) {
                $path = Join-Path $env:JET3_WORK "$($case.name)-original-$role.mdb"
                if ($role -eq 'candidate') { Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($case.name).mdb") -Destination $path }
                $outcome.original[$role] = Capture $path $case
                if ($outcome.original[$role].status -ne 'pass') { throw 'Original capture failed' }
                $next = Join-Path $env:JET3_WORK "$($case.name)-native-$role.mdb"
                Copy-Item -LiteralPath $path -Destination $next
                $outcome.mutations[$role] = Mutate $next $case
                $outcome.native[$role] = Capture $next $case
                if ($outcome.native[$role].status -ne 'pass') { throw 'Native continuation capture failed' }
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
