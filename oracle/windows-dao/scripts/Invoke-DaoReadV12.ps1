[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InventoryPath,
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [string]$ScenarioId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# DAO API constants recorded by SRC-0002, SRC-0003, SRC-0009, SRC-0014,
# and SRC-0030. They are adapter inputs, not MDB-format assertions.
$DaoTypes = @{
    dbBoolean = 1; dbByte = 2; dbInteger = 3; dbLong = 4; dbCurrency = 5
    dbSingle = 6; dbDouble = 7; dbDate = 8; dbBinary = 9; dbText = 10
    dbLongBinary = 11; dbMemo = 12; dbGUID = 15
}
$DaoTypeNames = @{}
foreach ($entry in $DaoTypes.GetEnumerator()) {
    $DaoTypeNames[[int]$entry.Value] = [string]$entry.Key
}
$DbVersion30 = 32
$DbVersion40 = 64
$DbEncrypt = 2
$DbAutoIncrField = 16
$DbSystemObject = -2147483646
$DbDescending = 1
$RelationUpdateCascade = 256
$RelationDeleteCascade = 4096
$MaximumGeneratedRows = 10000000
$Utf8 = New-Object Text.UTF8Encoding($false)
$CodePage = [Text.Encoding]::GetEncoding(
    1252,
    (New-Object Text.EncoderExceptionFallback),
    (New-Object Text.DecoderExceptionFallback)
)

function Release-ComObject {
    param([object]$Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Write-JsonDocument {
    param([string]$Path, [object]$Document)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 32 -Compress) + "`n"),
        $Utf8
    )
}

function Convert-ToLowerHex {
    param([byte[]]$Bytes)
    return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
}

