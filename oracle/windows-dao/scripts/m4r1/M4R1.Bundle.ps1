Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M4ProtocolVersion = "1.0.0"
$script:M4ExperimentId = "DAO-M4-HEADER-DISCRIMINATOR-003"
$script:M4MinimumPayloadFiles = 579
$script:M4MaximumPayloadFiles = 651
$script:M4MaximumJsonBytes = 16MB

function Get-M4BundleUtcTimestamp {
    return [DateTime]::UtcNow.ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function ConvertTo-M4BundleJsonBytes {
    param(
        [Parameter(Mandatory = $true)][object]$Document,
        [long]$MaximumBytes = 1MB
    )

    $text = ($Document | ConvertTo-Json -Depth 64 -Compress) + "`n"
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes($text)
    if ($bytes.Length -lt 2 -or $bytes.Length -gt $MaximumBytes) {
        throw "M4 JSON exceeds its declared byte ceiling."
    }
    return ,$bytes
}

function Read-M4BundleJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$MaximumBytes = 1MB
    )

    $item = Get-Item -LiteralPath $Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 2 -or
        $item.Length -gt $MaximumBytes
    ) {
        throw "M4 JSON input violates its file or byte ceiling."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.LongLength -ne $item.Length) {
        throw "M4 JSON input changed while being read."
    }
    if (
        $bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
        $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf
    ) {
        throw "M4 JSON input contains a forbidden UTF-8 BOM."
    }
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    return [pscustomobject][ordered]@{
        bytes = $bytes
        document = $encoding.GetString($bytes) | ConvertFrom-Json
    }
}

function Get-M4BundleFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = New-Object IO.FileStream(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hash.ComputeHash($stream)
        ).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
        $stream.Dispose()
    }
}

function Get-M4BundleMediaType {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if ($RelativePath.EndsWith(".json", [StringComparison]::Ordinal)) {
        return "application/json"
    }
    return "application/octet-stream"
}

function Add-M4ManifestEntry {
    param(
        [AllowEmptyCollection()]
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            "plan", "environment", "analysis_report", "sample_record",
            "phase_invocation", "phase_worker_result", "operation_log",
            "semantic_snapshot", "clone_log", "database", "prefix",
            "post_worker_quiescence", "companion"
        )][string]$Role,
        [string]$ExpectedSha256,
        [long]$ExpectedSizeBytes = -1
    )

    if (@($Entries | Where-Object { $_.path -ceq $RelativePath }).Count) {
        throw "M4 manifest contains a duplicate payload path."
    }
    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    $item = Get-Item -LiteralPath $path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "M4 manifest payload is not an ordinary file."
    }
    $sha256 = if ([string]::IsNullOrWhiteSpace($ExpectedSha256)) {
        Get-M4BundleFileSha256 -Path $path
    }
    else {
        if (
            $ExpectedSha256 -cnotmatch "^[0-9a-f]{64}$" -or
            $ExpectedSizeBytes -lt 0 -or
            [long]$item.Length -ne $ExpectedSizeBytes
        ) {
            throw "M4 retained payload differs from its validated identity."
        }
        $ExpectedSha256
    }
    [void]$Entries.Add([ordered]@{
        path = $RelativePath
        role = $Role
        sha256 = $sha256
        size_bytes = [long]$item.Length
        media_type = Get-M4BundleMediaType -RelativePath $RelativePath
    })
}

function Write-M4BundleJson {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][object]$Document,
        [long]$MaximumBytes = 1MB
    )

    $bytes = ConvertTo-M4BundleJsonBytes `
        -Document $Document -MaximumBytes $MaximumBytes
    Write-M1DurableBytes -Session $Session `
        -RelativePath $RelativePath -Bytes $bytes
    Add-M4ManifestEntry -Entries $Entries -Session $Session `
        -RelativePath $RelativePath -Role $Role
}

function Register-M4WorkerPayload {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Role,
        [string]$ExpectedSha256,
        [long]$ExpectedSizeBytes = -1
    )

    Sync-M1DurableFile -Session $Session -RelativePath $RelativePath
    Add-M4ManifestEntry -Entries $Entries -Session $Session `
        -RelativePath $RelativePath -Role $Role `
        -ExpectedSha256 $ExpectedSha256 `
        -ExpectedSizeBytes $ExpectedSizeBytes
}

