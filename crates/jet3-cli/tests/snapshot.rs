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

/// Runs the protocol 1.2 Python validator's `document` command on one artifact.
fn validate_document(path: &Path) -> TestResult {
    let validator = repository_root().join("oracle/windows-dao/scripts/validate_protocol_v1_2.py");
    let default_python = if cfg!(windows) { "python" } else { "python3" };
    let python = std::env::var("PYTHON").unwrap_or_else(|_| default_python.to_owned());
    let validation = Command::new(python)
        .arg("-B")
        .arg(&validator)
        .arg("document")
        .arg(path)
        .output()?;
    assert!(
        validation.status.success(),
        "validator rejected {}:\n{}{}",
        path.display(),
        String::from_utf8_lossy(&validation.stdout),
        String::from_utf8_lossy(&validation.stderr)
    );
    Ok(())
}

/// Runs the protocol 1.2 Python validator on one artifact pair.
fn validate_pair(coverage: &Path, snapshot: Option<&Path>) -> TestResult {
    let validator = repository_root().join("oracle/windows-dao/scripts/validate_protocol_v1_2.py");
    let default_python = if cfg!(windows) { "python" } else { "python3" };
    let python = std::env::var("PYTHON").unwrap_or_else(|_| default_python.to_owned());
    let mut command = Command::new(python);
    command.arg("-B").arg(&validator).arg("pair").arg(coverage);
    if let Some(snapshot) = snapshot {
        command.arg(snapshot);
    }
    let validation = command.output()?;
    assert!(
        validation.status.success(),
        "validator rejected pair:\n{}{}",
        String::from_utf8_lossy(&validation.stdout),
        String::from_utf8_lossy(&validation.stderr)
    );
    Ok(())
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

    validate_document(&out.join("snapshot.json"))?;
    validate_document(&out.join("coverage.json"))?;
    validate_pair(&out.join("coverage.json"), Some(&out.join("snapshot.json")))
}

#[test]
fn unknown_scenario_id_writes_nothing() -> TestResult {
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
        "DAO-READ-NOT-IN-INVENTORY",
    ])?;
    assert!(!output.status.success());
    assert!(!out.exists());
    Ok(())
}

#[test]
fn rejected_header_writes_only_the_coverage_receipt() -> TestResult {
    let directory = tempfile::tempdir()?;
    let input = directory.path().join("jet4.mdb");
    let out = directory.path().join("out");
    fs::write(&input, synthetic_database())?;
    let initial = run_cli(&[
        "snapshot",
        input.to_str().ok_or("path")?,
        "--out",
        out.to_str().ok_or("path")?,
        "--scenario",
        "DAO-READ-ROWS-DUPLICATES",
    ])?;
    assert!(initial.status.success());
    assert!(out.join("snapshot.json").exists());

    let mut bytes = synthetic_database();
    bytes[0x14] = 0x01;
    fs::write(&input, bytes)?;
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
    validate_document(&out.join("coverage.json"))?;
    validate_pair(&out.join("coverage.json"), None)
}

#[test]
#[cfg(unix)]
fn write_snapshot_uses_separate_inventory_and_public_fixture_creation() -> TestResult {
    let directory = tempfile::tempdir()?;
    let database = directory.path().join("write.mdb");
    jet3_testkit::write_fixture("DAO-WRITE-AUTO-PRIMARY", &database)?;
    let output = directory.path().join("snapshot");
    let run = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .args(["snapshot"])
        .arg(&database)
        .args(["--scenario", "DAO-WRITE-AUTO-PRIMARY", "--out"])
        .arg(&output)
        .output()?;
    assert!(
        run.status.success(),
        "{}",
        String::from_utf8_lossy(&run.stderr)
    );
    let receipt: serde_json::Value =
        serde_json::from_slice(&fs::read(output.join("coverage.json"))?)?;
    assert_eq!(receipt["scenario_id"], "DAO-WRITE-AUTO-PRIMARY");
    let scenarios = receipt["scenarios"].as_array().ok_or("missing scenarios")?;
    assert!(scenarios.iter().all(|s| {
        s["id"]
            .as_str()
            .is_some_and(|id| id.starts_with("DAO-WRITE-"))
    }));
    assert!(
        scenarios
            .iter()
            .any(|s| s["id"] == "DAO-WRITE-AUTO-PRIMARY" && s["satisfied"] == true)
    );
    let absent = directory.path().join("unknown.mdb");
    assert!(jet3_testkit::write_fixture("DAO-WRITE-UNKNOWN", &absent).is_err());
    assert!(!absent.exists());
    Ok(())
}
