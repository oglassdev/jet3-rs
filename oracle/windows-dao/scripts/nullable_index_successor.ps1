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
function Values([string]$Arm, [string]$Name, [int]$Position) {
    $values = [object[]]::new(3)
    if ($Arm -ceq 'required') { $values[0] = [int]($Position % 3); $values[1] = -$Position }
    elseif ($Arm -in @('composite', 'composite-ignore', 'auto')) {
        switch ($Position % 4) {
            0 { $values[0] = $null; $values[1] = $null }
            1 { $values[0] = $null; $values[1] = 1 }
            2 { $values[0] = 1; $values[1] = $null }
            3 { $values[0] = $Position; $values[1] = -$Position }
        }
    }
    else {
        $values[0] = if ($Position % 3 -eq 0) { $null } else { [int]($Position - 6) }
        $values[1] = -$Position
    }
    if ($Arm -ceq 'auto') { $values[1] = [int]($Position + 1) }
    $values[2] = $Position
    return ,$values
}
function Set-Value($Recordset, [int]$Ordinal, $Value) {
    $field = $Recordset.Fields.Item($Ordinal)
    try {
        if ($null -eq $Value) { $field.Value = [DBNull]::Value }
        else { $field.Value = [int]$Value }
    }
    finally { Release $field }
}
function Seek-Row($Recordset, $Query) {
    [int]$first = $Query[0]
    if ($Query.Count -eq 1) { $Recordset.Seek('=', $first) }
    else { [int]$second = $Query[1]; $Recordset.Seek('=', $first, $second) }
}
function Remember-Failure($Failure) {
    if ($null -eq $script:firstFailure) {
        $exception = $Failure.Exception
        while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
        $script:firstFailure = @{arm=$script:currentArm; replica=$script:currentReplica; endpoint=$script:endpoint; message=$exception.Message; type=$exception.GetType().FullName;
            hresult=[int]$exception.HResult; stack=[string]$Failure.ScriptStackTrace}
    }
}
function Cleanup($Value, [string]$Action) {
    if ($null -eq $Value) { return }
    try {
        switch ($Action) {
            'close' { $Value.Close() }
            'rollback' { $Value.Rollback() }
            'release' { Release $Value }
        }
    }
    catch { Remember-Failure $_; [void]$script:cleanupFailures.Add(@{action=$Action; message=$_.Exception.Message}) }
}
function Add-Table($Database, $Spec) {
    $table = $index = $field = $null
    try {
        $table = $Database.CreateTableDef([string]$Spec.name)
        foreach ($column in $Spec.fields) {
            $field = $table.CreateField([string]$column.name, 4)
            if ($column.auto_increment) { $field.Attributes = 17 }
            $table.Fields.Append($field); Release $field; $field = $null
        }
        foreach ($definition in $Spec.indexes) {
            if ($definition.foreign) { continue }
            $index = $table.CreateIndex([string]$definition.name)
            $index.Primary = [bool]$definition.primary; $index.Unique = [bool]$definition.unique
            $index.Required = [bool]$definition.required; $index.IgnoreNulls = [bool]$definition.ignore_nulls
            foreach ($key in $definition.fields) {
                $field = $index.CreateField([string]$key.name)
                $field.Attributes = [int]$key.attributes
                $index.Fields.Append($field); Release $field; $field = $null
            }
            $table.Indexes.Append($index); Release $index; $index = $null
        }
        $Database.TableDefs.Append($table)
    }
    catch { Remember-Failure $_; throw }
    finally { Cleanup $field 'release'; Cleanup $index 'release'; Cleanup $table 'release' }
}
function New-Control([string]$Path, [string]$Arm) {
    $engine = $workspace = $db = $rs = $relation = $field = $null
    $transaction = $false
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $workspace = $engine.Workspaces.Item(0)
        $script:mutationStarted = $true
        $script:endpoint = 'control/create_database'
        $db = $workspace.CreateDatabase($Path, ';LANGID=0x0409;CP=1252;COUNTRY=0', 32)
        foreach ($table in $plan.arms.$Arm) { $script:endpoint = 'control/schema/' + $table.name; Add-Table $db $table }
        foreach ($table in $plan.arms.$Arm) {
            if ($table.row_count -eq 0) { continue }
            $script:endpoint = 'control/open_rows/' + $table.name
            $rs = $db.OpenRecordset([string]$table.name, 2)
            $workspace.BeginTrans(); $transaction = $true
            for ($position = 0; $position -lt [int]$table.row_count; $position++) {
                $values = Values $Arm ([string]$table.name) $position
                $script:endpoint = 'control/add_new/' + $position
                $rs.AddNew()
                for ($column = 0; $column -lt $values.Count; $column++) {
                    if ($table.fields[$column].auto_increment) { continue }
                    $script:endpoint = 'control/assign/' + $position + '/' + $column
                    Set-Value $rs $column $values[$column]
                }
                $script:endpoint = 'control/update/' + $position
                $rs.Update()
            }
            $script:endpoint = 'control/commit'
            $workspace.CommitTrans(); $transaction = $false
            $rs.Close(); Release $rs; $rs = $null
        }

    }
    catch { Remember-Failure $_; throw }
    finally {
        if ($transaction) { Cleanup $workspace 'rollback' }
        Cleanup $rs 'close'; Cleanup $rs 'release'
        Cleanup $field 'release'; Cleanup $relation 'release'
        Cleanup $db 'close'; Cleanup $db 'release'
        Cleanup $workspace 'release'; Cleanup $engine 'release'
    }
}
function Read-Row($Recordset, [int]$Count) {
    $values = [object[]]::new($Count)
    for ($column = 0; $column -lt $Count; $column++) {
        $value = $Recordset.Fields.Item($column).Value
        if ($null -eq $value -or [Convert]::IsDBNull($value)) { $values[$column] = $null }
        else { $values[$column] = [int]$value }
    }
    return ,$values
}
function Read-Table($Database, $Spec) {
    $table = $rs = $null
    try {
        $table = $Database.TableDefs.Item([string]$Spec.name)
        $snapshot = [ordered]@{name=[string]$Spec.name}
        $snapshot.fields = @($table.Fields | ForEach-Object { @{name=[string]$_.Name; type=[int]$_.Type; size=[int]$_.Size; auto_increment=(([int]$_.Attributes -band 16) -ne 0)} })
        $snapshot.indexes = @($table.Indexes | ForEach-Object {
            @{name=[string]$_.Name; primary=[bool]$_.Primary; unique=[bool]$_.Unique; required=[bool]$_.Required;
              foreign=[bool]$_.Foreign; ignore_nulls=[bool]$_.IgnoreNulls;
              fields=@($_.Fields | ForEach-Object { @{name=[string]$_.Name; descending=(([int]$_.Attributes -band 1) -ne 0); attributes=[int]$_.Attributes} })}
        })
        $rs = $Database.OpenRecordset([string]$Spec.name, 4)
        $rows = New-Object Collections.ArrayList
        while (-not $rs.EOF) { [void]$rows.Add((Read-Row $rs $Spec.fields.Count)); $rs.MoveNext() }
        $snapshot.rows = @($rows)
        if ($Spec.indexes.Count -gt 0) {
            $rs.Close(); Release $rs; $rs = $null
            $rs = $Database.OpenRecordset([string]$Spec.name, 1)
            $rs.Index = [string]$Spec.indexes[0].name
            if (-not ($rs.BOF -and $rs.EOF)) { $rs.MoveFirst() }
            $traversal = New-Object Collections.ArrayList
            while (-not $rs.EOF) { [void]$traversal.Add((Read-Row $rs $Spec.fields.Count)); $rs.MoveNext() }
            $snapshot.traversal = @($traversal)
            $seeks = New-Object Collections.ArrayList
            foreach ($query in $Spec.queries) {
                Seek-Row $rs $query
                $row = if ($rs.NoMatch) { $null } else { Read-Row $rs $Spec.fields.Count }
                [void]$seeks.Add(@{query=@($query); row=$row})
            }
            $snapshot.seek = @($seeks)
        }
        return $snapshot
    }
    catch { Remember-Failure $_; throw }
    finally { Cleanup $rs 'close'; Cleanup $rs 'release'; Cleanup $table 'release' }
}
function Observe([string]$Path, [string]$Arm) {
    $before = Identity $Path
    $engine = $db = $null
    $endpoint = 'open_database'; $snapshot = [ordered]@{}; $status = 'pass'; $errorDetail = $null
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $true)
        $endpoint = 'schema'
        $snapshot.version = [string]$db.Version
        $snapshot.tables = @($db.TableDefs | ForEach-Object { [string]$_.Name } | Sort-Object)
        $snapshot.relations = @()
        foreach ($relation in $db.Relations) {
            try {
                $fields = @($relation.Fields | ForEach-Object { @{name=[string]$_.Name; foreign_name=[string]$_.ForeignName} })
                $snapshot.relations += @{name=[string]$relation.Name; table=[string]$relation.Table; foreign_table=[string]$relation.ForeignTable; attributes=[int]$relation.Attributes; fields=$fields}
            }
            finally { Release $relation }
        }
        $endpoint = 'user_tables'
        $snapshot.user_tables = @(foreach ($spec in $plan.arms.$Arm) { Read-Table $db $spec })
        $endpoint = 'complete'
    }
    catch { Remember-Failure $_; $status = 'fail'; $errorDetail = ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message).Replace($Path, '<DATABASE>') }
    finally {
        Cleanup $db 'close'; Cleanup $db 'release'; Cleanup $engine 'release'
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
    return @{before=$before; after=(Identity $Path); status=$status; endpoint=$endpoint; error=$errorDetail; snapshot=$snapshot}
}
function Reject-Probe([string]$Original, [string]$Path, [string]$Arm) {
    Copy-Item -LiteralPath $Original -Destination $Path
    $originalIdentity = Identity $Path
    $engine = $db = $rs = $null
    $operation = @{status='updated'; endpoint='update'; native_codes=@(); hresult=$null; error=$null}
    try {
        $engine = New-Object -ComObject DAO.DBEngine.36
        $db = $engine.OpenDatabase($Path, $false, $false)
        $script:endpoint = 'probe/open_rows'
        $rs = $db.OpenRecordset('Rows', 2)
        $script:endpoint = 'probe/add_new'
        $rs.AddNew()
        $values = $plan.rejection_probes.$Arm
        for ($column = 0; $column -lt $values.Count; $column++) {
            $script:endpoint = 'probe/assign/' + $column
            Set-Value $rs $column $values[$column]
        }
        $script:endpoint = 'probe/update'
        try { $rs.Update() }
        catch {
            $operation.status = 'rejected'
            $operation.native_codes = @($engine.Errors | ForEach-Object { [int]$_.Number })
            $exception = $_.Exception
            while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
            $operation.hresult = [int]$exception.HResult
            $operation.error = ($exception.GetType().FullName + ': ' + $exception.Message).Replace($Path, '<DATABASE>')
            $rs.CancelUpdate()
        }
    }
    catch { Remember-Failure $_; throw }
    finally {
        Cleanup $rs 'close'; Cleanup $rs 'release'
        Cleanup $db 'close'; Cleanup $db 'release'; Cleanup $engine 'release'
    }
    return @{original=$originalIdentity; operation=$operation; observation=(Observe $Path $Arm)}
}
function Row-Json($Row) { return ConvertTo-Json -InputObject $Row -Compress }
function Key-Hex($Row, $Index) {
    $hex = ''
    foreach ($field in $Index.fields) {
        $position = if ($field.name -ceq 'A') { 0 } else { 1 }
        $value = $Row[$position]
        $component = if ($null -eq $value) { '00' } else { '7f' + ([uint32]([long]$value + 2147483648)).ToString('x8') }
        if ($field.descending) {
            $flipped = ''
            for ($n=0; $n -lt $component.Length; $n+=2) { $flipped += ([Convert]::ToByte($component.Substring($n,2),16) -bxor 255).ToString('x2') }
            $component = $flipped
        }
        $hex += $component
    }
    return $hex
}
function Assert-Baseline($Observation, [string]$Arm) {
    if ($Observation.status -cne 'pass' -or $Observation.endpoint -cne 'complete' -or $null -ne $Observation.error -or
        $Observation.before.sha256 -cne $Observation.after.sha256 -or $Observation.before.size -ne $Observation.after.size) { throw 'Baseline observation failed or changed bytes' }
    $snapshot = $Observation.snapshot; $specs = $plan.arms.$Arm
    if ($snapshot.version -cne '3.0' -or $snapshot.relations.Count -ne 0 -or $snapshot.user_tables.Count -ne $specs.Count -or
        (Row-Json $snapshot.tables) -cne '["Empty","MSysACEs","MSysObjects","MSysQueries","MSysRelationships","Rows"]') { throw 'Baseline database inventory mismatch' }
    for ($t=0; $t -lt $specs.Count; $t++) {
        $spec = $specs[$t]; $table = $snapshot.user_tables[$t]
        if ($table.name -cne $spec.name -or $table.fields.Count -ne $spec.fields.Count -or $table.indexes.Count -ne $spec.indexes.Count -or $table.rows.Count -ne $spec.row_count) { throw 'Baseline table inventory/count mismatch' }
        for ($f=0; $f -lt $spec.fields.Count; $f++) {
            foreach ($property in @('name','type','size','auto_increment')) {
                if ($table.fields[$f].$property -cne $spec.fields[$f].$property) { throw 'Baseline field metadata mismatch' }
            }
        }
        $expected = @{}; $byKey = @{}; $selected = @{}
        for ($n=0; $n -lt $spec.row_count; $n++) {
            $row = Values $Arm $spec.name $n; $expected[[string]$n] = Row-Json $row
            $index = $spec.indexes[0]
            $key = Key-Hex $row $index
            $allNull = $null -eq $row[0] -and ($index.fields.Count -eq 1 -or $null -eq $row[1])
            if (-not ($index.ignore_nulls -and $allNull)) { $selected[[string]$n] = $key; $byKey[$key] = $true }
        }
        $seen = @{}
        foreach ($row in $table.rows) {
            if ($row.Count -ne 3 -or $null -eq $row[2]) { throw 'Baseline row shape mismatch' }
            $id = [string]$row[2]
            if ($seen.ContainsKey($id) -or -not $expected.ContainsKey($id) -or (Row-Json $row) -cne $expected[$id]) { throw 'Baseline row values mismatch' }
            $seen[$id] = $true
        }
        if ($spec.indexes.Count -eq 0) { continue }
        $index = $spec.indexes[0]; $observed = $table.indexes[0]
        foreach ($property in @('name','primary','unique','required','foreign','ignore_nulls')) {
            if ($observed.$property -cne $index.$property) { throw 'Baseline index metadata mismatch' }
        }
        if ($observed.fields.Count -ne $index.fields.Count) { throw 'Baseline index fields mismatch' }
        for ($f=0; $f -lt $index.fields.Count; $f++) {
            foreach ($property in @('name','descending','attributes')) {
                if ($observed.fields[$f].$property -cne $index.fields[$f].$property) { throw 'Baseline index field mismatch' }
            }
        }
        if ($table.traversal.Count -ne $selected.Count) { throw 'Baseline traversal count mismatch' }
        $seen = @{}; $previous = ''
        foreach ($row in $table.traversal) {
            $id = [string]$row[2]; $key = Key-Hex $row $index
            if (-not $selected.ContainsKey($id) -or $seen.ContainsKey($id) -or (Row-Json $row) -cne $expected[$id] -or [string]::CompareOrdinal($previous,$key) -gt 0) { throw 'Baseline traversal values/order mismatch' }
            $seen[$id] = $true; $previous = $key
        }
        if ($table.seek.Count -ne $spec.queries.Count) { throw 'Baseline Seek inventory mismatch' }
        for ($q=0; $q -lt $spec.queries.Count; $q++) {
            $seek = $table.seek[$q]; $query = $spec.queries[$q]
            if ((Row-Json $seek.query) -cne (Row-Json $query)) { throw 'Baseline Seek query mismatch' }
            $key = Key-Hex $query $index
            if ($null -eq $seek.row) { if ($byKey.ContainsKey($key)) { throw 'Baseline Seek missed existing key' } }
            else {
                $id = [string]$seek.row[2]
                if (-not $selected.ContainsKey($id) -or $selected[$id] -cne $key -or (Row-Json $seek.row) -cne $expected[$id]) { throw 'Baseline Seek returned wrong row' }
            }
        }
    }
}
function Assert-Probe($Probe, [string]$Arm) {
    Assert-Baseline $Probe.observation $Arm
    if ($Probe.operation.status -cne 'rejected' -or $Probe.operation.endpoint -cne 'update' -or $null -eq $Probe.operation.hresult) { throw 'Expected Update rejection not observed' }
}
$planPath = Join-Path $env:JET3_WORK 'nullable-index-successor.plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ((Identity $PSCommandPath).sha256 -cne $plan.inputs.'oracle/windows-dao/scripts/nullable_index_successor.ps1') { throw 'Script pin mismatch' }
$arms = @('unique', 'ignore', 'required', 'composite', 'composite-ignore', 'auto')
foreach ($arm in $arms) {
    foreach ($replica in 1..3) {
        $path = Join-Path $env:JET3_WORK "$arm-candidate-r$replica.mdb"
        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$arm.mdb") -Destination $path
        $identity = Identity $path; $expected = $plan.candidates.$arm
        if ($identity.size -ne $expected.size -or $identity.sha256 -cne $expected.sha256) { throw 'Candidate pin mismatch' }
    }
}
$mutationStarted = $false
$firstFailure = $null; $endpoint = 'start'; $cleanupFailures = New-Object Collections.ArrayList
$retentionFailures = New-Object Collections.ArrayList
$result = [ordered]@{
    document_type='dao_nullable_index_result'; development_only=$true
    plan_sha256=(Identity $planPath).sha256; mutation_started=$false
    environment=@{process_bits=([IntPtr]::Size * 8); os=[Environment]::OSVersion.VersionString; provider='DAO.DBEngine.36'}
    replicas=@(); error=$null; cleanup_failures=@(); retention_failures=@()
}
try {
    foreach ($arm in $arms) {
        foreach ($replica in 1..3) {
            $script:currentArm = $arm; $script:currentReplica = $replica
            $control = Join-Path $env:JET3_WORK "$arm-control-r$replica.mdb"
            New-Control $control $arm
            $candidate = Join-Path $env:JET3_WORK "$arm-candidate-r$replica.mdb"
            $pair = @{arm=$arm; replica=$replica; control=(Observe $control $arm); candidate=(Observe $candidate $arm)}
            $result.replicas += $pair
            $script:endpoint = 'baseline/validate'
            Assert-Baseline $pair.control $arm
            Assert-Baseline $pair.candidate $arm
            if ($null -ne $firstFailure) { throw 'Cleanup failed' }
            if ($plan.rejection_probes.PSObject.Properties.Name -contains $arm) {
                $pair.probes = @{}
                $pair.probes.control = Reject-Probe $control (Join-Path $env:JET3_WORK "$arm-control-probe-r$replica.mdb") $arm
                Assert-Probe $pair.probes.control $arm
                $pair.probes.candidate = Reject-Probe $candidate (Join-Path $env:JET3_WORK "$arm-candidate-probe-r$replica.mdb") $arm
                Assert-Probe $pair.probes.candidate $arm
                if ((Row-Json $pair.probes.control.operation.native_codes) -cne (Row-Json $pair.probes.candidate.operation.native_codes) -or
                    $pair.probes.control.operation.hresult -ne $pair.probes.candidate.operation.hresult) { throw 'Probe native rejection differs from control' }

            }
            if ($null -ne $firstFailure) { throw 'Cleanup failed' }
        }
    }
}
catch { Remember-Failure $_ }
finally {
    $result.mutation_started = $mutationStarted
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*-r*.mdb') {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX }
        catch { [void]$retentionFailures.Add(@{file=$file.Name; message=$_.Exception.Message}) }
    }
    $result.error = $firstFailure
    $result.cleanup_failures = @($cleanupFailures); $result.retention_failures = @($retentionFailures)
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'result.json'), (($result | ConvertTo-Json -Depth 20 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
if ($null -ne $result.error -or $retentionFailures.Count) { exit 1 }
