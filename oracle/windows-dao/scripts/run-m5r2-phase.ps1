[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleRoot,
    [Parameter(Mandatory = $true)][string]$InvocationPath,
    [Parameter(Mandatory = $true)][string]$M4BundleRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$held = New-Object Collections.ArrayList
$invocation = $null
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop") {
        throw "M5 requires a fresh x86 Windows PowerShell Desktop worker."
    }
    $repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../../.."))
    $bundle = [IO.Path]::GetFullPath($BundleRoot)
    $m4Bundle = [IO.Path]::GetFullPath($M4BundleRoot)
    $invocationFile = [IO.Path]::GetFullPath($InvocationPath)
    if (-not $invocationFile.StartsWith(
        $bundle.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase
    )) { throw "M5 invocation escapes its private bundle root." }

    $m1 = Join-Path $repository "oracle/windows-dao/scripts/m1"
    . (Join-Path $m1 "M1.Preflight.ps1")
    . (Join-Path $m1 "M1.DaoValues.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m4/M4.Dao.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Bundle.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Worker.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Dao.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Artifacts.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")

    $invocationInput = Read-M5HeldFile -Path $invocationFile `
        -MaximumBytes 1MB -Label "M5 invocation"
    [void]$held.Add($invocationInput.Stream)
    $invocation = ConvertFrom-M5HeldJson -InputFile $invocationInput `
        -Label "M5 invocation"
    if ([string]$invocation.experiment_id -cne $script:M5ExperimentId -or
        [string]$invocation.repository_url -cne $script:M5RepositoryUrl -or
        [string]$invocation.remote_ref -cne $script:M5RemoteRef -or
        [string]$invocation.producer_commit -cnotmatch "^[0-9a-f]{40}$" -or
        -not [bool]$invocation.bindings_verified_before_com) {
        throw "M5 invocation identity or binding status is invalid."
    }
    if (-not ([IO.Path]::GetFullPath(
        [string]$invocation.repository_root
    )).Equals($repository, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFullPath(
        [string]$invocation.stage_root
    )).Equals($bundle, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Directory]::Exists($m4Bundle)) {
        throw "M5 worker root bindings differ from its invocation."
    }

    $git = [IO.Path]::GetFullPath((Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1).Source)
    $head = @(& $git -C $repository rev-parse --verify HEAD 2>&1)
    $dirty = @(& $git -C $repository status --porcelain=v1 `
        --untracked-files=all 2>&1)
    if ($LASTEXITCODE -ne 0 -or $head.Count -ne 1 -or
        [string]$head[0] -cne [string]$invocation.producer_commit -or
        $dirty.Count -ne 0) {
        throw "M5 worker requires the exact clean producer commit."
    }

    $validator = Join-Path $repository "oracle/windows-dao/scripts/m5_contract.py"
    $python = Get-M1Python3
    $pythonCommand = [string]$python.Command
    $pythonPrefix = @($python.Prefix)
    $validation = @(& $pythonCommand @pythonPrefix -B $validator `
        "validate-invocation" "--bundle-root" $bundle `
        "--invocation" $invocationFile `
        "--m4-bundle-root" $m4Bundle 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "M5 checked invocation validation failed: $($validation -join ' ')"
    }

    $planPath = Resolve-M5Locator -Root $bundle `
        -Locator ([string]$invocation.plan_path) -Label "plan_path"
    $environmentPath = Resolve-M5Locator -Root $bundle `
        -Locator ([string]$invocation.environment_path) `
        -Label "environment_path"
    $planInput = Read-M5HeldFile -Path $planPath -MaximumBytes 1MB `
        -Label "M5 plan"
    $environmentInput = Read-M5HeldFile -Path $environmentPath `
        -MaximumBytes 1MB -Label "M5 environment"
    [void]$held.Add($planInput.Stream)
    [void]$held.Add($environmentInput.Stream)
    if ($planInput.Sha256 -cne [string]$invocation.plan_sha256 -or
        $environmentInput.Sha256 -cne
            [string]$invocation.environment_sha256) {
        throw "M5 plan or environment differs from its invocation binding."
    }
    $plan = ConvertFrom-M5HeldJson -InputFile $planInput -Label "M5 plan"
    $environment = ConvertFrom-M5HeldJson `
        -InputFile $environmentInput -Label "M5 environment"
    Assert-M5PlanProjection -Invocation $invocation -Plan $plan

    $m4ManifestPath = Join-Path $m4Bundle "bundle-manifest.json"
    $m4Input = Read-M5HeldFile -Path $m4ManifestPath -MaximumBytes 1MB `
        -Label "M4 bundle manifest"
    [void]$held.Add($m4Input.Stream)
    if ($m4Input.Sha256 -cne $script:M5ExpectedM4ManifestSha256 -or
        $m4Input.Sha256 -cne
            [string]$invocation.m4_input.bundle_manifest_sha256) {
        throw "M5 worker M4 manifest differs from the exact validated input."
    }
    $m4Manifest = ConvertFrom-M5HeldJson -InputFile $m4Input `
        -Label "M4 bundle manifest"
    if ([string]$m4Manifest.producer_commit -cne
            [string]$invocation.m4_input.producer_commit -or
        [string]$m4Manifest.run_id -cne
            [string]$invocation.m4_input.campaign_run_id -or
        -not [bool]$invocation.m4_input.validated_before_com) {
        throw "M5 worker M4 identity binding drifted."
    }

    Assert-M1GitState -GitPath $git -Repository $repository `
        -Commit ([string]$invocation.producer_commit)
    [Environment]::SetEnvironmentVariable("GIT_TERMINAL_PROMPT", "0")
    [Environment]::SetEnvironmentVariable("GCM_INTERACTIVE", "Never")
    $remote = Invoke-BoundedChildProcess -Executable $git `
        -Arguments @(
            "-C", $repository, "-c", "credential.interactive=never",
            "ls-remote", "--heads", $script:M5RepositoryUrl,
            $script:M5RemoteRef
        ) -CallerLabel "M5 worker remote binding" `
        -TimeoutSeconds 30 -MaximumOutputBytes 16KB
    $remoteLines = @([string]$remote.stdout -split "\r?\n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($remoteLines.Count -ne 1 -or
        -not $remoteLines[0].StartsWith(
            [string]$invocation.producer_commit + "`t",
            [StringComparison]::Ordinal
        )) { throw "M5 worker could not prove the exact pushed commit." }

    $acceptedProvider = Assert-M1ProviderEnvironment `
        -Environment $environment
    $providerPath = Assert-M1CurrentRegistration `
        -AcceptedProvider $acceptedProvider
    $providerStream = New-Object IO.FileStream(
        $providerPath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan
    )
    [void]$held.Add($providerStream)
    $providerHash = Get-M1StreamSha256 -Stream $providerStream
    if ($providerHash -cne [string]$invocation.provider_sha256 -or
        $providerHash -cne [string]$acceptedProvider.server_sha256) {
        throw "M5 DAO provider bytes differ from the exact binding."
    }

    $databasePaths = Get-M5WorkerPaths -Invocation $invocation `
        -BundleRoot $bundle
    $cloneStream = Assert-M5CloneBindings -Invocation $invocation `
        -DatabasePaths $databasePaths -BundleRoot $bundle `
        -MaximumBytes ([long]$plan.bounds.max_database_bytes)
    if ($null -ne $cloneStream) { [void]$held.Add($cloneStream) }
    foreach ($pathProperty in $databasePaths.PSObject.Properties) {
        Assert-M4LockFileAbsent -DatabasePath ([string]$pathProperty.Value)
    }
    if ([string]$invocation.phase_id -ceq "source" -and
        (Test-Path -LiteralPath $databasePaths.source_database)) {
        throw "M5 source database path must be create-new."
    }
    if ([string]$invocation.phase_id -ceq "compact" -and
        (Test-Path -LiteralPath $databasePaths.compacted_database)) {
        throw "M5 compact destination must be create-new."
    }

    $preCompactInput = $null
    if ([string]$invocation.phase_id -ceq "compact") {
        $preCompactInput = Get-M4ClosedFileObservation `
            -DatabasePath $databasePaths.compact_input_database `
            -MaximumBytes ([long]$plan.bounds.max_database_bytes)
    }
    $operations = New-Object Collections.ArrayList
    Add-M4OperationEntry -Entries $operations -Action "bindings_verified"
    if ([string]$invocation.phase_id -cne "source") {
        Add-M4OperationEntry -Entries $operations -Action "clone_verified"
    }
    $startedAt = ([Diagnostics.Process]::GetCurrentProcess()).StartTime.`
        ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
    $snapshotObservation = Invoke-M5DaoPhase `
        -PhaseId ([string]$invocation.phase_id) `
        -Invocation $invocation -DatabasePaths $databasePaths `
        -AcceptedProvider $acceptedProvider `
        -OperationEntries $operations

    $paths = Get-M5PhasePaths -SampleId $invocation.sample_id `
        -PhaseId $invocation.phase_id
    $observations = New-Object Collections.ArrayList
    $prefixObservation = $null
    switch ([string]$invocation.phase_id) {
        "source" {
            $prefixObservation = Get-M4ClosedFileObservation `
                -DatabasePath $databasePaths.source_database `
                -MaximumBytes ([long]$plan.bounds.max_database_bytes)
            [void]$observations.Add((ConvertTo-M5DatabaseObservation `
                -Role "source_database" `
                -Locator ([string]$invocation.database_paths.source_database) `
                -Observation $prefixObservation `
                -PrefixLocator $paths.prefix))
        }
        "compact" {
            $postInput = Get-M4ClosedFileObservation `
                -DatabasePath $databasePaths.compact_input_database `
                -MaximumBytes ([long]$plan.bounds.max_database_bytes)
            if ($postInput.bytes -ne $preCompactInput.bytes -or
                $postInput.sha256 -cne $preCompactInput.sha256) {
                throw "M5 CompactDatabase changed its closed input."
            }
            [void]$observations.Add((ConvertTo-M5DatabaseObservation `
                -Role "compact_input_database" `
                -Locator ([string]$invocation.database_paths.compact_input_database) `
                -Observation $postInput -PrefixLocator $null))
            $prefixObservation = Get-M4ClosedFileObservation `
                -DatabasePath $databasePaths.compacted_database `
                -MaximumBytes ([long]$plan.bounds.max_database_bytes)
            [void]$observations.Add((ConvertTo-M5DatabaseObservation `
                -Role "compacted_database" `
                -Locator ([string]$invocation.database_paths.compacted_database) `
                -Observation $prefixObservation `
                -PrefixLocator $paths.prefix))
        }
        "verify" {
            $prefixObservation = Get-M4ClosedFileObservation `
                -DatabasePath $databasePaths.verify_database `
                -MaximumBytes ([long]$plan.bounds.max_database_bytes)
            [void]$observations.Add((ConvertTo-M5DatabaseObservation `
                -Role "verify_database" `
                -Locator ([string]$invocation.database_paths.verify_database) `
                -Observation $prefixObservation `
                -PrefixLocator $paths.prefix))
        }
    }
    Add-M4OperationEntry -Entries $operations -Action "prefix_observed"
    $logBytes = ConvertTo-M5JsonBytes `
        -Document (New-M5OperationLog -Invocation $invocation `
            -Entries $operations -StartedAt $startedAt) -MaximumBytes 64KB
    $snapshotBytes = $null
    if ($null -ne $snapshotObservation) {
        $snapshotBytes = ConvertTo-M5JsonBytes `
            -Document (New-M5Snapshot -Invocation $invocation `
                -Observation $snapshotObservation) -MaximumBytes 64KB
    }
    Write-M4CreateNewBytes -Path (Resolve-M5Locator -Root $bundle `
        -Locator $paths.prefix -Label "prefix") `
        -Bytes $prefixObservation.prefix -MaximumBytes 2048
    Write-M4CreateNewBytes -Path (Resolve-M5Locator -Root $bundle `
        -Locator $paths.operation_log -Label "operation log") `
        -Bytes $logBytes -MaximumBytes 64KB
    if ($null -ne $snapshotBytes) {
        Write-M4CreateNewBytes -Path (Resolve-M5Locator -Root $bundle `
            -Locator $paths.snapshot -Label "snapshot") `
            -Bytes $snapshotBytes -MaximumBytes 64KB
    }

    foreach ($stream in $held) { $stream.Dispose() }
    $held.Clear()
    $result = New-M5WorkerResult -Invocation $invocation `
        -Provider $acceptedProvider -ProviderHash $providerHash `
        -StartedAt $startedAt -InvocationSha256 $invocationInput.Sha256 `
        -Paths $paths -OperationLogBytes $logBytes `
        -SnapshotBytes $snapshotBytes `
        -DatabaseObservations @($observations)
    Write-M4CreateNewBytes -Path (Resolve-M5Locator -Root $bundle `
        -Locator ([string]$invocation.result_path) -Label "result") `
        -Bytes (ConvertTo-M5JsonBytes -Document $result -MaximumBytes 64KB) `
        -MaximumBytes 64KB
    exit 0
}
catch {
    $primary = $_
    $cleanup = New-Object Collections.ArrayList
    foreach ($stream in $held) {
        try { $stream.Dispose() }
        catch { [void]$cleanup.Add([string]$_.Exception.Message) }
    }
    if (Get-Command Complete-M5WorkerFailure -ErrorAction SilentlyContinue) {
        Complete-M5WorkerFailure -PrimaryError $primary `
            -Invocation $invocation -CleanupErrors @($cleanup)
    }
    else {
        [Console]::Error.WriteLine(
            "M5 worker failure: " + [string]$primary.Exception.Message
        )
    }
    exit 1
}
