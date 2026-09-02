[CmdletBinding()]
# SRC-0023 supplies checked DAO constants; EXP-0059 records this development-only job.
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,
    [Parameter(Mandatory = $true)]
    [string]$TypeInputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumTypes = 32
$MaximumTables = 32
$MaximumFields = 64
$MaximumIndexes = 32
$MaximumIndexFields = 16
$MaximumRelations = 32
$MaximumRelationFields = 16

function Write-JsonDocument {
    param([string]$Path, [object]$Document)

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 20) + "`n"),
        $encoding
    )
}

function Release-ComObject {
    param([object]$Value)

    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Invoke-WithDatabase {
    param([string]$Path, [scriptblock]$Action)

    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path)
        & $Action $database
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function New-Jet3Database {
    param([string]$Path)

    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Add-FieldToTable {
    param(
        [object]$Table,
        [string]$Name,
        [int]$Type,
        [Nullable[int]]$Size,
        [bool]$Required,
        [Nullable[long]]$Attributes
    )

    $field = $null
    try {
        if ($null -eq $Size) {
            $field = $Table.CreateField($Name, $Type)
        }
        else {
            $field = $Table.CreateField($Name, $Type, [int]$Size)
        }
        if ($null -ne $Attributes) {
            $field.Attributes = [long]$Attributes
        }
        $field.Required = $Required
        $Table.Fields.Append($field)
    }
    finally {
        Release-ComObject -Value $field
    }
}

function Test-TypeCandidate {
    param([object]$Candidate, [int]$Ordinal)

    $path = Join-Path $RunRoot ("type-probe-{0:D2}.mdb" -f $Ordinal)
    New-Jet3Database -Path $path
    $accepted = $false
    $hresult = $null
    try {
        Invoke-WithDatabase -Path $path -Action {
            param($database)
            $table = $null
            try {
                $table = $database.CreateTableDef("TypeProbe")
                $size = $null
                if ($null -ne $Candidate.PSObject.Properties["size"]) {
                    $size = [Nullable[int]]([int]$Candidate.size)
                }
                Add-FieldToTable -Table $table -Name "Value" `
                    -Type ([int]$Candidate.value) -Size $size -Required $false `
                    -Attributes $null
                $database.TableDefs.Append($table)
            }
            finally {
                Release-ComObject -Value $table
            }
        }
        $accepted = $true
    }
    catch {
        $hresult = [long]$_.Exception.HResult
    }
    finally {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force
        }
    }
    return [ordered]@{
        name = [string]$Candidate.name
        value = [int]$Candidate.value
        accepted = $accepted
        hresult = $hresult
    }
}

function Add-Table {
    param([object]$Database, [string]$Name, [array]$Fields)

    $table = $null
    try {
        $table = $Database.CreateTableDef($Name)
        foreach ($definition in $Fields) {
            Add-FieldToTable -Table $table -Name ([string]$definition.name) `
                -Type ([int]$definition.type) -Size $definition.size `
                -Required ([bool]$definition.required) -Attributes $definition.attributes
        }
        $Database.TableDefs.Append($table)
    }
    finally {
        Release-ComObject -Value $table
    }
}

function Add-Index {
    param(
        [object]$Database,
        [string]$TableName,
        [string]$Name,
        [bool]$Primary,
        [bool]$Unique,
        [bool]$Required,
        [array]$Fields
    )

    $table = $null
    $index = $null
    try {
        $table = $Database.TableDefs.Item($TableName)
        $index = $table.CreateIndex($Name)
        $index.Primary = $Primary
        $index.Unique = $Unique
        $index.Required = $Required
        foreach ($definition in $Fields) {
            $indexField = $null
            try {
                $indexField = $index.CreateField([string]$definition.name)
                if ([bool]$definition.descending) {
                    $indexField.Attributes = 1
                }
                $index.Fields.Append($indexField)
            }
            finally {
                Release-ComObject -Value $indexField
            }
        }
        $table.Indexes.Append($index)
    }
    finally {
        Release-ComObject -Value $index
        Release-ComObject -Value $table
    }
}

function Add-ProbeRelation {
    param([object]$Database)

    $relation = $null
    $field = $null
    try {
        $relation = $Database.CreateRelation(
            "ParentChild",
            "ParentProbe",
            "ChildProbe",
            4352
        )
        $field = $relation.CreateField("Id")
        $field.ForeignName = "ParentId"
        $relation.Fields.Append($field)
        $Database.Relations.Append($relation)
    }
    finally {
        Release-ComObject -Value $field
        Release-ComObject -Value $relation
    }
}

function Get-FieldSnapshot {
    param([object]$Fields, [int]$Maximum)

    $count = [int]$Fields.Count
    if ($count -gt $Maximum) {
        throw "DAO field count exceeds the bounded snapshot limit."
    }
    $items = New-Object Collections.ArrayList
    for ($index = 0; $index -lt $count; $index++) {
        $field = $null
        try {
            $field = $Fields.Item($index)
            [void]$items.Add([ordered]@{
                collection_ordinal = $index
                ordinal_position = [int]$field.OrdinalPosition
                name = [string]$field.Name
                type = [int]$field.Type
                size = [int]$field.Size
                attributes = [long]$field.Attributes
                required = [bool]$field.Required
            })
        }
        finally {
            Release-ComObject -Value $field
        }
    }
    return @($items)
}

function Get-IndexSnapshot {
    param([object]$Indexes)

    $count = [int]$Indexes.Count
    if ($count -gt $MaximumIndexes) {
        throw "DAO index count exceeds the bounded snapshot limit."
    }
    $items = New-Object Collections.ArrayList
    for ($indexOrdinal = 0; $indexOrdinal -lt $count; $indexOrdinal++) {
        $index = $null
        $fields = $null
        try {
            $index = $Indexes.Item($indexOrdinal)
            $fields = $index.Fields
            $fieldCount = [int]$fields.Count
            if ($fieldCount -gt $MaximumIndexFields) {
                throw "DAO index-field count exceeds the bounded snapshot limit."
            }
            $fieldItems = New-Object Collections.ArrayList
            for ($fieldOrdinal = 0; $fieldOrdinal -lt $fieldCount; $fieldOrdinal++) {
                $field = $null
                try {
                    $field = $fields.Item($fieldOrdinal)
                    [void]$fieldItems.Add([ordered]@{
                        ordinal = $fieldOrdinal
                        name = [string]$field.Name
                        attributes = [long]$field.Attributes
                    })
                }
                finally {
                    Release-ComObject -Value $field
                }
            }
            [void]$items.Add([ordered]@{
                ordinal = $indexOrdinal
                name = [string]$index.Name
                primary = [bool]$index.Primary
                unique = [bool]$index.Unique
                required = [bool]$index.Required
                ignore_nulls = [bool]$index.IgnoreNulls
                foreign = [bool]$index.Foreign
                fields = @($fieldItems)
            })
        }
        finally {
            Release-ComObject -Value $fields
            Release-ComObject -Value $index
        }
    }
    return @($items)
}

function Get-SchemaSnapshot {
    param([string]$Path)

    $snapshot = [ordered]@{ tables = @(); relations = @() }
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $tables = $null
        $relations = $null
        try {
            $tables = $database.TableDefs
            $tableCount = [int]$tables.Count
            if ($tableCount -gt $MaximumTables) {
                throw "DAO table count exceeds the bounded snapshot limit."
            }
            $tableItems = New-Object Collections.ArrayList
            for ($tableOrdinal = 0; $tableOrdinal -lt $tableCount; $tableOrdinal++) {
                $table = $null
                $fields = $null
                $indexes = $null
                try {
                    $table = $tables.Item($tableOrdinal)
                    if ([long]$table.Attributes -lt 0) {
                        continue
                    }
                    $fields = $table.Fields
                    $indexes = $table.Indexes
                    [void]$tableItems.Add([ordered]@{
                        ordinal = $tableOrdinal
                        name = [string]$table.Name
                        attributes = [long]$table.Attributes
                        fields = @(Get-FieldSnapshot -Fields $fields -Maximum $MaximumFields)
                        indexes = @(Get-IndexSnapshot -Indexes $indexes)
                    })
                }
                finally {
                    Release-ComObject -Value $indexes
                    Release-ComObject -Value $fields
                    Release-ComObject -Value $table
                }
            }
            $relations = $database.Relations
            $relationCount = [int]$relations.Count
            if ($relationCount -gt $MaximumRelations) {
                throw "DAO relation count exceeds the bounded snapshot limit."
            }
            $relationItems = New-Object Collections.ArrayList
            for ($relationOrdinal = 0; $relationOrdinal -lt $relationCount; $relationOrdinal++) {
                $relation = $null
                $fields = $null
                try {
                    $relation = $relations.Item($relationOrdinal)
                    $fields = $relation.Fields
                    $fieldCount = [int]$fields.Count
                    if ($fieldCount -gt $MaximumRelationFields) {
                        throw "DAO relation-field count exceeds the bounded snapshot limit."
                    }
                    $fieldItems = New-Object Collections.ArrayList
                    for ($fieldOrdinal = 0; $fieldOrdinal -lt $fieldCount; $fieldOrdinal++) {
                        $field = $null
                        try {
                            $field = $fields.Item($fieldOrdinal)
                            [void]$fieldItems.Add([ordered]@{
                                ordinal = $fieldOrdinal
                                name = [string]$field.Name
                                foreign_name = [string]$field.ForeignName
                            })
                        }
                        finally {
                            Release-ComObject -Value $field
                        }
                    }
                    [void]$relationItems.Add([ordered]@{
                        ordinal = $relationOrdinal
                        name = [string]$relation.Name
                        table = [string]$relation.Table
                        foreign_table = [string]$relation.ForeignTable
                        attributes = [long]$relation.Attributes
                        fields = @($fieldItems)
                    })
                }
                finally {
                    Release-ComObject -Value $fields
                    Release-ComObject -Value $relation
                }
            }
            $snapshot.tables = @($tableItems)
            $snapshot.relations = @($relationItems)
        }
        finally {
            Release-ComObject -Value $relations
            Release-ComObject -Value $tables
        }
    }
    return $snapshot
}

