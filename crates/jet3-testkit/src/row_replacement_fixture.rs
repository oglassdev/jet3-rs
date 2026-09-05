//! Finite hosted sole-release and full-row replacements through public APIs.
use jet3::{
    CatalogObjectClass, ColumnSpec, ColumnType, DatabaseReader, ResourceBudget, ResourceLimits,
    RowDelete, RowLocator, RowUpdate, RowValue, TableRows, TableSpec,
    create_database_with_table_rows, delete_row, update_row,
};
use serde_json::{Value, json};
use std::{fs, num::NonZeroU8, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const ROW_REPLACEMENT_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-replacement-scenarios.json");
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn integer(v: &Value) -> Result<i32> {
    Ok(v.as_i64().ok_or("Long")?.try_into()?)
}
fn scenario(id: &str) -> Result<Value> {
    let v: Value = serde_json::from_str(ROW_REPLACEMENT_SCENARIOS)?;
    v["scenarios"]
        .as_array()
        .ok_or("scenarios")?
        .iter()
        .find(|s| s["id"] == id)
        .cloned()
        .ok_or_else(|| "unknown scenario".into())
}
fn values(row: &Value) -> Result<Vec<RowValue<'_>>> {
    row.as_array()
        .ok_or("row")?
        .iter()
        .enumerate()
        .map(|(i, v)| {
            Ok(if v.is_null() {
                RowValue::Null
            } else {
                match i {
                    0 | 1 => RowValue::Long(integer(v)?),
                    2 => RowValue::Text(v.as_str().ok_or("Text")?.as_bytes()),
                    3 => RowValue::Binary(v.as_str().ok_or("Binary ASCII")?.as_bytes()),
                    4 => RowValue::Boolean(v.as_bool().ok_or("Boolean")?),
                    _ => return Err("column".into()),
                }
            })
        })
        .collect()
}
fn generate(path: &Path, case: &Value) -> Result<()> {
    let tables = case["tables"].as_array().ok_or("tables")?;
    let columns = tables
        .iter()
        .map(|t| {
            t["columns"]
                .as_array()
                .ok_or("columns")?
                .iter()
                .map(|c| {
                    Ok(ColumnSpec::new(
                        c["name"].as_str().ok_or("name")?.as_bytes(),
                        match c["kind"].as_str() {
                            Some("Long") => ColumnType::Long,
                            Some("Text") => ColumnType::Text {
                                max_len: NonZeroU8::MAX,
                            },
                            Some("Binary") => ColumnType::Binary {
                                max_len: NonZeroU8::MAX,
                            },
                            Some("Boolean") => ColumnType::Boolean,
                            _ => return Err("column type".into()),
                        },
                    ))
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let rows = tables
        .iter()
        .map(|t| {
            t.get("seed_rows")
                .unwrap_or(&t["rows"])
                .as_array()
                .ok_or("rows")?
                .iter()
                .map(values)
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let slices = rows
        .iter()
        .map(|r| r.iter().map(Vec::as_slice).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let requests = tables
        .iter()
        .enumerate()
        .map(|(i, t)| {
            Ok(TableRows {
                table: TableSpec {
                    name: t["name"].as_str().ok_or("name")?.as_bytes(),
                    columns: &columns[i],
                    indexes: &[],
                },
                rows: &slices[i],
            })
        })
        .collect::<Result<Vec<_>>>()?;
    create_database_with_table_rows(path, &requests, &mut budget())?;
    if let Some(id) = case["request"].get("seed_delete") {
        let table = case["request"]["table"].as_str().ok_or("table")?;
        let (_, row) = locate(path, table, integer(id)?)?;
        delete_row(
            path,
            RowDelete {
                table: table.as_bytes(),
                row,
            },
            &mut budget(),
        )?;
    }
    Ok(())
}
fn locate(path: &Path, table: &str, id: i32) -> Result<(u64, RowLocator)> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut c = db.catalog(&mut b)?;
        let mut roots = Vec::new();
        while let Some(r) = c.next_record()? {
            if r.class() == CatalogObjectClass::User && r.name().raw_bytes() == table.as_bytes() {
                roots.push(r.table_definition().ok_or("root")?);
            }
        }
        if roots.len() != 1 {
            return Err("table identity".into());
        }
        roots[0]
    };
    let def = db.table_definition(root, &mut b)?;
    let column = def.columns().first().ok_or("Id")?.ordinal();
    let mut found = Vec::new();
    {
        let mut rows = db.rows(&def, &mut b)?;
        while let Some(r) = rows.next_row()? {
            if r.field(column).and_then(|v| v.raw_bytes()) == Some(id.to_le_bytes().as_slice()) {
                if r.locator() != r.storage_locator() {
                    return Err("overflow".into());
                }
                found.push(r.locator());
            }
        }
    }
    if found.len() != 1 {
        return Err("selected Id".into());
    }
    Ok((root.get(), found[0]))
}
fn word(bytes: &[u8], offset: usize) -> Result<usize> {
    Ok(u16::from_le_bytes(
        bytes
            .get(offset..offset + 2)
            .ok_or("word range")?
            .try_into()?,
    ) as usize)
}
fn dword(bytes: &[u8], offset: usize) -> Result<usize> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .ok_or("dword range")?
            .try_into()?,
    ) as usize)
}
fn put(bytes: &mut [u8], offset: usize, value: &[u8]) -> Result<()> {
    bytes
        .get_mut(offset..offset + value.len())
        .ok_or("patch range")?
        .copy_from_slice(value);
    Ok(())
}
fn encoded(row: &Value) -> Result<Vec<u8>> {
    let r = row.as_array().ok_or("row")?;
    if r.len() != 5 {
        return Err("five columns".into());
    }
    let mut bytes = vec![5];
    bytes.extend(integer(&r[0])?.to_le_bytes());
    bytes.extend(if r[1].is_null() { 0 } else { integer(&r[1])? }.to_le_bytes());
    let mut ends = vec![9_u8];
    let mut bitmap = 1_u8;
    if !r[1].is_null() {
        bitmap |= 2;
    }
    for (n, v) in r.iter().enumerate().take(4).skip(2) {
        if !v.is_null() {
            let text = v.as_str().ok_or("ASCII variable field")?;
            if !text.is_ascii() {
                return Err("ASCII recipe".into());
            }
            bytes.extend(text.as_bytes());
            bitmap |= 1 << n;
        }
        ends.push(bytes.len().try_into()?);
    }
    if r[4].as_bool().ok_or("Boolean")? {
        bitmap |= 16;
    }
    bytes.extend(ends.into_iter().rev());
    bytes.extend([2, bitmap]);
    Ok(bytes)
}
fn release_maps(bytes: &mut [u8], root: usize, page: usize) -> Result<()> {
    let locators = [
        (1, 0),
        (dword(bytes, root + 35)? >> 8, bytes[root + 35] as usize),
        (dword(bytes, root + 39)? >> 8, bytes[root + 39] as usize),
    ];
    for (role, (map, slot)) in locators.into_iter().enumerate() {
        let base = map * 2048;
        let start = word(bytes, base + 10 + 2 * slot)?;
        let end = if slot == 0 {
            2048
        } else {
            word(bytes, base + 8 + 2 * slot)?
        };
        if start >= end || end > 2048 || bytes.get(base + start) != Some(&0) {
            return Err("inline map".into());
        }
        let relative = page
            .checked_sub(dword(bytes, base + start + 1)?)
            .ok_or("map base")?;
        let offset = base + start + 5 + relative / 8;
        if offset >= base + end {
            return Err("map coverage".into());
        }
        let mask = 1 << (relative % 8);
        if (bytes[offset] & mask != 0) == (role == 0) {
            return Err("release membership".into());
        }
        bytes[offset] = if role == 0 {
            bytes[offset] | mask
        } else {
            bytes[offset] & !mask
        };
    }
    Ok(())
}
fn verify(id: &str, case: &Value, directory: &Path) -> Result<Value> {
    let request = &case["request"];
    let table = request["table"].as_str().ok_or("table")?;
    let original = directory.join("before/database.mdb");
    let updated = directory.join("after/database.mdb");
    let before = fs::read(&original)?;
    let after = fs::read(&updated)?;
    let (root, locator) = locate(&original, table, integer(&request["selected_id"])?)?;
    let base = locator.page().get() as usize * 2048;
    let count = word(&before, base + 8)?;
    let mut expected = before.clone();
    if request["kind"] == "sole_release" {
        if count != 1 || locator.slot() != 0 || word(&before, base + 10)? >= 2048 {
            return Err("sole physical row".into());
        }
        expected[base] = 9;
        put(&mut expected, base + 2, &2036_u16.to_le_bytes())?;
        put(&mut expected, base + 10, &0xc800_u16.to_le_bytes())?;
        let definition = root as usize * 2048;
        let rows = dword(&before, definition + 12)?
            .checked_sub(1)
            .ok_or("row count")?;
        put(
            &mut expected,
            definition + 12,
            &u32::try_from(rows)?.to_le_bytes(),
        )?;
        release_maps(&mut expected, definition, locator.page().get() as usize)?;
    } else {
        let replacement = encoded(&request["replacement"])?;
        let mut end = 2048_usize;
        let mut has_tombstone = false;
        for slot in 0..count {
            let raw = word(&before, base + 10 + 2 * slot)?;
            let start = raw & 0x1fff;
            let oldend = if slot == 0 {
                2048
            } else {
                word(&before, base + 8 + 2 * slot)? & 0x1fff
            };
            if start > oldend || oldend > 2048 {
                return Err("row boundaries".into());
            }
            if raw & 0xe000 != 0 {
                if raw & 0xe000 != 0xc000 || start != oldend {
                    return Err("row flags".into());
                }
                has_tombstone = true;
            }
            let row = if slot == locator.slot() as usize {
                replacement.as_slice()
            } else {
                &before[base + start..base + oldend]
            };
            end = end.checked_sub(row.len()).ok_or("row width")?;
            put(&mut expected, base + end, row)?;
            put(
                &mut expected,
                base + 10 + 2 * slot,
                &u16::try_from((raw & 0xe000) | end)?.to_le_bytes(),
            )?;
        }
        if request["tombstone"] == true && !has_tombstone {
            return Err("missing tombstone".into());
        }
        let free = end.checked_sub(10 + 2 * count).ok_or("directory overlap")?;
        put(&mut expected, base + 2, &u16::try_from(free)?.to_le_bytes())?;
        if locate(&updated, table, integer(&request["selected_id"])?)?.1 != locator {
            return Err("changed target slot".into());
        }
    }
    if before.len() != after.len() || expected != after {
        return Err("unplanned replacement/release bytes".into());
    }
    Ok(
        json!({"scenario_id":id,"request":request,"locator":{"root":root,"page":locator.page().get(),"slot":locator.slot()},"preserved":true,"before_sha256":crate::sha256_hex(&before),"after_sha256":crate::sha256_hex(&after)}),
    )
}
/// Generates finite public mutation pairs, or verifies retained bytes read-only.
pub fn row_replacement_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    let case = scenario(id)?;
    let kind = case["request"]["kind"].as_str().unwrap_or("");
    if !matches!(kind, "sole_release" | "row_replace") {
        return crate::indexed_update_fixture(command, id, directory);
    }
    if command == "generate" {
        fs::create_dir(directory.join("before"))?;
        fs::create_dir(directory.join("after"))?;
        let before = directory.join("before/database.mdb");
        let after = directory.join("after/database.mdb");
        generate(&before, &case)?;
        fs::copy(&before, &after)?;
        let request = &case["request"];
        let table = request["table"].as_str().ok_or("table")?;
        let (_, row) = locate(&before, table, integer(&request["selected_id"])?)?;
        if kind == "sole_release" {
            delete_row(
                &after,
                RowDelete {
                    table: table.as_bytes(),
                    row,
                },
                &mut budget(),
            )?;
        } else {
            update_row(
                &after,
                RowUpdate {
                    table: table.as_bytes(),
                    row,
                    values: &values(&request["replacement"])?,
                },
                &mut budget(),
            )?;
        }
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
    fn public_replacement_release_and_exact_preservation() -> Result<()> {
        for suffix in [
            "SOLE-RELEASE",
            "ROW-GROW",
            "ROW-SHRINK",
            "ROW-LATER-TOMBSTONE",
        ] {
            let id = format!("DAO-UPDATE-{suffix}");
            let d = tempfile::tempdir()?;
            row_replacement_fixture("generate", &id, d.path())?;
            let p = d.path().join("after/database.mdb");
            let original = fs::read(&p)?;
            for offset in [1538, original.len() - 1] {
                let mut bad = original.clone();
                bad[offset] ^= 1;
                fs::write(&p, bad)?;
                assert!(verify(&id, &scenario(&id)?, d.path()).is_err());
            }
        }
        Ok(())
    }
}
