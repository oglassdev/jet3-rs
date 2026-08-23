[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ArtifactName,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Destination
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-A3ArtifactRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Operation
    )

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            return & $Operation
        }
        catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 10))
        }
    }
}

$headers = @{
    Accept = "application/vnd.github+json"
    Authorization = "Bearer $env:GITHUB_TOKEN"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$encodedName = [Uri]::EscapeDataString($ArtifactName)
$listUri = "$env:GITHUB_API_URL/repos/$env:GITHUB_REPOSITORY/actions/runs/" +
    "$env:GITHUB_RUN_ID/artifacts?name=$encodedName&per_page=100"
$response = Invoke-A3ArtifactRequest {
    Invoke-RestMethod -Method Get -Uri $listUri -Headers $headers
}
$artifact = @(
    $response.artifacts |
        Where-Object { $_.name -ceq $ArtifactName -and -not $_.expired } |
        Sort-Object -Property id -Descending |
        Select-Object -First 1
)
if ($artifact.Count -ne 1) {
    throw "The current run has no unexpired artifact named '$ArtifactName'."
}

$nonce = [Guid]::NewGuid().ToString("N")
$archive = Join-Path $env:RUNNER_TEMP "$ArtifactName-$nonce.zip"
$staging = "$Destination.rest-$nonce"
$downloadUri = "$env:GITHUB_API_URL/repos/$env:GITHUB_REPOSITORY/actions/artifacts/" +
    "$($artifact[0].id)/zip"

try {
    if ((Test-Path -LiteralPath $Destination)) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $null = Invoke-A3ArtifactRequest {
        Invoke-WebRequest -UseBasicParsing -Uri $downloadUri -Headers $headers `
            -OutFile $archive
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $staging
    Move-Item -LiteralPath $staging -Destination $Destination
}
finally {
    if ((Test-Path -LiteralPath $archive)) {
        Remove-Item -LiteralPath $archive -Force
    }
    if ((Test-Path -LiteralPath $staging)) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
