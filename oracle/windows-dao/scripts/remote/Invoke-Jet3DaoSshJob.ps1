[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Bootstrap", "Execute")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidateSet("provider-probe", "m1-controlled")]
    [string]$Job,
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [Parameter(Mandatory = $true)]
    [string]$GitCommit,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [string]$RemoteRoot,
    [int]$TimeoutSeconds = 120,
    [long]$MaximumOutputBytes = 1MB,
    [long]$MaximumArtifactBytes = 300MB,
    [string]$RunDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:GIT_TERMINAL_PROMPT = "0"

$remoteModule = Join-Path $PSScriptRoot "Remote.Process.ps1"
. $remoteModule

function Assert-Jet3RemoteInputs {
    if ($GitCommit -cnotmatch "^[0-9a-f]{40}$") {
        throw "GitCommit must be 40 lowercase hexadecimal digits."
    }
    if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$") {
        throw "RunId is not protocol-valid."
    }
    $repositoryUri = $null
    if (-not [Uri]::TryCreate($RepositoryUrl, [UriKind]::Absolute, [ref]$repositoryUri)) {
        throw "RepositoryUrl must be an absolute URI."
    }
    if (
        $RepositoryUrl.Length -gt 512 -or
        $repositoryUri.Scheme -cne "https" -or
        -not [string]::IsNullOrEmpty($repositoryUri.UserInfo) -or
        -not [string]::IsNullOrEmpty($repositoryUri.Query) -or
        -not [string]::IsNullOrEmpty($repositoryUri.Fragment)
    ) {
        throw "RepositoryUrl must be credential-free HTTPS."
    }
    if (
        ([string]$RemoteRoot).Length -gt 200 -or
        $RemoteRoot -match '[\x00-\x1f]'
    ) {
        throw "RemoteRoot is too long or contains control characters."
    }
    Assert-Jet3ProcessLimits -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    if (
        $MaximumArtifactBytes -lt 1MB -or
        $MaximumArtifactBytes -gt 1GB
    ) {
        throw "Artifact limit must be between 1 MiB and 1 GiB."
    }
}

