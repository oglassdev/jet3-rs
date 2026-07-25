Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "M1.Provider.ps1")

$script:M1ProtocolVersion = "1.1.0"
$script:M1MaximumJsonBytes = 1048576L
$script:M1MaximumSourceBytes = 2097152L
$script:M1InventoryRelativePath = "oracle/windows-dao/examples/m1-inventory.json"
$script:M1ValidatorRelativePath = "oracle/windows-dao/scripts/validate_m1_protocol.py"
$script:M1RequiredSupportPaths = @(
    "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
    "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
    "oracle/windows-dao/scripts/m1_bundle_validation.py",
    "oracle/windows-dao/scripts/protocol_cli.py",
    "oracle/windows-dao/scripts/protocol_validation.py",
    "oracle/windows-dao/scripts/validate_m1_protocol.py",
    "oracle/windows-dao/examples/m1-inventory.json",
    "oracle/windows-dao/protocol/v1_1/bundle-manifest.schema.json",
    "oracle/windows-dao/protocol/v1_1/canonical-snapshot.schema.json",
    "oracle/windows-dao/protocol/v1_1/environment.schema.json",
    "oracle/windows-dao/protocol/v1_1/evidence-report.schema.json",
    "oracle/windows-dao/protocol/v1_1/example-inventory.schema.json",
    "oracle/windows-dao/protocol/v1_1/operation-log.schema.json",
    "oracle/windows-dao/protocol/v1_1/pair.schema.json",
    "oracle/windows-dao/protocol/v1_1/scenario.schema.json"
)
$script:M1ExpectedExamples = @(
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

function New-M1PreflightException {
    param(
        [ValidateSet("Invocation", "Blocked", "Error")][string]$Category,
        [string]$Message
    )

    if ([string]::IsNullOrWhiteSpace($Message)) { $Message = "M1 preflight failed." }
    if ($Message.Length -gt 2000) { $Message = $Message.Substring(0, 2000) }
    $exception = New-Object InvalidOperationException($Message)
    $exception.Data["M1Category"] = $Category
    return $exception
}

function Throw-M1Preflight {
    param(
        [ValidateSet("Invocation", "Blocked", "Error")][string]$Category,
        [string]$Message
    )
    throw (New-M1PreflightException -Category $Category -Message $Message)
}

function Test-M1PathWithin {
    param(
        [string]$Path,
        [string]$Parent
    )

    $separator = [IO.Path]::DirectorySeparatorChar
    $normalizedParent = $Parent.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $comparison = [StringComparison]::OrdinalIgnoreCase
    if ($Path.Equals($normalizedParent, $comparison)) { return $true }
    return $Path.StartsWith($normalizedParent + $separator, $comparison)
}

function Assert-M1LocalPathSyntax {
    param(
        [string]$Path,
        [string]$Label
    )

    if (-not [IO.Path]::IsPathRooted($Path)) {
        Throw-M1Preflight "Invocation" "$Label must be an absolute path." }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.StartsWith("\\", [StringComparison]::Ordinal)) {
        Throw-M1Preflight "Invocation" "$Label cannot be a UNC path." }
    if ($full.Length -gt 2 -and $full.Substring(2).Contains(":")) {
        Throw-M1Preflight "Invocation" "$Label cannot name an alternate stream." }
    return $full
}

function Assert-M1NoReparseAncestors {
    param(
        [string]$Path,
        [string]$Label
    )

    $cursor = $Path
    while (-not (Test-Path -LiteralPath $cursor)) {
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) {
            Throw-M1Preflight "Invocation" "$Label has no local ancestor." }
        $cursor = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Throw-M1Preflight "Invocation" (
                "$Label contains a reparse point: " + $item.FullName) }
        $parent = [IO.Path]::GetDirectoryName($item.FullName)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-M1BoundedFile {
    param(
        [string]$Path,
        [long]$MaximumBytes,
        [string]$Label,
        [ValidateSet("Invocation", "Blocked")]
            [string]$MissingCategory = "Invocation"
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Throw-M1Preflight $MissingCategory "$Label is missing." }
    Assert-M1NoReparseAncestors -Path $Path -Label $Label
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Length -gt $MaximumBytes) {
        Throw-M1Preflight "Invocation" "$Label exceeds the $MaximumBytes-byte limit."
    }
    return $item
}

function Get-M1StreamSha256 {
    param([IO.Stream]$Stream)

    if (-not $Stream.CanRead -or -not $Stream.CanSeek) {
        Throw-M1Preflight "Error" "A retained stream is unreadable." }
    $original = $Stream.Position
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        $bytes = $hasher.ComputeHash($Stream)
        return ([BitConverter]::ToString($bytes)).Replace(
            "-", "").ToLowerInvariant()
    }
    finally {
        $Stream.Position = $original
        $hasher.Dispose()
    }
}

