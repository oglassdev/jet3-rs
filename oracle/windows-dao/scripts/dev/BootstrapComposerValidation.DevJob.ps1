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
    [string]$EmptyCandidatePath,
    [Parameter(Mandatory = $true)]
    [string]$AlphaCandidatePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DbOpenSnapshot = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumTableDefs = 16
$MaximumProperties = 64
$MaximumDetailCharacters = 512
$MaximumDatabaseBytes = 64 * 2048
$ExpectedSystemTables = @("MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships")
$ExpectedCandidates = @{
    "candidate_empty" = [pscustomobject]@{
        size = 40960
        sha256 = "f762dbc12d80eb3fb5dae53fb58696219d48b7fa1a15d5deb5c1f9333d8862d6"
    }
    "candidate_alpha" = [pscustomobject]@{
        size = 47104
        sha256 = "8552db1c7d0083429fcbbcf4dd59a5f1d8f36383c8bdef4d9decc06247cf77ca"
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

function Get-PropertyShape {
    param([object]$Properties)

    $count = [int]$Properties.Count
    if ($count -gt $MaximumProperties) {
        throw "DAO returned more than $MaximumProperties properties."
    }
    $rows = New-Object Collections.ArrayList
    for ($index = 0; $index -lt $count; $index++) {
        $property = $null
        try {
            $property = $Properties.Item($index)
            [void]$rows.Add([ordered]@{
                name = [string]$property.Name
                type = [int]$property.Type
            })
        }
        finally {
            Release-ComObject -Value $property
        }
    }
    return @($rows | Sort-Object -Property name)
}

function Test-EmptyCandidate {
    param([string]$Path)

    $completed = New-Object Collections.ArrayList
    $snapshot = [ordered]@{}
    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $true)
        [void]$completed.Add("open_database")
        if ([string]$database.Version -cne "3.0") {
            throw "DAO did not report database version 3.0."
        }
        [void]$completed.Add("version")
        $tabledefs = @(Get-TableDefNames -Database $database)
        Assert-ExactNames -Actual $tabledefs -Expected $ExpectedSystemTables -What "Empty TableDefs"
        $snapshot.tabledefs = $tabledefs
        [void]$completed.Add("tabledefs")
        $documents = @(Get-TableDocumentNames -Database $database)
        Assert-ExactNames -Actual $documents -Expected $ExpectedSystemTables -What "Empty table documents"
        $snapshot.table_documents = $documents
        [void]$completed.Add("documents")
        return [ordered]@{
            status = "pass"
            completed = @($completed)
            detail = "All bounded empty-image endpoints passed."
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
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Test-AlphaImage {
    param([string]$Path)

    $completed = New-Object Collections.ArrayList
    $snapshot = [ordered]@{}
    $engine = $null
    $database = $null
    $definitions = $null
    $target = $null
    $fields = $null
    $field = $null
    $tableProperties = $null
    $fieldProperties = $null
    $recordset = $null
    $container = $null
    $documents = $null
    $document = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $true)
        [void]$completed.Add("open_database")
        if ([string]$database.Version -cne "3.0") {
            throw "DAO did not report database version 3.0."
        }
        [void]$completed.Add("version")
        $expectedTables = @("Alpha") + $ExpectedSystemTables
        $tabledefs = @(Get-TableDefNames -Database $database)
        Assert-ExactNames -Actual $tabledefs -Expected $expectedTables -What "Alpha TableDefs"
        $snapshot.tabledefs = $tabledefs
        [void]$completed.Add("tabledefs")

        $definitions = $database.TableDefs
        $target = $definitions.Item("Alpha")
        if ([string]$target.Name -cne "Alpha") {
            throw "Named Alpha TableDef lookup returned another object."
        }
        [void]$completed.Add("direct_lookup")

        $fields = $target.Fields
        if ([int]$fields.Count -ne 1) {
            throw "Alpha does not have exactly one field."
        }
        $field = $fields.Item("Id")
        if ([string]$field.Name -cne "Id" -or [int]$field.Type -ne $DbLong) {
            throw "Named Id field lookup did not return one Long field."
        }
        $snapshot.field = [ordered]@{ name = [string]$field.Name; type = [int]$field.Type }
        [void]$completed.Add("field")

        $tableProperties = $target.Properties
        $fieldProperties = $field.Properties
        $snapshot.table_properties = @(Get-PropertyShape -Properties $tableProperties)
        $snapshot.field_properties = @(Get-PropertyShape -Properties $fieldProperties)
        $required = $fieldProperties.Item("Required")
        try {
            $snapshot.field_required = [bool]$required.Value
            if ($snapshot.field_required) {
                throw "Id.Required is true."
            }
        }
        finally {
            Release-ComObject -Value $required
        }
        [void]$completed.Add("properties")

        $recordset = $database.OpenRecordset("Alpha", $DbOpenSnapshot, 0)
        if (-not ([bool]$recordset.BOF -and [bool]$recordset.EOF)) {
            throw "Alpha snapshot is not empty."
        }
        [void]$completed.Add("snapshot")

        $container = $database.Containers.Item("Tables")
        $documents = $container.Documents
        $document = $documents.Item("Alpha")
        if ([string]$document.Name -cne "Alpha") {
            throw "Named Alpha table document lookup returned another object."
        }
        $snapshot.table_documents = @(Get-TableDocumentNames -Database $database)
        Assert-ExactNames -Actual $snapshot.table_documents -Expected $expectedTables -What "Alpha table documents"
        [void]$completed.Add("document")
        return [ordered]@{
            status = "pass"
            completed = @($completed)
            detail = "All bounded Alpha endpoints passed."
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
        Release-ComObject -Value $fieldProperties
        Release-ComObject -Value $tableProperties
        Release-ComObject -Value $field
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

function New-ControlAlpha {
    param([string]$Path)

    $engine = $null
    $workspace = $null
    $database = $null
    $table = $null
    $field = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        $table = $database.CreateTableDef("Alpha")
        $field = $table.CreateField("Id", $DbLong)
        $table.Fields.Append($field)
        $database.TableDefs.Append($table)
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $field
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
        [string]$Role
    )

    $sizeBefore = (Get-Item -LiteralPath $Path).Length
    $shaBefore = Get-Sha256 -Path $Path
    if ($sizeBefore -gt $MaximumDatabaseBytes -or ($sizeBefore % 2048) -ne 0) {
        throw "Image is outside the bounded 2 KiB page geometry."
    }
    if ($Role -ceq "control_alpha") {
        if ($sizeBefore -ne 47104) {
            throw "Fresh DAO Alpha control is not exactly 23 pages."
        }
    }
    else {
        $expected = $ExpectedCandidates[$Role]
        if ($null -eq $expected -or $sizeBefore -ne [long]$expected.size -or
            $shaBefore -cne [string]$expected.sha256) {
            throw "Guest-local candidate differs from its preregistered identity."
        }
    }
    $endpoints = if ($Role -ceq "candidate_empty") {
        Test-EmptyCandidate -Path $Path
    }
    else {
        Test-AlphaImage -Path $Path
    }
    return [ordered]@{
        role = $Role
        database = [IO.Path]::GetFileName($Path)
        size_before = $sizeBefore
        sha256_before = $shaBefore
        endpoints = $endpoints
        size_after = (Get-Item -LiteralPath $Path).Length
        sha256_after = Get-Sha256 -Path $Path
    }
}

function New-FailedImageObservation {
    param(
        [string]$Path,
        [string]$Role,
        [string]$Detail
    )

    $size = (Get-Item -LiteralPath $Path).Length
    $sha256 = Get-Sha256 -Path $Path
    return [ordered]@{
        role = $Role
        database = [IO.Path]::GetFileName($Path)
        size_before = $size
        sha256_before = $sha256
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
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    $images = New-Object Collections.ArrayList
    $state = [ordered]@{
        replica = $replica
        status = "fail"
        error = $null
        images = @()
    }
    try {
        $emptyPath = Join-Path $RunRoot "candidate-r$replica-empty.mdb"
        $alphaPath = Join-Path $RunRoot "candidate-r$replica-alpha.mdb"
        $controlPath = Join-Path $RunRoot "control-r$replica-alpha.mdb"
        Copy-Item -LiteralPath $EmptyCandidatePath -Destination $emptyPath
        Copy-Item -LiteralPath $AlphaCandidatePath -Destination $alphaPath
        New-ControlAlpha -Path $controlPath
        [void]$images.Add((Measure-Image -Path $emptyPath -Role "candidate_empty"))
        [void]$images.Add((Measure-Image -Path $alphaPath -Role "candidate_alpha"))
        [void]$images.Add((Measure-Image -Path $controlPath -Role "control_alpha"))
        $state.images = @($images)
        $state.status = "pass"
    }
    catch {
        $failure = ConvertTo-BoundedDetail -Detail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
        foreach ($artifact in @(
            [pscustomobject]@{ path = $emptyPath; role = "candidate_empty" },
            [pscustomobject]@{ path = $alphaPath; role = "candidate_alpha" },
            [pscustomobject]@{ path = $controlPath; role = "control_alpha" }
        )) {
            $database = [IO.Path]::GetFileName([string]$artifact.path)
            $alreadyRecorded = @($images | Where-Object { [string]$_.database -ceq $database }).Count -ne 0
            if (-not $alreadyRecorded -and (Test-Path -LiteralPath $artifact.path -PathType Leaf)) {
                [void]$images.Add((New-FailedImageObservation -Path $artifact.path `
                    -Role $artifact.role -Detail $failure))
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
    document_type = "dao_bootstrap_composer_validation_job_result"
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "bootstrap-composer-validation-job-result.json") -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
