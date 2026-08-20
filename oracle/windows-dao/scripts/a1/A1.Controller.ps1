Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A1RepositoryUrl = "https://github.com/oglassdev/jet3-rs.git"
$script:A1ExperimentId = "DAO-A1-ALLOCATION-MAPS-001"
$script:A1PlanRelative = `
    "oracle/windows-dao/experiments/a1/a1-allocation-maps.plan.json"
$script:A1ContractRelative = "oracle/windows-dao/scripts/a1_contract.py"
$script:A1AnalysisRelative = "oracle/windows-dao/scripts/a1_analysis.py"
$script:A1FrozenPlanSha256 = `
    "a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80"
$script:A1CampaignClock = $null

function Get-A1CampaignAllowance {
    param([Parameter(Mandatory = $true)][int]$MaximumSeconds)

    if ($null -eq $script:A1CampaignClock) { return $MaximumSeconds }
    $remaining = [int][Math]::Floor(
        7200 - $script:A1CampaignClock.Elapsed.TotalSeconds
    )
    if ($remaining -lt 1) {
        throw "A1 campaign exceeded its 7200-second wall-clock ceiling."
    }
    return [Math]::Min($MaximumSeconds, $remaining)
}

function Read-A1ControllerInput {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$MaximumBytes = 1MB
    )

    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -lt 2 -or
        $item.Length -gt $MaximumBytes) {
        throw "A1 controller input violates its byte or file-type bound."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length -or
        ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
            $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf)) {
        throw "A1 controller input changed or contains a forbidden BOM."
    }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    return [pscustomobject]@{
        bytes = $bytes
        document = ($text | ConvertFrom-Json)
        sha256 = Get-M1ByteArraySha256 -Bytes $bytes
    }
}

function New-A1EnvironmentBytes {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$PythonVersion
    )

    $accepted = $Context.AcceptedProvider
    $document = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a1_environment"
        experiment_id = $script:A1ExperimentId
        plan_sha256 = $PlanSha256
        producer_commit = $Context.GitCommit
        repository_url = $script:A1RepositoryUrl
        run_id = $Context.RunId
        status = "ready"
        host = [ordered]@{
            windows_version = [string]$Context.Environment.host.os_version
            process_architecture = "x86"
            powershell_version = $PSVersionTable.PSVersion.ToString()
            python_version = $PythonVersion
        }
        provider = [ordered]@{
            prog_id = [string]$accepted.prog_id
            clsid = [string]$accepted.clsid
            provider_version = [string]$accepted.provider_version
            server_path = [string]$accepted.server_path
            server_file_version = [string]$accepted.server_file_version
            server_sha256 = [string]$accepted.server_sha256
        }
    }
    $json = $document | ConvertTo-Json -Depth 8 -Compress
    return ,(New-Object Text.UTF8Encoding($false)).GetBytes($json + "`n")
}

function Assert-A1PythonRuntime {
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)

    $probe = Invoke-BoundedChildProcess -Executable $Context.PythonPath `
        -Arguments @(
            "-B", "-c",
            "import sys;print('.'.join(map(str,sys.version_info[:3])))"
        ) -CallerLabel "A1 Python runtime binding" `
        -TimeoutSeconds 30 -MaximumOutputBytes 1KB
    $text = ([string]$probe.stdout).Trim()
    if ($text -cnotmatch "^[0-9]+\.[0-9]+\.[0-9]+$") {
        throw "A1 Python runtime returned a noncanonical version."
    }
    $version = New-Object Version($text)
    if ($version.Major -ne 3 -or $version.Minor -lt 10) {
        throw "A1 requires Python 3.10 or newer."
    }
    if ($version.Major -ne 3 -or $version.Minor -ne 13 -or
        [string]$Context.Python.Version -cne $text) {
        throw "A1 requires the preflight-bound Python 3.13 runtime."
    }
    return $text
}

function Invoke-A1Python {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [ValidateRange(1, 1800)][int]$MaximumSeconds = 120
    )

    $timeout = Get-A1CampaignAllowance -MaximumSeconds $MaximumSeconds
    [void](Invoke-BoundedChildProcess -Executable $Context.PythonPath `
        -Arguments (@("-B", $ScriptPath) + $Arguments) `
        -CallerLabel $Label -TimeoutSeconds $timeout -MaximumOutputBytes 1MB)
}

