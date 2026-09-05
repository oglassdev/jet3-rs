Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
function Identity([string]$Path){return @{size=(Get-Item -LiteralPath $Path).Length;sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Release($Value){if($null-ne $Value-and [Runtime.InteropServices.Marshal]::IsComObject($Value)){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)}}
function Write-Json($Value,[string]$Path){[IO.File]::WriteAllText($Path,((ConvertTo-Json -InputObject $Value -Depth 30)+"`n"),(New-Object Text.UTF8Encoding($false)))}
function Detail($Record,$Engine){
    $numbers=@();if($null-ne $Engine){$numbers=@($Engine.Errors|ForEach-Object{[int]$_.Number})}
    return @{endpoint=$script:endpoint;type=$Record.Exception.GetType().FullName;message=$Record.Exception.Message;hresult=[int]$Record.Exception.HResult;numbers=$numbers;stack=[string]$Record.ScriptStackTrace}
}
function Close-Object($Value,[bool]$Close){
    if($null-eq $Value){return}
    try{if($Close){$Value.Close()};Release $Value}catch{if($null-eq $script:failure){$script:failure=Detail $_ $null}}
}
function Empty-Bytes {return ,([byte[]]::new(0))}
function Assign-Payload($Field,$Arm,[string]$State){
    if($State-eq 'null'){$Field.Value=[DBNull]::Value}
    elseif($Arm.type-eq 12){$text=if($State-eq 'empty'){[string]::Empty}else{'A'};$Field.Value=[string]$text}
    else{
        $bytes=if($State-eq 'empty'){Empty-Bytes}else{,([byte[]]@(65))}
        if($Arm.method-eq 'append'){$Field.AppendChunk([byte[]]$bytes)}else{$Field.Value=[byte[]]$bytes}
    }
}
function Create([string]$Path,$Arm){
    $engine=$workspace=$db=$table=$field=$rs=$null;$operations=New-Object Collections.ArrayList
    try{
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0)
        $script:endpoint='create_database';$script:mutationStarted=$true;$db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32)
        $script:endpoint='schema';$table=$db.CreateTableDef('Rows');$field=$table.CreateField('Id',4);$table.Fields.Append($field);Release $field
        $field=$table.CreateField('Payload',[int]$Arm.type)
        if($Arm.allow_zero_length){$script:endpoint='schema/allow_zero_length';$field.AllowZeroLength=$true}
        $table.Fields.Append($field);Release $field;$field=$null;$db.TableDefs.Append($table)
        $rs=$db.OpenRecordset('Rows',2);$id=0
        foreach($state in @('null','empty','one')){
            $id++;$script:endpoint="$state/add_new";$rs.AddNew();$field=$rs.Fields.Item('Id');$field.Value=[int]$id;Release $field;$field=$null
            $op=@{id=$id;state=$state;accepted=$false;error=$null}
            try{
                $field=$rs.Fields.Item('Payload');$script:endpoint="$state/assign";Assign-Payload $field $Arm $state;Release $field;$field=$null
                $script:endpoint="$state/update";$rs.Update();$op.accepted=$true
            }catch{
                $op.error=Detail $_ $engine
                # Only the planned empty attempt can produce a scientific negative.
                if($state-ne 'empty'-or $op.error.numbers.Count-eq 0){$script:failure=$op.error;throw}
                $script:endpoint='empty/cancel';$rs.CancelUpdate()
            }finally{Close-Object $field $false;$field=$null;[void]$operations.Add($op)}
        }
    }catch{if($null-eq $script:failure){$script:failure=Detail $_ $engine};throw}finally{
        Close-Object $rs $true;Close-Object $field $false;Close-Object $table $false;Close-Object $db $true;Close-Object $workspace $false;Close-Object $engine $false
    }
    return ,([object[]]$operations.ToArray())
}
function Field-Properties($Field){
    $properties=New-Object Collections.ArrayList
    foreach($property in $Field.Properties){
        $value=$property.Value;$nullValue=$null-eq $value-or [Convert]::IsDBNull($value)
        [void]$properties.Add(@{name=[string]$property.Name;type=[int]$property.Type;is_null=$nullValue;value=if($nullValue){$null}else{[string]$value}})
        Release $property
    }
    return ,([object[]]$properties.ToArray())
}
function Observe([string]$Path,$Arm){
    $before=Identity $Path;$engine=$db=$table=$field=$rs=$null;$snapshot=@{};$errorDetail=$null
    try{
        $script:endpoint='reopen';$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $script:endpoint='capture/schema';$snapshot.version=[string]$db.Version;$snapshot.tables=@($db.TableDefs|ForEach-Object{[string]$_.Name}|Sort-Object)
        $snapshot.relations=@($db.Relations|ForEach-Object{[string]$_.Name});$snapshot.queries=@($db.QueryDefs|ForEach-Object{[string]$_.Name})
        $table=$db.TableDefs.Item('Rows');$snapshot.attributes=[int]$table.Attributes;$snapshot.indexes=@($table.Indexes|ForEach-Object{[string]$_.Name})
        $fields=New-Object Collections.ArrayList
        foreach($field in $table.Fields){[void]$fields.Add(@{name=[string]$field.Name;type=[int]$field.Type;size=[int]$field.Size;attributes=[int]$field.Attributes;allow_zero_length=if([int]$field.Type-eq 12){[bool]$field.AllowZeroLength}else{$null};properties=(Field-Properties $field)});Release $field};$field=$null;$snapshot.fields=[object[]]$fields.ToArray()
        $script:endpoint='capture/rows';$rs=$db.OpenRecordset('Rows',4);$rows=New-Object Collections.ArrayList
        while(-not $rs.EOF){
            $field=$rs.Fields.Item('Id');$id=[int]$field.Value;Release $field;$field=$rs.Fields.Item('Payload')
            $value=$field.Value;$isNull=$null-eq $value-or [Convert]::IsDBNull($value)
            $payload=if($isNull){$null}elseif($Arm.type-eq 12){[string]$value}else{[BitConverter]::ToString([byte[]]$value).Replace('-','').ToLowerInvariant()}
            [void]$rows.Add(@{id=$id;is_null=$isNull;payload=$payload;field_size=[int]$field.FieldSize});Release $field;$field=$null;$rs.MoveNext()
        }
        $snapshot.rows=[object[]]$rows.ToArray()
    }catch{$errorDetail=Detail $_ $engine;if($null-eq $script:failure){$script:failure=$errorDetail}}finally{Close-Object $field $false;Close-Object $rs $true;Close-Object $table $false;Close-Object $db $true;Close-Object $engine $false}
    return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=if($null-eq $errorDetail){'pass'}else{'error'};error=$errorDetail;snapshot=$snapshot}
}
$planPath=Join-Path $env:JET3_WORK 'empty-long-values.plan.json';$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/empty_long_values.ps1'){throw 'Producer pin mismatch'}
$script:endpoint='start';$script:failure=$null;$script:mutationStarted=$false
$result=@{document_type='dao_empty_long_values_result';plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36'};mutation_started=$false;cases=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){
        $case=@{arm=$arm.name;replica=$replica;operations=@();capture=$null};$result.cases+=,$case
        $path=Join-Path $env:JET3_WORK "$($arm.name)-r$replica.mdb";$case.operations=Create $path $arm
        if($null-ne $script:failure){throw 'Creation cleanup failed'}
        $case.capture=Observe $path $arm;if($null-ne $script:failure-or $case.capture.status-ne 'pass'){throw 'Capture failed'}
    }}
}catch{if($null-eq $script:failure){$script:failure=Detail $_ $null}}finally{
    $result.error=$script:failure;$result.mutation_started=$script:mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb'){try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;message=$_.Exception.Message}}}
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
