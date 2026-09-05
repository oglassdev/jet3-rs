param([Parameter(Mandatory=$true)][string]$ScriptPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected actual x86 helper check'}
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($ScriptPath,[ref]$t,[ref]$e)
if($e.Count){throw ($e|Out-String)}
foreach($name in @('Release','Close-Value','Read-Row')){
    $defs=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name-eq $name},$false))
    if($defs.Count-ne 1){throw 'Helper missing'}
    Invoke-Expression $defs[0].Extent.Text
}
$failure=$null
$fields=[pscustomobject]@{Values=[object[]]@([int]1,[int]-2,[int]::MinValue)}
$fields|Add-Member ScriptMethod Item {param($i) return [pscustomobject]@{Value=$this.Values[$i]}}
$rs=[pscustomobject]@{Fields=$fields}
$row=Read-Row $rs
$rows=New-Object Collections.ArrayList;[void]$rows.Add($row)
foreach($query in @(@(1),@(-2,1))){
    $record=@{rows=@($rows);seek=@(@{query=[object[]]$query;row=$row})}
    $json=ConvertTo-Json -InputObject $record -Depth 10;$back=$json|ConvertFrom-Json
    if($back.rows.Count-ne 1-or $back.rows[0].Count-ne 3-or $back.rows[0][2]-ne [int]::MinValue-or $back.seek[0].query.Count-ne $query.Count-or $back.seek[0].row[1]-ne -2){throw 'Scalar row/query JSON mismatch'}
}
Write-Output 'x86 Read-Row and one/two-key JSON checks passed; no DAO'
