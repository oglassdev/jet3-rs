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
