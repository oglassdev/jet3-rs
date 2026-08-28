#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use jet3::{ByteCount, JET3_PAGE_SIZE, ReadLimits, ResourceBudget, ResourceLimits};
use jet3_testkit::{
    Producer, ProducerKind, RejectedFormatErrorClass, ScenarioId, SemanticOpenFailure,
    SemanticSnapshotArtifacts, Sha256,
};

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn create() -> std::io::Result<Self> {
        let path = std::env::temp_dir().join(format!(
            "jet3-open-failure-artifacts-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        if path.exists() {
            fs::remove_dir_all(&path)?;
        }
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn rejected_opening_artifacts_pass_the_shared_protocol_validator()
-> Result<(), Box<dyn std::error::Error>> {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let fixture = fs::read_to_string(repository.join(
        "oracle/windows-dao/protocol/v1_2/fixtures/rejected-format-normalization-vectors.tsv",
    ))?;
    let directory = TestDirectory::create()?;
    for line in fixture.lines().filter(|line| !line.starts_with('#')) {
        let fields = line.split('\t').collect::<Vec<_>>();
        let error_class = match fields[3] {
            "unsupported_version" => RejectedFormatErrorClass::UnsupportedVersion,
            "encrypted_database" => RejectedFormatErrorClass::EncryptedDatabase,
            "password_protected" => RejectedFormatErrorClass::PasswordProtected,
            _ => return Err("fixture contains an unknown normalized error class".into()),
        };
        let mut budget = ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
            ByteCount::new(0),
            JET3_PAGE_SIZE,
            ByteCount::new(0),
        )));
        let artifacts = SemanticSnapshotArtifacts::opening_failure(
            SemanticOpenFailure {
                scenario_id: ScenarioId::new(fields[1])?,
                producer: Producer::new(ProducerKind::Rust, "integration-test")?,
                database_sha256: Sha256::new("ab".repeat(32))?,
                error_class,
            },
            &mut budget,
        )?;
        let (snapshot, receipt) = artifacts.to_canonical_json(&mut budget)?;
        for (name, bytes) in [
            ("snapshot.json", snapshot),
            ("coverage-receipt.json", receipt),
        ] {
            let path = directory.0.join(format!("{}-{name}", fields[0]));
            fs::write(&path, bytes)?;
            let output = Command::new("python3")
                .arg("-B")
                .arg("oracle/windows-dao/scripts/validate_protocol_v1_2.py")
                .arg("document")
                .arg(&path)
                .current_dir(&repository)
                .output()?;
            if !output.status.success() {
                return Err(format!(
                    "shared validator rejected {}: {}",
                    path.display(),
                    String::from_utf8_lossy(&output.stderr)
                )
                .into());
            }
        }
    }
    Ok(())
}
