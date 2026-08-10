Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-M5ExclusiveCompanionObservation {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M5 companion is not an ordinary non-reparse file."
    }
    $stream = $null
    try {
        $stream = New-Object IO.FileStream(
            $item.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::None, 65536, [IO.FileOptions]::SequentialScan
        )
        if ($stream.Length -gt $MaximumBytes) {
            throw "M5 companion exceeds its byte bound."
        }
        $information = New-Object M4Phase.ByHandleFileInformation
        if (-not [M4Phase.NativeMethods]::GetFileInformationByHandle(
            $stream.SafeFileHandle, [ref]$information
        )) { throw "M5 companion identity query failed." }
        if ([long]$information.NumberOfLinks -ne 1 -or
            ($information.FileAttributes -band
                [uint32][IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "M5 companion must be single-link and non-reparse."
        }
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $sha = [BitConverter]::ToString(
                $hash.ComputeHash($stream)
            ).Replace("-", "").ToLowerInvariant()
        }
        finally { $hash.Dispose() }
        $index = ([uint64]$information.FileIndexHigh * [uint64]4294967296) +
            [uint64]$information.FileIndexLow
        return [pscustomobject][ordered]@{
            bytes = [long]$stream.Length
            sha256 = $sha
            file_identity = [ordered]@{
                volume_serial_number = "{0:x8}" -f
                    [uint64]$information.VolumeSerialNumber
                file_index = "{0:x16}" -f $index
                link_count = [long]$information.NumberOfLinks
            }
        }
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function New-M5PostWorkerQuiescence {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$WorkerResult,
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            "source_database", "compact_input_database",
            "compacted_database", "verify_database"
        )][string]$DatabaseRole,
        [Parameter(Mandatory = $true)][long]$MaximumDatabaseBytes,
        [Parameter(Mandatory = $true)][long]$MaximumCompanionBytes
    )
    $workerRows = @($WorkerResult.database_observations | Where-Object {
        $_.database_role -ceq $DatabaseRole
    })
    if ($workerRows.Count -ne 1) {
        throw "M5 quiescence has no unique worker database observation."
    }
    $worker = $workerRows[0]
    $databasePath = Get-M1PayloadPath -Session $Session `
        -RelativePath ([string]$worker.path)
    $started = Get-M5UtcTimestamp
    $database = Get-M4ClosedFileObservation `
        -DatabasePath $databasePath -MaximumBytes $MaximumDatabaseBytes
    if ([long]$database.file_identity.link_count -ne 1 -or
        [long]$database.bytes -ne [long]$worker.bytes -or
        [string]$database.sha256 -cne [string]$worker.sha256 -or
        [string]$database.prefix_sha256 -cne
            [string]$worker.prefix_sha256) {
        throw "M5 database drifted after the responsible worker exited."
    }
    $companionLocator = Get-M5CompanionLocator `
        -DatabaseLocator ([string]$worker.path)
    $companionPath = Get-M1PayloadPath -Session $Session `
        -RelativePath $companionLocator
    if ([IO.Directory]::Exists($companionPath)) {
        throw "M5 canonical companion locator is a directory."
    }
    $present = [IO.File]::Exists($companionPath)
    $companion = if ($present) {
        $observation = Get-M5ExclusiveCompanionObservation `
            -Path $companionPath -MaximumBytes $MaximumCompanionBytes
        [ordered]@{
            state = "present"
            path = $companionLocator
            bytes = [long]$observation.bytes
            sha256 = [string]$observation.sha256
            file_identity = $observation.file_identity
            exclusive_open_verified = $true
            checked_after_worker_exit = $true
        }
    }
    else {
        [ordered]@{
            state = "absent"
            path = $companionLocator
            checked_after_worker_exit = $true
        }
    }
    $relativePath = Get-M5QuiescencePath `
        -SampleId ([string]$Invocation.sample_id) `
        -DatabaseRole $DatabaseRole
    $document = [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_post_worker_quiescence"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        worker_run_id = [string]$Invocation.worker_run_id
        database_role = $DatabaseRole
        worker_finished_at_utc = [string]$WorkerResult.finished_at_utc
        observation_started_at_utc = $started
        observation_completed_at_utc = Get-M5UtcTimestamp
        worker_exit_wait_completed = $true
        database = [ordered]@{
            path = [string]$worker.path
            bytes = [long]$database.bytes
            sha256 = [string]$database.sha256
            prefix_sha256 = [string]$database.prefix_sha256
            file_identity = $database.file_identity
            exclusive_open_verified = $true
            matches_worker_observation = $true
        }
        companion = $companion
        status = "pass"
    }
    Write-M5BundleJson -Session $Session -Entries $Entries `
        -RelativePath $relativePath -Role "post_worker_quiescence" `
        -Document $document -MaximumBytes 16KB
    if ($present) {
        Add-M5ManifestEntry -Entries $Entries -Session $Session `
            -RelativePath $companionLocator -Role "companion" `
            -ExpectedSha256 ([string]$companion.sha256) `
            -ExpectedBytes ([long]$companion.bytes)
    }
    return [pscustomobject][ordered]@{
        document = [pscustomobject]$document
        artifact = Get-M5ArtifactBinding -Session $Session `
            -RelativePath $relativePath
        companion_artifact = if ($present) {
            Get-M5ArtifactBinding -Session $Session `
                -RelativePath $companionLocator
        } else { $null }
    }
}

function Assert-M5NoUnexpectedCompanions {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample
    )
    $sampleRoot = Get-M1PayloadPath -Session $Session `
        -RelativePath "evidence/samples/$($Sample.sample_id)"
    $allowed = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($locator in @(
        $Sample.source_database_path,
        $Sample.compact_input_database_path,
        $Sample.compacted_database_path,
        $Sample.verify_database_path
    )) {
        [void]$allowed.Add([IO.Path]::GetFullPath(
            (Get-M1PayloadPath -Session $Session `
                -RelativePath (Get-M5CompanionLocator `
                    -DatabaseLocator ([string]$locator)))
        ))
    }
    foreach ($path in [IO.Directory]::GetFiles($sampleRoot, "*.ldb")) {
        if (-not $allowed.Contains([IO.Path]::GetFullPath($path))) {
            throw "M5 sample contains an unexpected companion locator."
        }
    }
}
