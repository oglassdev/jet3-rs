Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M4MaximumDatabaseBytes = 1MB
$script:M4PrefixBytes = 2048
$script:M4FileBufferBytes = 65536
$script:M4MaximumTableDefinitions = 32
$script:M4SystemTableMask = -2147483646

if ($null -eq ("M4Phase.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace M4Phase {
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
    }
}
"@
}

function Get-M4UtcTimestamp {
    return [DateTime]::UtcNow.ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Add-M4OperationEntry {
    param(
        [Parameter(Mandatory = $true)]
        [Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)]
        [string]$Action
    )

    if ($Entries.Count -ge 10) {
        throw "M4 operation log exceeded its ten-entry ceiling."
    }
    [void]$Entries.Add([ordered]@{
        sequence = [int]($Entries.Count + 1)
        timestamp_utc = Get-M4UtcTimestamp
        action = $Action
        status = "pass"
    })
}

function Get-M4LowerHex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    return [BitConverter]::ToString($Bytes).Replace(
        "-",
        ""
    ).ToLowerInvariant()
}

function Get-M4BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return Get-M4LowerHex -Bytes $hash.ComputeHash($Bytes)
    }
    finally {
        $hash.Dispose()
    }
}

function Get-M4LockFilePath {
    param([Parameter(Mandatory = $true)][string]$DatabasePath)

    return [IO.Path]::ChangeExtension($DatabasePath, ".ldb")
}

function Assert-M4LockFileAbsent {
    param([Parameter(Mandatory = $true)][string]$DatabasePath)

    $lockPath = Get-M4LockFilePath -DatabasePath $DatabasePath
    if (
        [IO.File]::Exists($lockPath) -or
        [IO.Directory]::Exists($lockPath)
    ) {
        throw "The DAO lock file remains present after database close."
    }
}

function Read-M4EmptyUserSchema {
    param([Parameter(Mandatory = $true)][object]$Database)

    $tableDefinitions = $null
    $cleanupErrors = New-Object Collections.ArrayList
    $primaryError = $null
    $userTableCount = 0
    try {
        $tableDefinitions = $Database.TableDefs
        $tableDefinitions.Refresh()
        $count = [int]$tableDefinitions.Count
        if (
            $count -lt 0 -or
            $count -gt $script:M4MaximumTableDefinitions
        ) {
            throw "DAO TableDefs.Count exceeds the M4 empty-schema bound."
        }
        for ($index = 0; $index -lt $count; $index++) {
            $tableDefinition = $null
            try {
                $tableDefinition = $tableDefinitions.Item([int]$index)
                $attributes = [int]$tableDefinition.Attributes
                if (
                    ($attributes -band $script:M4SystemTableMask) -eq 0
                ) {
                    $userTableCount++
                }
            }
            finally {
                Release-M1ComObject -Value $tableDefinition `
                    -CleanupErrors $cleanupErrors `
                    -Label "DAO TableDef release"
            }
        }
        if ($userTableCount -ne 0) {
            throw "M4 requires an empty user schema."
        }
    }
    catch {
        $primaryError = $_
    }
    finally {
        Release-M1ComObject -Value $tableDefinitions `
            -CleanupErrors $cleanupErrors `
            -Label "DAO TableDefs release"
    }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "M4 empty-schema read"
    return $userTableCount
}

