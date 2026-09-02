[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("catalog", "table-definition", "row", "value", "index", "bootstrap-layout", "system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics", "bootstrap-composer-validation", "schema-generalization", "multiple-indexes", "definition-continuation", "extended-names", "lvprop-null")]
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
    [string]$RowJobPath,
    [Parameter(Mandatory = $true)]
    [string]$ValueJobPath,
    [Parameter(Mandatory = $true)]
    [string]$IndexJobPath,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapLayoutJobPath,
    [Parameter(Mandatory = $true)]
    [string]$SystemCatalogJobPath,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapComposerValidationJobPath,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapComposerEmptyPath,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapComposerAlphaPath,
    [Parameter(Mandatory = $true)]
    [string]$SchemaGeneralizationJobPath,
    [Parameter(Mandatory = $true)]
    [string]$MultipleIndexesJobPath,
    [Parameter(Mandatory = $true)]
    [string]$DefinitionContinuationJobPath,
    [Parameter(Mandatory = $true)]
    [string]$ExtendedNamesJobPath,
    [Parameter(Mandatory = $true)]
    [string]$LvPropNullJobPath,
    [Parameter(Mandatory = $true)]
    [string]$LvPropFixedAlphaPath,
    [Parameter(Mandatory = $true)]
    [string]$LvPropNullAlphaPath,
    [string]$PlanSha256 = "",
    [string]$RunId = ""
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
    "value" { $ValueJobPath }
    "index" { $IndexJobPath }
    "bootstrap-layout" { $BootstrapLayoutJobPath }
    "system-catalog" { $SystemCatalogJobPath }
    "long-value-maps" { $SystemCatalogJobPath }
    "long-value-maps-followup" { $SystemCatalogJobPath }
    "bootstrap-composer-semantics" { $SystemCatalogJobPath }
    "bootstrap-composer-validation" { $BootstrapComposerValidationJobPath }
    "schema-generalization" { $SchemaGeneralizationJobPath }
    "multiple-indexes" { $MultipleIndexesJobPath }
    "definition-continuation" { $DefinitionContinuationJobPath }
    "extended-names" { $ExtendedNamesJobPath }
    "lvprop-null" { $LvPropNullJobPath }
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
elseif ($Job -ceq "bootstrap-layout") {
    $arguments += @("-PlanSha256", $PlanSha256)
}
elseif ($Job -in @("system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics")) {
    $arguments += @("-PlanSha256", $PlanSha256, "-RunId", $RunId, "-Experiment", $Job)
}
elseif ($Job -in @("schema-generalization", "multiple-indexes", "definition-continuation", "extended-names")) {
    $arguments += @("-PlanSha256", $PlanSha256, "-RunId", $RunId)
}
elseif ($Job -ceq "lvprop-null") {
    $arguments += @(
        "-PlanSha256", $PlanSha256,
        "-RunId", $RunId,
        "-FixedCandidatePath", $LvPropFixedAlphaPath,
        "-NullCandidatePath", $LvPropNullAlphaPath
    )
}
elseif ($Job -ceq "bootstrap-composer-validation") {
    $arguments += @(
        "-PlanSha256", $PlanSha256,
        "-RunId", $RunId,
        "-EmptyCandidatePath", $BootstrapComposerEmptyPath,
        "-AlphaCandidatePath", $BootstrapComposerAlphaPath
    )
}
& (Join-Path $PSHOME "powershell.exe") @arguments
$jobExitCode = [int]$LASTEXITCODE
$resultName = switch ($Job) {
    "catalog" { "catalog-job-result.json" }
    "table-definition" { "table-definition-job-result.json" }
    "row" { "row-job-result.json" }
    "value" { "value-job-result.json" }
    "index" { "index-job-result.json" }
    "bootstrap-layout" { "bootstrap-layout-job-result.json" }
    "system-catalog" { "system-catalog-job-result.json" }
    "long-value-maps" { "long-value-maps-job-result.json" }
    "long-value-maps-followup" { "long-value-maps-followup-job-result.json" }
    "bootstrap-composer-semantics" { "bootstrap-composer-semantics-job-result.json" }
    "bootstrap-composer-validation" { "bootstrap-composer-validation-job-result.json" }
    "schema-generalization" { "schema-generalization-job-result.json" }
    "multiple-indexes" { "multiple-indexes-job-result.json" }
    "definition-continuation" { "definition-continuation-job-result.json" }
    "extended-names" { "extended-names-job-result.json" }
    "lvprop-null" { "lvprop-null-job-result.json" }
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
        value_scenarios = @()
        index_scenarios = @()
        bootstrap_layout_replicas = @()
        system_catalog_replicas = @()
        schema_generalization_replicas = @()
        multiple_indexes_replicas = @()
        definition_continuation_replicas = @()
        extended_names_replicas = @()
        lvprop_null_replicas = @()
    }
    Write-JsonDocument -Path (Join-Path $RunRoot "dispatch-result.json") -Document $result
    exit 1
}

