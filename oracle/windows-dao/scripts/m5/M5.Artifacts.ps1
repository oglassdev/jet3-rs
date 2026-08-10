Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-M5Snapshot {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$Observation
    )
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_snapshot"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        captured_while_database_open = $true
        captured_at_utc = [string]$Observation.captured_at_utc
        dao_version = [string]$Observation.dao_version
        empty_user_schema = $true
        user_table_count = [int]$Observation.user_table_count
    }
}

function New-M5OperationLog {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][Collections.ArrayList]$Entries,
        [Parameter(Mandatory = $true)][string]$StartedAt
    )
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_operation_log"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        worker_run_id = [string]$Invocation.worker_run_id
        started_at_utc = $StartedAt
        completed_at_utc = Get-M5UtcTimestamp
        actions = @($Entries | ForEach-Object { [string]$_.action })
        status = "pass"
    }
}

function ConvertTo-M5DatabaseObservation {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Locator,
        [Parameter(Mandatory = $true)][pscustomobject]$Observation,
        [AllowNull()][string]$PrefixLocator
    )
    return [ordered]@{
        database_role = $Role
        path = $Locator
        bytes = [long]$Observation.bytes
        sha256 = [string]$Observation.sha256
        prefix_sha256 = [string]$Observation.prefix_sha256
        prefix = if ($null -eq $PrefixLocator) { $null } else {
            [ordered]@{
                path = $PrefixLocator
                sha256 = [string]$Observation.prefix_sha256
            }
        }
    }
}

function New-M5WorkerResult {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$Provider,
        [Parameter(Mandatory = $true)][string]$ProviderHash,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$InvocationSha256,
        [Parameter(Mandatory = $true)][pscustomobject]$Paths,
        [Parameter(Mandatory = $true)][byte[]]$OperationLogBytes,
        [AllowNull()][byte[]]$SnapshotBytes,
        [Parameter(Mandatory = $true)][object[]]$DatabaseObservations
    )
    return [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_worker_result"
        experiment_id = $script:M5ExperimentId
        sample_id = [string]$Invocation.sample_id
        condition_id = [string]$Invocation.condition_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        worker_run_id = [string]$Invocation.worker_run_id
        worker_ordinal = [int]$Invocation.worker_ordinal
        nonce = [string]$Invocation.nonce
        process_id = [int]$PID
        architecture = "x86"
        provider = [ordered]@{
            powershell_version = $PSVersionTable.PSVersion.ToString()
            prog_id = [string]$Provider.prog_id
            clsid = ([string]$Provider.clsid).ToUpperInvariant()
            server_sha256 = $ProviderHash
        }
        started_at_utc = $StartedAt
        finished_at_utc = Get-M5UtcTimestamp
        bindings_verified_before_com = $true
        invocation_sha256 = $InvocationSha256
        operation_log = [ordered]@{
            path = [string]$Paths.operation_log
            sha256 = Get-M4BytesSha256 -Bytes $OperationLogBytes
        }
        snapshot = if ($null -eq $SnapshotBytes) { $null } else {
            [ordered]@{
                path = [string]$Paths.snapshot
                sha256 = Get-M4BytesSha256 -Bytes $SnapshotBytes
            }
        }
        database_observations = @($DatabaseObservations)
        execution_status = "pass"
    }
}

function Complete-M5WorkerFailure {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.ErrorRecord]$PrimaryError,
        [AllowNull()][object]$Invocation,
        [string[]]$CleanupErrors = @()
    )
    $document = [ordered]@{
        protocol_version = $script:M5ProtocolVersion
        document_type = "dao_m5_worker_error"
        experiment_id = $script:M5ExperimentId
        sample_id = if ($null -eq $Invocation) { $null } else {
            [string]$Invocation.sample_id
        }
        phase_id = if ($null -eq $Invocation) { $null } else {
            [string]$Invocation.phase_id
        }
        process_id = [int]$PID
        timestamp_utc = Get-M5UtcTimestamp
        error = Get-M1ExceptionRecord -ErrorRecord $PrimaryError `
            -CleanupErrors $CleanupErrors
    }
    [Console]::Error.WriteLine(
        ($document | ConvertTo-Json -Depth 12 -Compress)
    )
}