function Resolve-Jet3RemoteRoot {
    $candidate = $RemoteRoot
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Join-Path ([IO.Path]::GetTempPath()) "jet3-rs-ssh"
    }
    if (
        $candidate -notmatch '^[A-Za-z]:[\\/]' -or
        @($candidate -split '[\\/]' | Where-Object { $_ -ceq ".." }).Count -gt 0
    ) {
        throw "RemoteRoot must be an absolute local-drive path without traversal."
    }
    $full = [IO.Path]::GetFullPath($candidate)
    $volume = [IO.Path]::GetPathRoot($full)
    if (
        [string]::IsNullOrWhiteSpace($volume) -or
        $full.TrimEnd('\', '/') -ceq $volume.TrimEnd('\', '/')
    ) {
        throw "RemoteRoot must be an absolute non-volume-root path."
    }
    return $full.TrimEnd('\', '/')
}

function Assert-Jet3CleanCheckout {
    param([string]$Repository)

    $head = Invoke-Jet3BootstrapProcess -Executable "git.exe" `
        -Arguments @("-C", $Repository, "rev-parse", "HEAD") `
        -Label "git rev-parse" -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    if (
        $head.exit_code -ne 0 -or
        $head.stdout.Trim() -cne $GitCommit
    ) {
        throw "Remote checkout did not resolve to the requested commit."
    }
    $status = Invoke-Jet3BootstrapProcess -Executable "git.exe" `
        -Arguments @(
            "-C", $Repository, "status", "--porcelain=v1",
            "--untracked-files=all"
        ) -Label "git status" -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    if ($status.exit_code -ne 0 -or $status.stdout.Length -ne 0) {
        throw "Remote checkout is not clean."
    }
}

function Write-Jet3Utf8 {
    param([string]$Path, [string]$Text)

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Write-Jet3Result {
    param([System.Collections.IDictionary]$Result)

    $json = $Result | ConvertTo-Json -Depth 8 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    [Console]::WriteLine(
        "JET3_REMOTE_RESULT=" + [Convert]::ToBase64String($bytes)
    )
}

function Invoke-Jet3Bootstrap {
    $root = Resolve-Jet3RemoteRoot
    $runsRoot = Join-Path $root "runs"
    $commitRoot = Join-Path $runsRoot $GitCommit
    $runRoot = Join-Path $commitRoot $RunId
    foreach ($parent in @($root, $runsRoot, $commitRoot)) {
        if (-not (Test-Path -LiteralPath $parent)) {
            [void](New-Item -ItemType Directory -Path $parent -Force:$false)
        }
        $parentItem = Get-Item -LiteralPath $parent -Force
        if (
            -not $parentItem.PSIsContainer -or
            ($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Remote run parents must be ordinary directories."
        }
    }
    if (Test-Path -LiteralPath $runRoot) {
        throw "The remote run directory already exists."
    }
    [void](New-Item -ItemType Directory -Path $runRoot -Force:$false)
    $checkout = Join-Path $runRoot "repository"
    $clone = Invoke-Jet3BootstrapProcess -Executable "git.exe" `
        -Arguments @("clone", "--no-checkout", $RepositoryUrl, $checkout) `
        -Label "git clone" -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    if ($clone.exit_code -ne 0) {
        throw "Remote clone failed: $($clone.stderr.Trim())"
    }
    $checkoutResult = Invoke-Jet3BootstrapProcess -Executable "git.exe" `
        -Arguments @("-C", $checkout, "checkout", "--detach", $GitCommit) `
        -Label "git checkout" -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    if ($checkoutResult.exit_code -ne 0) {
        throw "Remote checkout failed: $($checkoutResult.stderr.Trim())"
    }
    Assert-Jet3CleanCheckout -Repository $checkout

    $relativeScript = "oracle/windows-dao/scripts/remote/Invoke-Jet3DaoSshJob.ps1"
    $relativeModule = "oracle/windows-dao/scripts/remote/Remote.Process.ps1"
    $checkedScript = Join-Path $checkout $relativeScript
    $checkedModule = Join-Path $checkout $relativeModule
    foreach ($binding in @(
        @($PSCommandPath, $checkedScript),
        @($remoteModule, $checkedModule)
    )) {
        $sourceHash = (Get-FileHash -LiteralPath $binding[0] -Algorithm SHA256).Hash
        $checkedHash = (Get-FileHash -LiteralPath $binding[1] -Algorithm SHA256).Hash
        if ($sourceHash -cne $checkedHash) {
            throw "Uploaded remote automation does not match the checked commit."
        }
    }

    & $checkedScript -Mode Execute -Job $Job `
        -RepositoryUrl $RepositoryUrl -GitCommit $GitCommit -RunId $RunId `
        -RemoteRoot $root -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes `
        -MaximumArtifactBytes $MaximumArtifactBytes `
        -RunDirectory $runRoot
    exit $LASTEXITCODE
}

function Invoke-Jet3Job {
    $root = Resolve-Jet3RemoteRoot
    if ([string]::IsNullOrWhiteSpace($RunDirectory)) {
        throw "Execute mode requires RunDirectory."
    }
    $runRoot = [IO.Path]::GetFullPath($RunDirectory)
    $expectedRunRoot = [IO.Path]::GetFullPath(
        (Join-Path (Join-Path (Join-Path $root "runs") $GitCommit) $RunId)
    )
    if ($runRoot -cne $expectedRunRoot) {
        throw "RunDirectory is outside the bound run path."
    }
    $checkout = Join-Path $runRoot "repository"
    Assert-Jet3CleanCheckout -Repository $checkout

    $boundedProcess = Join-Path $checkout (
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1"
    )
    . $boundedProcess
    $artifacts = Join-Path $runRoot "artifacts"
    [void](New-Item -ItemType Directory -Path $artifacts -Force:$false)
    $environmentPath = Join-Path $artifacts "environment.json"
    $stdoutPath = Join-Path $artifacts "stdout.log"
    $stderrPath = Join-Path $artifacts "stderr.log"
    $winps32 = Join-Path $env:WINDIR (
        "SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
    )
    if (-not (Test-Path -LiteralPath $winps32 -PathType Leaf)) {
        throw "x86 Windows PowerShell is unavailable."
    }

    $probeScript = Join-Path $checkout (
        "oracle/windows-dao/scripts/probe-provider.ps1"
    )
    $probe = Invoke-Jet3CheckedChildProcess -Executable $winps32 `
        -Arguments @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", $probeScript, "-ProtocolVersion", "1.1.0",
            "-OutputPath", $environmentPath
        ) -Label "DAO provider probe" -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes $MaximumOutputBytes
    $result = $probe
    $phase = "provider-probe"

    if ($Job -eq "m1-controlled" -and $probe.exit_code -eq 0) {
        Assert-Jet3CleanCheckout -Repository $checkout
        $m1Script = Join-Path $checkout (
            "oracle/windows-dao/scripts/run-m1-controlled.ps1"
        )
        $evidence = Join-Path $artifacts "evidence"
        $result = Invoke-Jet3CheckedChildProcess -Executable $winps32 `
            -Arguments @(
                "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", $m1Script, "-RepositoryRoot", $checkout,
                "-EnvironmentPath", $environmentPath,
                "-OutputRoot", $evidence, "-GitCommit", $GitCommit,
                "-RunId", $RunId
            ) -Label "M1 controlled run" -TimeoutSeconds $TimeoutSeconds `
            -MaximumOutputBytes $MaximumOutputBytes
        $phase = "m1-controlled"
    }
    Write-Jet3Utf8 -Path $stdoutPath -Text ([string]$result.stdout)
    Write-Jet3Utf8 -Path $stderrPath -Text ([string]$result.stderr)

    $downloadable = @(0, 1, 3) -contains [int]$result.exit_code
    $metadata = [ordered]@{
        artifact_limit_bytes = $MaximumArtifactBytes
        commit = $GitCommit
        downloadable = $downloadable
        exit_code = [int]$result.exit_code
        job = $Job
        phase = $phase
        remote_root = $root
        run_id = $RunId
        timeout_seconds = $TimeoutSeconds
    }
    Write-Jet3Utf8 -Path (Join-Path $artifacts "remote-job.json") `
        -Text (($metadata | ConvertTo-Json -Depth 5) + "`n")

    if (-not $downloadable) {
        Write-Jet3Result -Result $metadata
        exit [int]$result.exit_code
    }
    $totalBytes = [long]0
    foreach ($item in Get-ChildItem -LiteralPath $artifacts -Recurse -Force) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Artifact trees cannot contain reparse points."
        }
        if (-not $item.PSIsContainer) {
            $totalBytes += $item.Length
            if ($totalBytes -gt $MaximumArtifactBytes) {
                throw "Artifact tree exceeds its byte ceiling."
            }
        }
    }
    $archive = Join-Path $runRoot "artifacts.zip"
    Compress-Archive -LiteralPath $artifacts -DestinationPath $archive `
        -CompressionLevel Optimal
    $archiveItem = Get-Item -LiteralPath $archive -Force
    if ($archiveItem.Length -gt $MaximumArtifactBytes) {
        Remove-Item -LiteralPath $archive -Force
        throw "Artifact archive exceeds its byte ceiling."
    }
    $metadata["archive_path"] = $archiveItem.FullName
    $metadata["archive_sha256"] = (
        Get-FileHash -LiteralPath $archive -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $metadata["archive_size"] = $archiveItem.Length
    Write-Jet3Result -Result $metadata
    exit [int]$result.exit_code
}

try {
    Assert-Jet3RemoteInputs
    if ($Mode -eq "Bootstrap") {
        Invoke-Jet3Bootstrap
    }
    Invoke-Jet3Job
}
catch {
    Write-Jet3Result -Result ([ordered]@{
        commit = $GitCommit
        downloadable = $false
        exit_code = 4
        job = $Job
        reason = $_.Exception.Message
        run_id = $RunId
    })
    [Console]::Error.WriteLine("ERROR: " + $_.Exception.Message)
    exit 4
}
