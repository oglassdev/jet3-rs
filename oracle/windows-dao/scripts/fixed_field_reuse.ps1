Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86 DAO'}
$helper=Join-Path $env:JET3_WORK 'fixed_field_successor.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Pinned helper parse failure'}
foreach($name in @('Identity','Release','Write-Json','To-Hex','Read-Value','Failure','Observe')){
    $found=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name-eq $name},$false))
    if($found.Count-ne 1){throw 'Missing helper'}
    Invoke-Expression $found[0].Extent.Text
}
$CodePage=[Text.Encoding]::GetEncoding(1252,(New-Object Text.EncoderExceptionFallback),(New-Object Text.DecoderExceptionFallback))
$planPath=Join-Path $env:JET3_WORK 'fixed-field-reuse.plan.json'
$plan=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Identity $helper).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/fixed_field_successor.ps1'-or (Identity $PSCommandPath).sha256-cne $plan.inputs.'oracle/windows-dao/scripts/fixed_field_reuse.ps1'){throw 'Producer input pin mismatch'}
$failure=$null;$endpoint='start'
$result=@{document_type='dao_fixed_field_reuse_phase';plan_sha256=(Identity $planPath).sha256;environment=@{process_bits=32;provider='DAO.DBEngine.36';os=[Environment]::OSVersion.VersionString};mutation_started=$false;observations=@();error=$null;retention_failures=@()}
try{
    foreach($arm in $plan.arms){foreach($replica in 1..3){foreach($role in @('original','updated')){
        $endpoint="$($arm.name)/$replica/$role";$script:endpoint=$endpoint
        $path=Join-Path $env:JET3_WORK "$($arm.name)-r$replica-$role.mdb"
        $obs=Observe $path $plan $arm
        $result.observations+=@{arm=[string]$arm.name;replica=$replica;role=$role;observation=$obs}
        if($obs.status-ne 'pass'){throw 'Read-only capture failed'}
    }}}
}catch{$result.error=if($null-ne $failure){$failure}else{Failure $_}}finally{
    foreach($file in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb'){
        try{Copy-Item -LiteralPath $file.FullName -Destination $env:JET3_OUTBOX}catch{$result.retention_failures+=@{file=$file.Name;error=$_.Exception.Message}}
    }
    Write-Json $result (Join-Path $env:JET3_OUTBOX 'result.json')
}
if($null-ne $result.error-or $result.retention_failures.Count){exit 1}
