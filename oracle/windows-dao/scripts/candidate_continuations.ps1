$ErrorActionPreference = 'Stop'

function Release-Com($value) {
    if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
    }
}

function Set-Property($owner, [string]$name, $value) {
    $arguments = [object[]]::new(1)
    $arguments[0] = $value
    [void]$owner.GetType().InvokeMember($name, [Reflection.BindingFlags]::SetProperty, $null, $owner, $arguments)
}

function Error-Info($engine, $exception) {
    $numbers = @()
    $messages = @()
    $errors = $errorItem = $null
    try {
        $errors = $engine.Errors
        for ($i = 0; $i -lt $errors.Count; $i++) {
            $errorItem = $errors.Item($i)
            $numbers += [int]$errorItem.Number
            $messages += [string]$errorItem.Description
            Release-Com $errorItem; $errorItem = $null
        }
    } finally { Release-Com $errorItem; Release-Com $errors }
    return [ordered]@{ hresult=$exception.HResult; numbers=$numbers; messages=$messages; exception=$exception.Message }
}

function Add-Row($db, [string]$table, $values) {
    $rs = $fields = $field = $null
    try {
        $rs = $db.OpenRecordset($table, 2)
        $rs.AddNew()
        foreach ($name in $values.Keys) {
            $fields = $rs.Fields
            $field = $fields.Item([string]$name)
            Set-Property $field 'Value' $values[$name]
            Release-Com $field; $field = $null
            Release-Com $fields; $fields = $null
        }
        $rs.Update()
        $rs.Close()
        Release-Com $rs; $rs = $null
    } finally {
        Release-Com $field; Release-Com $fields
        if ($null -ne $rs) { try { $rs.Close() } catch {}; Release-Com $rs }
    }
}

function Rows($db, [string]$table) {
    $answer = @()
    $rs = $fields = $field = $null
    try {
        $rs = $db.OpenRecordset($table, 4)
        while (-not $rs.EOF) {
            $row = [ordered]@{}
            $fields = $rs.Fields
            for ($i = 0; $i -lt $fields.Count; $i++) {
                $field = $fields.Item($i)
                $value = $field.Value
                $row[[string]$field.Name] = if ($null -eq $value -or [Convert]::IsDBNull($value)) { $null } else { $value }
                Release-Com $field; $field = $null
            }
            Release-Com $fields; $fields = $null
            $answer += ,$row
            $rs.MoveNext()
        }
        $rs.Close(); Release-Com $rs; $rs = $null
    } finally {
        Release-Com $field; Release-Com $fields
        if ($null -ne $rs) { try { $rs.Close() } catch {}; Release-Com $rs }
    }
    return ,$answer
}

function Run-Case($engine, [string]$name, [string]$inputName, [scriptblock]$action, [string[]]$tables) {
    $local = Join-Path $env:JET3_WORK ($name + '--after.mdb')
    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $inputName) -Destination $local -Force
    $before = (Get-FileHash -Algorithm SHA256 -LiteralPath $local).Hash.ToLowerInvariant()
    $db = $null
    try {
        $db = $engine.OpenDatabase($local, $false, $false)
        $events = @(& $action $db $engine)
        $snapshots = [ordered]@{}
        foreach ($table in $tables) { $snapshots[$table] = @(Rows $db $table) }
        $db.Close(); Release-Com $db; $db = $null
        Copy-Item -LiteralPath $local -Destination (Join-Path $env:JET3_OUTBOX ($name + '--after.mdb')) -Force
        return [ordered]@{
            name=$name; outcome='success'; input_sha256=$before
            output_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $local).Hash.ToLowerInvariant()
            output_size=(Get-Item $local).Length; events=$events; rows=$snapshots
        }
    } finally {
        if ($null -ne $db) { try { $db.Close() } catch {}; Release-Com $db }
    }
}

