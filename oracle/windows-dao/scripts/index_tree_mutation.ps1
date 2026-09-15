Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) }
}
function Identity([string]$Path) {
    return @{ size = (Get-Item -LiteralPath $Path).Length; sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Write-Json([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 50 -Compress) + "`n", [Text.UTF8Encoding]::new($false))
}
function Failure($Record) { return @{ endpoint = $script:endpoint; message = $Record.Exception.Message; hresult = $Record.Exception.HResult; stack = $Record.ScriptStackTrace } }
function From-Hex([string]$Value) {
    $bytes = [byte[]]::new($Value.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($Value.Substring($i * 2, 2), 16) }
    return ,$bytes
}
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
    $bytes = [byte[]]$Rs.Fields.Item('Bytes').Value
    return ,([object[]]@($id, [string]$Rs.Fields.Item('Text').Value, ([BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant())))
}
function Set-Row($Rs, $Value, [bool]$Deep) {
    $Rs.Fields.Item('Id').Value = [int]$Value[0]
    if ($Deep) { $Rs.Fields.Item('Value').Value = [int]$Value[1] }
    else { $Rs.Fields.Item('Text').Value = [string]$Value[1]; $Rs.Fields.Item('Bytes').Value = From-Hex ([string]$Value[2]) }
}
function Save-Rows($Rs, [string]$Name, [bool]$Deep, [string]$File) {
    $rows = New-Object Collections.ArrayList
    while (-not $Rs.EOF) { [void]$rows.Add((Row $Rs $Name $Deep)); $Rs.MoveNext() }
    $path = Join-Path $env:JET3_WORK $File
    Write-Json $path ([object[]]$rows.ToArray())
    $saved = Identity $path; $saved.file = $File
    return $saved
}
function New-Control([string]$Path, $Case) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $script:endpoint = "$($Case.name)/create_database"
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
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
                    $rs.AddNew(); Set-Row $rs (Base-Row $id $Case.deep) $Case.deep; $rs.Update()
                }
            } else {
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 7; $rs.Fields.Item('Body').Value = [string]('n' * 4096); $rs.Update()
                $rs.AddNew(); $rs.Fields.Item('Id').Value = 8; $rs.Fields.Item('Body').Value = [DBNull]::Value; $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $engine
    }
}
function Mutate([string]$Path, $Case, $Operation) {
    $engine = $db = $rs = $null
    $before = Identity $Path
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($Operation.kind)"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        if ($Operation.kind -eq 'insert') { $rs.AddNew(); Set-Row $rs $Operation.row $Case.deep; $rs.Update() }
        else {
            $rs.Seek('=', [int]$Operation.id); if ($rs.NoMatch) { throw 'Mutation key absent' }
            switch ($Operation.kind) {
                'delete' { $rs.Delete() }
                'key' { $rs.Edit(); $rs.Fields.Item('Id').Value = [int]$Operation.next_id; $rs.Update() }
                'row' { $rs.Edit(); Set-Row $rs $Operation.row $Case.deep; $rs.Update() }
                default { throw 'Unknown operation' }
            }
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ request = $Operation; status = 'pass'; before = $before; after = (Identity $Path) }
}
function Capture([string]$Path, $Case) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    $stem = [IO.Path]::GetFileNameWithoutExtension($Path)
    try {
        $script:endpoint = "$stem/capture/open"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = @{ version = [string]$db.Version; tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object);
            queries = @($db.QueryDefs | ForEach-Object { [string]$_.Name }); relations = @($db.Relations | ForEach-Object { [string]$_.Name }); user_tables = @() }
        foreach ($name in @('Items', 'Notes')) {
            $script:endpoint = "$stem/capture/$name"
            $table = $db.TableDefs.Item($name)
            $item = @{ name = $name; attributes = [int]$table.Attributes }
            $item.fields = @($table.Fields | ForEach-Object {
                @{ name = [string]$_.Name; type = [int]$_.Type; size = [int]$_.Size; attributes = [int]$_.Attributes; required = [bool]$_.Required; allow_zero_length = [bool]$_.AllowZeroLength; default_value = [string]$_.DefaultValue }
            })
            $item.indexes = @($table.Indexes | ForEach-Object {
                @{ name = [string]$_.Name; primary = [bool]$_.Primary; unique = [bool]$_.Unique; required = [bool]$_.Required; ignore_nulls = [bool]$_.IgnoreNulls; foreign = [bool]$_.Foreign;
                   fields = @($_.Fields | ForEach-Object { @{ name = [string]$_.Name; attributes = [int]$_.Attributes } }) }
            })
            $rs = $db.OpenRecordset($name, 4)
            $item.rows = Save-Rows $rs $name $Case.deep "$stem-$name.rows.json"
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item; Release $table; $table = $null
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
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'index-tree-mutation.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_index_tree_mutation_result'; source_revision = $manifest.source_revision;
    manifest_sha256 = (Identity $manifestPath).sha256; round = [string]$manifest.round; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    if ((Identity $PSCommandPath).sha256 -cne $manifest.producer.sha256) { throw 'Producer identity differs' }
    $engine = New-Object -ComObject DAO.DBEngine.36
    try {
        $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
        if ($dll.Count -ne 1) { throw 'Loaded DAO module absent or ambiguous' }
        $result.environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; provider_version = [string]$engine.Version;
            os = [Environment]::OSVersion.VersionString; powershell = [string]$PSVersionTable.PSVersion; clr = [Environment]::Version.ToString();
            culture = [Globalization.CultureInfo]::CurrentCulture.Name; timezone = [TimeZoneInfo]::Local.Id;
            dll = @{ path = $dll[0].FileName; version = $dll[0].FileVersionInfo.FileVersion; sha256 = (Identity $dll[0].FileName).sha256 } }
    } finally { Release $engine }
    if ($manifest.round -eq 'continuation') {
        foreach ($case in $manifest.cases) {
            $outcome = @{ name = [string]$case.name; status = 'running'; roles = @{}; operation = $null; error = $null }; $result.cases += ,$outcome
            try {
                foreach ($role in @('candidate', 'control')) {
                    $inputFile = if ($role -eq 'candidate') { [string]$case.candidate_file } else { [string]$case.source_file }
                    $source = Join-Path $env:JET3_WORK $inputFile
                    $actual = Identity $source; $wanted = $manifest.files.$inputFile
                    if ($actual.sha256 -cne $wanted.sha256 -or $actual.size -ne $wanted.size) { throw 'Continuation source identity differs' }
                    $path = Join-Path $env:JET3_WORK "$($case.name)-continued-$role.mdb"
                    Copy-Item -LiteralPath $source -Destination $path
                    if ($role -eq 'control') { $outcome.operation = Mutate $path $case $case.operation }
                    $outcome.roles[$role] = Capture $path $case
                    if ($outcome.roles[$role].status -ne 'pass') { throw 'Continuation capture failed' }
                }
                $outcome.status = 'pass'
            } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
        }
    } else {
    foreach ($case in $manifest.cases) {
        $outcome = @{ name = [string]$case.name; status = 'running'; stages = @(); native = @{}; error = $null }; $result.cases += ,$outcome
        try {
            $control = Join-Path $env:JET3_WORK "$($case.name)-control-working.mdb"
            New-Control $control $case
            foreach ($stage in $case.stages) {
                $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{} }; $outcome.stages += ,$checkpoint
                foreach ($operation in $stage.operations) { $null = Mutate $control $case $operation }
                foreach ($role in @('candidate', 'control')) {
                    $file = "$($case.name)-$($stage.name)-$role.mdb"; $path = Join-Path $env:JET3_WORK $file
                    $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($case.name)-$($stage.name).mdb" } else { $control }
                    if ($role -eq 'candidate') {
                        $actual = Identity $source; $wanted = $manifest.files.([IO.Path]::GetFileName($source))
                        if ($actual.sha256 -cne $wanted.sha256 -or $actual.size -ne $wanted.size) { throw 'Candidate identity differs' }
                    }
                    Copy-Item -LiteralPath $source -Destination $path
                    $record = @{ file = $file; image = (Identity $path); capture = $null }; $checkpoint.roles[$role] = $record
                    if ($stage.capture) {
                        $record.capture = Capture $path $case
                        if ($record.capture.status -ne 'pass') { throw 'Checkpoint capture failed' }
                    }
                }
            }
            foreach ($role in @('candidate', 'control')) {
                $file = "$($case.name)-native-$role.mdb"; $path = Join-Path $env:JET3_WORK $file
                Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($case.name)-regrown-$role.mdb") -Destination $path
                $native = @{ operations = @(); capture = $null }; $outcome.native[$role] = $native
                $step = 0
                foreach ($operation in $case.native) {
                    $receipt = Mutate $path $case $operation; $step++
                    $receipt.file = "$($case.name)-native-$role-step$step.mdb"
                    Copy-Item -LiteralPath $path -Destination (Join-Path $env:JET3_WORK $receipt.file)
                    $native.operations += ,$receipt
                }
                $native.capture = Capture $path $case
                if ($native.capture.status -ne 'pass') { throw 'Native successor capture failed' }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json') -and $_.Name -ne 'index-tree-mutation.json' }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json (Join-Path $env:JET3_OUTBOX 'result.json') $result
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
