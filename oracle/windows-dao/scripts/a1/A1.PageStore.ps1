Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A1PageBytes = 2048L
$script:A1MaximumPagesPerReplica = 20480L
$script:A1MaximumUniqueBlobs = 262144L
$script:A1MaximumPageStoreBytes = 512MB
$script:A1MaximumChangedEntries = 1500000L
$script:A1MaximumLogicalReadBytes = 8GB
$script:A1StrictUtf8 = New-Object Text.UTF8Encoding($false, $true)

function Initialize-A1PageSnapshotNative {
    if ("Jet3A1PageSnapshotNative" -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

public sealed class Jet3A1PageSnapshotResult
{
    public byte[] Bytes;
    public string FileSha256;
    public string[] PageSha256;
    public long[] ChangedIndices;
    public byte[][] ChangedPages;
}

public sealed class Jet3A1PageStoreInventoryResult
{
    public HashSet<string> Seen;
    public long RetainedBytes;
}

public static class Jet3A1PageSnapshotNative
{
    private static string LowerHex(byte[] value)
    {
        StringBuilder text = new StringBuilder(value.Length * 2);
        for (int index = 0; index < value.Length; index++)
        {
            text.Append(value[index].ToString("x2"));
        }
        return text.ToString();
    }

    private static bool IsLowerSha256(string value)
    {
        if (value == null || value.Length != 64) return false;
        for (int index = 0; index < value.Length; index++)
        {
            char current = value[index];
            if (!((current >= '0' && current <= '9') ||
                  (current >= 'a' && current <= 'f'))) return false;
        }
        return true;
    }

    private static bool PageEquals(
        byte[] current, int currentOffset, byte[] prior, int priorOffset,
        int pageBytes)
    {
        for (int offset = 0; offset < pageBytes; offset++)
        {
            if (current[currentOffset + offset] != prior[priorOffset + offset])
                return false;
        }
        return true;
    }

    public static Jet3A1PageStoreInventoryResult Inventory(
        string bundleRoot, string pageRoot, int maximumEntries,
        int maximumPages, long maximumBytes, int pageBytes)
    {
        bundleRoot = Path.GetFullPath(bundleRoot).TrimEnd(
            Path.DirectorySeparatorChar);
        pageRoot = Path.GetFullPath(pageRoot).TrimEnd(
            Path.DirectorySeparatorChar);
        if ((File.GetAttributes(bundleRoot) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("Bundle root is a reparse point.");
        Stack<string> pending = new Stack<string>();
        pending.Push(bundleRoot);
        HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
        long retained = 0;
        int entries = 0;
        while (pending.Count != 0)
        {
            string directory = pending.Pop();
            foreach (string child in Directory.EnumerateDirectories(
                directory, "*", SearchOption.TopDirectoryOnly))
            {
                if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Bundle directory is a reparse point.");
                pending.Push(child);
            }
            foreach (string path in Directory.EnumerateFiles(
                directory, "*", SearchOption.TopDirectoryOnly))
            {
                FileInfo item = new FileInfo(path);
                entries++;
                if (entries > maximumEntries || item.Length < 0 ||
                    item.Length > maximumBytes - retained ||
                    (item.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Bundle inventory exceeds its bound.");
                retained += item.Length;
                if (!path.StartsWith(
                        pageRoot + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase) ||
                    !path.EndsWith(".page", StringComparison.OrdinalIgnoreCase))
                    continue;
                string digest = Path.GetFileNameWithoutExtension(path);
                string expected = Path.Combine(pageRoot, digest + ".page");
                if (!path.Equals(expected, StringComparison.Ordinal) ||
                    !IsLowerSha256(digest) || item.Length != pageBytes ||
                    !seen.Add(digest) || seen.Count > maximumPages)
                    throw new InvalidDataException(
                        "Existing page-store entry is noncanonical.");
            }
        }
        return new Jet3A1PageStoreInventoryResult {
            Seen = seen,
            RetainedBytes = retained
        };
    }

    public static Jet3A1PageSnapshotResult Capture(
        string path, int pageBytes, int maximumPages, long maximumBytes,
        byte[] priorBytes, string[] priorHashes)
    {
        if (pageBytes < 1 || maximumPages < 1 || maximumBytes < pageBytes)
            throw new ArgumentOutOfRangeException("page snapshot bound");
        if ((priorBytes == null) != (priorHashes == null))
            throw new InvalidDataException("Prior snapshot cache is incomplete.");
        if (priorBytes != null)
        {
            if (priorBytes.Length % pageBytes != 0 ||
                priorBytes.Length / pageBytes != priorHashes.Length ||
                priorHashes.Length > maximumPages)
                throw new InvalidDataException("Prior snapshot cache is invalid.");
            foreach (string digest in priorHashes)
            {
                if (!IsLowerSha256(digest))
                    throw new InvalidDataException("Prior page digest is invalid.");
            }
        }

        byte[] bytes;
        using (FileStream stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.None, 1048576,
            FileOptions.SequentialScan))
        {
            long length = stream.Length;
            if (length < pageBytes || length % pageBytes != 0 ||
                length / pageBytes > maximumPages || length > maximumBytes ||
                length > Int32.MaxValue)
                throw new InvalidDataException("Page snapshot size is invalid.");
            bytes = new byte[(int)length];
            int offset = 0;
            while (offset < bytes.Length)
            {
                int read = stream.Read(bytes, offset, bytes.Length - offset);
                if (read <= 0)
                    throw new EndOfStreamException("Page snapshot ended early.");
                offset += read;
            }
            if (stream.ReadByte() != -1 || stream.Length != length)
                throw new IOException("Page snapshot changed during capture.");
        }

        int pageCount = bytes.Length / pageBytes;
        int priorCount = priorBytes == null ? 0 : priorHashes.Length;
        string[] hashes = new string[pageCount];
        List<long> changed = new List<long>();
        List<byte[]> changedPages = new List<byte[]>();
        string fileDigest;
        using (SHA256 hash = SHA256.Create())
        {
            // page_capture.checkpoint_representation fixes these exact bytes
            // and ordered page digests, independent of loop implementation.
            fileDigest = LowerHex(hash.ComputeHash(bytes));
            for (int index = 0; index < pageCount; index++)
            {
                int pageOffset = index * pageBytes;
                bool unchanged = index < priorCount && PageEquals(
                    bytes, pageOffset, priorBytes, pageOffset, pageBytes);
                if (unchanged)
                {
                    hashes[index] = priorHashes[index];
                    continue;
                }
                hashes[index] = LowerHex(
                    hash.ComputeHash(bytes, pageOffset, pageBytes));
                // changed_page_indices is the ordered-digest delta.
                if (index < priorCount && hashes[index].Equals(
                    priorHashes[index], StringComparison.Ordinal))
                    continue;
                byte[] page = new byte[pageBytes];
                Buffer.BlockCopy(bytes, pageOffset, page, 0, pageBytes);
                changed.Add(index);
                changedPages.Add(page);
            }
        }
        for (int index = pageCount; index < priorCount; index++)
        {
            changed.Add(index);
            changedPages.Add(null);
        }
        return new Jet3A1PageSnapshotResult {
            Bytes = bytes,
            FileSha256 = fileDigest,
            PageSha256 = hashes,
            ChangedIndices = changed.ToArray(),
            ChangedPages = changedPages.ToArray()
        };
    }
}
'@
}

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
    if ($Store.Seen.Contains($Sha256)) {
        if (-not [IO.File]::Exists($path)) {
            throw "A1 previously seen page-store blob is missing."
        }
        if ([long](Get-Item -LiteralPath $path -Force).Length -ne
            $script:A1PageBytes) {
            throw "A1 previously seen page-store blob has a non-page length."
        }
    }
    else {
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

    $root = Get-M1PayloadPath -Session $Session -RelativePath "page-store"
    Assert-M1NoReparseComponents -Path $Session.StagingBundle
    Initialize-A1PageSnapshotNative
    # page_capture.store binds canonical content-addressed blobs; the complete
    # validator rehashes every blob before publication.
    $inventory = [Jet3A1PageSnapshotNative]::Inventory(
        $Session.StagingBundle, $root, 262399,
        [int]$script:A1MaximumUniqueBlobs, 768MB,
        [int]$script:A1PageBytes
    )
    return [pscustomobject]@{
        Session = $Session
        Seen = $inventory.Seen
        UniqueBytes = [long]($inventory.Seen.Count * $script:A1PageBytes)
        RetainedBytes = [long]$inventory.RetainedBytes
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
        [AllowNull()][byte[]]$PriorPages
    )

    Assert-M1NoReparseComponents -Path $DatabasePath
    if (($null -eq $PriorHashes) -ne ($null -eq $PriorPages) -or
        ($null -ne $PriorHashes -and
            $PriorHashes.Count -ne
                ($PriorPages.LongLength / $script:A1PageBytes)) -or
        ($null -ne $PriorPages -and
            ($PriorPages.LongLength % $script:A1PageBytes) -ne 0) -or
        ($null -ne $PriorPages -and
            ($PriorPages.LongLength / $script:A1PageBytes) -gt
                $script:A1MaximumPagesPerReplica)) {
        throw "A1 prior snapshot cache is incomplete or exceeds its bound."
    }
    Initialize-A1PageSnapshotNative
    $remaining = [long](
        $script:A1MaximumLogicalReadBytes - $Store.LogicalReadBytes
    )
    $capture = [Jet3A1PageSnapshotNative]::Capture(
        $DatabasePath, [int]$script:A1PageBytes,
        [int]$script:A1MaximumPagesPerReplica, $remaining,
        $PriorPages, $PriorHashes
    )
    if ($Store.ChangedEntries + $capture.ChangedIndices.Length -gt
        $script:A1MaximumChangedEntries) {
        throw "A1 changed-page index exceeded its ceiling."
    }
    $changed = New-Object Collections.ArrayList
    for ($offset = 0; $offset -lt $capture.ChangedIndices.Length; $offset++) {
        $index = [long]$capture.ChangedIndices[$offset]
        $sha = if ($index -lt $capture.PageSha256.Length) {
            [string]$capture.PageSha256[[int]$index]
        } else { $null }
        if ($null -ne $sha) {
            # page_capture.store keeps the exact changed page under its digest.
            Add-A1PageBlob -Store $Store `
                -Page ([byte[]]$capture.ChangedPages[$offset]) -Sha256 $sha
        }
        [void]$changed.Add([ordered]@{
            page_index = $index
            sha256 = $sha
        })
    }
    $Store.LogicalReadBytes = [long](
        $Store.LogicalReadBytes + $capture.Bytes.LongLength
    )
    $Store.ChangedEntries = [long](
        $Store.ChangedEntries + $changed.Count
    )
    return [pscustomobject]@{
        file_bytes = [long]$capture.Bytes.LongLength
        file_sha256 = [string]$capture.FileSha256
        page_count = [long]$capture.PageSha256.Length
        changed_pages = @($changed)
        hashes = [string[]]$capture.PageSha256
        pages = [byte[]]$capture.Bytes
    }
}
