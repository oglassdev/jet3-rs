Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M5ProtocolVersion = "1.0.0"
$script:M5ExperimentId = "DAO-M5-COMPACT-CONFIRM-007"
$script:M5ExpectedM4ManifestSha256 = (
    "0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d"
)
$script:M5MinimumPayloadFiles = 2703
$script:M5MaximumPayloadFiles = 3135

function Get-M5UtcTimestamp {
    return [DateTime]::UtcNow.ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffffZ",
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function ConvertTo-M5JsonBytes {
    param(
        [Parameter(Mandatory = $true)][object]$Document,
        [long]$MaximumBytes = 16MB
    )
    $json = $Document | ConvertTo-Json -Depth 32 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes(
        $json + "`n"
    )
    if ($bytes.Length -gt $MaximumBytes) {
        throw "M5 JSON exceeds its byte bound."
    }
    return ,$bytes
}

function Read-M5Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$MaximumBytes = 16MB
    )
    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -lt 1 -or
        $item.Length -gt $MaximumBytes -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M5 JSON is not a bounded ordinary file."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
        $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf) {
        throw "M5 JSON contains a forbidden UTF-8 BOM."
    }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    return [pscustomobject]@{
        bytes = $bytes
        document = ($text | ConvertFrom-Json)
    }
}

function Get-M5FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = New-Object IO.FileStream(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan
    )
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hash.ComputeHash($stream)
        ).Replace("-", "").ToLowerInvariant()
    }
    finally { $hash.Dispose(); $stream.Dispose() }
}

function Get-M5MediaType {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    if ($RelativePath.EndsWith(".json", [StringComparison]::Ordinal)) {
        return "application/json"
    }
    return "application/octet-stream"
}

function Add-M5ManifestEntry {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Role,
        [AllowEmptyCollection()][string[]]$AllowedExistingRoles = @(),
        [AllowNull()][string]$ExpectedSha256,
        [long]$ExpectedBytes = -1
    )
    $aliases = @($Entries | Where-Object {
        [string]$_.path -ieq $RelativePath
    })
    if ($aliases.Count -gt 0) {
        if ($aliases.Count -eq 1 -and
            [string]$aliases[0].path -ceq $RelativePath -and
            $AllowedExistingRoles -ccontains [string]$aliases[0].role) {
            return
        }
        throw "M5 manifest path is duplicate or case-aliased: $RelativePath"
    }
    $path = Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
    Assert-M1NoReparseComponents -Path $path
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M5 manifest payload is not an ordinary file."
    }
    $sha = Get-M5FileSha256 -Path $path
    if ((-not [string]::IsNullOrEmpty($ExpectedSha256) -and
            $sha -cne $ExpectedSha256) -or
        ($ExpectedBytes -ge 0 -and [long]$item.Length -ne $ExpectedBytes)) {
        throw "M5 payload differs from its expected binding."
    }
    [void]$Entries.Add([ordered]@{
        path = $RelativePath
        role = $Role
        size_bytes = [long]$item.Length
        sha256 = $sha
        media_type = Get-M5MediaType -RelativePath $RelativePath
    })
}

