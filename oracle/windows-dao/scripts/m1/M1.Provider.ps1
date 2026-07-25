# EXP-0006 established the only reviewed M1 PowerShell/DAO adapter policy.
$script:M1ProviderPolicy = [ordered]@{
    prog_id = "DAO.DBEngine.36"
    clsid = "{00000100-0000-0010-8000-00AA006D2EA4}"
    registry_view = "x86"
    registration_scope = "machine"
    provider_version = "3.6"
    server_path = "C:\Program Files (x86)\Common Files\Microsoft Shared\DAO\dao360.dll"
    server_file_version = "03.60.9765.0"
    server_sha256 = "4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac"
    database_version = "dbVersion30"
}

function Get-M1CurrentHostBinding {
    $caption = [Environment]::OSVersion.VersionString
    $version = [Environment]::OSVersion.Version.ToString()
    $build = [Environment]::OSVersion.Version.Build.ToString()
    $architecture = [Environment]::GetEnvironmentVariable(
        "PROCESSOR_ARCHITEW6432"
    )
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = [Environment]::GetEnvironmentVariable(
            "PROCESSOR_ARCHITECTURE"
        )
    }
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = "unknown"
    }
    try {
        $operatingSystem = Get-CimInstance Win32_OperatingSystem
        $caption = [string]$operatingSystem.Caption
        $version = [string]$operatingSystem.Version
        $build = [string]$operatingSystem.BuildNumber
        $architecture = [string]$operatingSystem.OSArchitecture
    }
    catch {
        # Match the probe's documented Environment/.NET fallback.
    }
    return [ordered]@{
        computer_name = [Environment]::MachineName
        os_architecture = $architecture
        os_build = $build
        os_caption = $caption
        os_version = $version
        process_architecture = if ([IntPtr]::Size -eq 4) { "x86" } else { "x64" }
    }
}

function Get-M1CurrentRegionalBinding {
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
        ansi_code_page = $culture.TextInfo.ANSICodePage
        culture = $culture.Name
        oem_code_page = $culture.TextInfo.OEMCodePage
        timezone_id = $timeZone.Id
        ui_culture = $uiCulture.Name
        utc_offset = $offsetText
    }
}

function Assert-M1ExactRecordFields {
    param(
        [object]$Recorded,
        [Collections.IDictionary]$Current,
        [string[]]$Fields,
        [string]$Label
    )

    foreach ($field in $Fields) {
        if ([string]$Recorded.$field -cne [string]$Current[$field]) {
            Throw-M1Preflight "Blocked" "$Label field is stale: $field"
        }
    }
}

function Get-M1RegistryRegistration {
    param(
        [ValidateSet("user", "machine")][string]$Scope,
        [string]$ProgId
    )

    $hive = if ($Scope -eq "user") {
        [Microsoft.Win32.RegistryHive]::CurrentUser } else {
        [Microsoft.Win32.RegistryHive]::LocalMachine }
    $base = $null
    $progKey = $null
    $serverKey = $null
    try {
        $view = [Microsoft.Win32.RegistryView]::Registry32
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
        $progKey = $base.OpenSubKey("Software\Classes\$ProgId\CLSID")
        if ($null -eq $progKey) { return $null }
        $clsid = [string]$progKey.GetValue("")
        if ([string]::IsNullOrWhiteSpace($clsid)) {
            return [pscustomobject]@{ Clsid = ""; ServerPath = "" }
        }
        $serverPath = ""
        foreach ($kind in @("InprocServer32", "LocalServer32")) {
            if ($null -ne $serverKey) {
                $serverKey.Dispose()
                $serverKey = $null
            }
            $keyPath = "Software\Classes\CLSID\$clsid\$kind"
            $serverKey = $base.OpenSubKey($keyPath)
            if ($null -eq $serverKey) { continue }
            $raw = [Environment]::ExpandEnvironmentVariables(
                ([string]$serverKey.GetValue("")).Trim()
            )
            if ($raw.StartsWith('"')) {
                $closing = $raw.IndexOf('"', 1)
                if ($closing -gt 1 -and $closing -eq $raw.Length - 1) {
                    $serverPath = $raw.Substring(1, $closing - 1)
                }
            }
            elseif (-not [string]::IsNullOrWhiteSpace($raw)) {
                $serverPath = $raw
            }
            break
        }
        return [pscustomobject]@{ Clsid = $clsid; ServerPath = $serverPath }
    }
    finally {
        if ($null -ne $serverKey) { $serverKey.Dispose() }
        if ($null -ne $progKey) { $progKey.Dispose() }
        if ($null -ne $base) { $base.Dispose() }
    }
}

