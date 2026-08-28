#![forbid(unsafe_code)]

//! Bounded command-line diagnostics for supported Jet 3 databases.
//!
//! This binary reports the narrow version and protection state checked by
//! [`jet3::DatabaseReader`] from the exploratory `EXP-0056` discriminator. It
//! does not claim whole-database structural validity or application
//! compatibility.

use std::env;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use jet3::{
    ByteCount, CandidateError, DatabaseFormatError, DatabaseOpenError, DatabaseReader, FileSource,
    JET3_PAGE_SIZE, JetFileKind, ReadLimits, ResourceBudget, ResourceLimits, TextCodePage,
};
use jet3_testkit::{
    Producer, ProducerKind, ScenarioId, SemanticSnapshotOptions, Sha256, Sha256Hasher, hex_digest,
    snapshot_database_with_receipt,
};

const DEFAULT_MAX_INPUT_BYTES: u64 = 256 * 1024 * 1024;
// `SRC-0004` documents the generic Jet signature window as 15 bytes.
const SIGNATURE_READ_BYTES: u64 = 15;
// `JET3_PAGE_SIZE` is the `SRC-0005`-traced library constant.
const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const OPENING_READ_BYTES: u64 = SIGNATURE_READ_BYTES + PAGE_BYTES as u64;
const OPENING_PAGE_VISITS: u64 = 1;
const OPENING_WORK_UNITS: u64 = 1;
const FNV1A64_OFFSET_BASIS: u64 = 0xcbf2_9ce4_8422_2325;
const FNV1A64_PRIME: u64 = 0x0000_0100_0000_01b3;
const IO_ERROR_EXIT: u8 = 74;

const HELP: &str = "\
jet3-cli — bounded diagnostics for local database files

Usage:
  jet3-cli probe <file> [--max-input-bytes <bytes>]
  jet3-cli probe <file> [--max-input-bytes <bytes>] --scan-pages \
    --max-scan-bytes <bytes> --max-pages <count>
  jet3-cli snapshot <file> --scenario-id <id> --source-revision <revision> \
    --code-page <1251|1252> --snapshot-output <path> \
    --coverage-output <path> [--max-input-bytes <bytes>]
  jet3-cli --help
  jet3-cli --version

probe accepts only the supported Jet 3, unencrypted, no-password header state
and reports 2 KiB geometry. --scan-pages rereads every complete page with a
fixed 2 KiB buffer after checking both explicit scan limits.

The JSON result does not validate whole-database structure or establish
application compatibility.
";

#[derive(Debug)]
struct ProbeOptions {
    path: PathBuf,
    max_input_bytes: u64,
    scan: Option<ScanLimits>,
}

#[derive(Debug)]
struct SnapshotOptions {
    path: PathBuf,
    scenario_id: ScenarioId,
    source_revision: String,
    code_page: TextCodePage,
    snapshot_output: PathBuf,
    coverage_output: PathBuf,
    max_input_bytes: u64,
}

#[derive(Debug, Clone, Copy)]
struct ScanLimits {
    max_bytes: u64,
    max_pages: u64,
}

#[derive(Debug, Clone, Copy)]
struct ScanResult {
    pages_read: u64,
    hashed_bytes: u64,
    checksum: u64,
}

#[derive(Debug)]
enum Command {
    Help,
    Version,
    Probe(ProbeOptions),
    Snapshot(SnapshotOptions),
}

fn main() -> ExitCode {
    let command = match parse_args(env::args_os().skip(1)) {
        Ok(command) => command,
        Err(code) => {
            return exit_after_write(write_stderr(&error_json(code)), 2);
        }
    };

    match command {
        Command::Help => exit_after_write(write_stdout(HELP), 0),
        Command::Version => exit_after_write(
            write_stdout(&format!("jet3-cli {}\n", env!("CARGO_PKG_VERSION"))),
            0,
        ),
        Command::Probe(options) => match probe(&options) {
            Ok(json) => exit_after_write(write_stdout(&json), 0),
            Err(code) => exit_after_write(write_stderr(&error_json(code)), 1),
        },
        Command::Snapshot(options) => match snapshot(&options) {
            Ok(()) => ExitCode::SUCCESS,
            Err(code) => exit_after_write(write_stderr(&error_json(code)), 1),
        },
    }
}

