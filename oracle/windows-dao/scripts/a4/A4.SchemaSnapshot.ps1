Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A4Canonicalization =
    "after each Refresh require exactly the extant scheduled physical table names from expected_schema_by_checkpoint and exactly the extant scheduled index name A4IX_ID; include every field and index field; use no implementation-defined hidden/system index test; assign zero-based ordinals after those exact filters; sort each filtered collection by ordinal then strict Windows-1252 name bytes; reject duplicate ordinals or names; retain exact BSTR UTF-16 code units plus strict Windows-1252 and UTF-8 expected bytes without comparing either to physical bytes; emit integers as JSON integers and lowercase SHA-256"

function ConvertTo-A4Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
}

function Get-A4NameProjection {
    param([Parameter(Mandatory = $true)][string]$Name)
    $strict = New-Object Text.EncoderExceptionFallback
    $cp1252 = [Text.Encoding]::GetEncoding(
        1252, $strict, (New-Object Text.DecoderExceptionFallback)
    )
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $units = New-Object Collections.ArrayList
    foreach ($character in $Name.ToCharArray()) {
        [void]$units.Add([int][char]$character)
    }
    return [ordered]@{
        name = $Name
        name_utf16_code_units = @($units)
        name_windows_1252_hex = ConvertTo-A4Hex -Bytes $cp1252.GetBytes($Name)
        name_utf8_hex = ConvertTo-A4Hex -Bytes $utf8.GetBytes($Name)
    }
}

function Get-A4ExpectedDescriptors {
    param([Parameter(Mandatory = $true)][string]$CheckpointId)
    $property = $script:A4Plan.tables.expected_schema_by_checkpoint.PSObject.Properties[
        $CheckpointId
    ]
    if ($null -eq $property) {
        throw "A4 schema checkpoint is absent from the checked plan."
    }
    return @($property.Value)
}

function Get-A4Descriptor {
    param([Parameter(Mandatory = $true)][string]$Value)
    $parts = @($Value -split ":")
    if ($parts.Count -lt 2 -or $parts.Count -gt 3) {
        throw "A4 schema descriptor is malformed."
    }
    $role = [string]$parts[0]
    $layout = [string]$parts[$parts.Count - 1]
    $instance = if ($parts.Count -eq 3) {
        "$role-$([string]$parts[1])"
    } else { "$role-v1" }
    if ($role -notin $script:A4Roles -or
        $layout -notin @("id", "id+payload", "id+payload+index")) {
        throw "A4 schema descriptor is outside the checked grammar."
    }
    return [pscustomobject]@{
        Role = $role
        Instance = $instance
        HasPayload = $layout.Contains("+payload")
        HasIndex = $layout.Contains("+index")
    }
}

function Get-A4ScheduledTableNames {
    param([Parameter(Mandatory = $true)][object[]]$Descriptors)
    return @($Descriptors | ForEach-Object {
        $descriptor = Get-A4Descriptor -Value ([string]$_)
        [string]$script:A1RoleNames[$descriptor.Role]
    })
}