function Invoke-M4DaoPhase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [Parameter(Mandatory = $true)]
        [ValidateSet("creator", "reopen")][string]$PhaseId,
        [Parameter(Mandatory = $true)][pscustomobject]$PhaseContract,
        [Parameter(Mandatory = $true)][pscustomobject]$AcceptedProvider,
        [Parameter(Mandatory = $true)]
        [Collections.ArrayList]$OperationEntries
    )

    $engine = $null
    $workspaces = $null
    $workspace = $null
    $database = $null
    $cleanupErrors = New-Object Collections.ArrayList
    $primaryError = $null
    $snapshot = $null
    try {
        $providerType = [Type]::GetTypeFromProgID(
            [string]$AcceptedProvider.prog_id,
            $false
        )
        if ($null -eq $providerType) {
            throw "The rebound DAO ProgID has no activation type."
        }
        $actualClsid = $providerType.GUID.ToString(
            "B"
        ).ToUpperInvariant()
        $expectedClsid = ([string]$AcceptedProvider.clsid).ToUpperInvariant()
        if ($actualClsid -cne $expectedClsid) {
            throw "DAO activation type CLSID differs from the rebound provider."
        }
        $engine = [Activator]::CreateInstance($providerType)
        $engineVersion = [string]$engine.Version
        if (
            $engineVersion -cne
                [string]$AcceptedProvider.provider_version
        ) {
            throw "DBEngine.Version differs from the rebound provider."
        }
        Add-M4OperationEntry -Entries $OperationEntries `
            -Action "com_activated"
        $workspaces = $engine.Workspaces
        $workspace = $workspaces.Item([int]0)

        if ($PhaseId -ceq "creator") {
            $database = $workspace.CreateDatabase(
                $DatabasePath,
                [string]$PhaseContract.locale,
                [int]$PhaseContract.create_option_value
            )
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "database_created"
        }
        else {
            $database = $workspace.OpenDatabase($DatabasePath)
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "database_opened"
        }

        $version = [string]$database.Version
        if (
            $version -cne
                [string]$PhaseContract.expected_dao_version
        ) {
            throw "Database.Version differs from the exact M4 expectation."
        }
        Add-M4OperationEntry -Entries $OperationEntries `
            -Action "version_read"

        $userTableCount = Read-M4EmptyUserSchema -Database $database
        Add-M4OperationEntry -Entries $OperationEntries `
            -Action "empty_schema_read"
        $snapshot = [ordered]@{
            captured_at_utc = Get-M4UtcTimestamp
            dao_version = $version
            empty_user_schema = $true
            user_table_count = [int]$userTableCount
        }
    }
    catch {
        $primaryError = $_
    }
    finally {
        Close-M1ComObject -Value $database `
            -CleanupErrors $cleanupErrors -Label "DAO Database close"
        Release-M1ComObject -Value $database `
            -CleanupErrors $cleanupErrors -Label "DAO Database release"
        Release-M1ComObject -Value $workspace `
            -CleanupErrors $cleanupErrors -Label "DAO Workspace release"
        Release-M1ComObject -Value $workspaces `
            -CleanupErrors $cleanupErrors -Label "DAO Workspaces release"
        Release-M1ComObject -Value $engine `
            -CleanupErrors $cleanupErrors -Label "DAO DBEngine release"
    }
    try {
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    catch {
        $finalizationDetail = Get-M1SafeText -Value (
            "Post-release finalization: " + $_.Exception.Message
        ) -Maximum 1000
        [void]$cleanupErrors.Add($finalizationDetail)
    }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "M4 DAO phase"
    Add-M4OperationEntry -Entries $OperationEntries `
        -Action "database_closed"
    return [pscustomobject]$snapshot
}

function Get-M4ClosedFileObservation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [long]$MaximumBytes = 1MB
    )

    if (
        $MaximumBytes -lt $script:M4PrefixBytes -or
        $MaximumBytes -gt $script:M4MaximumDatabaseBytes
    ) {
        throw "M4 database byte ceiling is outside its fixed bounds."
    }
    $stream = $null
    $hash = $null
    try {
        $stream = New-Object IO.FileStream(
            $DatabasePath,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::None,
            $script:M4FileBufferBytes,
            [IO.FileOptions]::SequentialScan
        )
        $hash = [Security.Cryptography.SHA256]::Create()
        $fileInformation = New-Object M4Phase.ByHandleFileInformation
        $identityRead = [M4Phase.NativeMethods]::GetFileInformationByHandle(
            $stream.SafeFileHandle,
            [ref]$fileInformation
        )
        if (-not $identityRead) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "M4 database identity query failed with Win32 error $code."
        }
        $fileIndex = (
            ([uint64]$fileInformation.FileIndexHigh *
                [uint64]4294967296) +
            [uint64]$fileInformation.FileIndexLow
        )
        if (
            $stream.Length -lt $script:M4PrefixBytes -or
            $stream.Length -gt $MaximumBytes
        ) {
            throw "Closed M4 database size is outside its fixed bounds."
        }
        $prefix = New-Object byte[] $script:M4PrefixBytes
        $buffer = New-Object byte[] $script:M4FileBufferBytes
        $total = [long]0
        $prefixOffset = 0
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($total -gt ($MaximumBytes - $read)) {
                throw "Closed M4 database exceeded its byte ceiling."
            }
            if ($prefixOffset -lt $prefix.Length) {
                $copy = [Math]::Min(
                    $read,
                    $prefix.Length - $prefixOffset
                )
                [Array]::Copy(
                    $buffer,
                    0,
                    $prefix,
                    $prefixOffset,
                    $copy
                )
                $prefixOffset += $copy
            }
            [void]$hash.TransformBlock(
                $buffer,
                0,
                $read,
                $buffer,
                0
            )
            $total = [long]($total + $read)
        }
        if (
            $total -ne $stream.Length -or
            $prefixOffset -ne $script:M4PrefixBytes
        ) {
            throw "Closed M4 database changed or ended during observation."
        }
        $empty = New-Object byte[] 0
        [void]$hash.TransformFinalBlock($empty, 0, 0)
        return [pscustomobject][ordered]@{
            bytes = $total
            sha256 = Get-M4LowerHex -Bytes $hash.Hash
            prefix = $prefix
            prefix_sha256 = Get-M4BytesSha256 -Bytes $prefix
            file_identity = [ordered]@{
                volume_serial_number = (
                    "{0:x8}" -f
                    [uint64]$fileInformation.VolumeSerialNumber
                )
                file_index = "{0:x16}" -f $fileIndex
                link_count = [long]$fileInformation.NumberOfLinks
            }
        }
    }
    finally {
        if ($null -ne $hash) { $hash.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function ConvertTo-M4JsonBytes {
    param(
        [Parameter(Mandatory = $true)][object]$Document,
        [long]$MaximumBytes = 1MB
    )

    $json = $Document | ConvertTo-Json -Depth 32 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        $json + "`n"
    )
    if ($bytes.Length -lt 2 -or $bytes.Length -gt $MaximumBytes) {
        throw "M4 JSON artifact exceeds its byte bound."
    }
    return ,$bytes
}

function Write-M4CreateNewBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [long]$MaximumBytes = 1MB
    )

    if ($Bytes.Length -lt 1 -or $Bytes.Length -gt $MaximumBytes) {
        throw "M4 artifact exceeds its byte bound."
    }
    $stream = New-Object IO.FileStream(
        $Path,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}
