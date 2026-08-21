#![forbid(unsafe_code)]

//! Emits a canonical, commit-bound page classifier snapshot.

use std::env;
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use jet3::ByteCount;
use jet3_testkit::{ClassifierSnapshot, CommitId, Sha256, classify_fixture};

const USAGE: &str = "\
Usage:
  jet3-classifier-snapshot <commit> \\
    <fixture> <verified-sha256> <verified-size-bytes> [...]

Verify every fixture with the repository corpus verifier before invoking this
tool. Each fixture group must contain exactly a path, digest, and byte size.
";

fn main() -> ExitCode {
    match run(env::args_os().skip(1)) {
        Ok(json) => exit_after_write(io::stdout().lock(), &json, 0),
        Err(error) => {
            let message = format!("jet3-classifier-snapshot: {error}\n{USAGE}");
            exit_after_write(io::stderr().lock(), message.as_bytes(), 2)
        }
    }
}

fn run(arguments: impl Iterator<Item = OsString>) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut arguments = arguments;
    let commit = arguments.next().ok_or("missing source commit")?;
    let commit = commit.to_str().ok_or("source commit is not UTF-8")?;
    let mut snapshot = ClassifierSnapshot::new(CommitId::new(commit)?);
    let mut fixture_count = 0_u64;

    while let Some(path) = arguments.next() {
        let path = PathBuf::from(path);
        if is_quarantined(&path) {
            return Err("fixture path names a quarantined bundle area".into());
        }
        let sha256 = arguments.next().ok_or("missing verified fixture SHA-256")?;
        let size = arguments
            .next()
            .ok_or("missing verified fixture byte size")?;
        let sha256 = sha256
            .to_str()
            .ok_or("verified fixture SHA-256 is not UTF-8")?;
        let size = size
            .to_str()
            .ok_or("verified fixture byte size is not UTF-8")?
            .parse::<u64>()?;
        snapshot.insert(classify_fixture(
            path,
            Sha256::new(sha256)?,
            ByteCount::new(size),
        )?)?;
        fixture_count = fixture_count
            .checked_add(1)
            .ok_or("fixture count overflow")?;
    }
    if fixture_count == 0 {
        return Err("at least one fixture is required".into());
    }
    Ok(snapshot.to_canonical_json()?)
}

fn is_quarantined(path: &std::path::Path) -> bool {
    let rendered = path.as_os_str().to_string_lossy();
    rendered.contains("project-source") || rendered.contains("project-context")
}

fn exit_after_write(mut output: impl Write, bytes: &[u8], success: u8) -> ExitCode {
    if output.write_all(bytes).is_ok() {
        ExitCode::from(success)
    } else {
        ExitCode::from(74)
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::is_quarantined;

    #[test]
    fn quarantined_bundle_paths_are_refused() {
        assert!(is_quarantined(Path::new("bundle/project-source/file.mdb")));
        assert!(is_quarantined(Path::new(
            "bundle/project-context-copy/file.mdb"
        )));
        assert!(!is_quarantined(Path::new(
            "bundle/controller-backups/file.mdb"
        )));
    }
}