function Convert-FromLowerHex {
    param([string]$Text)
    if ($Text -cnotmatch "^(?:[0-9a-f]{2})*$") {
        throw "Expected lowercase even-length hexadecimal text."
    }
    $bytes = New-Object byte[] ($Text.Length / 2)
    for ($index = 0; $index -lt $bytes.Length; $index++) {
        $bytes[$index] = [Convert]::ToByte($Text.Substring($index * 2, 2), 16)
    }
    return ,$bytes
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-DeclaredValue {
    param([object]$Plan, [string]$DaoType)
    $culture = [Globalization.CultureInfo]::InvariantCulture
    switch ([string]$Plan.encoding) {
        "null" { return $null }
        "boolean" { return [bool]$Plan.value }
        "integer" {
            switch ($DaoType) {
                "dbByte" { return [byte]$Plan.value }
                "dbInteger" { return [int16]$Plan.value }
                "dbLong" { return [int32]$Plan.value }
                default { return [long]$Plan.value }
            }
        }
        "invariant_decimal" {
            return [decimal]::Parse([string]$Plan.value, $culture)
        }
        "ieee_bits_hex" {
            $bytes = Convert-FromLowerHex -Text ([string]$Plan.value)
            if ($DaoType -ceq "dbSingle" -and $bytes.Length -eq 4) {
                [Array]::Reverse($bytes)
                return [BitConverter]::ToSingle($bytes, 0)
            }
            if ($DaoType -ceq "dbDouble" -and $bytes.Length -eq 8) {
                [Array]::Reverse($bytes)
                return [BitConverter]::ToDouble($bytes, 0)
            }
            throw "IEEE bit width does not match the declared DAO type."
        }
        "invariant_datetime" {
            return [datetime]::Parse(
                [string]$Plan.value,
                $culture,
                [Globalization.DateTimeStyles]::AllowWhiteSpaces
            )
        }
        "lowercase_hex" { return ,(Convert-FromLowerHex -Text ([string]$Plan.value)) }
        "unicode_string" { return [string]$Plan.value }
        "repeat_byte" {
            $unit = [Convert]::ToByte([string]$Plan.value.unit, 16)
            $bytes = New-Object byte[] ([int]$Plan.value.length)
            for ($index = 0; $index -lt $bytes.Length; $index++) { $bytes[$index] = $unit }
            return ,$bytes
        }
        "repeat_ascii" { return ([string]$Plan.value.unit) * [int]$Plan.value.length }
        "guid" { return [guid]([string]$Plan.value) }
        default { throw "Unsupported value encoding $($Plan.encoding)." }
    }
}

function Set-RecordsetValue {
    param([object]$Recordset, [object]$Plan, [hashtable]$FieldTypes)
    $field = $null
    try {
        $field = $Recordset.Fields.Item([string]$Plan.field)
        $daoType = [string]$FieldTypes[[string]$Plan.field]
        $value = Get-DeclaredValue -Plan $Plan -DaoType $daoType
        if ($null -eq $value) {
            $field.Value = [DBNull]::Value
        }
        elseif ($daoType -ceq "dbLongBinary") {
            $field.AppendChunk([byte[]]$value)
        }
        elseif ($daoType -ceq "dbMemo") {
            $field.AppendChunk([string]$value)
        }
        else {
            $field.Value = $value
        }
    }
    finally { Release-ComObject -Value $field }
}

function Add-RecipeRows {
    param(
        [object]$Database,
        [string]$TableName,
        [object[]]$Rows,
        [int]$Repeat,
        [string]$DatabasePath,
        [int]$TargetPageCount = 0
    )
    $recordset = $null
    try {
        $recordset = $Database.OpenRecordset($TableName)
        $types = @{}
        for ($ordinal = 0; $ordinal -lt $recordset.Fields.Count; $ordinal++) {
            $field = $null
            try {
                $field = $recordset.Fields.Item($ordinal)
                $types[[string]$field.Name] = $DaoTypeNames[[int]$field.Type]
            }
            finally { Release-ComObject -Value $field }
        }
        $generated = 0
        do {
            foreach ($values in $Rows) {
                $recordset.AddNew()
                foreach ($plan in $values) {
                    Set-RecordsetValue -Recordset $recordset -Plan $plan -FieldTypes $types
                }
                $recordset.Update()
                $generated++
                if ($generated -gt $MaximumGeneratedRows) {
                    throw "The DAO row-generation ceiling was exceeded."
                }
                if ($TargetPageCount -gt 0) {
                    $pages = [int]((Get-Item -LiteralPath $DatabasePath).Length / 2048)
                    if ($pages -gt $TargetPageCount) {
                        throw "DAO overshot the exact page-count target."
                    }
                    if ($pages -eq $TargetPageCount) { return }
                }
            }
            $Repeat--
        } while ($TargetPageCount -gt 0 -or $Repeat -gt 0)
        if ($TargetPageCount -gt 0) {
            throw "DAO did not reach the exact page-count target."
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
    }
}

function New-RecipeTable {
    param([object]$Database, [object]$Step)
    $table = $null
    try {
        $table = $Database.CreateTableDef([string]$Step.name)
        foreach ($fieldPlan in $Step.fields) {
            $field = $null
            try {
                $type = [int]$DaoTypes[[string]$fieldPlan.dao_type]
                if ($null -ne $fieldPlan.size) {
                    $field = $table.CreateField([string]$fieldPlan.name, $type, [int]$fieldPlan.size)
                }
                else {
                    $field = $table.CreateField([string]$fieldPlan.name, $type)
                }
                $field.Required = [bool]$fieldPlan.required
                $table.Fields.Append($field)
            }
            finally { Release-ComObject -Value $field }
        }
        foreach ($indexPlan in $Step.indexes) {
            $index = $null
            try {
                $index = $table.CreateIndex([string]$indexPlan.name)
                $index.Primary = [bool]$indexPlan.primary
                $index.Unique = [bool]$indexPlan.unique
                $index.Required = [bool]$indexPlan.required
                $index.IgnoreNulls = [bool]$indexPlan.ignore_nulls
                foreach ($fieldPlan in $indexPlan.fields) {
                    $field = $null
                    try {
                        $field = $index.CreateField([string]$fieldPlan.name)
                        if ([bool]$fieldPlan.descending) { $field.Attributes = $DbDescending }
                        $index.Fields.Append($field)
                    }
                    finally { Release-ComObject -Value $field }
                }
                $table.Indexes.Append($index)
            }
            finally { Release-ComObject -Value $index }
        }
        $Database.TableDefs.Append($table)
    }
    finally { Release-ComObject -Value $table }
}

function New-RecipeRelationship {
    param([object]$Database, [object]$Step)
    $attributes = 0
    if ([bool]$Step.cascade_updates) { $attributes += $RelationUpdateCascade }
    if ([bool]$Step.cascade_deletes) { $attributes += $RelationDeleteCascade }
    $relation = $null
    try {
        $relation = $Database.CreateRelation(
            [string]$Step.name,
            [string]$Step.table,
            [string]$Step.foreign_table,
            $attributes
        )
        foreach ($fieldPlan in $Step.fields) {
            $field = $null
            try {
                $field = $relation.CreateField([string]$fieldPlan.field)
                $field.ForeignName = [string]$fieldPlan.foreign_field
                $relation.Fields.Append($field)
            }
            finally { Release-ComObject -Value $field }
        }
        $Database.Relations.Append($relation)
    }
    finally { Release-ComObject -Value $relation }
}

function Remove-RecipeRows {
    param([object]$Database, [string]$TableName, [object]$Count)
    $recordset = $null
    try {
        $recordset = $Database.OpenRecordset($TableName)
        $remaining = if ([string]$Count -ceq "all") { [int]::MaxValue } else { [int]$Count }
        if (-not $recordset.EOF) { $recordset.MoveFirst() }
        while (-not $recordset.EOF -and $remaining -gt 0) {
            $recordset.Delete()
            $remaining--
            $recordset.MoveNext()
        }
        if ([string]$Count -cne "all" -and $remaining -ne 0) {
            throw "The delete step requested more rows than DAO returned."
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
    }
}

function Get-DateText {
    param([datetime]$Date)
    $text = $Date.ToString("yyyy-MM-ddTHH:mm:ss.fffffff", [Globalization.CultureInfo]::InvariantCulture)
    return $text.TrimEnd("0").TrimEnd(".")
}

function Get-TypedValue {
    param([object]$Field)
    $raw = $Field.Value
    if ($null -eq $raw -or [Convert]::IsDBNull($raw)) {
        return [ordered]@{ kind = "null"; value = $null }
    }
    switch ([int]$Field.Type) {
        1 { return [ordered]@{ kind = "boolean"; value = [bool]$raw } }
        2 { return [ordered]@{ kind = "byte"; raw_hex = Convert-ToLowerHex ([byte[]]@([byte]$raw)); value = [int][byte]$raw } }
        3 { return [ordered]@{ kind = "integer"; raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes([int16]$raw)); value = [int][int16]$raw } }
        4 { return [ordered]@{ kind = "long"; raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes([int32]$raw)); value = [long][int32]$raw } }
        5 {
            $decimal = [decimal]$raw
            $scaled = [decimal]::ToInt64($decimal * [decimal]10000)
            return [ordered]@{
                kind = "currency"
                raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes([int64]$scaled))
                value = $decimal.ToString("0.0000", [Globalization.CultureInfo]::InvariantCulture)
            }
        }
        6 {
            $number = [single]$raw
            return [ordered]@{
                kind = "single"
                raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes($number))
                value = $number.ToString("R", [Globalization.CultureInfo]::InvariantCulture)
            }
        }
        7 {
            $number = [double]$raw
            return [ordered]@{
                kind = "double"
                raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes($number))
                value = $number.ToString("R", [Globalization.CultureInfo]::InvariantCulture)
            }
        }
        8 {
            $date = [datetime]$raw
            return [ordered]@{
                kind = "datetime"
                raw_hex = Convert-ToLowerHex ([BitConverter]::GetBytes([double]$date.ToOADate()))
                value = Get-DateText -Date $date
            }
        }
        9 {
            $bytes = [byte[]]$raw
            return [ordered]@{ kind = "binary"; raw_hex = Convert-ToLowerHex $bytes; value = Convert-ToLowerHex $bytes }
        }
        10 {
            $text = [string]$raw
            return [ordered]@{ code_page = 1252; kind = "text"; raw_hex = Convert-ToLowerHex ($CodePage.GetBytes($text)); value = $text }
        }
        11 {
            $bytes = [byte[]]$raw
            return [ordered]@{ kind = "ole"; raw_hex = Convert-ToLowerHex $bytes; value = Convert-ToLowerHex $bytes }
        }
        12 {
            $text = [string]$raw
            return [ordered]@{ code_page = 1252; kind = "memo"; raw_hex = Convert-ToLowerHex ($CodePage.GetBytes($text)); value = $text }
        }
        15 {
            $guid = [guid]$raw
            return [ordered]@{ kind = "guid"; raw_hex = Convert-ToLowerHex ($guid.ToByteArray()); value = $guid.ToString("D").ToLowerInvariant() }
        }
        default { throw "DAO returned unsupported field type $($Field.Type)." }
    }
}

