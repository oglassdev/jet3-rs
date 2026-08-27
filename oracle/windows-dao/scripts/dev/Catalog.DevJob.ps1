[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbLong = 4
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$AsciiTableName = "CatalogAscii"
$Cp1252TableName = "Caf" + [char]0x00e9 + "_Euro" + [char]0x20ac

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

function Invoke-WithDatabase {
    param(
        [string]$Path,
        [scriptblock]$Action
    )

    $engine = $null
    $database = $null
    try {
        $engine = New-Object -ComObject "DAO.DBEngine.36"
        $database = $engine.OpenDatabase($Path)
        & $Action $database
        $database.Close()
        Release-ComObject -Value $database
        $database = $null
    }
    finally {
        if ($null -ne $database) {
            try { $database.Close() } catch { }
        }
        Release-ComObject -Value $database
        Release-ComObject -Value $engine
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function New-CatalogTable {
    param(
        [string]$Path,
        [string]$Name
    )

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $field = $null
        try {
            $table = $database.CreateTableDef($Name)
            $field = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($field)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $field
            Release-ComObject -Value $table
        }
    }
}

function Remove-CatalogTable {
    param(
        [string]$Path,
        [string]$Name
    )

    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $database.TableDefs.Delete($Name)
    }
}

function Get-TableSnapshot {
    param([string]$Path)

    $tables = New-Object Collections.ArrayList
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $definitions = $null
        try {
            $definitions = $database.TableDefs
            $count = [int]$definitions.Count
            if ($count -gt 128) {
                throw "DAO returned more than 128 table definitions."
            }
            for ($index = 0; $index -lt $count; $index++) {
                $table = $null
                try {
                    $table = $definitions.Item($index)
                    [void]$tables.Add([ordered]@{
                        name = [string]$table.Name
                        attributes = [long]$table.Attributes
                    })
                }
                finally {
                    Release-ComObject -Value $table
                }
            }
        }
        finally {
            Release-ComObject -Value $definitions
        }
    }
    return @($tables)
}

function Save-CatalogCheckpoint {
    param(
        [string]$Source,
        [string]$Name
    )

    # Get-TableSnapshot opens and closes DAO before the MDB is copied.
    $tables = @(Get-TableSnapshot -Path $Source)
    $fileName = "catalog-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $item = Get-Item -LiteralPath $destination
    if (($item.Length % 2048) -ne 0) {
        throw "Catalog checkpoint is not an exact sequence of 2 KiB pages."
    }
    return [ordered]@{
        name = $Name
        database = $fileName
        size = [long]$item.Length
        page_count = [long]($item.Length / 2048)
        tables = @($tables)
    }
}

$workingPath = Join-Path $RunRoot "catalog-working.mdb"
$engine = $null
$workspace = $null
$database = $null
try {
    $engine = New-Object -ComObject "DAO.DBEngine.36"
    $workspace = $engine.Workspaces.Item(0)
    $database = $workspace.CreateDatabase(
        $workingPath,
        $DatabaseLocale,
        $DbVersion30
    )
    $database.Close()
    Release-ComObject -Value $database
    $database = $null
    Release-ComObject -Value $workspace
    $workspace = $null
    Release-ComObject -Value $engine
    $engine = $null

    $checkpoints = New-Object Collections.ArrayList
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "00-empty"))
    New-CatalogTable -Path $workingPath -Name $AsciiTableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "01-ascii-created"))
    Remove-CatalogTable -Path $workingPath -Name $AsciiTableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "02-ascii-dropped"))
    New-CatalogTable -Path $workingPath -Name $AsciiTableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "03-ascii-recreated"))
    New-CatalogTable -Path $workingPath -Name $Cp1252TableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "04-cp1252-created"))
    Remove-CatalogTable -Path $workingPath -Name $Cp1252TableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "05-cp1252-dropped"))
    New-CatalogTable -Path $workingPath -Name $Cp1252TableName
    [void]$checkpoints.Add((Save-CatalogCheckpoint -Source $workingPath -Name "06-cp1252-recreated"))

    $result = [ordered]@{
        development_only = $true
        status = "pass"
        detail = "Completed the bounded catalog create, drop, and recreate scenario."
        database_locale = $DatabaseLocale
        ascii_table_name = $AsciiTableName
        cp1252_table_name = $Cp1252TableName
        checkpoints = @($checkpoints)
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "catalog-job-result.json") -Document $result
    exit 0
}
catch {
    $result = [ordered]@{
        development_only = $true
        status = "fail"
        detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
        database_locale = $DatabaseLocale
        ascii_table_name = $AsciiTableName
        cp1252_table_name = $Cp1252TableName
        checkpoints = @()
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "catalog-job-result.json") -Document $result
    exit 1
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
