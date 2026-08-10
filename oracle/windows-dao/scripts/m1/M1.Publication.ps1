Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "M1.PublicationPaths.ps1")

# M1 evidence publication uses durable file handles plus a same-volume
# directory rename. Managed .NET does not expose a safe, portable Windows
# Directory-handle fsync operation. Flush(true) therefore durably requests each
# file's data and metadata before publication, but sudden power loss can still
# lose parent-directory metadata on filesystems or storage stacks that do not
# persist the rename. The protocol promises atomic visibility, not immunity
# from every hardware or filesystem failure.

$script:M1DefaultMaxFileBytes = 64MB
$script:M1DefaultMaxTotalBytes = 256MB

function Invoke-M1PublicationFault {
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$Phase
    )

    if ($null -ne $Session.FaultInjector) {
        & $Session.FaultInjector $Phase $Session | Out-Null
    }
}

function New-M1PublicationSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$OutputRoot,
        [Parameter(Mandatory = $true)]
        [string]$GitCommit,
        [Parameter(Mandatory = $true)]
        [string]$RunId,
        [long]$MaxFileBytes = $script:M1DefaultMaxFileBytes,
        [long]$MaxTotalBytes = $script:M1DefaultMaxTotalBytes,
        [scriptblock]$FaultInjector
    )

    if ($GitCommit -cnotmatch "^[0-9a-f]{40}$") {
        throw "GitCommit must be 40 lowercase hexadecimal digits."
    }
    if (
        $RunId -cnotmatch (
            "^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$"
        )
    ) {
        throw "RunId does not match the protocol pattern."
    }
    if (
        $MaxFileBytes -le 0 -or
        $MaxTotalBytes -le 0 -or
        $MaxFileBytes -gt $MaxTotalBytes
    ) {
        throw "Publication byte ceilings are invalid."
    }

    $repository = Get-M1FullPath -Path $RepositoryRoot
    $output = Get-M1FullPath -Path $OutputRoot
    if (-not (Test-Path -LiteralPath $repository -PathType Container)) {
        throw "RepositoryRoot must be an existing directory."
    }
    Assert-M1LocalFixedVolume -Path $repository
    Assert-M1LocalFixedVolume -Path $output
    Assert-M1NoReparseComponents -Path $repository
    Assert-M1NoReparseComponents -Path $output
    Assert-M1OutputOutsideRepository -RepositoryRoot $repository `
        -OutputRoot $output

    $commitDirectory = Join-Path $output $GitCommit
    $finalDirectory = Join-Path $commitDirectory $RunId
    if (Test-Path -LiteralPath $finalDirectory) {
        throw "The immutable evidence directory already exists."
    }

    [void][IO.Directory]::CreateDirectory($output)
    Assert-M1NoReparseComponents -Path $output
    [void][IO.Directory]::CreateDirectory($commitDirectory)
    Assert-M1NoReparseComponents -Path $commitDirectory

    $stageName = ".m1-stage-" + [Guid]::NewGuid().ToString("N")
    $stagingRoot = Join-Path $output $stageName
    if (Test-Path -LiteralPath $stagingRoot) {
        throw "The randomly selected private staging path already exists."
    }
    New-M1PrivateDirectory -Path $stagingRoot
    try {
        $stagingCommit = Join-Path $stagingRoot $GitCommit
        $stagingBundle = Join-Path $stagingCommit $RunId
        $workingPath = Join-Path $stagingRoot "working"
        [void][IO.Directory]::CreateDirectory($stagingBundle)
        [void][IO.Directory]::CreateDirectory($workingPath)
        Assert-M1NoReparseComponents -Path $stagingBundle
        $paths = New-Object (
            "Collections.Generic.HashSet[string]"
        ) ([StringComparer]::Ordinal)
        return [pscustomobject]@{
            RepositoryRoot = $repository
            OutputRoot = $output
            CommitDirectory = $commitDirectory
            FinalDirectory = $finalDirectory
            StageRoot = $stagingRoot
            BundlePath = $stagingBundle
            WorkingPath = $workingPath
            StagingRoot = $stagingRoot
            StagingBundle = $stagingBundle
            GitCommit = $GitCommit
            RunId = $RunId
            MaxFileBytes = $MaxFileBytes
            MaxTotalBytes = $MaxTotalBytes
            TotalBytes = [long]0
            RegisteredPaths = $paths
            FaultInjector = $FaultInjector
        }
    }
    catch {
        if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
            [IO.Directory]::Delete($stagingRoot, $true)
        }
        throw
    }
}

function Get-M1PayloadPath {
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        $RelativePath.IndexOf([char]0) -ge 0 -or
        $RelativePath.Contains("\") -or
        $RelativePath.Contains(":") -or
        [IO.Path]::IsPathRooted($RelativePath)
    ) {
        throw "Payload paths must be canonical relative paths."
    }
    $parts = $RelativePath.Split("/")
    if (
        $parts.Count -eq 0 -or
        @($parts | Where-Object {
            [string]::IsNullOrEmpty($_) -or $_ -eq "." -or $_ -eq ".."
        }).Count -ne 0
    ) {
        throw "Payload paths contain a forbidden component."
    }
    $candidate = $Session.StagingBundle
    foreach ($part in $parts) {
        $candidate = Join-Path $candidate $part
    }
    $full = [IO.Path]::GetFullPath($candidate)
    $prefix = $Session.StagingBundle.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $full.StartsWith(
            $prefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Payload path escapes the private staging bundle."
    }
    return $full
}

function Register-M1PayloadLength {
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [long]$Length
    )

    if ($Length -lt 0 -or $Length -gt $Session.MaxFileBytes) {
        throw "Payload exceeds the per-file publication ceiling."
    }
    if (-not $Session.RegisteredPaths.Contains($RelativePath)) {
        if ($Length -gt ($Session.MaxTotalBytes - $Session.TotalBytes)) {
            throw "Payloads exceed the total publication ceiling."
        }
        [void]$Session.RegisteredPaths.Add($RelativePath)
        $Session.TotalBytes = [long]($Session.TotalBytes + $Length)
    }
}

function Write-M1DurableBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes
    )

    $length = [long]$Bytes.LongLength
    Register-M1PayloadLength -Session $Session `
        -RelativePath $RelativePath -Length $length
    $target = Get-M1PayloadPath -Session $Session `
        -RelativePath $RelativePath
    $parent = [IO.Path]::GetDirectoryName($target)
    [void][IO.Directory]::CreateDirectory($parent)
    Assert-M1NoReparseComponents -Path $parent
    Invoke-M1PublicationFault -Session $Session -Phase "before_file_create"
    $stream = New-Object IO.FileStream(
        $target,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        65536,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        Invoke-M1PublicationFault -Session $Session `
            -Phase "before_file_flush"
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    Invoke-M1PublicationFault -Session $Session -Phase "after_file_flush"
}

