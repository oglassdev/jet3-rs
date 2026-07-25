Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "M1.DaoValues.ps1")

function ConvertTo-M1JsonText {
    param(
        [object]$Document,
        [switch]$Canonical
    )

    if ($Canonical) {
        $json = $Document | ConvertTo-Json -Depth 40 -Compress
    }
    else {
        $json = $Document | ConvertTo-Json -Depth 40
    }
    return $json + "`n"
}

function Get-M1BundleFileReference {
    param(
        [pscustomobject]$Session,
        [string]$RelativePath
    )

    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    return [ordered]@{
        path = $RelativePath
        sha256 = Get-M1FileSha256 -Path $path
    }
}

function Get-M1BundleManifestEntry {
    param(
        [pscustomobject]$Session,
        [string]$RelativePath,
        [string]$Role
    )

    $mediaTypes = @{
        environment = "application/json"
        inventory = "application/json"
        report = "application/json"
        scenario_input = "application/json"
        pair_input = "application/json"
        output_database = "application/vnd.ms-access"
        dao_snapshot = "application/json"
        operation_log = "application/json"
    }
    if (-not $mediaTypes.ContainsKey($Role)) {
        throw "Unsupported M1 manifest role."
    }
    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    $item = Get-Item -LiteralPath $path -Force
    return [ordered]@{
        media_type = $mediaTypes[$Role]
        path = $RelativePath
        role = $Role
        sha256 = Get-M1FileSha256 -Path $path
        size_bytes = [long]$item.Length
    }
}