function Get-M1ByteArraySha256 {
    param([byte[]]$Bytes)

    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hasher.ComputeHash($Bytes)
        ).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Assert-M1ByteArraySha256 {
    param(
        [byte[]]$Bytes,
        [string]$ExpectedSha256,
        [string]$Label
    )

    if (
        $ExpectedSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        (Get-M1ByteArraySha256 -Bytes $Bytes) -cne $ExpectedSha256
    ) {
        Throw-M1Preflight "Blocked" "$Label bytes differ from the bound digest."
    }
}

function Read-M1StreamBytes {
    param(
        [IO.Stream]$Stream,
        [long]$MaximumBytes
    )

    if ($Stream.Length -gt $MaximumBytes) {
        Throw-M1Preflight "Invocation" "Input grew beyond its byte limit." }
    $length = [int]$Stream.Length
    $bytes = New-Object byte[] $length
    $Stream.Position = 0
    $offset = 0
    while ($offset -lt $length) {
        $read = $Stream.Read($bytes, $offset, $length - $offset)
        if ($read -le 0) { Throw-M1Preflight "Error" "Bounded read ended early." }
        $offset += $read
    }
    return ,$bytes
}

function Read-M1BoundedFileBytes {
    param(
        [string]$Path,
        [long]$MaximumBytes
    )

    [void](Assert-M1BoundedFile -Path $Path -MaximumBytes $MaximumBytes `
        -Label "Bounded JSON input")
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::Read, 4096,
        [IO.FileOptions]::SequentialScan)
    try {
        return ,(Read-M1StreamBytes -Stream $stream -MaximumBytes $MaximumBytes)
    } finally { $stream.Dispose() }
}

function ConvertFrom-M1Utf8Json {
    param(
        [byte[]]$Bytes,
        [string]$Label
    )

    if (
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xef -and
        $Bytes[1] -eq 0xbb -and
        $Bytes[2] -eq 0xbf
    ) {
        Throw-M1Preflight "Invocation" "$Label contains a forbidden UTF-8 BOM."
    }
    try {
        $encoding = New-Object Text.UTF8Encoding($false, $true)
        return $encoding.GetString($Bytes) | ConvertFrom-Json
    }
    catch { Throw-M1Preflight "Invocation" (
        "$Label is not strict UTF-8 JSON: " + $_.Exception.Message) }
}

function Get-M1GitExecutable {
    $candidate = Get-Command git -CommandType Application `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $candidate -or -not [IO.File]::Exists($candidate.Source)) {
        Throw-M1Preflight "Blocked" "An exact Git executable is required." }
    return [IO.Path]::GetFullPath($candidate.Source)
}

function Get-M1PythonRegistryCandidates {
    $paths = New-Object Collections.ArrayList
    $hives = @([Microsoft.Win32.RegistryHive]::CurrentUser,
        [Microsoft.Win32.RegistryHive]::LocalMachine)
    $views = @([Microsoft.Win32.RegistryView]::Registry32,
        [Microsoft.Win32.RegistryView]::Registry64)
    foreach ($hive in $hives) {
        foreach ($view in $views) {
            $base = $null
            $root = $null
            try {
                $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
                $root = $base.OpenSubKey("Software\Python\PythonCore")
                if ($null -eq $root) { continue }
                foreach ($version in $root.GetSubKeyNames()) {
                    $install = $null
                    try {
                        $install = $root.OpenSubKey("$version\InstallPath")
                        if ($null -eq $install) { continue }
                        $path = [string]$install.GetValue("ExecutablePath")
                        if ([string]::IsNullOrWhiteSpace($path)) {
                            $directory = [string]$install.GetValue("")
                            if ($directory) {
                                $path = Join-Path $directory "python.exe"
                            }
                        }
                        if ($path) { [void]$paths.Add($path) }
                    }
                    finally {
                        if ($null -ne $install) { $install.Dispose() }
                    }
                }
            }
            finally {
                if ($null -ne $root) { $root.Dispose() }
                if ($null -ne $base) { $base.Dispose() }
            }
        }
    }
    return @($paths)
}

