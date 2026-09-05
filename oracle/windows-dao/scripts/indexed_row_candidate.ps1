Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'field_update.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper parse failure'}
foreach($name in @('Identity','Release','Write-Json')){
    $found=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing helper'};Invoke-Expression $found[0].Extent.Text
}
function Failure($Record){return @{type=$Record.Exception.GetType().FullName;message=$Record.Exception.Message;hresult=$Record.Exception.HResult;endpoint=$script:endpoint;stack=$Record.ScriptStackTrace}}
function Close($Object,[bool]$Database){if($null-eq $Object){return};try{if($Database){$Object.Close()}}catch{if($null-eq $script:failure){$script:failure=Failure $_}}finally{Release $Object}}
function Set-Long($Rs,[string]$Name,[int]$Value){
    $field=$null;$prior=$script:endpoint;$script:endpoint="$prior/field/$Name"
    try{$field=$Rs.Fields.Item($Name);$field.Value=[int]$Value}catch{if($null-eq $script:failure){$script:failure=Failure $_};throw}finally{Release $field;$script:endpoint=$prior}
}
function Append-Row($Rs,$Values){$Rs.AddNew();Set-Long $Rs 'Id' ([int]$Values[0]);Set-Long $Rs 'Value' ([int]$Values[1]);$Rs.Update()}
function Row($Rs){return ,([object[]]@([int]$Rs.Fields.Item('Id').Value,[int]$Rs.Fields.Item('Value').Value))}
function Create-Control([string]$Path,$Arm){
    $engine=$workspace=$db=$table=$index=$field=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0);$script:endpoint='control/create';$script:mutationStarted=$true
        $db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32);$table=$db.CreateTableDef('Items')
        foreach($name in @('Id','Value')){$field=$table.CreateField($name,4);$table.Fields.Append($field);Release $field;$field=$null}
        $index=$table.CreateIndex('ByKey');$index.Primary=[bool]$Arm.primary;$index.Unique=$true;$index.Required=[bool]$Arm.primary
        $field=$index.CreateField('Id');$field.Attributes=[int]$Arm.descending;$index.Fields.Append($field);Release $field;$field=$null;$table.Indexes.Append($index);$db.TableDefs.Append($table)
        $rs=$db.OpenRecordset('Items',2);foreach($row in $Arm.rows){$script:endpoint='control/seed';Append-Row $rs $row}
    }catch{if($null-eq $script:failure){$script:failure=Failure $_};throw}finally{Close $rs $true;Close $field $false;Close $index $false;Close $table $false;Close $db $true;Close $workspace $false;Close $engine $false}
}
function Mutate([string]$Path,$Arm,[string]$Mode){
    $engine=$db=$rs=$null;$operation=@{status='complete'}
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset('Items',1);$rs.Index='ByKey';$script:endpoint="mutate/$Mode"
        if($Mode-eq 'duplicate'){
            $accepted=$false;$errorDetail=$null;$numbers=@()
            try{Append-Row $rs $Arm.duplicate;$accepted=$true}catch{$errorDetail=Failure $_;$numbers=@($engine.Errors|ForEach-Object{[int]$_.Number})}
            $operation=@{accepted=$accepted;error=$errorDetail;numbers=$numbers}
            if(-not $accepted){$rs.CancelUpdate()}
            if($accepted-or $numbers-notcontains 3022){throw 'Expected duplicate-key rejection'}
        }elseif($Mode-eq 'next'){Append-Row $rs $Arm.follow}
        elseif($Arm.kind-eq 'insert'){Append-Row $rs $Arm.insert}
        else{foreach($id in $Arm.delete){$rs.Seek('=',[int]$id);if($rs.NoMatch){throw 'Deleted key absent'};$rs.Delete()}}
    }catch{if($null-eq $script:failure){$script:failure=Failure $_};throw}finally{Close $rs $true;Close $db $true;Close $engine $false}
    return $operation
}
function Capture([string]$Path,$Arm){
    $before=Identity $Path;$engine=$db=$table=$rs=$null;$snapshot=@{};$errorDetail=$null
    try{
        $script:endpoint='capture/open';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $snapshot.version=[string]$db.Version;$snapshot.tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object);$snapshot.relations=@($db.Relations|ForEach-Object{[string]$_.Name});$snapshot.queries=@($db.QueryDefs|ForEach-Object{[string]$_.Name})
        $table=$db.TableDefs.Item('Items');$item=@{name=[string]$table.Name;attributes=[int]$table.Attributes}
        $item.fields=@($table.Fields|ForEach-Object{@{name=[string]$_.Name;type=[int]$_.Type;size=[int]$_.Size;attributes=[int]$_.Attributes;required=[bool]$_.Required;allow_zero_length=[bool]$_.AllowZeroLength;default_value=[string]$_.DefaultValue}})
        $item.indexes=@($table.Indexes|ForEach-Object{@{name=[string]$_.Name;primary=[bool]$_.Primary;unique=[bool]$_.Unique;required=[bool]$_.Required;foreign=[bool]$_.Foreign;ignore_nulls=[bool]$_.IgnoreNulls;fields=@($_.Fields|ForEach-Object{@{name=[string]$_.Name;attributes=[int]$_.Attributes}})}})
        $script:endpoint='capture/rows';$rs=$db.OpenRecordset('Items',4);$rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){[void]$rows.Add((Row $rs));$rs.MoveNext()};$item.rows=[object[]]$rows.ToArray();$snapshot.user_tables=@($item);Close $rs $true;$rs=$null
        $script:endpoint='capture/index';$rs=$db.OpenRecordset('Items',1);$rs.Index='ByKey';$rows=New-Object Collections.ArrayList
        if(-not $rs.EOF){$rs.MoveFirst()};while(-not $rs.EOF){[void]$rows.Add((Row $rs));$rs.MoveNext()};$snapshot.traversal=[object[]]$rows.ToArray();$seeks=New-Object Collections.ArrayList
        foreach($query in $Arm.queries){$rs.Seek('=',[int]$query);$row=if($rs.NoMatch){$null}else{Row $rs};[void]$seeks.Add(@{query=[int]$query;row=$row})};$snapshot.seek=[object[]]$seeks.ToArray()
    }catch{$errorDetail=Failure $_;if($null-eq $script:failure){$script:failure=$errorDetail}}finally{Close $rs $true;Close $table $false;Close $db $true;Close $engine $false}
    return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=if($null-eq $errorDetail){'pass'}else{'error'};error=$errorDetail;snapshot=$snapshot}
}
$planPath=Join-Path $env:JET3_WORK 'indexed-row-candidate.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/indexed_row_candidate.ps1'-or (Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/field_update.ps1'){throw 'Producer input mismatch'}
$script:failure=$null;$script:mutationStarted=$false;$script:endpoint='start'
$result=@{document_type='dao_indexed_row_result';plan_sha256=(Identity $planPath).sha256;source_revision=$plan.source_revision;environment=@{process_bits=32;provider='DAO.DBEngine.36'};pairs=@();error=$null;retention_failures=@();mutation_started=$false}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$pair=@{arm=$arm.name;replica=$replica;captures=@{};operations=@{}};$result.pairs+=,$pair
        foreach($role in @('original','candidate')){$source=Join-Path $env:JET3_WORK "$($arm.name)-$role.mdb";$pin=$plan.images."$($arm.name)-$role.mdb";$actual=Identity $source;if($actual.sha256-cne $pin.sha256-or $actual.size-ne $pin.size){throw 'Public image pin'};Copy-Item $source (Join-Path $env:JET3_WORK "$prefix-$role.mdb")}
        $control=Join-Path $env:JET3_WORK "$prefix-control-original.mdb";Create-Control $control $arm
        foreach($role in @('original','candidate','control-original')){$pair.captures[$role]=Capture (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $arm;if($pair.captures[$role].status-ne 'pass'-or $null-ne $script:failure){throw 'Baseline capture failed'}}
        $path=Join-Path $env:JET3_WORK "$prefix-control.mdb";Copy-Item $control $path;$pair.operations.control=Mutate $path $arm 'control';$pair.captures.control=Capture $path $arm
        if($pair.captures.control.status-ne 'pass'-or $null-ne $script:failure){throw 'Control mutation/capture failed'}
        foreach($mode in @('next','duplicate')){foreach($role in @('candidate','control')){
            $name="$role-$mode";$path=Join-Path $env:JET3_WORK "$prefix-$name.mdb";Copy-Item (Join-Path $env:JET3_WORK "$prefix-$role.mdb") $path
            $pair.operations[$name]=Mutate $path $arm $mode;$pair.captures[$name]=Capture $path $arm
            if($pair.captures[$name].status-ne 'pass'-or $null-ne $script:failure){throw 'Continuation mutation/capture failed'}
        }}
    }}
}catch{if($null-eq $script:failure){$script:failure=Failure $_}}finally{
    $result.error=$script:failure;$result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){try{Copy-Item $file.FullName $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;error=$_.Exception.Message}}};Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
