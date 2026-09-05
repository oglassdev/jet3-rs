param([string]$ScriptPath, [string]$PlanPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Expected actual x86 helper test' }
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($ScriptPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($name in @('Release','From-Hex','To-Hex','Set-Value','Read-Value','Get-Fields')) {
    $functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$false))
    if ($functions.Count -ne 1) { throw "Missing helper $name" }
    Invoke-Expression $functions[0].Extent.Text
}
$CodePage=[Text.Encoding]::GetEncoding(1252,(New-Object Text.EncoderExceptionFallback),(New-Object Text.DecoderExceptionFallback))
$plan=Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
$mockField=[pscustomobject]@{Value=$null}
$fields=New-Object PSObject
$fields | Add-Member ScriptMethod Item {param($name) return $script:mockField}
$rs=[pscustomobject]@{Fields=$fields}
$count=0
foreach ($arm in $plan.arms) {
    foreach ($hex in @($arm.initial_hex,$arm.replacement_hex)) {
        Set-Value $rs $arm.field $hex
        $actual=Read-Value $rs $arm.field
        if ($actual -cne $hex) { throw "Typed conversion drift $($arm.name): $actual != $hex" }
        $count++
    }
    foreach ($table in $plan.tables) {
        $definitions=Get-Fields $plan $arm ([string]$table.name)
        $rows=New-Object Collections.ArrayList
        foreach ($row in $table.rows) {
            $values=[object[]]::new($definitions.Length)
            for ($i=0;$i -lt $values.Length;$i++) {
                $expected=if($table.name -eq 'Items' -and $definitions[$i].name -eq 'Value'){$arm.initial_hex}else{$row[$i]}
                Set-Value $rs $definitions[$i] $expected
                $values[$i]=Read-Value $rs $definitions[$i]
                if($values[$i] -cne $expected){throw 'Common field conversion drift'}
                $count++
            }
            [void]$rows.Add($values)
        }
        $json=ConvertTo-Json -InputObject @{rows=@($rows)} -Depth 12
        $roundtrip=$json|ConvertFrom-Json
        if($roundtrip.rows.Count -ne 3){throw 'Row array drift'}
        foreach($row in $roundtrip.rows){if($row -isnot [array] -or $row.Count -ne 3 -or $row[0] -isnot [string]){throw 'Saved value array wrapper'}}
    }
}
$mockField.Value='{guid {00112233-4455-6677-8899-AABBCCDDEEFF}}'
$guid=@{name='Value';type=15}
if((Read-Value $rs $guid) -cne '33221100554477668899aabbccddeeff'){throw 'DAO GUID wrapper drift'}
Write-Output "Passed $count exact typed conversion checks and all row JSON roundtrips; no DAO."
