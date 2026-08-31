[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PlanSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($PlanSha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw "PlanSha256 must be a lowercase 64-hex digest."
}

$DbVersion30 = 32
$DbLong = 4
$DbOpenSnapshot = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$CreatedTableName = "BootstrapLayout"
$RenamedTableName = "BootstrapRenamed"
$PageSize = 2048
$MaximumPages = 64
$MaximumTableDefs = 128
$MaximumCatalogRows = 512
$MaximumLvPropBytes = 65536
$MaximumVariants = 64

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

function ConvertTo-Hex {
    param([byte[]]$Bytes)

    return [BitConverter]::ToString($Bytes).Replace("-", "").ToLowerInvariant()
}

function Get-BoundedFileFacts {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path
    if (($item.Length % $PageSize) -ne 0) {
        throw "Database is not an exact sequence of 2 KiB pages: $Path"
    }
    $pageCount = [long]($item.Length / $PageSize)
    if ($pageCount -gt $MaximumPages) {
        throw "Database exceeds the 64-page development bound: $Path"
    }
    return [ordered]@{
        size = [long]$item.Length
        page_count = $pageCount
        sha256 = Get-Sha256 -Path $Path
    }
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

function New-BootstrapTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $field = $null
        try {
            $table = $database.CreateTableDef($CreatedTableName)
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

function Rename-BootstrapTable {
    param([string]$Path)

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $definitions = $null
        try {
            $definitions = $database.TableDefs
            $table = $definitions.Item($CreatedTableName)
            $table.Name = $RenamedTableName
        }
        finally {
            Release-ComObject -Value $table
            Release-ComObject -Value $definitions
        }
    }
}

function Get-LvPropSnapshot {
    param(
        [object]$Database,
        [string]$TableName
    )

    $recordset = $null
    $nameField = $null
    $lvPropField = $null
    try {
        $recordset = $Database.OpenRecordset("MSysObjects", $DbOpenSnapshot, 0)
        $rowCount = 0
        $matches = New-Object Collections.ArrayList
        while (-not [bool]$recordset.EOF) {
            $rowCount++
            if ($rowCount -gt $MaximumCatalogRows) {
                throw "MSysObjects exceeds the 512-row development bound."
            }
            try {
                $nameField = $recordset.Fields.Item("Name")
                $name = [string]$nameField.Value
            }
            finally {
                Release-ComObject -Value $nameField
                $nameField = $null
            }
            if ($name -ceq $TableName) {
                try {
                    $lvPropField = $recordset.Fields.Item("LvProp")
                    $value = $lvPropField.Value
                    if ($null -eq $value) {
                        [void]$matches.Add($null)
                    }
                    else {
                        [byte[]]$bytes = $value
                        if ($bytes.Length -gt $MaximumLvPropBytes) {
                            throw "MSysObjects LvProp exceeds the 65536-byte development bound."
                        }
                        [void]$matches.Add($bytes)
                    }
                }
                finally {
                    Release-ComObject -Value $lvPropField
                    $lvPropField = $null
                }
            }
            $recordset.MoveNext()
        }
        if ($matches.Count -ne 1) {
            return [pscustomobject]@{
                report = [ordered]@{
                    status = "no_outcome"
                    detail = "Expected one bounded MSysObjects row for $TableName; found $($matches.Count)."
                }
                bytes = $null
            }
        }
        if ($null -eq $matches[0] -or ([byte[]]$matches[0]).Length -eq 0) {
            return [pscustomobject]@{
                report = [ordered]@{
                    status = "no_outcome"
                    detail = "MSysObjects LvProp was null or empty."
                }
                bytes = $null
            }
        }
        [byte[]]$captured = $matches[0]
        return [pscustomobject]@{
            report = [ordered]@{
                status = "captured"
                detail = "Captured the bounded DAO MSysObjects LvProp value."
                length = [int]$captured.Length
                bytes_hex = ConvertTo-Hex -Bytes $captured
            }
            bytes = $captured
        }
    }
    catch {
        return [pscustomobject]@{
            report = [ordered]@{
                status = "no_outcome"
                detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
            }
            bytes = $null
        }
    }
    finally {
        if ($null -ne $recordset) {
            try { $recordset.Close() } catch { }
        }
        Release-ComObject -Value $lvPropField
        Release-ComObject -Value $nameField
        Release-ComObject -Value $recordset
    }
}

function Get-DaoSnapshot {
    param(
        [string]$Path,
        [AllowNull()][string]$TargetName
    )

    $holder = [pscustomobject]@{
        report = $null
        lvprop_bytes = $null
    }
    Invoke-WithDatabase -Path $Path -ReadOnly -Action {
        param($database)
        $definitions = $null
        try {
            $definitions = $database.TableDefs
            $count = [int]$definitions.Count
            if ($count -gt $MaximumTableDefs) {
                throw "DAO returned more than 128 table definitions."
            }
            if ([string]::IsNullOrEmpty($TargetName)) {
                $holder.report = [ordered]@{ table_definition_count = $count }
                return
            }

            $matches = New-Object Collections.ArrayList
            for ($index = 0; $index -lt $count; $index++) {
                $table = $null
                try {
                    $table = $definitions.Item($index)
                    if ([string]$table.Name -ceq $TargetName) {
                        [void]$matches.Add($index)
                    }
                }
                finally {
                    Release-ComObject -Value $table
                }
            }
            if ($matches.Count -ne 1) {
                throw "Expected exactly one DAO TableDef named $TargetName; found $($matches.Count)."
            }

            $target = $null
            $fields = $null
            try {
                $target = $definitions.Item([int]$matches[0])
                $fieldRows = New-Object Collections.ArrayList
                $fields = $target.Fields
                $fieldCount = [int]$fields.Count
                if ($fieldCount -gt 128) {
                    throw "DAO returned more than 128 fields."
                }
                for ($fieldIndex = 0; $fieldIndex -lt $fieldCount; $fieldIndex++) {
                    $field = $null
                    try {
                        $field = $fields.Item($fieldIndex)
                        [void]$fieldRows.Add([ordered]@{
                            name = [string]$field.Name
                            type = [int]$field.Type
                        })
                    }
                    finally {
                        Release-ComObject -Value $field
                    }
                }
                $lvProp = Get-LvPropSnapshot -Database $database -TableName $TargetName
                $holder.lvprop_bytes = $lvProp.bytes
                $holder.report = [ordered]@{
                    table_name = [string]$target.Name
                    date_created_oadate = [double]([datetime]$target.DateCreated).ToOADate()
                    last_updated_oadate = [double]([datetime]$target.LastUpdated).ToOADate()
                    fields = @($fieldRows)
                    lvprop = $lvProp.report
                }
            }
            finally {
                Release-ComObject -Value $fields
                Release-ComObject -Value $target
            }
        }
        finally {
            Release-ComObject -Value $definitions
        }
    }
    return [pscustomobject]@{
        report = $holder.report
        lvprop_bytes = $holder.lvprop_bytes
    }
}

function Save-Checkpoint {
    param(
        [string]$Source,
        [int]$Replica,
        [ValidateSet("empty", "created", "renamed")]
        [string]$Name,
        [AllowNull()][string]$TargetName
    )

    # DAO is closed and released by Get-DaoSnapshot before the copy and hash.
    $dao = Get-DaoSnapshot -Path $Source -TargetName $TargetName
    $fileName = "bootstrap-layout-r$Replica-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    if ([IO.Path]::GetFullPath($Source) -ne [IO.Path]::GetFullPath($destination)) {
        Copy-Item -LiteralPath $Source -Destination $destination
    }
    $facts = Get-BoundedFileFacts -Path $destination
    return [pscustomobject]@{
        report = [ordered]@{
            name = $Name
            database = $fileName
            size = $facts.size
            page_count = $facts.page_count
            sha256 = $facts.sha256
            dao = $dao.report
        }
        path = $destination
        bytes = [IO.File]::ReadAllBytes($destination)
        dao = $dao.report
        lvprop_bytes = $dao.lvprop_bytes
    }
}

function Find-ByteSequence {
    param(
        [byte[]]$Haystack,
        [byte[]]$Needle
    )

    $matches = New-Object Collections.ArrayList
    if ($Needle.Length -eq 0 -or $Needle.Length -gt $Haystack.Length) {
        return @()
    }
    $last = $Haystack.Length - $Needle.Length
    for ($start = 0; $start -le $last; $start++) {
        $same = $true
        for ($index = 0; $index -lt $Needle.Length; $index++) {
            if ($Haystack[$start + $index] -ne $Needle[$index]) {
                $same = $false
                break
            }
        }
        if ($same) {
            [void]$matches.Add([long]$start)
        }
    }
    return @($matches)
}

function Get-TimestampCorrelation {
    param(
        [double]$OaDate,
        [double]$OtherOaDate,
        [byte[]]$RenamedBytes
    )

    $needle = [BitConverter]::GetBytes($OaDate)
    if ($OaDate -eq $OtherOaDate) {
        return [pscustomobject]@{
            report = [ordered]@{
                status = "no_outcome"
                detail = "DAO timestamps were equal and could not be attributed independently."
            }
            offsets = @()
        }
    }
    $offsets = @(Find-ByteSequence -Haystack $RenamedBytes -Needle $needle)
    if ($offsets.Count -ne 1) {
        return [pscustomobject]@{
            report = [ordered]@{
                status = "no_outcome"
                detail = "The exact DAO OLE Date byte sequence occurred $($offsets.Count) times in the renamed MDB."
            }
            offsets = @()
        }
    }
    return [pscustomobject]@{
        report = [ordered]@{
            status = "resolved"
            detail = "The exact DAO OLE Date byte sequence occurred once in the renamed MDB."
            offsets = @($offsets)
        }
        offsets = @($offsets)
    }
}

function Test-ByteRangeEqual {
    param(
        [byte[]]$Left,
        [int]$LeftStart,
        [byte[]]$Right,
        [int]$RightStart,
        [int]$Length
    )

    for ($index = 0; $index -lt $Length; $index++) {
        if ($Left[$LeftStart + $index] -ne $Right[$RightStart + $index]) {
            return $false
        }
    }
    return $true
}

function Get-LvPropCorrelation {
    param(
        [AllowNull()][byte[]]$LvPropBytes,
        [byte[]]$RenamedBytes
    )

    $noOutcome = {
        param([string]$Detail)
        return [pscustomobject]@{
            report = [ordered]@{
                status = "no_outcome"
                detail = $Detail
                header_offset = $null
                payload_page = $null
                payload_row = $null
            }
            header_offset = $null
        }
    }
    if ($null -eq $LvPropBytes -or $LvPropBytes.Length -eq 0) {
        return (& $noOutcome "DAO did not expose one bounded non-empty LvProp payload.")
    }
    if ($LvPropBytes.Length -gt 0x00ffffff) {
        return (& $noOutcome "The LvProp payload exceeds the observed 24-bit external length.")
    }

    $payloadLocators = New-Object Collections.ArrayList
    $pageCount = [int]($RenamedBytes.Length / $PageSize)
    for ($page = 0; $page -lt $pageCount; $page++) {
        $pageStart = $page * $PageSize
        if ($RenamedBytes[$pageStart] -ne 1) { continue }
        if (-not (Test-ByteRangeEqual -Left $RenamedBytes -LeftStart ($pageStart + 4) -Right ([Text.Encoding]::ASCII.GetBytes("LVAL")) -RightStart 0 -Length 4)) {
            continue
        }
        $rowCount = [int][BitConverter]::ToUInt16($RenamedBytes, $pageStart + 8)
        if ($rowCount -gt 256 -or (10 + (2 * $rowCount)) -gt $PageSize) { continue }
        $prior = $PageSize
        $directoryEnd = 10 + (2 * $rowCount)
        for ($row = 0; $row -lt $rowCount; $row++) {
            $raw = [int][BitConverter]::ToUInt16($RenamedBytes, $pageStart + 10 + (2 * $row))
            if (($raw -band 0xe000) -ne 0) {
                $prior = -1
                break
            }
            $start = $raw -band 0x1fff
            if ($start -lt $directoryEnd -or $start -ge $prior) {
                $prior = -1
                break
            }
            $length = $prior - $start
            if ($length -eq $LvPropBytes.Length -and
                (Test-ByteRangeEqual -Left $RenamedBytes -LeftStart ($pageStart + $start) -Right $LvPropBytes -RightStart 0 -Length $length)) {
                [void]$payloadLocators.Add([ordered]@{ page = $page; row = $row })
            }
            $prior = $start
        }
    }
    if ($payloadLocators.Count -ne 1) {
        return (& $noOutcome "The exact DAO LvProp payload occupied $($payloadLocators.Count) complete tag-01 LVAL rows.")
    }

    $locator = $payloadLocators[0]
    $header = New-Object byte[] 12
    $lengthAndFlag = [uint32]$LvPropBytes.Length -bor [uint32]0x40000000
    [BitConverter]::GetBytes($lengthAndFlag).CopyTo($header, 0)
    $header[4] = [byte]$locator.row
    $pageBytes = [BitConverter]::GetBytes([uint32]$locator.page)
    $header[5] = $pageBytes[0]
    $header[6] = $pageBytes[1]
    $header[7] = $pageBytes[2]
    $headerOffsets = @(Find-ByteSequence -Haystack $RenamedBytes -Needle $header)
    if ($headerOffsets.Count -ne 1) {
        return (& $noOutcome "The derived 12-byte single-page external LvProp header occurred $($headerOffsets.Count) times.")
    }
    return [pscustomobject]@{
        report = [ordered]@{
            status = "resolved"
            detail = "The exact DAO payload occupied one LVAL row and its derived external header occurred once."
            header_offset = [long]$headerOffsets[0]
            payload_page = [int]$locator.page
            payload_row = [int]$locator.row
        }
        header_offset = [long]$headerOffsets[0]
    }
}

function Get-DifferenceRanges {
    param(
        [byte[]]$EmptyBytes,
        [byte[]]$RenamedBytes,
        [int]$Page
    )

    $ranges = New-Object Collections.ArrayList
    $pageStart = $Page * $PageSize
    $runStart = -1
    for ($offset = 0; $offset -lt $PageSize; $offset++) {
        $different = $EmptyBytes[$pageStart + $offset] -ne $RenamedBytes[$pageStart + $offset]
        if ($different -and $runStart -lt 0) {
            $runStart = $pageStart + $offset
        }
        elseif (-not $different -and $runStart -ge 0) {
            [void]$ranges.Add([ordered]@{ start = [long]$runStart; end = [long]($pageStart + $offset) })
            $runStart = -1
        }
    }
    if ($runStart -ge 0) {
        [void]$ranges.Add([ordered]@{ start = [long]$runStart; end = [long]($pageStart + $PageSize) })
    }
    return @($ranges)
}

function Test-BaselineEndpoints {
    param(
        [string]$Path,
        [string]$TableName
    )

    $endpoints = [ordered]@{
        open_database = $false
        table_enumerated = $false
        field_enumerated = $false
        table_opened = $false
        detail = $null
    }
    $engine = $null
    $database = $null
    $definitions = $null
    $target = $null
    $fields = $null
    $recordset = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path, $false, $true)
        $endpoints.open_database = $true
        $definitions = $database.TableDefs
        $count = [int]$definitions.Count
        if ($count -gt $MaximumTableDefs) {
            throw "DAO returned more than 128 table definitions."
        }
        $matches = New-Object Collections.ArrayList
        for ($index = 0; $index -lt $count; $index++) {
            $table = $null
            try {
                $table = $definitions.Item($index)
                if ([string]$table.Name -ceq $TableName) {
                    [void]$matches.Add($index)
                }
            }
            finally {
                Release-ComObject -Value $table
            }
        }
        if ($matches.Count -ne 1) {
            throw "Expected exactly one TableDef named $TableName; found $($matches.Count)."
        }
        $endpoints.table_enumerated = $true

        $target = $definitions.Item([int]$matches[0])
        $fields = $target.Fields
        if ([int]$fields.Count -ne 1) {
            throw "Expected exactly one field on $TableName."
        }
        $field = $null
        try {
            $field = $fields.Item(0)
            if ([string]$field.Name -cne "Id" -or [int]$field.Type -ne $DbLong) {
                throw "Expected exactly the Id Long field."
            }
        }
        finally {
            Release-ComObject -Value $field
        }
        $endpoints.field_enumerated = $true

        $recordset = $database.OpenRecordset($TableName, $DbOpenSnapshot, 0)
        if (-not ([bool]$recordset.BOF -and [bool]$recordset.EOF)) {
            throw "Expected the target table snapshot to be empty."
        }
        $endpoints.table_opened = $true
        $endpoints.detail = "All bounded read-only endpoints passed."
    }
    catch {
        $endpoints.detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    }
    finally {
        if ($null -ne $recordset) {
            try { $recordset.Close() } catch { }
        }
        Release-ComObject -Value $recordset
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
    return $endpoints
}

