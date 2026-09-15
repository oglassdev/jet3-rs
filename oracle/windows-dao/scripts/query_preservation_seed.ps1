$ErrorActionPreference = 'Stop'

function Release($value) {
    if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
    }
}
function Identity([string]$path) {
    @{ size = (Get-Item -LiteralPath $path).Length; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Write-Json($value, [string]$path) {
    [IO.File]::WriteAllText($path, ((ConvertTo-Json -InputObject $value -Depth 30 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
}
function Safe-Value($property) {
    try {
        $value = $property.Value
        if ($null -eq $value -or [Convert]::IsDBNull($value)) { return @{ status='ok'; is_null=$true; value=$null } }
        return @{ status='ok'; is_null=$false; value=[string]$value }
    } catch {
        return @{ status='error'; type=$_.Exception.GetType().FullName; message=$_.Exception.Message; hresult=[int]$_.Exception.HResult }
    }
}
function Read-Query($query) {
    $parameters=$properties=@(); $parameter=$property=$null
    try {
        foreach ($parameter in $query.Parameters) {
            $parameters += @{ name=[string]$parameter.Name; type=[int]$parameter.Type; direction=[int]$parameter.Direction; properties=@($parameter.Properties | ForEach-Object { @{ name=[string]$_.Name; type=[int]$_.Type; result=(Safe-Value $_) } }) }
            Release $parameter; $parameter=$null
        }
        foreach ($property in $query.Properties) {
            $properties += @{ name=[string]$property.Name; type=[int]$property.Type; result=(Safe-Value $property) }
            Release $property; $property=$null
        }
        return @{ name=[string]$query.Name; sql=[string]$query.SQL; type=[int]$query.Type; returns_records=[bool]$query.ReturnsRecords; updatable=[bool]$query.Updatable; date_created=([datetime]$query.DateCreated).ToOADate(); last_updated=([datetime]$query.LastUpdated).ToOADate(); parameters=@($parameters); properties=@($properties) }
    } finally { Release $property; Release $parameter }
}

$queries = @(
    @{ name='Q Simple Select'; sql='SELECT Parent.Id, Parent.Label FROM Parent ORDER BY Parent.Id;' },
    @{ name='Q_Join_Child_Parent'; sql='SELECT Child.Id, Child.ParentId, Parent.Label FROM Parent INNER JOIN Child ON Parent.Id = Child.ParentId;' },
    @{ name='Q Aggregate Count'; sql='SELECT Child.ParentId, Count(Child.Id) AS ChildCount FROM Child GROUP BY Child.ParentId;' },
    @{ name='Q Parameter'; sql='PARAMETERS [pParent] Long; SELECT Child.Id, Child.ParentId FROM Child WHERE Child.ParentId=[pParent] ORDER BY Child.Id;' }
)
$engine=$database=$query=$null
$receipts=@()
try {
    foreach ($name in @('plain-r1','plain-r2','rich-r1','rich-r2','boundary-r1','boundary-r2')) {
        $source=Join-Path $PSScriptRoot "$name.mdb"
        $work=$source
        $before=Identity $work
        $engine=New-Object -ComObject DAO.DBEngine.36
        $database=$engine.OpenDatabase($work,$false,$false)
        foreach($spec in $queries) {
            $query=$database.CreateQueryDef([string]$spec.name,[string]$spec.sql)
            Release $query; $query=$null
        }
        $database.Close(); Release $database; $database=$null; Release $engine; $engine=$null
        $engine=New-Object -ComObject DAO.DBEngine.36
        $database=$engine.OpenDatabase($work,$false,$true)
        $captured=@()
        foreach($query in $database.QueryDefs) { $captured += ,(Read-Query $query); Release $query; $query=$null }
        $version=[string]$database.Version
        $database.Close(); Release $database; $database=$null; Release $engine; $engine=$null
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        $after=Identity $work
        Copy-Item -LiteralPath $work -Destination (Join-Path $env:JET3_OUTBOX "$name-query-source.mdb")
        $receipt=@{ case=$name; before=$before; after=$after; version=$version; queries=@($captured) }
        Write-Json $receipt (Join-Path $env:JET3_OUTBOX "$name-query-source.json")
        $receipts += ,$receipt
    }
    $provider=(Get-Item 'C:\Program Files (x86)\Common Files\Microsoft Shared\DAO\dao360.dll')
    Write-Json @{ status='pass'; cases=@($receipts); environment=@{ os=[Environment]::OSVersion.VersionString; culture=[Globalization.CultureInfo]::CurrentCulture.Name; provider_path=$provider.FullName; provider_version=$provider.VersionInfo.FileVersion; provider_sha256=(Get-FileHash $provider.FullName -Algorithm SHA256).Hash.ToLowerInvariant() } } (Join-Path $env:JET3_OUTBOX 'seed-report.json')
} finally {
    Release $query
    if ($null -ne $database) { try { $database.Close() } catch {} }
    Release $database; Release $engine
}
