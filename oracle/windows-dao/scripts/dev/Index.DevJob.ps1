[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumRows = 4096
$MaximumDatabaseBytes = 16MB
$RelationUpdateCascade = 256
$RelationDeleteCascade = 4096

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

function New-Database {
    param([string]$Path)

    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $workspace = $engine.Workspaces.Item(0)
        $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $database
        Release-ComObject -Value $workspace
        Release-ComObject -Value $engine
    }
}

function Add-Field {
    param([object]$Table, [string]$Name, [int]$Type, [int]$Size = 0)

    $field = $null
    try {
        if ($Size -gt 0) { $field = $Table.CreateField($Name, $Type, $Size) }
        else { $field = $Table.CreateField($Name, $Type) }
        $Table.Fields.Append($field)
    }
    finally { Release-ComObject -Value $field }
}

function Add-Index {
    param(
        [object]$Table,
        [string]$Name,
        [string[]]$Fields,
        [bool[]]$Descending,
        [bool]$Primary = $false,
        [bool]$Unique = $false
    )

    $index = $null
    try {
        $index = $Table.CreateIndex($Name)
        $index.Primary = $Primary
        $index.Unique = $Unique
        $index.Required = $Primary
        for ($ordinal = 0; $ordinal -lt $Fields.Count; $ordinal++) {
            $field = $null
            try {
                $field = $index.CreateField($Fields[$ordinal])
                if ($Descending[$ordinal]) {
                    $field.Attributes = 1
                }
                $index.Fields.Append($field)
            }
            finally { Release-ComObject -Value $field }
        }
        $Table.Indexes.Append($index)
    }
    finally { Release-ComObject -Value $index }
}

function Assert-BoundedFile {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path
    if ($item.Length -le 0 -or $item.Length -gt $MaximumDatabaseBytes -or
        ($item.Length % 2048) -ne 0) {
        throw "Index scenario output is outside the checked file bounds."
    }
    return $item
}

function Set-KeyField {
    param([object]$Recordset, [string]$Name, [object]$Value)

    $field = $null
    try {
        $field = $Recordset.Fields.Item($Name)
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
            15 { $field.Value = [guid]$Value }
            default { throw "Unsupported populated key type." }
        }
    }
    catch {
        throw "Key field $Name assignment failed: " + $_.Exception.Message
    }
    finally { Release-ComObject -Value $field }
}

