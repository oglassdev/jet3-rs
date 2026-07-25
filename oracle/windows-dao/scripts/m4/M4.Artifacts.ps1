Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-M4SnapshotDocument {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$PhaseSnapshot
    )

    return [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_empty_schema_version_snapshot"
        experiment_id = $script:M4ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        captured_while_database_open = $true
        captured_at_utc = [string]$PhaseSnapshot.captured_at_utc
        dao_version = [string]$PhaseSnapshot.dao_version
        empty_user_schema = $true
        user_table_count = [int]$PhaseSnapshot.user_table_count
    }
}

function New-M4OperationLogDocument {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)]
        [Collections.ArrayList]$Operations
    )

    return [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_operation_log"
        experiment_id = $script:M4ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        worker_run_id = [string]$Invocation.worker_run_id
        entries = @($Operations)
        final_status = "pass"
    }
}

function New-M4WorkerResultDocument {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Invocation,
        [Parameter(Mandatory = $true)][pscustomobject]$AcceptedProvider,
        [Parameter(Mandatory = $true)][string]$ProviderHash,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$InvocationSha256,
        [Parameter(Mandatory = $true)][string]$LogLocator,
        [Parameter(Mandatory = $true)][byte[]]$LogBytes,
        [Parameter(Mandatory = $true)][string]$SnapshotLocator,
        [Parameter(Mandatory = $true)][byte[]]$SnapshotBytes,
        [AllowNull()][object]$PreComBinding,
        [Parameter(Mandatory = $true)][pscustomobject]$PostClose,
        [Parameter(Mandatory = $true)][string]$PrefixLocator
    )

    return [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_worker_result"
        experiment_id = $script:M4ExperimentId
        sample_id = [string]$Invocation.sample_id
        phase_id = [string]$Invocation.phase_id
        phase_ordinal = [int]$Invocation.phase_ordinal
        worker_run_id = [string]$Invocation.worker_run_id
        worker_ordinal = [int]$Invocation.worker_ordinal
        nonce = [string]$Invocation.nonce
        process_id = [int]$PID
        architecture = "x86"
        provider = [ordered]@{
            powershell_version = $PSVersionTable.PSVersion.ToString()
            prog_id = [string]$AcceptedProvider.prog_id
            clsid = ([string]$AcceptedProvider.clsid).ToUpperInvariant()
            server_sha256 = $ProviderHash
        }
        started_at_utc = $StartedAt
        finished_at_utc = Get-M4UtcTimestamp
        bindings_verified_before_com = $true
        invocation_sha256 = $InvocationSha256
        operation_log = [ordered]@{
            path = $LogLocator
            sha256 = Get-M4BytesSha256 -Bytes $LogBytes
        }
        snapshot = [ordered]@{
            path = $SnapshotLocator
            sha256 = Get-M4BytesSha256 -Bytes $SnapshotBytes
        }
        pre_com_file_binding = $PreComBinding
        post_close_file_observations = [ordered]@{
            database_path = [string]$Invocation.database_path
            database_bytes = [long]$PostClose.bytes
            database_sha256 = [string]$PostClose.sha256
            prefix = [ordered]@{
                path = $PrefixLocator
                sha256 = [string]$PostClose.prefix_sha256
            }
            prefix_bytes = 2048
            lock_file_absent_after_close = $true
        }
        execution_status = "pass"
    }
}

function Write-M4FailureTombstone {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$ErrorDocument
    )

    $bytes = ConvertTo-M4JsonBytes -Document $ErrorDocument `
        -MaximumBytes 16KB
    Write-M4CreateNewBytes -Path $Path -Bytes $bytes -MaximumBytes 16KB
}

function Complete-M4WorkerFailure {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.ErrorRecord]$PrimaryError,
        [AllowNull()][object]$Invocation,
        [string[]]$CleanupErrors = @(),
        [AllowNull()][string]$FailureTombstonePath,
        [bool]$ResultCommitted
    )

    $structuredError = Get-M1ExceptionRecord `
        -ErrorRecord $PrimaryError -CleanupErrors $CleanupErrors
    $errorDocument = [ordered]@{
        protocol_version = "1.0.0"
        document_type = "dao_m4_worker_error"
        experiment_id = $script:M4ExperimentId
        phase_id = if ($null -eq $Invocation) {
            $null
        }
        else {
            [string]$Invocation.phase_id
        }
        process_id = [int]$PID
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        error = $structuredError
    }
    if (
        -not $ResultCommitted -and
        -not [string]::IsNullOrWhiteSpace($FailureTombstonePath)
    ) {
        try {
            Write-M4FailureTombstone -Path $FailureTombstonePath `
                -ErrorDocument $errorDocument
        }
        catch {
            # Structured stderr remains available; no existing path is replaced.
        }
    }
    [Console]::Error.WriteLine(
        ($errorDocument | ConvertTo-Json -Depth 8 -Compress)
    )
}