fn parse_args(arguments: impl Iterator<Item = OsString>) -> Result<Command, &'static str> {
    let mut arguments = arguments.peekable();
    let Some(first) = arguments.next() else {
        return Ok(Command::Help);
    };
    if first == "--help" || first == "-h" {
        return no_more(arguments, Command::Help);
    }
    if first == "--version" || first == "-V" {
        return no_more(arguments, Command::Version);
    }
    let path = arguments.next().ok_or("missing_file")?;
    if path.to_string_lossy().starts_with('-') {
        return Err("missing_file");
    }

    if first == "snapshot" {
        return parse_snapshot(PathBuf::from(path), arguments);
    }
    if first != "probe" {
        return Err("unknown_command");
    }
    let mut max_input_bytes = DEFAULT_MAX_INPUT_BYTES;
    let mut scan_pages = false;
    let mut max_scan_bytes = None;
    let mut max_pages = None;
    while let Some(option) = arguments.next() {
        if option == "--scan-pages" {
            if scan_pages {
                return Err("duplicate_option");
            }
            scan_pages = true;
        } else if option == "--max-input-bytes" {
            max_input_bytes = parse_u64(arguments.next(), "missing_option_value", "invalid_limit")?;
        } else if option == "--max-scan-bytes" {
            max_scan_bytes = Some(parse_u64(
                arguments.next(),
                "missing_option_value",
                "invalid_limit",
            )?);
        } else if option == "--max-pages" {
            max_pages = Some(parse_u64(
                arguments.next(),
                "missing_option_value",
                "invalid_limit",
            )?);
        } else {
            return Err("unknown_option");
        }
    }

    let scan = match (scan_pages, max_scan_bytes, max_pages) {
        (false, None, None) => None,
        (true, Some(max_bytes), Some(max_pages)) => Some(ScanLimits {
            max_bytes,
            max_pages,
        }),
        (true, _, _) => return Err("scan_limits_required"),
        (false, Some(_), _) | (false, _, Some(_)) => return Err("scan_not_requested"),
    };
    Ok(Command::Probe(ProbeOptions {
        path: PathBuf::from(path),
        max_input_bytes,
        scan,
    }))
}

fn parse_snapshot(
    path: PathBuf,
    mut arguments: impl Iterator<Item = OsString>,
) -> Result<Command, &'static str> {
    let mut scenario_id = None;
    let mut source_revision = None;
    let mut code_page = None;
    let mut snapshot_output = None;
    let mut coverage_output = None;
    let mut max_input_bytes = DEFAULT_MAX_INPUT_BYTES;
    while let Some(option) = arguments.next() {
        let target = match option.to_str() {
            Some("--scenario-id") => &mut scenario_id,
            Some("--source-revision") => &mut source_revision,
            Some("--code-page") => &mut code_page,
            Some("--snapshot-output") => &mut snapshot_output,
            Some("--coverage-output") => &mut coverage_output,
            Some("--max-input-bytes") => {
                max_input_bytes =
                    parse_u64(arguments.next(), "missing_option_value", "invalid_limit")?;
                continue;
            }
            _ => return Err("unknown_option"),
        };
        if target.is_some() {
            return Err("duplicate_option");
        }
        *target = Some(arguments.next().ok_or("missing_option_value")?);
    }
    let scenario_id = scenario_id
        .and_then(|value| value.into_string().ok())
        .ok_or("invalid_scenario_id")?;
    let source_revision = source_revision
        .and_then(|value| value.into_string().ok())
        .filter(|value| !value.is_empty())
        .ok_or("invalid_source_revision")?;
    let code_page = code_page
        .and_then(|value| value.into_string().ok())
        .ok_or("invalid_code_page")?;
    let code_page = match code_page.as_str() {
        "1251" => TextCodePage::Windows1251,
        "1252" => TextCodePage::Windows1252,
        _ => return Err("invalid_code_page"),
    };
    let snapshot_output = snapshot_output
        .map(PathBuf::from)
        .ok_or("missing_snapshot_output")?;
    let coverage_output = coverage_output
        .map(PathBuf::from)
        .ok_or("missing_coverage_output")?;
    if snapshot_output == coverage_output || snapshot_output == path || coverage_output == path {
        return Err("output_path_conflict");
    }
    Ok(Command::Snapshot(SnapshotOptions {
        path,
        scenario_id: ScenarioId::new(scenario_id).map_err(|_| "invalid_scenario_id")?,
        source_revision,
        code_page,
        snapshot_output,
        coverage_output,
        max_input_bytes,
    }))
}

