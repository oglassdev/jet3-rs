Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
function Release($value) {
    if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
    }
}
function Identity([string]$path) {
    @{size=(Get-Item $path).Length;sha256=(Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Write-Json($value, [string]$path) {
    [IO.File]::WriteAllText($path, ((ConvertTo-Json -InputObject $value -Depth 30 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function Set-Value($field, $value) {
    $args = [object[]]::new(1); $args[0] = $value
    [void]$field.GetType().InvokeMember('Value', [Reflection.BindingFlags]::SetProperty, $null, $field, $args)
}
$engine = New-Object -ComObject DAO.DBEngine.36
$dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object {$_.ModuleName -ieq 'dao360.dll'})[0]
$result = @{environment=@{bits=32;os=[Environment]::OSVersion.VersionString;dll_version=$dll.FileVersionInfo.FileVersion;dll=Identity $dll.FileName};cases=@()}
$specs = Get-Content -Raw -Encoding UTF8 (Join-Path $env:JET3_WORK 'locales.json') | ConvertFrom-Json
try {
    foreach ($spec in $specs) {
        $path = Join-Path $env:JET3_WORK ($spec.name+'.mdb')
        $db = $rs = $fields = $id = $value = $null
        $entry = @{name=$spec.name;lang=$spec.lang;cp=$spec.cp;rows=@();indexes=@();error=$null}
        try {
            $locale = ';LANGID=0x{0:x4};CP={1};COUNTRY=0' -f [int]$spec.lang,[int]$spec.cp
            $db = $engine.CreateDatabase($path,$locale,32)
            $entry.collating_order = [int]$db.CollatingOrder
            $db.Execute('CREATE TABLE T (Id LONG, V TEXT(255), CONSTRAINT pk PRIMARY KEY(Id))',128)
            $db.Execute('CREATE INDEX ix ON T (V)',128)
            $db.Execute('CREATE INDEX dx ON T (V DESC)',128)
            $rs = $db.OpenRecordset('T',1); $fields=$rs.Fields; $id=$fields.Item('Id'); $value=$fields.Item('V')
            for ($i=0; $i -lt $spec.samples.Count; $i++) {
                $rs.AddNew(); Set-Value $id ([int]$i); Set-Value $value ([string]$spec.samples[$i]); $rs.Update()
            }
            Release $value; $value=$null; Release $id; $id=$null; Release $fields; $fields=$null
            $rs.Close(); Release $rs; $rs=$null
            $db.Close(); Release $db; $db=$null
            $entry.identity = Identity $path
            $db=$engine.OpenDatabase($path,$false,$true)
            $rs=$db.OpenRecordset('T',1); $fields=$rs.Fields; $id=$fields.Item('Id'); $value=$fields.Item('V')
            foreach ($index in @('pk','ix','dx')) {
                $rs.Index=$index; $rs.MoveFirst(); $traversal=New-Object 'Collections.Generic.List[int]'
                while (-not $rs.EOF) {
                    $n=[int]$id.Value; $text=[string]$value.Value
                    if ($text -cne [string]$spec.samples[$n]) { throw ('Text round trip '+$n+' expected '+[int][char]([string]$spec.samples[$n])[0]+' observed '+[int][char]$text[0]) }
                    $traversal.Add($n)
                    if ($index -eq 'pk') { $entry.rows += ,@{id=$n;value=$text} }
                    $rs.MoveNext()
                }
                $entry.indexes += @{name=$index;ids=@($traversal.ToArray())}
            }
            Release $value; $value=$null; Release $id; $id=$null; Release $fields; $fields=$null
            $rs.Close(); Release $rs; $rs=$null
            $db.Close(); Release $db; $db=$null
            if ((Identity $path).sha256 -cne $entry.identity.sha256) { throw 'Readback changed bytes' }
        } catch {
            $numbers=@(); $errors=$engine.Errors
            foreach ($errorItem in $errors) { $numbers += [int]$errorItem.Number; Release $errorItem }
            Release $errors
            $entry.error=@{message=$_.Exception.Message;numbers=$numbers}
        } finally {
            Release $value; Release $id; Release $fields
            if ($null -ne $rs) { try {$rs.Close()} catch {}; Release $rs }
            if ($null -ne $db) { try {$db.Close()} catch {}; Release $db }
        }
        if (Test-Path $path) {
            Copy-Item $path (Join-Path $env:JET3_OUTBOX ($spec.name+'.mdb'))
            $entry.closed_identity=Identity $path
        }
        $result.cases += $entry
        Write-Json $entry (Join-Path $env:JET3_OUTBOX ($spec.name+'.json'))
        Write-Json $result (Join-Path $env:JET3_OUTBOX 'RESULT.json')
        Write-Output ($spec.name+' '+$(if($entry.error){$entry.error.message}else{'pass'}))
    }
} finally { Release $engine }