function Save-Checkpoint {
    param([string]$Source, [string]$Name)

    $schema = Get-SchemaSnapshot -Path $Source
    $fileName = "table-definition-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $item = Get-Item -LiteralPath $destination
    if (($item.Length % 2048) -ne 0) {
        throw "Table-definition checkpoint is not an exact sequence of 2 KiB pages."
    }
    return [ordered]@{
        name = $Name
        database = $fileName
        size = [long]$item.Length
        page_count = [long]($item.Length / 2048)
        schema = $schema
    }
}

$typeInput = Get-Content -LiteralPath $TypeInputPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$typeInput.schema_version -ne 1 -or
    [string]$typeInput.source_commit -cne "eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5" -or
    [string]$typeInput.source_sha256 -cne "51147cb927489b36583de4729355fccc78cc0781032453775f2a011f58535d7b" -or
    $typeInput.candidates.Count -lt 1 -or
    $typeInput.candidates.Count -gt $MaximumTypes) {
    throw "The checked DAO type inventory input is malformed."
}
$seenNames = @{}
$seenValues = @{}
foreach ($candidate in $typeInput.candidates) {
    $name = [string]$candidate.name
    $value = [int]$candidate.value
    if ($name -cnotmatch '^db[A-Za-z0-9]{1,40}$' -or
        $value -lt 1 -or $value -gt 255 -or
        $seenNames.ContainsKey($name) -or $seenValues.ContainsKey($value)) {
        throw "The checked DAO type inventory contains an invalid or duplicate candidate."
    }
    $seenNames[$name] = $true
    $seenValues[$value] = $true
}

