[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PlanSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$')]
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DbText = 10
$DbMemo = 12
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$PageSize = 2048
$MaximumPages = 512
$MaximumTables = 64
$MaximumFields = 16
$MaximumIndexes = 8
$MaximumDetailCharacters = 512
# Probe only the CP1252 ranges whose byte value equals its Unicode code point,
# so the observation never depends on this script file's own encoding.
$ProbeRanges = @(
    [pscustomobject]@{ first = 0x20; last = 0x7E },
    [pscustomobject]@{ first = 0xA0; last = 0xFF }
)
# Access rejects these characters in object names.
$ExcludedProbeBytes = @(0x21, 0x2E, 0x5B, 0x5D, 0x60)
$ProbeGroupSize = 16
$MaximumProbeTables = 24

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
        throw "Database exceeds the $MaximumPages-page development bound: $Path"
    }
    return $length
}

function ConvertTo-BoundedDetail {
    param([AllowNull()][object]$Detail)

    $text = if ($null -eq $Detail) { "No additional detail was reported." } else { [string]$Detail }
    if ($text.Length -gt $MaximumDetailCharacters) {
        return $text.Substring(0, $MaximumDetailCharacters)
    }
    return $text
}

function ConvertTo-OaDate {
    param([object]$Value)

    return [double]([DateTime]$Value).ToOADate()
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

# --- fixed schema checkpoints -------------------------------------------------

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

function New-BetaTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $idField = $null
        $nameField = $null
        $noteField = $null
        try {
            $table = $database.CreateTableDef("Beta")
            $idField = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($idField)
            $nameField = $table.CreateField("Name", $DbText, 50)
            $table.Fields.Append($nameField)
            $noteField = $table.CreateField("Note", $DbMemo)
            $table.Fields.Append($noteField)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $noteField
            Release-ComObject -Value $nameField
            Release-ComObject -Value $idField
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
        $index = $null
        $indexField = $null
        try {
            $table = $database.CreateTableDef("Gamma")
            $idField = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($idField)
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
            Release-ComObject -Value $idField
            Release-ComObject -Value $table
        }
    }
}

function New-DeltaTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $labelField = $null
        $index = $null
        $indexField = $null
        try {
            $table = $database.CreateTableDef("Delta")
            $labelField = $table.CreateField("Label", $DbText, 30)
            $table.Fields.Append($labelField)
            $index = $table.CreateIndex("ByLabel")
            $indexField = $index.CreateField("Label")
            $index.Fields.Append($indexField)
            $table.Indexes.Append($index)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $indexField
            Release-ComObject -Value $index
            Release-ComObject -Value $labelField
            Release-ComObject -Value $table
        }
    }
}

# --- probed-name checkpoint ---------------------------------------------------

function Get-ProbeNames {
    $names = New-Object Collections.ArrayList
    $groupIndex = 0
    # Each probed range is grouped separately so no name mixes the two ranges.
    foreach ($range in $ProbeRanges) {
        $probed = New-Object Collections.ArrayList
        for ($value = [int]$range.first; $value -le [int]$range.last; $value++) {
            if ($ExcludedProbeBytes -contains $value) {
                continue
            }
            [void]$probed.Add($value)
        }
        for ($start = 0; $start -lt $probed.Count; $start += $ProbeGroupSize) {
            $length = [Math]::Min($ProbeGroupSize, $probed.Count - $start)
            $group = @($probed.GetRange($start, $length))
            $reversed = @($group.Clone())
            [Array]::Reverse($reversed)
            $groupIndex++
            foreach ($ordering in @(
                [pscustomobject]@{ suffix = "Q"; bytes = $group },
                [pscustomobject]@{ suffix = "R"; bytes = $reversed }
            )) {
                # The decimal group index keeps names distinct under the
                # provider's case- and accent-folding comparison, which
                # otherwise collapses probed groups onto one existing name.
                $builder = New-Object Text.StringBuilder
                [void]$builder.Append("P")
                [void]$builder.Append($groupIndex.ToString("D2"))
                foreach ($value in $ordering.bytes) {
                    [void]$builder.Append([char][int]$value)
                }
                [void]$builder.Append([string]$ordering.suffix)
                [void]$names.Add([pscustomobject]@{
                    name = $builder.ToString()
                    code_points = @($ordering.bytes)
                })
            }
        }
    }
    if ($names.Count -gt $MaximumProbeTables) {
        throw "The probed-name inventory exceeds the $MaximumProbeTables-table bound."
    }
    return @($names)
}