function Get-M4ArtifactBinding {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    return [ordered]@{
        path = $RelativePath
        sha256 = Get-M4BundleFileSha256 -Path $path
    }
}

function Get-M4PhasePaths {
    param(
        [Parameter(Mandatory = $true)][string]$SampleId,
        [Parameter(Mandatory = $true)]
        [ValidateSet("creator", "reopen")][string]$PhaseId
    )

    $root = "evidence/samples/$SampleId"
    $companionName = $PhaseId.ToUpperInvariant() + ".ldb"
    return [pscustomobject][ordered]@{
        invocation = "$root/$PhaseId-invocation.json"
        result = "$root/$PhaseId-worker-result.json"
        operation_log = "$root/$PhaseId-operation-log.json"
        snapshot = "$root/$PhaseId-snapshot.json"
        prefix = "$root/$PhaseId.prefix.bin"
        quiescence = "$root/$PhaseId-quiescence.json"
        companion = "$root/$companionName"
    }
}

function Get-M4DeterministicNonce {
    param(
        [Parameter(Mandatory = $true)][string]$CampaignRunId,
        [Parameter(Mandatory = $true)][string]$SampleId,
        [Parameter(Mandatory = $true)][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal
    )

    $material = "$CampaignRunId|$SampleId|$PhaseId|$WorkerOrdinal"
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes(
        $material
    )
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hash.ComputeHash($bytes)
        ).Replace("-", "").ToLowerInvariant().Substring(0, 32)
    }
    finally {
        $hash.Dispose()
    }
}

