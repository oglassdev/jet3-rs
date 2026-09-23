Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
# Applies text_property_suite.py creation and edit requests through DAO 3.6.
$General = ';LANGID=0x0409;CP=1252;COUNTRY=0'
$Types = @{ boolean=1; byte=2; integer=3; long=4; auto_increment=4; currency=5; single=6; double=7; date_time=8; binary=9; text=10; fixed_text=10; long_binary=11; memo=12; guid=15 }

function Release($x) { if ($null -ne $x -and [Runtime.InteropServices.Marshal]::IsComObject($x)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($x) } }
function Set-Property($owner, [string]$name, $value) { $a = [object[]]::new(1); $a[0] = $value; [void]$owner.GetType().InvokeMember($name, [Reflection.BindingFlags]::SetProperty, $null, $owner, $a) }
function Has($object, [string]$name) { return $null -ne $object.PSObject.Properties[$name] }
function Identity([string]$p) { @{ size = (Get-Item $p).Length; sha256 = (Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() } }
function Error-Info($engine, $record) {
  $numbers = @(); $es = $ei = $null
  try { $es = $engine.Errors; for ($i = 0; $i -lt $es.Count; $i++) { $ei = $es.Item($i); $numbers += [int]$ei.Number; Release $ei; $ei = $null } } finally { Release $ei; Release $es }
  $x = $record.Exception; while ($null -ne $x.InnerException) { $x = $x.InnerException }
  @{ numbers = $numbers; message = $x.Message; hresult = [int]$x.HResult }
}
function Table-Of($db, [string]$name) { $tds = $db.TableDefs; return $tds.Item($name) }
function Field-Of($db, [string]$table, [string]$name) { $td = Table-Of $db $table; $fs = $td.Fields; $f = $fs.Item($name); Release $fs; Release $td; return $f }

function New-Field($td, $column) {
  $kind = [string]$column.type
  $size = 0; if (Has $column 'size') { $size = [int]$column.size }
  $f = $td.CreateField([string]$column.name, [int]$Types[$kind], $size)
  if ($kind -eq 'auto_increment') { Set-Property $f 'Attributes' ($f.Attributes -bor 16) }
  if ($kind -eq 'fixed_text') { Set-Property $f 'Attributes' ($f.Attributes -bor 1) }
  if ((Has $column 'required') -and $column.required) { Set-Property $f 'Required' $true }
  if ((Has $column 'allow_zero_length') -and $column.allow_zero_length) { Set-Property $f 'AllowZeroLength' $true }
  foreach ($pair in @(@('validation_rule','ValidationRule'), @('validation_text','ValidationText'), @('default_value','DefaultValue'))) {
    if (Has $column $pair[0]) { Set-Property $f $pair[1] ([string]$column.($pair[0])) }
  }
  return $f
}

function Set-Description($object, $value) {
  $ps = $object.Properties; $p = $null
  try {
    $existing = $false; for ($i = 0; $i -lt $ps.Count; $i++) { $q = $ps.Item($i); if ([string]$q.Name -eq 'Description') { $existing = $true }; Release $q }
    if ($null -eq $value) { if ($existing) { $ps.Delete('Description') } }
    elseif ($existing) { $p = $ps.Item('Description'); Set-Property $p 'Value' ([string]$value) }
    else { $p = $object.CreateProperty('Description', 10, [string]$value); $ps.Append($p) }
  } finally { Release $p; Release $ps }
}

function New-Index($td, $spec) {
  $x = $td.CreateIndex([string]$spec.name); $xfs = $x.Fields
  foreach ($field in $spec.fields) {
    $xf = $x.CreateField([string]$field.column)
    if ((Has $field 'direction') -and [string]$field.direction -eq 'descending') { Set-Property $xf 'Attributes' 1 }
    $xfs.Append($xf); Release $xf
  }
  Release $xfs
  $policy = 'include'; if (Has $spec 'null_policy') { $policy = [string]$spec.null_policy }
  if ([string]$spec.kind -eq 'primary') { Set-Property $x 'Primary' $true; Set-Property $x 'Unique' $true; $policy = 'required' }
  elseif ([string]$spec.kind -eq 'unique') { Set-Property $x 'Unique' $true }
  if ($policy -eq 'required') { Set-Property $x 'Required' $true }
  if ($policy -eq 'ignore_all_null') { Set-Property $x 'IgnoreNulls' $true }
  return $x
}

function Insert-Row($db, [string]$table, $cells) {
  $td = Table-Of $db $table; $names = @(); $fs = $td.Fields
  for ($i = 0; $i -lt $fs.Count; $i++) { $f = $fs.Item($i); $names += [string]$f.Name; Release $f }
  Release $fs; Release $td
  $rs = $db.OpenRecordset($table, 2)
  try {
    $rs.AddNew()
    for ($i = 0; $i -lt $cells.Count; $i++) {
      $cell = $cells[$i]
      if ($cell -is [string] -and $cell -eq 'auto_increment') { continue }
      $value = [DBNull]::Value
      if ($null -ne $cell) {
        $key = @($cell.PSObject.Properties)[0].Name; $raw = $cell.$key
        switch ($key) { 'long' { $value = [int]$raw } 'text' { $value = [string]$raw } 'memo' { $value = [string]$raw } 'boolean' { $value = [bool]$raw } default { throw "unsupported cell $key" } }
      }
      $rfs = $rs.Fields; $rf = $rfs.Item($names[$i]); Set-Property $rf 'Value' $value; Release $rf; Release $rfs
    }
    $rs.Update()
  } finally { $rs.Close(); Release $rs }
}

function Create-Table($db, $spec) {
  $td = $db.CreateTableDef([string]$spec.name); $fs = $td.Fields
  foreach ($column in $spec.columns) { $f = New-Field $td $column; $fs.Append($f); Release $f }
  Release $fs
  if (Has $spec 'validation_rule') { Set-Property $td 'ValidationRule' ([string]$spec.validation_rule) }
  if (Has $spec 'validation_text') { Set-Property $td 'ValidationText' ([string]$spec.validation_text) }
  if (Has $spec 'indexes') { $xs = $td.Indexes; foreach ($ix in $spec.indexes) { $x = New-Index $td $ix; $xs.Append($x); Release $x }; Release $xs }
  $tds = $db.TableDefs; $tds.Append($td); Release $td
  foreach ($column in $spec.columns) { if (Has $column 'description') { $f = Field-Of $db ([string]$spec.name) ([string]$column.name); try { Set-Description $f $column.description } finally { Release $f } } }
  if (Has $spec 'rows') { foreach ($row in $spec.rows) { Insert-Row $db ([string]$spec.name) @($row) } }
}

function Create-Relation($db, $spec) {
  $r = $db.CreateRelation([string]$spec.name, [string]$spec.parent.table, [string]$spec.child.table, 0)
  $f = $r.CreateField([string]$spec.parent.column); Set-Property $f 'ForeignName' ([string]$spec.child.column)
  $rfs = $r.Fields; $rfs.Append($f); Release $f; Release $rfs
  $rs = $db.Relations; $rs.Append($r); Release $r
}

function Apply-Request($db, [string]$command, $r) {
  if ($command -eq 'mutate') {
    if ([string]$r.operation -ne 'insert') { throw 'unsupported mutation' }
    Insert-Row $db ([string]$r.table) @($r.values); return
  }
  switch ([string]$r.operation) {
    'set_column_properties' {
      $f = Field-Of $db ([string]$r.table) ([string]$r.column)
      try {
        foreach ($pair in @(@('validation_rule','ValidationRule'), @('validation_text','ValidationText'), @('default_value','DefaultValue'))) {
          if (Has $r $pair[0]) { $v = $r.($pair[0]); if ($null -eq $v) { $v = '' }; Set-Property $f $pair[1] ([string]$v) }
        }
        if (Has $r 'description') { Set-Description $f $r.description }
      } finally { Release $f }
    }
    'set_table_properties' {
      $td = Table-Of $db ([string]$r.table)
      try { foreach ($pair in @(@('validation_rule','ValidationRule'), @('validation_text','ValidationText'))) { if (Has $r $pair[0]) { $v = $r.($pair[0]); if ($null -eq $v) { $v = '' }; Set-Property $td $pair[1] ([string]$v) } } } finally { Release $td }
    }
    'set_column_options' {
      $f = Field-Of $db ([string]$r.table) ([string]$r.column)
      try { if (Has $r 'allow_zero_length') { Set-Property $f 'AllowZeroLength' ([bool]$r.allow_zero_length) }; if (Has $r 'required') { Set-Property $f 'Required' ([bool]$r.required) } } finally { Release $f }
    }
    'create_column' {
      $td = Table-Of $db ([string]$r.table); $fs = $td.Fields
      try { $f = New-Field $td $r.column; $fs.Append($f); Release $f } finally { Release $fs; Release $td }
      if (Has $r.column 'description') { $f = Field-Of $db ([string]$r.table) ([string]$r.column.name); try { Set-Description $f $r.column.description } finally { Release $f } }
    }
    'rename_column' { $f = Field-Of $db ([string]$r.table) ([string]$r.column); try { Set-Property $f 'Name' ([string]$r.name) } finally { Release $f } }
    'drop_column' { $td = Table-Of $db ([string]$r.table); $fs = $td.Fields; try { $fs.Delete([string]$r.column) } finally { Release $fs; Release $td } }
    'create_table' { Create-Table $db $r.table }
    'create_index' { $td = Table-Of $db ([string]$r.table); $xs = $td.Indexes; try { $x = New-Index $td $r.index; $xs.Append($x); Release $x } finally { Release $xs; Release $td } }
    'replace_index' {
      $td = Table-Of $db ([string]$r.table); $xs = $td.Indexes
      try { $xs.Delete([string]$r.index); $x = New-Index $td $r.replacement; $xs.Append($x); Release $x } finally { Release $xs; Release $td }
    }
    default { throw ('unsupported operation ' + $r.operation) }
  }
}

$E = New-Object -ComObject DAO.DBEngine.36
$dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
$jobs = Get-Content -Raw -Encoding UTF8 (Join-Path $env:JET3_WORK 'jobs.json') | ConvertFrom-Json
$result = [ordered]@{ document_type = 'jet3_text_property_native'; environment = @{ version = [string]$E.Version; bits = [IntPtr]::Size * 8; os = [Environment]::OSVersion.VersionString; culture = [Globalization.CultureInfo]::CurrentCulture.Name; ansi = [Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage; dll_version = $dll[0].FileVersionInfo.FileVersion; dll_sha256 = (Identity $dll[0].FileName).sha256 }; creations = @(); edits = @() }
$only = $null; if (Test-Path (Join-Path $env:JET3_WORK 'only.txt')) { $only = @(Get-Content (Join-Path $env:JET3_WORK 'only.txt')) }

foreach ($job in $jobs.creations) {
  if ($null -ne $only -and $only -notcontains $job.name) { continue }
  foreach ($replica in $job.replicas) {
    $name = 'native-' + $job.name + '-r' + $replica; $path = Join-Path $env:JET3_WORK ($name + '.mdb')
    $db = $null; $failure = $null
    try {
      $db = $E.CreateDatabase($path, $General, 32)
      foreach ($table in $job.request.tables) { Create-Table $db $table }
      if (Has $job.request 'relationships') { foreach ($relation in $job.request.relationships) { Create-Relation $db $relation } }
      $db.Close(); Release $db; $db = $null
    } catch { $failure = Error-Info $E $_ } finally { if ($null -ne $db) { try { $db.Close() } catch {}; Release $db } }
    if (Test-Path $path) { Copy-Item $path (Join-Path $env:JET3_OUTBOX ($name + '.mdb')); $identity = Identity $path } else { $identity = $null }
    $result.creations += @{ name = $job.name; replica = $replica; file = $name + '.mdb'; identity = $identity; failure = $failure }
    Write-Output ('{0} {1}' -f $name, ($(if ($failure) { 'FAILED ' + $failure.message } else { 'ok' })))
  }
}

foreach ($case in $jobs.edits) {
  if ($null -ne $only -and $only -notcontains $case.name) { continue }
  $source = Join-Path $env:JET3_WORK ([string]$case.input + '.mdb')
  if (-not (Test-Path $source)) { $source = @(Get-ChildItem $env:JET3_WORK -Filter ('native-' + ([string]$case.input).Substring(2) + '-*-r1.mdb'))[0].FullName }
  $path = Join-Path $env:JET3_WORK ('native-' + $case.name + '.mdb'); Copy-Item $source $path -Force
  $before = Identity $path; $steps = @(); $db = $null
  try {
    $db = $E.OpenDatabase($path)
    foreach ($step in $case.steps) {
      try { Apply-Request $db ([string]$step.command) $step.request; $steps += @{ ok = $true } }
      catch { $steps += @{ ok = $false; error = (Error-Info $E $_) }; break }
    }
    $db.Close(); Release $db; $db = $null
  } finally { if ($null -ne $db) { try { $db.Close() } catch {}; Release $db } }
  Copy-Item $path (Join-Path $env:JET3_OUTBOX ('native-' + $case.name + '.mdb'))
  $result.edits += @{ name = $case.name; input = [string]$case.input; before = $before; after = (Identity $path); steps = $steps }
  Write-Output ('{0} {1}' -f $case.name, (($steps | ForEach-Object { if ($_.ok) { 'ok' } else { 'refused ' + ($_.error.numbers -join ',') } }) -join ' '))
}
[IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'), ((ConvertTo-Json $result -Depth 100 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
'done'
