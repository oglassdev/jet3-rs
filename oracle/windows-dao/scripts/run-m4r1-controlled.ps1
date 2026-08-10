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

$runIdMatch = [regex]::Match(
    $RunId, "^[0-9]{8}T[0-9]{6}Z-m4-[a-z0-9-]{1,24}$"
)
if (-not $runIdMatch.Success -or $runIdMatch.Length -ne $RunId.Length) {
    throw "M4 RunId does not match the controlled campaign format."
}

function Assert-M4BootstrapSource {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )

    if (
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop"
    ) {
        throw "M4 controller bootstrap requires x86 Windows PowerShell."
    }
    if ($Commit -cnotmatch "^[0-9a-f]{40}$") {
        throw "M4 bootstrap commit must be lowercase SHA-1 hex."
    }
    if (
        -not [IO.Path]::IsPathRooted($Repository) -or
        $Repository.StartsWith("\\", [StringComparison]::Ordinal) -or
        $Repository.Substring(2).Contains(":")
    ) {
        throw "M4 bootstrap repository must be an absolute local path."
    }
    $expectedEntry = Join-Path $Repository (
        "oracle/windows-dao/scripts/run-m4r1-controlled.ps1"
    )
    if (-not ([IO.Path]::GetFullPath($PSCommandPath)).Equals(
        [IO.Path]::GetFullPath($expectedEntry),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "M4 bootstrap entrypoint differs from the bound repository."
    }
    $root = [IO.Path]::GetPathRoot($Repository)
    $relative = $Repository.Substring($root.Length)
    $current = $root.TrimEnd([IO.Path]::DirectorySeparatorChar)
    foreach ($part in $relative.Split(
        [IO.Path]::DirectorySeparatorChar,
        [StringSplitOptions]::RemoveEmptyEntries
    )) {
        $current = Join-Path $current $part
        $item = Get-Item -LiteralPath $current -Force
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "M4 bootstrap repository contains a reparse component."
        }
    }
    $gitCommand = Get-Command git -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1
    $git = [IO.Path]::GetFullPath($gitCommand.Source)
    $head = @(& $git -C $Repository rev-parse --verify HEAD 2>&1)
    if (
        $LASTEXITCODE -ne 0 -or $head.Count -ne 1 -or
        [string]$head[0] -cne $Commit
    ) {
        throw "M4 bootstrap HEAD differs from the requested commit."
    }
    $dirty = @(
        & $git -C $Repository status --porcelain=v1 `
            --untracked-files=all 2>&1
    )
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
        throw "M4 bootstrap requires an exact clean worktree."
    }
    $repositoryUrl = "https://github.com/oglassdev/jet3-rs.git"
    $remoteRef = "refs/heads/codex/m4r2-canonical-paths"
    $origin = @(& $git -C $Repository remote get-url origin 2>&1)
    if (
        $LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne $repositoryUrl
    ) {
        throw "M4 bootstrap origin differs from the private source binding."
    }
    return $git
}

function Assert-M4BootstrapRemote {
    param(
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit
    )

    $savedPrompt = [Environment]::GetEnvironmentVariable(
        "GIT_TERMINAL_PROMPT", "Process"
    )
    $savedInteractive = [Environment]::GetEnvironmentVariable(
        "GCM_INTERACTIVE", "Process"
    )
    try {
        [Environment]::SetEnvironmentVariable(
            "GIT_TERMINAL_PROMPT", "0", "Process"
        )
        [Environment]::SetEnvironmentVariable(
            "GCM_INTERACTIVE", "Never", "Process"
        )
        $captured = Invoke-BoundedChildProcess `
            -Executable $Git `
            -Arguments @(
                "-c", "credential.interactive=never",
                "-c", "core.askPass=",
                "-C", $Repository,
                "ls-remote", "--heads",
                "https://github.com/oglassdev/jet3-rs.git",
                "refs/heads/codex/m4r2-canonical-paths"
            ) `
            -CallerLabel "M4 bootstrap remote" `
            -TimeoutSeconds 30 `
            -MaximumOutputBytes 64KB
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "GIT_TERMINAL_PROMPT", $savedPrompt, "Process"
        )
        [Environment]::SetEnvironmentVariable(
            "GCM_INTERACTIVE", $savedInteractive, "Process"
        )
    }
    $lines = @(
        [string]$captured.stdout -split "\r?\n" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if (
        $lines.Count -ne 1 -or
        -not $lines[0].StartsWith(
            $Commit + "`t",
            [StringComparison]::Ordinal
        )
    ) {
        throw "M4 bootstrap commit is not the exact pushed ref."
    }
}

