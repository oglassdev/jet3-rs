[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleRoot,
    [Parameter(Mandatory = $true)][string]$InvocationPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:M4MaximumInputBytes = 1MB
$script:M4ExperimentId = "DAO-M4-HEADER-DISCRIMINATOR-001"
$script:M4RepositoryUrl = "https://github.com/oglassdev/jet3-rs.git"
$script:M4RemoteRef = "refs/heads/codex/jet3-v1-foundations"
$script:M4ExecutedSources = @(
    "oracle/windows-dao/scripts/run-m4-phase.ps1", "oracle/windows-dao/scripts/m4/M4.Dao.ps1",
    "oracle/windows-dao/scripts/m4/M4.Worker.ps1", "oracle/windows-dao/scripts/m4/M4.Artifacts.ps1",
    "oracle/windows-dao/scripts/m4_contract.py", "oracle/windows-dao/scripts/m4_records.py",
    "oracle/windows-dao/scripts/m4_bundle.py", "oracle/windows-dao/scripts/m4_analysis.py",
    "oracle/windows-dao/scripts/m4_campaign.py",
    "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
    "oracle/windows-dao/scripts/m1_bundle_validation.py", "oracle/windows-dao/scripts/protocol_validation.py",
    "oracle/windows-dao/scripts/protocol_cli.py", "oracle/windows-dao/scripts/validate_m1_protocol.py",
    "oracle/windows-dao/scripts/m1/M1.Preflight.ps1", "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
    "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1", "oracle/windows-dao/experiments/m4/m4-header-discriminator.plan.json",
    "oracle/windows-dao/experiments/m4/plan.schema.json", "oracle/windows-dao/experiments/m4/invocation.schema.json",
    "oracle/windows-dao/experiments/m4/worker-result.schema.json", "oracle/windows-dao/experiments/m4/operation-log.schema.json",
    "oracle/windows-dao/experiments/m4/snapshot.schema.json", "oracle/windows-dao/experiments/m4/clone-log.schema.json",
    "oracle/windows-dao/experiments/m4/sample-record.schema.json", "oracle/windows-dao/experiments/m4/analysis-report.schema.json",
    "oracle/windows-dao/experiments/m4/bundle-manifest.schema.json", "oracle/windows-dao/protocol/v1_1/bundle-manifest.schema.json",
    "oracle/windows-dao/protocol/v1_1/canonical-snapshot.schema.json", "oracle/windows-dao/protocol/v1_1/environment.schema.json",
    "oracle/windows-dao/protocol/v1_1/evidence-report.schema.json", "oracle/windows-dao/protocol/v1_1/example-inventory.schema.json",
    "oracle/windows-dao/protocol/v1_1/operation-log.schema.json", "oracle/windows-dao/protocol/v1_1/pair.schema.json",
    "oracle/windows-dao/protocol/v1_1/scenario.schema.json"
)
$invocationInput = $null
$planInput = $null
$environmentInput = $null
$cloneInput = $null
$providerStream = $null
$gitExecutableStream = $null
$bootstrapStream = $null
$invocation = $null
$bindingsClosed = $false
$failureTombstonePath = $null
$resultCommitted = $false
try {
    if (
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop"
    ) {
        throw "M4 requires a fresh x86 Windows PowerShell Desktop worker."
    }
    $sourceRepository = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "../../..")
    )
    $bootstrapBundle = [IO.Path]::GetFullPath($BundleRoot)
    $bootstrapInvocationPath = [IO.Path]::GetFullPath($InvocationPath)
    $bootstrapPrefix = $bootstrapBundle.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $bootstrapInvocationPath.StartsWith(
        $bootstrapPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "M4 bootstrap invocation escapes BundleRoot."
    }
    $bootstrapStream = New-Object IO.FileStream(
        $bootstrapInvocationPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    if (
        $bootstrapStream.Length -lt 1 -or
        $bootstrapStream.Length -gt $script:M4MaximumInputBytes
    ) {
        throw "M4 bootstrap invocation violates its byte bound."
    }
    $bootstrapBytes = New-Object byte[] ([int]$bootstrapStream.Length)
    $bootstrapOffset = 0
    while ($bootstrapOffset -lt $bootstrapBytes.Length) {
        $bootstrapRead = $bootstrapStream.Read(
            $bootstrapBytes,
            $bootstrapOffset,
            $bootstrapBytes.Length - $bootstrapOffset
        )
        if ($bootstrapRead -le 0) {
            throw "M4 bootstrap invocation ended during its bounded read."
        }
        $bootstrapOffset += $bootstrapRead
    }
    $bootstrapStream.Position = 0
    if (
        $bootstrapBytes.Length -ge 3 -and
        $bootstrapBytes[0] -eq 0xef -and
        $bootstrapBytes[1] -eq 0xbb -and
        $bootstrapBytes[2] -eq 0xbf
    ) {
        throw "M4 bootstrap invocation contains a forbidden UTF-8 BOM."
    }
    $bootstrapText = (
        New-Object Text.UTF8Encoding($false, $true)
    ).GetString($bootstrapBytes)
    $bootstrapInvocation = $bootstrapText | ConvertFrom-Json
    $bootstrapCommit = [string]$bootstrapInvocation.producer_commit
    if ($bootstrapCommit -cnotmatch "^[0-9a-f]{40}$") {
        throw "M4 bootstrap producer_commit is invalid."
    }
    if (
        -not ([IO.Path]::GetFullPath(
            [string]$bootstrapInvocation.repository_root
        )).Equals(
            $sourceRepository,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not ([IO.Path]::GetFullPath(
            [string]$bootstrapInvocation.stage_root
        )).Equals(
            $bootstrapBundle,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "M4 bootstrap root bindings differ."
    }
    $bootstrapGitCommand = Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1
    $bootstrapGit = [IO.Path]::GetFullPath($bootstrapGitCommand.Source)
    $bootstrapHead = (
        & $bootstrapGit -C $sourceRepository rev-parse HEAD 2>&1 |
            Out-String
    ).Trim()
    $bootstrapHeadExit = $LASTEXITCODE
    $bootstrapDirty = (
        & $bootstrapGit -C $sourceRepository status --porcelain=v1 `
            --untracked-files=all 2>&1 | Out-String
    ).Trim()
    $bootstrapStatusExit = $LASTEXITCODE
    if (
        $bootstrapHeadExit -ne 0 -or
        $bootstrapStatusExit -ne 0 -or
        $bootstrapHead -cne $bootstrapCommit -or
        $bootstrapDirty
    ) {
        throw "M4 bootstrap requires the exact clean producer commit."
    }
    foreach ($source in $script:M4ExecutedSources) {
        $objectName = "${bootstrapCommit}:" + $source
        $expectedObject = (
            & $bootstrapGit -C $sourceRepository rev-parse `
                $objectName 2>&1 | Out-String
        ).Trim()
        $expectedObjectExit = $LASTEXITCODE
        $sourcePath = [IO.Path]::GetFullPath(
            (Join-Path $sourceRepository $source)
        )
        $actualObject = (
            & $bootstrapGit -C $sourceRepository hash-object `
                -- $sourcePath 2>&1 | Out-String
        ).Trim()
        $actualObjectExit = $LASTEXITCODE
        if (
            $expectedObjectExit -ne 0 -or
            $actualObjectExit -ne 0 -or
            $expectedObject -cnotmatch "^[0-9a-f]{40}$" -or
            $actualObject -cne $expectedObject
        ) {
            throw "M4 bootstrap source differs from producer commit: $source"
        }
    }
    $gitExecutableStream = New-Object IO.FileStream(
        $bootstrapGit,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $invocationInput = [pscustomobject]@{
        Stream = $bootstrapStream
        Bytes = $bootstrapBytes
        Sha256 = $null
    }
    $bootstrapStream = $null
    $m1Root = Join-Path $sourceRepository "oracle/windows-dao/scripts/m1"
    . (Join-Path $m1Root "M1.Preflight.ps1")
    . (Join-Path $m1Root "M1.DaoValues.ps1")
    . (Join-Path $sourceRepository "oracle/windows-dao/scripts/m4/M4.Dao.ps1")
    . (Join-Path $sourceRepository "oracle/windows-dao/scripts/m4/M4.Worker.ps1")
    . (Join-Path $sourceRepository "oracle/windows-dao/scripts/m4/M4.Artifacts.ps1")
    . (Join-Path $sourceRepository "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")
    $invocationInput.Sha256 = Get-M4BytesSha256 `
        -Bytes $invocationInput.Bytes
    $bundle = Get-M4WorkerLocalPath -Path $BundleRoot `
        -Label "BundleRoot"
    $invocationFile = Get-M4WorkerLocalPath -Path $InvocationPath `
        -Label "InvocationPath"
    if (
        -not [IO.Directory]::Exists($bundle) -or
        -not (Test-M4WorkerPathWithin -Path $invocationFile -Root $bundle)
    ) {
        throw "M4 invocation is not inside its private bundle root."
    }
    Assert-M4WorkerNoReparseAncestors -Path $bundle -Label "BundleRoot"

    $python = Get-M1Python3
    $pythonCommand = [string]$python.Command
    $validatorPath = Join-Path $sourceRepository (
        "oracle/windows-dao/scripts/m4_contract.py"
    )
    [void](Assert-M1BoundedFile -Path $validatorPath `
        -MaximumBytes 2MB -Label "M4 checked validator")
    $pythonPrefix = @($python.Prefix)
    $validatorDetail = (
        & $pythonCommand @pythonPrefix -B $validatorPath `
            "validate-invocation" "--bundle-root" $bundle `
            "--invocation" $invocationFile 2>&1 | Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "M4 checked invocation validation failed: $validatorDetail"
    }

    $invocation = ConvertFrom-M1Utf8Json `
        -Bytes $invocationInput.Bytes -Label "M4 invocation"
    $requiredInvocation = @(
        "protocol_version", "document_type", "experiment_id", "sample_id",
        "condition_id", "phase_id", "phase_ordinal", "worker_run_id",
        "worker_ordinal", "nonce", "campaign_run_id", "producer_commit",
        "repository_url", "remote_ref", "repository_root", "plan_path",
        "plan_sha256", "environment_path", "environment_sha256",
        "provider_sha256", "stage_root", "output_root", "database_path",
        "result_path", "phase_contract", "created_at_utc",
        "bindings_verified_before_com"
    )
    Assert-M4ExactProperties -Value $invocation `
        -Names $requiredInvocation -Label "M4 invocation"

    $paths = Get-M4InvocationPaths -Invocation $invocation `
        -SourceRepository $sourceRepository -BundleRoot $bundle `
        -InvocationFile $invocationFile
    $repository = [string]$paths.Repository
    $planPath = [string]$paths.Plan
    $environmentPath = [string]$paths.Environment
    $databasePath = [string]$paths.Database
    $resultPath = [string]$paths.Result

    $planInput = Read-M4HeldFile -Path $planPath `
        -MaximumBytes $script:M4MaximumInputBytes -Label "M4 plan"
    $environmentInput = Read-M4HeldFile -Path $environmentPath `
        -MaximumBytes $script:M4MaximumInputBytes `
        -Label "M4 environment"
    if (
        $planInput.Sha256 -cne [string]$invocation.plan_sha256 -or
        $environmentInput.Sha256 -cne
            [string]$invocation.environment_sha256
    ) {
        throw "M4 plan or environment bytes differ from their bindings."
    }
    $plan = ConvertFrom-M1Utf8Json -Bytes $planInput.Bytes `
        -Label "M4 plan"
    $environment = ConvertFrom-M1Utf8Json `
        -Bytes $environmentInput.Bytes -Label "M4 environment"
    [void](Assert-M4PlanProjection -Invocation $invocation -Plan $plan)

    $git = Get-M1GitExecutable
    if (-not $git.Equals(
        $bootstrapGit,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "M4 Git executable changed after bootstrap binding."
    }
    Assert-M1GitState -GitPath $git -Repository $repository `
        -Commit ([string]$invocation.producer_commit)
    foreach ($source in $script:M4ExecutedSources) {
        [void](Assert-M1GitBoundPath -GitPath $git `
            -Repository $repository `
            -Commit ([string]$invocation.producer_commit) `
            -RelativePath $source)
    }
    if (
        [string]$invocation.repository_url -cne
            $script:M4RepositoryUrl -or
        [string]$invocation.remote_ref -cne $script:M4RemoteRef
    ) {
        throw "M4 repository or remote-ref identity differs."
    }
    [Environment]::SetEnvironmentVariable("GIT_TERMINAL_PROMPT", "0")
    [Environment]::SetEnvironmentVariable("GCM_INTERACTIVE", "Never")
    $remoteProcess = Invoke-BoundedChildProcess -Executable $git `
        -Arguments @(
            "-C", $repository, "-c", "credential.interactive=never",
            "ls-remote", "--heads",
            ([string]$invocation.repository_url),
            ([string]$invocation.remote_ref)
        ) -CallerLabel "M4 remote binding" -TimeoutSeconds 30 `
        -MaximumOutputBytes 16KB
    $remote = @(
        ([string]$remoteProcess.stdout).Trim().Split(
            @("`r`n", "`n"),
            [StringSplitOptions]::RemoveEmptyEntries
        )
    )
    if (
        $remote.Count -ne 1 -or
        -not ([string]$remote[0]).StartsWith(
            ([string]$invocation.producer_commit) + "`t",
            [StringComparison]::Ordinal
        )
    ) {
        throw "M4 could not prove the exact pushed remote commit."
    }

    $acceptedProvider = Assert-M1ProviderEnvironment `
        -Environment $environment
    $providerPath = Assert-M1CurrentRegistration `
        -AcceptedProvider $acceptedProvider
    $providerStream = New-Object IO.FileStream(
        $providerPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    $providerHash = Get-M1StreamSha256 -Stream $providerStream
    if (
        $providerHash -cne [string]$invocation.provider_sha256 -or
        $providerHash -cne [string]$acceptedProvider.server_sha256
    ) {
        throw "M4 DAO provider bytes differ from the exact binding."
    }

    $artifacts = Get-M4PhaseArtifactSet -Invocation $invocation `
        -BundleRoot $bundle -ResultPath $resultPath
    $prefixLocator = [string]$artifacts.Locators.prefix
    $logLocator = [string]$artifacts.Locators.operation_log
    $snapshotLocator = [string]$artifacts.Locators.snapshot
    $prefixPath = [string]$artifacts.Paths.prefix
    $logPath = [string]$artifacts.Paths.operation_log
    $snapshotPath = [string]$artifacts.Paths.snapshot
    $failureTombstonePath = [string]$artifacts.Paths.failure

    $operations = New-Object Collections.ArrayList
    Add-M4OperationEntry -Entries $operations `
        -Action "bindings_verified"
    $preComBinding = $null
    if ([string]$invocation.phase_id -ceq "creator") {
        if (
            [IO.File]::Exists($databasePath) -or
            [IO.Directory]::Exists($databasePath)
        ) {
            throw "M4 creator database path must be create-new."
        }
    }
    else {
        $clonePath = Resolve-M4BundleLocator `
            -Locator ([string]$invocation.phase_contract.clone_log.path) `
            -Root $bundle -Label "clone_log.path"
        $cloneInput = Read-M4HeldFile -Path $clonePath `
            -MaximumBytes $script:M4MaximumInputBytes `
            -Label "M4 clone log"
        if (
            $cloneInput.Sha256 -cne
                [string]$invocation.phase_contract.clone_log.sha256
        ) {
            throw "M4 clone log differs from the invocation binding."
        }
        $preCom = Get-M4ClosedFileObservation `
            -DatabasePath $databasePath `
            -MaximumBytes ([long]$plan.bounds.max_database_bytes)
        if (
            $preCom.bytes -ne
                [long]$invocation.phase_contract.pre_com_database_bytes -or
            $preCom.sha256 -cne
                [string]$invocation.phase_contract.pre_com_database_sha256
        ) {
            throw "M4 reopen database differs from its pre-COM binding."
        }
        Assert-M4ReopenCloneBinding -Invocation $invocation `
            -Plan $plan -CloneInput $cloneInput -PreCom $preCom
        $preComBinding = [ordered]@{
            database_path = [string]$invocation.database_path
            database_bytes = [long]$preCom.bytes
            database_sha256 = [string]$preCom.sha256
        }
        Add-M4OperationEntry -Entries $operations `
            -Action "clone_verified"
    }
    Assert-M4LockFileAbsent -DatabasePath $databasePath

    $process = Get-Process -Id $PID
    $startedAt = $process.StartTime.ToUniversalTime().ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $phaseSnapshot = Invoke-M4DaoPhase `
        -DatabasePath $databasePath `
        -PhaseId ([string]$invocation.phase_id) `
        -PhaseContract $invocation.phase_contract `
        -AcceptedProvider $acceptedProvider `
        -OperationEntries $operations

    Assert-M4LockFileAbsent -DatabasePath $databasePath
    Add-M4OperationEntry -Entries $operations `
        -Action "ldb_absence_verified"
    $postClose = Get-M4ClosedFileObservation `
        -DatabasePath $databasePath `
        -MaximumBytes ([long]$plan.bounds.max_database_bytes)
    Add-M4OperationEntry -Entries $operations `
        -Action "prefix_observed"

    $snapshot = New-M4SnapshotDocument -Invocation $invocation `
        -PhaseSnapshot $phaseSnapshot
    $operationLog = New-M4OperationLogDocument `
        -Invocation $invocation -Operations $operations
    $snapshotBytes = ConvertTo-M4JsonBytes -Document $snapshot `
        -MaximumBytes 64KB
    $logBytes = ConvertTo-M4JsonBytes -Document $operationLog `
        -MaximumBytes 64KB
    Write-M4CreateNewBytes -Path $prefixPath `
        -Bytes $postClose.prefix -MaximumBytes 2048
    Write-M4CreateNewBytes -Path $snapshotPath `
        -Bytes $snapshotBytes -MaximumBytes 64KB
    Write-M4CreateNewBytes -Path $logPath `
        -Bytes $logBytes -MaximumBytes 64KB

    $bindingStreams = @(Get-M4BindingStreams `
        -ProviderStream $providerStream -CloneInput $cloneInput `
        -GitExecutableStream $gitExecutableStream `
        -EnvironmentInput $environmentInput -PlanInput $planInput `
        -InvocationInput $invocationInput)
    $bindingCleanup = @(Close-M4BindingStreams -Streams $bindingStreams)
    $bindingsClosed = $true
    $providerStream = $null
    $gitExecutableStream = $null
    $cloneInput = $null
    $environmentInput = $null
    $planInput = $null
    $invocationInput.Stream = $null
    if ($bindingCleanup.Count -gt 0) {
        $bindingException = New-Object InvalidOperationException(
            "M4 binding stream cleanup failed before result commit."
        )
        $bindingException.Data["M1CleanupErrors"] = $bindingCleanup
        throw $bindingException
    }

    $result = New-M4WorkerResultDocument -Invocation $invocation `
        -AcceptedProvider $acceptedProvider -ProviderHash $providerHash `
        -StartedAt $startedAt `
        -InvocationSha256 ([string]$invocationInput.Sha256) `
        -LogLocator $logLocator -LogBytes $logBytes `
        -SnapshotLocator $snapshotLocator -SnapshotBytes $snapshotBytes `
        -PreComBinding $preComBinding -PostClose $postClose `
        -PrefixLocator $prefixLocator
    $resultBytes = ConvertTo-M4JsonBytes -Document $result `
        -MaximumBytes 64KB
    Write-M4CreateNewBytes -Path $resultPath `
        -Bytes $resultBytes -MaximumBytes 64KB
    $resultCommitted = $true
    exit 0
}
catch {
    $primaryError = $_
    $catchCleanup = @()
    if (-not $bindingsClosed) {
        $streamCloser = Get-Command Close-M4BindingStreams `
            -CommandType Function -ErrorAction SilentlyContinue
        if ($null -ne $streamCloser) {
            $streams = @(Get-M4BindingStreams `
                -BootstrapStream $bootstrapStream `
                -ProviderStream $providerStream -CloneInput $cloneInput `
                -GitExecutableStream $gitExecutableStream `
                -EnvironmentInput $environmentInput -PlanInput $planInput `
                -InvocationInput $invocationInput)
            $catchCleanup = @(Close-M4BindingStreams -Streams $streams)
        }
        else {
            $fallbackErrors = New-Object Collections.ArrayList
            foreach ($stream in @(
                $bootstrapStream,
                $gitExecutableStream,
                $(if ($null -ne $invocationInput) {
                    $invocationInput.Stream
                })
            )) {
                if ($null -eq $stream) { continue }
                try { $stream.Dispose() }
                catch {
                    [void]$fallbackErrors.Add(
                        [string]$_.Exception.Message
                    )
                }
            }
            $catchCleanup = @($fallbackErrors)
        }
        $bindingsClosed = $true
    }
    $failureHandler = Get-Command Complete-M4WorkerFailure `
        -CommandType Function -ErrorAction SilentlyContinue
    if ($null -ne $failureHandler) {
        Complete-M4WorkerFailure -PrimaryError $primaryError `
            -Invocation $invocation -CleanupErrors $catchCleanup `
            -FailureTombstonePath $failureTombstonePath `
            -ResultCommitted $resultCommitted
    }
    else {
        $fallback = [ordered]@{
            document_type = "dao_m4_worker_error"
            cleanup_errors = @($catchCleanup)
            exception_type = $primaryError.Exception.GetType().FullName
            message = [string]$primaryError.Exception.Message
        }
        [Console]::Error.WriteLine(
            ($fallback | ConvertTo-Json -Depth 4 -Compress)
        )
    }
    exit 1
}
finally {
    if (-not $bindingsClosed) {
        foreach ($held in @(
            $bootstrapStream,
            $providerStream,
            $gitExecutableStream,
            $(if ($null -ne $invocationInput) {
                $invocationInput.Stream
            })
        )) {
            if ($null -ne $held) {
                try { $held.Dispose() } catch { }
            }
        }
    }
}
