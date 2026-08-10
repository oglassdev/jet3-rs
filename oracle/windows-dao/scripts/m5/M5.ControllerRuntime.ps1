Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:M5HardProcessTimeoutSeconds = 120

function Invoke-M5Contract {
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
        -TimeoutSeconds $script:M5HardProcessTimeoutSeconds `
        -MaximumOutputBytes 1MB)
}

function Assert-M5ExactRemoteCommit {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan
    )
    $origin = @(& $Context.GitExecutable -C $Context.RepositoryRoot `
        remote get-url origin 2>&1)
    if ($LASTEXITCODE -ne 0 -or $origin.Count -ne 1 -or
        [string]$origin[0] -cne [string]$Plan.repository_url) {
        throw "M5 origin differs from its repository binding."
    }
    $captured = Invoke-BoundedChildProcess `
        -Executable $Context.GitExecutable `
        -Arguments @(
            "-c", "credential.interactive=never", "-c", "core.askPass=",
            "-C", $Context.RepositoryRoot, "ls-remote", "--heads",
            [string]$Plan.repository_url, [string]$Plan.remote_ref
        ) -CallerLabel "M5 exact remote binding" `
        -TimeoutSeconds 30 -MaximumOutputBytes 64KB
    $lines = @([string]$captured.stdout -split "\r?\n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1 -or -not $lines[0].StartsWith(
        $Context.GitCommit + "`t", [StringComparison]::Ordinal
    )) { throw "M5 requires the exact pushed commit at its checked ref." }
}

function Get-M5WorkerPowerShell {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 4 -or
        $PSVersionTable.PSEdition -cne "Desktop") {
        throw "M5 controller and workers require x86 Windows PowerShell."
    }
    $path = [IO.Path]::GetFullPath(
        [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    )
    Assert-M1NoReparseComponents -Path $path
    $stream = New-Object IO.FileStream(
        $path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan
    )
    try {
        return [pscustomobject]@{
            Path = $path
            Bytes = [long]$stream.Length
            Sha256 = Get-M1StreamSha256 -Stream $stream
            Stream = $stream
        }
    }
    catch { $stream.Dispose(); throw }
}

function Assert-M5WorkerPowerShell {
    param([Parameter(Mandatory = $true)][pscustomobject]$Binding)
    Assert-M1NoReparseComponents -Path $Binding.Path
    $item = Get-Item -LiteralPath $Binding.Path -Force
    if ($item.PSIsContainer -or [long]$item.Length -ne $Binding.Bytes -or
        -not $Binding.Stream.CanRead -or
        (Get-M1StreamSha256 -Stream $Binding.Stream) -cne $Binding.Sha256) {
        throw "M5 worker PowerShell binding changed before launch."
    }
}

function Assert-M5M4BundleReadOnly {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][string]$M4BundleRoot,
        [Parameter(Mandatory = $true)][string]$M4ContractPath
    )
    $root = [IO.Path]::GetFullPath($M4BundleRoot)
    Assert-M1NoReparseComponents -Path $root
    $manifestPath = Join-Path $root "bundle-manifest.json"
    $input = Read-M5Json -Path $manifestPath -MaximumBytes 1MB
    $sha = Get-M1ByteArraySha256 -Bytes $input.bytes
    if ($sha -cne $script:M5ExpectedM4ManifestSha256 -or
        [string]$input.document.experiment_id -cne
            "DAO-M4-HEADER-DISCRIMINATOR-003" -or
        [string]$input.document.producer_commit -cne
            "35f5f55f0b7277fc07831db540eab7fa69a41a20" -or
        [string]$input.document.run_id -cne
            "20260810T220332Z-m4-r2") {
        throw "M5 M4 input identity differs from the immutable binding."
    }
    Invoke-M5Contract -Context $Context -ContractPath $M4ContractPath `
        -Arguments @("validate-bundle", $root) `
        -Label "M5 independent read-only M4 bundle validation"
    $after = Get-M5FileSha256 -Path $manifestPath
    if ($after -cne $sha) {
        throw "M5 M4 manifest changed during read-only validation."
    }
    return [pscustomobject]@{
        Root = $root
        Manifest = $input.document
        ManifestSha256 = $sha
    }
}

function Invoke-M5Worker {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Binding,
        [Parameter(Mandatory = $true)][string]$WorkerPath,
        [Parameter(Mandatory = $true)][string]$BundleRoot,
        [Parameter(Mandatory = $true)][string]$InvocationPath,
        [Parameter(Mandatory = $true)][string]$M4BundleRoot,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    if ($TimeoutSeconds -lt 1 -or
        $TimeoutSeconds -gt $script:M5HardProcessTimeoutSeconds) {
        throw "M5 worker timeout exceeds the fixed 120-second ceiling."
    }
    Assert-M5WorkerPowerShell -Binding $Binding
    [void](Invoke-BoundedChildProcess -Executable $Binding.Path `
        -Arguments @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", $WorkerPath, "-BundleRoot", $BundleRoot,
            "-InvocationPath", $InvocationPath,
            "-M4BundleRoot", $M4BundleRoot
        ) -CallerLabel "M5 isolated worker" `
        -TimeoutSeconds $TimeoutSeconds -MaximumOutputBytes 1MB)
}

function Assert-M5FreshWorker {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Result,
        [AllowNull()][pscustomobject]$PriorResult
    )
    if ([string]$Result.execution_status -cne "pass" -or
        [string]$Result.architecture -cne "x86" -or
        -not [bool]$Result.bindings_verified_before_com) {
        throw "M5 worker did not return a bound passing x86 result."
    }
    if ($null -ne $PriorResult -and (
        [long]$PriorResult.process_id -eq [long]$Result.process_id -or
        [string]$PriorResult.worker_run_id -ceq [string]$Result.worker_run_id -or
        [string]$PriorResult.nonce -ceq [string]$Result.nonce
    )) { throw "M5 phases did not use distinct fresh workers." }
}

