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

function Assert-A1Bootstrap {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )

    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop" -or
        $PSVersionTable.PSVersion.Major -ne 5) {
        throw "A1 bootstrap requires x86 Windows PowerShell 5 Desktop."
    }
    if ($Commit -cnotmatch "^[0-9a-f]{40}$") {
        throw "A1 bootstrap commit must be lowercase SHA-1 hex."
    }
    $entry = Join-Path $Repository `
        "oracle/windows-dao/scripts/run-a1-controlled.ps1"
    if (-not ([IO.Path]::GetFullPath($PSCommandPath)).Equals(
        [IO.Path]::GetFullPath($entry), [StringComparison]::OrdinalIgnoreCase
    )) { throw "A1 bootstrap entrypoint differs from its repository." }
    $cursor = [IO.Path]::GetFullPath($Repository)
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "A1 bootstrap repository has a reparse component."
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -ceq $cursor) {
            break
        }
        $cursor = $parent
    }
    $git = [IO.Path]::GetFullPath((Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1).Source)
    $head = @(& $git -C $Repository rev-parse --verify HEAD 2>&1)
    $dirty = @(& $git -C $Repository status --porcelain=v1 `
        --untracked-files=all 2>&1)
    $origin = @(& $git -C $Repository remote get-url origin 2>&1)
    if ($head.Count -ne 1 -or [string]$head[0] -cne $Commit -or
        $dirty.Count -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne
            "https://github.com/oglassdev/jet3-rs.git") {
        throw "A1 bootstrap requires the exact clean repository binding."
    }
    return $git
}

function Open-A1BootstrapSources {
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
                throw "A1 bootstrap source locator is unsafe."
            }
            $path = [IO.Path]::GetFullPath((Join-Path $Repository $relative))
            $item = Get-Item -LiteralPath $path -Force
            if ($item.PSIsContainer -or $item.Length -gt 2MB -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "A1 bootstrap source is not a bounded ordinary file."
            }
            $expected = @(& $Git -C $Repository rev-parse `
                "${Commit}:$relative" 2>&1)
            $actual = @(& $Git -C $Repository hash-object -- $path 2>&1)
            if ($expected.Count -ne 1 -or $actual.Count -ne 1 -or
                [string]$actual[0] -cne [string]$expected[0]) {
                throw "A1 bootstrap source differs from the producer commit."
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

if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-a1-[a-z0-9-]{1,24}$") {
    throw "A1 RunId does not match the controlled campaign format."
}
$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$git = Assert-A1Bootstrap -Repository $repository -Commit $GitCommit
$sources = @(
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
    "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1",
    "oracle/windows-dao/scripts/m1_bundle_validation.py",
    "oracle/windows-dao/scripts/protocol_cli.py",
    "oracle/windows-dao/scripts/protocol_validation.py",
    "oracle/windows-dao/scripts/validate_m1_protocol.py",
    "oracle/windows-dao/examples/m1-inventory.json"
)
$streams = @(Open-A1BootstrapSources -Repository $repository `
    -Commit $GitCommit -Git $git -RelativePaths $sources)
try {
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/m1/M1.Preflight.ps1")
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/m1/M1.Publication.ps1")
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1")
    . (Join-Path $repository `
        "oracle/windows-dao/scripts/a1/A1.Controller.ps1")
    $published = Invoke-A1Campaign -RepositoryRoot $repository `
        -EnvironmentPath $EnvironmentPath -OutputRoot $OutputRoot `
        -GitCommit $GitCommit -RunId $RunId
    Write-Output "PASS: retained A1 campaign at $published"
    exit 0
}
catch {
    [Console]::Error.WriteLine(
        $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    )
    exit 1
}
finally {
    foreach ($stream in $streams) { try { $stream.Dispose() } catch { } }
}
