Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
function Load-Functions([string]$Path, [string[]]$Names) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Helper syntax' }
    foreach ($name in $Names) {
        $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $false))
        if ($found.Count -ne 1) { throw 'Missing helper function' }
        . ([scriptblock]::Create($found[0].Extent.Text.Replace("function $name", "function script:$name")))
    }
}
Load-Functions (Join-Path $env:JET3_WORK 'creation_tables.ps1') @('Identity', 'Release', 'Failure', 'Read-Row', 'Read-Rows', 'New-Control', 'Capture')
Load-Functions (Join-Path $env:JET3_WORK 'field_update.ps1') @('Write-Json')
function System-Row($Recordset) {
    $values = [object[]]::new($Recordset.Fields.Count)
    for ($i = 0; $i -lt $values.Length; $i++) {
        $field = $Recordset.Fields.Item($i)
        try {
            $value = $field.Value
            $values[$i] = if ($value -is [DBNull]) { $null }
                elseif ([int]$field.Type -in @(9, 11)) { [BitConverter]::ToString([byte[]]$value).Replace('-', '').ToLowerInvariant() }
                elseif ([int]$field.Type -eq 8) { ([DateTime]$value).ToOADate() }
                elseif ([int]$field.Type -in @(10, 12)) { [string]$value }
                elseif ([int]$field.Type -eq 1) { [bool]$value }
                else { [int]$value }
        } finally { Release $field }
    }
    return ,$values
}
function System-Rows($Recordset) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((System-Row $Recordset)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}
function System-Capture([string]$Path) {
    $before = Identity $Path; $engine = $db = $table = $rs = $null; $tables = @(); $status = 'pass'; $detail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        foreach ($name in @('MSysObjects', 'MSysACEs')) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$name/rows"
            $table = $db.TableDefs.Item($name)
            $item = @{ name = $name; fields = @($table.Fields | ForEach-Object { @{ name = [string]$_.Name; type = [int]$_.Type; size = [int]$_.Size; attributes = [int]$_.Attributes } }); indexes = @() }
            $rs = $db.OpenRecordset($name, 4); $item.rows = System-Rows $rs
            $rs.Close(); Release $rs; $rs = $null
            foreach ($index in $table.Indexes) {
                $script:endpoint = "$([IO.Path]::GetFileName($Path))/$name/index/$($index.Name)"
                $rs = $db.OpenRecordset($name, 1); $rs.Index = [string]$index.Name
                if (-not $rs.EOF) { $rs.MoveFirst() }
                $item.indexes += ,@{ name = [string]$index.Name; primary = [bool]$index.Primary; unique = [bool]$index.Unique; required = [bool]$index.Required; ignore_nulls = [bool]$index.IgnoreNulls;
                    fields = @($index.Fields | ForEach-Object { @{ name = [string]$_.Name; attributes = [int]$_.Attributes } }); traversal = (System-Rows $rs) }
                $rs.Close(); Release $rs; $rs = $null; Release $index
            }
            $tables += ,$item; Release $table; $table = $null
        }
    } catch { $status = 'error'; $detail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ before = $before; after = (Identity $Path); status = $status; error = $detail; tables = $tables }
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'catalog-pages-native.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_catalog_pages_native_result'; source_revision = $manifest.source_revision; manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($name in @('catalog_pages_native.ps1', 'creation_tables.ps1', 'field_update.ps1')) {
        if ((Identity (Join-Path $env:JET3_WORK $name)).sha256 -cne $manifest.files.$name.sha256) { throw "Script identity: $name" }
    }
    $engine = New-Object -ComObject DAO.DBEngine.36
    try {
        $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
        if ($dll.Count -ne 1) { throw 'DAO module absent or ambiguous' }
        $result.environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; provider_version = [string]$engine.Version; os = [Environment]::OSVersion.VersionString;
            powershell = [string]$PSVersionTable.PSVersion; clr = [Environment]::Version.ToString(); culture = [Globalization.CultureInfo]::CurrentCulture.Name;
            dll = @{ path = $dll[0].FileName; version = $dll[0].FileVersionInfo.FileVersion; sha256 = (Identity $dll[0].FileName).sha256 } }
    } finally { Release $engine }
    foreach ($case in $manifest.cases) {
        foreach ($replica in 1..2) {
            $outcome = @{ name = [string]$case.name; replica = $replica; status = 'running'; user = $null; system = $null; error = $null }; $result.cases += ,$outcome
            try {
                $path = Join-Path $env:JET3_WORK "$($case.name)-r$replica.mdb"
                if ($manifest.PSObject.Properties.Name -contains 'parent') {
                    $source = @($manifest.native_sources | Where-Object { $_.name -eq $case.name -and $_.replica -eq $replica })
                    if ($source.Count -ne 1 -or (Identity $path).sha256 -cne $source[0].original.sha256) { throw 'Original native identity' }
                    $systemPath = Join-Path $env:JET3_WORK $source[0].readable_file
                    if ((Identity $systemPath).sha256 -cne $source[0].readable.sha256) { throw 'Readable clone identity' }
                } else { New-Control $path $case; $systemPath = $path }
                $outcome.user = Capture $path $case
                if ($outcome.user.status -ne 'pass') { throw 'User schema capture failed' }
                $outcome.system = System-Capture $systemPath
                $outcome.system.file = [IO.Path]::GetFileName($systemPath)
                if ($outcome.system.status -ne 'pass') { throw 'System row/index capture failed' }
                $outcome.status = 'pass'
            } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
        }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json') }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
