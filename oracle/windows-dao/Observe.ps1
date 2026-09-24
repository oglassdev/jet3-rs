# Read-only DAO observation of every staged .mdb (or observe.json `files`).
#
# For each file: database properties, every user table (properties, fields, indexes with
# complete traversals, rows), relations and QueryDefs with parameters. Optional
# observe.json `seeks` lists Seek probes: [{ table, index, keys: [[cell...]...] }].
# Opening, reading and closing must leave the file bytes unchanged.
. (Join-Path $PSScriptRoot 'Common.ps1')

function Read-Properties($Object) {
    $items = @()
    $properties = $property = $null
    try {
        $properties = $Object.Properties
        for ($i = 0; $i -lt $properties.Count; $i++) {
            $property = $properties.Item($i)
            $item = [ordered]@{ ordinal = $i; name = [string]$property.Name; type = [int]$property.Type }
            try { $item.value = Norm $property.Value }
            catch { $item.error = @{ hresult = [int]$_.Exception.HResult; message = $_.Exception.Message } }
            $items += $item
            Release $property
            $property = $null
        }
    } finally { Release $property; Release $properties }
    , $items
}

function Read-Row($Recordset) {
    $row = [ordered]@{}
    $fields = $field = $null
    try {
        $fields = $Recordset.Fields
        for ($i = 0; $i -lt $fields.Count; $i++) {
            $field = $fields.Item($i)
            $row[[string]$field.Name] = Norm $field.Value
            Release $field
            $field = $null
        }
    } finally { Release $field; Release $fields }
    $row
}

function Read-Rows($Recordset) {
    $rows = New-Object Collections.ArrayList
    while (-not $Recordset.EOF) {
        [void]$rows.Add((Read-Row $Recordset))
        $Recordset.MoveNext()
    }
    , $rows.ToArray()
}

function Read-Fields($Definition) {
    $items = @()
    $fields = $field = $null
    try {
        $fields = $Definition.Fields
        for ($i = 0; $i -lt $fields.Count; $i++) {
            $field = $fields.Item($i)
            $items += [ordered]@{
                ordinal = $i
                name = [string]$field.Name
                type = [int]$field.Type
                size = [int]$field.Size
                attributes = [int]$field.Attributes
                required = [bool]$field.Required
                allow_zero_length = [bool]$field.AllowZeroLength
                properties = (Read-Properties $field)
            }
            Release $field
            $field = $null
        }
    } finally { Release $field; Release $fields }
    , $items
}

function Read-Index($Db, [string]$Table, $Index, [int]$Ordinal) {
    $keys = @()
    $fields = $field = $recordset = $null
    try {
        $fields = $Index.Fields
        for ($j = 0; $j -lt $fields.Count; $j++) {
            $field = $fields.Item($j)
            $keys += [ordered]@{
                ordinal = $j
                name = [string]$field.Name
                attributes = [int]$field.Attributes
                properties = (Read-Properties $field)
            }
            Release $field
            $field = $null
        }
        $recordset = $Db.OpenRecordset($Table, 1)
        $recordset.Index = [string]$Index.Name
        if (-not ($recordset.BOF -and $recordset.EOF)) { $recordset.MoveFirst() }
        $traversal = Read-Rows $recordset
        [ordered]@{
            ordinal = $Ordinal
            name = [string]$Index.Name
            primary = [bool]$Index.Primary
            unique = [bool]$Index.Unique
            required = [bool]$Index.Required
            foreign = [bool]$Index.Foreign
            ignore_nulls = [bool]$Index.IgnoreNulls
            fields = $keys
            properties = (Read-Properties $Index)
            traversal = $traversal
        }
    } finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch {} }
        Release $recordset; Release $field; Release $fields
    }
}

function Read-Table($Db, $Definition, [int]$Ordinal) {
    $name = [string]$Definition.Name
    $indexes = @()
    $collection = $index = $recordset = $null
    try {
        $collection = $Definition.Indexes
        for ($i = 0; $i -lt $collection.Count; $i++) {
            $index = $collection.Item($i)
            $indexes += (Read-Index $Db $name $index $i)
            Release $index
            $index = $null
        }
        $recordset = $Db.OpenRecordset($name, 4)
        $rows = Read-Rows $recordset
        [ordered]@{
            ordinal = $Ordinal
            name = $name
            attributes = [int]$Definition.Attributes
            properties = (Read-Properties $Definition)
            fields = (Read-Fields $Definition)
            indexes = $indexes
            rows = $rows
        }
    } finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch {} }
        Release $recordset; Release $index; Release $collection
    }
}

function Read-Tables($Db) {
    $items = @()
    $tables = $definition = $null
    try {
        $tables = $Db.TableDefs
        for ($i = 0; $i -lt $tables.Count; $i++) {
            $definition = $tables.Item($i)
            if (-not ([string]$definition.Name).StartsWith('MSys')) { $items += (Read-Table $Db $definition $i) }
            Release $definition
            $definition = $null
        }
    } finally { Release $definition; Release $tables }
    , $items
}