function Add-VariantSpec {
    param(
        [Collections.ArrayList]$Specs,
        [string]$Name,
        [string]$Kind,
        [ValidateSet("created", "renamed")]
        [string]$BaseCheckpoint,
        [AllowNull()][object]$Page,
        [object[]]$Ranges,
        [ValidateSet("copy_byte", "zero_date", "revert_page", "zero_page")]
        [string]$Mutation,
        [int]$MutationOffset
    )

    [void]$Specs.Add([pscustomobject]@{
        name = $Name
        kind = $Kind
        base_checkpoint = $BaseCheckpoint
        page = $Page
        ranges = @($Ranges)
        mutation = $Mutation
        mutation_offset = $MutationOffset
    })
}

function Invoke-ReplicaCore {
    param(
        [int]$Replica,
        [object]$State
    )

    $workingPath = Join-Path $RunRoot "working-r$Replica.mdb"
    New-Jet3Database -Path $workingPath

    $empty = Save-Checkpoint -Source $workingPath -Replica $Replica -Name "empty" -TargetName $null
    [void]$State.checkpoints.Add($empty.report)
    New-BootstrapTable -Path $workingPath
    $created = Save-Checkpoint -Source $workingPath -Replica $Replica -Name "created" -TargetName $CreatedTableName
    [void]$State.checkpoints.Add($created.report)
    Start-Sleep -Seconds 2
    Rename-BootstrapTable -Path $workingPath
    $renamed = Save-Checkpoint -Source $workingPath -Replica $Replica -Name "renamed" -TargetName $RenamedTableName
    [void]$State.checkpoints.Add($renamed.report)

    $State.page0_values = [ordered]@{
        empty = [int]$empty.bytes[1538]
        created = [int]$created.bytes[1538]
        renamed = [int]$renamed.bytes[1538]
    }
    $State.page0_changed_ranges = [ordered]@{
        empty_to_created = @(Get-DifferenceRanges -EmptyBytes $empty.bytes -RenamedBytes $created.bytes -Page 0)
        created_to_renamed = @(Get-DifferenceRanges -EmptyBytes $created.bytes -RenamedBytes $renamed.bytes -Page 0)
    }

    $dateCreated = Get-TimestampCorrelation `
        -OaDate ([double]$renamed.dao.date_created_oadate) `
        -OtherOaDate ([double]$renamed.dao.last_updated_oadate) `
        -RenamedBytes $renamed.bytes
    $dateUpdated = Get-TimestampCorrelation `
        -OaDate ([double]$renamed.dao.last_updated_oadate) `
        -OtherOaDate ([double]$renamed.dao.date_created_oadate) `
        -RenamedBytes $renamed.bytes
    $lvProp = Get-LvPropCorrelation -LvPropBytes $renamed.lvprop_bytes -RenamedBytes $renamed.bytes
    $State.correlations = [ordered]@{
        date_created = $dateCreated.report
        date_updated = $dateUpdated.report
        lvprop = $lvProp.report
    }

    $emptyPageCount = [int]$empty.report.page_count
    $createdPageCount = [int]$created.report.page_count
    if ($createdPageCount -lt $emptyPageCount) {
        throw "The created database has fewer pages than the fresh database."
    }
    $changedPageGroups = New-Object Collections.ArrayList
    for ($page = 1; $page -lt $emptyPageCount; $page++) {
        $ranges = @(Get-DifferenceRanges -EmptyBytes $empty.bytes -RenamedBytes $created.bytes -Page $page)
        if ($ranges.Count -gt 0) {
            [void]$changedPageGroups.Add([ordered]@{
                name = "existing-page-$page"
                page = $page
                ranges = @($ranges)
            })
        }
    }
    $appendedPageGroups = New-Object Collections.ArrayList
    for ($page = $emptyPageCount; $page -lt $createdPageCount; $page++) {
        $start = [long]($page * $PageSize)
        [void]$appendedPageGroups.Add([ordered]@{
            name = "appended-page-$page"
            page = $page
            ranges = @([ordered]@{ start = $start; end = [long]($start + $PageSize) })
        })
    }
    $State.changed_page_groups = $changedPageGroups
    $State.appended_page_groups = $appendedPageGroups

    $specs = New-Object Collections.ArrayList
    $pageZeroStart = 1538
    Add-VariantSpec -Specs $specs -Name "page0-byte-1538" -Kind "candidate_page0" -BaseCheckpoint "created" -Page 0 `
        -Ranges @([ordered]@{ start = [long]$pageZeroStart; end = [long]($pageZeroStart + 1) }) `
        -Mutation "copy_byte" -MutationOffset $pageZeroStart
    if ($dateCreated.report.status -eq "resolved") {
        $offset = [int]$dateCreated.offsets[0]
        Add-VariantSpec -Specs $specs -Name "date-created-zero" -Kind "candidate_date_created" -BaseCheckpoint "renamed" -Page ([int]($offset / $PageSize)) `
            -Ranges @([ordered]@{ start = [long]$offset; end = [long]($offset + 8) }) `
            -Mutation "zero_date" -MutationOffset $offset
    }
    if ($dateUpdated.report.status -eq "resolved") {
        $offset = [int]$dateUpdated.offsets[0]
        Add-VariantSpec -Specs $specs -Name "date-updated-zero" -Kind "candidate_date_updated" -BaseCheckpoint "renamed" -Page ([int]($offset / $PageSize)) `
            -Ranges @([ordered]@{ start = [long]$offset; end = [long]($offset + 8) }) `
            -Mutation "zero_date" -MutationOffset $offset
    }
    foreach ($group in $changedPageGroups) {
        $page = [int]$group.page
        Add-VariantSpec -Specs $specs -Name ([string]$group.name) -Kind "revert_existing_page" -BaseCheckpoint "created" -Page $page `
            -Ranges @($group.ranges) -Mutation "revert_page" -MutationOffset ($page * $PageSize)
    }
    foreach ($group in $appendedPageGroups) {
        $page = [int]$group.page
        Add-VariantSpec -Specs $specs -Name ([string]$group.name) -Kind "zero_appended_page" -BaseCheckpoint "created" -Page $page `
            -Ranges @($group.ranges) -Mutation "zero_page" -MutationOffset ($page * $PageSize)
    }
    if ($specs.Count -gt $MaximumVariants) {
        throw "Replica $Replica requires more than 64 ablation variants."
    }

    $baselineName = "bootstrap-layout-r$Replica-variant-baseline-created.mdb"
    $baselinePath = Join-Path $RunRoot $baselineName
    try {
        Copy-Item -LiteralPath $created.path -Destination $baselinePath
        $baselineFactsBefore = Get-BoundedFileFacts -Path $baselinePath
        $baselineHashBefore = [string]$baselineFactsBefore.sha256
    }
    catch {
        if (Test-Path -LiteralPath $baselinePath -PathType Leaf) {
            Remove-Item -LiteralPath $baselinePath -Force
        }
        throw
    }
    $State.baseline = [ordered]@{
        database = $baselineName
        sha256_before_open = $baselineHashBefore
        sha256_after_open = $baselineHashBefore
        open_database = $false
        table_enumerated = $false
        field_enumerated = $false
        table_opened = $false
        detail = "Baseline endpoints were not reached."
    }
    $baselineEndpoints = Test-BaselineEndpoints -Path $baselinePath -TableName $CreatedTableName
    $State.baseline.open_database = [bool]$baselineEndpoints.open_database
    $State.baseline.table_enumerated = [bool]$baselineEndpoints.table_enumerated
    $State.baseline.field_enumerated = [bool]$baselineEndpoints.field_enumerated
    $State.baseline.table_opened = [bool]$baselineEndpoints.table_opened
    $State.baseline.detail = [string]$baselineEndpoints.detail
    $baselineFactsAfter = Get-BoundedFileFacts -Path $baselinePath
    $baselineHashAfter = [string]$baselineFactsAfter.sha256
    $State.baseline.sha256_after_open = $baselineHashAfter
    if ($baselineFactsAfter.size -ne $baselineFactsBefore.size) {
        throw "DAO changed the created baseline clone size during read-only endpoint checks."
    }
    if ($baselineHashBefore -ne $baselineHashAfter) {
        $State.baseline.detail = "DAO changed the created baseline clone during read-only endpoint checks."
    }

    foreach ($spec in $specs) {
        if ($spec.name -notmatch '^[a-z0-9-]+$') {
            throw "Variant name is not filename-safe: $($spec.name)"
        }
        $fileName = "bootstrap-layout-r$Replica-variant-$($spec.name).mdb"
        $path = Join-Path $RunRoot $fileName
        $basePath = if ($spec.base_checkpoint -eq "created") { $created.path } else { $renamed.path }
        try {
            Copy-Item -LiteralPath $basePath -Destination $path
            [byte[]]$bytes = [IO.File]::ReadAllBytes($path)
            switch ($spec.mutation) {
                "copy_byte" {
                    $bytes[$spec.mutation_offset] = $empty.bytes[$spec.mutation_offset]
                }
                "zero_date" {
                    [Array]::Clear($bytes, $spec.mutation_offset, 8)
                }
                "revert_page" {
                    [Array]::Copy(
                        $empty.bytes,
                        $spec.mutation_offset,
                        $bytes,
                        $spec.mutation_offset,
                        $PageSize
                    )
                }
                "zero_page" {
                    [Array]::Clear($bytes, $spec.mutation_offset, $PageSize)
                }
            }
            [IO.File]::WriteAllBytes($path, $bytes)
            $variantFacts = Get-BoundedFileFacts -Path $path
            $before = [string]$variantFacts.sha256
        }
        catch {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                Remove-Item -LiteralPath $path -Force
            }
            throw
        }
        $variant = [ordered]@{
            name = $spec.name
            kind = $spec.kind
            database = $fileName
            size = $variantFacts.size
            base_checkpoint = $spec.base_checkpoint
            ranges = @($spec.ranges)
            sha256_before_open = $before
            sha256_after_open = $before
            endpoints = [ordered]@{
                open_database = $false
                table_enumerated = $false
                field_enumerated = $false
                table_opened = $false
                detail = "DAO endpoints were not reached."
            }
            detail = "DAO endpoints were not reached."
        }
        if ($null -ne $spec.page) {
            $variant.page = [int]$spec.page
        }
        [void]$State.variants.Add($variant)

        $targetName = if ($spec.base_checkpoint -eq "created") { $CreatedTableName } else { $RenamedTableName }
        $endpoints = Test-BaselineEndpoints -Path $path -TableName $targetName
        $variant.endpoints = $endpoints
        $variant.detail = [string]$endpoints.detail
        $afterFacts = Get-BoundedFileFacts -Path $path
        $after = [string]$afterFacts.sha256
        $variant.sha256_after_open = $after
        if ($afterFacts.size -ne $variant.size) {
            throw "DAO changed a variant clone size during read-only endpoint checks."
        }
        $detail = [string]$endpoints.detail
        if ($before -ne $after) {
            $detail += " DAO changed the clone during the read-only open."
        }
        $variant.detail = $detail
    }
}

