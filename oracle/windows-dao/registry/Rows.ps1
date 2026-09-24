# Shared helpers for the registry suite producers. Dot-source after Common.ps1; nothing runs here.
#
# Sections: producer plumbing (manifest, environment, retention, per-case workers), DAO metadata
# reads, scalar rows (numeric, capacity and wide-row suites) and long-value rows (Memo/OLE suites).

$script:endpoint = 'start'
$script:X86Shell = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'

# --- Producer plumbing ---------------------------------------------------------------

function Failure($Record) {
    @{ endpoint = $script:endpoint; message = $Record.Exception.Message; hresult = $Record.Exception.HResult; stack = $Record.ScriptStackTrace }
}

function Read-Manifest([string]$Name) {
    $script:manifestPath = Join-Path $env:JET3_WORK $Name
    Read-Json $script:manifestPath
}

function New-Result([string]$Type, $Manifest) {
    $round = if (Has $Manifest 'round') { [string]$Manifest.round } else { '' }
    @{ document_type = $Type; source_revision = [string]$Manifest.source_revision; round = $round
       manifest_sha256 = (Identity $script:manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
}

# Every staged input listed in the manifest must be the prepared file.
function Test-Inputs($Manifest) {
    foreach ($property in $Manifest.files.PSObject.Properties) {
        $actual = Identity (Join-Path $env:JET3_WORK $property.Name)
        if ($actual.sha256 -cne $property.Value.sha256 -or $actual.size -ne $property.Value.size) { throw "Input identity differs: $($property.Name)" }
    }
}

function Get-Environment {
    $engine = New-Object -ComObject DAO.DBEngine.36
    try { Environment-Record $engine } finally { Release $engine }
}

function Save-Outputs($Result, [string]$File, [string[]]$Extensions = @('.mdb', '.json')) {
    foreach ($item in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in $Extensions }) {
        try { Copy-Item -LiteralPath $item.FullName -Destination $env:JET3_OUTBOX }
        catch { $Result.retention_failures += @{ file = $item.Name; message = $_.Exception.Message } }
    }
    Write-Json $Result (Join-Path $env:JET3_OUTBOX $File)
}

function Test-Complete($Result) {
    $null -eq $Result.error -and -not $Result.retention_failures.Count -and -not @($Result.cases | Where-Object { $_.status -ne 'pass' }).Count
}

# Runs this script once per case in a fresh x86 process; each writes <case>-result.json.
function Invoke-Workers($Manifest, [string]$Script) {
    $workers = @(); $failed = $false
    foreach ($case in $Manifest.cases) {
        $name = [string]$case.name
        & $script:X86Shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script -CaseName $name
        $code = $LASTEXITCODE; $file = "$name-result.json"; $path = Join-Path $env:JET3_OUTBOX $file
        $image = if (Test-Path -LiteralPath $path) { Identity $path } else { $null }
        $workers += @{ name = $name; exit_code = $code; file = $file; image = $image }
        if ($code -ne 0 -or $null -eq $image) { $failed = $true }
    }
    $round = if (Has $Manifest 'round') { [string]$Manifest.round } else { '' }
    Write-Json @{ document_type = 'dao_workers'; source_revision = [string]$Manifest.source_revision; round = $round
        manifest_sha256 = (Identity $script:manifestPath).sha256; workers = $workers } (Join-Path $env:JET3_OUTBOX 'workers.json')
    if ($failed) { exit 1 }
    exit 0
}

function Select-Case($Manifest, [string]$CaseName) {
    $selected = @($Manifest.cases | Where-Object { $_.name -ceq $CaseName })
    if ($selected.Count -ne 1) { throw "Unknown worker case $CaseName" }
    $selected[0]
}

# --- DAO metadata --------------------------------------------------------------------

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
            $items += @{ name = [string]$field.Name; type = [int]$field.Type; size = [int]$field.Size; attributes = [int]$field.Attributes
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
                $keys += @{ name = [string]$field.Name; attributes = [int]$field.Attributes }
                Release $field; $field = $null
            }
            Release $fields; $fields = $null
            $items += @{ name = [string]$index.Name; primary = [bool]$index.Primary; unique = [bool]$index.Unique; required = [bool]$index.Required
                         foreign = [bool]$index.Foreign; ignore_nulls = [bool]$index.IgnoreNulls; fields = $keys }
            Release $index; $index = $null
        }
    } finally { Release $field; Release $fields; Release $index; Release $indexes }
    return $items
}