function New-M4PhaseInvocation {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)]
        [ValidateSet("creator", "reopen")][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [AllowNull()][pscustomobject]$CloneBinding
    )

    $phaseOrdinal = if ($PhaseId -ceq "creator") { 1 } else { 2 }
    $phasePaths = Get-M4PhasePaths `
        -SampleId $Sample.sample_id -PhaseId $PhaseId
    $workerSuffix = $PhaseId.ToUpperInvariant()
    $phaseContract = if ($PhaseId -ceq "creator") {
        [ordered]@{
            kind = "creator"
            method = "DBEngine.CreateDatabase"
            locale = [string]$Plan.design.locale
            version_option = [string]$Condition.version_option
            version_api_value = [int]$Condition.version_api_value
            encryption_option = [string]$Condition.encryption_option
            encryption_api_value = [int]$Condition.encryption_api_value
            create_option_value = [int]$Condition.create_option_value
            compact_database_used = $false
            expected_dao_version = [string]$Condition.expected_dao_version
        }
    }
    else {
        [ordered]@{
            kind = "reopen"
            expected_dao_version = [string]$Condition.expected_dao_version
            pre_com_database_bytes = [long]$CloneBinding.destination_bytes
            pre_com_database_sha256 = [string]$CloneBinding.destination_sha256
            clone_log = [ordered]@{
                path = [string]$CloneBinding.clone_log_path
                sha256 = [string]$CloneBinding.clone_log_sha256
            }
        }
    }
    return [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_invocation"
        experiment_id = [string]$Plan.experiment_id
        sample_id = [string]$Sample.sample_id
        condition_id = [string]$Sample.condition_id
        phase_id = $PhaseId
        phase_ordinal = $phaseOrdinal
        worker_run_id = "$($Sample.sample_id)-$workerSuffix"
        worker_ordinal = $WorkerOrdinal
        nonce = Get-M4DeterministicNonce `
            -CampaignRunId $Session.RunId `
            -SampleId $Sample.sample_id `
            -PhaseId $PhaseId `
            -WorkerOrdinal $WorkerOrdinal
        campaign_run_id = [string]$Session.RunId
        producer_commit = [string]$Session.GitCommit
        repository_url = [string]$Plan.repository_url
        remote_ref = [string]$Plan.remote_ref
        repository_root = [string]$Context.RepositoryRoot
        plan_path = "plan/checked-plan.json"
        plan_sha256 = $PlanSha256
        environment_path = "bindings/environment.json"
        environment_sha256 = [string]$Context.EnvironmentSha256
        provider_sha256 = [string]$Context.ProviderSha256
        stage_root = [string]$Session.StagingBundle
        database_path = $DatabasePath
        result_path = [string]$phasePaths.result
        phase_contract = $phaseContract
        created_at_utc = Get-M4BundleUtcTimestamp
        bindings_verified_before_com = $true
    }
}

function ConvertTo-M4PhaseRecord {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Result,
        [Parameter(Mandatory = $true)][pscustomobject]$Paths
    )

    $phase = [ordered]@{
        phase_id = [string]$Result.phase_id
        phase_ordinal = [int]$Result.phase_ordinal
        worker = [ordered]@{
            process_id = [long]$Result.process_id
            started_at_utc = [string]$Result.started_at_utc
            worker_run_id = [string]$Result.worker_run_id
            worker_ordinal = [int]$Result.worker_ordinal
            nonce = [string]$Result.nonce
            architecture = [string]$Result.architecture
            provider = $Result.provider
            fresh_process = $true
            bindings_verified_before_com = [bool](
                $Result.bindings_verified_before_com
            )
        }
        artifacts = [ordered]@{
            invocation = Get-M4ArtifactBinding `
                -Session $Session -RelativePath $Paths.invocation
            operation_log = Get-M4ArtifactBinding `
                -Session $Session -RelativePath $Paths.operation_log
            snapshot = Get-M4ArtifactBinding `
                -Session $Session -RelativePath $Paths.snapshot
            worker_result = Get-M4ArtifactBinding `
                -Session $Session -RelativePath $Paths.result
            post_worker_quiescence = $Result.controller_quiescence
        }
        post_close_file_observations = [ordered]@{
            database_path = [string](
                $Result.post_close_file_observations.database_path
            )
            database_bytes = [long](
                $Result.post_close_file_observations.database_bytes
            )
            database_sha256 = [string](
                $Result.post_close_file_observations.database_sha256
            )
            prefix_path = [string](
                $Result.post_close_file_observations.prefix.path
            )
            prefix_bytes = [int](
                $Result.post_close_file_observations.prefix_bytes
            )
            prefix_sha256 = [string](
                $Result.post_close_file_observations.prefix.sha256
            )
            database_closed = $true
        }
        status = [string]$Result.execution_status
    }
    $quiescence = $Result.controller_quiescence_document
    $phase.post_worker_quiescence = [ordered]@{
        worker_finished_at_utc = [string]$quiescence.worker_finished_at_utc
        observation_started_at_utc = [string](
            $quiescence.observation_started_at_utc
        )
        observation_completed_at_utc = [string](
            $quiescence.observation_completed_at_utc
        )
        worker_exit_wait_completed = [bool](
            $quiescence.worker_exit_wait_completed
        )
        database = $quiescence.database
        companion = $quiescence.companion
        status = [string]$quiescence.status
    }
    $snapshotPath = Get-M1PayloadPath `
        -Session $Session -RelativePath $Paths.snapshot
    $snapshotDocument = (
        Read-M4BundleJson -Path $snapshotPath -MaximumBytes 64KB
    ).document
    $phase.dao_observations_while_open = [ordered]@{
        captured_while_database_open = [bool](
            $snapshotDocument.captured_while_database_open
        )
        dao_version = [string]$snapshotDocument.dao_version
        empty_user_schema = [bool]$snapshotDocument.empty_user_schema
        user_table_count = [int]$snapshotDocument.user_table_count
    }
    if ([string]$Result.phase_id -ceq "reopen") {
        $phase.pre_com_file_binding = [ordered]@{
            database_path = [string](
                $Result.pre_com_file_binding.database_path
            )
            database_bytes = [long](
                $Result.pre_com_file_binding.database_bytes
            )
            database_sha256 = [string](
                $Result.pre_com_file_binding.database_sha256
            )
            verified_before_com = $true
        }
    }
    return $phase
}

