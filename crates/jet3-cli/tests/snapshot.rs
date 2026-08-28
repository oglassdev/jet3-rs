#![forbid(unsafe_code)]

use std::process::{Command, Output};

fn run(arguments: &[&str]) -> Result<Output, std::io::Error> {
    Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .args(arguments)
        .output()
}

#[test]
fn snapshot_rejects_caller_controlled_source_revision() -> Result<(), Box<dyn std::error::Error>> {
    let output = run(&[
        "snapshot",
        "input.mdb",
        "--scenario-id",
        "DAO-READ-ROWS-SINGLE",
        "--code-page",
        "1252",
        "--output-bundle",
        "bundle",
        "--source-revision",
        "caller-value",
    ])?;
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        output.stderr,
        b"{\"schema_version\":1,\"ok\":false,\"error\":\"unknown_option\",\
\"compatibility_established\":false}\n"
    );
    Ok(())
}

#[test]
fn snapshot_rejects_independent_artifact_output_paths() -> Result<(), Box<dyn std::error::Error>> {
    let output = run(&[
        "snapshot",
        "input.mdb",
        "--scenario-id",
        "DAO-READ-ROWS-SINGLE",
        "--code-page",
        "1252",
        "--snapshot-output",
        "snapshot.json",
        "--coverage-output",
        "coverage-receipt.json",
    ])?;
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8(output.stderr)?.contains("\"error\":\"unknown_option\""));
    Ok(())
}

#[test]
fn snapshot_requires_the_single_bundle_destination() -> Result<(), Box<dyn std::error::Error>> {
    let output = run(&[
        "snapshot",
        "input.mdb",
        "--scenario-id",
        "DAO-READ-ROWS-SINGLE",
        "--code-page",
        "1252",
    ])?;
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8(output.stderr)?.contains("\"error\":\"missing_output_bundle\""));
    Ok(())
}
