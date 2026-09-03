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
    [string]$QuadCandidatePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DbText = 10
$DbMemo = 12
$DbOpenSnapshot = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumTableDefs = 16
$MaximumFields = 16
$MaximumIndexes = 4
$MaximumIndexFields = 4
$MaximumDetailCharacters = 512
$MaximumDatabaseBytes = 64 * 2048
$ExpectedSystemTables = @("MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships")
# EXP-0087 observed this fresh DAO size after the four creates.
$ExpectedControlSize = 63488
$ExpectedCandidate = [pscustomobject]@{
    size = 63488
    sha256 = "f4bad46de7c24ba92c0c9472d128eed48a2dbf1469594372d1098068940545ee"
}

# The exact EXP-0087 Alpha, Beta, Gamma, and Delta schemas in create order.
function Get-Tables {
    return @(
        [pscustomobject]@{
            name = "Alpha"
            fields = @([pscustomobject]@{ name = "Id"; type = $DbLong; size = 0 })
            indexes = @()
        },
        [pscustomobject]@{
            name = "Beta"
            fields = @(
                [pscustomobject]@{ name = "Id"; type = $DbLong; size = 0 },
                [pscustomobject]@{ name = "Name"; type = $DbText; size = 50 },
                [pscustomobject]@{ name = "Note"; type = $DbMemo; size = 0 }
            )
            indexes = @()
        },
        [pscustomobject]@{
            name = "Gamma"
            fields = @([pscustomobject]@{ name = "Id"; type = $DbLong; size = 0 })
            indexes = @(
                [pscustomobject]@{
                    name = "PrimaryKey"; primary = $true; unique = $true
                    fields = @([pscustomobject]@{ name = "Id"; descending = $false })
                }
            )
        },
        [pscustomobject]@{
            name = "Delta"
            fields = @([pscustomobject]@{ name = "Label"; type = $DbText; size = 30 })
            indexes = @(
                [pscustomobject]@{
                    name = "ByLabel"; primary = $false; unique = $false
                    fields = @([pscustomobject]@{ name = "Label"; descending = $false })
                }
            )
        }
    )
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
                size = [int]$field.Size
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

# Expected DAO field size: Long reports 4, Text its declared length, Memo 0.
function Get-ExpectedFieldSize {
    param([object]$Field)

    if ([int]$Field.type -eq $DbLong) { return 4 }
    return [int]$Field.size
}

function Test-Image {
    param([string]$Path)

    $completed = New-Object Collections.ArrayList
    $snapshot = [ordered]@{}
    $engine = $null
    $database = $null
    $definitions = $null
    $container = $null
    $documents = $null
    $targets = New-Object Collections.ArrayList
    $tables = @(Get-Tables)
    $userNames = @($tables | ForEach-Object { [string]$_.name })
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $true)
        [void]$completed.Add("open_database")
        if ([string]$database.Version -cne "3.0") {
            throw "DAO did not report database version 3.0."
        }
        [void]$completed.Add("version")
        $expectedTables = $userNames + $ExpectedSystemTables
        $tabledefs = @(Get-TableDefNames -Database $database)
        Assert-ExactNames -Actual $tabledefs -Expected $expectedTables -What "TableDefs"
        $snapshot.tabledefs = $tabledefs
        [void]$completed.Add("tabledefs")

        $definitions = $database.TableDefs
        foreach ($table in $tables) {
            $tableName = [string]$table.name
            $target = $definitions.Item($tableName)
            [void]$targets.Add($target)
            if ([string]$target.Name -cne $tableName) {
                throw "Named $tableName TableDef lookup returned another object."
            }
        }
        [void]$completed.Add("direct_lookup")

        $tableShapes = New-Object Collections.ArrayList
        for ($position = 0; $position -lt $tables.Count; $position++) {
            $table = $tables[$position]
            $tableName = [string]$table.name
            $fields = $null
            try {
                $fields = $targets[$position].Fields
                $shape = @(Get-FieldShape -Fields $fields)
            }
            finally {
                Release-ComObject -Value $fields
            }
            $expectedFields = @($table.fields)
            if ($shape.Count -ne $expectedFields.Count) {
                throw "$tableName does not have exactly $($expectedFields.Count) fields."
            }
            for ($ordinal = 0; $ordinal -lt $expectedFields.Count; $ordinal++) {
                $actual = $shape[$ordinal]
                $expected = $expectedFields[$ordinal]
                if ([string]$actual.name -cne [string]$expected.name -or
                    [int]$actual.type -ne [int]$expected.type -or
                    [int]$actual.size -ne (Get-ExpectedFieldSize -Field $expected)) {
                    throw "$tableName field $ordinal is not the expected field."
                }
            }
            [void]$tableShapes.Add([ordered]@{ name = $tableName; fields = $shape; indexes = @() })
        }
        [void]$completed.Add("fields")

        for ($position = 0; $position -lt $tables.Count; $position++) {
            $table = $tables[$position]
            $indexes = $null
            try {
                $indexes = $targets[$position].Indexes
                $shape = @(Get-IndexShape -Indexes $indexes)
            }
            finally {
                Release-ComObject -Value $indexes
            }
            if ($shape.Count -ne @($table.indexes).Count) {
                throw "$([string]$table.name) does not have exactly $(@($table.indexes).Count) indexes."
            }
            $tableShapes[$position].indexes = $shape
        }
        $snapshot.tables = @($tableShapes)
        [void]$completed.Add("indexes")

        foreach ($table in $tables) {
            $tableName = [string]$table.name
            $recordset = $null
            try {
                $recordset = $database.OpenRecordset($tableName, $DbOpenSnapshot, 0)
                if (-not ([bool]$recordset.BOF -and [bool]$recordset.EOF)) {
                    throw "$tableName snapshot is not empty."
                }
            }
            finally {
                if ($null -ne $recordset) {
                    try { $recordset.Close() } catch { }
                }
                Release-ComObject -Value $recordset
            }
        }
        [void]$completed.Add("snapshot")

        $container = $database.Containers.Item("Tables")
        $documents = $container.Documents
        foreach ($table in $tables) {
            $tableName = [string]$table.name
            $document = $null
            try {
                $document = $documents.Item($tableName)
                if ([string]$document.Name -cne $tableName) {
                    throw "Named $tableName table document lookup returned another object."
                }
            }
            finally {
                Release-ComObject -Value $document
            }
        }
        $snapshot.table_documents = @(Get-TableDocumentNames -Database $database)
        Assert-ExactNames -Actual $snapshot.table_documents -Expected $expectedTables -What "Table documents"
        [void]$completed.Add("document")
        return [ordered]@{
            status = "pass"
            completed = @($completed)
            detail = "All bounded multi-table endpoints passed."
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
        Release-ComObject -Value $documents
        Release-ComObject -Value $container
        if ($null -ne $targets) {
            foreach ($target in $targets) { Release-ComObject -Value $target }
        }
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
        if ([bool]$Definition.primary) {
            $index.Primary = $true
            $index.Unique = $true
        }
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

function Add-Table {
    param([object]$Database, [object]$Definition)

    $table = $null
    $fields = New-Object Collections.ArrayList
    try {
        $table = $Database.CreateTableDef([string]$Definition.name)
        foreach ($fieldDefinition in @($Definition.fields)) {
            $field = if ([int]$fieldDefinition.type -eq $DbText) {
                $table.CreateField([string]$fieldDefinition.name, $DbText, [int]$fieldDefinition.size)
            }
            else {
                $table.CreateField([string]$fieldDefinition.name, [int]$fieldDefinition.type)
            }
            [void]$fields.Add($field)
            $table.Fields.Append($field)
        }
        foreach ($index in @($Definition.indexes)) { Add-Index -Table $table -Definition $index }
        $Database.TableDefs.Append($table)
    }
    finally {
        foreach ($field in $fields) { Release-ComObject -Value $field }
        Release-ComObject -Value $table
    }
}

# Creates the EXP-0087 sequence Alpha, Beta, Gamma, Delta in one fresh database.
function New-Control {
    param(
        [string]$Path,
        [ref]$MutationStarted
    )

    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $MutationStarted.Value = $true
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        foreach ($table in @(Get-Tables)) { Add-Table -Database $database -Definition $table }
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

function Measure-Image {
    param(
        [string]$Path,
        [string]$Role
    )

    $before = Get-BoundedIdentity -Path $Path
    $sizeBefore = [long]$before.size
    $shaBefore = [string]$before.sha256
    if ($Role -ceq "control_quad") {
        if ($sizeBefore -ne [long]$ExpectedControlSize) {
            throw "Fresh DAO control is not the observed page count."
        }
    }
    elseif ($sizeBefore -ne [long]$ExpectedCandidate.size -or
        $shaBefore -cne [string]$ExpectedCandidate.sha256) {
        throw "Guest-local candidate differs from its preregistered identity."
    }
    $endpoints = Test-Image -Path $Path
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
    if ($Role -ceq "candidate_quad") {
        $sizeBefore = [long]$ExpectedCandidate.size
        $sha256Before = [string]$ExpectedCandidate.sha256
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

[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$preparedPaths = @{}
foreach ($replica in 1..3) {
    $path = Join-Path $RunRoot "candidate-r$replica-quad.mdb"
    Copy-Item -LiteralPath $QuadCandidatePath -Destination $path
    $identity = Get-BoundedIdentity -Path $path
    $size = [long]$identity.size
    $digest = [string]$identity.sha256
    if ($size -ne [long]$ExpectedCandidate.size -or $digest -cne [string]$ExpectedCandidate.sha256) {
        throw "Prepared candidate differs from its preregistered identity."
    }
    $preparedPaths[$replica] = $path
}
$mutationStarted = $false
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    $images = New-Object Collections.ArrayList
    $controlPath = Join-Path $RunRoot "control-r$replica-quad.mdb"
    $artifacts = @(
        [pscustomobject]@{ role = "candidate_quad"; path = [string]$preparedPaths[$replica] },
        [pscustomobject]@{ role = "control_quad"; path = $controlPath }
    )
    $state = [ordered]@{
        replica = $replica
        status = "fail"
        error = $null
        images = @()
    }
    try {
        New-Control -Path $controlPath -MutationStarted ([ref]$mutationStarted)
        foreach ($artifact in $artifacts) {
            [void]$images.Add((Measure-Image -Path ([string]$artifact.path) -Role ([string]$artifact.role)))
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
    document_type = "dao_multi_table_create_job_result"
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    mutation_started = $mutationStarted
    replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "multi-table-create-job-result.json") -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
