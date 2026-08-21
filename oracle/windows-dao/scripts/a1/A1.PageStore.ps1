Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A1PageBytes = 2048L
$script:A1MaximumPagesPerReplica = 20480L
$script:A1MaximumUniqueBlobs = 262144L
$script:A1MaximumPageStoreBytes = 512MB
$script:A1MaximumChangedEntries = 1500000L
$script:A1MaximumLogicalReadBytes = 8GB
$script:A1StrictUtf8 = New-Object Text.UTF8Encoding($false, $true)

function Read-A1CheckedJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )

    Assert-M1NoReparseComponents -Path $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -lt 2 -or
        $item.Length -gt $MaximumBytes) {
        throw "A1 checked JSON input violates its file bound."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length -or
        ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
            $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf)) {
        throw "A1 checked JSON input changed or contains a BOM."
    }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    return [pscustomobject]@{
        bytes = $bytes
        document = ($text | ConvertFrom-Json)
        sha256 = Get-A1LowerSha256 -Bytes $bytes
    }
}

function Get-A1LowerSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString(
            $hash.ComputeHash($Bytes)
        )).Replace("-", "").ToLowerInvariant()
    }
    finally { $hash.Dispose() }
}

function New-A1SemanticSha256 {
    return [Security.Cryptography.SHA256]::Create()
}

function Add-A1SemanticHashRow {
    param(
        [Parameter(Mandatory = $true)]
        [Security.Cryptography.HashAlgorithm]$Hash,
        [Parameter(Mandatory = $true)][int]$Id,
        [Parameter(Mandatory = $true)][string]$Payload
    )

    if (-not [BitConverter]::IsLittleEndian) {
        throw "A1 rolling hash requires an explicitly little-endian host."
    }
    $payloadBytes = $script:A1StrictUtf8.GetBytes($Payload)
    if ($payloadBytes.Length -gt [uint16]::MaxValue) {
        throw "A1 semantic payload exceeds its rolling-hash length field."
    }
    # tables.row_algorithm.rolling_sha256 fixes this exact byte encoding;
    # one reusable SHA256 instance preserves it across every ordered row.
    foreach ($part in @(
        [BitConverter]::GetBytes([int]$Id),
        [BitConverter]::GetBytes([uint16]$payloadBytes.Length),
        $payloadBytes
    )) {
        [void]$Hash.TransformBlock($part, 0, $part.Length, $part, 0)
    }
}

function Complete-A1SemanticSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [Security.Cryptography.HashAlgorithm]$Hash
    )

    $empty = New-Object byte[] 0
    [void]$Hash.TransformFinalBlock($empty, 0, 0)
    return ([BitConverter]::ToString(
        $Hash.Hash
    )).Replace("-", "").ToLowerInvariant()
}

function Get-A1PageBlobLocator {
    param([Parameter(Mandatory = $true)][string]$Sha256)

    if ($Sha256 -cnotmatch "^[0-9a-f]{64}$") {
        throw "A1 page blob digest is not lowercase SHA-256."
    }
    return "page-store/$Sha256.page"
}

function Write-A1CreateNewBytes {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )

    if ($Bytes.LongLength -lt 1 -or $Bytes.LongLength -gt $MaximumBytes) {
        throw "A1 retained artifact violates its byte ceiling."
    }
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    [void][IO.Directory]::CreateDirectory($parent)
    Assert-M1NoReparseComponents -Path $parent
    $stream = New-Object IO.FileStream(
        $Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 65536, [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}

function Assert-A1ExistingPageBlob {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    Assert-M1NoReparseComponents -Path $Path
    $stream = New-Object IO.FileStream(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 2048, [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -ne $script:A1PageBytes) {
            throw "A1 page-store collision has a non-page length."
        }
        $bytes = New-Object byte[] ([int]$script:A1PageBytes)
        $read = $stream.Read($bytes, 0, $bytes.Length)
        if ($read -ne $bytes.Length -or $stream.ReadByte() -ne -1 -or
            (Get-A1LowerSha256 -Bytes $bytes) -cne $ExpectedSha256) {
            throw "A1 page-store collision does not match its digest."
        }
    }
    finally { $stream.Dispose() }
}

function Add-A1PageBlob {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Store,
        [Parameter(Mandatory = $true)][byte[]]$Page,
        [Parameter(Mandatory = $true)][string]$Sha256
    )

    if ($Page.LongLength -ne $script:A1PageBytes) {
        throw "A1 page store accepts only exact 2048-byte pages."
    }
    $locator = Get-A1PageBlobLocator -Sha256 $Sha256
    $path = Get-M1PayloadPath -Session $Store.Session -RelativePath $locator
    if (-not $Store.Seen.Contains($Sha256)) {
        if ($Store.Seen.Count -ge $script:A1MaximumUniqueBlobs -or
            $Store.UniqueBytes -gt (
                $script:A1MaximumPageStoreBytes - $script:A1PageBytes
            ) -or $Store.RetainedBytes -gt (768MB - $script:A1PageBytes)) {
            throw "A1 page store exceeded its preregistered ceiling."
        }
        if ([IO.File]::Exists($path)) {
            Assert-A1ExistingPageBlob -Path $path -ExpectedSha256 $Sha256
        }
        else {
            try {
                Write-A1CreateNewBytes -Path $path -Bytes $Page `
                    -MaximumBytes $script:A1PageBytes
            }
            catch [IO.IOException] {
                if (-not [IO.File]::Exists($path)) { throw }
                Assert-A1ExistingPageBlob -Path $path `
                    -ExpectedSha256 $Sha256
            }
        }
        [void]$Store.Seen.Add($Sha256)
        $Store.UniqueBytes = [long](
            $Store.UniqueBytes + $script:A1PageBytes
        )
        $Store.RetainedBytes = [long](
            $Store.RetainedBytes + $script:A1PageBytes
        )
    }
}

