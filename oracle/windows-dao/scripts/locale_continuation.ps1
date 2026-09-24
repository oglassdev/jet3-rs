Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Release($x){if($null-ne$x-and[Runtime.InteropServices.Marshal]::IsComObject($x)){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($x)}}
$e=New-Object -ComObject DAO.DBEngine.36
$result=@()
foreach($f in Get-ChildItem -LiteralPath $env:JET3_WORK -Filter '*.mdb'){
 $db=$e.OpenDatabase($f.FullName)
 try{
  $text='Continued'+[string][char]0xc1
  $db.Execute("UPDATE Items SET code='$text' WHERE id=999",128)
  $db.Execute("INSERT INTO Items (id,code,spare) VALUES (1000,'NativeNext',1000)",128)
  $db.Execute('DELETE FROM Items WHERE id=3',128)
 }finally{$db.Close();Release $db}
 $name=$f.Name.Replace('-rows.mdb','-continued.mdb')
 Copy-Item $f.FullName (Join-Path $env:JET3_OUTBOX $name)
 $result+=@{file=$name;sha256=(Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
Release $e
[IO.File]::WriteAllText((Join-Path $env:JET3_OUTBOX 'RESULT.json'),((ConvertTo-Json $result -Depth 10)+"`n"),(New-Object Text.UTF8Encoding($false)))
