param([int]$Replica=0,[string]$CaseName='')
Set-StrictMode -Version Latest;$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne4){throw 'Expected x86 DAO'}
$Cp=[Text.Encoding]::GetEncoding(1252,(New-Object Text.EncoderExceptionFallback),(New-Object Text.DecoderExceptionFallback))
function Release($x){if($null-ne$x-and[Runtime.InteropServices.Marshal]::IsComObject($x)){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($x)}}
function Identity([string]$p){@{size=(Get-Item -LiteralPath $p).Length;sha256=(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Hex([byte[]]$b){[BitConverter]::ToString($b).Replace('-','').ToLowerInvariant()}
function Hex-Bytes([string]$s){$b=[byte[]]::new($s.Length/2);for($i=0;$i-lt$b.Length;$i++){$b[$i]=[Convert]::ToByte($s.Substring(2*$i,2),16)};return ,$b}
function Write-Json($v,[string]$p){[IO.File]::WriteAllText($p,((ConvertTo-Json -InputObject $v -Depth 100 -Compress)+"`n"),(New-Object Text.UTF8Encoding($false)))}
function Name-Record([string]$s){$encoded=$null;$ok=$true;try{$encoded=[BitConverter]::ToString($Cp.GetBytes([string]$s)).Replace('-','').ToLowerInvariant()}catch{$ok=$false};@{value=$s;utf16le=Hex([Text.Encoding]::Unicode.GetBytes($s));cp1252=if($ok){$encoded}else{$null};cp1252_defined=$ok;characters=$s.Length}}
function Error-Record($record,$engine){$numbers=@();$es=$ei=$null;try{if($null-ne$engine){$es=$engine.Errors;for($i=0;$i-lt$es.Count;$i++){$ei=$es.Item($i);$numbers+=[int]$ei.Number;Release $ei;$ei=$null}}}finally{Release $ei;Release $es};$e=$record.Exception;while($null-ne$e.InnerException){$e=$e.InnerException};@{type=$e.GetType().FullName;message=$e.Message;hresult=[int]$e.HResult;numbers=@($numbers);stack=[string]$record.ScriptStackTrace}}
function Set-Property($owner,[string]$name,$value){$a=[object[]]::new(1);$a[0]=$value;[void]$owner.GetType().InvokeMember($name,[Reflection.BindingFlags]::SetProperty,$null,$owner,$a)}
function Set-Cell($rs,[string]$name,$value){$fs=$f=$null;try{$fs=$rs.Fields;$f=$fs.Item($name);Set-Property $f 'Value' $value}finally{Release $f;Release $fs}}
function Convert-Value($value,[int]$type){if($null-eq$value){return [DBNull]::Value};if($type-eq4){return [int]$value};return [string]$value}
function New-Database([string]$path){$e=$w=$db=$null;try{$e=New-Object -ComObject DAO.DBEngine.36;$w=$e.Workspaces.Item(0);$db=$w.CreateDatabase($path,';LANGID=0x0409;CP=1252;COUNTRY=0',32);$db.Close();Release $db;$db=$null}finally{if($null-ne$db){try{$db.Close()}catch{}};Release $db;Release $w;Release $e}}
function Add-Table($db,$spec){
 $td=$f=$x=$xf=$rs=$null
 try{$td=$db.CreateTableDef([string]$spec.name)
  foreach($s in $spec.fields){$f=if([int]$s.type-eq4){$td.CreateField([string]$s.name,4)}else{$td.CreateField([string]$s.name,[int]$s.type,[int]$s.size)};$f.Required=[bool]$s.required;if([int]$s.type-in@(10,12)){$f.AllowZeroLength=[bool]$s.allow_zero_length};$td.Fields.Append($f);Release $f;$f=$null}
  foreach($ix in $spec.indexes){$x=$td.CreateIndex([string]$ix.name);$xf=$x.CreateField([string]$ix.field);if([string]$ix.direction-ceq'desc'){$xf.Attributes=1};$x.Fields.Append($xf);$x.Primary=[bool]$ix.primary;$x.Unique=[bool]$ix.unique;$x.Required=[bool]$ix.required;$x.IgnoreNulls=[bool]$ix.ignore_nulls;$td.Indexes.Append($x);Release $xf;$xf=$null;Release $x;$x=$null}
  $db.TableDefs.Append($td);Release $td;$td=$null
  if($spec.rows.Count){$rs=$db.OpenRecordset([string]$spec.name,2);foreach($row in $spec.rows){$rs.AddNew();foreach($s in $spec.fields){Set-Cell $rs ([string]$s.name) (Convert-Value $row.([string]$s.name) ([int]$s.type))};$rs.Update()};$rs.Close();Release $rs;$rs=$null}
 }finally{if($null-ne$rs){try{$rs.Close()}catch{}};Release $rs;Release $xf;Release $x;Release $f;Release $td}
}
function Add-Relation($db,$spec){$r=$f=$null;try{$r=$db.CreateRelation([string]$spec.name,[string]$spec.parent,[string]$spec.child,0);$f=$r.CreateField([string]$spec.parent_field);$f.ForeignName=[string]$spec.child_field;$r.Fields.Append($f);$db.Relations.Append($r)}finally{Release $f;Release $r}}
function Read-Properties($owner){$a=@();$ps=$p=$null;try{$ps=$owner.Properties;for($i=0;$i-lt$ps.Count;$i++){$p=$ps.Item($i);$v=$p.Value;$a+=@{ordinal=$i;name=Name-Record([string]$p.Name);type=[int]$p.Type;is_null=($null-eq$v-or[Convert]::IsDBNull($v));value=if($null-eq$v-or[Convert]::IsDBNull($v)){$null}else{[string]$v}};Release $p;$p=$null}}finally{Release $p;Release $ps};return ,$a}
function Read-Fields($td){$a=@();$fs=$f=$null;try{$fs=$td.Fields;for($i=0;$i-lt$fs.Count;$i++){$f=$fs.Item($i);$a+=@{ordinal=$i;name=Name-Record([string]$f.Name);type=[int]$f.Type;size=[int]$f.Size;attributes=[int]$f.Attributes;required=[bool]$f.Required;allow_zero_length=[bool]$f.AllowZeroLength;properties=Read-Properties $f};Release $f;$f=$null}}finally{Release $f;Release $fs};return ,$a}
function Read-Indexes($td){$a=@();$xs=$x=$fs=$f=$null;try{$xs=$td.Indexes;for($i=0;$i-lt$xs.Count;$i++){$x=$xs.Item($i);$keys=@();$fs=$x.Fields;for($j=0;$j-lt$fs.Count;$j++){$f=$fs.Item($j);$keys+=@{ordinal=$j;name=Name-Record([string]$f.Name);attributes=[int]$f.Attributes};Release $f;$f=$null};Release $fs;$fs=$null;$a+=@{ordinal=$i;name=Name-Record([string]$x.Name);primary=[bool]$x.Primary;unique=[bool]$x.Unique;required=[bool]$x.Required;foreign=[bool]$x.Foreign;ignore_nulls=[bool]$x.IgnoreNulls;fields=@($keys);properties=Read-Properties $x};Release $x;$x=$null}}finally{Release $f;Release $fs;Release $x;Release $xs};return ,$a}
function Read-Row($rs,$specs){
 $row=[ordered]@{};$fs=$f=$null
 try{$fs=$rs.Fields;foreach($s in $specs){$f=$fs.Item([string]$s.name.value);$v=$f.Value;$value=$null
  if($null-ne$v-and-not[Convert]::IsDBNull($v)){
   $value=switch([int]$s.type){
    1 {[bool]$v} 2 {[int]$v} 3 {[int]$v} 4 {[int]$v}
    5 {Hex([BitConverter]::GetBytes([long]([decimal]$v*10000)))}
    6 {Hex([BitConverter]::GetBytes([single]$v))} 7 {Hex([BitConverter]::GetBytes([double]$v))}
    8 {Hex([BitConverter]::GetBytes(([datetime]$v).ToOADate()))}
    9 {Hex([byte[]]$v)} 10 {Hex($Cp.GetBytes([string]$v))} 11 {Hex([byte[]]$v)} 12 {Hex($Cp.GetBytes([string]$v))}
    15 {if([string]$v-notmatch'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'){throw 'GUID read shape'};Hex(([guid]$Matches[0]).ToByteArray())}
    default {throw 'Unsupported observed type'}
   }
  };$row[[string]$s.name.value]=$value;Release $f;$f=$null
 }}finally{Release $f;Release $fs};return $row
}
function Seek-Value($value,[int]$type){if($null-eq$value){return [DBNull]::Value};if($type-eq4){return [int]$value};return $Cp.GetString((Hex-Bytes ([string]$value)))}
function Read-Table($db,$td){
 $specs=Read-Fields $td;$out=[ordered]@{ordinal=0;name=Name-Record([string]$td.Name);attributes=[int]$td.Attributes;properties=Read-Properties $td;fields=$specs;indexes=Read-Indexes $td;rows=@();index_reads=[ordered]@{}};$rs=$xs=$x=$fs=$f=$null
 try{$rs=$db.OpenRecordset([string]$td.Name,4);while(-not$rs.EOF){$out.rows+=,(Read-Row $rs $specs);$rs.MoveNext()};$rs.Close();Release $rs;$rs=$null;$xs=$td.Indexes
  for($i=0;$i-lt$xs.Count;$i++){
   $x=$xs.Item($i);$name=[string]$x.Name;$fs=$x.Fields;if($fs.Count-ne1){throw 'Expected one-column index'};$f=$fs.Item(0);$field=[string]$f.Name;Release $f;$f=$null;Release $fs;$fs=$null;$ks=@($specs|Where-Object{$_.name.value-ceq$field});if($ks.Count-ne1){throw 'Index field absent'}
   $tr=@();$seeks=@();$readError=$null
   try{$rs=$db.OpenRecordset([string]$td.Name,1);$rs.Index=$name;if(-not($rs.BOF-and$rs.EOF)){$rs.MoveFirst()};while(-not$rs.EOF){$tr+=,(Read-Row $rs $specs);$rs.MoveNext()};$queries=@();$seen=@{};foreach($row in $tr){$v=$row[$field];$token=if($null-eq$v){'<NULL>'}else{[string]$v};if(-not$seen.ContainsKey($token)){$seen[$token]=$true;$queries+=,$v}};$missing=if([int]$ks[0].type-eq4){2147483000}else{Hex($Cp.GetBytes('~missing~'))};$queries+=,$missing;foreach($v in $queries){$seek=Seek-Value $v ([int]$ks[0].type);$rs.Seek('=',$seek);$seeks+=@{query=$v;no_match=[bool]$rs.NoMatch;row=if($rs.NoMatch){$null}else{Read-Row $rs $specs}}}}
   catch{$e=$_.Exception;while($null-ne$e.InnerException){$e=$e.InnerException};$readError=@{type=$e.GetType().FullName;message=$e.Message;hresult=[int]$e.HResult;stack=[string]$_.ScriptStackTrace}}
   finally{if($null-ne$rs){try{$rs.Close()}catch{}};Release $rs;$rs=$null}
   $out.index_reads[$name]=@{field=$field;traversal=@($tr);seek=@($seeks);error=$readError};Release $x;$x=$null
  }
 }finally{if($null-ne$rs){try{$rs.Close()}catch{}};Release $rs;Release $f;Release $fs;Release $x;Release $xs};return $out
}
function Capture([string]$path){$before=Identity $path;$e=$db=$tds=$td=$rels=$r=$rfs=$rf=$null;try{$e=New-Object -ComObject DAO.DBEngine.36;$db=$e.OpenDatabase($path,$false,$true);$tables=@();$tds=$db.TableDefs;for($i=0;$i-lt$tds.Count;$i++){$td=$tds.Item($i);if(-not([string]$td.Name).StartsWith('MSys')){$t=Read-Table $db $td;$t.ordinal=$i;$tables+=,$t};Release $td;$td=$null};$relations=@();$rels=$db.Relations;for($i=0;$i-lt$rels.Count;$i++){$r=$rels.Item($i);$keys=@();$rfs=$r.Fields;for($j=0;$j-lt$rfs.Count;$j++){$rf=$rfs.Item($j);$keys+=@{name=Name-Record([string]$rf.Name);foreign_name=Name-Record([string]$rf.ForeignName)};Release $rf;$rf=$null};Release $rfs;$rfs=$null;$relations+=@{ordinal=$i;name=Name-Record([string]$r.Name);table=Name-Record([string]$r.Table);foreign_table=Name-Record([string]$r.ForeignTable);attributes=[int]$r.Attributes;fields=@($keys)};Release $r;$r=$null};$snapshot=@{version=[string]$db.Version;tables=@($tables);relations=@($relations)}}finally{Release $rf;Release $rfs;Release $r;Release $rels;Release $td;Release $tds;if($null-ne$db){try{$db.Close()}catch{}};Release $db;Release $e;[GC]::Collect();[GC]::WaitForPendingFinalizers()};$after=Identity $path;if($before.sha256-cne$after.sha256){throw 'Read-only capture changed image'};return @{before=$before;after=$after;snapshot=$snapshot}}
function Payload($spec,[string]$state){
 if($state-eq'null'){return [DBNull]::Value}
 if($state-eq'special'){$state=if([int]$spec.type-in@(9,10,11,12)){'empty'}else{'zero'}}
 $empty=$state-eq'empty';$zero=$state-eq'zero'
 if($state-eq'changed'){
  switch([int]$spec.type){
   1 {return $false} 2 {return [byte]24} 3 {return [int16]-124} 4 {return [int]124}
   5 {return [decimal]12.3457} 6 {return [single]-2.5} 7 {return [double]-2.5}
   8 {return [datetime]::FromOADate(45001.5)}
   9 {return ,([byte[]]@(1,0,66,254))}
   10 {if($spec.name-eq'FixedText'){return 'WXYZ'};return 'changed'}
   11 {$bytes=[byte[]]::new(4096);$pattern=[byte[]]@(1,0,66,254);for($i=0;$i-lt4096;$i++){$bytes[$i]=$pattern[$i%4]};return ,$bytes}
   12 {return ('M'*4096)} 15 {return '{12345678-1234-5678-9abc-def012345679}'}
   default {throw 'Unsupported changed payload'}
  }
 }

 switch([int]$spec.type){
  1 {return (-not$zero)} 2 {if($zero){return [byte]0};return [byte]23}
  3 {if($zero){return [int16]0};return [int16]-123} 4 {if($zero){return [int]0};return [int]123}
  5 {if($zero){return [decimal]0};return [decimal]12.3456}
  6 {if($zero){return [single]0};return [single]1.25} 7 {if($zero){return [double]0};return [double]1.25}
  8 {return [datetime]::FromOADate($(if($zero){0}else{45000.25}))}
  9 {if($empty){return ,([byte[]]::new(0))};return ,([byte[]]@(0,65,255))}
  10 {if($empty){return [string]::Empty};if($spec.name-eq'FixedText'){return 'ABCD'};return 'text'}
  11 {if($empty){return ,([byte[]]::new(0))};return ,([byte[]]@(0,65,255))}
  12 {if($empty){return [string]::Empty};return 'memo'}
  15 {if($zero){return '{00000000-0000-0000-0000-000000000000}'};return '{12345678-1234-5678-9abc-def012345678}'}
  default {throw 'Unsupported requested type'}
 }
}
function Mutate([string]$path,$spec,$op){
 $e=$db=$rs=$null;$record=@{operation=$op;accepted=$false;error=$null;endpoint='open'}
 try{$e=New-Object -ComObject DAO.DBEngine.36;$db=$e.OpenDatabase($path,$false,$false);$rs=$db.OpenRecordset('Rows',2)
  if($op.kind-eq'insert'){$record.endpoint='add_new';$rs.AddNew();Set-Cell $rs 'Id' ([int]$op.id)}else{$rs.FindFirst('Id = '+[int]$op.id);if($rs.NoMatch){throw 'Edit row missing'};$rs.Edit()}
  if($op.state-ne'omit'){$record.endpoint='assign';Set-Cell $rs 'Payload' (Payload $spec ([string]$op.state))}
  $record.endpoint='update';$rs.Update();$record.accepted=$true
 }catch{$record.error=Error-Record $_ $e;if($null-ne$rs-and$rs.EditMode-ne0){$rs.CancelUpdate()}}
 finally{if($null-ne$rs){$rs.Close()};Release $rs;if($null-ne$db){$db.Close()};Release $db;Release $e}
 return $record
}

$archive=Join-Path $env:JET3_WORK 'required-inputs.zip';$inputRoot=Join-Path $env:JET3_WORK 'required-inputs'
if($Replica-eq0){Expand-Archive -LiteralPath $archive -DestinationPath $inputRoot -ErrorAction Stop}
$matrixPath=Join-Path $inputRoot 'matrix.json';$matrix=Get-Content -Raw -Encoding UTF8 -LiteralPath $matrixPath|ConvertFrom-Json
if($Replica-eq0){
 $shell=Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe';$workers=@();$active=@();$failed=$false
 $names=@($matrix.creations|ForEach-Object{$_.case.id}|Select-Object -Unique)
 foreach($name in $names){
  $process=Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$PSCommandPath,'-Replica',1,'-CaseName',$name) -PassThru
  $active+=@{case=$name;process=$process}
  if($active.Count-eq4){foreach($item in $active){$item.process.WaitForExit();$code=[int]$item.process.ExitCode;$result=Join-Path $env:JET3_OUTBOX ($item.case+'-result.json');$workers+=@{case=$item.case;exit_code=$code;result=$(if(Test-Path -LiteralPath $result){Identity $result}else{$null})};if($code-ne0){$failed=$true};$item.process.Dispose()};$active=@()}
 }
 foreach($item in $active){$item.process.WaitForExit();$code=[int]$item.process.ExitCode;$result=Join-Path $env:JET3_OUTBOX ($item.case+'-result.json');$workers+=@{case=$item.case;exit_code=$code;result=$(if(Test-Path -LiteralPath $result){Identity $result}else{$null})};if($code-ne0){$failed=$true};$item.process.Dispose()}
 Write-Json @{document_type='required_column_acceptance_workers';archive=Identity $archive;matrix=Identity $matrixPath;script=Identity $PSCommandPath;workers=$workers} (Join-Path $env:JET3_OUTBOX 'workers.json');if($failed){exit 1};exit 0
}
function Capture-Saved([string]$path,[string]$name){
 $capture=Capture $path;$destination=Join-Path $env:JET3_OUTBOX $name;Copy-Item -LiteralPath $path -Destination $destination
 return @{file=$name;capture=$capture}
}
$result=@{document_type='required_column_acceptance';case=$CaseName;archive=Identity $archive;matrix=Identity $matrixPath;script=Identity $PSCommandPath;source_revision=$matrix.source_revision;environment=$null;creations=@();mutations=@();status='fail';error=$null}
try{
 $probe=New-Object -ComObject DAO.DBEngine.36
 try{$dll=@([Diagnostics.Process]::GetCurrentProcess().Modules|Where-Object{$_.ModuleName-ieq'dao360.dll'});$result.environment=@{provider='DAO.DBEngine.36';version=[string]$probe.Version;bits=32;os=[Environment]::OSVersion.VersionString;culture=[Globalization.CultureInfo]::CurrentCulture.Name;ansi=[Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage;dll_version=$dll[0].FileVersionInfo.FileVersion;dll_sha256=(Identity $dll[0].FileName).sha256}}finally{Release $probe}
 $creations=@($matrix.creations|Where-Object{$_.case.id-ceq$CaseName});if($creations.Count-ne2){throw 'Expected two creation pairs'}
 foreach($item in $creations){
  $candidate=Join-Path $inputRoot ([string]$item.candidate);$control=Join-Path $inputRoot ([string]$item.control)
  $result.creations+=,@{id=$item.id;candidate=Capture-Saved $candidate ($item.id+'-rust.mdb');control=Capture-Saved $control ($item.id+'-native.mdb')}
 }
 foreach($item in @($matrix.mutations|Where-Object{$_.case.id-ceq$CaseName})){
  $source=Join-Path $inputRoot ([string]$item.source);$native=Join-Path $env:JET3_WORK ($item.id+'-native.mdb');Copy-Item -LiteralPath $source -Destination $native
  $before=Identity $native;$operation=Mutate $native $item.case $item.operation
  $candidate=Join-Path $inputRoot ([string]$item.stage)
  $result.mutations+=,@{id=$item.id;operation=$operation;before=$before;candidate=Capture-Saved $candidate ($item.id+'-rust.mdb');control=Capture-Saved $native ($item.id+'-native.mdb')}
 }
 $result.status='pass'
}catch{$result.error=Error-Record $_ $null}
Write-Json $result (Join-Path $env:JET3_OUTBOX ($CaseName+'-result.json'));if($result.status-ne'pass'){exit 1}
