[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InvocationPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-M3BoundedJson {
    param([string]$Path, [long]$MaximumBytes = 1MB)

    $item = Get-Item -LiteralPath $Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 1 -or
        $item.Length -gt $MaximumBytes
    ) {
        throw "M3 worker input violates its file or byte bound."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length) {
        throw "M3 worker input changed while being read."
    }
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    $text = $encoding.GetString($bytes)
    if ($text[0] -eq [char]0xfeff) {
        throw "M3 worker JSON cannot contain a byte-order mark."
    }
    return [ordered]@{
        bytes = $bytes
        document = $text | ConvertFrom-Json
    }
}

function Assert-M3WorkerPath {
    param([string]$Path, [string]$StageRoot)

    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($StageRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "M3 worker path escapes the private stage."
    }
    return $full
}

function Write-M3WorkerResult {
    param([string]$Path, [object]$Value)

    $json = $Value | ConvertTo-Json -Depth 100 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json + "`n")
    $stream = New-Object IO.FileStream(
        $Path,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

$context = $null
try {
    $invocationInput = Read-M3BoundedJson -Path $InvocationPath
    $invocation = $invocationInput.document
    $required = @(
        "block", "campaign_run_id", "condition_id", "environment_path",
        "environment_sha256", "git_commit",
        "launch_nonce", "launch_ordinal", "output_root", "plan_path",
        "plan_sha256", "remote_ref", "replica", "repository_root",
        "repository_url",
        "result_path", "run_id", "sample_id", "scenario_id",
        "scenario_path", "scenario_sha256", "stage_root", "working_path"
    )
    $actual = @($invocation.PSObject.Properties.Name | Sort-Object)
    if (($actual -join "`n") -cne (($required | Sort-Object) -join "`n")) {
        throw "M3 worker invocation keys differ from the checked contract."
    }
    $repository = [IO.Path]::GetFullPath([string]$invocation.repository_root)
    $stageRoot = [IO.Path]::GetFullPath([string]$invocation.stage_root)
    $working = Assert-M3WorkerPath -Path ([string]$invocation.working_path) `
        -StageRoot $stageRoot
    $resultPath = Assert-M3WorkerPath -Path ([string]$invocation.result_path) `
        -StageRoot $stageRoot
    if (-not (Test-Path -LiteralPath $working -PathType Container)) {
        throw "M3 worker directory is absent."
    }
    if (Get-ChildItem -LiteralPath $working -Force) {
        throw "M3 worker directory is not empty."
    }

    $moduleRoot = Join-Path $repository "oracle/windows-dao/scripts/m1"
    . (Join-Path $moduleRoot "M1.Preflight.ps1")
    . (Join-Path $moduleRoot "M1.Dao.ps1")
    $executedSources = @(
        "oracle/windows-dao/scripts/run-m3-sample.ps1",
        "oracle/windows-dao/scripts/run-m3-controlled.ps1",
        "oracle/windows-dao/scripts/m3_contract.py",
        "oracle/windows-dao/scripts/m3_analysis.py",
        "oracle/windows-dao/scripts/m3_experiment.py",
        "oracle/windows-dao/scripts/m3/M3.Process.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
        "oracle/windows-dao/experiments/m3/plan.schema.json",
        "oracle/windows-dao/experiments/m3/m3-index-isolation.plan.json",
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
        "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
        "oracle/windows-dao/scripts/m1/M1.Dao.ps1",
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1"
    )
    $context = Invoke-M1Preflight `
        -RepositoryRoot $repository `
        -EnvironmentPath ([string]$invocation.environment_path) `
        -OutputRoot ([string]$invocation.output_root) `
        -GitCommit ([string]$invocation.git_commit) `
        -RunId ([string]$invocation.run_id) `
        -ExecutedRepoRelativeSourcePaths $executedSources
    Assert-M1ByteArraySha256 -Bytes $context.EnvironmentBytes `
        -ExpectedSha256 ([string]$invocation.environment_sha256) `
        -Label "M3 environment"
    $invocationValidation = (
        & $context.PythonPath -B (
            Join-Path $repository "oracle/windows-dao/scripts/m3_contract.py"
        ) invocation $InvocationPath 2>&1 | Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "M3 worker invocation validation failed: $invocationValidation"
    }

    $plan = Read-M3BoundedJson -Path ([string]$invocation.plan_path)
    Assert-M1ByteArraySha256 -Bytes $plan.bytes `
        -ExpectedSha256 ([string]$invocation.plan_sha256) `
        -Label "M3 checked plan"
    $scenario = Read-M3BoundedJson -Path ([string]$invocation.scenario_path)
    Assert-M1ByteArraySha256 -Bytes $scenario.bytes `
        -ExpectedSha256 ([string]$invocation.scenario_sha256) `
        -Label "M3 checked scenario"
    if (
        [string]$scenario.document.scenario_id -cne
            [string]$invocation.scenario_id
    ) {
        throw "M3 worker scenario ID differs from its checked condition."
    }

    $origin = @(
        & $context.GitExecutable -C $repository remote get-url origin 2>&1
    )
    if (
        $LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne [string]$invocation.repository_url
    ) {
        throw "M3 worker origin differs from the checked private repository."
    }
    $remoteOutput = @(
        & $context.GitExecutable -C $repository ls-remote --heads `
            ([string]$invocation.repository_url) `
            ([string]$invocation.remote_ref) 2>&1
    )
    if (
        $LASTEXITCODE -ne 0 -or
        $remoteOutput.Count -ne 1 -or
        -not ([string]$remoteOutput[0]).StartsWith(
            ([string]$invocation.git_commit) + "`t",
            [StringComparison]::Ordinal
        )
    ) {
        throw "M3 worker could not prove the exact pushed remote commit."
    }

    [void](Assert-M1RuntimeBinding -Context $context)
    $process = Get-Process -Id $PID
    $execution = Invoke-M1DaoScenario `
        -Scenario $scenario.document `
        -AcceptedProvider $context.AcceptedProvider `
        -WorkingRoot $working `
        -GitCommit ([string]$invocation.git_commit) `
        -RunId ([string]$invocation.run_id)
    if ([string]$execution.status -cne "pass") {
        throw "M3 worker DAO scenario did not pass: $($execution.reason)"
    }
    [void](Assert-M1RuntimeBinding -Context $context)
    $result = [ordered]@{
        database_path = [string]$execution.database_path
        launch_nonce = [string]$invocation.launch_nonce
        operation_log = $execution.operation_log
        process = [ordered]@{
            architecture = [string]$context.Environment.host.process_architecture
            id = [int]$PID
            powershell_version = $PSVersionTable.PSVersion.ToString()
            provider_clsid = [string]$context.AcceptedProvider.clsid
            provider_prog_id = [string]$context.AcceptedProvider.prog_id
            provider_server_path = [string]$context.ProviderPath
            provider_server_sha256 = [string]$context.ProviderSha256
            started_at_utc = $process.StartTime.ToUniversalTime().ToString("o")
        }
        snapshot = $execution.snapshot
        status = "pass"
    }
    Write-M3WorkerResult -Path $resultPath -Value $result
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.GetType().FullName + ": " +
        $_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $context) {
        Close-M1PreflightContext -Context $context
    }
}
