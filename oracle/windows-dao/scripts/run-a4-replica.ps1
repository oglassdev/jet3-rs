[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
    [Parameter(Mandatory = $true)][string]$GitCommit,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica,
    [Parameter(Mandatory = $true)][string]$MatrixJobId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A4Stage = "bootstrap"
$script:A4RepositoryUrl = "https://github.com/oglassdev/jet3-rs.git"
$script:A4PlanSha256 = `
    "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
$script:A4RevisionPlanSha256 = `
    "3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1"
$script:A4ExperimentId = "DAO-A4-ROW-ANCHORED-MAPS-001"
$script:A4RequiredPlanPath = `
    "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json"

function Write-A4Failure {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Stage,
        [AllowNull()][object]$Message
    )
    $diagnostics = [IO.Path]::GetFullPath($Root)
    [void][IO.Directory]::CreateDirectory($diagnostics)
    $text = [Convert]::ToString($Message)
    if ($text.Length -gt 4000) { $text = $text.Substring(0, 4000) }
    $document = [ordered]@{ stage = $Stage; message = $text }
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        (($document | ConvertTo-Json -Depth 3 -Compress) + "`n")
    )
    $path = Join-Path $diagnostics "failure.json"
    $stream = New-Object IO.FileStream(
        $path, [IO.FileMode]::Create, [IO.FileAccess]::Write,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough
    )
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Assert-A4Bootstrap {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or $PSVersionTable.PSEdition -cne "Desktop" -or
        $PSVersionTable.PSVersion.Major -ne 5) {
        throw "A4 requires x86 Windows PowerShell 5 Desktop."
    }
    if ($Commit -cnotmatch "^[0-9a-f]{40}$") {
        throw "A4 producer commit must be lowercase SHA-1 hex."
    }
    if ($RunId -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,115}$" -or
        $MatrixJobId -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$") {
        throw "A4 run or matrix job identifier is not protocol-valid."
    }
    $entry = Join-Path $Repository `
        "oracle/windows-dao/scripts/run-a4-replica.ps1"
    if (-not ([IO.Path]::GetFullPath($PSCommandPath)).Equals(
        [IO.Path]::GetFullPath($entry), [StringComparison]::OrdinalIgnoreCase
    )) { throw "A4 entrypoint differs from its repository binding." }
    $cursor = [IO.Path]::GetFullPath($Repository)
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "A4 repository has a reparse component."
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -ceq $cursor) { break }
        $cursor = $parent
    }
    $git = [IO.Path]::GetFullPath((Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1).Source)
    $head = @(& $git -C $Repository rev-parse --verify HEAD 2>&1)
    $headExit = $LASTEXITCODE
    $dirty = @(& $git -C $Repository status --porcelain=v1 `
        --untracked-files=all 2>&1)
    $dirtyExit = $LASTEXITCODE
    $origin = @(& $git -C $Repository remote get-url origin 2>&1)
    $originExit = $LASTEXITCODE
    if ($headExit -ne 0 -or $head.Count -ne 1 -or
        [string]$head[0] -cne $Commit -or $dirtyExit -ne 0 -or
        $dirty.Count -ne 0 -or $originExit -ne 0 -or
        $origin.Count -ne 1 -or [string]$origin[0] -cne $script:A4RepositoryUrl) {
        throw "A4 requires the exact clean repository and origin binding."
    }
    return $git
}

