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
function Append-Row($Recordset, $Row) {
    $Recordset.AddNew(); $Recordset.Fields.Item('Id').Value = [int]$Row[0]
    $Recordset.Fields.Item('Value').Value = [int]$Row[1]; $Recordset.Update()
}
function Mutate([string]$Path, $Arm, [string]$Step, $Plan) {
    $engine = $workspace = $db = $table = $field = $rs = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        if ($Step -eq 'before') {
            $workspace = $engine.Workspaces.Item(0); $script:mutationStarted = $true
            $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
            $table = $db.CreateTableDef('Rows')
            foreach ($name in @('Id', 'Value')) {
                $field = $table.CreateField($name, 4); $table.Fields.Append($field)
                Release $field; $field = $null
            }
            $db.TableDefs.Append($table)
        } else { $db = $engine.OpenDatabase($Path, $false, $false) }
        $rs = $db.OpenRecordset('Rows', 2)
        if ($Step -eq 'deleted') {
            $rs.FindFirst('[Id] = ' + [string]$Arm.delete_id)
            if ($rs.NoMatch) { throw 'Selected deletion Id absent' }
            $rs.Delete()
        } elseif ($Step -eq 'before') {
            foreach ($row in $Arm.rows) { Append-Row $rs $row }
        } else { Append-Row $rs $Plan.insert }
    } finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $field; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $workspace; Release $engine
    }
}
function Observe([string]$Path) {
    $before = Identity $Path; $engine = $db = $table = $rs = $null
    $snapshot = [ordered]@{}; $status = 'pass'; $errorDetail = $null; $endpoint = 'open_database'
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @($db.Relations | ForEach-Object { [string]$_.Name })
        $snapshot.queries = @($db.QueryDefs | ForEach-Object { [string]$_.Name })
        $endpoint = 'schema'; $table = $db.TableDefs.Item('Rows')
        $snapshot.attributes = [int]$table.Attributes
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; attributes=[int]$_.Attributes} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object { [string]$_.Name })
        $endpoint = 'rows'; $rs = $db.OpenRecordset('Rows', 4); $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) {
            [void]$rows.Add(@([int]$rs.Fields.Item('Id').Value, [int]$rs.Fields.Item('Value').Value)); $rs.MoveNext()
        }
        $snapshot.rows = @($rows)
    } catch { $status = 'error'; $errorDetail = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
    finally {
        if ($null -ne $rs) { $rs.Close() }; Release $rs; Release $table
        if ($null -ne $db) { $db.Close() }; Release $db; Release $engine
    }
    return @{before=$before; after=(Identity $Path); status=$status; error=$errorDetail; endpoint=$endpoint; snapshot=$snapshot}
}
$planPath = Join-Path $env:JET3_WORK 'row-delete-layout.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/row_delete_layout.ps1') { throw 'Script pin mismatch' }
$mutationStarted = $false
$result = [ordered]@{document_type='dao_row_delete_layout_result'; plan_sha256=(Identity $planPath).sha256; mutation_started=$false;
    environment=@{process_bits=32; provider='DAO.DBEngine.36'; os=[Environment]::OSVersion.VersionString}; captures=@(); endpoint='start'; error=$null}
try {
    foreach ($arm in $plan.arms) {
        foreach ($replica in 1..3) {
            $working = Join-Path $env:JET3_WORK "$($arm.name)-r$replica-working.mdb"
            foreach ($checkpoint in @('before', 'deleted', 'inserted')) {
                $result.endpoint = "$($arm.name)/$replica/$checkpoint/mutate"
                Mutate $working $arm $checkpoint $plan
                $file = "$($arm.name)-r$replica-$checkpoint.mdb"
                $retained = Join-Path $env:JET3_WORK $file; Copy-Item -LiteralPath $working -Destination $retained
                $result.endpoint = "$($arm.name)/$replica/$checkpoint/observe"
                $observation = Observe $retained
                $result.captures += @{arm=[string]$arm.name; replica=$replica; checkpoint=$checkpoint; file=$file; observation=$observation}
                if ($observation.status -ne 'pass') { throw 'Read-only observation failed' }
            }
        }
    }
} catch { $result.error = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message }
finally {
    $result.mutation_started = $mutationStarted
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
    Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb' | Copy-Item -Destination $env:JET3_OUTBOX
}
if ($null -ne $result.error) { exit 1 }