function Get-M1Python3 {
    $candidates = New-Object Collections.ArrayList
    foreach ($name in @("python3", "python", "py")) {
        $command = Get-Command $name -CommandType Application -ErrorAction `
            SilentlyContinue | Select-Object -First 1
        if ($null -ne $command) {
            [void]$candidates.Add([ordered]@{
                command = $command.Source
                prefix = $(if ($name -eq "py") { @("-3") } else { @() })
            })
        }
    }
    foreach ($path in Get-M1PythonRegistryCandidates) {
        [void]$candidates.Add([ordered]@{
            command = $path
            prefix = @()
        })
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        $command = [IO.Path]::GetFullPath([string]$candidate.command)
        if ($seen.ContainsKey($command) -or -not [IO.File]::Exists($command)) {
            continue
        }
        $seen[$command] = $true
        $prefix = @($candidate.prefix)
        $code = "import json,platform,sys;print(json.dumps({" +
            "'executable':sys.executable,'major':sys.version_info[0]," +
            "'version':platform.python_version()}))"
        $probe = (& $command @prefix -B -c $code 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { continue }
        try { $identity = $probe | ConvertFrom-Json } catch { continue }
        if (
            $identity.major -eq 3 -and
            -not [string]::IsNullOrWhiteSpace([string]$identity.executable)
        ) {
            return [pscustomobject]@{
                Command = $command
                Prefix = $prefix
                Executable = [IO.Path]::GetFullPath($identity.executable)
                Version = [string]$identity.version
            }
        }
    }
    Throw-M1Preflight "Blocked" "An exactly identified Python 3 is required."
}

function Invoke-M1Validator {
    param(
        [pscustomobject]$Python,
        [string]$ValidatorPath,
        [string[]]$Arguments
    )

    $prefix = @($Python.Prefix)
    $detail = (& $Python.Command @prefix -B $ValidatorPath @Arguments `
        2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        if ($detail.Length -gt 2000) { $detail = $detail.Substring(0, 2000) }
        Throw-M1Preflight "Invocation" ("Protocol validation failed: " + $detail)
    }
}