function Open-A4BootstrapSources {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string[]]$RelativePaths
    )
    $streams = New-Object Collections.ArrayList
    try {
        foreach ($relative in $RelativePaths) {
            if ($relative -cnotmatch "^[A-Za-z0-9._/-]+$" -or
                $relative.Contains("..") -or $relative.Contains("//")) {
                throw "A4 bootstrap source locator is unsafe."
            }
            $path = [IO.Path]::GetFullPath((Join-Path $Repository $relative))
            $item = Get-Item -LiteralPath $path -Force
            if ($item.PSIsContainer -or $item.Length -gt 2MB -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "A4 bootstrap source is not a bounded ordinary file."
            }
            $expected = @(& $Git -C $Repository rev-parse `
                "${Commit}:$relative" 2>&1)
            $expectedExit = $LASTEXITCODE
            $actual = @(& $Git -C $Repository hash-object -- $path 2>&1)
            $actualExit = $LASTEXITCODE
            if ($expectedExit -ne 0 -or $actualExit -ne 0 -or
                $expected.Count -ne 1 -or $actual.Count -ne 1 -or
                [string]$actual[0] -cne [string]$expected[0]) {
                throw "A4 bootstrap source differs from the producer commit."
            }
            $stream = New-Object IO.FileStream(
                $path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                [IO.FileShare]::Read
            )
            [void]$streams.Add($stream)
        }
        return ,$streams.ToArray()
    }
    catch {
        foreach ($stream in $streams) { try { $stream.Dispose() } catch { } }
        throw
    }
}

function Read-A4JsonInput {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$MaximumBytes = 64MB
    )
    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -lt 2 -or
        $item.Length -gt $MaximumBytes) {
        throw "A4 JSON input violates its byte or file bound."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length -or
        ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
            $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf)) {
        throw "A4 JSON input changed or contains a BOM."
    }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    return [pscustomobject]@{
        bytes = $bytes; document = ($text | ConvertFrom-Json)
        sha256 = Get-M1ByteArraySha256 -Bytes $bytes
    }
}

function Write-A4NewFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [long]$MaximumBytes = 64MB
    )
    if ($Bytes.Length -lt 1 -or $Bytes.Length -gt $MaximumBytes) {
        throw "A4 output violates its artifact byte ceiling."
    }
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    [void][IO.Directory]::CreateDirectory($parent)
    Assert-M1NoReparseComponents -Path $parent
    $stream = New-Object IO.FileStream(
        $Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 65536, [IO.FileOptions]::WriteThrough
    )
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Invoke-A4PythonValidation {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$Validator,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    [void](Invoke-BoundedChildProcess -Executable $Context.PythonPath `
        -Arguments @("-B", $Validator, $Path) -CallerLabel $Label `
        -TimeoutSeconds 120 -MaximumOutputBytes 1MB)
}

function Assert-A4PythonRuntime {
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)
    $result = Invoke-BoundedChildProcess -Executable $Context.PythonPath `
        -Arguments @("-B", "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))") `
        -CallerLabel "A4 Python runtime" -TimeoutSeconds 30 `
        -MaximumOutputBytes 1KB
    $text = ([Convert]::ToString($result.stdout)).Trim()
    if ($text -cnotmatch "^3\.13\.[0-9]+$" -or
        [string]$Context.Python.Version -cne $text) {
        throw "A4 requires the preflight-bound Python 3.13 runtime."
    }
    return $text
}

function Assert-A4ExactPushedCommit {
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)
    $remote = Invoke-BoundedChildProcess -Executable $Context.GitExecutable `
        -Arguments @(
            "-c", "credential.interactive=never", "-c", "core.askPass=",
            "ls-remote", "--heads", $script:A4RepositoryUrl
        ) -CallerLabel "A4 pushed commit binding" -TimeoutSeconds 30 `
        -MaximumOutputBytes 1MB
    $advertised = @([Convert]::ToString($remote.stdout) -split "\r?\n" | Where-Object {
        $_.StartsWith($Context.GitCommit + "`t", [StringComparison]::Ordinal)
    })
    if ($advertised.Count -lt 1) {
        throw "A4 producer commit is not advertised by a pushed branch."
    }
}

function Invoke-A4ProviderProbe {
    param(
        [Parameter(Mandatory = $true)][string]$PowerShellPath,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$ProbePath
    )
    if ([IO.File]::Exists($ProbePath)) {
        throw "A4 provider probe output already exists."
    }
    $scriptPath = Join-Path $Repository "oracle/windows-dao/scripts/probe-provider.ps1"
    [void](Invoke-BoundedChildProcess -Executable $PowerShellPath `
        -Arguments @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "Bypass", "-File", $scriptPath, "-ProtocolVersion", "1.1.0",
            "-OutputPath", $ProbePath
        ) -CallerLabel "A4 DAO provider probe" -TimeoutSeconds 120 `
        -MaximumOutputBytes 1MB)
}

function New-A4EnvironmentBytes {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$CampaignId,
        [Parameter(Mandatory = $true)][string]$PythonVersion
    )
    $accepted = $Context.AcceptedProvider
    if ($null -eq ("Jet3.A4.NativeCodePages" -as [type])) {
        Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
namespace Jet3.A4 {
    public static class NativeCodePages {
        [DllImport("kernel32.dll")]
        public static extern uint GetACP();
        [DllImport("kernel32.dll")]
        public static extern uint GetOEMCP();
    }
}
"@
    }
    $ansiCodePage = [int][Jet3.A4.NativeCodePages]::GetACP()
    $oemCodePage = [int][Jet3.A4.NativeCodePages]::GetOEMCP()
    if ($ansiCodePage -ne 1252 -or $oemCodePage -lt 1 -or
        $oemCodePage -gt 65535) {
        throw "A4 requires Windows GetACP() to return 1252."
    }
    $runnerImage = [Environment]::GetEnvironmentVariable("ImageOS")
    if ([string]::IsNullOrWhiteSpace($runnerImage)) {
        $runnerImage = [Environment]::OSVersion.VersionString
    }
    if ($runnerImage.Length -gt 128) { $runnerImage = $runnerImage.Substring(0, 128) }
    $document = [ordered]@{
        protocol_version = "1.0.0"; document_type = "dao_a4_environment"
        experiment_id = $script:A4ExperimentId
        plan_sha256 = $script:A4PlanSha256
        revision_plan_sha256 = $script:A4RevisionPlanSha256
        producer_commit = $Context.GitCommit
        repository_url = $script:A4RepositoryUrl; campaign_id = $CampaignId
        replica = $Replica; matrix_job_id = $MatrixJobId; status = "ready"
        host = [ordered]@{
            windows_version = [string]$Context.Environment.host.os_version
            process_architecture = "x86"
            powershell_version = $PSVersionTable.PSVersion.ToString()
            python_version = $PythonVersion; runner_image = $runnerImage
            windows_ansi_code_page = $ansiCodePage
            windows_oem_code_page = $oemCodePage
            locale_name = [Globalization.CultureInfo]::CurrentCulture.Name
        }
        provider = [ordered]@{
            prog_id = [string]$accepted.prog_id; clsid = [string]$accepted.clsid
            provider_version = [string]$accepted.provider_version
            server_path = [string]$accepted.server_path
            server_file_version = [string]$accepted.server_file_version
            server_sha256 = [string]$accepted.server_sha256
        }
    }
    return ,(New-Object Text.UTF8Encoding($false)).GetBytes(
        (($document | ConvertTo-Json -Depth 8 -Compress) + "`n")
    )
}

