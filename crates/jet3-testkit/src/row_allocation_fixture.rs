//! Finite EOF insertion and slot-preserving deletion fixtures, using public APIs.
use jet3::{
    CatalogObjectClass, ColumnSpec, ColumnType, DatabaseReader, ResourceBudget, ResourceLimits,
    RowDelete, RowLocator, RowValue, TableRows, TableSpec, create_database_with_table_rows,
    delete_row, insert_row,
};
use serde_json::{Value, json};
use std::{fs, num::NonZeroU8, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const ROW_ALLOCATION_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-allocation-scenarios.json");
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn scenario(id: &str) -> Result<Value> {
    let inventory: Value = serde_json::from_str(ROW_ALLOCATION_SCENARIOS)?;
    inventory["scenarios"]
        .as_array()
        .ok_or("scenarios")?
        .iter()
        .find(|s| s["id"] == id)
        .cloned()
        .ok_or_else(|| "unknown scenario".into())
}
fn values(row: &Value) -> Result<Vec<RowValue<'_>>> {
    let r = row.as_array().ok_or("row")?;
    if r.len() != 3 {
        return Err("row width".into());
    }
    Ok(vec![
        RowValue::Long(r[0].as_i64().ok_or("Id")?.try_into()?),
        RowValue::Long(r[1].as_i64().ok_or("Value")?.try_into()?),
        RowValue::Text(r[2].as_str().ok_or("Payload")?.as_bytes()),
    ])
}
fn generate(path: &Path, case: &Value) -> Result<()> {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: NonZeroU8::MAX,
            },
        ),
    ];
    let tables = case["tables"].as_array().ok_or("tables")?;
    let rows = tables
        .iter()
        .map(|t| {
            t["rows"]
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
        .zip(&slices)
        .map(|(t, r)| {
            Ok(TableRows {
                table: TableSpec {
                    name: t["name"].as_str().ok_or("name")?.as_bytes(),
                    columns: &columns,
                    indexes: &[],
                },
                rows: r,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    create_database_with_table_rows(path, &requests, &mut budget())?;
    Ok(())
}
fn locate(path: &Path, id: Option<i32>) -> Result<(u64, Option<RowLocator>)> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut catalog = db.catalog(&mut b)?;
        let mut roots = Vec::new();
        while let Some(r) = catalog.next_record()? {
            if r.class() == CatalogObjectClass::User && r.name().raw_bytes() == b"Items" {
                roots.push(r.table_definition().ok_or("root")?);
            }
        }
        if roots.len() != 1 {
            return Err("table root".into());
        }
        roots[0]
    };
    let def = db.table_definition(root, &mut b)?;
    let ordinal = def.columns().first().ok_or("Id column")?.ordinal();
    let mut found = None;
    if let Some(id) = id {
        let mut rows = db.rows(&def, &mut b)?;
        while let Some(row) = rows.next_row()? {
            if row.field(ordinal).and_then(|f| f.raw_bytes()) == Some(id.to_le_bytes().as_slice()) {
                if found.is_some() || row.locator() != row.storage_locator() {
                    return Err("ambiguous row".into());
                }
                found = Some(row.locator());
            }
        }
        if found.is_none() {
            return Err("missing row".into());
        }
    }
    Ok((root.get(), found))
}
fn u16_at(bytes: &[u8], offset: usize) -> Result<usize> {
    Ok(u16::from_le_bytes(
        bytes
            .get(offset..offset + 2)
            .ok_or("u16 range")?
            .try_into()?,
    ) as usize)
}
fn u32_at(bytes: &[u8], offset: usize) -> Result<usize> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .ok_or("u32 range")?
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
fn encoded(value: &Value) -> Result<Vec<u8>> {
    let r = values(value)?;
    let [RowValue::Long(id), RowValue::Long(v), RowValue::Text(text)] = r.as_slice() else {
        return Err("finite row".into());
    };
    if text.len() > 246 || !text.is_ascii() {
        return Err("finite text width".into());
    }
    let mut out = vec![3];
    out.extend(id.to_le_bytes());
    out.extend(v.to_le_bytes());
    out.extend(*text);
    out.extend([9 + text.len() as u8, 9, 1, 7]);
    Ok(out)
}
fn map_bit(bytes: &mut [u8], page: usize, slot: usize, target: usize, set: bool) -> Result<usize> {
    let base = page * 2048;
    let start = u16_at(bytes, base + 10 + slot * 2)?;
    let end = if slot == 0 {
        2048
    } else {
        u16_at(bytes, base + 8 + slot * 2)?
    };
    if start >= end || end > 2048 || bytes.get(base + start) != Some(&0) {
        return Err("inline map".into());
    }
    let bit = target
        .checked_sub(u32_at(bytes, base + start + 1)?)
        .ok_or("map base")?;
    let offset = base + start + 5 + bit / 8;
    if offset >= base + end {
        return Err("map coverage".into());
    }
    let mask = 1 << (bit % 8);
    let old = bytes[offset];
    if (old & mask != 0) == set {
        return Err("map prior membership".into());
    }
    bytes[offset] = if set { old | mask } else { old & !mask };
    Ok(offset)
}
fn verify(id: &str, case: &Value, directory: &Path) -> Result<Value> {
    let before_path = directory.join("before/database.mdb");
    let after_path = directory.join("after/database.mdb");
    let before = fs::read(&before_path)?;
    let after = fs::read(&after_path)?;
    let (root, _) = locate(&before_path, None)?;
    let definition = root as usize * 2048;
    let request = &case["request"];
    let mut expected = before.clone();
    let mut steps = Vec::new();
    if request["kind"] == "eof_insert" {
        let row = encoded(&request["row"])?;
        let page = before.len() / 2048;
        if before.len() % 2048 != 0 || after.len() != before.len() + 2048 {
            return Err("EOF length".into());
        }
        let mut image = [0_u8; 2048];
        image[0] = 1;
        image[1] = 1;
        put(
            &mut image,
            2,
            &u16::try_from(2036 - row.len())?.to_le_bytes(),
        )?;
        put(&mut image, 4, &u32::try_from(root)?.to_le_bytes())?;
        put(&mut image, 8, &1_u16.to_le_bytes())?;
        put(
            &mut image,
            10,
            &u16::try_from(2048 - row.len())?.to_le_bytes(),
        )?;
        put(&mut image, 2048 - row.len(), &row)?;
        let mut offsets = vec![map_bit(&mut expected, 1, 0, page, false)?];
        for pointer in [35, 39] {
            let slot = expected[definition + pointer] as usize;
            let p = u32_at(&expected, definition + pointer)? >> 8;
            offsets.push(map_bit(&mut expected, p, slot, page, true)?);
        }
        let count = u32_at(&before, definition + 12)?;
        put(
            &mut expected,
            definition + 12,
            &u32::try_from(count + 1)?.to_le_bytes(),
        )?;
        expected.extend(image);
        let (_, locator) = locate(
            &after_path,
            Some(request["row"][0].as_i64().ok_or("Id")?.try_into()?),
        )?;
        let locator = locator.ok_or("insert locator")?;
        if locator.page().get() != page as u64 || locator.slot() != 0 {
            return Err("EOF slot".into());
        }
        steps.push(json!({"page":page,"slot":0,"map_offsets":offsets}));
    } else {
        for selected in request["selected_ids"].as_array().ok_or("selected IDs")? {
            let selected = selected.as_i64().ok_or("selected Id")? as i32;
            // Original public locators remain stable through the declared sequence.
            let (_, locator) = locate(&before_path, Some(selected))?;
            let locator = locator.ok_or("delete locator")?;
            let page = locator.page().get() as usize;
            let base = page * 2048;
            let slot = locator.slot() as usize;
            let count = u16_at(&expected, base + 8)?;
            let start = u16_at(&expected, base + 10 + 2 * slot)? & 0x1fff;
            let end = if slot == 0 {
                2048
            } else {
                u16_at(&expected, base + 8 + 2 * slot)? & 0x1fff
            };
            let lowest = u16_at(&expected, base + 8 + 2 * count)? & 0x1fff;
            if count < 2
                || start >= end
                || lowest > start
                || u16_at(&expected, base + 2)? != lowest - 10 - 2 * count
            {
                return Err("delete bounds/free".into());
            }
            let rows = case["tables"][0]["rows"].as_array().ok_or("rows")?;
            let row = rows
                .iter()
                .find(|r| r[0] == selected)
                .ok_or("selected recipe")?;
            if expected[base + start..base + end] != encoded(row)? {
                return Err("selected bytes".into());
            }
            let width = end - start;
            let moved = expected[base + lowest..base + start].to_vec();
            put(&mut expected, base + lowest + width, &moved)?;
            for ordinal in slot..count {
                let old = u16_at(&expected, base + 10 + 2 * ordinal)?;
                let word = if ordinal == slot {
                    end | 0xc000
                } else {
                    (old & 0xe000) | ((old & 0x1fff) + width)
                };
                put(
                    &mut expected,
                    base + 10 + 2 * ordinal,
                    &u16::try_from(word)?.to_le_bytes(),
                )?;
            }
            let free = u16_at(&expected, base + 2)? + width;
            put(&mut expected, base + 2, &u16::try_from(free)?.to_le_bytes())?;
            let count = u32_at(&expected, definition + 12)?;
            put(
                &mut expected,
                definition + 12,
                &u32::try_from(count - 1)?.to_le_bytes(),
            )?;
            steps.push(
                json!({"selected_id":selected,"page":page,"slot":slot,"start":start,"end":end}),
            );
        }
    }
    if expected != after {
        return Err("unplanned allocation/compaction bytes".into());
    }
    Ok(
        json!({"scenario_id":id,"request":request,"root":root,"steps":steps,"before_sha256":crate::sha256_hex(&before),"after_sha256":crate::sha256_hex(&after),"preserved":true}),
    )
}
/// Uses public creation/mutation APIs, or verifies files without writing them.
pub fn row_allocation_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    let case = scenario(id)?;
    if !matches!(
        case["request"]["kind"].as_str(),
        Some("eof_insert" | "compact_delete")
    ) {
        return crate::row_update_fixture(command, id, directory);
    }
    if command == "generate" {
        fs::create_dir(directory.join("before"))?;
        fs::create_dir(directory.join("after"))?;
        let before = directory.join("before/database.mdb");
        let after = directory.join("after/database.mdb");
        generate(&before, &case)?;
        fs::copy(&before, &after)?;
        if case["request"]["kind"] == "eof_insert" {
            insert_row(
                &after,
                b"Items",
                &values(&case["request"]["row"])?,
                &mut budget(),
            )?;
        } else {
            for id in case["request"]["selected_ids"]
                .as_array()
                .ok_or("selected IDs")?
            {
                let (_, row) = locate(&after, Some(id.as_i64().ok_or("Id")?.try_into()?))?;
                delete_row(
                    &after,
                    RowDelete {
                        table: b"Items",
                        row: row.ok_or("row")?,
                    },
                    &mut budget(),
                )?;
            }
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
    fn public_allocation_compaction_and_exact_preservation() -> Result<()> {
        for suffix in [
            "EMPTY-EOF",
            "FULL-EOF",
            "MIDDLE-COMPACT",
            "REPEATED-COMPACT",
        ] {
            let id = format!("DAO-UPDATE-{suffix}");
            let dir = tempfile::tempdir()?;
            row_allocation_fixture("generate", &id, dir.path())?;
            let path = dir.path().join("after/database.mdb");
            let original = fs::read(&path)?;
            for offset in [1538, original.len() - 1] {
                let mut bad = original.clone();
                bad[offset] ^= 1;
                fs::write(&path, bad)?;
                assert!(verify(&id, &scenario(&id)?, dir.path()).is_err());
            }
            fs::write(path, original)?;
        }
        Ok(())
    }
}
