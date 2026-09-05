param([Parameter(Mandatory=$true)][string]$Producer)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Producer,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Producer parser failure'}
foreach($name in @('Set-Field','Failure')){
    $found=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing test function'};Invoke-Expression $found[0].Extent.Text
}
function Release($Value){}
class TestFields {
    [hashtable]$Values=@{Id=[pscustomobject]@{Value=$null};Value=[pscustomobject]@{Value=$null};Payload=[pscustomobject]@{Value=$null}}
    [object]Item([string]$Name){return $this.Values[$Name]}
}
$rs=[pscustomobject]@{Fields=[TestFields]::new()};$script:endpoint='mock';$script:failure=$null
foreach($n in 1..100){
    Set-Field $rs 'Id' $n;Set-Field $rs 'Payload' "row$n";Set-Field $rs 'Value' (-$n)
    if($rs.Fields.Item('Id').Value-isnot [int]-or $rs.Fields.Item('Payload').Value-isnot [string]-or $rs.Fields.Item('Value').Value-isnot [int]){throw 'Typed assignment mismatch'}
}
try{Set-Field $rs 'Id' 'not-an-integer';throw 'Expected cast failure'}catch{if($null-eq $script:failure-or $script:failure.endpoint-ne 'mock/field/Id'){throw 'Field failure endpoint lost'}}
if($script:endpoint-ne 'mock'){throw 'Caller endpoint not restored'}
Write-Output 'Parser and 300 typed mock assignments passed; no DAO.'
