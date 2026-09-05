Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) }
}
function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, ((ConvertTo-Json -InputObject $Value -Depth 30) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function Failure($Record) { return @{type=$Record.Exception.GetType().FullName;message=$Record.Exception.Message;stack=[string]$Record.ScriptStackTrace;endpoint=$script:endpoint} }
function Close-Value($Value,[bool]$Close) {
    if($null-eq $Value){return}
    try{if($Close){$Value.Close()};Release $Value}catch{if($null-eq $script:failure){$script:failure=Failure $_}}
}
function Read-Row($Recordset) {
    $values=[object[]]::new(3)
    for($i=0;$i-lt 3;$i++){$field=$Recordset.Fields.Item($i);try{$values[$i]=[int]$field.Value}finally{Close-Value $field $false}}
    return ,$values
}
function Create-Original([string]$Path,$Plan,$Arm) {
    $engine=$workspace=$db=$table=$field=$rs=$query=$index=$key=$null
    try {
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspace=$engine.Workspaces.Item(0)
        $script:mutationStarted=$true;$script:endpoint='create_database'
        $db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32)
        foreach($spec in $Plan.tables){
            $script:endpoint='schema/'+$spec.name;$table=$db.CreateTableDef([string]$spec.name)
            foreach($column in $Plan.fields){$field=$table.CreateField([string]$column.name,[int]$column.type,[int]$column.size);$table.Fields.Append($field);Close-Value $field $false;$field=$null}
            if($spec.name-eq $Arm.table){
                $index=$table.CreateIndex('ByKey');$index.Primary=[bool]$Arm.index.primary;$index.Unique=[bool]$Arm.index.unique
                foreach($definition in $Arm.index.fields){
                    $key=$index.CreateField([string]$definition.name)
                    if($definition.descending){$key.Attributes=1}
                    $index.Fields.Append($key);Close-Value $key $false;$key=$null
                }
                $table.Indexes.Append($index);Close-Value $index $false;$index=$null
            }
            $db.TableDefs.Append($table);$rs=$db.OpenRecordset([string]$spec.name,2)
            foreach($row in $spec.rows){
                $rs.AddNew()
                for($i=0;$i-lt 3;$i++){
                    $script:endpoint='assign/'+$spec.name+'/'+$Plan.fields[$i].name
                    $field=$rs.Fields.Item($i);try{$field.Value=[int]$row[$i]}catch{$script:failure=Failure $_;throw}finally{Close-Value $field $false;$field=$null}
                }
                $script:endpoint='update/'+$spec.name;$rs.Update()
            }
            Close-Value $rs $true;$rs=$null;Close-Value $table $false;$table=$null
        }
        $script:endpoint='query';$query=$db.CreateQueryDef([string]$Plan.query.name,[string]$Plan.query.sql)
    }catch{if($null-eq $script:failure){$script:failure=Failure $_};throw}finally{
        Close-Value $rs $true;Close-Value $query $false;Close-Value $key $false;Close-Value $index $false;Close-Value $field $false;Close-Value $table $false
        Close-Value $db $true;Close-Value $workspace $false;Close-Value $engine $false
    }
}
function Observe([string]$Path,$Arm) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $status = 'pass'; $errorDetail = $null; $endpoint = 'open_database'; $snapshot = [ordered]@{}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { @{name=[string]$_.Name; table=[string]$_.Table; foreign_table=[string]$_.ForeignTable; attributes=[int]$_.Attributes; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })} })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { @{name=[string]$_.Name; sql=[string]$_.SQL; type=[int]$_.Type} } | Sort-Object { $_.name })
        $snapshot.user_tables = @()
        foreach ($name in @($snapshot.tables | Where-Object { -not $_.StartsWith('MSys') })) {
            $endpoint = 'schema';$script:endpoint=$endpoint; $table = $db.TableDefs.Item([string]$name)
            $item = [ordered]@{name=[string]$table.Name; attributes=[int]$table.Attributes}
            $item.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes; required=[bool]$_.Required; allow_zero_length=[bool]$_.AllowZeroLength; default_value=[string]$_.DefaultValue} })
            $item.indexes = @($table.Indexes | ForEach-Object { @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls; fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes} })} })
            $endpoint = 'rows';$script:endpoint=$endpoint; $rs = $db.OpenRecordset([string]$name, 4)
            $rows = New-Object Collections.ArrayList
            while (-not $rs.EOF) {
                [void]$rows.Add((Read-Row $rs))
                $rs.MoveNext()
            }
            $item.rows = @($rows); Close-Value $rs $true; $rs = $null
            $snapshot.user_tables += $item; Release $table; $table = $null
        }
        $script:endpoint='traversal';$rs=$db.OpenRecordset([string]$Arm.table,1);$rs.Index='ByKey';$rs.MoveFirst()
        $traversal=New-Object Collections.ArrayList
        while(-not $rs.EOF){[void]$traversal.Add((Read-Row $rs));$rs.MoveNext()}
        $script:endpoint='seek';$seeks=New-Object Collections.ArrayList
        foreach($query in $Arm.queries){
            if($Arm.index.fields.Count-eq 1){$rs.Seek('=',[int]$query[0])}else{$rs.Seek('=',[int]$query[0],[int]$query[1])}
            $value=if($rs.NoMatch){$null}else{Read-Row $rs}
            [void]$seeks.Add(@{query=[object[]]$query;row=$value})
        }
        $snapshot.index_observations=@{traversal=@($traversal);seek=@($seeks)}
    } catch { $status='error';$errorDetail=Failure $_;$script:failure=$errorDetail }
    finally { Close-Value $rs $true;Close-Value $table $false;Close-Value $db $true;Close-Value $engine $false }
    return @{file=[IO.Path]::GetFileName($Path); before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'indexed-payload-update.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/indexed_payload_update.ps1') { throw 'Script pin mismatch' }
$phase = (Get-Content -LiteralPath (Join-Path $env:JET3_WORK 'phase.txt') -Raw).Trim()
if ($phase -notin @('create', 'observe')) { throw 'Unknown phase' }
$mutationStarted = $false;$failure=$null;$endpoint='start'
$result = [ordered]@{document_type='dao_indexed_payload_update_phase'; phase=$phase; plan_sha256=(Identity $planPath).sha256; environment=@{process_bits=32; provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}; mutation_started=$false; observations=@(); error=$null; retention_failures=@(); endpoint='start'}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $roles = if ($phase -eq 'create') { @('original') } else { @('original', 'updated') }
            foreach ($role in $roles) {
                $path = Join-Path $env:JET3_WORK "$($arm.name)-r$replica-$role.mdb"
                $result.endpoint = "$($arm.name)/$replica/$role"
                if ($phase -eq 'create') { Create-Original $path $plan $arm }
                $observation = Observe $path $arm
                $result.observations += @{arm=[string]$arm.name; replica=$replica; role=$role; observation=$observation}
                if ($observation.status -ne 'pass' -or $null-ne $failure) { throw 'Read-only observation failed' }
            }
        }
    }
} catch { $result.error=if($null-ne $failure){$failure}else{Failure $_} }
finally {
    $result.mutation_started = $mutationStarted
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb'){try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;error=$_.Exception.Message}}}
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if ($null -ne $result.error -or $result.retention_failures.Count) { exit 1 }
