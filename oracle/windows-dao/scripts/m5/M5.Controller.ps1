Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Wait-M5DirectoryMetadataBarrier {
    param([Parameter(Mandatory = $true)][string]$Root)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    $previous = $null
    $stable = 0
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $rows = New-Object Collections.ArrayList
        $pending = New-Object 'Collections.Generic.Queue[string]'
        $pending.Enqueue([IO.Path]::GetFullPath($Root))
        while ($pending.Count -gt 0) {
            $path = $pending.Dequeue()
            $item = Get-Item -LiteralPath $path -Force
            if (-not $item.PSIsContainer -or
                ($item.Attributes -band
                    [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "M5 directory barrier encountered a replacement."
            }
            [void]$rows.Add("{0}|{1}|{2}|{3}" -f @(
                $item.FullName, [long]$item.CreationTimeUtc.Ticks,
                [long]$item.LastWriteTimeUtc.Ticks, [long]$item.Attributes
            ))
            if ($rows.Count -gt 512) {
                throw "M5 directory barrier exceeded its topology bound."
            }
            foreach ($child in [IO.Directory]::GetDirectories($path)) {
                $pending.Enqueue([IO.Path]::GetFullPath($child))
            }
        }
        $current = (@($rows | Sort-Object) -join "`n")
        if ($null -ne $previous -and $current -ceq $previous) {
            $stable += 1
            if ($stable -ge 3) { return }
        }
        else { $stable = 0 }
        $previous = $current
        Start-Sleep -Milliseconds 1000
    }
    throw "M5 directory metadata did not reach the exact publication barrier."
}

function Assert-M5RuntimeGate {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][string]$Repository
    )
    if ([string]$Plan.execution_gate.status -cne "BLOCKED") {
        throw "M5 preregistration gate history was unexpectedly rewritten."
    }
    $required = (
        "windows_dao_host_bound_to_the_exact_clean_pushed_producer_commit"
    )
    if (@($Plan.execution_gate.blocking_requirements).Count -ne 1 -or
        [string]$Plan.execution_gate.blocking_requirements[0] -cne $required) {
        throw "M5 preregistration blocking-requirement set drifted."
    }
    foreach ($relative in @(
        "oracle/windows-dao/scripts/run-m5r2-controlled.ps1",
        "oracle/windows-dao/scripts/run-m5r2-phase.ps1",
        "oracle/windows-dao/scripts/m5_contract.py",
        "oracle/windows-dao/scripts/m5_analysis.py"
    )) {
        $path = Join-Path $Repository $relative
        if (-not [IO.File]::Exists($path)) {
            throw "M5 runtime requirement is not implemented: $relative"
        }
        Assert-M1NoReparseComponents -Path $path
    }
}

function New-M5CloneLog {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][string]$CloneId,
        [Parameter(Mandatory = $true)][pscustomobject]$Clone
    )
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_clone_log"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Sample.sample_id
        clone_id = $CloneId
        started_at_utc = [string]$Clone.started_at_utc
        completed_at_utc = [string]$Clone.completed_at_utc
        source_path = [string]$Clone.source_path
        destination_path = [string]$Clone.destination_path
        source_bytes = [long]$Clone.source_bytes
        destination_bytes = [long]$Clone.destination_bytes
        source_sha256_before_clone = [string]$Clone.source_sha256_before_clone
        source_sha256_after_clone = [string]$Clone.source_sha256_after_clone
        destination_sha256 = [string]$Clone.destination_sha256
        source_file_identity = $Clone.source_file_identity
        destination_file_identity = $Clone.destination_file_identity
        all_hashes_equal = [bool]$Clone.all_hashes_equal
        exact_byte_clone = [bool]$Clone.exact_byte_clone
        source_unchanged_after_clone = $true
        no_hardlink = [bool]$Clone.no_hardlink
        same_volume = [bool]$Clone.same_volume
        distinct_file_identity = [bool]$Clone.distinct_file_identity
        status = "pass"
    }
}

