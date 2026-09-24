Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$tokens = $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'relationship_forms_suite.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Invalid DAO helper' }
foreach ($f in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}, $false)) { Invoke-Expression $f.Extent.Text }
$TypeCodes = @{ long = 4; text = 10; memo = 12; long_binary = 11 }
function Apply-LocaleRequest($db, $r) {
    if ($r.operation -eq 'create_table') {
        Make-Table $db $r.table
    } elseif ($r.operation -eq 'create_column') {
        $tds=$db.TableDefs; $td=$tds.Item([string]$r.table); $fs=$td.Fields; $f=$null
        try {
            $size=0; if (Has $r.column 'size') { $size=[int]$r.column.size }
            $f=$td.CreateField([string]$r.column.name,$TypeCodes[[string]$r.column.type],$size)
            $fs.Append($f)
        } finally { Release $f; Release $fs; Release $td; Release $tds }
    } elseif ($r.operation -eq 'rename_index') {
        $tds=$db.TableDefs; $tds.Refresh(); $td=$tds.Item([string]$r.table); $xs=$td.Indexes; $x=$xs.Item([string]$r.index)
        try { Set-Property $x 'Name' ([string]$r.name) } finally { Release $x; Release $xs; Release $td; Release $tds }
    } elseif ($r.operation -eq 'set_column_properties') {
        $tds=$db.TableDefs; $tds.Refresh(); $td=$tds.Item([string]$r.table); $fs=$td.Fields; $f=$fs.Item([string]$r.column)
        try {
            $ps=$f.Properties; $p=$f.CreateProperty('Description',10,[string]$r.description)
            try { $ps.Append($p) } finally { Release $p; Release $ps }
        } finally { Release $f; Release $fs; Release $td; Release $tds }
    } else { Apply-Request $db $r }
}
$E = New-Object -ComObject DAO.DBEngine.36
$dll=@([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object {$_.ModuleName -ieq 'dao360.dll'})[0]
$result=@{environment=@{version=[string]$E.Version;bits=32;os=[Environment]::OSVersion.VersionString;ansi=[Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage;dll_version=$dll.FileVersionInfo.FileVersion;dll_sha256=(Identity $dll.FileName).sha256};inputs=@();edits=@()}
$jobs=Get-Content -Raw -Encoding UTF8 (Join-Path $env:JET3_WORK 'jobs.json') | ConvertFrom-Json
foreach ($job in $jobs.inputs) {
    $path=Join-Path $env:JET3_WORK ('input-'+$job.name+'.mdb'); $db=$null; $failure=$null
    try {
        $db=$E.CreateDatabase($path,[string]$job.locale,32)
        foreach($op in $job.ops){Apply-Op $db $op}
    } catch {$failure=Error-Info $E $_}
    finally {if($null -ne $db){$db.Close();Release $db}}
    Copy-Item $path (Join-Path $env:JET3_OUTBOX ('input-'+$job.name+'.mdb'))
    $result.inputs+=@{name=$job.name;identity=Identity $path;failure=$failure}
    Write-Output ('input '+$job.name+' '+$(if($failure){$failure.message}else{'ok'}))
}
foreach($case in $jobs.edits){
    $path=Join-Path $env:JET3_WORK ('native-'+$case.name+'.mdb');Copy-Item (Join-Path $env:JET3_WORK ('input-'+$case.input+'.mdb')) $path
    $before=Identity $path;$steps=@();$db=$null
    try {
        $db=$E.OpenDatabase($path)
        foreach($step in $case.steps){
            try {
                if(Has $step 'sql'){$db.Execute([string]$step.sql,128)}else{Apply-LocaleRequest $db $step.request}
                $steps+=@{ok=$true}
            } catch {$steps+=@{ok=$false;error=Error-Info $E $_};break}
        }
    } finally {if($null -ne $db){$db.Close();Release $db}}
    Copy-Item $path (Join-Path $env:JET3_OUTBOX ('native-'+$case.name+'.mdb'))
    $result.edits+=@{name=$case.name;input=$case.input;before=$before;after=Identity $path;steps=$steps}
    Write-Output ($case.name+' '+(($steps|ForEach-Object{if($_.ok){'ok'}else{'refused '+($_.error.numbers -join ',')}})-join ' '))
}
[IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'),((ConvertTo-Json $result -Depth 100)+"`n"),(New-Object Text.UTF8Encoding($false)))
Release $E
