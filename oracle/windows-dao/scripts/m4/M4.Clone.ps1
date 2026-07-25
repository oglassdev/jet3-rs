Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M4CloneMinimumBytes = 2048
$script:M4CloneMaximumBytes = 1MB
$script:M4CloneBufferBytes = 65536
$script:M4CloneMaximumPathCharacters = 1024

if ($null -eq ("M4Clone.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace M4Clone {
    [StructLayout(LayoutKind.Sequential)]
    public struct FileTime {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation {
        public uint FileAttributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    public static class NativeMethods {
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information
        );

        [DllImport(
            "kernel32.dll",
            CharSet = CharSet.Unicode,
            SetLastError = true
        )]
        public static extern uint GetFinalPathNameByHandle(
            SafeFileHandle file,
            StringBuilder path,
            uint pathCharacters,
            uint flags
        );
    }
}
"@
}

function Get-M4CloneUtcTimestamp {
    return [DateTime]::UtcNow.ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Get-M4CloneLocalFullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        $Path.Length -gt $script:M4CloneMaximumPathCharacters -or
        $Path.IndexOf([char]0) -ge 0
    ) {
        throw "$Label path is empty, contains NUL, or exceeds its bound."
    }
    if (
        $Path.StartsWith("\\", [StringComparison]::Ordinal) -or
        $Path.IndexOf("/") -ge 0 -or
        $Path -cnotmatch "^[A-Za-z]:\\"
    ) {
        throw "$Label path must be an absolute local Windows path, not UNC."
    }
    if ($Path.Substring(2).Contains(":")) {
        throw "$Label alternate data stream paths are forbidden."
    }

    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Length -gt $script:M4CloneMaximumPathCharacters) {
        throw "$Label canonical path exceeds its bound."
    }
    $supplied = $Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
    $canonical = $full.TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not $supplied.Equals(
        $canonical,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label path aliases and non-canonical paths are forbidden."
    }
    if ($canonical -ceq [IO.Path]::GetPathRoot($canonical)) {
        throw "$Label may not be a drive root."
    }
    return $canonical
}

function Assert-M4ClonePathInsideRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ControllerRoot,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $prefix = $ControllerRoot.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $Path.StartsWith(
        $prefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label path escapes the controller root."
    }
}

function Assert-M4CloneNoReparseComponents {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    $relative = $full.Substring($root.Length)
    $current = $root.TrimEnd([IO.Path]::DirectorySeparatorChar)
    foreach ($part in $relative.Split(
        [IO.Path]::DirectorySeparatorChar,
        [StringSplitOptions]::RemoveEmptyEntries
    )) {
        $current = Join-Path $current $part
        if (
            -not [IO.File]::Exists($current) -and
            -not [IO.Directory]::Exists($current)
        ) {
            throw "$Label path contains a missing component."
        }
        $attributes = [IO.File]::GetAttributes($current)
        if (
            ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label path contains a forbidden reparse point."
        }
    }
}

function Assert-M4CloneRegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (
        -not [IO.File]::Exists($Path) -or
        [IO.Directory]::Exists($Path)
    ) {
        throw "$Label must be an ordinary file."
    }
    $attributes = [IO.File]::GetAttributes($Path)
    $forbidden = (
        [IO.FileAttributes]::Directory -bor
        [IO.FileAttributes]::Device -bor
        [IO.FileAttributes]::ReparsePoint
    )
    if (($attributes -band $forbidden) -ne 0) {
        throw "$Label must be an ordinary non-reparse file."
    }
}

