param([string]$Root)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 runtime'}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'producer.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
function Release($Value) {}
foreach($name in @('Remember','Close-Object','Set-Row')){
 $defs=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name-eq $name},$false))
 if($defs.Count-ne 1){throw 'Missing pure helper'}
 Invoke-Expression $defs[0].Extent.Text
}
$plan=Get-Content (Join-Path $Root 'plan.json') -Raw|ConvertFrom-Json
$failure=$null;$endpoint='pure test';$count=0
foreach($arm in $plan.arms){
 $rows=[Collections.ArrayList]::new();[void]$rows.Add($arm.replacement);[void]$rows.Add($plan.insert)
 foreach($table in $arm.tables){foreach($row in $table.seed_rows){[void]$rows.Add($row)}}
 foreach($row in $rows){
  $fields=[pscustomobject]@{Storage=@{}}
  $fields|Add-Member ScriptMethod Item {param($n) return $this.Storage[[int]$n]}
  foreach($n in 0..4){$fields.Storage[$n]=[pscustomobject]@{Value='untouched'}}
  $rs=[pscustomobject]@{Fields=$fields};Set-Row $rs $row
  foreach($n in 0..4){
   $actual=$fields.Storage[$n].Value
   if($null-eq $row[$n]){if($actual-isnot [DBNull]){throw 'Null assignment'}}
   elseif($n-eq 3){if($actual.GetType().FullName-cne 'System.Byte[]'-or ([BitConverter]::ToString($actual)).Replace('-','').ToLowerInvariant()-cne $row[$n]){throw 'Binary assignment'}}
   elseif($n-le 1){if($actual-isnot [int]-or $actual-ne $row[$n]){throw 'Long assignment'}}
   elseif($n-eq 2){if($actual-isnot [string]-or $actual-cne $row[$n]){throw 'Text assignment'}}
   elseif($actual-isnot [bool]-or $actual-ne $row[$n]){throw 'Boolean assignment'}
   $count++
  }
 }
}
$endpoint='initiating';try{throw 'first'}catch{Remember $_}
$mock=[pscustomobject]@{};$mock|Add-Member ScriptMethod Close {throw 'cleanup'};Close-Object $mock $true
if($failure.endpoint-cne 'initiating'-or $failure.message-cne 'first'){throw 'Masked first failure'}
Write-Output "Parser and $count explicit typed mock assignments passed; no DAO."
