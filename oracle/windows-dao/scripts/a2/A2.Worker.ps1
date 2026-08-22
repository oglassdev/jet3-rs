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
    [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
    [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$m1Root = Join-Path $RepositoryRoot "oracle/windows-dao/scripts/m1"
. (Join-Path $m1Root "M1.Preflight.ps1")
. (Join-Path $m1Root "M1.Publication.ps1")
. (Join-Path $m1Root "M1.DaoValues.ps1")
. (Join-Path $PSScriptRoot "A2.PageStore.ps1")
. (Join-Path $PSScriptRoot "A2.Progress.ps1")

$script:A2ExperimentId = "DAO-A2-ALLOCATION-MAPS-001"
$script:A2FrozenPlanSha256 = `
    "804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2"
$script:A1DbVersion30 = 32
$script:A1DbLong = 4
$script:A1DbText = 10
$script:A1DbFixedField = 1
$script:A1DbOpenSnapshot = 4
$script:A2Locale = ";LANGID=0x0409;CP=1252;COUNTRY=0"

function Assert-A2WorkerPlan {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    $expectedIds = @(
        "E0", "E0R", "D_GROW_0128", "D_DROP", "D_RECREATE_EMPTY",
        "D_REGROW_0128", "L_REL_0064", "L_REL_0512", "L_REL_0768",
        "L_REL_0896", "L_REL_0904", "L_REL_1024", "L_REL_1088",
        "L_REL_1280", "L_DELETE_ALL", "L_REINSERT_SAME",
        "L_IDLE_REOPEN", "P_ABS_04096", "P_ABS_08192",
        "P_ABS_12288", "P_ABS_16480", "H_REL_0064",
        "H_REL_0896", "H_REL_0904", "H_IDLE_REOPEN"
    )
    $ids = @($Plan.checkpoint_design.checkpoint_ids)
    $names = @($Plan.tables.physical_names)
    $binding = @($Plan.tables.role_bindings | Where-Object {
        [int]$_.replica -eq $script:A2Replica
    })
    if ([string]$Plan.experiment_id -cne $script:A2ExperimentId -or
        [string]$Plan.document_type -cne "dao_a2_allocation_maps_plan" -or
        [int]$Plan.replicas.count -ne 3 -or $binding.Count -ne 1 -or
        $names.Count -ne 4 -or @($names | Select-Object -Unique).Count -ne 4 -or
        @($names | Where-Object { $_ -cnotmatch "^[A-Z0-9_]{1,32}$" }).Count -ne 0 -or
        @($names | ForEach-Object { $_.Length } | Select-Object -Unique).Count -ne 1) {
        throw "A2 plan identity, replica, or equal-length table design drifted."
    }
    if ($ids.Count -ne 25 -or $ids.Count -ne [int]$Plan.checkpoint_design.count -or
        $ids.Count -ne [int]$Plan.bounds.planned_checkpoints_per_replica -or
        $ids.Count -gt [int]$Plan.bounds.max_checkpoints_per_replica -or
        @($ids | Select-Object -Unique).Count -ne $ids.Count) {
        throw "A2 checkpoint enumeration is not exact or bounded."
    }
    for ($index = 0; $index -lt $expectedIds.Count; $index++) {
        if ([string]$ids[$index] -cne [string]$expectedIds[$index]) {
            throw "A2 checkpoint schedule differs from checkpoint_design."
        }
    }
    $definition = $Plan.tables.definition
    $row = $Plan.tables.row_algorithm
    $bounds = $Plan.bounds
    if ($definition.indexed -ne $false -or @($definition.fields).Count -ne 2 -or
        [string]$definition.fields[0].name -cne "Id" -or
        [string]$definition.fields[0].dao_type -cne "dbLong" -or
        [string]$definition.fields[1].name -cne "Payload" -or
        [string]$definition.fields[1].dao_type -cne "dbText" -or
        [int]$definition.fields[1].size -ne 240 -or
        $definition.fields[1].fixed_length -ne $true -or
        [int]$row.growth_batch_rows -ne 32 -or
        [int]$Plan.page_capture.page_size -ne 2048 -or
        [long]$bounds.max_final_pages_per_replica -ne 65536 -or
        [long]$bounds.max_logical_checkpoint_read_bytes_per_replica -ne 2GB -or
        [long]$bounds.max_inserted_rows_per_replica -ne 524288 -or
        [long]$bounds.max_changed_hash_entries_per_replica -ne 65536 -or
        [long]$bounds.max_unique_page_blobs -ne 65536 -or
        [long]$bounds.max_retained_page_store_bytes -ne 512MB -or
        [long]$bounds.max_bundle_bytes -ne 768MB -or
        [long]$bounds.max_json_bytes -ne 64MB -or
        [int]$bounds.worker_timeout_seconds_per_replica -ne 1700 -or
        [long]$bounds.max_child_log_bytes -ne 1MB -or
        [long]$bounds.max_companion_bytes_per_checkpoint -ne 64KB) {
        throw "A2 schema, row algorithm, or resource bounds drifted."
    }
    return $binding[0]
}

function Get-A2Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$Id
    )
    $seed = "A2|$Role|$($Id.ToString('D10'))|"
    $builder = New-Object Text.StringBuilder 240
    while ($builder.Length -lt 240) { [void]$builder.Append($seed) }
    return $builder.ToString(0, 240)
}

function Invoke-A2WithDatabase {
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
            $database = $script:A2Workspace.CreateDatabase(
                $script:A2DatabasePath, $script:A2Locale,
                $script:A1DbVersion30
            )
        }
        else { $database = $script:A2Workspace.OpenDatabase($script:A2DatabasePath) }
        if ([string]$database.Version -cne "3.0") {
            throw "A2 database version differs from Jet 3."
        }
        $result = & $Action $database
    }
    catch { $primary = $_ }
    finally {
        Close-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A2 database close"
        Release-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A2 database release"
    }
    Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
        -Label "A2 database action"
    return $result
}

function Add-A2Table {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A2WithDatabase -Action {
        param($database)
        $table = $null; $fields = $null; $idField = $null
        $payloadField = $null; $tableDefinitions = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $table = $database.CreateTableDef($name)
            $fields = $table.Fields
            $idField = $table.CreateField("Id", $script:A1DbLong)
            $fields.Append($idField)
            $payloadField = $table.CreateField("Payload", $script:A1DbText, 240)
            $payloadField.Attributes = $script:A1DbFixedField
            $fields.Append($payloadField)
            $tableDefinitions = $database.TableDefs
            $tableDefinitions.Append($table)
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $tableDefinitions $cleanup "A2 table definitions release"
            Release-M1ComObject $payloadField $cleanup "A2 payload field release"
            Release-M1ComObject $idField $cleanup "A2 id field release"
            Release-M1ComObject $fields $cleanup "A2 fields release"
            Release-M1ComObject $table $cleanup "A2 table release"
        }
        Complete-M1DaoHelper $primary $cleanup "A2 table creation"
    } | Out-Null
    $script:A1Extant[$Role] = $true
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
    Set-A2ExpectedSemanticDirty -Role $Role
}

function Remove-A2Table {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A2WithDatabase -Action {
        param($database)
        $definitions = $null; $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try { $definitions = $database.TableDefs; $definitions.Delete($name) }
        catch { $primary = $_ }
        finally { Release-M1ComObject $definitions $cleanup "A2 table definitions release" }
        Complete-M1DaoHelper $primary $cleanup "A2 table deletion"
    } | Out-Null
    $script:A1Extant[$Role] = $false
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
    Set-A2ExpectedSemanticDirty -Role $Role
}

function Add-A2Ids {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int[]]$Ids
    )
    if ($Ids.Count -lt 1 -or
        $script:A2InsertedRows -gt (524288 - $Ids.Count)) {
        throw "A2 insertion would violate its row ceiling."
    }
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A2WithDatabase -Action {
        param($database)
        $recordset = $null; $fields = $null; $idField = $null
        $payloadField = $null; $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $recordset = $database.OpenRecordset($name)
            $fields = $recordset.Fields
            $idField = $fields.Item("Id")
            $payloadField = $fields.Item("Payload")
            foreach ($id in $Ids) {
                $recordset.AddNew()
                $idField.Value = [Int32]$id
                $payloadField.Value = [string](Get-A2Payload -Role $Role -Id $id)
                $recordset.Update()
            }
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $payloadField $cleanup "A2 payload field release"
            Release-M1ComObject $idField $cleanup "A2 id field release"
            Release-M1ComObject $fields $cleanup "A2 recordset fields release"
            Close-M1ComObject $recordset $cleanup "A2 insert recordset close"
            Release-M1ComObject $recordset $cleanup "A2 insert recordset release"
        }
        Complete-M1DaoHelper $primary $cleanup "A2 row insertion"
    } | Out-Null
    foreach ($id in $Ids) { [void]$script:A1Rows[$Role].Add([int]$id) }
    $script:A2InsertedRows += $Ids.Count
    Set-A2ExpectedSemanticDirty -Role $Role
}

function Add-A2RowBatch {
    param([Parameter(Mandatory = $true)][string]$Role)
    $first = [int]$script:A1NextId[$Role]
    $ids = New-Object int[] 32
    for ($offset = 0; $offset -lt 32; $offset++) {
        $ids[$offset] = [int]($first + $offset)
    }
    Add-A2Ids -Role $Role -Ids $ids
    $script:A1NextId[$Role] = [int]($first + 32)
}

function Remove-A2AllLRows {
    $script:A2DeletedLIds = [int[]]@($script:A1Rows["L"] | Sort-Object)
    if ($script:A2DeletedLIds.Count -lt 1) {
        throw "A2 full-delete checkpoint requires nonempty L."
    }
    $name = [string]$script:A1RoleNames["L"]
    $expectedIds = [int[]]$script:A2DeletedLIds
    Invoke-A2WithDatabase -Action {
        param($database)
        $recordset = $null; $fields = $null; $idField = $null
        $cleanup = New-Object Collections.ArrayList; $primary = $null
        try {
            $sql = "SELECT Id FROM [$name] ORDER BY Id"
            $recordset = $database.OpenRecordset($sql)
            $fields = $recordset.Fields
            $idField = $fields.Item("Id")
            foreach ($expectedId in $expectedIds) {
                if ($recordset.EOF -or [int]$idField.Value -ne $expectedId) {
                    throw "A2 L delete order differs from expected Id order."
                }
                $recordset.Delete()
                $recordset.MoveNext()
            }
            if (-not $recordset.EOF) { throw "A2 L delete left unexpected rows." }
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject $idField $cleanup "A2 delete id field release"
            Release-M1ComObject $fields $cleanup "A2 delete fields release"
            Close-M1ComObject $recordset $cleanup "A2 delete recordset close"
            Release-M1ComObject $recordset $cleanup "A2 delete recordset release"
        }
        Complete-M1DaoHelper $primary $cleanup "A2 full L deletion"
    } | Out-Null
    $script:A1Rows["L"].Clear()
    Set-A2ExpectedSemanticDirty -Role "L"
}

function Restore-A2AllLRows {
    $ids = [int[]]@($script:A2DeletedLIds)
    if ($ids.Count -lt 1) { throw "A2 L reinsert set is empty." }
    Add-A2Ids -Role "L" -Ids $ids
}

function Get-A2ClosedPageCount {
    Assert-M1NoReparseComponents -Path $script:A2DatabasePath
    $stream = $null
    try {
        try {
            $stream = New-Object IO.FileStream(
                $script:A2DatabasePath, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::None, 2048,
                [IO.FileOptions]::SequentialScan
            )
        }
        catch [IO.IOException] {
            $nativeCode = ($_.Exception.HResult -band 0xffff)
            if ($nativeCode -notin @(32, 33)) { throw }
            [GC]::Collect(); [GC]::WaitForPendingFinalizers()
            $stream = New-Object IO.FileStream(
                $script:A2DatabasePath, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::None, 2048,
                [IO.FileOptions]::SequentialScan
            )
        }
        if ($stream.Length -lt 2048 -or ($stream.Length % 2048) -ne 0) {
            throw "A2 database length is not an exact page sequence."
        }
        $pages = [long]($stream.Length / 2048)
        if ($pages -gt 65536) { throw "A2 final-page ceiling was exceeded." }
        return $pages
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Assert-A2Quiescent {
    $companion = [IO.Path]::ChangeExtension($script:A2DatabasePath, ".ldb")
    if ([IO.File]::Exists($companion)) {
        $item = Get-Item -LiteralPath $companion -Force
        if ($item.Length -gt 64KB) {
            throw "A2 companion exceeded its checkpoint byte ceiling."
        }
        throw "A2 DAO lock companion remains after close."
    }
    if ([IO.Directory]::Exists($companion)) {
        throw "A2 DAO lock companion was replaced by a directory."
    }
}

function Add-A2Checkpoint {
    param(
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [AllowNull()][object]$Target,
        [long]$BaselinePages = -1
    )
    Assert-A2Quiescent
    $semantic = @(Read-A2SemanticTables)
    Assert-A2Quiescent
    $snapshot = Read-A2PageSnapshot -Store $script:A2Store `
        -DatabasePath $script:A2DatabasePath `
        -PriorHashes $script:A2PriorHashes -PriorPages $script:A2PriorPages
    $threshold = $null; $overshoot = $null
    if ($null -ne $Target) {
        if ([string]$Target.kind -ceq "relative") {
            $threshold = [long]($BaselinePages + [long]$Target.pages)
        }
        else { $threshold = [long]$Target.pages }
        if ([long]$snapshot.page_count -lt $threshold) {
            throw "A2 checkpoint missed its frozen growth target."
        }
        $overshoot = [long]($snapshot.page_count - $threshold)
    }
    $ordinal = [int]$script:A2Checkpoints.Count
    $indexDocument = [ordered]@{
        protocol_version = "1.0.0"; document_type = "dao_a2_page_index"
        experiment_id = $script:A2ExperimentId; plan_sha256 = $script:A2PlanSha256
        producer_commit = $script:A2ProducerCommit; campaign_id = $script:A2CampaignId
        environment_sha256 = $script:A2EnvironmentSha256
        provider_sha256 = $script:A2ProviderSha256; replica = $script:A2Replica
        checkpoint_id = $CheckpointId; ordinal = $ordinal
        predecessor_checkpoint_id = $script:A2PriorCheckpoint
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
        $indexBytes.Length -gt (768MB - $script:A2Store.RetainedBytes)) {
        throw "A2 page index exceeds its retained byte ceiling."
    }
    $locator = "page-indexes/replica-{0:D2}/{1:D2}-{2}.json" -f @(
        $script:A2Replica, $ordinal, $CheckpointId
    )
    $path = Get-M1PayloadPath -Session $script:A2Store.Session `
        -RelativePath $locator
    Write-A2CreateNewBytes -Path $path -Bytes $indexBytes -MaximumBytes 64MB
    $script:A2Store.RetainedBytes += [long]$indexBytes.Length
    [void]$script:A2Checkpoints.Add([ordered]@{
        checkpoint_id = $CheckpointId; ordinal = $ordinal
        actual_file_pages = [long]$snapshot.page_count
        actual_size_bytes = [long]$snapshot.file_bytes
        target_baseline_pages = if ($BaselinePages -ge 0) { $BaselinePages } else { $null }
        target_threshold_pages = $threshold; target_overshoot_pages = $overshoot
        inserted_rows_total = [long]$script:A2InsertedRows
        table_row_counts = [ordered]@{
            D = [int]$script:A1Rows["D"].Count; L = [int]$script:A1Rows["L"].Count
            P = [int]$script:A1Rows["P"].Count; H = [int]$script:A1Rows["H"].Count
        }
        dao_reread = @($semantic); quiescent = $true
        post_close_companion = [ordered]@{
            present_after_close = $false; observed_size_bytes = 0
            retained_for_physical_analysis = $false
        }
        page_index = [ordered]@{
            path = $locator; sha256 = Get-A2LowerSha256 -Bytes $indexBytes
            size_bytes = [long]$indexBytes.Length
        }
    })
    $script:A2PriorHashes = [string[]]$snapshot.hashes
    $script:A2PriorPages = [byte[]]$snapshot.pages
    $script:A2PriorCheckpoint = $CheckpointId
    Add-A2ProgressRecord -Progress $script:A2Progress `
        -CheckpointId $CheckpointId -PageCount ([long]$snapshot.page_count)
}

function Add-A2UntilTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][long]$ThresholdPages
    )
    do {
        Add-A2RowBatch -Role $Role
        Assert-A2Quiescent
        $pages = Get-A2ClosedPageCount
    } while ($pages -lt $ThresholdPages)
}

function Invoke-A2Schedule {
    Invoke-A2WithDatabase -Create -Action { param($database) } | Out-Null
    foreach ($role in @("D", "L", "P", "H")) { Add-A2Table -Role $role }
    Assert-A2Quiescent
    foreach ($value in @($script:A2Plan.checkpoint_design.checkpoint_ids)) {
        $id = [string]$value
        if ($id -in @("E0", "E0R", "L_IDLE_REOPEN", "H_IDLE_REOPEN")) {
            Add-A2Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "D_DROP") {
            Remove-A2Table -Role "D"
            Add-A2Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "D_RECREATE_EMPTY") {
            Add-A2Table -Role "D"
            Add-A2Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "L_DELETE_ALL") {
            Remove-A2AllLRows
            Add-A2Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "L_REINSERT_SAME") {
            Restore-A2AllLRows
            Add-A2Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -match "^D_(GROW|REGROW)_0128$") {
            $baseline = Get-A2ClosedPageCount
            Add-A2UntilTarget -Role "D" -ThresholdPages ($baseline + 128)
            Add-A2Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "relative"; pages = 128 }) `
                -BaselinePages $baseline
            $latest = $script:A2Checkpoints[$script:A2Checkpoints.Count - 1]
            if ($id -ceq "D_GROW_0128") { $script:A2FirstGrowth = $latest }
            elseif ([long]$latest.actual_file_pages -le
                [long]$script:A2FirstGrowth.actual_file_pages) {
                throw "A2 D regrowth is not strictly greater than first growth."
            }
            continue
        }
        if ($id -match "^([LH])_REL_([0-9]{4})$") {
            $role = [string]$Matches[1]; $targetPages = [long]$Matches[2]
            if (-not $script:A2Baselines.ContainsKey($role)) {
                $script:A2Baselines[$role] = Get-A2ClosedPageCount
            }
            $baseline = [long]$script:A2Baselines[$role]
            Add-A2UntilTarget -Role $role -ThresholdPages ($baseline + $targetPages)
            Add-A2Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "relative"; pages = $targetPages }) `
                -BaselinePages $baseline
            continue
        }
        if ($id -match "^P_ABS_([0-9]{5})$") {
            $targetPages = [long]$Matches[1]
            Add-A2UntilTarget -Role "P" -ThresholdPages $targetPages
            Add-A2Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "absolute"; pages = $targetPages })
            continue
        }
        throw "A2 checkpoint operation is not implemented: $id"
    }
}

function Write-A2JsonArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Document
    )
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        (($Document | ConvertTo-Json -Depth 32 -Compress) + "`n")
    )
    if ($bytes.Length -gt 64MB -or
        $bytes.Length -gt (768MB - $script:A2Store.RetainedBytes)) {
        throw "A2 JSON artifact exceeds its byte ceiling."
    }
    $path = Get-M1PayloadPath -Session $script:A2Store.Session `
        -RelativePath $RelativePath
    Write-A2CreateNewBytes -Path $path -Bytes $bytes -MaximumBytes 64MB
    $script:A2Store.RetainedBytes += [long]$bytes.Length
    return $bytes
}

function Get-A2ArtifactRole {
    param([Parameter(Mandatory = $true)][string]$Relative)
    if ($Relative -ceq ("environment/replica-{0:D2}.json" -f $script:A2Replica)) {
        return "environment"
    }
    if ($Relative -ceq ("observations/replica-{0:D2}.json" -f $script:A2Replica)) {
        return "replica_observation"
    }
    $pageIndexPattern =
        "^page-indexes/replica-{0:D2}/[0-9]{{2}}-[A-Z0-9_]+\.json$" -f `
        $script:A2Replica
    if ($Relative -match $pageIndexPattern) {
        return "page_index"
    }
    if ($Relative -match "^page-store/[0-9a-f]{64}\.page$") { return "page_blob" }
    throw "A2 output contains an unexpected artifact path: $Relative"
}

