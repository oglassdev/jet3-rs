# Wide rows: many variable columns, long fixed text and jump bytes (EXP-0257/0258/0259): one x86 worker per case
# replays the recipe on a DAO control and captures candidate and control; see Rows.ps1.
param([string]$CaseName = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

Invoke-CaseSuite 'wide-row-lifecycle.json' $CaseName $PSCommandPath 'dao_scalar_mutation_result' {
    param($Manifest, $Case)
    Invoke-ScalarCase $Manifest $Case 'regrown'
}
