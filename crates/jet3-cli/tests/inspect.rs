#![cfg(any(unix, windows))]
mod common;

use common::{Result, cli, request};
use serde_json::{Value, json};
use std::path::Path;
use std::process::Output;

fn create(path: &Path) -> Result {
    let output = request(
        "create",
        path,
        &json!({"tables": [
            {"name":"Items", "columns":[{"name":"Id","type":"long"},{"name":"Label","type":"text","size":20}],"rows":[[{"long":1},{"text":[233]}],[{"long":2},null]]},
            {"name":"Other", "columns":[{"name":"error","type":"long"}],"rows":[[{"long":7}]]}
        ]}),
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}
fn inspect(path: &Path, args: &[&str]) -> Result<Output> {
    Ok(cli().arg("inspect").arg(path).args(args).output()?)
}
fn document(output: &Output) -> Result<Value> {
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
fn create_then_inspect_named_tables_rows_and_page_without_modifying_input() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("input.mdb");
    create(&path)?;
    let before = std::fs::read(&path)?;
    let output = inspect(&path, &["--table", "Items", "--rows"])?;
    assert!(output.status.success());
    let value = document(&output)?;
    assert_eq!(value["ok"], true);
    assert_eq!(value["issues"], json!([]));
    assert_eq!(value["tables"].as_array().ok_or("expected array")?.len(), 1);
    assert_eq!(value["tables"][0]["name"], "Items");
    assert_eq!(
        value["tables"][0]["rows"],
        json!([{"Id":1,"Label":"é"},{"Id":2,"Label":null}])
    );
    assert!(value["catalog"].as_array().ok_or("expected array")?.len() > 2);
    let other = document(&inspect(&path, &["--table", "Other", "--rows"])?)?;
    assert_eq!(other["ok"], true);
    assert_eq!(other["tables"][0]["rows"], json!([{"error":7}]));
    let page = inspect(&path, &["--page", "0", "--hex"])?;
    assert!(page.status.success());
    assert_eq!(document(&page)?["page"], 0);
    assert_eq!(
        document(&page)?["hex"]
            .as_array()
            .ok_or("expected array")?
            .len(),
        128
    );
    assert_eq!(std::fs::read(path)?, before);
    Ok(())
}

#[test]
fn partial_decode_failure_is_json_with_nonzero_status() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("input.mdb");
    create(&path)?;
    let original = document(&inspect(&path, &["--table", "Items"])?)?;
    let root = original["tables"][0]["root"]
        .as_u64()
        .ok_or("expected number")? as usize;
    let mut bytes = std::fs::read(&path)?;
    bytes[root * 2048 + 20] = 0;
    std::fs::write(&path, &bytes)?;
    let output = inspect(&path, &["--rows"])?;
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stderr.is_empty());
    let value = document(&output)?;
    assert_eq!(value["ok"], false);
    assert!(
        !value["issues"]
            .as_array()
            .ok_or("expected array")?
            .is_empty()
    );
    assert!(
        value["tables"]
            .as_array()
            .ok_or("expected array")?
            .iter()
            .any(|t| t["name"] == "Other")
    );
    assert!(
        inspect(&path, &["--table", "Other", "--rows"])?
            .status
            .success()
    );
    assert_eq!(std::fs::read(path)?, bytes);
    Ok(())
}

#[test]
fn argument_open_selection_and_input_limit_errors_are_script_friendly() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("input.mdb");
    create(&path)?;
    for args in [
        ["--page", "0", "--rows"].as_slice(),
        ["--layout", "--rows"].as_slice(),
        ["--table", "Items", "--table", "Other"].as_slice(),
        ["--table", ""].as_slice(),
    ] {
        let output = inspect(&path, args)?;
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&output.stderr)?["ok"],
            false
        );
    }
    for (file, args) in [
        (directory.path().join("missing.mdb"), vec![]),
        (path.clone(), vec!["--table", "Missing"]),
        (path.clone(), vec!["--table", "é"]),
    ] {
        let output = inspect(&file, &args)?;
        assert_eq!(output.status.code(), Some(1));
        assert!(output.stdout.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&output.stderr)?["error"],
            "inspect_failed"
        );
    }
    std::fs::OpenOptions::new()
        .write(true)
        .open(&path)?
        .set_len(256 * 1024 * 1024 + 2048)?;
    let output = inspect(&path, &["--page", "0"])?;
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stderr)?["error"],
        "inspect_failed"
    );
    Ok(())
}

#[test]
fn layout_reports_locators_stored_values_long_values_and_index_trees() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("layout.mdb");
    let memo = "m".repeat(3000);
    let output = request(
        "create",
        &path,
        &json!({"tables": [{"name":"Items",
            "columns":[{"name":"Id","type":"long"},{"name":"Flag","type":"boolean"},{"name":"Body","type":"memo"}],
            "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],
            "rows":[[{"long":1},{"boolean":true},{"memo":memo}],[{"long":2},{"boolean":false},null]]}]}),
    )?;
    assert!(output.status.success());
    let before = std::fs::read(&path)?;
    let output = inspect(&path, &["--layout"])?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let layout = document(&output)?;
    assert_eq!(layout["page_count"], before.len() / 2048);
    let tables = layout["tables"].as_array().ok_or("tables")?;
    assert_eq!(tables.len(), 1);
    let items = &tables[0];
    assert_eq!(items["name"], "Items");
    assert_eq!(items["row_count"], 2);
    let types: Vec<Value> = items["columns"]
        .as_array()
        .ok_or("columns")?
        .iter()
        .map(|c| c["type"].clone())
        .collect();
    assert_eq!(types, [json!(4), json!(1), json!(12)]);
    let rows = items["rows"].as_array().ok_or("rows")?;
    assert_eq!(rows[0]["values"][0], "01000000");
    assert_eq!(rows[0]["values"][1], true);
    assert_eq!(rows[0]["values"][2], "6d".repeat(3000));
    assert_eq!(rows[0]["long_values"][0]["column"], 2);
    assert_eq!(rows[0]["long_values"][0]["length"], 3000);
    assert_eq!(rows[1]["values"], json!(["02000000", false, null]));
    assert_eq!(rows[1]["long_values"], json!([]));
    let index = &items["indexes"][0];
    assert_eq!(index["name"], "ById");
    assert_eq!(index["depth"], 1);
    let entries: Vec<[Value; 2]> = index["entries"]
        .as_array()
        .ok_or("entries")?
        .iter()
        .map(|e| [e[1].clone(), e[2].clone()])
        .collect();
    let locators: Vec<[Value; 2]> = rows
        .iter()
        .map(|r| [r["page"].clone(), r["slot"].clone()])
        .collect();
    assert_eq!(entries, locators);
    let named = document(&inspect(&path, &["--layout", "--table", "MSysObjects"])?)?;
    assert_eq!(named["tables"][0]["name"], "MSysObjects");
    assert_eq!(std::fs::read(&path)?, before);
    Ok(())
}
