# Multi-column Memo/OLE creation (EXP-0235/0236): capture each Rust candidate and a DAO
# control, then apply the native insert/replace/delete to both and capture again.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

$manifest = Read-Manifest 'multiple-long-value-creation.json'
$result = New-Result 'dao_multiple_long_value_creation_result' $manifest
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($case in $manifest.cases) { $result.cases += ,(Invoke-LongCreation $case) }
} catch { $result.error = Failure $_ } finally { Save-Outputs $result 'result.json' }
if (-not (Test-Complete $result)) { exit 1 }
