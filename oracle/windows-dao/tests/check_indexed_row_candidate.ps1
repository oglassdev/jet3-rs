param([Parameter(Mandatory=$true)][string]$ScriptPath,[Parameter(Mandatory=$true)][string]$PlanPath,[Parameter(Mandatory=$true)][string]$HelperPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected actual x86 process'}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($ScriptPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
$helper=[Management.Automation.Language.Parser]::ParseFile($HelperPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper syntax'}
foreach($item in @(@($ast,'Set-Long'),@($ast,'Append-Row'),@($ast,'Row'),@($ast,'Failure'),@($helper,'Release'))){
    $name=$item[1];$found=@($item[0].FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw "Missing pure helper $name"};Invoke-Expression $found[0].Extent.Text
}
$fields=@{Id=[pscustomobject]@{Value=$null};Value=[pscustomobject]@{Value=$null}}
$collection=[pscustomobject]@{Fields=$fields};$collection|Add-Member ScriptMethod Item {param($Name)return $this.Fields[$Name]}
$rs=[pscustomobject]@{Fields=$collection};$rs|Add-Member ScriptMethod AddNew {}
$rs|Add-Member ScriptMethod Update {foreach($name in @('Id','Value')){if($this.Fields.Item($name).Value-isnot [int]){throw 'Non-Int32 assignment'}};$script:updates++}
$script:updates=0;$script:failure=$null;$script:endpoint='pure'
$plan=Get-Content -LiteralPath $PlanPath -Raw|ConvertFrom-Json
foreach($arm in $plan.arms){
    $requests=New-Object Collections.ArrayList
    foreach($row in $arm.rows){[void]$requests.Add($row)}
    if($null-ne $arm.insert){[void]$requests.Add($arm.insert)}
    [void]$requests.Add($arm.follow);[void]$requests.Add($arm.duplicate)
    foreach($request in $requests){
        Append-Row $rs $request;$actual=Row $rs
        if($actual-isnot [object[]]-or $actual.Count-ne 2-or $actual[0]-ne [int]$request[0]-or $actual[1]-ne [int]$request[1]){throw 'Typed row return differs'}
    }
}
if($script:updates-ne 222-or $null-ne $script:failure){throw "Unexpected pure helper count $script:updates"}
Write-Output "x86 parser and $script:updates typed row assignments passed; no DAO executed"