# Version, table/query/relation names and the metadata of one user table.
function Read-Inventory($Db) {
    @{ version = [string]$Db.Version; tables = @(Read-Names $Db.TableDefs | Sort-Object)
       queries = @(Read-Names $Db.QueryDefs); relations = @(Read-Names $Db.Relations) }
}

function Read-Table($Db, [string]$Name) {
    $tables = $Db.TableDefs; $table = $null
    try {
        $table = $tables.Item($Name)
        @{ name = $Name; attributes = [int]$table.Attributes; fields = @(Read-Fields $table); indexes = @(Read-Indexes $table) }
    } finally { Release $table; Release $tables }
}

function From-Hex([string]$Hex) {
    $bytes = New-Object byte[] ($Hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16) }
    return ,$bytes
}

function Close-Com($Object, [bool]$Close) {
    if ($null -eq $Object) { return }
    if ($Close) { try { $Object.Close() } catch {} }
    Release $Object
}

# --- Scalar rows: cases {name, fields [[name, type, size]], indexes, initial_rows, ...} ---

# Seek arguments and stored values; Currency is scaled by 10^4, Text and Binary are hex.
function Variant([int]$Type, $Value) {
    if ($null -eq $Value) { return [DBNull]::Value }
    switch ($Type) {
        1 { return [bool]$Value }
        2 { return [byte]$Value }
        3 { return [int16]$Value }
        4 { return [int]$Value }
        5 { return [double]([decimal]$Value / [decimal]10000) }
        6 { return [single]$Value }
        7 { return [double]$Value }
        8 { return [datetime]::FromOADate([double]$Value) }
        { $_ -in @(9, 11) } { return ,([byte[]](From-Hex ([string]$Value))) }
        { $_ -in @(10, 12) } { return [Text.Encoding]::GetEncoding(1252).GetString((From-Hex ([string]$Value))) }
        15 { return '{' + ([guid]([string]$Value)).ToString() + '}' }
        default { throw 'Unknown scalar type' }
    }
}