$jobResult = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
$catalogCheckpoints = @()
$tableDefinitionCheckpoints = @()
$tableDefinitionTypeResults = @()
$rowScenarios = @()
$valueScenarios = @()
$indexScenarios = @()
$bootstrapLayoutReplicas = @()
$systemCatalogReplicas = @()
$schemaGeneralizationReplicas = @()
$multipleIndexesReplicas = @()
$definitionContinuationReplicas = @()
$extendedNamesReplicas = @()
$lvpropNullReplicas = @()
# The system-catalog result carries no detail field; derive one from its status.
$detail = if ($Job -in @("system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics")) {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three system-catalog replicas once without retry."
    }
    else {
        "At least one system-catalog replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "schema-generalization") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three schema-generalization replicas once without retry."
    }
    else {
        "At least one schema-generalization replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "multiple-indexes") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three multiple-indexes replicas once without retry."
    }
    else {
        "At least one multiple-indexes replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "definition-continuation") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three definition-continuation replicas once without retry."
    }
    else {
        "At least one definition-continuation replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "extended-names") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three extended-names replicas once without retry."
    }
    else {
        "At least one extended-names replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "lvprop-null") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three LvProp-null replicas once without retry."
    }
    else {
        "At least one LvProp-null replica failed; per-replica error records the message."
    }
}
elseif ($Job -ceq "bootstrap-composer-validation") {
    if ([string]$jobResult.status -ceq "pass") {
        "Completed all three bootstrap-composer validation replicas once without retry."
    }
    else {
        "At least one bootstrap-composer validation replica failed; per-replica error records the message."
    }
}
else { [string]$jobResult.detail }
if ($Job -ceq "catalog") { $catalogCheckpoints = @($jobResult.checkpoints) }
elseif ($Job -ceq "table-definition") {
    $tableDefinitionCheckpoints = @($jobResult.checkpoints)
    $tableDefinitionTypeResults = @($jobResult.type_results)
}
elseif ($Job -ceq "row") { $rowScenarios = @($jobResult.scenarios) }
elseif ($Job -ceq "value") { $valueScenarios = @($jobResult.scenarios) }
elseif ($Job -ceq "index") { $indexScenarios = @($jobResult.scenarios) }
elseif ($Job -ceq "bootstrap-layout") {
    $bootstrapLayoutReplicas = @($jobResult.replicas)
}
elseif ($Job -in @("system-catalog", "long-value-maps", "long-value-maps-followup", "bootstrap-composer-semantics")) {
    $systemCatalogReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "bootstrap-composer-validation") {
    $systemCatalogReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "schema-generalization") {
    $schemaGeneralizationReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "multiple-indexes") {
    $multipleIndexesReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "definition-continuation") {
    $definitionContinuationReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "extended-names") {
    $extendedNamesReplicas = @($jobResult.replicas)
}
elseif ($Job -ceq "lvprop-null") {
    $lvpropNullReplicas = @($jobResult.replicas)
}
$result = [ordered]@{
    development_only = $true
    job = $Job
    status = [string]$jobResult.status
    detail = $detail
    catalog_checkpoints = @($catalogCheckpoints)
    table_definition_checkpoints = @($tableDefinitionCheckpoints)
    table_definition_type_results = @($tableDefinitionTypeResults)
    row_scenarios = @($rowScenarios)
    value_scenarios = @($valueScenarios)
    index_scenarios = @($indexScenarios)
    bootstrap_layout_replicas = @($bootstrapLayoutReplicas)
    system_catalog_replicas = @($systemCatalogReplicas)
    schema_generalization_replicas = @($schemaGeneralizationReplicas)
    multiple_indexes_replicas = @($multipleIndexesReplicas)
    definition_continuation_replicas = @($definitionContinuationReplicas)
    extended_names_replicas = @($extendedNamesReplicas)
    lvprop_null_replicas = @($lvpropNullReplicas)
}
Write-JsonDocument -Path (Join-Path $RunRoot "dispatch-result.json") -Document $result
exit $jobExitCode
