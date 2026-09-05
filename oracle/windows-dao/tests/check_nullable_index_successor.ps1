param([string]$Root)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size -ne 4){throw 'Expected acquisition x86 runtime'}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'producer.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($name in @('Release','Values','Read-Row','Set-Value','Seek-Row','Row-Json','Key-Hex','Assert-Baseline','Remember-Failure','Cleanup')){
 $defs=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false))
 if($defs.Count-ne 1){throw 'Missing pure helper'}
 Invoke-Expression $defs[0].Extent.Text
}
$plan=Get-Content (Join-Path $Root 'plan.json') -Raw|ConvertFrom-Json
$expected=Get-Content (Join-Path $Root 'expected.json') -Raw|ConvertFrom-Json
$output=[Collections.ArrayList]::new()
foreach($arm in @('unique','ignore','required','composite','composite-ignore','auto')){
 $fields=[pscustomobject]@{Storage=@{}}
 $fields|Add-Member ScriptMethod Item {param($n) return $this.Storage[[int]$n]}
 foreach($n in 0..2){$fields.Storage[$n]=[pscustomobject]@{Value='untouched'}}
 $rs=[pscustomobject]@{Fields=$fields;Arguments=$null}
 $rs|Add-Member ScriptMethod Seek {param($operator,$first,$second) $this.Arguments=[object[]]@($operator,$first,$second)}
 foreach($position in @(0,1,2,3,([int]$plan.arms.$arm[1].row_count-1))){
  $values=Values $arm 'Rows' $position
  if($values.GetType().FullName-cne 'System.Object[]' -or $values.Count-ne 3){throw 'Value array shape'}
  for($column=0;$column-lt 3;$column++){Set-Value $rs $column $values[$column]}
  $row=Read-Row $rs 3
  if((Row-Json $row)-cne (Row-Json $values)){throw 'Mock assignment/read mismatch'}
  [void]$output.Add(@{arm=$arm;position=$position;values=$values;row=$row})
 }
 foreach($query in $plan.arms.$arm[1].queries){
  Seek-Row $rs $query
  if($rs.Arguments[0]-cne '=' -or $rs.Arguments[1]-ne $query[0]){throw 'Seek first argument'}
  if($query.Count-eq 2 -and $rs.Arguments[2]-ne $query[1]){throw 'Seek second argument'}
 }
 $identity=@{sha256='mock';size=0}
 $observation=@{status='pass';endpoint='complete';error=$null;before=$identity;after=$identity;snapshot=$expected.$arm}
 Assert-Baseline $observation $arm
 $saved=$observation.snapshot.user_tables[1].rows[0][2]
 $observation.snapshot.user_tables[1].rows[0][2]=$null
 $rejected=$false
 try{Assert-Baseline $observation $arm}catch{$rejected=$true}
 if(-not $rejected){throw 'Corrupt baseline passed'}
 $observation.snapshot.user_tables[1].rows[0][2]=$saved
}
$firstFailure=$null; $endpoint='mock/assignment'; $currentArm='mock'; $currentReplica=1
$cleanupFailures=[Collections.ArrayList]::new()
try { throw 'initiating error' } catch { Remember-Failure $_ }
$mock=[pscustomobject]@{}
$mock|Add-Member ScriptMethod Close { throw 'cleanup error' }
Cleanup $mock 'close'
if($firstFailure.message-cne 'initiating error' -or $firstFailure.endpoint-cne 'mock/assignment' -or $cleanupFailures.Count-ne 1){throw 'Cleanup masked initiating error'}
ConvertTo-Json -InputObject @($output) -Depth 10 -Compress
