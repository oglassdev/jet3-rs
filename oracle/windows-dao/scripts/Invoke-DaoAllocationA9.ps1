[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$EnvironmentPath,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [int]$TimeoutMinutes = 120
)

# A9 (issue #99) page-allocation generator. It drives DAO through the creation,
# growth, deletion, and reinsertion sequences preregistered in
# acquisition/a9-allocation.plan.json and retains raw 2 KiB page images at each
# closed-file checkpoint. Page selection uses only byte zero (SRC-0020) and the
# EXP-0057 table-map locators; nothing here interprets allocation semantics.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# DAO API constants (SRC-0002, SRC-0009, SRC-0014); adapter inputs only.
$DbVersion30 = 32
$DbLong = 4
$DbText = 10
$DbLongBinary = 11
$DbMemo = 12
$DatabaseLocale = ";LANGID=0x0409;CP=1252;COUNTRY=0"
$PageSize = 2048
$Replicas = 3
$MaximumRows = 32768
$MaximumPages = 40000
$MaximumTaggedPages = 64
$LongBinaryBytes = 1800
$LongBinaryBatch = 64
$MemoLength = 4096
$MemoMarkerByte = 0x4c
$Deadline = [DateTime]::UtcNow.AddMinutes($TimeoutMinutes)
$Utf8 = New-Object Text.UTF8Encoding($false)
$Checkpoints = New-Object Collections.ArrayList
$Provider = $null

function Assert-Budget {
    if ([DateTime]::UtcNow -gt $Deadline) { throw "The A9 generator exceeded its time budget." }
}

function Release-ComObject {
    param([object]$Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Write-JsonDocument {
    param([string]$Path, [object]$Document)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 8 -Compress) + "`n"),
        $Utf8
    )
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Convert-ToLowerHex {
    param([byte[]]$Bytes)
    return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
}

function New-Engine {
    $type = [Type]::GetTypeFromProgID([string]$Provider.prog_id, $false)
    if ($null -eq $type) { throw "Accepted DAO provider is unavailable." }
    $engine = [Activator]::CreateInstance($type)
    if ([string]$engine.Version -cne [string]$Provider.provider_version) {
        throw "The active DAO version differs from the probed provider."
    }
    return $engine
}

function Invoke-WithDatabase {
    param([string]$Path, [switch]$Create, [scriptblock]$Action)
    $engine = $null
    $workspace = $null
    $database = $null
    try {
        $engine = New-Engine
        $workspace = $engine.Workspaces.Item(0)
        if ($Create) {
            $database = $workspace.CreateDatabase($Path, $DatabaseLocale, $DbVersion30)
        }
        else {
            $database = $engine.OpenDatabase($Path)
        }
        if ($null -ne $Action) { & $Action $database }
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
    Assert-Budget
}

function New-Database {
    param([string]$Path)
    Invoke-WithDatabase -Path $Path -Create -Action $null
}

function New-Table {
    param([string]$Path, [string]$Name, [ValidateSet("text", "long_binary", "keyed_memo")][string]$Kind)
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $table = $null
        $id = $null
        $payload = $null
        $index = $null
        $indexField = $null
        try {
            $table = $database.CreateTableDef($Name)
            $id = $table.CreateField("Id", $DbLong)
            $table.Fields.Append($id)
            switch ($Kind) {
                "text" { $payload = $table.CreateField("Payload", $DbText, 255) }
                "long_binary" { $payload = $table.CreateField("Payload", $DbLongBinary) }
                "keyed_memo" { $payload = $table.CreateField("Note", $DbMemo) }
            }
            $table.Fields.Append($payload)
            if ($Kind -ceq "keyed_memo") {
                $index = $table.CreateIndex("PrimaryKey")
                $index.Primary = $true
                $index.Unique = $true
                $indexField = $index.CreateField("Id")
                $index.Fields.Append($indexField)
                $table.Indexes.Append($index)
            }
            $database.TableDefs.Append($table)
        }
        finally {
            Release-ComObject -Value $indexField
            Release-ComObject -Value $index
            Release-ComObject -Value $payload
            Release-ComObject -Value $id
            Release-ComObject -Value $table
        }
    }
}