function Assert-A1ExactPushedCommit {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan
    )

    $url = @(& $Context.GitExecutable -C $Context.RepositoryRoot `
        remote get-url origin 2>&1)
    if ($LASTEXITCODE -ne 0 -or $url.Count -ne 1 -or
        [string]$url[0] -cne $script:A1RepositoryUrl -or
        [string]$Plan.repository_binding.canonical_https_url -cne
            $script:A1RepositoryUrl) {
        throw "A1 repository origin differs from its immutable binding."
    }
    $timeout = Get-A1CampaignAllowance -MaximumSeconds 30
    $remote = Invoke-BoundedChildProcess -Executable $Context.GitExecutable `
        -Arguments @(
            "-c", "credential.interactive=never", "-c", "core.askPass=",
            "ls-remote", "--heads", $script:A1RepositoryUrl
        ) -CallerLabel "A1 pushed commit binding" `
        -TimeoutSeconds $timeout -MaximumOutputBytes 1MB
    $matches = @([string]$remote.stdout -split "\r?\n" | Where-Object {
        $_.StartsWith($Context.GitCommit + "`t", [StringComparison]::Ordinal)
    })
    if ($matches.Count -lt 1) {
        throw "A1 producer commit is not advertised by a pushed branch."
    }
}

function Assert-A1RuntimeGate {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][string]$Repository
    )

    if ([string]$Plan.execution_gate.status -cne "BLOCKED") {
        throw "A1 preregistration gate history was unexpectedly rewritten."
    }
    $expected = @(
        "checked_windows_acquisition",
        "independent_complete_bundle_validator",
        "exact_clean_pushed_producer_commit",
        "licensed_x86_dao_host_binding"
    )
    $actual = @($Plan.execution_gate.blocking_requirements)
    if ($actual.Count -ne $expected.Count) {
        throw "A1 preregistration blocking-requirement set drifted."
    }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ([string]$actual[$index] -cne $expected[$index]) {
            throw "A1 preregistration blocking-requirement set drifted."
        }
    }
    foreach ($relative in @(
        "oracle/windows-dao/scripts/run-a1-controlled.ps1",
        "oracle/windows-dao/scripts/a1/A1.Controller.ps1",
        "oracle/windows-dao/scripts/a1/A1.Worker.ps1",
        "oracle/windows-dao/scripts/a1/A1.PageStore.ps1",
        "oracle/windows-dao/scripts/a1_contract.py"
    )) {
        $path = Join-Path $Repository $relative
        if (-not [IO.File]::Exists($path)) {
            throw "A1 runtime requirement is not implemented: $relative"
        }
        Assert-M1NoReparseComponents -Path $path
    }
}

function Get-A1WorkerPowerShell {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop" -or
        $PSVersionTable.PSVersion.Major -ne 5) {
        throw "A1 requires x86 Windows PowerShell 5 Desktop."
    }
    $path = [IO.Path]::GetFullPath(
        [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    )
    Assert-M1NoReparseComponents -Path $path
    $stream = New-Object IO.FileStream(
        $path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan
    )
    try {
        return [pscustomobject]@{
            path = $path
            bytes = [long]$stream.Length
            sha256 = Get-M1StreamSha256 -Stream $stream
            stream = $stream
        }
    }
    catch { $stream.Dispose(); throw }
}

function Assert-A1WorkerPowerShell {
    param([Parameter(Mandatory = $true)][pscustomobject]$Binding)

    Assert-M1NoReparseComponents -Path $Binding.path
    $item = Get-Item -LiteralPath $Binding.path -Force
    if ($item.PSIsContainer -or $item.Length -ne $Binding.bytes -or
        -not $Binding.stream.CanRead -or
        (Get-M1StreamSha256 -Stream $Binding.stream) -cne $Binding.sha256) {
        throw "A1 worker PowerShell binding changed before launch."
    }
}