function Write-M1DurableUtf8 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $encoding = New-Object Text.UTF8Encoding($false, $true)
    $bytes = $encoding.GetBytes($Text)
    Write-M1DurableBytes -Session $Session `
        -RelativePath $RelativePath -Bytes $bytes
}

function Copy-M1DurableFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Alias("Stage")]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $source = Get-M1FullPath -Path $SourcePath
    Assert-M1NoReparseComponents -Path $source
    $sourceItem = Get-Item -LiteralPath $source -Force
    if (
        -not $sourceItem.PSIsContainer -and
        ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
    ) {
        $length = [long]$sourceItem.Length
    }
    else {
        throw "Durable copy source must be a regular non-reparse file."
    }
    Register-M1PayloadLength -Session $Session `
        -RelativePath $RelativePath -Length $length
    $target = Get-M1PayloadPath -Session $Session `
        -RelativePath $RelativePath
    $parent = [IO.Path]::GetDirectoryName($target)
    [void][IO.Directory]::CreateDirectory($parent)
    Assert-M1NoReparseComponents -Path $parent

    $input = New-Object IO.FileStream(
        $source,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    $output = $null
    try {
        $output = New-Object IO.FileStream(
            $target,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            65536,
            [IO.FileOptions]::WriteThrough
        )
        $buffer = New-Object byte[] 65536
        $copied = [long]0
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $copied += $read
            if ($copied -gt $length) {
                throw "Durable copy source changed while being read."
            }
            $output.Write($buffer, 0, $read)
        }
        if ($copied -ne $length -or $input.Length -ne $length) {
            throw "Durable copy source changed while being read."
        }
        Invoke-M1PublicationFault -Session $Session `
            -Phase "before_file_flush"
        $output.Flush($true)
    }
    finally {
        if ($null -ne $output) {
            $output.Dispose()
        }
        $input.Dispose()
    }
    Invoke-M1PublicationFault -Session $Session -Phase "after_file_flush"
}