function New-A1PageStore {
    param([Parameter(Mandatory = $true)][pscustomobject]$Session)

    $seen = New-Object 'Collections.Generic.HashSet[string]' `
        ([StringComparer]::Ordinal)
    $root = Get-M1PayloadPath -Session $Session -RelativePath "page-store"
    $retained = [long]0
    $allFiles = @([IO.Directory]::EnumerateFiles(
        $Session.StagingBundle, "*", [IO.SearchOption]::AllDirectories
    ))
    if ($allFiles.Count -gt 262399) {
        throw "A1 staged bundle exceeds its entry ceiling."
    }
    foreach ($path in $allFiles) {
        Assert-M1NoReparseComponents -Path $path
        $length = [long](Get-Item -LiteralPath $path -Force).Length
        if ($length -gt (768MB - $retained)) {
            throw "A1 staged bundle exceeds its retained-byte ceiling."
        }
        $retained += $length
    }
    if ([IO.Directory]::Exists($root)) {
        $files = @([IO.Directory]::EnumerateFiles(
            $root, "*.page", [IO.SearchOption]::AllDirectories
        ))
        if ($files.Count -gt $script:A1MaximumUniqueBlobs) {
            throw "A1 existing page store exceeds its entry ceiling."
        }
        foreach ($path in $files) {
            Assert-M1NoReparseComponents -Path $path
            $name = [IO.Path]::GetFileNameWithoutExtension($path)
            $expected = Get-A1PageBlobLocator -Sha256 $name
            $actual = $path.Substring(
                $Session.StagingBundle.TrimEnd('\').Length + 1
            ).Replace('\', '/')
            if ($actual -cne $expected) {
                throw "A1 existing page-store locator is noncanonical."
            }
            Assert-A1ExistingPageBlob -Path $path -ExpectedSha256 $name
            [void]$seen.Add($name)
        }
    }
    return [pscustomobject]@{
        Session = $Session
        Seen = $seen
        UniqueBytes = [long]($seen.Count * $script:A1PageBytes)
        RetainedBytes = $retained
        LogicalReadBytes = [long]0
        ChangedEntries = [long]0
    }
}

function Set-A1ExpectedSemanticDirty {
    param([Parameter(Mandatory = $true)][string]$Role)
    [void]$script:A1ExpectedSemanticCache.Remove($Role)
}

function Get-A1ExpectedSemanticResult {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)]$Rows
    )

    if ($script:A1ExpectedSemanticCache.ContainsKey($Role)) {
        return $script:A1ExpectedSemanticCache[$Role]
    }
    $hash = New-A1SemanticSha256
    try {
        foreach ($id in @($Rows | Sort-Object)) {
            Add-A1SemanticHashRow -Hash $hash -Id ([int]$id) `
                -Payload (Get-A1Payload -Role $Role -Id ([int]$id))
        }
        # tables.row_algorithm.reread_requirement compares the same expected
        # count/digest; every role mutation invalidates this deterministic cache.
        $result = [pscustomobject]@{
            count = [int]$Rows.Count
            sha256 = Complete-A1SemanticSha256 -Hash $hash
        }
        $script:A1ExpectedSemanticCache[$Role] = $result
        return $result
    }
    finally { $hash.Dispose() }
}

