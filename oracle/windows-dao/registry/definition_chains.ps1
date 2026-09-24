# Definition chains (EXP-0247): one x86 worker per case captures the Rust candidate and a DAO
# control, then applies the native insert/replace/delete to both and captures again.
param([string]$CaseName = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

Invoke-CaseSuite 'creation-definition-chains.json' $CaseName $PSCommandPath 'dao_creation_definition_chains_result' {
    param($Manifest, $Case)
    Invoke-LongCreation $Case
}