function Read-A1LongProcessOutput {
    param(
        [Parameter(Mandatory = $true)][Jet3BoundedProcessLaunch]$Launch,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][long]$MaximumOutputBytes
    )

    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 1800 -or
        $MaximumOutputBytes -lt 1 -or $MaximumOutputBytes -gt 1MB) {
        throw "A1 worker process bounds are outside the preregistration."
    }
    $stdout = New-Object IO.MemoryStream
    $stderr = New-Object IO.MemoryStream
    $outBuffer = New-Object byte[] 4096
    $errBuffer = New-Object byte[] 4096
    $outTask = $Launch.StandardOutput.ReadAsync($outBuffer, 0, 4096)
    $errTask = $Launch.StandardError.ReadAsync($errBuffer, 0, 4096)
    $outDone = $false
    $errDone = $false
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        while (-not ($outDone -and $errDone -and $Launch.HasExited)) {
            if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Stop-BoundedProcessJob -Launch $Launch
                throw "A1 worker exceeded its 1800-second ceiling."
            }
            $tasks = New-Object Collections.ArrayList
            $labels = New-Object Collections.ArrayList
            if (-not $outDone) {
                [void]$tasks.Add($outTask); [void]$labels.Add("out")
            }
            if (-not $errDone) {
                [void]$tasks.Add($errTask); [void]$labels.Add("err")
            }
            if ($tasks.Count -eq 0) { Start-Sleep -Milliseconds 10; continue }
            $index = [Threading.Tasks.Task]::WaitAny(
                [Threading.Tasks.Task[]]$tasks, 100
            )
            if ($index -lt 0) { continue }
            $label = [string]$labels[$index]
            $task = if ($label -ceq "out") { $outTask } else { $errTask }
            $read = $task.GetAwaiter().GetResult()
            if ($read -eq 0) {
                if ($label -ceq "out") { $outDone = $true }
                else { $errDone = $true }
                continue
            }
            if ($stdout.Length + $stderr.Length + $read -gt
                $MaximumOutputBytes) {
                Stop-BoundedProcessJob -Launch $Launch
                throw "A1 worker output exceeded its byte ceiling."
            }
            if ($label -ceq "out") {
                $stdout.Write($outBuffer, 0, $read)
                $outTask = $Launch.StandardOutput.ReadAsync($outBuffer, 0, 4096)
            }
            else {
                $stderr.Write($errBuffer, 0, $read)
                $errTask = $Launch.StandardError.ReadAsync($errBuffer, 0, 4096)
            }
        }
        if (-not $Launch.WaitForExit(1000)) {
            throw "A1 worker exit was not observable within its ceiling."
        }
        $encoding = New-Object Text.UTF8Encoding($false, $false)
        return [pscustomobject]@{
            stdout = $encoding.GetString($stdout.ToArray())
            stderr = $encoding.GetString($stderr.ToArray())
        }
    }
    finally {
        $clock.Stop(); $stdout.Dispose(); $stderr.Dispose()
    }
}

function Invoke-A1ReplicaWorker {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Binding,
        [Parameter(Mandatory = $true)][string]$WorkerPath,
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][string]$PlanPath,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica,
        [Parameter(Mandatory = $true)][ValidateRange(1, 1800)][int]$TimeoutSeconds
    )

    Assert-A1WorkerPowerShell -Binding $Binding
    $arguments = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $WorkerPath, "-RepositoryRoot", $Context.RepositoryRoot,
        "-BundleRoot", $Session.StagingBundle,
        "-WorkingRoot", $Session.WorkingPath, "-PlanPath", $PlanPath,
        "-EnvironmentPath", $EnvironmentPath,
        "-ProducerCommit", $Context.GitCommit,
        "-RunId", $Context.RunId,
        "-PlanSha256", $PlanSha256,
        "-EnvironmentSha256", $EnvironmentSha256,
        "-ReplicaOrdinal", $Replica.ToString()
    )
    $argumentText = (@($arguments | ForEach-Object {
        ConvertTo-BoundedProcessCommandLineArgument -Value $_
    }) -join " ")
    $commandLine = ConvertTo-BoundedProcessCommandLineArgument `
        -Value $Binding.path
    $commandLine += " " + $argumentText
    if ($commandLine.Length -gt 32766) {
        throw "A1 worker command line exceeds the Windows ceiling."
    }
    Initialize-BoundedProcessJobNative
    $launch = [Jet3BoundedProcessJobNative]::StartSuspendedInJob(
        $Binding.path, $commandLine
    )
    try {
        $captured = Read-A1LongProcessOutput -Launch $launch `
            -TimeoutSeconds $TimeoutSeconds -MaximumOutputBytes 1MB
        if ($launch.ExitCode -ne 0) {
            $detail = [string]$captured.stderr
            if ($detail.Length -gt 2000) { $detail = $detail.Substring(0, 2000) }
            throw "A1 replica worker failed: $detail"
        }
    }
    finally {
        try { Stop-BoundedProcessJob -Launch $launch }
        finally { $launch.Dispose() }
    }
}

