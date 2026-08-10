Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M4R1MaximumCompanionBytes = 65536

function Get-M4R1ExclusiveFileObservation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Label is not an ordinary non-reparse file."
    }

    $stream = $null
    $hash = $null
    try {
        $stream = New-Object IO.FileStream(
            $item.FullName,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::None,
            65536,
            [IO.FileOptions]::SequentialScan
        )
        if ($stream.Length -gt $MaximumBytes) {
            throw "$Label violates its fixed byte bound."
        }
        $information = New-Object M4Phase.ByHandleFileInformation
        if (-not [M4Phase.NativeMethods]::GetFileInformationByHandle(
            $stream.SafeFileHandle,
            [ref]$information
        )) {
            $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "$Label identity query failed with Win32 error $code."
        }
        if (
            ($information.FileAttributes -band
                [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [long]$information.NumberOfLinks -ne 1
        ) {
            throw "$Label must be non-reparse and single-link."
        }
        $hash = [Security.Cryptography.SHA256]::Create()
        $buffer = New-Object byte[] 65536
        $total = [long]0
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($total -gt ($MaximumBytes - $read)) {
                throw "$Label exceeded its byte ceiling during read."
            }
            [void]$hash.TransformBlock($buffer, 0, $read, $buffer, 0)
            $total = [long]($total + $read)
        }
        if ($total -ne $stream.Length) {
            throw "$Label changed or ended during its exclusive read."
        }
        $empty = New-Object byte[] 0
        [void]$hash.TransformFinalBlock($empty, 0, 0)
        $fileIndex = (
            ([uint64]$information.FileIndexHigh * [uint64]4294967296) +
            [uint64]$information.FileIndexLow
        )
        return [pscustomobject][ordered]@{
            bytes = $total
            sha256 = Get-M4LowerHex -Bytes $hash.Hash
            file_identity = [ordered]@{
                volume_serial_number = (
                    "{0:x8}" -f [uint64]$information.VolumeSerialNumber
                )
                file_index = "{0:x16}" -f $fileIndex
                link_count = [long]$information.NumberOfLinks
            }
        }
    }
    finally {
        if ($null -ne $hash) { $hash.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function New-M4R1PostWorkerQuiescence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$WorkerResult,
        [Parameter(Mandatory = $true)][pscustomobject]$Paths,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [Parameter(Mandatory = $true)][long]$MaximumDatabaseBytes
    )

    $started = Get-M4BundleUtcTimestamp
    Assert-M1NoReparseComponents -Path $DatabasePath
    $databaseItem = Get-Item -LiteralPath $DatabasePath -Force
    if (
        $databaseItem.PSIsContainer -or
        ($databaseItem.Attributes -band
            [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "M4R1 post-worker database is not an ordinary file."
    }
    $database = Get-M4ClosedFileObservation `
        -DatabasePath $DatabasePath -MaximumBytes $MaximumDatabaseBytes
    if ([long]$database.file_identity.link_count -ne 1) {
        throw "M4R1 post-worker database must have one link."
    }
    $worker = $WorkerResult.post_close_file_observations
    if (
        [long]$database.bytes -ne [long]$worker.database_bytes -or
        [string]$database.sha256 -cne [string]$worker.database_sha256 -or
        [string]$database.prefix_sha256 -cne [string]$worker.prefix.sha256
    ) {
        throw "M4R1 database drifted after the worker observation."
    }

    $companionLocator = [string]$Paths.companion
    $companionPath = Get-M1PayloadPath `
        -Session $Session -RelativePath $companionLocator
    $companion = $null
    $companionPresent = [IO.File]::Exists($companionPath)
    if ([IO.Directory]::Exists($companionPath)) {
        throw "M4R1 canonical companion path is a directory."
    }
    if ($companionPresent) {
        $observedCompanion = Get-M4R1ExclusiveFileObservation `
            -Path $companionPath `
            -MaximumBytes $script:M4R1MaximumCompanionBytes `
            -Label "M4R1 companion"
        $companion = [ordered]@{
            state = "present"
            path = $companionLocator
            bytes = [long]$observedCompanion.bytes
            sha256 = [string]$observedCompanion.sha256
            file_identity = $observedCompanion.file_identity
            exclusive_open_verified = $true
            checked_after_worker_exit = $true
        }
    }
    else {
        $companion = [ordered]@{
            state = "absent"
            path = $companionLocator
            checked_after_worker_exit = $true
        }
    }

    $document = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_post_worker_quiescence"
        experiment_id = "DAO-M4-HEADER-DISCRIMINATOR-003"
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        worker_run_id = [string]$Invocation.worker_run_id
        worker_finished_at_utc = [string]$WorkerResult.finished_at_utc
        observation_started_at_utc = $started
        observation_completed_at_utc = Get-M4BundleUtcTimestamp
        worker_exit_wait_completed = $true
        database = [ordered]@{
            path = [string]$Invocation.database_path
            bytes = [long]$database.bytes
            sha256 = [string]$database.sha256
            prefix_sha256 = [string]$database.prefix_sha256
            file_identity = $database.file_identity
            exclusive_open_verified = $true
            matches_worker_post_close_observation = $true
        }
        companion = $companion
        status = "pass"
    }
    Write-M4BundleJson -Session $Session -Entries $Entries `
        -RelativePath ([string]$Paths.quiescence) `
        -Role "post_worker_quiescence" -Document $document `
        -MaximumBytes 64KB
    if ($companionPresent) {
        Register-M4WorkerPayload -Session $Session -Entries $Entries `
            -RelativePath $companionLocator -Role "companion" `
            -ExpectedSha256 ([string]$companion.sha256) `
            -ExpectedSizeBytes ([long]$companion.bytes)
    }
    return [pscustomobject][ordered]@{
        document = [pscustomobject]$document
        binding = Get-M4ArtifactBinding -Session $Session `
            -RelativePath ([string]$Paths.quiescence)
        companion_binding = if ($companionPresent) {
            Get-M4ArtifactBinding -Session $Session `
                -RelativePath $companionLocator
        }
        else { $null }
    }
}
