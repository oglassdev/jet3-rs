Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-M4PhaseResult {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Result,
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [AllowNull()][pscustomobject]$PriorResult
    )

    foreach ($property in @(
        "sample_id", "phase_id", "phase_ordinal", "worker_run_id",
        "worker_ordinal", "nonce"
    )) {
        if (
            [string]$Result.$property -cne
                [string]$Invocation.$property
        ) {
            throw "M4 worker result identity differs from its invocation."
        }
    }
    if (
        [string]$Result.execution_status -cne "pass" -or
        [string]$Result.architecture -cne "x86" -or
        -not [bool]$Result.bindings_verified_before_com
    ) {
        throw "M4 worker did not return a bound passing x86 result."
    }
    if (
        [string]$Result.provider.server_sha256 -cne
            [string]$Invocation.provider_sha256
    ) {
        throw "M4 worker provider identity drifted."
    }
    $started = [DateTimeOffset]::Parse(
        [string]$Result.started_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    $finished = [DateTimeOffset]::Parse(
        [string]$Result.finished_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    if ($finished -lt $started) {
        throw "M4 worker timestamps are reversed."
    }
    if ($null -ne $PriorResult) {
        if (
            [string]$PriorResult.worker_run_id -ceq
                [string]$Result.worker_run_id -or
            [string]$PriorResult.nonce -ceq [string]$Result.nonce -or
            (
                [long]$PriorResult.process_id -eq [long]$Result.process_id -and
                [string]$PriorResult.started_at_utc -ceq
                    [string]$Result.started_at_utc
            )
        ) {
            throw "M4 creator and reopen did not use distinct fresh workers."
        }
        $priorFinished = [DateTimeOffset]::Parse(
            [string]$PriorResult.finished_at_utc,
            [Globalization.CultureInfo]::InvariantCulture
        )
        if ($started -lt $priorFinished) {
            throw "M4 reopen began before the creator worker exited."
        }
    }
}

function Assert-M4CloneChronology {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Creator,
        [Parameter(Mandatory = $true)][pscustomobject]$Clone,
        [Parameter(Mandatory = $true)][pscustomobject]$Reopen
    )

    $creatorFinished = [DateTimeOffset]::Parse(
        [string]$Creator.finished_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    $cloneStarted = [DateTimeOffset]::Parse(
        [string]$Clone.started_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    $cloneFinished = [DateTimeOffset]::Parse(
        [string]$Clone.completed_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    $reopenStarted = [DateTimeOffset]::Parse(
        [string]$Reopen.started_at_utc,
        [Globalization.CultureInfo]::InvariantCulture
    )
    if (
        $creatorFinished -gt $cloneStarted -or
        $cloneStarted -gt $cloneFinished -or
        $cloneFinished -gt $reopenStarted
    ) {
        throw "M4 creator, clone, and reopen chronology is invalid."
    }
}

function Get-M4DirectoryMetadataProjection {
    param([Parameter(Mandatory = $true)][string]$Root)

    Assert-M1NoReparseComponents -Path $Root
    $items = New-Object Collections.ArrayList
    $pending = New-Object 'Collections.Generic.Queue[string]'
    $pending.Enqueue([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $path = $pending.Dequeue()
        $item = Get-Item -LiteralPath $path -Force
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "M4 staged directory projection contains a replacement."
        }
        [void]$items.Add($item)
        if ($items.Count -gt 256) {
            throw "M4 staged directory count exceeds its quiescence bound."
        }
        foreach ($child in [IO.Directory]::GetDirectories($path)) {
            $pending.Enqueue([IO.Path]::GetFullPath($child))
        }
    }
    $rows = foreach ($item in ($items | Sort-Object -Property FullName)) {
        "{0}|{1}|{2}|{3}" -f @(
            [string]$item.FullName,
            [long]$item.CreationTimeUtc.Ticks,
            [long]$item.LastWriteTimeUtc.Ticks,
            [long]$item.Attributes
        )
    }
    return ($rows -join "`n")
}

function Wait-M4DirectoryMetadataQuiescence {
    param([Parameter(Mandatory = $true)][string]$Root)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    $previous = Get-M4DirectoryMetadataProjection -Root $Root
    $stableIntervals = 0
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        $current = Get-M4DirectoryMetadataProjection -Root $Root
        if ([string]$current -ceq [string]$previous) {
            $stableIntervals += 1
            if ($stableIntervals -ge 3) { return }
        }
        else {
            $stableIntervals = 0
        }
        $previous = $current
    }
    throw "M4 staged directory metadata did not quiesce within 15 seconds."
}

function Move-M4OwnedDirectoryToQuarantine {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedParent,
        [Parameter(Mandatory = $true)][string]$ExpectedNamePattern,
        [Parameter(Mandatory = $true)][string]$QuarantinePrefix
    )

    $full = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($full)
    $name = [IO.Path]::GetFileName($full)
    if (
        -not $parent.Equals(
            [IO.Path]::GetFullPath($ExpectedParent),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $name -cnotmatch $ExpectedNamePattern
    ) {
        throw "Refusing to quarantine outside the owned M4 boundary."
    }
    $item = Get-Item -LiteralPath $full -Force
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Refusing to quarantine a replacement or reparse root."
    }
    Assert-M1NoReparseComponents -Path $parent
    $target = Join-Path $parent (
        $QuarantinePrefix + [Guid]::NewGuid().ToString("N")
    )
    if (Test-Path -LiteralPath $target) {
        throw "M4 quarantine destination collision."
    }
    [IO.Directory]::Move($full, $target)
}

function Register-M4PhaseArtifacts {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Paths,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [Parameter(Mandatory = $true)][pscustomobject]$Result
    )

    foreach ($payload in @(
        @($Paths.result, "phase_worker_result"),
        @($Paths.operation_log, "operation_log"),
        @($Paths.snapshot, "semantic_snapshot"),
        @($Paths.prefix, "prefix")
    )) {
        Register-M4WorkerPayload -Session $Session -Entries $Entries `
            -RelativePath ([string]$payload[0]) -Role ([string]$payload[1])
    }
    Register-M4WorkerPayload -Session $Session -Entries $Entries `
        -RelativePath $DatabasePath -Role "database" `
        -ExpectedSha256 (
            [string]$Result.post_close_file_observations.database_sha256
        ) `
        -ExpectedSizeBytes (
            [long]$Result.post_close_file_observations.database_bytes
    )
}

function Write-M4RetainedAnalysis {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$ContractPath
    )

    $relativePath = "analysis/report.json"
    $scratchPath = Join-Path $Session.WorkingPath "analysis-report.json"
    if (Test-Path -LiteralPath $scratchPath) {
        throw "M4 analysis scratch path collision."
    }
    Assert-M1NoReparseComponents -Path $Session.WorkingPath
    Invoke-M4ContractCommand -Context $Context `
        -ContractPath $ContractPath `
        -Arguments @(
            "build-analysis", "--bundle-root",
            $Session.StagingBundle, "--output", $scratchPath
        ) -Label "M4 bounded analysis"
    Assert-M1NoReparseComponents -Path $scratchPath
    $analysisInput = Read-M4BundleJson `
        -Path $scratchPath -MaximumBytes 16MB
    Write-M1DurableBytes -Session $Session `
        -RelativePath $relativePath -Bytes $analysisInput.bytes
    Add-M4ManifestEntry -Entries $Entries -Session $Session `
        -RelativePath $relativePath -Role "analysis_report"
    [IO.File]::Delete($scratchPath)
}

function Invoke-M4Campaign {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$GitCommit,
        [Parameter(Mandatory = $true)][string]$RunId,
        [AllowNull()][scriptblock]$FaultInjector
    )

    $runIdMatch = [regex]::Match(
        $RunId, "^[0-9]{8}T[0-9]{6}Z-m4-[a-z0-9-]{1,24}$"
    )
    if (-not $runIdMatch.Success -or $runIdMatch.Length -ne $RunId.Length) {
        throw "M4 RunId does not match the controlled campaign format."
    }
    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    $planPath = Join-Path $repository (
        "oracle/windows-dao/experiments/m4r2/" +
        "m4-header-discriminator-r2.plan.json"
    )
    $contractPath = Join-Path $repository (
        "oracle/windows-dao/scripts/m4r1_contract.py"
    )
    $workerPath = Join-Path $repository (
        "oracle/windows-dao/scripts/run-m4r1-phase.ps1"
    )
    $executedSources = @(
        "oracle/windows-dao/scripts/run-m4r1-controlled.ps1",
        "oracle/windows-dao/scripts/run-m4r1-phase.ps1",
        "oracle/windows-dao/scripts/m4r1_contract.py",
        "oracle/windows-dao/scripts/m4r1_records.py",
        "oracle/windows-dao/scripts/m4r1_bundle.py",
        "oracle/windows-dao/scripts/m4r1_campaign.py",
        "oracle/windows-dao/scripts/m4r1_spec.py",
        "oracle/windows-dao/scripts/m4r1_phase.py",
        "oracle/windows-dao/scripts/m4r1_snapshot.py",
        "oracle/windows-dao/scripts/m4r1_analysis.py",
        "oracle/windows-dao/scripts/m4r1/M4R1.Controller.ps1",
        "oracle/windows-dao/scripts/m4r1/M4R1.ControllerRuntime.ps1",
        "oracle/windows-dao/scripts/m4r1/M4R1.Bundle.ps1",
        "oracle/windows-dao/scripts/m4r1/M4R1.Quiescence.ps1",
        "oracle/windows-dao/scripts/m4r1/M4R1.Artifacts.ps1",
        "oracle/windows-dao/scripts/m4/M4.Clone.ps1",
        "oracle/windows-dao/scripts/m4/M4.Dao.ps1",
        "oracle/windows-dao/scripts/m4/M4.Worker.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
        "oracle/windows-dao/experiments/m4r2/plan.schema.json",
        "oracle/windows-dao/experiments/m4r2/invocation.schema.json",
        "oracle/windows-dao/experiments/m4r2/worker-result.schema.json",
        "oracle/windows-dao/experiments/m4r2/operation-log.schema.json",
        "oracle/windows-dao/experiments/m4r2/snapshot.schema.json",
        "oracle/windows-dao/experiments/m4r2/clone-log.schema.json",
        "oracle/windows-dao/experiments/m4r2/post-worker-quiescence.schema.json",
        "oracle/windows-dao/experiments/m4r2/sample-record.schema.json",
        "oracle/windows-dao/experiments/m4r2/analysis-report.schema.json",
        "oracle/windows-dao/experiments/m4r2/bundle-manifest.schema.json",
        "oracle/windows-dao/experiments/m4r2/m4-header-discriminator-r2.plan.json",
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
        "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
        "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
        "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
        "oracle/windows-dao/scripts/m1/M1.Dao.ps1",
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1",
        "oracle/windows-dao/scripts/m1_bundle_validation.py",
        "oracle/windows-dao/scripts/protocol_cli.py",
        "oracle/windows-dao/scripts/protocol_validation.py",
        "oracle/windows-dao/scripts/validate_m1_protocol.py",
        "oracle/windows-dao/protocol/v1_1/bundle-manifest.schema.json",
        "oracle/windows-dao/protocol/v1_1/canonical-snapshot.schema.json",
        "oracle/windows-dao/protocol/v1_1/environment.schema.json",
        "oracle/windows-dao/protocol/v1_1/evidence-report.schema.json",
        "oracle/windows-dao/protocol/v1_1/example-inventory.schema.json",
        "oracle/windows-dao/protocol/v1_1/operation-log.schema.json",
        "oracle/windows-dao/protocol/v1_1/pair.schema.json",
        "oracle/windows-dao/protocol/v1_1/scenario.schema.json"
    )

    $context = $null
    $session = $null
    $workerPowerShell = $null
    try {
        $context = Invoke-M1Preflight `
            -RepositoryRoot $repository `
            -EnvironmentPath $EnvironmentPath `
            -OutputRoot $OutputRoot `
            -GitCommit $GitCommit `
            -RunId $RunId `
            -ExecutedRepoRelativeSourcePaths $executedSources
        if (
            [string]$context.Environment.host.process_architecture -cne
                "x86" -or
            [string]$context.AcceptedProvider.prog_id -cne
                "DAO.DBEngine.36"
        ) {
            throw "M4 requires the ready checked x86 DAO provider environment."
        }
        $workerPowerShell = Get-M4WorkerPowerShellBinding

        $planInput = Read-M4BundleJson -Path $planPath -MaximumBytes 1MB
        $plan = $planInput.document
        $planSha = Get-M1ByteArraySha256 -Bytes $planInput.bytes
        Invoke-M4ContractCommand -Context $context `
            -ContractPath $contractPath `
            -Arguments @("validate-plan", $planPath) `
            -Label "M4 checked-plan validation"
        Assert-M4ExactRemoteCommit -Context $context `
            -RepositoryUrl ([string]$plan.repository_url) `
            -RemoteRef ([string]$plan.remote_ref)
        [void](Assert-M1RuntimeBinding -Context $context)

        $session = New-M1PublicationSession `
            -RepositoryRoot $repository `
            -OutputRoot $OutputRoot `
            -GitCommit $GitCommit `
            -RunId $RunId `
            -MaxFileBytes 16MB `
            -MaxTotalBytes 128MB `
            -FaultInjector $FaultInjector
        $entries = New-Object Collections.ArrayList
        Write-M1DurableBytes -Session $session `
            -RelativePath "plan/checked-plan.json" -Bytes $planInput.bytes
        Add-M4ManifestEntry -Entries $entries -Session $session `
            -RelativePath "plan/checked-plan.json" -Role "plan"
        Write-M1DurableBytes -Session $session `
            -RelativePath "bindings/environment.json" `
            -Bytes $context.EnvironmentBytes
        Add-M4ManifestEntry -Entries $entries -Session $session `
            -RelativePath "bindings/environment.json" -Role "environment"

        $completedSamples = 0
        foreach ($sample in $plan.samples) {
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-M4ExactRemoteCommit -Context $context `
                -RepositoryUrl ([string]$plan.repository_url) `
                -RemoteRef ([string]$plan.remote_ref)
            $condition = @(
                $plan.conditions | Where-Object {
                    $_.condition_id -ceq $sample.condition_id
                }
            )
            if ($condition.Count -ne 1) {
                throw "M4 sample condition projection is not unique."
            }
            $condition = $condition[0]
            $creatorOrdinal = (2 * [int]$sample.launch_ordinal) - 1
            $reopenOrdinal = 2 * [int]$sample.launch_ordinal
            $creatorResult = Invoke-M4CheckedPhase `
                -Context $context -Session $session -Plan $plan `
                -Entries $entries -Sample $sample -Condition $condition `
                -PhaseId "creator" -WorkerOrdinal $creatorOrdinal `
                -PlanSha256 $planSha `
                -DatabasePath ([string]$sample.creator_database_path) `
                -ContractPath $contractPath `
                -WorkerPath $workerPath `
                -PowerShellBinding $workerPowerShell

            $cloneRelative = (
                "evidence/samples/$($sample.sample_id)/clone-log.json"
            )
            $creatorDatabase = Get-M1PayloadPath -Session $session `
                -RelativePath ([string]$sample.creator_database_path)
            $reopenDatabase = Get-M1PayloadPath -Session $session `
                -RelativePath ([string]$sample.reopen_database_path)
            $clone = Invoke-M4BoundedClone `
                -ControllerRoot $session.StagingBundle `
                -SourcePath $creatorDatabase `
                -DestinationPath $reopenDatabase `
                -MaximumBytes ([long]$plan.bounds.max_database_bytes)
            $cloneLog = [ordered]@{
                protocol_version = "1.0.0"
                document_type = "dao_m4_clone_log"
                experiment_id = [string]$plan.experiment_id
                sample_id = [string]$sample.sample_id
                started_at_utc = [string]$clone.started_at_utc
                completed_at_utc = [string]$clone.completed_at_utc
                source_path = [string]$clone.source_path
                destination_path = [string]$clone.destination_path
                source_bytes = [long]$clone.source_bytes
                destination_bytes = [long]$clone.destination_bytes
                source_sha256_before_clone = [string](
                    $clone.source_sha256_before_clone
                )
                source_sha256_after_clone = [string](
                    $clone.source_sha256_after_clone
                )
                destination_sha256 = [string]$clone.destination_sha256
                source_file_identity = $clone.source_file_identity
                destination_file_identity = $clone.destination_file_identity
                all_hashes_equal = [bool]$clone.all_hashes_equal
                same_volume = [bool]$clone.same_volume
                distinct_file_identity = [bool]$clone.distinct_file_identity
                no_hardlink = [bool]$clone.no_hardlink
                reparse_free = (
                    [bool]$clone.source_reparse_free -and
                    [bool]$clone.destination_reparse_free
                )
                completed_before_reopen_com = $true
                status = [string]$clone.status
            }
            Write-M4BundleJson -Session $session -Entries $entries `
                -RelativePath $cloneRelative -Role "clone_log" `
                -Document $cloneLog
            $cloneBinding = [pscustomobject][ordered]@{
                destination_bytes = [long]$clone.destination_bytes
                destination_sha256 = [string]$clone.destination_sha256
                clone_log_path = $cloneRelative
                clone_log_sha256 = (
                    Get-M4BundleFileSha256 -Path (
                        Get-M1PayloadPath -Session $session `
                            -RelativePath $cloneRelative
                    )
                )
            }

            $reopenResult = Invoke-M4CheckedPhase `
                -Context $context -Session $session -Plan $plan `
                -Entries $entries -Sample $sample -Condition $condition `
                -PhaseId "reopen" -WorkerOrdinal $reopenOrdinal `
                -PlanSha256 $planSha `
                -DatabasePath ([string]$sample.reopen_database_path) `
                -ContractPath $contractPath `
                -WorkerPath $workerPath `
                -PowerShellBinding $workerPowerShell `
                -PriorResult $creatorResult `
                -CloneBinding $cloneBinding
            Assert-M4CloneChronology -Creator $creatorResult `
                -Clone $clone -Reopen $reopenResult

            $record = New-M4SampleRecord `
                -Session $session -Sample $sample -Condition $condition `
                -CreatorResult $creatorResult -ReopenResult $reopenResult `
                -Clone $clone -CloneLogPath $cloneRelative `
                -PlanSha256 $planSha -ProducerCommit $GitCommit `
                -EnvironmentSha256 $context.EnvironmentSha256 `
                -ProviderSha256 $context.ProviderSha256
            Write-M4BundleJson -Session $session -Entries $entries `
                -RelativePath ([string]$sample.record_path) `
                -Role "sample_record" -Document $record `
                -MaximumBytes ([long]$plan.bounds.max_sample_record_bytes)
            $recordPath = Get-M1PayloadPath -Session $session `
                -RelativePath ([string]$sample.record_path)
            Invoke-M4ContractCommand -Context $context `
                -ContractPath $contractPath `
                -Arguments @(
                    "validate-sample", "--bundle-root",
                    $session.StagingBundle, "--record", $recordPath
                ) -Label "M4 completed sample validation"
            $completedSamples += 1
        }
        if (
            $completedSamples -ne 36 -or
            $entries.Count -lt 578 -or $entries.Count -gt 650
        ) {
            throw "M4 retained sample topology is incomplete before analysis."
        }

        Write-M4RetainedAnalysis -Context $context -Session $session `
            -Entries $entries -ContractPath $contractPath
        Write-M4BundleManifest -Session $session -Entries $entries
        Wait-M4DirectoryMetadataQuiescence -Root $session.StagingBundle

        $validationBlock = {
            param($bundle)
            Invoke-M4ContractCommand -Context $context `
                -ContractPath $contractPath `
                -Arguments @("validate-bundle", $bundle) `
                -Label "M4 staged bundle validation"
        }
        $recheck = {
            param($stage)
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-M4ExactRemoteCommit -Context $context `
                -RepositoryUrl ([string]$plan.repository_url) `
                -RemoteRef ([string]$plan.remote_ref)
            Invoke-M4ContractCommand -Context $context `
                -ContractPath $contractPath `
                -Arguments @("validate-bundle", $stage.StagingBundle) `
                -Label "M4 pre-publication binding recheck"
            return $true
        }
        Publish-M1Stage -Stage $session `
            -RecheckScriptBlock $recheck `
            -ValidationScriptBlock $validationBlock
        $session = $null
        return Join-Path (Join-Path $OutputRoot $GitCommit) $RunId
    }
    catch {
        $originalError = $_
        if ($null -ne $session) {
            try {
                Remove-M1PublicationStaging -Session $session
            }
            catch {
                if (
                    Test-Path -LiteralPath $session.StagingRoot `
                        -PathType Container
                ) {
                    Move-M4OwnedDirectoryToQuarantine `
                        -Path $session.StagingRoot `
                        -ExpectedParent $session.OutputRoot `
                        -ExpectedNamePattern "^\.m1-stage-[0-9a-f]{32}$" `
                        -QuarantinePrefix ".m4-quarantine-"
                }
            }
        }
        throw $originalError
    }
    finally {
        if ($null -ne $workerPowerShell) {
            try { $workerPowerShell.Stream.Dispose() } catch {}
        }
        if ($null -ne $context) {
            Close-M1PreflightContext -Context $context
        }
    }
}
