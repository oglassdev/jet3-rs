Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
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
            default { throw 'Undeclared field type' }
        }
    }
    finally { Release $field }
}
function Read-Row($Recordset, $Arm) {
    $values = [object[]]::new($Arm.fields.Count)
    for ($i = 0; $i -lt $Arm.fields.Count; $i++) { $values[$i] = Read-Value $Recordset $Arm.fields[$i] }
    return @{tag=[int]$Recordset.Fields.Item('Tag').Value; values=$values}
}
function New-Control([string]$Path, $Arm, $Operations) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $script:endpoint = 'create_engine'
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $script:endpoint = 'create_database'
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $script:endpoint = 'create_table_index'
        $table = $db.CreateTableDef('Rows')
        foreach ($definition in @($Arm.fields) + @(@{name='Tag'; type=4; size=4})) {
            $field = $table.CreateField([string]$definition.name, [int]$definition.type, [int]$definition.size)
            $table.Fields.Append($field)
            Release $field; $field = $null
        }
        $index = $table.CreateIndex('ByKey')
        $index.Primary = $false
        $index.Unique = [bool]$Arm.unique
        $index.Required = [bool]$Arm.required
        $index.IgnoreNulls = [bool]$Arm.ignore_nulls
        for ($i = 0; $i -lt $Arm.fields.Count; $i++) {
            $key = $index.CreateField([string]$Arm.fields[$i].name)
            if ($Arm.directions[$i]) { $key.Attributes = 1 }
            $index.Fields.Append($key)
            Release $key; $key = $null
        }
        $table.Indexes.Append($index)
        $db.TableDefs.Append($table)
        $rs = $db.OpenRecordset('Rows', 2)
        $tag = 0
        foreach ($row in $Arm.rows) {
            $tag++
            $script:endpoint = 'add_new_tag_' + $tag
            $rs.AddNew()
            for ($i = 0; $i -lt $Arm.fields.Count; $i++) {
                $script:endpoint = 'assign_tag_' + $tag + '_field_' + $Arm.fields[$i].name
                Set-Value $rs $Arm.fields[$i] $row[$i]
            }
            $script:endpoint = 'assign_tag_' + $tag + '_Tag'
            $rs.Fields.Item('Tag').Value = [int]$tag
            $operation = @{tag=$tag; status='updated'; endpoint='update'; native_codes=@(); hresult=$null; error=$null}
            $script:endpoint = 'update_tag_' + $tag
            try { $rs.Update() }
            catch {
                $operation.status = 'rejected'
                $operation.native_codes = @($engine.Errors | ForEach-Object { [int]$_.Number })
                $exception = $_.Exception
                while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
                $operation.hresult = [int]$exception.HResult
                $operation.error = $_.Exception.Message
                if ($rs.EditMode -ne 0) { $rs.CancelUpdate() }
            }
            [void]$Operations.Add($operation)
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Observe([string]$Path, $Arm) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $endpoint = 'open_database'
    $snapshot = [ordered]@{}
    $status = 'pass'; $errorDetail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $endpoint = 'schema'
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $table = $db.TableDefs.Item('Rows')
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object {
            @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; ignore_nulls=[bool]$_.IgnoreNulls;
              fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; descending=(([int]$_.Attributes -band 1) -ne 0); attributes=[int]$_.Attributes} })}
        })
        $endpoint = 'rows'
        $rs = $db.OpenRecordset('Rows', 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) { [void]$rows.Add((Read-Row $rs $Arm)); $rs.MoveNext() }
        $snapshot.rows = @($rows)
        $rs.Close(); Release $rs; $rs = $null
        $endpoint = 'index_traversal'
        $rs = $db.OpenRecordset('Rows', 1)
        $rs.Index = 'ByKey'
        $traversal = New-Object Collections.ArrayList
        if (-not ($rs.BOF -and $rs.EOF)) { $rs.MoveFirst() }
        while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs $Arm)); $rs.MoveNext() }
        $snapshot.traversal = @($traversal)
        $endpoint = 'complete'
    }
    catch {
        $status = 'fail'
        $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>')
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'scalar-index-remaining.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/scalar_index_remaining.ps1') { throw 'Script pin mismatch' }
$mutationStarted = $false
$endpoint = 'initial'
$result = [ordered]@{
    document_type='dao_scalar_index_remaining_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); attempts=@(); error=$null; failure_endpoint=$null; failure_stack=$null
}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $name = "$($arm.name)-r$replica.mdb"
            $control = Join-Path $env:JET3_WORK $name
            $operations = New-Object Collections.ArrayList
            $result.attempts += @{arm=$arm.name; replica=$replica; operations=$operations}
            New-Control $control $arm $operations
            $observation = Observe $control $arm
            $observation.arm = $arm.name; $observation.replica = $replica; $observation.file = $name
            $observation.operations = @($operations)
            $result.replicas += $observation
        }
    }
}
catch {
    $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message
    $result.failure_endpoint = $endpoint
    $result.failure_stack = $_.ScriptStackTrace
}
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 30) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
