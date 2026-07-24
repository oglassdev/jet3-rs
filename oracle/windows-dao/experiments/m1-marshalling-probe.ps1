[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$GitCommit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbBinary = 9
$DbLongBinary = 11
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$Marker = [byte[]](0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77)
$LadderLengths = @(1, 2047, 2048, 2049, 32767, 32768, 32769)

function Release-ComObject {
    param([object]$Value)

    if (
        $null -ne $Value -and
        [Runtime.InteropServices.Marshal]::IsComObject($Value)
    ) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-LowerSha256 {
    param([string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ByteSha256 {
    param([byte[]]$Value)

    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return (
            [BitConverter]::ToString($hasher.ComputeHash($Value))
        ).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-LowerHex {
    param([byte[]]$Value)

    return (($Value | ForEach-Object { $_.ToString("x2") }) -join "")
}

function New-RepeatedBytes {
    param(
        [int]$Length,
        [byte]$Value
    )

    $bytes = New-Object byte[] $Length
    for ($index = 0; $index -lt $Length; $index++) {
        $bytes[$index] = $Value
    }
    return ,$bytes
}

function Get-ComFailure {
    param([Management.Automation.ErrorRecord]$ErrorRecord)

    $exception = $ErrorRecord.Exception
    return [ordered]@{
        type = $exception.GetType().FullName
        hresult = ("0x{0:X8}" -f ($exception.HResult -band 0xffffffffL))
        message = $exception.Message
    }
}

$repository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$environmentFile = (Resolve-Path -LiteralPath $EnvironmentPath).Path
$outputDirectory = [IO.Path]::GetDirectoryName(
    [IO.Path]::GetFullPath($OutputPath)
)
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    [IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "OutputPath already exists: $OutputPath"
}

$actualCommit = (
    & git -C $repository rev-parse HEAD
).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $GitCommit) {
    throw "GitCommit does not match repository HEAD."
}
if (-not [string]::IsNullOrWhiteSpace(
    ((& git -C $repository status --porcelain) -join "")
)) {
    throw "The marshalling experiment requires a clean worktree."
}

$environment = Get-Content -LiteralPath $environmentFile -Raw |
    ConvertFrom-Json
if (
    $environment.protocol_version -ne "1.1.0" -or
    $environment.document_type -ne "dao_environment" -or
    $environment.status -ne "ready"
) {
    throw "A ready protocol-1.1 DAO environment is required."
}
if (-not $environment.host.is_windows) {
    throw "The environment record is not a Windows host."
}
$processArchitecture = if ([IntPtr]::Size -eq 4) { "x86" } else { "x64" }
if ($environment.host.process_architecture -ne $processArchitecture) {
    throw "The environment record process architecture does not match."
}
$provider = $environment.accepted_provider
if (
    -not (Test-Path -LiteralPath $provider.server_path -PathType Leaf) -or
    (Get-LowerSha256 -Path $provider.server_path) -ne $provider.server_sha256
) {
    throw "The accepted provider binary is absent or has drifted."
}

$databasePath = Join-Path $env:TEMP (
    "jet3-rs-m1-marshalling-" + [Guid]::NewGuid().ToString("N") + ".mdb"
)
$engine = $null
$database = $null
$recordset = $null
$results = New-Object Collections.ArrayList

try {
    $engine = New-Object -ComObject $provider.prog_id
    if ([string]$engine.Version -ne [string]$provider.provider_version) {
        throw "The activated provider version differs from the environment."
    }
    $database = $engine.Workspaces.Item(0).CreateDatabase(
        $databasePath,
        $DatabaseLocale,
        $DbVersion30
    )

    $table = $database.CreateTableDef("BinaryMarker")
    $field = $table.CreateField("payload", $DbBinary)
    $table.Fields.Append($field)
    $database.TableDefs.Append($table)
    Release-ComObject -Value $field
    Release-ComObject -Value $table

    foreach ($mode in @("value", "append_chunk", "value_unary_comma")) {
        $result = [ordered]@{
            case = "dbBinary_$mode"
            dao_type = "dbBinary"
            input_clr_type = $Marker.GetType().AssemblyQualifiedName
            input_hex = Get-LowerHex -Value $Marker
            status = "not_run"
            readback_clr_type = $null
            readback_hex = $null
            failure = $null
        }
        try {
            $recordset = $database.OpenRecordset("BinaryMarker")
            $recordset.AddNew()
            $target = $recordset.Fields.Item("payload")
            if ($mode -eq "value") {
                $target.Value = $Marker
            }
            elseif ($mode -eq "append_chunk") {
                $target.AppendChunk($Marker)
            }
            else {
                $target.Value = (,$Marker)
            }
            $recordset.Update()
            Release-ComObject -Value $target
            $recordset.MoveLast()
            $target = $recordset.Fields.Item("payload")
            $readback = [byte[]]$target.Value
            $result.status = "pass"
            $result.readback_clr_type = $readback.GetType().AssemblyQualifiedName
            $result.readback_hex = Get-LowerHex -Value $readback
            Release-ComObject -Value $target
        }
        catch {
            $result.status = "fail"
            $result.failure = Get-ComFailure -ErrorRecord $_
        }
        finally {
            if ($null -ne $recordset) {
                $recordset.Close()
                Release-ComObject -Value $recordset
                $recordset = $null
            }
        }
        [void]$results.Add($result)
    }

    foreach ($length in $LadderLengths) {
        $bytes = New-RepeatedBytes -Length $length -Value 0xa5
        $tableName = "LongBinary$length"
        $result = [ordered]@{
            case = "dbLongBinary_$length"
            dao_type = "dbLongBinary"
            input_clr_type = $bytes.GetType().AssemblyQualifiedName
            input_length = $length
            input_sha256 = Get-ByteSha256 -Value $bytes
            status = "not_run"
            readback_clr_type = $null
            readback_length = $null
            readback_sha256 = $null
            failure = $null
        }
        try {
            $table = $database.CreateTableDef($tableName)
            $field = $table.CreateField("payload", $DbLongBinary)
            $table.Fields.Append($field)
            $database.TableDefs.Append($table)
            Release-ComObject -Value $field
            Release-ComObject -Value $table
            $recordset = $database.OpenRecordset($tableName)
            $recordset.AddNew()
            $target = $recordset.Fields.Item("payload")
            $target.AppendChunk($bytes)
            $recordset.Update()
            Release-ComObject -Value $target
            $recordset.MoveFirst()
            $target = $recordset.Fields.Item("payload")
            $readback = [byte[]]$target.Value
            $result.status = "pass"
            $result.readback_clr_type = $readback.GetType().AssemblyQualifiedName
            $result.readback_length = $readback.Length
            $result.readback_sha256 = Get-ByteSha256 -Value $readback
            Release-ComObject -Value $target
        }
        catch {
            $result.status = "fail"
            $result.failure = Get-ComFailure -ErrorRecord $_
        }
        finally {
            if ($null -ne $recordset) {
                $recordset.Close()
                Release-ComObject -Value $recordset
                $recordset = $null
            }
        }
        [void]$results.Add($result)
    }

    $database.Close()
    Release-ComObject -Value $database
    $database = $null

    $document = [ordered]@{
        document_type = "m1_marshalling_experiment"
        experiment_version = "1.0.0"
        captured_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        git_commit = $GitCommit
        environment_sha256 = Get-LowerSha256 -Path $environmentFile
        host = $environment.host
        runtime = [ordered]@{
            powershell_edition = $PSVersionTable.PSEdition
            powershell_version = $PSVersionTable.PSVersion.ToString()
            clr_version = [Environment]::Version.ToString()
            process_architecture = $processArchitecture
        }
        provider = $provider
        database_sha256 = Get-LowerSha256 -Path $databasePath
        cases = @($results)
        claim_boundary = (
            "Controlled DAO API marshalling observation only; not protocol " +
            "evidence and not a Jet format or compatibility result."
        )
    }
    $json = $document | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($OutputPath),
        $json + "`n",
        (New-Object Text.UTF8Encoding($false))
    )
}
finally {
    if ($null -ne $recordset) {
        try { $recordset.Close() } catch {}
        Release-ComObject -Value $recordset
    }
    if ($null -ne $database) {
        try { $database.Close() } catch {}
        Release-ComObject -Value $database
    }
    Release-ComObject -Value $engine
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (Test-Path -LiteralPath $databasePath) {
        Remove-Item -LiteralPath $databasePath -Force
    }
}

Write-Output "PASS: retained controlled M1 marshalling result at $OutputPath"