$engine = $null
try {
    $engine = New-Object -ComObject DAO.DBEngine.36
    $results = @()
    $results += Run-Case $engine 'auto-append' 'candidate-column-add-auto.mdb' {
        param($db,$engine)
        Add-Row $db 'Target' ([ordered]@{Id=900;Code='dao-auto';Label='DAO auto';Spare=10})
        [ordered]@{ operation='insert'; outcome='success'; expected_added_auto=4 }
    } @('Target')
    $results += Run-Case $engine 'required-option' 'candidate-column-required-old-null.mdb' {
        param($db,$engine)
        $events = @()
        try {
            Add-Row $db 'Target' ([ordered]@{Id=901;Code='missing-label';Spare=11})
            $events += [ordered]@{ operation='insert-missing-required'; outcome='unexpected-success' }
        } catch {
            $events += [ordered]@{ operation='insert-missing-required'; outcome='refusal'; error=Error-Info $engine $_.Exception }
        }
        Add-Row $db 'Target' ([ordered]@{Id=902;Code='valid-label';Label='valid';Spare=12})
        $events += [ordered]@{ operation='insert-valid-required'; outcome='success' }
        return ,$events
    } @('Target')
    $results += Run-Case $engine 'azl-option' 'candidate-column-azl-old-empty.mdb' {
        param($db,$engine)
        $events = @()
        try {
            Add-Row $db 'Target' ([ordered]@{Id=903;Code='empty-label';Label='';Spare=13})
            $events += [ordered]@{ operation='insert-empty-disallowed'; outcome='unexpected-success' }
        } catch {
            $events += [ordered]@{ operation='insert-empty-disallowed'; outcome='refusal'; error=Error-Info $engine $_.Exception }
        }
        Add-Row $db 'Target' ([ordered]@{Id=904;Code='nonempty-label';Label='nonempty';Spare=14})
        $events += [ordered]@{ operation='insert-nonempty'; outcome='success' }
        return ,$events
    } @('Target')
    $results += Run-Case $engine 'gap-insert' 'candidate-column-gap-variable-insert.mdb' {
        param($db,$engine)
        Add-Row $db 'Target' ([ordered]@{Id=500;Label='Epsilon';Spare=11;NewVariable='dao-gap-continuation'})
        [ordered]@{ operation='insert-after-gap'; outcome='success' }
    } @('Target')
    $results += Run-Case $engine 'relationship-mutation' 'candidate-relationship-add.mdb' {
        param($db,$engine)
        $events = @()
        $rs = $fields = $field = $null
        try {
            $rs = $db.OpenRecordset('AltParent',2); $rs.FindFirst('Id = 1'); $rs.Edit()
            $fields=$rs.Fields; $field=$fields.Item('Id'); Set-Property $field 'Value' ([int]11)
            Release-Com $field; $field=$null; Release-Com $fields; $fields=$null
            $rs.Update()
            $events += [ordered]@{ operation='update-referenced-key-without-cascade'; outcome='unexpected-success' }
        } catch {
            $events += [ordered]@{ operation='update-referenced-key-without-cascade'; outcome='refusal'; error=Error-Info $engine $_.Exception }
        } finally {
            Release-Com $field; Release-Com $fields; if($null-ne$rs){try{$rs.Close()}catch{};Release-Com $rs}
        }
        Add-Row $db 'AltParent' ([ordered]@{Id=2})
        Add-Row $db 'AltChild' ([ordered]@{Id=2;ParentId=2})
        $events += [ordered]@{ operation='insert-valid-parent-child'; outcome='success' }
        try {
            Add-Row $db 'AltChild' ([ordered]@{Id=3;ParentId=999})
            $events += [ordered]@{ operation='insert-orphan'; outcome='unexpected-success' }
        } catch {
            $events += [ordered]@{ operation='insert-orphan'; outcome='refusal'; error=Error-Info $engine $_.Exception }
        }
        return ,$events
    } @('AltParent','AltChild')

    $providerPath = 'C:\Program Files (x86)\Common Files\Microsoft Shared\DAO\dao360.dll'
    $provider = Get-Item -LiteralPath $providerPath
    $document = [ordered]@{
        document_type='jet3_schema_candidate_native_continuations'; status='complete'
        provider=[ordered]@{ version=$provider.VersionInfo.FileVersion; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $providerPath).Hash.ToLowerInvariant(); bits=[IntPtr]::Size*8; windows=[Environment]::OSVersion.Version.ToString() }
        results=$results
    }
    [IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'),(($document | ConvertTo-Json -Depth 20)+"`n"),(New-Object Text.UTF8Encoding($false)))
    $bad = @($results | ForEach-Object { $_.events } | Where-Object { $_.outcome -eq 'unexpected-success' })
    if ($bad.Count -gt 0) { exit 1 }
} finally {
    Release-Com $engine
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
