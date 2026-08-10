[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [string]$GitCommit,
    [Parameter(Mandatory = $true)]
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryCandidate = [IO.Path]::GetFullPath($RepositoryRoot)
$moduleRoot = Join-Path $repositoryCandidate "oracle/windows-dao/scripts/m1"
$preflightModule = Join-Path $moduleRoot "M1.Preflight.ps1"
$publicationModule = Join-Path $moduleRoot "M1.Publication.ps1"
$daoModule = Join-Path $moduleRoot "M1.Dao.ps1"
$bundleModule = Join-Path $moduleRoot "M1.Bundle.ps1"
. $preflightModule
. $publicationModule
. $daoModule
. $bundleModule

$executedSources = @(
    "oracle/windows-dao/scripts/run-m1-controlled.ps1",
    "oracle/windows-dao/scripts/m1/M1.Preflight.ps1",
    "oracle/windows-dao/scripts/m1/M1.Provider.ps1",
    "oracle/windows-dao/scripts/m1/M1.Publication.ps1",
    "oracle/windows-dao/scripts/m1/M1.PublicationPaths.ps1",
    "oracle/windows-dao/scripts/m1/M1.Dao.ps1",
    "oracle/windows-dao/scripts/m1/M1.DaoValues.ps1",
    "oracle/windows-dao/scripts/m1/M1.Bundle.ps1",
    "oracle/windows-dao/scripts/m1_pair_compare.py",
    "oracle/windows-dao/scripts/m1_bundle_validation.py"
)

function Read-M1CheckedInput {
    param(
        [string]$Path,
        [long]$MaximumBytes = 1MB
    )

    $item = Get-Item -LiteralPath $Path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -lt 1 -or
        $item.Length -gt $MaximumBytes
    ) {
        throw "A checked M1 input violates its byte or file-type bound."
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -ne $item.Length) {
        throw "A checked M1 input changed while being read."
    }
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    $text = $encoding.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xfeff) {
        throw "M1 JSON inputs cannot contain a byte-order mark."
    }
    $document = $text | ConvertFrom-Json
    return [ordered]@{
        bytes = $bytes
        document = $document
    }
}

function Get-M1ExitCode {
    param([string]$Status)

    switch ($Status) {
        "pass" { return 0 }
        "fail" { return 1 }
        "blocked" { return 3 }
        default { return 4 }
    }
}

$context = $null
$session = $null
$published = $false
$publishedStatus = "error"
$publishedReason = "M1 execution did not complete."

