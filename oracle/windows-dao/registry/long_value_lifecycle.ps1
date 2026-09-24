# Long-value lifecycle (EXP-0234/0238/0240): replay each phase on a DAO control beside the
# Rust candidate, apply native writes to both, and in the continuation round capture Rust
# edits of the native DAO output beside the same edits made by DAO.
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'Rows.ps1')

function Invoke-Phases($Case) {
    $outcome = @{ name = [string]$Case.name; status = 'running'; phases = @(); error = $null }
    try {
        if ($manifest.mode -eq 'continuation') {
            $phase = @{ name = 'continued'; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
            foreach ($role in @('candidate', 'control')) {
                $path = Join-Path $env:JET3_WORK "$($Case.name)-continued-$role.mdb"
                $source = if ($role -eq 'candidate') { "$($Case.name)-continued.mdb" } else { "$($Case.name)-native-source.mdb" }
                Copy-Item -LiteralPath (Join-Path $env:JET3_WORK $source) -Destination $path
                if ($role -eq 'control') { $phase.mutations[$role] = Update-LongRows $path $Case $Case.continue }
                $phase.captures[$role] = Capture-LongRows $path $Case
                if ($phase.captures[$role].status -ne 'pass') { throw 'Continuation capture failed' }
            }
        } else {
            $previous = @{}
            foreach ($spec in $Case.phases) {
                $phase = @{ name = [string]$spec.name; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
                foreach ($role in @('candidate', 'control')) {
                    $path = Join-Path $env:JET3_WORK "$($Case.name)-$($spec.name)-$role.mdb"
                    if ($role -eq 'candidate') {
                        Copy-Item -LiteralPath (Join-Path $env:JET3_WORK "$($Case.name)-$($spec.name).mdb") -Destination $path
                    } elseif ($spec.name -eq 'initial') {
                        New-LongControl $path $Case
                    } else {
                        Copy-Item -LiteralPath $previous[$role] -Destination $path
                        $phase.mutations[$role] = Update-LongRows $path $Case $spec.operations
                    }
                    $phase.captures[$role] = Capture-LongRows $path $Case
                    if ($phase.captures[$role].status -ne 'pass') { throw 'Lifecycle capture failed' }
                    $previous[$role] = $path
                }
            }
            $phase = @{ name = 'native'; captures = @{}; mutations = @{} }; $outcome.phases += ,$phase
            foreach ($role in @('candidate', 'control')) {
                $path = Join-Path $env:JET3_WORK "$($Case.name)-native-$role.mdb"
                Copy-Item -LiteralPath $previous[$role] -Destination $path
                $phase.mutations[$role] = Update-LongRows $path $Case $Case.native
                $phase.captures[$role] = Capture-LongRows $path $Case
                if ($phase.captures[$role].status -ne 'pass') { throw 'Native writability capture failed' }
            }
        }
        $outcome.status = 'pass'
    } catch { $outcome.status = 'error'; $outcome.error = Failure $_ }
    return $outcome
}

$manifest = Read-Manifest 'long-value-lifecycle.json'
$result = New-Result 'dao_long_value_lifecycle_result' $manifest
try {
    Test-Inputs $manifest
    $result.environment = Get-Environment
    foreach ($case in $manifest.cases) { $result.cases += ,(Invoke-Phases $case) }
} catch { $result.error = Failure $_ } finally { Save-Outputs $result 'result.json' }
if (-not (Test-Complete $result)) { exit 1 }