function Sync-M1DurableFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $path = Get-M1PayloadPath -Session $Session `
        -RelativePath $RelativePath
    Assert-M1NoReparseComponents -Path $path
    $item = Get-Item -LiteralPath $path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Durable sync target must be a regular non-reparse file."
    }
    Register-M1PayloadLength -Session $Session `
        -RelativePath $RelativePath -Length ([long]$item.Length)
    $stream = New-Object IO.FileStream(
        $path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        Invoke-M1PublicationFault -Session $Session `
            -Phase "before_file_flush"
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    Invoke-M1PublicationFault -Session $Session -Phase "after_file_flush"
}

function Assert-M1CleanupTreeBounded {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [long]$MaxBytes,
        [int]$MaxEntries = 4096
    )

    $pending = New-Object Collections.Stack
    $pending.Push((New-Object IO.DirectoryInfo($Root)))
    $entries = 0
    $bytes = [long]0
    while ($pending.Count -gt 0) {
        $directory = [IO.DirectoryInfo]$pending.Pop()
        foreach ($item in $directory.EnumerateFileSystemInfos()) {
            $entries++
            if ($entries -gt $MaxEntries) {
                throw "Private staging cleanup exceeds its entry ceiling."
            }
            if (
                ($item.Attributes -band (
                    [IO.FileAttributes]::ReparsePoint
                )) -ne 0
            ) {
                throw "Private staging cleanup encountered a reparse point."
            }
            if ($item -is [IO.DirectoryInfo]) {
                $pending.Push($item)
            }
            else {
                $length = [long]([IO.FileInfo]$item).Length
                if ($length -gt ($MaxBytes - $bytes)) {
                    throw "Private staging cleanup exceeds its byte ceiling."
                }
                $bytes += $length
            }
        }
    }
}

function Remove-M1PublicationStaging {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Session
    )

    $stage = [IO.Path]::GetFullPath($Session.StagingRoot)
    $parent = [IO.Path]::GetDirectoryName($stage)
    $name = [IO.Path]::GetFileName($stage)
    if (
        -not $parent.Equals(
            $Session.OutputRoot,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $name -cnotmatch "^\.m1-stage-[0-9a-f]{32}$"
    ) {
        throw "Refusing cleanup outside the owned M1 staging boundary."
    }
    if (-not (Test-Path -LiteralPath $stage)) {
        return
    }
    $item = Get-Item -LiteralPath $stage -Force
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Refusing recursive cleanup of a reparse or non-directory path."
    }
    Assert-M1CleanupTreeBounded -Root $stage `
        -MaxBytes $Session.MaxTotalBytes
    [IO.Directory]::Delete($stage, $true)
}

