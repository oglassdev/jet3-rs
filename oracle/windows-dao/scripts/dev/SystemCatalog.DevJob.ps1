[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PlanSha256,

    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [ValidateSet("system-catalog", "long-value-maps", "long-value-maps-followup")]
    [string]$Experiment = "system-catalog"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PlanSha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw "PlanSha256 must be a lowercase 64-hex digest."
}

$DbVersion30 = 32
$DbLong = 4
$DbText = 10
$DbMemo = 12
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$PageSize = 2048
$MaximumPages = 64
$MaximumTables = 16
$MaximumCollectionItems = 64
$MaximumPropertyValueCharacters = 256

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

function Get-Sha256 {
    param([string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BoundedSize {
    param([string]$Path)

    $length = [long](Get-Item -LiteralPath $Path).Length
    if (($length % $PageSize) -ne 0) {
        throw "Database is not an exact sequence of 2 KiB pages: $Path"
    }
    if (($length / $PageSize) -gt $MaximumPages) {
        throw "Database exceeds the 64-page development bound: $Path"
    }
    return $length
}

function Invoke-WithDatabase {
    param(
        [string]$Path,
        [switch]$ReadOnly,
        [scriptblock]$Action
    )

    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, [bool]$ReadOnly)
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

function New-AlphaTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $field = $null
        try {
            $table = $database.CreateTableDef("Alpha")
            $field = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($field)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $field
            Release-ComObject -Value $table
        }
    }
}

function New-GammaTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $idField = $null
        $noteField = $null
        try {
            $table = $database.CreateTableDef("Gamma")
            $idField = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($idField)
            $noteField = $table.CreateField("Note", $DbMemo)
            $table.Fields.Append($noteField)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $noteField
            Release-ComObject -Value $idField
            Release-ComObject -Value $table
        }
    }
}

function Add-GammaLongMemoRow {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $records = $null
        try {
            $records = $database.OpenRecordset("Gamma")
            $records.AddNew()
            $records.Fields.Item("Id").Value = 1
            $records.Fields.Item("Note").Value = ("memo-" + ("x" * 4096))
            $records.Update()
            $records.Close()
            Release-ComObject -Value $records
            $records = $null
        }
        finally {
            if ($null -ne $records) {
                try { $records.Close() } catch { }
            }
            Release-ComObject -Value $records
        }
    }
}

function New-BetaTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $idField = $null
        $nameField = $null
        $index = $null
        $indexField = $null
        try {
            $table = $database.CreateTableDef("Beta")
            $idField = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($idField)
            $nameField = $table.CreateField("Name", $DbText, 50)
            $table.Fields.Append($nameField)
            $index = $table.CreateIndex("PrimaryKey")
            $index.Primary = $true
            $index.Unique = $true
            $indexField = $index.CreateField("Id")
            $index.Fields.Append($indexField)
            $table.Indexes.Append($index)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $indexField
            Release-ComObject -Value $index
            Release-ComObject -Value $nameField
            Release-ComObject -Value $idField
            Release-ComObject -Value $table
        }
    }
}

function New-SavedQuery {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $query = $null
        try {
            $query = $database.CreateQueryDef("QueryOne", "SELECT Id FROM Alpha;")
        }
        finally {
            Release-ComObject -Value $query
        }
    }
}

function New-BetaAlphaRelation {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $relation = $null
        $field = $null
        try {
            $relation = $database.CreateRelation("BetaAlpha", "Beta", "Alpha", 0)
            $field = $relation.CreateField("Id")
            $field.ForeignName = "Id"
            $relation.Fields.Append($field)
            $database.Relations.Append($relation)
        }
        finally {
            Release-ComObject -Value $field
            Release-ComObject -Value $relation
        }
    }
}

function Get-BoundedCount {
    param([object]$Collection, [int]$Maximum, [string]$Label)

    $count = [int]$Collection.Count
    if ($count -gt $Maximum) {
        throw "DAO returned more than $Maximum $Label."
    }
    return $count
}

function ConvertTo-OaDate {
    param([object]$Value)

    return [double]([DateTime]$Value).ToOADate()
}

function ConvertTo-BoundedPropertyValue {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return $null
    }
    $text = [string]$Value
    if ($text.Length -gt $MaximumPropertyValueCharacters) {
        $text = $text.Substring(0, $MaximumPropertyValueCharacters)
    }
    return $text
}

