Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'DAO requires x86 PowerShell' }
function Release($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}
function Identity([string]$Path) {
    return @{size=(Get-Item -LiteralPath $Path).Length; sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $table = $field = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        $table = $db.CreateTableDef('Rows')
        foreach ($name in @('Id', 'Tag')) {
            $field = $table.CreateField($name, 4)
            if ($Arm -eq 'auto' -and $name -eq 'Id') { $field.Attributes = 16 }
            $table.Fields.Append($field)
            Release $field; $field = $null
        }
        $db.TableDefs.Append($table)
    }
    finally {
        Release $field; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $workspace; Release $engine
    }
}
function Mutate([string]$Path, [string]$Arm, [string]$Checkpoint) {
    $engine = $db = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path)
        $rs = $db.OpenRecordset('Rows', 2)
        if ($Checkpoint -eq 'deleted') {
            $rs.FindFirst('Tag = 256')
            if ($rs.NoMatch) { throw 'Missing row Tag256 for planned deletion' }
            $rs.Delete()
        }
        else {
            $tags = switch ($Checkpoint) {
                'one' { @(1) }
                'n255' { @(2..255) }
                'n256' { @(256) }
                'next' { @(257) }
                default { throw 'Unexpected mutation checkpoint' }
            }
            foreach ($tag in $tags) {
                $rs.AddNew()
                if ($Arm -eq 'long') { $rs.Fields.Item('Id').Value = [int]$tag }
                $rs.Fields.Item('Tag').Value = [int]$tag
                $rs.Update()
            }
        }
    }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
    }
}
function Observe([string]$Path) {
    $before = Identity $Path
    $engine = $db = $table = $rs = $null
    $snapshot = [ordered]@{}
    $status = 'pass'; $errorDetail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $table = $db.TableDefs.Item('Rows')
        $snapshot.table_attributes = [int]$table.Attributes
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes} })
        $snapshot.index_count = [int]$table.Indexes.Count
        $rs = $db.OpenRecordset('Rows', 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            [void]$rows.Add(@([int]$rs.Fields.Item('Id').Value, [int]$rs.Fields.Item('Tag').Value))
            $rs.MoveNext()
        }
        $snapshot.rows = @($rows)
    }
    catch { $status = 'fail'; $errorDetail = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
    finally {
        if ($null -ne $rs) { $rs.Close() }
        Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }
        Release $db; Release $engine
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; error=$errorDetail; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'autoincrement-layout.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/autoincrement_layout.ps1') { throw 'Script pin mismatch' }
$mutationStarted = $false
$result = [ordered]@{
    document_type='dao_autoincrement_layout_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}
    checkpoints=@(); error=$null
}
try {
    foreach ($replica in 1..3) {
        foreach ($arm in @('auto', 'long')) {
            $working = Join-Path $env:JET3_WORK "$arm-r$replica-working.mdb"
            New-Control $working $arm
            foreach ($checkpoint in @('empty', 'one', 'n255', 'n256', 'deleted', 'next')) {
                if ($checkpoint -ne 'empty') { Mutate $working $arm $checkpoint }
                $name = "$arm-r$replica-$checkpoint.mdb"
                $path = Join-Path $env:JET3_WORK $name
                Copy-Item -LiteralPath $working -Destination $path
                $observation = Observe $path
                $observation.replica = $replica; $observation.arm = $arm
                $observation.checkpoint = $checkpoint; $observation.file = $name
                $result.checkpoints += $observation
            }
        }
    }
}
catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*-*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
