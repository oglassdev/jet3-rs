[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9a-f]{64}$")][string]$PlanSha256,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")][string]$RunId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$DbVersion30 = 32
$DbLong = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$PageSize = 2048
$MaximumPages = 128
$MaximumTables = 32
$MaximumFieldsPerTable = 32
$MaximumIndexesPerTable = 16
$MaximumDetailCharacters = 512
$UndefinedSlots = @(0x81, 0x8D, 0x8F, 0x90, 0x9D)
$DefinedBytes = @(0x80..0xFF | Where-Object { $_ -notin $UndefinedSlots })
$Cp1252 = [Text.Encoding]::GetEncoding(1252, [Text.EncoderFallback]::ExceptionFallback, [Text.DecoderFallback]::ExceptionFallback)
$TransportSentinel = "T" + $Cp1252.GetString([byte[]]$DefinedBytes) + [char]0x007F + [char]0x0081 + [char]0x008D + [char]0x008F + [char]0x0090 + [char]0x009D + "Z"

function Release-ComObject { param([object]$Value); if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) } }
function Get-Sha256 { param([string]$Path); return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-BoundedSize {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Database must not be a reparse point: $Path" }
    $length = [long]$item.Length
    if ($length -lt $PageSize -or ($length % $PageSize) -ne 0 -or ($length / $PageSize) -gt $MaximumPages) { throw "Database is outside the bounded 2 KiB page geometry: $Path" }
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
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($Path), (($Document | ConvertTo-Json -Depth 20) + "`n"), $encoding)
}
function Invoke-WithDatabase {
    param([string]$Path, [switch]$ReadOnly, [scriptblock]$Action)
    $engine = $null; $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, [bool]$ReadOnly)
        & $Action $database
        $database.Close(); Release-ComObject $database; $database = $null
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject $database; Release-ComObject $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}
function New-Jet3Database {
    param([string]$Path, [ref]$MutationStarted)
    $engine = $null; $workspace = $null; $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"; $workspace = $engine.Workspaces.Item(0)
        $MutationStarted.Value = $true
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        $database.Close(); Release-ComObject $database; $database = $null
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject $database; Release-ComObject $workspace; Release-ComObject $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}
function ConvertTo-Utf16LeHex { param([string]$Value); return (([Text.Encoding]::Unicode.GetBytes($Value) | ForEach-Object { $_.ToString("x2") }) -join "") }
function Get-DefinedCharacter { param([int]$Byte); return $Cp1252.GetString([byte[]]@($Byte)) }
function Get-BatchSpecs {
    param([int]$BatchIndex, [int[]]$Bytes)
    $result = New-Object Collections.ArrayList
    [void]$result.Add([pscustomobject]@{ role = "ascii_control"; name = "C" + $BatchIndex.ToString("D2") + "B"; bytes = @() })
    foreach ($point in $Bytes) {
        $position = [Array]::IndexOf($DefinedBytes, [int]$point)
        $neighborPoint = [int]$DefinedBytes[($position + 1) % $DefinedBytes.Count]
        $tag = ([int]$point).ToString("X2"); $current = Get-DefinedCharacter $point; $neighbor = Get-DefinedCharacter $neighborPoint
        foreach ($spec in @(
            [pscustomobject]@{ role = "single_left"; name = "N${tag}L${current}AZ"; bytes = @($point) },
            [pscustomobject]@{ role = "single_middle"; name = "N${tag}MA${current}Z"; bytes = @($point) },
            [pscustomobject]@{ role = "single_right"; name = "N${tag}RAZ${current}"; bytes = @($point) },
            [pscustomobject]@{ role = "repeat"; name = "N${tag}DA${current}${current}Z"; bytes = @($point, $point) },
            [pscustomobject]@{ role = "forward"; name = "N${tag}FA${current}${neighbor}Z"; bytes = @($point, $neighborPoint) },
            [pscustomobject]@{ role = "reverse"; name = "N${tag}VA${neighbor}${current}Z"; bytes = @($neighborPoint, $point) }
        )) { [void]$result.Add($spec) }
    }
    return @($result)
}
function Get-ControlSpecs {
    $result = New-Object Collections.ArrayList
    [void]$result.Add([pscustomobject]@{ role = "ascii_control"; name = "CREJECTB"; bytes = @() })
    foreach ($point in @(0x7F, 0x81, 0x8D, 0x8F, 0x90, 0x9D)) {
        $tag = ([int]$point).ToString("X2")
        [void]$result.Add([pscustomobject]@{ role = if ($point -eq 0x7F) { "boundary_7f" } else { "undefined_$tag" }; name = "R${tag}A" + [char]$point + "Z"; bytes = @() })
    }
    return @($result)
}
function Add-ProbeTable {
    param([string]$Path, [object]$Spec)
    $created = $false; $errorText = $null; $failureOperation = $null
    $engine = $null; $database = $null; $table = $null; $field = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $false)
        try {
            try { $table = $database.CreateTableDef([string]$Spec.name) }
            catch {
                $failureOperation = "create_tabledef"
                $errorText = ConvertTo-BoundedDetail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
            }
            if ($null -eq $failureOperation) {
                $field = $table.CreateField("Id", $DbLong)
                $table.Fields.Append($field)
                try { $database.TableDefs.Append($table) }
                catch {
                    $failureOperation = "tabledefs_append"
                    $errorText = ConvertTo-BoundedDetail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
                }
            }
        }
        finally {
            Release-ComObject $field
            Release-ComObject $table
        }
        $database.Close(); Release-ComObject $database; $database = $null
        $created = $null -eq $failureOperation
    }
    finally {
        if ($null -ne $database) { $database.Close() }
        Release-ComObject $database; Release-ComObject $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return [ordered]@{ role = [string]$Spec.role; name = [string]$Spec.name; name_utf16le_hex = ConvertTo-Utf16LeHex ([string]$Spec.name); inserted_bytes = @($Spec.bytes); created = $created; failure_operation = $failureOperation; error = $errorText }
}
function Get-DaoMetadata {
    param([string]$Path)
    $holder = [pscustomobject]@{ value = $null }
    Invoke-WithDatabase -Path $Path -ReadOnly -Action {
        param($database)
        $definitions = $null; $rows = New-Object Collections.ArrayList
        try {
            $definitions = $database.TableDefs
            if ([int]$definitions.Count -gt $MaximumTables) { throw "DAO table bound exceeded." }
            for ($position = 0; $position -lt [int]$definitions.Count; $position++) {
                $table = $null; $fields = $null; $indexes = $null
                try {
                    $table = $definitions.Item($position); $tableName = [string]$table.Name
                    $fieldRows = New-Object Collections.ArrayList; $indexRows = New-Object Collections.ArrayList
                    $fields = $table.Fields
                    if ([int]$fields.Count -gt $MaximumFieldsPerTable) { throw "DAO field bound exceeded." }
                    for ($fieldPosition = 0; $fieldPosition -lt [int]$fields.Count; $fieldPosition++) {
                        $field = $null
                        try { $field = $fields.Item($fieldPosition); [void]$fieldRows.Add([ordered]@{ ordinal = $fieldPosition; name = [string]$field.Name; type = [int]$field.Type; size = [int]$field.Size }) }
                        finally { Release-ComObject $field }
                    }
                    $indexes = $table.Indexes
                    if ([int]$indexes.Count -gt $MaximumIndexesPerTable) { throw "DAO index bound exceeded." }
                    for ($indexPosition = 0; $indexPosition -lt [int]$indexes.Count; $indexPosition++) {
                        $index = $null
                        try { $index = $indexes.Item($indexPosition); [void]$indexRows.Add([ordered]@{ ordinal = $indexPosition; name = [string]$index.Name; primary = [bool]$index.Primary; unique = [bool]$index.Unique }) }
                        finally { Release-ComObject $index }
                    }
                    [void]$rows.Add([ordered]@{ ordinal = $position; name = $tableName; fields = @($fieldRows); indexes = @($indexRows) })
                }
                finally { Release-ComObject $indexes; Release-ComObject $fields; Release-ComObject $table }
            }
        }
        finally { Release-ComObject $definitions }
        $holder.value = [ordered]@{ tabledefs = @($rows) }
    }
    return $holder.value
}
function Save-Checkpoint {
    param([string]$Source, [int]$Replica, [string]$Name, [AllowNull()][object]$ArmBefore, [object[]]$Attempts)
    $fileName = "extended-names-r$Replica-$Name.mdb"; $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination -Force
    $size = Get-BoundedSize $destination; $sha256 = Get-Sha256 $destination; $dao = Get-DaoMetadata $destination
    return [ordered]@{ name = $Name; database = $fileName; size = $size; sha256 = $sha256; size_after_metadata = Get-BoundedSize $destination; sha256_after_metadata = Get-Sha256 $destination; arm_before = $ArmBefore; attempts = @($Attempts); dao = $dao }
}
function Invoke-Replica {
    param([int]$Replica)
    $state = [ordered]@{ replica = $Replica; status = "fail"; error = $null; mutation_started = $false; phase = "before_create_database"; transport_sentinel = [ordered]@{ value = $TransportSentinel; utf16le_hex = ConvertTo-Utf16LeHex $TransportSentinel }; checkpoints = @(); recovery = @() }
    $checkpoints = New-Object Collections.ArrayList; $recovery = New-Object Collections.ArrayList
    $replicaWork = Join-Path (Join-Path $RunRoot "_working") "r$Replica"
    $basePath = Join-Path $replicaWork "base.mdb"; $activeName = $null; $activePath = $null
    $mutationStarted = $false; $recoveryEligible = $false
    try {
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($replicaWork))
        $state.phase = "create_database"; New-Jet3Database $basePath ([ref]$mutationStarted); $state.mutation_started = $mutationStarted
        $state.phase = "capture_empty"; $activeName = "empty"; $activePath = $basePath; $recoveryEligible = $true
        $empty = Save-Checkpoint $basePath $Replica "empty" $null @(); [void]$checkpoints.Add($empty)
        $activeName = $null; $activePath = $null; $recoveryEligible = $false
        for ($batchIndex = 0; $batchIndex -lt 41; $batchIndex++) {
            $batchOffset = $batchIndex * 3
            $batchBytes = @($DefinedBytes[$batchOffset..($batchOffset + 2)])
            $activeName = "b" + $batchIndex.ToString("D2"); $state.phase = "append_$activeName"; $activePath = Join-Path $replicaWork "$activeName.mdb"
            Copy-Item -LiteralPath $basePath -Destination $activePath; $recoveryEligible = $true
            $armSize = Get-BoundedSize $activePath; $armSha256 = Get-Sha256 $activePath
            if ($armSize -ne [long]$empty.size -or $armSha256 -cne [string]$empty.sha256) { throw "Batch $activeName differs from the retained empty baseline before mutation." }
            $attempts = New-Object Collections.ArrayList
            foreach ($spec in Get-BatchSpecs $batchIndex $batchBytes) {
                $attempt = Add-ProbeTable $activePath $spec; [void]$attempts.Add($attempt)
                if ([string]$spec.role -ceq "ascii_control" -and -not [bool]$attempt.created) { throw "Batch $activeName ASCII control was rejected." }
            }
            [void]$checkpoints.Add((Save-Checkpoint $activePath $Replica $activeName ([ordered]@{ size = $armSize; sha256 = $armSha256 }) @($attempts)))
            $state.phase = "cleanup_$activeName"; $recoveryEligible = $false
            Remove-Item -LiteralPath $activePath -Force; $activeName = $null; $activePath = $null
        }
        $activeName = "controls"; $state.phase = "append_controls"; $activePath = Join-Path $replicaWork "controls.mdb"
        Copy-Item -LiteralPath $basePath -Destination $activePath; $recoveryEligible = $true
        $armSize = Get-BoundedSize $activePath; $armSha256 = Get-Sha256 $activePath
        if ($armSize -ne [long]$empty.size -or $armSha256 -cne [string]$empty.sha256) { throw "Controls arm differs from the retained empty baseline before mutation." }
        $attempts = New-Object Collections.ArrayList
        foreach ($spec in Get-ControlSpecs) {
            $attempt = Add-ProbeTable $activePath $spec; [void]$attempts.Add($attempt)
            if ([string]$spec.role -ceq "ascii_control" -and -not [bool]$attempt.created) { throw "Controls-arm ASCII control was rejected." }
        }
        [void]$checkpoints.Add((Save-Checkpoint $activePath $Replica $activeName ([ordered]@{ size = $armSize; sha256 = $armSha256 }) @($attempts)))
        $state.phase = "cleanup_controls"; $recoveryEligible = $false
        Remove-Item -LiteralPath $activePath -Force; $activeName = $null; $activePath = $null
        $state.phase = "cleanup_complete"; $state.status = "pass"
    }
    catch {
        $state.mutation_started = $mutationStarted; $state.error = ConvertTo-BoundedDetail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
        if ($recoveryEligible -and $null -ne $activePath -and $null -ne $activeName) {
            try {
                $fileName = "extended-names-r$Replica-$activeName.mdb"; $destination = Join-Path $RunRoot $fileName
                Copy-Item -LiteralPath $activePath -Destination $destination -Force
                [void]$recovery.Add([ordered]@{ name = $activeName; database = $fileName; size = Get-BoundedSize $destination; sha256 = Get-Sha256 $destination })
            }
            catch {
                try { if (Test-Path -LiteralPath $destination -PathType Leaf) { Remove-Item -LiteralPath $destination -Force } } catch { }
                $state.error = ConvertTo-BoundedDetail ($state.error + " Recovery retention failed: " + $_.Exception.Message)
            }
        }
    }
    finally {
        foreach ($working in @($activePath, $basePath)) {
            try { if ($null -ne $working -and (Test-Path -LiteralPath $working -PathType Leaf)) { Remove-Item -LiteralPath $working -Force } }
            catch { $state.status = "fail"; $state.error = ConvertTo-BoundedDetail ($state.error + " Cleanup failed: " + $_.Exception.Message) }
        }
        try { if (Test-Path -LiteralPath $replicaWork -PathType Container) { Remove-Item -LiteralPath $replicaWork -Force } }
        catch { $state.status = "fail"; $state.error = ConvertTo-BoundedDetail ($state.error + " Working-directory cleanup failed: " + $_.Exception.Message) }
    }
    if ([string]$state.status -ceq "pass") { $state.phase = "complete" }
    $state.checkpoints = @($checkpoints); $state.recovery = @($recovery); return $state
}
[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$replicas = New-Object Collections.ArrayList
foreach ($replica in 1..3) {
    $entry = Invoke-Replica $replica; [void]$replicas.Add($entry)
    if ([string]$entry.status -cne "pass" -and -not [bool]$entry.mutation_started -and $replicas.Count -eq 1) { break }
}
$status = if (@($replicas | Where-Object { [string]$_.status -cne "pass" }).Count -eq 0) { "pass" } else { "fail" }
$result = [ordered]@{ document_type = "dao_extended_names_job_result"; development_only = $true; plan_sha256 = $PlanSha256; run_id = $RunId; status = $status; replicas = @($replicas) }
Write-JsonDocument (Join-Path $RunRoot "extended-names-job-result.json") $result
if ($status -ceq "pass") { exit 0 } else { exit 1 }
