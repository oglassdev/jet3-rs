Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:JET3_WORK 'field_update.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Pinned helper parse failure'}
foreach($name in @('Identity','Release','Write-Json','Observe')) {
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
function Append-Row($Recordset,$Row) {
    $Recordset.AddNew()
    foreach($ordinal in 0..2){
        $field=$Recordset.Fields.Item($ordinal)
        try { if($ordinal-eq 2){$field.Value=[string]$Row[$ordinal]}else{$field.Value=[int]$Row[$ordinal]} }
        catch{Remember $_;throw} finally{Close-Object $field $false}
    }
    $Recordset.Update()
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
            foreach($row in $spec.rows){$script:endpoint='insert/'+$spec.name;Append-Row $rs $row}
            Close-Object $rs $true;$rs=$null;Close-Object $table $false;$table=$null
        }
        if($null-ne $Arm.delete_id){
            $rs=$db.OpenRecordset([string]$Arm.table,2);$found=0
            while(-not $rs.EOF){if([int]$rs.Fields.Item('Id').Value-eq [int]$Arm.delete_id){$found++};$rs.MoveNext()}
            if($found-ne 1){throw 'Preparatory delete Id not unique'}
            $rs.MoveFirst();while([int]$rs.Fields.Item('Id').Value-ne [int]$Arm.delete_id){$rs.MoveNext()}
            $script:endpoint='prepare/delete';$rs.Delete();Close-Object $rs $true;$rs=$null
        }
        $query=$db.CreateQueryDef([string]$plan.query.name,[string]$plan.query.sql)
    }catch{Remember $_;throw}finally{
        Close-Object $rs $true;Close-Object $field $false;Close-Object $table $false;Close-Object $query $false
        Close-Object $db $true;Close-Object $workspace $false;Close-Object $engine $false
    }
}
function Mutate-Copy([string]$Source,[string]$Path,$Arm,[string]$Operation) {
    Copy-Item -LiteralPath $Source -Destination $Path
    $engine=$db=$rs=$null;$outcome=@{operation=$Operation;status='complete'}
    try {
        $script:mutationStarted=$true;$script:endpoint=$Operation+'/open'
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false)
        $rs=$db.OpenRecordset([string]$Arm.table,2)
        $script:endpoint=$Operation+'/append'
        if($Operation-eq 'insert'){Append-Row $rs $Arm.insert}else{Append-Row $rs $plan.insert}
    }catch{Remember $_;throw}finally{Close-Object $rs $true;Close-Object $db $true;Close-Object $engine $false}
    return $outcome
}
$planPath=Join-Path $env:JET3_WORK 'row-insert-candidate.plan.json'
$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
foreach($entry in @(@('row_insert_candidate.ps1',$PSCommandPath),@('field_update.ps1',(Join-Path $env:JET3_WORK 'field_update.ps1')))){
    if((Identity $entry[1]).sha256-cne $plan.inputs.('oracle/windows-dao/scripts/'+$entry[0])){throw 'Script pin mismatch'}
}
$phase=(Get-Content (Join-Path $env:JET3_WORK 'phase.txt') -Raw).Trim()
if($phase-notin @('create','observe')){throw 'Unknown phase'}
$failure=$null;$mutationStarted=$false;$endpoint='start'
$result=@{document_type='dao_row_insert_candidate_phase';phase=$phase;plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36'};observations=@();operations=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $prefix="$($arm.name)-r$replica";$original=Join-Path $env:JET3_WORK "$prefix-original.mdb";$control=Join-Path $env:JET3_WORK "$prefix-control.mdb"
        if($phase-eq 'create'){
            Create-Original $original $arm
            $result.operations+=@{arm=$arm.name;replica=$replica;role='control';result=(Mutate-Copy $original $control $arm 'insert')}
            $roles=@('original','control')
        }else{$roles=@('original','control','rust','control-next','rust-next')}
        foreach($role in $roles){
            $path=Join-Path $env:JET3_WORK "$prefix-$role.mdb"
            if($role.EndsWith('-next')){
                $source=Join-Path $env:JET3_WORK "$prefix-$($role.Replace('-next','')).mdb"
                $result.operations+=@{arm=$arm.name;replica=$replica;role=$role;result=(Mutate-Copy $source $path $arm 'continue')}
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