function Get-A4FilteredTableNames {
    param([Parameter(Mandatory = $true)][object]$TableDefinitions)
    $allNames = @($script:A4Plan.tables.physical_names)
    $found = New-Object Collections.ArrayList
    foreach ($table in @($TableDefinitions)) {
        $name = [string]$table.Name
        if ($name -cin $allNames) { [void]$found.Add($name) }
        Release-M1ComObject $table (New-Object Collections.ArrayList) `
            "A4 filtered table release"
    }
    return @($found)
}

function Get-A4FieldDocument {
    param(
        [Parameter(Mandatory = $true)][object]$Field,
        [Parameter(Mandatory = $true)][int]$Ordinal
    )
    $name = [string]$Field.Name
    $projection = Get-A4NameProjection -Name $name
    return [ordered]@{
        ordinal = $Ordinal
        ordinal_source = "Fields zero-based position after Refresh and the all-fields filter"
        name = $projection.name
        name_utf16_code_units = $projection.name_utf16_code_units
        name_windows_1252_hex = $projection.name_windows_1252_hex
        type = [int]$Field.Type
        size = [int]$Field.Size
        attributes = [int]$Field.Attributes
        required = [bool]$Field.Required
        allow_zero_length = if ($name -ceq "Payload") {
            [bool]$Field.AllowZeroLength
        } else { $null }
        name_utf8_hex = $projection.name_utf8_hex
    }
}

function Get-A4IndexFieldDocument {
    param(
        [Parameter(Mandatory = $true)][object]$Field,
        [Parameter(Mandatory = $true)][int]$Ordinal
    )
    $projection = Get-A4NameProjection -Name ([string]$Field.Name)
    return [ordered]@{
        ordinal = $Ordinal
        ordinal_source = "Index.Fields zero-based position after Refresh and the all-fields filter"
        name = $projection.name
        name_utf16_code_units = $projection.name_utf16_code_units
        name_windows_1252_hex = $projection.name_windows_1252_hex
        descending = (([int]$Field.Attributes -band 1) -ne 0)
        name_utf8_hex = $projection.name_utf8_hex
    }
}

function Get-A4IndexDocument {
    param(
        [Parameter(Mandatory = $true)][object]$Index,
        [Parameter(Mandatory = $true)][int]$Ordinal
    )
    $projection = Get-A4NameProjection -Name ([string]$Index.Name)
    $fields = $null; $fieldRows = New-Object Collections.ArrayList
    $cleanup = New-Object Collections.ArrayList; $primary = $null
    try {
        $fields = $Index.Fields
        $fields.Refresh()
        $fieldOrdinal = 0
        foreach ($field in @($fields)) {
            try {
                [void]$fieldRows.Add((Get-A4IndexFieldDocument `
                    -Field $field -Ordinal $fieldOrdinal))
                $fieldOrdinal++
            }
            finally {
                Release-M1ComObject $field $cleanup "A4 index snapshot field release"
            }
        }
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject $fields $cleanup "A4 index snapshot fields release"
    }
    Complete-M1DaoHelper $primary $cleanup "A4 index snapshot"
    return [ordered]@{
        ordinal = $Ordinal
        ordinal_source = "Indexes zero-based position after Refresh and exact A4IX_ID scheduled-name filtering"
        name = $projection.name
        name_utf16_code_units = $projection.name_utf16_code_units
        name_windows_1252_hex = $projection.name_windows_1252_hex
        attributes = [int]$Index.Attributes
        primary = [bool]$Index.Primary
        unique = [bool]$Index.Unique
        required = [bool]$Index.Required
        ignore_nulls = [bool]$Index.IgnoreNulls
        fields = @($fieldRows)
        name_utf8_hex = $projection.name_utf8_hex
    }
}

function Get-A4TableDocument {
    param(
        [Parameter(Mandatory = $true)][object]$Database,
        [Parameter(Mandatory = $true)][object]$Table,
        [Parameter(Mandatory = $true)][pscustomobject]$Descriptor,
        [Parameter(Mandatory = $true)][int]$Ordinal
    )
    $fields = $null; $indexes = $null
    $fieldRows = New-Object Collections.ArrayList
    $indexRows = New-Object Collections.ArrayList
    $cleanup = New-Object Collections.ArrayList; $primary = $null
    $semantic = $null
    try {
        $fields = $Table.Fields
        $fields.Refresh()
        foreach ($field in @($fields)) {
            try {
                [void]$fieldRows.Add((Get-A4FieldDocument `
                    -Field $field -Ordinal $fieldRows.Count))
            }
            finally {
                Release-M1ComObject $field $cleanup "A4 schema field release"
            }
        }
        $expectedFieldCount = if ($Descriptor.HasPayload) { 2 } else { 1 }
        if ($fieldRows.Count -ne $expectedFieldCount -or
            [string]$fieldRows[0].name -cne "Id" -or
            ($Descriptor.HasPayload -and [string]$fieldRows[1].name -cne "Payload")) {
            throw "A4 table fields differ from the scheduled schema."
        }

        $indexes = $Table.Indexes
        $indexes.Refresh()
        foreach ($index in @($indexes)) {
            try {
                if ([string]$index.Name -ceq
                    [string]$script:A4Plan.tables.definition.index.name) {
                    [void]$indexRows.Add((Get-A4IndexDocument `
                        -Index $index -Ordinal $indexRows.Count))
                }
            }
            finally {
                Release-M1ComObject $index $cleanup "A4 schema index release"
            }
        }
        if ($indexRows.Count -ne [int]$Descriptor.HasIndex) {
            throw "A4 scheduled index inventory differs."
        }

        if ($Descriptor.HasPayload) {
            $expected = Get-A1ExpectedSemanticResult `
                -Role $Descriptor.Role -Rows $script:A1Rows[$Descriptor.Role]
            $semantic = Read-A1SemanticTable -Database $Database `
                -Role $Descriptor.Role -Expected $expected
        }
        else {
            if ($script:A1Rows[$Descriptor.Role].Count -ne 0) {
                throw "A4 Id-only table unexpectedly has rows."
            }
            $semantic = [ordered]@{
                role = $Descriptor.Role; row_count = 0
                rolling_sha256 =
                    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            }
        }
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject $indexes $cleanup "A4 schema indexes release"
        Release-M1ComObject $fields $cleanup "A4 schema fields release"
    }
    Complete-M1DaoHelper $primary $cleanup "A4 table schema snapshot"
    $projection = Get-A4NameProjection -Name ([string]$Table.Name)
    return [pscustomobject]@{
        Document = [ordered]@{
            ordinal = $Ordinal
            ordinal_source = "TableDefs zero-based position after Refresh and exact extant scheduled-name filtering"
            logical_role = $Descriptor.Role
            lifecycle_instance = $Descriptor.Instance
            name = $projection.name
            name_utf16_code_units = $projection.name_utf16_code_units
            name_windows_1252_hex = $projection.name_windows_1252_hex
            attributes = [int]$Table.Attributes
            row_count = [int]$semantic.row_count
            rolling_row_sha256 = [string]$semantic.rolling_sha256
            fields = @($fieldRows)
            indexes = @($indexRows)
            name_utf8_hex = $projection.name_utf8_hex
        }
        Semantic = $semantic
    }
}