function Invoke-M5ControllerClone {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][string]$CloneId,
        [Parameter(Mandatory = $true)][string]$SourceLocator,
        [Parameter(Mandatory = $true)][string]$DestinationLocator,
        [Parameter(Mandatory = $true)][string]$LogLocator
    )
    $clone = Invoke-M4BoundedClone -ControllerRoot $Session.StagingBundle `
        -SourcePath (Get-M1PayloadPath -Session $Session `
            -RelativePath $SourceLocator) `
        -DestinationPath (Get-M1PayloadPath -Session $Session `
            -RelativePath $DestinationLocator) `
        -MaximumBytes ([long]$Plan.bounds.max_database_bytes)
    $log = New-M5CloneLog -Plan $Plan -Sample $Sample `
        -CloneId $CloneId -Clone $clone
    Write-M5BundleJson -Session $Session -Entries $Entries `
        -RelativePath $LogLocator -Role "clone_log" -Document $log `
        -MaximumBytes 64KB
    return [pscustomobject]@{
        observation = $clone
        artifact = Get-M5ArtifactBinding -Session $Session `
            -RelativePath $LogLocator
        invocation_binding = [pscustomobject][ordered]@{
            destination_bytes = [long]$clone.destination_bytes
            destination_sha256 = [string]$clone.destination_sha256
            clone_log = Get-M5ArtifactBinding -Session $Session `
                -RelativePath $LogLocator
        }
    }
}