function Write-M5BundleJson {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][object]$Document,
        [long]$MaximumBytes = 16MB
    )
    $bytes = ConvertTo-M5JsonBytes -Document $Document `
        -MaximumBytes $MaximumBytes
    Write-M1DurableBytes -Session $Session `
        -RelativePath $RelativePath -Bytes $bytes
    Add-M5ManifestEntry -Entries $Entries -Session $Session `
        -RelativePath $RelativePath -Role $Role
}

function Get-M5ArtifactBinding {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    return [ordered]@{
        path = $RelativePath
        sha256 = Get-M5FileSha256 -Path (
            Get-M1PayloadPath -Session $Session -RelativePath $RelativePath
        )
    }
}

function Get-M5PhasePaths {
    param(
        [Parameter(Mandatory = $true)][string]$SampleId,
        [Parameter(Mandatory = $true)]
        [ValidateSet("source", "compact", "verify")][string]$PhaseId
    )
    $root = "evidence/samples/$SampleId"
    $upper = $PhaseId.ToUpperInvariant()
    return [pscustomobject][ordered]@{
        invocation = "$root/$PhaseId-invocation.json"
        result = "$root/$upper-worker-result.json"
        operation_log = "$root/$upper-operation-log.json"
        snapshot = if ($PhaseId -ceq "compact") { $null } else {
            "$root/$upper-snapshot.json"
        }
        prefix = "$root/$upper.prefix.bin"
        failure = "$root/$upper-worker-error.json"
    }
}

function Get-M5QuiescencePath {
    param(
        [Parameter(Mandatory = $true)][string]$SampleId,
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            "source_database", "compact_input_database",
            "compacted_database", "verify_database"
        )][string]$DatabaseRole
    )
    return "evidence/quiescence/$SampleId/$DatabaseRole.json"
}

function Get-M5CompanionLocator {
    param([Parameter(Mandatory = $true)][string]$DatabaseLocator)
    if (-not $DatabaseLocator.EndsWith(".MDB", [StringComparison]::Ordinal)) {
        throw "M5 database locator must have the exact uppercase .MDB suffix."
    }
    return $DatabaseLocator.Substring(0, $DatabaseLocator.Length - 4) + ".ldb"
}

function Get-M5Nonce {
    param(
        [Parameter(Mandatory = $true)][string]$CampaignRunId,
        [Parameter(Mandatory = $true)][string]$SampleId,
        [Parameter(Mandatory = $true)][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal
    )
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes(
        "$CampaignRunId|$SampleId|$PhaseId|$WorkerOrdinal"
    )
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hash.ComputeHash($bytes)
        ).Replace("-", "").ToLowerInvariant().Substring(0, 32)
    }
    finally { $hash.Dispose() }
}

function New-M5Invocation {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)]
        [ValidateSet("source", "compact", "verify")][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][pscustomobject]$M4Manifest
    )
    $paths = Get-M5PhasePaths -SampleId $Sample.sample_id -PhaseId $PhaseId
    $databasePaths = switch ($PhaseId) {
        "source" { [ordered]@{
            source_database = [string]$Sample.source_database_path
        } }
        "compact" { [ordered]@{
            compact_input_database = [string]$Sample.compact_input_database_path
            compacted_database = [string]$Sample.compacted_database_path
        } }
        "verify" { [ordered]@{
            verify_database = [string]$Sample.verify_database_path
        } }
    }
    $contract = switch ($PhaseId) {
        "source" { [ordered]@{
            kind = "source"
            method = "DBEngine.CreateDatabase"
            locale = [string]$Plan.design.locale
            version_option = [string]$Condition.source_version_option
            version_api_value = [int]$Condition.source_version_api_value
            encryption_option = [string]$Condition.source_encryption_option
            encryption_api_value = [int]$Condition.source_encryption_api_value
            create_option_value = [int]$Condition.source_create_option_value
            expected_dao_version = [string]$Condition.expected_source_dao_version
        } }
        "compact" { [ordered]@{
            kind = "compact"
            method = "DBEngine.CompactDatabase"
            destination_locale_argument = "omitted"
            password_argument = "omitted"
            destination_version_option = [string]$Condition.destination_version_option
            destination_version_api_value = [int]$Condition.destination_version_api_value
            encryption_option = [string]$Condition.compact_encryption_option
            encryption_api_value = [int]$Condition.compact_encryption_api_value
            compact_option_value = [int]$Condition.compact_option_value
            expected_dao_version = [string]$Condition.expected_destination_dao_version
        } }
        "verify" { [ordered]@{
            kind = "verify"
            method = "DBEngine.OpenDatabase"
            mutation_requested = $false
            expected_dao_version = [string]$Condition.expected_destination_dao_version
        } }
    }
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_invocation"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Sample.sample_id
        condition_id = [string]$Sample.condition_id
        phase_id = $PhaseId
        phase_ordinal = @{ source = 1; compact = 2; verify = 3 }[$PhaseId]
        worker_run_id = "$($Sample.sample_id)-$($PhaseId.ToUpperInvariant())"
        worker_ordinal = $WorkerOrdinal
        nonce = Get-M5Nonce -CampaignRunId $Session.RunId `
            -SampleId $Sample.sample_id -PhaseId $PhaseId `
            -WorkerOrdinal $WorkerOrdinal
        campaign_run_id = [string]$Session.RunId
        producer_commit = [string]$Session.GitCommit
        repository_url = [string]$Plan.repository_url
        remote_ref = [string]$Plan.remote_ref
        repository_root = [string]$Context.RepositoryRoot
        stage_root = [string]$Session.StagingBundle
        plan_path = "plan.json"
        plan_sha256 = $PlanSha256
        environment_path = "environment.json"
        environment_sha256 = [string]$Context.EnvironmentSha256
        provider_sha256 = [string]$Context.ProviderSha256
        m4_input = [ordered]@{
            bundle_manifest_sha256 = $script:M5ExpectedM4ManifestSha256
            producer_commit = [string]$M4Manifest.producer_commit
            campaign_run_id = [string]$M4Manifest.run_id
            validated_before_com = $true
        }
        database_paths = $databasePaths
        result_path = [string]$paths.result
        phase_contract = $contract
        created_at_utc = Get-M5UtcTimestamp
        bindings_verified_before_com = $true
    }
}

function Write-M5Manifest {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries
    )
    if ($Entries.Count -lt $script:M5MinimumPayloadFiles -or
        $Entries.Count -gt $script:M5MaximumPayloadFiles) {
        throw "M5 payload topology is incomplete or over its bound."
    }
    $paths = [string[]]@($Entries | ForEach-Object { [string]$_.path })
    [Array]::Sort($paths, [StringComparer]::Ordinal)
    $byPath = New-Object 'Collections.Generic.Dictionary[string,object]' (
        [StringComparer]::Ordinal
    )
    foreach ($entry in $Entries) { $byPath.Add([string]$entry.path, $entry) }
    $files = @($paths | ForEach-Object { $byPath[$_] })
    $manifest = [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_bundle_manifest"
        experiment_id = $script:M5ExperimentId
        run_id = [string]$Session.RunId
        producer_commit = [string]$Session.GitCommit
        created_at_utc = Get-M5UtcTimestamp
        sample_count = 108
        worker_count = 324
        file_count = [int]$Entries.Count
        m4_manifest_sha256 = $script:M5ExpectedM4ManifestSha256
        bundle_tree_complete = $true
        unexpected_files_present = $false
        symlinks_or_reparses_present = $false
        execution_status = "pass"
        files = $files
    }
    Write-M1DurableBytes -Session $Session `
        -RelativePath "bundle-manifest.json" `
        -Bytes (ConvertTo-M5JsonBytes -Document $manifest -MaximumBytes 4MB)
}