fn no_more(
    mut arguments: impl Iterator<Item = OsString>,
    command: Command,
) -> Result<Command, &'static str> {
    if arguments.next().is_some() {
        Err("unexpected_argument")
    } else {
        Ok(command)
    }
}

fn parse_u64(
    value: Option<OsString>,
    missing: &'static str,
    invalid: &'static str,
) -> Result<u64, &'static str> {
    let value = value.ok_or(missing)?;
    value
        .to_str()
        .ok_or(invalid)?
        .parse::<u64>()
        .map_err(|_| invalid)
}

fn probe(options: &ProbeOptions) -> Result<String, &'static str> {
    let (max_total_read, max_page_visits, max_total_work) = match options.scan {
        Some(limits) => (
            limits
                .max_bytes
                .checked_add(OPENING_READ_BYTES)
                .ok_or("invalid_limit")?,
            limits
                .max_pages
                .checked_add(OPENING_PAGE_VISITS)
                .ok_or("invalid_limit")?,
            limits
                .max_bytes
                .checked_add(limits.max_pages)
                .and_then(|work| work.checked_add(OPENING_WORK_UNITS))
                .ok_or("invalid_limit")?,
        ),
        None => (OPENING_READ_BYTES, OPENING_PAGE_VISITS, OPENING_WORK_UNITS),
    };
    let read_limits = ReadLimits::new(
        ByteCount::new(options.max_input_bytes),
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new(max_total_read),
    );
    let resource_limits = ResourceLimits::new(read_limits)
        .with_max_page_visits(max_page_visits)
        .with_max_total_work_units(max_total_work);
    let mut budget = ResourceBudget::new(resource_limits);
    let mut database = DatabaseReader::open(&options.path, &mut budget).map_err(open_error_code)?;
    let geometry = database.geometry();

    let signature_name = match database.signature_kind() {
        JetFileKind::Standard => "standard",
        JetFileKind::System => "system",
        JetFileKind::Temporary => "temporary",
        _ => return Err("unsupported_signature_kind"),
    };

    let scan_json = if let Some(limits) = options.scan {
        let scan_bytes = geometry
            .page_count()
            .checked_mul(PAGE_BYTES as u64)
            .ok_or("scan_count_overflow")?;
        if scan_bytes > limits.max_bytes {
            return Err("scan_byte_limit_exceeded");
        }
        if geometry.page_count() > limits.max_pages {
            return Err("scan_page_limit_exceeded");
        }
        let expected_work = scan_bytes
            .checked_add(geometry.page_count())
            .and_then(|work| work.checked_add(OPENING_WORK_UNITS))
            .ok_or("scan_count_overflow")?;
        let result = scan_pages(&mut database, &mut budget)?;
        if result.pages_read != geometry.page_count()
            || result.hashed_bytes != scan_bytes
            || budget.page_visits() != geometry.page_count() + OPENING_PAGE_VISITS
            || budget.total_work_units() != expected_work
        {
            return Err("scan_accounting_failed");
        }
        format!(
            "{{\"checksum_algorithm\":\"fnv1a64\",\"checksum_hex\":\"{:016x}\",\
             \"pages_read\":{},\"bytes_read\":{},\"hash_work_units\":{},\
             \"total_work_units\":{}}}",
            result.checksum,
            result.pages_read,
            result.hashed_bytes,
            result.hashed_bytes,
            budget.total_work_units()
        )
    } else {
        "null".to_owned()
    };

    Ok(format!(
        "{{\"schema_version\":1,\"ok\":true,\"signature_kind\":\"{signature_name}\",\
         \"database_version\":\"jet3\",\"protection\":\"unencrypted_without_password\",\
         \"candidate_geometry\":{{\"page_size_bytes\":{PAGE_BYTES},\"page_count\":{},\
         \"source_length_bytes\":{}}},\"raw_scan\":{scan_json},\
         \"caveats\":[\"opening_header_only\",\"database_structure_not_validated\",\
         \"compatibility_not_established\"]}}\n",
        geometry.page_count(),
        geometry.source_len().get()
    ))
}

