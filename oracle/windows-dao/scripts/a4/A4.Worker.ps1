[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$WorkingRoot,
    [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$ProducerCommit,
    [Parameter(Mandatory = $true)][string]$CampaignId,
    [Parameter(Mandatory = $true)][string]$MatrixJobId,
    [Parameter(Mandatory = $true)][string]$PlanSha256,
    [Parameter(Mandatory = $true)][string]$RevisionPlanSha256,
    [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
    [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$m1Root = Join-Path $RepositoryRoot "oracle/windows-dao/scripts/m1"
. (Join-Path $m1Root "M1.Preflight.ps1")
. (Join-Path $m1Root "M1.Publication.ps1")
. (Join-Path $m1Root "M1.DaoValues.ps1")
. (Join-Path $PSScriptRoot "A4.PageStore.ps1")
. (Join-Path $PSScriptRoot "A4.Progress.ps1")
. (Join-Path $PSScriptRoot "A4.SchemaSnapshot.ps1")

$script:A4ExperimentId = "DAO-A4-ROW-ANCHORED-MAPS-001"
$script:A4FrozenPlanSha256 = `
    "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
$script:A4RequiredPlanPath = `
    "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json"
$script:A4RevisionPlanSha256 = `
    "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
$script:A1DbVersion30 = 32
$script:A1DbLong = 4
$script:A1DbText = 10
$script:A1DbFixedField = 1
$script:A1DbOpenSnapshot = 4
$script:A4Locale = ";LANGID=0x0409;CP=1252;COUNTRY=0"

function Assert-A4PlanChain {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    if ([string]$Plan.preregistration.status -cne "frozen_before_acquisition" -or
        $Plan.preregistration.acquisition_started -ne $false -or
        [string]$Plan.implementation_rebinding.required_plan_path -cne
            $script:A4RequiredPlanPath -or
        [string]$Plan.implementation_rebinding.required_experiment_id -cne
            $script:A4ExperimentId) {
        throw "A4 base-plan chain or implementation binding drifted."
    }
}

function Assert-A4WorkerPlan {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    $ids = @($Plan.checkpoint_design.checkpoint_ids)
    $names = @($Plan.tables.physical_names)
    $roles = @($Plan.tables.logical_roles)
    $binding = @($Plan.tables.role_bindings | Where-Object {
        [int]$_.replica -eq $script:A4Replica
    })
    if ([string]$Plan.experiment_id -cne $script:A4ExperimentId -or
        [string]$Plan.document_type -cne "dao_a4_row_anchored_maps_plan" -or
        [int]$Plan.replicas.count -ne 3 -or $binding.Count -ne 1 -or
        $names.Count -ne 4 -or @($names | Select-Object -Unique).Count -ne 4 -or
        ($roles -join ",") -cne "T1,T2,T3,T4" -or
        @($names | ForEach-Object { $_.Length } | Select-Object -Unique).Count -ne 1) {
        throw "A4 plan identity, replica, or table design drifted."
    }
    if ($ids.Count -ne 25 -or $ids.Count -ne [int]$Plan.checkpoint_design.count -or
        $ids.Count -ne [int]$Plan.bounds.planned_checkpoints_per_replica -or
        $ids.Count -gt [int]$Plan.bounds.max_checkpoints_per_replica -or
        @($ids | Select-Object -Unique).Count -ne $ids.Count) {
        throw "A4 checkpoint enumeration is not exact or bounded."
    }
    foreach ($id in $ids) {
        if ($null -eq $Plan.tables.checkpoint_operations.PSObject.Properties[$id]) {
            throw "A4 checkpoint lacks its plan-derived DAO operation."
        }
    }
    $definition = $Plan.tables.definition
    $row = $Plan.tables.row_algorithm
    $bounds = $Plan.bounds
    if (@($definition.fields).Count -ne 2 -or
        [string]$definition.fields[0].name -cne "Id" -or
        [string]$definition.fields[0].dao_type -cne "dbLong" -or
        [string]$definition.fields[1].name -cne "Payload" -or
        [string]$definition.fields[1].dao_type -cne "dbText" -or
        [int]$definition.fields[1].size -ne 240 -or
        $definition.fields[1].fixed_length -ne $true -or
        [string]$definition.index.name -cne "A4IX_ID" -or
        $definition.index.unique -ne $false -or
        [int]$row.growth_batch_rows -ne 32 -or
        [int]$Plan.page_capture.page_size -ne 2048 -or
        [long]$bounds.max_final_pages_per_replica -ne 20480 -or
        [long]$bounds.max_logical_checkpoint_read_bytes_per_replica -ne 2GB -or
        [long]$bounds.max_inserted_rows_per_replica -ne 200000 -or
        [long]$bounds.max_changed_hash_entries_per_replica -ne 65536 -or
        [long]$bounds.max_unique_page_blobs -ne 65536 -or
        [long]$bounds.max_retained_page_store_bytes -ne 128MB -or
        [long]$bounds.max_bundle_bytes -ne 768MB -or
        [long]$bounds.max_json_bytes -ne 64MB -or
        [int]$bounds.worker_timeout_seconds_per_replica -ne 1700 -or
        [long]$bounds.max_child_log_bytes -ne 1MB -or
        [long]$bounds.max_companion_bytes_per_checkpoint -ne 64KB) {
        throw "A4 schema, row algorithm, or resource bounds drifted."
    }
    return $binding[0]
}

function Get-A4Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$Id
    )
    $seed = "A4|$Role|$($Id.ToString('D10'))|"
    $builder = New-Object Text.StringBuilder 240
    while ($builder.Length -lt 240) { [void]$builder.Append($seed) }
    return $builder.ToString(0, 240)
}

function Invoke-A4WithDatabase {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [switch]$Create
    )
    $database = $null
    $cleanup = New-Object Collections.ArrayList
    $primary = $null
    $result = $null
    try {
        if ($Create) {
            $database = $script:A4Workspace.CreateDatabase(
                $script:A4DatabasePath, $script:A4Locale,
                $script:A1DbVersion30
            )
        }
        else { $database = $script:A4Workspace.OpenDatabase($script:A4DatabasePath) }
        if ([string]$database.Version -cne "3.0") {
            throw "A4 database version differs from Jet 3."
        }
        $result = & $Action $database
    }
    catch { $primary = $_ }
    finally {
        Close-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A4 database close"
        Release-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A4 database release"
    }
    Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
        -Label "A4 database action"
    return $result
}

function Add-A4Table {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$WithPayload
    )
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A4WithDatabase -Action {
        param($database)
        $table = $null; $fields = $null; $idField = $null
        $payloadField = $null; $tableDefinitions = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $table = $database.CreateTableDef($name)
            $table.Attributes = [int]$script:A4Plan.tables.definition.table_attributes_numeric
            $fields = $table.Fields
            $idField = $table.CreateField("Id", $script:A1DbLong)
            $idField.Attributes = 0
            $idField.Required = $false
            $fields.Append($idField)
            if ($WithPayload) {
                $payloadField = $table.CreateField("Payload", $script:A1DbText, 240)
                $payloadField.Attributes = $script:A1DbFixedField
                $payloadField.Required = $false
                $payloadField.AllowZeroLength = $false
                $fields.Append($payloadField)
            }
            $tableDefinitions = $database.TableDefs
            $tableDefinitions.Append($table)
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $tableDefinitions $cleanup "A4 table definitions release"
            Release-M1ComObject $payloadField $cleanup "A4 payload field release"
            Release-M1ComObject $idField $cleanup "A4 id field release"
            Release-M1ComObject $fields $cleanup "A4 fields release"
            Release-M1ComObject $table $cleanup "A4 table release"
        }
        Complete-M1DaoHelper $primary $cleanup "A4 table creation"
    } | Out-Null
    $script:A1Extant[$Role] = $true
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
    Set-A4ExpectedSemanticDirty -Role $Role
}

function Add-A4PayloadField {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A4WithDatabase -Action {
        param($database)
        $definitions = $null; $table = $null; $fields = $null; $field = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $definitions = $database.TableDefs
            $definitions.Refresh()
            $table = $definitions.Item($name)
            $fields = $table.Fields
            $field = $table.CreateField("Payload", $script:A1DbText, 240)
            $field.Attributes = $script:A1DbFixedField
            $field.Required = $false
            $field.AllowZeroLength = $false
            $fields.Append($field)
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $field $cleanup "A4 appended field release"
            Release-M1ComObject $fields $cleanup "A4 appended fields release"
            Release-M1ComObject $table $cleanup "A4 appended table release"
            Release-M1ComObject $definitions $cleanup "A4 appended definitions release"
        }
        Complete-M1DaoHelper $primary $cleanup "A4 payload field append"
    } | Out-Null
    Set-A4ExpectedSemanticDirty -Role $Role
}

function Add-A4Index {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    $indexName = [string]$script:A4Plan.tables.definition.index.name
    Invoke-A4WithDatabase -Action {
        param($database)
        $definitions = $null; $table = $null; $indexes = $null
        $index = $null; $indexFields = $null; $indexField = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $definitions = $database.TableDefs
            $definitions.Refresh()
            $table = $definitions.Item($name)
            $indexes = $table.Indexes
            $index = $table.CreateIndex($indexName)
            $index.Primary = $false
            $index.Unique = $false
            $index.Required = $false
            $index.IgnoreNulls = $false
            $indexFields = $index.Fields
            $indexField = $index.CreateField("Id")
            $indexField.Attributes = 0
            $indexFields.Append($indexField)
            $indexes.Append($index)
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $indexField $cleanup "A4 index field release"
            Release-M1ComObject $indexFields $cleanup "A4 index fields release"
            Release-M1ComObject $index $cleanup "A4 index release"
            Release-M1ComObject $indexes $cleanup "A4 indexes release"
            Release-M1ComObject $table $cleanup "A4 indexed table release"
            Release-M1ComObject $definitions $cleanup "A4 indexed definitions release"
        }
        Complete-M1DaoHelper $primary $cleanup "A4 index append"
    } | Out-Null
}

function Remove-A4Table {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A4WithDatabase -Action {
        param($database)
        $definitions = $null; $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try { $definitions = $database.TableDefs; $definitions.Delete($name) }
        catch { $primary = $_ }
        finally { Release-M1ComObject $definitions $cleanup "A4 table definitions release" }
        Complete-M1DaoHelper $primary $cleanup "A4 table deletion"
    } | Out-Null
    $script:A1Extant[$Role] = $false
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
    Set-A4ExpectedSemanticDirty -Role $Role
}

function Add-A4Ids {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int[]]$Ids
    )
    if ($Ids.Count -lt 1 -or
        $script:A4InsertedRows -gt (200000 - $Ids.Count)) {
        throw "A4 insertion would violate its row ceiling."
    }
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A4WithDatabase -Action {
        param($database)
        $recordset = $null; $fields = $null; $idField = $null
        $payloadField = $null; $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $recordset = $database.OpenRecordset($name, 2, 0)
            $fields = $recordset.Fields
            $idField = $fields.Item("Id")
            $payloadField = $fields.Item("Payload")
            foreach ($id in $Ids) {
                $recordset.AddNew()
                $idField.Value = [Int32]$id
                $payloadField.Value = [string](Get-A4Payload -Role $Role -Id $id)
                $recordset.Update()
            }
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $payloadField $cleanup "A4 payload field release"
            Release-M1ComObject $idField $cleanup "A4 id field release"
            Release-M1ComObject $fields $cleanup "A4 recordset fields release"
            Close-M1ComObject $recordset $cleanup "A4 insert recordset close"
            Release-M1ComObject $recordset $cleanup "A4 insert recordset release"
        }
        Complete-M1DaoHelper $primary $cleanup "A4 row insertion"
    } | Out-Null
    foreach ($id in $Ids) { [void]$script:A1Rows[$Role].Add([int]$id) }
    $script:A4InsertedRows += $Ids.Count
    Set-A4ExpectedSemanticDirty -Role $Role
}

function Add-A4RowBatch {
    param([Parameter(Mandatory = $true)][string]$Role)
    $first = [int]$script:A1NextId[$Role]
    $ids = New-Object int[] 32
    for ($offset = 0; $offset -lt 32; $offset++) {
        $ids[$offset] = [int]($first + $offset)
    }
    Add-A4Ids -Role $Role -Ids $ids
    $script:A1NextId[$Role] = [int]($first + 32)
}

function Remove-A4AllT1Rows {
    $script:A4DeletedT1Ids = [int[]]@($script:A1Rows["T1"] | Sort-Object)
    if ($script:A4DeletedT1Ids.Count -lt 1) {
        throw "A4 full-delete checkpoint requires nonempty T1."
    }
    $name = [string]$script:A1RoleNames["T1"]
    $expectedIds = [int[]]$script:A4DeletedT1Ids
    Invoke-A4WithDatabase -Action {
        param($database)
        $recordset = $null; $fields = $null; $idField = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $sql = "SELECT Id FROM [$name] ORDER BY Id"
            $recordset = $database.OpenRecordset($sql, 2, 0)
            $fields = $recordset.Fields
            $idField = $fields.Item("Id")
            foreach ($expectedId in $expectedIds) {
                if ($recordset.EOF -or [int]$idField.Value -ne $expectedId) {
                    throw "A4 T1 delete order differs from expected Id order."
                }
                $recordset.Delete()
                $recordset.MoveNext()
            }
            if (-not $recordset.EOF) { throw "A4 T1 delete left unexpected rows." }
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $idField $cleanup "A4 delete id field release"
            Release-M1ComObject $fields $cleanup "A4 delete fields release"
            Close-M1ComObject $recordset $cleanup "A4 delete recordset close"
            Release-M1ComObject $recordset $cleanup "A4 delete recordset release"
        }
        Complete-M1DaoHelper $primary $cleanup "A4 full T1 deletion"
    } | Out-Null
    $script:A1Rows["T1"].Clear()
    Set-A4ExpectedSemanticDirty -Role "T1"
}

function Restore-A4AllT1Rows {
    $ids = [int[]]@($script:A4DeletedT1Ids)
    if ($ids.Count -lt 1) { throw "A4 T1 reinsert set is empty." }
    Add-A4Ids -Role "T1" -Ids $ids
}

function Get-A4ClosedPageCount {
    Assert-M1NoReparseComponents -Path $script:A4DatabasePath
    $stream = $null
    try {
        try {
            $stream = New-Object IO.FileStream(
                $script:A4DatabasePath, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::None, 2048,
                [IO.FileOptions]::SequentialScan
            )
        }
        catch [IO.IOException] {
            $nativeCode = ($_.Exception.HResult -band 0xffff)
            if ($nativeCode -notin @(32, 33)) { throw }
            [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            $stream = New-Object IO.FileStream(
                $script:A4DatabasePath, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::None, 2048,
                [IO.FileOptions]::SequentialScan
            )
        }
        if ($stream.Length -lt 2048 -or ($stream.Length % 2048) -ne 0) {
            throw "A4 database length is not an exact page sequence."
        }
        $pages = [long]($stream.Length / 2048)
        if ($pages -gt 20480) { throw "A4 final-page ceiling was exceeded." }
        return $pages
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Assert-A4Quiescent {
    $companion = [IO.Path]::ChangeExtension($script:A4DatabasePath, ".ldb")
    if ([IO.File]::Exists($companion)) {
        $item = Get-Item -LiteralPath $companion -Force
        if ($item.Length -gt 64KB) {
            throw "A4 companion exceeded its checkpoint byte ceiling."
        }
        throw "A4 DAO lock companion remains after close."
    }
    if ([IO.Directory]::Exists($companion)) {
        throw "A4 DAO lock companion was replaced by a directory."
    }
}

function Add-A4Checkpoint {
    param(
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [AllowNull()][object]$Target,
        [long]$BaselinePages = -1
    )
    Assert-A4Quiescent
    $ordinal = [int]$script:A4Checkpoints.Count
    $schema = Read-A4SchemaSnapshot -CheckpointId $CheckpointId `
        -Ordinal $ordinal
    $semantic = @($schema.Semantic)
    $schemaLocator = "schema-snapshots/replica-{0:D2}/{1:D2}-{2}.json" -f @(
        $script:A4Replica, $ordinal, $CheckpointId
    )
    $schemaBytes = Write-A4JsonArtifact -RelativePath $schemaLocator `
        -Document $schema.Document
    Assert-A4Quiescent
    $snapshot = Read-A4PageSnapshot -Store $script:A4Store `
        -DatabasePath $script:A4DatabasePath `
        -PriorHashes $script:A4PriorHashes -PriorPages $script:A4PriorPages
    if ([string]$snapshot.file_sha256 -cne
        [string]$schema.Document.database_sha256_after_read) {
        throw "A4 schema snapshot and physical capture database hashes differ."
    }
    $threshold = $null; $overshoot = $null
    if ($null -ne $Target) {
        if ([string]$Target.kind -ceq "relative") {
            $threshold = [long]($BaselinePages + [long]$Target.pages)
        }
        else { $threshold = [long]$Target.pages }
        if ([long]$snapshot.page_count -lt $threshold) {
            throw "A4 checkpoint missed its frozen growth target."
        }
        $overshoot = [long]($snapshot.page_count - $threshold)
    }
    $indexDocument = [ordered]@{
        protocol_version = "1.0.0"; document_type = "dao_a4_page_index"
        experiment_id = $script:A4ExperimentId; plan_sha256 = $script:A4PlanSha256
        revision_plan_sha256 = $script:A4RevisionPlanSha256
        producer_commit = $script:A4ProducerCommit; campaign_id = $script:A4CampaignId
        environment_sha256 = $script:A4EnvironmentSha256
        provider_sha256 = $script:A4ProviderSha256; replica = $script:A4Replica
        checkpoint_id = $CheckpointId; ordinal = $ordinal
        predecessor_checkpoint_id = $script:A4PriorCheckpoint
        page_count = [long]$snapshot.page_count
        file_size_bytes = [long]$snapshot.file_bytes
        database_sha256 = [string]$snapshot.file_sha256
        ordered_page_sha256 = @($snapshot.hashes)
        changed_page_indices = @($snapshot.changed_pages | ForEach-Object {
            [long]$_.page_index
        })
    }
    $indexBytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        (($indexDocument | ConvertTo-Json -Depth 8 -Compress) + "`n")
    )
    if ($indexBytes.Length -gt 64MB -or
        $indexBytes.Length -gt (768MB - $script:A4Store.RetainedBytes)) {
        throw "A4 page index exceeds its retained byte ceiling."
    }
    $locator = "page-indexes/replica-{0:D2}/{1:D2}-{2}.json" -f @(
        $script:A4Replica, $ordinal, $CheckpointId
    )
    $path = Get-M1PayloadPath -Session $script:A4Store.Session `
        -RelativePath $locator
    Write-A4CreateNewBytes -Path $path -Bytes $indexBytes -MaximumBytes 64MB
    $script:A4Store.RetainedBytes += [long]$indexBytes.Length
    [void]$script:A4Checkpoints.Add([ordered]@{
        checkpoint_id = $CheckpointId; ordinal = $ordinal
        actual_file_pages = [long]$snapshot.page_count
        actual_size_bytes = [long]$snapshot.file_bytes
        target_baseline_pages = if ($BaselinePages -ge 0) { $BaselinePages } else { $null }
        target_threshold_pages = $threshold; target_overshoot_pages = $overshoot
        inserted_rows_total = [long]$script:A4InsertedRows
        table_row_counts = [ordered]@{
            T1 = [int]$script:A1Rows["T1"].Count
            T2 = [int]$script:A1Rows["T2"].Count
            T3 = [int]$script:A1Rows["T3"].Count
            T4 = [int]$script:A1Rows["T4"].Count
        }
        dao_reread = @($semantic); quiescent = $true
        post_close_companion = [ordered]@{
            present_after_close = $false; observed_size_bytes = 0
            retained_for_physical_analysis = $false
        }
        page_index = [ordered]@{
            path = $locator; sha256 = Get-A4LowerSha256 -Bytes $indexBytes
            size_bytes = [long]$indexBytes.Length
        }
        dao_schema_snapshot = [ordered]@{
            path = $schemaLocator
            sha256 = Get-A4LowerSha256 -Bytes $schemaBytes
            size_bytes = [long]$schemaBytes.Length
        }
    })
    $script:A4PriorHashes = [string[]]$snapshot.hashes
    $script:A4PriorPages = [byte[]]$snapshot.pages
    $script:A4PriorCheckpoint = $CheckpointId
    Add-A4ProgressRecord -Progress $script:A4Progress `
        -CheckpointId $CheckpointId -PageCount ([long]$snapshot.page_count)
}

function Add-A4UntilTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][long]$ThresholdPages
    )
    do {
        Add-A4RowBatch -Role $Role
        Assert-A4Quiescent
        $pages = Get-A4ClosedPageCount
    } while ($pages -lt $ThresholdPages)
}