function Read-A4SchemaSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [Parameter(Mandatory = $true)][int]$Ordinal
    )
    Assert-A4Quiescent
    $before = Get-M1FileSha256 -Path $script:A4DatabasePath
    $descriptors = @(Get-A4ExpectedDescriptors -CheckpointId $CheckpointId)
    $expectedNames = @(Get-A4ScheduledTableNames -Descriptors $descriptors)
    $database = $null; $definitions = $null
    $tables = New-Object Collections.ArrayList
    $semantic = New-Object Collections.ArrayList
    $cleanup = New-Object Collections.ArrayList; $primary = $null
    try {
        $database = $script:A4Workspace.OpenDatabase(
            $script:A4DatabasePath, $false, $true, ""
        )
        $definitions = $database.TableDefs
        $definitions.Refresh()
        $actualNames = @(Get-A4FilteredTableNames -TableDefinitions $definitions)
        if (($actualNames -join "`n") -cne ($expectedNames -join "`n")) {
            throw "A4 scheduled table inventory differs after read-only reopen."
        }
        for ($ordinalIndex = 0; $ordinalIndex -lt $descriptors.Count; $ordinalIndex++) {
            $descriptor = Get-A4Descriptor -Value ([string]$descriptors[$ordinalIndex])
            $table = $null
            try {
                $table = $definitions.Item([string]$expectedNames[$ordinalIndex])
                $row = Get-A4TableDocument -Database $database -Table $table `
                    -Descriptor $descriptor -Ordinal $ordinalIndex
                [void]$tables.Add($row.Document)
                [void]$semantic.Add($row.Semantic)
            }
            finally {
                Release-M1ComObject $table $cleanup "A4 schema table release"
            }
        }
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject $definitions $cleanup "A4 schema definitions release"
        Close-M1ComObject $database $cleanup "A4 read-only schema database close"
        Release-M1ComObject $database $cleanup "A4 read-only schema database release"
    }
    Complete-M1DaoHelper $primary $cleanup "A4 read-only schema snapshot"
    Assert-A4Quiescent
    $after = Get-M1FileSha256 -Path $script:A4DatabasePath
    if ($before -cne $after) {
        throw "A4 read-only schema observation changed the database bytes."
    }
    return [pscustomobject]@{
        Semantic = @($semantic)
        Document = [ordered]@{
            protocol_version = "1.0.0"
            document_type = "dao_a4_schema_snapshot"
            experiment_id = $script:A4ExperimentId
            plan_sha256 = $script:A4PlanSha256
            revision_plan_sha256 = $script:A4RevisionPlanSha256
            producer_commit = $script:A4ProducerCommit
            campaign_id = $script:A4CampaignId
            environment_sha256 = $script:A4EnvironmentSha256
            provider_sha256 = $script:A4ProviderSha256
            replica = $script:A4Replica
            checkpoint_id = $CheckpointId
            ordinal = $Ordinal
            windows_ansi_code_page = 1252
            database_sha256_before_read = $before
            database_sha256_after_read = $after
            database_unchanged_by_read = $true
            dao_identifier_observable = $false
            identity_oracle = "listed_operation_instance_equality_only"
            canonicalization = $script:A4Canonicalization
            tables = @($tables)
        }
    }
}
