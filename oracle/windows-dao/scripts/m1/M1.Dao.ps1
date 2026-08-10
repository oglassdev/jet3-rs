Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "M1.DaoValues.ps1")

# DAO numeric API values are public enumeration values recorded in
# docs/PROVENANCE.md SRC-0002, SRC-0003, and SRC-0009. They are oracle adapter
# constants only and are not MDB byte-format assertions.
$script:M1DbVersion30 = 32
$script:M1DbSystemObject = -2147483646
$script:M1DaoTypes = @{
    dbBinary = 9
    dbText = 10
    dbLongBinary = 11
    dbMemo = 12
}
$script:M1DaoTypeNames = @{
    9 = "dbBinary"
    10 = "dbText"
    11 = "dbLongBinary"
    12 = "dbMemo"
}
$script:M1MaxTables = 32
$script:M1MaxFields = 16
$script:M1MaxIndexes = 16
$script:M1MaxRows = 16
$script:M1MaxDatabaseBytes = 16MB

function New-M1DaoTable {
    param(
        [object]$Database,
        [object]$Arguments
    )

    $table = $null
    $field = $null
    $index = $null
    $indexField = $null
    $primaryError = $null
    $cleanupErrors = New-Object Collections.ArrayList
    try {
        $table = $Database.CreateTableDef([string]$Arguments.name)
        for ($fieldOrdinal = 0; $fieldOrdinal -lt $Arguments.fields.Count; $fieldOrdinal++) {
            $plan = $Arguments.fields[$fieldOrdinal]
            $daoType = $script:M1DaoTypes[[string]$plan.dao_type]
            if ($null -eq $daoType) {
                throw "The controlled table contains an unsupported DAO type."
            }
            if ([string]$plan.dao_type -eq "dbText") {
                $field = $table.CreateField(
                    [string]$plan.name,
                    [int]$daoType,
                    [int]$plan.size
                )
            }
            else {
                $field = $table.CreateField([string]$plan.name, [int]$daoType)
            }
            $field.Required = [bool]$plan.required
            $table.Fields.Append($field)
            Release-M1ComObject -Value $field -CleanupErrors $cleanupErrors `
                -Label "field release"
            $field = $null
        }
        for ($indexOrdinal = 0; $indexOrdinal -lt $Arguments.indexes.Count; $indexOrdinal++) {
            $indexPlan = $Arguments.indexes[$indexOrdinal]
            $index = $table.CreateIndex([string]$indexPlan.name)
            $index.Primary = [bool]$indexPlan.primary
            $index.Unique = [bool]$indexPlan.unique
            $index.Required = [bool]$indexPlan.required
            $index.IgnoreNulls = [bool]$indexPlan.ignore_nulls
            for (
                $indexFieldOrdinal = 0;
                $indexFieldOrdinal -lt $indexPlan.fields.Count;
                $indexFieldOrdinal++
            ) {
                $indexField = $index.CreateField(
                    [string]$indexPlan.fields[$indexFieldOrdinal]
                )
                $index.Fields.Append($indexField)
                Release-M1ComObject -Value $indexField `
                    -CleanupErrors $cleanupErrors -Label "index field release"
                $indexField = $null
            }
            $table.Indexes.Append($index)
            Release-M1ComObject -Value $index -CleanupErrors $cleanupErrors `
                -Label "index release"
            $index = $null
        }
        $Database.TableDefs.Append($table)
    }
    catch {
        $primaryError = $_
    }
    finally {
        Release-M1ComObject -Value $indexField -CleanupErrors $cleanupErrors `
            -Label "index field release"
        Release-M1ComObject -Value $index -CleanupErrors $cleanupErrors `
            -Label "index release"
        Release-M1ComObject -Value $field -CleanupErrors $cleanupErrors `
            -Label "field release"
        Release-M1ComObject -Value $table -CleanupErrors $cleanupErrors `
            -Label "table release"
    }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "create table"
}

function Add-M1DaoRow {
    param(
        [object]$Database,
        [object]$Arguments
    )

    $recordset = $null
    $field = $null
    $primaryError = $null
    $cleanupErrors = New-Object Collections.ArrayList
    try {
        $recordset = $Database.OpenRecordset([string]$Arguments.table)
        $recordset.AddNew()
        for ($ordinal = 0; $ordinal -lt $Arguments.values.Count; $ordinal++) {
            $plan = $Arguments.values[$ordinal]
            $field = $recordset.Fields.Item([string]$plan.field)
            $value = Get-M1DeclaredValue -ValuePlan $plan
            switch ([string]$plan.dao_type) {
                "dbBinary" {
                    if ($value.GetType() -ne [byte[]]) {
                        throw "dbBinary assignment requires exact System.Byte[]."
                    }
                    # EXP-0006: direct, non-enumerated System.Byte[] assignment.
                    $field.Value = $value
                }
                "dbLongBinary" {
                    if ($value.GetType() -ne [byte[]]) {
                        throw "dbLongBinary AppendChunk requires exact System.Byte[]."
                    }
                    # EXP-0006: AppendChunk(System.Byte[]) for dbLongBinary.
                    $field.AppendChunk($value)
                }
                "dbMemo" {
                    $field.AppendChunk([string]$value)
                }
                "dbText" {
                    $field.Value = [string]$value
                }
                default {
                    throw "Unsupported controlled DAO value type."
                }
            }
            Release-M1ComObject -Value $field -CleanupErrors $cleanupErrors `
                -Label "row field release"
            $field = $null
        }
        $recordset.Update()
    }
    catch {
        $primaryError = $_
    }
    finally {
        Release-M1ComObject -Value $field -CleanupErrors $cleanupErrors `
            -Label "row field release"
        Close-M1ComObject -Value $recordset -CleanupErrors $cleanupErrors `
            -Label "recordset.Close"
        Release-M1ComObject -Value $recordset -CleanupErrors $cleanupErrors `
            -Label "recordset release"
    }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "insert row"
}

function Get-M1ScenarioRowPlans {
    param([object]$Scenario)

    $plans = New-Object Collections.ArrayList
    for ($index = 0; $index -lt $Scenario.steps.Count; $index++) {
        $step = $Scenario.steps[$index]
        if ([string]$step.action -eq "insert_row") {
            [void]$plans.Add($step.arguments.values[0])
        }
    }
    return ,@($plans)
}

function Get-M1TypedReadback {
    param(
        [string]$DaoType,
        [object]$Raw
    )

    if ($DaoType -eq "dbBinary") {
        [void](Get-M1ValueIdentity -DaoType $DaoType -Value $Raw)
        return [ordered]@{ kind = "binary"; value = Get-M1LowerHex -Value $Raw }
    }
    if ($DaoType -eq "dbLongBinary") {
        [void](Get-M1ValueIdentity -DaoType $DaoType -Value $Raw)
        return [ordered]@{ kind = "ole"; value = Get-M1LowerHex -Value $Raw }
    }
    [void](Get-M1ValueIdentity -DaoType $DaoType -Value $Raw)
    if ($DaoType -eq "dbMemo") {
        return [ordered]@{ kind = "memo"; value = [string]$Raw }
    }
    return [ordered]@{ kind = "text"; value = [string]$Raw }
}

function Read-M1DaoTables {
    param(
        [object]$Database,
        [object]$Scenario
    )

    $tablePlans = @(
        $Scenario.steps |
            Where-Object { [string]$_.action -eq "create_table" }
    )
    $rowPlans = Get-M1ScenarioRowPlans -Scenario $Scenario
    $tables = New-Object Collections.ArrayList
    $observations = New-Object Collections.ArrayList
    $tableDefinitions = $null
    $primaryError = $null
    $cleanupErrors = New-Object Collections.ArrayList
    $result = $null
    try {
    $tableDefinitions = $Database.TableDefs
    if ([int]$tableDefinitions.Count -gt $script:M1MaxTables) {
        throw "DAO returned too many table definitions."
    }
    for ($tableOrdinal = 0; $tableOrdinal -lt $tableDefinitions.Count; $tableOrdinal++) {
        $table = $null
        try {
            $table = $tableDefinitions.Item($tableOrdinal)
            $attributes = [int]$table.Attributes
            if (($attributes -band $script:M1DbSystemObject) -ne 0) {
                continue
            }
            if ($tables.Count -ge $tablePlans.Count) {
                throw "DAO returned an unexpected user table."
            }
            $columns = New-Object Collections.ArrayList
            if ([int]$table.Fields.Count -gt $script:M1MaxFields) {
                throw "DAO returned too many fields."
            }
            for ($fieldOrdinal = 0; $fieldOrdinal -lt $table.Fields.Count; $fieldOrdinal++) {
                $field = $null
                try {
                    $field = $table.Fields.Item($fieldOrdinal)
                    $daoType = $script:M1DaoTypeNames[[int]$field.Type]
                    if ($null -eq $daoType) {
                        throw "DAO returned an unsupported controlled field type."
                    }
                    $required = [bool]$field.Required
                    $size = if (
                        $daoType -in @("dbText", "dbMemo", "dbLongBinary")
                    ) {
                        [int]$field.Size
                    }
                    else {
                        $null
                    }
                    [void]$columns.Add([ordered]@{
                        attributes = [int]$field.Attributes
                        auto_increment = $false
                        dao_type = $daoType
                        name = [string]$field.Name
                        nullable = -not $required
                        ordinal = $fieldOrdinal
                        properties = [ordered]@{}
                        required = $required
                        size = $size
                    })
                }
                finally {
                    Release-M1ComObject -Value $field `
                        -CleanupErrors $cleanupErrors -Label "field release"
                }
            }

            $indexes = New-Object Collections.ArrayList
            if ([int]$table.Indexes.Count -gt $script:M1MaxIndexes) {
                throw "DAO returned too many indexes."
            }
            for ($indexOrdinal = 0; $indexOrdinal -lt $table.Indexes.Count; $indexOrdinal++) {
                $index = $null
                try {
                    $index = $table.Indexes.Item($indexOrdinal)
                    $indexFields = New-Object Collections.ArrayList
                    if ([int]$index.Fields.Count -gt $script:M1MaxFields) {
                        throw "DAO returned too many index fields."
                    }
                    for (
                        $indexFieldOrdinal = 0;
                        $indexFieldOrdinal -lt $index.Fields.Count;
                        $indexFieldOrdinal++
                    ) {
                        $indexField = $null
                        try {
                            $indexField = $index.Fields.Item($indexFieldOrdinal)
                            if ([int]$indexField.Attributes -ne 0) {
                                throw "M1 permits only ascending index fields."
                            }
                            [void]$indexFields.Add([ordered]@{
                                descending = $false
                                name = [string]$indexField.Name
                            })
                        }
                        finally {
                            Release-M1ComObject -Value $indexField `
                                -CleanupErrors $cleanupErrors `
                                -Label "index field release"
                        }
                    }
                    [void]$indexes.Add([ordered]@{
                        fields = @($indexFields)
                        ignore_nulls = [bool]$index.IgnoreNulls
                        name = [string]$index.Name
                        primary = [bool]$index.Primary
                        properties = [ordered]@{}
                        required = [bool]$index.Required
                        unique = [bool]$index.Unique
                    })
                }
                finally {
                    Release-M1ComObject -Value $index `
                        -CleanupErrors $cleanupErrors -Label "index release"
                }
            }
            $sortedIndexes = @($indexes | Sort-Object -Property name)

            $rows = New-Object Collections.ArrayList
            $recordset = $null
            try {
                $recordset = $Database.OpenRecordset([string]$table.Name)
                $rawRows = New-Object Collections.ArrayList
                if (-not $recordset.EOF) {
                    $recordset.MoveFirst()
                }
                while (-not $recordset.EOF) {
                    if ($rawRows.Count -ge $script:M1MaxRows) {
                        throw "DAO returned too many rows."
                    }
                    $valueMap = [ordered]@{}
                    for ($fieldOrdinal = 0; $fieldOrdinal -lt $columns.Count; $fieldOrdinal++) {
                        $field = $null
                        try {
                            $column = $columns[$fieldOrdinal]
                            $field = $recordset.Fields.Item([string]$column.name)
                            $raw = $field.Value
                            $valueMap[[string]$column.name] = Get-M1TypedReadback `
                                -DaoType ([string]$column.dao_type) -Raw $raw
                        }
                        finally {
                            Release-M1ComObject -Value $field `
                                -CleanupErrors $cleanupErrors `
                                -Label "recordset field release"
                        }
                    }
                    [void]$rawRows.Add($valueMap)
                    $recordset.MoveNext()
                }
            }
            finally {
                Close-M1ComObject -Value $recordset `
                    -CleanupErrors $cleanupErrors -Label "recordset.Close"
                Release-M1ComObject -Value $recordset `
                    -CleanupErrors $cleanupErrors -Label "recordset release"
            }

            $sortedRawRows = @(
                $rawRows |
                    Sort-Object -Property {
                        ($_ | ConvertTo-Json -Depth 8 -Compress)
                    }
            )
            if ($sortedRawRows.Count -ne $rowPlans.Count) {
                throw "DAO row count differs from the controlled scenario."
            }
            for ($rowOrdinal = 0; $rowOrdinal -lt $sortedRawRows.Count; $rowOrdinal++) {
                $rowValues = $sortedRawRows[$rowOrdinal]
                $plan = $rowPlans[$rowOrdinal]
                $rawValue = $rowValues[[string]$plan.field].value
                if ([string]$plan.dao_type -in @("dbBinary", "dbLongBinary")) {
                    $rawValue = ConvertFrom-M1LowerHex -Value ([string]$rawValue)
                }
                [void]$observations.Add(
                    (New-M1ValueObservation -ValuePlan $plan `
                        -Readback $rawValue -RowOrdinal $rowOrdinal)
                )
                [void]$rows.Add([ordered]@{
                    canonical_key = ("{0:D8}" -f $rowOrdinal)
                    values = $rowValues
                })
            }

            [void]$tables.Add([ordered]@{
                attributes = $attributes
                columns = @($columns)
                indexes = @($sortedIndexes)
                kind = "user"
                name = [string]$table.Name
                properties = [ordered]@{}
                rows = @($rows)
            })
        }
        finally {
            Release-M1ComObject -Value $table -CleanupErrors $cleanupErrors `
                -Label "table release"
        }
    }
    $result = [ordered]@{
        observations = @($observations)
        tables = @($tables | Sort-Object -Property name)
    }
    }
    catch {
        $primaryError = $_
    }
    finally {
        Release-M1ComObject -Value $tableDefinitions `
            -CleanupErrors $cleanupErrors -Label "table definitions release"
    }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "DAO readback"
    return $result
}

function Flush-M1DaoDatabase {
    param([string]$Path)

    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -gt $script:M1MaxDatabaseBytes) {
        throw "DAO output exceeds the M1 database byte ceiling."
    }
    $stream = New-Object IO.FileStream(
        $file.FullName,
        [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

function Invoke-M1DaoScenario {
    param(
        [object]$Scenario,
        [object]$AcceptedProvider,
        [string]$WorkingRoot,
        [string]$GitCommit,
        [string]$RunId
    )

    $scenarioId = [string]$Scenario.scenario_id
    $databasePath = Join-Path $WorkingRoot ($scenarioId + ".mdb")
    $lockPath = $databasePath.Substring(0, $databasePath.Length - 4) + ".ldb"
    $entries = New-Object Collections.ArrayList
    $engine = $null
    $workspace = $null
    $database = $null
    $status = "error"
    $reason = "The DAO scenario did not complete."
    $phase = "activate_provider"
    $snapshot = $null
    $databaseRetainable = $false
    $primaryError = $null
    $finalError = $null
    $cleanupErrors = New-Object Collections.ArrayList

    try {
        $comType = [Type]::GetTypeFromProgID(
            [string]$AcceptedProvider.prog_id,
            $false
        )
        if ($null -eq $comType) {
            throw "Accepted provider ProgID cannot be resolved."
        }
        $actualClsid = "{" + $comType.GUID.ToString().ToUpperInvariant() + "}"
        if ($actualClsid -ine [string]$AcceptedProvider.clsid) {
            throw "Active COM registration CLSID differs from the environment."
        }
        $engine = [Activator]::CreateInstance($comType)
        if ([string]$engine.Version -cne [string]$AcceptedProvider.provider_version) {
            throw "Active DAO engine version differs from the environment."
        }
        $workspace = $engine.Workspaces.Item(0)
        Add-M1OperationEntry -Entries $entries -Action $phase -Status "pass" `
            -Detail "Activated the exact x86 provider bound by preflight." | Out-Null

        for ($stepIndex = 0; $stepIndex -lt $Scenario.steps.Count; $stepIndex++) {
            $step = $Scenario.steps[$stepIndex]
            $phase = [string]$step.action
            switch ($phase) {
                "create_database" {
                    $database = $workspace.CreateDatabase(
                        $databasePath,
                        [string]$step.arguments.locale,
                        $script:M1DbVersion30
                    )
                }
                "create_table" {
                    New-M1DaoTable -Database $database -Arguments $step.arguments
                }
                "insert_row" {
                    Add-M1DaoRow -Database $database -Arguments $step.arguments
                }
                "close_database" {
                    $database.Close()
                    Release-M1ComObject -Value $database
                    $database = $null
                }
                default {
                    throw "The checked scenario contains an unsupported action."
                }
            }
            Add-M1OperationEntry -Entries $entries -Action $phase -Status "pass" `
                -Detail ("Completed controlled action " + $phase + ".") | Out-Null
        }

        if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
            throw "DAO did not retain the controlled dbVersion30 database."
        }
        $phase = "reopen_database"
        $database = $workspace.OpenDatabase($databasePath)
        $readback = Read-M1DaoTables -Database $database -Scenario $Scenario
        $database.Close()
        Release-M1ComObject -Value $database
        $database = $null
        Add-M1OperationEntry -Entries $entries -Action $phase -Status "pass" `
            -Detail "Closed and reopened the database through DAO." | Out-Null

        $phase = "snapshot"
        Flush-M1DaoDatabase -Path $databasePath
        if (Test-Path -LiteralPath $lockPath) {
            throw "DAO left an unexpected companion lock file after close."
        }
        $databaseHash = Get-M1FileSha256 -Path $databasePath
        $databaseRetainable = $true
        $snapshot = [ordered]@{
            database_properties = [ordered]@{}
            database_sha256 = $databaseHash
            document_type = "canonical_snapshot"
            ordering = [ordered]@{
                columns = "ordinal_ascending"
                indexes = "name_codepoint_ascending"
                object_keys = "unicode_codepoint_ascending"
                objects = "name_codepoint_ascending"
                relationships = "name_codepoint_ascending"
                rows = "declared_key_then_canonical_value"
            }
            producer = [ordered]@{
                kind = "dao"
                source_revision = $GitCommit
            }
            protocol_version = "1.1.0"
            raw_preservation = @()
            relationships = @()
            scenario_id = $scenarioId
            tables = @($readback.tables)
        }
        $insertEntries = @(
            $entries | Where-Object { $_.action -eq "insert_row" }
        )
        if ($insertEntries.Count -ne $readback.observations.Count) {
            throw "Operation log insert count differs from DAO observations."
        }
        for ($index = 0; $index -lt $insertEntries.Count; $index++) {
            $insertEntries[$index].value_observations = @(
                $readback.observations[$index]
            )
        }
        Add-M1OperationEntry -Entries $entries -Action $phase -Status "pass" `
            -Detail "Captured exact DAO semantic readback and runtime identities." |
            Out-Null
        $status = "pass"
        $reason = "DAO completed and exactly read back the controlled scenario."
    }
    catch {
        $primaryError = $_
        if ($phase -eq "activate_provider") {
            $status = "blocked"
            $reason = "The exact probed DAO provider could not be activated."
        }
        elseif ($phase -in @(
            "create_database", "create_table", "insert_row",
            "close_database", "reopen_database", "snapshot"
        )) {
            $status = "fail"
            $reason = "DAO did not satisfy the controlled scenario."
        }
        else {
            $status = "error"
            $reason = "The M1 DAO adapter failed unexpectedly."
        }
        $finalError = Get-M1ExceptionRecord -ErrorRecord $_
        Add-M1OperationEntry -Entries $entries -Action $phase -Status $status `
            -Detail ($_.Exception.GetType().FullName + ": " + $_.Exception.Message) `
            -ErrorRecord $finalError | Out-Null
    }
    finally {
        if ($null -ne $database) {
            try {
                $database.Close()
            }
            catch {
                [void]$cleanupErrors.Add(
                    "database.Close: " + $_.Exception.Message
                )
            }
        }
        foreach ($item in @($database, $workspace, $engine)) {
            try {
                Release-M1ComObject -Value $item
            }
            catch {
                [void]$cleanupErrors.Add(
                    "COM release: " + $_.Exception.Message
                )
            }
        }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }

    if (
        -not $databaseRetainable -and
        (Test-Path -LiteralPath $databasePath -PathType Leaf)
    ) {
        try {
            Flush-M1DaoDatabase -Path $databasePath
            if (Test-Path -LiteralPath $lockPath) {
                throw "DAO retained a companion lock file."
            }
            $databaseRetainable = $true
        }
        catch {
            [void]$cleanupErrors.Add(
                "database retention: " + $_.Exception.Message
            )
        }
    }

    if ($cleanupErrors.Count -gt 0) {
        $status = "error"
        $reason = "DAO cleanup failed after scenario execution."
        if ($null -ne $primaryError) {
            $errorValue = Get-M1ExceptionRecord -ErrorRecord $primaryError `
                -CleanupErrors @($cleanupErrors)
        }
        else {
            $synthetic = New-Object Management.Automation.ErrorRecord(
                (New-Object InvalidOperationException($reason)),
                "M1Cleanup",
                [Management.Automation.ErrorCategory]::CloseError,
                $scenarioId
            )
            $errorValue = Get-M1ExceptionRecord -ErrorRecord $synthetic `
                -CleanupErrors @($cleanupErrors)
        }
        $finalError = $errorValue
        $lastEntry = $entries[$entries.Count - 1]
        $lastEntry.status = "error"
        $lastEntry.detail = Get-M1SafeText -Value ($cleanupErrors -join "; ")
        $lastEntry.error = $errorValue
    }
    Add-M1OperationEntry -Entries $entries -Action "finalize" -Status $status `
        -Detail $reason -ErrorRecord $finalError | Out-Null

    $operationLog = [ordered]@{
        document_type = "dao_operation_log"
        entries = @($entries)
        final_status = $status
        git_commit = $GitCommit
        protocol_version = "1.1.0"
        run_id = $RunId
        scenario_id = $scenarioId
    }
    return [ordered]@{
        database_path = if ($databaseRetainable) {
            $databasePath
        }
        else {
            $null
        }
        operation_log = $operationLog
        reason = $reason
        snapshot = $snapshot
        status = $status
    }
}
