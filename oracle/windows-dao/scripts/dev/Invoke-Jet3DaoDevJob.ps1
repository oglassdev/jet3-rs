[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("provider-probe", "create-empty", "opening-matrix")]
    [string]$Job,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$ProviderProbePath,
    [Parameter(Mandatory = $true)]
    [string]$SharedOutputPath,
    [string]$GuestOutputRoot = (Join-Path $env:LOCALAPPDATA "jet3-rs-dev")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbVersion40 = 64
$DbEncrypt = 2
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
# SRC-0022 bounds this development-only DAO NewPassword control to 20 characters.
$OpeningPassword = "J3dev!Only2026"

function Write-JsonDocument {
    param([string]$Path, [object]$Document)

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 20) + "`n"),
        $encoding
    )
}

function Release-ComObject {
    param([object]$Value)

    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function New-OpeningCase {
    param(
        [string]$Root,
        [string]$Name,
        [int]$VersionOption,
        [string]$ExpectedVersion,
        [bool]$Encrypted,
        [bool]$Passworded
    )

    $path = Join-Path $Root ($Name + ".mdb")
    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $option = $VersionOption
        if ($Encrypted) {
            $option += $DbEncrypt
        }
        $database = $workspace.CreateDatabase($path, $DatabaseLocale, $option)
        if ($Passworded) {
            $database.NewPassword("", $OpeningPassword)
        }
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        if ($Passworded) {
            $database = $engine.OpenDatabase(
                $path,
                $false,
                $false,
                ";PWD=$OpeningPassword"
            )
        }
        else {
            $database = $engine.OpenDatabase($path)
        }
        $observedVersion = [string]$database.Version
        if ($observedVersion -cne $ExpectedVersion) {
            throw "DAO reported unexpected database version $observedVersion."
        }
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        return [ordered]@{
            name = $Name
            database = $Name + ".mdb"
            version = $observedVersion
            encrypted = $Encrypted
            passworded = $Passworded
            size = [long](Get-Item -LiteralPath $path).Length
        }
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Publish-DevelopmentOutput {
    param([string]$Source, [string]$Destination)

    if (Test-Path -LiteralPath $Destination) {
        throw "Shared output path already exists."
    }
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Destination))
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $staging = $Destination + ".building." + [Guid]::NewGuid().ToString("N")
    try {
        [IO.Directory]::CreateDirectory($staging) | Out-Null
        foreach ($name in @(
            "environment.json", "result.json", "empty.mdb",
            "v30-u-n.mdb", "v30-e-n.mdb", "v30-u-p.mdb", "v30-e-p.mdb",
            "v40-u-n.mdb", "v40-e-n.mdb", "v40-u-p.mdb", "v40-e-p.mdb"
        )) {
            $sourcePath = Join-Path $Source $name
            if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
                Copy-Item -LiteralPath $sourcePath -Destination $staging
            }
        }
        [IO.Directory]::Move($staging, $Destination)
    }
    catch {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        throw
    }
}

if (-not (Test-Path -LiteralPath $ProviderProbePath -PathType Leaf)) {
    [Console]::Error.WriteLine("INVALID: provider probe does not exist.")
    exit 2
}

