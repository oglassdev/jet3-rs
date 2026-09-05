Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'field_update.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Helper syntax'}
foreach($name in @('Identity','Release','Write-Json')){
    $found=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing helper'};Invoke-Expression $found[0].Extent.Text
}
function Row($Rs,[string]$Table){
    $id=[int]$Rs.Fields.Item('Id').Value
    if($Table-eq 'Notes'){
        $body=$Rs.Fields.Item('Body').Value
        return ,([object[]]@($id,$(if($body-is [DBNull]){$null}else{[string]$body})))
    }
    $price=$Rs.Fields.Item('Price').Value
    return ,([object[]]@($id,[string]$Rs.Fields.Item('Name').Value,$(if($price-is [DBNull]){$null}else{([decimal]$price).ToString('0.0000',[Globalization.CultureInfo]::InvariantCulture)}),[bool]$Rs.Fields.Item('Active').Value))
}
function Capture([string]$Path){
    $before=Identity $Path;$engine=$db=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $snapshot=@{version=[string]$db.Version;tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object);queries=@($db.QueryDefs|ForEach-Object{[string]$_.Name});relations=@($db.Relations|ForEach-Object{[string]$_.Name});user_tables=@()}
        foreach($name in @('Items','Notes')){
            $table=$db.TableDefs.Item($name)
            try{
                $item=@{name=$name;attributes=[int]$table.Attributes}
                $item.fields=@($table.Fields|ForEach-Object{@{name=[string]$_.Name;type=[int]$_.Type;size=[int]$_.Size;attributes=[int]$_.Attributes;required=[bool]$_.Required;allow_zero_length=[bool]$_.AllowZeroLength;default_value=[string]$_.DefaultValue}})
                $item.indexes=@($table.Indexes|ForEach-Object{@{name=[string]$_.Name;primary=[bool]$_.Primary;unique=[bool]$_.Unique;required=[bool]$_.Required;foreign=[bool]$_.Foreign;ignore_nulls=[bool]$_.IgnoreNulls;fields=@($_.Fields|ForEach-Object{@{name=[string]$_.Name;attributes=[int]$_.Attributes}})}})
                $rs=$db.OpenRecordset($name,4);$rows=New-Object Collections.ArrayList
                while(-not $rs.EOF){[void]$rows.Add((Row $rs $name));$rs.MoveNext()}
                $item.rows=[object[]]$rows.ToArray();$snapshot.user_tables+=,$item;$rs.Close();Release $rs;$rs=$null
            }finally{Release $table}
        }
        $rs=$db.OpenRecordset('Items',1);$rs.Index='ById';$rows=New-Object Collections.ArrayList
        if(-not $rs.EOF){$rs.MoveFirst()};while(-not $rs.EOF){[void]$rows.Add((Row $rs 'Items'));$rs.MoveNext()}
        $snapshot.traversal=[object[]]$rows.ToArray();$seeks=New-Object Collections.ArrayList
        foreach($query in -1..201){$rs.Seek('=',[int]$query);$row=if($rs.NoMatch){$null}else{Row $rs 'Items'};[void]$seeks.Add(@{query=[int]$query;row=$row})}
        $snapshot.seek=[object[]]$seeks.ToArray()
    }finally{if($null-ne $rs){$rs.Close();Release $rs};if($null-ne $db){$db.Close();Release $db};Release $engine}
    return @{before=$before;after=(Identity $Path);snapshot=$snapshot}
}
function Mutate([string]$Path,[int]$Id,[bool]$Duplicate){
    $engine=$db=$rs=$null
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset('Items',2)
        $script:mutationStarted=$true
        try{
            $rs.AddNew();$rs.Fields.Item('Id').Value=[int]$Id;$rs.Fields.Item('Name').Value=[string]('x'*80)
            $rs.Fields.Item('Price').Value=$(if($Id%2-eq 0){[DBNull]::Value}else{[decimal]::Parse('-12.3456',[Globalization.CultureInfo]::InvariantCulture)})
            $rs.Fields.Item('Active').Value=[bool]($Id%2-ne 0);$rs.Update()
        }catch{
            $numbers=@($engine.Errors|ForEach-Object{[int]$_.Number})
            if(-not $Duplicate-or $numbers-notcontains 3022){throw}
            $rs.CancelUpdate();return @{status='duplicate';numbers=$numbers}
        }
        if($Duplicate){throw 'Duplicate accepted'}
        return @{status='inserted'}
    }finally{if($null-ne $rs){$rs.Close();Release $rs};if($null-ne $db){$db.Close();Release $db};Release $engine}
}
$planPath=Join-Path $env:JET3_WORK 'indexed-boundary.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
foreach($pair in @(@($PSCommandPath,'oracle/windows-dao/scripts/indexed_boundary.ps1'),@($helper,'oracle/windows-dao/scripts/field_update.ps1'))){if((Identity $pair[0]).sha256-cne $plan.inputs.($pair[1])){throw 'Producer pin'}}
$script:mutationStarted=$false
$result=@{document_type='dao_indexed_boundary_result';plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36'};captures=@{};operations=@{};error=$null;mutation_started=$false;retention_failures=@()}
try{
    foreach($arm in $plan.arms){
        foreach($role in @('original','candidate')){
            $name="$($arm.name)-$role.mdb";$path=Join-Path $env:JET3_WORK $name;$pin=$plan.images.$name;$actual=Identity $path
            if($actual.sha256-cne $pin.sha256-or $actual.size-ne $pin.size){throw 'Image pin'}
            $result.captures[$name]=Capture $path
        }
        $name="$($arm.name)-control.mdb";$path=Join-Path $env:JET3_WORK $name
        Copy-Item (Join-Path $env:JET3_WORK "$($arm.name)-original.mdb") $path
        if($arm.name-ne 'split'){$result.operations[$arm.name]=Mutate $path ([int]$arm.id) ($arm.name-eq 'duplicate')}
        $result.captures[$name]=Capture $path
    }
}catch{$result.error=@{message=$_.Exception.Message;stack=$_.ScriptStackTrace}}finally{
    $result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb'){try{Copy-Item $file.FullName $env:JET3_OUTBOX}catch{$result.retention_failures+=,$file.Name}}
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