fn snapshot(options: &SnapshotOptions) -> Result<(), &'static str> {
    let input = File::open(&options.path).map_err(|_| "open_failed")?;
    let input_length = input.metadata().map_err(|_| "metadata_failed")?.len();
    if input_length > options.max_input_bytes {
        return Err("input_limit_exceeded");
    }
    let hash_input = input.try_clone().map_err(|_| "open_failed")?;
    let database_sha256 = hash_file(hash_input, input_length)?;
    let max_total_read = options
        .max_input_bytes
        .checked_mul(32)
        .ok_or("invalid_limit")?;
    let read_limits = ReadLimits::new(
        ByteCount::new(options.max_input_bytes),
        JET3_PAGE_SIZE,
        ByteCount::new(max_total_read),
    );
    let mut budget = ResourceBudget::new(ResourceLimits::new(read_limits));
    let source = FileSource::from_file(input, budget.read_budget()).map_err(|_| "open_failed")?;
    let mut database = DatabaseReader::from_source(source, &mut budget).map_err(open_error_code)?;
    let producer = Producer::new(ProducerKind::Rust, options.source_revision.clone())
        .map_err(|_| "invalid_source_revision")?;
    let artifacts = snapshot_database_with_receipt(
        &mut database,
        &SemanticSnapshotOptions {
            scenario_id: options.scenario_id.clone(),
            producer,
            database_sha256,
            code_page: options.code_page,
        },
        &mut budget,
    )
    .map_err(|_| "snapshot_failed")?;
    let snapshot = artifacts
        .snapshot
        .to_canonical_json()
        .map_err(|_| "snapshot_failed")?;
    let receipt = artifacts
        .coverage_receipt
        .to_canonical_json()
        .map_err(|_| "snapshot_failed")?;
    fs::write(&options.snapshot_output, snapshot).map_err(|_| "snapshot_output_failed")?;
    fs::write(&options.coverage_output, receipt).map_err(|_| "coverage_output_failed")?;
    Ok(())
}

fn hash_file(mut file: File, expected_length: u64) -> Result<Sha256, &'static str> {
    // `SRC-0027` binds the protocol database identity to FIPS SHA-256.
    let mut hasher = Sha256Hasher::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut total = 0_u64;
    loop {
        let count = file.read(&mut buffer).map_err(|_| "hash_read_failed")?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(u64::try_from(count).map_err(|_| "hash_read_failed")?)
            .ok_or("hash_read_failed")?;
        if total > expected_length {
            return Err("input_changed");
        }
        hasher
            .update(&buffer[..count])
            .map_err(|_| "hash_read_failed")?;
    }
    if total != expected_length {
        return Err("input_changed");
    }
    Sha256::new(hex_digest(
        hasher.finalize().map_err(|_| "hash_read_failed")?,
    ))
    .map_err(|_| "hash_failed")
}

fn scan_pages(
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
) -> Result<ScanResult, &'static str> {
    let mut checksum = FNV1A64_OFFSET_BASIS;
    let mut hashed_bytes = 0_u64;
    let mut raw_pages = database.raw_pages();
    while let Some(page) = raw_pages
        .next_page(budget)
        .map_err(|_| "scan_read_failed")?
    {
        // One explicit work unit represents each byte processed by the
        // checksum loop, in addition to the raw reader's page-visit charge.
        budget
            .charge_work_units(PAGE_BYTES as u64)
            .map_err(|_| "scan_work_limit_exceeded")?;
        for byte in page.bytes() {
            checksum ^= u64::from(*byte);
            checksum = checksum.wrapping_mul(FNV1A64_PRIME);
        }
        hashed_bytes = hashed_bytes
            .checked_add(PAGE_BYTES as u64)
            .ok_or("scan_count_overflow")?;
    }
    let pages_read = raw_pages.pages_read();
    Ok(ScanResult {
        pages_read,
        hashed_bytes,
        checksum,
    })
}

