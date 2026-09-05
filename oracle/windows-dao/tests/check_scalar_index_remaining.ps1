param([string]$Root)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Host-language check requires acquisition x86 runtime' }
$scriptPath = Join-Path $Root 'script.ps1'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($name in @('Release','From-Hex','To-Hex','Set-Value','Read-Value','Read-Row')) {
    $definition=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$false))
    if($definition.Count -ne 1){throw "Missing helper $name"}
    Invoke-Expression $definition[0].Extent.Text
}
$plan=Get-Content (Join-Path $Root 'plan.json') -Raw | ConvertFrom-Json
$output=New-Object Collections.ArrayList
foreach($arm in $plan.arms){
    $fields=[pscustomobject]@{Storage=@{}}
    $fields | Add-Member -MemberType ScriptMethod -Name Item -Value {param($Name) return $this.Storage[$Name]}
    foreach($definition in $arm.fields){$fields.Storage[$definition.name]=[pscustomobject]@{Value=$null}}
    $fields.Storage.Tag=[pscustomobject]@{Value=1}
    $rs=[pscustomobject]@{Fields=$fields}
    foreach($row in $arm.rows){
        for($i=0;$i -lt $arm.fields.Count;$i++){Set-Value $rs $arm.fields[$i] $row[$i]}
        $read=Read-Row $rs $arm
        [void]$output.Add(@{arm=$arm.name; row=$read.values})
    }
}
ConvertTo-Json -InputObject @($output) -Depth 10 -Compress
