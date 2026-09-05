param([string]$Producer,[string]$Helper,[string]$Plan)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86'}
foreach($caseSpec in @(@{path=$Helper;names=@('From-Hex','To-Hex','Set-Value','Read-Value')},@{path=$Producer;names=@('Variant','Row')})){
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($caseSpec.path,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Parser failure'}
    foreach($name in $caseSpec.names){$found=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false));if($found.Count-ne 1){throw 'Missing function'};Invoke-Expression $found[0].Extent.Text}
}
function Release($Value){}
class MockFields {
    [hashtable]$Values=@{A=[pscustomobject]@{Value=$null};B=[pscustomobject]@{Value=$null};Tag=[pscustomobject]@{Value=$null}}
    [object]Item([string]$Name){return $this.Values[$Name]}
}
$rs=[pscustomobject]@{Fields=[MockFields]::new()};$planValue=Get-Content -LiteralPath $Plan -Raw|ConvertFrom-Json;$count=0
foreach($arm in $planValue.arms){
    $rows=New-Object Collections.ArrayList
    foreach($values in $arm.rows){
        for($i=0;$i-lt $arm.fields.Count;$i++){
            $field=$arm.fields[$i];Set-Value $rs $field $values[$i];$actual=Read-Value $rs $field
            if($actual-cne $values[$i]){throw "Saved bits differ $($arm.name)/$i"}
            if($null-ne $values[$i]){$variant=Variant $field $values[$i];if($variant.GetType()-ne $rs.Fields.Item($field.name).Value.GetType()){throw 'Seek variant type differs'}}
            $count++
        }
        [void]$rows.Add((Row $rs $arm))
    }
    $json=ConvertTo-Json -InputObject ([object[]]$rows.ToArray()) -Depth 20 -Compress
    $expected=ConvertTo-Json -InputObject ([object[]]$arm.rows) -Depth 20 -Compress
    if($json-cne $expected){throw 'Plain row JSON differs'}
}
Write-Output "Parser, $count typed conversions/Seek variants and all row JSON passed; no DAO."
