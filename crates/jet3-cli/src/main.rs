#![forbid(unsafe_code)]

//! Bounded command-line diagnostics for supported Jet 3 databases.
//!
//! This binary reports the narrow version and protection state checked by
//! [`jet3::DatabaseReader`] from the exploratory `EXP-0056` discriminator. It
//! does not claim whole-database structural validity or application
//! compatibility.

use std::env;
use std::ffi::OsString;
use std::fs::File;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use jet3::{
    ByteCount, CandidateError, DatabaseFormatError, DatabaseOpenError, DatabaseReader, FileSource,
    JET3_PAGE_SIZE, JetFileKind, ReadLimits, ResourceBudget, ResourceLimits, TextCodePage,
};
use jet3_testkit::{
    ProtocolScenario, ScenarioExpectationError, SemanticSnapshotOptions,
    snapshot_database_with_receipt,
};
use jet3_testkit::{RejectedFormatErrorClass, SemanticOpenFailure, SemanticSnapshotArtifacts};

mod build_identity;
#[cfg(test)]
mod scenario_expectation_tests;
mod snapshot_bundle;
mod snapshot_input;

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
  jet3-cli snapshot <file> --scenario-id <id> --code-page <1251|1252> \
    --output-bundle <directory> [--max-input-bytes <bytes>]
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
    scenario: ProtocolScenario,
    code_page: TextCodePage,
    output_bundle: PathBuf,
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
        Command::Snapshot(options) => match snapshot(options) {
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
    let mut code_page = None;
    let mut output_bundle = None;
    let mut max_input_bytes = DEFAULT_MAX_INPUT_BYTES;
    while let Some(option) = arguments.next() {
        let target = match option.to_str() {
            Some("--scenario-id") => &mut scenario_id,
            Some("--code-page") => &mut code_page,
            Some("--output-bundle") => &mut output_bundle,
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
    let scenario = ProtocolScenario::resolve(&scenario_id).map_err(scenario_error_code)?;
    let code_page = code_page
        .and_then(|value| value.into_string().ok())
        .ok_or("invalid_code_page")?;
    let code_page = match code_page.as_str() {
        "1251" => TextCodePage::Windows1251,
        "1252" => TextCodePage::Windows1252,
        _ => return Err("invalid_code_page"),
    };
    let output_bundle = output_bundle
        .map(PathBuf::from)
        .ok_or("missing_output_bundle")?;
    Ok(Command::Snapshot(SnapshotOptions {
        path,
        scenario,
        code_page,
        output_bundle,
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

fn snapshot(options: SnapshotOptions) -> Result<(), &'static str> {
    let producer = build_identity::snapshot_producer()?;
    snapshot_with_producer(options, producer)
}

fn snapshot_with_producer(
    options: SnapshotOptions,
    producer: jet3_testkit::Producer,
) -> Result<(), &'static str> {
    let SnapshotOptions {
        path,
        scenario,
        code_page,
        output_bundle,
        max_input_bytes,
    } = options;
    // Open the caller-controlled pathname exactly once. The semantic reader
    // receives only the private bytes copied and hashed by this staging pass.
    // `SRC-0027` binds the staged database identity to FIPS SHA-256.
    let input = File::open(path).map_err(|_| "open_failed")?;
    let staged = snapshot_input::StagedInput::copy_from(input, max_input_bytes)?;
    let input_length = staged.length();
    let database_sha256 = staged.sha256();
    let max_total_read = input_length.checked_mul(32).ok_or("invalid_limit")?;
    let read_limits = ReadLimits::new(
        ByteCount::new(input_length),
        JET3_PAGE_SIZE,
        ByteCount::new(max_total_read),
    );
    let mut budget = ResourceBudget::new(ResourceLimits::new(read_limits));
    let source = staged.traversal_source(budget.read_budget())?;
    let artifacts = match DatabaseReader::from_source(source, &mut budget) {
        Ok(mut database) => {
            scenario.validate_success().map_err(scenario_error_code)?;
            snapshot_database_with_receipt(
                &mut database,
                &SemanticSnapshotOptions {
                    scenario_id: scenario.scenario_id().clone(),
                    producer,
                    database_sha256,
                    code_page,
                },
                &mut budget,
            )
            .map_err(|_| "snapshot_failed")?
        }
        Err(error) => {
            // `EXP-0065` closes normalization to the three format variants.
            let error_class = RejectedFormatErrorClass::from_open_error(&error)
                .ok_or_else(|| open_error_code(error))?;
            scenario
                .validate_opening_failure(error_class)
                .map_err(scenario_error_code)?;
            SemanticSnapshotArtifacts::opening_failure(
                SemanticOpenFailure {
                    scenario_id: scenario.scenario_id().clone(),
                    producer,
                    database_sha256,
                    error_class,
                },
                &mut budget,
            )
            .map_err(|_| "snapshot_failed")?
        }
    };
    let (snapshot, receipt) = artifacts
        .to_canonical_json(&mut budget)
        .map_err(|_| "snapshot_failed")?;
    staged.close()?;
    snapshot_bundle::publish(&output_bundle, &snapshot, &receipt).map_err(publication_error_code)
}

fn scenario_error_code(error: ScenarioExpectationError) -> &'static str {
    match error {
        ScenarioExpectationError::UnknownScenarioId => "unknown_scenario_id",
        ScenarioExpectationError::InvalidInventory => "invalid_scenario_inventory",
        ScenarioExpectationError::ExpectedSuccess => "scenario_expected_success",
        ScenarioExpectationError::ExpectedOpeningFailure { .. } => {
            "scenario_expected_opening_failure"
        }
        ScenarioExpectationError::ErrorClassMismatch { .. } => "scenario_error_class_mismatch",
    }
}

fn publication_error_code(error: snapshot_bundle::PublishError) -> &'static str {
    use snapshot_bundle::PublishError;

    match error {
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::InvalidDestination => "invalid_output_bundle",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::DestinationExists => "output_bundle_exists",
        #[cfg(not(any(target_os = "linux", target_vendor = "apple")))]
        PublishError::UnsupportedPlatform => "atomic_bundle_unsupported",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::StageFailed => "output_bundle_stage_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::SnapshotFailed => "snapshot_output_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::ReceiptFailed => "coverage_output_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::StageSyncFailed => "output_bundle_stage_sync_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::RenameFailed => "output_bundle_publish_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::RenameCollision => "output_bundle_exists",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::CleanupFailed => "output_bundle_cleanup_failed",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::CleanupUncertain => "output_bundle_cleanup_uncertain",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::PublishedCleanupUncertain => "output_bundle_published_cleanup_uncertain",
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        PublishError::PublishedDurabilityUncertain => {
            "output_bundle_published_durability_uncertain"
        }
    }
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
    use super::{Command, SnapshotOptions, parse_args, snapshot_with_producer, write_output};
    use std::ffi::OsString;
    use std::fs;
    use std::io::{self, Write};

    use jet3::TextCodePage;
    #[cfg(any(target_os = "linux", target_vendor = "apple"))]
    use jet3_testkit::sha256_hex;
    use jet3_testkit::{Producer, ProducerKind, ProtocolScenario};

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
    fn snapshot_command_uses_one_fixed_name_output_bundle() {
        let command = parse_args(args(&[
            "snapshot",
            "input.mdb",
            "--scenario-id",
            "DAO-READ-ROWS-SINGLE",
            "--code-page",
            "1252",
            "--output-bundle",
            "bundle",
        ]));
        assert!(matches!(command, Ok(Command::Snapshot(_))));
    }

    #[test]
    fn caller_controlled_revision_and_independent_outputs_are_rejected() {
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
                "--output-bundle",
                "bundle",
            ])),
            Err("unknown_option")
        ));
        assert!(matches!(
            parse_args(args(&[
                "snapshot",
                "input.mdb",
                "--scenario-id",
                "DAO-READ-ROWS-SINGLE",
                "--code-page",
                "1252",
                "--snapshot-output",
                "snapshot.json",
                "--coverage-output",
                "receipt.json",
            ])),
            Err("unknown_option")
        ));
    }

    #[cfg(any(target_os = "linux", target_vendor = "apple"))]
    #[test]
    fn expected_open_rejections_publish_canonical_validated_bundles()
    -> Result<(), Box<dyn std::error::Error>> {
        let producer = Producer::new(
            ProducerKind::Rust,
            "0123456789abcdef0123456789abcdef01234567",
        )?;
        for (case, scenario, mutate, error_class) in [
            (
                "jet4",
                "DAO-READ-OPEN-REJECT-JET4",
                (0x14, 0x01),
                "unsupported_version",
            ),
            (
                "encrypted",
                "DAO-READ-OPEN-REJECT-ENCRYPTED",
                (0x41, 0x00),
                "encrypted_database",
            ),
            (
                "password",
                "DAO-READ-OPEN-REJECT-PASSWORD",
                (0x42, 0x00),
                "password_protected",
            ),
        ] {
            let directory = tempfile::tempdir()?;
            let input = directory.path().join(format!("{case}.mdb"));
            let bundle = directory.path().join("bundle");
            let mut bytes = supported_header();
            bytes[mutate.0] = mutate.1;
            let expected_sha256 = sha256_hex(&bytes)?;
            fs::write(&input, &bytes)?;
            snapshot_with_producer(
                SnapshotOptions {
                    path: input,
                    scenario: ProtocolScenario::resolve(scenario)?,
                    code_page: TextCodePage::Windows1252,
                    output_bundle: bundle.clone(),
                    max_input_bytes: bytes.len() as u64,
                },
                producer.clone(),
            )?;
            for artifact in ["snapshot.json", "coverage-receipt.json"] {
                let contents = fs::read_to_string(bundle.join(artifact))?;
                assert!(contents.contains("\"outcome\":\"opening_failure\""));
                assert!(contents.contains(&format!("\"error_class\":\"{error_class}\"")));
                assert!(contents.contains(&expected_sha256));
            }
            let receipt = fs::read_to_string(bundle.join("coverage-receipt.json"))?;
            assert!(receipt.contains("\"allocated_set_sha256\":null"));
        }
        Ok(())
    }

    #[test]
    fn non_format_open_failure_remains_an_error_without_artifacts()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let input = directory.path().join("unknown.mdb");
        let bundle = directory.path().join("bundle");
        let mut bytes = supported_header();
        bytes[4..19].copy_from_slice(b"Not a Jet file!");
        fs::write(&input, &bytes)?;
        let error = snapshot_with_producer(
            SnapshotOptions {
                path: input,
                scenario: ProtocolScenario::resolve("DAO-READ-OPEN-REJECT-JET4")?,
                code_page: TextCodePage::Windows1252,
                output_bundle: bundle.clone(),
                max_input_bytes: bytes.len() as u64,
            },
            Producer::new(
                ProducerKind::Rust,
                "0123456789abcdef0123456789abcdef01234567",
            )?,
        );
        assert_eq!(error, Err("unrecognized_signature"));
        assert!(!bundle.exists());
        Ok(())
    }

    fn supported_header() -> Vec<u8> {
        let mut bytes = vec![0_u8; 2_048];
        bytes[4..19].copy_from_slice(b"Standard Jet DB");
        bytes[0x41] = 0x4e;
        bytes[0x42..0x50].copy_from_slice(&[
            0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
        ]);
        bytes
    }
}