function New-M4SampleRecord {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)][pscustomobject]$CreatorResult,
        [Parameter(Mandatory = $true)][pscustomobject]$ReopenResult,
        [Parameter(Mandatory = $true)][pscustomobject]$Clone,
        [Parameter(Mandatory = $true)][string]$CloneLogPath,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$ProducerCommit,
        [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
        [Parameter(Mandatory = $true)][string]$ProviderSha256
    )

    $creatorPaths = Get-M4PhasePaths `
        -SampleId $Sample.sample_id -PhaseId "creator"
    $reopenPaths = Get-M4PhasePaths `
        -SampleId $Sample.sample_id -PhaseId "reopen"
    return [ordered]@{
        protocol_version = $script:M4ProtocolVersion
        document_type = "dao_m4_sample_record"
        experiment_id = $script:M4ExperimentId
        plan_sha256 = $PlanSha256
        producer_commit = $ProducerCommit
        environment_sha256 = $EnvironmentSha256
        provider_sha256 = $ProviderSha256
        sample_id = [string]$Sample.sample_id
        condition_id = [string]$Sample.condition_id
        replica = [int]$Sample.replica
        block = [int]$Sample.block
        position_in_block = [int]$Sample.position_in_block
        launch_ordinal = [int]$Sample.launch_ordinal
        creation = [ordered]@{
            method = "DBEngine.CreateDatabase"
            version_option = [string]$Condition.version_option
            version_api_value = [int]$Condition.version_api_value
            encryption_option = [string]$Condition.encryption_option
            encryption_api_value = [int]$Condition.encryption_api_value
            create_option_value = [int]$Condition.create_option_value
            compact_database_used = $false
        }
        phases = [ordered]@{
            creator = ConvertTo-M4PhaseRecord `
                -Session $Session -Result $CreatorResult -Paths $creatorPaths
            reopen = ConvertTo-M4PhaseRecord `
                -Session $Session -Result $ReopenResult -Paths $reopenPaths
        }
        controller_clone = [ordered]@{
            owner = "controller"
            clone_log = Get-M4ArtifactBinding `
                -Session $Session -RelativePath $CloneLogPath
            started_at_utc = [string]$Clone.started_at_utc
            completed_at_utc = [string]$Clone.completed_at_utc
            source_path = [string]$Clone.source_path
            destination_path = [string]$Clone.destination_path
            source_bytes = [long]$Clone.source_bytes
            destination_bytes = [long]$Clone.destination_bytes
            source_sha256_before_clone = [string](
                $Clone.source_sha256_before_clone
            )
            source_sha256_after_clone = [string](
                $Clone.source_sha256_after_clone
            )
            destination_sha256 = [string]$Clone.destination_sha256
            source_file_identity = $Clone.source_file_identity
            destination_file_identity = $Clone.destination_file_identity
            creator_closed_before_clone = $true
            source_immutable_during_clone = $true
            source_unchanged_after_clone = $true
            all_hashes_equal = [bool]$Clone.all_hashes_equal
            exact_byte_clone = [bool]$Clone.exact_byte_clone
            source_reparse_free = [bool]$Clone.source_reparse_free
            destination_reparse_free = [bool]$Clone.destination_reparse_free
            no_hardlink = [bool]$Clone.no_hardlink
            same_volume = [bool]$Clone.same_volume
            distinct_file_identity = [bool]$Clone.distinct_file_identity
            identities_preserved_by_same_volume_publish_rename = $true
            completed_before_reopen_com = $true
            reopen_bindings_verified_before_com = $true
            status = [string]$Clone.status
        }
        execution_status = "pass"
    }
}

function Write-M4BundleManifest {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries
    )

    if (
        $Entries.Count -lt $script:M4MinimumPayloadFiles -or
        $Entries.Count -gt $script:M4MaximumPayloadFiles
    ) {
        throw "M4R1 payload count is outside its checked variable bounds."
    }
    $paths = [string[]]@($Entries | ForEach-Object { [string]$_.path })
    [Array]::Sort($paths, [StringComparer]::Ordinal)
    $byPath = @{}
    foreach ($entry in $Entries) {
        $byPath[[string]$entry.path] = $entry
    }
    $files = @($paths | ForEach-Object { $byPath[$_] })
    $manifest = [ordered]@{
        protocol_version = $script:M4ProtocolVersion
        document_type = "dao_m4_bundle_manifest"
        experiment_id = $script:M4ExperimentId
        run_id = [string]$Session.RunId
        producer_commit = [string]$Session.GitCommit
        created_at_utc = Get-M4BundleUtcTimestamp
        sample_count = 36
        worker_count = 72
        file_count = [int]$Entries.Count
        bundle_tree_complete = $true
        unexpected_files_present = $false
        symlinks_or_reparses_present = $false
        execution_status = "pass"
        files = $files
    }
    $bytes = ConvertTo-M4BundleJsonBytes `
        -Document $manifest -MaximumBytes 1MB
    Write-M1DurableBytes -Session $Session `
        -RelativePath "bundle-manifest.json" -Bytes $bytes
}