function New-ProbeTables {
    param([string]$Path)

    $attempts = New-Object Collections.ArrayList
    foreach ($probe in Get-ProbeNames) {
        $record = [ordered]@{
            name = [string]$probe.name
            code_points = @($probe.code_points)
            created = $false
            error = $null
        }
        try {
            Invoke-WithDatabase -Path $Path -Action {
                param($database)
                $table = $null
                $field = $null
                try {
                    $table = $database.CreateTableDef([string]$probe.name)
                    $field = $table.CreateField("Id", $DbLong)
                    $table.Fields.Append($field)
                    $database.TableDefs.Append($table)
                }
                finally {
                    Release-ComObject -Value $field
                    Release-ComObject -Value $table
                }
            }
            $record.created = $true
        }
        catch {
            # A rejected name is a bounded per-name observation, never a retry.
            $record.error = ConvertTo-BoundedDetail -Detail $_.Exception.Message
        }
        [void]$attempts.Add($record)
    }
    return @($attempts)
}

# --- bounded DAO metadata -----------------------------------------------------

function Get-FieldRows {
    param([object]$TableDef)

    $fields = $null
    $rows = New-Object Collections.ArrayList
    try {
        $fields = $TableDef.Fields
        $count = [int]$fields.Count
        if ($count -gt $MaximumFields) {
            throw "DAO returned more than $MaximumFields fields."
        }
        for ($index = 0; $index -lt $count; $index++) {
            $field = $null
            try {
                $field = $fields.Item($index)
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
    }
    finally {
        Release-ComObject -Value $fields
    }
    return @($rows)
}

function Get-IndexRows {
    param([object]$TableDef)

    $indexes = $null
    $rows = New-Object Collections.ArrayList
    try {
        $indexes = $TableDef.Indexes
        $count = [int]$indexes.Count
        if ($count -gt $MaximumIndexes) {
            throw "DAO returned more than $MaximumIndexes indexes."
        }
        for ($index = 0; $index -lt $count; $index++) {
            $entry = $null
            $entryFields = $null
            $names = New-Object Collections.ArrayList
            try {
                $entry = $indexes.Item($index)
                $entryFields = $entry.Fields
                $fieldCount = [int]$entryFields.Count
                if ($fieldCount -gt $MaximumFields) {
                    throw "DAO returned more than $MaximumFields index fields."
                }
                for ($fieldIndex = 0; $fieldIndex -lt $fieldCount; $fieldIndex++) {
                    $field = $null
                    try {
                        $field = $entryFields.Item($fieldIndex)
                        [void]$names.Add([string]$field.Name)
                    }
                    finally {
                        Release-ComObject -Value $field
                    }
                }
                [void]$rows.Add([ordered]@{
                    name = [string]$entry.Name
                    primary = [bool]$entry.Primary
                    unique = [bool]$entry.Unique
                    fields = @($names)
                })
            }
            finally {
                Release-ComObject -Value $entryFields
                Release-ComObject -Value $entry
            }
        }
    }
    finally {
        Release-ComObject -Value $indexes
    }
    return @($rows)
}

# Every per-item DAO read is wrapped so a refused read (this provider has no
# workgroup file) is recorded verbatim instead of failing the checkpoint.
function Get-DaoMetadata {
    param([string]$Path, [bool]$IncludeSchema)

    $holder = [pscustomobject]@{ metadata = $null }
    Invoke-WithDatabase -Path $Path -ReadOnly -Action {
        param($database)
        $definitions = $null
        $rows = New-Object Collections.ArrayList
        try {
            $definitions = $database.TableDefs
            $count = [int]$definitions.Count
            if ($count -gt $MaximumTables) {
                throw "DAO returned more than $MaximumTables table definitions."
            }
            for ($index = 0; $index -lt $count; $index++) {
                $table = $null
                $row = [ordered]@{
                    name = $null
                    attributes = $null
                    date_created = $null
                    last_updated = $null
                    fields = @()
                    indexes = @()
                    error = $null
                }
                try {
                    $table = $definitions.Item($index)
                    $row.name = [string]$table.Name
                    $row.attributes = [int]$table.Attributes
                    $row.date_created = ConvertTo-OaDate -Value $table.DateCreated
                    $row.last_updated = ConvertTo-OaDate -Value $table.LastUpdated
                    if ($IncludeSchema) {
                        $row.fields = @(Get-FieldRows -TableDef $table)
                        $row.indexes = @(Get-IndexRows -TableDef $table)
                    }
                }
                catch {
                    $row.error = ConvertTo-BoundedDetail -Detail $_.Exception.Message
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
        $holder.metadata = [ordered]@{ tabledefs = @($rows) }
    }
    return $holder.metadata
}

function Save-Checkpoint {
    param(
        [string]$Source,
        [int]$Replica,
        [string]$Name,
        [bool]$IncludeSchema
    )

    # DAO is closed and released by the preceding mutation before the copy.
    $fileName = "schema-generalization-r$Replica-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $size = Get-BoundedSize -Path $destination
    $sha256 = Get-Sha256 -Path $destination
    $dao = Get-DaoMetadata -Path $destination -IncludeSchema $IncludeSchema
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
        probe_attempts = @()
    }
    $checkpoints = New-Object Collections.ArrayList
    $probeAttempts = @()
    $schemaPath = Join-Path $RunRoot "working-schema-r$Replica.mdb"
    $namesPath = Join-Path $RunRoot "working-names-r$Replica.mdb"
    try {
        New-Jet3Database -Path $schemaPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $schemaPath -Replica $Replica -Name "empty" -IncludeSchema $true))
        New-AlphaTable -Path $schemaPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $schemaPath -Replica $Replica -Name "alpha" -IncludeSchema $true))
        New-BetaTable -Path $schemaPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $schemaPath -Replica $Replica -Name "beta" -IncludeSchema $true))
        New-GammaTable -Path $schemaPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $schemaPath -Replica $Replica -Name "gamma" -IncludeSchema $true))
        New-DeltaTable -Path $schemaPath
        [void]$checkpoints.Add((Save-Checkpoint -Source $schemaPath -Replica $Replica -Name "delta" -IncludeSchema $true))

        New-Jet3Database -Path $namesPath
        $probeAttempts = @(New-ProbeTables -Path $namesPath)
        [void]$checkpoints.Add((Save-Checkpoint -Source $namesPath -Replica $Replica -Name "names" -IncludeSchema $false))
        $state.status = "pass"
    }
    catch {
        $state.status = "fail"
        $state.error = ConvertTo-BoundedDetail -Detail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
    }
    finally {
        foreach ($working in @($schemaPath, $namesPath)) {
            try {
                if (Test-Path -LiteralPath $working -PathType Leaf) {
                    Remove-Item -LiteralPath $working -Force
                }
            }
            catch {
                $state.status = "fail"
                $state.error = "Working-file cleanup failed: " + $_.Exception.Message
            }
        }
    }
    $state.checkpoints = @($checkpoints)
    $state.probe_attempts = @($probeAttempts)
    return $state
}

[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    [void]$replicas.Add((Invoke-Replica -Replica $replica))
}
$status = "pass"
foreach ($entry in $replicas) {
    if ([string]$entry.status -cne "pass") {
        $status = "fail"
    }
}
$result = [ordered]@{
    document_type = "dao_schema_generalization_job_result"
    development_only = $true
    plan_sha256 = $PlanSha256
    run_id = $RunId
    status = $status
    replicas = @($replicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "schema-generalization-job-result.json") -Document $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