try {
    $context = Invoke-M1Preflight `
        -RepositoryRoot $repositoryCandidate `
        -EnvironmentPath $EnvironmentPath `
        -OutputRoot $OutputRoot `
        -GitCommit $GitCommit `
        -RunId $RunId `
        -ExecutedRepoRelativeSourcePaths $executedSources

    $inventoryInput = Read-M1CheckedInput -Path $context.InventoryPath
    Assert-M1ByteArraySha256 -Bytes $inventoryInput.bytes `
        -ExpectedSha256 ([string]$context.InventorySha256) `
        -Label "Loaded M1 inventory"
    $checkedInputs = New-Object Collections.ArrayList
    foreach ($entry in $context.Inventory.files) {
        $sourcePath = Join-Path (
            Join-Path $context.Repository "oracle/windows-dao/examples"
        ) ([string]$entry.path)
        $loaded = Read-M1CheckedInput -Path $sourcePath
        Assert-M1ByteArraySha256 -Bytes $loaded.bytes `
            -ExpectedSha256 ([string]$entry.sha256) `
            -Label "Loaded M1 scenario or pair"
        [void]$checkedInputs.Add([ordered]@{
            bytes = $loaded.bytes
            document = $loaded.document
            entry = $entry
        })
    }

    # Refuse any identity drift before the first evidence-root mutation.
    [void](Assert-M1RuntimeBinding -Context $context)
    $session = New-M1PublicationSession `
        -RepositoryRoot $context.Repository `
        -OutputRoot $context.OutputRoot `
        -GitCommit $GitCommit `
        -RunId $RunId `
        -MaxFileBytes 64MB `
        -MaxTotalBytes 256MB
    $manifestInputs = New-Object Collections.ArrayList
    Write-M1DurableBytes -Session $session -RelativePath "environment.json" `
        -Bytes $context.EnvironmentBytes
    Add-M1ManifestPayload -ManifestInputs $manifestInputs -Session $session `
        -RelativePath "environment.json" -Role "environment"
    Write-M1DurableBytes -Session $session -RelativePath "inventory.json" `
        -Bytes $inventoryInput.bytes
    Add-M1ManifestPayload -ManifestInputs $manifestInputs -Session $session `
        -RelativePath "inventory.json" -Role "inventory"

    $scenarioPlans = New-Object Collections.ArrayList
    $pairPlans = New-Object Collections.ArrayList
    foreach ($input in $checkedInputs) {
        $document = $input.document
        if ([string]$input.entry.document_type -eq "dao_scenario") {
            $relative = "scenarios/$($document.scenario_id)/input.json"
            $role = "scenario_input"
            [void]$scenarioPlans.Add([ordered]@{
                document = $document
                relative = $relative
            })
        }
        elseif ([string]$input.entry.document_type -eq "dao_pair") {
            $relative = "pairs/$($document.pair_id)/input.json"
            $role = "pair_input"
            [void]$pairPlans.Add([ordered]@{
                document = $document
                relative = $relative
            })
        }
        else {
            throw "unsupported M1 inventory document type: $($input.entry.document_type)"
        }
        Write-M1DurableBytes -Session $session -RelativePath $relative `
            -Bytes $input.bytes
        Add-M1ManifestPayload -ManifestInputs $manifestInputs `
            -Session $session -RelativePath $relative -Role $role
    }

    # Recheck after retaining the exact checked bytes and immediately before
    # the first COM activation.
    [void](Assert-M1RuntimeBinding -Context $context)
    $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $scenarioResults = New-Object Collections.ArrayList
    $scenarioState = @{}
    foreach ($plan in $scenarioPlans) {
        $execution = Invoke-M1DaoScenario `
            -Scenario $plan.document `
            -AcceptedProvider $context.AcceptedProvider `
            -WorkingRoot $session.WorkingPath `
            -GitCommit $GitCommit `
            -RunId $RunId
        $result = Write-M1ScenarioArtifacts `
            -Session $session `
            -Scenario $plan.document `
            -Execution $execution `
            -ManifestInputs $manifestInputs `
            -ScenarioState $scenarioState
        [void]$scenarioResults.Add($result)
    }

    $pairResults = New-Object Collections.ArrayList
    $comparator = Join-Path $context.Repository (
        "oracle/windows-dao/scripts/m1_pair_compare.py"
    )
    foreach ($plan in $pairPlans) {
        $pair = $plan.document
        $left = $scenarioState[[string]$pair.left_scenario_id]
        $right = $scenarioState[[string]$pair.right_scenario_id]
        $pairStatus = "skipped"
        $pairReason = "A pair side did not produce a passing DAO snapshot."
        $observed = @()
        $leftReference = $null
        $rightReference = $null
        if (
            [string]$left.execution.status -eq "pass" -and
            [string]$right.execution.status -eq "pass"
        ) {
            $pairInputPath = Get-M1PayloadPath -Session $session `
                -RelativePath $plan.relative
            $comparison = Invoke-M1PairComparison `
                -PythonPath $context.PythonPath `
                -ComparatorPath $comparator `
                -PairPath $pairInputPath `
                -LeftSnapshotPath $left.snapshot_path `
                -RightSnapshotPath $right.snapshot_path
            $pairStatus = [string]$comparison.status
            $pairReason = [string]$comparison.detail
            $observed = @($comparison.observed)
            $leftReference = $left.snapshot_reference
            $rightReference = $right.snapshot_reference
        }
        [void]$pairResults.Add([ordered]@{
            input = Get-M1BundleFileReference -Session $session `
                -RelativePath $plan.relative
            left_scenario_id = [string]$pair.left_scenario_id
            left_snapshot = $leftReference
            observed_difference_paths = @($observed)
            pair_id = [string]$pair.pair_id
            reason = $pairReason
            right_scenario_id = [string]$pair.right_scenario_id
            right_snapshot = $rightReference
            status = $pairStatus
        })
    }

    $commandLine = @([Environment]::GetCommandLineArgs())
    $sealed = Write-M1ReportAndManifest `
        -Session $session `
        -GitCommit $GitCommit `
        -RunId $RunId `
        -StartedAt $startedAt `
        -ScenarioResults @($scenarioResults) `
        -PairResults @($pairResults) `
        -ManifestInputs $manifestInputs `
        -CommandLine $commandLine
    $recheck = {
        param($stage)
        if ($stage.FinalDirectory -cne $context.FinalDirectory) {
            return $false
        }
        return [bool](Assert-M1RuntimeBinding -Context $context)
    }
    Publish-M1Stage -Stage $session `
        -RecheckScriptBlock $recheck `
        -ValidatorPath $context.ValidatorPath `
        -PythonExecutable $context.PythonPath
    $published = $true
    $publishedStatus = [string]$sealed.status
    $publishedReason = [string]$sealed.reason
}
catch {
    $category = if ($_.Exception.Data.Contains("M1Category")) {
        [string]$_.Exception.Data["M1Category"]
    }
    else {
        "Error"
    }
    if (-not $published -and $null -ne $session) {
        try {
            Remove-M1PublicationStaging -Session $session
        }
        catch {
            [Console]::Error.WriteLine(
                "ERROR: staging cleanup failed: " + $_.Exception.Message
            )
        }
    }
    $prefix = switch ($category) {
        "Invocation" { "INVALID" }
        "Blocked" { "BLOCKED" }
        default { "ERROR" }
    }
    [Console]::Error.WriteLine(
        $prefix + ": " + (Get-M1SafeText -Value $_.Exception.Message)
    )
    if ($category -eq "Invocation") {
        exit 2
    }
    if ($category -eq "Blocked") {
        exit 3
    }
    exit 4
}
finally {
    if ($null -ne $context) {
        try {
            Close-M1PreflightContext -Context $context
        }
        catch {
            if (-not $published) {
                [Console]::Error.WriteLine(
                    "ERROR: preflight handle cleanup failed: " +
                    $_.Exception.Message
                )
            }
        }
    }
}

[Console]::WriteLine(
    $publishedStatus.ToUpperInvariant() + ": " + $publishedReason +
    " Bundle: " + $context.FinalDirectory
)
exit (Get-M1ExitCode -Status $publishedStatus)