$workingPath = Join-Path $RunRoot "table-definition-working.mdb"
$checkpoints = New-Object Collections.ArrayList
$typeResults = New-Object Collections.ArrayList
try {
    for ($ordinal = 0; $ordinal -lt $typeInput.candidates.Count; $ordinal++) {
        [void]$typeResults.Add((Test-TypeCandidate `
            -Candidate $typeInput.candidates[$ordinal] -Ordinal $ordinal))
    }

    New-Jet3Database -Path $workingPath
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "00-empty"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        $fields = New-Object Collections.ArrayList
        for ($ordinal = 0; $ordinal -lt $typeResults.Count; $ordinal++) {
            if (-not [bool]$typeResults[$ordinal].accepted) {
                continue
            }
            $candidate = $typeInput.candidates[$ordinal]
            $size = $null
            if ($null -ne $candidate.PSObject.Properties["size"]) {
                $size = [Nullable[int]]([int]$candidate.size)
            }
            [void]$fields.Add([pscustomobject]@{
                name = ("T{0:D2}_{1}" -f $ordinal, [string]$candidate.name)
                type = [int]$candidate.value
                size = $size
                required = $false
                attributes = $null
            })
        }
        Add-Table -Database $database -Name "TypeInventory" -Fields @($fields)
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "01-type-inventory"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Table -Database $database -Name "ColumnProbe" -Fields @(
            [pscustomobject]@{name="LongNullable";type=4;size=$null;required=$false;attributes=$null},
            [pscustomobject]@{name="LongRequired";type=4;size=$null;required=$true;attributes=$null},
            [pscustomobject]@{name="TextVariable";type=10;size=[Nullable[int]]13;required=$false;attributes=[Nullable[long]]2},
            [pscustomobject]@{name="TextFixed";type=10;size=[Nullable[int]]13;required=$false;attributes=[Nullable[long]]1},
            [pscustomobject]@{name="AutoLong";type=4;size=$null;required=$false;attributes=[Nullable[long]]16},
            [pscustomobject]@{name="GuidNullable";type=15;size=$null;required=$false;attributes=$null},
            [pscustomobject]@{name="GuidRequired";type=15;size=$null;required=$true;attributes=$null}
        )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "02-column-probe"))

    $boundaryPath = Join-Path $RunRoot "table-definition-boundary-working.mdb"
    New-Jet3Database -Path $boundaryPath
    Invoke-WithDatabase -Path $boundaryPath -Action {
        param($database)
        $fields = New-Object Collections.ArrayList
        for ($ordinal = 0; $ordinal -lt 64; $ordinal++) {
            $isVariable = (($ordinal % 2) -eq 1)
            [void]$fields.Add([pscustomobject]@{
                name = ("Boundary_{0:D2}_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" -f $ordinal)
                type = $(if ($isVariable) { 10 } else { 4 })
                size = $(if ($isVariable) { [Nullable[int]]31 } else { $null })
                required = $false
                attributes = $null
            })
        }
        Add-Table -Database $database -Name "BoundaryProbe" -Fields @($fields)
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $boundaryPath -Name "03-boundary-probe"))
    Remove-Item -LiteralPath $boundaryPath -Force

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Table -Database $database -Name "IndexProbe" -Fields @(
            [pscustomobject]@{name="Key";type=4;size=$null;required=$true;attributes=$null},
            [pscustomobject]@{name="Code";type=10;size=[Nullable[int]]12;required=$false;attributes=$null},
            [pscustomobject]@{name="Sequence";type=3;size=$null;required=$true;attributes=$null}
        )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "04-index-base"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Index -Database $database -TableName "IndexProbe" -Name "PrimaryKey" `
            -Primary $true -Unique $true -Required $true -Fields @(
                [pscustomobject]@{name="Key";descending=$false}
            )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "05-index-primary"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Index -Database $database -TableName "IndexProbe" -Name "UniqueComposite" `
            -Primary $false -Unique $true -Required $false -Fields @(
                [pscustomobject]@{name="Code";descending=$true},
                [pscustomobject]@{name="Sequence";descending=$false}
            )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "06-index-composite"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Index -Database $database -TableName "IndexProbe" -Name "RequiredNonUnique" `
            -Primary $false -Unique $false -Required $true -Fields @(
                [pscustomobject]@{name="Sequence";descending=$false}
            )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "07-index-required"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-Table -Database $database -Name "ParentProbe" -Fields @(
            [pscustomobject]@{name="Id";type=4;size=$null;required=$true;attributes=$null}
        )
        Add-Index -Database $database -TableName "ParentProbe" -Name "ParentPrimary" `
            -Primary $true -Unique $true -Required $true -Fields @(
                [pscustomobject]@{name="Id";descending=$false}
            )
        Add-Table -Database $database -Name "ChildProbe" -Fields @(
            [pscustomobject]@{name="ParentId";type=4;size=$null;required=$false;attributes=$null}
        )
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "08-relationship-base"))

    Invoke-WithDatabase -Path $workingPath -Action {
        param($database)
        Add-ProbeRelation -Database $database
    }
    [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Name "09-relationship-created"))

    $result = [ordered]@{
        development_only = $true
        status = "pass"
        detail = "Completed the bounded table-definition type, column, index, and relationship scenario."
        database_locale = $DatabaseLocale
        type_source_commit = [string]$typeInput.source_commit
        type_source_sha256 = [string]$typeInput.source_sha256
        type_results = @($typeResults)
        checkpoints = @($checkpoints)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "table-definition-job-result.json") `
        -Document $result
    exit 0
}
catch {
    $result = [ordered]@{
        development_only = $true
        status = "fail"
        detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
        database_locale = $DatabaseLocale
        type_source_commit = [string]$typeInput.source_commit
        type_source_sha256 = [string]$typeInput.source_sha256
        type_results = @($typeResults)
        checkpoints = @($checkpoints)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "table-definition-job-result.json") `
        -Document $result
    exit 1
}