function Set-Cell($Recordset, $Case, [int]$Column, $Value) {
    $spec = $Case.fields[$Column]; $fields = $Recordset.Fields; $field = $null
    try {
        $field = $fields.Item([string]$spec[0])
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        $converted = Variant ([int]$spec[1]) $Value
        if ([int]$spec[1] -eq 5 -and $null -ne $Value) { $converted = [decimal]([decimal]$Value / [decimal]10000) }
        Set-Property $field 'Value' $converted
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
                $type = [int]$specs[$i][1]
                if ($Name -eq 'Notes' -and $type -eq 12) { $values[$i] = [string]$value; continue }
                switch ($type) {
                    5 { $values[$i] = [long]([decimal]$value * [decimal]10000) }
                    8 { $values[$i] = ([datetime]$value).ToOADate() }
                    { $_ -in @(9, 11) } { $values[$i] = Hex ([byte[]]$value) }
                    { $_ -in @(10, 12) } { $values[$i] = Hex ([Text.Encoding]::GetEncoding(1252).GetBytes([string]$value)) }
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

function Read-Rows($Recordset, [string]$Name, $Case) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-Row $Recordset $Name $Case)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

function Seek-Composite($Recordset, $Case, $Index, $Query) {
    if ($Query.Count -ne $Index.fields.Count) { throw 'Incomplete composite Seek arguments' }
    $values = [object[]]::new($Query.Count)
    for ($i = 0; $i -lt $values.Length; $i++) { $values[$i] = Variant ([int]$Case.fields[[int]$Index.fields[$i][0]][1]) $Query[$i] }
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

# Items per case plus the Notes sentinel (Memo 'n'*4096 and a null); `fixed_fields` are fixed Text.
function New-Control([string]$Path, $Case) {
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $field = $indexes = $index = $keys = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspaces = $engine.Workspaces; $workspace = $workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, (Locale-String 'general'), 32); $tables = $db.TableDefs
        $fixed = if (Has $Case 'fixed_fields') { @($Case.fixed_fields) } else { @() }
        foreach ($name in @('Items', 'Notes')) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/create/$name"
            $table = $db.CreateTableDef($name); $fields = $table.Fields; $indexes = $table.Indexes
            $specs = if ($name -eq 'Items') { $Case.fields } else { @(@('Id', 4, 4), @('Body', 12, 0)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                if ($name -eq 'Items' -and [string]$spec[0] -in $fixed) { $field.Attributes = 1 }
                $fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                foreach ($spec in $Case.indexes) {
                    $index = $table.CreateIndex([string]$spec.name); $keys = $index.Fields
                    $index.Primary = [bool]$spec.primary; $index.Unique = [bool]$spec.unique
                    $index.Required = [bool]$spec.required; $index.IgnoreNulls = [bool]$spec.ignore
                    foreach ($component in $spec.fields) {
                        $key = $index.CreateField([string]$Case.fields[[int]$component[0]][0])
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
        Close-Com $rs $true
        Release $key; Release $keys; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        Close-Com $db $true; Release $workspace; Release $workspaces; Release $engine
    }
}

# Operations: insert {row}, replace {id, row}, field {id, column, value}, delete {id}.
function Mutate([string]$Path, $Case, $Operations) {
    $before = Identity $Path; $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 1); $rs.Index = 'ById'
        foreach ($operation in $Operations) {
            $id = if ($operation.kind -eq 'insert') { $operation.row[0] } else { $operation.id }
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($operation.kind)/$id"
            if ($operation.kind -eq 'insert') { $rs.AddNew(); Set-Row $rs $Case $operation.row; $rs.Update(); continue }
            $rs.Seek('=', [int]$operation.id)
            if ($rs.NoMatch) { throw 'Mutation key absent' }
            switch ($operation.kind) {
                'replace' { $rs.Edit(); Set-Row $rs $Case $operation.row; $rs.Update() }
                'field' { $rs.Edit(); Set-Cell $rs $Case ([int]$operation.column) $operation.value; $rs.Update() }
                'delete' { $rs.Delete() }
                default { throw 'Unknown operation' }
            }
        }
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ before = $before; after = (Identity $Path); count = @($Operations).Count }
}

# Read-only snapshot: inventory, both tables, and each index's traversal and Seek queries.
function Capture([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $snapshot.user_tables = @(); $snapshot.index_reads = @{}
        foreach ($name in @('Items', 'Notes')) {
            $item = Read-Table $db $name
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-Rows $rs $name $Case
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item
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
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

# One scalar case. Mutation round: build the control, then per stage mutate it and capture
# the Rust candidate and the control; finally apply the native operations to both outputs
# of stage $Final. Continuation round: capture the Rust continuation and the native source
# after the same operations.
function Invoke-ScalarCase($Manifest, $Case, [string]$Final) {
    $outcome = @{ name = [string]$Case.name; status = 'running'; created = $null; stages = @(); native = @{}; roles = @{}; operation = $null; error = $null }
    try {
        if ($Manifest.round -eq 'continuation') {
            foreach ($role in @('candidate', 'control')) {
                $source = if ($role -eq 'candidate') { $Case.candidate_file } else { $Case.source_file }
                $path = Join-Path $env:JET3_WORK "$($Case.name)-continued-$role.mdb"
                Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $source) -Destination $path
                if ($role -eq 'control') { $outcome.operation = Mutate $path $Case $Case.operations }
                $outcome.roles[$role] = Capture $path $Case
                if ($outcome.roles[$role].status -ne 'pass') { throw 'Continuation capture failed' }
            }
        } else {
            $control = Join-Path $env:JET3_WORK "$($Case.name)-control-working.mdb"; New-Control $control $Case
            $createdFile = "$($Case.name)-control-created.mdb"; Copy-Item -LiteralPath $control -Destination (Join-Path $env:JET3_WORK $createdFile)
            $outcome.created = @{ file = $createdFile; image = (Identity $control) }
            foreach ($stage in $Case.stages) {
                $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{}; mutation = $null }; $outcome.stages += ,$checkpoint
                $checkpoint.mutation = Mutate $control $Case $stage.operations
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($Case.name)-$($stage.name)-$role.mdb"
                    $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($Case.name)-$($stage.name).mdb" } else { $control }
                    Copy-Item -LiteralPath $source -Destination $path
                    $checkpoint.roles[$role] = Capture $path $Case
                    if ($checkpoint.roles[$role].status -ne 'pass') { throw 'Checkpoint capture failed' }
                }
            }
            foreach ($role in @('candidate', 'control')) {
                $path = Join-Path $env:JET3_WORK "$($Case.name)-native-$role.mdb"
                Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($Case.name)-$Final-$role.mdb") -Destination $path
                $native = @{ mutation = (Mutate $path $Case $Case.native); capture = (Capture $path $Case) }; $outcome.native[$role] = $native
                if ($native.capture.status -ne 'pass') { throw 'Native follow-up capture failed' }
            }
        }
        $outcome.status = 'pass'
    } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    return $outcome
}

# A per-case worker producer: without -CaseName dispatch workers, otherwise run one case.
function Invoke-CaseSuite([string]$ManifestName, [string]$CaseName, [string]$Script, [string]$Type, [scriptblock]$Body, [string[]]$Extensions = @('.mdb', '.json')) {
    $manifest = Read-Manifest $ManifestName
    if (-not $CaseName) { Invoke-Workers $manifest $Script }
    $case = Select-Case $manifest $CaseName
    $result = New-Result $Type $manifest
    try {
        Test-Inputs $manifest
        $result.environment = Get-Environment
        $result.cases += ,(& $Body $manifest $case)
    } catch { $result.error = Failure $_ } finally { Save-Outputs $result "$CaseName-result.json" $Extensions }
    if (-not (Test-Complete $result)) { exit 1 }
}

# --- Long-value rows: Memo (12) as text, OLE (11) as hex; `generated` Id columns are AutoNumber ---

# Long-value rows keep Recordset.Fields alive: releasing it between long-value edits crashed
# DAO 3.6 with an AccessViolation (registry-4/5 multiple-long-values).
function Set-LongCell($Recordset, $Case, [int]$Column, $Value) {
    if ($Column -eq 0 -and $Case.generated) { return }
    $spec = $Case.fields[$Column]; $field = $null
    try {
        $field = $Recordset.Fields.Item([string]$spec[0])
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        if ($null -eq $Value) { $field.Value = [DBNull]::Value; return }
        switch ([int]$spec[1]) {
            2 { $field.Value = [byte]$Value }
            4 { $field.Value = [int]$Value }
            12 { $field.Value = [string]$Value }
            11 { $field.Value = [DBNull]::Value; $field.AppendChunk([byte[]](From-Hex ([string]$Value))) }
            default { throw 'Unknown field type' }
        }
    } finally { Release $field }
}

function Set-LongRow($Recordset, $Case, $Values) {
    for ($i = 0; $i -lt $Case.fields.Count; $i++) { Set-LongCell $Recordset $Case $i $Values[$i] }
}

function Field-Value($Recordset, [string]$Name) {
    ,$Recordset.Fields.Item($Name).Value
}

function Read-LongRow($Recordset, [string]$Name, $Case) {
    if ($Name -eq 'Notes') {
        $body = Field-Value $Recordset 'Body'
        return ,([object[]]@([int](Field-Value $Recordset 'Id'), $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    $values = [object[]]::new($Case.fields.Count)
    for ($i = 0; $i -lt $values.Length; $i++) {
        $value = Field-Value $Recordset ([string]$Case.fields[$i][0])
        $type = [int]$Case.fields[$i][1]
        $values[$i] = if ($value -is [DBNull]) { $null } elseif ($type -eq 11) { Hex ([byte[]]$value) } elseif ($type -eq 12) { [string]$value } else { [int]$value }
    }
    return ,$values
}

function Read-LongRows($Recordset, [string]$Name, $Case) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) { [void]$rows.Add((Read-LongRow $Recordset $Name $Case)); $Recordset.MoveNext() }
    return ,([object[]]$rows.ToArray())
}

# Items per case (created after Notes when `later`) plus the Notes Memo sentinel.
function New-LongControl([string]$Path, $Case) {
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $indexes = $field = $index = $keys = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspaces = $engine.Workspaces; $workspace = $workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, (Locale-String 'general'), 32); $tables = $db.TableDefs
        $order = if ($Case.later) { @('Notes', 'Items') } else { @('Items', 'Notes') }
        foreach ($name in $order) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/create/$name"
            $table = $db.CreateTableDef($name); $fields = $table.Fields; $indexes = $table.Indexes
            $specs = if ($name -eq 'Items') { $Case.fields } else { @(@('Id', 4, 4, $false), @('Body', 12, 0, $false)) }
            foreach ($spec in $specs) {
                $field = $table.CreateField([string]$spec[0], [int]$spec[1], [int]$spec[2])
                if ([bool]$spec[3]) { $field.Attributes = 16 }
                $fields.Append($field); Release $field; $field = $null
            }
            if ($name -eq 'Items') {
                foreach ($spec in $Case.indexes) {
                    $index = $table.CreateIndex([string]$spec.name); $keys = $index.Fields
                    $index.Primary = [bool]$spec.primary; $index.Unique = [bool]$spec.unique
                    $index.Required = [bool]$spec.required; $index.IgnoreNulls = [bool]$spec.ignore
                    foreach ($component in $spec.fields) {
                        $key = $index.CreateField([string]$Case.fields[[int]$component[0]][0])
                        if ([bool]$component[1]) { $key.Attributes = 1 }
                        $keys.Append($key); Release $key; $key = $null
                    }
                    $indexes.Append($index); Release $keys; $keys = $null; Release $index; $index = $null
                }
            }
            $tables.Append($table); Release $indexes; $indexes = $null; Release $fields; $fields = $null; Release $table; $table = $null
            $rs = $db.OpenRecordset($name, 2)
            if ($name -eq 'Notes') {
                $notes = @{ name = 'Notes'; generated = $false; fields = $specs }
                $rs.AddNew(); Set-LongRow $rs $notes @([int]7, [string]('n' * 4096)); $rs.Update()
                $rs.AddNew(); Set-LongRow $rs $notes @([int]8, $null); $rs.Update()
            } else {
                foreach ($row in $Case.initial_rows) {
                    $rs.AddNew(); Set-LongRow $rs $Case $row; $rs.Update(); $rs.Bookmark = $rs.LastModified
                    if ([int](Field-Value $rs 'Id') -ne [int]$row[0]) { throw 'Generated/control initial Id differs' }
                }
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        Close-Com $rs $true
        Release $key; Release $keys; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        Close-Com $db $true; Release $workspace; Release $workspaces; Release $engine
    }
}

# Operations: insert {row}, replace {id, row}, delete {id}; located with FindFirst on a dynaset.
function Update-LongRows([string]$Path, $Case, $Operations) {
    $before = Identity $Path; $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $false)
        $rs = $db.OpenRecordset('Items', 2)
        foreach ($operation in $Operations) {
            $id = if ($operation.kind -eq 'insert') { $operation.row[0] } else { $operation.id }
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/$($operation.kind)/$id"
            if ($operation.kind -eq 'insert') { $rs.AddNew(); Set-LongRow $rs $Case $operation.row; $rs.Update() }
            else {
                $rs.FindFirst('[Id] = ' + [string][int]$operation.id)
                if ($rs.NoMatch) { throw 'Mutation Id absent' }
                switch ($operation.kind) {
                    'replace' { $rs.Edit(); Set-LongRow $rs $Case $operation.row; $rs.Update() }
                    'delete' { $rs.Delete() }
                    default { throw 'Unknown operation' }
                }
            }
            if ($operation.kind -ne 'delete') {
                $rs.Bookmark = $rs.LastModified
                if ([int](Field-Value $rs 'Id') -ne [int]$operation.row[0]) { throw 'Native generated/mutated Id differs' }
            }
        }
    } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ before = $before; after = (Identity $Path); operations = $Operations }
}

