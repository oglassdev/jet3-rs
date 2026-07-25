Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-M4WorkerLocalPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        -not [IO.Path]::IsPathRooted($Path) -or
        $Path -cnotmatch "^[A-Za-z]:\\" -or
        $Path.StartsWith("\\", [StringComparison]::Ordinal) -or
        $Path.IndexOf([char]0) -ge 0
    ) {
        throw "$Label must be an absolute local path."
    }
    $full = [IO.Path]::GetFullPath($Path)
    if (
        $full.Length -gt 500 -or
        ($full.Length -gt 2 -and $full.Substring(2).Contains(":"))
    ) {
        throw "$Label path exceeds its bound or names an alternate stream."
    }
    $supplied = $Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
    $canonical = $full.TrimEnd([IO.Path]::DirectorySeparatorChar)
    $driveRoot = [IO.Path]::GetPathRoot($full).TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    )
    if ($canonical.Equals(
        $driveRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label may not be a drive root."
    }
    if (-not $supplied.Equals(
        $canonical,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label path aliases and non-canonical paths are forbidden."
    }
    return $full
}

function Test-M4WorkerPathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $prefix = $Root.TrimEnd(
        [IO.Path]::DirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith(
        $prefix,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Resolve-M4BundleLocator {
    param(
        [Parameter(Mandatory = $true)][string]$Locator,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $locatorPattern = (
        "^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}/){1,5}" +
        "[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )
    if (
        $Locator.Length -lt 3 -or
        $Locator.Length -gt 255 -or
        $Locator.Contains("\") -or
        $Locator.Contains(":") -or
        $Locator -match "(?:^|/)\.\.?(?:/|$)" -or
        $Locator -cnotmatch $locatorPattern
    ) {
        throw "$Label is not a safe bounded bundle-relative locator."
    }
    $native = $Locator.Replace(
        [IO.Path]::AltDirectorySeparatorChar,
        [IO.Path]::DirectorySeparatorChar
    )
    $full = [IO.Path]::GetFullPath((Join-Path $Root $native))
    if (-not (Test-M4WorkerPathWithin -Path $full -Root $Root)) {
        throw "$Label escapes the private bundle root."
    }
    return $full
}

function Assert-M4WorkerNoReparseAncestors {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $cursor = $Path
    while (
        -not [IO.File]::Exists($cursor) -and
        -not [IO.Directory]::Exists($cursor)
    ) {
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if (
            [string]::IsNullOrWhiteSpace($parent) -or
            $parent -ceq $cursor
        ) {
            throw "$Label has no existing local ancestor."
        }
        $cursor = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (
            ($item.Attributes -band
                [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label contains a forbidden reparse point."
        }
        $parent = [IO.Path]::GetDirectoryName($item.FullName)
        if (
            [string]::IsNullOrWhiteSpace($parent) -or
            $parent -ceq $cursor
        ) {
            break
        }
        $cursor = $parent
    }
}

function Read-M4HeldFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-M4WorkerNoReparseAncestors -Path $Path -Label $Label
    $stream = New-Object IO.FileStream(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaximumBytes) {
            throw "$Label violates its byte bound."
        }
        $length = [int]$stream.Length
        $bytes = New-Object byte[] $length
        $offset = 0
        while ($offset -lt $length) {
            $read = $stream.Read($bytes, $offset, $length - $offset)
            if ($read -le 0) {
                throw "$Label ended during its bounded read."
            }
            $offset += $read
        }
        $stream.Position = 0
        return [pscustomobject]@{
            Stream = $stream
            Bytes = $bytes
            Sha256 = Get-M4BytesSha256 -Bytes $bytes
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Assert-M4ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string[]]$Names,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $expected = @($Names | Sort-Object)
    if (($actual -join "`n") -cne ($expected -join "`n")) {
        throw "$Label keys differ from the checked contract."
    }
}

function Get-M4InvocationPaths {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][string]$SourceRepository,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$InvocationFile
    )

    $repository = Get-M4WorkerLocalPath `
        -Path ([string]$Invocation.repository_root) -Label "repository_root"
    $stage = Get-M4WorkerLocalPath `
        -Path ([string]$Invocation.stage_root) -Label "stage_root"
    $output = Get-M4WorkerLocalPath `
        -Path ([string]$Invocation.output_root) -Label "output_root"
    if (-not $repository.Equals(
        $SourceRepository,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Executing repository differs from the invocation binding."
    }
    if (-not $stage.Equals(
        $BundleRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "BundleRoot differs from the invocation stage_root binding."
    }
    foreach ($root in @($stage, $output)) {
        if (-not [IO.Directory]::Exists($root)) {
            throw "A bound M4 private root is absent."
        }
        Assert-M4WorkerNoReparseAncestors -Path $root `
            -Label "M4 private root"
    }
    if (
        $output.Equals(
            $repository,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        (Test-M4WorkerPathWithin -Path $output -Root $repository) -or
        (Test-M4WorkerPathWithin -Path $repository -Root $output) -or
        $output.Equals($stage, [StringComparison]::OrdinalIgnoreCase) -or
        (Test-M4WorkerPathWithin -Path $output -Root $stage)
    ) {
        throw "M4 output_root must remain outside repository and stage roots."
    }
    if (-not (Test-M4WorkerPathWithin `
        -Path $InvocationFile -Root $stage)) {
        throw "M4 invocation is not inside its declared stage root."
    }
    return [pscustomobject]@{
        Repository = $repository
        StageRoot = $stage
        OutputRoot = $output
        Plan = Resolve-M4BundleLocator `
            -Locator ([string]$Invocation.plan_path) `
            -Root $BundleRoot -Label "plan_path"
        Environment = Resolve-M4BundleLocator `
            -Locator ([string]$Invocation.environment_path) `
            -Root $BundleRoot -Label "environment_path"
        Database = Resolve-M4BundleLocator `
            -Locator ([string]$Invocation.database_path) `
            -Root $BundleRoot -Label "database_path"
        Result = Resolve-M4BundleLocator `
            -Locator ([string]$Invocation.result_path) `
            -Root $BundleRoot -Label "result_path"
    }
}

function Get-M4DerivedArtifactLocator {
    param(
        [Parameter(Mandatory = $true)][string]$DatabaseLocator,
        [Parameter(Mandatory = $true)][string]$Suffix
    )

    $separator = $DatabaseLocator.LastIndexOf("/")
    if ($separator -lt 1) {
        throw "M4 database locator has no artifact directory."
    }
    $directory = $DatabaseLocator.Substring(0, $separator)
    $fileName = $DatabaseLocator.Substring($separator + 1)
    $extension = $fileName.LastIndexOf(".")
    if ($extension -lt 1) {
        throw "M4 database locator has no extension."
    }
    return $directory + "/" + $fileName.Substring(0, $extension) + $Suffix
}

function Assert-M4ArtifactDestination {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-M4WorkerNoReparseAncestors -Path $Path -Label $Label
    $parent = [IO.Path]::GetDirectoryName($Path)
    if (-not [IO.Directory]::Exists($parent)) {
        throw "$Label parent directory is absent."
    }
    if ([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)) {
        throw "$Label already exists; create-new evidence is required."
    }
}

function Get-M4PhaseArtifactSet {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$ResultPath
    )

    $databaseLocator = [string]$Invocation.database_path
    $locators = [ordered]@{
        prefix = Get-M4DerivedArtifactLocator `
            -DatabaseLocator $databaseLocator -Suffix ".prefix.bin"
        operation_log = Get-M4DerivedArtifactLocator `
            -DatabaseLocator $databaseLocator -Suffix "-operation-log.json"
        snapshot = Get-M4DerivedArtifactLocator `
            -DatabaseLocator $databaseLocator -Suffix "-snapshot.json"
        failure = Get-M4DerivedArtifactLocator `
            -DatabaseLocator $databaseLocator -Suffix "-worker-failure.json"
    }
    $paths = [ordered]@{
        result = $ResultPath
        prefix = Resolve-M4BundleLocator -Locator $locators.prefix `
            -Root $BundleRoot -Label "prefix artifact"
        operation_log = Resolve-M4BundleLocator `
            -Locator $locators.operation_log -Root $BundleRoot `
            -Label "operation-log artifact"
        snapshot = Resolve-M4BundleLocator -Locator $locators.snapshot `
            -Root $BundleRoot -Label "snapshot artifact"
        failure = Resolve-M4BundleLocator -Locator $locators.failure `
            -Root $BundleRoot -Label "worker failure tombstone"
    }
    foreach ($name in @(
        "result", "prefix", "operation_log", "snapshot", "failure"
    )) {
        Assert-M4ArtifactDestination -Path $paths[$name] `
            -Label ("M4 " + $name + " destination")
    }
    return [pscustomobject]@{
        Locators = [pscustomobject]$locators
        Paths = [pscustomobject]$paths
    }
}

function Assert-M4PlanProjection {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan
    )

    if (
        [string]$Plan.experiment_id -cne $script:M4ExperimentId -or
        [string]$Plan.repository_url -cne $script:M4RepositoryUrl -or
        [string]$Plan.remote_ref -cne $script:M4RemoteRef
    ) {
        throw "M4 plan identity differs from the worker contract."
    }
    $conditions = @(
        $Plan.conditions | Where-Object {
            [string]$_.condition_id -ceq
                [string]$Invocation.condition_id
        }
    )
    $samples = @(
        $Plan.samples | Where-Object {
            [string]$_.sample_id -ceq [string]$Invocation.sample_id
        }
    )
    if ($conditions.Count -ne 1 -or $samples.Count -ne 1) {
        throw "M4 invocation does not select one plan condition and sample."
    }
    $condition = $conditions[0]
    $sample = $samples[0]
    if (
        [string]$sample.condition_id -cne
            [string]$Invocation.condition_id
    ) {
        throw "M4 sample condition differs from the invocation."
    }
    $expectedOrdinal = if (
        [string]$Invocation.phase_id -ceq "creator"
    ) {
        [int](2 * [int]$sample.launch_ordinal - 1)
    }
    else {
        [int](2 * [int]$sample.launch_ordinal)
    }
    $expectedDatabase = if (
        [string]$Invocation.phase_id -ceq "creator"
    ) {
        [string]$sample.creator_database_path
    }
    else {
        [string]$sample.reopen_database_path
    }
    $expectedResult = Get-M4DerivedArtifactLocator `
        -DatabaseLocator $expectedDatabase -Suffix "-worker-result.json"
    if (
        [int]$Invocation.worker_ordinal -ne $expectedOrdinal -or
        [string]$Invocation.database_path -cne $expectedDatabase -or
        [string]$Invocation.result_path -cne $expectedResult -or
        [string]$Invocation.phase_contract.expected_dao_version -cne
            [string]$condition.expected_dao_version
    ) {
        throw "M4 invocation differs from its exact plan projection."
    }
    if ([string]$Invocation.phase_id -ceq "creator") {
        foreach ($field in @(
            "version_option", "version_api_value", "encryption_option",
            "encryption_api_value", "create_option_value",
            "expected_dao_version"
        )) {
            if (
                [string]$Invocation.phase_contract.$field -cne
                    [string]$condition.$field
            ) {
                throw "M4 creator contract differs at $field."
            }
        }
        if (
            [string]$Invocation.phase_contract.locale -cne
                [string]$Plan.design.locale -or
            [string]$Invocation.phase_contract.method -cne
                "DBEngine.CreateDatabase" -or
            $Invocation.phase_contract.compact_database_used -ne $false
        ) {
            throw "M4 creator call contract differs from the plan."
        }
    }
    return [pscustomobject]@{
        Condition = $condition
        Sample = $sample
    }
}

function Assert-M4ReopenCloneBinding {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$CloneInput,
        [Parameter(Mandatory = $true)][pscustomobject]$PreCom
    )

    $clone = ConvertFrom-M1Utf8Json -Bytes $CloneInput.Bytes `
        -Label "M4 clone log"
    Assert-M4ExactProperties -Value $clone -Label "M4 clone log" -Names @(
        "protocol_version", "document_type", "experiment_id", "sample_id",
        "started_at_utc", "completed_at_utc", "source_path",
        "destination_path", "source_bytes", "destination_bytes",
        "source_sha256_before_clone", "source_sha256_after_clone",
        "destination_sha256", "source_file_identity",
        "destination_file_identity", "all_hashes_equal", "same_volume",
        "distinct_file_identity", "no_hardlink", "reparse_free",
        "completed_before_reopen_com", "status"
    )
    $samples = @(
        $Plan.samples | Where-Object {
            [string]$_.sample_id -ceq [string]$Invocation.sample_id
        }
    )
    if ($samples.Count -ne 1) {
        throw "M4 clone binding does not select one checked sample."
    }
    $sample = $samples[0]
    $sourceIdentity = $clone.source_file_identity
    $destinationIdentity = $clone.destination_file_identity
    if (
        [string]$clone.protocol_version -cne "1.0.0" -or
        [string]$clone.document_type -cne "dao_m4_clone_log" -or
        [string]$clone.experiment_id -cne $script:M4ExperimentId -or
        [string]$clone.sample_id -cne [string]$Invocation.sample_id -or
        [string]$clone.source_path -cne
            [string]$sample.creator_database_path -or
        [string]$clone.destination_path -cne
            [string]$Invocation.database_path -or
        [long]$clone.source_bytes -ne [long]$PreCom.bytes -or
        [long]$clone.destination_bytes -ne [long]$PreCom.bytes -or
        [string]$clone.source_sha256_before_clone -cne
            [string]$PreCom.sha256 -or
        [string]$clone.source_sha256_after_clone -cne
            [string]$PreCom.sha256 -or
        [string]$clone.destination_sha256 -cne
            [string]$PreCom.sha256 -or
        $clone.all_hashes_equal -ne $true -or
        $clone.same_volume -ne $true -or
        $clone.distinct_file_identity -ne $true -or
        $clone.no_hardlink -ne $true -or
        $clone.reparse_free -ne $true -or
        $clone.completed_before_reopen_com -ne $true -or
        [string]$clone.status -cne "pass" -or
        [long]$sourceIdentity.link_count -ne 1 -or
        [long]$destinationIdentity.link_count -ne 1 -or
        [long]$PreCom.file_identity.link_count -ne 1 -or
        [string]$sourceIdentity.volume_serial_number -cne
            [string]$destinationIdentity.volume_serial_number -or
        (
            [string]$sourceIdentity.file_index -ceq
                [string]$destinationIdentity.file_index
        ) -or
        [string]$destinationIdentity.volume_serial_number -cne
            [string]$PreCom.file_identity.volume_serial_number -or
        [string]$destinationIdentity.file_index -cne
            [string]$PreCom.file_identity.file_index
    ) {
        throw "M4 clone log or live destination identity is inconsistent."
    }
}

function Close-M4BindingStreams {
    param([object[]]$Streams)

    $errors = New-Object Collections.ArrayList
    foreach ($stream in $Streams) {
        if ($null -eq $stream) { continue }
        try {
            $stream.Dispose()
        }
        catch {
            $detail = Get-M1SafeText -Value (
                "Binding stream disposal: " + $_.Exception.Message
            ) -Maximum 1000
            [void]$errors.Add($detail)
        }
    }
    return @($errors)
}

function Get-M4BindingStreams {
    param(
        [AllowNull()][object]$BootstrapStream,
        [AllowNull()][object]$ProviderStream,
        [AllowNull()][object]$GitExecutableStream,
        [AllowNull()][object]$CloneInput,
        [AllowNull()][object]$EnvironmentInput,
        [AllowNull()][object]$PlanInput,
        [AllowNull()][object]$InvocationInput
    )

    return @(
        $BootstrapStream,
        $ProviderStream,
        $GitExecutableStream,
        $(if ($null -ne $CloneInput) { $CloneInput.Stream }),
        $(if ($null -ne $EnvironmentInput) {
            $EnvironmentInput.Stream
        }),
        $(if ($null -ne $PlanInput) { $PlanInput.Stream }),
        $(if ($null -ne $InvocationInput) {
            $InvocationInput.Stream
        })
    )
}