function Assert-M1ProviderEnvironment {
    param([pscustomobject]$Environment)

    try {
        $accepted = $Environment.accepted_provider
        $ready = ($Environment.protocol_version -ceq $script:M1ProtocolVersion -and
            $Environment.document_type -ceq "dao_environment" -and
            $Environment.status -ceq "ready" -and
            $Environment.host.is_windows -eq $true -and $null -ne $accepted)
    } catch { $ready = $false }
    if (-not $ready) {
        Throw-M1Preflight "Blocked" "A ready protocol-1.1 environment is required."
    }
    foreach ($field in $script:M1ProviderPolicy.Keys) {
        $actual = [string]$accepted.$field
        $expected = [string]$script:M1ProviderPolicy[$field]
        $comparison = if ($field -in @("clsid", "server_path")) {
            [StringComparison]::OrdinalIgnoreCase } else {
            [StringComparison]::Ordinal }
        if (-not $actual.Equals($expected, $comparison)) {
            Throw-M1Preflight "Blocked" (
                "Environment differs from EXP-0006 policy: " + $field)
        }
    }
    $matching = @(
        $Environment.provider_candidates | Where-Object {
            $_.registered -eq $true -and
            $_.activation -ceq "succeeded" -and
            $_.dbversion30_test.status -ceq "pass" -and
            $_.prog_id -ceq $accepted.prog_id -and
            $_.clsid -ieq $accepted.clsid -and
            $_.registry_view -ceq $accepted.registry_view -and
            $_.registration_scope -ceq $accepted.registration_scope -and
            $_.provider_version -ceq $accepted.provider_version -and
            $_.server_path -ieq $accepted.server_path -and
            $_.server_file_version -ceq $accepted.server_file_version -and
            $_.server_sha256 -ceq $accepted.server_sha256
        }
    )
    if ($matching.Count -ne 1) {
        Throw-M1Preflight "Blocked" "No exact successful provider candidate."
    }
    if (
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop" -or
        $Environment.host.process_architecture -cne "x86" -or
        $accepted.registry_view -cne "x86"
    ) {
        Throw-M1Preflight "Blocked" "M1 requires reviewed x86 Windows PowerShell."
    }
    $currentHost = Get-M1CurrentHostBinding
    Assert-M1ExactRecordFields -Recorded $Environment.host `
        -Current $currentHost -Label "Host environment" -Fields @(
            "computer_name", "os_architecture", "os_build", "os_caption",
            "os_version", "process_architecture"
        )
    if (
        [string]$Environment.runtime.powershell_edition -cne
            [string]$PSVersionTable.PSEdition -or
        [string]$Environment.runtime.powershell_version -cne
            $PSVersionTable.PSVersion.ToString() -or
        [string]$Environment.runtime.dotnet_version -cne
            [Environment]::Version.ToString()
    ) {
        Throw-M1Preflight "Blocked" "PowerShell runtime differs from the probe."
    }
    $currentRegional = Get-M1CurrentRegionalBinding
    Assert-M1ExactRecordFields -Recorded $Environment.regional `
        -Current $currentRegional -Label "Regional environment" -Fields @(
            "ansi_code_page", "culture", "oem_code_page", "timezone_id",
            "ui_culture", "utc_offset"
        )
    return $accepted
}

function Assert-M1CurrentRegistration {
    param([pscustomobject]$AcceptedProvider)

    $userOverride = Get-M1RegistryRegistration -Scope "user" `
        -ProgId $AcceptedProvider.prog_id
    if ($null -ne $userOverride) {
        Throw-M1Preflight "Blocked" (
            "A user registration shadows the probed machine provider.") }
    $registration = Get-M1RegistryRegistration -Scope "machine" `
        -ProgId $AcceptedProvider.prog_id
    if (
        $null -eq $registration -or
        -not ([string]$registration.Clsid).Equals(
            [string]$AcceptedProvider.clsid,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [string]::IsNullOrWhiteSpace([string]$registration.ServerPath)
    ) {
        Throw-M1Preflight "Blocked" "The x86 provider registration has drifted."
    }
    $registeredPath = Assert-M1LocalPathSyntax `
        -Path $registration.ServerPath -Label "Provider server path"
    $acceptedPath = [IO.Path]::GetFullPath(
        [string]$AcceptedProvider.server_path
    )
    if (-not $registeredPath.Equals(
        $acceptedPath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Throw-M1Preflight "Blocked" "Registered COM server path has drifted."
    }
    [void](Assert-M1BoundedFile -Path $registeredPath `
        -MaximumBytes 67108864L -Label "Provider server binary" `
        -MissingCategory "Blocked")
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo(
        $registeredPath
    ).FileVersion
    if ([string]$version -cne [string]$AcceptedProvider.server_file_version) {
        Throw-M1Preflight "Blocked" "Provider file version has drifted."
    }
    return $registeredPath
}
