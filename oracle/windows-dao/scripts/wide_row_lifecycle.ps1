param([string]$CaseName = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$helper = Join-Path $env:JET3_WORK 'field_update.ps1'
$scalarHelper = Join-Path $env:JET3_WORK 'numeric_index_mutation.ps1'
foreach ($source in @($helper, $scalarHelper)) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Shared helper syntax' }
    $names = if ($source -eq $helper) { @('Identity', 'Release', 'Write-Json') } else {
        @('Failure', 'Binary-Bytes', 'Variant', 'Set-Cell', 'Set-Row', 'Read-Row', 'Read-Names', 'Read-Fields', 'Read-Indexes', 'Read-Rows', 'New-Control', 'Mutate')
    }
    foreach ($name in $names) {
        $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $false))
        if ($found.Count -ne 1) { throw "Missing shared helper: $name" }
        $definition = $found[0].Extent.Text
        if ($name -eq 'New-Control') {
            $definition = $definition.Replace('$fields.Append($field);', 'if ($name -eq "Items" -and [string]$spec[0] -in $Case.fixed_fields) { $field.Attributes = 1 }; $fields.Append($field);')
        }
        Invoke-Expression $definition
    }
}
function Variant([int]$Type, $Value) {
    if ($null -eq $Value) { return [DBNull]::Value }
    switch ($Type) {
        4 { return [int]$Value }
        { $_ -in @(9, 11) } { return ,([byte[]](Binary-Bytes ([string]$Value))) }
        { $_ -in @(10, 12) } { return [Text.Encoding]::GetEncoding(1252).GetString((Binary-Bytes ([string]$Value))) }
        default { throw 'Unexpected wide-row field type' }
    }
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
                    4 { $values[$i] = [int]$value }
                    { $_ -in @(9, 11) } { $values[$i] = [BitConverter]::ToString([byte[]]$value).Replace('-', '').ToLowerInvariant() }
                    { $_ -in @(10, 12) } {
                        $values[$i] = if ($Name -eq 'Notes') { [string]$value } else {
                            [BitConverter]::ToString([Text.Encoding]::GetEncoding(1252).GetBytes([string]$value)).Replace('-', '').ToLowerInvariant()
                        }
                    }
                    default { throw 'Unexpected wide-row field type' }
                }
            } finally { Release $field; $field = $null }
        }
    } finally { Release $field; Release $fields }
    return ,$values
}
function Set-Cell($Recordset, $Case, [int]$Column, $Value) {
    $spec = $Case.fields[$Column]; $fields = $Recordset.Fields; $field = $null
    try {
        $field = $fields.Item([string]$spec[0])
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        $converted = Variant ([int]$spec[1]) $Value
        if ([int]$spec[1] -eq 5 -and $null -ne $Value) {
            $converted = [decimal]([decimal]$Value / [decimal]10000)
        }
        $arguments = [object[]]::new(1); $arguments[0] = $converted
        [void]$field.GetType().InvokeMember('Value', [Reflection.BindingFlags]::SetProperty, $null, $field, $arguments)
    } finally { Release $field; Release $fields }
}
function Seek-Composite($Recordset, $Case, $Index, $Query) {
    if ($Query.Count -ne $Index.fields.Count) { throw 'Incomplete composite Seek arguments' }
    $values = [object[]]::new($Query.Count)
    for ($i = 0; $i -lt $values.Length; $i++) {
        $column = [int]$Index.fields[$i][0]
        $values[$i] = Variant ([int]$Case.fields[$column][1]) $Query[$i]
    }
    switch ($values.Length) {
        1 { $Recordset.Seek('=', $values[0]) }
        2 { $Recordset.Seek('=', $values[0], $values[1]) }
        3 { $Recordset.Seek('=', $values[0], $values[1], $values[2]) }
        4 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3]) }
        5 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4]) }
        6 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4], $values[5]) }
        7 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4], $values[5], $values[6]) }
        8 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4], $values[5], $values[6], $values[7]) }
        9 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4], $values[5], $values[6], $values[7], $values[8]) }
        10 { $Recordset.Seek('=', $values[0], $values[1], $values[2], $values[3], $values[4], $values[5], $values[6], $values[7], $values[8], $values[9]) }
        default { throw 'Unsupported composite Seek width' }
    }
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
                Seek-Composite $rs $Case $index $query
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
$manifestPath = Join-Path $env:JET3_WORK 'wide-row-lifecycle.json'
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
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/wide_row_lifecycle.ps1'), @($scalarHelper, 'oracle/windows-dao/scripts/numeric_index_mutation.ps1'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
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
