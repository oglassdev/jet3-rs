Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:A2ProgressMaximumBytes = 1MB

function Get-A2ProgressPath {
    param(
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
    )
    $root = [IO.Path]::GetFullPath($DiagnosticsRoot)
    return Join-Path (Join-Path $root "progress") `
        ("replica-{0:D2}.jsonl" -f $Replica)
}
function New-A2ProgressFile {
    param(
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
    )
    $root = [IO.Path]::GetFullPath($DiagnosticsRoot)
    Assert-M1NoReparseComponents -Path $root
    $progressRoot = Join-Path $root "progress"
    [void][IO.Directory]::CreateDirectory($progressRoot)
    Assert-M1NoReparseComponents -Path $progressRoot
    $path = Get-A2ProgressPath -DiagnosticsRoot $root -Replica $Replica
    $stream = New-Object IO.FileStream(
        $path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough
    )
    try { $stream.Flush($true) }
    finally { $stream.Dispose() }
    return $path
}

function Open-A2WorkerProgress {
    param(
        [Parameter(Mandatory = $true)][string]$DiagnosticsRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3)][int]$Replica
    )
    $path = Get-A2ProgressPath -DiagnosticsRoot $DiagnosticsRoot `
        -Replica $Replica
    Assert-M1NoReparseComponents -Path $path
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -gt $script:A2ProgressMaximumBytes) {
        throw "A2 progress file violates its file or byte bound."
    }
    return [pscustomobject]@{
        Path = $item.FullName
        Clock = [Diagnostics.Stopwatch]::StartNew()
    }
}

function Add-A2ProgressRecord {
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
        throw "A2 progress record exceeds its line bound."
    }
    $stream = New-Object IO.FileStream(
        $Progress.Path, [IO.FileMode]::Open, [IO.FileAccess]::Write,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete), 4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        if ($stream.Length -gt
            ($script:A2ProgressMaximumBytes - $bytes.Length)) {
            throw "A2 progress file exceeds its byte ceiling."
        }
        [void]$stream.Seek(0, [IO.SeekOrigin]::End)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}
