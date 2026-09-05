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
function Create-Table([string]$Path,$Arm){
    $engine=$workspace=$db=$table=$field=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0)
        $script:endpoint='create';$script:mutationStarted=$true;$db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32)
        $table=$db.CreateTableDef([string]$Arm.table);$field=$table.CreateField('Id',4);$table.Fields.Append($field);Release $field;$field=$null
        foreach($name in $Arm.columns){$field=$table.CreateField([string]$name,12);$table.Fields.Append($field);Release $field;$field=$null}
        $db.TableDefs.Append($table)
    }catch{if($null-eq $script:failure){$script:failure=Detail $_ $engine};throw}finally{Close-Object $field $false;Close-Object $table $false;Close-Object $db $true;Close-Object $workspace $false;Close-Object $engine $false}
}
function Set-Property([string]$Path,$Arm,$Checkpoint){
    $engine=$db=$table=$field=$null
    try{
        $script:endpoint=$Arm.name+'/'+$Checkpoint.name+'/open';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false)
        $table=$db.TableDefs.Item([string]$Arm.table);$field=$table.Fields.Item([string]$Arm.columns[[int]$Checkpoint.target])
        $script:endpoint=$Arm.name+'/'+$Checkpoint.name+'/AllowZeroLength';$field.AllowZeroLength=[bool]$Checkpoint.values[[int]$Checkpoint.target]
    }catch{if($null-eq $script:failure){$script:failure=Detail $_ $engine};throw}finally{Close-Object $field $false;Close-Object $table $false;Close-Object $db $true;Close-Object $engine $false}
}
function Capture([string]$Path,$Arm){
    $before=Identity $Path;$engine=$db=$table=$field=$rs=$null;$snapshot=@{};$detail=$null
    try{
        $script:endpoint=[IO.Path]::GetFileName($Path)+'/read';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $snapshot.version=[string]$db.Version;$snapshot.tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object)
        $snapshot.relations=@($db.Relations|ForEach-Object{[string]$_.Name});$snapshot.queries=@($db.QueryDefs|ForEach-Object{[string]$_.Name})
        $table=$db.TableDefs.Item([string]$Arm.table);$snapshot.attributes=[int]$table.Attributes;$snapshot.indexes=@($table.Indexes|ForEach-Object{[string]$_.Name});$fields=New-Object Collections.ArrayList
        foreach($field in $table.Fields){[void]$fields.Add(@{name=[string]$field.Name;type=[int]$field.Type;size=[int]$field.Size;attributes=[int]$field.Attributes;properties=(Field-Properties $field);allow_zero_length=if([int]$field.Type-eq 12){[bool]$field.AllowZeroLength}else{$null}});Release $field};$field=$null;$snapshot.fields=[object[]]$fields.ToArray()
        $rs=$db.OpenRecordset([string]$Arm.table,4);$count=0;while(-not $rs.EOF){$count++;$rs.MoveNext()};$snapshot.row_count=$count
    }catch{$detail=Detail $_ $engine;if($null-eq $script:failure){$script:failure=$detail}}finally{Close-Object $rs $true;Close-Object $field $false;Close-Object $table $false;Close-Object $db $true;Close-Object $engine $false}
    return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=if($null-eq $detail){'pass'}else{'error'};error=$detail;snapshot=$snapshot}
}
$planPath=Join-Path $env:JET3_WORK 'memo-property.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/memo_property.ps1'-or (Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/empty_long_values.ps1'){throw 'Producer input pins'}
$script:endpoint='start';$script:failure=$null;$script:mutationStarted=$false
$result=@{document_type='dao_memo_property_result';plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36'};mutation_started=$false;cases=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $case=@{arm=$arm.name;replica=$replica;captures=@();operations=@()};$result.cases+=,$case;$live=Join-Path $env:JET3_WORK "$($arm.name)-r$replica-live.mdb";Create-Table $live $arm
        foreach($checkpoint in $arm.checkpoints){
            if($null-ne $checkpoint.target){Set-Property $live $arm $checkpoint;$case.operations+=@{checkpoint=$checkpoint.name;column=$arm.columns[[int]$checkpoint.target];value=[bool]$checkpoint.values[[int]$checkpoint.target];status='complete'}}
            if($null-ne $script:failure){throw 'Mutation cleanup failure'}
            $path=Join-Path $env:JET3_WORK "$($arm.name)-r$replica-$($checkpoint.name).mdb";Copy-Item -LiteralPath $live -Destination $path
            $capture=Capture $path $arm;$case.captures+=@{checkpoint=$checkpoint.name;capture=$capture};if($capture.status-ne 'pass'-or $null-ne $script:failure){throw 'Capture failure'}
        }
    }}
}catch{if($null-eq $script:failure){$script:failure=Detail $_ $null}}finally{
    $result.error=$script:failure;$result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;message=$_.Exception.Message}}}
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