function Add-Rows {
    param([string]$Path, [string]$Table, [int]$FirstId, [int]$Count, [string]$Kind)
    if ($FirstId + $Count - 1 -gt $MaximumRows) { throw "The A9 row ceiling would be exceeded." }
    $longBinary = New-Object byte[] $LongBinaryBytes
    for ($index = 0; $index -lt $longBinary.Length; $index++) {
        $longBinary[$index] = [byte](($index * 29 + 17) % 251)
    }
    $memo = [string]::new([char]$MemoMarkerByte, $MemoLength)
    $text = [string]::new([char]0x78, 200)
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $recordset = $null
        $id = $null
        $payload = $null
        try {
            $recordset = $database.OpenRecordset($Table, 2, 0)
            $id = $recordset.Fields.Item(0)
            $payload = $recordset.Fields.Item(1)
            for ($offset = 0; $offset -lt $Count; $offset++) {
                $recordset.AddNew()
                $id.Value = [int]($FirstId + $offset)
                switch ($Kind) {
                    "text" { $payload.Value = $text }
                    "long_binary" { $payload.AppendChunk($longBinary) }
                    "keyed_memo" { $payload.AppendChunk($memo) }
                }
                $recordset.Update()
            }
        }
        finally {
            Release-ComObject -Value $payload
            Release-ComObject -Value $id
            if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
            Release-ComObject -Value $recordset
        }
    }
}

function Remove-RowsFrom {
    param([string]$Path, [string]$Table, [int]$FirstId)
    Invoke-WithDatabase -Path $Path -Action {
        param($database)
        $recordset = $null
        try {
            $recordset = $database.OpenRecordset(
                "SELECT Id FROM [$Table] WHERE Id >= $FirstId ORDER BY Id", 2, 0)
            while (-not $recordset.EOF) {
                $recordset.Delete()
                $recordset.MoveNext()
            }
        }
        finally {
            if ($null -ne $recordset) { try { $recordset.Close() } catch { } }
            Release-ComObject -Value $recordset
        }
    }
}

function Remove-Table {
    param([string]$Path, [string]$Table)
    Invoke-WithDatabase -Path $Path -Action { param($database) $database.TableDefs.Delete($Table) }
}

function Get-PageCount {
    param([string]$Path)
    $length = (Get-Item -LiteralPath $Path).Length
    if (($length % $PageSize) -ne 0) { throw "The database is not an exact sequence of 2 KiB pages." }
    $count = [int]($length / $PageSize)
    if ($count -gt $MaximumPages) { throw "The database exceeded the A9 page ceiling." }
    return $count
}

function Get-PageTags {
    param([byte[]]$Bytes)
    $tags = New-Object byte[] ([int]($Bytes.Length / $PageSize))
    for ($page = 0; $page -lt $tags.Length; $page++) { $tags[$page] = $Bytes[$page * $PageSize] }
    return ,$tags
}

function Get-TaggedPageCount {
    param([string]$Path, [byte]$Tag)
    $tags = Get-PageTags -Bytes ([IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path)))
    return @($tags | Where-Object { $_ -eq $Tag }).Count
}

function Get-SelectedPages {
    # Pages 0 and 1, every tag-02 and tag-05 page, and the pages named by the
    # EXP-0057 locators at tabledef offsets 35 and 39 (three-byte page numbers).
    param([byte[]]$Bytes)
    $tags = Get-PageTags -Bytes $Bytes
    $selected = New-Object Collections.Generic.SortedSet[int]
    [void]$selected.Add(0)
    [void]$selected.Add(1)
    $tagged = 0
    for ($page = 2; $page -lt $tags.Length; $page++) {
        if ($tags[$page] -ne 2 -and $tags[$page] -ne 5) { continue }
        if (++$tagged -gt $MaximumTaggedPages) { throw "Too many tag-02/05 pages to capture." }
        [void]$selected.Add($page)
        if ($tags[$page] -ne 2) { continue }
        foreach ($offset in @(36, 40)) {
            $base = $page * $PageSize + $offset
            $reference = [int]$Bytes[$base] -bor ([int]$Bytes[$base + 1] -shl 8) -bor ([int]$Bytes[$base + 2] -shl 16)
            if ($reference -gt 1 -and $reference -lt $tags.Length) { [void]$selected.Add($reference) }
        }
    }
    return @($selected)
}