$runRoot = Join-Path ([IO.Path]::GetFullPath($GuestOutputRoot)) ("runs\" + $RunId)
if (Test-Path -LiteralPath $runRoot) {
    [Console]::Error.WriteLine("INVALID: guest run directory already exists.")
    exit 2
}
[IO.Directory]::CreateDirectory($runRoot) | Out-Null
$environmentPath = Join-Path $runRoot "environment.json"
& (Join-Path $PSHOME "powershell.exe") -NoProfile -NonInteractive `
    -ExecutionPolicy Bypass -File $ProviderProbePath -OutputPath $environmentPath `
    -ProtocolVersion "1.1.0"
$probeExitCode = [int]$LASTEXITCODE

$status = "blocked"
$detail = "The DAO provider probe did not report a ready environment."
$exitCode = 3
$databaseName = $null
$databaseVersion = $null
$openingCases = @()

if ($Job -ceq "provider-probe") {
    if ($probeExitCode -eq 0) {
        $status = "pass"
        $detail = "The x86 DAO provider probe reported ready."
        $exitCode = 0
    }
    elseif ($probeExitCode -eq 1) {
        $status = "fail"
        $detail = "The x86 DAO provider probe failed."
        $exitCode = 1
    }
}
elseif ($Job -ceq "create-empty" -and $probeExitCode -eq 0) {
    $environment = Get-Content -LiteralPath $environmentPath -Raw | ConvertFrom-Json
    if ([string]$environment.accepted_provider.prog_id -cne "DAO.DBEngine.36") {
        $detail = "The ready provider is not DAO.DBEngine.36."
    }
    else {
        $engine = $null
        $workspace = $null
        $database = $null
        try {
            $databaseName = "empty.mdb"
            $databasePath = Join-Path $runRoot $databaseName
            $engine = New-Object -ComObject "DAO.DBEngine.36"
            $workspace = $engine.Workspaces.Item(0)
            $database = $workspace.CreateDatabase(
                $databasePath,
                $DatabaseLocale,
                $DbVersion30
            )
            $database.Close()
            Release-ComObject -Value $database
            $database = $null
            $database = $engine.OpenDatabase($databasePath)
            $databaseVersion = [string]$database.Version
            $database.Close()
            Release-ComObject -Value $database
            $database = $null
            $status = "pass"
            $detail = "Created, closed, reopened, and closed an empty Jet 3 database."
            $exitCode = 0
        }
        catch {
            $status = "fail"
            $detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
            $exitCode = 1
        }
        finally {
            if ($null -ne $database) {
                try { $database.Close() } catch { }
            }
            Release-ComObject -Value $database
            Release-ComObject -Value $workspace
            Release-ComObject -Value $engine
            [GC]::Collect()
            [GC]::WaitForPendingFinalizers()
        }
    }
}
elseif ($Job -ceq "opening-matrix" -and $probeExitCode -eq 0) {
    $environment = Get-Content -LiteralPath $environmentPath -Raw | ConvertFrom-Json
    if ([string]$environment.accepted_provider.prog_id -cne "DAO.DBEngine.36") {
        $detail = "The ready provider is not DAO.DBEngine.36."
    }
    else {
        try {
            $matrix = @(
                @{ name = "v30-u-n"; option = $DbVersion30; version = "3.0"; encrypted = $false; passworded = $false },
                @{ name = "v30-e-n"; option = $DbVersion30; version = "3.0"; encrypted = $true; passworded = $false },
                @{ name = "v30-u-p"; option = $DbVersion30; version = "3.0"; encrypted = $false; passworded = $true },
                @{ name = "v30-e-p"; option = $DbVersion30; version = "3.0"; encrypted = $true; passworded = $true },
                @{ name = "v40-u-n"; option = $DbVersion40; version = "4.0"; encrypted = $false; passworded = $false },
                @{ name = "v40-e-n"; option = $DbVersion40; version = "4.0"; encrypted = $true; passworded = $false },
                @{ name = "v40-u-p"; option = $DbVersion40; version = "4.0"; encrypted = $false; passworded = $true },
                @{ name = "v40-e-p"; option = $DbVersion40; version = "4.0"; encrypted = $true; passworded = $true }
            )
            foreach ($case in $matrix) {
                $openingCases += New-OpeningCase -Root $runRoot `
                    -Name $case.name -VersionOption $case.option `
                    -ExpectedVersion $case.version -Encrypted $case.encrypted `
                    -Passworded $case.passworded
            }
            $status = "pass"
            $detail = "Created, closed, and reopened all eight opening controls."
            $exitCode = 0
        }
        catch {
            $status = "fail"
            $detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
            $exitCode = 1
        }
    }
}

$result = [ordered]@{
    development_only = $true
    run_id = $RunId
    job = $Job
    status = $status
    detail = $detail
    probe_exit_code = $probeExitCode
    database = $databaseName
    database_version = $databaseVersion
    opening_cases = @($openingCases)
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
}
Write-JsonDocument -Path (Join-Path $runRoot "result.json") -Document $result

try {
    Publish-DevelopmentOutput -Source $runRoot -Destination $SharedOutputPath
}
catch {
    [Console]::Error.WriteLine("ERROR: development publication failed: " + $_.Exception.Message)
    exit 4
}

[Console]::WriteLine($status.ToUpperInvariant() + ": " + $detail)
exit $exitCode
