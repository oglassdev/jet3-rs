Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected x86 DAO' }
$helper = Join-Path $env:JET3_WORK 'field_update.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($helper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Helper syntax' }
foreach ($name in @('Identity', 'Release', 'Write-Json')) {
    $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $false))
    if ($found.Count -ne 1) { throw 'Missing helper' }
    Invoke-Expression $found[0].Extent.Text
}
$creationHelper = Join-Path $env:JET3_WORK 'multiple_long_value_creation.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($creationHelper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Creation helper syntax' }
foreach ($name in @('Failure', 'Set-Cell', 'Set-Row', 'Read-Row', 'Read-Rows', 'New-Control', 'Mutate', 'Capture')) {
    $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $false))
    if ($found.Count -ne 1) { throw 'Missing creation helper function' }
    Invoke-Expression $found[0].Extent.Text
}
function Apply-Operations([string]$Path, $Case, $Operations) {
    $request = @{ name = $Case.name; fields = $Case.fields; generated = $Case.generated; native = $Operations }
    return Mutate $Path $request
}
$script:endpoint = 'manifest'
$manifestPath = Join-Path $env:JET3_WORK 'long-value-lifecycle.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$result = @{ document_type = 'dao_long_value_lifecycle_result'; source_revision = $manifest.source_revision;
    manifest_sha256 = (Identity $manifestPath).sha256; environment = @{}; cases = @(); error = $null; retention_failures = @() }
try {
    foreach ($pair in @(@($PSCommandPath, 'oracle/windows-dao/scripts/long_value_lifecycle.ps1'), @($creationHelper, 'oracle/windows-dao/scripts/multiple_long_value_creation.ps1'), @($helper, 'oracle/windows-dao/scripts/field_update.ps1'))) {
        if ((Identity $pair[0]).sha256 -cne $manifest.inputs.($pair[1]).sha256) { throw 'Producer/helper identity differs' }
    }
    foreach ($property in $manifest.files.PSObject.Properties) {
        $actual = Identity (Join-Path $env:JET3_WORK $property.Name)
        if ($actual.sha256 -cne $property.Value.sha256 -or $actual.size -ne $property.Value.size) { throw "Input identity differs: $($property.Name)" }
    }
    $engine = New-Object -ComObject DAO.DBEngine.36
    try {
        $dll = @([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'dao360.dll' })
        if ($dll.Count -ne 1) { throw 'Loaded DAO module absent or ambiguous' }
        $result.environment = @{ process_bits = 32; provider = 'DAO.DBEngine.36'; provider_version = [string]$engine.Version;
            os = [Environment]::OSVersion.VersionString; powershell = [string]$PSVersionTable.PSVersion; clr = [Environment]::Version.ToString();
            culture = [Globalization.CultureInfo]::CurrentCulture.Name; timezone = [TimeZoneInfo]::Local.Id;
            dll = @{ path = $dll[0].FileName; version = $dll[0].FileVersionInfo.FileVersion; sha256 = (Identity $dll[0].FileName).sha256 } }
    } finally { Release $engine }
    foreach ($case in $manifest.cases) {
        $outcome = @{ name = [string]$case.name; status = 'running'; phases = @(); error = $null }; $result.cases += ,$outcome
        try {
            if ($manifest.mode -eq 'continuation') {
                $phase = @{ name = 'continued'; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($case.name)-continued-$role.mdb"
                    $source = if ($role -eq 'candidate') { "$($case.name)-continued.mdb" } else { "$($case.name)-native-source.mdb" }
                    Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $source) -Destination $path
                    if ($role -eq 'control') { $phase.mutations[$role] = Apply-Operations $path $case $case.continue }
                    $phase.captures[$role] = Capture $path $case
                    if ($phase.captures[$role].status -ne 'pass') { throw 'Continuation capture failed' }
                }
            } else {
                $previous = @{}
                foreach ($spec in $case.phases) {
                    $phase = @{ name = [string]$spec.name; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
                    foreach ($role in @('candidate', 'control')) {
                        $path = Join-Path $env:JET3_WORK "$($case.name)-$($spec.name)-$role.mdb"
                        if ($role -eq 'candidate') {
                            Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($case.name)-$($spec.name).mdb") -Destination $path
                        } elseif ($spec.name -eq 'initial') {
                            New-Control $path $case
                        } else {
                            Copy-Item -LiteralPath $previous[$role] -Destination $path
                            $phase.mutations[$role] = Apply-Operations $path $case $spec.operations
                        }
                        $phase.captures[$role] = Capture $path $case
                        if ($phase.captures[$role].status -ne 'pass') { throw 'Lifecycle capture failed' }
                        $previous[$role] = $path
                    }
                }
                $phase = @{ name = 'native'; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($case.name)-native-$role.mdb"
                    Copy-Item -LiteralPath $previous[$role] -Destination $path
                    $phase.mutations[$role] = Apply-Operations $path $case $case.native
                    $phase.captures[$role] = Capture $path $case
                    if ($phase.captures[$role].status -ne 'pass') { throw 'Native writability capture failed' }
                }
            }
            $outcome.status = 'pass'
        } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    }
} catch { $result.error = Failure $_ } finally {
    foreach ($file in Get-ChildItem -LiteralPath $env:JET3_WORK -File | Where-Object { $_.Extension -in @('.mdb', '.json') }) {
        try { Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX } catch { $result.retention_failures += @{ file = $file.Name; message = $_.Exception.Message } }
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if ($null -ne $result.error -or $result.retention_failures.Count -or @($result.cases | Where-Object { $_.status -ne 'pass' }).Count) { exit 1 }