function Read-A1SemanticTable {
    param(
        [Parameter(Mandatory = $true)][object]$Database,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][pscustomobject]$Expected
    )

    $name = [string]$script:A1RoleNames[$Role]
    $rows = $script:A1Rows[$Role]
    $recordset = $null
    $fields = $null
    $idField = $null
    $payloadField = $null
    $hash = $null
    $cleanup = New-Object Collections.ArrayList
    $primary = $null
    $count = 0
    $prior = 0
    $digest = $null
    try {
        $sql = "SELECT Id, Payload FROM [$name] ORDER BY Id"
        $recordset = $Database.OpenRecordset(
            $sql, $script:A1DbOpenSnapshot
        )
        # tables.row_algorithm.reread_requirement fixes Id-order values;
        # field COM objects can be cached for this recordset without changing them.
        $fields = $recordset.Fields
        $idField = $fields.Item("Id")
        $payloadField = $fields.Item("Payload")
        $hash = New-A1SemanticSha256
        if (-not $recordset.EOF) { $recordset.MoveFirst() }
        while (-not $recordset.EOF) {
            $id = [int]$idField.Value
            $payload = [string]$payloadField.Value
            if ($id -le $prior -or -not $rows.Contains($id) -or
                $payload -cne (Get-A1Payload -Role $Role -Id $id)) {
                throw "A1 DAO semantic readback differs from expected rows."
            }
            Add-A1SemanticHashRow -Hash $hash -Id $id -Payload $payload
            $prior = $id
            $count++
            $recordset.MoveNext()
        }
        $digest = Complete-A1SemanticSha256 -Hash $hash
    }
    catch { $primary = $_ }
    finally {
        if ($null -ne $hash) { $hash.Dispose() }
        Release-M1ComObject -Value $payloadField -CleanupErrors $cleanup `
            -Label "A1 payload field release"
        Release-M1ComObject -Value $idField -CleanupErrors $cleanup `
            -Label "A1 id field release"
        Release-M1ComObject -Value $fields -CleanupErrors $cleanup `
            -Label "A1 semantic fields release"
        Close-M1ComObject -Value $recordset -CleanupErrors $cleanup `
            -Label "A1 semantic recordset close"
        Release-M1ComObject -Value $recordset -CleanupErrors $cleanup `
            -Label "A1 semantic recordset release"
    }
    Complete-M1DaoHelper -PrimaryError $primary -CleanupErrors $cleanup `
        -Label "A1 semantic readback"
    if ($count -ne [int]$Expected.count -or
        $digest -cne [string]$Expected.sha256) {
        throw "A1 semantic row count or rolling digest differs from expectation."
    }
    return [ordered]@{
        role = $Role
        row_count = $count
        rolling_sha256 = $digest
    }
}

function Read-A1SemanticTables {
    $expectedByRole = @{}
    foreach ($role in @("D", "L", "P", "H")) {
        if ([bool]$script:A1Extant[$role]) {
            $expectedByRole[$role] = Get-A1ExpectedSemanticResult `
                -Role $role -Rows $script:A1Rows[$role]
        }
    }
    # tables.row_algorithm.reread_requirement still reads every extant table;
    # the closed checkpoint needs no database close between those ordered scans.
    $documents = Invoke-A1WithDatabase -Action {
        param($database)
        foreach ($role in @("D", "L", "P", "H")) {
            if ([bool]$script:A1Extant[$role]) {
                Read-A1SemanticTable -Database $database -Role $role `
                    -Expected $expectedByRole[$role]
            }
        }
    }
    return @($documents)
}

