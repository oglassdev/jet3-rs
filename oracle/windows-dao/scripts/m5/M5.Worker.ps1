Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:M5MaximumInputBytes = 16MB
$script:M5RepositoryUrl = "https://github.com/oglassdev/jet3-rs.git"
$script:M5RemoteRef = "refs/heads/codex/m5r3-timeout-bounded"

function Resolve-M5LocalPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not [IO.Path]::IsPathRooted($Path) -or
        $Path.StartsWith("\\", [StringComparison]::Ordinal) -or
        $Path.Substring(2).Contains(":")) {
        throw "$Label must be an absolute local path."
    }
    return [IO.Path]::GetFullPath($Path)
}

function Resolve-M5Locator {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Locator,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Locator.Length -lt 1 -or $Locator.Length -gt 512 -or
        $Locator.Contains("\") -or $Locator.StartsWith("/") -or
        $Locator.Contains(":") -or $Locator.Contains("//")) {
        throw "$Label is not a canonical bundle locator."
    }
    foreach ($part in $Locator.Split('/')) {
        if ($part.Length -lt 1 -or $part -ceq "." -or $part -ceq "..") {
            throw "$Label contains a forbidden path segment."
        }
    }
    $full = [IO.Path]::GetFullPath(
        (Join-Path $Root ($Locator.Replace('/', '\')))
    )
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes the bundle root."
    }
    return $full
}

function Read-M5HeldFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-M1NoReparseComponents -Path $Path
    $stream = New-Object IO.FileStream(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaximumBytes) {
            throw "$Label violates its byte bound."
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw "$Label ended during its bounded read." }
            $offset += $read
        }
        $stream.Position = 0
        return [pscustomobject]@{
            Stream = $stream
            Bytes = $bytes
            Sha256 = Get-M4BytesSha256 -Bytes $bytes
        }
    }
    catch { $stream.Dispose(); throw }
}

function ConvertFrom-M5HeldJson {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$InputFile,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $bytes = $InputFile.Bytes
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
        $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf) {
        throw "$Label contains a forbidden UTF-8 BOM."
    }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    return $text | ConvertFrom-Json
}

function Get-M5WorkerPaths {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][string]$BundleRoot
    )
    $mapped = [ordered]@{}
    foreach ($property in $Invocation.database_paths.PSObject.Properties) {
        $locator = [string]$property.Value
        $expectedName = @{
            source_database = "SOURCE.MDB"
            compact_input_database = "COMPACT-INPUT.MDB"
            compacted_database = "COMPACTED.MDB"
            verify_database = "VERIFY.MDB"
        }[[string]$property.Name]
        if ([IO.Path]::GetFileName($locator) -cne $expectedName) {
            throw "M5 database path projection has a noncanonical basename."
        }
        $mapped[[string]$property.Name] = Resolve-M5Locator `
            -Root $BundleRoot -Locator $locator `
            -Label "database_paths.$($property.Name)"
    }
    return [pscustomobject]$mapped
}

function Assert-M5PlanProjection {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan
    )
    if ([string]$Plan.experiment_id -cne $script:M5ExperimentId -or
        [string]$Plan.remote_ref -cne $script:M5RemoteRef) {
        throw "M5 plan identity differs from the worker contract."
    }
    $samples = @($Plan.samples | Where-Object {
        $_.sample_id -ceq $Invocation.sample_id
    })
    $conditions = @($Plan.conditions | Where-Object {
        $_.condition_id -ceq $Invocation.condition_id
    })
    if ($samples.Count -ne 1 -or $conditions.Count -ne 1 -or
        [string]$samples[0].condition_id -cne
            [string]$conditions[0].condition_id) {
        throw "M5 invocation does not have a unique plan projection."
    }
    $expectedPaths = switch ([string]$Invocation.phase_id) {
        "source" { @("source_database_path", "source_database") }
        "compact" { @(
            "compact_input_database_path", "compact_input_database",
            "compacted_database_path", "compacted_database"
        ) }
        "verify" { @("verify_database_path", "verify_database") }
        default { throw "M5 invocation phase is unknown." }
    }
    for ($index = 0; $index -lt $expectedPaths.Count; $index += 2) {
        if ([string]$samples[0].($expectedPaths[$index]) -cne
            [string]$Invocation.database_paths.($expectedPaths[$index + 1])) {
            throw "M5 invocation path differs from the plan sample."
        }
    }
    $contract = $Invocation.phase_contract
    $condition = $conditions[0]
    switch ([string]$Invocation.phase_id) {
        "source" {
            if ([int]$contract.create_option_value -ne
                    [int]$condition.source_create_option_value -or
                [string]$contract.expected_dao_version -cne
                    [string]$condition.expected_source_dao_version) {
                throw "M5 source contract differs from its condition."
            }
        }
        "compact" {
            if ([int]$contract.compact_option_value -ne
                [int]$condition.compact_option_value) {
                throw "M5 compact option sum differs from its condition."
            }
            if ([string]$condition.compact_encryption_option -ceq
                "dbDecrypt" -and
                [int]$condition.compact_encryption_api_value -ne 4) {
                throw "M5 checked dbDecrypt value is not exactly 4."
            }
        }
        "verify" {
            if ([string]$contract.expected_dao_version -cne
                [string]$condition.expected_destination_dao_version) {
                throw "M5 verify contract differs from its condition."
            }
        }
    }
}

function Assert-M5CloneBindings {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$DatabasePaths,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    if ([string]$Invocation.phase_id -ceq "source") { return }
    $name = if ([string]$Invocation.phase_id -ceq "compact") {
        "source-to-compact-input-clone.json"
    } else { "compacted-to-verify-input-clone.json" }
    $locator = "evidence/samples/$($Invocation.sample_id)/$name"
    $logPath = Resolve-M5Locator -Root $BundleRoot `
        -Locator $locator -Label "clone log"
    $logInput = Read-M5HeldFile -Path $logPath -MaximumBytes 64KB `
        -Label "M5 clone log"
    try {
        $log = ConvertFrom-M5HeldJson -InputFile $logInput `
            -Label "M5 clone log"
        $database = if ([string]$Invocation.phase_id -ceq "compact") {
            [string]$DatabasePaths.compact_input_database
        } else { [string]$DatabasePaths.verify_database }
        $observed = Get-M4ClosedFileObservation `
            -DatabasePath $database -MaximumBytes $MaximumBytes
        $expectedDestination = if (
            [string]$Invocation.phase_id -ceq "compact"
        ) { [string]$Invocation.database_paths.compact_input_database }
        else { [string]$Invocation.database_paths.verify_database }
        if ([string]$log.destination_path -cne $expectedDestination -or
            [string]$log.source_sha256_before_clone -cne
                [string]$log.source_sha256_after_clone -or
            [string]$log.source_sha256_before_clone -cne
                [string]$log.destination_sha256 -or
            [long]$observed.bytes -ne [long]$log.destination_bytes -or
            [string]$observed.sha256 -cne
                [string]$log.destination_sha256) {
            throw "M5 phase input differs from its controller clone."
        }
        return $logInput.Stream
    }
    catch { $logInput.Stream.Dispose(); throw }
}
