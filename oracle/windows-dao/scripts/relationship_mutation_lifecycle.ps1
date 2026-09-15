. (Join-Path $env:JET3_WORK 'relationship_mutation_dao.ps1')
function Convert-Value($Value,[string]$Table,[int]$Column) {
 if($null-eq$Value){return [DBNull]::Value}
 if($Column-eq0-or($Table-eq'Child'-and$Column-eq1)){return [int]$Value}
 $bytes=[byte[]]::new(([string]$Value).Length/2);for($i=0;$i-lt$bytes.Length;$i++){$bytes[$i]=[Convert]::ToByte(([string]$Value).Substring($i*2,2),16)}
 if($Table-eq'Child'-and$Column-eq3){return ,$bytes};return $CodePage.GetString($bytes)
}
function Apply([string]$Path,$Operation) {
 $before=Identity $Path;$engine=$db=$rs=$null;$status='success';$errorDetail=$null
 try {
  $script:Endpoint="apply/$($Operation.table)/$($Operation.kind)";$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset([string]$Operation.table,2)
  $names=if($Operation.table-eq'Parent'){@('Id','Label')}else{@('Id','ParentId','Body Memo','Blob OLE')}
  if($Operation.kind-eq'insert'){$rs.AddNew()}else{$rs.FindFirst("[Id]=$([int]$Operation.id)");if($rs.NoMatch){throw 'Target absent'};if($Operation.kind-ne'delete'){$rs.Edit()}}
  if($Operation.kind-eq'delete'){$rs.Delete()}
  else {
   if($Operation.kind-eq'field'){$c=[int]$Operation.column;Set-Cell $rs $names[$c] (Convert-Value $Operation.value $Operation.table $c)}
   else {for($c=0;$c-lt$names.Count;$c++){Set-Cell $rs $names[$c] (Convert-Value $Operation.row[$c] $Operation.table $c)}}
   $rs.Update()
  }
 }catch{$status='rejected';$errorDetail=Failure $_ $engine;if($null-ne$rs){try{if($rs.EditMode-ne0){$rs.CancelUpdate()}}catch{}}}
 finally{if($null-ne$rs){try{$rs.Close()}catch{}};Release $rs;if($null-ne$db){try{$db.Close()}catch{}};Release $db;Release $engine;[GC]::Collect();[GC]::WaitForPendingFinalizers()}
 return @{request=$Operation;status=$status;error=$errorDetail;before=$before;after=Identity $Path}
}
function Save-Capture([string]$Path,[string]$Name) {
 $capture=Capture $Path $Name;$file="$Name.mdb";Copy-Item -LiteralPath $Path -Destination (Join-Path $env:JET3_OUTBOX $file);$capture.file=$file
 if($capture.status-ne'pass'){throw "Capture failed $Name"};return $capture
}
$manifestPath=Join-Path $env:JET3_WORK 'relationship-mutation-lifecycle.json'
$manifest=Get-Content -LiteralPath $manifestPath -Raw|ConvertFrom-Json
$result=@{source_revision=$manifest.source_revision;manifest=Identity $manifestPath;environment=@{};cases=@();error=$null}
try {
 $probe=New-Object -ComObject DAO.DBEngine.36
 try {$dll=@([Diagnostics.Process]::GetCurrentProcess().Modules|Where-Object{$_.ModuleName-ieq'dao360.dll'});if($dll.Count-ne1){throw 'DAO DLL absent'};$result.environment=@{provider='DAO.DBEngine.36';version=[string]$probe.Version;bits=[IntPtr]::Size*8;os=[Environment]::OSVersion.VersionString;culture=[Globalization.CultureInfo]::CurrentCulture.Name;dll_version=$dll[0].FileVersionInfo.FileVersion;dll_sha256=(Identity $dll[0].FileName).sha256}}
 finally{Release $probe}
 foreach($case in $manifest.cases) {
  $name=[string]$case.name;$entry=@{name=$name;stages=@();operations=@();native=@{};refusals=@()};$result.cases+=,$entry
  $control=Join-Path $env:JET3_WORK "$name-control-live.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $case.source) -Destination $control
  foreach($stage in $case.stages) {
   foreach($operation in $stage.operations){$outcome=Apply $control $operation;$entry.operations+=,$outcome;if($outcome.status-ne'success'){throw "Control operation failed $name/$($stage.name)"}}
   $candidate=Join-Path $env:JET3_WORK $stage.file
   if((Identity $candidate).sha256-ne$stage.identity.sha256){throw 'Candidate input identity'}
   $entry.stages+=@{name=$stage.name;candidate=Save-Capture $candidate "$name-$($stage.name)-candidate";control=Save-Capture $control "$name-$($stage.name)-control"}
  }
  foreach($role in @('candidate','control')) {
   $last=$entry.stages[-1][$role];$live=Join-Path $env:JET3_WORK "$name-native-$role.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_OUTBOX $last.file) -Destination $live
   foreach($operation in $manifest.recipe.native){$outcome=Apply $live $operation;$entry.operations+=,$outcome;if($outcome.status-ne'success'){throw "Native successor failed $name/$role"}}
   $entry.native[$role]=Save-Capture $live "$name-native-$role"
  }
  if($manifest.round-eq'mutations') {
   foreach($request in $manifest.recipe.refusals) {
    if($null-eq$request.number){continue};$attempt=@{name=$request.name}
    foreach($role in @('candidate','control')) {
     $first=$entry.stages[0][$role];$live=Join-Path $env:JET3_WORK "$name-refusal-$($request.name)-$role.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_OUTBOX $first.file) -Destination $live
     $outcome=Apply $live $request.operation;$attempt[$role]=@{operation=$outcome;capture=Save-Capture $live "$name-refusal-$($request.name)-$role"}
    }
    $entry.refusals+=,$attempt
   }
  }
 }
}catch{$result.error=Failure $_ $null}
finally{Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json');Copy-Item $manifestPath (Join-Path $env:JET3_OUTBOX 'relationship-mutation-lifecycle.json')}
if($null-ne$result.error){exit 1}
