param([string]$CaseName = '')
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
function Binary-Bytes([string]$Hex) {
    $bytes = New-Object byte[] ($Hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16) }
    return ,$bytes
}
function Variant([int]$Type, $Value) {
    if ($null -eq $Value) { return [DBNull]::Value }
    switch ($Type) {
        1 { return [bool]$Value }; 2 { return [byte]$Value }; 3 { return [int16]$Value }; 4 { return [int]$Value }
        5 { return [double]([decimal]$Value / [decimal]10000) }; 6 { return [single]$Value }; 7 { return [double]$Value }
        8 { return [datetime]::FromOADate([double]$Value) }; 9 { return ,([byte[]](Binary-Bytes ([string]$Value))) }
        10 { return [Text.Encoding]::GetEncoding(1252).GetString((Binary-Bytes ([string]$Value))) }
        15 { return '{' + ([guid]([string]$Value)).ToString() + '}' }
        default { throw 'Unknown scalar type' }
    }
}
function Set-Cell($Recordset, $Case, [int]$Column, $Value) {
    $spec = $Case.fields[$Column]; $fields = $Recordset.Fields; $field = $null
    try {
        $field = $fields.Item([string]$spec[0])
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        if ($null -eq $Value) { $field.Value = [DBNull]::Value; return }
        switch ([int]$spec[1]) {
            1 { $field.Value = [bool]$Value }; 2 { $field.Value = [byte]$Value }
            3 { $field.Value = [int16]$Value }; 4 { $field.Value = [int]$Value }
            5 { $field.Value = [decimal]([decimal]$Value / [decimal]10000) }
            6 { $field.Value = [single]$Value }; 7 { $field.Value = [double]$Value }
            8 { $field.Value = [datetime]::FromOADate([double]$Value) }; 9 { $field.Value = [byte[]](Binary-Bytes ([string]$Value)) }
            10 { $field.Value = [Text.Encoding]::GetEncoding(1252).GetString((Binary-Bytes ([string]$Value))) }
            15 { $field.Value = '{' + ([guid]([string]$Value)).ToString() + '}' }
            default { throw 'Unknown scalar type' }
        }
    } finally { Release $field; Release $fields }
}
function Set-Row($Recordset, $Case, $Values) {
    for ($i = 0; $i -lt $Case.fields.Count; $i++) { Set-Cell $Recordset $Case $i $Values[$i] }
}
function Read-Row($Recordset, [string]$Name, $Case) {
    $fields = $Recordset.Fields; $field = $null
    $specs = if ($Name -eq 'Notes') { @(@('Id', 4), @('Body', 12)) } else { $Case.fields }
    $values = [object[]]::new($specs.Count)
    try {
        for ($i = 0; $i -lt $values.Length; $i++) {
            $field = $fields.Item([string]$specs[$i][0])
            try {
                $value = $field.Value
                if ($value -is [DBNull]) { $values[$i] = $null; continue }
                switch ([int]$specs[$i][1]) {
                    5 { $values[$i] = [long]([decimal]$value * [decimal]10000) }
                    8 { $values[$i] = ([datetime]$value).ToOADate() }
                    9 { $values[$i] = [BitConverter]::ToString([byte[]]$value).Replace('-', '').ToLowerInvariant() }
                    10 { $values[$i] = [BitConverter]::ToString([Text.Encoding]::GetEncoding(1252).GetBytes([string]$value)).Replace('-', '').ToLowerInvariant() }
                    15 {
                        if ([string]$value -notmatch '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}') { throw 'Unrecognized GUID value' }
                        $values[$i] = ([guid]$Matches[0]).ToString('N')
                    }
                    default { $values[$i] = $value }
                }
            } finally { Release $field; $field = $null }
        }
    } finally { Release $field; Release $fields }
    return ,$values
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
                         required = [bool]$field.Required; allow_zero_length = [bool]$field.AllowZeroLength; default_value = [string]$field.DefaultValue }
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