function Get-NormalizedSize {
    param([int]$Type, [int]$Declared)
    switch ($Type) {
        1 { return 1 }; 2 { return 1 }; 3 { return 2 }; 4 { return 4 }
        5 { return 8 }; 6 { return 4 }; 7 { return 8 }; 8 { return 8 }
        15 { return 16 }; 11 { return 0 }; 12 { return 0 }
        default { return $Declared }
    }
}

function Get-DaoSnapshot {
    param([object]$Database, [string]$Scenario, [string]$Revision, [string]$DatabaseHash)
    $tables = New-Object Collections.ArrayList
    for ($tableOrdinal = 0; $tableOrdinal -lt $Database.TableDefs.Count; $tableOrdinal++) {
        $table = $null
        try {
            $table = $Database.TableDefs.Item($tableOrdinal)
            if (([int]$table.Attributes -band $DbSystemObject) -ne 0) { continue }
            $columns = New-Object Collections.ArrayList
            for ($fieldOrdinal = 0; $fieldOrdinal -lt $table.Fields.Count; $fieldOrdinal++) {
                $field = $null
                try {
                    $field = $table.Fields.Item($fieldOrdinal)
                    $type = [int]$field.Type
                    $attributes = if ($type -in @(1,2,3,4,5,6,7,8,15)) { 1 } else { 2 }
                    $auto = (([int]$field.Attributes -band $DbAutoIncrField) -ne 0)
                    if ($auto) { $attributes += $DbAutoIncrField }
                    [void]$columns.Add([ordered]@{
                        attributes = $attributes
                        auto_increment = $auto
                        dao_type = [string]$DaoTypeNames[$type]
                        name = [string]$field.Name
                        ordinal = [int]$field.OrdinalPosition
                        properties = [ordered]@{}
                        size = Get-NormalizedSize -Type $type -Declared ([int]$field.Size)
                    })
                }
                finally { Release-ComObject -Value $field }
            }
            $indexes = New-Object Collections.ArrayList
            for ($indexOrdinal = 0; $indexOrdinal -lt $table.Indexes.Count; $indexOrdinal++) {
                $index = $null
                try {
                    $index = $table.Indexes.Item($indexOrdinal)
                    $fields = New-Object Collections.ArrayList
                    for ($fieldOrdinal = 0; $fieldOrdinal -lt $index.Fields.Count; $fieldOrdinal++) {
                        $field = $null
                        try {
                            $field = $index.Fields.Item($fieldOrdinal)
                            [void]$fields.Add([ordered]@{
                                descending = (([int]$field.Attributes -band $DbDescending) -ne 0)
                                name = [string]$field.Name
                            })
                        }
                        finally { Release-ComObject -Value $field }
                    }
                    [void]$indexes.Add([ordered]@{
                        fields = @($fields)
                        name = [string]$index.Name
                        primary = [bool]$index.Primary
                        properties = [ordered]@{}
                        required = [bool]$index.Required
                        unique = [bool]$index.Unique
                    })
                }
                finally { Release-ComObject -Value $index }
            }
            $rows = New-Object Collections.ArrayList
            $recordset = $null
            try {
                $recordset = $Database.OpenRecordset([string]$table.Name)
                if (-not $recordset.EOF) { $recordset.MoveFirst() }
                while (-not $recordset.EOF) {
                    $values = [ordered]@{}
                    foreach ($column in @($columns | Sort-Object ordinal)) {
                        $field = $null
                        try {
                            $field = $recordset.Fields.Item([string]$column.name)
                            $values[[string]$column.name] = Get-TypedValue -Field $field
                        }
                        finally { Release-ComObject -Value $field }
                    }
                    [void]$rows.Add([ordered]@{
                        canonical_key = ("0" * 64)
                        duplicate_ordinal = 0
                        values = $values
                    })
                    $recordset.MoveNext()
                }
            }
            finally {
                if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
                Release-ComObject -Value $recordset
            }
            [void]$tables.Add([ordered]@{
                attributes = [int]$table.Attributes
                columns = @($columns | Sort-Object ordinal)
                indexes = @($indexes | Sort-Object name)
                kind = "user"
                name = [string]$table.Name
                properties = [ordered]@{}
                rows = @($rows)
            })
        }
        finally { Release-ComObject -Value $table }
    }
    $relationships = New-Object Collections.ArrayList
    for ($relationOrdinal = 0; $relationOrdinal -lt $Database.Relations.Count; $relationOrdinal++) {
        $relation = $null
        try {
            $relation = $Database.Relations.Item($relationOrdinal)
            $fields = New-Object Collections.ArrayList
            for ($fieldOrdinal = 0; $fieldOrdinal -lt $relation.Fields.Count; $fieldOrdinal++) {
                $field = $null
                try {
                    $field = $relation.Fields.Item($fieldOrdinal)
                    [void]$fields.Add([ordered]@{
                        field = [string]$field.Name
                        foreign_field = [string]$field.ForeignName
                    })
                }
                finally { Release-ComObject -Value $field }
            }
            [void]$relationships.Add([ordered]@{
                attributes = [int]$relation.Attributes
                fields = @($fields)
                foreign_table = [string]$relation.ForeignTable
                name = [string]$relation.Name
                properties = [ordered]@{}
                table = [string]$relation.Table
            })
        }
        finally { Release-ComObject -Value $relation }
    }
    return [ordered]@{
        comparison_projection = @("/producer", "/producer_extensions")
        database_properties = [ordered]@{}
        database_sha256 = $DatabaseHash
        document_type = "canonical_semantic_snapshot"
        ordering = [ordered]@{
            columns = "ordinal_ascending"
            indexes = "name_codepoint_ascending"
            object_keys = "unicode_codepoint_ascending"
            objects = "name_codepoint_ascending"
            relationships = "name_codepoint_ascending"
            rows = "values_sha256_then_duplicate_ordinal"
        }
        producer = [ordered]@{ kind = "dao"; source_revision = $Revision }
        producer_extensions = [ordered]@{}
        protocol_version = "1.2.0"
        raw_preservation = @()
        relationships = @($relationships | Sort-Object name)
        scenario_id = $Scenario
        tables = @($tables | Sort-Object name)
    }
}

