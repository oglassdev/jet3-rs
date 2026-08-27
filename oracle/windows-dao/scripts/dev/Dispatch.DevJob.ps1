[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("catalog", "table-definition", "row")]
    [string]$Job,
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,
    [Parameter(Mandatory = $true)]
    [string]$CatalogJobPath,
    [Parameter(Mandatory = $true)]
    [string]$TableDefinitionJobPath,
    [Parameter(Mandatory = $true)]
    [string]$TableDefinitionTypeInputPath,
    [Parameter(Mandatory = $true)]
    [string]$RowJobPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-JsonDocument {
    param([string]$Path, [object]$Document)

    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($Path),
        (($Document | ConvertTo-Json -Depth 20) + "`n"),
        $encoding
    )
}

$scriptPath = switch ($Job) {
    "catalog" { $CatalogJobPath }
    "table-definition" { $TableDefinitionJobPath }
    "row" { $RowJobPath }
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    [Console]::Error.WriteLine("INVALID: selected staged job does not exist.")
    exit 2
}
if ($Job -ceq "table-definition" -and
    -not (Test-Path -LiteralPath $TableDefinitionTypeInputPath -PathType Leaf)) {
    [Console]::Error.WriteLine("INVALID: table-definition input does not exist.")
    exit 2
}

$arguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", $scriptPath, "-RunRoot", $RunRoot
)
if ($Job -ceq "table-definition") {
    $arguments += @("-TypeInputPath", $TableDefinitionTypeInputPath)
}
& (Join-Path $PSHOME "powershell.exe") @arguments
$jobExitCode = [int]$LASTEXITCODE
$resultName = switch ($Job) {
    "catalog" { "catalog-job-result.json" }
    "table-definition" { "table-definition-job-result.json" }
    "row" { "row-job-result.json" }
}
$resultPath = Join-Path $RunRoot $resultName
if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    $result = [ordered]@{
        development_only = $true
        job = $Job
        status = "fail"
        detail = "The selected staged job did not write its bounded result."
        catalog_checkpoints = @()
        table_definition_checkpoints = @()
        table_definition_type_results = @()
        row_scenarios = @()
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "dispatch-result.json") -Document $result
    exit 1
}

$jobResult = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
$catalogCheckpoints = @()
$tableDefinitionCheckpoints = @()
$tableDefinitionTypeResults = @()
$rowScenarios = @()
if ($Job -ceq "catalog") { $catalogCheckpoints = @($jobResult.checkpoints) }
elseif ($Job -ceq "table-definition") {
    $tableDefinitionCheckpoints = @($jobResult.checkpoints)
    $tableDefinitionTypeResults = @($jobResult.type_results)
}
elseif ($Job -ceq "row") { $rowScenarios = @($jobResult.scenarios) }
$result = [ordered]@{
    development_only = $true
    job = $Job
    status = [string]$jobResult.status
    detail = [string]$jobResult.detail
    catalog_checkpoints = @($catalogCheckpoints)
    table_definition_checkpoints = @($tableDefinitionCheckpoints)
    table_definition_type_results = @($tableDefinitionTypeResults)
    row_scenarios = @($rowScenarios)
}
Write-JsonDocument -Path (Join-Path $RunRoot "dispatch-result.json") -Document $result
exit $jobExitCode
