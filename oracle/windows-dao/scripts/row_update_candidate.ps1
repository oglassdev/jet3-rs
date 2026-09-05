Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:JET3_WORK 'field_update.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Pinned helper parse failure'}
foreach($name in @('Identity','Release','Write-Json')) {
    $definitions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$false))
    if($definitions.Count-ne 1){throw 'Helper missing'}
    Invoke-Expression $definitions[0].Extent.Text
}
function Remember($Record) {
    if($null-eq $script:failure){$script:failure=@{endpoint=$script:endpoint; message=$Record.Exception.Message; stack=[string]$Record.ScriptStackTrace; hresult=[int]$Record.Exception.HResult}}
}
function Close-Object($Value,[bool]$Close) {
    if($null-eq $Value){return}
    try{if($Close){$Value.Close()}}catch{Remember $_}
    try{Release $Value}catch{Remember $_}
}
function Set-Row($Recordset,$Row) {
    foreach($ordinal in 0..4){
        $script:endpoint='field/'+[string]$ordinal;$field=$Recordset.Fields.Item($ordinal)
        try {
            if($null-eq $Row[$ordinal]){$field.Value=[DBNull]::Value}
            elseif($ordinal-le 1){$field.Value=[int]$Row[$ordinal]}
            elseif($ordinal-eq 2){$field.Value=[string]$Row[$ordinal]}
            elseif($ordinal-eq 3){
                $hex=[string]$Row[3];$bytes=[byte[]]::new($hex.Length/2)
                for($i=0;$i-lt $bytes.Length;$i++){$bytes[$i]=[Convert]::ToByte($hex.Substring(2*$i,2),16)}
                $field.Value=[byte[]]$bytes
            }else{$field.Value=[bool]$Row[$ordinal]}
        }catch{Remember $_;throw}finally{Close-Object $field $false}
    }
}
function Append-Row($Recordset,$Row) {
    $script:endpoint='add_new';$Recordset.AddNew();Set-Row $Recordset $Row
    $script:endpoint='insert_update';$Recordset.Update()
}
function Select-Row($Recordset,[int]$Id) {
    $Recordset.MoveFirst();$found=0
    while(-not $Recordset.EOF){if([int]$Recordset.Fields.Item('Id').Value-eq $Id){$found++};$Recordset.MoveNext()}
    if($found-ne 1){throw 'Id not unique'}
    $Recordset.MoveFirst();while([int]$Recordset.Fields.Item('Id').Value-ne $Id){$Recordset.MoveNext()}
}
function Create-Original([string]$Path,$Arm) {
    $engine=$workspace=$db=$table=$field=$rs=$query=$null
    try {
        $engine=New-Object -ComObject DAO.DBEngine.36; $workspace=$engine.Workspaces.Item(0)
        $script:mutationStarted=$true; $script:endpoint='create_database'
        $db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32)
        foreach($spec in $Arm.tables){
            $script:endpoint='schema/'+$spec.name; $table=$db.CreateTableDef([string]$spec.name)
            foreach($column in $plan.fields){
                $field=$table.CreateField([string]$column.name,[int]$column.type,[int]$column.size)
                $table.Fields.Append($field);Close-Object $field $false;$field=$null
            }
            $db.TableDefs.Append($table);$rs=$db.OpenRecordset([string]$spec.name,2)
            foreach($row in $spec.seed_rows){$script:endpoint='insert/'+$spec.name;Append-Row $rs $row}
            if($Arm.tombstone-and $spec.name-eq $Arm.table){Select-Row $rs 2;$script:endpoint='seed_delete';$rs.Delete()}
            Close-Object $rs $true;$rs=$null;Close-Object $table $false;$table=$null
        }
        $query=$db.CreateQueryDef([string]$plan.query.name,[string]$plan.query.sql)
    }catch{Remember $_;throw}finally{
        Close-Object $rs $true;Close-Object $field $false;Close-Object $table $false;Close-Object $query $false
        Close-Object $db $true;Close-Object $workspace $false;Close-Object $engine $false
    }
}
function Mutate-Copy([string]$Source,[string]$Path,$Arm,[string]$Operation) {
    Copy-Item -LiteralPath $Source -Destination $Path
    $engine=$db=$rs=$null;$outcome=@{operation=$Operation;status='complete';selected_id=$Arm.selected_id}
    try {
        $script:mutationStarted=$true;$script:endpoint=$Operation+'/open'
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false)
        $rs=$db.OpenRecordset([string]$Arm.table,2)
        if($Operation-eq 'update'){
            Select-Row $rs ([int]$Arm.selected_id)
            $script:endpoint='edit';$rs.Edit();Set-Row $rs $Arm.replacement
            $script:endpoint='row_update';$rs.Update()
        }else{$script:endpoint='follow-on insert';Append-Row $rs $plan.insert}
    }catch{Remember $_;throw}finally{Close-Object $rs $true;Close-Object $db $true;Close-Object $engine $false}
    return $outcome
}
function Observe([string]$Path) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $script:endpoint = 'open_database'; $snapshot = [ordered]@{}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { @{name=[string]$_.Name; table=[string]$_.Table; foreign_table=[string]$_.ForeignTable; attributes=[int]$_.Attributes; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })} })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { @{name=[string]$_.Name; sql=[string]$_.SQL; type=[int]$_.Type} } | Sort-Object { $_.name })
        $snapshot.user_tables = @()
        foreach ($name in @($snapshot.tables | Where-Object { -not $_.StartsWith('MSys') })) {
            $script:endpoint = 'schema'; $table = $db.TableDefs.Item([string]$name)
            $item = [ordered]@{name=[string]$table.Name; attributes=[int]$table.Attributes}
            $item.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes; required=[bool]$_.Required; allow_zero_length=[bool]$_.AllowZeroLength; default_value=[string]$_.DefaultValue} })
            $item.indexes = @($table.Indexes | ForEach-Object { @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes} })} })
            $script:endpoint = 'rows'; $rs = $db.OpenRecordset([string]$name, 4)
            $rows = New-Object Collections.ArrayList
            while (-not $rs.EOF) {
                $values=[object[]]::new(5)
                foreach($ordinal in 0..4){
                    $field=$rs.Fields.Item($ordinal)
                    try{$v=$field.Value
                        if($v-is [DBNull]){$values[$ordinal]=$null}
                        elseif($ordinal-le 1){$values[$ordinal]=[int]$v}
                        elseif($ordinal-eq 2){$values[$ordinal]=[string]$v}
                        elseif($ordinal-eq 3){$values[$ordinal]=([BitConverter]::ToString([byte[]]$v)).Replace('-','').ToLowerInvariant()}
                        else{$values[$ordinal]=[bool]$v}
                    }finally{Close-Object $field $false}
                }
                [void]$rows.Add($values)
                $rs.MoveNext()
            }
            $item.rows = @($rows); $rs.Close(); Release $rs; $rs = $null
            $snapshot.user_tables += $item; Release $table; $table = $null
        }
    } catch { Remember $_; $status = 'error'; $errorDetail = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
    finally {
        Close-Object $rs $true; Close-Object $table $false
        Close-Object $db $true; Close-Object $engine $false
    }
    return @{file=[IO.Path]::GetFileName($Path); before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath=Join-Path $env:JET3_WORK 'row-update-candidate.plan.json'
$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
foreach($entry in @(@('row_update_candidate.ps1',$PSCommandPath),@('field_update.ps1',(Join-Path $env:JET3_WORK 'field_update.ps1')))){
    if((Identity $entry[1]).sha256-cne $plan.inputs.('oracle/windows-dao/scripts/'+$entry[0])){throw 'Script pin mismatch'}
}
$phase=(Get-Content (Join-Path $env:JET3_WORK 'phase.txt') -Raw).Trim()
if($phase-notin @('create','observe')){throw 'Unknown phase'}
$failure=$null;$mutationStarted=$false;$endpoint='start'
$result=@{document_type='dao_row_update_candidate_phase';phase=$phase;plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36'};observations=@();operations=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$original=Join-Path $env:JET3_WORK "$prefix-original.mdb";$control=Join-Path $env:JET3_WORK "$prefix-control.mdb"
        if($phase-eq 'create'){
            Create-Original $original $arm
            $result.operations+=@{arm=$arm.name;replica=$replica;role='control';result=(Mutate-Copy $original $control $arm 'update')}
            $roles=@('original','control')
        }else{$roles=@('original','control','rust','control-next','rust-next')}
        foreach($role in $roles){
            $path=Join-Path $env:JET3_WORK "$prefix-$role.mdb"
            if($role.EndsWith('-next')){
                $source=Join-Path $env:JET3_WORK "$prefix-$($role.Replace('-next','')).mdb"
                $result.operations+=@{arm=$arm.name;replica=$replica;role=$role;result=(Mutate-Copy $source $path $arm 'insert')}
            }
            $observation=Observe $path
            $result.observations+=@{arm=$arm.name;replica=$replica;role=$role;observation=$observation}
            if($observation.status-ne 'pass'-or $null-ne $failure){throw 'Capture or cleanup failed'}
        }
    }}
}catch{Remember $_}finally{
    $result.error=$failure;$result.mutation_started=$mutationStarted
    [GC]::Collect();[GC]::WaitForPendingFinalizers()
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb'){
        try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;message=$_.Exception.Message}}
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $failure-or $result.retention_failures.Count){exit 1}