function Assert-A4PlanIdentity {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    if ([string]$Plan.experiment_id -cne $script:A4ExperimentId -or
        [string]$Plan.document_type -cne "dao_a4_row_anchored_maps_plan" -or
        [string]$Plan.implementation_rebinding.required_experiment_id -cne
            $script:A4ExperimentId -or
        [string]$Plan.implementation_rebinding.required_plan_path -cne
            $script:A4RequiredPlanPath) {
        throw "A4 rejects any plan other than the frozen row-anchored-maps plan."
    }
}

function Assert-A4RuntimeGate {
    param([Parameter(Mandatory = $true)][pscustomobject]$Plan)
    $expected = @(
        "checked_a4_analyzer_and_synthetic_generator",
        "independent_recomputing_a4_validator",
        "a4_worker_and_workflow_with_fail_closed_binding",
        "passing_a4_dry_runs_disclosed_additively",
        "a4_contract_validator_accepts_decisive_and_no_outcome_reports",
        "exact_clean_pushed_producer_commit",
        "licensed_x86_dao_host_with_ansi_code_page_1252"
    )
    $actual = @($Plan.execution_gate.blocking_requirements)
    if ([string]$Plan.execution_gate.status -cne "BLOCKED" -or
        $actual.Count -ne $expected.Count -or
        [int]$Plan.bounds.worker_timeout_seconds_per_replica -ne 1700 -or
        [int]$Plan.checkpoint_design.count -ne 25) {
        throw "A4 preregistration runtime gate or worker bounds drifted."
    }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ([string]$actual[$index] -cne [string]$expected[$index]) {
            throw "A4 preregistration blocking-requirement set drifted."
        }
    }
}