function Add-M1ManifestPayload {
    param(
        [Collections.ArrayList]$ManifestInputs,
        [pscustomobject]$Session,
        [string]$RelativePath,
        [string]$Role
    )

    if (@(
        $ManifestInputs |
            Where-Object { [string]$_.path -ceq $RelativePath }
    ).Count -ne 0) {
        throw "M1 manifest payload paths must be unique."
    }
    [void]$ManifestInputs.Add(
        (Get-M1BundleManifestEntry -Session $Session `
            -RelativePath $RelativePath -Role $Role)
    )
}

function Sort-M1ManifestEntriesOrdinal {
    param([Collections.ArrayList]$Entries)

    $sorted = New-Object Collections.ArrayList
    foreach ($entry in $Entries) {
        $position = 0
        while (
            $position -lt $sorted.Count -and
            [StringComparer]::Ordinal.Compare(
                [string]$sorted[$position].path,
                [string]$entry.path
            ) -lt 0
        ) {
            $position++
        }
        if (
            $position -lt $sorted.Count -and
            [string]$sorted[$position].path -ceq [string]$entry.path
        ) {
            throw "M1 manifest payload paths must be unique."
        }
        $sorted.Insert($position, $entry)
    }
    return @($sorted)
}

function New-M1Counts {
    param([object[]]$Results)

    $counts = [ordered]@{
        blocked = 0
        error = 0
        fail = 0
        pass = 0
        selected = $Results.Count
        skipped = 0
    }
    foreach ($result in $Results) {
        $status = [string]$result.status
        if (-not $counts.Contains($status)) {
            throw "Unknown M1 result status."
        }
        $counts[$status] = [int]$counts[$status] + 1
    }
    return $counts
}

function Get-M1AggregateStatus {
    param(
        [object[]]$ScenarioResults,
        [object[]]$PairResults
    )

    $statuses = @(
        @($ScenarioResults) + @($PairResults) |
            ForEach-Object { [string]$_.status }
    )
    if ($statuses -contains "error") {
        return "error"
    }
    if ($statuses -contains "fail") {
        return "fail"
    }
    if ($statuses -contains "blocked") {
        return "blocked"
    }
    if ($statuses -contains "skipped") {
        return "blocked"
    }
    if ($statuses.Count -gt 0 -and @($statuses | Where-Object {
        $_ -ne "pass"
    }).Count -eq 0) {
        return "pass"
    }
    return "error"
}

function Invoke-M1PairComparison {
    param(
        [string]$PythonPath,
        [string]$ComparatorPath,
        [string]$PairPath,
        [string]$LeftSnapshotPath,
        [string]$RightSnapshotPath
    )

    $detail = (
        & $PythonPath -B $ComparatorPath $PairPath `
            $LeftSnapshotPath $RightSnapshotPath 2>&1 |
            Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        return [ordered]@{
            detail = Get-M1SafeText -Value $detail -Maximum 2000
            observed = @()
            status = "fail"
        }
    }
    try {
        $observed = @($detail | ConvertFrom-Json)
    }
    catch {
        return [ordered]@{
            detail = "The checked pair comparator returned invalid JSON."
            observed = @()
            status = "error"
        }
    }
    return [ordered]@{
        detail = "The checked deep comparator observed only exact allowances."
        observed = @($observed)
        status = "pass"
    }
}

function Write-M1ScenarioArtifacts {
    param(
        [pscustomobject]$Session,
        [object]$Scenario,
        [object]$Execution,
        [Collections.ArrayList]$ManifestInputs,
        [hashtable]$ScenarioState
    )

    $scenarioId = [string]$Scenario.scenario_id
    $directory = "scenarios/$scenarioId"
    $logRelative = "$directory/operation-log.json"
    Write-M1DurableUtf8 -Session $Session -RelativePath $logRelative `
        -Text (ConvertTo-M1JsonText -Document $Execution.operation_log)
    Add-M1ManifestPayload -ManifestInputs $ManifestInputs -Session $Session `
        -RelativePath $logRelative -Role "operation_log"
    $logReference = Get-M1BundleFileReference -Session $Session `
        -RelativePath $logRelative
    $databaseReference = $null
    $snapshotReference = $null
    $snapshotRelative = $null

    $hasDatabase = -not [string]::IsNullOrWhiteSpace(
        [string]$Execution.database_path
    )
    if ([string]$Execution.status -eq "pass" -and (
        $null -eq $Execution.snapshot -or -not $hasDatabase
    )) {
        throw "Passing DAO execution lacks its database or snapshot."
    }
    if ($hasDatabase) {
        $databaseHash = if ($null -ne $Execution.snapshot) {
            [string]$Execution.snapshot.database_sha256
        }
        else {
            Get-M1FileSha256 -Path ([string]$Execution.database_path)
        }
        $databaseRelative = "databases/$databaseHash.mdb"
        $existingDatabaseEntries = @(
            $ManifestInputs |
                Where-Object { [string]$_.path -ceq $databaseRelative }
        )
        $databasePayloadPath = Get-M1PayloadPath -Session $Session `
            -RelativePath $databaseRelative
        if ($existingDatabaseEntries.Count -eq 0) {
            if (Test-Path -LiteralPath $databasePayloadPath) {
                throw "Untracked content-addressed database payload exists."
            }
            Copy-M1DurableFile -Session $Session `
                -SourcePath ([string]$Execution.database_path) `
                -RelativePath $databaseRelative
            Add-M1ManifestPayload -ManifestInputs $ManifestInputs `
                -Session $Session -RelativePath $databaseRelative `
                -Role "output_database"
        }
        elseif (
            $existingDatabaseEntries.Count -ne 1 -or
            [string]$existingDatabaseEntries[0].role -cne "output_database" -or
            -not (Test-Path -LiteralPath $databasePayloadPath -PathType Leaf) -or
            (Get-M1FileSha256 -Path $databasePayloadPath) -cne $databaseHash
        ) {
            throw "Existing content-addressed database payload is inconsistent."
        }
        $databaseReference = Get-M1BundleFileReference -Session $Session `
            -RelativePath $databaseRelative
        if ($databaseReference.sha256 -cne $databaseHash) {
            throw "Durable database copy differs from the DAO-closed source."
        }
    }

    if ([string]$Execution.status -eq "pass") {
        $snapshotRelative = "$directory/dao-snapshot.json"
        Write-M1DurableUtf8 -Session $Session -RelativePath $snapshotRelative `
            -Text (ConvertTo-M1JsonText -Document $Execution.snapshot -Canonical)
        Add-M1ManifestPayload -ManifestInputs $ManifestInputs -Session $Session `
            -RelativePath $snapshotRelative -Role "dao_snapshot"
        $snapshotReference = Get-M1BundleFileReference -Session $Session `
            -RelativePath $snapshotRelative
    }

    $ScenarioState[$scenarioId] = [ordered]@{
        execution = $Execution
        snapshot_path = if ($null -ne $snapshotRelative) {
            Get-M1PayloadPath -Session $Session -RelativePath $snapshotRelative
        }
        else {
            $null
        }
        snapshot_reference = $snapshotReference
    }
    return [ordered]@{
        dao_snapshot = $snapshotReference
        input = Get-M1BundleFileReference -Session $Session `
            -RelativePath "$directory/input.json"
        operation_log = $logReference
        output_database = $databaseReference
        reason = [string]$Execution.reason
        recipe = [string]$Scenario.recipe
        scenario_id = $scenarioId
        status = [string]$Execution.status
    }
}

function Write-M1ReportAndManifest {
    param(
        [pscustomobject]$Session,
        [string]$GitCommit,
        [string]$RunId,
        [string]$StartedAt,
        [object[]]$ScenarioResults,
        [object[]]$PairResults,
        [Collections.ArrayList]$ManifestInputs,
        [string[]]$CommandLine
    )

    $status = Get-M1AggregateStatus -ScenarioResults $ScenarioResults `
        -PairResults $PairResults
    $reason = if ($status -eq "pass") {
        "All seven controlled DAO scenarios and both exact pairs passed."
    }
    else {
        "The complete controlled M1 inventory recorded a non-passing result."
    }
    $inventoryReference = Get-M1BundleFileReference -Session $Session `
        -RelativePath "inventory.json"
    $environmentReference = Get-M1BundleFileReference -Session $Session `
        -RelativePath "environment.json"
    $report = [ordered]@{
        command_line = @($CommandLine)
        document_type = "dao_evidence_report"
        ended_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        environment = $environmentReference
        git = [ordered]@{
            commit = $GitCommit
            dirty = $false
        }
        inventory = $inventoryReference
        oracle_revision = $GitCommit
        pair_counts = New-M1Counts -Results $PairResults
        pairs = @($PairResults)
        protocol_version = "1.1.0"
        run_id = $RunId
        scenario_counts = New-M1Counts -Results $ScenarioResults
        scenarios = @($ScenarioResults)
        started_at_utc = $StartedAt
        status = $status
        status_reason = $reason
    }
    $reportRelative = "report.json"
    Write-M1DurableUtf8 -Session $Session -RelativePath $reportRelative `
        -Text (ConvertTo-M1JsonText -Document $report)
    Add-M1ManifestPayload -ManifestInputs $ManifestInputs -Session $Session `
        -RelativePath $reportRelative -Role "report"

    $sortedFiles = @(
        Sort-M1ManifestEntriesOrdinal -Entries $ManifestInputs
    )
    $manifest = [ordered]@{
        created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        dirty = $false
        document_type = "dao_bundle_manifest"
        files = @($sortedFiles)
        git_commit = $GitCommit
        pair_ids = @($PairResults | ForEach-Object { $_.pair_id })
        protocol_version = "1.1.0"
        report_path = $reportRelative
        run_id = $RunId
        scenario_ids = @($ScenarioResults | ForEach-Object { $_.scenario_id })
        status = $status
    }
    Write-M1DurableUtf8 -Session $Session `
        -RelativePath "bundle-manifest.json" `
        -Text (ConvertTo-M1JsonText -Document $manifest)
    return [ordered]@{
        reason = $reason
        status = $status
    }
}
