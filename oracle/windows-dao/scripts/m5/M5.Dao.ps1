Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-M5EmptyUserSchema {
    param([Parameter(Mandatory = $true)][object]$Database)
    return Read-M4EmptyUserSchema -Database $Database
}

function Invoke-M5DaoPhase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("source", "compact", "verify")][string]$PhaseId,
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$DatabasePaths,
        [Parameter(Mandatory = $true)][pscustomobject]$AcceptedProvider,
        [Parameter(Mandatory = $true)]
        [Collections.ArrayList]$OperationEntries
    )
    $engine = $null
    $workspaces = $null
    $workspace = $null
    $database = $null
    $cleanupErrors = New-Object Collections.ArrayList
    $primaryError = $null
    $snapshot = $null
    try {
        $providerType = [Type]::GetTypeFromProgID(
            [string]$AcceptedProvider.prog_id, $false
        )
        if ($null -eq $providerType) {
            throw "The rebound DAO ProgID has no activation type."
        }
        if ($providerType.GUID.ToString("B").ToUpperInvariant() -cne
            ([string]$AcceptedProvider.clsid).ToUpperInvariant()) {
            throw "M5 DAO activation type CLSID differs from its binding."
        }
        $engine = [Activator]::CreateInstance($providerType)
        if ([string]$engine.Version -cne
            [string]$AcceptedProvider.provider_version) {
            throw "M5 DBEngine.Version differs from its provider binding."
        }
        Add-M4OperationEntry -Entries $OperationEntries -Action "com_activated"
        $paths = $DatabasePaths
        $contract = $Invocation.phase_contract
        if ($PhaseId -ceq "source") {
            $workspaces = $engine.Workspaces
            $workspace = $workspaces.Item([int]0)
            $database = $workspace.CreateDatabase(
                [string]$paths.source_database,
                [string]$contract.locale,
                [int]$contract.create_option_value
            )
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "database_created"
            $version = [string]$database.Version
            if ($version -cne [string]$contract.expected_dao_version) {
                throw "M5 source Database.Version differs from its condition."
            }
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "version_read"
            $count = Read-M5EmptyUserSchema -Database $database
            $snapshot = [ordered]@{
                captured_at_utc = Get-M5UtcTimestamp
                dao_version = $version
                empty_user_schema = $true
                user_table_count = [int]$count
            }
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "empty_schema_read"
        }
        elseif ($PhaseId -ceq "compact") {
            $source = [string]$paths.compact_input_database
            $destination = [string]$paths.compacted_database
            if ([IO.File]::Exists($destination) -or
                [IO.Directory]::Exists($destination)) {
                throw "M5 compact destination must be create-new."
            }
            # SRC-0019 records dbDecrypt=4; the checked plan supplies the
            # complete destination-version/encryption option sum.
            $engine.CompactDatabase(
                $source,
                $destination,
                [Type]::Missing,
                [int]$contract.compact_option_value,
                [Type]::Missing
            )
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "database_compacted"
        }
        else {
            $workspaces = $engine.Workspaces
            $workspace = $workspaces.Item([int]0)
            $database = $workspace.OpenDatabase(
                [string]$paths.verify_database
            )
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "database_opened"
            $version = [string]$database.Version
            if ($version -cne [string]$contract.expected_dao_version) {
                throw "M5 verify Database.Version differs from its condition."
            }
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "version_read"
            $count = Read-M5EmptyUserSchema -Database $database
            $snapshot = [ordered]@{
                captured_at_utc = Get-M5UtcTimestamp
                dao_version = $version
                empty_user_schema = $true
                user_table_count = [int]$count
            }
            Add-M4OperationEntry -Entries $OperationEntries `
                -Action "empty_schema_read"
        }
    }
    catch { $primaryError = $_ }
    finally {
        Close-M1ComObject -Value $database -CleanupErrors $cleanupErrors `
            -Label "M5 DAO Database close"
        Release-M1ComObject -Value $database -CleanupErrors $cleanupErrors `
            -Label "M5 DAO Database release"
        Release-M1ComObject -Value $workspace -CleanupErrors $cleanupErrors `
            -Label "M5 DAO Workspace release"
        Release-M1ComObject -Value $workspaces -CleanupErrors $cleanupErrors `
            -Label "M5 DAO Workspaces release"
        Release-M1ComObject -Value $engine -CleanupErrors $cleanupErrors `
            -Label "M5 DAO DBEngine release"
    }
    try { [GC]::Collect(); [GC]::WaitForPendingFinalizers() }
    catch { [void]$cleanupErrors.Add([string]$_.Exception.Message) }
    Complete-M1DaoHelper -PrimaryError $primaryError `
        -CleanupErrors $cleanupErrors -Label "M5 DAO phase"
    Add-M4OperationEntry -Entries $OperationEntries -Action "database_closed"
    return if ($null -eq $snapshot) { $null } else {
        [pscustomobject]$snapshot
    }
}
