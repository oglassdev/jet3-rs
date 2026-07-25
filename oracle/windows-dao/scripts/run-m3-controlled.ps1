[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$GitCommit,
    [Parameter(Mandatory = $true)][string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$moduleRoot = Join-Path $repository "oracle/windows-dao/scripts/m1"
. (Join-Path $moduleRoot "M1.Preflight.ps1")
. (Join-Path $moduleRoot "M1.Publication.ps1")
. (Join-Path $moduleRoot "M1.DaoValues.ps1")
. (Join-Path $repository "oracle/windows-dao/scripts/m3/M3.Process.ps1")

$planPath = Join-Path $repository (
    "oracle/windows-dao/experiments/m3/m3-index-isolation.plan.json"
)
$contractPath = Join-Path $repository "oracle/windows-dao/scripts/m3_contract.py"
$workerPath = Join-Path $repository "oracle/windows-dao/scripts/run-m3-sample.ps1"
$winps32 = Join-Path $env:WINDIR (
    "SysWOW64/WindowsPowerShell/v1.0/powershell.exe"
)
$executedSources = @(
    "oracle/windows-dao/scripts/run-m3-controlled.ps1",
    "oracle/windows-dao/scripts/run-m3-sample.ps1",
    "oracle/windows-dao/scripts/m3_contract.py",
    "oracle/windows-dao/scripts/m3_analysis.py",
    "oracle/windows-dao/scripts/m3_experiment.py",
    "oracle/windows-dao/scripts/m3/M3.Process.ps1",
    "oracle/windows-dao/experiments/m3/plan.schema.json",
    "oracle/windows-dao/experiments/m3/m3-index-isolation.plan.json",
    "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
    "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
    "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
    "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
    "oracle/windows-dao/scripts/m1/M1.Dao.ps1",
    "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1"
)

function Read-M3JsonBytes {
    param([string]$Path, [long]$MaximumBytes = 1MB)

    $item = Get-Item -LiteralPath $Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 1 -or
        $item.Length -gt $MaximumBytes
    ) {
        throw "M3 JSON violates its file or byte ceiling."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length) {
        throw "M3 JSON changed while being read."
    }
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    $text = $encoding.GetString($bytes)
    if ($text[0] -eq [char]0xfeff) {
        throw "M3 JSON cannot contain a byte-order mark."
    }
    return [ordered]@{ bytes = $bytes; document = $text | ConvertFrom-Json }
}

function ConvertTo-M3JsonText {
    param([object]$Document)
    return ($Document | ConvertTo-Json -Depth 100 -Compress) + "`n"
}

function Add-M3ManifestEntry {
    param(
        [Collections.ArrayList]$Entries,
        [pscustomobject]$Session,
        [string]$RelativePath,
        [string]$Role
    )

    if (@($Entries | Where-Object { $_.path -ceq $RelativePath }).Count) {
        throw "M3 manifest path is duplicated."
    }
    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    $item = Get-Item -LiteralPath $path -Force
    $media = if ($RelativePath.EndsWith(".mdb")) {
        "application/vnd.ms-access"
    }
    elseif ($RelativePath.EndsWith(".bin")) {
        "application/octet-stream"
    }
    else {
        "application/json"
    }
    [void]$Entries.Add([ordered]@{
        media_type = $media
        path = $RelativePath
        role = $Role
        sha256 = Get-M1FileSha256 -Path $path
        size_bytes = [long]$item.Length
    })
}

function Assert-M3RemoteCommit {
    param(
        [pscustomobject]$Context,
        [string]$RemoteRef,
        [string]$RepositoryUrl
    )

    $origin = @(
        & $Context.GitExecutable -C $Context.RepositoryRoot `
            remote get-url origin 2>&1
    )
    if (
        $LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne $RepositoryUrl
    ) {
        throw "M3 origin differs from the checked private repository."
    }
    $lines = @(
        & $Context.GitExecutable -C $Context.RepositoryRoot `
            ls-remote --heads $RepositoryUrl $RemoteRef 2>&1
    )
    if (
        $LASTEXITCODE -ne 0 -or $lines.Count -ne 1 -or
        -not ([string]$lines[0]).StartsWith(
            $Context.GitCommit + "`t", [StringComparison]::Ordinal
        )
    ) {
        throw "M3 requires the exact clean commit at the checked origin ref."
    }
}

function Invoke-M3Worker {
    param(
        [string]$Executable,
        [string]$ScriptPath,
        [string]$InvocationPath,
        [int]$TimeoutSeconds
    )

    [void](Invoke-M3ChildProcess -Executable $Executable -Arguments @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $ScriptPath, "-InvocationPath", $InvocationPath
    ) -TimeoutSeconds $TimeoutSeconds)
}