function Read-A1PageSnapshot {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Store,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [AllowNull()][string[]]$PriorHashes,
        [AllowNull()][byte[][]]$PriorPages
    )

    Assert-M1NoReparseComponents -Path $DatabasePath
    if (($null -eq $PriorHashes) -ne ($null -eq $PriorPages) -or
        ($null -ne $PriorHashes -and
            $PriorHashes.Count -ne $PriorPages.Count) -or
        ($null -ne $PriorPages -and
            $PriorPages.Count -gt $script:A1MaximumPagesPerReplica)) {
        throw "A1 prior snapshot cache is incomplete or exceeds its bound."
    }
    $stream = New-Object IO.FileStream(
        $DatabasePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None, 65536, [IO.FileOptions]::SequentialScan
    )
    $fileHash = $null
    $pageHash = $null
    try {
        if ($stream.Length -lt $script:A1PageBytes -or
            ($stream.Length % $script:A1PageBytes) -ne 0) {
            throw "A1 closed database is not an exact sequence of pages."
        }
        $pageCount = [long]($stream.Length / $script:A1PageBytes)
        if ($pageCount -gt $script:A1MaximumPagesPerReplica -or
            $stream.Length -gt (
                $script:A1MaximumLogicalReadBytes - $Store.LogicalReadBytes
            )) {
            throw "A1 snapshot exceeded its page or logical-read ceiling."
        }
        $hashes = New-Object 'Collections.Generic.List[string]' `
            ([int]$pageCount)
        $pages = New-Object 'Collections.Generic.List[byte[]]' `
            ([int]$pageCount)
        $changed = New-Object Collections.ArrayList
        $fileHash = [Security.Cryptography.SHA256]::Create()
        $pageHash = [Security.Cryptography.SHA256]::Create()
        $pageComparer = `
            [Collections.StructuralComparisons]::StructuralEqualityComparer
        $page = New-Object byte[] ([int]$script:A1PageBytes)
        for ($index = 0L; $index -lt $pageCount; $index++) {
            $offset = 0
            while ($offset -lt $page.Length) {
                $read = $stream.Read($page, $offset, $page.Length - $offset)
                if ($read -le 0) { throw "A1 page read ended early." }
                $offset += $read
            }
            [void]$fileHash.TransformBlock(
                $page, 0, $page.Length, $page, 0
            )
            # page-index.schema database_sha256 is streamed during the same
            # page_capture.checkpoint_representation read.
            $unchanged = $false
            if ($null -ne $PriorPages -and $index -lt $PriorPages.Count) {
                $priorPage = [byte[]]$PriorPages[[int]$index]
                if ($priorPage.LongLength -ne $script:A1PageBytes) {
                    throw "A1 prior snapshot cache contains a non-page."
                }
                # page_capture.checkpoint_representation requires exact ordered
                # hashes; byte equality alone permits reuse of the prior digest.
                $unchanged = $pageComparer.Equals($page, $priorPage)
            }
            if ($unchanged) {
                $sha = [string]$PriorHashes[[int]$index]
                [void]$pages.Add($priorPage)
            }
            else {
                $sha = ([BitConverter]::ToString(
                    $pageHash.ComputeHash($page)
                )).Replace("-", "").ToLowerInvariant()
                # page_capture.store uses this digest for the exact blob name.
                Add-A1PageBlob -Store $Store -Page $page -Sha256 $sha
                $retainedPage = New-Object byte[] ([int]$script:A1PageBytes)
                [Buffer]::BlockCopy(
                    $page, 0, $retainedPage, 0, $retainedPage.Length
                )
                [void]$pages.Add($retainedPage)
            }
            $hashes.Add($sha)
            # page-index.schema changed_page_indices is the ordered-hash delta.
            if ($null -eq $PriorHashes -or $index -ge $PriorHashes.Count -or
                $PriorHashes[[int]$index] -cne $sha) {
                if ($Store.ChangedEntries + $changed.Count -ge
                    $script:A1MaximumChangedEntries) {
                    throw "A1 changed-page index exceeded its ceiling."
                }
                [void]$changed.Add([ordered]@{
                    page_index = $index
                    sha256 = $sha
                })
            }
        }
        if ($null -ne $PriorHashes -and $PriorHashes.Count -gt $pageCount) {
            for ($index = $pageCount; $index -lt $PriorHashes.Count; $index++) {
                if ($Store.ChangedEntries + $changed.Count -ge
                    $script:A1MaximumChangedEntries) {
                    throw "A1 changed-page index exceeded its ceiling."
                }
                [void]$changed.Add([ordered]@{
                    page_index = [long]$index
                    sha256 = $null
                })
            }
        }
        if ($stream.ReadByte() -ne -1) {
            throw "A1 database grew during its exclusive snapshot."
        }
        $empty = New-Object byte[] 0
        [void]$fileHash.TransformFinalBlock($empty, 0, 0)
        $Store.LogicalReadBytes = [long](
            $Store.LogicalReadBytes + $stream.Length
        )
        $Store.ChangedEntries = [long](
            $Store.ChangedEntries + $changed.Count
        )
        return [pscustomobject]@{
            file_bytes = [long]$stream.Length
            file_sha256 = ([BitConverter]::ToString(
                $fileHash.Hash
            )).Replace("-", "").ToLowerInvariant()
            page_count = $pageCount
            changed_pages = @($changed)
            hashes = $hashes.ToArray()
            pages = $pages.ToArray()
        }
    }
    finally {
        if ($null -ne $pageHash) { $pageHash.Dispose() }
        if ($null -ne $fileHash) { $fileHash.Dispose() }
        $stream.Dispose()
    }
}