function Get-M4CloneHandleFacts {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream]$Stream,
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $information = New-Object M4Clone.ByHandleFileInformation
    $ok = [M4Clone.NativeMethods]::GetFileInformationByHandle(
        $Stream.SafeFileHandle,
        [ref]$information
    )
    if (-not $ok) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "$Label file identity query failed with Win32 error $code."
    }

    $builder = New-Object Text.StringBuilder 4096
    $characters = [M4Clone.NativeMethods]::GetFinalPathNameByHandle(
        $Stream.SafeFileHandle,
        $builder,
        [uint32]$builder.Capacity,
        [uint32]0
    )
    if (
        $characters -eq 0 -or
        $characters -ge [uint32]$builder.Capacity
    ) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "$Label final-path query failed with Win32 error $code."
    }
    $finalPath = $builder.ToString()
    if ($finalPath.StartsWith("\\?\UNC\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label resolved to a forbidden UNC path."
    }
    if ($finalPath.StartsWith("\\?\", [StringComparison]::Ordinal)) {
        $finalPath = $finalPath.Substring(4)
    }
    if (-not $finalPath.Equals(
        $ExpectedPath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label path alias resolved to a different canonical path."
    }

    $fileIndex = (
        ([uint64]$information.FileIndexHigh * [uint64]4294967296) +
        [uint64]$information.FileIndexLow
    )
    $bytes = (
        ([uint64]$information.FileSizeHigh * [uint64]4294967296) +
        [uint64]$information.FileSizeLow
    )
    return [pscustomobject][ordered]@{
        volume_serial_number = (
            "{0:x8}" -f [uint64]$information.VolumeSerialNumber
        )
        file_index = "{0:x16}" -f $fileIndex
        link_count = [long]$information.NumberOfLinks
        bytes = [long]$bytes
    }
}

function Test-M4CloneSameIdentity {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Left,
        [Parameter(Mandatory = $true)][pscustomobject]$Right
    )

    return (
        $Left.volume_serial_number -ceq $Right.volume_serial_number -and
        $Left.file_index -ceq $Right.file_index
    )
}

function ConvertTo-M4CloneIdentityObservation {
    param([Parameter(Mandatory = $true)][pscustomobject]$Facts)

    return [ordered]@{
        volume_serial_number = [string]$Facts.volume_serial_number
        file_index = [string]$Facts.file_index
        link_count = [long]$Facts.link_count
    }
}

function ConvertTo-M4CloneLowerHex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    return [BitConverter]::ToString($Bytes).Replace(
        "-",
        ""
    ).ToLowerInvariant()
}

function Get-M4CloneStreamSha256 {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream]$Stream,
        [Parameter(Mandatory = $true)][long]$MaximumBytes,
        [Parameter(Mandatory = $true)][string]$Label
    )

    [void]$Stream.Seek(0, [IO.SeekOrigin]::Begin)
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $buffer = New-Object byte[] $script:M4CloneBufferBytes
        $total = [long]0
        while (($read = $Stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($total -gt ($MaximumBytes - $read)) {
                throw "$Label exceeded the clone byte ceiling."
            }
            $total = [long]($total + $read)
            [void]$hash.TransformBlock($buffer, 0, $read, $buffer, 0)
        }
        $empty = New-Object byte[] 0
        [void]$hash.TransformFinalBlock($empty, 0, 0)
        return [pscustomobject][ordered]@{
            bytes = $total
            sha256 = ConvertTo-M4CloneLowerHex -Bytes $hash.Hash
        }
    }
    finally {
        $hash.Dispose()
    }
}

