[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$M4BundleRoot,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$GitCommit,
    [Parameter(Mandatory = $true)][string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-M5Bootstrap {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop") {
        throw "M5 bootstrap requires x86 Windows PowerShell Desktop."
    }
    if ($Commit -cnotmatch "^[0-9a-f]{40}$") {
        throw "M5 bootstrap commit must be lowercase SHA-1 hex."
    }
    $repository = [IO.Path]::GetFullPath($Repository)
    $entry = Join-Path $repository `
        "oracle/windows-dao/scripts/run-m5r2-controlled.ps1"
    if (-not ([IO.Path]::GetFullPath($PSCommandPath)).Equals(
        [IO.Path]::GetFullPath($entry), [StringComparison]::OrdinalIgnoreCase
    )) { throw "M5 bootstrap entrypoint differs from its repository." }
    $cursor = $repository
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "M5 bootstrap repository has a reparse component."
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -ceq $cursor) {
            break
        }
        $cursor = $parent
    }
    $git = [IO.Path]::GetFullPath((Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1).Source)
    $head = @(& $git -C $repository rev-parse --verify HEAD 2>&1)
    if ($LASTEXITCODE -ne 0 -or $head.Count -ne 1 -or
        [string]$head[0] -cne $Commit) {
        throw "M5 bootstrap HEAD differs from the requested commit."
    }
    $dirty = @(& $git -C $repository status --porcelain=v1 `
        --untracked-files=all 2>&1)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
        throw "M5 bootstrap requires an exact clean worktree."
    }
    $origin = @(& $git -C $repository remote get-url origin 2>&1)
    if ($LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne "https://github.com/oglassdev/jet3-rs.git") {
        throw "M5 bootstrap origin differs from its repository binding."
    }
    return $git
}

function Open-M5BootstrapSources {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string[]]$RelativePaths
    )
    $streams = New-Object Collections.ArrayList
    try {
        foreach ($relative in $RelativePaths) {
            $path = [IO.Path]::GetFullPath((Join-Path $Repository $relative))
            $item = Get-Item -LiteralPath $path -Force
            if ($item.PSIsContainer -or
                ($item.Attributes -band
                    [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "M5 bootstrap source is not an ordinary file."
            }
            $expected = @(& $Git -C $Repository rev-parse `
                "${Commit}:$relative" 2>&1)
            $actual = @(& $Git -C $Repository hash-object -- $path 2>&1)
            if ($expected.Count -ne 1 -or $actual.Count -ne 1 -or
                [string]$actual[0] -cne [string]$expected[0]) {
                throw "M5 bootstrap source differs from the producer commit."
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

function Assert-M5BootstrapRemote {
    param(
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )
    $result = Invoke-BoundedChildProcess -Executable $Git `
        -Arguments @(
            "-c", "credential.interactive=never", "-c", "core.askPass=",
            "-C", $Repository, "ls-remote", "--heads",
            "https://github.com/oglassdev/jet3-rs.git",
            "refs/heads/codex/m5r6-null-prefix-bound"
        ) -CallerLabel "M5 bootstrap remote" `
        -TimeoutSeconds 30 -MaximumOutputBytes 64KB
    $lines = @([string]$result.stdout -split "\r?\n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1 -or -not $lines[0].StartsWith(
        $Commit + "`t", [StringComparison]::Ordinal
    )) { throw "M5 bootstrap commit is not the exact pushed ref." }
}

if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-m5-[a-z0-9-]{1,24}$") {
    throw "M5 RunId does not match the controlled campaign format."
}
$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$git = Assert-M5Bootstrap -Repository $repository -Commit $GitCommit
$sources = @(
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
    "oracle/windows-dao/experiments/m5r6/plan.schema.json",
    "oracle/windows-dao/experiments/m5r6/invocation.schema.json",
    "oracle/windows-dao/experiments/m5r6/worker-result.schema.json",
    "oracle/windows-dao/experiments/m5r6/operation-log.schema.json",
    "oracle/windows-dao/experiments/m5r6/snapshot.schema.json",
    "oracle/windows-dao/experiments/m5r6/clone-log.schema.json",
    "oracle/windows-dao/experiments/m5r6/post-worker-quiescence.schema.json",
    "oracle/windows-dao/experiments/m5r6/sample-record.schema.json",
    "oracle/windows-dao/experiments/m5r6/analysis-report.schema.json",
    "oracle/windows-dao/experiments/m5r6/bundle-manifest.schema.json",
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
    "oracle/windows-dao/experiments/m5/m5-compact-confirm-r7.plan.json"
)
$streams = @(Open-M5BootstrapSources -Repository $repository `
    -Commit $GitCommit -Git $git -RelativePaths $sources)
try {
    . (Join-Path $repository "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")
    Assert-M5BootstrapRemote -Git $git -Repository $repository `
        -Commit $GitCommit
    . (Join-Path $repository "oracle/windows-dao/scripts/m1/M1.Preflight.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m1/M1.Publication.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m4/M4.Clone.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m4/M4.Dao.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Bundle.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Quiescence.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.ControllerRuntime.ps1")
    . (Join-Path $repository "oracle/windows-dao/scripts/m5/M5.Controller.ps1")
}
finally {
    foreach ($stream in $streams) { try { $stream.Dispose() } catch { } }
}

try {
    $published = Invoke-M5Campaign -RepositoryRoot $repository `
        -EnvironmentPath $EnvironmentPath -M4BundleRoot $M4BundleRoot `
        -OutputRoot $OutputRoot -GitCommit $GitCommit -RunId $RunId
    Write-Output "PASS: retained M5 campaign at $published"
    exit 0
}
catch {
    [Console]::Error.WriteLine(
        $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    )
    exit 1
}