function Invoke-Replica {
    param([int]$Replica)

    $state = [ordered]@{
        replica = $Replica
        status = "fail"
        detail = "Replica did not start."
        checkpoints = New-Object Collections.ArrayList
        page0_values = $null
        page0_changed_ranges = $null
        baseline = [ordered]@{
            database = $null
            sha256_before_open = $null
            sha256_after_open = $null
            open_database = $false
            table_enumerated = $false
            field_enumerated = $false
            table_opened = $false
            detail = "Baseline endpoints were not reached."
        }
        correlations = [ordered]@{
            date_created = [ordered]@{ status = "no_outcome"; detail = "Correlation was not reached." }
            date_updated = [ordered]@{ status = "no_outcome"; detail = "Correlation was not reached." }
            lvprop = [ordered]@{ status = "no_outcome"; detail = "Correlation was not reached." }
        }
        changed_page_groups = New-Object Collections.ArrayList
        appended_page_groups = New-Object Collections.ArrayList
        variants = New-Object Collections.ArrayList
    }
    try {
        Invoke-ReplicaCore -Replica $Replica -State $state
        $state.status = "pass"
        $state.detail = "Completed the preregistered replica once without retry."
    }
    catch {
        $state.status = "fail"
        $state.detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    }
    finally {
        $workingPath = Join-Path $RunRoot "working-r$Replica.mdb"
        try {
            if (Test-Path -LiteralPath $workingPath -PathType Leaf) {
                Remove-Item -LiteralPath $workingPath -Force
            }
        }
        catch {
            $state.status = "fail"
            $state.detail += " Working-file cleanup failed: " + $_.Exception.Message
        }
    }
    return $state
}

[void][IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($RunRoot))
$resultPath = Join-Path $RunRoot "bootstrap-layout-job-result.json"
$replicas = New-Object Collections.ArrayList
try {
    foreach ($replica in 1..3) {
        [void]$replicas.Add((Invoke-Replica -Replica $replica))
    }
    $result = [ordered]@{
        development_only = $true
        status = "pass"
        detail = "Attempted each of the three bounded bootstrap-layout replicas once; per-replica status records partial outcomes."
        plan_sha256 = $PlanSha256
        replicas = @($replicas)
    }
    Write-JsonDocument -Path $resultPath -Document $result
    exit 0
}
catch {
    $result = [ordered]@{
        development_only = $true
        status = "fail"
        detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
        plan_sha256 = $PlanSha256
        replicas = @($replicas)
    }
    Write-JsonDocument -Path $resultPath -Document $result
    exit 1
}