function Complete-M1Publication {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Session,
        [Parameter(Mandatory = $true)]
        [scriptblock]$RecheckScriptBlock,
        [string]$ValidatorPath,
        [string]$PythonExecutable,
        [scriptblock]$ValidationScriptBlock
    )

    try {
        if (Test-Path -LiteralPath $Session.WorkingPath) {
            $workingItem = Get-Item -LiteralPath $Session.WorkingPath -Force
            if (
                -not $workingItem.PSIsContainer -or
                ($workingItem.Attributes -band (
                    [IO.FileAttributes]::ReparsePoint
                )) -ne 0
            ) {
                throw "The private working path is not a regular directory."
            }
            Assert-M1CleanupTreeBounded -Root $Session.WorkingPath `
                -MaxBytes $Session.MaxTotalBytes
            [IO.Directory]::Delete($Session.WorkingPath, $true)
        }
        $manifest = Join-Path $Session.StagingBundle "bundle-manifest.json"
        if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
            throw "The staged bundle manifest is missing."
        }
        Invoke-M1PublicationFault -Session $Session `
            -Phase "before_validation"
        if ($null -ne $ValidationScriptBlock) {
            & $ValidationScriptBlock $Session.StagingBundle | Out-Null
        }
        else {
            if (
                [string]::IsNullOrWhiteSpace($ValidatorPath) -or
                [string]::IsNullOrWhiteSpace($PythonExecutable)
            ) {
                throw "The checked validator and Python executable are required."
            }
            $validator = Get-M1FullPath -Path $ValidatorPath
            $python = Get-M1FullPath -Path $PythonExecutable
            Assert-M1NoReparseComponents -Path $validator
            Assert-M1NoReparseComponents -Path $python
            $validationDetail = (
                & $python -B $validator bundle $Session.StagingBundle 2>&1 |
                    Out-String
            ).Trim()
            if ($LASTEXITCODE -ne 0) {
                if ($validationDetail.Length -gt 2000) {
                    $validationDetail = $validationDetail.Substring(0, 2000)
                }
                throw "Staged protocol validation failed: $validationDetail"
            }
        }
        Invoke-M1PublicationFault -Session $Session `
            -Phase "after_validation"

        $recheck = @(& $RecheckScriptBlock $Session)
        if (
            $recheck.Count -ne 1 -or
            $recheck[0] -isnot [bool] -or
            -not [bool]$recheck[0]
        ) {
            throw "The required pre-publication identity recheck failed."
        }
        Invoke-M1PublicationFault -Session $Session `
            -Phase "after_recheck"
        if (Test-Path -LiteralPath $Session.FinalDirectory) {
            throw "The immutable evidence directory appeared before publication."
        }
        Assert-M1LocalFixedVolume -Path $Session.OutputRoot
        Assert-M1NoReparseComponents -Path $Session.OutputRoot
        Assert-M1NoReparseComponents -Path $Session.CommitDirectory
        $finalParent = [IO.Path]::GetDirectoryName($Session.FinalDirectory)
        if (
            -not $finalParent.Equals(
                $Session.CommitDirectory,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "The immutable evidence parent changed before publication."
        }
        Invoke-M1PublicationFault -Session $Session -Phase "before_move"
        Assert-M1LocalFixedVolume -Path $Session.OutputRoot
        Assert-M1NoReparseComponents -Path $Session.OutputRoot
        Assert-M1NoReparseComponents -Path $Session.CommitDirectory
        if (Test-Path -LiteralPath $Session.FinalDirectory) {
            throw "The immutable evidence directory appeared at publication."
        }

        # This is the sole publication commit point. Directory.Move refuses a
        # racing destination. Only non-authoritative, best-effort empty-shell
        # cleanup follows; cleanup failure cannot revoke or alter the bundle.
        [IO.Directory]::Move(
            $Session.StagingBundle,
            $Session.FinalDirectory
        )
        try {
            $stagingCommit = [IO.Path]::GetDirectoryName(
                $Session.StagingBundle
            )
            [IO.Directory]::Delete($stagingCommit, $false)
            [IO.Directory]::Delete($Session.StagingRoot, $false)
        }
        catch {
            # The complete final bundle is already published. Never turn an
            # empty-shell cleanup failure into a false publication failure.
        }
    }
    catch {
        $original = $_
        try {
            Remove-M1PublicationStaging -Session $Session
        }
        catch {
            throw (
                $original.Exception.Message +
                " Staging cleanup also failed: " +
                $_.Exception.Message
            )
        }
        throw $original
    }
}

function Publish-M1Stage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Stage,
        [Parameter(Mandatory = $true)]
        [scriptblock]$RecheckScriptBlock,
        [string]$ValidatorPath,
        [string]$PythonExecutable,
        [scriptblock]$ValidationScriptBlock,
        [scriptblock]$FaultInjector
    )

    if ($null -ne $FaultInjector) {
        $Stage.FaultInjector = $FaultInjector
    }
    Complete-M1Publication -Session $Stage `
        -RecheckScriptBlock $RecheckScriptBlock `
        -ValidatorPath $ValidatorPath `
        -PythonExecutable $PythonExecutable `
        -ValidationScriptBlock $ValidationScriptBlock
}

function Invoke-M1AtomicPublication {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$OutputRoot,
        [Parameter(Mandatory = $true)]
        [string]$GitCommit,
        [Parameter(Mandatory = $true)]
        [string]$RunId,
        [Parameter(Mandatory = $true)]
        [scriptblock]$BuildBundleScriptBlock,
        [Parameter(Mandatory = $true)]
        [scriptblock]$RecheckScriptBlock,
        [long]$MaxFileBytes = $script:M1DefaultMaxFileBytes,
        [long]$MaxTotalBytes = $script:M1DefaultMaxTotalBytes,
        [string]$ValidatorPath,
        [string]$PythonExecutable,
        [scriptblock]$ValidationScriptBlock,
        [scriptblock]$FaultInjector
    )

    $session = $null
    try {
        $session = New-M1PublicationSession `
            -RepositoryRoot $RepositoryRoot `
            -OutputRoot $OutputRoot `
            -GitCommit $GitCommit `
            -RunId $RunId `
            -MaxFileBytes $MaxFileBytes `
            -MaxTotalBytes $MaxTotalBytes `
            -FaultInjector $FaultInjector
        Invoke-M1PublicationFault -Session $session `
            -Phase "after_stage_created"
        & $BuildBundleScriptBlock $session | Out-Null
        Complete-M1Publication -Session $session `
            -RecheckScriptBlock $RecheckScriptBlock `
            -ValidatorPath $ValidatorPath `
            -PythonExecutable $PythonExecutable `
            -ValidationScriptBlock $ValidationScriptBlock
    }
    catch {
        $original = $_
        if ($null -ne $session) {
            try {
                Remove-M1PublicationStaging -Session $session
            }
            catch {
                throw (
                    $original.Exception.Message +
                    " Staging cleanup also failed: " +
                    $_.Exception.Message
                )
            }
        }
        throw $original
    }
}
