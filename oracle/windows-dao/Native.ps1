# Builds native inputs and applies native edits described by native.json.
#
# native.json:
#   inputs: [{ name, file, locale?, ops: [op...] }]      new databases built from operations
#   edits:  [{ name, source, file, steps: [op...], transaction? }]
#           copies `source` (a built input or a staged file) to `file`, then applies the steps;
#           the first failing step ends the edit and is recorded.
#
# Operations (`op` field): table, sql, relation, relationship, query, property, payload,
# insert, replace, delete, request, checkpoint. See oracle/windows-dao/README.md.
. (Join-Path $PSScriptRoot 'Common.ps1')

$script:engine = New-Object -ComObject DAO.DBEngine.36
$script:ColumnTextProperties = @(@('validation_rule', 'ValidationRule'), @('validation_text', 'ValidationText'), @('default_value', 'DefaultValue'))

function Table-Of($Db, [string]$Name) {
    $tables = $Db.TableDefs
    try { $tables.Refresh(); $tables.Item($Name) } finally { Release $tables }
}

function Field-Of($Db, [string]$Table, [string]$Name) {
    $definition = Table-Of $Db $Table
    $fields = $definition.Fields
    try { $fields.Item($Name) } finally { Release $fields; Release $definition }
}

function Set-Description($Object, $Value) {
    $properties = $Object.Properties
    $property = $null
    try {
        $existing = $false
        for ($i = 0; $i -lt $properties.Count; $i++) {
            $item = $properties.Item($i)
            if ([string]$item.Name -eq 'Description') { $existing = $true }
            Release $item
        }
        if ($null -eq $Value) {
            if ($existing) { $properties.Delete('Description') }
        } elseif ($existing) {
            $property = $properties.Item('Description')
            Set-Property $property 'Value' ([string]$Value)
        } else {
            $property = $Object.CreateProperty('Description', 10, [string]$Value)
            $properties.Append($property)
        }
    } finally { Release $property; Release $properties }
}

function Add-TextProperty($Object, [string]$Name, [string]$Value) {
    $property = $Object.CreateProperty($Name, 10, $Value)
    $properties = $Object.Properties
    try { $properties.Append($property) } finally { Release $property; Release $properties }
}

function New-Field($Definition, $Column) {
    $kind = [string]$Column.type
    $size = 0
    if (Has $Column 'size') { $size = [int]$Column.size }
    $field = $Definition.CreateField([string]$Column.name, [int]$script:FieldTypes[$kind], $size)
    if ($kind -eq 'auto_increment') { Set-Property $field 'Attributes' ($field.Attributes -bor 16) }
    if ($kind -eq 'fixed_text') { Set-Property $field 'Attributes' ($field.Attributes -bor 1) }
    if ((Has $Column 'required') -and $Column.required) { Set-Property $field 'Required' $true }
    if ((Has $Column 'allow_zero_length') -and $Column.allow_zero_length) { Set-Property $field 'AllowZeroLength' $true }
    foreach ($pair in $script:ColumnTextProperties) {
        if (Has $Column $pair[0]) { Set-Property $field $pair[1] ([string]$Column.($pair[0])) }
    }
    $field
}

function New-Index($Definition, $Spec) {
    $index = $Definition.CreateIndex([string]$Spec.name)
    $keys = $index.Fields
    foreach ($key in $Spec.fields) {
        $field = $index.CreateField([string]$key.column)
        if ((Has $key 'direction') -and [string]$key.direction -eq 'descending') { Set-Property $field 'Attributes' 1 }
        $keys.Append($field)
        Release $field
    }
    Release $keys
    $policy = 'include'
    if (Has $Spec 'null_policy') { $policy = [string]$Spec.null_policy }
    $kind = 'ordinary'
    if (Has $Spec 'kind') { $kind = [string]$Spec.kind }
    if ($kind -eq 'primary') {
        Set-Property $index 'Primary' $true
        Set-Property $index 'Unique' $true
        $policy = 'required'
    } elseif ($kind -eq 'unique') {
        Set-Property $index 'Unique' $true
    }
    if ($policy -eq 'required') { Set-Property $index 'Required' $true }
    if ($policy -eq 'ignore_all_null') { Set-Property $index 'IgnoreNulls' $true }
    $index
}

