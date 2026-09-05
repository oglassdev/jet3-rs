Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}
function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function New-Control([string]$Path, $Tables) {
    $engine = $workspace = $db = $table = $field = $index = $key = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($spec in $Tables) {
            $table = $db.CreateTableDef([string]$spec.name)
            foreach ($name in @('Id', 'Tag')) {
                $field = $table.CreateField($name, 4)
                if ($name -eq 'Id') { $field.Attributes = 16 }
                $table.Fields.Append($field)
                Release $field; $field = $null
            }
            if ($spec.indexed) {
                $index = $table.CreateIndex('PrimaryKey'); $index.Primary = $true
                $key = $index.CreateField('Id'); $index.Fields.Append($key)
                $table.Indexes.Append($index)
                Release $key; $key = $null; Release $index; $index = $null
            }
            $db.TableDefs.Append($table)
            $rs = $db.OpenRecordset([string]$spec.name, 2)
            foreach ($n in 1..([int]$spec.count)) {
                $rs.AddNew()
                $rs.Fields.Item('Tag').Value = if ($spec.name -eq 'Later') { -1 } else { $n }
                $rs.Update()
            }
            $rs.Close(); Release $rs; $rs = $null; Release $table; $table = $null
        }
    } finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $key; Release $index; Release $field; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $workspace; Release $engine
    }
}
function Read-Row($Recordset) { return ,@([int]$Recordset.Fields.Item('Id').Value, [int]$Recordset.Fields.Item('Tag').Value) }
function Observe([string]$Path, $Tables, [bool]$Post) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $endpoint = 'open_database'; $status = 'pass'; $errorDetail = $null; $snapshot = [ordered]@{}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.user_tables = @()
        foreach ($spec in $Tables) {
            $endpoint = 'schema'
            $table = $db.TableDefs.Item([string]$spec.name)
            $item = [ordered]@{name=[string]$table.Name; attributes=[int]$table.Attributes}
            $item.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes} })
            $item.indexes = @($table.Indexes | ForEach-Object {
                @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required; foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls;
                  fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; attributes=[int]$_.Attributes; descending=(([int]$_.Attributes -band 1) -ne 0)} })}
            })
            $endpoint = 'rows'; $rs = $db.OpenRecordset([string]$spec.name, 4)
            $rows = New-Object Collections.ArrayList
            while (-not $rs.EOF) { [void]$rows.Add((Read-Row $rs)); $rs.MoveNext() }
            $item.rows = @($rows); $rs.Close(); Release $rs; $rs = $null
            $item.traversal = @(); $item.seek = @()
            if ($spec.indexed) {
                $endpoint = 'index_traversal'; $rs = $db.OpenRecordset([string]$spec.name, 1); $rs.Index = 'PrimaryKey'
                $rs.MoveFirst(); $traversal = New-Object Collections.ArrayList
                while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs)); $rs.MoveNext() }
                $item.traversal = @($traversal); $endpoint = 'seek'; $seeks = New-Object Collections.ArrayList
                $count = [int]$spec.count + [int]$Post
                foreach ($n in 1..$count) {
                    $rs.Seek('=', [int]$n)
                    $row = if ($rs.NoMatch) { $null } else { Read-Row $rs }
                    [void]$seeks.Add(@{query=$n; row=$row})
                }
                $item.seek = @($seeks); $rs.Close(); Release $rs; $rs = $null
            }
            $snapshot.user_tables += $item
            Release $table; $table = $null
        }
        $endpoint = 'complete'
    } catch { $status = 'fail'; $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>') }
    finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
function Insert-One([string]$Path, $Tables) {
    $engine = $db = $rs = $null
    $result = @{status='pass'; error=$null; ids=@()}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $false)
        foreach ($spec in $Tables) {
            $rs = $db.OpenRecordset([string]$spec.name, 2)
            $rs.AddNew(); $rs.Fields.Item('Tag').Value = 1001; $rs.Update()
            $rs.Bookmark = $rs.LastModified
            $result.ids += [int]$rs.Fields.Item('Id').Value
            $rs.Close(); Release $rs; $rs = $null
        }
    } catch { $result.status = 'fail'; $result.error = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>') }
    finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs
        if ($null -ne $db) { $db.Close() }; Release $db; Release $engine
    }
    return $result
}
$planPath = Join-Path $env:JET3_WORK 'autoincrement-candidate.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/autoincrement_candidate.ps1') { throw 'Script pin mismatch' }
$arms = @('unindexed', 'indexed', 'multi')
foreach ($name in $arms) {
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "$name-candidate-r$replica-initial.mdb"
        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$name.mdb") -Destination $path
        $actual = Identity $path; $expected = $plan.candidates.$name
        if ($actual.size -ne $expected.size -or $actual.sha256 -cne $expected.sha256) { throw 'Candidate pin mismatch' }
    }
}
$mutationStarted = $false
$result = [ordered]@{document_type='dao_autoincrement_candidate_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=32; os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); error=$null}
try {
    foreach ($name in $arms) {
        foreach ($replica in 1..3) {
            $tables = $plan.arms.$name
            New-Control (Join-Path $env:JET3_WORK "$name-control-r$replica-initial.mdb") $tables
            $pair = @{arm=$name; replica=$replica}
            foreach ($role in @('control', 'candidate')) {
                $initial = Join-Path $env:JET3_WORK "$name-$role-r$replica-initial.mdb"
                $post = Join-Path $env:JET3_WORK "$name-$role-r$replica-post.mdb"
                $observation = Observe $initial $tables $false
                Copy-Item -LiteralPath $initial -Destination $post
                $copyBefore = Identity $post
                $insert = Insert-One $post $tables
                $pair[$role] = @{initial=$observation; copy_before=$copyBefore; insert=$insert; post=(Observe $post $tables $true)}
            }
            $result.replicas += $pair
        }
    }
} catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
