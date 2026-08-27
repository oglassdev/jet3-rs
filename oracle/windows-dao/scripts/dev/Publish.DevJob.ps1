[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("provider-probe", "create-empty", "opening-matrix", "allocation-map", "catalog", "table-definition", "row", "value", "index")]
    [string]$Job,
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Test-Path -LiteralPath $Destination) {
    throw "Shared output path already exists."
}
$names = New-Object Collections.ArrayList
[void]$names.Add("environment.json")
[void]$names.Add("result.json")
switch ($Job) {
    "create-empty" { [void]$names.Add("empty.mdb") }
    "opening-matrix" {
        foreach ($name in @(
            "v30-u-n.mdb", "v30-e-n.mdb", "v30-u-p.mdb", "v30-e-p.mdb",
            "v40-u-n.mdb", "v40-e-n.mdb", "v40-u-p.mdb", "v40-e-p.mdb"
        )) { [void]$names.Add($name) }
    }
    "allocation-map" {
        foreach ($name in @(
            "allocation-00-empty.mdb", "allocation-01-created.mdb",
            "allocation-02-seeded.mdb", "allocation-03-before-extended.mdb",
            "allocation-04-after-extended.mdb", "allocation-05-grown.mdb",
            "allocation-06-deleted.mdb", "allocation-07-reinserted.mdb"
        )) { [void]$names.Add($name) }
    }
    "catalog" {
        [void]$names.Add("catalog-job-result.json")
        foreach ($name in @(
            "00-empty", "01-ascii-created", "02-ascii-dropped",
            "03-ascii-recreated", "04-cp1252-created", "05-cp1252-dropped",
            "06-cp1252-recreated"
        )) { [void]$names.Add("catalog-$name.mdb") }
    }
    "table-definition" {
        [void]$names.Add("table-definition-job-result.json")
        foreach ($name in @(
            "00-empty", "01-type-inventory", "02-column-probe",
            "03-boundary-probe", "04-index-base", "05-index-primary",
            "06-index-composite", "07-index-required", "08-relationship-base",
            "09-relationship-created"
        )) { [void]$names.Add("table-definition-$name.mdb") }
    }
    "row" {
        [void]$names.Add("row-job-result.json")
        foreach ($replica in 1..3) {
            foreach ($scenario in @(
                "fixed-only", "variable-only", "mixed", "all-null",
                "page-boundary", "growing", "shrinking", "deleted", "overflowing"
            )) { [void]$names.Add("row-r$replica-$scenario.mdb") }
        }
    }
    "value" {
        [void]$names.Add("value-job-result.json")
        foreach ($replica in 1..3) {
            [void]$names.Add("value-r$replica-scalars.mdb")
            [void]$names.Add("value-r$replica-cp1252.mdb")
            [void]$names.Add("value-r$replica-cp1251.mdb")
            foreach ($kind in @("memo", "ole")) {
                foreach ($length in @(32, 512, 2048, 4096)) {
                    [void]$names.Add("value-r$replica-$kind-$($length.ToString('D5')).mdb")
                }
            }
        }
    }
    "index" {
        [void]$names.Add("index-job-result.json")
        foreach ($name in @(
            "long-ascending", "long-descending", "long-permuted",
            "composite-descending", "key-types", "relationship-base",
            "relationship-created", "relationship-update", "relationship-delete",
            "relationship-cascade", "relationship-deleted"
        )) { [void]$names.Add("index-$name.mdb") }
    }
}

$parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Destination))
[IO.Directory]::CreateDirectory($parent) | Out-Null
$staging = $Destination + ".building." + [Guid]::NewGuid().ToString("N")
try {
    [IO.Directory]::CreateDirectory($staging) | Out-Null
    foreach ($name in $names) {
        $sourcePath = Join-Path $Source $name
        if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
            Copy-Item -LiteralPath $sourcePath -Destination $staging
        }
    }
    [IO.Directory]::Move($staging, $Destination)
}
catch {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
    throw
}