function Remove-M3WorkingSample {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "M3 working sample is not an owned ordinary directory."
    }
    $children = @(Get-ChildItem -LiteralPath $Path -Force)
    if ($children.Count -gt 3) {
        throw "M3 working sample exceeds its cleanup entry ceiling."
    }
    foreach ($child in $children) {
        if (
            $child.PSIsContainer -or
            ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "M3 working sample contains an unsafe cleanup entry."
        }
        [IO.File]::Delete($child.FullName)
    }
    [IO.Directory]::Delete($Path, $false)
}

$context = $null
$session = $null
try {
    if (-not (Test-Path -LiteralPath $winps32 -PathType Leaf)) {
        throw "The required 32-bit Windows PowerShell executable is absent."
    }
    $context = Invoke-M1Preflight `
        -RepositoryRoot $repository `
        -EnvironmentPath $EnvironmentPath `
        -OutputRoot $OutputRoot `
        -GitCommit $GitCommit `
        -RunId $RunId `
        -ExecutedRepoRelativeSourcePaths $executedSources
    $planInput = Read-M3JsonBytes -Path $planPath
    $planSha = Get-M1ByteArraySha256 -Bytes $planInput.bytes
    $plan = $planInput.document
    $validation = (
        & $context.PythonPath -B $contractPath plan $planPath 2>&1 |
            Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Checked M3 plan validation failed: $validation"
    }
    Assert-M3RemoteCommit -Context $context `
        -RemoteRef ([string]$plan.remote_ref) `
        -RepositoryUrl ([string]$plan.repository_url)
    [void](Assert-M1RuntimeBinding -Context $context)

    $session = New-M1PublicationSession `
        -RepositoryRoot $repository `
        -OutputRoot $OutputRoot `
        -GitCommit $GitCommit `
        -RunId $RunId `
        -MaxFileBytes 16MB `
        -MaxTotalBytes 64MB
    $entries = New-Object Collections.ArrayList
    Write-M1DurableBytes -Session $session -RelativePath "plan.json" `
        -Bytes $planInput.bytes
    Add-M3ManifestEntry -Entries $entries -Session $session `
        -RelativePath "plan.json" -Role "plan"
    Write-M1DurableBytes -Session $session -RelativePath "environment.json" `
        -Bytes $context.EnvironmentBytes
    Add-M3ManifestEntry -Entries $entries -Session $session `
        -RelativePath "environment.json" -Role "environment"

    $sampleResults = New-Object Collections.ArrayList
    $retainedDatabases = @{}
    $campaignPrefix = $RunId.Substring(0, 16)
    foreach ($sample in $plan.samples) {
        [void](Assert-M1RuntimeBinding -Context $context)
        Assert-M3RemoteCommit -Context $context `
            -RemoteRef ([string]$plan.remote_ref) `
            -RepositoryUrl ([string]$plan.repository_url)
        $condition = @(
            $plan.conditions |
                Where-Object { $_.condition_id -ceq $sample.condition_id }
        )[0]
        $working = Join-Path $session.WorkingPath "sample"
        [void][IO.Directory]::CreateDirectory($working)
        $sampleRoot = "samples/$($sample.sample_id)"
        $invocationRelative = "$sampleRoot/invocation.json"
        $resultPath = Join-Path $working "result.json"
        $workerRunId = $campaignPrefix + "-m3-w" +
            ([int]$sample.launch_ordinal).ToString("00")
        $workerOutput = Join-Path (
            Join-Path $session.StageRoot "worker-preflight"
        ) ([string]$sample.sample_id)
        $invocation = [ordered]@{
            block = [int]$sample.block
            condition_id = [string]$sample.condition_id
            campaign_run_id = $RunId
            environment_path = [string]$context.EnvironmentPath
            environment_sha256 = [string]$context.EnvironmentSha256
            git_commit = $GitCommit
            launch_nonce = [Guid]::NewGuid().ToString("D")
            launch_ordinal = [int]$sample.launch_ordinal
            output_root = $workerOutput
            plan_path = $planPath
            plan_sha256 = $planSha
            remote_ref = [string]$plan.remote_ref
            repository_url = [string]$plan.repository_url
            replica = [int]$sample.replica
            repository_root = $repository
            result_path = $resultPath
            run_id = $workerRunId
            sample_id = [string]$sample.sample_id
            scenario_id = [string]$condition.scenario_id
            scenario_path = Join-Path $repository ([string]$condition.scenario_path)
            scenario_sha256 = [string]$condition.scenario_sha256
            stage_root = $session.StageRoot
            working_path = $working
        }
        Write-M1DurableUtf8 -Session $session `
            -RelativePath $invocationRelative `
            -Text (ConvertTo-M3JsonText -Document $invocation)
        Add-M3ManifestEntry -Entries $entries -Session $session `
            -RelativePath $invocationRelative -Role "worker_invocation"
        $invocationPath = Get-M1PayloadPath -Session $session `
            -RelativePath $invocationRelative
        Invoke-M3Worker -Executable $winps32 -ScriptPath $workerPath `
            -InvocationPath $invocationPath `
            -TimeoutSeconds ([int]$plan.bounds.worker_timeout_seconds)

        $workerInput = Read-M3JsonBytes -Path $resultPath
        $workerResult = $workerInput.document
        $workerKeys = @(
            $workerResult.PSObject.Properties.Name | Sort-Object
        )
        $expectedWorkerKeys = @(
            "database_path", "launch_nonce", "operation_log", "process",
            "snapshot", "status"
        )
        if (
            ($workerKeys -join "`n") -cne (
                ($expectedWorkerKeys | Sort-Object) -join "`n"
            ) -or
            [string]$workerResult.status -cne "pass" -or
            [string]$workerResult.launch_nonce -cne
                [string]$invocation.launch_nonce
        ) {
            throw "M3 worker result did not pass."
        }
        $databasePath = [IO.Path]::GetFullPath(
            [string]$workerResult.database_path
        )
        $expectedDatabasePath = Join-Path $working (
            [string]$condition.scenario_id + ".mdb"
        )
        if (-not $databasePath.Equals(
            [IO.Path]::GetFullPath($expectedDatabasePath),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "M3 worker database path differs from its checked output."
        }
        Assert-M1NoReparseComponents -Path $databasePath
        $database = Get-Item -LiteralPath $databasePath -Force
        if (
            $database.PSIsContainer -or
            ($database.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $database.Length -lt 1 -or
            $database.Length -gt [long]$plan.bounds.max_database_bytes -or
            ($database.Length % 2048) -ne 0
        ) {
            throw "M3 worker database violates its physical bounds."
        }
        if ($database.LinkType -or $database.Target) {
            throw "M3 worker database cannot be a linked file."
        }
        $databaseSha = Get-M1FileSha256 -Path $databasePath
        $databaseRelative = "databases/$databaseSha.mdb"
        if (-not $retainedDatabases.ContainsKey($databaseRelative)) {
            Copy-M1DurableFile -Session $session `
                -SourcePath $databasePath -RelativePath $databaseRelative
            Add-M3ManifestEntry -Entries $entries -Session $session `
                -RelativePath $databaseRelative -Role "output_database"
            $retainedDatabases[$databaseRelative] = $true
        }
        $snapshotRelative = "$sampleRoot/dao-snapshot.json"
        Write-M1DurableUtf8 -Session $session -RelativePath $snapshotRelative `
            -Text (ConvertTo-M3JsonText -Document $workerResult.snapshot)
        Add-M3ManifestEntry -Entries $entries -Session $session `
            -RelativePath $snapshotRelative -Role "dao_snapshot"
        $logRelative = "$sampleRoot/operation-log.json"
        Write-M1DurableUtf8 -Session $session -RelativePath $logRelative `
            -Text (ConvertTo-M3JsonText -Document $workerResult.operation_log)
        Add-M3ManifestEntry -Entries $entries -Session $session `
            -RelativePath $logRelative -Role "operation_log"
        $record = [ordered]@{
            block = [int]$sample.block
            condition_id = [string]$sample.condition_id
            database = [ordered]@{
                path = $databaseRelative
                sha256 = $databaseSha
                size_bytes = [long]$database.Length
            }
            document_type = "dao_m3_sample_record"
            git_commit = $GitCommit
            invocation = [ordered]@{
                path = $invocationRelative
                sha256 = Get-M1FileSha256 -Path $invocationPath
            }
            launch_nonce = [string]$workerResult.launch_nonce
            launch_ordinal = [int]$sample.launch_ordinal
            operation_log = [ordered]@{
                path = $logRelative
                sha256 = Get-M1FileSha256 -Path (
                    Get-M1PayloadPath -Session $session -RelativePath $logRelative
                )
            }
            process = $workerResult.process
            protocol_version = "1.0.0"
            replica = [int]$sample.replica
            run_id = $RunId
            sample_id = [string]$sample.sample_id
            scenario_id = [string]$condition.scenario_id
            scenario_sha256 = [string]$condition.scenario_sha256
            snapshot = [ordered]@{
                path = $snapshotRelative
                sha256 = Get-M1FileSha256 -Path (
                    Get-M1PayloadPath -Session $session `
                        -RelativePath $snapshotRelative
                )
            }
            status = "pass"
            worker_run_id = $workerRunId
        }
        $recordRelative = "$sampleRoot/record.json"
        Write-M1DurableUtf8 -Session $session -RelativePath $recordRelative `
            -Text (ConvertTo-M3JsonText -Document $record)
        Add-M3ManifestEntry -Entries $entries -Session $session `
            -RelativePath $recordRelative -Role "sample_record"
        [void]$sampleResults.Add([ordered]@{
            database_sha256 = $databaseSha
            sample_id = [string]$sample.sample_id
            status = "pass"
        })
        Remove-M3WorkingSample -Path $working
    }

    $analysisOutput = Join-Path $session.WorkingPath "analysis-output"
    $analysisDetail = (
        & $context.PythonPath -B $contractPath analyze `
            $session.StagingBundle $analysisOutput 2>&1 | Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "M3 analysis failed: $analysisDetail"
    }
    Assert-M1NoReparseComponents -Path $analysisOutput
    $analysisDirectories = @(
        Get-ChildItem -LiteralPath $analysisOutput -Directory -Recurse
    )
    if (
        $analysisDirectories.Count -ne 1 -or
        $analysisDirectories[0].Name -cne "masks" -or
        ($analysisDirectories[0].Attributes -band (
            [IO.FileAttributes]::ReparsePoint
        )) -ne 0
    ) {
        throw "M3 analysis output directory shape differs."
    }
    $analysisFiles = @(
        Get-ChildItem -LiteralPath $analysisOutput -File -Recurse |
            Sort-Object FullName
    )
    $analysisBytes = [long]0
    if ($analysisFiles.Count -lt 2 -or $analysisFiles.Count -gt 64) {
        throw "M3 analysis output violates its file-count bound."
    }
    foreach ($file in $analysisFiles) {
        if (
            ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "M3 analysis output contains a reparse point."
        }
        $analysisBytes += [long]$file.Length
    }
    if ($analysisBytes -gt [long]$plan.bounds.max_analysis_bytes) {
        throw "M3 analysis output exceeds its aggregate byte ceiling."
    }
    foreach ($file in $analysisFiles) {
        $inside = $file.FullName.Substring($analysisOutput.Length + 1)
        $relative = "analysis/" + $inside.Replace("\", "/")
        Copy-M1DurableFile -Session $session -SourcePath $file.FullName `
            -RelativePath $relative
        $role = if ($relative.EndsWith(".bin")) {
            "analysis_mask"
        }
        else {
            "analysis_summary"
        }
        Add-M3ManifestEntry -Entries $entries -Session $session `
            -RelativePath $relative -Role $role
    }
    foreach ($file in ($analysisFiles | Sort-Object FullName -Descending)) {
        [IO.File]::Delete($file.FullName)
    }
    [IO.Directory]::Delete((Join-Path $analysisOutput "masks"), $false)
    [IO.Directory]::Delete($analysisOutput, $false)

    $report = [ordered]@{
        comparison_count = 18
        document_type = "dao_m3_report"
        environment_sha256 = [string]$context.EnvironmentSha256
        git_commit = $GitCommit
        plan_sha256 = $planSha
        remote_ref = [string]$plan.remote_ref
        repository_url = [string]$plan.repository_url
        run_id = $RunId
        sample_count = 9
        samples = @($sampleResults)
        status = "pass"
    }
    Write-M1DurableUtf8 -Session $session -RelativePath "report.json" `
        -Text (ConvertTo-M3JsonText -Document $report)
    Add-M3ManifestEntry -Entries $entries -Session $session `
        -RelativePath "report.json" -Role "report"

    $pathArray = [string[]]@($entries | ForEach-Object { [string]$_.path })
    [Array]::Sort($pathArray, [StringComparer]::Ordinal)
    $byPath = @{}
    foreach ($entry in $entries) { $byPath[[string]$entry.path] = $entry }
    $sortedEntries = @($pathArray | ForEach-Object { $byPath[$_] })
    $manifest = [ordered]@{
        created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        dirty = $false
        document_type = "dao_m3_campaign_manifest"
        files = $sortedEntries
        git_commit = $GitCommit
        plan = [ordered]@{ path = "plan.json"; sha256 = $planSha }
        protocol_version = "1.0.0"
        report_path = "report.json"
        run_id = $RunId
        status = "pass"
    }
    Write-M1DurableUtf8 -Session $session `
        -RelativePath "bundle-manifest.json" `
        -Text (ConvertTo-M3JsonText -Document $manifest)

    $validationBlock = {
        param($bundle)
        $detail = (
            & $context.PythonPath -B $contractPath bundle $bundle 2>&1 |
                Out-String
        ).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Staged M3 validation failed: $detail"
        }
    }
    $recheck = {
        param($stage)
        [void](Assert-M1RuntimeBinding -Context $context)
        Assert-M3RemoteCommit -Context $context `
            -RemoteRef ([string]$plan.remote_ref) `
            -RepositoryUrl ([string]$plan.repository_url)
        return $true
    }
    Publish-M1Stage -Stage $session -RecheckScriptBlock $recheck `
        -ValidationScriptBlock $validationBlock
    $session = $null
    Write-Output "PASS: retained M3 campaign at $OutputRoot\$GitCommit\$RunId"
    exit 0
}
catch {
    if ($null -ne $session) {
        try { Remove-M1PublicationStaging -Session $session } catch {}
    }
    [Console]::Error.WriteLine($_.Exception.GetType().FullName + ": " +
        $_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $context) {
        Close-M1PreflightContext -Context $context
    }
}