function Get-A1FileRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -gt 64MB) {
        throw "A1 bundle artifact violates its file bound."
    }
    $relative = $Path.Substring($Root.TrimEnd('\').Length + 1).Replace('\', '/')
    $role = if ($relative -ceq "plan/a1-allocation-maps.plan.json") { "plan" }
        elseif ($relative -ceq "environment/environment.json") { "environment" }
        elseif ($relative.StartsWith("observations/")) { "replica_observation" }
        elseif ($relative.StartsWith("page-indexes/")) { "page_index" }
        elseif ($relative.StartsWith("page-store/")) { "page_blob" }
        elseif ($relative -ceq "analysis/analysis-report.json") { "analysis_report" }
        else { throw "A1 bundle contains an unexpected artifact path." }
    $media = if ($role -ceq "page_blob") {
        "application/octet-stream"
    } else { "application/json" }
    return [ordered]@{
        path = $relative
        role = $role
        sha256 = Get-M1FileSha256 -Path $Path
        size_bytes = [long]$item.Length
        media_type = $media
    }
}

function Write-A1Manifest {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$EnvironmentSha256
    )

    $files = @([IO.Directory]::EnumerateFiles(
        $Session.StagingBundle, "*", [IO.SearchOption]::AllDirectories
    ))
    if ($files.Count -gt 262399) {
        throw "A1 bundle exceeds its artifact-count ceiling."
    }
    $records = New-Object Collections.ArrayList
    $total = [long]0
    $pageBlobs = 0
    foreach ($path in @($files | Sort-Object)) {
        $record = Get-A1FileRecord -Root $Session.StagingBundle -Path $path
        if ($record.size_bytes -gt (768MB - $total)) {
            throw "A1 bundle exceeds its retained-byte ceiling."
        }
        $total += [long]$record.size_bytes
        if ($record.role -ceq "page_blob") { $pageBlobs++ }
        [void]$records.Add($record)
    }
    if ($pageBlobs -gt 262144) {
        throw "A1 page-store exceeds its unique-blob ceiling."
    }
    $manifest = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_a1_bundle_manifest"
        experiment_id = $script:A1ExperimentId
        run_id = $Context.RunId
        producer_commit = $Context.GitCommit
        repository_url = $script:A1RepositoryUrl
        created_utc = [DateTime]::UtcNow.ToString("o")
        plan_sha256 = $PlanSha256
        environment_sha256 = $EnvironmentSha256
        provider_sha256 = [string]$Context.AcceptedProvider.server_sha256
        replica_count = 3
        checkpoint_count = [int](3 * $Plan.checkpoint_design.count)
        page_blob_count = $pageBlobs
        bundle_size_bytes_excluding_manifest = $total
        inventory_closed = $true
        hashes_verified = $true
        paths_closed = $true
        execution_status = "pass"
        files = @($records)
    }
    $json = $manifest | ConvertTo-Json -Depth 8 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json + "`n")
    Write-M1DurableBytes -Session $Session `
        -RelativePath "bundle-manifest.json" -Bytes $bytes
}

function Remove-A1PrivateStaging {
    param([Parameter(Mandatory = $true)][pscustomobject]$Session)

    $stage = [IO.Path]::GetFullPath($Session.StagingRoot)
    $output = [IO.Path]::GetFullPath($Session.OutputRoot)
    $parent = [IO.Path]::GetDirectoryName($stage)
    $name = [IO.Path]::GetFileName($stage)
    if (-not $parent.Equals($output, [StringComparison]::OrdinalIgnoreCase) -or
        $name -cnotmatch "^\.m1-stage-[0-9a-f]{32}$") {
        throw "Refusing cleanup outside the owned A1 staging boundary."
    }
    if (-not (Test-Path -LiteralPath $stage)) { return }
    $item = Get-Item -LiteralPath $stage -Force
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing cleanup of a reparse or non-directory A1 stage."
    }
    Assert-M1CleanupTreeBounded -Root $stage -MaxBytes 1GB `
        -MaxEntries 263000
    [IO.Directory]::Delete($stage, $true)
}