# Every per-item DAO read is wrapped so a refused read (this provider has no
# workgroup file) is recorded verbatim instead of failing the checkpoint.
function Get-TableDefRows {
    param([object]$Database)

    $definitions = $null
    $rows = New-Object Collections.ArrayList
    try {
        $definitions = $Database.TableDefs
        $count = Get-BoundedCount -Collection $definitions -Maximum $MaximumTables -Label "table definitions"
        for ($index = 0; $index -lt $count; $index++) {
            $table = $null
            $row = [ordered]@{
                name = $null
                attributes = $null
                date_created = $null
                last_updated = $null
                error = $null
            }
            try {
                $table = $definitions.Item($index)
                $row.name = [string]$table.Name
                $row.attributes = [int]$table.Attributes
                $row.date_created = ConvertTo-OaDate -Value $table.DateCreated
                $row.last_updated = ConvertTo-OaDate -Value $table.LastUpdated
            }
            catch {
                $row.error = $_.Exception.Message
            }
            finally {
                Release-ComObject -Value $table
            }
            [void]$rows.Add($row)
        }
    }
    finally {
        Release-ComObject -Value $definitions
    }
    return @($rows)
}

function Get-ContainerRows {
    param([object]$Database)

    $containers = $null
    $rows = New-Object Collections.ArrayList
    try {
        $containers = $Database.Containers
        $count = Get-BoundedCount -Collection $containers -Maximum $MaximumCollectionItems -Label "containers"
        for ($index = 0; $index -lt $count; $index++) {
            $container = $null
            $documents = $null
            $row = [ordered]@{
                name = $null
                owner = $null
                error = $null
                documents = @()
            }
            $documentRows = New-Object Collections.ArrayList
            try {
                $container = $containers.Item($index)
                $row.name = [string]$container.Name
                $row.owner = [string]$container.Owner
                $documents = $container.Documents
                $documentCount = Get-BoundedCount -Collection $documents -Maximum $MaximumCollectionItems -Label "documents"
                for ($documentIndex = 0; $documentIndex -lt $documentCount; $documentIndex++) {
                    $document = $null
                    $documentRow = [ordered]@{
                        name = $null
                        owner = $null
                        error = $null
                    }
                    try {
                        $document = $documents.Item($documentIndex)
                        $documentRow.name = [string]$document.Name
                        $documentRow.owner = [string]$document.Owner
                    }
                    catch {
                        $documentRow.error = $_.Exception.Message
                    }
                    finally {
                        Release-ComObject -Value $document
                    }
                    [void]$documentRows.Add($documentRow)
                }
            }
            catch {
                $row.error = $_.Exception.Message
            }
            finally {
                Release-ComObject -Value $documents
                Release-ComObject -Value $container
            }
            $row.documents = @($documentRows)
            [void]$rows.Add($row)
        }
    }
    finally {
        Release-ComObject -Value $containers
    }
    return @($rows)
}

function Get-QueryDefRows {
    param([object]$Database)

    $queries = $null
    $rows = New-Object Collections.ArrayList
    try {
        $queries = $Database.QueryDefs
        $count = Get-BoundedCount -Collection $queries -Maximum $MaximumCollectionItems -Label "query definitions"
        for ($index = 0; $index -lt $count; $index++) {
            $query = $null
            $row = [ordered]@{
                name = $null
                sql = $null
                date_created = $null
                last_updated = $null
                error = $null
            }
            try {
                $query = $queries.Item($index)
                $row.name = [string]$query.Name
                $row.sql = [string]$query.SQL
                $row.date_created = ConvertTo-OaDate -Value $query.DateCreated
                $row.last_updated = ConvertTo-OaDate -Value $query.LastUpdated
            }
            catch {
                $row.error = $_.Exception.Message
            }
            finally {
                Release-ComObject -Value $query
            }
            [void]$rows.Add($row)
        }
    }
    finally {
        Release-ComObject -Value $queries
    }
    return @($rows)
}

function Get-RelationRows {
    param([object]$Database)

    $relations = $null
    $rows = New-Object Collections.ArrayList
    try {
        $relations = $Database.Relations
        $count = Get-BoundedCount -Collection $relations -Maximum $MaximumCollectionItems -Label "relations"
        for ($index = 0; $index -lt $count; $index++) {
            $relation = $null
            $fields = $null
            $row = [ordered]@{
                name = $null
                table = $null
                foreign_table = $null
                attributes = $null
                error = $null
                fields = @()
            }
            $fieldRows = New-Object Collections.ArrayList
            try {
                $relation = $relations.Item($index)
                $row.name = [string]$relation.Name
                $row.table = [string]$relation.Table
                $row.foreign_table = [string]$relation.ForeignTable
                $row.attributes = [int]$relation.Attributes
                $fields = $relation.Fields
                $fieldCount = Get-BoundedCount -Collection $fields -Maximum $MaximumCollectionItems -Label "relation fields"
                for ($fieldIndex = 0; $fieldIndex -lt $fieldCount; $fieldIndex++) {
                    $field = $null
                    try {
                        $field = $fields.Item($fieldIndex)
                        [void]$fieldRows.Add([ordered]@{
                            name = [string]$field.Name
                            foreign_name = [string]$field.ForeignName
                        })
                    }
                    finally {
                        Release-ComObject -Value $field
                    }
                }
            }
            catch {
                $row.error = $_.Exception.Message
            }
            finally {
                Release-ComObject -Value $fields
                Release-ComObject -Value $relation
            }
            $row.fields = @($fieldRows)
            [void]$rows.Add($row)
        }
    }
    finally {
        Release-ComObject -Value $relations
    }
    return @($rows)
}

