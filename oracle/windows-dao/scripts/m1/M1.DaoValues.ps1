Set-StrictMode -Version Latest

# Provenance usage: EXP-0006.
$script:M1Utf8 = New-Object Text.UTF8Encoding($false)

function Release-M1ComObject {
    param(
        [object]$Value,
        [Collections.ArrayList]$CleanupErrors = $null,
        [string]$Label = "COM release"
    )

    try {
        if (
            $null -ne $Value -and
            [Runtime.InteropServices.Marshal]::IsComObject($Value)
        ) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
        }
    }
    catch {
        if ($null -eq $CleanupErrors) { throw }
        $cleanupDetail = Get-M1SafeText `
            -Value ($Label + ": " + $_.Exception.Message) -Maximum 1000
        [void]$CleanupErrors.Add($cleanupDetail)
    }
}

function Close-M1ComObject {
    param(
        [object]$Value,
        [Collections.ArrayList]$CleanupErrors,
        [string]$Label
    )

    if ($null -eq $Value) { return }
    try {
        $Value.Close()
    }
    catch {
        $cleanupDetail = Get-M1SafeText `
            -Value ($Label + ": " + $_.Exception.Message) -Maximum 1000
        [void]$CleanupErrors.Add($cleanupDetail)
    }
}

function Complete-M1DaoHelper {
    param(
        [Management.Automation.ErrorRecord]$PrimaryError,
        [Collections.ArrayList]$CleanupErrors,
        [string]$Label
    )

    $bounded = @($CleanupErrors | Select-Object -First 8)
    if ($null -ne $PrimaryError) {
        $existing = @()
        if ($PrimaryError.Exception.Data.Contains("M1CleanupErrors")) {
            $existing = @($PrimaryError.Exception.Data["M1CleanupErrors"])
        }
        $PrimaryError.Exception.Data["M1CleanupErrors"] = @(
            @($existing) + $bounded | Select-Object -First 8
        )
        throw $PrimaryError
    }
    if ($bounded.Count -gt 0) {
        $exception = New-Object InvalidOperationException(
            "$Label cleanup failed."
        )
        $exception.Data["M1CleanupErrors"] = $bounded
        throw $exception
    }
}

function Get-M1SafeText {
    param(
        [string]$Value,
        [int]$Maximum = 4000
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "No detail was provided."
    }
    if ($Value.Length -le $Maximum) {
        return $Value
    }
    return $Value.Substring(0, $Maximum)
}

function Get-M1ExceptionRecord {
    param(
        [Management.Automation.ErrorRecord]$ErrorRecord,
        [string[]]$CleanupErrors = @()
    )

    $exception = $ErrorRecord.Exception
    $inheritedCleanup = @()
    if ($exception.Data.Contains("M1CleanupErrors")) {
        $inheritedCleanup = @($exception.Data["M1CleanupErrors"])
    }
    $boundedCleanup = @(
        @($inheritedCleanup) + @($CleanupErrors) |
            Select-Object -First 8 |
            ForEach-Object { Get-M1SafeText -Value $_ -Maximum 1000 }
    )
    return [ordered]@{
        cleanup_errors = @($boundedCleanup)
        exception_type = $exception.GetType().FullName
        hresult = ("0x{0:X8}" -f ($exception.HResult -band 0xffffffffL))
        message = Get-M1SafeText -Value $exception.Message -Maximum 2000
    }
}

