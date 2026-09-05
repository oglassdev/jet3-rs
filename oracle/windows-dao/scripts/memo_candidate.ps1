Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'empty_long_values.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper parse failure'}
foreach($name in @('Identity','Release','Write-Json','Detail','Close-Object','Field-Properties')){
    $definitions=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($definitions.Count-ne 1){throw 'Missing helper'};Invoke-Expression $definitions[0].Extent.Text
}
function Append-Row($Rs,$Row){
    $field=$null
    try{
        $script:endpoint='insert/add_new';$Rs.AddNew();$field=$Rs.Fields.Item('Id');$script:endpoint='insert/Id';$field.Value=[int]$Row[0];Release $field;$field=$null
        $field=$Rs.Fields.Item(1);$script:endpoint='insert/Memo'
        if($null-eq $Row[1]){$field.Value=[DBNull]::Value}else{$field.Value=[string]$Row[1]}
        Release $field;$field=$null;$script:endpoint='insert/Update';$Rs.Update()
    }finally{Close-Object $field $false}
}
function Create-Control([string]$Path,$Arm){
    $engine=$workspace=$db=$table=$field=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0);$script:endpoint='create_database';$script:mutationStarted=$true
        $db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32);$table=$db.CreateTableDef([string]$Arm.table)
        $field=$table.CreateField('Id',4);$table.Fields.Append($field);Release $field;$field=$table.CreateField([string]$Arm.memo,12);$field.AllowZeroLength=$true;$table.Fields.Append($field);Release $field;$field=$null
        $db.TableDefs.Append($table);$rs=$db.OpenRecordset([string]$Arm.table,2)
        foreach($row in $Arm.rows){Append-Row $rs $row}
    }catch{if($null-eq $script:failure){$script:failure=Detail $_ $engine};throw}finally{Close-Object $rs $true;Close-Object $field $false;Close-Object $table $false;Close-Object $db $true;Close-Object $workspace $false;Close-Object $engine $false}
}
function Insert-Copy([string]$Source,[string]$Path,$Arm,$Row){
    Copy-Item -LiteralPath $Source -Destination $Path;$engine=$db=$rs=$null
    try{$script:endpoint='continuation/open';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset([string]$Arm.table,2);Append-Row $rs $Row}
    catch{if($null-eq $script:failure){$script:failure=Detail $_ $engine};throw}finally{Close-Object $rs $true;Close-Object $db $true;Close-Object $engine $false}
}
function Capture([string]$Path,$Arm){
    $before=Identity $Path;$engine=$db=$table=$field=$rs=$null;$snapshot=@{};$errorDetail=$null
    try{
        $script:endpoint='capture/open';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $script:endpoint='capture/schema';$snapshot.version=[string]$db.Version;$snapshot.tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object);$snapshot.relations=@($db.Relations|ForEach-Object{[string]$_.Name});$snapshot.queries=@($db.QueryDefs|ForEach-Object{[string]$_.Name})
        $table=$db.TableDefs.Item([string]$Arm.table);$snapshot.attributes=[int]$table.Attributes;$snapshot.indexes=@($table.Indexes|ForEach-Object{[string]$_.Name});$fields=New-Object Collections.ArrayList
        foreach($field in $table.Fields){[void]$fields.Add(@{name=[string]$field.Name;type=[int]$field.Type;size=[int]$field.Size;attributes=[int]$field.Attributes;properties=(Field-Properties $field);allow_zero_length=if([int]$field.Type-eq 12){[bool]$field.AllowZeroLength}else{$null}});Release $field};$field=$null;$snapshot.fields=[object[]]$fields.ToArray()
        $script:endpoint='capture/rows';$rs=$db.OpenRecordset([string]$Arm.table,4);$rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){
            $field=$rs.Fields.Item('Id');$id=[int]$field.Value;Release $field;$field=$rs.Fields.Item(1);$value=$field.Value;$isNull=$null-eq $value-or [Convert]::IsDBNull($value)
            [void]$rows.Add(@{id=$id;is_null=$isNull;payload=if($isNull){$null}else{[string]$value};field_size=[int]$field.FieldSize});Release $field;$field=$null;$rs.MoveNext()
        };$snapshot.rows=[object[]]$rows.ToArray()
    }catch{$errorDetail=Detail $_ $engine;if($null-eq $script:failure){$script:failure=$errorDetail}}finally{Close-Object $field $false;Close-Object $rs $true;Close-Object $table $false;Close-Object $db $true;Close-Object $engine $false}
    return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=if($null-eq $errorDetail){'pass'}else{'error'};error=$errorDetail;snapshot=$snapshot}
}
$planPath=Join-Path $env:JET3_WORK 'memo-candidate.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/memo_candidate.ps1'-or (Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/empty_long_values.ps1'){throw 'Producer pin mismatch'}
$script:failure=$null;$script:mutationStarted=$false;$script:endpoint='start'
$result=@{document_type='dao_memo_candidate_result';plan_sha256=(Identity $planPath).sha256;source_revision=$plan.source_revision;environment=@{process_bits=32;provider='DAO.DBEngine.36'};mutation_started=$false;pairs=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$pair=@{arm=$arm.name;replica=$replica;captures=@{};operations=@{}};$result.pairs+=,$pair
        $source=Join-Path $env:JET3_WORK "$($arm.name).mdb";$expected=$plan.images."$($arm.name).mdb";$actual=Identity $source
        if($actual.sha256-cne $expected.sha256-or $actual.size-ne $expected.size){throw 'Candidate image pin'}
        Copy-Item -LiteralPath $source -Destination (Join-Path $env:JET3_WORK "$prefix-candidate.mdb");Create-Control (Join-Path $env:JET3_WORK "$prefix-control.mdb") $arm
        foreach($role in @('candidate','control')){
            $path=Join-Path $env:JET3_WORK "$prefix-$role.mdb";$pair.captures[$role]=Capture $path $arm
            if($pair.captures[$role].status-ne 'pass'-or $null-ne $script:failure){throw 'Baseline failure'}
        }
        foreach($request in $plan.continuations){foreach($role in @('candidate','control')){
            $name="$role-$($request.name)";$path=Join-Path $env:JET3_WORK "$prefix-$name.mdb";Insert-Copy (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $path $arm $request.row
            if($null-ne $script:failure){throw 'Continuation cleanup failure'};$pair.operations[$name]=@{status='complete';row=$request.row};$pair.captures[$name]=Capture $path $arm
            if($pair.captures[$name].status-ne 'pass'-or $null-ne $script:failure){throw 'Continuation capture failure'}
        }}
    }}
}catch{if($null-eq $script:failure){$script:failure=Detail $_ $null}}finally{
    $result.error=$script:failure;$result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;message=$_.Exception.Message}}};Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
