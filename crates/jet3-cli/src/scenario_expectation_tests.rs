use std::ffi::OsString;
use std::fs;

use jet3::TextCodePage;
use jet3_testkit::{Producer, ProducerKind, ProtocolScenario};

use super::{SnapshotOptions, parse_args, snapshot_with_producer};

#[test]
fn snapshot_command_rejects_scenario_outside_closed_inventory() {
    assert!(matches!(
        parse_args(args(&[
            "snapshot",
            "input.mdb",
            "--scenario-id",
            "DAO-READ-NOT-IN-INVENTORY",
            "--code-page",
            "1252",
            "--output-bundle",
            "bundle",
        ])),
        Err("unknown_scenario_id")
    ));
}

#[test]
fn unsupported_version_for_success_scenario_returns_error_without_bundle()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let input = directory.path().join("jet4.mdb");
    let bundle = directory.path().join("bundle");
    let mut bytes = supported_header();
    bytes[0x14] = 0x01;
    fs::write(&input, &bytes)?;

    let error = snapshot_with_producer(
        SnapshotOptions {
            path: input,
            scenario: ProtocolScenario::resolve("DAO-READ-ROWS-SINGLE")?,
            code_page: TextCodePage::Windows1252,
            output_bundle: bundle.clone(),
            max_input_bytes: bytes.len() as u64,
        },
        test_producer()?,
    );
    assert_eq!(error, Err("scenario_expected_success"));
    assert!(!bundle.exists());
    Ok(())
}

#[test]
fn expected_error_scenario_rejects_success_and_wrong_class_without_bundle()
-> Result<(), Box<dyn std::error::Error>> {
    for (case, mutate, expected_error) in [
        ("opened", None, "scenario_expected_opening_failure"),
        (
            "wrong-class",
            Some((0x41, 0x00)),
            "scenario_error_class_mismatch",
        ),
    ] {
        let directory = tempfile::tempdir()?;
        let input = directory.path().join(format!("{case}.mdb"));
        let bundle = directory.path().join("bundle");
        let mut bytes = supported_header();
        if let Some((offset, value)) = mutate {
            bytes[offset] = value;
        }
        fs::write(&input, &bytes)?;

        let error = snapshot_with_producer(
            SnapshotOptions {
                path: input,
                scenario: ProtocolScenario::resolve("DAO-READ-OPEN-REJECT-JET4")?,
                code_page: TextCodePage::Windows1252,
                output_bundle: bundle.clone(),
                max_input_bytes: bytes.len() as u64,
            },
            test_producer()?,
        );
        assert_eq!(error, Err(expected_error));
        assert!(!bundle.exists());
    }
    Ok(())
}

fn args(values: &[&str]) -> impl Iterator<Item = OsString> {
    values
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>()
        .into_iter()
}

fn test_producer() -> Result<Producer, jet3_testkit::SnapshotError> {
    Producer::new(
        ProducerKind::Rust,
        "0123456789abcdef0123456789abcdef01234567",
    )
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