# A table in the jet3-cli create-request shape, plus optional `properties` (custom Text
# properties on the table) and per-column `properties`.
function Create-Table($Db, $Spec) {
    $definition = $Db.CreateTableDef([string]$Spec.name)
    $fields = $definition.Fields
    foreach ($column in $Spec.columns) {
        $field = New-Field $definition $column
        $fields.Append($field)
        Release $field
    }
    Release $fields
    if (Has $Spec 'validation_rule') { Set-Property $definition 'ValidationRule' ([string]$Spec.validation_rule) }
    if (Has $Spec 'validation_text') { Set-Property $definition 'ValidationText' ([string]$Spec.validation_text) }
    if (Has $Spec 'indexes') {
        $indexes = $definition.Indexes
        foreach ($indexSpec in $Spec.indexes) {
            $index = New-Index $definition $indexSpec
            $indexes.Append($index)
            Release $index
        }
        Release $indexes
    }
    $tables = $Db.TableDefs
    $tables.Append($definition)
    Release $tables
    try {
        if (Has $Spec 'properties') {
            foreach ($property in $Spec.properties.PSObject.Properties) {
                Add-TextProperty $definition $property.Name ([string]$property.Value)
            }
        }
        foreach ($column in $Spec.columns) {
            $hasDescription = Has $column 'description'
            $hasProperties = Has $column 'properties'
            if (-not ($hasDescription -or $hasProperties)) { continue }
            $field = Field-Of $Db ([string]$Spec.name) ([string]$column.name)
            try {
                if ($hasDescription) { Set-Description $field $column.description }
                if ($hasProperties) {
                    foreach ($property in $column.properties.PSObject.Properties) {
                        Add-TextProperty $field $property.Name ([string]$property.Value)
                    }
                }
            } finally { Release $field }
        }
    } finally { Release $definition }
    if (Has $Spec 'rows') {
        foreach ($row in $Spec.rows) { Insert-Row $Db ([string]$Spec.name) @($row) 'dynaset' }
    }
}

function Relation-Attributes($Spec) {
    $value = 0
    if ((Has $Spec 'unique') -and $Spec.unique) { $value = $value -bor 1 }
    if ((Has $Spec 'enforce') -and -not $Spec.enforce) { $value = $value -bor 2 }
    if ((Has $Spec 'cascade_updates') -and $Spec.cascade_updates) { $value = $value -bor 256 }
    if ((Has $Spec 'cascade_deletes') -and $Spec.cascade_deletes) { $value = $value -bor 4096 }
    $join = 'inner'
    if (Has $Spec 'join') { $join = [string]$Spec.join }
    if ($join -eq 'left' -or $join -eq 'left_and_right') { $value = $value -bor 16777216 }
    if ($join -eq 'right' -or $join -eq 'left_and_right') { $value = $value -bor 33554432 }
    $value
}

function Add-Relation($Db, [string]$Name, [string]$Parent, [string]$Child, [int]$Attributes, $Pairs) {
    $relation = $Db.CreateRelation($Name, $Parent, $Child, $Attributes)
    $fields = $relation.Fields
    foreach ($pair in $Pairs) {
        $pair = @($pair)
        $field = $relation.CreateField([string]$pair[0])
        Set-Property $field 'ForeignName' ([string]$pair[1])
        $fields.Append($field)
        Release $field
    }
    Release $fields
    $relations = $Db.Relations
    $relations.Append($relation)
    Release $relation
    Release $relations
}

function Endpoint-Columns($Endpoint) {
    if (Has $Endpoint 'columns') { return , @($Endpoint.columns) }
    , @([string]$Endpoint.column)
}

# A relationship in the jet3-cli request shape.
function Create-Relationship($Db, $Spec) {
    $parent = Endpoint-Columns $Spec.parent
    $child = Endpoint-Columns $Spec.child
    $pairs = @()
    for ($i = 0; $i -lt $parent.Count; $i++) { $pairs += , @($parent[$i], $child[$i]) }
    Add-Relation $Db ([string]$Spec.name) ([string]$Spec.parent.table) ([string]$Spec.child.table) (Relation-Attributes $Spec) $pairs
}

function Field-Names($Db, [string]$Table) {
    $definition = Table-Of $Db $Table
    $fields = $definition.Fields
    $names = @()
    try {
        for ($i = 0; $i -lt $fields.Count; $i++) {
            $field = $fields.Item($i)
            $names += [string]$field.Name
            Release $field
        }
    } finally { Release $fields; Release $definition }
    , $names
}

