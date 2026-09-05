//! Finite hosted indexed field updates from EXP-0178/0186, through public APIs.
use jet3::{
    CatalogObjectClass, ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate,
    IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits, RowLocator, RowValue,
    TableSpec, create_database_with_rows, update_field,
};
use serde_json::{Value, json};
use std::{fs, num::NonZeroU8, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const INDEXED_UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/indexed-update-scenarios.json");
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn scenario(id: &str) -> Result<Value> {
    let v: Value = serde_json::from_str(INDEXED_UPDATE_SCENARIOS)?;
    v["scenarios"]
        .as_array()
        .ok_or("scenarios")?
        .iter()
        .find(|s| s["id"] == id)
        .cloned()
        .ok_or_else(|| "unknown scenario".into())
}
fn integer(value: &Value) -> Result<i32> {
    Ok(value.as_i64().ok_or("Long")?.try_into()?)
}
fn generate(path: &Path, case: &Value) -> Result<()> {
    let table = &case["tables"][0];
    let index = &table["indexes"][0];
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: NonZeroU8::new(8).ok_or("width")?,
            },
        ),
    ];
    let fields = [if index["fields"][0]["descending"] == true {
        IndexColumnSpec::descending(0)
    } else {
        IndexColumnSpec::ascending(0)
    }];
    let indexes = [IndexSpec {
        name: b"ByKey",
        fields: &fields,
        kind: match index["kind"].as_str() {
            Some("primary") => IndexKind::Primary,
            Some("unique") => IndexKind::Unique,
            Some("ordinary") => IndexKind::Ordinary,
            _ => return Err("index kind".into()),
        },
    }];
    let rows = table["rows"]
        .as_array()
        .ok_or("rows")?
        .iter()
        .map(|r| {
            Ok([
                RowValue::Long(integer(&r[0])?),
                RowValue::Long(integer(&r[1])?),
                RowValue::Text(r[2].as_str().ok_or("text")?.as_bytes()),
            ])
        })
        .collect::<Result<Vec<_>>>()?;
    let slices = rows.iter().map(|r| r.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        &slices,
        &mut budget(),
    )?;
    Ok(())
}
struct Row {
    locator: RowLocator,
    values: [i32; 2],
    offset: usize,
}
struct Read {
    root: u64,
    index: u64,
    column: ColumnOrdinal,
    rows: Vec<Row>,
    entries: Vec<Vec<u8>>,
}
fn key(value: i32, descending: bool) -> [u8; 5] {
    let bytes = ((value as u32) ^ 0x80000000).to_be_bytes();
    let mut key = [0x7f, bytes[0], bytes[1], bytes[2], bytes[3]];
    if descending {
        for b in &mut key {
            *b ^= 255
        }
    }
    key
}
fn read(path: &Path, case: &Value) -> Result<Read> {
    let bytes = fs::read(path)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut c = db.catalog(&mut b)?;
        let mut roots = Vec::new();
        while let Some(r) = c.next_record()? {
            if r.class() == CatalogObjectClass::User && r.name().raw_bytes() == b"Items" {
                roots.push(r.table_definition().ok_or("root")?);
            }
        }
        if roots.len() != 1 {
            return Err("ambiguous root".into());
        }
        roots[0]
    };
    let def = db.table_definition(root, &mut b)?;
    let column = def
        .columns()
        .get(case["request"]["column"].as_u64().ok_or("column")? as usize)
        .ok_or("column absent")?
        .ordinal();
    let mut rows = Vec::new();
    {
        let mut cursor = db.rows(&def, &mut b)?;
        while let Some(row) = cursor.next_row()? {
            if row.locator() != row.storage_locator() {
                return Err("overflow row".into());
            }
            let mut values = [0; 2];
            for (i, v) in values.iter_mut().enumerate() {
                *v = i32::from_le_bytes(
                    row.field(def.columns()[i].ordinal())
                        .and_then(|f| f.raw_bytes())
                        .ok_or("Long bytes")?
                        .try_into()?,
                );
            }
            let base = row.locator().page().get() as usize * 2048;
            let slot = row.locator().slot() as usize;
            let start =
                u16::from_le_bytes(bytes[base + 10 + 2 * slot..base + 12 + 2 * slot].try_into()?)
                    as usize;
            if start >= 2048
                || bytes.get(base + start..base + start + row.raw_bytes().len())
                    != Some(row.raw_bytes())
            {
                return Err("row directory binding".into());
            }
            rows.push(Row {
                locator: row.locator(),
                values,
                offset: base + start + 1 + 4 * column.get() as usize,
            });
        }
    }
    let tree = db.index_tree(&def, 0, &mut b)?;
    let descending = case["tables"][0]["indexes"][0]["fields"][0]["descending"] == true;
    let mut expected = Vec::new();
    for row in &rows {
        let mut record = key(row.values[0], descending).to_vec();
        record.extend_from_slice(&(row.locator.page().get() as u32).to_be_bytes()[1..]);
        record.push(row.locator.slot());
        expected.push(record)
    }
    expected.sort();
    if tree.entries().len() != expected.len() {
        return Err("index row count".into());
    }
    for (actual, wanted) in tree.entries().iter().zip(&expected) {
        if actual.key().raw_bytes() != &wanted[..5]
            || actual.row().page().get()
                != u32::from_be_bytes([0, wanted[5], wanted[6], wanted[7]]) as u64
            || actual.row().slot() != wanted[8]
        {
            return Err("complete index row/locator binding".into());
        }
    }
    Ok(Read {
        root: root.get(),
        index: tree.root().get(),
        column,
        rows,
        entries: expected,
    })
}
fn selected<'a>(read: &'a Read, case: &Value) -> Result<&'a Row> {
    let selector = case["request"]["selector_column"]
        .as_u64()
        .ok_or("selector column")? as usize;
    let selected = integer(&case["request"]["selected"])?;
    let found = read
        .rows
        .iter()
        .filter(|r| r.values.get(selector) == Some(&selected))
        .collect::<Vec<_>>();
    if found.len() != 1 {
        return Err("ambiguous selected row".into());
    }
    Ok(found[0])
}
fn verify(id: &str, case: &Value, directory: &Path) -> Result<Value> {
    let before_path = directory.join("before/database.mdb");
    let after_path = directory.join("after/database.mdb");
    let before = fs::read(&before_path)?;
    let after = fs::read(&after_path)?;
    let read_before = read(&before_path, case)?;
    let read_after = read(&after_path, case)?;
    let target = selected(&read_before, case)?;
    let replacement = integer(&case["request"]["replacement"])?;
    let mut expected = before.clone();
    expected[target.offset..target.offset + 4].copy_from_slice(&replacement.to_le_bytes());
    let index_start = read_before.index as usize * 2048 + 248;
    let index_length = read_before.entries.len() * 9;
    if case["request"]["column"] == 0 {
        let base = read_before.index as usize * 2048;
        if before[base..base + 2] != [4, 1]
            || before[base + 8..base + 22] != [0; 14]
            || before[index_start..index_start + index_length] != read_before.entries.concat()
        {
            return Err("isolated uncompressed leaf".into());
        }
        let mut entries = read_before.entries.clone();
        let key = key(
            replacement,
            case["tables"][0]["indexes"][0]["fields"][0]["descending"] == true,
        );
        for entry in &mut entries {
            if entry[5..8] == (target.locator.page().get() as u32).to_be_bytes()[1..]
                && entry[8] == target.locator.slot()
            {
                entry[..5].copy_from_slice(&key)
            }
        }
        entries.sort();
        expected[index_start..index_start + index_length].copy_from_slice(&entries.concat());
    }
    if read_before.root != read_after.root
        || read_before.index != read_after.index
        || expected != after
    {
        return Err("exact field/index preservation".into());
    }
    Ok(
        json!({"scenario_id":id,"request":case["request"],"root":read_before.root,"page":target.locator.page().get(),"slot":target.locator.slot(),"column":read_before.column.get(),"offset":target.offset,"index":read_before.index,"index_offset":index_start,"index_length":index_length,"before_sha256":crate::sha256_hex(&before),"after_sha256":crate::sha256_hex(&after),"preserved":true}),
    )
}
/// Creates finite public API pairs, or verifies retained pairs without mutation.
pub fn indexed_update_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    let case = scenario(id)?;
    if case["request"]["kind"] != "indexed_field" {
        return crate::row_allocation_fixture(command, id, directory);
    }
    if command == "generate" {
        fs::create_dir(directory.join("before"))?;
        fs::create_dir(directory.join("after"))?;
        let before = directory.join("before/database.mdb");
        let after = directory.join("after/database.mdb");
        generate(&before, &case)?;
        let read = read(&before, &case)?;
        let row = selected(&read, &case)?.locator;
        fs::copy(before, &after)?;
        update_field(
            &after,
            FieldUpdate {
                table: b"Items",
                row,
                column: read.column,
                value: RowValue::Long(integer(&case["request"]["replacement"])?),
            },
            &mut budget(),
        )?;
    } else if command != "verify" {
        return Err("expected generate or verify".into());
    }
    println!("{}", verify(id, &case, directory)?);
    Ok(())
}
#[cfg(all(test, unix))]
mod tests {
    use super::*;
    #[test]
    fn public_indexed_updates_and_preservation() -> Result<()> {
        for suffix in [
            "PRIMARY-KEY",
            "DESCENDING-KEY",
            "FULL-LEAF-KEY",
            "INDEXED-PAYLOAD",
        ] {
            let id = format!("DAO-UPDATE-{suffix}");
            let dir = tempfile::tempdir()?;
            indexed_update_fixture("generate", &id, dir.path())?;
            let path = dir.path().join("after/database.mdb");
            let original = fs::read(&path)?;
            for offset in [1538, original.len() - 1, 23 * 2048 + 250] {
                let mut bad = original.clone();
                bad[offset] ^= 1;
                fs::write(&path, bad)?;
                assert!(verify(&id, &scenario(&id)?, dir.path()).is_err())
            }
            fs::write(path, original)?;
        }
        Ok(())
    }
}
