Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A1ProgressMaximumBytes = 1MB

function Get-A1ProgressPath {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )

    $root = [IO.Path]::GetFullPath($WorkingRoot)
    return (Join-Path $root ("replica-{0:D2}.progress.jsonl" -f $ReplicaOrdinal))
}

function Assert-A1ProgressFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )

    $expected = Get-A1ProgressPath -WorkingRoot $WorkingRoot `
        -ReplicaOrdinal $ReplicaOrdinal
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "A1 progress path differs from its private worker binding."
    }
    Assert-M1NoReparseComponents -Path $full
    $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -gt $script:A1ProgressMaximumBytes) {
        throw "A1 progress file violates its file or byte bound."
    }
    return $full
}

function New-A1ProgressFile {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )

    $root = [IO.Path]::GetFullPath($WorkingRoot)
    Assert-M1NoReparseComponents -Path $root
    $path = Get-A1ProgressPath -WorkingRoot $root `
        -ReplicaOrdinal $ReplicaOrdinal
    $stream = New-Object IO.FileStream(
        $path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough
    )
    try { $stream.Flush($true) }
    finally { $stream.Dispose() }
    return $path
}

function Open-A1WorkerProgress {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )

    $path = Get-A1ProgressPath -WorkingRoot $WorkingRoot `
        -ReplicaOrdinal $ReplicaOrdinal
    $path = Assert-A1ProgressFile -Path $path -WorkingRoot $WorkingRoot `
        -ReplicaOrdinal $ReplicaOrdinal
    return [pscustomobject]@{
        Path = $path
        Clock = [Diagnostics.Stopwatch]::StartNew()
    }
}

function Add-A1ProgressRecord {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Progress,
        [Parameter(Mandatory = $true)][string]$CheckpointId,
        [Parameter(Mandatory = $true)][long]$PageCount
    )

    $document = [ordered]@{
        checkpoint_id = $CheckpointId
        elapsed_seconds = [Math]::Round(
            [double]$Progress.Clock.Elapsed.TotalSeconds, 3
        )
        page_count = $PageCount
    }
    $json = $document | ConvertTo-Json -Depth 3 -Compress
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json + "`n")
    if ($bytes.Length -gt 4096) {
        throw "A1 checkpoint progress record exceeds its line bound."
    }
    $stream = New-Object IO.FileStream(
        $Progress.Path, [IO.FileMode]::Open, [IO.FileAccess]::Write,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete), 4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        if ($stream.Length -gt ($script:A1ProgressMaximumBytes - $bytes.Length)) {
            throw "A1 checkpoint progress exceeds its byte ceiling."
        }
        [void]$stream.Seek(0, [IO.SeekOrigin]::End)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}

function Copy-A1ProgressFile {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$ReplicaOrdinal
    )

    Assert-M1NoReparseComponents -Path $SourcePath
    $sourceItem = Get-Item -LiteralPath $SourcePath -Force
    if ($sourceItem.PSIsContainer -or
        ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $sourceItem.Length -gt $script:A1ProgressMaximumBytes) {
        throw "A1 retained progress source violates its byte or file bound."
    }
    $diagnostics = [IO.Path]::GetFullPath($DiagnosticsRoot)
    Assert-M1NoReparseComponents -Path $diagnostics
    $diagnosticsItem = Get-Item -LiteralPath $diagnostics -Force
    if (-not $diagnosticsItem.PSIsContainer -or
        ($diagnosticsItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "A1 diagnostics root is not a bounded ordinary directory."
    }
    $retained = [long]0
    foreach ($path in [IO.Directory]::EnumerateFiles(
        $diagnostics, "replica-*.progress.jsonl", [IO.SearchOption]::TopDirectoryOnly
    )) {
        Assert-M1NoReparseComponents -Path $path
        $retained += [long](Get-Item -LiteralPath $path -Force).Length
    }
    if ($sourceItem.Length -gt ($script:A1ProgressMaximumBytes - $retained)) {
        throw "A1 retained progress exceeds its aggregate byte ceiling."
    }
    $destination = Join-Path $diagnostics `
        ("replica-{0:D2}.progress.jsonl" -f $ReplicaOrdinal)
    $input = New-Object IO.FileStream(
        $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete), 4096,
        [IO.FileOptions]::SequentialScan
    )
    $output = $null
    try {
        $output = New-Object IO.FileStream(
            $destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough
        )
        $buffer = New-Object byte[] 4096
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $output.Write($buffer, 0, $read)
        }
        $output.Flush($true)
    }
    finally {
        if ($null -ne $output) { $output.Dispose() }
        $input.Dispose()
    }
    return $destination
}
