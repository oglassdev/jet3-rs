# Provenance usage: EXP-0005 and EXP-0009.
[CmdletBinding()]
param(
    [string]$RepositoryRoot,
    [string]$EnvironmentPath,
    [string]$OutputRoot,
    [string]$GitCommit,
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProtocolVersion = "1.0.0"
$ScenarioId = "DAO-GEN-PROBE-001"
$ScenarioRelativePath = "oracle/windows-dao/examples/$ScenarioId.scenario.json"
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
# Microsoft Learn DatabaseTypeEnum documents dbVersion30 as 32.
# Clean-room provenance: docs/PROVENANCE.md SRC-0002.
$DbVersion30 = 32
# Microsoft Learn TableDefAttributeEnum documents dbSystemObject.
# Clean-room provenance: docs/PROVENANCE.md SRC-0003.
$DbSystemObject = -2147483646

trap {
    [Console]::Error.WriteLine(
        "ERROR: unhandled DAO runner failure: " + $_.Exception.Message
    )
    exit 4
}

function Exit-InvocationError {
    param([string]$Message)

    [Console]::Error.WriteLine("INVALID: " + $Message)
    exit 2
}

function Get-LowerSha256 {
    param([string]$Path)

    $hashRecord = Get-FileHash -LiteralPath $Path -Algorithm SHA256
    return $hashRecord.Hash.ToLowerInvariant()
}

function Write-Utf8Text {
    param(
        [string]$Path,
        [string]$Text
    )

    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $encoding = New-Object Text.UTF8Encoding($false)
    $bytes = $encoding.GetBytes($Text)
    $temporaryPath = Join-Path $parent (
        "." + [IO.Path]::GetFileName($Path) + "." +
        [Guid]::NewGuid().ToString("N") + ".tmp"
    )
    $stream = New-Object IO.FileStream(
        $temporaryPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    [IO.File]::Move($temporaryPath, $Path)
}

function Write-JsonDocument {
    param(
        [string]$Path,
        [System.Collections.IDictionary]$Document,
        [switch]$Canonical
    )

    if ($Canonical) {
        $json = $Document | ConvertTo-Json -Depth 20 -Compress
    }
    else {
        $json = $Document | ConvertTo-Json -Depth 20
    }
    Write-Utf8Text -Path $Path -Text ($json + "`n")
}

function Release-ComObject {
    param([object]$Value)

    if (
        $null -ne $Value -and
        [Runtime.InteropServices.Marshal]::IsComObject($Value)
    ) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-ProcessArchitecture {
    $architecture = [Environment]::GetEnvironmentVariable("PROCESSOR_ARCHITECTURE")
    if ($architecture -eq "ARM64") {
        return "arm64"
    }
    if ([IntPtr]::Size -eq 8) {
        return "x64"
    }
    if ([IntPtr]::Size -eq 4) {
        return "x86"
    }
    return "unknown"
}

function Get-SafeDetail {
    param([string]$Detail)

    if ([string]::IsNullOrWhiteSpace($Detail)) {
        return "No detail was provided."
    }
    if ($Detail.Length -le 4000) {
        return $Detail
    }
    return $Detail.Substring(0, 4000)
}

function Add-OperationEntry {
    param(
        [Collections.ArrayList]$Entries,
        [string]$Action,
        [string]$Status,
        [string]$Detail
    )

    $safeDetail = Get-SafeDetail -Detail $Detail
    [void]$Entries.Add([ordered]@{
        sequence = $Entries.Count + 1
        timestamp_utc = [DateTimeOffset]::UtcNow.ToString("o")
        action = $Action
        status = $Status
        detail = $safeDetail
    })
}

function New-FileReference {
    param(
        [string]$BundleRoot,
        [string]$RelativePath
    )

    $path = Join-Path $BundleRoot $RelativePath
    $hash = Get-LowerSha256 -Path $path
    return [ordered]@{
        path = $RelativePath.Replace("\", "/")
        sha256 = $hash
    }
}

function New-ManifestEntry {
    param(
        [string]$BundleRoot,
        [string]$RelativePath,
        [string]$Role,
        [string]$MediaType
    )

    $path = Join-Path $BundleRoot $RelativePath
    $file = Get-Item -LiteralPath $path
    $hash = Get-LowerSha256 -Path $path
    return [ordered]@{
        path = $RelativePath.Replace("\", "/")
        role = $Role
        sha256 = $hash
        size_bytes = [long]$file.Length
        media_type = $MediaType
    }
}

foreach ($required in @(
    @{ Name = "RepositoryRoot"; Value = $RepositoryRoot },
    @{ Name = "EnvironmentPath"; Value = $EnvironmentPath },
    @{ Name = "OutputRoot"; Value = $OutputRoot },
    @{ Name = "GitCommit"; Value = $GitCommit },
    @{ Name = "RunId"; Value = $RunId }
)) {
    if ([string]::IsNullOrWhiteSpace([string]$required.Value)) {
        Exit-InvocationError -Message ($required.Name + " is required.")
    }
}
if ($GitCommit -cnotmatch "^[0-9a-f]{40}$") {
    Exit-InvocationError -Message "GitCommit must be 40 lowercase hexadecimal digits."
}
if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$") {
    Exit-InvocationError -Message "RunId does not match the protocol pattern."
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    [Console]::Error.WriteLine("BLOCKED: DAO scenario execution requires Windows.")
    exit 3
}

$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$environmentSource = [IO.Path]::GetFullPath($EnvironmentPath)
$output = [IO.Path]::GetFullPath($OutputRoot)
$scenarioSource = [IO.Path]::GetFullPath(
    (Join-Path $repository $ScenarioRelativePath)
)
if (-not (Test-Path -LiteralPath $scenarioSource -PathType Leaf)) {
    Exit-InvocationError -Message "The checked M0 scenario input is missing."
}
if (-not (Test-Path -LiteralPath $environmentSource -PathType Leaf)) {
    [Console]::Error.WriteLine("BLOCKED: the ready environment record is missing.")
    exit 3
}

try {
    $head = (& git -C $repository rev-parse HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git rev-parse failed: $head"
    }
    if ($head -cne $GitCommit) {
        [Console]::Error.WriteLine(
            "BLOCKED: GitCommit does not match repository HEAD."
        )
        exit 3
    }
    $dirty = (& git -C $repository status --porcelain=v1 `
        --untracked-files=all 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed: $dirty"
    }
    if (-not [string]::IsNullOrWhiteSpace($dirty)) {
        [Console]::Error.WriteLine(
            "BLOCKED: release evidence requires a clean git worktree."
        )
        exit 3
    }
    foreach ($boundSource in @(
        @{
            Relative = $ScenarioRelativePath
            Absolute = $scenarioSource
        },
        @{
            Relative = "oracle/windows-dao/scripts/run-dao-gen-probe.ps1"
            Absolute = $PSCommandPath
        }
    )) {
        $expectedBlob = (& git -C $repository rev-parse (
            "${GitCommit}:" + $boundSource.Relative
        ) 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "git could not resolve " + $boundSource.Relative
        }
        $actualBlob = (& git -C $repository hash-object (
            [string]$boundSource.Absolute
        ) 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $actualBlob -cne $expectedBlob) {
            [Console]::Error.WriteLine(
                "BLOCKED: executed oracle input differs from GitCommit: " +
                $boundSource.Relative
            )
            exit 3
        }
    }
}
catch {
    [Console]::Error.WriteLine(
        "ERROR: unable to verify git identity: " + $_.Exception.Message
    )
    exit 4
}

try {
    $scenario = Get-Content -LiteralPath $scenarioSource -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $environment = Get-Content -LiteralPath $environmentSource -Raw -Encoding UTF8 |
        ConvertFrom-Json
}
catch {
    Exit-InvocationError -Message (
        "Scenario or environment JSON could not be parsed: " +
        $_.Exception.Message
    )
}

try {
    $scenarioIsValid = (
        $scenario.protocol_version -eq $ProtocolVersion -and
        $scenario.document_type -eq "dao_scenario" -and
        $scenario.scenario_id -eq $ScenarioId -and
        $scenario.mode -eq "dao_generate_fixture" -and
        $scenario.requirements.database_version -eq "dbVersion30" -and
        $scenario.requirements.provider_api -eq "DAO COM" -and
        $scenario.database.input_role -eq "none" -and
        $scenario.steps.Count -eq 2 -and
        $scenario.steps[0].action -eq "create_database" -and
        $scenario.steps[0].arguments.locale -eq $DatabaseLocale -and
        $scenario.steps[0].arguments.version -eq "dbVersion30" -and
        $scenario.steps[1].action -eq "close_database" -and
        $scenario.expected.outcome -eq "success" -and
        $scenario.expected.reopen_before_snapshot -eq $true
    )
}
catch {
    $scenarioIsValid = $false
}
if (-not $scenarioIsValid) {
    Exit-InvocationError -Message "The checked M0 scenario contract is invalid."
}
try {
    $environmentIsReady = (
        $environment.protocol_version -eq $ProtocolVersion -and
        $environment.document_type -eq "dao_environment" -and
        $environment.status -eq "ready" -and
        $environment.host.is_windows -eq $true -and
        $null -ne $environment.accepted_provider -and
        $environment.accepted_provider.database_version -eq "dbVersion30"
    )
}
catch {
    $environmentIsReady = $false
}
if (-not $environmentIsReady) {
    [Console]::Error.WriteLine(
        "BLOCKED: environment record is not ready for dbVersion30."
    )
    exit 3
}
$accepted = $environment.accepted_provider
$matchingCandidates = @(
    $environment.provider_candidates | Where-Object {
        $_.prog_id -eq $accepted.prog_id -and
        $_.clsid -eq $accepted.clsid -and
        $_.registry_view -eq $accepted.registry_view -and
        $_.registration_scope -eq $accepted.registration_scope -and
        $_.dbversion30_test.status -eq "pass"
    }
)
if ($matchingCandidates.Count -eq 0) {
    [Console]::Error.WriteLine(
        "BLOCKED: accepted provider has no matching dbVersion30 probe result."
    )
    exit 3
}
if (
    $environment.host.computer_name -ine [Environment]::MachineName -or
    $accepted.registry_view -ne (Get-ProcessArchitecture)
) {
    [Console]::Error.WriteLine(
        "BLOCKED: environment host or provider bitness differs from this process."
    )
    exit 3
}
if (-not (Test-Path -LiteralPath $accepted.server_path -PathType Leaf)) {
    [Console]::Error.WriteLine("BLOCKED: accepted provider binary is missing.")
    exit 3
}
if ((Get-LowerSha256 -Path $accepted.server_path) -cne $accepted.server_sha256) {
    [Console]::Error.WriteLine(
        "BLOCKED: accepted provider binary hash has changed since probing."
    )
    exit 3
}

$commitDirectory = Join-Path $output $GitCommit
$finalDirectory = Join-Path $commitDirectory $RunId
if (Test-Path -LiteralPath $finalDirectory) {
    Exit-InvocationError -Message "The immutable evidence directory already exists."
}
[IO.Directory]::CreateDirectory($commitDirectory) | Out-Null
$stagingDirectory = Join-Path $commitDirectory (
    "." + $RunId + ".building." + [Guid]::NewGuid().ToString("N")
)
[IO.Directory]::CreateDirectory($stagingDirectory) | Out-Null

$scenarioDirectoryRelative = "scenarios/$ScenarioId"
$scenarioDirectory = Join-Path $stagingDirectory $scenarioDirectoryRelative
$databaseWorkingDirectory = Join-Path $stagingDirectory "working"
[IO.Directory]::CreateDirectory($scenarioDirectory) | Out-Null
[IO.Directory]::CreateDirectory($databaseWorkingDirectory) | Out-Null
$environmentRelative = "environment.json"
$scenarioInputRelative = "$scenarioDirectoryRelative/input.json"
[IO.File]::Copy(
    $environmentSource,
    (Join-Path $stagingDirectory $environmentRelative),
    $false
)
[IO.File]::Copy(
    $scenarioSource,
    (Join-Path $stagingDirectory $scenarioInputRelative),
    $false
)

$startedAt = [DateTimeOffset]::UtcNow.ToString("o")
$entries = New-Object Collections.ArrayList
$engine = $null
$workspace = $null
$database = $null
$workingDatabase = Join-Path $databaseWorkingDirectory "probe.mdb"
$runStatus = "error"
$statusReason = "Runner did not complete."
$phase = "activate_provider"
$databaseRelative = $null
$snapshotRelative = $null
$diagnosticRelative = $null

try {
    $comType = [Type]::GetTypeFromProgID([string]$accepted.prog_id, $false)
    if ($null -eq $comType) {
        throw "Accepted provider ProgID cannot be resolved."
    }
    $actualClsid = "{" + $comType.GUID.ToString().ToUpperInvariant() + "}"
    if ($actualClsid -ine [string]$accepted.clsid) {
        throw "Accepted provider CLSID differs from the active COM registration."
    }
    $engine = [Activator]::CreateInstance($comType)
    if ([string]$engine.Version -cne [string]$accepted.provider_version) {
        throw "Accepted provider version differs from the active DAO engine."
    }
    Add-OperationEntry -Entries $entries -Action "activate_provider" `
        -Status "pass" -Detail "Activated the exact probed DAO provider."

    $phase = "create_database"
    $workspace = $engine.Workspaces.Item(0)
    # DAO CreateDatabase/OpenDatabase API usage is recorded by SRC-0001.
    $database = $workspace.CreateDatabase(
        $workingDatabase,
        $DatabaseLocale,
        $DbVersion30
    )
    Add-OperationEntry -Entries $entries -Action "create_database" `
        -Status "pass" -Detail "Created an unencrypted dbVersion30 MDB."
    $database.Close()
    Release-ComObject -Value $database
    $database = $null
    if (-not (Test-Path -LiteralPath $workingDatabase -PathType Leaf)) {
        throw "DAO returned without retaining the dbVersion30 MDB."
    }
    Add-OperationEntry -Entries $entries -Action "close_database" `
        -Status "pass" -Detail "Closed and retained the dbVersion30 MDB."

    $phase = "reopen_database"
    $database = $workspace.OpenDatabase($workingDatabase)
    $database.TableDefs.Refresh()
    $userTableNames = @()
    foreach ($tableDefinition in $database.TableDefs) {
        $attributes = [int]$tableDefinition.Attributes
        $isSystem = (
            ($attributes -band $DbSystemObject) -ne 0
        )
        if (-not $isSystem) {
            $userTableNames += [string]$tableDefinition.Name
        }
        Release-ComObject -Value $tableDefinition
    }
    if ($userTableNames.Count -ne 0) {
        throw (
            "Expected empty user schema; DAO reported: " +
            ($userTableNames -join ", ")
        )
    }
    $database.Close()
    Release-ComObject -Value $database
    $database = $null
    Add-OperationEntry -Entries $entries -Action "reopen_database" `
        -Status "pass" -Detail "Reopened MDB; DAO reported no user tables."

    $phase = "snapshot"
    $databaseHash = Get-LowerSha256 -Path $workingDatabase
    $databaseRelative = "databases/$databaseHash.mdb"
    $databasePath = Join-Path $stagingDirectory $databaseRelative
    [IO.Directory]::CreateDirectory(
        [IO.Path]::GetDirectoryName($databasePath)
    ) | Out-Null
    [IO.File]::Move($workingDatabase, $databasePath)

    # Keys are inserted in Unicode code-point order for canonical JSON.
    $snapshot = [ordered]@{
        database_properties = [ordered]@{}
        database_sha256 = $databaseHash
        document_type = "canonical_snapshot"
        ordering = [ordered]@{
            columns = "ordinal_ascending"
            indexes = "name_codepoint_ascending"
            object_keys = "unicode_codepoint_ascending"
            objects = "name_codepoint_ascending"
            relationships = "name_codepoint_ascending"
            rows = "declared_key_then_canonical_value"
        }
        producer = [ordered]@{
            kind = "dao"
            source_revision = $GitCommit
        }
        protocol_version = $ProtocolVersion
        raw_preservation = @()
        relationships = @()
        scenario_id = $ScenarioId
        tables = @()
    }
    $snapshotRelative = "$scenarioDirectoryRelative/dao-snapshot.json"
    Write-JsonDocument -Path (
        Join-Path $stagingDirectory $snapshotRelative
    ) -Document $snapshot -Canonical
    Add-OperationEntry -Entries $entries -Action "snapshot" -Status "pass" `
        -Detail "Emitted canonical empty-user-schema DAO snapshot."

    $runStatus = "pass"
    $statusReason = (
        "DAO created, closed, reopened, and snapshotted the dbVersion30 MDB."
    )
}
catch {
    $detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    if ($phase -eq "activate_provider") {
        $runStatus = "blocked"
        $statusReason = "The probed DAO provider is no longer activatable."
    }
    elseif ($phase -in @("create_database", "reopen_database")) {
        $runStatus = "fail"
        $statusReason = "DAO did not satisfy the checked scenario."
    }
    else {
        $runStatus = "error"
        $statusReason = "The evidence runner failed while building artifacts."
    }
    Add-OperationEntry -Entries $entries -Action $phase -Status $runStatus `
        -Detail $detail
}
finally {
    if ($null -ne $database) {
        try {
            $database.Close()
        }
        catch {
            # The primary result already records the operation failure.
        }
    }
    Release-ComObject -Value $database
    Release-ComObject -Value $workspace
    Release-ComObject -Value $engine
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if ($runStatus -ne "pass" -and (Test-Path -LiteralPath $workingDatabase)) {
    $partialHash = Get-LowerSha256 -Path $workingDatabase
    $diagnosticRelative = "diagnostics/partial-$partialHash.mdb"
    $diagnosticPath = Join-Path $stagingDirectory $diagnosticRelative
    [IO.Directory]::CreateDirectory(
        [IO.Path]::GetDirectoryName($diagnosticPath)
    ) | Out-Null
    [IO.File]::Move($workingDatabase, $diagnosticPath)
}
elseif (
    $runStatus -ne "pass" -and
    $null -ne $databaseRelative -and
    (Test-Path -LiteralPath (Join-Path $stagingDirectory $databaseRelative))
) {
    $publishedDatabase = Join-Path $stagingDirectory $databaseRelative
    $partialHash = Get-LowerSha256 -Path $publishedDatabase
    $diagnosticRelative = "diagnostics/partial-$partialHash.mdb"
    $diagnosticPath = Join-Path $stagingDirectory $diagnosticRelative
    [IO.Directory]::CreateDirectory(
        [IO.Path]::GetDirectoryName($diagnosticPath)
    ) | Out-Null
    [IO.File]::Move($publishedDatabase, $diagnosticPath)
}
Add-OperationEntry -Entries $entries -Action "finalize" -Status $runStatus `
    -Detail $statusReason

$operationLog = [ordered]@{
    protocol_version = $ProtocolVersion
    document_type = "dao_operation_log"
    run_id = $RunId
    scenario_id = $ScenarioId
    git_commit = $GitCommit
    final_status = $runStatus
    entries = @($entries)
}
$operationLogRelative = "$scenarioDirectoryRelative/operation-log.json"
Write-JsonDocument -Path (
    Join-Path $stagingDirectory $operationLogRelative
) -Document $operationLog

$environmentReference = New-FileReference -BundleRoot $stagingDirectory `
    -RelativePath $environmentRelative
$inputReference = New-FileReference -BundleRoot $stagingDirectory `
    -RelativePath $scenarioInputRelative
$operationLogReference = New-FileReference -BundleRoot $stagingDirectory `
    -RelativePath $operationLogRelative
$databaseReference = $null
$snapshotReference = $null
if ($runStatus -eq "pass") {
    $databaseReference = New-FileReference -BundleRoot $stagingDirectory `
        -RelativePath $databaseRelative
    $snapshotReference = New-FileReference -BundleRoot $stagingDirectory `
        -RelativePath $snapshotRelative
}

$counts = [ordered]@{
    selected = 1
    pass = 0
    fail = 0
    blocked = 0
    error = 0
    skipped = 0
}
$counts[$runStatus] = 1
$report = [ordered]@{
    protocol_version = $ProtocolVersion
    document_type = "dao_evidence_report"
    run_id = $RunId
    git = [ordered]@{
        commit = $GitCommit
        dirty = $false
    }
    oracle_revision = $GitCommit
    command_line = @([Environment]::GetCommandLineArgs())
    started_at_utc = $startedAt
    ended_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    status = $runStatus
    status_reason = $statusReason
    environment = $environmentReference
    counts = $counts
    scenarios = @(
        [ordered]@{
            scenario_id = $ScenarioId
            mode = [string]$scenario.mode
            capabilities = @($scenario.capabilities)
            status = $runStatus
            reason = $statusReason
            input = $inputReference
            source_database = $null
            output_database = $databaseReference
            dao_snapshot = $snapshotReference
            rust_snapshot = $null
            operation_log = $operationLogReference
        }
    )
}
$reportRelative = "report.json"
Write-JsonDocument -Path (
    Join-Path $stagingDirectory $reportRelative
) -Document $report

$manifestFiles = New-Object Collections.ArrayList
[void]$manifestFiles.Add(
    (New-ManifestEntry -BundleRoot $stagingDirectory `
        -RelativePath $environmentRelative -Role "environment" `
        -MediaType "application/json")
)
[void]$manifestFiles.Add(
    (New-ManifestEntry -BundleRoot $stagingDirectory `
        -RelativePath $reportRelative -Role "report" `
        -MediaType "application/json")
)
[void]$manifestFiles.Add(
    (New-ManifestEntry -BundleRoot $stagingDirectory `
        -RelativePath $scenarioInputRelative -Role "scenario_input" `
        -MediaType "application/json")
)
[void]$manifestFiles.Add(
    (New-ManifestEntry -BundleRoot $stagingDirectory `
        -RelativePath $operationLogRelative -Role "operation_log" `
        -MediaType "application/json")
)
if ($runStatus -eq "pass") {
    [void]$manifestFiles.Add(
        (New-ManifestEntry -BundleRoot $stagingDirectory `
            -RelativePath $databaseRelative -Role "output_database" `
            -MediaType "application/vnd.ms-access")
    )
    [void]$manifestFiles.Add(
        (New-ManifestEntry -BundleRoot $stagingDirectory `
            -RelativePath $snapshotRelative -Role "dao_snapshot" `
            -MediaType "application/json")
    )
}
elseif ($null -ne $diagnosticRelative) {
    [void]$manifestFiles.Add(
        (New-ManifestEntry -BundleRoot $stagingDirectory `
            -RelativePath $diagnosticRelative -Role "other" `
            -MediaType "application/vnd.ms-access")
    )
}

try {
    if (Test-Path -LiteralPath $databaseWorkingDirectory) {
        [IO.Directory]::Delete($databaseWorkingDirectory, $true)
    }
    $manifest = [ordered]@{
        protocol_version = $ProtocolVersion
        document_type = "dao_bundle_manifest"
        run_id = $RunId
        git_commit = $GitCommit
        dirty = $false
        created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        status = $runStatus
        report_path = $reportRelative
        scenario_ids = @($ScenarioId)
        files = @($manifestFiles)
    }
    Write-JsonDocument -Path (
        Join-Path $stagingDirectory "bundle-manifest.json"
    ) -Document $manifest
    [IO.Directory]::Move($stagingDirectory, $finalDirectory)
}
catch {
    [Console]::Error.WriteLine(
        "ERROR: evidence publication failed; staging retained at " +
        $stagingDirectory + ": " + $_.Exception.Message
    )
    exit 4
}

[Console]::WriteLine(
    $runStatus.ToUpperInvariant() + ": " + $statusReason +
    " Bundle: " + $finalDirectory
)
if ($runStatus -eq "pass") {
    exit 0
}
if ($runStatus -eq "fail") {
    exit 1
}
if ($runStatus -eq "blocked") {
    exit 3
}
exit 4