function Get-PropertyRows {
    param([object]$Database)

    $properties = $null
    $rows = New-Object Collections.ArrayList
    try {
        $properties = $Database.Properties
        $count = Get-BoundedCount -Collection $properties -Maximum $MaximumCollectionItems -Label "properties"
        for ($index = 0; $index -lt $count; $index++) {
            $property = $null
            $row = [ordered]@{
                name = $null
                type = $null
                value = $null
                error = $null
            }
            try {
                $property = $properties.Item($index)
                $row.name = [string]$property.Name
                $row.type = [int]$property.Type
                $row.value = ConvertTo-BoundedPropertyValue -Value $property.Value
            }
            catch {
                $row.error = $_.Exception.Message
            }
            finally {
                Release-ComObject -Value $property
            }
            [void]$rows.Add($row)
        }
    }
    finally {
        Release-ComObject -Value $properties
    }
    return @($rows)
}

function Get-DaoMetadata {
    param([string]$Path)

    $holder = [pscustomobject]@{ metadata = $null }
    Invoke-WithDatabase -Path $Path -ReadOnly -Action {
        param($database)
        $holder.metadata = [ordered]@{
            tabledefs = @(Get-TableDefRows -Database $database)
            containers = @(Get-ContainerRows -Database $database)
            querydefs = @(Get-QueryDefRows -Database $database)
            relations = @(Get-RelationRows -Database $database)
            properties = @(Get-PropertyRows -Database $database)
        }
    }
    return $holder.metadata
}

function Save-Checkpoint {
    param(
        [string]$Source,
        [int]$Replica,
        [string]$Name
    )

    # DAO is closed and released by the preceding mutation before the copy.
    $fileName = "$Experiment-r$Replica-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $size = Get-BoundedSize -Path $destination
    $sha256 = Get-Sha256 -Path $destination
    $dao = Get-DaoMetadata -Path $destination
    return [ordered]@{
        name = $Name
        database = $fileName
        size = $size
        sha256 = $sha256
        sha256_after_metadata = Get-Sha256 -Path $destination
        dao = $dao
    }
}

function Invoke-Replica {
    param([int]$Replica)

    $state = [ordered]@{
        replica = $Replica
        status = "fail"
        error = $null
        checkpoints = @()
    }
    $checkpoints = New-Object Collections.ArrayList
    $workingPath = Join-Path $RunRoot "working-r$Replica.mdb"
    try {
        New-Jet3Database -Path $workingPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "empty"))
        if ($Experiment -in @("long-value-maps", "long-value-maps-followup")) {
            New-GammaTable -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "table"))
            Add-GammaLongMemoRow -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "row"))
        }
        else {
            New-AlphaTable -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "table1"))
            New-BetaTable -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "table2"))
            New-SavedQuery -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "query"))
            New-BetaAlphaRelation -Path $workingPath
            [void]$checkpoints.Add((Save-Checkpoint -Source $workingPath -Replica $Replica -Name "relationship"))
        }
        $state.status = "pass"
    }
    catch {
        $state.status = "fail"
        $state.error = $_.Exception.Message
    }
    finally {
        try {
            if (Test-Path -LiteralPath $workingPath -PathType Leaf) {
                Remove-Item -LiteralPath $workingPath -Force
            }
        }
        catch {
            $state.status = "fail"
            $state.error = "Working-file cleanup failed: " + $_.Exception.Message
        }
    }
    $state.checkpoints = @($checkpoints)
    return $state
}

[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$resultPath = Join-Path $RunRoot "$Experiment-job-result.json"
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    [void]$replicas.Add((Invoke-Replica -Replica $replica))
}
$status = "pass"
foreach ($entry in $replicas) {
    if ($entry.status -cne "pass") {
        $status = "fail"
    }
}
$result = [ordered]@{
    document_type = if ($Experiment -ceq "long-value-maps") {
        "dao_long_value_maps_job_result"
    }
    elseif ($Experiment -ceq "long-value-maps-followup") {
        "dao_long_value_maps_followup_job_result"
    }
    else { "dao_system_catalog_job_result" }
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    replicas = @($replicas)
}
Write-JsonDocument -Path $resultPath -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