function Add-KeyTypeRows {
    param([object]$Database)

    $culture = [Globalization.CultureInfo]::InvariantCulture
    $rows = @(
        @($false,0,-32768,[int32]::MinValue,"-1234.5000",-10.5,-100.25,
            [datetime]::new(1900,1,1),[byte[]](0,0,0,0,0,0,0,0),"A"),
        @($false,1,-1,-1,"-0.0001",-0.5,-0.25,
            [datetime]::new(1999,12,31),[byte[]](0,1,2,3,4,5,6,7),"Cafe"),
        @($true,127,0,0,"0.0000",0.0,0.0,
            [datetime]::new(2000,1,1),[byte[]](16,32,48,64,80,96,112,128),"Cafe Z"),
        @($true,255,32767,[int32]::MaxValue,"1234.5000",10.5,100.25,
            [datetime]::new(2026,8,27),[byte[]](255,255,255,255,255,255,255,255),"z")
    )
    $names = @(
        "BooleanKey", "ByteKey", "IntegerKey", "LongKey", "CurrencyKey",
        "SingleKey", "DoubleKey", "DateKey", "BinaryKey", "TextKey"
    )
    $recordset = $null
    try {
        $recordset = $Database.OpenRecordset("KeyTypes", 2, 0)
        foreach ($values in $rows) {
            $recordset.AddNew()
            for ($ordinal = 0; $ordinal -lt $names.Count; $ordinal++) {
                $value = $values[$ordinal]
                if ($names[$ordinal] -ceq "CurrencyKey") {
                    $value = [decimal]::Parse([string]$value, $culture)
                }
                Set-KeyField -Recordset $recordset -Name $names[$ordinal] -Value $value
            }
            $recordset.Update()
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
    }
}

function Invoke-LongScenario {
    param([string]$Name, [string]$Order, [bool]$Descending)

    $path = Join-Path $RunRoot "index-$Name.mdb"
    New-Database -Path $path
    $engine = $null
    $database = $null
    $table = $null
    $recordset = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($path)
        $table = $database.CreateTableDef("IndexRows")
        Add-Field -Table $table -Name "KeyValue" -Type $DbLong
        Add-Field -Table $table -Name "Payload" -Type $DbLong
        Add-Index -Table $table -Name "LongIndex" -Fields @("KeyValue") `
            -Descending @($Descending) -Unique $true
        $database.TableDefs.Append($table)
        $recordset = $database.OpenRecordset("IndexRows", 2, 0)
        foreach ($position in 0..($MaximumRows - 1)) {
            $key = switch ($Order) {
                "ascending" { $position - 2048 }
                "descending" { 2047 - $position }
                "permuted" { (($position * 4051) % $MaximumRows) - 2048 }
            }
            $recordset.AddNew()
            $recordset.Fields.Item("KeyValue").Value = [int32]$key
            $recordset.Fields.Item("Payload").Value = [int32]$position
            $recordset.Update()
        }
        $recordset.Close()
        Release-ComObject -Value $recordset
        $recordset = $null
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        $item = Assert-BoundedFile -Path $path
        return [ordered]@{
            scenario = $Name
            database = "index-$Name.mdb"
            row_count = $MaximumRows
            insertion_order = $Order
            descending = $Descending
            size = [long]$item.Length
            page_count = [long]($item.Length / 2048)
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $recordset
        Release-ComObject -Value $table
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-CompositeScenario {
    $name = "composite-descending"
    $path = Join-Path $RunRoot "index-$name.mdb"
    New-Database -Path $path
    $engine = $null
    $database = $null
    $table = $null
    $recordset = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($path)
        $table = $database.CreateTableDef("CompositeRows")
        Add-Field -Table $table -Name "GroupName" -Type $DbText -Size 16
        Add-Field -Table $table -Name "Sequence" -Type $DbInteger
        Add-Field -Table $table -Name "Identity" -Type $DbLong
        Add-Index -Table $table -Name "CompositeIndex" `
            -Fields @("GroupName", "Sequence", "Identity") `
            -Descending @($true, $false, $true) -Unique $true
        $database.TableDefs.Append($table)
        $recordset = $database.OpenRecordset("CompositeRows", 2, 0)
        foreach ($position in 0..1023) {
            $recordset.AddNew()
            $recordset.Fields.Item("GroupName").Value = [string]("G" + ($position % 8))
            $recordset.Fields.Item("Sequence").Value = [int16](($position % 33) - 16)
            $recordset.Fields.Item("Identity").Value = [int32]$position
            $recordset.Update()
        }
        $recordset.Close()
        Release-ComObject -Value $recordset
        $recordset = $null
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        $item = Assert-BoundedFile -Path $path
        return [ordered]@{
            scenario = $name
            database = "index-$name.mdb"
            row_count = 1024
            directions = @("descending", "ascending", "descending")
            size = [long]$item.Length
            page_count = [long]($item.Length / 2048)
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $recordset
        Release-ComObject -Value $table
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-KeyTypeScenario {
    $name = "key-types"
    $path = Join-Path $RunRoot "index-$name.mdb"
    New-Database -Path $path
    $candidates = @(
        @{name="BooleanKey";type=$DbBoolean;size=0},
        @{name="ByteKey";type=$DbByte;size=0},
        @{name="IntegerKey";type=$DbInteger;size=0},
        @{name="LongKey";type=$DbLong;size=0},
        @{name="CurrencyKey";type=$DbCurrency;size=0},
        @{name="SingleKey";type=$DbSingle;size=0},
        @{name="DoubleKey";type=$DbDouble;size=0},
        @{name="DateKey";type=$DbDate;size=0},
        @{name="BinaryKey";type=$DbBinary;size=8},
        @{name="TextKey";type=$DbText;size=24},
        @{name="GuidKey";type=$DbGuid;size=0}
    )
    $rejectedCandidates = @(
        @{name="LongBinaryKey";type=$DbLongBinary;size=0},
        @{name="MemoKey";type=$DbMemo;size=0}
    )
    $results = New-Object Collections.ArrayList
    $engine = $null
    $database = $null
    $table = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($path)
        $table = $database.CreateTableDef("KeyTypes")
        foreach ($candidate in $candidates) {
            Add-Field -Table $table -Name $candidate.name -Type $candidate.type -Size $candidate.size
        }
        $database.TableDefs.Append($table)
        foreach ($candidate in $candidates) {
            $accepted = $true
            $errorCode = $null
            try {
                Add-Index -Table $table -Name ("I_" + $candidate.name) `
                    -Fields @($candidate.name) -Descending @($false)
            }
            catch {
                $accepted = $false
                $errorCode = [int]$_.Exception.HResult
            }
            [void]$results.Add([ordered]@{
                name = $candidate.name
                dao_type = [int]$candidate.type
                accepted = $accepted
                populated = ($accepted -and $candidate.name -cne "GuidKey")
                error_code = $errorCode
            })
        }
        Add-KeyTypeRows -Database $database
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        foreach ($candidate in $rejectedCandidates) {
            $rejectedPath = Join-Path $RunRoot ("rejected-" + $candidate.name + ".mdb")
            New-Database -Path $rejectedPath
            $rejectedEngine = $null
            $rejectedDatabase = $null
            $rejectedTable = $null
            $accepted = $true
            $errorCode = $null
            try {
                $rejectedEngine = New-Object -ComObject "DAO.DBEngine.36"
                $rejectedDatabase = $rejectedEngine.OpenDatabase($rejectedPath)
                $rejectedTable = $rejectedDatabase.CreateTableDef("RejectedKey")
                Add-Field -Table $rejectedTable -Name $candidate.name `
                    -Type $candidate.type -Size $candidate.size
                $rejectedDatabase.TableDefs.Append($rejectedTable)
                Add-Index -Table $rejectedTable -Name ("I_" + $candidate.name) `
                    -Fields @($candidate.name) -Descending @($false)
            }
            catch {
                $accepted = $false
                $errorCode = [int]$_.Exception.HResult
            }
            finally {
                if ($null -ne $rejectedDatabase) { try { $rejectedDatabase.Close() } catch { } }
                Release-ComObject -Value $rejectedTable
                Release-ComObject -Value $rejectedDatabase
                Release-ComObject -Value $rejectedEngine
                Remove-Item -LiteralPath $rejectedPath -Force
            }
            [void]$results.Add([ordered]@{
                name = $candidate.name
                dao_type = [int]$candidate.type
                accepted = $accepted
                populated = $false
                error_code = $errorCode
            })
        }
        $item = Assert-BoundedFile -Path $path
        return [ordered]@{
            scenario = $name
            database = "index-$name.mdb"
            row_count = 4
            key_type_results = @($results)
            size = [long]$item.Length
            page_count = [long]($item.Length / 2048)
        }
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $table
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Invoke-RelationshipScenarios {
    $workingPath = Join-Path $RunRoot "index-relationship-working.mdb"
    New-Database -Path $workingPath
    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($workingPath)
        foreach ($definition in @(
            @{name="Parents";field="Id"},
            @{name="Children";field="ParentId"}
        )) {
            $table = $null
            try {
                $table = $database.CreateTableDef($definition.name)
                Add-Field -Table $table -Name $definition.field -Type $DbLong
                if ($definition.name -ceq "Parents") {
                    Add-Index -Table $table -Name "PrimaryKey" -Fields @("Id") `
                        -Descending @($false) -Primary $true -Unique $true
                }
                $database.TableDefs.Append($table)
            }
            finally { Release-ComObject -Value $table }
        }
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        Copy-Item -LiteralPath $workingPath -Destination (Join-Path $RunRoot "index-relationship-base.mdb")

        foreach ($scenario in @(
            @{name="created";attributes=0},
            @{name="update";attributes=$RelationUpdateCascade},
            @{name="delete";attributes=$RelationDeleteCascade},
            @{name="cascade";attributes=($RelationUpdateCascade + $RelationDeleteCascade)}
        )) {
            $database = $engine.OpenDatabase($workingPath)
            if ($database.Relations.Count -gt 0) {
                $database.Relations.Delete("ParentChild")
            }
            $relation = $null
            $field = $null
            try {
                $relation = $database.CreateRelation(
                    "ParentChild", "Parents", "Children", $scenario.attributes
                )
                $field = $relation.CreateField("Id")
                $field.ForeignName = "ParentId"
                $relation.Fields.Append($field)
                $database.Relations.Append($relation)
            }
            finally {
                Release-ComObject -Value $field
                Release-ComObject -Value $relation
            }
            $database.Close()
            Release-ComObject -Value $database
            $database = $null
            Copy-Item -LiteralPath $workingPath -Destination (
                Join-Path $RunRoot "index-relationship-$($scenario.name).mdb"
            )
        }

        $database = $engine.OpenDatabase($workingPath)
        $database.Relations.Delete("ParentChild")
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        Copy-Item -LiteralPath $workingPath -Destination (Join-Path $RunRoot "index-relationship-deleted.mdb")
        Remove-Item -LiteralPath $workingPath -Force
        $items = @(
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-base.mdb")
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-created.mdb")
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-update.mdb")
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-delete.mdb")
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-cascade.mdb")
            Assert-BoundedFile -Path (Join-Path $RunRoot "index-relationship-deleted.mdb")
        )
        return @(
            [ordered]@{scenario="relationship-base";database="index-relationship-base.mdb";attributes=$null;size=[long]$items[0].Length},
            [ordered]@{scenario="relationship-created";database="index-relationship-created.mdb";attributes=0;size=[long]$items[1].Length},
            [ordered]@{scenario="relationship-update";database="index-relationship-update.mdb";attributes=$RelationUpdateCascade;size=[long]$items[2].Length},
            [ordered]@{scenario="relationship-delete";database="index-relationship-delete.mdb";attributes=$RelationDeleteCascade;size=[long]$items[3].Length},
            [ordered]@{scenario="relationship-cascade";database="index-relationship-cascade.mdb";attributes=($RelationUpdateCascade + $RelationDeleteCascade);size=[long]$items[4].Length},
            [ordered]@{scenario="relationship-deleted";database="index-relationship-deleted.mdb";attributes=$null;size=[long]$items[5].Length}
        )
    }
    finally {
        if ($null -ne $database) { try { $database.Close() } catch { } }
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

$scenarios = New-Object Collections.ArrayList
try {
    [void]$scenarios.Add((Invoke-LongScenario -Name "long-ascending" -Order "ascending" -Descending $false))
    [void]$scenarios.Add((Invoke-LongScenario -Name "long-descending" -Order "descending" -Descending $true))
    [void]$scenarios.Add((Invoke-LongScenario -Name "long-permuted" -Order "permuted" -Descending $false))
    [void]$scenarios.Add((Invoke-CompositeScenario))
    [void]$scenarios.Add((Invoke-KeyTypeScenario))
    foreach ($relationship in @(Invoke-RelationshipScenarios)) {
        [void]$scenarios.Add($relationship)
    }
    $result = [ordered]@{
        development_only = $true
        status = "pass"
        detail = "Completed the bounded index-tree, key-type, and relationship scenarios without compaction."
        compacted = $false
        scenarios = @($scenarios)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "index-job-result.json") -Document $result
    exit 0
}
catch {
    $result = [ordered]@{
        development_only = $true
        status = "fail"
        detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message +
            " at " + $_.InvocationInfo.ScriptLineNumber + " " + $_.ScriptStackTrace
        compacted = $false
        scenarios = @($scenarios)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "index-job-result.json") -Document $result
    exit 1
}
