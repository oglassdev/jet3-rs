#![forbid(unsafe_code)]

//! Runs `jet3-cli snapshot` on the synthetic database and checks the result
//! with the protocol 1.2 Python validator.

use std::fs;
use std::path::Path;
use std::process::Command;

use jet3_testkit::synthetic::synthetic_database;

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn repository_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap_or(Path::new("."))
}

fn run_cli(arguments: &[&str]) -> Result<std::process::Output, std::io::Error> {
    Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .args(arguments)
        .output()
}

#[test]
fn snapshot_pair_passes_the_protocol_validator() -> TestResult {
    let directory = tempfile::tempdir()?;
    let input = directory.path().join("synthetic.mdb");
    fs::write(&input, synthetic_database())?;
    let out = directory.path().join("out");
    let output = run_cli(&[
        "snapshot",
        input.to_str().ok_or("path")?,
        "--out",
        out.to_str().ok_or("path")?,
        "--scenario",
        "DAO-READ-ROWS-DUPLICATES",
        "--source-revision",
        "test",
    ])?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let summary = String::from_utf8(output.stdout)?;
    assert!(summary.contains("\"outcome\":\"success\""), "{summary}");
    assert!(summary.contains("\"scenario_satisfied\":true"), "{summary}");

    let validator = repository_root().join("oracle/windows-dao/scripts/validate_protocol_v1_2.py");
    let python = std::env::var("PYTHON").unwrap_or_else(|_| "python3".to_owned());
    let validation = Command::new(python)
        .arg("-B")
        .arg(&validator)
        .arg("document")
        .arg(out.join("snapshot.json"))
        .output()?;
    assert!(
        validation.status.success(),
        "validator failed:\n{}{}",
        String::from_utf8_lossy(&validation.stdout),
        String::from_utf8_lossy(&validation.stderr)
    );

    let coverage = fs::read_to_string(out.join("coverage.json"))?;
    assert!(coverage.contains("\"document_type\":\"coverage_receipt\""));
    assert!(coverage.ends_with("}\n"));
    Ok(())
}

#[test]
fn rejected_header_writes_only_the_coverage_receipt() -> TestResult {
    let directory = tempfile::tempdir()?;
    let input = directory.path().join("jet4.mdb");
    let mut bytes = synthetic_database();
    bytes[0x14] = 0x01;
    fs::write(&input, bytes)?;
    let out = directory.path().join("out");
    let output = run_cli(&[
        "snapshot",
        input.to_str().ok_or("path")?,
        "--out",
        out.to_str().ok_or("path")?,
        "--scenario",
        "DAO-READ-OPEN-REJECT-JET4",
    ])?;
    assert!(output.status.success());
    let summary = String::from_utf8(output.stdout)?;
    assert!(
        summary.contains("\"error_class\":\"unsupported_version\""),
        "{summary}"
    );
    assert!(summary.contains("\"scenario_satisfied\":true"), "{summary}");
    assert!(!out.join("snapshot.json").exists());
    assert!(out.join("coverage.json").exists());
    Ok(())
}
