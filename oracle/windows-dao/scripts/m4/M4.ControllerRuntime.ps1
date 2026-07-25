Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-M4ExactRemoteCommit {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$RepositoryUrl,
        [Parameter(Mandatory = $true)][string]$RemoteRef
    )

    $origin = @(
        & $Context.GitExecutable -C $Context.RepositoryRoot `
            remote get-url origin 2>&1
    )
    if (
        $LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne $RepositoryUrl
    ) {
        throw "M4 origin differs from the checked private repository."
    }
    $savedPrompt = [Environment]::GetEnvironmentVariable(
        "GIT_TERMINAL_PROMPT", "Process"
    )
    $savedInteractive = [Environment]::GetEnvironmentVariable(
        "GCM_INTERACTIVE", "Process"
    )
    try {
        [Environment]::SetEnvironmentVariable(
            "GIT_TERMINAL_PROMPT", "0", "Process"
        )
        [Environment]::SetEnvironmentVariable(
            "GCM_INTERACTIVE", "Never", "Process"
        )
        $captured = Invoke-BoundedChildProcess `
            -Executable $Context.GitExecutable `
            -Arguments @(
                "-c", "credential.interactive=never",
                "-c", "core.askPass=",
                "-C", $Context.RepositoryRoot,
                "ls-remote", "--heads", $RepositoryUrl, $RemoteRef
            ) `
            -CallerLabel "M4 remote binding" `
            -TimeoutSeconds 30 `
            -MaximumOutputBytes 64KB
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "GIT_TERMINAL_PROMPT", $savedPrompt, "Process"
        )
        [Environment]::SetEnvironmentVariable(
            "GCM_INTERACTIVE", $savedInteractive, "Process"
        )
    }
    $remote = @(
        [string]$captured.stdout -split "\r?\n" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if (
        $remote.Count -ne 1 -or
        -not ([string]$remote[0]).StartsWith(
            $Context.GitCommit + "`t",
            [StringComparison]::Ordinal
        )
    ) {
        throw "M4 requires the exact clean pushed commit at the checked ref."
    }
}

function Get-M4WorkerPowerShellBinding {
    if (
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop"
    ) {
        throw "M4 workers require the current x86 Windows PowerShell."
    }
    $process = [Diagnostics.Process]::GetCurrentProcess()
    $currentPath = [IO.Path]::GetFullPath($process.MainModule.FileName)
    if (
        [IO.Path]::GetFileName($currentPath) -cne "powershell.exe" -or
        $currentPath.StartsWith("\\", [StringComparison]::Ordinal) -or
        $currentPath.Substring(2).Contains(":")
    ) {
        throw "M4 worker PowerShell identity is not a canonical local executable."
    }
    Assert-M1NoReparseComponents -Path $currentPath
    $item = Get-Item -LiteralPath $currentPath -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "M4 worker PowerShell is not an ordinary executable."
    }
    $stream = New-Object IO.FileStream(
        $currentPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        65536,
        [IO.FileOptions]::SequentialScan
    )
    try {
        return [pscustomobject]@{
            Path = $currentPath
            Length = [long]$stream.Length
            Sha256 = Get-M1StreamSha256 -Stream $stream
            Stream = $stream
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Assert-M4WorkerPowerShellBinding {
    param([Parameter(Mandatory = $true)][pscustomobject]$Binding)

    Assert-M1NoReparseComponents -Path $Binding.Path
    $item = Get-Item -LiteralPath $Binding.Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$item.Length -ne [long]$Binding.Length -or
        -not $Binding.Stream.CanRead -or
        (Get-M1StreamSha256 -Stream $Binding.Stream) -cne
            [string]$Binding.Sha256
    ) {
        throw "M4 worker PowerShell identity changed before launch."
    }
}

function Invoke-M4ContractCommand {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )

    [void](Invoke-BoundedChildProcess `
        -Executable $Context.PythonPath `
        -Arguments (@("-B", $ContractPath) + $Arguments) `
        -CallerLabel $Label `
        -TimeoutSeconds 120 `
        -MaximumOutputBytes 1MB)
}

function Invoke-M4PhaseWorker {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$PowerShellBinding,
        [Parameter(Mandatory = $true)][string]$WorkerPath,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$InvocationPath,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    Assert-M4WorkerPowerShellBinding -Binding $PowerShellBinding
    [void](Invoke-BoundedChildProcess `
        -Executable $PowerShellBinding.Path `
        -Arguments @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", $WorkerPath,
            "-BundleRoot", $BundleRoot,
            "-InvocationPath", $InvocationPath
        ) `
        -CallerLabel "M4 phase" `
        -TimeoutSeconds $TimeoutSeconds `
        -MaximumOutputBytes 1MB)
}

function Invoke-M4CheckedPhase {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)]
        [ValidateSet("creator", "reopen")][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$WorkerPath,
        [Parameter(Mandatory = $true)][pscustomobject]$PowerShellBinding,
        [AllowNull()][pscustomobject]$PriorResult,
        [AllowNull()][pscustomobject]$CloneBinding
    )

    $paths = Get-M4PhasePaths `
        -SampleId $Sample.sample_id -PhaseId $PhaseId
    $invocation = New-M4PhaseInvocation `
        -Context $Context -Session $Session -Plan $Plan `
        -Sample $Sample -Condition $Condition -PhaseId $PhaseId `
        -WorkerOrdinal $WorkerOrdinal -PlanSha256 $PlanSha256 `
        -DatabasePath $DatabasePath `
        -CloneBinding $CloneBinding
    Write-M4BundleJson -Session $Session -Entries $Entries `
        -RelativePath $paths.invocation `
        -Role "phase_invocation" -Document $invocation
    $invocationPath = Get-M1PayloadPath `
        -Session $Session -RelativePath $paths.invocation
    Invoke-M4ContractCommand -Context $Context `
        -ContractPath $ContractPath `
        -Arguments @(
            "validate-invocation", "--bundle-root",
            $Session.StagingBundle, "--invocation", $invocationPath
        ) -Label "M4 $PhaseId invocation validation"
    Invoke-M4PhaseWorker -PowerShellBinding $PowerShellBinding `
        -WorkerPath $WorkerPath `
        -BundleRoot $Session.StagingBundle `
        -InvocationPath $invocationPath `
        -TimeoutSeconds ([int]$Plan.bounds.worker_timeout_seconds)
    $resultPath = Get-M1PayloadPath `
        -Session $Session -RelativePath $paths.result
    Invoke-M4ContractCommand -Context $Context `
        -ContractPath $ContractPath `
        -Arguments @(
            "validate-result", "--bundle-root",
            $Session.StagingBundle, "--result", $resultPath
        ) -Label "M4 $PhaseId result validation"
    $result = (Read-M4BundleJson -Path $resultPath).document
    Assert-M4PhaseResult -Result $result `
        -Invocation ([pscustomobject]$invocation) `
        -PriorResult $PriorResult
    Register-M4PhaseArtifacts -Session $Session -Entries $Entries `
        -Paths $paths -DatabasePath $DatabasePath -Result $result
    return $result
}