function Invoke-A4Schedule {
    Invoke-A4WithDatabase -Create -Action { param($database) } | Out-Null
    Assert-A4Quiescent
    foreach ($value in @($script:A4Plan.checkpoint_design.checkpoint_ids)) {
        $id = [string]$value
        $operation = $script:A4Plan.tables.checkpoint_operations.PSObject.Properties[
            $id
        ]
        if ($null -eq $operation -or [string]::IsNullOrWhiteSpace(
            [string]$operation.Value
        )) { throw "A4 checkpoint operation is absent from the plan." }
        if ($id -in @("EMPTY", "EMPTY_R", "T1_IDLE_R", "T4_IDLE_R")) {
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "T1_CREATE_ID") {
            Add-A4Table -Role "T1"
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "T1_ADD_TEXT") {
            Add-A4PayloadField -Role "T1"
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "T1_ADD_INDEX") {
            Add-A4Index -Role "T1"
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -in @("T2_CREATE", "T2_RECREATE")) {
            Add-A4Table -Role "T2" -WithPayload
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "T2_DROP") {
            Remove-A4Table -Role "T2"
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -in @("T3_CREATE", "T4_CREATE")) {
            $role = $id.Substring(0, 2)
            Add-A4Table -Role $role -WithPayload
            Add-A4Checkpoint -CheckpointId $id -Target $null
            if ($id -ceq "T4_CREATE") {
                $latest = $script:A4Checkpoints[$script:A4Checkpoints.Count - 1]
                $script:A4Baselines["T1"] = [long]$latest.actual_file_pages
            }
            continue
        }
        if ($id -ceq "T1_DELETE_ALL") {
            Remove-A4AllT1Rows
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "T1_REINSERT_SAME") {
            Restore-A4AllT1Rows
            Add-A4Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -match "^(T1|T4)_REL_([0-9]{4})$") {
            $role = [string]$Matches[1]; $targetPages = [long]$Matches[2]
            if (-not $script:A4Baselines.ContainsKey($role)) {
                throw "A4 relative baseline was not captured at its plan checkpoint."
            }
            $baseline = [long]$script:A4Baselines[$role]
            Add-A4UntilTarget -Role $role -ThresholdPages ($baseline + $targetPages)
            Add-A4Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "relative"; pages = $targetPages }) `
                -BaselinePages $baseline
            continue
        }
        if ($id -match "^T3_ABS_([0-9]{5})$") {
            $targetPages = [long]$Matches[1]
            Add-A4UntilTarget -Role "T3" -ThresholdPages $targetPages
            Add-A4Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "absolute"; pages = $targetPages })
            if ($id -ceq "T3_ABS_16480") {
                $latest = $script:A4Checkpoints[$script:A4Checkpoints.Count - 1]
                $script:A4Baselines["T4"] = [long]$latest.actual_file_pages
            }
            continue
        }
        throw "A4 checkpoint operation is not implemented: $id"
    }
}

function Write-A4JsonArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Document
    )
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        (($Document | ConvertTo-Json -Depth 32 -Compress) + "`n")
    )
    if ($bytes.Length -gt 64MB -or
        $bytes.Length -gt (768MB - $script:A4Store.RetainedBytes)) {
        throw "A4 JSON artifact exceeds its byte ceiling."
    }
    $path = Get-M1PayloadPath -Session $script:A4Store.Session `
        -RelativePath $RelativePath
    Write-A4CreateNewBytes -Path $path -Bytes $bytes -MaximumBytes 64MB
    $script:A4Store.RetainedBytes += [long]$bytes.Length
    return $bytes
}

function Get-A4ArtifactRole {
    param([Parameter(Mandatory = $true)][string]$Relative)
    if ($Relative -ceq ("environment/replica-{0:D2}.json" -f $script:A4Replica)) {
        return "environment"
    }
    if ($Relative -ceq ("observations/replica-{0:D2}.json" -f $script:A4Replica)) {
        return "replica_observation"
    }
    $pageIndexPattern =
        "^page-indexes/replica-{0:D2}/[0-9]{{2}}-[A-Z0-9_]+\.json$" -f `
        $script:A4Replica
    if ($Relative -match $pageIndexPattern) {
        return "page_index"
    }
    $schemaSnapshotPattern =
        "^schema-snapshots/replica-{0:D2}/[0-9]{{2}}-[A-Z0-9_]+\.json$" -f `
        $script:A4Replica
    if ($Relative -match $schemaSnapshotPattern) {
        return "dao_schema_snapshot"
    }
    if ($Relative -match "^page-store/[0-9a-f]{64}\.page$") { return "page_blob" }
    throw "A4 output contains an unexpected artifact path: $Relative"
}

function Write-A4ReplicaManifest {
    $manifestRelative = "replica-artifacts/replica-{0:D2}-manifest.json" -f $script:A4Replica
    $files = @([IO.Directory]::EnumerateFiles(
        $script:A4Output, "*", [IO.SearchOption]::AllDirectories
    ))
    $records = New-Object Collections.ArrayList
    $pageBlobs = 0; $pageIndexes = 0; $schemaSnapshots = 0; $total = [long]0
    foreach ($path in @($files | Sort-Object)) {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $item.Length -lt 1 -or $item.Length -gt 64MB) {
            throw "A4 output artifact violates its file bound."
        }
        $relative = $path.Substring($script:A4Output.TrimEnd('\').Length + 1).Replace('\', '/')
        if ($relative -ceq $manifestRelative) { throw "A4 manifest already exists." }
        $role = Get-A4ArtifactRole -Relative $relative
        if ($role -ceq "page_blob") {
            $pageBlobs++
            if ($item.Length -ne 2048) { throw "A4 page blob has a non-page length." }
        }
        elseif ($role -ceq "page_index") { $pageIndexes++ }
        elseif ($role -ceq "dao_schema_snapshot") { $schemaSnapshots++ }
        if ($item.Length -gt (768MB - $total)) {
            throw "A4 replica output exceeds its retained-byte ceiling."
        }
        $total += [long]$item.Length
        [void]$records.Add([ordered]@{
            path = $relative; role = $role
            sha256 = Get-M1FileSha256 -Path $path
            size_bytes = [long]$item.Length
            media_type = if ($role -ceq "page_blob") {
                "application/octet-stream"
            } else { "application/json" }
        })
    }
    if ($pageIndexes -ne 25 -or $schemaSnapshots -ne 25 -or
        $pageBlobs -lt 1 -or $pageBlobs -gt 65536) {
        throw "A4 replica inventory does not contain its exact checkpoint artifacts."
    }
    $manifest = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a4_replica_artifact_manifest"
        experiment_id = $script:A4ExperimentId; plan_sha256 = $script:A4PlanSha256
        revision_plan_sha256 = $script:A4RevisionPlanSha256
        producer_commit = $script:A4ProducerCommit; campaign_id = $script:A4CampaignId
        matrix_job_id = $script:A4MatrixJobId; replica = $script:A4Replica
        environment_sha256 = $script:A4EnvironmentSha256
        provider_sha256 = $script:A4ProviderSha256; checkpoint_count = 25
        inventory_closed = $true; hashes_verified = $true; paths_closed = $true
        files = @($records)
    }
    [void](Write-A4JsonArtifact -RelativePath $manifestRelative -Document $manifest)
}

function Invoke-A4Worker {
    if ([IntPtr]::Size -ne 4 -or $PSVersionTable.PSVersion.Major -ne 5) {
        throw "A4 worker requires x86 Windows PowerShell 5."
    }
    $script:A4Replica = $Replica; $script:A4ProducerCommit = $ProducerCommit
    $script:A4CampaignId = $CampaignId; $script:A4MatrixJobId = $MatrixJobId
    $script:A4PlanSha256 = $PlanSha256
    $script:A4EnvironmentSha256 = $EnvironmentSha256
    $requiredPlan = [IO.Path]::GetFullPath(
        (Join-Path $RepositoryRoot $script:A4RequiredPlanPath)
    )
    if (-not ([IO.Path]::GetFullPath($PlanPath)).Equals(
        $requiredPlan, [StringComparison]::OrdinalIgnoreCase
    )) { throw "A4 worker rejects any plan other than the required A4 plan path." }
    $planInput = Read-A1CheckedJson -Path $PlanPath -MaximumBytes 1MB
    $environmentInput = Read-A1CheckedJson -Path $EnvironmentPath -MaximumBytes 1MB
    if ($PlanSha256 -cne $script:A4FrozenPlanSha256 -or
        $RevisionPlanSha256 -cne $script:A4RevisionPlanSha256 -or
        $planInput.sha256 -cne $PlanSha256 -or
        $environmentInput.sha256 -cne $EnvironmentSha256) {
        throw "A4 worker input bytes differ from controller bindings."
    }
    $script:A4Plan = $planInput.document
    $roleBinding = Assert-A4WorkerPlan -Plan $script:A4Plan
    Assert-A4PlanChain -Plan $script:A4Plan
    $environment = $environmentInput.document
    if ([string]$environment.document_type -cne "dao_a4_environment" -or
        [string]$environment.experiment_id -cne $script:A4ExperimentId -or
        [string]$environment.plan_sha256 -cne $PlanSha256 -or
        [string]$environment.revision_plan_sha256 -cne $RevisionPlanSha256 -or
        [string]$environment.producer_commit -cne $ProducerCommit -or
        [string]$environment.repository_url -cne
            "https://github.com/oglassdev/jet3-rs.git" -or
        [string]$environment.campaign_id -cne $CampaignId -or
        [int]$environment.replica -ne $Replica -or
        [string]$environment.matrix_job_id -cne $MatrixJobId -or
        [string]$environment.status -cne "ready" -or
        [string]$environment.host.process_architecture -cne "x86" -or
        [int]$environment.host.windows_ansi_code_page -ne 1252 -or
        [string]$environment.provider.prog_id -cne "DAO.DBEngine.36") {
        throw "A4 published environment differs from the worker binding."
    }
    $accepted = $environment.provider
    [void](Assert-M1CurrentRegistration -AcceptedProvider $accepted)
    if ((Get-M1FileSha256 -Path ([string]$accepted.server_path)) -cne
        [string]$accepted.server_sha256) {
        throw "A4 provider binary differs from its environment digest."
    }
    $script:A4ProviderSha256 = [string]$accepted.server_sha256
    $providerType = [Type]::GetTypeFromProgID([string]$accepted.prog_id, $false)
    if ($null -eq $providerType -or
        $providerType.GUID.ToString("B").ToUpperInvariant() -cne
        ([string]$accepted.clsid).ToUpperInvariant()) {
        throw "A4 provider activation binding differs from preflight."
    }
    $script:A4Output = [IO.Path]::GetFullPath($OutputRoot)
    $working = [IO.Path]::GetFullPath($WorkingRoot)
    Assert-M1NoReparseComponents -Path $script:A4Output
    Assert-M1NoReparseComponents -Path $working
    $script:A4Progress = Open-A4WorkerProgress `
        -DiagnosticsRoot $DiagnosticsRoot -Replica $Replica
    $replicaRoot = Join-Path $working ("replica-{0:D2}" -f $Replica)
    if ([IO.Directory]::Exists($replicaRoot)) {
        throw "A4 private replica directory already exists."
    }
    [void][IO.Directory]::CreateDirectory($replicaRoot)
    $script:A4DatabasePath = Join-Path $replicaRoot "ACQUISITION.MDB"
    $script:A1RoleNames = @{}
    $script:A4Roles = @($script:A4Plan.tables.logical_roles)
    foreach ($role in $script:A4Roles) {
        $script:A1RoleNames[$role] = [string]$roleBinding.$role
    }
    $script:A1Extant = @{}
    $script:A1Rows = @{}; $script:A1NextId = @{}
    $script:A1ExpectedSemanticCache = @{}
    foreach ($role in $script:A4Roles) {
        $script:A1Extant[$role] = $false
        $script:A1Rows[$role] = New-Object 'Collections.Generic.HashSet[int]'
        $script:A1NextId[$role] = 1
    }
    $script:A4Baselines = @{}; $script:A4InsertedRows = 0
    $script:A4Checkpoints = New-Object Collections.ArrayList
    $script:A4PriorHashes = $null; $script:A4PriorPages = $null
    $script:A4PriorCheckpoint = $null
    $session = [pscustomobject]@{ StagingBundle = $script:A4Output }
    $script:A4Store = New-A4PageStore -Session $session
    $engine = $null; $workspaces = $null; $workspace = $null
    $cleanup = New-Object Collections.ArrayList; $primary = $null
    try {
        $engine = [Activator]::CreateInstance($providerType)
        if ([string]$engine.Version -cne [string]$accepted.provider_version) {
            throw "A4 DBEngine version differs from the bound provider."
        }
        $workspaces = $engine.Workspaces
        $workspace = $workspaces.Item([int]0)
        $script:A4Workspace = $workspace
        Invoke-A4Schedule
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject $workspace $cleanup "A4 workspace release"
        Release-M1ComObject $workspaces $cleanup "A4 workspaces release"
        Release-M1ComObject $engine $cleanup "A4 engine release"
    }
    Complete-M1DaoHelper $primary $cleanup "A4 replica acquisition"
    if ($script:A4Checkpoints.Count -ne 25) {
        throw "A4 worker did not complete the exact checkpoint schedule."
    }
    $growthObservations = New-Object Collections.ArrayList
    foreach ($checkpoint in $script:A4Checkpoints) {
        if ($null -eq $checkpoint.target_threshold_pages) { continue }
        $role = if ([string]$checkpoint.checkpoint_id -like "T1_REL_*") {
            "T1"
        } elseif ([string]$checkpoint.checkpoint_id -like "T3_ABS_*") {
            "T3"
        } elseif ([string]$checkpoint.checkpoint_id -like "T4_REL_*") {
            "T4"
        } else {
            throw "A4 growth checkpoint has no plan-defined role."
        }
        [void]$growthObservations.Add([ordered]@{
            checkpoint_id = [string]$checkpoint.checkpoint_id
            baseline_pages = $checkpoint.target_baseline_pages
            target_pages = [long]$checkpoint.target_threshold_pages
            achieved_pages = [long]$checkpoint.actual_file_pages
            overshoot_pages = [long]$checkpoint.target_overshoot_pages
            rows = [int]$checkpoint.table_row_counts[$role]
        })
    }
    $observation = [ordered]@{
        protocol_version = "1.0.0"; document_type = "dao_a4_replica_observation"
        experiment_id = $script:A4ExperimentId; plan_sha256 = $PlanSha256
        revision_plan_sha256 = $script:A4RevisionPlanSha256
        producer_commit = $ProducerCommit
        repository_url = "https://github.com/oglassdev/jet3-rs.git"
        campaign_id = $CampaignId
        matrix_job = [ordered]@{
            job_id = $MatrixJobId; replica_only = $true; shared_mutable_state = $false
        }
        environment_sha256 = $EnvironmentSha256
        provider_sha256 = $script:A4ProviderSha256; replica = $Replica
        role_binding = [ordered]@{
            T1 = [string]$roleBinding.T1; T2 = [string]$roleBinding.T2
            T3 = [string]$roleBinding.T3; T4 = [string]$roleBinding.T4
        }
        growth_observations = @($growthObservations)
        logical_checkpoint_read_bytes = [long]$script:A4Store.LogicalReadBytes
        inserted_rows_total = [long]$script:A4InsertedRows
        changed_hash_entries = [long]$script:A4Store.ChangedEntries
        checkpoints = @($script:A4Checkpoints)
    }
    $observationRelative = "observations/replica-{0:D2}.json" -f $Replica
    [void](Write-A4JsonArtifact -RelativePath $observationRelative `
        -Document $observation)
    Write-A4ReplicaManifest
}

Invoke-A4Worker
