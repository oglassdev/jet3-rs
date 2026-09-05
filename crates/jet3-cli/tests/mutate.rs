#![cfg(unix)]
use serde_json::{Value, json};
use std::{
    io::Write,
    path::Path,
    process::{Command, Output, Stdio},
};
type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;
fn request(command: &str, path: &Path, input: &Value) -> Result<Output> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg(command)
        .arg(path)
        .args(["--input", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or("stdin")?
        .write_all(input.to_string().as_bytes())?;
    Ok(child.wait_with_output()?)
}
fn create(path: &Path) -> Result {
    let output = request(
        "create",
        path,
        &json!({"tables":[{"name":"Rows","columns":[{"name":"Id","type":"long"}],"rows":[[{"long":1}],[{"long":2}],[{"long":3}]]}]}),
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}
fn rows(path: &Path) -> Result<Vec<(jet3::RowLocator, i32, usize)>> {
    let mut budget = jet3::ResourceBudget::new(jet3::ResourceLimits::default());
    let mut db = jet3::DatabaseReader::open(path, &mut budget)?;
    let root = {
        let mut catalog = db.catalog(&mut budget)?;
        let mut root = None;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == b"Rows" {
                root = record.table_definition();
            }
        }
        root.ok_or("table")?
    };
    let definition = db.table_definition(root, &mut budget)?;
    let ordinal = definition.columns().first().ok_or("column")?.ordinal();
    let mut cursor = db.rows(&definition, &mut budget)?;
    let mut rows = Vec::new();
    while let Some(row) = cursor.next_row()? {
        let bytes: [u8; 4] = row
            .field(ordinal)
            .and_then(|f| f.raw_bytes())
            .ok_or("field")?
            .try_into()?;
        rows.push((
            row.locator(),
            i32::from_le_bytes(bytes),
            row.raw_bytes().len(),
        ));
    }
    Ok(rows)
}
fn locator(row: jet3::RowLocator) -> Value {
    json!({"page":row.page().get(),"slot":row.slot()})
}

#[test]
fn public_create_update_and_typed_errors_preserve_source() -> Result {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("file.mdb");
    create(&path)?;
    let row = rows(&path)?[1].0;
    let output = request(
        "mutate",
        &path,
        &json!({"operation":"update","table":"Rows","row":locator(row),"column":0,"value":{"long":-42}}),
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout)?["row"],
        locator(row)
    );
    assert_eq!(rows(&path)?[1].1, -42);
    let before = std::fs::read(&path)?;
    for input in [
        json!({"operation":"update","table":"Rows","row":locator(row),"column":0,"value":null}),
        json!({"operation":"update","table":"Rows","row":locator(row),"column":0}),
        json!({"operation":"update","table":"Rows","row":{"page":999,"slot":1},"column":0,"value":{"long":7}}),
        json!({"operation":"update","table":"Rows","row":locator(row),"column":0,"value":{"long":2147483648_i64}}),
        json!({"operation":"insert","table":"Rows","values":[{"text":"é"}]}),
        json!({"operation":"delete","table":"Other","row":locator(row)}),
        json!({"operation":"delete","table":"Rows","row":{"page":23,"slot":256}}),
        json!({"operation":"delete","table":"Rows","row":locator(row),"overwrite":true}),
    ] {
        let output = request("mutate", &path, &input)?;
        assert_eq!(output.status.code(), Some(1));
        assert!(output.stdout.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&output.stderr)?["error"],
            "mutation_failed"
        );
        assert_eq!(std::fs::read(&path)?, before);
    }
    Ok(())
}

#[test]
fn synthetic_consistent_page_fixture_exercises_public_insert_delete_dispatch() -> Result {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("file.mdb");
    create(&path)?;
    let initial = rows(&path)?;
    let page = initial[0].0.page();
    assert!(initial.iter().all(|r| r.0.page() == page));
    // Synthetic unit-test fixture: source the free-byte field from EXP-0162.
    // This checks CLI dispatch and makes no DAO compatibility assertion.
    let free =
        jet3::PAGE_BYTES - 10 - 2 * initial.len() - initial.iter().map(|r| r.2).sum::<usize>();
    let mut bytes = std::fs::read(&path)?;
    let offset = page.get() as usize * jet3::PAGE_BYTES + 2;
    bytes[offset..offset + 2].copy_from_slice(&u16::try_from(free)?.to_le_bytes());
    std::fs::write(&path, &bytes)?;
    let output = request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Rows","values":[{"long":4}]}),
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let after = rows(&path)?;
    assert_eq!(
        after.iter().map(|r| r.1).collect::<Vec<_>>(),
        vec![1, 2, 3, 4]
    );
    let new = after[3].0;
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout)?["row"],
        locator(new)
    );
    let output = request(
        "mutate",
        &path,
        &json!({"operation":"delete","table":"Rows","row":locator(new)}),
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        rows(&path)?.iter().map(|r| r.1).collect::<Vec<_>>(),
        vec![1, 2, 3]
    );
    assert_eq!(
        request(
            "mutate",
            &path,
            &json!({"operation":"delete","table":"Rows","row":locator(new)})
        )?
        .status
        .code(),
        Some(1)
    );
    Ok(())
}