function Capture-LongRows([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot = Read-Inventory $db
        $snapshot.user_tables = @(); $snapshot.index_reads = @{}
        foreach ($name in @('Items', 'Notes')) {
            $item = Read-Table $db $name
            $rs = $db.OpenRecordset($name, 4); $item.rows = Read-LongRows $rs $name $Case
            $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += ,$item
        }
        $rs = $db.OpenRecordset('Items', 1)
        foreach ($index in $Case.indexes) {
            $script:endpoint = "$([IO.Path]::GetFileName($Path))/index/$($index.name)"
            $rs.Index = [string]$index.name
            if (-not ($rs.BOF -and $rs.EOF)) { $rs.MoveFirst() }
            $read = @{ traversal = (Read-LongRows $rs 'Items' $Case); seek = @() }
            foreach ($query in $index.queries) {
                $script:endpoint = "$([IO.Path]::GetFileName($Path))/index/$($index.name)/seek/$($query -join '/')"
                if ($index.fields.Count -eq 1) { $rs.Seek('=', [int]$query[0]) } else { $rs.Seek('=', [int]$query[0], [int]$query[1]) }
                $row = if ($rs.NoMatch) { $null } else { Read-LongRow $rs 'Items' $Case }
                $read.seek += ,@{ query = $query; row = $row }
            }
            $snapshot.index_reads[[string]$index.name] = $read
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally { Close-Com $rs $true; Close-Com $db $true; Release $engine }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}

# One creation case: capture the candidate and a DAO control, then apply `native` to copies of both.
function Invoke-LongCreation($Case) {
    $outcome = @{ name = [string]$Case.name; status = 'running'; original = @{}; native = @{}; mutations = @{}; error = $null }
    try {
        New-LongControl (Join-Path $env:JET3_WORK "$($Case.name)-original-control.mdb") $Case
        foreach ($role in @('candidate', 'control')) {
            $path = Join-Path $env:JET3_WORK "$($Case.name)-original-$role.mdb"
            if ($role -eq 'candidate') { Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($Case.name).mdb") -Destination $path }
            $outcome.original[$role] = Capture-LongRows $path $Case
            if ($outcome.original[$role].status -ne 'pass') { throw 'Original capture failed' }
            $next = Join-Path $env:JET3_WORK "$($Case.name)-native-$role.mdb"
            Copy-Item -LiteralPath $path -Destination $next
            $outcome.mutations[$role] = Update-LongRows $next $Case $Case.native
            $outcome.native[$role] = Capture-LongRows $next $Case
            if ($outcome.native[$role].status -ne 'pass') { throw 'Native continuation capture failed' }
        }
        $outcome.status = 'pass'
    } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    $outcome
}
