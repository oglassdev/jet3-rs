[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# SRC-0009, SRC-0010, SRC-0012, SRC-0023, and EXP-0006 permit only the DAO
# schema, record, long-value, and marshalling operations used by this bounded
# development job. Physical interpretation requires a separate observation.
$DbVersion30 = 32
$DbBoolean = 1
$DbByte = 2
$DbInteger = 3
$DbLong = 4
$DbCurrency = 5
$DbSingle = 6
$DbDouble = 7
$DbDate = 8
$DbBinary = 9
$DbText = 10
$DbLongBinary = 11
$DbMemo = 12
$DbGuid = 15
$Locale1252 = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$Locale1251 = ";LANGID=0x0419;CP=1251;COUNTRY=0"
$MaximumDatabaseBytes = 4MB
$LongLengths = @(32, 512, 2048, 4096)

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

function Add-Field {
    param(
        [object]$Table,
        [string]$Name,
        [int]$Type,
        [int]$Size = 0,
        [bool]$AllowZeroLength = $false
    )

    $field = $null
    try {
        if ($Size -gt 0) { $field = $Table.CreateField($Name, $Type, $Size) }
        else { $field = $Table.CreateField($Name, $Type) }
        if ($Type -eq $DbText) { $field.AllowZeroLength = $AllowZeroLength }
        $Table.Fields.Append($field)
    }
    finally { Release-ComObject -Value $field }
}

function New-Database {
    param([string]$Path, [string]$Locale)

    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $database = $workspace.CreateDatabase($Path, $Locale, $DbVersion30)
        $database.Close()
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Open-Database {
    param([string]$Path)

    $engine = New-Object -ComObject "DAO.DBEngine.36"
    $database = $engine.OpenDatabase($Path)
    return [pscustomobject]@{engine=$engine;database=$database}
}

function Convert-ToHex {
    param([byte[]]$Bytes)

    return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
}

function Get-ScalarObservation {
    param([object]$Field)

    $raw = $Field.Value
    if ($null -eq $raw -or [Convert]::IsDBNull($raw)) {
        return [ordered]@{kind="null"}
    }
    switch ([int]$Field.Type) {
        1 { return [ordered]@{kind="boolean";value=[bool]$raw} }
        2 { return [ordered]@{kind="byte";value=[int][byte]$raw} }
        3 { return [ordered]@{kind="integer";value=[int][int16]$raw} }
        4 { return [ordered]@{kind="long";value=[long][int32]$raw} }
        5 { return [ordered]@{kind="currency";value=([decimal]$raw).ToString([Globalization.CultureInfo]::InvariantCulture)} }
        6 { return [ordered]@{kind="single";bits=Convert-ToHex ([BitConverter]::GetBytes([single]$raw))} }
        7 { return [ordered]@{kind="double";bits=Convert-ToHex ([BitConverter]::GetBytes([double]$raw))} }
        8 {
            $date = [datetime]$raw
            return [ordered]@{kind="date";iso=$date.ToString("o", [Globalization.CultureInfo]::InvariantCulture);bits=Convert-ToHex ([BitConverter]::GetBytes([double]$date.ToOADate()))}
        }
        9 { return [ordered]@{kind="binary";hex=Convert-ToHex ([byte[]]$raw)} }
        10 { return [ordered]@{kind="text";value=[string]$raw} }
        15 { return [ordered]@{kind="guid";value=[string]$raw} }
        default { throw "Unsupported scalar readback type." }
    }
}

function New-TextFromCodePoints {
    param([int[]]$CodePoints)

    $builder = New-Object Text.StringBuilder
    foreach ($codePoint in $CodePoints) {
        [void]$builder.Append([char]$codePoint)
    }
    return $builder.ToString()
}

function New-LongBytes {
    param([int]$Length)

    $bytes = New-Object byte[] $Length
    for ($index = 0; $index -lt $Length; $index++) {
        $bytes[$index] = [byte](($index * 29 + 17) % 251)
    }
    return ,$bytes
}

function Assert-BoundedDatabase {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path
    if ($item.Length -gt $MaximumDatabaseBytes -or $item.Length % 2048 -ne 0) {
        throw "Value scenario database violated its byte or page bound."
    }
    return [ordered]@{
        size = [long]$item.Length
        page_count = [long]($item.Length / 2048)
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function New-ScalarTable {
    param([object]$Database)

    $table = $null
    try {
        $table = $Database.CreateTableDef("ScalarValues")
        Add-Field -Table $table -Name "BoolValue" -Type $DbBoolean
        Add-Field -Table $table -Name "ByteValue" -Type $DbByte
        Add-Field -Table $table -Name "IntegerValue" -Type $DbInteger
        Add-Field -Table $table -Name "LongValue" -Type $DbLong
        Add-Field -Table $table -Name "CurrencyValue" -Type $DbCurrency
        Add-Field -Table $table -Name "SingleValue" -Type $DbSingle
        Add-Field -Table $table -Name "DoubleValue" -Type $DbDouble
        Add-Field -Table $table -Name "DateValue" -Type $DbDate
        Add-Field -Table $table -Name "BinaryValue" -Type $DbBinary -Size 8
        Add-Field -Table $table -Name "TextValue" -Type $DbText -Size 32 -AllowZeroLength $true
        Add-Field -Table $table -Name "GuidValue" -Type $DbGuid
        $Database.TableDefs.Append($table)
    }
    finally { Release-ComObject -Value $table }
}

function Set-ScalarField {
    param([object]$Recordset, [string]$Name, [object]$Value)

    $field = $null
    try {
        $field = $Recordset.Fields.Item($Name)
        if ($null -eq $Value) { $field.Value = [DBNull]::Value }
        else {
            switch ([int]$field.Type) {
                1 { $field.Value = [bool]$Value }
                2 { $field.Value = [byte]$Value }
                3 { $field.Value = [int16]$Value }
                4 { $field.Value = [int32]$Value }
                5 { $field.Value = [decimal]$Value }
                6 { $field.Value = [single]$Value }
                7 { $field.Value = [double]$Value }
                8 { $field.Value = [datetime]$Value }
                9 { $field.Value = [byte[]]$Value }
                10 { $field.Value = [string]$Value }
                15 { $field.Value = "{" + ([guid]$Value).ToString() + "}" }
                default { throw "Unsupported scalar DAO field type." }
            }
        }
    }
    catch { throw "Scalar field $Name assignment failed: " + $_.Exception.Message }
    finally { Release-ComObject -Value $field }
}

function Add-ScalarRow {
    param([object]$Recordset, [string]$Boundary)

    $culture = [Globalization.CultureInfo]::InvariantCulture
    $Recordset.AddNew()
    if ($Boundary -ceq "null") {
        foreach ($name in @(
            "BoolValue", "ByteValue", "IntegerValue", "LongValue",
            "CurrencyValue", "SingleValue", "DoubleValue", "DateValue",
            "BinaryValue", "TextValue", "GuidValue"
        )) { Set-ScalarField -Recordset $Recordset -Name $name -Value $null }
    }
    elseif ($Boundary -ceq "minimum") {
        Set-ScalarField $Recordset "BoolValue" ([bool]$false)
        Set-ScalarField $Recordset "ByteValue" ([byte]0)
        Set-ScalarField $Recordset "IntegerValue" ([int16]::MinValue)
        Set-ScalarField $Recordset "LongValue" ([int32]::MinValue)
        Set-ScalarField $Recordset "CurrencyValue" ([decimal]::Parse("-922337203685477.5808", $culture))
        Set-ScalarField $Recordset "SingleValue" ([single]::MinValue)
        Set-ScalarField $Recordset "DoubleValue" ([double]::MinValue)
        Set-ScalarField $Recordset "DateValue" ([datetime]::new(100, 1, 1, 0, 0, 0))
        Set-ScalarField $Recordset "BinaryValue" ([byte[]](0,0,0,0,0,0,0,0))
        Set-ScalarField $Recordset "TextValue" ([string]::Empty)
        Set-ScalarField $Recordset "GuidValue" ([guid]::Empty)
    }
    elseif ($Boundary -ceq "representative") {
        Set-ScalarField $Recordset "BoolValue" ([bool]$true)
        Set-ScalarField $Recordset "ByteValue" ([byte]165)
        Set-ScalarField $Recordset "IntegerValue" ([int16]-12345)
        Set-ScalarField $Recordset "LongValue" ([int32]270544960)
        Set-ScalarField $Recordset "CurrencyValue" ([decimal]::Parse("123456.7890", $culture))
        Set-ScalarField $Recordset "SingleValue" ([single]1.25)
        Set-ScalarField $Recordset "DoubleValue" ([double]-3.5)
        Set-ScalarField $Recordset "DateValue" ([datetime]::new(2001, 2, 3, 4, 5, 6))
        Set-ScalarField $Recordset "BinaryValue" ([byte[]](0xde,0xad,0xbe,0xef,0x10,0x20,0x30,0x40))
        Set-ScalarField $Recordset "TextValue" (New-TextFromCodePoints @(0x43,0x61,0x66,0xe9,0x20,0x20ac))
        Set-ScalarField $Recordset "GuidValue" ([guid]"00112233-4455-6677-8899-aabbccddeeff")
    }
    elseif ($Boundary -ceq "maximum") {
        Set-ScalarField $Recordset "BoolValue" ([bool]$true)
        Set-ScalarField $Recordset "ByteValue" ([byte]::MaxValue)
        Set-ScalarField $Recordset "IntegerValue" ([int16]::MaxValue)
        Set-ScalarField $Recordset "LongValue" ([int32]::MaxValue)
        Set-ScalarField $Recordset "CurrencyValue" ([decimal]::Parse("922337203685477.5807", $culture))
        Set-ScalarField $Recordset "SingleValue" ([single]::MaxValue)
        Set-ScalarField $Recordset "DoubleValue" ([double]::MaxValue)
        Set-ScalarField $Recordset "DateValue" ([datetime]::new(9999, 12, 31, 23, 59, 59))
        Set-ScalarField $Recordset "BinaryValue" ([byte[]](255,255,255,255,255,255,255,255))
        Set-ScalarField $Recordset "TextValue" ([string]("Z" * 32))
        Set-ScalarField $Recordset "GuidValue" ([guid]"ffffffff-ffff-ffff-ffff-ffffffffffff")
    }
    else { throw "Unknown scalar boundary." }
    $Recordset.Update()
}

function Invoke-ScalarScenario {
    param([int]$Replica)

    $fileName = "value-r$Replica-scalars.mdb"
    $path = Join-Path $RunRoot $fileName
    New-Database -Path $path -Locale $Locale1252
    $opened = $null
    $recordset = $null
    $readback = New-Object Collections.ArrayList
    try {
        $opened = Open-Database -Path $path
        New-ScalarTable -Database $opened.database
        $recordset = $opened.database.OpenRecordset("ScalarValues", 2, 0)
        foreach ($boundary in @("null", "minimum", "representative", "maximum")) {
            Add-ScalarRow -Recordset $recordset -Boundary $boundary
        }
        $recordset.MoveFirst()
        foreach ($boundary in @("null", "minimum", "representative", "maximum")) {
            if ($recordset.EOF) { throw "Scalar readback ended early." }
            $values = [ordered]@{}
            foreach ($name in @(
                "BoolValue", "ByteValue", "IntegerValue", "LongValue",
                "CurrencyValue", "SingleValue", "DoubleValue", "DateValue",
                "BinaryValue", "TextValue", "GuidValue"
            )) {
                $field = $null
                try {
                    $field = $recordset.Fields.Item($name)
                    $values[$name] = Get-ScalarObservation -Field $field
                }
                finally { Release-ComObject -Value $field }
            }
            [void]$readback.Add([ordered]@{boundary=$boundary;values=$values})
            $recordset.MoveNext()
        }
        if (-not $recordset.EOF) { throw "Scalar readback returned too many rows." }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
        if ($null -ne $opened) { try { $opened.database.Close() } catch { } }
        if ($null -ne $opened) { Release-ComObject -Value $opened.database; Release-ComObject -Value $opened.engine }
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    $bounded = Assert-BoundedDatabase -Path $path
    return [ordered]@{kind="scalars";replica=$Replica;database=$fileName;size=$bounded.size;page_count=$bounded.page_count;sha256=$bounded.sha256;rows=4;readback=@($readback)}
}

function Invoke-TextScenario {
    param([int]$Replica, [int]$CodePage, [string]$Locale, [string]$Value)

    $name = "value-r$Replica-cp$CodePage.mdb"
    $path = Join-Path $RunRoot $name
    New-Database -Path $path -Locale $Locale
    $opened = $null
    $table = $null
    $recordset = $null
    $readback = New-Object Collections.ArrayList
    try {
        $opened = Open-Database -Path $path
        $table = $opened.database.CreateTableDef("TextValues")
        Add-Field -Table $table -Name "Value" -Type $DbText -Size 255 -AllowZeroLength $true
        $opened.database.TableDefs.Append($table)
        Release-ComObject -Value $table; $table = $null
        $recordset = $opened.database.OpenRecordset("TextValues", 2, 0)
        foreach ($text in @($null, [string]::Empty, $Value)) {
            $recordset.AddNew()
            $field = $null
            try {
                $field = $recordset.Fields.Item("Value")
                if ($null -eq $text) { $field.Value = [DBNull]::Value }
                else { $field.Value = [string]$text }
            }
            finally { Release-ComObject -Value $field }
            $recordset.Update()
        }
        $recordset.MoveFirst()
        while (-not $recordset.EOF) {
            $field = $null
            try {
                $field = $recordset.Fields.Item("Value")
                $raw = $field.Value
                if ($null -eq $raw -or [Convert]::IsDBNull($raw)) {
                    [void]$readback.Add([ordered]@{kind="null"})
                }
                else { [void]$readback.Add([ordered]@{kind="text";value=[string]$raw}) }
            }
            finally { Release-ComObject -Value $field }
            $recordset.MoveNext()
        }
    }
    finally {
        Release-ComObject -Value $table
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
        if ($null -ne $opened) { try { $opened.database.Close() } catch { } }
        if ($null -ne $opened) { Release-ComObject -Value $opened.database; Release-ComObject -Value $opened.engine }
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    $bounded = Assert-BoundedDatabase -Path $path
    return [ordered]@{kind="text";replica=$Replica;code_page=$CodePage;database=$name;size=$bounded.size;page_count=$bounded.page_count;sha256=$bounded.sha256;rows=3;value=$Value;readback=@($readback)}
}

function Invoke-LongScenario {
    param([int]$Replica, [string]$Kind, [int]$Length)

    $name = "value-r$Replica-$Kind-$($Length.ToString('D5')).mdb"
    $path = Join-Path $RunRoot $name
    New-Database -Path $path -Locale $Locale1252
    $opened = $null
    $table = $null
    $recordset = $null
    $field = $null
    $readback = $null
    try {
        $opened = Open-Database -Path $path
        $table = $opened.database.CreateTableDef("LongValues")
        Add-Field -Table $table -Name "Value" -Type $(if ($Kind -ceq "memo") { $DbMemo } else { $DbLongBinary })
        $opened.database.TableDefs.Append($table)
        Release-ComObject -Value $table; $table = $null
        $recordset = $opened.database.OpenRecordset("LongValues", 2, 0)
        $recordset.AddNew()
        $field = $recordset.Fields.Item("Value")
        if ($Kind -ceq "memo") { $field.AppendChunk([string]("M" * $Length)) }
        else { $field.AppendChunk((New-LongBytes -Length $Length)) }
        $recordset.Update()
        $recordset.MoveFirst()
        $raw = $field.Value
        if ($Kind -ceq "memo") {
            $text = [string]$raw
            if ($text.Length -ne $Length -or $text -cne ("M" * $Length)) {
                throw "Memo readback did not match the bounded input."
            }
            $readback = [ordered]@{kind="memo";length=$text.Length;field_size=[long]$field.FieldSize}
        }
        else {
            $bytes = [byte[]]$raw
            $expected = New-LongBytes -Length $Length
            if ((Convert-ToHex $bytes) -cne (Convert-ToHex $expected)) {
                throw "OLE readback did not match the bounded input."
            }
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $hash = Convert-ToHex ($sha.ComputeHash($bytes)) }
            finally { $sha.Dispose() }
            $readback = [ordered]@{kind="ole";length=$bytes.Length;field_size=[long]$field.FieldSize;sha256=$hash}
        }
    }
    finally {
        Release-ComObject -Value $field
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
        Release-ComObject -Value $table
        if ($null -ne $opened) { try { $opened.database.Close() } catch { } }
        if ($null -ne $opened) { Release-ComObject -Value $opened.database; Release-ComObject -Value $opened.engine }
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    $bounded = Assert-BoundedDatabase -Path $path
    return [ordered]@{kind=$Kind;replica=$Replica;length=$Length;database=$name;size=$bounded.size;page_count=$bounded.page_count;sha256=$bounded.sha256;readback=$readback}
}

$status = "fail"
$detail = "Value scenario did not complete."
$scenarios = New-Object Collections.ArrayList
$exitCode = 1
try {
    [IO.Directory]::CreateDirectory($RunRoot) | Out-Null
    $cp1252 = New-TextFromCodePoints @(0x43,0x61,0x66,0xe9,0x20,0x20ac,0x20,0x152,0x20,0x178)
    $cp1251 = New-TextFromCodePoints @(0x45,0x75,0x72,0x6f,0x20,0x20ac)
    foreach ($replica in 1..3) {
        [void]$scenarios.Add((Invoke-ScalarScenario -Replica $replica))
        [void]$scenarios.Add((Invoke-TextScenario -Replica $replica -CodePage 1252 -Locale $Locale1252 -Value $cp1252))
        [void]$scenarios.Add((Invoke-TextScenario -Replica $replica -CodePage 1251 -Locale $Locale1251 -Value $cp1251))
        foreach ($kind in @("memo", "ole")) {
            foreach ($length in $LongLengths) {
                [void]$scenarios.Add((Invoke-LongScenario -Replica $replica -Kind $kind -Length $length))
            }
        }
    }
    $status = "pass"
    $detail = "Completed 33 bounded scalar, text-code-page, and long-value scenarios."
    $exitCode = 0
}
catch { $detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message }

$result = [ordered]@{
    development_only = $true
    job = "value"
    status = $status
    detail = $detail
    long_lengths = @($LongLengths)
    scenarios = @($scenarios)
}
Write-JsonDocument -Path (Join-Path $RunRoot "value-job-result.json") -Document $result
[Console]::WriteLine($status.ToUpperInvariant() + ": " + $detail)
exit $exitCode
