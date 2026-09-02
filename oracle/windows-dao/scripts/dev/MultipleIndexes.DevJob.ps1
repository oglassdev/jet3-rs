[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$PlanSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")]
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$PageSize = 2048
$MaximumPages = 64
$MaximumTables = 16
$MaximumFields = 8
$MaximumIndexes = 4
$MaximumIndexFields = 4
$MaximumDetailCharacters = 512
$ScenarioOrder = @("one", "two", "three", "composite")

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
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Database must not be a reparse point: $Path"
    }
    $length = [long]$item.Length
    if ($length -lt $PageSize -or ($length % $PageSize) -ne 0 -or
        ($length / $PageSize) -gt $MaximumPages) {
        throw "Database is outside the bounded 2 KiB page geometry: $Path"
    }
    return $length
}

function ConvertTo-BoundedDetail {
    param([AllowNull()][object]$Detail)
    $text = if ($null -eq $Detail) { "No additional detail was reported." } else { [string]$Detail }
    if ($text.Length -gt $MaximumDetailCharacters) { return $text.Substring(0, $MaximumDetailCharacters) }
    return $text
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

function Invoke-WithDatabase {
    param([string]$Path, [switch]$ReadOnly, [scriptblock]$Action)
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
        if ($null -ne $database) { try { $database.Close() } catch { } }
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
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
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

function Get-ScenarioDefinition {
    param([string]$Name)
    $primary = [pscustomobject]@{
        name = "ZPrimary"; primary = $true; unique = $true; required = $true
        fields = @([pscustomobject]@{ name = "Id"; descending = $false })
    }
    $secondaryCode = [pscustomobject]@{
        name = "ASecondx"; primary = $false; unique = $false; required = $false
        fields = @([pscustomobject]@{ name = "Code"; descending = $false })
    }
    switch ($Name) {
        "one" { return [pscustomobject]@{ table = "IdxOne"; indexes = @($primary) } }
        "two" { return [pscustomobject]@{ table = "IdxTwo"; indexes = @($primary, $secondaryCode) } }
        "three" {
            $unique = [pscustomobject]@{
                name = "MUniqueX"; primary = $false; unique = $true; required = $false
                fields = @([pscustomobject]@{ name = "Code"; descending = $true })
            }
            $secondary = [pscustomobject]@{
                name = "ASecondx"; primary = $false; unique = $false; required = $false
                fields = @([pscustomobject]@{ name = "Sequence"; descending = $false })
            }
            return [pscustomobject]@{ table = "IdxTri"; indexes = @($primary, $unique, $secondary) }
        }
        "composite" {
            $composite = [pscustomobject]@{
                name = "ZComposi"; primary = $false; unique = $true; required = $false
                fields = @(
                    [pscustomobject]@{ name = "Code"; descending = $true },
                    [pscustomobject]@{ name = "Sequence"; descending = $false }
                )
            }
            $secondary = [pscustomobject]@{
                name = "ASecondx"; primary = $false; unique = $false; required = $false
                fields = @([pscustomobject]@{ name = "Id"; descending = $true })
            }
            return [pscustomobject]@{ table = "IdxMix"; indexes = @($composite, $secondary) }
        }
        default { throw "Unknown scenario $Name." }
    }
}

function New-ScenarioTable {
    param([string]$Path, [string]$Scenario)
    $definition = Get-ScenarioDefinition -Name $Scenario
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $fields = New-Object Collections.ArrayList
        try {
            $table = $database.CreateTableDef([string]$definition.table)
            foreach ($name in @("Id", "Code", "Sequence")) {
                $field = $table.CreateField($name, $DbLong)
                [void]$fields.Add($field)
                $table.Fields.Append($field)
            }
            foreach ($index in $definition.indexes) { Add-Index -Table $table -Definition $index }
            $database.TableDefs.Append($table)
        }
        finally {
            foreach ($field in $fields) { Release-ComObject -Value $field }
            Release-ComObject -Value $table
        }
    }
}

function Get-DaoMetadata {
    param([string]$Path)
    $holder = [pscustomobject]@{ value = $null }
    Invoke-WithDatabase -Path $Path -ReadOnly -Action {
        param($database)
        $definitions = $null
        $rows = New-Object Collections.ArrayList
        try {
            $definitions = $database.TableDefs
            if ([int]$definitions.Count -gt $MaximumTables) { throw "DAO table bound exceeded." }
            for ($position = 0; $position -lt [int]$definitions.Count; $position++) {
                $table = $null
                $fields = $null
                $indexes = $null
                try {
                    $table = $definitions.Item($position)
                    $tableName = [string]$table.Name
                    $fieldRows = New-Object Collections.ArrayList
                    $indexRows = New-Object Collections.ArrayList
                    if (-not $tableName.StartsWith("MSys", [StringComparison]::Ordinal)) {
                        $fields = $table.Fields
                        if ([int]$fields.Count -gt $MaximumFields) { throw "DAO field bound exceeded." }
                        for ($fieldPosition = 0; $fieldPosition -lt [int]$fields.Count; $fieldPosition++) {
                            $field = $null
                            try {
                                $field = $fields.Item($fieldPosition)
                                [void]$fieldRows.Add([ordered]@{
                                    ordinal = $fieldPosition; name = [string]$field.Name
                                    type = [int]$field.Type; size = [int]$field.Size
                                })
                            }
                            finally { Release-ComObject -Value $field }
                        }
                        $indexes = $table.Indexes
                        if ([int]$indexes.Count -gt $MaximumIndexes) { throw "DAO index bound exceeded." }
                        for ($indexPosition = 0; $indexPosition -lt [int]$indexes.Count; $indexPosition++) {
                            $index = $null
                            $indexFields = $null
                            try {
                                $index = $indexes.Item($indexPosition)
                                $indexFields = $index.Fields
                                if ([int]$indexFields.Count -gt $MaximumIndexFields) { throw "DAO index-field bound exceeded." }
                                $keys = New-Object Collections.ArrayList
                                for ($keyPosition = 0; $keyPosition -lt [int]$indexFields.Count; $keyPosition++) {
                                    $key = $null
                                    try {
                                        $key = $indexFields.Item($keyPosition)
                                        [void]$keys.Add([ordered]@{
                                            ordinal = $keyPosition; name = [string]$key.Name
                                            descending = (([long]$key.Attributes -band 1) -ne 0)
                                        })
                                    }
                                    finally { Release-ComObject -Value $key }
                                }
                                [void]$indexRows.Add([ordered]@{
                                    ordinal = $indexPosition; name = [string]$index.Name
                                    primary = [bool]$index.Primary; unique = [bool]$index.Unique
                                    required = [bool]$index.Required; fields = @($keys)
                                })
                            }
                            finally {
                                Release-ComObject -Value $indexFields
                                Release-ComObject -Value $index
                            }
                        }
                    }
                    [void]$rows.Add([ordered]@{
                        ordinal = $position; name = $tableName
                        fields = @($fieldRows); indexes = @($indexRows)
                    })
                }
                finally {
                    Release-ComObject -Value $indexes
                    Release-ComObject -Value $fields
                    Release-ComObject -Value $table
                }
            }
        }
        finally { Release-ComObject -Value $definitions }
        $holder.value = [ordered]@{ tabledefs = @($rows) }
    }
    return $holder.value
}

function Save-Checkpoint {
    param([string]$Source, [int]$Replica, [string]$Name, [object]$ArmBefore)
    $fileName = "multiple-indexes-r$Replica-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $size = Get-BoundedSize -Path $destination
    $sha256 = Get-Sha256 -Path $destination
    $dao = Get-DaoMetadata -Path $destination
    return [ordered]@{
        name = $Name; database = $fileName; size = $size; sha256 = $sha256
        sha256_after_metadata = Get-Sha256 -Path $destination
        arm_before = $ArmBefore; dao = $dao
    }
}

function Invoke-Replica {
    param([int]$Replica)
    $state = [ordered]@{ replica = $Replica; status = "fail"; error = $null; checkpoints = @(); recovery = @() }
    $checkpoints = New-Object Collections.ArrayList
    $recovery = New-Object Collections.ArrayList
    $workingPaths = New-Object Collections.ArrayList
    $basePath = Join-Path $RunRoot "working-base-r$Replica.mdb"
    $arms = @{}
    $activeScenario = $null
    try {
        New-Jet3Database -Path $basePath
        [void]$workingPaths.Add($basePath)
        $empty = Save-Checkpoint -Source $basePath -Replica $Replica -Name "empty" -ArmBefore $null
        [void]$checkpoints.Add($empty)
        foreach ($scenario in $ScenarioOrder) {
            $arm = Join-Path $RunRoot "working-$scenario-r$Replica.mdb"
            Copy-Item -LiteralPath $basePath -Destination $arm
            [void]$workingPaths.Add($arm)
            $armSize = Get-BoundedSize -Path $arm
            $armSha256 = Get-Sha256 -Path $arm
            if ($armSize -ne [long]$empty.size -or $armSha256 -cne [string]$empty.sha256) {
                throw "Arm $scenario differs from the retained empty baseline before mutation."
            }
            $arms[$scenario] = [pscustomobject]@{ path = $arm; size = $armSize; sha256 = $armSha256 }
        }
        foreach ($scenario in $ScenarioOrder) {
            $activeScenario = $scenario
            $arm = $arms[$scenario]
            New-ScenarioTable -Path ([string]$arm.path) -Scenario $scenario
            [void]$checkpoints.Add((Save-Checkpoint -Source ([string]$arm.path) -Replica $Replica `
                -Name $scenario -ArmBefore ([ordered]@{ size = [long]$arm.size; sha256 = [string]$arm.sha256 })))
            $activeScenario = $null
        }
        $state.status = "pass"
    }
    catch {
        $state.error = ConvertTo-BoundedDetail -Detail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
        if ($null -ne $activeScenario -and $arms.ContainsKey($activeScenario)) {
            try {
                $fileName = "multiple-indexes-r$Replica-$activeScenario.mdb"
                $destination = Join-Path $RunRoot $fileName
                if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
                    Copy-Item -LiteralPath ([string]$arms[$activeScenario].path) -Destination $destination
                }
                [void]$recovery.Add([ordered]@{
                    name = $activeScenario; database = $fileName
                    size = Get-BoundedSize -Path $destination; sha256 = Get-Sha256 -Path $destination
                })
            }
            catch {
                $state.error = ConvertTo-BoundedDetail -Detail `
                    ($state.error + " Recovery retention failed: " + $_.Exception.Message)
            }
        }
    }
    finally {
        foreach ($working in $workingPaths) {
            try { if (Test-Path -LiteralPath $working -PathType Leaf) { Remove-Item -LiteralPath $working -Force } }
            catch {
                $state.status = "fail"
                $state.error = ConvertTo-BoundedDetail -Detail ($state.error + " Cleanup failed: " + $_.Exception.Message)
            }
        }
    }
    $state.checkpoints = @($checkpoints)
    $state.recovery = @($recovery)
    return $state
}

[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) { [void]$replicas.Add((Invoke-Replica -Replica $replica)) }
$status = if (@($replicas | Where-Object { [string]$_.status -cne "pass" }).Count -eq 0) { "pass" } else { "fail" }
$result = [ordered]@{
    document_type = "dao_multiple_indexes_job_result"; development_only = $true
    plan_sha256 = $PlanSha256; run_id = $RunId; status = $status; replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "multiple-indexes-job-result.json") -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
