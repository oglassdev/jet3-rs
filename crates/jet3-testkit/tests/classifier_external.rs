use std::env;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

use jet3::ByteCount;
use jet3_testkit::{ClassifierSnapshot, CommitId, Sha256, classify_fixture};

const SOURCE_COMMIT: &str = "0a48b190ffb3211e3e1fd1f0483327b507d15136";
const EXTERNAL_ROOT_VARIABLE: &str = "JET3_EXTERNAL_FIXTURE_ROOT";
const MANIFEST_FIELDS_SCRIPT: &str = r#"import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    manifest = json.load(source)
for fixture in manifest["fixtures"]:
    fields = (fixture["id"], fixture["path"], fixture["sha256"], str(fixture["size_bytes"]))
    sys.stdout.buffer.write(("\0".join(fields) + "\0").encode("ascii"))
"#;
type VerifiedFixture = (PathBuf, Sha256, ByteCount);

fn verified_manifest_fixtures(
    repository: &Path,
    external_root: &Path,
) -> Result<Vec<VerifiedFixture>, Box<dyn std::error::Error>> {
    let verifier = Command::new(repository.join("tools/inspect_external_corpus.py"))
        .current_dir(repository)
        .env(EXTERNAL_ROOT_VARIABLE, external_root)
        .output()?;
    if !verifier.status.success() {
        return Err(format!(
            "external corpus verifier failed: {}",
            String::from_utf8_lossy(&verifier.stderr)
        )
        .into());
    }
    let parser = Command::new("python3")
        .arg("-c")
        .arg(MANIFEST_FIELDS_SCRIPT)
        .arg(repository.join("docs/validation/external-corpus.json"))
        .output()?;
    if !parser.status.success() {
        return Err(format!(
            "external corpus manifest parser failed: {}",
            String::from_utf8_lossy(&parser.stderr)
        )
        .into());
    }
    let fields = parser
        .stdout
        .split(|byte| *byte == 0)
        .filter(|field| !field.is_empty())
        .map(std::str::from_utf8)
        .collect::<Result<Vec<_>, _>>()?;
    if fields.is_empty() || fields.len() % 4 != 0 {
        return Err("external corpus manifest parser returned an invalid record set".into());
    }
    fields
        .chunks_exact(4)
        .map(|fixture| {
            let relative = PathBuf::from(fixture[1]);
            if relative.is_absolute()
                || relative
                    .components()
                    .any(|component| matches!(component, Component::ParentDir | Component::CurDir))
            {
                return Err("external corpus manifest returned an unsafe path".into());
            }
            Ok((
                external_root.join(relative),
                Sha256::new(fixture[2])?,
                ByteCount::new(fixture[3].parse::<u64>()?),
            ))
        })
        .collect()
}

#[test]
fn real_corpus_run_reproduces_committed_snapshot_byte_for_byte()
-> Result<(), Box<dyn std::error::Error>> {
    let Some(external_root) = env::var_os(EXTERNAL_ROOT_VARIABLE) else {
        return Ok(());
    };
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut snapshot = ClassifierSnapshot::new(CommitId::new(SOURCE_COMMIT)?);
    for (path, sha256, size) in verified_manifest_fixtures(&repository, Path::new(&external_root))?
    {
        snapshot.insert(classify_fixture(path, sha256, size)?)?;
    }
    assert_eq!(
        snapshot.to_canonical_json()?,
        include_bytes!("../../../docs/validation/stage1-classifier-snapshot.json")
    );
    Ok(())
}
