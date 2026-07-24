#![forbid(unsafe_code)]

use std::env;
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use jet3::{
    ByteCount, FileSource, JET3_PAGE_SIZE, JetFileKind, PageGeometry, PageNumber, ReadAt,
    ReadBudget, ReadLimits, jet3_page_geometry, read_jet_signature,
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
    let max_total_read = match options.scan {
        Some(limits) => limits
            .max_bytes
            .checked_add(SIGNATURE_READ_BYTES)
            .ok_or("invalid_limit")?,
        None => SIGNATURE_READ_BYTES,
    };
    let read_limits = ReadLimits::new(
        ByteCount::new(options.max_input_bytes),
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new(max_total_read),
    );
    let mut budget = ReadBudget::new(read_limits);
    let mut source = FileSource::open(&options.path, &budget).map_err(|_| "open_failed")?;
    let signature =
        read_jet_signature(&mut source, &mut budget).map_err(|_| "unrecognized_signature")?;
    let geometry = jet3_page_geometry(&source).map_err(|_| "not_2k_aligned")?;

    let signature_name = match signature {
        JetFileKind::Standard => "standard",
        JetFileKind::System => "system",
        JetFileKind::Temporary => "temporary",
        _ => return Err("unsupported_signature_kind"),
    };

    let scan_json = if let Some(limits) = options.scan {
        if source.len().get() > limits.max_bytes {
            return Err("scan_byte_limit_exceeded");
        }
        if geometry.page_count() > limits.max_pages {
            return Err("scan_page_limit_exceeded");
        }
        let (pages_read, checksum) = scan_pages(&mut source, &mut budget, geometry)?;
        format!(
            "{{\"checksum_algorithm\":\"fnv1a64\",\"checksum_hex\":\"{checksum:016x}\",\
             \"pages_read\":{pages_read},\"bytes_read\":{}}}",
            source.len().get()
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
        source.len().get()
    ))
}

fn scan_pages(
    source: &mut FileSource,
    budget: &mut ReadBudget,
    geometry: PageGeometry,
) -> Result<(u64, u64), &'static str> {
    let mut buffer = [0_u8; PAGE_BYTES];
    let mut checksum = FNV1A64_OFFSET_BASIS;
    let mut pages_read = 0_u64;
    while pages_read < geometry.page_count() {
        let (offset, count) = geometry
            .page_byte_range(PageNumber::new(pages_read))
            .map_err(|_| "scan_range_failed")?;
        if count.get() != PAGE_BYTES as u64 {
            return Err("scan_range_failed");
        }
        source
            .read_exact_at(offset, &mut buffer, budget)
            .map_err(|_| "scan_read_failed")?;
        for byte in buffer {
            checksum ^= u64::from(byte);
            checksum = checksum.wrapping_mul(FNV1A64_PRIME);
        }
        pages_read = pages_read.checked_add(1).ok_or("scan_count_overflow")?;
    }
    Ok((pages_read, checksum))
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
