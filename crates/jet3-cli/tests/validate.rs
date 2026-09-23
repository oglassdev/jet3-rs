#![cfg(any(unix, windows))]

use std::{
    path::Path,
    process::{Command, Output},
};

use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits,
    RowValue, TableRows, TableSpec, create_database_with_table_rows,
};
use serde_json::Value;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error>>;

fn fixture(path: &Path) -> TestResult {
    let requests = [
        TableRows {
            table: TableSpec {
                validation: jet3::TableValidation::NONE,
                name: b"Items",
                columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
                indexes: &[IndexSpec {
                    name: b"PrimaryKey",
                    fields: &[IndexColumnSpec::ascending(b"Id")],
                    kind: IndexKind::Primary,
                }],
            },
            rows: &[&[RowValue::Long(1)], &[RowValue::Long(2)]],
        },
        TableRows {
            table: TableSpec {
                validation: jet3::TableValidation::NONE,
                name: b"Notes",
                columns: &[ColumnSpec::new(b"Body", ColumnType::Memo)],
                indexes: &[],
            },
            rows: &[&[RowValue::Memo(&[b'a'; 4096])]],
        },
    ];
    create_database_with_table_rows(
        path,
        &requests,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}

fn run(path: &Path, args: &[&str]) -> TestResult<Output> {
    Ok(Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("validate")
        .arg(path)
        .args(args)
        .output()?)
}

#[test]
fn validation_reports_coverage_and_counts_without_modifying_input() -> TestResult {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("input.mdb");
    fixture(&path)?;
    let before = std::fs::read(&path)?;
    let output = run(&path, &[])?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result["ok"], true);
    assert_eq!(result["scope"], "catalogued_allocations_and_tables");
    assert_eq!(result["checked"]["user_tables"], 2);
    assert_eq!(result["checked"]["system_tables"], 4);
    assert_eq!(result["checked"]["relationship_catalog_rows"], 0);
    assert_eq!(result["checked"]["relationships_with_verified_keys"], 0);
    assert_eq!(result["checked"]["relationship_inventory_checked"], true);
    assert_eq!(
        result["coverage_limits"]["uninterpreted_relationship_rows"],
        0
    );
    assert_eq!(result["checked"]["rows"], 33);
    assert_eq!(result["checked"]["index_entries"], 42);
    assert_eq!(result["checked"]["indexes_with_verified_keys"], 8);
    assert_eq!(result["coverage_limits"]["uninterpreted_indexes"], 0);
    assert_eq!(result["checked"]["long_value_bytes"], 4167);
    assert_eq!(result["coverage_limits"]["skipped_system_objects"], 4);
    assert!(
        result["resources"]["bytes_read"]
            .as_u64()
            .ok_or("missing bytes_read count")?
            > before.len() as u64
    );
    assert_eq!(std::fs::read(&path)?, before);
    Ok(())
}

#[test]
fn invalid_content_budget_and_open_failures_are_json_errors() -> TestResult {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("input.mdb");
    fixture(&path)?;
    for args in [["--max-work-units", "1"], ["--max-input-bytes", "1"]] {
        let output = run(&path, &args)?;
        assert_eq!(output.status.code(), Some(1));
        assert!(output.stdout.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&output.stderr)?["error"],
            "validation_failed"
        );
    }
    let output = run(&directory.path().join("missing.mdb"), &[])?;
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stderr)?["ok"],
        false
    );
    let mut changed = std::fs::read(&path)?;
    // EXP-0073/0093: first created definition is page 20; live count at byte 12.
    changed[20 * 2048 + 12..20 * 2048 + 16].copy_from_slice(&3_u32.to_le_bytes());
    std::fs::write(&path, &changed)?;
    let output = run(&path, &[])?;
    assert_eq!(output.status.code(), Some(1));
    let error: Value = serde_json::from_slice(&output.stderr)?;
    assert!(
        error["message"]
            .as_str()
            .ok_or("missing error message")?
            .contains("RowCount")
    );
    assert_eq!(std::fs::read(&path)?, changed);
    Ok(())
}

#[test]
fn invalid_options_exit_two() -> TestResult {
    for (args, expected) in [
        (vec!["--code-page", "65001"], "invalid_code_page"),
        (vec!["--code-page", "garbage"], "invalid_code_page"),
        (vec!["--max-work-units", "-1"], "invalid_limit"),
        (vec!["--max-input-bytes"], "missing_option_value"),
        (vec!["--rows"], "unknown_option"),
        (
            vec!["--code-page", "1252", "--code-page", "1251"],
            "duplicate_option",
        ),
    ] {
        let output = run(Path::new("unused.mdb"), &args)?;
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        let error: Value = serde_json::from_slice(&output.stderr)?;
        assert_eq!(error["ok"], false);
        assert_eq!(error["error"], expected);
    }
    Ok(())
}
