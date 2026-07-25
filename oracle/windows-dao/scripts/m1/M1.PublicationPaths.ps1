Set-StrictMode -Version Latest

function Get-M1FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.IndexOf([char]0) -ge 0) {
        throw "Publication paths must be non-empty and contain no NUL."
    }
    $full = [IO.Path]::GetFullPath($Path)
    if (
        $full.StartsWith("\\", [StringComparison]::Ordinal) -or
        $full -cnotmatch "^[A-Za-z]:\\"
    ) {
        throw "Publication paths must be on a local drive, not UNC."
    }
    if ($full.Substring(2).Contains(":")) {
        throw "Alternate data stream paths are forbidden."
    }
    if ($full -ceq [IO.Path]::GetPathRoot($full)) {
        throw "Drive-root publication paths are forbidden."
    }
    return $full.TrimEnd([IO.Path]::DirectorySeparatorChar)
}

function Assert-M1LocalFixedVolume {
    param([Parameter(Mandatory = $true)][string]$Path)

    $root = [IO.Path]::GetPathRoot($Path)
    $drive = New-Object IO.DriveInfo($root)
    if (
        -not $drive.IsReady -or
        $drive.DriveType -ne [IO.DriveType]::Fixed
    ) {
        throw "Evidence publication requires a ready local fixed volume."
    }
}

function Assert-M1NoReparseComponents {
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    $relative = $full.Substring($root.Length)
    $current = $root.TrimEnd([IO.Path]::DirectorySeparatorChar)
    foreach ($part in $relative.Split(
        [IO.Path]::DirectorySeparatorChar,
        [StringSplitOptions]::RemoveEmptyEntries
    )) {
        $current = Join-Path $current $part
        if (-not (Test-Path -LiteralPath $current)) {
            break
        }
        $item = Get-Item -LiteralPath $current -Force
        if (
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Reparse points are forbidden in publication paths."
        }
    }
}

function Assert-M1OutputOutsideRepository {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$OutputRoot
    )

    $repository = $RepositoryRoot.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    )
    $output = $OutputRoot.TrimEnd([IO.Path]::DirectorySeparatorChar)
    $repositoryPrefix = $repository + [IO.Path]::DirectorySeparatorChar
    if (
        $output.Equals($repository, [StringComparison]::OrdinalIgnoreCase) -or
        $output.StartsWith(
            $repositoryPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Evidence output must remain outside the repository."
    }
}

function New-M1PrivateDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = $identity.User
    if ($null -eq $sid) {
        throw "The current Windows identity has no security identifier."
    }
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetOwner($sid)
    $security.SetAccessRuleProtection($true, $false)
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    [void][IO.Directory]::CreateDirectory($Path, $security)
}