# Positional cells; AutoNumber values are never assigned on edit and must stay unchanged.
function Set-Cells($Recordset, $Cells, [bool]$Inserting) {
    $fields = $Recordset.Fields
    try {
        for ($i = 0; $i -lt $Cells.Count; $i++) {
            $cell = $Cells[$i]
            $field = $fields.Item($i)
            try {
                if (Is-AutoCell $cell) { continue }
                if (-not $Inserting -and ([int]$field.Attributes -band 16)) {
                    if ($null -eq $cell -or [int](@($cell.PSObject.Properties)[0].Value) -ne [int]$field.Value) {
                        throw 'Replacement changes an AutoNumber value'
                    }
                    continue
                }
                Set-Property $field 'Value' (Cell-Value $cell)
            } finally { Release $field }
        }
    } finally { Release $fields }
}

function Insert-Row($Db, [string]$Table, $Cells, [string]$Kind = 'table') {
    $type = 1
    if ($Kind -eq 'dynaset') { $type = 2 }
    $recordset = $Db.OpenRecordset($Table, $type)
    try {
        $recordset.AddNew()
        Set-Cells $recordset $Cells $true
        $recordset.Update()
    } finally {
        try { $recordset.CancelUpdate() } catch {}
        $recordset.Close()
        Release $recordset
    }
}

# Seek on `index` (default PrimaryKey) with the Long `id`, then edit or delete that row.
function Change-Row($Db, $Op) {
    $recordset = $Db.OpenRecordset([string]$Op.table, 1)
    try {
        $index = 'PrimaryKey'
        if (Has $Op 'index') { $index = [string]$Op.index }
        $recordset.Index = $index
        $recordset.Seek('=', [int]$Op.id)
        if ($recordset.NoMatch) { throw ('Missing row ' + $Op.id) }
        if ([string]$Op.op -eq 'delete') {
            $recordset.Delete()
            return
        }
        $recordset.Edit()
        Set-Cells $recordset @($Op.values) $false
        $recordset.Update()
    } finally {
        try { $recordset.CancelUpdate() } catch {}
        $recordset.Close()
        Release $recordset
    }
}

# Deterministic Memo text or OLE bytes in one existing row.
function Set-Payload($Db, $Op) {
    $recordset = $Db.OpenRecordset("SELECT * FROM [$($Op.table)] WHERE [$($Op.key)] = $([int]$Op.id)", 2)
    try {
        $recordset.Edit()
        $fields = $recordset.Fields
        $field = $fields.Item([string]$Op.column)
        $length = [int]$Op.length
        $seed = [int]$Op.seed
        if ([int]$field.Type -eq 12) {
            $builder = New-Object Text.StringBuilder
            for ($i = 0; $i -lt $length; $i++) { [void]$builder.Append([char](65 + (($i * 7 + $seed) % 26))) }
            Set-Property $field 'Value' $builder.ToString()
        } else {
            $bytes = New-Object byte[] $length
            for ($i = 0; $i -lt $length; $i++) { $bytes[$i] = [byte](($i * 13 + $seed) % 256) }
            Set-Property $field 'Value' $bytes
        }
        Release $field
        Release $fields
        $recordset.Update()
    } finally {
        $recordset.Close()
        Release $recordset
    }
}

function Set-CustomProperty($Db, $Op) {
    $target = [string]$Op.target
    $object = $null
    try {
        switch ($target) {
            'database' { Add-TextProperty $Db ([string]$Op.name) ([string]$Op.value); return }
            'table' { $object = Table-Of $Db ([string]$Op.table) }
            'field' { $object = Field-Of $Db ([string]$Op.table) ([string]$Op.column) }
            'query' { $queries = $Db.QueryDefs; $object = $queries.Item([string]$Op.query); Release $queries }
            default { throw ('Unknown property target ' + $target) }
        }
        Add-TextProperty $object ([string]$Op.name) ([string]$Op.value)
    } finally { Release $object }
}

function Set-TextProperties($Object, $Request, $Names) {
    foreach ($pair in $Names) {
        if (Has $Request $pair[0]) {
            $value = $Request.($pair[0])
            if ($null -eq $value) { $value = '' }
            Set-Property $Object $pair[1] ([string]$value)
        }
    }
}

