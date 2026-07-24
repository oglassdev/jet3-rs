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

$ProtocolVersion = "1.1.0"
$InventoryRelativePath = "oracle/windows-dao/examples/m1-inventory.json"
$ValidatorRelativePath = (
    "oracle/windows-dao/scripts/validate_m1_protocol.py"
)
$RunnerRelativePath = (
    "oracle/windows-dao/scripts/preflight-m1-controlled.ps1"
)
$ExpectedExampleNames = @(
    "DAO-GEN-BINARY-MARKER-001.scenario.json",
    "DAO-GEN-EMPTY-REPEAT-A.scenario.json",
    "DAO-GEN-EMPTY-REPEAT-B.scenario.json",
    "DAO-GEN-LONGBINARY-LADDER-001.scenario.json",
    "DAO-GEN-MEMO-LADDER-001.scenario.json",
    "DAO-GEN-TEXT8-BASELINE-001.scenario.json",
    "DAO-GEN-TEXT8-INDEXED-001.scenario.json",
    "DAO-PAIR-EMPTY-REPEAT-001.pair.json",
    "DAO-PAIR-TEXT8-INDEX-001.pair.json"
)

trap {
    [Console]::Error.WriteLine(
        "ERROR: unhandled M1 preflight failure: " + $_.Exception.Message
    )
    exit 4
}

function Exit-InvocationError {
    param([string]$Message)

    [Console]::Error.WriteLine("INVALID: " + $Message)
    exit 2
}

function Exit-Blocked {
    param([string]$Message)

    [Console]::Error.WriteLine("BLOCKED: " + $Message)
    exit 3
}

function Get-LowerSha256 {
    param([string]$Path)

    $hashRecord = Get-FileHash -LiteralPath $Path -Algorithm SHA256
    return $hashRecord.Hash.ToLowerInvariant()
}

function Get-ProcessArchitecture {
    $architecture = [Environment]::GetEnvironmentVariable(
        "PROCESSOR_ARCHITECTURE"
    )
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

function Assert-GitBoundFile {
    param(
        [string]$Repository,
        [string]$Commit,
        [string]$RelativePath,
        [string]$AbsolutePath
    )

    $expectedBlob = (& git -C $Repository rev-parse (
        "${Commit}:" + $RelativePath
    ) 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git could not resolve $RelativePath at $Commit"
    }
    $actualBlob = (& git -C $Repository hash-object $AbsolutePath `
        2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git could not hash $RelativePath"
    }
    if ($actualBlob -cne $expectedBlob) {
        Exit-Blocked -Message (
            "executed M1 input differs from GitCommit: " + $RelativePath
        )
    }
}

function Invoke-M1DocumentValidator {
    param(
        [string]$ValidatorPath,
        [string]$DocumentPath
    )

    $candidates = @(
        @{ Name = "python3"; Prefix = @("-B") },
        @{ Name = "python"; Prefix = @("-B") },
        @{ Name = "py"; Prefix = @("-3", "-B") }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command -Name $candidate.Name `
            -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $command) {
            continue
        }
        $prefix = @($candidate.Prefix)
        $detail = (& $command.Source @prefix $ValidatorPath document `
            $DocumentPath 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($detail.Length -gt 2000) {
            $detail = $detail.Substring(0, 2000)
        }
        Exit-InvocationError -Message (
            "Protocol 1.1 document validation failed: " + $detail
        )
    }
    Exit-Blocked -Message (
        "Python 3 is required for fail-closed protocol 1.1 validation."
    )
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
    Exit-InvocationError -Message (
        "GitCommit must be 40 lowercase hexadecimal digits."
    )
}
if ($RunId -cnotmatch "^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$") {
    Exit-InvocationError -Message "RunId does not match the protocol pattern."
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Exit-Blocked -Message "M1 DAO preflight requires Windows."
}

$repository = [IO.Path]::GetFullPath($RepositoryRoot)
$environmentSource = [IO.Path]::GetFullPath($EnvironmentPath)
$output = [IO.Path]::GetFullPath($OutputRoot)
$inventorySource = [IO.Path]::GetFullPath(
    (Join-Path $repository $InventoryRelativePath)
)
$validatorSource = [IO.Path]::GetFullPath(
    (Join-Path $repository $ValidatorRelativePath)
)
if (-not (Test-Path -LiteralPath $repository -PathType Container)) {
    Exit-InvocationError -Message "RepositoryRoot is not a directory."
}
if (-not (Test-Path -LiteralPath $inventorySource -PathType Leaf)) {
    Exit-InvocationError -Message "The checked M1 inventory is missing."
}
if (-not (Test-Path -LiteralPath $validatorSource -PathType Leaf)) {
    Exit-InvocationError -Message "The checked M1 validator is missing."
}
if (-not (Test-Path -LiteralPath $environmentSource -PathType Leaf)) {
    Exit-Blocked -Message "the ready M1 environment record is missing."
}

try {
    $head = (& git -C $repository rev-parse HEAD 2>&1 |
        Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git rev-parse failed: $head"
    }
    if ($head -cne $GitCommit) {
        Exit-Blocked -Message "GitCommit does not match repository HEAD."
    }
    $dirty = (& git -C $repository status --porcelain=v1 `
        --untracked-files=all 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed: $dirty"
    }
    if (-not [string]::IsNullOrWhiteSpace($dirty)) {
        Exit-Blocked -Message (
            "release evidence requires a clean git worktree."
        )
    }
    Assert-GitBoundFile -Repository $repository -Commit $GitCommit `
        -RelativePath $RunnerRelativePath -AbsolutePath $PSCommandPath
    Assert-GitBoundFile -Repository $repository -Commit $GitCommit `
        -RelativePath $InventoryRelativePath -AbsolutePath $inventorySource
    Assert-GitBoundFile -Repository $repository -Commit $GitCommit `
        -RelativePath $ValidatorRelativePath -AbsolutePath $validatorSource
}
catch {
    [Console]::Error.WriteLine(
        "ERROR: unable to verify git identity: " + $_.Exception.Message
    )
    exit 4
}

Invoke-M1DocumentValidator -ValidatorPath $validatorSource `
    -DocumentPath $inventorySource
Invoke-M1DocumentValidator -ValidatorPath $validatorSource `
    -DocumentPath $environmentSource

try {
    $inventory = Get-Content -LiteralPath $inventorySource -Raw `
        -Encoding UTF8 | ConvertFrom-Json
    $environment = Get-Content -LiteralPath $environmentSource -Raw `
        -Encoding UTF8 | ConvertFrom-Json
}
catch {
    Exit-InvocationError -Message (
        "Inventory or environment JSON could not be parsed: " +
        $_.Exception.Message
    )
}

try {
    $inventoryHeaderIsValid = (
        $inventory.protocol_version -eq $ProtocolVersion -and
        $inventory.document_type -eq "dao_example_inventory" -and
        $inventory.generator -eq (
            "oracle/windows-dao/scripts/build_m1_examples.py"
        ) -and
        $inventory.files.Count -eq $ExpectedExampleNames.Count
    )
}
catch {
    $inventoryHeaderIsValid = $false
}
if (-not $inventoryHeaderIsValid) {
    Exit-InvocationError -Message "The checked M1 inventory is invalid."
}

$seenExampleNames = @{}
foreach ($entry in $inventory.files) {
    $name = [string]$entry.path
    if (
        [IO.Path]::GetFileName($name) -cne $name -or
        $ExpectedExampleNames -cnotcontains $name -or
        $seenExampleNames.ContainsKey($name) -or
        [string]$entry.sha256 -cnotmatch "^[0-9a-f]{64}$"
    ) {
        Exit-InvocationError -Message (
            "The M1 inventory contains an unexpected or duplicate entry."
        )
    }
    $seenExampleNames[$name] = $true
    $relativePath = "oracle/windows-dao/examples/$name"
    $source = [IO.Path]::GetFullPath((Join-Path $repository $relativePath))
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        Exit-InvocationError -Message "The M1 example is missing: $name"
    }
    if ((Get-LowerSha256 -Path $source) -cne [string]$entry.sha256) {
        Exit-InvocationError -Message (
            "The M1 example hash differs from its inventory: " + $name
        )
    }
    try {
        Assert-GitBoundFile -Repository $repository -Commit $GitCommit `
            -RelativePath $relativePath -AbsolutePath $source
    }
    catch {
        [Console]::Error.WriteLine(
            "ERROR: unable to bind M1 example to git: " +
            $_.Exception.Message
        )
        exit 4
    }
}
foreach ($expectedName in $ExpectedExampleNames) {
    if (-not $seenExampleNames.ContainsKey($expectedName)) {
        Exit-InvocationError -Message (
            "The M1 inventory omits the controlled example: " + $expectedName
        )
    }
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
    Exit-Blocked -Message (
        "environment record is not ready for M1 dbVersion30 execution."
    )
}

