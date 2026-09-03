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
$MaximumCheckpointPages = 256
$MaximumRecoveryPages = 512
$MaximumTables = 16
$MaximumFields = 140
$MaximumDetailCharacters = 512
$ScenarioOrder = @("zero", "one", "two")
$ScenarioFields = @{ zero = 1; one = 70; two = 140 }
$ScenarioTables = @{ zero = "Alpha"; one = "ContOneX"; two = "ContTwoX" }

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

function Get-DatabaseMeasurement {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
        throw "Database must be a regular file: $Path"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Database must not be a reparse point: $Path"
    }
    $length = [long]$item.Length
    $divisible = ($length % $PageSize) -eq 0
    $pages = if ($divisible) { [long]($length / $PageSize) } else { $null }
    $failedPredicate = if ($length -lt $PageSize) {
        "minimum_page_length"
    }
    elseif (-not $divisible) {
        "page_alignment"
    }
    elseif ($pages -gt $MaximumCheckpointPages) {
        "checkpoint_bound_exceeded"
    }
    else { $null }
    return [ordered]@{
        raw_byte_length = $length
        divisible_by_page_size = $divisible
        page_count = $pages
        failed_predicate = $failedPredicate
    }
}

function Assert-CheckpointMeasurement {
    param([object]$Measurement, [string]$Path)
    if ($null -ne $Measurement.failed_predicate) {
        $message = "Database failed checkpoint predicate {0}: {1} " +
            "(raw_byte_length={2}, divisible_by_page_size={3}, page_count={4})"
        throw ($message -f
            $Measurement.failed_predicate, $Path, $Measurement.raw_byte_length,
            $Measurement.divisible_by_page_size, $Measurement.page_count)
    }
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
    param([string]$Path, [ref]$MutationStarted)
    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $MutationStarted.Value = $true
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

function Get-FieldName {
    param([int]$Ordinal)
    return "F{0:D3}AAAAAA" -f $Ordinal
}

function New-ScenarioTable {
    param([string]$Path, [string]$Scenario)
    $count = [int]$ScenarioFields[$Scenario]
    $tableName = [string]$ScenarioTables[$Scenario]
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $fields = New-Object Collections.ArrayList
        try {
            $table = $database.CreateTableDef($tableName)
            for ($ordinal = 0; $ordinal -lt $count; $ordinal++) {
                $fieldName = if ($Scenario -ceq "zero") {
                    "Id"
                }
                else { Get-FieldName -Ordinal $ordinal }
                $field = $table.CreateField($fieldName, $DbLong)
                [void]$fields.Add($field)
                $table.Fields.Append($field)
            }
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
                try {
                    $table = $definitions.Item($position)
                    $tableName = [string]$table.Name
                    $fieldRows = New-Object Collections.ArrayList
                    if (-not $tableName.StartsWith("MSys", [StringComparison]::Ordinal)) {
                        $fields = $table.Fields
                        if ([int]$fields.Count -gt $MaximumFields) { throw "DAO field bound exceeded." }
                        for ($fieldPosition = 0; $fieldPosition -lt [int]$fields.Count; $fieldPosition++) {
                            $field = $null
                            try {
                                $field = $fields.Item($fieldPosition)
                                [void]$fieldRows.Add([ordered]@{
                                    ordinal = $fieldPosition
                                    name = [string]$field.Name
                                    type = [int]$field.Type
                                    size = [int]$field.Size
                                })
                            }
                            finally { Release-ComObject -Value $field }
                        }
                    }
                    [void]$rows.Add([ordered]@{
                        ordinal = $position
                        name = $tableName
                        fields = @($fieldRows)
                    })
                }
                finally {
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
    param(
        [string]$Source,
        [int]$Replica,
        [string]$Name,
        [object]$ArmBefore,
        [ref]$LastMeasurement
    )
    $fileName = "definition-continuation-r$Replica-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination -Force
    $measurement = Get-DatabaseMeasurement -Path $destination
    $LastMeasurement.Value = $measurement
    Assert-CheckpointMeasurement -Measurement $measurement -Path $destination
    $size = [long]$measurement.raw_byte_length
    $sha256 = Get-Sha256 -Path $destination
    $dao = Get-DaoMetadata -Path $destination
    $measurementAfterMetadata = Get-DatabaseMeasurement -Path $destination
    $LastMeasurement.Value = $measurementAfterMetadata
    Assert-CheckpointMeasurement -Measurement $measurementAfterMetadata -Path $destination
    return [ordered]@{
        name = $Name
        database = $fileName
        size = $size
        sha256 = $sha256
        measurement = $measurement
        size_after_metadata = [long]$measurementAfterMetadata.raw_byte_length
        sha256_after_metadata = Get-Sha256 -Path $destination
        measurement_after_metadata = $measurementAfterMetadata
        arm_before = $ArmBefore
        dao = $dao
    }
}

function Invoke-Replica {
    param([int]$Replica)
    $state = [ordered]@{
        replica = $Replica
        status = "fail"
        error = $null
        mutation_started = $false
        phase = "before_create_database"
        checkpoints = @()
        arm_baselines = @()
        failure_measurement = $null
        recovery = @()
    }
    $checkpoints = New-Object Collections.ArrayList
    $armBaselines = New-Object Collections.ArrayList
    $recovery = New-Object Collections.ArrayList
    $workingPaths = New-Object Collections.ArrayList
    $basePath = Join-Path $WorkingRoot "working-continuation-base-r$Replica.mdb"
    $arms = @{}
    $activeCheckpoint = $null
    $mutationStarted = $false
    $lastMeasurement = $null
    try {
        [void]$workingPaths.Add($basePath)
        $state.phase = "create_database"
        $activeCheckpoint = "empty"
        New-Jet3Database -Path $basePath -MutationStarted ([ref]$mutationStarted)
        $state.mutation_started = $mutationStarted
        $state.phase = "capture_empty"
        $empty = Save-Checkpoint -Source $basePath -Replica $Replica -Name "empty" `
            -ArmBefore $null -LastMeasurement ([ref]$lastMeasurement)
        [void]$checkpoints.Add($empty)
        $activeCheckpoint = $null
        $state.phase = "copy_arms"
        foreach ($scenario in $ScenarioOrder) {
            $arm = Join-Path $WorkingRoot "working-continuation-$scenario-r$Replica.mdb"
            [void]$workingPaths.Add($arm)
            Copy-Item -LiteralPath $basePath -Destination $arm
            $armMeasurement = Get-DatabaseMeasurement -Path $arm
            $lastMeasurement = $armMeasurement
            Assert-CheckpointMeasurement -Measurement $armMeasurement -Path $arm
            $armSize = [long]$armMeasurement.raw_byte_length
            $armSha256 = Get-Sha256 -Path $arm
            if ($armSize -ne [long]$empty.size -or $armSha256 -cne [string]$empty.sha256) {
                throw "Arm $scenario differs from the retained empty baseline before mutation."
            }
            $baseline = [ordered]@{
                name = $scenario
                size = $armSize
                sha256 = $armSha256
                measurement = $armMeasurement
            }
            [void]$armBaselines.Add($baseline)
            $arms[$scenario] = [pscustomobject]@{
                path = $arm
                size = $armSize
                sha256 = $armSha256
                measurement = $armMeasurement
            }
        }
        foreach ($scenario in $ScenarioOrder) {
            $activeCheckpoint = $scenario
            $state.phase = "append_$scenario"
            $arm = $arms[$scenario]
            New-ScenarioTable -Path ([string]$arm.path) -Scenario $scenario
            $state.phase = "capture_$scenario"
            [void]$checkpoints.Add((Save-Checkpoint -Source ([string]$arm.path) -Replica $Replica `
                -Name $scenario -ArmBefore ([ordered]@{
                    size = [long]$arm.size
                    sha256 = [string]$arm.sha256
                    measurement = $arm.measurement
                }) -LastMeasurement ([ref]$lastMeasurement)))
            $activeCheckpoint = $null
        }
        $state.phase = "complete"
        $state.status = "pass"
    }
    catch {
        $state.mutation_started = $mutationStarted
        $state.error = ConvertTo-BoundedDetail -Detail `
            ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
        if ($null -ne $lastMeasurement -and
            $null -ne $lastMeasurement.failed_predicate) {
            $state.failure_measurement = $lastMeasurement
        }
        if ($null -ne $activeCheckpoint) {
            $destination = $null
            try {
                $source = if ($activeCheckpoint -ceq "empty") {
                    $basePath
                }
                elseif ($arms.ContainsKey($activeCheckpoint)) {
                    [string]$arms[$activeCheckpoint].path
                }
                else { throw "Recovery source is unavailable." }
                $fileName = "definition-continuation-r$Replica-$activeCheckpoint.mdb"
                $destination = Join-Path $RunRoot $fileName
                Copy-Item -LiteralPath $source -Destination $destination -Force
                $recoveryMeasurement = Get-DatabaseMeasurement -Path $destination
                if ($null -eq $state.failure_measurement -and
                    $null -ne $recoveryMeasurement.failed_predicate) {
                    $state.failure_measurement = $recoveryMeasurement
                }
                if ($recoveryMeasurement.raw_byte_length -lt $PageSize -or
                    -not $recoveryMeasurement.divisible_by_page_size -or
                    $recoveryMeasurement.page_count -gt $MaximumRecoveryPages) {
                    $message = "Database is outside the recovery-only salvage bound: {0} " +
                        "(raw_byte_length={1}, divisible_by_page_size={2}, page_count={3})"
                    throw ($message -f
                        $destination, $recoveryMeasurement.raw_byte_length,
                        $recoveryMeasurement.divisible_by_page_size,
                        $recoveryMeasurement.page_count)
                }
                $reason = if ($recoveryMeasurement.failed_predicate -ceq
                    "checkpoint_bound_exceeded") {
                    "checkpoint_bound_exceeded"
                }
                else { "post_mutation_failure" }
                [void]$recovery.Add([ordered]@{
                    name = $activeCheckpoint
                    database = $fileName
                    size = [long]$recoveryMeasurement.raw_byte_length
                    sha256 = Get-Sha256 -Path $destination
                    measurement = $recoveryMeasurement
                    reason = $reason
                    interpreted = $false
                })
            }
            catch {
                try {
                    if ($null -ne $destination -and
                        (Test-Path -LiteralPath $destination -PathType Leaf)) {
                        Remove-Item -LiteralPath $destination -Force
                    }
                }
                catch { }
                $state.error = ConvertTo-BoundedDetail -Detail `
                    ($state.error + " Recovery retention failed: " + $_.Exception.Message)
            }
        }
    }
    finally {
        foreach ($working in $workingPaths) {
            try {
                if (Test-Path -LiteralPath $working -PathType Leaf) {
                    Remove-Item -LiteralPath $working -Force
                }
            }
            catch {
                $state.status = "fail"
                $state.error = ConvertTo-BoundedDetail -Detail `
                    ($state.error + " Cleanup failed: " + $_.Exception.Message)
            }
        }
    }
    $state.checkpoints = @($checkpoints)
    $state.arm_baselines = @($armBaselines)
    $state.recovery = @($recovery)
    return $state
}

$resolvedRunRoot = [IO.Path]::GetFullPath($RunRoot)
[void][IO.Directory]::CreateDirectory($resolvedRunRoot)
$WorkingRoot = Join-Path $resolvedRunRoot "working-definition-continuation"
if (Test-Path -LiteralPath $WorkingRoot) {
    throw "Definition-continuation working directory already exists."
}
[void][IO.Directory]::CreateDirectory($WorkingRoot)
$workingRootItem = Get-Item -LiteralPath $WorkingRoot -Force
if (-not $workingRootItem.PSIsContainer -or
    ($workingRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Definition-continuation working path must be a non-reparse directory."
}
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    $entry = Invoke-Replica -Replica $replica
    [void]$replicas.Add($entry)
    if ([string]$entry.status -cne "pass" -and
        -not [bool]$entry.mutation_started -and $replicas.Count -eq 1) {
        break
    }
}
$status = if (@($replicas | Where-Object { [string]$_.status -cne "pass" }).Count -eq 0) {
    "pass"
} else { "fail" }
$result = [ordered]@{
    document_type = "dao_definition_continuation_job_result"
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "definition-continuation-job-result.json") `
    -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