function Write-A2ReplicaManifest {
    $manifestRelative = "replica-artifacts/replica-{0:D2}-manifest.json" -f $script:A2Replica
    $files = @([IO.Directory]::EnumerateFiles(
        $script:A2Output, "*", [IO.SearchOption]::AllDirectories
    ))
    $records = New-Object Collections.ArrayList
    $pageBlobs = 0; $pageIndexes = 0; $total = [long]0
    foreach ($path in @($files | Sort-Object)) {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $item.Length -lt 1 -or $item.Length -gt 64MB) {
            throw "A2 output artifact violates its file bound."
        }
        $relative = $path.Substring($script:A2Output.TrimEnd('\').Length + 1).Replace('\', '/')
        if ($relative -ceq $manifestRelative) { throw "A2 manifest already exists." }
        $role = Get-A2ArtifactRole -Relative $relative
        if ($role -ceq "page_blob") {
            $pageBlobs++
            if ($item.Length -ne 2048) { throw "A2 page blob has a non-page length." }
        }
        elseif ($role -ceq "page_index") { $pageIndexes++ }
        if ($item.Length -gt (768MB - $total)) {
            throw "A2 replica output exceeds its retained-byte ceiling."
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
    if ($pageIndexes -ne 25 -or $pageBlobs -lt 1 -or $pageBlobs -gt 65536) {
        throw "A2 replica inventory does not contain its exact checkpoint artifacts."
    }
    $manifest = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a2_replica_artifact_manifest"
        experiment_id = $script:A2ExperimentId; plan_sha256 = $script:A2PlanSha256
        producer_commit = $script:A2ProducerCommit; campaign_id = $script:A2CampaignId
        matrix_job_id = $script:A2MatrixJobId; replica = $script:A2Replica
        environment_sha256 = $script:A2EnvironmentSha256
        provider_sha256 = $script:A2ProviderSha256; checkpoint_count = 25
        inventory_closed = $true; hashes_verified = $true; paths_closed = $true
        files = @($records)
    }
    [void](Write-A2JsonArtifact -RelativePath $manifestRelative -Document $manifest)
}

function Invoke-A2Worker {
    if ([IntPtr]::Size -ne 4 -or $PSVersionTable.PSVersion.Major -ne 5) {
        throw "A2 worker requires x86 Windows PowerShell 5."
    }
    $script:A2Replica = $Replica; $script:A2ProducerCommit = $ProducerCommit
    $script:A2CampaignId = $CampaignId; $script:A2MatrixJobId = $MatrixJobId
    $script:A2PlanSha256 = $PlanSha256
    $script:A2EnvironmentSha256 = $EnvironmentSha256
    $planInput = Read-A1CheckedJson -Path $PlanPath -MaximumBytes 1MB
    $environmentInput = Read-A1CheckedJson -Path $EnvironmentPath -MaximumBytes 1MB
    if ($PlanSha256 -cne $script:A2FrozenPlanSha256 -or
        $planInput.sha256 -cne $PlanSha256 -or
        $environmentInput.sha256 -cne $EnvironmentSha256) {
        throw "A2 worker input bytes differ from controller bindings."
    }
    $script:A2Plan = $planInput.document
    $roleBinding = Assert-A2WorkerPlan -Plan $script:A2Plan
    $environment = $environmentInput.document
    if ([string]$environment.document_type -cne "dao_a2_environment" -or
        [string]$environment.experiment_id -cne $script:A2ExperimentId -or
        [string]$environment.plan_sha256 -cne $PlanSha256 -or
        [string]$environment.producer_commit -cne $ProducerCommit -or
        [string]$environment.repository_url -cne
            "https://github.com/oglassdev/jet3-rs.git" -or
        [string]$environment.campaign_id -cne $CampaignId -or
        [int]$environment.replica -ne $Replica -or
        [string]$environment.matrix_job_id -cne $MatrixJobId -or
        [string]$environment.status -cne "ready" -or
        [string]$environment.host.process_architecture -cne "x86" -or
        [string]$environment.provider.prog_id -cne "DAO.DBEngine.36") {
        throw "A2 published environment differs from the worker binding."
    }
    $accepted = $environment.provider
    [void](Assert-M1CurrentRegistration -AcceptedProvider $accepted)
    if ((Get-M1FileSha256 -Path ([string]$accepted.server_path)) -cne
        [string]$accepted.server_sha256) {
        throw "A2 provider binary differs from its environment digest."
    }
    $script:A2ProviderSha256 = [string]$accepted.server_sha256
    $providerType = [Type]::GetTypeFromProgID([string]$accepted.prog_id, $false)
    if ($null -eq $providerType -or
        $providerType.GUID.ToString("B").ToUpperInvariant() -cne
        ([string]$accepted.clsid).ToUpperInvariant()) {
        throw "A2 provider activation binding differs from preflight."
    }
    $script:A2Output = [IO.Path]::GetFullPath($OutputRoot)
    $working = [IO.Path]::GetFullPath($WorkingRoot)
    Assert-M1NoReparseComponents -Path $script:A2Output
    Assert-M1NoReparseComponents -Path $working
    $script:A2Progress = Open-A2WorkerProgress `
        -DiagnosticsRoot $DiagnosticsRoot -Replica $Replica
    $replicaRoot = Join-Path $working ("replica-{0:D2}" -f $Replica)
    if ([IO.Directory]::Exists($replicaRoot)) {
        throw "A2 private replica directory already exists."
    }
    [void][IO.Directory]::CreateDirectory($replicaRoot)
    $script:A2DatabasePath = Join-Path $replicaRoot "ACQUISITION.MDB"
    $script:A1RoleNames = @{}
    foreach ($role in @("D", "L", "P", "H")) {
        $script:A1RoleNames[$role] = [string]$roleBinding.$role
    }
    $script:A1Extant = @{ D = $false; L = $false; P = $false; H = $false }
    $script:A1Rows = @{}; $script:A1NextId = @{}
    $script:A1ExpectedSemanticCache = @{}
    foreach ($role in @("D", "L", "P", "H")) {
        $script:A1Rows[$role] = New-Object 'Collections.Generic.HashSet[int]'
        $script:A1NextId[$role] = 1
    }
    $script:A2Baselines = @{}; $script:A2InsertedRows = 0
    $script:A2DeletedLIds = New-Object int[] 0
    $script:A2Checkpoints = New-Object Collections.ArrayList
    $script:A2PriorHashes = $null; $script:A2PriorPages = $null
    $script:A2PriorCheckpoint = $null; $script:A2FirstGrowth = $null
    $session = [pscustomobject]@{ StagingBundle = $script:A2Output }
    $script:A2Store = New-A2PageStore -Session $session
    $engine = $null; $workspaces = $null; $workspace = $null
    $cleanup = New-Object Collections.ArrayList; $primary = $null
    try {
        $engine = [Activator]::CreateInstance($providerType)
        if ([string]$engine.Version -cne [string]$accepted.provider_version) {
            throw "A2 DBEngine version differs from the bound provider."
        }
        $workspaces = $engine.Workspaces
        $workspace = $workspaces.Item([int]0)
        $script:A2Workspace = $workspace
        Invoke-A2Schedule
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject $workspace $cleanup "A2 workspace release"
        Release-M1ComObject $workspaces $cleanup "A2 workspaces release"
        Release-M1ComObject $engine $cleanup "A2 engine release"
    }
    Complete-M1DaoHelper $primary $cleanup "A2 replica acquisition"
    if ($script:A2Checkpoints.Count -ne 25) {
        throw "A2 worker did not complete the exact checkpoint schedule."
    }
    $first = $script:A2Checkpoints[2]; $recreated = $script:A2Checkpoints[4]
    $regrown = $script:A2Checkpoints[5]
    $observation = [ordered]@{
        protocol_version = "1.0.0"; document_type = "dao_a2_replica_observation"
        experiment_id = $script:A2ExperimentId; plan_sha256 = $PlanSha256
        producer_commit = $ProducerCommit
        repository_url = "https://github.com/oglassdev/jet3-rs.git"
        campaign_id = $CampaignId
        matrix_job = [ordered]@{
            job_id = $MatrixJobId; replica_only = $true; shared_mutable_state = $false
        }
        environment_sha256 = $EnvironmentSha256
        provider_sha256 = $script:A2ProviderSha256; replica = $Replica
        role_binding = [ordered]@{
            D = [string]$roleBinding.D; L = [string]$roleBinding.L
            P = [string]$roleBinding.P; H = [string]$roleBinding.H
        }
        d_growth_observation = [ordered]@{
            first_baseline_pages = [long]$first.target_baseline_pages
            first_target_pages = [long]$first.target_threshold_pages
            first_achieved_pages = [long]$first.actual_file_pages
            first_rows = [int]$first.table_row_counts.D
            regrowth_baseline_pages = [long]$recreated.actual_file_pages
            regrowth_target_pages = [long]$regrown.target_threshold_pages
            regrowth_achieved_pages = [long]$regrown.actual_file_pages
            regrowth_rows = [int]$regrown.table_row_counts.D
        }
        logical_checkpoint_read_bytes = [long]$script:A2Store.LogicalReadBytes
        inserted_rows_total = [long]$script:A2InsertedRows
        changed_hash_entries = [long]$script:A2Store.ChangedEntries
        checkpoints = @($script:A2Checkpoints)
    }
    $observationRelative = "observations/replica-{0:D2}.json" -f $Replica
    [void](Write-A2JsonArtifact -RelativePath $observationRelative `
        -Document $observation)
    Write-A2ReplicaManifest
}

Invoke-A2Worker
