Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'fixed_field_successor.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper parse failure'}
foreach($name in @('Identity','Release','Write-Json','From-Hex','To-Hex','Set-Value','Read-Value','Failure')){
    $found=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing helper'};Invoke-Expression $found[0].Extent.Text
}
function Variant($Definition,$Value){
    if($Definition.type-eq 1){return [bool]$Value};$raw=From-Hex ([string]$Value)
    switch([int]$Definition.type){
        2{return [byte]$raw[0]};3{return [int16][BitConverter]::ToInt16($raw,0)};4{return [int][BitConverter]::ToInt32($raw,0)}
        5{$scaled=[decimal][BitConverter]::ToInt64($raw,0)/[decimal]10000;return [decimal]$scaled}
        6{return [single][BitConverter]::ToSingle($raw,0)};7{return [double][BitConverter]::ToDouble($raw,0)}
        default{throw 'Unsupported Seek type'}
    }
}
function Row($Rs,$Arm){$values=[object[]]::new($Arm.fields.Count);for($i=0;$i-lt $values.Length;$i++){$values[$i]=Read-Value $Rs $Arm.fields[$i]};return ,$values}
function Append($Rs,$Arm,$Values){
    $Rs.AddNew();for($i=0;$i-lt $Arm.fields.Count;$i++){$script:endpoint="assign/$($Arm.name)/$($Arm.fields[$i].name)";Set-Value $Rs $Arm.fields[$i] $Values[$i]}
    $script:endpoint="update/$($Arm.name)";$Rs.Update()
}
function New-Control([string]$Path,$Arm){
    $engine=$workspace=$db=$table=$field=$index=$key=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0)
        $script:mutationStarted=$true;$script:endpoint='create_database';$db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32)
        $table=$db.CreateTableDef('Rows')
        foreach($column in $Arm.fields){$field=$table.CreateField([string]$column.name,[int]$column.type,[int]$column.size);$table.Fields.Append($field);Release $field;$field=$null}
        $index=$table.CreateIndex('ByKey');$index.Unique=$true;$index.Required=[bool]$Arm.required;$index.IgnoreNulls=[bool]$Arm.ignore
        for($i=0;$i-lt $Arm.directions.Count;$i++){$key=$index.CreateField([string]$Arm.fields[$i].name);if($Arm.directions[$i]){$key.Attributes=1};$index.Fields.Append($key);Release $key;$key=$null}
        $table.Indexes.Append($index);$db.TableDefs.Append($table);$rs=$db.OpenRecordset('Rows',2)
        foreach($row in $Arm.rows){Append $rs $Arm $row}
    }catch{$script:failure=Failure $_;throw}finally{
        if($null-ne $rs){try{$rs.Close()}catch{}};Release $rs;Release $key;Release $index;Release $field;Release $table
        if($null-ne $db){try{$db.Close()}catch{}};Release $db;Release $workspace;Release $engine
    }
}
function Capture([string]$Path,$Arm){
    $before=Identity $Path;$snapshot=@{};$errorDetail=$null;$status='pass';$engine=$db=$table=$rs=$null
    try{
        $script:endpoint='capture/open';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $snapshot.version=[string]$db.Version;$snapshot.tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object)
        $snapshot.relations=@($db.Relations|ForEach-Object{@{name=[string]$_.Name;table=[string]$_.Table;foreign_table=[string]$_.ForeignTable;attributes=[int]$_.Attributes;fields=@($_.Fields|ForEach-Object{@{name=[string]$_.Name;foreign_name=[string]$_.ForeignName}})}})
        $snapshot.queries=@($db.QueryDefs|ForEach-Object{@{name=[string]$_.Name;sql=[string]$_.SQL;type=[int]$_.Type}})
        $table=$db.TableDefs.Item('Rows');$snapshot.attributes=[int]$table.Attributes
        $snapshot.fields=@($table.Fields|ForEach-Object{@{name=[string]$_.Name;type=[int]$_.Type;size=[int]$_.Size;attributes=[int]$_.Attributes;required=[bool]$_.Required;default_value=[string]$_.DefaultValue}})
        $snapshot.indexes=@($table.Indexes|ForEach-Object{@{name=[string]$_.Name;primary=[bool]$_.Primary;unique=[bool]$_.Unique;required=[bool]$_.Required;ignore_nulls=[bool]$_.IgnoreNulls;foreign=[bool]$_.Foreign;fields=@($_.Fields|ForEach-Object{@{name=[string]$_.Name;attributes=[int]$_.Attributes}})}})
        $rs=$db.OpenRecordset('Rows',4);$rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){[void]$rows.Add((Row $rs $Arm));$rs.MoveNext()};$snapshot.rows=[object[]]$rows.ToArray();$rs.Close();Release $rs;$rs=$null
        $rs=$db.OpenRecordset('Rows',1);$rs.Index='ByKey';$rs.MoveFirst();$rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){[void]$rows.Add((Row $rs $Arm));$rs.MoveNext()};$snapshot.traversal=[object[]]$rows.ToArray();$seeks=New-Object Collections.ArrayList
        foreach($query in $Arm.queries){$first=Variant $Arm.fields[0] $query[0];if($Arm.directions.Count-eq 1){$rs.Seek('=',$first)}else{$second=Variant $Arm.fields[1] $query[1];$rs.Seek('=',$first,$second)};$row=if($rs.NoMatch){$null}else{Row $rs $Arm};[void]$seeks.Add(@{query=[object[]]$query;row=$row})}
        $snapshot.seek=[object[]]$seeks.ToArray()
    }catch{$status='error';$errorDetail=Failure $_}finally{if($null-ne $rs){try{$rs.Close()}catch{}};Release $rs;Release $table;if($null-ne $db){try{$db.Close()}catch{}};Release $db;Release $engine}
    return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=$status;error=$errorDetail;snapshot=$snapshot}
}
function Probe([string]$Path,$Arm,$Request){
    $engine=$db=$rs=$null;$accepted=$false;$detail=$null;$numbers=@()
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset('Rows',2)
        try{Append $rs $Arm $Request.row;$accepted=$true}catch{$detail=Failure $_;$numbers=@($engine.Errors|ForEach-Object{[int]$_.Number})}
        if(-not $accepted){$rs.CancelUpdate()}
    }finally{if($null-ne $rs){try{$rs.Close()}catch{}};Release $rs;if($null-ne $db){try{$db.Close()}catch{}};Release $db;Release $engine}
    return @{accepted=$accepted;error=$detail;numbers=$numbers}
}
$planPath=Join-Path $env:JET3_WORK 'numeric-index.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/numeric_index.ps1'-or (Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/fixed_field_successor.ps1'){throw 'Producer pin mismatch'}
$script:mutationStarted=$false;$script:endpoint='start';$script:failure=$null
$result=@{document_type='dao_numeric_index_result';plan_sha256=(Identity $planPath).sha256;source_revision=$plan.source_revision;environment=@{process_bits=32;provider='DAO.DBEngine.36';os=[Environment]::OSVersion.VersionString};mutation_started=$false;pairs=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$pair=@{arm=[string]$arm.name;replica=$replica;captures=@{};probes=@{}};$result.pairs+=,$pair
        $candidate=Join-Path $env:JET3_WORK "$prefix-candidate.mdb";$source=Join-Path $env:JET3_WORK "$($arm.name).mdb";$expected=$plan.images."$($arm.name).mdb";$actual=Identity $source
        if($expected.sha256-cne $actual.sha256-or $expected.size-ne $actual.size){throw 'Candidate pin mismatch'};Copy-Item -LiteralPath $source -Destination $candidate
        $control=Join-Path $env:JET3_WORK "$prefix-control.mdb";New-Control $control $arm
        foreach($role in @('candidate','control')){
            $path=Join-Path $env:JET3_WORK "$prefix-$role.mdb";$pair.captures[$role]=Capture $path $arm
            if($pair.captures[$role].status-ne 'pass'){throw 'Baseline capture failed'}
        }
        foreach($request in $arm.probes){foreach($role in @('candidate','control')){
            $name="$role-$($request.name)";$path=Join-Path $env:JET3_WORK "$prefix-$name.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$prefix-$role.mdb") -Destination $path
            $pair.probes[$name]=Probe $path $arm $request
            if($pair.probes[$name].accepted-or $pair.probes[$name].numbers-notcontains ([int]$request.number)){throw 'Probe outcome differs'}
            $pair.captures[$name]=Capture $path $arm;if($pair.captures[$name].status-ne 'pass'){throw 'Probe capture failed'}
        }}
    }}
}catch{$result.error=if($null-ne $script:failure){$script:failure}else{Failure $_}}finally{
    $result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;error=$_.Exception.Message}}}
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
