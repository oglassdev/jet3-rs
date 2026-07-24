[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$SkipDbVersion30Test
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProtocolVersion = "1.0.0"
# Microsoft Learn DatabaseTypeEnum documents dbVersion30 as 32.
# Clean-room provenance: docs/PROVENANCE.md SRC-0002.
$DbVersion30 = 32
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$CandidateProgIds = @(
    "DAO.DBEngine.30",
    "DAO.DBEngine.35",
    "DAO.DBEngine.36",
    "DAO.DBEngine.120",
    "DAO.DBEngine.140",
    "DAO.DBEngine.160"
)

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

function Get-RegistryViewName {
    param([Microsoft.Win32.RegistryView]$View)

    if ($View -eq [Microsoft.Win32.RegistryView]::Registry32) {
        return "x86"
    }
    return "x64"
}

function Get-RegisteredCandidate {
    param(
        [string]$ProgId,
        [Microsoft.Win32.RegistryView]$View,
        [Microsoft.Win32.RegistryHive]$Hive,
        [string]$Scope
    )

    $baseKey = $null
    $progIdKey = $null
    $serverKey = $null
    try {
        $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
            $Hive,
            $View
        )
        $progIdKey = $baseKey.OpenSubKey("Software\Classes\$ProgId\CLSID")
        if ($null -eq $progIdKey) {
            return $null
        }
        $clsid = [string]$progIdKey.GetValue("")
        if ([string]::IsNullOrWhiteSpace($clsid)) {
            return $null
        }

        $serverPath = $null
        foreach ($serverKind in @("InprocServer32", "LocalServer32")) {
            if ($null -ne $serverKey) {
                $serverKey.Dispose()
                $serverKey = $null
            }
            $serverKey = $baseKey.OpenSubKey(
                "Software\Classes\CLSID\$clsid\$serverKind"
            )
            if ($null -ne $serverKey) {
                $candidatePath = [string]$serverKey.GetValue("")
                if (-not [string]::IsNullOrWhiteSpace($candidatePath)) {
                    $expandedPath = [Environment]::ExpandEnvironmentVariables(
                        $candidatePath.Trim()
                    )
                    if ($expandedPath.StartsWith('"')) {
                        $closingQuote = $expandedPath.IndexOf('"', 1)
                        if ($closingQuote -gt 1) {
                            $serverPath = $expandedPath.Substring(
                                1,
                                $closingQuote - 1
                            )
                        }
                    }
                    elseif (Test-Path -LiteralPath $expandedPath -PathType Leaf) {
                        $serverPath = $expandedPath
                    }
                    elseif ($expandedPath -match "^(.*?\\.(?:dll|exe))(?:\\s|$)") {
                        $serverPath = $Matches[1]
                    }
                    break
                }
            }
        }

        $fileVersion = $null
        $fileHash = $null
        if (
            -not [string]::IsNullOrWhiteSpace($serverPath) -and
            (Test-Path -LiteralPath $serverPath -PathType Leaf)
        ) {
            $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo(
                $serverPath
            ).FileVersion
            $hashRecord = Get-FileHash -LiteralPath $serverPath -Algorithm SHA256
            $fileHash = $hashRecord.Hash.ToLowerInvariant()
        }

        return [ordered]@{
            prog_id = $ProgId
            clsid = $clsid
            registry_view = (Get-RegistryViewName -View $View)
            registration_scope = $Scope
            registered = $true
            server_path = $serverPath
            server_file_version = $fileVersion
            server_sha256 = $fileHash
            activation = "not_tested"
            provider_version = $null
            dbversion30_test = [ordered]@{
                status = "not_run"
                detail = "Candidate has not been tested in this process."
            }
        }
    }
    finally {
        if ($null -ne $serverKey) {
            $serverKey.Dispose()
        }
        if ($null -ne $progIdKey) {
            $progIdKey.Dispose()
        }
        if ($null -ne $baseKey) {
            $baseKey.Dispose()
        }
    }
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

function Test-DbVersion30Provider {
    param([System.Collections.IDictionary]$Candidate)

    $engine = $null
    $workspace = $null
    $database = $null
    $temporaryDirectory = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("jet3-rs-dao-probe-" + [Guid]::NewGuid().ToString("N"))
    $databasePath = Join-Path $temporaryDirectory "probe.mdb"

    try {
        $comType = [Type]::GetTypeFromProgID($Candidate.prog_id, $false)
        if ($null -eq $comType) {
            $Candidate.activation = "failed"
            $Candidate.dbversion30_test = [ordered]@{
                status = "fail"
                detail = "ProgID could not be resolved in the current process."
            }
            return
        }

        $engine = [Activator]::CreateInstance($comType)
        $Candidate.activation = "succeeded"
        try {
            $Candidate.provider_version = [string]$engine.Version
        }
        catch {
            $Candidate.provider_version = $null
        }

        if ($SkipDbVersion30Test) {
            $Candidate.dbversion30_test = [ordered]@{
                status = "not_run"
                detail = "dbVersion30 creation was explicitly skipped."
            }
            return
        }

        [IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
        $workspace = $engine.Workspaces.Item(0)
        $database = $workspace.CreateDatabase(
            $databasePath,
            $DatabaseLocale,
            $DbVersion30
        )
        $database.Close()
        Release-ComObject -Value $database
        $database = $null

        if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
            $Candidate.dbversion30_test = [ordered]@{
                status = "fail"
                detail = "CreateDatabase returned without creating the MDB file."
            }
            return
        }
        $Candidate.dbversion30_test = [ordered]@{
            status = "pass"
            detail = "Created and closed a disposable dbVersion30 MDB."
        }
    }
    catch {
        if ($Candidate.activation -ne "succeeded") {
            $Candidate.activation = "failed"
        }
        $Candidate.dbversion30_test = [ordered]@{
            status = "fail"
            detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
        }
    }
    finally {
        if ($null -ne $database) {
            try {
                $database.Close()
            }
            catch {
                # Cleanup errors are reflected by the failed primary operation.
            }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
        if (Test-Path -LiteralPath $databasePath) {
            Remove-Item -LiteralPath $databasePath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Force -Recurse `
                -ErrorAction SilentlyContinue
        }
    }
}

function Get-HostRecord {
    $processArchitecture = Get-ProcessArchitecture
    $computerName = [Environment]::MachineName
    $caption = [Environment]::OSVersion.VersionString
    $version = [Environment]::OSVersion.Version.ToString()
    $build = [Environment]::OSVersion.Version.Build.ToString()
    $osArchitecture = [Environment]::GetEnvironmentVariable(
        "PROCESSOR_ARCHITEW6432"
    )
    if ([string]::IsNullOrWhiteSpace($osArchitecture)) {
        $osArchitecture = [Environment]::GetEnvironmentVariable(
            "PROCESSOR_ARCHITECTURE"
        )
    }
    if ([string]::IsNullOrWhiteSpace($osArchitecture)) {
        $osArchitecture = "unknown"
    }

    try {
        $operatingSystem = Get-CimInstance Win32_OperatingSystem
        $caption = [string]$operatingSystem.Caption
        $version = [string]$operatingSystem.Version
        $build = [string]$operatingSystem.BuildNumber
        $osArchitecture = [string]$operatingSystem.OSArchitecture
    }
    catch {
        # Environment/.NET values above remain reproducible fallbacks.
    }

    return [ordered]@{
        is_windows = $true
        computer_name = $computerName
        os_caption = $caption
        os_version = $version
        os_build = $build
        os_architecture = $osArchitecture
        process_architecture = $processArchitecture
    }
}

function Get-RegionalRecord {
    $culture = [Globalization.CultureInfo]::CurrentCulture
    $uiCulture = [Globalization.CultureInfo]::CurrentUICulture
    $timeZone = [TimeZoneInfo]::Local
    $offset = $timeZone.GetUtcOffset([DateTimeOffset]::Now)
    $offsetText = "{0}{1:00}:{2:00}" -f @(
        $(if ($offset.Ticks -lt 0) { "-" } else { "+" }),
        [Math]::Abs($offset.Hours),
        [Math]::Abs($offset.Minutes)
    )

    return [ordered]@{
        culture = $culture.Name
        ui_culture = $uiCulture.Name
        ansi_code_page = $culture.TextInfo.ANSICodePage
        oem_code_page = $culture.TextInfo.OEMCodePage
        timezone_id = $timeZone.Id
        utc_offset = $offsetText
    }
}

function Write-Record {
    param(
        [System.Collections.IDictionary]$Record,
        [string]$Path
    )

    $absolutePath = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($absolutePath)
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    $json = $Record | ConvertTo-Json -Depth 12
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($absolutePath, $json + "`n", $encoding)
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    [Console]::Error.WriteLine("OutputPath is required.")
    exit 2
}

$record = [ordered]@{
    protocol_version = $ProtocolVersion
    document_type = "dao_environment"
    captured_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    status = "error"
    status_reason = "Probe did not complete."
    host = [ordered]@{
        is_windows = $false
        computer_name = [Environment]::MachineName
        os_caption = [Environment]::OSVersion.VersionString
        os_version = [Environment]::OSVersion.Version.ToString()
        os_build = [Environment]::OSVersion.Version.Build.ToString()
        os_architecture = "unknown"
        process_architecture = (Get-ProcessArchitecture)
    }
    runtime = [ordered]@{
        powershell_edition = [string]$PSVersionTable.PSEdition
        powershell_version = [string]$PSVersionTable.PSVersion
        dotnet_version = [string][Environment]::Version
    }
    regional = (Get-RegionalRecord)
    provider_candidates = @()
    accepted_provider = $null
}

$exitCode = 1
try {
    $isWindowsHost = [Environment]::OSVersion.Platform -eq (
        [PlatformID]::Win32NT
    )
    if (-not $isWindowsHost) {
        $record.status = "blocked"
        $record.status_reason = (
            "Microsoft DAO COM probing requires Windows; this host is not Windows."
        )
        $exitCode = 3
    }
    else {
        $record.host = Get-HostRecord
        $registryViews = @(
            [Microsoft.Win32.RegistryView]::Registry32,
            [Microsoft.Win32.RegistryView]::Registry64
        )
        $candidates = @()
        foreach ($progId in $CandidateProgIds) {
            foreach ($view in $registryViews) {
                $candidate = Get-RegisteredCandidate -ProgId $progId -View $view `
                    -Hive ([Microsoft.Win32.RegistryHive]::CurrentUser) `
                    -Scope "user"
                if ($null -eq $candidate) {
                    $candidate = Get-RegisteredCandidate -ProgId $progId `
                        -View $view `
                        -Hive ([Microsoft.Win32.RegistryHive]::LocalMachine) `
                        -Scope "machine"
                }
                if ($null -ne $candidate) {
                    $candidates += $candidate
                }
            }
        }

        $processView = if ((Get-ProcessArchitecture) -eq "x86") {
            "x86"
        }
        else {
            "x64"
        }
        foreach ($candidate in $candidates) {
            if ($candidate.registry_view -eq $processView) {
                Test-DbVersion30Provider -Candidate $candidate
            }
            else {
                $candidate.dbversion30_test = [ordered]@{
                    status = "not_run"
                    detail = (
                        "Candidate bitness differs from this PowerShell process."
                    )
                }
            }
        }
        $record.provider_candidates = @($candidates)

        $passing = @(
            $candidates | Where-Object {
                $_.dbversion30_test.status -eq "pass"
            }
        )
        $identifiedPassing = @(
            $passing | Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.clsid) -and
                -not [string]::IsNullOrWhiteSpace($_.provider_version) -and
                -not [string]::IsNullOrWhiteSpace($_.server_path) -and
                -not [string]::IsNullOrWhiteSpace($_.server_file_version) -and
                -not [string]::IsNullOrWhiteSpace($_.server_sha256)
            }
        )
        if ($identifiedPassing.Count -gt 0 -and -not $SkipDbVersion30Test) {
            $selected = $identifiedPassing[0]
            $record.accepted_provider = [ordered]@{
                prog_id = $selected.prog_id
                clsid = $selected.clsid
                registry_view = $selected.registry_view
                registration_scope = $selected.registration_scope
                provider_version = $selected.provider_version
                server_path = $selected.server_path
                server_file_version = $selected.server_file_version
                server_sha256 = $selected.server_sha256
                database_version = "dbVersion30"
            }
            $record.status = "ready"
            $record.status_reason = (
                "A provider created and closed a disposable dbVersion30 MDB."
            )
            $exitCode = 0
        }
        elseif ($SkipDbVersion30Test) {
            $record.status = "blocked"
            $record.status_reason = (
                "Provider inventory completed, but dbVersion30 testing was skipped."
            )
            $exitCode = 3
        }
        elseif ($candidates.Count -eq 0) {
            $record.status = "blocked"
            $record.status_reason = (
                "No known DAO-capable COM ProgID was registered in either bitness."
            )
            $exitCode = 3
        }
        elseif ($passing.Count -gt 0) {
            $record.status = "blocked"
            $record.status_reason = (
                "A candidate passed dbVersion30, but its exact COM server and " +
                "provider versions could not be recorded."
            )
            $exitCode = 3
        }
        else {
            $record.status = "blocked"
            $record.status_reason = (
                "Registered candidates exist, but none passed dbVersion30 in this " +
                "PowerShell process. Inspect candidate bitness and test details."
            )
            $exitCode = 3
        }
    }
}
catch {
    $record.status = "error"
    $record.status_reason = (
        $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    )
    $record.accepted_provider = $null
    $exitCode = 1
}

try {
    Write-Record -Record $record -Path $OutputPath
}
catch {
    [Console]::Error.WriteLine(
        "Unable to write environment record: " + $_.Exception.Message
    )
    exit 1
}

Write-Output (
    $record.status.ToUpperInvariant() + ": " + $record.status_reason
)
exit $exitCode