function Open-M4BootstrapSources {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)][string]$Git,
        [Parameter(Mandatory = $true)][string[]]$RelativePaths
    )

    $streams = New-Object Collections.ArrayList
    try {
        foreach ($relativePath in $RelativePaths) {
            $sourcePath = [IO.Path]::GetFullPath(
                (Join-Path $Repository $relativePath)
            )
            $item = Get-Item -LiteralPath $sourcePath -Force
            if (
                $item.PSIsContainer -or
                ($item.Attributes -band
                    [IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw "M4 bootstrap source is not an ordinary file."
            }
            $cursor = [IO.Path]::GetDirectoryName($sourcePath)
            while ($true) {
                $directory = Get-Item -LiteralPath $cursor -Force
                if (
                    -not $directory.PSIsContainer -or
                    ($directory.Attributes -band
                        [IO.FileAttributes]::ReparsePoint) -ne 0
                ) {
                    throw "M4 bootstrap source has a reparse ancestor."
                }
                if ($cursor.Equals(
                    $Repository,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                    break
                }
                $parent = [IO.Path]::GetDirectoryName($cursor)
                if ([string]::IsNullOrWhiteSpace($parent)) {
                    throw "M4 bootstrap source escapes the repository."
                }
                $cursor = $parent
            }
            $stream = New-Object IO.FileStream(
                $sourcePath,
                [IO.FileMode]::Open,
                [IO.FileAccess]::Read,
                [IO.FileShare]::Read,
                4096,
                [IO.FileOptions]::SequentialScan
            )
            [void]$streams.Add($stream)
            $objectName = "${Commit}:$relativePath"
            $expected = @(& $Git -C $Repository rev-parse $objectName 2>&1)
            $actual = @(
                & $Git -C $Repository hash-object -- $sourcePath 2>&1
            )
            if (
                $expected.Count -ne 1 -or $actual.Count -ne 1 -or
                [string]$expected[0] -cnotmatch "^[0-9a-f]{40}$" -or
                [string]$actual[0] -cne [string]$expected[0]
            ) {
                throw "M4 bootstrap helper differs from the producer commit."
            }
        }
        return ,$streams.ToArray()
    }
    catch {
        foreach ($stream in $streams) {
            try { $stream.Dispose() } catch {}
        }
        throw
    }
}

$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$bootstrapGit = Assert-M4BootstrapSource `
    -Repository $repository -Commit $GitCommit
$bootstrapHelpers = @(
    "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
    "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
    "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
    "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
    "oracle/windows-dao/scripts/shared/BoundedProcess.ps1",
    "oracle/windows-dao/scripts/m4r1_campaign.py",
    "oracle/windows-dao/scripts/m4r1_spec.py",
    "oracle/windows-dao/scripts/m4r1_phase.py",
    "oracle/windows-dao/scripts/m4r1_snapshot.py",
    "oracle/windows-dao/scripts/shared/BoundedProcess.Native.cs",
    "oracle/windows-dao/scripts/m4/M4.Clone.ps1",
    "oracle/windows-dao/scripts/m4/M4.Dao.ps1",
    "oracle/windows-dao/scripts/m4r1/M4R1.Bundle.ps1",
    "oracle/windows-dao/scripts/m4r1/M4R1.Quiescence.ps1",
    "oracle/windows-dao/scripts/m4r1/M4R1.ControllerRuntime.ps1",
    "oracle/windows-dao/scripts/m4r1/M4R1.Controller.ps1"
)
$bootstrapStreams = @(Open-M4BootstrapSources `
    -Repository $repository -Commit $GitCommit `
    -Git $bootstrapGit -RelativePaths $bootstrapHelpers)
$m1 = Join-Path $repository "oracle/windows-dao/scripts/m1"
$m4 = Join-Path $repository "oracle/windows-dao/scripts/m4"
$m4r1 = Join-Path $repository "oracle/windows-dao/scripts/m4r1"
try {
    . (Join-Path $repository "oracle/windows-dao/scripts/shared/BoundedProcess.ps1")
    Assert-M4BootstrapRemote -Git $bootstrapGit `
        -Repository $repository -Commit $GitCommit
    . (Join-Path $m1 "M1.Preflight.ps1")
    . (Join-Path $m1 "M1.Publication.ps1")
    . (Join-Path $m4 "M4.Clone.ps1")
    . (Join-Path $m4 "M4.Dao.ps1")
    . (Join-Path $m4r1 "M4R1.Bundle.ps1")
    . (Join-Path $m4r1 "M4R1.Quiescence.ps1")
    . (Join-Path $m4r1 "M4R1.ControllerRuntime.ps1")
    . (Join-Path $m4r1 "M4R1.Controller.ps1")
}
finally {
    foreach ($stream in $bootstrapStreams) {
        try { $stream.Dispose() } catch {}
    }
}

try {
    $published = Invoke-M4Campaign `
        -RepositoryRoot $repository `
        -EnvironmentPath $EnvironmentPath `
        -OutputRoot $OutputRoot `
        -GitCommit $GitCommit `
        -RunId $RunId
    Write-Output "PASS: retained M4 campaign at $published"
    exit 0
}
catch {
    [Console]::Error.WriteLine(
        $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    )
    exit 1
}