function Save-Checkpoint {
    param([string]$Path, [int]$Replica, [string]$Question, [string]$Name, [ValidateSet("full", "selected")][string]$Capture)
    Assert-Budget
    $bytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
    $pageCount = Get-PageCount -Path $Path
    $numbers = if ($Capture -ceq "full") { @(0..($pageCount - 1)) } else { Get-SelectedPages -Bytes $bytes }
    $pages = New-Object Collections.ArrayList
    foreach ($number in $numbers) {
        $image = New-Object byte[] $PageSize
        [Array]::Copy($bytes, $number * $PageSize, $image, 0, $PageSize)
        $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($image)
        [void]$pages.Add([ordered]@{
            page = [int]$number
            sha256 = Convert-ToLowerHex $digest
            hex = Convert-ToLowerHex $image
        })
    }
    $relative = "r$Replica/$Question/$Name.json"
    $target = Join-Path $OutputRoot $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
    Write-JsonDocument -Path $target -Document ([ordered]@{
        document_type = "dao_allocation_a9_checkpoint"
        replica = $Replica
        question = $Question
        name = $Name
        capture = $Capture
        page_count = $pageCount
        pages = @($pages)
    })
    [void]$Checkpoints.Add([ordered]@{
        replica = $Replica
        question = $Question
        name = $Name
        capture = $Capture
        page_count = $pageCount
        path = $relative
        sha256 = Get-FileSha256 -Path $target
    })
    Write-Host "checkpoint $relative ($pageCount pages)"
}

function Invoke-Replica {
    param([int]$Replica)
    $work = Join-Path $OutputRoot "work\r$Replica"
    [IO.Directory]::CreateDirectory($work) | Out-Null
    $previous = Join-Path $work "previous.mdb"

    # Q1: fresh empty database (EXP-0058 expects 20 pages).
    $q1 = Join-Path $work "q1.mdb"
    New-Database -Path $q1
    Save-Checkpoint -Path $q1 -Replica $Replica -Question "Q1" -Name "00-empty" -Capture "full"

    # Q2: first table-definition append, then the first data-page append.
    $q2 = Join-Path $work "q2.mdb"
    New-Database -Path $q2
    Save-Checkpoint -Path $q2 -Replica $Replica -Question "Q2" -Name "00-empty" -Capture "full"
    New-Table -Path $q2 -Name "A9Rows" -Kind "text"
    Save-Checkpoint -Path $q2 -Replica $Replica -Question "Q2" -Name "01-table-created" -Capture "full"
    $baseline = Get-PageCount -Path $q2
    $grew = $false
    for ($row = 1; $row -le 64; $row++) {
        Copy-Item -LiteralPath $q2 -Destination $previous -Force
        Add-Rows -Path $q2 -Table "A9Rows" -FirstId $row -Count 1 -Kind "text"
        if ((Get-PageCount -Path $q2) -gt $baseline) { $grew = $true; break }
    }
    if (-not $grew) { throw "Q2: no data page was appended within 64 rows." }
    Save-Checkpoint -Path $previous -Replica $Replica -Question "Q2" -Name "02-before-data-page" -Capture "full"
    Save-Checkpoint -Path $q2 -Replica $Replica -Question "Q2" -Name "03-after-data-page" -Capture "full"

    # Q3: populate several data pages, free the tail and a second table, reinsert.
    $q3 = Join-Path $work "q3.mdb"
    New-Database -Path $q3
    New-Table -Path $q3 -Name "A9Rows" -Kind "text"
    Save-Checkpoint -Path $q3 -Replica $Replica -Question "Q3" -Name "00-first-table" -Capture "full"
    New-Table -Path $q3 -Name "A9Drop" -Kind "text"
    Add-Rows -Path $q3 -Table "A9Drop" -FirstId 1 -Count 8 -Kind "text"
    Save-Checkpoint -Path $q3 -Replica $Replica -Question "Q3" -Name "01-second-table" -Capture "full"
    $baseline = Get-PageCount -Path $q3
    $rows = 0
    while ((Get-PageCount -Path $q3) -lt $baseline + 6) {
        if ($rows -ge 512) { throw "Q3: six data pages were not reached within 512 rows." }
        Add-Rows -Path $q3 -Table "A9Rows" -FirstId ($rows + 1) -Count 8 -Kind "text"
        $rows += 8
    }
    Save-Checkpoint -Path $q3 -Replica $Replica -Question "Q3" -Name "02-populated" -Capture "full"
    $keep = [int]($rows / 2)
    Remove-RowsFrom -Path $q3 -Table "A9Rows" -FirstId ($keep + 1)
    Remove-Table -Path $q3 -Table "A9Drop"
    Save-Checkpoint -Path $q3 -Replica $Replica -Question "Q3" -Name "03-freed" -Capture "full"
    $step = [int](($rows - $keep) / 4)
    for ($ordinal = 1; $ordinal -le 4; $ordinal++) {
        Add-Rows -Path $q3 -Table "A9Rows" -FirstId ($keep + 1 + ($ordinal - 1) * $step) -Count $step -Kind "text"
        Save-Checkpoint -Path $q3 -Replica $Replica -Question "Q3" -Name "04-reinsert-$ordinal" -Capture "full"
    }

    # Q4: long-binary growth until the first and second tag-05 pages appear (EXP-0057).
    $q4 = Join-Path $work "q4.mdb"
    New-Database -Path $q4
    New-Table -Path $q4 -Name "A9Lval" -Kind "long_binary"
    Save-Checkpoint -Path $q4 -Replica $Replica -Question "Q4" -Name "00-created" -Capture "selected"
    $rows = 0
    foreach ($ordinal in @("first", "second")) {
        $observed = Get-TaggedPageCount -Path $q4 -Tag 5
        $found = $false
        while ($rows -lt $MaximumRows) {
            Copy-Item -LiteralPath $q4 -Destination $previous -Force
            Add-Rows -Path $q4 -Table "A9Lval" -FirstId ($rows + 1) -Count $LongBinaryBatch -Kind "long_binary"
            $rows += $LongBinaryBatch
            if ((Get-TaggedPageCount -Path $q4 -Tag 5) -gt $observed) { $found = $true; break }
        }
        if (-not $found) { throw "Q4: the $ordinal type-05 page did not appear within $MaximumRows rows." }
        $prefix = if ($ordinal -ceq "first") { "01" } else { "03" }
        $suffix = if ($ordinal -ceq "first") { "02" } else { "04" }
        Save-Checkpoint -Path $previous -Replica $Replica -Question "Q4" -Name "$prefix-before-$ordinal-type05" -Capture "selected"
        Save-Checkpoint -Path $q4 -Replica $Replica -Question "Q4" -Name "$suffix-after-$ordinal-type05" -Capture "selected"
    }
    Remove-Item -LiteralPath $q4 -Force

    # Q5: primary key plus memo; index-tree and long-value page ownership.
    $q5 = Join-Path $work "q5.mdb"
    New-Database -Path $q5
    New-Table -Path $q5 -Name "A9Keyed" -Kind "keyed_memo"
    Save-Checkpoint -Path $q5 -Replica $Replica -Question "Q5" -Name "00-created" -Capture "full"
    Add-Rows -Path $q5 -Table "A9Keyed" -FirstId 1 -Count 64 -Kind "keyed_memo"
    Save-Checkpoint -Path $q5 -Replica $Replica -Question "Q5" -Name "01-populated" -Capture "full"
    Remove-Item -LiteralPath $previous -Force -ErrorAction SilentlyContinue
}