function Register-M5PhasePayloads {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Paths,
        [Parameter(Mandatory = $true)][pscustomobject]$Result
    )
    foreach ($payload in @(
        @($Paths.result, "phase_worker_result"),
        @($Paths.operation_log, "operation_log"),
        @($Paths.prefix, "prefix")
    )) {
        Add-M5ManifestEntry -Entries $Entries -Session $Session `
            -RelativePath ([string]$payload[0]) -Role ([string]$payload[1])
    }
    if ($null -ne $Paths.snapshot) {
        Add-M5ManifestEntry -Entries $Entries -Session $Session `
            -RelativePath ([string]$Paths.snapshot) -Role "semantic_snapshot"
    }
    foreach ($row in $Result.database_observations) {
        Add-M5ManifestEntry -Entries $Entries -Session $Session `
            -RelativePath ([string]$row.path) -Role "database" `
            -ExpectedSha256 ([string]$row.sha256) `
            -ExpectedBytes ([long]$row.bytes)
    }
}

function Invoke-M5CheckedPhase {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][pscustomobject]$Sample,
        [Parameter(Mandatory = $true)][pscustomobject]$Condition,
        [Parameter(Mandatory = $true)]
        [ValidateSet("source", "compact", "verify")][string]$PhaseId,
        [Parameter(Mandatory = $true)][int]$WorkerOrdinal,
        [Parameter(Mandatory = $true)][string]$PlanSha256,
        [Parameter(Mandatory = $true)][pscustomobject]$M4Binding,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$WorkerPath,
        [Parameter(Mandatory = $true)][pscustomobject]$PowerShellBinding,
        [AllowNull()][pscustomobject]$PriorResult
    )
    $paths = Get-M5PhasePaths -SampleId $Sample.sample_id -PhaseId $PhaseId
    $invocation = New-M5Invocation -Context $Context -Session $Session `
        -Plan $Plan -Sample $Sample -Condition $Condition -PhaseId $PhaseId `
        -WorkerOrdinal $WorkerOrdinal -PlanSha256 $PlanSha256 `
        -M4Manifest $M4Binding.Manifest
    Write-M5BundleJson -Session $Session -Entries $Entries `
        -RelativePath $paths.invocation -Role "phase_invocation" `
        -Document $invocation -MaximumBytes 64KB
    $invocationPath = Get-M1PayloadPath -Session $Session `
        -RelativePath $paths.invocation
    Invoke-M5Contract -Context $Context -ContractPath $ContractPath `
        -Arguments @(
            "validate-invocation", "--bundle-root", $Session.StagingBundle,
            "--invocation", $invocationPath,
            "--m4-bundle-root", $M4Binding.Root
        ) -Label "M5 $PhaseId invocation validation"
    $workerTimeout = [int]$Plan.bounds.worker_timeout_seconds
    if ($workerTimeout -lt 1 -or
        $workerTimeout -gt $script:M5HardProcessTimeoutSeconds) {
        throw "M5 checked plan worker timeout exceeds 120 seconds."
    }
    Invoke-M5Worker -Binding $PowerShellBinding -WorkerPath $WorkerPath `
        -BundleRoot $Session.StagingBundle -InvocationPath $invocationPath `
        -M4BundleRoot $M4Binding.Root `
        -TimeoutSeconds $workerTimeout
    $resultPath = Get-M1PayloadPath -Session $Session `
        -RelativePath $paths.result
    Invoke-M5Contract -Context $Context -ContractPath $ContractPath `
        -Arguments @(
            "validate-result", "--bundle-root", $Session.StagingBundle,
            "--result", $resultPath
        ) -Label "M5 $PhaseId result validation"
    $result = (Read-M5Json -Path $resultPath -MaximumBytes 64KB).document
    Assert-M5FreshWorker -Result $result -PriorResult $PriorResult
    Register-M5PhasePayloads -Session $Session -Entries $Entries `
        -Paths $paths -Result $result
    return [pscustomobject]@{
        invocation = [pscustomobject]$invocation
        result = $result
        paths = $paths
    }
}

function Add-M5Quiescence {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Context,
        [Parameter(Mandatory = $true)][pscustomobject]$Session,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][pscustomobject]$Phase,
        [Parameter(Mandatory = $true)][string]$DatabaseRole,
        [Parameter(Mandatory = $true)][pscustomobject]$Plan,
        [Parameter(Mandatory = $true)][string]$ContractPath
    )
    $q = New-M5PostWorkerQuiescence -Session $Session -Entries $Entries `
        -Invocation $Phase.invocation -WorkerResult $Phase.result `
        -DatabaseRole $DatabaseRole `
        -MaximumDatabaseBytes ([long]$Plan.bounds.max_database_bytes) `
        -MaximumCompanionBytes ([long]$Plan.bounds.max_companion_bytes)
    $qPath = Get-M1PayloadPath -Session $Session `
        -RelativePath $q.artifact.path
    $resultPath = Get-M1PayloadPath -Session $Session `
        -RelativePath $Phase.paths.result
    Invoke-M5Contract -Context $Context -ContractPath $ContractPath `
        -Arguments @(
            "validate-quiescence", "--bundle-root", $Session.StagingBundle,
            "--quiescence", $qPath, "--result", $resultPath
        ) -Label "M5 $DatabaseRole quiescence validation"
    return $q
}