function Assert-A4ReplicaOutput {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Validator,
        [Parameter(Mandatory = $true)][string]$EnvironmentSha256,
        [Parameter(Mandatory = $true)][string]$CampaignId
    )
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $replicaId = "replica-{0:D2}" -f $Replica
    $environmentRelative = "environment/$replicaId.json"
    $observationRelative = "observations/$replicaId.json"
    $manifestRelative = "replica-artifacts/$replicaId-manifest.json"
    $allowedDirectories = @(
        "environment", "observations", "replica-artifacts", "page-indexes",
        "page-indexes/$replicaId", "page-store", "schema-snapshots",
        "schema-snapshots/$replicaId"
    )
    $directories = @([IO.Directory]::EnumerateDirectories(
        $rootPath, "*", [IO.SearchOption]::AllDirectories
    ) | ForEach-Object {
        $_.Substring($rootPath.Length + 1).Replace('\', '/')
    } | Sort-Object)
    if ($directories.Count -ne $allowedDirectories.Count) {
        throw "A4 output directory inventory is not closed."
    }
    foreach ($directory in $directories) {
        if ($directory -cnotin $allowedDirectories) {
            throw "A4 output contains an unexpected directory."
        }
    }
    $manifestPath = Join-Path $rootPath $manifestRelative.Replace('/', '\')
    $manifestInput = Read-A4JsonInput -Path $manifestPath
    $manifest = $manifestInput.document
    $records = @($manifest.files)
    $recordByPath = @{}
    foreach ($record in $records) {
        $relative = [string]$record.path
        if ($recordByPath.ContainsKey($relative)) {
            throw "A4 manifest contains a duplicate path."
        }
        $recordByPath[$relative] = $record
    }
    $files = @([IO.Directory]::EnumerateFiles(
        $rootPath, "*", [IO.SearchOption]::AllDirectories
    ))
    if ($files.Count -ne ($records.Count + 1)) {
        throw "A4 output file inventory differs from its manifest."
    }
    foreach ($path in $files) {
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "A4 output artifact is not a bounded ordinary file."
        }
        $relative = $path.Substring($rootPath.Length + 1).Replace('\', '/')
        if ($relative -ceq $manifestRelative) { continue }
        if (-not $recordByPath.ContainsKey($relative)) {
            throw "A4 output contains an unmanifested artifact."
        }
        $record = $recordByPath[$relative]
        if ([long]$record.size_bytes -ne [long]$item.Length -or
            [string]$record.sha256 -cne (Get-M1FileSha256 -Path $path)) {
            throw "A4 artifact differs from its manifest hash or size."
        }
        if ([string]$record.role -ceq "page_blob") {
            if ($item.Length -ne 2048 -or
                [IO.Path]::GetFileNameWithoutExtension($path) -cne
                [string]$record.sha256) {
                throw "A4 page-store blob is not canonical content-addressed data."
            }
        }
    }
    $environmentPath = Join-Path $rootPath $environmentRelative.Replace('/', '\')
    $observationPath = Join-Path $rootPath $observationRelative.Replace('/', '\')
    $environment = (Read-A4JsonInput -Path $environmentPath).document
    $observation = (Read-A4JsonInput -Path $observationPath).document
    foreach ($document in @($environment, $observation, $manifest)) {
        if ([string]$document.plan_sha256 -cne $script:A4PlanSha256 -or
            [string]$document.revision_plan_sha256 -cne
                $script:A4RevisionPlanSha256 -or
            [string]$document.producer_commit -cne $Context.GitCommit -or
            [string]$document.campaign_id -cne $CampaignId -or
            [int]$document.replica -ne $Replica) {
            throw "A4 output document differs from its campaign binding."
        }
    }
    if ([string]$manifest.environment_sha256 -cne $EnvironmentSha256 -or
        [string]$recordByPath[$environmentRelative].sha256 -cne
            $EnvironmentSha256 -or
        [string]$observation.environment_sha256 -cne $EnvironmentSha256 -or
        [string]$manifest.provider_sha256 -cne
            [string]$environment.provider.server_sha256 -or
        [string]$observation.provider_sha256 -cne
            [string]$environment.provider.server_sha256) {
        throw "A4 output environment or provider binding drifted."
    }
    if ([string]$environment.matrix_job_id -cne $MatrixJobId -or
        [string]$manifest.matrix_job_id -cne $MatrixJobId -or
        [string]$observation.matrix_job.job_id -cne $MatrixJobId) {
        throw "A4 output matrix-job binding drifted."
    }
    $checkpoints = @($observation.checkpoints)
    if ($checkpoints.Count -ne 25) { throw "A4 observation checkpoint count drifted." }
    $changedEntries = [long]0
    foreach ($checkpoint in $checkpoints) {
        $relative = [string]$checkpoint.page_index.path
        if (-not $recordByPath.ContainsKey($relative)) {
            throw "A4 checkpoint references an unmanifested page index."
        }
        $indexPath = Join-Path $rootPath $relative.Replace('/', '\')
        $indexInput = Read-A4JsonInput -Path $indexPath
        if ($indexInput.sha256 -cne [string]$checkpoint.page_index.sha256 -or
            $indexInput.bytes.Length -ne [long]$checkpoint.page_index.size_bytes) {
            throw "A4 checkpoint page-index reference is not byte-exact."
        }
        $indexDocument = $indexInput.document
        if ([string]$indexDocument.plan_sha256 -cne $script:A4PlanSha256 -or
            [string]$indexDocument.revision_plan_sha256 -cne
                $script:A4RevisionPlanSha256 -or
            [string]$indexDocument.producer_commit -cne $Context.GitCommit -or
            [string]$indexDocument.campaign_id -cne $CampaignId -or
            [string]$indexDocument.environment_sha256 -cne $EnvironmentSha256 -or
            [string]$indexDocument.provider_sha256 -cne
                [string]$environment.provider.server_sha256 -or
            [int]$indexDocument.replica -ne $Replica) {
            throw "A4 page index differs from its campaign binding."
        }
        $hashes = @($indexDocument.ordered_page_sha256)
        if ($hashes.Count -ne [long]$indexDocument.page_count) {
            throw "A4 ordered page hash count differs from page_count."
        }
        foreach ($digest in $hashes) {
            $blob = "page-store/$digest.page"
            if (-not $recordByPath.ContainsKey($blob)) {
                throw "A4 snapshot cannot be reconstructed from its page store."
            }
        }
        $changedEntries += @($indexDocument.changed_page_indices).Count
        Invoke-A4PythonValidation -Context $Context -Validator $Validator `
            -Path $indexPath -Label "A4 page-index schema validation"
        $schemaRelative = [string]$checkpoint.dao_schema_snapshot.path
        if (-not $recordByPath.ContainsKey($schemaRelative)) {
            throw "A4 checkpoint references an unmanifested DAO schema snapshot."
        }
        $schemaPath = Join-Path $rootPath $schemaRelative.Replace('/', '\')
        $schemaInput = Read-A4JsonInput -Path $schemaPath
        if ($schemaInput.sha256 -cne
                [string]$checkpoint.dao_schema_snapshot.sha256 -or
            $schemaInput.bytes.Length -ne
                [long]$checkpoint.dao_schema_snapshot.size_bytes) {
            throw "A4 checkpoint schema-snapshot reference is not byte-exact."
        }
        Invoke-A4PythonValidation -Context $Context -Validator $Validator `
            -Path $schemaPath -Label "A4 DAO schema-snapshot validation"
    }
    if ($changedEntries -ne [long]$observation.changed_hash_entries -or
        $changedEntries -gt 65536) {
        throw "A4 changed-page accounting differs from the observation."
    }
    foreach ($validation in @(
        [pscustomobject]@{ path = $environmentPath; label = "A4 environment validation" },
        [pscustomobject]@{ path = $observationPath; label = "A4 observation validation" },
        [pscustomobject]@{ path = $manifestPath; label = "A4 replica manifest validation" }
    )) {
        Invoke-A4PythonValidation -Context $Context -Validator $Validator `
            -Path $validation.path -Label $validation.label
    }
}

function Remove-A4PrivateWorkingRoot {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][string]$Diagnostics
    )
    $working = [IO.Path]::GetFullPath($WorkingRoot)
    $expected = Join-Path ([IO.Path]::GetFullPath($Diagnostics)) "private"
    if (-not $working.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing A4 cleanup outside its private diagnostics boundary."
    }
    if (-not [IO.Directory]::Exists($working)) { return }
    Assert-M1NoReparseComponents -Path $working
    Assert-M1CleanupTreeBounded -Root $working -MaxBytes 256MB -MaxEntries 64
    [IO.Directory]::Delete($working, $true)
}

function Move-A4FailedDatabase {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][string]$Diagnostics,
        [Parameter(Mandatory = $true)][int]$Replica
    )
    $working = [IO.Path]::GetFullPath($WorkingRoot)
    $diagnosticsPath = [IO.Path]::GetFullPath($Diagnostics)
    $expected = Join-Path $diagnosticsPath "private"
    if (-not $working.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing A4 failed-database retention outside its private boundary."
    }
    $replicaRoot = Join-Path $working ("replica-{0:D2}" -f $Replica)
    $source = Join-Path $replicaRoot "ACQUISITION.MDB"
    if (-not [IO.File]::Exists($source)) { return }
    Assert-M1NoReparseComponents -Path $source
    $item = Get-Item -LiteralPath $source -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 1 -or $item.Length -gt 128MB) {
        throw "A4 failed database is not a bounded ordinary file."
    }
    $destination = Join-Path $diagnosticsPath `
        ("failed-replica-{0:D2}.mdb" -f $Replica)
    if ([IO.File]::Exists($destination) -or
        [IO.Directory]::Exists($destination)) {
        throw "A4 failed-database diagnostic already exists."
    }
    [IO.File]::Move($item.FullName, $destination)
}

function Invoke-A4ReplicaCampaign {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Output,
        [Parameter(Mandatory = $true)][string]$Diagnostics,
        [Parameter(Mandatory = $true)][string]$PowerShellPath,
        [Parameter(Mandatory = $true)][string[]]$ExecutedSources
    )
    $context = $null
    $working = Join-Path $Diagnostics "private"
    $primary = $null
    $secondaryErrors = New-Object Collections.ArrayList
    try {
        $script:A4Stage = "output-preflight"
        [void][IO.Directory]::CreateDirectory($Output)
        [void][IO.Directory]::CreateDirectory($Diagnostics)
        Assert-M1NoReparseComponents -Path $Output
        Assert-M1NoReparseComponents -Path $Diagnostics
        if ((Test-M1PathWithin -Path $Output -Parent $Repository) -or
            (Test-M1PathWithin -Path $Diagnostics -Parent $Repository) -or
            (Test-M1PathWithin -Path $Diagnostics -Parent $Output)) {
            throw "A4 output and diagnostics must remain outside the repository and separate."
        }
        if (@([IO.Directory]::EnumerateFileSystemEntries($Output)).Count -ne 0) {
            throw "A4 OutputRoot must be empty."
        }
        $script:A4Stage = "plan-identity"
        $planPath = Join-Path $Repository $script:A4RequiredPlanPath
        $planInput = Read-A4JsonInput -Path $planPath -MaximumBytes 1MB
        if ($planInput.sha256 -cne $script:A4PlanSha256) {
            throw "A4 plan bytes differ from the frozen preregistration."
        }
        Assert-A4PlanIdentity -Plan $planInput.document
        $probePath = Join-Path $Diagnostics "provider-environment.json"
        $script:A4Stage = "provider-probe"
        Invoke-A4ProviderProbe -PowerShellPath $PowerShellPath `
            -Repository $Repository -ProbePath $probePath
        $preflightOutput = Join-Path $Diagnostics "preflight-output"
        [void][IO.Directory]::CreateDirectory($preflightOutput)
        $preflightRunId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") +
            "-a4-r$Replica"
        $script:A4Stage = "preflight"
        $context = Invoke-M1Preflight -RepositoryRoot $Repository `
            -EnvironmentPath $probePath -OutputRoot $preflightOutput `
            -GitCommit $GitCommit -RunId $preflightRunId `
            -ExecutedRepoRelativeSourcePaths $ExecutedSources
        if ([string]$context.AcceptedProvider.prog_id -cne "DAO.DBEngine.36" -or
            [string]$context.AcceptedProvider.server_path -match '"' -or
            [string]$context.ProviderSha256 -cne
                [string]$context.AcceptedProvider.server_sha256) {
            throw "A4 requires one quote-free, hash-bound DAO.DBEngine.36 provider."
        }
        $pythonVersion = Assert-A4PythonRuntime -Context $context
        Assert-A4ExactPushedCommit -Context $context
        [void](Assert-M1RuntimeBinding -Context $context)
        $validator = Join-Path $Repository "oracle/windows-dao/scripts/a4_spec.py"
        Invoke-A4PythonValidation -Context $context -Validator $validator `
            -Path $planPath -Label "A4 immutable plan validation"
        Assert-A4RuntimeGate -Plan $planInput.document
        $campaignId = "a4-run-$RunId"
        if ($campaignId.Length -gt 128) { throw "A4 campaign id exceeds its bound." }
        foreach ($directory in @(
            "environment", "observations", "replica-artifacts", "page-indexes",
            ("page-indexes/replica-{0:D2}" -f $Replica), "page-store",
            "schema-snapshots",
            ("schema-snapshots/replica-{0:D2}" -f $Replica)
        )) {
            [void][IO.Directory]::CreateDirectory((Join-Path $Output $directory))
        }
        $environmentBytes = New-A4EnvironmentBytes -Context $context `
            -CampaignId $campaignId -PythonVersion $pythonVersion
        $environmentSha = Get-M1ByteArraySha256 -Bytes $environmentBytes
        $environmentPath = Join-Path $Output `
            ("environment/replica-{0:D2}.json" -f $Replica)
        Write-A4NewFile -Path $environmentPath -Bytes $environmentBytes
        Invoke-A4PythonValidation -Context $context -Validator $validator `
            -Path $environmentPath -Label "A4 environment validation"
        [void][IO.Directory]::CreateDirectory($working)
        [void](New-A4ProgressFile -DiagnosticsRoot $Diagnostics -Replica $Replica)
        $workerPath = Join-Path $Repository `
            "oracle/windows-dao/scripts/a4/A4.Worker.ps1"
        $script:A4Stage = "replica-worker"
        [void](Invoke-BoundedChildProcess -Executable $PowerShellPath `
            -Arguments @(
                "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", $workerPath, "-RepositoryRoot", $Repository,
                "-OutputRoot", $Output, "-WorkingRoot", $working,
                "-DiagnosticsRoot", $Diagnostics, "-PlanPath", $planPath,
                "-EnvironmentPath", $environmentPath,
                "-ProducerCommit", $GitCommit, "-CampaignId", $campaignId,
                "-MatrixJobId", $MatrixJobId, "-PlanSha256", $script:A4PlanSha256,
                "-RevisionPlanSha256", $script:A4RevisionPlanSha256,
                "-EnvironmentSha256", $environmentSha,
                "-Replica", $Replica.ToString()
            ) -CallerLabel "A4 replica worker" -TimeoutSeconds 1700 `
            -MaximumOutputBytes 1MB -ReviewedTimeoutCeilingSeconds 1700)
        $script:A4Stage = "self-verification"
        Assert-A4ReplicaOutput -Context $context -Root $Output `
            -Validator $validator -EnvironmentSha256 $environmentSha `
            -CampaignId $campaignId
        [void](Assert-M1RuntimeBinding -Context $context)
        Assert-A4ExactPushedCommit -Context $context
    }
    catch { $primary = $_ }
    finally {
        $removePrivate = $true
        if ($null -ne $primary) {
            try {
                Move-A4FailedDatabase -WorkingRoot $working `
                    -Diagnostics $Diagnostics -Replica $Replica
            }
            catch {
                $removePrivate = $false
                [void]$secondaryErrors.Add(
                    "A4 failed-database retention also failed: " +
                    [Convert]::ToString($_.Exception.Message)
                )
            }
        }
        if ($removePrivate -and [IO.Directory]::Exists($working)) {
            try {
                Remove-A4PrivateWorkingRoot -WorkingRoot $working `
                    -Diagnostics $Diagnostics
            }
            catch {
                [void]$secondaryErrors.Add(
                    "A4 private cleanup also failed: " +
                    [Convert]::ToString($_.Exception.Message)
                )
            }
        }
        if ($null -ne $context) {
            try { Close-M1PreflightContext -Context $context }
            catch {
                [void]$secondaryErrors.Add(
                    "A4 preflight cleanup also failed: " +
                    [Convert]::ToString($_.Exception.Message)
                )
            }
        }
    }
    if ($null -ne $primary) {
        if ($secondaryErrors.Count -ne 0) {
            $detail = @($secondaryErrors) -join " "
            if ($detail.Length -gt 2000) { $detail = $detail.Substring(0, 2000) }
            throw ([Convert]::ToString($primary.Exception.Message) + " " + $detail)
        }
        throw $primary
    }
    if ($secondaryErrors.Count -ne 0) {
        throw (@($secondaryErrors) -join " ")
    }
}

$streams = @()
try {
    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    $diagnostics = [IO.Path]::GetFullPath($DiagnosticsRoot)
    [void][IO.Directory]::CreateDirectory($diagnostics)
    $git = Assert-A4Bootstrap -Repository $repository -Commit $GitCommit
    $sources = @(
        "oracle/windows-dao/scripts/run-a4-replica.ps1",
        "oracle/windows-dao/scripts/a4/A4.Worker.ps1",
        "oracle/windows-dao/scripts/a4/A4.PageStore.ps1",
        "oracle/windows-dao/scripts/a4/A4.Progress.ps1",
        "oracle/windows-dao/scripts/a4/A4.SchemaSnapshot.ps1",
        "oracle/windows-dao/scripts/a2/A2.Progress.ps1",
        "oracle/windows-dao/scripts/a1/A1.PageStore.ps1",
        "oracle/windows-dao/scripts/a4_spec.py",
        "oracle/windows-dao/scripts/protocol_validation.py",
        "oracle/windows-dao/scripts/probe-provider.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
        "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
        "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
        "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
        "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1",
        "oracle/windows-dao/scripts/m1_bundle_validation.py",
        "oracle/windows-dao/scripts/protocol_cli.py",
        "oracle/windows-dao/scripts/validate_m1_protocol.py",
        "oracle/windows-dao/examples/m1-inventory.json",
        "oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json",
        "oracle/windows-dao/experiments/a4/plan.schema.json",
        "oracle/windows-dao/experiments/a4/replica-observation.schema.json",
        "oracle/windows-dao/experiments/a4/dao-schema-snapshot.schema.json",
        "oracle/windows-dao/experiments/a4/page-index.schema.json",
        "oracle/windows-dao/experiments/a4/replica-artifact-manifest.schema.json",
        "oracle/windows-dao/experiments/a4/environment.schema.json",
        "oracle/windows-dao/experiments/a4/analysis-report.schema.json",
        "oracle/windows-dao/experiments/a4/bundle-manifest.schema.json",
        "oracle/windows-dao/experiments/a4/dry-run-report.schema.json",
        "oracle/windows-dao/experiments/a4/holdout-structure-receipt.schema.json",
        "oracle/windows-dao/experiments/a4/derivation-candidates.schema.json",
        "oracle/windows-dao/experiments/a4/h4-occurrence-evidence.schema.json",
        "oracle/windows-dao/experiments/a4/independent-validation-report.schema.json"
    )
    $streams = @(Open-A4BootstrapSources -Repository $repository `
        -Commit $GitCommit -Git $git -RelativePaths $sources)
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m1/M1.Preflight.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m1/M1.Publication.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/a4/A4.Progress.ps1")
    $powerShellPath = [IO.Path]::GetFullPath(
        [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    )
    Invoke-A4ReplicaCampaign -Repository $repository `
        -Output ([IO.Path]::GetFullPath($OutputRoot)) `
        -Diagnostics $diagnostics -PowerShellPath $powerShellPath `
        -ExecutedSources $sources
    Write-Output ("PASS: retained A4 replica {0:D2}" -f $Replica)
    exit 0
}
catch {
    $failure = $_
    try {
        Write-A4Failure -Root $DiagnosticsRoot -Stage $script:A4Stage `
            -Message $failure.Exception.Message
    }
    catch {
        [Console]::Error.WriteLine(
            "A4 failure diagnostic retention also failed: " + $_.Exception.Message
        )
    }
    [Console]::Error.WriteLine(
        $failure.Exception.GetType().FullName + ": " + $failure.Exception.Message
    )
    exit 1
}
finally {
    foreach ($stream in $streams) { try { $stream.Dispose() } catch { } }
}