function Read-Rows($Recordset, [string]$Name, $Case) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Name $Case)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function New-Control([string]$Path, $Case) {
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $field = $indexes = $index = $keys = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspaces = $engine.Workspaces; $workspace = $workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32); $tables = $db.TableDefs
        foreach ($name in @('Items', 'Notes')) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/create/$name"
            $table = $db.CreateTableDef($name); $fields = $table.Fields; $indexes = $table.Indexes
            $specs = if ($name -eq 'Items') { $Case.fields } else { @(@('Id', 4, 4), @('Body', 12, 0)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                $fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                foreach ($spec in $Case.indexes) {
                    $index = $table.CreateIndex([string]$spec.name); $keys = $index.Fields
                    $index.Primary = [bool]$spec.primary; $index.Unique = [bool]$spec.unique
                    $index.Required = [bool]$spec.required; $index.IgnoreNulls = [bool]$spec.ignore
                    foreach ($component in $spec.fields) {
                        $column = [int]$component[0]
                        $key = $index.CreateField([string]$Case.fields[$column][0])
                        if ([bool]$component[1]) { $key.Attributes = 1 }
                        $keys.Append($key); Release $key; $key = $null
                    }
                    $indexes.Append($index); Release $keys; $keys = $null; Release $index; $index = $null
                }
            }
            $tables.Append($table); Release $indexes; $indexes = $null; Release $fields; $fields = $null; Release $table; $table = $null
        }
        $rs = $db.OpenRecordset('Notes', 2); $fields = $rs.Fields
        foreach ($id in @(7, 8)) {
            $rs.AddNew()
            $field = $fields.Item('Id'); $field.Value = [int]$id; Release $field; $field = $null
            $field = $fields.Item('Body')
            if ($id -eq 7) { $field.Value = [string]('n' * 4096) } else { $field.Value = [DBNull]::Value }
            Release $field; $field = $null; $rs.Update()
        }
        Release $fields; $fields = $null
        $rs.Close(); Release $rs; $rs = $db.OpenRecordset('Items', 2)
        foreach ($row in $Case.initial_rows) { $rs.AddNew(); Set-Row $rs $Case $row; $rs.Update() }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $keys; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $workspaces; Release $engine
    }
}
function Mutate([string]$Path, $Case, $Operations) {
    $before = Identity $Path; $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        foreach ($operation in $Operations) {
            $id = if ($operation.kind -eq 'insert') { $operation.row[0] } else { $operation.id }
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($operation.kind)/$id"
            if ($operation.kind -eq 'insert') { $rs.AddNew(); Set-Row $rs $Case $operation.row; $rs.Update() }
            else {
                $rs.Seek('=', [int]$operation.id); if ($rs.NoMatch) { throw 'Mutation key absent' }
                switch ($operation.kind) {
                    'replace' { $rs.Edit(); Set-Row $rs $Case $operation.row; $rs.Update() }
                    'field' { $rs.Edit(); Set-Cell $rs $Case ([int]$operation.column) $operation.value; $rs.Update() }
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
function Capture([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = @{ version = [string]$db.Version; tables = @(Read-Names $db.TableDefs | Sort-Object);
            queries = @(Read-Names $db.QueryDefs); relations = @(Read-Names $db.Relations); user_tables = @(); index_reads = @{} }
        foreach ($name in @('Items', 'Notes')) {
            $tables = $db.TableDefs
            try { $table = $tables.Item($name) } finally { Release $tables }
            $item = @{ name = $name; attributes = [int]$table.Attributes }
            $item.fields = @(Read-Fields $table)
            $item.indexes = @(Read-Indexes $table)
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
                $first = Variant ([int]$Case.fields[$firstColumn][1]) $query[0]
                if ($index.fields.Count -eq 1) { $rs.Seek('=', $first) }
                else {
                    $secondColumn = [int]$index.fields[1][0]
                    $second = Variant ([int]$Case.fields[$secondColumn][1]) $query[1]
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
$manifestPath = Join-Path $env:JET3_WORK 'numeric-index-mutation.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $CaseName) {
    $workers = @(); $failed = $false
    $shell = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($case in $manifest.cases) {
        $name = [string]$case.name
        & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -CaseName $name
        $code = $LASTEXITCODE; $file = "$name-result.json"; $path = Join-Path $env:JET3_OUTBOX $file
        $pin = if (Test-Path -LiteralPath $path) { Identity $path } else { $null }
        $workers += @{ name = $name; exit_code = $code; file = $file; image = $pin }
        if ($code -ne 0 -or $null -eq $pin) { $failed = $true }
    }
    Write-Json @{ document_type = 'dao_numeric_index_workers'; source_revision = $manifest.source_revision;
        manifest_sha256 = (Identity $manifestPath).sha256; round = [string]$manifest.round; workers = $workers } (Join-Path $env:JET3_OUTBOX 'numeric-index-workers.json')
    if ($failed) { exit 1 }; exit 0
}
if (@($manifest.cases | Where-Object { $_.name -ceq $CaseName }).Count -ne 1) { throw 'Unknown worker case' }
$result = @{ document_type = 'dao_numeric_index_mutation_result'; source_revision = $manifest.source_revision; round = [string]$manifest.round;
    manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/numeric_index_mutation.ps1'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
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
        if ($case.name -cne $CaseName) { continue }
        $outcome = @{ name = [string]$case.name; status = 'running'; created = $null; stages = @(); native = @{}; roles = @{}; operation = $null; error = $null }; $result.cases += ,$outcome
        try {
            if ($manifest.round -eq 'continuation') {
                foreach ($role in @('candidate', 'control')) {
                    $source = if ($role -eq 'candidate') { $case.candidate_file } else { $case.source_file }
                    $path = Join-Path $env:JET3_WORK "$($case.name)-continued-$role.mdb"
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $source) -Destination $path
                    if ($role -eq 'control') { $outcome.operation = Mutate $path $case $case.operations }
                    $outcome.roles[$role] = Capture $path $case
                    if ($outcome.roles[$role].status -ne 'pass') { throw 'Continuation capture failed' }
                }
            } else {
                $control = Join-Path $env:JET3_WORK "$($case.name)-control-working.mdb"; New-Control $control $case
                $createdFile = "$($case.name)-control-created.mdb"; Copy-Item -LiteralPath $control -Destination (Join-Path $env:JET3_WORK $createdFile)
                $outcome.created = @{ file = $createdFile; image = (Identity $control) }
                foreach ($stage in $case.stages) {
                    $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{}; mutation = $null }; $outcome.stages += ,$checkpoint
                    $checkpoint.mutation = Mutate $control $case $stage.operations
                    foreach ($role in @('candidate', 'control')) {
                        $path = Join-Path $env:JET3_WORK "$($case.name)-$($stage.name)-$role.mdb"
                        $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($case.name)-$($stage.name).mdb" } else { $control }
                        Copy-Item -LiteralPath $source -Destination $path
                        $checkpoint.roles[$role] = Capture $path $case
                        if ($checkpoint.roles[$role].status -ne 'pass') { throw 'Checkpoint capture failed' }
                    }
                }
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($case.name)-native-$role.mdb"
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($case.name)-regrown-$role.mdb") -Destination $path
                    $native = @{ mutation = (Mutate $path $case $case.native); capture = (Capture $path $case) }; $outcome.native[$role] = $native
                    if ($native.capture.status -ne 'pass') { throw 'Native follow-up capture failed' }
                }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json') }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX "$CaseName-result.json")
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