function Read-Relations($Db) {
    $items = @()
    $relations = $relation = $fields = $field = $null
    try {
        $relations = $Db.Relations
        for ($i = 0; $i -lt $relations.Count; $i++) {
            $relation = $relations.Item($i)
            $keys = @()
            $fields = $relation.Fields
            for ($j = 0; $j -lt $fields.Count; $j++) {
                $field = $fields.Item($j)
                $keys += [ordered]@{
                    ordinal = $j
                    name = [string]$field.Name
                    foreign_name = [string]$field.ForeignName
                    properties = (Read-Properties $field)
                }
                Release $field
                $field = $null
            }
            Release $fields
            $fields = $null
            $items += [ordered]@{
                ordinal = $i
                name = [string]$relation.Name
                table = [string]$relation.Table
                foreign_table = [string]$relation.ForeignTable
                attributes = [int]$relation.Attributes
                properties = (Read-Properties $relation)
                fields = $keys
            }
            Release $relation
            $relation = $null
        }
    } finally { Release $field; Release $fields; Release $relation; Release $relations }
    , $items
}

function Read-Queries($Db) {
    $items = @()
    $queries = $query = $parameters = $parameter = $null
    try {
        $queries = $Db.QueryDefs
        for ($i = 0; $i -lt $queries.Count; $i++) {
            $query = $queries.Item($i)
            $values = @()
            $parameters = $query.Parameters
            for ($j = 0; $j -lt $parameters.Count; $j++) {
                $parameter = $parameters.Item($j)
                $values += [ordered]@{
                    ordinal = $j
                    name = [string]$parameter.Name
                    type = [int]$parameter.Type
                    direction = [int]$parameter.Direction
                    properties = (Read-Properties $parameter)
                }
                Release $parameter
                $parameter = $null
            }
            Release $parameters
            $parameters = $null
            $items += [ordered]@{
                ordinal = $i
                name = [string]$query.Name
                type = [int]$query.Type
                sql = [string]$query.SQL
                returns_records = [bool]$query.ReturnsRecords
                updatable = [bool]$query.Updatable
                parameters = $values
                properties = (Read-Properties $query)
            }
            Release $query
            $query = $null
        }
    } finally { Release $parameter; Release $parameters; Release $query; Release $queries }
    , $items
}

# Seek each key tuple on a table-type recordset; a miss records null.
function Read-Seeks($Db, $Probes) {
    $items = @()
    foreach ($probe in $Probes) {
        $recordset = $null
        try {
            $recordset = $Db.OpenRecordset([string]$probe.table, 1)
            $recordset.Index = [string]$probe.index
            foreach ($key in $probe.keys) {
                $values = @($key | ForEach-Object { Cell-Value $_ })
                $arguments = [object[]]::new($values.Count + 1)
                $arguments[0] = '='
                for ($i = 0; $i -lt $values.Count; $i++) { $arguments[$i + 1] = $values[$i] }
                [void]$recordset.GetType().InvokeMember('Seek', [Reflection.BindingFlags]::InvokeMethod, $null, $recordset, $arguments)
                $row = $null
                if (-not $recordset.NoMatch) { $row = Read-Row $recordset }
                $items += [ordered]@{ table = [string]$probe.table; index = [string]$probe.index; key = $key; row = $row }
            }
        } finally {
            if ($null -ne $recordset) { try { $recordset.Close() } catch {} }
            Release $recordset
        }
    }
    , $items
}

$options = $null
$optionsPath = Join-Path $PSScriptRoot 'observe.json'
if (Test-Path -LiteralPath $optionsPath) { $options = Read-Json $optionsPath }
$names = @(Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb' | Sort-Object Name | ForEach-Object { $_.Name })
if (Has $options 'files') { $names = @($options.files) }
$probes = @()
if (Has $options 'seeks') { $probes = @($options.seeks) }

$engine = New-Object -ComObject DAO.DBEngine.36
try { $environment = Environment-Record $engine } finally { Release $engine }
$result = [ordered]@{ environment = $environment; files = @() }
foreach ($name in $names) {
    $path = Join-Path $env:JET3_WORK $name
    $before = Identity $path
    $engine = $db = $properties = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($path, $false, $true)
        $item = [ordered]@{
            file = $name
            identity = $before
            database = [ordered]@{ version = [string]$db.Version; properties = (Read-Properties $db) }
            tables = (Read-Tables $db)
            relations = (Read-Relations $db)
            querydefs = (Read-Queries $db)
        }
        if ($probes.Count) { $item.seeks = Read-Seeks $db $probes }
        $result.files += $item
    } finally {
        if ($null -ne $db) { try { $db.Close() } catch {} }
        Release $db
        Release $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    if ((Identity $path).sha256 -cne $before.sha256) { throw ('Observation changed ' + $name) }
    Write-Output ('observed ' + $name)
}
Write-Json $result (Join-Path $env:JET3_OUTBOX 'observe-result.json')
