Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'field_update.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper parse failure'}
foreach($name in @('Identity','Release','Write-Json','Observe')){
    $found=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing helper'};Invoke-Expression $found[0].Extent.Text
}
function Failure($Record){return @{type=$Record.Exception.GetType().FullName;message=$Record.Exception.Message;hresult=$Record.Exception.HResult;endpoint=$script:endpoint;stack=$Record.ScriptStackTrace}}
function Row($Rs){return ,([object[]]@([int]$Rs.Fields.Item('Id').Value,[int]$Rs.Fields.Item('Value').Value,[string]$Rs.Fields.Item('Payload').Value))}
function Capture([string]$Path,$Arm){
    $obs=Observe $Path;if($obs.status-ne 'pass'){return $obs}
    $engine=$db=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $rs=$db.OpenRecordset('Items',1);$rs.Index='ByKey';$rs.MoveFirst()
        $rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){[void]$rows.Add((Row $rs));$rs.MoveNext()}
        $obs.snapshot.traversal=[object[]]$rows.ToArray();$seeks=New-Object Collections.ArrayList
        foreach($query in $Arm.queries){$rs.Seek('=',[int]$query);$row=if($rs.NoMatch){$null}else{Row $rs};[void]$seeks.Add(@{query=[int]$query;row=$row})}
        $obs.snapshot.seek=[object[]]$seeks.ToArray()
    }catch{$obs.status='error';$obs.error=Failure $_}finally{if($null-ne $rs){$rs.Close()};Release $rs;if($null-ne $db){$db.Close()};Release $db;Release $engine}
    $obs.after=Identity $Path
    if($obs.before.sha256-cne $obs.after.sha256-or $obs.before.size-ne $obs.after.size){$obs.status='error';$obs.error='Read-only image changed'}
    return $obs
}
function Set-Field($Rs,[string]$Name,$Value){
    $prior=$script:endpoint;$script:endpoint="$prior/field/$Name";$field=$null
    try{
        $field=$Rs.Fields.Item($Name)
        if($Name-eq 'Payload'){$field.Value=[string]$Value}else{$field.Value=[int]$Value}
    }catch{$script:failure=Failure $_;throw}finally{Release $field;$script:endpoint=$prior}
}
function Append-Row($Rs,$Values){$Rs.AddNew();Set-Field $Rs 'Id' ([int]$Values[0]);Set-Field $Rs 'Value' ([int]$Values[1]);Set-Field $Rs 'Payload' ([string]$Values[2]);$Rs.Update()}
function Mutate([string]$Path,$Arm,[bool]$Continuation){
    $engine=$db=$rs=$null;$result=@{status='complete';duplicate=$null}
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false)
        if(-not $Continuation){
            $rs=$db.OpenRecordset(('SELECT * FROM [Items] WHERE [Id] = '+[string]$Arm.selected),2)
            if($rs.EOF){throw 'Selected key missing'}
            $script:mutationStarted=$true;$rs.Edit();Set-Field $rs 'Id' ([int]$Arm.replacement);$rs.Update()
        }else{
            $rs=$db.OpenRecordset('Items',2);$script:mutationStarted=$true;Append-Row $rs $Arm.follow
            $accepted=$false;$failure=$null;$numbers=@()
            try{Append-Row $rs $Arm.duplicate;$accepted=$true}catch{$failure=Failure $_;$numbers=@($engine.Errors|ForEach-Object{[int]$_.Number})}
            $result.duplicate=@{accepted=$accepted;error=$failure;numbers=$numbers}
            if(-not $accepted){$rs.CancelUpdate()}
            if($accepted-or $numbers-notcontains 3022){throw 'Expected duplicate-key rejection'}
        }
    }finally{
        if($null-ne $rs){try{$rs.Close()}catch{}};Release $rs
        if($null-ne $db){try{$db.Close()}catch{}};Release $db;Release $engine
    }
    return $result
}
$planPath=Join-Path $env:JET3_WORK 'single-leaf-key-successor.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/single_leaf_key_successor.ps1'-or (Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/field_update.ps1'){throw 'Input pin mismatch'}
$script:mutationStarted=$false;$script:endpoint='prepare';$script:failure=$null
$result=@{document_type='dao_single_leaf_key_result';plan_sha256=(Identity $planPath).sha256;source_revision=$plan.source_revision;environment=@{process_bits=32;provider='DAO.DBEngine.36';os=[Environment]::OSVersion.VersionString};mutation_started=$false;pairs=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$pair=@{arm=[string]$arm.name;replica=$replica;captures=@{};operations=@{}}
        $result.pairs+=,$pair
        foreach($role in @('original','candidate')){
            $source=Join-Path $env:JET3_WORK "$($arm.name)-$role.mdb";$path=Join-Path $env:JET3_WORK "$prefix-$role.mdb"
            $actual=Identity $source;$expected=$plan.images."$($arm.name)-$role.mdb"
            if($actual.sha256-cne $expected.sha256-or $actual.size-ne $expected.size){throw 'Pinned image mismatch'}
            Copy-Item -LiteralPath $source -Destination $path
            $script:endpoint="$prefix/$role/capture";$pair.captures[$role]=Capture $path $arm;if($pair.captures[$role].status-ne 'pass'){throw 'Capture failed'}
        }
        $control=Join-Path $env:JET3_WORK "$prefix-control.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$prefix-original.mdb") -Destination $control
        $script:endpoint="$prefix/control/update";$pair.operations.control=Mutate $control $arm $false
        $pair.captures.control=Capture $control $arm;if($pair.captures.control.status-ne 'pass'){throw 'Control capture failed'}
        foreach($role in @('candidate','control')){
            $post=Join-Path $env:JET3_WORK "$prefix-$role-next.mdb";Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$prefix-$role.mdb") -Destination $post
            $script:endpoint="$prefix/$role/continue";$pair.operations["$role-next"]=Mutate $post $arm $true
            $pair.captures["$role-next"]=Capture $post $arm;if($pair.captures["$role-next"].status-ne 'pass'){throw 'Continuation capture failed'}
        }
    }}
}catch{$result.error=if($null-ne $script:failure){$script:failure}else{Failure $_}}finally{
    $result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){
        try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;error=$_.Exception.Message}}
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
