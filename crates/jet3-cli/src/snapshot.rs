//! `jet3-cli snapshot`: protocol 1.2 snapshot and coverage pair for one MDB.

use std::ffi::OsString;
use std::fs;
use std::path::PathBuf;

use jet3::TextCodePage;
use jet3_testkit::{
    PROTOCOL_SCENARIOS, Producer, SnapshotOptions, SnapshotOutcome, WRITE_SCENARIOS,
    canonical_json, coverage, parse_scenarios, snapshot_bytes,
};

pub(crate) const HELP: &str = "\
  jet3-cli snapshot <file> --out <dir> --scenario <DAO-READ-...|DAO-WRITE-...> \
    [--source-revision <text>] [--code-page 1252|1251]

snapshot reads the whole database with the jet3 reader and writes
<dir>/snapshot.json (protocol 1.2 canonical semantic snapshot; omitted when
the header is rejected) and <dir>/coverage.json (reader branches observed and
a verdict for every inventory scenario).
";

#[derive(Debug)]
pub(crate) struct SnapshotCommand {
    path: PathBuf,
    out: PathBuf,
    scenario_id: String,
    source_revision: String,
    code_page: TextCodePage,
}

pub(crate) fn parse_args(
    mut arguments: impl Iterator<Item = OsString>,
) -> Result<SnapshotCommand, &'static str> {
    let path = arguments.next().ok_or("missing_file")?;
    if path.to_string_lossy().starts_with('-') {
        return Err("missing_file");
    }
    let mut out = None;
    let mut scenario_id = None;
    let mut source_revision = None;
    let mut code_page = TextCodePage::Windows1252;
    while let Some(option) = arguments.next() {
        let value = arguments.next().ok_or("missing_option_value")?;
        if option == "--out" {
            out = Some(PathBuf::from(value));
            continue;
        }
        let text = value.to_str().ok_or("invalid_option_value")?;
        if option == "--scenario" {
            scenario_id = Some(text.to_owned());
        } else if option == "--source-revision" {
            source_revision = Some(text.to_owned());
        } else if option == "--code-page" {
            code_page = match text {
                "1252" => TextCodePage::Windows1252,
                "1251" => TextCodePage::Windows1251,
                _ => return Err("invalid_code_page"),
            };
        } else {
            return Err("unknown_option");
        }
    }
    Ok(SnapshotCommand {
        path: PathBuf::from(path),
        out: out.ok_or("missing_out")?,
        scenario_id: scenario_id.ok_or("missing_scenario")?,
        source_revision: source_revision
            .unwrap_or_else(|| format!("jet3-cli {}", env!("CARGO_PKG_VERSION"))),
        code_page,
    })
}

/// Writes the artifact pair and returns a one-line JSON summary.
pub(crate) fn run(command: &SnapshotCommand) -> Result<String, String> {
    let scenarios = parse_scenarios(if command.scenario_id.starts_with("DAO-WRITE-") {
        WRITE_SCENARIOS
    } else {
        PROTOCOL_SCENARIOS
    })
    .map_err(|error| error.to_string())?;
    if !scenarios
        .iter()
        .any(|scenario| scenario.id == command.scenario_id)
    {
        return Err(format!(
            "scenario {} is not in the protocol 1.2 inventory",
            command.scenario_id
        ));
    }
    let bytes = fs::read(&command.path).map_err(|error| format!("read input: {error}"))?;
    let options = SnapshotOptions {
        scenario_id: command.scenario_id.clone(),
        source_revision: command.source_revision.clone(),
        code_page: command.code_page,
    };
    let outcome = snapshot_bytes(&bytes, &options).map_err(|error| error.to_string())?;
    let producer = Producer {
        kind: "rust",
        source_revision: command.source_revision.clone(),
    };
    let database_sha256 = match &outcome {
        SnapshotOutcome::Snapshot { snapshot, .. } => snapshot.database_sha256.clone(),
        SnapshotOutcome::OpeningFailure {
            database_sha256, ..
        } => database_sha256.clone(),
    };
    let receipt = coverage(
        &command.scenario_id,
        producer,
        database_sha256,
        &outcome,
        &scenarios,
    );
    fs::create_dir_all(&command.out).map_err(|error| format!("create output dir: {error}"))?;
    let write = |name: &str, bytes: Vec<u8>| {
        fs::write(command.out.join(name), bytes).map_err(|error| format!("write {name}: {error}"))
    };
    let (outcome_name, error_class) = match &outcome {
        SnapshotOutcome::Snapshot { snapshot, .. } => {
            write(
                "snapshot.json",
                snapshot
                    .to_canonical_json()
                    .map_err(|error| error.to_string())?,
            )?;
            ("success", None)
        }
        SnapshotOutcome::OpeningFailure { error_class, .. } => {
            match fs::remove_file(command.out.join("snapshot.json")) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(format!("remove stale snapshot.json: {error}")),
            }
            ("opening_failure", Some(*error_class))
        }
    };
    write(
        "coverage.json",
        canonical_json(&receipt).map_err(|error| error.to_string())?,
    )?;
    let satisfied = receipt
        .scenarios
        .iter()
        .any(|scenario| scenario.id == command.scenario_id && scenario.satisfied);
    let summary = serde_json::json!({
        "ok": true,
        "outcome": outcome_name,
        "error_class": error_class,
        "scenario_id": command.scenario_id,
        "scenario_satisfied": satisfied,
        "branches": receipt.branches.len(),
    });
    let mut line = serde_json::to_string(&summary).map_err(|error| error.to_string())?;
    line.push('\n');
    Ok(line)
}
