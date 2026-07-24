#![forbid(unsafe_code)]

use std::env;
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use jet3::{
    ByteCount, CandidateError, FileSource, JET3_PAGE_SIZE, JetFileKind, PageNumber,
    RawJet3Candidate, ReadLimits, ResourceBudget, ResourceLimits,
};

const DEFAULT_MAX_INPUT_BYTES: u64 = 256 * 1024 * 1024;
// `SRC-0004` documents the generic Jet signature window as 15 bytes.
const SIGNATURE_READ_BYTES: u64 = 15;
// `JET3_PAGE_SIZE` is the `SRC-0005`-traced library constant.
const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const FNV1A64_OFFSET_BASIS: u64 = 0xcbf2_9ce4_8422_2325;
const FNV1A64_PRIME: u64 = 0x0000_0100_0000_01b3;

const HELP: &str = "\
jet3-cli — bounded diagnostics for local database files

Usage:
  jet3-cli probe <file> [--max-input-bytes <bytes>]
  jet3-cli probe <file> [--max-input-bytes <bytes>] --scan-pages \
    --max-scan-bytes <bytes> --max-pages <count>
  jet3-cli --help
  jet3-cli --version

probe recognizes only the documented generic Jet signature and reports candidate
2 KiB geometry. --scan-pages rereads every complete candidate page with a fixed
2 KiB buffer after checking both explicit scan limits.

The JSON result does not identify a Jet generation or encryption state, validate
database structure, or establish application compatibility.
";

#[derive(Debug)]
struct ProbeOptions {
    path: PathBuf,
    max_input_bytes: u64,
    scan: Option<ScanLimits>,
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
}

fn main() -> ExitCode {
    let command = match parse_args(env::args_os().skip(1)) {
        Ok(command) => command,
        Err(code) => {
            write_stderr(&error_json(code));
            return ExitCode::from(2);
        }
    };

    match command {
        Command::Help => {
            write_stdout(HELP);
            ExitCode::SUCCESS
        }
        Command::Version => {
            write_stdout(&format!("jet3-cli {}\n", env!("CARGO_PKG_VERSION")));
            ExitCode::SUCCESS
        }
        Command::Probe(options) => match probe(&options) {
            Ok(json) => {
                write_stdout(&json);
                ExitCode::SUCCESS
            }
            Err(code) => {
                write_stderr(&error_json(code));
                ExitCode::from(1)
            }
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
    if first != "probe" {
        return Err("unknown_command");
    }

    let path = arguments.next().ok_or("missing_file")?;
    if path.to_string_lossy().starts_with('-') {
        return Err("missing_file");
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
                .checked_add(SIGNATURE_READ_BYTES)
                .ok_or("invalid_limit")?,
            limits.max_pages,
            limits
                .max_bytes
                .checked_add(limits.max_pages)
                .ok_or("invalid_limit")?,
        ),
        None => (SIGNATURE_READ_BYTES, 0, 0),
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
    let source =
        FileSource::open(&options.path, budget.read_budget()).map_err(|_| "open_failed")?;
    let mut candidate =
        RawJet3Candidate::inspect(source, &mut budget).map_err(|error| match error {
            CandidateError::Input(_) => "input_limit_exceeded",
            CandidateError::Signature(_) => "unrecognized_signature",
            CandidateError::Geometry(_) => "not_2k_aligned",
            _ => "candidate_inspection_failed",
        })?;
    let geometry = candidate.geometry();

    let signature_name = match candidate.signature_kind() {
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
            .ok_or("scan_count_overflow")?;
        let result = scan_pages(&mut candidate, &mut budget)?;
        if result.pages_read != geometry.page_count()
            || result.hashed_bytes != scan_bytes
            || budget.page_visits() != geometry.page_count()
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
         \"candidate_geometry\":{{\"page_size_bytes\":{PAGE_BYTES},\"page_count\":{},\
         \"source_length_bytes\":{}}},\"raw_scan\":{scan_json},\
         \"caveats\":[\"generic_signature_only\",\"jet_generation_not_identified\",\
         \"encryption_state_not_inspected\",\"database_structure_not_validated\",\
         \"compatibility_not_established\"]}}\n",
        geometry.page_count(),
        geometry.source_len().get()
    ))
}

fn scan_pages(
    candidate: &mut RawJet3Candidate<FileSource>,
    budget: &mut ResourceBudget,
) -> Result<ScanResult, &'static str> {
    let mut buffer = [0_u8; PAGE_BYTES];
    let mut checksum = FNV1A64_OFFSET_BASIS;
    let mut pages_read = 0_u64;
    let mut hashed_bytes = 0_u64;
    while pages_read < candidate.geometry().page_count() {
        candidate
            .read_raw_page(PageNumber::new(pages_read), &mut buffer, budget)
            .map_err(|_| "scan_read_failed")?;
        // One explicit work unit represents each byte processed by the
        // checksum loop, in addition to the raw reader's page-visit charge.
        budget
            .charge_work_units(PAGE_BYTES as u64)
            .map_err(|_| "scan_work_limit_exceeded")?;
        for byte in buffer {
            checksum ^= u64::from(byte);
            checksum = checksum.wrapping_mul(FNV1A64_PRIME);
        }
        hashed_bytes = hashed_bytes
            .checked_add(PAGE_BYTES as u64)
            .ok_or("scan_count_overflow")?;
        pages_read = pages_read.checked_add(1).ok_or("scan_count_overflow")?;
    }
    Ok(ScanResult {
        pages_read,
        hashed_bytes,
        checksum,
    })
}

fn error_json(code: &str) -> String {
    format!(
        "{{\"schema_version\":1,\"ok\":false,\"error\":\"{code}\",\
         \"compatibility_established\":false}}\n"
    )
}

fn write_stdout(value: &str) {
    let _result = io::stdout().lock().write_all(value.as_bytes());
}

fn write_stderr(value: &str) {
    let _result = io::stderr().lock().write_all(value.as_bytes());
}

#[cfg(test)]
mod tests {
    use super::{Command, parse_args};
    use std::ffi::OsString;

    fn args(values: &[&str]) -> impl Iterator<Item = OsString> {
        values
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>()
            .into_iter()
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
}