function Invoke-Scenario {
    param([object]$Scenario, [object]$AcceptedProvider)
    $scenarioRoot = Join-Path $OutputRoot ([string]$Scenario.id)
    [IO.Directory]::CreateDirectory($scenarioRoot) | Out-Null
    $databasePath = Join-Path $scenarioRoot "database.mdb"
    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $type = [Type]::GetTypeFromProgID([string]$AcceptedProvider.prog_id, $false)
        if ($null -eq $type) { throw "Accepted DAO provider is unavailable." }
        $actualClsid = "{" + $type.GUID.ToString().ToUpperInvariant() + "}"
        if ($actualClsid -ine [string]$AcceptedProvider.clsid) {
            throw "The active DAO registration differs from the probed provider."
        }
        $engine = [Activator]::CreateInstance($type)
        if ([string]$engine.Version -cne [string]$AcceptedProvider.provider_version) {
            throw "The active DAO version differs from the probed provider."
        }
        $workspace = $engine.Workspaces.Item(0)
        $recipe = $Scenario.generator_recipe
        foreach ($step in $recipe.steps) {
            switch ([string]$step.action) {
                "create_database" {
                    $version = if ([string]$recipe.database_version -ceq "dbVersion30") { $DbVersion30 } else { $DbVersion40 }
                    if ([bool]$recipe.encrypted) { $version += $DbEncrypt }
                    $database = $workspace.CreateDatabase($databasePath, [string]$recipe.locale, $version)
                    if ($null -ne $recipe.password) { $database.NewPassword("", [string]$recipe.password) }
                }
                "create_table" { New-RecipeTable -Database $database -Step $step }
                "create_relationship" { New-RecipeRelationship -Database $database -Step $step }
                "insert_rows" {
                    Add-RecipeRows -Database $database -TableName ([string]$step.table) `
                        -Rows @($step.rows) -Repeat ([int]$step.repeat) `
                        -DatabasePath $databasePath
                }
                "insert_until_page_count" {
                    Add-RecipeRows -Database $database -TableName ([string]$step.table) `
                        -Rows @(,@($step.row)) -Repeat 1 -DatabasePath $databasePath `
                        -TargetPageCount ([int]$step.page_count)
                }
                "delete_rows" { Remove-RecipeRows -Database $database -TableName ([string]$step.table) -Count $step.count }
                "drop_table" { $database.TableDefs.Delete([string]$step.name) }
                "reopen" {
                    $database.Close(); Release-ComObject -Value $database; $database = $null
                    $connect = if ($null -eq $recipe.password) { "" } else { ";PWD=$($recipe.password)" }
                    $database = $engine.OpenDatabase($databasePath, $false, $false, $connect)
                }
                "close_database" { $database.Close(); Release-ComObject -Value $database; $database = $null }
                default { throw "Unsupported recipe action $($step.action)." }
            }
        }
        if ($null -ne $database) { $database.Close(); Release-ComObject -Value $database; $database = $null }
        $expectedError = ([string]$Scenario.operation.expected_outcome -ceq "expected_error")
        if (-not $expectedError) {
            $database = $engine.OpenDatabase($databasePath)
            $snapshot = Get-DaoSnapshot -Database $database -Scenario ([string]$Scenario.id) `
                -Revision $SourceRevision -DatabaseHash ("0" * 64)
            $database.Close(); Release-ComObject -Value $database; $database = $null
            $databaseHash = Get-FileSha256 -Path $databasePath
            $snapshot.database_sha256 = $databaseHash
            Write-JsonDocument -Path (Join-Path $scenarioRoot "dao-snapshot.raw.json") -Document $snapshot
        }
        else {
            $databaseHash = Get-FileSha256 -Path $databasePath
        }
        return [ordered]@{
            database = ([string]$Scenario.id + "/database.mdb")
            database_sha256 = $databaseHash
            expected_error = $expectedError
            scenario_id = [string]$Scenario.id
            snapshot = if ($expectedError) { $null } else { ([string]$Scenario.id + "/dao-snapshot.raw.json") }
        }
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

$inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
$environment = Get-Content -LiteralPath $EnvironmentPath -Raw | ConvertFrom-Json
if ([IntPtr]::Size -ne 4) { throw "The DAO producer must run in an x86 process." }
if ([string]$inventory.protocol_version -cne "1.2.0") { throw "Inventory is not protocol 1.2.0." }
if ([string]$environment.status -cne "ready" -or $null -eq $environment.accepted_provider) {
    throw "The provider environment is not ready."
}
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$selected = @($inventory.scenarios | Where-Object {
    [string]::IsNullOrEmpty($ScenarioId) -or [string]$_.id -ceq $ScenarioId
})
if ($selected.Count -eq 0) { throw "No inventory scenario matched the selection." }
$results = New-Object Collections.ArrayList
foreach ($scenario in $selected) {
    [void]$results.Add((Invoke-Scenario -Scenario $scenario -AcceptedProvider $environment.accepted_provider))
}
Write-JsonDocument -Path (Join-Path $OutputRoot "dao-manifest.raw.json") -Document ([ordered]@{
    document_type = "dao_read_manifest"
    protocol_version = "1.2.0"
    source_revision = $SourceRevision
    scenarios = @($results)
})
