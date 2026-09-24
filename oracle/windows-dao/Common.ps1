# Shared helpers for Native.ps1 and Observe.ps1. Dot-source only; nothing runs here.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 Windows PowerShell' }

# DataTypeEnum values (SRC-0002).
$script:FieldTypes = @{
    boolean = 1; byte = 2; integer = 3; long = 4; auto_increment = 4; currency = 5; single = 6
    double = 7; date_time = 8; binary = 9; text = 10; fixed_text = 10; long_binary = 11; memo = 12; guid = 15
}
$script:Locales = @{
    general = ';LANGID=0x0409;CP=1252;COUNTRY=0'
}

function Release($value) {
    if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
    }
}

function Identity([string]$Path) {
    @{
        size = (Get-Item -LiteralPath $Path).Length
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Write-Json($Value, [string]$Path) {
    $text = (ConvertTo-Json -InputObject $Value -Depth 100 -Compress) + "`n"
    [IO.File]::WriteAllText($Path, $text, (New-Object Text.UTF8Encoding($false)))
}

function Read-Json([string]$Path) {
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Has($Object, [string]$Name) {
    $null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]
}

# Late-bound setter; avoids PowerShell call-site caching across value types.
function Set-Property($Owner, [string]$Name, $Value) {
    $arguments = [object[]]::new(1)
    $arguments[0] = $Value
    [void]$Owner.GetType().InvokeMember($Name, [Reflection.BindingFlags]::SetProperty, $null, $Owner, $arguments)
}

function Hex([byte[]]$Bytes) {
    [BitConverter]::ToString($Bytes).Replace('-', '').ToLowerInvariant()
}

function Hash-Bytes([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { Hex $hash.ComputeHash($Bytes) } finally { $hash.Dispose() }
}

function Error-Info($Engine, $Record) {
    $numbers = @()
    $errors = $item = $null
    try {
        $errors = $Engine.Errors
        for ($i = 0; $i -lt $errors.Count; $i++) {
            $item = $errors.Item($i)
            $numbers += [int]$item.Number
            Release $item
            $item = $null
        }
    } finally { Release $item; Release $errors }
    $exception = $Record.Exception
    while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
    @{ numbers = $numbers; message = $exception.Message; hresult = [int]$exception.HResult }
}

# Observed values: large or binary values are reduced to length and SHA-256.
function Norm($Value) {
    if ($null -eq $Value -or [Convert]::IsDBNull($Value)) { return $null }
    if ($Value -is [byte[]]) {
        $result = @{ kind = 'bytes'; length = $Value.Length; sha256 = (Hash-Bytes $Value) }
        if ($Value.Length -le 255) { $result.hex = Hex $Value }
        return $result
    }
    if ($Value -is [datetime]) { return @{ kind = 'date'; oa = ([datetime]$Value).ToOADate() } }
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [byte] -or $Value -is [int16] -or $Value -is [int32] -or $Value -is [int64]) { return [long]$Value }
    if ($Value -is [double] -or $Value -is [single]) {
        return @{ kind = 'float'; text = $Value.ToString('R', [Globalization.CultureInfo]::InvariantCulture) }
    }
    if ($Value -is [decimal]) {
        return @{ kind = 'decimal'; text = $Value.ToString([Globalization.CultureInfo]::InvariantCulture) }
    }
    if ($Value -is [string] -and $Value.Length -gt 1024) {
        $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Value)
        return @{ kind = 'text'; length = $Value.Length; sha256 = (Hash-Bytes $bytes) }
    }
    [string]$Value
}

function Environment-Record($Engine) {
    $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })[0]
    @{
        provider = 'DAO.DBEngine.36'
        version = [string]$Engine.Version
        bits = [IntPtr]::Size * 8
        os = [Environment]::OSVersion.VersionString
        culture = [Globalization.CultureInfo]::CurrentCulture.Name
        ansi = [Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage
        dll_path = $dll.FileName
        dll_version = $dll.FileVersionInfo.FileVersion
        dll_sha256 = (Identity $dll.FileName).sha256
    }
}

function Locale-String([string]$Name) {
    if ([string]::IsNullOrEmpty($Name)) { return $script:Locales.general }
    if ($script:Locales.ContainsKey($Name)) { return $script:Locales[$Name] }
    $Name
}

# Byte-list text uses the host ANSI page, as DAO 3.6 converts BSTR names and values.
function Text-Value($Raw) {
    if ($Raw -is [string]) { return $Raw }
    $bytes = [byte[]]@($Raw | ForEach-Object { [byte]$_ })
    [Text.Encoding]::Default.GetString($bytes)
}

# jet3-cli typed cells ({long: 1}, {text: "a"}, ...) as COM values. The unary comma keeps
# byte arrays from unrolling into object[], which DAO rejects (error 3421).
function Cell-Value($Cell) {
    if ($null -eq $Cell) { return [DBNull]::Value }
    $property = @($Cell.PSObject.Properties)[0]
    $raw = $property.Value
    switch ($property.Name) {
        'boolean' { return [bool]$raw }
        'byte' { return [byte]$raw }
        'integer' { return [int16]$raw }
        'long' { return [int]$raw }
        'currency' { return [Runtime.InteropServices.CurrencyWrapper]::new([decimal]$raw / [decimal]10000) }
        'single' { return [single]$raw }
        'double' { return [double]$raw }
        'date_time' { return [datetime]::FromOADate([double]$raw) }
        'text' { return (Text-Value $raw) }
        'memo' { return (Text-Value $raw) }
        'binary' { return , [byte[]]@($raw | ForEach-Object { [byte]$_ }) }
        'long_binary' { return , [byte[]]@($raw | ForEach-Object { [byte]$_ }) }
        'guid' { return ([guid]::new([byte[]]@($raw | ForEach-Object { [byte]$_ }))).ToString('B') }
        default { throw ('Unsupported cell ' + $property.Name) }
    }
}

function Is-AutoCell($Cell) {
    ($Cell -is [string] -and $Cell -eq 'auto_increment') -or ((Has $Cell 'auto_increment'))
}
