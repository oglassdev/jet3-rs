Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
# Applies relationship_forms_suite.py inputs and edit steps through DAO 3.6.
$General = ';LANGID=0x0409;CP=1252;COUNTRY=0'
$TypeCodes = @{ long = 4; text = 10; memo = 12; long_binary = 11 }

function Release($x) { if ($null -ne $x -and [Runtime.InteropServices.Marshal]::IsComObject($x)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($x) } }
function Set-Property($owner, [string]$name, $value) { $a = [object[]]::new(1); $a[0] = $value; [void]$owner.GetType().InvokeMember($name, [Reflection.BindingFlags]::SetProperty, $null, $owner, $a) }
function Has($object, [string]$name) { return $null -ne $object.PSObject.Properties[$name] }
function Identity([string]$p) { @{ size = (Get-Item $p).Length; sha256 = (Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() } }
function Error-Info($engine, $record) {
  $numbers = @(); $es = $ei = $null
  try { $es = $engine.Errors; for ($i = 0; $i -lt $es.Count; $i++) { $ei = $es.Item($i); $numbers += [int]$ei.Number; Release $ei; $ei = $null } } finally { Release $ei; Release $es }
  $x = $record.Exception; while ($null -ne $x.InnerException) { $x = $x.InnerException }
  @{ numbers = $numbers; message = $x.Message }
}

function Make-Table($db, $spec) {
  $td = $db.CreateTableDef([string]$spec.name); $fs = $td.Fields
  foreach ($c in $spec.columns) { $size = 0; if (Has $c 'size') { $size = [int]$c.size }; $f = $td.CreateField([string]$c.name, $TypeCodes[[string]$c.type], $size); $fs.Append($f); Release $f }
  Release $fs
  $xs = $td.Indexes
  foreach ($ix in $spec.indexes) {
    $x = $td.CreateIndex([string]$ix.name); $xfs = $x.Fields
    foreach ($field in $ix.fields) { $xf = $x.CreateField([string]$field.column); $xfs.Append($xf); Release $xf }
    Release $xfs
    if ([string]$ix.kind -eq 'primary') { Set-Property $x 'Primary' $true; Set-Property $x 'Unique' $true }
    elseif ([string]$ix.kind -eq 'unique') { Set-Property $x 'Unique' $true }
    $xs.Append($x); Release $x
  }
  Release $xs
  $tds = $db.TableDefs; $tds.Append($td); Release $td; Release $tds
}
function Relate($db, [string]$name, [string]$parent, [string]$child, [int]$attributes, $pairs) {
  $r = $db.CreateRelation($name, $parent, $child, $attributes); $rfs = $r.Fields
  foreach ($pair in $pairs) { $pair = @($pair); $f = $r.CreateField([string]$pair[0]); Set-Property $f 'ForeignName' ([string]$pair[1]); $rfs.Append($f); Release $f }
  Release $rfs
  $rs = $db.Relations; $rs.Append($r); Release $r; Release $rs
}
function Attributes($spec) {
  $value = 0
  if ((Has $spec 'unique') -and $spec.unique) { $value = $value -bor 1 }
  if ((Has $spec 'enforce') -and -not $spec.enforce) { $value = $value -bor 2 }
  if ((Has $spec 'cascade_updates') -and $spec.cascade_updates) { $value = $value -bor 256 }
  if ((Has $spec 'cascade_deletes') -and $spec.cascade_deletes) { $value = $value -bor 4096 }
  $join = 'inner'; if (Has $spec 'join') { $join = [string]$spec.join }
  if ($join -eq 'left' -or $join -eq 'left_and_right') { $value = $value -bor 16777216 }
  if ($join -eq 'right' -or $join -eq 'left_and_right') { $value = $value -bor 33554432 }
  return $value
}
function Columns($endpoint) { if (Has $endpoint 'columns') { return ,@($endpoint.columns) }; return ,@([string]$endpoint.column) }
function Create-Relation($db, $spec) {
  $parent = Columns $spec.parent; $child = Columns $spec.child; $pairs = @()
  for ($i = 0; $i -lt $parent.Count; $i++) { $pairs += ,@($parent[$i], $child[$i]) }
  Relate $db ([string]$spec.name) ([string]$spec.parent.table) ([string]$spec.child.table) (Attributes $spec) $pairs
}
function Set-Binary($db, [string]$table, [string]$key, [int]$id, [string]$column, [int]$length, [int]$seed) {
  $rs = $db.OpenRecordset("SELECT * FROM [$table] WHERE [$key] = $id", 2)
  try {
    $rs.Edit(); $fs = $rs.Fields; $f = $fs.Item($column)
    if ([int]$f.Type -eq 12) { $sb = New-Object Text.StringBuilder; for ($i = 0; $i -lt $length; $i++) { [void]$sb.Append([char](65 + (($i * 7 + $seed) % 26))) }; Set-Property $f 'Value' $sb.ToString() }
    else { $bytes = New-Object byte[] $length; for ($i = 0; $i -lt $length; $i++) { $bytes[$i] = [byte](($i * 13 + $seed) % 256) }; Set-Property $f 'Value' $bytes }
    Release $f; Release $fs; $rs.Update()
  } finally { $rs.Close(); Release $rs }
}

function Apply-Op($db, $op) {
  switch ([string]$op.op) {
    'table' { Make-Table $db $op.table }
    'sql' { $db.Execute([string]$op.text, 128) }
    'relation' { $pairs = @(); foreach ($pair in $op.pairs) { $pairs += ,@($pair) }; Relate $db ([string]$op.name) ([string]$op.parent) ([string]$op.child) ([int]$op.attributes) $pairs }
    'query' { $q = $db.CreateQueryDef([string]$op.name, [string]$op.sql); Release $q }
    'payload' { Set-Binary $db ([string]$op.table) ([string]$op.key) ([int]$op.id) ([string]$op.column) ([int]$op.length) ([int]$op.seed) }
    default { throw ('unsupported op ' + $op.op) }
  }
}

function Apply-Request($db, $r) {
  $tds = $null
  switch ([string]$r.operation) {
    'create_relationship' { Create-Relation $db $r.relationship }
    'replace_relationship' { $rs = $db.Relations; $rs.Delete([string]$r.name); Release $rs; Create-Relation $db $r.relationship }
    'drop_relationship' { $rs = $db.Relations; $rs.Delete([string]$r.name); Release $rs }
    'drop_table' { $tds = $db.TableDefs; try { $tds.Delete([string]$r.table) } finally { Release $tds } }
    'drop_column' { $tds = $db.TableDefs; $td = $tds.Item([string]$r.table); $fs = $td.Fields; try { $fs.Delete([string]$r.column) } finally { Release $fs; Release $td; Release $tds } }
    'drop_index' { $tds = $db.TableDefs; $td = $tds.Item([string]$r.table); $xs = $td.Indexes; try { $xs.Delete([string]$r.index) } finally { Release $xs; Release $td; Release $tds } }
    'rename_table' { $tds = $db.TableDefs; $td = $tds.Item([string]$r.table); try { Set-Property $td 'Name' ([string]$r.name) } finally { Release $td; Release $tds } }
    'rename_column' { $tds = $db.TableDefs; $td = $tds.Item([string]$r.table); $fs = $td.Fields; $f = $fs.Item([string]$r.column); try { Set-Property $f 'Name' ([string]$r.name) } finally { Release $f; Release $fs; Release $td; Release $tds } }
    'create_column' { $tds = $db.TableDefs; $td = $tds.Item([string]$r.table); $fs = $td.Fields; try { $f = $td.CreateField([string]$r.column.name, $TypeCodes[[string]$r.column.type], 0); $fs.Append($f); Release $f } finally { Release $fs; Release $td; Release $tds } }
    default { throw ('unsupported operation ' + $r.operation) }
  }
}

$E = New-Object -ComObject DAO.DBEngine.36
$dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
$jobs = Get-Content -Raw -Encoding UTF8 (Join-Path $env:JET3_WORK 'jobs.json') | ConvertFrom-Json
$result = [ordered]@{ document_type = 'jet3_relationship_forms_suite_native'; environment = @{ version = [string]$E.Version; bits = [IntPtr]::Size * 8; os = [Environment]::OSVersion.VersionString; culture = [Globalization.CultureInfo]::CurrentCulture.Name; ansi = [Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage; dll_version = $dll[0].FileVersionInfo.FileVersion; dll_sha256 = (Identity $dll[0].FileName).sha256 }; inputs = @(); edits = @() }

foreach ($job in $jobs.inputs) {
  $path = Join-Path $env:JET3_WORK ('input-' + $job.name + '.mdb'); $db = $null; $failure = $null
  try {
    $db = $E.CreateDatabase($path, $General, 32)
    foreach ($op in $job.ops) { Apply-Op $db $op }
    $db.Close(); Release $db; $db = $null
  } catch { $failure = Error-Info $E $_ } finally { if ($null -ne $db) { try { $db.Close() } catch {}; Release $db } }
  Copy-Item $path (Join-Path $env:JET3_OUTBOX ('input-' + $job.name + '.mdb'))
  $result.inputs += @{ name = $job.name; identity = (Identity $path); failure = $failure }
  Write-Output ('input {0} {1}' -f $job.name, ($(if ($failure) { 'FAILED ' + $failure.message } else { 'ok' })))
}

foreach ($case in $jobs.edits) {
  $source = Join-Path $env:JET3_WORK ('input-' + $case.input + '.mdb')
  $path = Join-Path $env:JET3_WORK ('native-' + $case.name + '.mdb'); Copy-Item $source $path -Force
  $before = Identity $path; $steps = @(); $db = $null
  try {
    $db = $E.OpenDatabase($path)
    foreach ($step in $case.steps) {
      try {
        if (Has $step 'sql') { $db.Execute([string]$step.sql, 128) } else { Apply-Request $db $step.request }
        $steps += @{ ok = $true }
      } catch { $steps += @{ ok = $false; error = (Error-Info $E $_) }; break }
    }
    $db.Close(); Release $db; $db = $null
  } finally { if ($null -ne $db) { try { $db.Close() } catch {}; Release $db } }
  Copy-Item $path (Join-Path $env:JET3_OUTBOX ('native-' + $case.name + '.mdb'))
  $result.edits += @{ name = $case.name; input = [string]$case.input; before = $before; after = (Identity $path); steps = $steps }
  Write-Output ('{0} {1}' -f $case.name, (($steps | ForEach-Object { if ($_.ok) { 'ok' } else { 'refused ' + ($_.error.numbers -join ',') } }) -join ' '))
}
[IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'), ((ConvertTo-Json $result -Depth 100 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
'done'