function Get-M4CloneRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ControllerRoot
    )

    $prefix = $ControllerRoot.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    return $Path.Substring($prefix.Length).Replace(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Invoke-M4CloneFault {
    param(
        [AllowNull()][scriptblock]$FaultInjector,
        [Parameter(Mandatory = $true)][string]$Phase
    )

    if ($null -ne $FaultInjector) {
        $null = & $FaultInjector $Phase
    }
}

function Remove-M4ClonePartialDestination {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][pscustomobject]$CreatedFacts
    )

    if (
        -not [IO.File]::Exists($Path) -and
        -not [IO.Directory]::Exists($Path)
    ) {
        return
    }
    if ($null -eq $CreatedFacts -or [IO.Directory]::Exists($Path)) {
        throw "Partial clone cleanup refused an unbound replacement."
    }
    $stream = New-Object IO.FileStream(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $current = Get-M4CloneHandleFacts `
            -Stream $stream -ExpectedPath $Path `
            -Label "Partial destination"
        if (-not (Test-M4CloneSameIdentity `
            -Left $CreatedFacts -Right $current)) {
            throw "Partial clone cleanup refused an identity replacement."
        }
    }
    finally {
        $stream.Dispose()
    }
    [IO.File]::Delete($Path)
    if ([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)) {
        throw "Partial clone destination could not be removed."
    }
}

function Invoke-M4BoundedClone {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ControllerRoot,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [long]$MaximumBytes = 1MB,
        [AllowNull()][scriptblock]$FaultInjector = $null
    )

    if (
        $MaximumBytes -lt $script:M4CloneMinimumBytes -or
        $MaximumBytes -gt $script:M4CloneMaximumBytes
    ) {
        throw "M4 clone byte ceiling must be between 2048 and 1048576."
    }
    $startedAt = Get-M4CloneUtcTimestamp
    $root = Get-M4CloneLocalFullPath `
        -Path $ControllerRoot -Label "Controller root"
    $source = Get-M4CloneLocalFullPath -Path $SourcePath -Label "Source"
    $destination = Get-M4CloneLocalFullPath `
        -Path $DestinationPath -Label "Destination"
    Assert-M4ClonePathInsideRoot `
        -Path $source -ControllerRoot $root -Label "Source"
    Assert-M4ClonePathInsideRoot `
        -Path $destination -ControllerRoot $root -Label "Destination"
    if ($source.Equals(
        $destination,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Source and destination path aliases are forbidden."
    }
    if (
        [IO.Path]::GetPathRoot($source) -ine
        [IO.Path]::GetPathRoot($destination)
    ) {
        throw "Source and destination must be on the same local volume."
    }

    Assert-M4CloneNoReparseComponents -Path $root -Label "Controller root"
    Assert-M4CloneNoReparseComponents -Path $source -Label "Source"
    Assert-M4CloneRegularFile -Path $source -Label "Source"
    $destinationParent = [IO.Path]::GetDirectoryName($destination)
    Assert-M4CloneNoReparseComponents `
        -Path $destinationParent -Label "Destination parent"
    if (
        [IO.File]::Exists($destination) -or
        [IO.Directory]::Exists($destination)
    ) {
        throw "Destination already exists; CreateNew clone refused."
    }

    $input = $null
    $output = $null
    $verification = $null
    $destinationCreated = $false
    $destinationCreatedFacts = $null
    $verified = $false
    try {
        $input = New-Object IO.FileStream(
            $source,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::None,
            $script:M4CloneBufferBytes,
            [IO.FileOptions]::SequentialScan
        )
        $sourceBefore = Get-M4CloneHandleFacts `
            -Stream $input -ExpectedPath $source -Label "Source"
        if (
            $sourceBefore.link_count -ne 1 -or
            $sourceBefore.bytes -lt $script:M4CloneMinimumBytes -or
            $sourceBefore.bytes -gt $MaximumBytes
        ) {
            throw "Source is hard-linked or outside the M4 database byte bounds."
        }

        $output = New-Object IO.FileStream(
            $destination,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            $script:M4CloneBufferBytes,
            [IO.FileOptions]::WriteThrough
        )
        $destinationCreated = $true
        $destinationCreatedFacts = Get-M4CloneHandleFacts `
            -Stream $output -ExpectedPath $destination `
            -Label "Created destination"
        Invoke-M4CloneFault `
            -FaultInjector $FaultInjector -Phase "after_destination_create"
        if (
            $destinationCreatedFacts.link_count -ne 1 -or
            $sourceBefore.volume_serial_number -cne
                $destinationCreatedFacts.volume_serial_number -or
            (Test-M4CloneSameIdentity `
                -Left $sourceBefore -Right $destinationCreatedFacts)
        ) {
            throw "Clone identities are linked, equal, or on different volumes."
        }

        $sourceHash = [Security.Cryptography.SHA256]::Create()
        try {
            $buffer = New-Object byte[] $script:M4CloneBufferBytes
            $copied = [long]0
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                if ($copied -gt ($MaximumBytes - $read)) {
                    throw "Source exceeded the clone byte ceiling."
                }
                $copied = [long]($copied + $read)
                [void]$sourceHash.TransformBlock(
                    $buffer,
                    0,
                    $read,
                    $buffer,
                    0
                )
                $output.Write($buffer, 0, $read)
            }
            $empty = New-Object byte[] 0
            [void]$sourceHash.TransformFinalBlock($empty, 0, 0)
            $sourceShaBefore = ConvertTo-M4CloneLowerHex `
                -Bytes $sourceHash.Hash
        }
        finally {
            $sourceHash.Dispose()
        }
        if (
            $copied -ne $sourceBefore.bytes -or
            $input.Length -ne $sourceBefore.bytes -or
            $output.Length -ne $sourceBefore.bytes
        ) {
            throw "Source size changed during the exact clone."
        }
        $output.Flush($true)
        Invoke-M4CloneFault `
            -FaultInjector $FaultInjector -Phase "after_destination_flush"
        $output.Dispose()
        $output = $null

        Assert-M4CloneNoReparseComponents `
            -Path $destination -Label "Destination"
        Assert-M4CloneRegularFile -Path $destination -Label "Destination"
        $verification = New-Object IO.FileStream(
            $destination,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::None,
            $script:M4CloneBufferBytes,
            [IO.FileOptions]::SequentialScan
        )
        $destinationVerified = Get-M4CloneHandleFacts `
            -Stream $verification -ExpectedPath $destination `
            -Label "Verified destination"
        if (
            -not (Test-M4CloneSameIdentity `
                -Left $destinationCreatedFacts -Right $destinationVerified) -or
            $destinationVerified.link_count -ne 1
        ) {
            throw "Destination identity changed or became hard-linked."
        }
        $destinationHash = Get-M4CloneStreamSha256 `
            -Stream $verification -MaximumBytes $MaximumBytes `
            -Label "Destination"
        $sourceAfter = Get-M4CloneStreamSha256 `
            -Stream $input -MaximumBytes $MaximumBytes -Label "Source"
        $sourceFinal = Get-M4CloneHandleFacts `
            -Stream $input -ExpectedPath $source -Label "Source"

        if (
            -not (Test-M4CloneSameIdentity `
                -Left $sourceBefore -Right $sourceFinal) -or
            $sourceFinal.link_count -ne 1 -or
            $sourceBefore.bytes -ne $sourceFinal.bytes -or
            $sourceBefore.bytes -ne $destinationVerified.bytes -or
            $sourceBefore.bytes -ne $destinationHash.bytes -or
            $sourceBefore.bytes -ne $sourceAfter.bytes -or
            $sourceShaBefore -cne $sourceAfter.sha256 -or
            $sourceShaBefore -cne $destinationHash.sha256
        ) {
            throw "Three-way clone size, hash, or identity verification failed."
        }
        Assert-M4CloneNoReparseComponents -Path $source -Label "Source"
        Assert-M4CloneNoReparseComponents `
            -Path $destination -Label "Destination"
        $verified = $true
    }
    finally {
        if ($null -ne $verification) {
            $verification.Dispose()
        }
        if ($null -ne $output) {
            $output.Dispose()
        }
        if ($null -ne $input) {
            $input.Dispose()
        }
        if (-not $verified -and $destinationCreated) {
            Remove-M4ClonePartialDestination `
                -Path $destination -CreatedFacts $destinationCreatedFacts
        }
    }

    $completedAt = Get-M4CloneUtcTimestamp
    return [pscustomobject][ordered]@{
        started_at_utc = $startedAt
        completed_at_utc = $completedAt
        source_path = Get-M4CloneRelativePath `
            -Path $source -ControllerRoot $root
        destination_path = Get-M4CloneRelativePath `
            -Path $destination -ControllerRoot $root
        source_bytes = [long]$sourceBefore.bytes
        destination_bytes = [long]$destinationVerified.bytes
        source_sha256_before_clone = [string]$sourceShaBefore
        source_sha256_after_clone = [string]$sourceAfter.sha256
        destination_sha256 = [string]$destinationHash.sha256
        source_file_identity = (
            ConvertTo-M4CloneIdentityObservation -Facts $sourceFinal
        )
        destination_file_identity = (
            ConvertTo-M4CloneIdentityObservation -Facts $destinationVerified
        )
        all_hashes_equal = $true
        exact_byte_clone = $true
        source_reparse_free = $true
        destination_reparse_free = $true
        no_hardlink = $true
        same_volume = $true
        distinct_file_identity = $true
        status = "pass"
    }
}
