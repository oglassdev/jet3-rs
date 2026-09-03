[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$PlanSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$AlphaCandidatePath,
    [Parameter(Mandatory = $true)]
    [string]$IndexedCandidatePath,
    [Parameter(Mandatory = $true)]
    [string]$WideCandidatePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DbOpenSnapshot = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumTableDefs = 16
$MaximumFields = 80
$MaximumIndexes = 4
$MaximumIndexFields = 4
$MaximumDetailCharacters = 512
$MaximumDatabaseBytes = 80 * 2048
$ExpectedSystemTables = @("MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships")
$SchemaOrder = @("alpha", "indexed", "wide")
# EXP-0091, EXP-0093, and EXP-0105 observed these fresh DAO control sizes.
$ExpectedControlSizes = @{ alpha = 47104; indexed = 53248; wide = 141312 }
$ExpectedCandidates = @{
    "candidate_alpha" = [pscustomobject]@{
        size = 47104
        sha256 = "c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21"
    }
    "candidate_indexed" = [pscustomobject]@{
        size = 53248
        sha256 = "bb7e0d408a5e844dd0fbe6eae008a4ca31bd83f376e611339ad5f8385572835e"
    }
    "candidate_wide" = [pscustomobject]@{
        size = 49152
        sha256 = "81cfd7b86616f9928b71cab4398f26305d5dafdbe4bfa0a514e6f9b4146f1cf6"
    }
}

function Get-Schema {
    param([string]$Name)

    switch ($Name) {
        "alpha" { return [pscustomobject]@{ table = "Alpha"; fields = @("Id"); indexes = @() } }
        "indexed" {
            return [pscustomobject]@{
                table = "IdxTri"
                fields = @("Id", "Code", "Sequence")
                indexes = @(
                    [pscustomobject]@{
                        name = "ZPrimary"; primary = $true; unique = $true; required = $true
                        fields = @([pscustomobject]@{ name = "Id"; descending = $false })
                    },
                    [pscustomobject]@{
                        name = "MUniqueX"; primary = $false; unique = $true; required = $false
                        fields = @([pscustomobject]@{ name = "Code"; descending = $true })
                    },
                    [pscustomobject]@{
                        name = "ASecondx"; primary = $false; unique = $false; required = $false
                        fields = @([pscustomobject]@{ name = "Sequence"; descending = $false })
                    }
                )
            }
        }
        "wide" {
            $fields = New-Object Collections.ArrayList
            for ($ordinal = 0; $ordinal -lt 70; $ordinal++) {
                [void]$fields.Add(("F{0:D3}AAAAAA" -f $ordinal))
            }
            return [pscustomobject]@{ table = "ContOneX"; fields = @($fields); indexes = @() }
        }
        default { throw "Unknown schema $Name." }
    }
}

function Release-ComObject {
    param([object]$Value)

    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-Sha256 {
    param([string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BoundedIdentity {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 2048 -or ($item.Length % 2048) -ne 0 -or
        $item.Length -gt $MaximumDatabaseBytes) {
        throw "Image is outside the bounded regular 2 KiB page geometry."
    }
    return [pscustomobject]@{
        size = [long]$item.Length
        sha256 = Get-Sha256 -Path $Path
    }
}

function ConvertTo-BoundedDetail {
    param([AllowNull()][object]$Detail)

    $text = if ($null -eq $Detail) { "No additional detail was reported." } else { [string]$Detail }
    if ($text.Length -gt $MaximumDetailCharacters) {
        return $text.Substring(0, $MaximumDetailCharacters)
    }
    return $text
}

function ConvertTo-EndpointDetail {
    param(
        [string]$Path,
        [object]$ErrorRecord
    )

    $detail = $ErrorRecord.Exception.GetType().FullName + ": " + $ErrorRecord.Exception.Message
    $fullPath = [IO.Path]::GetFullPath($Path)
    return ConvertTo-BoundedDetail -Detail $detail.Replace($fullPath, "<DATABASE>")
}

function Write-JsonDocument {
    param([string]$Path, [object]$Document)

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 20) + "`n"),
        $encoding
    )
}

function Assert-ExactNames {
    param(
        [string[]]$Actual,
        [string[]]$Expected,
        [string]$What
    )

    $actualSorted = @($Actual | Sort-Object)
    $expectedSorted = @($Expected | Sort-Object)
    if (($actualSorted -join "`n") -cne ($expectedSorted -join "`n")) {
        throw "$What differs from the exact expected inventory."
    }
}

function Get-TableDefNames {
    param([object]$Database)

    $definitions = $null
    $names = New-Object Collections.ArrayList
    try {
        $definitions = $Database.TableDefs
        $count = [int]$definitions.Count
        if ($count -gt $MaximumTableDefs) {
            throw "DAO returned more than $MaximumTableDefs table definitions."
        }
        for ($index = 0; $index -lt $count; $index++) {
            $table = $null
            try {
                $table = $definitions.Item($index)
                [void]$names.Add([string]$table.Name)
            }
            finally {
                Release-ComObject -Value $table
            }
        }
    }
    finally {
        Release-ComObject -Value $definitions
    }
    return @($names | Sort-Object)
}

function Get-TableDocumentNames {
    param([object]$Database)

    $container = $null
    $documents = $null
    $names = New-Object Collections.ArrayList
    try {
        $container = $Database.Containers.Item("Tables")
        $documents = $container.Documents
        $count = [int]$documents.Count
        if ($count -gt $MaximumTableDefs) {
            throw "DAO returned more than $MaximumTableDefs table documents."
        }
        for ($index = 0; $index -lt $count; $index++) {
            $document = $null
            try {
                $document = $documents.Item($index)
                [void]$names.Add([string]$document.Name)
            }
            finally {
                Release-ComObject -Value $document
            }
        }
    }
    finally {
        Release-ComObject -Value $documents
        Release-ComObject -Value $container
    }
    return @($names | Sort-Object)
}

function Get-FieldShape {
    param([object]$Fields)

    $count = [int]$Fields.Count
    if ($count -gt $MaximumFields) {
        throw "DAO returned more than $MaximumFields fields."
    }
    $rows = New-Object Collections.ArrayList
    for ($index = 0; $index -lt $count; $index++) {
        $field = $null
        try {
            $field = $Fields.Item($index)
            [void]$rows.Add([ordered]@{
                name = [string]$field.Name
                type = [int]$field.Type
            })
        }
        finally {
            Release-ComObject -Value $field
        }
    }
    return @($rows)
}

function Get-IndexShape {
    param([object]$Indexes)

    $count = [int]$Indexes.Count
    if ($count -gt $MaximumIndexes) {
        throw "DAO returned more than $MaximumIndexes indexes."
    }
    $rows = New-Object Collections.ArrayList
    for ($index = 0; $index -lt $count; $index++) {
        $definition = $null
        $fields = $null
        try {
            $definition = $Indexes.Item($index)
            $fields = $definition.Fields
            $fieldCount = [int]$fields.Count
            if ($fieldCount -gt $MaximumIndexFields) {
                throw "DAO returned more than $MaximumIndexFields index fields."
            }
            $keys = New-Object Collections.ArrayList
            for ($position = 0; $position -lt $fieldCount; $position++) {
                $key = $null
                try {
                    $key = $fields.Item($position)
                    [void]$keys.Add([ordered]@{
                        name = [string]$key.Name
                        descending = (([long]$key.Attributes -band 1) -ne 0)
                    })
                }
                finally {
                    Release-ComObject -Value $key
                }
            }
            [void]$rows.Add([ordered]@{
                name = [string]$definition.Name
                primary = [bool]$definition.Primary
                unique = [bool]$definition.Unique
                required = [bool]$definition.Required
                fields = @($keys)
            })
        }
        finally {
            Release-ComObject -Value $fields
            Release-ComObject -Value $definition
        }
    }
    return @($rows)
}

function Test-Image {
    param(
        [string]$Path,
        [object]$Schema
    )

    $completed = New-Object Collections.ArrayList
    $snapshot = [ordered]@{}
    $engine = $null
    $database = $null
    $definitions = $null
    $target = $null
    $fields = $null
    $indexes = $null
    $recordset = $null
    $container = $null
    $documents = $null
    $document = $null
    $tableName = [string]$Schema.table
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $true)
        [void]$completed.Add("open_database")
        if ([string]$database.Version -cne "3.0") {
            throw "DAO did not report database version 3.0."
        }
        [void]$completed.Add("version")
        $expectedTables = @($tableName) + $ExpectedSystemTables
        $tabledefs = @(Get-TableDefNames -Database $database)
        Assert-ExactNames -Actual $tabledefs -Expected $expectedTables -What "$tableName TableDefs"
        $snapshot.tabledefs = $tabledefs
        [void]$completed.Add("tabledefs")

        $definitions = $database.TableDefs
        $target = $definitions.Item($tableName)
        if ([string]$target.Name -cne $tableName) {
            throw "Named $tableName TableDef lookup returned another object."
        }
        [void]$completed.Add("direct_lookup")

        $fields = $target.Fields
        $snapshot.fields = @(Get-FieldShape -Fields $fields)
        $expectedFields = @($Schema.fields)
        if ($snapshot.fields.Count -ne $expectedFields.Count) {
            throw "$tableName does not have exactly $($expectedFields.Count) fields."
        }
        for ($position = 0; $position -lt $expectedFields.Count; $position++) {
            $actual = $snapshot.fields[$position]
            if ([string]$actual.name -cne [string]$expectedFields[$position] -or [int]$actual.type -ne $DbLong) {
                throw "$tableName field $position is not the expected Long field."
            }
        }
        [void]$completed.Add("fields")

        $indexes = $target.Indexes
        $snapshot.indexes = @(Get-IndexShape -Indexes $indexes)
        if ($snapshot.indexes.Count -ne @($Schema.indexes).Count) {
            throw "$tableName does not have exactly $(@($Schema.indexes).Count) indexes."
        }
        [void]$completed.Add("indexes")

        $recordset = $database.OpenRecordset($tableName, $DbOpenSnapshot, 0)
        if (-not ([bool]$recordset.BOF -and [bool]$recordset.EOF)) {
            throw "$tableName snapshot is not empty."
        }
        [void]$completed.Add("snapshot")

        $container = $database.Containers.Item("Tables")
        $documents = $container.Documents
        $document = $documents.Item($tableName)
        if ([string]$document.Name -cne $tableName) {
            throw "Named $tableName table document lookup returned another object."
        }
        $snapshot.table_documents = @(Get-TableDocumentNames -Database $database)
        Assert-ExactNames -Actual $snapshot.table_documents -Expected $expectedTables -What "$tableName table documents"
        [void]$completed.Add("document")
        return [ordered]@{
            status = "pass"
            completed = @($completed)
            detail = "All bounded $tableName endpoints passed."
            snapshot = $snapshot
        }
    }
    catch {
        return [ordered]@{
            status = "fail"
            completed = @($completed)
            detail = ConvertTo-EndpointDetail -Path $Path -ErrorRecord $_
            snapshot = $snapshot
        }
    }
    finally {
        if ($null -ne $recordset) {
            try { $recordset.Close() } catch { }
        }
        Release-ComObject -Value $recordset
        Release-ComObject -Value $document
        Release-ComObject -Value $documents
        Release-ComObject -Value $container
        Release-ComObject -Value $indexes
        Release-ComObject -Value $fields
        Release-ComObject -Value $target
        Release-ComObject -Value $definitions
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Add-Index {
    param([object]$Table, [object]$Definition)

    $index = $null
    try {
        $index = $Table.CreateIndex([string]$Definition.name)
        $index.Primary = [bool]$Definition.primary
        $index.Unique = [bool]$Definition.unique
        $index.Required = [bool]$Definition.required
        foreach ($fieldDefinition in $Definition.fields) {
            $field = $null
            try {
                $field = $index.CreateField([string]$fieldDefinition.name)
                if ([bool]$fieldDefinition.descending) { $field.Attributes = 1 }
                $index.Fields.Append($field)
            }
            finally { Release-ComObject -Value $field }
        }
        $Table.Indexes.Append($index)
    }
    finally { Release-ComObject -Value $index }
}

function New-Control {
    param(
        [string]$Path,
        [object]$Schema,
        [ref]$MutationStarted
    )

    $engine = $null
    $workspace = $null
    $database = $null
    $table = $null
    $fields = New-Object Collections.ArrayList
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $MutationStarted.Value = $true
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        $table = $database.CreateTableDef([string]$Schema.table)
        foreach ($name in @($Schema.fields)) {
            $field = $table.CreateField($name, $DbLong)
            [void]$fields.Add($field)
            $table.Fields.Append($field)
        }
        foreach ($index in @($Schema.indexes)) { Add-Index -Table $table -Definition $index }
        $database.TableDefs.Append($table)
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        foreach ($field in $fields) { Release-ComObject -Value $field }
        Release-ComObject -Value $table
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Measure-Image {
    param(
        [string]$Path,
        [string]$Role,
        [string]$SchemaName
    )

    $before = Get-BoundedIdentity -Path $Path
    $sizeBefore = [long]$before.size
    $shaBefore = [string]$before.sha256
    if ($Role.StartsWith("control_", [StringComparison]::Ordinal)) {
        if ($sizeBefore -ne [long]$ExpectedControlSizes[$SchemaName]) {
            throw "Fresh DAO $SchemaName control is not the observed page count."
        }
    }
    else {
        $expected = $ExpectedCandidates[$Role]
        if ($null -eq $expected -or $sizeBefore -ne [long]$expected.size -or
            $shaBefore -cne [string]$expected.sha256) {
            throw "Guest-local candidate differs from its preregistered identity."
        }
    }
    $endpoints = Test-Image -Path $Path -Schema (Get-Schema -Name $SchemaName)
    $after = Get-BoundedIdentity -Path $Path
    return [ordered]@{
        role = $Role
        database = [IO.Path]::GetFileName($Path)
        size_before = $sizeBefore
        sha256_before = $shaBefore
        endpoints = $endpoints
        size_after = [long]$after.size
        sha256_after = [string]$after.sha256
    }
}

function New-FailedImageObservation {
    param(
        [string]$Path,
        [string]$Role,
        [string]$Detail
    )

    $identity = Get-BoundedIdentity -Path $Path
    $size = [long]$identity.size
    $sha256 = [string]$identity.sha256
    $sizeBefore = $size
    $sha256Before = $sha256
    if ($ExpectedCandidates.ContainsKey($Role)) {
        $sizeBefore = [long]$ExpectedCandidates[$Role].size
        $sha256Before = [string]$ExpectedCandidates[$Role].sha256
    }
    return [ordered]@{
        role = $Role
        database = [IO.Path]::GetFileName($Path)
        size_before = $sizeBefore
        sha256_before = $sha256Before
        endpoints = [ordered]@{
            status = "fail"
            completed = @()
            detail = ConvertTo-BoundedDetail -Detail $Detail
            snapshot = [ordered]@{}
        }
        size_after = $size
        sha256_after = $sha256
    }
}

$candidateSources = @{
    alpha = $AlphaCandidatePath
    indexed = $IndexedCandidatePath
    wide = $WideCandidatePath
}
[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$preparedPaths = @{}
foreach ($replica in 1..3) {
    $prepared = @{}
    foreach ($schema in $SchemaOrder) {
        $path = Join-Path $RunRoot "candidate-r$replica-$schema.mdb"
        Copy-Item -LiteralPath ([string]$candidateSources[$schema]) -Destination $path
        $expected = $ExpectedCandidates["candidate_$schema"]
        $identity = Get-BoundedIdentity -Path $path
        $size = [long]$identity.size
        $digest = [string]$identity.sha256
        if ($size -ne [long]$expected.size -or $digest -cne [string]$expected.sha256) {
            throw "Prepared candidate differs from its preregistered identity."
        }
        $prepared[$schema] = $path
    }
    $preparedPaths[$replica] = $prepared
}
$mutationStarted = $false
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    $images = New-Object Collections.ArrayList
    $artifacts = New-Object Collections.ArrayList
    foreach ($schema in $SchemaOrder) {
        [void]$artifacts.Add([pscustomobject]@{
            role = "candidate_$schema"; schema = $schema
            path = [string]$preparedPaths[$replica][$schema]
        })
    }
    foreach ($schema in $SchemaOrder) {
        [void]$artifacts.Add([pscustomobject]@{
            role = "control_$schema"; schema = $schema
            path = (Join-Path $RunRoot "control-r$replica-$schema.mdb")
        })
    }
    $state = [ordered]@{
        replica = $replica
        status = "fail"
        error = $null
        images = @()
    }
    try {
        foreach ($schema in $SchemaOrder) {
            New-Control -Path (Join-Path $RunRoot "control-r$replica-$schema.mdb") `
                -Schema (Get-Schema -Name $schema) -MutationStarted ([ref]$mutationStarted)
        }
        foreach ($artifact in $artifacts) {
            [void]$images.Add((Measure-Image -Path ([string]$artifact.path) -Role ([string]$artifact.role) `
                -SchemaName ([string]$artifact.schema)))
        }
        $state.images = @($images)
        $state.status = "pass"
    }
    catch {
        $failure = ConvertTo-BoundedDetail -Detail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
        foreach ($artifact in $artifacts) {
            $database = [IO.Path]::GetFileName([string]$artifact.path)
            $alreadyRecorded = @($images | Where-Object { [string]$_.database -ceq $database }).Count -ne 0
            try {
                if (-not $alreadyRecorded -and (Test-Path -LiteralPath $artifact.path -PathType Leaf)) {
                    [void]$images.Add((New-FailedImageObservation -Path ([string]$artifact.path) `
                        -Role ([string]$artifact.role) -Detail $failure))
                }
            }
            catch {
                $recovery = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
                $failure = ConvertTo-BoundedDetail -Detail `
                    ($failure + " Recovery observation failed for $database`: $recovery")
            }
        }
        $state.images = @($images)
        $state.error = $failure
    }
    [void]$replicas.Add($state)
}

$status = "pass"
foreach ($replica in $replicas) {
    if ([string]$replica.status -cne "pass") {
        $status = "fail"
    }
}
$result = [ordered]@{
    document_type = "dao_lvprop_null_schemas_job_result"
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    mutation_started = $mutationStarted
    replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "lvprop-null-schemas-job-result.json") -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
