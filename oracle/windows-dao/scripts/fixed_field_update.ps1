Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) }
}
function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, ((ConvertTo-Json -InputObject $Value -Depth 30) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function From-Hex([string]$Text) {
    $bytes = New-Object byte[] ($Text.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($Text.Substring($i * 2, 2), 16) }
    return ,$bytes
}
function To-Hex([byte[]]$Bytes) { return [BitConverter]::ToString($Bytes).Replace('-', '').ToLowerInvariant() }
function Set-Value($Recordset, $Definition, $Value) {
    $field = $Recordset.Fields.Item([string]$Definition.name)
    try {
        if ($null -eq $Value) { $field.Value = [DBNull]::Value; return }
        if ($Definition.type -eq 1) { $field.Value = [bool]$Value; return }
        $raw = From-Hex ([string]$Value)
        switch ([int]$Definition.type) {
            2 { $field.Value = [byte]$raw[0] }
            3 { $field.Value = [int16][BitConverter]::ToInt16($raw, 0) }
            4 { $field.Value = [int][BitConverter]::ToInt32($raw, 0) }
            5 { $field.Value = ([decimal][BitConverter]::ToInt64($raw, 0) / [decimal]10000) }
            6 { $field.Value = [single][BitConverter]::ToSingle($raw, 0) }
            7 { $field.Value = [double][BitConverter]::ToDouble($raw, 0) }
            8 {
                $dateValue = [datetime]::FromOADate([BitConverter]::ToDouble($raw, 0))
                $field.Value = [datetime]$dateValue
            }
            9 { $field.Value = [byte[]]$raw }
            10 { $field.Value = $CodePage.GetString($raw) }
            15 { $field.Value = '{' + ([guid]::new([byte[]]$raw)).ToString() + '}' }
            default { throw 'Undeclared field type' }
        }
    }
    finally { Release $field }
}
function Read-Value($Recordset, $Definition) {
    $field = $Recordset.Fields.Item([string]$Definition.name)
    try {
        $value = $field.Value
        if ($null -eq $value -or $value -is [DBNull]) { return $null }
        switch ([int]$Definition.type) {
            1 { return [bool]$value }
            2 { return To-Hex ([byte[]]@([byte]$value)) }
            3 { return To-Hex ([BitConverter]::GetBytes([int16]$value)) }
            4 { return To-Hex ([BitConverter]::GetBytes([int]$value)) }
            5 { return To-Hex ([BitConverter]::GetBytes([long]([decimal]$value * [decimal]10000))) }
            6 { return To-Hex ([BitConverter]::GetBytes([single]$value)) }
            7 { return To-Hex ([BitConverter]::GetBytes([double]$value)) }
            8 { return To-Hex ([BitConverter]::GetBytes(([datetime]$value).ToOADate())) }
            9 { return To-Hex ([byte[]]$value) }
            10 { return To-Hex ($CodePage.GetBytes([string]$value)) }
            15 {
                $text = [string]$value
                if ($text -notmatch '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}') { throw 'Unrecognized GUID value' }
                return To-Hex (([guid]$Matches[0]).ToByteArray())
            }
            default { throw 'Undeclared field type' }
        }
    }
    finally { Release $field }
}
function Get-Fields($Plan, $Arm, [string]$TableName) {
    $fields = [object[]]::new($Plan.fields.Count)
    for ($i=0; $i -lt $fields.Length; $i++) {
        $fields[$i] = if ($TableName -eq 'Items' -and $Plan.fields[$i].name -eq 'Value') { $Arm.field } else { $Plan.fields[$i] }
    }
    return ,$fields
}
function Failure($Record) {
    return @{type=$Record.Exception.GetType().FullName; message=$Record.Exception.Message; stack=[string]$Record.ScriptStackTrace; endpoint=$script:endpoint}
}
$CodePage = [Text.Encoding]::GetEncoding(1252, (New-Object Text.EncoderExceptionFallback), (New-Object Text.DecoderExceptionFallback))
function Create-Original([string]$Path, $Plan, $Arm) {
    $engine = $workspace = $db = $table = $field = $rs = $query = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $script:endpoint = 'create_database'; $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($spec in $Plan.tables) {
            $script:endpoint = 'create_table'; $table = $db.CreateTableDef([string]$spec.name)
            foreach ($column in (Get-Fields $Plan $Arm ([string]$spec.name))) {
                $script:endpoint = "create_field/$($column.name)"; $field = $table.CreateField([string]$column.name, [int]$column.type, [int]$column.size)
                if ($column.fixed) { $field.Attributes = 1 }
                $table.Fields.Append($field); Release $field; $field = $null
            }
            $db.TableDefs.Append($table)
            $rs = $db.OpenRecordset([string]$spec.name, 2)
            foreach ($row in $spec.rows) {
                $rs.AddNew()
                $definitions = Get-Fields $Plan $Arm ([string]$spec.name)
                for ($i=0; $i -lt $definitions.Length; $i++) {
                    $value = if ($spec.name -eq 'Items' -and $definitions[$i].name -eq 'Value') { $Arm.initial_hex } else { $row[$i] }
                    $script:endpoint = "assign/$($spec.name)/$($definitions[$i].name)"
                    Set-Value $rs $definitions[$i] $value
                }
                $script:endpoint = "update/$($spec.name)"
                $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null; Release $table; $table = $null
        }
        $script:endpoint = 'create_query'; $query = $db.CreateQueryDef([string]$Plan.query.name, [string]$Plan.query.sql)
    } catch { $script:failure = Failure $_; throw } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $query; Release $field; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $engine
    }
}
function Observe([string]$Path, $Plan, $Arm) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $endpoint = 'open_database'; $script:endpoint = $endpoint; $snapshot = [ordered]@{}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { @{name=[string]$_.Name; table=[string]$_.Table; foreign_table=[string]$_.ForeignTable; attributes=[int]$_.Attributes; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })} })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { @{name=[string]$_.Name; sql=[string]$_.SQL; type=[int]$_.Type} } | Sort-Object { $_.name })
        $snapshot.user_tables = @()
        foreach ($name in @($snapshot.tables | Where-Object { -not $_.StartsWith('MSys') })) {
            $endpoint = 'schema'; $script:endpoint = $endpoint; $table = $db.TableDefs.Item([string]$name)
            $item = [ordered]@{name=[string]$table.Name; attributes=[int]$table.Attributes}
            $item.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes; required=[bool]$_.Required; allow_zero_length=[bool]$_.AllowZeroLength; default_value=[string]$_.DefaultValue} })
            $item.indexes = @($table.Indexes | ForEach-Object { @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes} })} })
            $endpoint = 'rows'; $script:endpoint = $endpoint; $rs = $db.OpenRecordset([string]$name, 4)
            $rows = New-Object Collections.ArrayList
            while (-not $rs.EOF) {
                $values = [object[]]::new($item.fields.Count)
                for ($i=0; $i -lt $values.Length; $i++) { $values[$i] = Read-Value $rs $item.fields[$i] }
                [void]$rows.Add($values)
                $rs.MoveNext()
            }
            $item.rows = @($rows); $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += $item; Release $table; $table = $null
        }
    } catch { $status = 'error'; $errorDetail = Failure $_; $script:failure = $errorDetail }
    finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{file=[IO.Path]::GetFileName($Path); before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'fixed-field-update.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/fixed_field_update.ps1') { throw 'Script pin mismatch' }
$phase = (Get-Content -LiteralPath (Join-Path $env:JET3_WORK 'phase.txt') -Raw).Trim()
if ($phase -notin @('create', 'observe')) { throw 'Unknown phase' }
$mutationStarted = $false; $failure = $null; $endpoint = 'start'
$result = [ordered]@{document_type='dao_fixed_field_update_phase'; phase=$phase; plan_sha256=(Identity $planPath).sha256; environment=@{process_bits=32; provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}; mutation_started=$false; observations=@(); error=$null; endpoint='start'}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $roles = if ($phase -eq 'create') { @('original') } else { @('original', 'updated') }
            foreach ($role in $roles) {
                $path = Join-Path $env:JET3_WORK "$($arm.name)-r$replica-$role.mdb"
                $result.endpoint = "$($arm.name)/$replica/$role"; $script:endpoint = $result.endpoint
                if ($phase -eq 'create') { Create-Original $path $plan $arm }
                $observation = Observe $path $plan $arm
                $result.observations += @{arm=[string]$arm.name; replica=$replica; role=$role; observation=$observation}
                if ($observation.status -ne 'pass') { throw 'Read-only observation failed' }
            }
        }
    }
} catch { $result.error = if ($null -ne $failure) { $failure } else { Failure $_ } }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