function Invoke-A1Campaign {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$GitCommit,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    $executed = @(
        "oracle/windows-dao/scripts/run-a1-controlled.ps1",
        "oracle/windows-dao/scripts/a1/A1.Controller.ps1",
        "oracle/windows-dao/scripts/a1/A1.Worker.ps1",
        "oracle/windows-dao/scripts/a1/A1.PageStore.ps1",
        "oracle/windows-dao/scripts/a1_contract.py",
        "oracle/windows-dao/scripts/a1_bundle.py",
        "oracle/windows-dao/scripts/a1_analysis.py",
        "oracle/windows-dao/scripts/a1_spec.py",
        "oracle/windows-dao/experiments/a1/a1-allocation-maps.plan.json",
        "oracle/windows-dao/experiments/a1/plan.schema.json",
        "oracle/windows-dao/experiments/a1/replica-observation.schema.json",
        "oracle/windows-dao/experiments/a1/page-index.schema.json",
        "oracle/windows-dao/experiments/a1/environment.schema.json",
        "oracle/windows-dao/experiments/a1/analysis-report.schema.json",
        "oracle/windows-dao/experiments/a1/bundle-manifest.schema.json",
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
        "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
        "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
        "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1"
    )
    $context = $null
    $session = $null
    $binding = $null
    $campaignClock = [Diagnostics.Stopwatch]::StartNew()
    $script:A1CampaignClock = $campaignClock
    try {
        $context = Invoke-M1Preflight -RepositoryRoot $repository `
            -EnvironmentPath $EnvironmentPath -OutputRoot $OutputRoot `
            -GitCommit $GitCommit -RunId $RunId `
            -ExecutedRepoRelativeSourcePaths $executed
        $pythonVersion = Assert-A1PythonRuntime -Context $context
        $planPath = Join-Path $repository $script:A1PlanRelative
        $planInput = Read-A1ControllerInput -Path $planPath
        if ($planInput.sha256 -cne $script:A1FrozenPlanSha256) {
            throw "A1 plan bytes differ from the frozen preregistration."
        }
        $contractPath = Join-Path $repository $script:A1ContractRelative
        Invoke-A1Python -Context $context -ScriptPath $contractPath `
            -Arguments @("validate-plan", $planPath) `
            -Label "A1 immutable plan validation"
        $plan = $planInput.document
        if ([string]$plan.experiment_id -cne $script:A1ExperimentId -or
            [int]$plan.replicas.count -ne 3 -or
            [int]$plan.bounds.campaign_timeout_seconds -ne 7200 -or
            [int]$plan.bounds.worker_timeout_seconds -ne 1800) {
            throw "A1 plan identity or process ceilings drifted."
        }
        Assert-A1RuntimeGate -Plan $plan -Repository $repository
        Assert-A1ExactPushedCommit -Context $context -Plan $plan
        [void](Assert-M1RuntimeBinding -Context $context)
        # The publication session cleanup ceiling also covers three private
        # working MDBs; the retained bundle is independently capped at 768 MiB.
        $session = New-M1PublicationSession -RepositoryRoot $repository `
            -OutputRoot $OutputRoot -GitCommit $GitCommit -RunId $RunId `
            -MaxFileBytes 64MB -MaxTotalBytes 1GB
        Write-M1DurableBytes -Session $session `
            -RelativePath "plan/a1-allocation-maps.plan.json" `
            -Bytes $planInput.bytes
        $a1EnvironmentBytes = New-A1EnvironmentBytes -Context $context `
            -PlanSha256 $planInput.sha256 -PythonVersion $pythonVersion
        $a1EnvironmentSha = Get-M1ByteArraySha256 -Bytes $a1EnvironmentBytes
        Write-M1DurableBytes -Session $session `
            -RelativePath "environment/environment.json" `
            -Bytes $a1EnvironmentBytes
        $stagedPlan = Get-M1PayloadPath -Session $session `
            -RelativePath "plan/a1-allocation-maps.plan.json"
        $stagedEnvironment = Get-M1PayloadPath -Session $session `
            -RelativePath "environment/environment.json"
        Invoke-A1Python -Context $context -ScriptPath $contractPath `
            -Arguments @("validate-document", $stagedEnvironment) `
            -Label "A1 environment validation"
        $workerPath = Join-Path $repository `
            "oracle/windows-dao/scripts/a1/A1.Worker.ps1"
        $binding = Get-A1WorkerPowerShell
        for ($replica = 1; $replica -le 3; $replica++) {
            if ($campaignClock.Elapsed.TotalSeconds -ge 7200) {
                throw "A1 campaign exceeded its wall-clock ceiling."
            }
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-A1ExactPushedCommit -Context $context -Plan $plan
            Invoke-A1ReplicaWorker -Binding $binding -WorkerPath $workerPath `
                -Context $context -Session $session -PlanPath $stagedPlan `
                -PlanSha256 $planInput.sha256 `
                -EnvironmentPath $stagedEnvironment `
                -EnvironmentSha256 $a1EnvironmentSha `
                -Replica $replica `
                -TimeoutSeconds (Get-A1CampaignAllowance -MaximumSeconds 1800)
            $observation = Get-M1PayloadPath -Session $session `
                -RelativePath ("observations/replica-{0:D2}.json" -f $replica)
            Invoke-A1Python -Context $context -ScriptPath $contractPath `
                -Arguments @("validate-document", $observation) `
                -Label "A1 replica observation validation"
        }
        $analysisPath = Join-Path $repository $script:A1AnalysisRelative
        $analysisOutput = Join-Path $session.WorkingPath `
            "a1-analysis-report.json"
        if (Test-Path -LiteralPath $analysisOutput) {
            throw "A1 private analysis output unexpectedly exists."
        }
        Invoke-A1Python -Context $context -ScriptPath $analysisPath `
            -Arguments @(
                "--replica", (Get-M1PayloadPath -Session $session `
                    -RelativePath "observations/replica-01.json"),
                "--replica", (Get-M1PayloadPath -Session $session `
                    -RelativePath "observations/replica-02.json"),
                "--replica", (Get-M1PayloadPath -Session $session `
                    -RelativePath "observations/replica-03.json"),
                "--bundle-root", $session.StagingBundle,
                "--output", $analysisOutput
            ) -Label "A1 preregistered analysis" -MaximumSeconds 1800
        $analysisInput = Read-A1ControllerInput -Path $analysisOutput `
            -MaximumBytes 64MB
        Invoke-A1Python -Context $context -ScriptPath $contractPath `
            -Arguments @("validate-document", $analysisOutput) `
            -Label "A1 analysis report validation"
        Write-M1DurableBytes -Session $session `
            -RelativePath "analysis/analysis-report.json" `
            -Bytes $analysisInput.bytes
        Write-A1Manifest -Context $context -Session $session -Plan $plan `
            -PlanSha256 $planInput.sha256 `
            -EnvironmentSha256 $a1EnvironmentSha
        if ($campaignClock.Elapsed.TotalSeconds -ge 7200) {
            throw "A1 campaign exceeded its wall-clock ceiling."
        }
        $validate = {
            param($bundle)
            Invoke-A1Python -Context $context -ScriptPath $contractPath `
                -Arguments @("validate-bundle", $bundle) `
                -Label "A1 complete bundle validation" -MaximumSeconds 1800
        }
        $recheck = {
            param($stage)
            [void](Assert-M1RuntimeBinding -Context $context)
            Assert-A1ExactPushedCommit -Context $context -Plan $plan
            & $validate $stage.StagingBundle
            return $true
        }
        Publish-M1Stage -Stage $session -RecheckScriptBlock $recheck `
            -ValidationScriptBlock $validate
        $published = $session.FinalDirectory
        $session = $null
        return $published
    }
    catch {
        $original = $_
        if ($null -ne $session) {
            try { Remove-A1PrivateStaging -Session $session }
            catch {
                $cleanup = $_
                throw ($original.Exception.Message +
                    " A1 private staging cleanup also failed: " +
                    $cleanup.Exception.Message)
            }
        }
        throw $original
    }
    finally {
        $campaignClock.Stop()
        $script:A1CampaignClock = $null
        if ($null -ne $binding) { try { $binding.stream.Dispose() } catch { } }
        if ($null -ne $context) { Close-M1PreflightContext -Context $context }
    }
}