# jet3-cli `schema` and `mutate` requests, applied through DAO objects.
function Apply-Request($Db, [string]$Command, $Request) {
    $operation = [string]$Request.operation
    if ($Command -eq 'mutate') {
        if ($operation -ne 'insert') { throw ('Unsupported native mutation ' + $operation) }
        Insert-Row $Db ([string]$Request.table) @($Request.values) 'dynaset'
        return
    }
    $definition = $collection = $object = $null
    try {
        switch ($operation) {
            'create_table' { Create-Table $Db $Request.table }
            'drop_table' { $collection = $Db.TableDefs; $collection.Delete([string]$Request.table) }
            'rename_table' { $object = Table-Of $Db ([string]$Request.table); Set-Property $object 'Name' ([string]$Request.name) }
            'set_table_properties' {
                $object = Table-Of $Db ([string]$Request.table)
                Set-TextProperties $object $Request @(@('validation_rule', 'ValidationRule'), @('validation_text', 'ValidationText'))
            }
            'create_column' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Fields
                $object = New-Field $definition $Request.column
                $collection.Append($object)
                if (Has $Request.column 'description') {
                    Release $object
                    $object = Field-Of $Db ([string]$Request.table) ([string]$Request.column.name)
                    Set-Description $object $Request.column.description
                }
            }
            'drop_column' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Fields
                $collection.Delete([string]$Request.column)
            }
            'rename_column' {
                $object = Field-Of $Db ([string]$Request.table) ([string]$Request.column)
                Set-Property $object 'Name' ([string]$Request.name)
            }
            'set_column_properties' {
                $object = Field-Of $Db ([string]$Request.table) ([string]$Request.column)
                Set-TextProperties $object $Request $script:ColumnTextProperties
                if (Has $Request 'description') { Set-Description $object $Request.description }
            }
            'set_column_options' {
                $object = Field-Of $Db ([string]$Request.table) ([string]$Request.column)
                if (Has $Request 'allow_zero_length') { Set-Property $object 'AllowZeroLength' ([bool]$Request.allow_zero_length) }
                if (Has $Request 'required') { Set-Property $object 'Required' ([bool]$Request.required) }
            }
            'create_index' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Indexes
                $object = New-Index $definition $Request.index
                $collection.Append($object)
            }
            'drop_index' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Indexes
                $collection.Delete([string]$Request.index)
            }
            'rename_index' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Indexes
                $object = $collection.Item([string]$Request.index)
                Set-Property $object 'Name' ([string]$Request.name)
            }
            'replace_index' {
                $definition = Table-Of $Db ([string]$Request.table)
                $collection = $definition.Indexes
                $collection.Delete([string]$Request.index)
                $object = New-Index $definition $Request.replacement
                $collection.Append($object)
            }
            'create_relationship' { Create-Relationship $Db $Request.relationship }
            'drop_relationship' { $collection = $Db.Relations; $collection.Delete([string]$Request.name) }
            'replace_relationship' {
                $collection = $Db.Relations
                $collection.Delete([string]$Request.name)
                Create-Relationship $Db $Request.relationship
            }
            default { throw ('Unsupported native schema operation ' + $operation) }
        }
    } finally { Release $object; Release $collection; Release $definition }
}

function Apply-Op($Db, $Op, [string]$Path) {
    switch ([string]$Op.op) {
        'table' { Create-Table $Db $Op.table }
        'sql' { $Db.Execute([string]$Op.text, 128) }
        'relation' { Add-Relation $Db ([string]$Op.name) ([string]$Op.parent) ([string]$Op.child) ([int]$Op.attributes) @($Op.pairs) }
        'relationship' { Create-Relationship $Db $Op.relationship }
        'query' { $query = $Db.CreateQueryDef([string]$Op.name, [string]$Op.sql); Release $query }
        'property' { Set-CustomProperty $Db $Op }
        'payload' { Set-Payload $Db $Op }
        'insert' {
            $kind = 'table'
            if (Has $Op 'recordset') { $kind = [string]$Op.recordset }
            Insert-Row $Db ([string]$Op.table) @($Op.values) $kind
        }
        'replace' { Change-Row $Db $Op }
        'delete' { Change-Row $Db $Op }
        'request' { Apply-Request $Db ([string]$Op.command) $Op.request }
        default { throw ('Unsupported native op ' + $Op.op) }
    }
}

