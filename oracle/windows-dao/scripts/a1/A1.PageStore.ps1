Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A1PageBytes = 2048L
$script:A1MaximumPagesPerReplica = 20480L
$script:A1MaximumUniqueBlobs = 262144L
$script:A1MaximumPageStoreBytes = 512MB
$script:A1MaximumChangedEntries = 1500000L
$script:A1MaximumLogicalReadBytes = 8GB

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
        [Parameter(Mandatory = $true)][byte[]]$Page
    )

    if ($Page.LongLength -ne $script:A1PageBytes) {
        throw "A1 page store accepts only exact 2048-byte pages."
    }
    $sha = Get-A1LowerSha256 -Bytes $Page
    $locator = Get-A1PageBlobLocator -Sha256 $sha
    $path = Get-M1PayloadPath -Session $Store.Session -RelativePath $locator
    if (-not $Store.Seen.Contains($sha)) {
        if ($Store.Seen.Count -ge $script:A1MaximumUniqueBlobs -or
            $Store.UniqueBytes -gt (
                $script:A1MaximumPageStoreBytes - $script:A1PageBytes
            ) -or $Store.RetainedBytes -gt (768MB - $script:A1PageBytes)) {
            throw "A1 page store exceeded its preregistered ceiling."
        }
        if ([IO.File]::Exists($path)) {
            Assert-A1ExistingPageBlob -Path $path -ExpectedSha256 $sha
        }
        else {
            try {
                Write-A1CreateNewBytes -Path $path -Bytes $Page `
                    -MaximumBytes $script:A1PageBytes
            }
            catch [IO.IOException] {
                if (-not [IO.File]::Exists($path)) { throw }
                Assert-A1ExistingPageBlob -Path $path -ExpectedSha256 $sha
            }
        }
        [void]$Store.Seen.Add($sha)
        $Store.UniqueBytes = [long](
            $Store.UniqueBytes + $script:A1PageBytes
        )
        $Store.RetainedBytes = [long](
            $Store.RetainedBytes + $script:A1PageBytes
        )
    }
    return $sha
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

function Read-A1PageSnapshot {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Store,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [AllowNull()][string[]]$PriorHashes
    )

    Assert-M1NoReparseComponents -Path $DatabasePath
    $stream = New-Object IO.FileStream(
        $DatabasePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None, 65536, [IO.FileOptions]::SequentialScan
    )
    $fileHash = $null
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
        $changed = New-Object Collections.ArrayList
        $page = New-Object byte[] ([int]$script:A1PageBytes)
        $fileHash = [Security.Cryptography.SHA256]::Create()
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
            $sha = Add-A1PageBlob -Store $Store -Page $page
            $hashes.Add($sha)
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
        }
    }
    finally {
        if ($null -ne $fileHash) { $fileHash.Dispose() }
        $stream.Dispose()
    }
}
