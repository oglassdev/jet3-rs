param([string]$CaseName = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$helper = Join-Path $env:JET3_WORK 'field_update.ps1'
$scalarHelper = Join-Path $env:JET3_WORK 'numeric_index_mutation.ps1'
$rowHelper = Join-Path $env:JET3_WORK 'allocation_lifecycle.cs'
Add-Type -Path $rowHelper
foreach ($source in @($helper, $scalarHelper)) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw 'Shared helper syntax' }
    $names=if($source -eq $helper){@('Identity','Release','Write-Json')}else{@('Read-Names','Read-Fields','Read-Indexes')}
    foreach($name in $names) {
        $found=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$false))
        if($found.Count -ne 1){throw "Missing shared helper: $name"};Invoke-Expression $found[0].Extent.Text
    }
}
function Failure($Record) {return @{endpoint=$script:endpoint;message=$Record.Exception.Message;hresult=$Record.Exception.HResult;stack=$Record.ScriptStackTrace}}
function Set-Value($Field,$Value) {
    $arguments=[object[]]::new(1);$arguments[0]=$Value
    [void]$Field.GetType().InvokeMember('Value',[Reflection.BindingFlags]::SetProperty,$null,$Field,$arguments)
}
function Set-RecipeRow($Handles,$Case,[int]$Id,[int]$Seed) {
    Set-Value $Handles[0] ([int]$Id)
    if([bool]$Case.payload) {
        $tag=if($Seed%13 -eq 0){[DBNull]::Value}else{[int]($Seed%17-8)};Set-Value $Handles[1] $tag
        $memo=if($Seed -eq 0){[DBNull]::Value}else{[AllocationRows]::Memo($Seed)};Set-Value $Handles[2] $memo
        $blob=if($Seed -eq 1){[DBNull]::Value}else{,[AllocationRows]::Ole($Seed)};Set-Value $Handles[3] $blob
    } else {for($c=0;$c -lt 4;$c++){Set-Value $Handles[$c+1] ([AllocationRows]::Pad($Seed,$c))}}
}
function Feed-Row($Hash,$Handles,$Case) {
    for($c=0;$c -lt $Handles.Count;$c++) {
        $value=$Handles[$c].Value;$bytes=$null
        if($value -isnot [DBNull]) {
            switch([int]$Case.fields[$c][1]) {
                4 {$bytes=[BitConverter]::GetBytes([int]$value)}
                11 {$bytes=[byte[]]$value}
                default {$bytes=[Text.Encoding]::GetEncoding(1252).GetBytes([string]$value)}
            }
        };[AllocationRows]::Feed($Hash,$bytes)
    }
}
function New-Control([string]$Path,$Case) {
    $engine=$workspaces=$workspace=$db=$tables=$table=$fields=$field=$indexes=$index=$keys=$key=$rs=$null;$handles=@()
    try {
        $engine=New-Object -ComObject DAO.DBEngine.36;$workspaces=$engine.Workspaces;$workspace=$workspaces.Item(0)
        $db=$workspace.CreateDatabase($Path,';LANGID=0x0409;CP=1252;COUNTRY=0',32);$tables=$db.TableDefs
        foreach($name in @('Items','Notes')) {
            $script:endpoint="$($Case.name)/create/$name";$table=$db.CreateTableDef($name);$fields=$table.Fields
            $specs=if($name -eq 'Items'){$Case.fields}else{@(@('Id',4,4,$false),@('Body',10,64,$false))}
            foreach($spec in $specs) {
                $field=$table.CreateField([string]$spec[0],[int]$spec[1],[int]$spec[2]);if([bool]$spec[3]){$field.Attributes=1}
                $fields.Append($field);Release $field;$field=$null
            }
            if($name -eq 'Items') {
                $indexes=$table.Indexes
                foreach($spec in $Case.indexes) {
                    $index=$table.CreateIndex([string]$spec.name);$keys=$index.Fields
                    $index.Primary=[bool]$spec.primary;$index.Unique=[bool]$spec.unique;$index.Required=[bool]$spec.required
                    foreach($component in $spec.fields) {
                        $key=$index.CreateField([string]$Case.fields[[int]$component[0]][0]);if([bool]$component[1]){$key.Attributes=1}
                        $keys.Append($key);Release $key;$key=$null
                    };$indexes.Append($index);Release $keys;$keys=$null;Release $index;$index=$null
                };Release $indexes;$indexes=$null
            };$tables.Append($table);Release $fields;$fields=$null;Release $table;$table=$null
        }
        $rs=$db.OpenRecordset('Notes',2);$fields=$rs.Fields;$rs.AddNew()
        $field=$fields.Item('Id');Set-Value $field ([int]7);Release $field;$field=$fields.Item('Body');Set-Value $field 'allocation-control';Release $field;$field=$null;$rs.Update()
        Release $fields;$fields=$null;$rs.Close();Release $rs;$rs=$db.OpenRecordset('Items',2);$fields=$rs.Fields
        foreach($spec in $Case.fields){$handles+=,$fields.Item([string]$spec[0])}
        for($id=0;$id -lt $Case.initial_count;$id++) {$script:endpoint="$($Case.name)/initial/$id";$rs.AddNew();Set-RecipeRow $handles $Case $id $id;$rs.Update()}
    } finally {
        foreach($handle in $handles){Release $handle};Release $field;Release $fields;Release $key;Release $keys;Release $index;Release $indexes;Release $table;Release $tables
        if($null -ne $rs){try{$rs.Close()}catch{}};Release $rs
        if($null -ne $db){try{$db.Close()}catch{}};Release $db;Release $workspace;Release $workspaces;Release $engine
    }
}
function Mutate([string]$Path,$Case,$Operations) {
    $before=Identity $Path;$engine=$db=$rs=$fields=$null;$handles=@()
    try {
        $engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$false);$rs=$db.OpenRecordset('Items',1);$rs.Index='ById';$fields=$rs.Fields
        foreach($spec in $Case.fields){$handles+=,$fields.Item([string]$spec[0])}
        foreach($operation in $Operations) {
            $script:endpoint="$($Case.name)/$($operation.kind)/$($operation.id)"
            if($operation.kind -eq 'insert') {$rs.AddNew();Set-RecipeRow $handles $Case ([int]$operation.id) ([int]$operation.seed);$rs.Update()}
            else {
                $old=if($operation.kind -eq 'replace'){$operation.old}else{$operation.id};$rs.Seek('=',[int]$old);if($rs.NoMatch){throw 'Mutation key absent'}
                if($operation.kind -eq 'delete'){$rs.Delete()}
                elseif($operation.kind -eq 'replace'){$rs.Edit();Set-RecipeRow $handles $Case ([int]$operation.id) ([int]$operation.seed);$rs.Update()}
                else{throw 'Unknown operation'}
            }
        }
    } finally {
        foreach($handle in $handles){Release $handle};Release $fields
        if($null -ne $rs){try{$rs.Close()}catch{}};Release $rs
        if($null -ne $db){try{$db.Close()}catch{}};Release $db;Release $engine
    };return @{before=$before;after=(Identity $Path);count=@($Operations).Count}
}
function Capture([string]$Path,$Case) {
    $before=Identity $Path;$engine=$db=$table=$tables=$rs=$fields=$hash=$null;$handles=@();$status='pass';$errorDetail=$null;$snapshot=@{}
    try {
        $script:endpoint="$($Case.name)/capture";$engine=New-Object -ComObject DAO.DBEngine.36;$db=$engine.OpenDatabase($Path,$false,$true)
        $snapshot=@{version=[string]$db.Version;tables=@(Read-Names $db.TableDefs|Sort-Object);queries=@(Read-Names $db.QueryDefs);relations=@(Read-Names $db.Relations);schema=@();count=0;digest='';notes=@();index_reads=@{}}
        foreach($name in @('Items','Notes')) {
            $tables=$db.TableDefs;$table=$tables.Item($name);Release $tables;$tables=$null
            $snapshot.schema+=,@{name=$name;attributes=[int]$table.Attributes;fields=@(Read-Fields $table);indexes=@(Read-Indexes $table)};Release $table;$table=$null
        }
        $rs=$db.OpenRecordset('Items',1);$rs.Index='ById';$fields=$rs.Fields
        foreach($spec in $Case.fields){$handles+=,$fields.Item([string]$spec[0])}
        $hash=[Security.Cryptography.SHA256]::Create();$previous=-1
        if(-not($rs.BOF -and $rs.EOF)){$rs.MoveFirst()}
        while(-not $rs.EOF){$id=[int]$handles[0].Value;if($id -le $previous){throw 'Unordered primary traversal'};$previous=$id;Feed-Row $hash $handles $Case;$snapshot.count++;$rs.MoveNext()}
        $snapshot.digest=[AllocationRows]::Finish($hash);$hash.Dispose();$hash=$null
        foreach($index in $Case.indexes) {
            $rs.Index=[string]$index.name;if(-not($rs.BOF -and $rs.EOF)){$rs.MoveFirst()};$ids=New-Object Collections.ArrayList
            while(-not $rs.EOF){[void]$ids.Add([int]$handles[0].Value);$rs.MoveNext()}
            $read=@{ids=@($ids.ToArray());seek=@()}
            foreach($query in $index.queries) {
                if($query.Count -ne $index.fields.Count -or $query.Count -ne 1){throw 'Complete one-field Seek arguments required'}
                $script:endpoint="$($Case.name)/seek/$($index.name)/$($query[0])";$rs.Seek('=',[int]$query[0]);$id=$null;$rowDigest=$null
                if(-not $rs.NoMatch){$id=[int]$handles[0].Value;$hash=[Security.Cryptography.SHA256]::Create();Feed-Row $hash $handles $Case;$rowDigest=[AllocationRows]::Finish($hash);$hash.Dispose();$hash=$null}
                $read.seek+=,@{query=$query;id=$id;digest=$rowDigest}
            };$snapshot.index_reads[[string]$index.name]=$read
        }
        foreach($handle in $handles){Release $handle};$handles=@();Release $fields;$fields=$null;$rs.Close();Release $rs;$rs=$db.OpenRecordset('Notes',4);$fields=$rs.Fields
        $idField=$fields.Item('Id');$bodyField=$fields.Item('Body')
        try{while(-not $rs.EOF){$snapshot.notes+=,@([int]$idField.Value,[string]$bodyField.Value);$rs.MoveNext()}}finally{Release $idField;Release $bodyField}
    } catch {$status='error';$errorDetail=Failure $_} finally {
        if($null -ne $hash){$hash.Dispose()};foreach($handle in $handles){Release $handle};Release $fields
        if($null -ne $rs){try{$rs.Close()}catch{}};Release $rs;Release $table;Release $tables
        if($null -ne $db){try{$db.Close()}catch{}};Release $db;Release $engine
    };return @{file=[IO.Path]::GetFileName($Path);before=$before;after=(Identity $Path);status=$status;error=$errorDetail;snapshot=$snapshot}
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'allocation-lifecycle.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $CaseName) {
    $workers = @(); $failed = $false
    $shell = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($case in $manifest.cases) {
        $name = [string]$case.name
        & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -CaseName $name
        $code = $LASTEXITCODE; $file = "$name-result.json"; $path = Join-Path $env:JET3_OUTBOX $file
        $pin = if (Test-Path -LiteralPath $path) { Identity $path } else { $null }
        $workers += @{ name = $name; exit_code = $code; file = $file; image = $pin }
        if ($code -ne 0 -or $null -eq $pin) { $failed = $true }
    }
    Write-Json @{ document_type = 'dao_allocation_lifecycle_workers'; source_revision = $manifest.source_revision;
        manifest_sha256 = (Identity $manifestPath).sha256; round = [string]$manifest.round; workers = $workers } (Join-Path $env:JET3_OUTBOX 'allocation-workers.json')
    if ($failed) { exit 1 }; exit 0
}
if (@($manifest.cases | Where-Object { $_.name -ceq $CaseName }).Count -ne 1) { throw 'Unknown worker case' }
$result = @{ document_type = 'dao_allocation_lifecycle_mutation_result'; source_revision = $manifest.source_revision; round = [string]$manifest.round;
    manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/allocation_lifecycle.ps1'), @($scalarHelper, 'oracle/windows-dao/scripts/numeric_index_mutation.ps1'), @($rowHelper, 'oracle/windows-dao/scripts/allocation_lifecycle.cs'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
        if ((Identity $pair[0]).sha256 -cne $manifest.inputs.($pair[1]).sha256) { throw 'Producer/helper identity differs' }
    }
    foreach ($property in $manifest.files.PSObject.Properties) {
        $actual = Identity (Join-Path $env:JET3_WORK $property.Name)
        if ($actual.sha256 -cne $property.Value.sha256 -or $actual.size -ne $property.Value.size) { throw "Input identity differs: $($property.Name)" }
    }
    $engine = New-Object -ComObject DAO.DBEngine.36
    try {
        $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
        if ($dll.Count -ne 1) { throw 'Loaded DAO module absent or ambiguous' }
        $result.environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; provider_version = [string]$engine.Version;
            os = [Environment]::OSVersion.VersionString; powershell = [string]$PSVersionTable.PSVersion; clr = [Environment]::Version.ToString();
            culture = [Globalization.CultureInfo]::CurrentCulture.Name; timezone = [TimeZoneInfo]::Local.Id;
            dll = @{ path = $dll[0].FileName; version = $dll[0].FileVersionInfo.FileVersion; sha256 = (Identity $dll[0].FileName).sha256 } }
    } finally { Release $engine }
    foreach ($case in $manifest.cases) {
        if ($case.name -cne $CaseName) { continue }
        $outcome = @{ name = [string]$case.name; status = 'running'; created = $null; stages = @(); native = @{}; roles = @{}; operation = $null; error = $null }; $result.cases += ,$outcome
        try {
            if ($manifest.round -eq 'continuation') {
                foreach ($role in @('candidate', 'control')) {
                    $source = if ($role -eq 'candidate') { $case.candidate_file } else { $case.source_file }
                    $path = Join-Path $env:JET3_WORK "$($case.name)-continued-$role.mdb"
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $source) -Destination $path
                    if ($role -eq 'control') { $outcome.operation = Mutate $path $case $case.operations }
                    $outcome.roles[$role] = Capture $path $case
                    if ($outcome.roles[$role].status -ne 'pass') { throw 'Continuation capture failed' }
                }
            } else {
                $control = Join-Path $env:JET3_WORK "$($case.name)-control-working.mdb"; New-Control $control $case
                $createdFile = "$($case.name)-control-created.mdb"; Copy-Item -LiteralPath $control -Destination (Join-Path $env:JET3_WORK $createdFile)
                $outcome.created = @{ file = $createdFile; image = (Identity $control) }
                foreach ($stage in $case.stages) {
                    $checkpoint = @{ name = [string]$stage.name; operations = $stage.operations; roles = @{}; mutation = $null }; $outcome.stages += ,$checkpoint
                    $checkpoint.mutation = Mutate $control $case $stage.operations
                    foreach ($role in @('candidate', 'control')) {
                        $path = Join-Path $env:JET3_WORK "$($case.name)-$($stage.name)-$role.mdb"
                        $source = if ($role -eq 'candidate') { Join-Path $env:JET3_WORK "$($case.name)-$($stage.name).mdb" } else { $control }
                        Copy-Item -LiteralPath $source -Destination $path
                        $checkpoint.roles[$role] = Capture $path $case
                        if ($checkpoint.roles[$role].status -ne 'pass') { throw 'Checkpoint capture failed' }
                    }
                }
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($case.name)-native-$role.mdb"
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($case.name)-mutated-$role.mdb") -Destination $path
                    $native = @{ mutation = (Mutate $path $case $case.native); capture = (Capture $path $case) }; $outcome.native[$role] = $native
                    if ($native.capture.status -ne 'pass') { throw 'Native follow-up capture failed' }
                }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json', '.bin', '.log') }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX "$CaseName-result.json")
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
