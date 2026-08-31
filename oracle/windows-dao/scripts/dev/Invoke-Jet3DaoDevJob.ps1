[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("provider-probe", "create-empty", "opening-matrix", "allocation-map", "catalog", "table-definition", "row", "value", "index", "bootstrap-layout")]
    [string]$Job,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,31}$")]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$ProviderProbePath,
    [Parameter(Mandatory = $true)]
    [string]$SharedOutputPath,
    [Parameter(Mandatory = $true)]
    [string]$CatalogJobPath,
    [Parameter(Mandatory = $true)]
    [string]$TableDefinitionJobPath,
    [Parameter(Mandatory = $true)]
    [string]$TableDefinitionTypeInputPath,
    [Parameter(Mandatory = $true)]
    [string]$DispatchPath,
    [Parameter(Mandatory = $true)]
    [string]$PublicationPath,
    [Parameter(Mandatory = $true)]
    [string]$RowJobPath,
    [Parameter(Mandatory = $true)]
    [string]$ValueJobPath,
    [Parameter(Mandatory = $true)]
    [string]$IndexJobPath,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapLayoutJobPath,
    [string]$PlanSha256 = "",
    [string]$PlanPath = "",
    [string]$GuestOutputRoot = (Join-Path $env:LOCALAPPDATA "jet3-rs-dev")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DbVersion30 = 32
$DbVersion40 = 64
$DbEncrypt = 2
$DbLong = 4
$DbLongBinary = 11
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

function Invoke-WithAllocationDatabase {
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

function New-AllocationTable {
    param([string]$Path)

    Invoke-WithAllocationDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $idField = $null
        $payloadField = $null
        try {
            $table = $database.CreateTableDef("AllocationDev")
            $idField = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($idField)
            $payloadField = $table.CreateField("Payload", $DbLongBinary)
            $table.Fields.Append($payloadField)
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $payloadField
            Release-ComObject -Value $idField
            Release-ComObject -Value $table
        }
    }
}

function Add-AllocationRows {
    param(
        [string]$Path,
        [int]$FirstId,
        [int]$Count,
        [byte[]]$Payload
    )

    Invoke-WithAllocationDatabase -Path $Path -Action {
        param($database)
        $recordset = $null
        $idField = $null
        $payloadField = $null
        try {
            $recordset = $database.OpenRecordset("AllocationDev", 2, 0)
            $idField = $recordset.Fields.Item("Id")
            $payloadField = $recordset.Fields.Item("Payload")
            for ($offset = 0; $offset -lt $Count; $offset++) {
                $recordset.AddNew()
                $idField.Value = [int]($FirstId + $offset)
                $payloadField.AppendChunk($Payload)
                $recordset.Update()
            }
        }
        finally {
            Release-ComObject -Value $payloadField
            Release-ComObject -Value $idField
            if ($null -ne $recordset) {
                try { $recordset.Close() } catch { }
            }
            Release-ComObject -Value $recordset
        }
    }
}

function Remove-AllocationRows {
    param(
        [string]$Path,
        [int]$FirstId,
        [int]$Count
    )

    Invoke-WithAllocationDatabase -Path $Path -Action {
        param($database)
        $recordset = $null
        try {
            $lastId = $FirstId + $Count - 1
            $sql = "SELECT Id FROM [AllocationDev] WHERE Id >= $FirstId AND Id <= $lastId ORDER BY Id"
            $recordset = $database.OpenRecordset($sql, 2, 0)
            $deleted = 0
            while (-not $recordset.EOF) {
                $recordset.Delete()
                $recordset.MoveNext()
                $deleted++
            }
            if ($deleted -ne $Count) {
                throw "Allocation deletion removed $deleted rows; expected $Count."
            }
        }
        finally {
            if ($null -ne $recordset) {
                try { $recordset.Close() } catch { }
            }
            Release-ComObject -Value $recordset
        }
    }
}

