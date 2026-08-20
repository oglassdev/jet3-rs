[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$BundleRoot,
    [Parameter(Mandatory = $true)][string]$WorkingRoot,
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$ProducerCommit,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$PlanSha256,
    [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
    [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$m1Root = Join-Path $RepositoryRoot "oracle/windows-dao/scripts/m1"
. (Join-Path $m1Root "M1.Preflight.ps1")
. (Join-Path $m1Root "M1.Publication.ps1")
. (Join-Path $m1Root "M1.DaoValues.ps1")
. (Join-Path $PSScriptRoot "A1.PageStore.ps1")
$script:A1ExperimentId = "DAO-A1-ALLOCATION-MAPS-001"
$script:A1FrozenPlanSha256 = `
    "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80"
$script:A1DbVersion30 = 32
# SRC-0021 records these public DAO enumeration values for the oracle only.
$script:A1DbLong = 4
$script:A1DbText = 10
$script:A1DbFixedField = 1
$script:A1DbOpenSnapshot = 4
$script:A1Locale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
function Assert-A1WorkerPlan {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    $ids = @($Plan.checkpoint_design.checkpoint_ids)
    $names = @($Plan.tables.physical_names)
    $binding = @($Plan.tables.role_bindings | Where-Object {
        [int]$_.replica -eq $script:A1ReplicaOrdinal
    })
    if ([string]$Plan.experiment_id -cne $script:A1ExperimentId -or
        [string]$Plan.document_type -cne "dao_a1_allocation_maps_plan" -or
        [int]$Plan.replicas.count -ne 3 -or
        $script:A1ReplicaOrdinal -lt 1 -or $script:A1ReplicaOrdinal -gt 3 -or
        $binding.Count -ne 1 -or $names.Count -ne 4 -or
        @($names | Select-Object -Unique).Count -ne 4 -or
        @($names | Where-Object { $_ -cnotmatch "^[A-Z0-9_]{1,32}$" }).Count -ne 0 -or
        @($names | ForEach-Object { $_.Length } | Select-Object -Unique).Count -ne 1) {
        throw "A1 plan identity, replica, or equal-length table design drifted."
    }
    if ($ids.Count -ne [int]$Plan.checkpoint_design.count -or
        $ids.Count -ne [int]$Plan.bounds.planned_checkpoints_per_replica -or
        $ids.Count -gt [int]$Plan.bounds.max_checkpoints_per_replica -or
        @($ids | Select-Object -Unique).Count -ne $ids.Count) {
        throw "A1 checkpoint enumeration is not exact or bounded."
    }
    foreach ($id in $ids) {
        if ([string]$id -cnotmatch (
            "^(E0R?|D_(GROW_0128|DROP|REGROW_0128)|" +
            "[LH]_(REL_[0-9]{4}|DELETE_ALTERNATING|REINSERT_SAME|" +
            "IDLE_REOPEN)|P_ABS_[0-9]{5})$"
        )) { throw "A1 plan contains an unknown checkpoint operation." }
    }
    $definition = $Plan.tables.definition
    $row = $Plan.tables.row_algorithm
    $bounds = $Plan.bounds
    if ($definition.indexed -ne $false -or
        @($definition.fields).Count -ne 2 -or
        [string]$definition.fields[0].name -cne "Id" -or
        [string]$definition.fields[0].dao_type -cne "dbLong" -or
        [string]$definition.fields[1].name -cne "Payload" -or
        [string]$definition.fields[1].dao_type -cne "dbText" -or
        [int]$definition.fields[1].size -ne 240 -or
        $definition.fields[1].fixed_length -ne $true -or
        [int]$row.growth_batch_rows -ne 32 -or
        [int]$Plan.page_capture.page_size -ne 2048 -or
        [int]$bounds.replicas -ne 3 -or
        [long]$bounds.max_final_pages_per_replica -ne 20480 -or
        [long]$bounds.max_logical_checkpoint_read_bytes_per_replica -ne 8GB -or
        [long]$bounds.max_unique_page_blobs -ne 262144 -or
        [long]$bounds.max_retained_page_store_bytes -ne 512MB -or
        [long]$bounds.max_bundle_bytes -ne 768MB -or
        [long]$bounds.max_inserted_rows_per_replica -ne 200000 -or
        [long]$bounds.max_changed_hash_entries -ne 1500000 -or
        [long]$bounds.max_json_bytes -ne 64MB -or
        [int]$bounds.worker_timeout_seconds -ne 1800 -or
        [int]$bounds.campaign_timeout_seconds -ne 7200 -or
        [long]$bounds.max_child_log_bytes -ne 1MB -or
        [long]$bounds.max_companion_bytes_per_checkpoint -ne 64KB) {
        throw "A1 fixed schema, row algorithm, or resource bounds drifted."
    }
    return $binding[0]
}
function Get-A1Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$Id
    )
    $seed = "A1|$Role|$($Id.ToString('D10'))|"
    $builder = New-Object Text.StringBuilder 240
    while ($builder.Length -lt 240) { [void]$builder.Append($seed) }
    return $builder.ToString(0, 240)
}
function Get-A1FieldValue {
    param([object]$Recordset, [string]$Name)
    $fields = $null
    $field = $null
    try {
        $fields = $Recordset.Fields
        $field = $fields.Item($Name)
        return $field.Value
    }
    finally {
        Release-M1ComObject -Value $field
        Release-M1ComObject -Value $fields
    }
}
function Set-A1FieldValue {
    param([object]$Recordset, [string]$Name, [object]$Value)
    $fields = $null
    $field = $null
    try {
        $fields = $Recordset.Fields
        $field = $fields.Item($Name)
        $field.Value = $Value
    }
    finally {
        Release-M1ComObject -Value $field
        Release-M1ComObject -Value $fields
    }
}
function Add-A1HashRow {
    param(
        [Parameter(Mandatory = $true)][Security.Cryptography.HashAlgorithm]$Hash,
        [Parameter(Mandatory = $true)][int]$Id,
        [Parameter(Mandatory = $true)][string]$Payload
    )
    if (-not [BitConverter]::IsLittleEndian) {
        throw "A1 rolling hash requires an explicitly little-endian host."
    }
    $idBytes = [BitConverter]::GetBytes([int]$Id)
    $payloadBytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes($Payload)
    if ($payloadBytes.Length -gt [uint16]::MaxValue) {
        throw "A1 semantic payload exceeds its rolling-hash length field."
    }
    $lengthBytes = [BitConverter]::GetBytes([uint16]$payloadBytes.Length)
    foreach ($part in @($idBytes, $lengthBytes, $payloadBytes)) {
        [void]$Hash.TransformBlock($part, 0, $part.Length, $part, 0)
    }
}
function Get-A1ExpectedSemanticHash {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)]$Rows
    )
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($id in @($Rows | Sort-Object)) {
            Add-A1HashRow -Hash $hash -Id ([int]$id) `
                -Payload (Get-A1Payload -Role $Role -Id ([int]$id))
        }
        $empty = New-Object byte[] 0
        [void]$hash.TransformFinalBlock($empty, 0, 0)
        return ([BitConverter]::ToString($hash.Hash)).Replace(
            "-", ""
        ).ToLowerInvariant()
    }
    finally { $hash.Dispose() }
}
function Invoke-A1WithDatabase {
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
            $database = $script:A1Workspace.CreateDatabase(
                $script:A1DatabasePath, $script:A1Locale,
                $script:A1DbVersion30
            )
        }
        else {
            $database = $script:A1Workspace.OpenDatabase($script:A1DatabasePath)
        }
        if ([string]$database.Version -cne "3.0") {
            throw "A1 database version differs from Jet 3."
        }
        $result = & $Action $database
    }
    catch { $primary = $_ }
    finally {
        Close-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A1 database close"
        Release-M1ComObject -Value $database -CleanupErrors $cleanup `
            -Label "A1 database release"
    }
    Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
        -Label "A1 database action"
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    return $result
}
function Add-A1Table {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A1WithDatabase -Action {
        param($database)
        $table = $null
        $idField = $null
        $payloadField = $null
        $fields = $null
        $tableDefinitions = $null
        $cleanup = New-Object Collections.ArrayList
        $primary = $null
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
            Release-M1ComObject -Value $tableDefinitions -CleanupErrors $cleanup -Label "A1 table definitions release"
            Release-M1ComObject -Value $payloadField -CleanupErrors $cleanup `
                -Label "A1 payload field release"
            Release-M1ComObject -Value $idField -CleanupErrors $cleanup `
                -Label "A1 id field release"
            Release-M1ComObject -Value $fields -CleanupErrors $cleanup -Label "A1 table fields release"
            Release-M1ComObject -Value $table -CleanupErrors $cleanup `
                -Label "A1 table release"
        }
        Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
            -Label "A1 table creation"
    } | Out-Null
    $script:A1Extant[$Role] = $true
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
}
function Remove-A1Table {
    param([Parameter(Mandatory = $true)][string]$Role)
    $name = [string]$script:A1RoleNames[$Role]
    Invoke-A1WithDatabase -Action {
        param($database)
        $tableDefinitions = $null
        $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $tableDefinitions = $database.TableDefs
            $tableDefinitions.Delete($name)
        }
        catch { $primary = $_ }
        finally {
            Release-M1ComObject -Value $tableDefinitions -CleanupErrors $cleanup -Label "A1 table definitions release"
        }
        Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
            -Label "A1 table deletion"
    } | Out-Null
    $script:A1Extant[$Role] = $false
    $script:A1Rows[$Role].Clear()
    $script:A1NextId[$Role] = 1
}
function Add-A1RowBatch {
    param([Parameter(Mandatory = $true)][string]$Role)
    if ($script:A1InsertedRows -gt (200000 - 32)) {
        throw "A1 inserted-row ceiling would be exceeded."
    }
    $name = [string]$script:A1RoleNames[$Role]
    $first = [int]$script:A1NextId[$Role]
    Invoke-A1WithDatabase -Action {
        param($database)
        $recordset = $null
        $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $recordset = $database.OpenRecordset($name)
            for ($offset = 0; $offset -lt 32; $offset++) {
                $id = [int]($first + $offset)
                $recordset.AddNew()
                Set-A1FieldValue -Recordset $recordset -Name "Id" -Value $id
                Set-A1FieldValue -Recordset $recordset -Name "Payload" `
                    -Value (Get-A1Payload -Role $Role -Id $id)
                $recordset.Update()
            }
        }
        catch { $primary = $_ }
        finally {
            Close-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 insert recordset close"
            Release-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 insert recordset release"
        }
        Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
            -Label "A1 row batch"
    } | Out-Null
    for ($id = $first; $id -lt ($first + 32); $id++) {
        [void]$script:A1Rows[$Role].Add([int]$id)
    }
    $script:A1NextId[$Role] = [int]($first + 32)
    $script:A1InsertedRows += 32
}
function Remove-A1AlternatingRows {
    $name = [string]$script:A1RoleNames["L"]
    Invoke-A1WithDatabase -Action {
        param($database)
        $recordset = $null
        $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $recordset = $database.OpenRecordset($name)
            if (-not $recordset.EOF) { $recordset.MoveFirst() }
            while (-not $recordset.EOF) {
                $id = [int](Get-A1FieldValue -Recordset $recordset -Name "Id")
                if (($id % 2) -eq 0) { $recordset.Delete() }
                $recordset.MoveNext()
            }
        }
        catch { $primary = $_ }
        finally {
            Close-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 delete recordset close"
            Release-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 delete recordset release"
        }
        Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
            -Label "A1 alternating delete"
    } | Out-Null
    foreach ($id in @($script:A1Rows["L"])) {
        if (([int]$id % 2) -eq 0) {
            [void]$script:A1Rows["L"].Remove([int]$id)
        }
    }
}
function Restore-A1AlternatingRows {
    $limit = [int]($script:A1NextId["L"] - 1)
    $name = [string]$script:A1RoleNames["L"]
    $ids = @(2..$limit | Where-Object { ($_ % 2) -eq 0 })
    if ($script:A1InsertedRows -gt (200000 - $ids.Count)) {
        throw "A1 reinsertions would exceed the row ceiling."
    }
    Invoke-A1WithDatabase -Action {
        param($database)
        $recordset = $null
        $cleanup = New-Object Collections.ArrayList
        $primary = $null
        try {
            $recordset = $database.OpenRecordset($name)
            foreach ($id in $ids) {
                $recordset.AddNew()
                Set-A1FieldValue -Recordset $recordset -Name "Id" -Value ([int]$id)
                Set-A1FieldValue -Recordset $recordset -Name "Payload" `
                    -Value (Get-A1Payload -Role "L" -Id ([int]$id))
                $recordset.Update()
            }
        }
        catch { $primary = $_ }
        finally {
            Close-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 reinsert recordset close"
            Release-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                -Label "A1 reinsert recordset release"
        }
        Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
            -Label "A1 alternating reinsert"
    } | Out-Null
    foreach ($id in $ids) { [void]$script:A1Rows["L"].Add([int]$id) }
    $script:A1InsertedRows += $ids.Count
}
function Get-A1ClosedPageCount {
    Assert-M1NoReparseComponents -Path $script:A1DatabasePath
    $stream = New-Object IO.FileStream(
        $script:A1DatabasePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None, 2048, [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -lt 2048 -or ($stream.Length % 2048) -ne 0) {
            throw "A1 database length is not an exact page sequence."
        }
        $pages = [long]($stream.Length / 2048)
        if ($pages -gt 20480) { throw "A1 final-page ceiling was exceeded." }
        return $pages
    }
    finally { $stream.Dispose() }
}
function Assert-A1Quiescent {
    $companion = [IO.Path]::ChangeExtension($script:A1DatabasePath, ".ldb")
    if ([IO.File]::Exists($companion)) {
        $item = Get-Item -LiteralPath $companion -Force
        if ($item.Length -gt 64KB) {
            throw "A1 companion exceeded its checkpoint byte ceiling."
        }
        throw "A1 DAO lock companion remains after close."
    }
    if ([IO.Directory]::Exists($companion)) {
        throw "A1 DAO lock companion was replaced by a directory."
    }
}
function Read-A1SemanticTables {
    $documents = New-Object Collections.ArrayList
    foreach ($role in @("D", "L", "P", "H")) {
        if (-not [bool]$script:A1Extant[$role]) { continue }
        $name = [string]$script:A1RoleNames[$role]
        $rows = $script:A1Rows[$role]
        $expectedHash = Get-A1ExpectedSemanticHash -Role $role -Rows $rows
        $observed = Invoke-A1WithDatabase -Action {
            param($database)
            $recordset = $null
            $hash = $null
            $cleanup = New-Object Collections.ArrayList
            $primary = $null
            $count = 0
            $prior = 0
            $digest = $null
            try {
                $sql = "SELECT Id, Payload FROM [$name] ORDER BY Id"
                $recordset = $database.OpenRecordset(
                    $sql, $script:A1DbOpenSnapshot
                )
                $hash = [Security.Cryptography.SHA256]::Create()
                if (-not $recordset.EOF) { $recordset.MoveFirst() }
                while (-not $recordset.EOF) {
                    $id = [int](Get-A1FieldValue `
                        -Recordset $recordset -Name "Id")
                    $payload = [string](Get-A1FieldValue `
                        -Recordset $recordset -Name "Payload")
                    if ($id -le $prior -or -not $rows.Contains($id) -or
                        $payload -cne (Get-A1Payload -Role $role -Id $id)) {
                        throw "A1 DAO semantic readback differs from expected rows."
                    }
                    Add-A1HashRow -Hash $hash -Id $id -Payload $payload
                    $prior = $id
                    $count++
                    $recordset.MoveNext()
                }
                $empty = New-Object byte[] 0
                [void]$hash.TransformFinalBlock($empty, 0, 0)
                $digest = ([BitConverter]::ToString($hash.Hash)).Replace(
                    "-", ""
                ).ToLowerInvariant()
            }
            catch { $primary = $_ }
            finally {
                if ($null -ne $hash) { $hash.Dispose() }
                Close-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                    -Label "A1 semantic recordset close"
                Release-M1ComObject -Value $recordset -CleanupErrors $cleanup `
                    -Label "A1 semantic recordset release"
            }
            Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
                -Label "A1 semantic readback"
            return [pscustomobject]@{ count = $count; sha256 = $digest }
        }
        if ([int]$observed.count -ne $rows.Count -or
            [string]$observed.sha256 -cne $expectedHash) {
            throw "A1 semantic row count or rolling digest differs from expectation."
        }
        [void]$documents.Add([ordered]@{
            role = $role
            row_count = [int]$observed.count
            rolling_sha256 = [string]$observed.sha256
        })
    }
    if ($documents.Count -eq 0) {
        Invoke-A1WithDatabase -Action { param($database) } | Out-Null
    }
    return @($documents)
}
function Add-A1Checkpoint {
    param(
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [AllowNull()][object]$Target,
        [long]$BaselinePages = -1
    )
    Assert-A1Quiescent
    $semantic = Read-A1SemanticTables
    Assert-A1Quiescent
    $snapshot = Read-A1PageSnapshot -Store $script:A1Store `
        -DatabasePath $script:A1DatabasePath `
        -PriorHashes $script:A1PriorHashes
    $overshoot = $null
    if ($null -ne $Target) {
        $threshold = if ([string]$Target.kind -ceq "relative") {
            [long]($BaselinePages + [long]$Target.pages)
        }
        else { [long]$Target.pages }
        if ([long]$snapshot.page_count -lt $threshold) {
            throw "A1 retained checkpoint missed its frozen growth target."
        }
        $overshoot = [long]($snapshot.page_count - $threshold)
    }
    $ordinal = [int]$script:A1Checkpoints.Count
    $indexDocument = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a1_page_index"
        experiment_id = $script:A1ExperimentId
        producer_commit = $script:A1ProducerCommit
        run_id = $script:A1RunId
        plan_sha256 = $script:A1PlanSha256
        environment_sha256 = $script:A1EnvironmentSha256
        provider_sha256 = $script:A1ProviderSha256
        replica = $script:A1ReplicaOrdinal
        checkpoint_id = $CheckpointId
        ordinal = $ordinal
        predecessor_checkpoint_id = $script:A1PriorCheckpoint
        page_count = [long]$snapshot.page_count
        file_size_bytes = [long]$snapshot.file_bytes
        database_sha256 = [string]$snapshot.file_sha256
        ordered_page_sha256 = @($snapshot.hashes)
        changed_page_indices = @(
            $snapshot.changed_pages | ForEach-Object { [long]$_.page_index }
        )
    }
    $indexJson = $indexDocument | ConvertTo-Json -Depth 8 -Compress
    $indexBytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        $indexJson + "`n"
    )
    if ($indexBytes.Length -gt 64MB) {
        throw "A1 checkpoint page index exceeds its JSON byte ceiling."
    }
    if ($indexBytes.Length -gt (768MB - $script:A1Store.RetainedBytes)) {
        throw "A1 checkpoint page index exceeds the bundle byte ceiling."
    }
    $indexLocator = "page-indexes/replica-{0:D2}/{1:D2}-{2}.json" -f @(
        $script:A1ReplicaOrdinal, $ordinal, $CheckpointId
    )
    $indexPath = Get-M1PayloadPath -Session $script:A1Store.Session `
        -RelativePath $indexLocator
    Write-A1CreateNewBytes -Path $indexPath -Bytes $indexBytes `
        -MaximumBytes 64MB
    $script:A1Store.RetainedBytes += [long]$indexBytes.Length
    [void]$script:A1Checkpoints.Add([ordered]@{
        checkpoint_id = $CheckpointId
        ordinal = $ordinal
        actual_file_pages = [long]$snapshot.page_count
        actual_size_bytes = [long]$snapshot.file_bytes
        inserted_rows_total = [long]$script:A1InsertedRows
        table_row_counts = [ordered]@{
            D = [int]$script:A1Rows["D"].Count
            L = [int]$script:A1Rows["L"].Count
            P = [int]$script:A1Rows["P"].Count
            H = [int]$script:A1Rows["H"].Count
        }
        dao_reread = @($semantic)
        quiescent = $true
        post_close_companion = [ordered]@{
            present_after_close = $false
            observed_size_bytes = 0
            retained_for_physical_analysis = $false
        }
        target_baseline_pages = if ($BaselinePages -ge 0) {
            $BaselinePages
        } else { $null }
        target_threshold_pages = if ($null -ne $Target) { $threshold } else { $null }
        target_overshoot_pages = $overshoot
        page_index = [ordered]@{
            path = $indexLocator
            sha256 = Get-A1LowerSha256 -Bytes $indexBytes
            size_bytes = [long]$indexBytes.Length
        }
    })
    $script:A1PriorHashes = [string[]]$snapshot.hashes
    $script:A1PriorCheckpoint = $CheckpointId
}
function Add-A1UntilTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][long]$ThresholdPages
    )
    do {
        Add-A1RowBatch -Role $Role
        Assert-A1Quiescent
        $pages = Get-A1ClosedPageCount
    } while ($pages -lt $ThresholdPages)
}
function Invoke-A1Replica {
    Invoke-A1WithDatabase -Create -Action { param($database) } | Out-Null
    Assert-A1Quiescent
    foreach ($idValue in @($script:A1Plan.checkpoint_design.checkpoint_ids)) {
        $id = [string]$idValue
        if ($id -ceq "E0" -or $id -ceq "E0R" -or
            $id -ceq "L_IDLE_REOPEN" -or $id -ceq "H_IDLE_REOPEN") {
            Add-A1Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "D_DROP") {
            Remove-A1Table -Role "D"
            Add-A1Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "L_DELETE_ALTERNATING") {
            Remove-A1AlternatingRows
            Add-A1Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -ceq "L_REINSERT_SAME") {
            Restore-A1AlternatingRows
            Add-A1Checkpoint -CheckpointId $id -Target $null
            continue
        }
        if ($id -match "^D_(GROW|REGROW)_0128$") {
            if ($id -ceq "D_GROW_0128") { Add-A1Table -Role "D" }
            else { Add-A1Table -Role "D" }
            $baseline = Get-A1ClosedPageCount
            Add-A1UntilTarget -Role "D" -ThresholdPages ($baseline + 128)
            Add-A1Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{ kind = "relative"; pages = 128 }) `
                -BaselinePages $baseline
            continue
        }
        if ($id -match "^([LH])_REL_([0-9]{4})$") {
            $role = [string]$Matches[1]
            $targetPages = [long]$Matches[2]
            if (-not [bool]$script:A1Extant[$role]) {
                Add-A1Table -Role $role
                $script:A1Baselines[$role] = Get-A1ClosedPageCount
            }
            $baseline = [long]$script:A1Baselines[$role]
            Add-A1UntilTarget -Role $role `
                -ThresholdPages ($baseline + $targetPages)
            Add-A1Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{
                    kind = "relative"; role = $role; pages = $targetPages
                }) -BaselinePages $baseline
            continue
        }
        if ($id -match "^P_ABS_([0-9]{5})$") {
            $targetPages = [long]$Matches[1]
            if (-not [bool]$script:A1Extant["P"]) { Add-A1Table -Role "P" }
            Add-A1UntilTarget -Role "P" -ThresholdPages $targetPages
            Add-A1Checkpoint -CheckpointId $id `
                -Target ([pscustomobject]@{
                    kind = "absolute"; role = "P"; pages = $targetPages
                })
            continue
        }
        throw "A1 checkpoint operation is not implemented: $id"
    }
}
function Invoke-A1Worker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][string]$PlanPath,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$ProducerCommit,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )
    $script:A1ReplicaOrdinal = $ReplicaOrdinal
    $script:A1ProducerCommit = $ProducerCommit
    $script:A1RunId = $RunId
    $script:A1PlanSha256 = $PlanSha256
    $script:A1EnvironmentSha256 = $EnvironmentSha256
    $planInput = Read-A1CheckedJson -Path $PlanPath -MaximumBytes 1MB
    $environmentInput = Read-A1CheckedJson -Path $EnvironmentPath -MaximumBytes 1MB
    if ($PlanSha256 -cne $script:A1FrozenPlanSha256 -or
        $planInput.sha256 -cne $PlanSha256 -or
        $environmentInput.sha256 -cne $EnvironmentSha256) {
        throw "A1 worker input bytes differ from controller bindings."
    }
    $script:A1Plan = $planInput.document
    $roleBinding = Assert-A1WorkerPlan -Plan $script:A1Plan
    if ([string]$environmentInput.document.document_type -cne
            "dao_a1_environment" -or
        [string]$environmentInput.document.experiment_id -cne
            $script:A1ExperimentId -or
        [string]$environmentInput.document.plan_sha256 -cne $PlanSha256 -or
        [string]$environmentInput.document.producer_commit -cne
            $ProducerCommit -or
        [string]$environmentInput.document.run_id -cne $RunId -or
        [string]$environmentInput.document.host.process_architecture -cne "x86" -or
        [string]$environmentInput.document.provider.prog_id -cne
            "DAO.DBEngine.36") {
        throw "A1 published environment differs from the worker binding."
    }
    $accepted = $environmentInput.document.provider
    [void](Assert-M1CurrentRegistration -AcceptedProvider $accepted)
    if ((Get-M1FileSha256 -Path ([string]$accepted.server_path)) -cne
        [string]$accepted.server_sha256) {
        throw "A1 provider binary differs from its environment digest."
    }
    $script:A1ProviderSha256 = [string]$accepted.server_sha256
    $providerType = [Type]::GetTypeFromProgID([string]$accepted.prog_id, $false)
    if ($null -eq $providerType -or
        $providerType.GUID.ToString("B").ToUpperInvariant() -cne
            ([string]$accepted.clsid).ToUpperInvariant()) {
        throw "A1 worker provider activation binding differs from preflight."
    }
    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    $bundle = [IO.Path]::GetFullPath($BundleRoot)
    $working = [IO.Path]::GetFullPath($WorkingRoot)
    Assert-M1NoReparseComponents -Path $repository
    Assert-M1NoReparseComponents -Path $bundle
    Assert-M1NoReparseComponents -Path $working
    $session = [pscustomobject]@{ StagingBundle = $bundle }
    $replicaId = "replica-{0:D2}" -f $ReplicaOrdinal
    $replicaRoot = Join-Path $working $replicaId
    if ([IO.Directory]::Exists($replicaRoot)) {
        throw "A1 worker replica directory already exists."
    }
    [void][IO.Directory]::CreateDirectory($replicaRoot)
    Assert-M1NoReparseComponents -Path $replicaRoot
    $script:A1DatabasePath = Join-Path $replicaRoot "ACQUISITION.MDB"
    $script:A1RoleNames = @{}
    foreach ($role in @("D", "L", "P", "H")) {
        $script:A1RoleNames[$role] = [string]$roleBinding.$role
    }
    $script:A1Extant = @{ D = $false; L = $false; P = $false; H = $false }
    $script:A1Rows = @{}
    $script:A1NextId = @{}
    $script:A1Baselines = @{}
    foreach ($role in @("D", "L", "P", "H")) {
        $script:A1Rows[$role] = New-Object 'Collections.Generic.HashSet[int]'
        $script:A1NextId[$role] = 1
    }
    $script:A1InsertedRows = 0
    $script:A1Checkpoints = New-Object Collections.ArrayList
    $script:A1PriorHashes = $null
    $script:A1PriorCheckpoint = $null
    $script:A1Store = New-A1PageStore -Session $session

    $engine = $null
    $workspaces = $null
    $workspace = $null
    $cleanup = New-Object Collections.ArrayList
    $primary = $null
    try {
        $engine = [Activator]::CreateInstance($providerType)
        if ([string]$engine.Version -cne [string]$accepted.provider_version) {
            throw "A1 DBEngine version differs from the bound provider."
        }
        $workspaces = $engine.Workspaces
        $workspace = $workspaces.Item([int]0)
        $script:A1Workspace = $workspace
        Invoke-A1Replica
    }
    catch { $primary = $_ }
    finally {
        Release-M1ComObject -Value $workspace -CleanupErrors $cleanup `
            -Label "A1 workspace release"
        Release-M1ComObject -Value $workspaces -CleanupErrors $cleanup `
            -Label "A1 workspaces release"
        Release-M1ComObject -Value $engine -CleanupErrors $cleanup `
            -Label "A1 engine release"
    }
    Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
        -Label "A1 replica acquisition"
    if ($script:A1Checkpoints.Count -ne
        [int]$script:A1Plan.checkpoint_design.count) {
        throw "A1 worker did not complete the exact checkpoint enumeration."
    }
    $observation = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a1_replica_observation"
        experiment_id = $script:A1ExperimentId
        producer_commit = $ProducerCommit
        repository_url = "https://github.com/oglassdev/jet3-rs.git"
        run_id = $RunId
        plan_sha256 = $PlanSha256
        environment_sha256 = $EnvironmentSha256
        provider_sha256 = [string]$accepted.server_sha256
        replica = $ReplicaOrdinal
        role_binding = [ordered]@{
            D = [string]$roleBinding.D; L = [string]$roleBinding.L
            P = [string]$roleBinding.P; H = [string]$roleBinding.H
        }
        logical_checkpoint_read_bytes = [long]$script:A1Store.LogicalReadBytes
        inserted_rows_total = [long]$script:A1InsertedRows
        changed_hash_entries = [long]$script:A1Store.ChangedEntries
        checkpoints = @($script:A1Checkpoints)
    }
    $json = $observation | ConvertTo-Json -Depth 32 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json + "`n")
    if ($bytes.Length -gt 64MB) {
        throw "A1 replica observation exceeds its JSON byte ceiling."
    }
    if ($bytes.Length -gt (768MB - $script:A1Store.RetainedBytes)) {
        throw "A1 replica observation exceeds the bundle byte ceiling."
    }
    $locator = "observations/replica-{0:D2}.json" -f $ReplicaOrdinal
    $path = Get-M1PayloadPath -Session $session -RelativePath $locator
    Write-A1CreateNewBytes -Path $path -Bytes $bytes -MaximumBytes 64MB
    $script:A1Store.RetainedBytes += [long]$bytes.Length
}
Invoke-A1Worker -RepositoryRoot $RepositoryRoot -BundleRoot $BundleRoot `
    -WorkingRoot $WorkingRoot -PlanPath $PlanPath `
    -EnvironmentPath $EnvironmentPath -ProducerCommit $ProducerCommit `
    -RunId $RunId `
    -PlanSha256 $PlanSha256 -EnvironmentSha256 $EnvironmentSha256 `
    -ReplicaOrdinal $ReplicaOrdinal