function Assert-M1GitState {
    param(
        [string]$GitPath,
        [string]$Repository,
        [string]$Commit
    )

    $head = (& $GitPath -C $Repository rev-parse HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Throw-M1Preflight "Error" "Git could not resolve HEAD." }
    if ($head -cne $Commit) {
        Throw-M1Preflight "Blocked" "GitCommit does not match HEAD." }
    $dirty = (& $GitPath -C $Repository status --porcelain=v1 `
        --untracked-files=all 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Throw-M1Preflight "Error" "Git could not inspect worktree." }
    if ($dirty) {
        Throw-M1Preflight "Blocked" "Evidence requires an exact clean worktree." }
}

function Assert-M1SafeRelativePath {
    param([string]$RelativePath)

    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains("\") -or
        $RelativePath.Contains(":") -or
        $RelativePath -match "(?:^|/)\.\.(?:/|$)" -or
        $RelativePath -cnotmatch "^[A-Za-z0-9._/-]+$"
    ) {
        Throw-M1Preflight "Invocation" (
            "Unsafe repository-relative source path: " + $RelativePath
        )
    }
}

function Assert-M1GitBoundPath {
    param(
        [string]$GitPath,
        [string]$Repository,
        [string]$Commit,
        [string]$RelativePath
    )

    Assert-M1SafeRelativePath -RelativePath $RelativePath
    $absolute = [IO.Path]::GetFullPath((Join-Path $Repository $RelativePath))
    if (-not (Test-M1PathWithin -Path $absolute -Parent $Repository)) {
        Throw-M1Preflight "Invocation" "Repository source escaped its root." }
    [void](Assert-M1BoundedFile -Path $absolute `
        -MaximumBytes $script:M1MaximumSourceBytes `
        -Label "Repository source $RelativePath")
    $gitObject = "${Commit}:" + $RelativePath
    $expected = (& $GitPath -C $Repository rev-parse $gitObject `
        2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Throw-M1Preflight "Blocked" (
            "GitCommit lacks required source: " + $RelativePath)
    }
    $actual = (& $GitPath -C $Repository hash-object -- $absolute `
        2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Throw-M1Preflight "Error" ("Git could not hash: " + $RelativePath)
    }
    if ($actual -cne $expected) {
        Throw-M1Preflight "Blocked" (
            "Input differs from GitCommit: " + $RelativePath)
    }
    return $absolute
}

function Assert-M1FinalCollisionFree {
    param([string]$FinalDirectory)

    if ([IO.File]::Exists($FinalDirectory) -or
        [IO.Directory]::Exists($FinalDirectory)) {
        Throw-M1Preflight "Invocation" "Evidence destination already exists." }
}

function Invoke-M1Preflight {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$EnvironmentPath,
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$GitCommit,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)]
            [string[]]$ExecutedRepoRelativeSourcePaths
    )

    $environmentStream = $null
    $providerStream = $null
    try {
        if ($GitCommit -cnotmatch "^[0-9a-f]{40}$") {
            Throw-M1Preflight "Invocation" "GitCommit must be 40 lowercase hexadecimal digits."
        }
        $runPattern = "^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$"
        if ($RunId -cnotmatch $runPattern) {
            Throw-M1Preflight "Invocation" "RunId is not protocol-valid."
        }
        if (-not $ExecutedRepoRelativeSourcePaths) {
            Throw-M1Preflight "Invocation" "Executed source paths cannot be empty."
        }
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
            Throw-M1Preflight "Blocked" "M1 DAO execution requires Windows."
        }

        $repository = Assert-M1LocalPathSyntax -Path $RepositoryRoot `
            -Label "RepositoryRoot"
        $environmentFile = Assert-M1LocalPathSyntax -Path $EnvironmentPath `
            -Label "EnvironmentPath"
        $output = Assert-M1LocalPathSyntax -Path $OutputRoot -Label "OutputRoot"
        if (-not (Test-Path -LiteralPath $repository -PathType Container)) {
            Throw-M1Preflight "Invocation" "RepositoryRoot is not a directory."
        }
        Assert-M1NoReparseAncestors -Path $repository -Label "RepositoryRoot"
        [void](Assert-M1BoundedFile -Path $environmentFile `
            -MaximumBytes $script:M1MaximumJsonBytes `
            -Label "Environment record" -MissingCategory "Blocked")
        Assert-M1NoReparseAncestors -Path $output -Label "OutputRoot"
        if (Test-M1PathWithin -Path $output -Parent $repository) {
            Throw-M1Preflight "Invocation" "OutputRoot must remain outside the repository."
        }
        if (Test-M1PathWithin -Path $environmentFile -Parent $repository) {
            Throw-M1Preflight "Invocation" "EnvironmentPath must remain outside the repository."
        }
        if (Test-M1PathWithin -Path $environmentFile -Parent $output) {
            Throw-M1Preflight "Invocation" "EnvironmentPath cannot alias OutputRoot."
        }
        $finalDirectory = Join-Path (Join-Path $output $GitCommit) $RunId
        Assert-M1FinalCollisionFree -FinalDirectory $finalDirectory

        $git = Get-M1GitExecutable
        Assert-M1GitState -GitPath $git -Repository $repository -Commit $GitCommit

        $allPaths = New-Object Collections.ArrayList
        foreach ($path in $script:M1RequiredSupportPaths) { [void]$allPaths.Add($path) }
        foreach ($name in $script:M1ExpectedExamples) {
            [void]$allPaths.Add("oracle/windows-dao/examples/$name")
        }
        foreach ($path in $ExecutedRepoRelativeSourcePaths) { [void]$allPaths.Add($path) }
        $seenPaths = @{}
        $boundPaths = New-Object Collections.ArrayList
        foreach ($pathValue in $allPaths) {
            $path = [string]$pathValue
            if ($seenPaths.ContainsKey($path)) { continue }
            $seenPaths[$path] = $true
            [void](Assert-M1GitBoundPath -GitPath $git -Repository $repository `
                -Commit $GitCommit -RelativePath $path)
            [void]$boundPaths.Add($path)
        }

        $environmentStream = New-Object IO.FileStream($environmentFile,
            [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read,
            4096, [IO.FileOptions]::SequentialScan)
        if ($environmentStream.Length -gt $script:M1MaximumJsonBytes) {
            Throw-M1Preflight "Invocation" "Environment grew beyond its byte limit."
        }
        $environmentSha = Get-M1StreamSha256 -Stream $environmentStream
        $environmentBytes = Read-M1StreamBytes -Stream $environmentStream `
            -MaximumBytes $script:M1MaximumJsonBytes

        $python = Get-M1Python3
        $validator = Join-Path $repository $script:M1ValidatorRelativePath
        $inventory = Join-Path $repository $script:M1InventoryRelativePath
        Invoke-M1Validator -Python $python -ValidatorPath $validator -Arguments @("schemas")
        Invoke-M1Validator -Python $python -ValidatorPath $validator `
            -Arguments @("document", $inventory)
        Invoke-M1Validator -Python $python -ValidatorPath $validator `
            -Arguments @("document", $environmentFile)
        $inventoryBytes = Read-M1BoundedFileBytes -Path $inventory `
            -MaximumBytes $script:M1MaximumJsonBytes
        $inventorySha = Get-M1ByteArraySha256 -Bytes $inventoryBytes
        $inventoryDocument = ConvertFrom-M1Utf8Json -Bytes $inventoryBytes `
            -Label "M1 example inventory"
        $environment = ConvertFrom-M1Utf8Json -Bytes $environmentBytes `
            -Label "Environment record"
        $accepted = Assert-M1ProviderEnvironment -Environment $environment
        $providerPath = Assert-M1CurrentRegistration -AcceptedProvider $accepted
        $providerStream = New-Object IO.FileStream($providerPath,
            [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read,
            4096, [IO.FileOptions]::SequentialScan)
        $providerSha = Get-M1StreamSha256 -Stream $providerStream
        if ($providerSha -cne [string]$accepted.server_sha256) {
            Throw-M1Preflight "Blocked" "Provider binary hash has drifted."
        }

        $context = [pscustomobject]@{
            ContextKind = "jet3-rs-m1-preflight-v1"
            Repository = $repository
            RepositoryRoot = $repository
            EnvironmentPath = $environmentFile
            EnvironmentSha256 = $environmentSha
            EnvironmentLength = [long]$environmentStream.Length
            EnvironmentBytes = $environmentBytes
            Environment = $environment
            OutputRoot = $output
            FinalDirectory = $finalDirectory
            InventoryPath = $inventory
            InventoryBytes = $inventoryBytes
            InventorySha256 = $inventorySha
            Inventory = $inventoryDocument
            ValidatorPath = $validator
            GitCommit = $GitCommit
            RunId = $RunId
            GitExecutable = $git
            Python = $python
            PythonPath = $python.Executable
            BoundRepoRelativePaths = @($boundPaths)
            AcceptedProvider = $accepted
            ProviderPath = $providerPath
            ProviderSha256 = $providerSha
            EnvironmentStream = $environmentStream
            ProviderStream = $providerStream
        }
        $environmentStream = $null
        $providerStream = $null
        return $context
    }
    catch {
        if ($null -ne $providerStream) { $providerStream.Dispose() }
        if ($null -ne $environmentStream) { $environmentStream.Dispose() }
        if ($_.Exception.Data.Contains("M1Category")) { throw $_.Exception }
        Throw-M1Preflight "Error" ("Unhandled M1 preflight failure: " +
            $_.Exception.Message)
    }
}

