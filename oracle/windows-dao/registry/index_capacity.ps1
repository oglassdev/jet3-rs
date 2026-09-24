# Index capacity: 4, 13, 14 and 32 indexes and ten-component keys (EXP-0252/0253): one x86 worker per case
# replays the recipe on a DAO control and captures candidate and control; see Rows.ps1.
param([string]$CaseName = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

Invoke-CaseSuite 'index-capacity.json' $CaseName $PSCommandPath 'dao_scalar_mutation_result' {
    param($Manifest, $Case)
    Invoke-ScalarCase $Manifest $Case 'regrown'
}