function New-M5SampleRecord {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)][pscustomobject]$Source,
        [Parameter(Mandatory = $true)][pscustomobject]$Compact,
        [Parameter(Mandatory = $true)][pscustomobject]$Verify,
        [Parameter(Mandatory = $true)][pscustomobject]$SourceClone,
        [Parameter(Mandatory = $true)][pscustomobject]$VerifyClone,
        [Parameter(Mandatory = $true)][hashtable]$Quiescence,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][pscustomobject]$Context
    )
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_sample_record"
        experiment_id = $script:M5ExperimentId
        plan_sha256 = $PlanSha256
        producer_commit = [string]$Session.GitCommit
        environment_sha256 = [string]$Context.EnvironmentSha256
        provider_sha256 = [string]$Context.ProviderSha256
        m4_manifest_sha256 = $script:M5ExpectedM4ManifestSha256
        sample_id = [string]$Sample.sample_id
        condition_id = [string]$Sample.condition_id
        replica = [int]$Sample.replica
        block = [int]$Sample.block
        position_in_block = [int]$Sample.position_in_block
        launch_ordinal = [int]$Sample.launch_ordinal
        phases = [ordered]@{
            source = [ordered]@{
                worker_result = Get-M5ArtifactBinding -Session $Session `
                    -RelativePath $Source.paths.result
                status = "pass"
            }
            compact = [ordered]@{
                worker_result = Get-M5ArtifactBinding -Session $Session `
                    -RelativePath $Compact.paths.result
                status = "pass"
            }
            verify = [ordered]@{
                worker_result = Get-M5ArtifactBinding -Session $Session `
                    -RelativePath $Verify.paths.result
                status = "pass"
            }
        }
        controller_clones = @(
            $SourceClone.artifact
            $VerifyClone.artifact
        )
        post_worker_quiescence = [ordered]@{
            source_database = $Quiescence.source_database.artifact
            compact_input_database = $Quiescence.compact_input_database.artifact
            compacted_database = $Quiescence.compacted_database.artifact
            verify_database = $Quiescence.verify_database.artifact
        }
        execution_status = "pass"
    }
}

function Write-M5Analysis {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$M4BundleRoot
    )
    $scratch = Join-Path $Session.WorkingPath "m5-analysis.json"
    Invoke-M5Contract -Context $Context -ContractPath $ContractPath `
        -Arguments @(
            "build-analysis", "--bundle-root", $Session.StagingBundle,
            "--m4-bundle-root", $M4BundleRoot, "--output", $scratch
        ) -Label "M5 checked bounded analysis"
    $input = Read-M5Json -Path $scratch -MaximumBytes 16MB
    Write-M1DurableBytes -Session $Session `
        -RelativePath "analysis.json" -Bytes $input.bytes
    Add-M5ManifestEntry -Entries $Entries -Session $Session `
        -RelativePath "analysis.json" -Role "analysis_report"
    [IO.File]::Delete($scratch)
}

function Invoke-M5Campaign {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$M4BundleRoot,
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$GitCommit,
        [Parameter(Mandatory = $true)][string]$RunId,
        [AllowNull()][scriptblock]$FaultInjector
    )
    if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-m5-[a-z0-9-]{1,24}$") {
        throw "M5 RunId does not match the controlled format."
    }
    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    $planPath = Join-Path $repository (
        "oracle/windows-dao/experiments/m5/m5-compact-confirm-r4.plan.json"
    )
    $contractPath = Join-Path $repository `
        "oracle/windows-dao/scripts/m5_contract.py"
    $m4ContractPath = Join-Path $repository `
        "oracle/windows-dao/scripts/m4r1_contract.py"
    $workerPath = Join-Path $repository `
        "oracle/windows-dao/scripts/run-m5r2-phase.ps1"
    $executed = @(
        "oracle/windows-dao/scripts/run-m5r2-controlled.ps1",
        "oracle/windows-dao/scripts/run-m5r2-phase.ps1",
        "oracle/windows-dao/scripts/m5/M5.Bundle.ps1",
        "oracle/windows-dao/scripts/m5/M5.Controller.ps1",
        "oracle/windows-dao/scripts/m5/M5.ControllerRuntime.ps1",
        "oracle/windows-dao/scripts/m5/M5.Quiescence.ps1",
        "oracle/windows-dao/scripts/m5/M5.Worker.ps1",
        "oracle/windows-dao/scripts/m5/M5.Dao.ps1",
        "oracle/windows-dao/scripts/m5/M5.Artifacts.ps1",
        "oracle/windows-dao/scripts/m5_contract.py",
        "oracle/windows-dao/scripts/m5_analysis.py",
        "oracle/windows-dao/scripts/m5_bundle.py",
        "oracle/windows-dao/scripts/m5_phase.py",
        "oracle/windows-dao/scripts/m5_records.py",
        "oracle/windows-dao/scripts/m5_snapshot.py",
        "oracle/windows-dao/scripts/m5_spec.py",
        "oracle/windows-dao/experiments/m5r3/plan.schema.json",
        "oracle/windows-dao/experiments/m5r3/invocation.schema.json",
        "oracle/windows-dao/experiments/m5r3/worker-result.schema.json",
        "oracle/windows-dao/experiments/m5r3/operation-log.schema.json",
        "oracle/windows-dao/experiments/m5r3/snapshot.schema.json",
        "oracle/windows-dao/experiments/m5r3/clone-log.schema.json",
        "oracle/windows-dao/experiments/m5r3/post-worker-quiescence.schema.json",
        "oracle/windows-dao/experiments/m5r3/sample-record.schema.json",
        "oracle/windows-dao/experiments/m5r3/analysis-report.schema.json",
        "oracle/windows-dao/experiments/m5r3/bundle-manifest.schema.json",
        "oracle/windows-dao/scripts/m4r1_contract.py",
        "oracle/windows-dao/scripts/m4r1_bundle.py",
        "oracle/windows-dao/scripts/m4r1_campaign.py",
        "oracle/windows-dao/scripts/m4r1_phase.py",
        "oracle/windows-dao/scripts/m4r1_records.py",
        "oracle/windows-dao/scripts/m4r1_snapshot.py",
        "oracle/windows-dao/scripts/m4r1_analysis.py",
        "oracle/windows-dao/scripts/m4r1_spec.py",
        "oracle/windows-dao/scripts/m1_bundle_validation.py",
        "oracle/windows-dao/scripts/protocol_validation.py",
        "oracle/windows-dao/scripts/m4/M4.Clone.ps1",
        "oracle/windows-dao/scripts/m4/M4.Dao.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
        "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
        "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
        "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
        "oracle/windows-dao/scripts/m1/M1.Dao.ps1",
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1",
        "oracle/windows-dao/scripts/protocol_cli.py",
        "oracle/windows-dao/scripts/validate_m1_protocol.py",
        "oracle/windows-dao/protocol/v1_1/bundle-manifest.schema.json",
        "oracle/windows-dao/protocol/v1_1/canonical-snapshot.schema.json",
        "oracle/windows-dao/protocol/v1_1/environment.schema.json",
        "oracle/windows-dao/protocol/v1_1/evidence-report.schema.json",
        "oracle/windows-dao/protocol/v1_1/example-inventory.schema.json",
        "oracle/windows-dao/protocol/v1_1/operation-log.schema.json",
        "oracle/windows-dao/protocol/v1_1/pair.schema.json",
        "oracle/windows-dao/protocol/v1_1/scenario.schema.json",
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
        "oracle/windows-dao/experiments/m5/m5-compact-confirm-r4.plan.json"
    )
    $context = $null
    $session = $null
    $workerPowerShell = $null
    try {
        $context = Invoke-M1Preflight -RepositoryRoot $repository `
            -EnvironmentPath $EnvironmentPath -OutputRoot $OutputRoot `
            -GitCommit $GitCommit -RunId $RunId `
            -ExecutedRepoRelativeSourcePaths $executed
        if ([string]$context.Environment.host.process_architecture -cne "x86" -or
            [string]$context.AcceptedProvider.prog_id -cne "DAO.DBEngine.36") {
            throw "M5 requires the checked licensed x86 DAO.DBEngine.36 host."
        }
        $planInput = Read-M5Json -Path $planPath -MaximumBytes 1MB
        $plan = $planInput.document
        $planSha = Get-M1ByteArraySha256 -Bytes $planInput.bytes
        Invoke-M5Contract -Context $context -ContractPath $contractPath `
            -Arguments @("validate-plan", $planPath) `
            -Label "M5 checked immutable plan validation"
        Assert-M5RuntimeGate -Plan $plan -Repository $repository
        Assert-M5ExactRemoteCommit -Context $context -Plan $plan
        [void](Assert-M1RuntimeBinding -Context $context)
        $m4 = Assert-M5M4BundleReadOnly -Context $context `
            -M4BundleRoot $M4BundleRoot `
            -M4ContractPath $m4ContractPath
        $workerPowerShell = Get-M5WorkerPowerShell

        $session = New-M1PublicationSession -RepositoryRoot $repository `
            -OutputRoot $OutputRoot -GitCommit $GitCommit -RunId $RunId `
            -MaxFileBytes 16MB -MaxTotalBytes 640MB `
            -FaultInjector $FaultInjector
        $entries = New-Object Collections.ArrayList
        Write-M1DurableBytes -Session $session `
            -RelativePath "plan.json" -Bytes $planInput.bytes
        Add-M5ManifestEntry -Entries $entries -Session $session `
            -RelativePath "plan.json" -Role "plan"
        Write-M1DurableBytes -Session $session `
            -RelativePath "environment.json" `
            -Bytes $context.EnvironmentBytes
        Add-M5ManifestEntry -Entries $entries -Session $session `
            -RelativePath "environment.json" -Role "environment"

        $completed = 0
        foreach ($sample in $plan.samples) {
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-M5ExactRemoteCommit -Context $context -Plan $plan
            $matches = @($plan.conditions | Where-Object {
                $_.condition_id -ceq $sample.condition_id
            })
            if ($matches.Count -ne 1) {
                throw "M5 sample condition projection is not unique."
            }
            $condition = $matches[0]
            $baseOrdinal = 3 * [int]$sample.launch_ordinal
            $source = Invoke-M5CheckedPhase -Context $context `
                -Session $session -Entries $entries -Plan $plan `
                -Sample $sample -Condition $condition -PhaseId "source" `
                -WorkerOrdinal ($baseOrdinal - 2) -PlanSha256 $planSha `
                -M4Binding $m4 -ContractPath $contractPath `
                -WorkerPath $workerPath -PowerShellBinding $workerPowerShell
            $quiescence = @{}
            $quiescence.source_database = Add-M5Quiescence `
                -Context $context -Session $session -Entries $entries `
                -Phase $source -DatabaseRole "source_database" -Plan $plan `
                -ContractPath $contractPath

            $root = "evidence/samples/$($sample.sample_id)"
            $sourceClone = Invoke-M5ControllerClone -Session $session `
                -Entries $entries -Plan $plan -Sample $sample `
                -CloneId "source_to_compact_input" `
                -SourceLocator ([string]$sample.source_database_path) `
                -DestinationLocator ([string]$sample.compact_input_database_path) `
                -LogLocator "$root/source-to-compact-input-clone.json"
            $compact = Invoke-M5CheckedPhase -Context $context `
                -Session $session -Entries $entries -Plan $plan `
                -Sample $sample -Condition $condition -PhaseId "compact" `
                -WorkerOrdinal ($baseOrdinal - 1) -PlanSha256 $planSha `
                -M4Binding $m4 -ContractPath $contractPath `
                -WorkerPath $workerPath -PowerShellBinding $workerPowerShell `
                -PriorResult $source.result
            foreach ($role in @("compact_input_database", "compacted_database")) {
                $quiescence[$role] = Add-M5Quiescence `
                    -Context $context -Session $session -Entries $entries `
                    -Phase $compact -DatabaseRole $role -Plan $plan `
                    -ContractPath $contractPath
            }

            $verifyClone = Invoke-M5ControllerClone -Session $session `
                -Entries $entries -Plan $plan -Sample $sample `
                -CloneId "compacted_to_verify_input" `
                -SourceLocator ([string]$sample.compacted_database_path) `
                -DestinationLocator ([string]$sample.verify_database_path) `
                -LogLocator "$root/compacted-to-verify-input-clone.json"
            $verify = Invoke-M5CheckedPhase -Context $context `
                -Session $session -Entries $entries -Plan $plan `
                -Sample $sample -Condition $condition -PhaseId "verify" `
                -WorkerOrdinal $baseOrdinal -PlanSha256 $planSha `
                -M4Binding $m4 -ContractPath $contractPath `
                -WorkerPath $workerPath -PowerShellBinding $workerPowerShell `
                -PriorResult $compact.result
            $quiescence.verify_database = Add-M5Quiescence `
                -Context $context -Session $session -Entries $entries `
                -Phase $verify -DatabaseRole "verify_database" -Plan $plan `
                -ContractPath $contractPath
            Assert-M5NoUnexpectedCompanions -Session $session -Sample $sample

            $record = New-M5SampleRecord -Session $session -Plan $plan `
                -Sample $sample -Condition $condition -Source $source `
                -Compact $compact -Verify $verify `
                -SourceClone $sourceClone -VerifyClone $verifyClone `
                -Quiescence $quiescence -PlanSha256 $planSha -Context $context
            Write-M5BundleJson -Session $session -Entries $entries `
                -RelativePath ([string]$sample.record_path) `
                -Role "sample_record" -Document $record `
                -MaximumBytes ([long]$plan.bounds.max_sample_record_bytes)
            Invoke-M5Contract -Context $context -ContractPath $contractPath `
                -Arguments @(
                    "validate-sample", "--bundle-root", $session.StagingBundle,
                    "--record", (Get-M1PayloadPath -Session $session `
                        -RelativePath ([string]$sample.record_path))
                ) -Label "M5 complete sample validation"
            $completed += 1
        }
        if ($completed -ne 108 -or $entries.Count -lt 2702 -or
            $entries.Count -gt 3134) {
            throw "M5 retained sample topology is incomplete before analysis."
        }
        Write-M5Analysis -Context $context -Session $session `
            -Entries $entries -ContractPath $contractPath `
            -M4BundleRoot $m4.Root
        Write-M5Manifest -Session $session -Entries $entries
        Wait-M5DirectoryMetadataBarrier -Root $session.StagingBundle

        $validate = {
            param($bundle)
            Invoke-M5Contract -Context $context -ContractPath $contractPath `
                -Arguments @(
                    "validate-bundle", $bundle,
                    "--m4-bundle-root", $m4.Root
                ) -Label "M5 exact staged bundle validation"
        }
        $recheck = {
            param($stage)
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-M5ExactRemoteCommit -Context $context -Plan $plan
            [void](Assert-M5M4BundleReadOnly -Context $context `
                -M4BundleRoot $m4.Root -M4ContractPath $m4ContractPath)
            Invoke-M5Contract -Context $context -ContractPath $contractPath `
                -Arguments @(
                    "validate-bundle", $stage.StagingBundle,
                    "--m4-bundle-root", $m4.Root
                ) -Label "M5 pre-publication evidence recheck"
            return $true
        }
        Publish-M1Stage -Stage $session -RecheckScriptBlock $recheck `
            -ValidationScriptBlock $validate
        $session = $null
        return Join-Path (Join-Path $OutputRoot $GitCommit) $RunId
    }
    catch {
        $original = $_
        if ($null -ne $session) {
            try { Remove-M1PublicationStaging -Session $session }
            catch { }
        }
        throw $original
    }
    finally {
        if ($null -ne $workerPowerShell) {
            try { $workerPowerShell.Stream.Dispose() } catch { }
        }
        if ($null -ne $context) { Close-M1PreflightContext -Context $context }
    }
}
