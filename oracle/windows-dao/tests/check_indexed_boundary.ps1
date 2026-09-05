param([Parameter(Mandatory=$true)][string]$ScriptPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([IntPtr]::Size-ne 4){throw 'Expected x86'}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($ScriptPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
$row=@($ast.FindAll({param($n)$n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq 'Row'},$false))
if($row.Count-ne 1){throw 'Row helper'};Invoke-Expression $row[0].Extent.Text
$fields=@{Id=[pscustomobject]@{Value=101};Name=[pscustomobject]@{Value=('x'*80)};Price=[pscustomobject]@{Value=[decimal]::Parse('-12.3456',[Globalization.CultureInfo]::InvariantCulture)};Active=[pscustomobject]@{Value=$true};Body=[pscustomobject]@{Value=('n'*4096)}}
$collection=[pscustomobject]@{Fields=$fields};$collection|Add-Member ScriptMethod Item {param($Name)return $this.Fields[$Name]}
$rs=[pscustomobject]@{Fields=$collection}
$value=Row $rs 'Items'
if($value.Count-ne 4-or $value[0]-isnot [int]-or $value[1]-cne ('x'*80)-or $value[2]-cne '-12.3456'-or $value[3]-isnot [bool]-or -not $value[3]){throw 'Typed Items'}
$fields.Price.Value=[DBNull]::Value;$fields.Active.Value=$false
$value=Row $rs 'Items';if($null-ne $value[2]-or $value[3]){throw 'Null/false'}
$value=Row $rs 'Notes';if($value.Count-ne 2-or $value[1]-cne ('n'*4096)){throw 'Memo'}
$fields.Body.Value=[DBNull]::Value;$value=Row $rs 'Notes';if($null-ne $value[1]){throw 'Null Memo'}
Write-Output 'x86 producer syntax and typed full-row serialization passed; no DAO executed'

$fields.Price.Value=[decimal]::Parse('-12.3456',[Globalization.CultureInfo]::InvariantCulture)
$fields.Active.Value=$true
$rows=New-Object Collections.ArrayList;[void]$rows.Add((Row $rs 'Items'))
$found=if($true){Row $rs 'Items'}else{$null}
$nested=@{rows=[object[]]$rows.ToArray();seek=@(@{query=101;row=$found},@{query=-1;row=$null})}
$roundtrip=$nested|ConvertTo-Json -Depth 12 -Compress|ConvertFrom-Json
if($roundtrip.rows.Count-ne 1-or $roundtrip.rows[0].Count-ne 4-or $roundtrip.rows[0][2]-isnot [string]-or $roundtrip.rows[0][2]-cne '-12.3456'-or $roundtrip.seek[0].row.Count-ne 4-or $roundtrip.seek[0].row[3]-isnot [bool]-or $null-ne $roundtrip.seek[1].row){throw 'Nested JSON rows/Seek'}
Write-Output 'Nested JSON rows/Seek roundtrip passed; no DAO executed'