function Get-M1ByteSha256 {
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

function Get-M1FileSha256 {
    param([string]$Path)

    $stream = New-Object IO.FileStream(
        [IO.Path]::GetFullPath($Path),
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hasher.ComputeHash($stream)
        ).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function Get-M1LowerHex {
    param([byte[]]$Value)

    return (
        [BitConverter]::ToString($Value)
    ).Replace("-", "").ToLowerInvariant()
}

function ConvertFrom-M1LowerHex {
    param([string]$Value)

    if ($Value -cnotmatch "^(?:[0-9a-f]{2})+$") {
        throw "The controlled binary value is not lowercase even-length hex."
    }
    $bytes = New-Object byte[] ($Value.Length / 2)
    for ($index = 0; $index -lt $bytes.Length; $index++) {
        $bytes[$index] = [Convert]::ToByte(
            $Value.Substring($index * 2, 2),
            16
        )
    }
    return ,$bytes
}

function New-M1RepeatedBytes {
    param(
        [int]$Length,
        [byte]$Value
    )

    if ($Length -lt 1 -or $Length -gt 32769) {
        throw "The controlled byte length is outside the M1 bound."
    }
    $bytes = New-Object byte[] $Length
    for ($index = 0; $index -lt $Length; $index++) {
        $bytes[$index] = $Value
    }
    return ,$bytes
}

function Get-M1DeclaredValue {
    param([object]$ValuePlan)

    switch ([string]$ValuePlan.dao_type) {
        "dbBinary" {
            $value = ConvertFrom-M1LowerHex -Value ([string]$ValuePlan.value)
            if ($value.GetType() -ne [byte[]]) {
                throw "dbBinary construction did not produce System.Byte[]."
            }
            return ,$value
        }
        "dbText" {
            return [string]$ValuePlan.value
        }
        "dbMemo" {
            return ([string]$ValuePlan.ascii_character) * [int]$ValuePlan.length
        }
        "dbLongBinary" {
            $value = New-M1RepeatedBytes -Length ([int]$ValuePlan.length) `
                -Value ([byte]$ValuePlan.byte)
            if ($value.GetType() -ne [byte[]]) {
                throw "dbLongBinary construction did not produce System.Byte[]."
            }
            return ,$value
        }
        default {
            throw "Unsupported controlled DAO value type."
        }
    }
}

function Get-M1ValueIdentity {
    param(
        [string]$DaoType,
        [object]$Value
    )

    if ($DaoType -in @("dbBinary", "dbLongBinary")) {
        if ($null -eq $Value -or $Value.GetType() -ne [byte[]]) {
            throw "$DaoType readback runtime type is not exactly System.Byte[]."
        }
        $bytes = [byte[]]$Value
        return [ordered]@{
            hex = if ($DaoType -eq "dbBinary") {
                Get-M1LowerHex -Value $bytes
            }
            else {
                $null
            }
            length = $bytes.Length
            runtime_type = $bytes.GetType().FullName
            sha256 = Get-M1ByteSha256 -Value $bytes
        }
    }
    if ($null -eq $Value -or $Value.GetType() -ne [string]) {
        throw "$DaoType readback runtime type is not exactly System.String."
    }
    $text = [string]$Value
    $bytes = $script:M1Utf8.GetBytes($text)
    return [ordered]@{
        hex = $null
        length = $text.Length
        runtime_type = $text.GetType().FullName
        sha256 = Get-M1ByteSha256 -Value $bytes
    }
}

function New-M1ValueObservation {
    param(
        [object]$ValuePlan,
        [object]$Readback,
        [int]$RowOrdinal
    )

    $daoType = [string]$ValuePlan.dao_type
    $input = Get-M1DeclaredValue -ValuePlan $ValuePlan
    $inputIdentity = Get-M1ValueIdentity -DaoType $daoType -Value $input
    $readbackIdentity = Get-M1ValueIdentity -DaoType $daoType -Value $Readback
    if (
        $inputIdentity.length -ne $readbackIdentity.length -or
        $inputIdentity.sha256 -cne $readbackIdentity.sha256 -or
        $inputIdentity.hex -cne $readbackIdentity.hex
    ) {
        throw "DAO readback differs from the controlled input value."
    }
    return [ordered]@{
        dao_type = $daoType
        field = [string]$ValuePlan.field
        input_hex = $inputIdentity.hex
        input_length = $inputIdentity.length
        input_runtime_type = $inputIdentity.runtime_type
        input_sha256 = $inputIdentity.sha256
        readback_hex = $readbackIdentity.hex
        readback_length = $readbackIdentity.length
        readback_runtime_type = $readbackIdentity.runtime_type
        readback_sha256 = $readbackIdentity.sha256
        row_ordinal = $RowOrdinal
    }
}

function Add-M1OperationEntry {
    param(
        [Collections.ArrayList]$Entries,
        [string]$Action,
        [string]$Status,
        [string]$Detail,
        [object[]]$ValueObservations = @(),
        [object]$ErrorRecord = $null
    )

    $entry = [ordered]@{
        action = $Action
        detail = Get-M1SafeText -Value $Detail
        error = $ErrorRecord
        sequence = $Entries.Count + 1
        status = $Status
        timestamp_utc = [DateTimeOffset]::UtcNow.ToString("o")
        value_observations = @($ValueObservations)
    }
    [void]$Entries.Add($entry)
    return $entry
}
