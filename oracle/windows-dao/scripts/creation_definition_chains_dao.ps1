function Failure($Record) {
    return @{ endpoint = $script:endpoint; message = $Record.Exception.Message; hresult = $Record.Exception.HResult; stack = $Record.ScriptStackTrace }
}

function Set-Cell($Recordset, $Case, [int]$Column, $Value) {
    if ($Column -eq 0 -and $Case.generated) { return }
    $spec = $Case.fields[$Column]; $fields = $Recordset.Fields; $field = $null
    try {
        $field = $fields.Item([string]$spec[0])
        $script:endpoint = "$($Case.name)/assign/$($spec[0])"
        if ($null -eq $Value) { $field.Value = [DBNull]::Value; return }
        switch ([int]$spec[1]) {
            2 { $field.Value = [byte]$Value }
            4 { $field.Value = [int]$Value }
            12 { $field.Value = [string]$Value }
            11 {
                $text = [string]$Value; $bytes = [byte[]]::new($text.Length / 2)
                for ($n = 0; $n -lt $bytes.Length; $n++) { $bytes[$n] = [Convert]::ToByte($text.Substring(2 * $n, 2), 16) }
                $field.Value = [DBNull]::Value; $field.AppendChunk([byte[]]$bytes)
            }
            default { throw 'Unknown field type' }
        }
    } finally { Release $field; Release $fields }
}
function Set-Row($Recordset, $Case, $Values) {
    for ($i = 0; $i -lt $Case.fields.Count; $i++) { Set-Cell $Recordset $Case $i $Values[$i] }
}
function Field-Value($Recordset, $Name) {
    $fields = $Recordset.Fields; $field = $null
    try { $field = $fields.Item($Name); return ,$field.Value }
    finally { Release $field; Release $fields }
}
function Read-Row($Recordset, [string]$Name, $Case) {
    if ($Name -eq 'Notes') {
        $body = Field-Value $Recordset 'Body'
        return ,([object[]]@([int](Field-Value $Recordset 'Id'), $(if ($body -is [DBNull]) { $null } else { [string]$body })))
    }
    $values = [object[]]::new($Case.fields.Count)
    for ($i = 0; $i -lt $values.Length; $i++) {
        $value = Field-Value $Recordset ([string]$Case.fields[$i][0])
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
    $engine = $workspaces = $workspace = $db = $tables = $table = $fields = $indexes = $field = $index = $keyFields = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36; $workspaces = $engine.Workspaces; $workspace = $workspaces.Item(0)
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $tables = $db.TableDefs
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
                    $index = $table.CreateIndex([string]$spec.name); $keyFields = $index.Fields
                    $index.Primary = [bool]$spec.primary; $index.Unique = [bool]$spec.unique
                    $index.Required = [bool]$spec.required; $index.IgnoreNulls = [bool]$spec.ignore
                    foreach ($component in $spec.fields) {
                        $key = $index.CreateField([string]$Case.fields[[int]$component[0]][0])
                        if ([bool]$component[1]) { $key.Attributes = 1 }
                        $keyFields.Append($key); Release $key; $key = $null
                    }
                    $indexes.Append($index); Release $keyFields; $keyFields = $null; Release $index; $index = $null
                }
            }
            $tables.Append($table); Release $indexes; $indexes = $null; Release $fields; $fields = $null; Release $table; $table = $null
            $rs = $db.OpenRecordset($name, 2)
            if ($name -eq 'Notes') {
                $notes = @{ name = 'Notes'; generated = $false; fields = $specs }
                $rs.AddNew(); Set-Row $rs $notes @([int]7, [string]('n' * 4096)); $rs.Update()
                $rs.AddNew(); Set-Row $rs $notes @([int]8, $null); $rs.Update()
            } else {
                foreach ($row in $Case.initial_rows) {
                    $rs.AddNew(); Set-Row $rs $Case $row; $rs.Update(); $rs.Bookmark = $rs.LastModified
                    if ([int](Field-Value $rs 'Id') -ne [int]$row[0]) { throw 'Generated/control initial Id differs' }
                }
            }
            $rs.Close(); Release $rs; $rs = $null
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        Release $key; Release $keyFields; Release $index; Release $indexes; Release $field; Release $fields; Release $table; Release $tables
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $workspace; Release $workspaces; Release $engine
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
                if ([int](Field-Value $rs 'Id') -ne [int]$operation.row[0]) { throw 'Native generated/mutated Id differs' }
            }
        }
    } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ before = $before; after = (Identity $Path); operations = $Case.native }
}
function Collection-Names($Collection) {
    $names = @()
    for ($i = 0; $i -lt $Collection.Count; $i++) {
        $item = $Collection.Item($i)
        try { $names += [string]$item.Name } finally { Release $item }
    }
    return ,$names
}
function Table-Metadata($Table, [string]$Name) {
    $fields = $indexes = $index = $keys = $field = $null
    $item = @{ name = $Name; attributes = [int]$Table.Attributes; fields = @(); indexes = @() }
    try {
        $fields = $Table.Fields
        for ($i = 0; $i -lt $fields.Count; $i++) {
            $field = $fields.Item($i)
            $item.fields += ,@{ name = [string]$field.Name; type = [int]$field.Type; size = [int]$field.Size; attributes = [int]$field.Attributes;
                required = [bool]$field.Required; allow_zero_length = [bool]$field.AllowZeroLength; default_value = [string]$field.DefaultValue }
            Release $field; $field = $null
        }
        Release $fields; $fields = $null
        $indexes = $Table.Indexes
        for ($i = 0; $i -lt $indexes.Count; $i++) {
            $index = $indexes.Item($i)
            $entry = @{ name = [string]$index.Name; primary = [bool]$index.Primary; unique = [bool]$index.Unique; required = [bool]$index.Required;
                foreign = [bool]$index.Foreign; ignore_nulls = [bool]$index.IgnoreNulls; fields = @() }
            $keys = $index.Fields
            for ($j = 0; $j -lt $keys.Count; $j++) {
                $field = $keys.Item($j)
                $entry.fields += ,@{ name = [string]$field.Name; attributes = [int]$field.Attributes }
                Release $field; $field = $null
            }
            Release $keys; $keys = $null; Release $index; $index = $null
            $item.indexes += ,$entry
        }
    } finally { Release $field; Release $keys; Release $index; Release $indexes; Release $fields }
    return $item
}
function Capture([string]$Path, $Case) {
    $before = Identity $Path; $engine = $db = $tables = $queries = $relations = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $snapshot = @{}
    try {
        $script:endpoint = "$([IO.Path]::GetFileName($Path))/capture"
        $engine = New-Object -ComObject DAO.DBEngine.36; $db = $engine.OpenDatabase($Path, $false, $true)
        $tables = $db.TableDefs; $queries = $db.QueryDefs; $relations = $db.Relations
        $snapshot = @{ version = [string]$db.Version; tables = @((Collection-Names $tables) | Sort-Object);
            queries = (Collection-Names $queries); relations = (Collection-Names $relations); user_tables = @(); index_reads = @{} }
        Release $queries; $queries = $null; Release $relations; $relations = $null
        foreach ($name in @('Items', 'Notes')) {
            $table = $tables.Item($name)
            $item = Table-Metadata $table $name
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
                $first = [int]$query[0]
                if ($index.fields.Count -eq 1) { $rs.Seek('=', $first) }
                else { $second = [int]$query[1]; $rs.Seek('=', $first, $second) }
                $row = if ($rs.NoMatch) { $null } else { Read-Row $rs 'Items' $Case }
                $read.seek += ,@{ query = $query; row = $row }
            }
            $snapshot.index_reads[[string]$index.name] = $read
        }
    } catch { $status = 'error'; $errorDetail = Failure $_ } finally {
        if ($null -ne $rs) { try { $rs.Close() } catch {} }; Release $rs; Release $table; Release $tables; Release $queries; Release $relations
        if ($null -ne $db) { try { $db.Close() } catch {} }; Release $db; Release $engine
    }
    return @{ file = [IO.Path]::GetFileName($Path); before = $before; after = (Identity $Path); status = $status; error = $errorDetail; snapshot = $snapshot }
}