$accepted = $environment.accepted_provider
$matchingCandidates = @(
    $environment.provider_candidates | Where-Object {
        $_.prog_id -ceq $accepted.prog_id -and
        $_.clsid -ieq $accepted.clsid -and
        $_.registry_view -ceq $accepted.registry_view -and
        $_.registration_scope -ceq $accepted.registration_scope -and
        $_.provider_version -ceq $accepted.provider_version -and
        $_.server_path -ceq $accepted.server_path -and
        $_.server_file_version -ceq $accepted.server_file_version -and
        $_.server_sha256 -ceq $accepted.server_sha256 -and
        $_.registered -eq $true -and
        $_.activation -eq "succeeded" -and
        $_.dbversion30_test.status -eq "pass"
    }
)
if ($matchingCandidates.Count -ne 1) {
    Exit-Blocked -Message (
        "accepted provider lacks one exact successful probe result."
    )
}
if (
    $environment.host.computer_name -ine [Environment]::MachineName -or
    $environment.host.process_architecture -cne (Get-ProcessArchitecture) -or
    $accepted.registry_view -cne (Get-ProcessArchitecture)
) {
    Exit-Blocked -Message (
        "environment host or provider bitness differs from this process."
    )
}
if (-not (Test-Path -LiteralPath $accepted.server_path -PathType Leaf)) {
    Exit-Blocked -Message "accepted provider binary is missing."
}
if ((Get-LowerSha256 -Path $accepted.server_path) -cne $accepted.server_sha256) {
    Exit-Blocked -Message (
        "accepted provider binary hash has changed since probing."
    )
}

$finalDirectory = Join-Path (Join-Path $output $GitCommit) $RunId
if (Test-Path -LiteralPath $finalDirectory) {
    Exit-InvocationError -Message (
        "The immutable evidence directory already exists."
    )
}

# SRC-0012 records the unresolved boundary. In particular, the reviewed
# Microsoft sources do not specify how late-bound PowerShell must marshal and
# read back deterministic dbBinary and dbLongBinary values for DAO Variant and
# AppendChunk calls. This preflight intentionally performs no COM activation,
# database mutation, directory creation, or evidence publication.
Exit-Blocked -Message (
    "M1 execution is disabled pending a reviewed, commit-bound Windows " +
    "experiment for deterministic PowerShell COM Variant/AppendChunk " +
    "marshalling and DAO readback. No database or evidence bundle was created."
)