# Runs operations against one database; `checkpoint` closes, retains a copy and reopens.
function Run-Ops([string]$Path, $Ops, [bool]$Create, [string]$Locale, $Workspace, [bool]$Transaction, [bool]$StopOnError) {
    $db = $null
    $steps = @()
    $checkpoints = @()
    $inTransaction = $false
    try {
        if ($Create) { $db = $script:engine.CreateDatabase($Path, (Locale-String $Locale), 32) }
        else { $db = $script:engine.OpenDatabase($Path) }
        if ($Transaction) { $Workspace.BeginTrans(); $inTransaction = $true }
        foreach ($op in $Ops) {
            if ([string]$op.op -eq 'checkpoint') {
                $db.Close(); Release $db; $db = $null
                $target = Join-Path $env:JET3_OUTBOX ([string]$op.file)
                Copy-Item -LiteralPath $Path -Destination $target
                $checkpoints += @{ file = [string]$op.file; identity = (Identity $target) }
                $db = $script:engine.OpenDatabase($Path)
                continue
            }
            try {
                Apply-Op $db $op $Path
                $steps += @{ ok = $true }
            } catch {
                $steps += @{ ok = $false; error = (Error-Info $script:engine $_) }
                if ($StopOnError) { break }
                throw
            }
        }
    } finally {
        if ($inTransaction) { $Workspace.Rollback() }
        if ($null -ne $db) { try { $db.Close() } catch {}; Release $db }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    @{ steps = $steps; checkpoints = $checkpoints }
}

$job = Read-Json (Join-Path $PSScriptRoot 'native.json')
$result = [ordered]@{ environment = (Environment-Record $script:engine); inputs = @(); edits = @() }
$workspaces = $script:engine.Workspaces
$workspace = $workspaces.Item(0)
try {
    $inputs = @()
    if (Has $job 'inputs') { $inputs = @($job.inputs) }
    $edits = @()
    if (Has $job 'edits') { $edits = @($job.edits) }
    foreach ($spec in $inputs) {
        $path = Join-Path $env:JET3_WORK ([string]$spec.file)
        $failure = $null
        $outcome = @{ steps = @(); checkpoints = @() }
        $locale = $null
        if (Has $spec 'locale') { $locale = [string]$spec.locale }
        try { $outcome = Run-Ops $path @($spec.ops) $true $locale $workspace $false $false }
        catch { $failure = Error-Info $script:engine $_ }
        $identity = $null
        if (Test-Path -LiteralPath $path) {
            Copy-Item -LiteralPath $path -Destination (Join-Path $env:JET3_OUTBOX ([string]$spec.file))
            $identity = Identity $path
        }
        $result.inputs += @{
            name = [string]$spec.name; file = [string]$spec.file; identity = $identity
            failure = $failure; checkpoints = $outcome.checkpoints
        }
        Write-Output ('input {0} {1}' -f $spec.name, $(if ($failure) { 'FAILED ' + $failure.message } else { 'ok' }))
    }
    foreach ($edit in $edits) {
        $source = Join-Path $env:JET3_WORK ([string]$edit.source)
        $path = Join-Path $env:JET3_WORK ([string]$edit.file)
        Copy-Item -LiteralPath $source -Destination $path -Force
        $before = Identity $path
        $transaction = (Has $edit 'transaction') -and $edit.transaction
        $outcome = Run-Ops $path @($edit.steps) $false $null $workspace $transaction $true
        Copy-Item -LiteralPath $path -Destination (Join-Path $env:JET3_OUTBOX ([string]$edit.file)) -Force
        $result.edits += @{
            name = [string]$edit.name; source = [string]$edit.source; file = [string]$edit.file
            before = $before; after = (Identity $path); steps = $outcome.steps; checkpoints = $outcome.checkpoints
        }
        $summary = ($outcome.steps | ForEach-Object { if ($_.ok) { 'ok' } else { 'refused ' + ($_.error.numbers -join ',') } }) -join ' '
        Write-Output ('{0} {1}' -f $edit.name, $summary)
    }
} finally {
    Release $workspace
    Release $workspaces
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'native-result.json')
    Release $script:engine
}
