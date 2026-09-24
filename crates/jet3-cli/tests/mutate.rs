#![cfg(any(unix, windows))]
mod common;

use common::{Result, cli, request};
use serde_json::{Value, json};
use std::path::Path;

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
    for value in [Value::Null, json!({"long": -42})] {
        let output = request(
            "mutate",
            &path,
            &json!({"operation":"update","table":"Rows","row":locator(row),"column":0,"value":value}),
        )?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        if value.is_null() {
            let inspection = cli().arg("inspect").arg(&path).arg("--rows").output()?;
            assert!(inspection.status.success());
            let document: Value = serde_json::from_slice(&inspection.stdout)?;
            assert!(
                document["tables"]
                    .as_array()
                    .ok_or("tables")?
                    .iter()
                    .any(|table| {
                        table["kind"] == "User"
                            && table["rows"] == json!([{"Id":1},{"Id":null},{"Id":3}])
                    })
            );
        }
    }
    let before = std::fs::read(&path)?;
    for input in [
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

#[test]
fn replace_updates_indexed_text_and_nulls_preserving_locator() -> Result {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("file.mdb");
    let created = request(
        "create",
        &path,
        &json!({"tables":[{
            "name":"Rows", "columns":[{"name":"Id","type":"long"},{"name":"Name","type":"text","size":80}],
            "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],
            "rows":[[{"long":1},{"text":"One"}],[{"long":2},{"text":"Two"}]]
        }]}),
    )?;
    assert!(
        created.status.success(),
        "{}",
        String::from_utf8_lossy(&created.stderr)
    );
    let row = rows(&path)?[0].0;
    for text in [json!({"text":"A longer name"}), Value::Null] {
        let output = request(
            "mutate",
            &path,
            &json!({"operation":"replace","table":"Rows","row":locator(row),"values":[{"long":-7},text]}),
        )?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let response: Value = serde_json::from_slice(&output.stdout)?;
        assert_eq!(response["operation"], "replace");
        assert_eq!(response["row"], locator(row));
        assert_eq!(rows(&path)?[0].1, -7);
        let mut b = jet3::ResourceBudget::new(jet3::ResourceLimits::default());
        let mut db = jet3::DatabaseReader::open(&path, &mut b)?;
        let root = {
            let mut c = db.catalog(&mut b)?;
            let mut root = None;
            while let Some(r) = c.next_record()? {
                if r.name().raw_bytes() == b"Rows" {
                    root = r.table_definition();
                }
            }
            root.ok_or("Rows")?
        };
        let table = db.table_definition(root, &mut b)?;
        assert_eq!(db.index_tree(&table, 0, &mut b)?.entries()[0].row(), row);
        let mut cursor = db.rows(&table, &mut b)?;
        let first = cursor.next_row()?.ok_or("row")?;
        assert_eq!(
            first
                .field(table.columns()[1].ordinal())
                .and_then(|f| f.raw_bytes()),
            if text.is_null() {
                None
            } else {
                Some(b"A longer name".as_slice())
            }
        );
    }
    let before = std::fs::read(&path)?;
    let failed = request(
        "mutate",
        &path,
        &json!({"operation":"replace","table":"Rows","row":locator(row),"values":[{"long":2},null]}),
    )?;
    assert_eq!(failed.status.code(), Some(1));
    assert_eq!(std::fs::read(&path)?, before);
    Ok(())
}

#[test]
fn cascade_options_flow_from_create_through_update_and_delete() -> Result {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("cascade.mdb");
    let created = request(
        "create",
        &path,
        &json!({"tables":[
        {"name":"Rows","columns":[{"name":"Id","type":"long"}],
         "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],
         "rows":[[{"long":1}]]},
        {"name":"Child","columns":[{"name":"Foreign","type":"long"}],"rows":[[{"long":1}],[{"long":1}]]}
    ],"relationships":[{"name":"ParentChild","cascade_updates":true,"cascade_deletes":true,
       "parent":{"table":"Rows","column":"Id"},"child":{"table":"Child","column":"Foreign"}}]}),
    )?;
    assert!(
        created.status.success(),
        "{}",
        String::from_utf8_lossy(&created.stderr)
    );
    let row = rows(&path)?[0].0;
    let changed = request(
        "mutate",
        &path,
        &json!({"operation":"update","table":"Rows",
        "row":locator(row),"column":0,"value":{"long":11}}),
    )?;
    assert!(
        changed.status.success(),
        "{}",
        String::from_utf8_lossy(&changed.stderr)
    );
    let snapshot = cli().arg("inspect").arg(&path).arg("--rows").output()?;
    assert!(snapshot.status.success());
    let document: Value = serde_json::from_slice(&snapshot.stdout)?;
    assert!(
        document["tables"]
            .as_array()
            .ok_or("tables")?
            .iter()
            .any(|table| table["rows"] == json!([{"Foreign":11},{"Foreign":11}]))
    );
    let deleted = request(
        "mutate",
        &path,
        &json!({"operation":"delete","table":"Rows","row":locator(row)}),
    )?;
    assert!(
        deleted.status.success(),
        "{}",
        String::from_utf8_lossy(&deleted.stderr)
    );
    let snapshot = cli().arg("inspect").arg(&path).arg("--rows").output()?;
    assert!(snapshot.status.success());
    let document: Value = serde_json::from_slice(&snapshot.stdout)?;
    assert!(
        document["tables"]
            .as_array()
            .ok_or("tables")?
            .iter()
            .filter(|table| table["kind"] == "User")
            .all(|table| table["rows"] == json!([]))
    );
    Ok(())
}
