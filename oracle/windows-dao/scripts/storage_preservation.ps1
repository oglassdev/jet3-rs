param([ValidateSet('native','observe','continue','failures')][string]$Mode='native')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
# Reuse only function definitions, without running the other suites.
foreach ($source in @('schema_candidate_observer.ps1','query_preservation_seed.ps1','relationship_forms_suite.ps1')) {
    $tokens=$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $source),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw "Invalid helper $source" }
    foreach ($f in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
        Invoke-Expression $f.Extent.Text
    }
}
$General=';LANGID=0x0409;CP=1252;COUNTRY=0'
$plan=Get-Content -Raw (Join-Path $PSScriptRoot 'plan.json') | ConvertFrom-Json
$engine=New-Object -ComObject DAO.DBEngine.36
$dll=@([Diagnostics.Process]::GetCurrentProcess().Modules|Where-Object{$_.ModuleName-ieq'dao360.dll'})[0]
$environment=@{bits=[IntPtr]::Size*8; os=[Environment]::OSVersion.VersionString; culture=[Globalization.CultureInfo]::CurrentCulture.Name; ansi=[Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage; dll_version=$dll.FileVersionInfo.FileVersion; dll_sha256=(Identity $dll.FileName).sha256}
Write-Json $environment (Join-Path $env:JET3_OUTBOX 'environment.json')

function Custom-Property($owner,[string]$name,[string]$value) {
    $p=$owner.CreateProperty($name,10,$value); $ps=$owner.Properties
    try { $ps.Append($p) } catch { throw ('Custom property '+$name+' on '+$owner.Name+': '+$_.Exception.Message) } finally { Release $p; Release $ps }
}
function Make-StorageTable($db,[string]$name,$columns) {
    $td=$db.CreateTableDef($name); $fs=$td.Fields; $tds=$xs=$x=$null
    try {
        foreach($c in $columns) {
            $type=@{boolean=1;byte=2;long=4;auto_increment=4;text=10;memo=12;long_binary=11}[[string]$c.type]
            $size=0; if(Has $c 'size'){$size=[int]$c.size}
            $f=$td.CreateField([string]$c.name,$type,$size)
            if($c.type-eq'auto_increment'){Set-Property $f 'Attributes' 16}
            if((Has $c 'required')-and$c.required){Set-Property $f 'Required' $true}
            $fs.Append($f); Release $f
        }
        $x=$td.CreateIndex('PrimaryKey'); Set-Property $x 'Primary' $true
        $xf=$x.CreateField('Id'); $xfs=$x.Fields; $xfs.Append($xf); Release $xf; Release $xfs
        $xs=$td.Indexes; $xs.Append($x); Release $x; $x=$null
        $x=$td.CreateIndex('ByText'); $xf=$x.CreateField('Text1'); $xfs=$x.Fields; $xfs.Append($xf); Release $xf; Release $xfs
        $xs.Append($x); Release $x; $x=$null
        $tds=$db.TableDefs; $tds.Append($td)
        Custom-Property $td 'OpaqueTableTag' ('retained '+$name)
        $f=$fs.Item('Text1'); Custom-Property $f 'OpaqueFieldTag' ('retained field '+$name); Release $f
    } finally { Release $x; Release $xs; Release $tds; Release $fs; Release $td }
}
function Assign-Cells($rs,$values,[bool]$inserting=$true) {
    $fs=$rs.Fields
    try {
        for($i=0;$i-lt$values.Count;$i++) {
            $f=$fs.Item($i)
            try {
                $cell=$values[$i]; $v=[DBNull]::Value
                if(-not$inserting-and([int]$f.Attributes-band16)) {
                    if($null-eq$cell-or-not(Has $cell 'long')-or[int]$cell.long-ne[int]$f.Value){throw 'Replacement changes immutable AutoNumber'}
                    continue
                }
                if($null-ne$cell) {
                    $p=@($cell.PSObject.Properties)[0]
                    switch($p.Name) {
                        'boolean' {$v=[bool]$p.Value}
                        'byte' {$v=[byte]$p.Value}
                        'long' {$v=[int]$p.Value}
                        'text' {$v=[string]$p.Value}
                        'memo' {$v=[string]$p.Value}
                        'long_binary' {$v=[byte[]]$p.Value}
                        default {throw ('Unknown cell '+$p.Name)}
                    }
                }
                Set-Property $f 'Value' $v
            } finally {Release $f}
        }
    } finally {Release $fs}
}
function Apply-Storage($db,$op) {
    $rs=$db.OpenRecordset([string]$op.table,1)
    try {
        $rs.Index='PrimaryKey'
        if($op.operation-eq'insert'){$rs.AddNew();Assign-Cells $rs $op.values;$rs.Update();return}
        $rs.Seek('=',[int]$op.id);if($rs.NoMatch){throw 'Missing mutation target'}
        if($op.operation-eq'delete'){$rs.Delete();return}
        $rs.Edit();Assign-Cells $rs $op.values $false;$rs.Update()
    } finally { try{$rs.CancelUpdate()}catch{}; $rs.Close();Release $rs }
}
function Capture-Storage([string]$path) {
    $before=Identity $path; $db=$engine.OpenDatabase($path,$false,$true)
    try {
        $queries=@();$qs=$db.QueryDefs
        try { foreach($q in $qs){$queries+=,(Read-Query $q);Release $q} } finally {Release $qs}
        $props=@();$ps=$db.Properties
        try {foreach($p in $ps){$props+=@{name=[string]$p.Name;type=[int]$p.Type;result=Safe-Value $p};Release $p}} finally{Release $ps}
        $snapshot=@{file=[IO.Path]::GetFileName($path);identity=$before;tables=@(Read-Tables $db);relations=@(Read-Relations $db);queries=$queries;database_properties=$props}
    } finally {$db.Close();Release $db;[GC]::Collect();[GC]::WaitForPendingFinalizers()}
    if((Identity $path).sha256-cne$before.sha256){throw 'Observer changed input'}
    Write-Json $snapshot (Join-Path $env:JET3_OUTBOX ([IO.Path]::GetFileNameWithoutExtension($path)+'.json'))
}
function Retain([string]$path,[string]$name) {
    $copy=Join-Path $env:JET3_WORK ($name+'.mdb');Copy-Item $path $copy
    Capture-Storage $copy
    Copy-Item $copy (Join-Path $env:JET3_OUTBOX ($name+'.mdb'))
    Write-Output $name
}
try {
    if($Mode-eq'observe') {
        foreach($file in Get-ChildItem $env:JET3_WORK -Filter '*.mdb'|Sort-Object Name){Capture-Storage $file.FullName}
    } elseif($Mode-eq'native') {
        foreach($case in $plan.cases) {
            $path=Join-Path $env:JET3_WORK ($case.name+'.mdb');$db=$engine.CreateDatabase($path,$General,32)
            try {
                Make-StorageTable $db 'Items' $case.columns
                Make-StorageTable $db 'Watch' @(@{name='Id';type='long'},@{name='Text1';type='text';size=255},@{name='Memo';type='memo'},@{name='Blob';type='long_binary'})
                $db.Execute("INSERT INTO Watch (Id,Text1) VALUES (1,'sentinel')",128)
                Set-Binary $db 'Watch' 'Id' 1 'Memo' 9000 7;Set-Binary $db 'Watch' 'Id' 1 'Blob' 6000 11
                foreach($values in $case.initial){Apply-Storage $db @{operation='insert';table='Items';values=$values}}
                if($case.kind-eq'sparse') {
                    # Leave both a fixed and variable storage-ID gap before the lifecycle.
                    $tds=$db.TableDefs;$td=$tds.Item('Items');$fs=$td.Fields
                    $f=$td.CreateField('RetiredFixed',4);$fs.Append($f);Release $f
                    $f=$td.CreateField('RetiredVariable',10,20);$fs.Append($f);Release $f
                    $db.Execute("UPDATE Items SET RetiredFixed=42, RetiredVariable='old bytes'",128)
                    $fs.Delete('RetiredFixed');$fs.Delete('RetiredVariable')
                    Release $fs;Release $td;Release $tds
                }
                foreach($spec in $plan.queries){$q=$db.CreateQueryDef([string]$spec.name,[string]$spec.sql);Custom-Property $q 'OpaqueQueryTag' ('retained '+$spec.name);Release $q}
                Custom-Property $db 'OpaqueDatabaseTag' 'database sentinel'
            } finally {$db.Close();Release $db}
            Retain $path ('native-'+$case.name+'-original')
            foreach($stage in $case.stages) {
                $db=$engine.OpenDatabase($path)
                try{foreach($op in $stage.operations){Apply-Storage $db $op}}finally{$db.Close();Release $db}
                Retain $path ('native-'+$case.name+'-'+$stage.name)
            }
        }
    } elseif($Mode-eq'continue') {
        foreach($case in $plan.cases) {
            foreach($role in @('candidate','native')) {
                $path=Join-Path $env:JET3_WORK ($role+'-'+$case.name+'-reused.mdb')
                $db=$engine.OpenDatabase($path)
                try{foreach($op in $case.continuation){Apply-Storage $db $op}}finally{$db.Close();Release $db}
                Retain $path ($role+'-'+$case.name+'-continued')
            }
        }
    } elseif($Mode-eq'failures') {
        $failures=Get-Content -Raw (Join-Path $PSScriptRoot 'failures.json')|ConvertFrom-Json
        $outcomes=@();$workspaces=$engine.Workspaces;$workspace=$workspaces.Item(0)
        try {
            foreach($job in $failures) {
                $source=Join-Path $env:JET3_WORK ('native-'+$job.case+'-original.mdb')
                $path=Join-Path $env:JET3_WORK ('work-'+$job.case+'-'+$job.name+'.mdb')
                Copy-Item $source $path
                $before=Identity $path;$failure=$null;$transaction=$false
                $db=$engine.OpenDatabase($path)
                try {
                    if($job.transaction){$workspace.BeginTrans();$transaction=$true}
                    if($job.edit_first){$db.Execute("UPDATE Items SET Text1='rolled back' WHERE Id=1",128)}
                    if($job.command-eq'schema'){Apply-Request $db $job.request}else{Apply-Storage $db $job.request}
                } catch {$failure=Error-Info $engine $_}
                finally {
                    if($transaction){$workspace.Rollback()}
                    $db.Close();Release $db;[GC]::Collect();[GC]::WaitForPendingFinalizers()
                }
                $outcomes+=@{case=[string]$job.case;name=[string]$job.name;before=$before;after=Identity $path;error=$failure}
                Retain $path ('failure-'+$job.case+'-'+$job.name)
                Write-Json $outcomes (Join-Path $env:JET3_OUTBOX 'failures.json')
            }
        } finally {Release $workspace;Release $workspaces}
    }
} finally {Release $engine;[GC]::Collect();[GC]::WaitForPendingFinalizers()}
