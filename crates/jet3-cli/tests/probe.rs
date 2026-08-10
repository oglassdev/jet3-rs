#![forbid(unsafe_code)]

use std::fs;
use std::process::{Command, Output};

fn run(arguments: &[&str]) -> Result<Output, std::io::Error> {
    Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .args(arguments)
        .output()
}

fn fixture(page_count: usize) -> Result<tempfile::NamedTempFile, std::io::Error> {
    let file = tempfile::NamedTempFile::new()?;
    let mut bytes = vec![0_u8; page_count * 2_048];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    for (index, byte) in bytes.iter_mut().enumerate().skip(19) {
        *byte = (index % 251) as u8;
    }
    fs::write(file.path(), bytes)?;
    Ok(file)
}

#[test]
fn probe_reports_narrow_candidate_geometry_as_json() -> Result<(), Box<dyn std::error::Error>> {
    let file = fixture(2)?;
    let path = file.path().to_str().ok_or("temporary path is not UTF-8")?;
    let output = run(&["probe", path])?;
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout)?;
    assert_eq!(
        stdout,
        "{\"schema_version\":1,\"ok\":true,\"signature_kind\":\"standard\",\
\"candidate_geometry\":{\"page_size_bytes\":2048,\"page_count\":2,\
\"source_length_bytes\":4096},\"raw_scan\":null,\
\"caveats\":[\"generic_signature_only\",\"jet_generation_not_identified\",\
\"encryption_state_not_inspected\",\"database_structure_not_validated\",\
\"compatibility_not_established\"]}\n"
    );
    Ok(())
}

#[test]
fn raw_scan_is_deterministic_and_bounded() -> Result<(), Box<dyn std::error::Error>> {
    let file = fixture(1)?;
    let path = file.path().to_str().ok_or("temporary path is not UTF-8")?;
    let arguments = [
        "probe",
        path,
        "--scan-pages",
        "--max-scan-bytes",
        "2048",
        "--max-pages",
        "1",
    ];
    let first = run(&arguments)?;
    let second = run(&arguments)?;
    assert!(first.status.success());
    assert_eq!(first.stdout, second.stdout);
    let stdout = String::from_utf8(first.stdout)?;
    assert!(stdout.contains(
        "\"pages_read\":1,\"bytes_read\":2048,\"hash_work_units\":2048,\
\"total_work_units\":2049"
    ));
    assert!(stdout.contains("\"checksum_algorithm\":\"fnv1a64\""));
    Ok(())
}

#[test]
fn raw_scan_streams_every_candidate_page() -> Result<(), Box<dyn std::error::Error>> {
    let file = fixture(3)?;
    let path = file.path().to_str().ok_or("temporary path is not UTF-8")?;
    let output = run(&[
        "probe",
        path,
        "--scan-pages",
        "--max-scan-bytes",
        "6144",
        "--max-pages",
        "3",
    ])?;

    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let stdout = String::from_utf8(output.stdout)?;
    assert!(stdout.contains(
        "\"pages_read\":3,\"bytes_read\":6144,\"hash_work_units\":6144,\
\"total_work_units\":6147"
    ));
    Ok(())
}

#[test]
fn raw_scan_preflights_byte_and_page_limits() -> Result<(), Box<dyn std::error::Error>> {
    let file = fixture(2)?;
    let path = file.path().to_str().ok_or("temporary path is not UTF-8")?;
    let byte_limited = run(&[
        "probe",
        path,
        "--scan-pages",
        "--max-scan-bytes",
        "4095",
        "--max-pages",
        "2",
    ])?;
    assert_eq!(byte_limited.status.code(), Some(1));
    assert_eq!(
        byte_limited.stderr,
        b"{\"schema_version\":1,\"ok\":false,\"error\":\"scan_byte_limit_exceeded\",\
\"compatibility_established\":false}\n"
    );

    let page_limited = run(&[
        "probe",
        path,
        "--scan-pages",
        "--max-scan-bytes",
        "4096",
        "--max-pages",
        "1",
    ])?;
    assert_eq!(page_limited.status.code(), Some(1));
    assert_eq!(
        page_limited.stderr,
        b"{\"schema_version\":1,\"ok\":false,\"error\":\"scan_page_limit_exceeded\",\
\"compatibility_established\":false}\n"
    );
    Ok(())
}

#[test]
fn probe_rejects_unknown_signature_without_format_claims() -> Result<(), Box<dyn std::error::Error>>
{
    let file = tempfile::NamedTempFile::new()?;
    fs::write(file.path(), vec![0_u8; 2_048])?;
    let path = file.path().to_str().ok_or("temporary path is not UTF-8")?;
    let output = run(&["probe", path])?;
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(
        output.stderr,
        b"{\"schema_version\":1,\"ok\":false,\"error\":\"unrecognized_signature\",\
\"compatibility_established\":false}\n"
    );
    Ok(())
}