function Assert-M1PreflightCurrent {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)

    try {
        if ($Context.ContextKind -cne "jet3-rs-m1-preflight-v1") {
            Throw-M1Preflight "Invocation" "Invalid M1 preflight context."
        }
        Assert-M1GitState -GitPath $Context.GitExecutable -Repository `
            $Context.RepositoryRoot -Commit $Context.GitCommit
        foreach ($path in $Context.BoundRepoRelativePaths) {
            [void](Assert-M1GitBoundPath -GitPath $Context.GitExecutable `
                -Repository $Context.RepositoryRoot `
                -Commit $Context.GitCommit -RelativePath $path)
        }
        if (
            -not $Context.EnvironmentStream.CanRead -or
            $Context.EnvironmentStream.Length -ne $Context.EnvironmentLength -or
            (Get-M1StreamSha256 -Stream $Context.EnvironmentStream) -cne
                $Context.EnvironmentSha256
        ) {
            Throw-M1Preflight "Blocked" "Environment record changed after preflight."
        }
        $accepted = Assert-M1ProviderEnvironment -Environment $Context.Environment
        $providerPath = Assert-M1CurrentRegistration -AcceptedProvider $accepted
        if (-not $providerPath.Equals(
            $Context.ProviderPath,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            Throw-M1Preflight "Blocked" "Provider path changed after preflight."
        }
        if (
            -not $Context.ProviderStream.CanRead -or
            (Get-M1StreamSha256 -Stream $Context.ProviderStream) -cne
                $Context.ProviderSha256
        ) {
            Throw-M1Preflight "Blocked" "Provider binary changed after preflight."
        }
        Assert-M1FinalCollisionFree -FinalDirectory $Context.FinalDirectory
        return $true
    }
    catch {
        if ($_.Exception.Data.Contains("M1Category")) { throw $_.Exception }
        Throw-M1Preflight "Error" ("Unhandled M1 recheck failure: " +
            $_.Exception.Message)
    }
}

function Assert-M1RuntimeBinding {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)
    return Assert-M1PreflightCurrent -Context $Context
}

function Close-M1PreflightContext {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][pscustomobject]$Context)

    foreach ($name in @("ProviderStream", "EnvironmentStream")) {
        $stream = $Context.$name
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}