fn open_error_code(error: DatabaseOpenError) -> &'static str {
    match error {
        DatabaseOpenError::Source(_) => "open_failed",
        DatabaseOpenError::Candidate(CandidateError::Input(_)) => "input_limit_exceeded",
        DatabaseOpenError::Candidate(CandidateError::Signature(_)) => "unrecognized_signature",
        DatabaseOpenError::Candidate(CandidateError::Geometry(_)) => "not_2k_aligned",
        DatabaseOpenError::Format(DatabaseFormatError::UnsupportedVersion { .. }) => {
            "unsupported_database_version"
        }
        DatabaseOpenError::Format(DatabaseFormatError::EncryptedOrUnsupported { .. }) => {
            "encrypted_or_unsupported_database"
        }
        DatabaseOpenError::Format(DatabaseFormatError::PasswordedOrUnsupported) => {
            "passworded_or_unsupported_database"
        }
        _ => "database_open_failed",
    }
}

fn error_json(code: &str) -> String {
    format!(
        "{{\"schema_version\":1,\"ok\":false,\"error\":\"{code}\",\
         \"compatibility_established\":false}}\n"
    )
}

fn write_output(mut output: impl Write, value: &str) -> io::Result<()> {
    output.write_all(value.as_bytes())
}

fn write_stdout(value: &str) -> io::Result<()> {
    write_output(io::stdout().lock(), value)
}

fn write_stderr(value: &str) -> io::Result<()> {
    write_output(io::stderr().lock(), value)
}

fn exit_after_write(result: io::Result<()>, success_or_command_error: u8) -> ExitCode {
    if result.is_ok() {
        ExitCode::from(success_or_command_error)
    } else {
        ExitCode::from(IO_ERROR_EXIT)
    }
}

#[cfg(test)]
mod tests {
    use super::{Command, parse_args, write_output};
    use std::ffi::OsString;
    use std::io::{self, Write};

    struct RejectWrites;

    impl Write for RejectWrites {
        fn write(&mut self, _buffer: &[u8]) -> io::Result<usize> {
            Err(io::Error::other("injected output failure"))
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn args(values: &[&str]) -> impl Iterator<Item = OsString> {
        values
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>()
            .into_iter()
    }

    #[test]
    fn output_failures_are_not_discarded() {
        assert!(write_output(RejectWrites, "result").is_err());
    }

    #[test]
    fn scan_requires_both_explicit_limits() {
        assert!(matches!(
            parse_args(args(&["probe", "file.mdb", "--scan-pages"])),
            Err("scan_limits_required")
        ));
        assert!(matches!(
            parse_args(args(&[
                "probe",
                "file.mdb",
                "--scan-pages",
                "--max-scan-bytes",
                "2048"
            ])),
            Err("scan_limits_required")
        ));
    }

    #[test]
    fn scan_limits_are_rejected_without_scan_request() {
        assert!(matches!(
            parse_args(args(&["probe", "file.mdb", "--max-pages", "1"])),
            Err("scan_not_requested")
        ));
    }

    #[test]
    fn bounded_scan_arguments_are_parsed() {
        let command = parse_args(args(&[
            "probe",
            "file.mdb",
            "--max-input-bytes",
            "4096",
            "--scan-pages",
            "--max-scan-bytes",
            "4096",
            "--max-pages",
            "2",
        ]));
        assert!(matches!(command, Ok(Command::Probe(_))));
    }

    #[test]
    fn snapshot_command_requires_versioned_identity_and_distinct_outputs() {
        let command = parse_args(args(&[
            "snapshot",
            "input.mdb",
            "--scenario-id",
            "DAO-READ-ROWS-SINGLE",
            "--source-revision",
            "abc123",
            "--code-page",
            "1252",
            "--snapshot-output",
            "snapshot.json",
            "--coverage-output",
            "coverage-receipt.json",
        ]));
        assert!(matches!(command, Ok(Command::Snapshot(_))));
        assert!(matches!(
            parse_args(args(&[
                "snapshot",
                "input.mdb",
                "--scenario-id",
                "DAO-READ-ROWS-SINGLE",
                "--source-revision",
                "abc123",
                "--code-page",
                "1252",
                "--snapshot-output",
                "same.json",
                "--coverage-output",
                "same.json",
            ])),
            Err("output_path_conflict")
        ));
    }
}
