[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# SRC-0024 permits only these DAO record-mutation APIs; EXP-0060 records the
# separately repeated physical observations produced by this development job.

$DbVersion30 = 32
$DbBoolean = 1
$DbByte = 2
$DbInteger = 3
$DbLong = 4
$DbText = 10
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$MaximumRows = 64

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

function New-ScenarioDatabase {
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
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Add-Field {
    param(
        [object]$Table,
        [string]$Name,
        [int]$Type,
        [int]$Size = 0
    )

    $field = $null
    try {
        if ($Size -gt 0) { $field = $Table.CreateField($Name, $Type, $Size) }
        else { $field = $Table.CreateField($Name, $Type) }
        $Table.Fields.Append($field)
    }
    finally { Release-ComObject -Value $field }
}

function New-ScenarioTable {
    param([object]$Database, [string]$Scenario)

    $table = $null
    try {
        $table = $Database.CreateTableDef("Rows")
        switch ($Scenario) {
            "fixed-only" {
                Add-Field -Table $table -Name "A" -Type $DbByte
                Add-Field -Table $table -Name "B" -Type $DbInteger
                Add-Field -Table $table -Name "C" -Type $DbLong
                Add-Field -Table $table -Name "D" -Type $DbBoolean
            }
            "variable-only" {
                Add-Field -Table $table -Name "A" -Type $DbText -Size 50
                Add-Field -Table $table -Name "B" -Type $DbText -Size 50
            }
            "mixed" {
                Add-Field -Table $table -Name "A" -Type $DbLong
                Add-Field -Table $table -Name "B" -Type $DbText -Size 50
                Add-Field -Table $table -Name "C" -Type $DbByte
            }
            "all-null" {
                Add-Field -Table $table -Name "A" -Type $DbLong
                Add-Field -Table $table -Name "B" -Type $DbText -Size 50
                Add-Field -Table $table -Name "C" -Type $DbInteger
            }
            default {
                Add-Field -Table $table -Name "Id" -Type $DbLong
                Add-Field -Table $table -Name "Payload" -Type $DbText -Size 255
            }
        }
        $Database.TableDefs.Append($table)
    }
    finally { Release-ComObject -Value $table }
}

function Add-Record {
    param([object]$Recordset, [hashtable]$Values)

    $Recordset.AddNew()
    foreach ($name in $Values.Keys) {
        $field = $null
        try {
            $field = $Recordset.Fields.Item([string]$name)
            $value = $Values[$name]
            if ($value -is [DBNull]) { $field.Value = [DBNull]::Value }
            else {
                switch ([int]$field.Type) {
                    1 { $field.Value = [bool]$value }
                    2 { $field.Value = [byte]$value }
                    3 { $field.Value = [int16]$value }
                    4 { $field.Value = [int32]$value }
                    10 { $field.Value = [string]$value }
                    default { throw "Unsupported row scenario DAO field type." }
                }
            }
        }
        catch { throw "Row field $name assignment failed: " + $_.Exception.Message }
        finally { Release-ComObject -Value $field }
    }
    $Recordset.Update()
}

function Set-RecordPayload {
    param([object]$Recordset, [int]$Id, [string]$Payload)

    $Recordset.FindFirst("[Id] = $Id")
    if ($Recordset.NoMatch) { throw "Mutable scenario row $Id was not found." }
    $field = $null
    try {
        $Recordset.Edit()
        $field = $Recordset.Fields.Item("Payload")
        $field.Value = $Payload
        $Recordset.Update()
    }
    finally { Release-ComObject -Value $field }
}

function Populate-Scenario {
    param([object]$Database, [string]$Scenario)

    $recordset = $null
    try {
        $recordset = $Database.OpenRecordset("Rows", 2, 0)
        switch ($Scenario) {
            "fixed-only" {
                Add-Record -Recordset $recordset -Values @{
                    A = [byte]17
                    B = [int16]-1234
                    C = [int32]305419896
                    D = [bool]$true
                }
            }
            "variable-only" { Add-Record -Recordset $recordset -Values @{ A = "A"; B = "BCDE" } }
            "mixed" {
                Add-Record -Recordset $recordset -Values @{
                    A = [int32]270544960
                    B = [string]"mixed"
                    C = [byte]42
                }
            }
            "all-null" {
                Add-Record -Recordset $recordset -Values @{
                    A = [DBNull]::Value; B = [DBNull]::Value; C = [DBNull]::Value
                }
            }
            "page-boundary" {
                foreach ($id in 1..32) {
                    $payload = ("B" + $id.ToString("D2") + "-").PadRight(220, [char](64 + (($id - 1) % 26) + 1))
                    Add-Record -Recordset $recordset -Values @{ Id = $id; Payload = $payload }
                }
            }
            "growing" {
                foreach ($id in 1..4) { Add-Record -Recordset $recordset -Values @{ Id = $id; Payload = "g$id" } }
                Set-RecordPayload -Recordset $recordset -Id 2 -Payload ("G" * 200)
            }
            "shrinking" {
                foreach ($id in 1..4) { Add-Record -Recordset $recordset -Values @{ Id = $id; Payload = "g$id" } }
                Set-RecordPayload -Recordset $recordset -Id 2 -Payload ("G" * 200)
                Set-RecordPayload -Recordset $recordset -Id 2 -Payload "s"
            }
            "deleted" {
                foreach ($id in 1..4) { Add-Record -Recordset $recordset -Values @{ Id = $id; Payload = "g$id" } }
                $recordset.FindFirst("[Id] = 2")
                if ($recordset.NoMatch) { throw "Deleted scenario row was not found." }
                $recordset.Delete()
            }
            "overflowing" {
                foreach ($id in 1..30) { Add-Record -Recordset $recordset -Values @{ Id = $id; Payload = ("p" * 80) } }
                Set-RecordPayload -Recordset $recordset -Id 5 -Payload ("O" * 255)
            }
        }
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
    }
}

function Get-RowCount {
    param([object]$Database)

    $recordset = $null
    try {
        $recordset = $Database.OpenRecordset("Rows", 2, 4)
        if (-not $recordset.EOF) { $recordset.MoveLast() }
        $count = [int]$recordset.RecordCount
        if ($count -lt 0 -or $count -gt $MaximumRows) {
            throw "Scenario row count $count is outside the bounded range."
        }
        return $count
    }
    finally {
        if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
        Release-ComObject -Value $recordset
    }
}

function Invoke-Scenario {
    param([string]$Scenario, [int]$Replica)

    $fileName = "row-r$Replica-$Scenario.mdb"
    $path = Join-Path $RunRoot $fileName
    New-ScenarioDatabase -Path $path
    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($path)
        New-ScenarioTable -Database $database -Scenario $Scenario
        Populate-Scenario -Database $database -Scenario $Scenario
        $rowCount = Get-RowCount -Database $database
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
        $item = Get-Item -LiteralPath $path
        if (($item.Length % 2048) -ne 0) {
            throw "Row scenario is not an exact sequence of 2 KiB pages."
        }
        return [ordered]@{
            scenario = $Scenario
            replica = $Replica
            database = $fileName
            row_count = $rowCount
            size = [long]$item.Length
            page_count = [long]($item.Length / 2048)
        }
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
    foreach ($replica in 1..3) {
        foreach ($scenario in @(
            "fixed-only", "variable-only", "mixed", "all-null", "page-boundary",
            "growing", "shrinking", "deleted", "overflowing"
        )) {
            [void]$scenarios.Add((Invoke-Scenario -Scenario $scenario -Replica $replica))
        }
    }
    $result = [ordered]@{
        development_only = $true
        status = "pass"
        detail = "Completed three independent runs of all nine bounded row scenarios without compaction."
        compacted = $false
        scenarios = @($scenarios)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "row-job-result.json") -Document $result
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
    Write-JsonDocument -Path (Join-Path $RunRoot "row-job-result.json") -Document $result
    exit 1
}
