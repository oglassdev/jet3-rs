# Numeric and multiple-index mutations (EXP-0230 and successors): one x86 worker per case
# replays the recipe on a DAO control and captures candidate and control; see Rows.ps1.
param([string]$CaseName = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

Invoke-CaseSuite 'numeric-index-mutation.json' $CaseName $PSCommandPath 'dao_scalar_mutation_result' {
    param($Manifest, $Case)
    Invoke-ScalarCase $Manifest $Case 'regrown'
}