if ([IntPtr]::Size -ne 4) { throw "The A9 generator must run in an x86 process." }
$environment = Get-Content -LiteralPath $EnvironmentPath -Raw | ConvertFrom-Json
if ([string]$environment.status -cne "ready" -or $null -eq $environment.accepted_provider) {
    throw "The provider environment is not ready."
}
$Provider = $environment.accepted_provider
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) { throw "The A9 output root already exists." }
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null

$status = "failed"
$detail = ""
$exitCode = 1
try {
    for ($replica = 1; $replica -le $Replicas; $replica++) { Invoke-Replica -Replica $replica }
    $status = "complete"
    $detail = "All replicas completed."
    $exitCode = 0
}
catch {
    $detail = $_.Exception.GetType().FullName + ": " + $_.Exception.Message
    [Console]::Error.WriteLine("FAIL: " + $detail)
}
finally {
    Write-JsonDocument -Path (Join-Path $OutputRoot "manifest.raw.json") -Document ([ordered]@{
        document_type = "dao_allocation_a9_manifest"
        issue = 99
        source_revision = $SourceRevision
        status = $status
        detail = $detail
        provider = [ordered]@{
            prog_id = [string]$Provider.prog_id
            provider_version = [string]$Provider.provider_version
            server_file_version = [string]$Provider.server_file_version
            server_sha256 = [string]$Provider.server_sha256
        }
        replica_count = $Replicas
        memo_marker_hex = ("{0:x2}" -f $MemoMarkerByte) * 64
        checkpoints = @($Checkpoints)
    })
}
exit $exitCode
