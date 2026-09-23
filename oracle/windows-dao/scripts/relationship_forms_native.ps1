Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
# Native discovery of relationship attributes and unenforced relationship lifecycles.
# Relation attributes are the SRC-0023/SRC-0026 RelationAttributeEnum inputs.
$General = ';LANGID=0x0409;CP=1252;COUNTRY=0'

function Release($x) { if ($null -ne $x -and [Runtime.InteropServices.Marshal]::IsComObject($x)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($x) } }
function Set-Property($owner, [string]$name, $value) { $a = [object[]]::new(1); $a[0] = $value; [void]$owner.GetType().InvokeMember($name, [Reflection.BindingFlags]::SetProperty, $null, $owner, $a) }
function Identity([string]$p) { @{ size = (Get-Item $p).Length; sha256 = (Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() } }
function Error-Info($engine, $record) {
  $numbers = @(); $es = $ei = $null
  try { $es = $engine.Errors; for ($i = 0; $i -lt $es.Count; $i++) { $ei = $es.Item($i); $numbers += [int]$ei.Number; Release $ei; $ei = $null } } finally { Release $ei; Release $es }
  $x = $record.Exception; while ($null -ne $x.InnerException) { $x = $x.InnerException }
  @{ numbers = $numbers; message = $x.Message }
}

# Column spec: 'name:type[:size]'; types long text memo ole double
$TypeCodes = @{ long = 4; text = 10; memo = 12; ole = 11; double = 7; integer = 3 }
function Make-Table($db, [string]$name, [string[]]$columns, [string[]]$indexes) {
  $td = $db.CreateTableDef($name); $fs = $td.Fields
  foreach ($c in $columns) { $p = $c.Split(':'); $size = 0; if ($p.Count -gt 2) { $size = [int]$p[2] }; $f = $td.CreateField($p[0], $TypeCodes[$p[1]], $size); $fs.Append($f); Release $f }
  Release $fs
  # Index spec: 'name:kind:col1,col2' kind primary|unique|plain
  $xs = $td.Indexes
  foreach ($ix in $indexes) {
    $p = $ix.Split(':'); $x = $td.CreateIndex($p[0]); $xfs = $x.Fields
    foreach ($col in $p[2].Split(',')) { $xf = $x.CreateField($col); $xfs.Append($xf); Release $xf }
    Release $xfs
    if ($p[1] -eq 'primary') { Set-Property $x 'Primary' $true; Set-Property $x 'Unique' $true }
    elseif ($p[1] -eq 'unique') { Set-Property $x 'Unique' $true }
    $xs.Append($x); Release $x
  }
  Release $xs
  $tds = $db.TableDefs; $tds.Append($td); Release $td; Release $tds
}
function Relate($db, [string]$name, [string]$parent, [string]$child, [int]$attributes, [string[]]$pairs) {
  $r = $db.CreateRelation($name, $parent, $child, $attributes); $rfs = $r.Fields
  foreach ($pair in $pairs) { $p = $pair.Split('='); $f = $r.CreateField($p[0]); Set-Property $f 'ForeignName' $p[1]; $rfs.Append($f); Release $f }
  Release $rfs
  $rs = $db.Relations; $rs.Append($r); Release $r; Release $rs
}
function Sql($db, [string]$text) { $db.Execute($text, 128) }
function Unrelate($db, [string]$name) { $rs = $db.Relations; $rs.Delete($name); Release $rs }
function DropTable($db, [string]$name) { $tds = $db.TableDefs; $tds.Delete($name); Release $tds }
function DropColumn($db, [string]$table, [string]$column) { $tds = $db.TableDefs; $td = $tds.Item($table); $fs = $td.Fields; try { $fs.Delete($column) } finally { Release $fs; Release $td; Release $tds } }
function RenameTable($db, [string]$table, [string]$name) { $tds = $db.TableDefs; $td = $tds.Item($table); try { Set-Property $td 'Name' $name } finally { Release $td; Release $tds } }
function RenameColumn($db, [string]$table, [string]$column, [string]$name) { $tds = $db.TableDefs; $td = $tds.Item($table); $fs = $td.Fields; $f = $fs.Item($column); try { Set-Property $f 'Name' $name } finally { Release $f; Release $fs; Release $td; Release $tds } }
function RenameRelation($db, [string]$relation, [string]$name) { $rs = $db.Relations; $r = $rs.Item($relation); try { Set-Property $r 'Name' $name } finally { Release $r; Release $rs } }
function SetRelationAttributes($db, [string]$relation, [int]$value) { $rs = $db.Relations; $r = $rs.Item($relation); try { Set-Property $r 'Attributes' $value } finally { Release $r; Release $rs } }

function Readback($db) {
  $out = [ordered]@{ relations = @(); indexes = @() }
  $rs = $db.Relations
  for ($i = 0; $i -lt $rs.Count; $i++) {
    $r = $rs.Item($i); $fields = @(); $rfs = $r.Fields
    for ($j = 0; $j -lt $rfs.Count; $j++) { $f = $rfs.Item($j); $fields += ([string]$f.Name + '=' + [string]$f.ForeignName); Release $f }
    Release $rfs
    $out.relations += [ordered]@{ name = [string]$r.Name; table = [string]$r.Table; foreign = [string]$r.ForeignTable; attributes = [int]$r.Attributes; fields = $fields }
    Release $r
  }
  Release $rs
  $tds = $db.TableDefs
  for ($i = 0; $i -lt $tds.Count; $i++) {
    $td = $tds.Item($i); $tn = [string]$td.Name
    if ($tn.StartsWith('MSys')) { Release $td; continue }
    $xs = $td.Indexes
    for ($j = 0; $j -lt $xs.Count; $j++) {
      $x = $xs.Item($j); $cols = @(); $xfs = $x.Fields
      for ($k = 0; $k -lt $xfs.Count; $k++) { $xf = $xfs.Item($k); $cols += [string]$xf.Name; Release $xf }
      Release $xfs
      $out.indexes += [ordered]@{ table = $tn; name = [string]$x.Name; primary = [bool]$x.Primary; unique = [bool]$x.Unique; foreign = [bool]$x.Foreign; required = [bool]$x.Required; fields = $cols }
      Release $x
    }
    Release $xs; Release $td
  }
  Release $tds
  return $out
}
function Rows($db, [string]$table) {
  $values = @(); $rs = $db.OpenRecordset('SELECT * FROM [' + $table + ']', 4)
  try { while (-not $rs.EOF) { $row = @(); $fs = $rs.Fields; for ($i = 0; $i -lt $fs.Count; $i++) { $f = $fs.Item($i); $v = $f.Value; if ($v -is [DBNull]) { $row += $null } elseif ($v -is [byte[]]) { $row += ('bytes:' + $v.Length) } else { $row += [string]$v }; Release $f }; Release $fs; $values += ,$row; $rs.MoveNext() } } finally { $rs.Close(); Release $rs }
  return ,$values
}

$E = New-Object -ComObject DAO.DBEngine.36
$dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
$result = [ordered]@{ document_type = 'jet3_relationship_forms_native'; environment = @{ version = [string]$E.Version; os = [Environment]::OSVersion.VersionString; culture = [Globalization.CultureInfo]::CurrentCulture.Name; ansi = [Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage; dll_version = $dll[0].FileVersionInfo.FileVersion; dll_sha256 = (Identity $dll[0].FileName).sha256 }; cases = @() }
. (Join-Path $env:JET3_WORK 'cases.ps1')
$only = $null; if (Test-Path (Join-Path $env:JET3_WORK 'only.txt')) { $only = @(Get-Content (Join-Path $env:JET3_WORK 'only.txt')) }

foreach ($case in $Cases) {
  if ($null -ne $only -and $only -notcontains $case.name) { continue }
  foreach ($replica in 1..[int]$case.replicas) {
    $name = $case.name + '-r' + $replica; $path = Join-Path $env:JET3_WORK ($name + '.mdb')
    $steps = @(); $db = $null; $stage = 0
    try {
      $db = $E.CreateDatabase($path, $General, 32)
      foreach ($step in $case.steps) {
        if ($step -is [string] -and $step -eq 'checkpoint') {
          $db.Close(); Release $db; $db = $null
          Copy-Item $path (Join-Path $env:JET3_OUTBOX ('{0}-s{1}.mdb' -f $name, $stage)); $stage++
          $db = $E.OpenDatabase($path); continue
        }
        try { $value = & $step $db; $steps += @{ ok = $true; value = $value } }
        catch { $steps += @{ ok = $false; error = (Error-Info $E $_) } }
      }
      $rb = Readback $db; $rows = [ordered]@{}
      foreach ($t in $case.tables) { try { $rows[$t] = Rows $db $t } catch { $rows[$t] = 'error: ' + $_.Exception.Message } }
      $db.Close(); Release $db; $db = $null
      $identity = Identity $path; Copy-Item $path (Join-Path $env:JET3_OUTBOX ($name + '.mdb'))
      $result.cases += [ordered]@{ name = $name; identity = $identity; steps = $steps; readback = $rb; rows = $rows }
      Write-Output ('{0} {1}' -f $name, (($steps | ForEach-Object { if ($_.ok) { 'ok' } else { 'err' + ($_.error.numbers -join ',') } }) -join ' '))
    } catch { $result.cases += @{ name = $name; fatal = $_.Exception.Message }; Write-Output ('{0} FATAL {1}' -f $name, $_.Exception.Message) }
    finally { if ($null -ne $db) { try { $db.Close() } catch {}; Release $db } }
  }
}
[IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'), ((ConvertTo-Json $result -Depth 100) + "`n"), (New-Object Text.UTF8Encoding($false)))
'done'