function Get-Type05Pages {
    param([string]$Path)

    $stream = $null
    try {
        $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
        if (($stream.Length % 2048) -ne 0) {
            throw "Allocation checkpoint is not an exact sequence of 2 KiB pages."
        }
        $pages = New-Object Collections.ArrayList
        $buffer = New-Object byte[] 1
        $pageCount = [long]($stream.Length / 2048)
        for ($page = 1; $page -lt $pageCount; $page++) {
            $stream.Position = [long]$page * 2048
            if ($stream.Read($buffer, 0, 1) -ne 1) {
                throw "Allocation checkpoint page-tag read was short."
            }
            if ($buffer[0] -eq 5) {
                if ($pages.Count -ge 64) {
                    throw "Allocation checkpoint contains more than 64 type-05 pages."
                }
                [void]$pages.Add([long]$page)
            }
        }
        return @($pages)
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Get-MultiSlotUsageMap {
    param([string]$Path)

    $bytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
    if (($bytes.Length % 2048) -ne 0) {
        throw "Allocation checkpoint is not an exact sequence of 2 KiB pages."
    }
    $pageCount = [int]($bytes.Length / 2048)
    for ($page = 1; $page -lt $pageCount; $page++) {
        $pageStart = $page * 2048
        if ($bytes[$pageStart] -ne 1) { continue }
        $rowCount = [int]$bytes[$pageStart + 8] -bor `
            ([int]$bytes[$pageStart + 9] -shl 8)
        if ($rowCount -gt 1019) { continue }
        $directoryEnd = 10 + 2 * $rowCount
        $priorStart = 2048
        for ($row = 0; $row -lt $rowCount; $row++) {
            $entry = $pageStart + 10 + 2 * $row
            $rawStart = [int]$bytes[$entry] -bor ([int]$bytes[$entry + 1] -shl 8)
            $rowStart = $rawStart -band 0x1fff
            $rowEnd = $priorStart
            $priorStart = $rowStart
            if ($rowStart -lt $directoryEnd -or $rowStart -ge $rowEnd) { break }
            $rowLength = $rowEnd - $rowStart
            if ($bytes[$pageStart + $rowStart] -ne 1 -or (($rowLength - 1) % 4) -ne 0) {
                continue
            }
            $references = New-Object Collections.ArrayList
            $valid = $true
            for ($slot = 0; $slot -lt (($rowLength - 1) / 4); $slot++) {
                $referenceOffset = $pageStart + $rowStart + 1 + 4 * $slot
                $reference = [BitConverter]::ToUInt32($bytes, $referenceOffset)
                if ($reference -eq 0) { continue }
                if ($reference -ge $pageCount -or $bytes[[int]$reference * 2048] -ne 5) {
                    $valid = $false
                    break
                }
                [void]$references.Add([long]$reference)
            }
            if ($valid -and $references.Count -ge 2) {
                return [ordered]@{
                    record_page = [long]$page
                    record_row = $row
                    references = @($references)
                }
            }
        }
    }
    return $null
}

function Save-AllocationCheckpoint {
    param(
        [string]$Source,
        [string]$RunRoot,
        [string]$Name,
        [int]$Rows
    )

    $fileName = "allocation-$Name.mdb"
    $destination = Join-Path $RunRoot $fileName
    Copy-Item -LiteralPath $Source -Destination $destination
    $item = Get-Item -LiteralPath $destination
    $type05 = @(Get-Type05Pages -Path $destination)
    return [ordered]@{
        name = $Name
        database = $fileName
        rows = $Rows
        size = [long]$item.Length
        page_count = [long]($item.Length / 2048)
        type_05_pages = @($type05)
    }
}

if (-not (Test-Path -LiteralPath $ProviderProbePath -PathType Leaf)) {
    [Console]::Error.WriteLine("INVALID: provider probe does not exist.")
    exit 2
}
if ($Job -ceq "catalog" -and -not (Test-Path -LiteralPath $CatalogJobPath -PathType Leaf)) {
    [Console]::Error.WriteLine("INVALID: catalog job does not exist.")
    exit 2
}
if ($Job -ceq "table-definition" -and
    (-not (Test-Path -LiteralPath $TableDefinitionJobPath -PathType Leaf) -or
     -not (Test-Path -LiteralPath $TableDefinitionTypeInputPath -PathType Leaf))) {
    [Console]::Error.WriteLine("INVALID: table-definition job inputs do not exist.")
    exit 2
}
foreach ($requiredHelper in @(
    $DispatchPath, $PublicationPath, $RowJobPath, $ValueJobPath, $IndexJobPath,
    $BootstrapLayoutJobPath
)) {
    if (-not (Test-Path -LiteralPath $requiredHelper -PathType Leaf)) {
        [Console]::Error.WriteLine("INVALID: staged development helper does not exist.")
        exit 2
    }
}
if ($Job -ceq "bootstrap-layout" -and $PlanSha256 -cnotmatch "^[0-9a-f]{64}$") {
    [Console]::Error.WriteLine("INVALID: bootstrap-layout plan digest is malformed.")
    exit 2
}
if ($Job -ceq "bootstrap-layout") {
    if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {
        [Console]::Error.WriteLine("INVALID: bootstrap-layout plan is missing.")
        exit 2
    }
    $actualPlanSha256 = (Get-FileHash -LiteralPath $PlanPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualPlanSha256 -cne $PlanSha256) {
        [Console]::Error.WriteLine("INVALID: bootstrap-layout plan digest differs after staging.")
        exit 2
    }
    $plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
    $guestInputs = [ordered]@{
        "oracle/windows-dao/scripts/probe-provider.ps1" = $ProviderProbePath
        "oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1" = $PSCommandPath
        "oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1" = $DispatchPath
        "oracle/windows-dao/scripts/dev/Publish.DevJob.ps1" = $PublicationPath
        "oracle/windows-dao/scripts/dev/BootstrapLayout.DevJob.ps1" = $BootstrapLayoutJobPath
    }
    foreach ($entry in $guestInputs.GetEnumerator()) {
        $pin = $plan.inputs.PSObject.Properties[$entry.Key]
        if ($null -eq $pin) {
            [Console]::Error.WriteLine("INVALID: bootstrap-layout plan omits a staged input.")
            exit 2
        }
        $actual = (Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne [string]$pin.Value) {
            [Console]::Error.WriteLine("INVALID: bootstrap-layout staged input differs from its plan.")
            exit 2
        }
    }
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
$allocationCheckpoints = @()
$allocationBatchRows = $null
$allocationPayloadBytes = $null
$allocationExtendedDetectedAtRows = $null
$allocationMultiSlotMap = $null
$catalogCheckpoints = @()
$tableDefinitionCheckpoints = @()
$tableDefinitionTypeResults = @()
$rowScenarios = @()
$valueScenarios = @()
$indexScenarios = @()
$bootstrapLayoutReplicas = @()

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
elseif ($Job -ceq "allocation-map" -and $probeExitCode -eq 0) {
    $environment = Get-Content -LiteralPath $environmentPath -Raw | ConvertFrom-Json
    if ([string]$environment.accepted_provider.prog_id -cne "DAO.DBEngine.36") {
        $detail = "The ready provider is not DAO.DBEngine.36."
    }
    else {
        $engine = $null
        $workspace = $null
        $database = $null
        try {
            $workingPath = Join-Path $runRoot "allocation-working.mdb"
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

            $rows = 0
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "00-empty" -Rows $rows
            New-AllocationTable -Path $workingPath
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "01-created" -Rows $rows

            $allocationPayloadBytes = 1800
            $payload = New-Object byte[] $allocationPayloadBytes
            for ($index = 0; $index -lt $payload.Length; $index++) {
                $payload[$index] = [byte](($index * 29 + 17) % 251)
            }
            Add-AllocationRows -Path $workingPath -FirstId 1 -Count 2 -Payload $payload
            $rows = 2
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "02-seeded" -Rows $rows

            $baselineType05 = @(Get-Type05Pages -Path $workingPath).Count
            $allocationBatchRows = 256
            $maximumRows = 32768
            $previousPath = Join-Path $runRoot "allocation-previous.mdb"
            $foundExtended = $false
            while ($rows -lt $maximumRows) {
                Copy-Item -LiteralPath $workingPath -Destination $previousPath -Force
                $count = [Math]::Min($allocationBatchRows, $maximumRows - $rows)
                Add-AllocationRows -Path $workingPath -FirstId ($rows + 1) `
                    -Count $count -Payload $payload
                $rows += $count
                if (@(Get-Type05Pages -Path $workingPath).Count -gt $baselineType05) {
                    $foundExtended = $true
                    break
                }
            }
            if (-not $foundExtended) {
                throw "No new type-05 page appeared within the bounded growth scenario."
            }
            Copy-Item -LiteralPath $previousPath `
                -Destination (Join-Path $runRoot "allocation-03-before-extended.mdb")
            $beforeItem = Get-Item -LiteralPath $previousPath
            $allocationCheckpoints += [ordered]@{
                name = "03-before-extended"
                database = "allocation-03-before-extended.mdb"
                rows = $rows - $count
                size = [long]$beforeItem.Length
                page_count = [long]($beforeItem.Length / 2048)
                type_05_pages = @(Get-Type05Pages -Path $previousPath)
            }
            $allocationExtendedDetectedAtRows = $rows
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "04-after-extended" -Rows $rows

            $observedType05Count = @(Get-Type05Pages -Path $workingPath).Count
            while ($rows -lt $maximumRows -and $null -eq $allocationMultiSlotMap) {
                $count = [Math]::Min($allocationBatchRows, $maximumRows - $rows)
                Add-AllocationRows -Path $workingPath -FirstId ($rows + 1) `
                    -Count $count -Payload $payload
                $rows += $count
                $currentType05Count = @(Get-Type05Pages -Path $workingPath).Count
                if ($currentType05Count -gt $observedType05Count) {
                    $observedType05Count = $currentType05Count
                    $allocationMultiSlotMap = Get-MultiSlotUsageMap -Path $workingPath
                }
            }
            if ($null -eq $allocationMultiSlotMap) {
                throw "No type-1 row with two valid type-05 references appeared within the bounded growth scenario."
            }
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "05-grown" -Rows $rows

            $churnRows = 256
            $churnFirstId = $rows - $churnRows + 1
            Remove-AllocationRows -Path $workingPath -FirstId $churnFirstId `
                -Count $churnRows
            $rows -= $churnRows
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "06-deleted" -Rows $rows

            Add-AllocationRows -Path $workingPath -FirstId $churnFirstId `
                -Count $churnRows -Payload $payload
            $rows += $churnRows
            $allocationCheckpoints += Save-AllocationCheckpoint `
                -Source $workingPath -RunRoot $runRoot -Name "07-reinserted" -Rows $rows

            $status = "pass"
            $detail = "Completed the bounded allocation growth, deletion, and reinsertion scenario."
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
elseif ($Job -in @("catalog", "table-definition", "row", "value", "index", "bootstrap-layout") -and $probeExitCode -eq 0) {
    $environment = Get-Content -LiteralPath $environmentPath -Raw | ConvertFrom-Json
    if ([string]$environment.accepted_provider.prog_id -cne "DAO.DBEngine.36") {
        $detail = "The ready provider is not DAO.DBEngine.36."
    }
    else {
        & (Join-Path $PSHOME "powershell.exe") -NoProfile -NonInteractive `
            -ExecutionPolicy Bypass -File $DispatchPath -Job $Job -RunRoot $runRoot `
            -CatalogJobPath $CatalogJobPath -TableDefinitionJobPath $TableDefinitionJobPath `
            -TableDefinitionTypeInputPath $TableDefinitionTypeInputPath -RowJobPath $RowJobPath `
            -ValueJobPath $ValueJobPath -IndexJobPath $IndexJobPath `
            -BootstrapLayoutJobPath $BootstrapLayoutJobPath -PlanSha256 $PlanSha256
        $dispatchExitCode = [int]$LASTEXITCODE
        $dispatchResultPath = Join-Path $runRoot "dispatch-result.json"
        if (-not (Test-Path -LiteralPath $dispatchResultPath -PathType Leaf)) {
            $status = "fail"
            $detail = "The staged dispatcher did not write its bounded result."
            $exitCode = 1
        }
        else {
            $dispatchResult = Get-Content -LiteralPath $dispatchResultPath -Raw |
                ConvertFrom-Json
            $catalogCheckpoints = @($dispatchResult.catalog_checkpoints)
            $tableDefinitionCheckpoints = @($dispatchResult.table_definition_checkpoints)
            $tableDefinitionTypeResults = @($dispatchResult.table_definition_type_results)
            $rowScenarios = @($dispatchResult.row_scenarios)
            $valueScenarios = @($dispatchResult.value_scenarios)
            $indexScenarios = @($dispatchResult.index_scenarios)
            $bootstrapLayoutReplicas = @($dispatchResult.bootstrap_layout_replicas)
            $status = [string]$dispatchResult.status
            $detail = [string]$dispatchResult.detail
            $exitCode = $dispatchExitCode
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
    allocation_checkpoints = @($allocationCheckpoints)
    allocation_batch_rows = $allocationBatchRows
    allocation_payload_bytes = $allocationPayloadBytes
    allocation_extended_detected_at_rows = $allocationExtendedDetectedAtRows
    allocation_multi_slot_map = $allocationMultiSlotMap
    catalog_checkpoints = @($catalogCheckpoints)
    table_definition_checkpoints = @($tableDefinitionCheckpoints)
    table_definition_type_results = @($tableDefinitionTypeResults)
    row_scenarios = @($rowScenarios)
    value_scenarios = @($valueScenarios)
    index_scenarios = @($indexScenarios)
    plan_sha256 = $PlanSha256
    bootstrap_layout_replicas = @($bootstrapLayoutReplicas)
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
}
Write-JsonDocument -Path (Join-Path $runRoot "result.json") -Document $result

try {
    & (Join-Path $PSHOME "powershell.exe") -NoProfile -NonInteractive `
        -ExecutionPolicy Bypass -File $PublicationPath -Job $Job `
        -Source $runRoot -Destination $SharedOutputPath
    if ([int]$LASTEXITCODE -ne 0) {
        throw "The staged publication helper failed."
    }
}
catch {
    [Console]::Error.WriteLine("ERROR: development publication failed: " + $_.Exception.Message)
    exit 4
}

[Console]::WriteLine($status.ToUpperInvariant() + ": " + $detail)
exit $exitCode
