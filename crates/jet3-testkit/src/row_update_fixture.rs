//! Finite hosted public row mutations; byte checks follow EXP-0162/0168/0170.
use jet3::{
    CatalogObjectClass, ColumnSpec, ColumnType, DatabaseReader, ResourceBudget, ResourceLimits,
    RowDelete, RowLocator, RowValue, TableRows, TableSpec, create_database_with_table_rows,
    delete_row, insert_row,
};
use serde_json::{Value, json};
use std::{fs, num::NonZeroU8, ops::Range, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const ROW_UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-update-scenarios.json");
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn scenario(id: &str) -> Result<Value> {
    let inventory: Value = serde_json::from_str(ROW_UPDATE_SCENARIOS)?;
    inventory["scenarios"]
        .as_array()
        .ok_or("scenarios")?
        .iter()
        .find(|s| s["id"] == id)
        .cloned()
        .ok_or_else(|| "unknown scenario".into())
}
fn generate(path: &Path) -> Result<()> {
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
    let items = [
        &[
            RowValue::Long(1),
            RowValue::Long(101),
            RowValue::Text(b"aaa"),
        ][..],
        &[
            RowValue::Long(2),
            RowValue::Long(-202),
            RowValue::Text(b"bbb"),
        ][..],
        &[
            RowValue::Long(3),
            RowValue::Long(303),
            RowValue::Text(b"ccc"),
        ][..],
    ];
    let later = [
        &[
            RowValue::Long(11),
            RowValue::Long(-1101),
            RowValue::Text(b"ddd"),
        ][..],
        &[
            RowValue::Long(12),
            RowValue::Long(1202),
            RowValue::Text(b"eee"),
        ][..],
        &[
            RowValue::Long(13),
            RowValue::Long(-1303),
            RowValue::Text(b"fff"),
        ][..],
    ];
    create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    name: b"Items",
                    columns: &columns,
                    indexes: &[],
                },
                rows: &items,
            },
            TableRows {
                table: TableSpec {
                    name: b"Later",
                    columns: &columns,
                    indexes: &[],
                },
                rows: &later,
            },
        ],
        &mut budget(),
    )?;
    Ok(())
}
struct Located {
    root: u64,
    row: RowLocator,
    range: Range<usize>,
    count: usize,
}
fn locate(path: &Path, table: &str, id: i32) -> Result<Located> {
    let bytes = fs::read(path)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut catalog = db.catalog(&mut b)?;
        let mut roots = Vec::new();
        while let Some(r) = catalog.next_record()? {
            if r.class() == CatalogObjectClass::User && r.name().raw_bytes() == table.as_bytes() {
                roots.push(r.table_definition().ok_or("table root")?);
            }
        }
        if roots.len() != 1 {
            return Err("ambiguous table".into());
        }
        roots[0]
    };
    let def = db.table_definition(root, &mut b)?;
    let ordinal = def
        .columns()
        .iter()
        .find(|c| {
            c.name().raw_bytes() == b"Id" && c.physical_type() == jet3::ColumnPhysicalType::Long
        })
        .ok_or("ordinary Id absent")?
        .ordinal();
    let mut rows = db.rows(&def, &mut b)?;
    let mut found = None;
    let mut count = 0;
    while let Some(row) = rows.next_row()? {
        count += 1;
        if row.field(ordinal).and_then(|f| f.raw_bytes()) != Some(id.to_le_bytes().as_slice()) {
            continue;
        }
        if found.is_some() || row.locator() != row.storage_locator() {
            return Err("ambiguous or overflow row".into());
        }
        let base = usize::try_from(row.locator().page().get())? * jet3::PAGE_BYTES;
        let page = bytes
            .get(base..base + jet3::PAGE_BYTES)
            .ok_or("page absent")?;
        let raw = row.raw_bytes();
        let positions = page
            .windows(raw.len())
            .enumerate()
            .filter_map(|(n, v)| (v == raw).then_some(n))
            .collect::<Vec<_>>();
        let [start] = positions.as_slice() else {
            return Err("raw row match not unique".into());
        };
        found = Some((row.locator(), base + start..base + start + raw.len()));
    }
    let (row, range) = found.ok_or("target row absent")?;
    Ok(Located {
        root: root.get(),
        row,
        range,
        count,
    })
}
fn patch(expected: &mut [u8], changes: &mut Vec<Value>, offset: usize, value: &[u8]) -> Result<()> {
    let target = expected
        .get_mut(offset..offset + value.len())
        .ok_or("patch range")?;
    changes.push(json!({"offset":offset,"before":target,"after":value}));
    target.copy_from_slice(value);
    Ok(())
}
fn verify(id: &str, request: &Value, directory: &Path) -> Result<Value> {
    let before_path = directory.join("before/database.mdb");
    let after_path = directory.join("after/database.mdb");
    let before = fs::read(&before_path)?;
    let after = fs::read(&after_path)?;
    let insert = request["kind"] == "insert";
    let selected = if insert { 88 } else { 3 };
    let located = locate(
        if insert { &after_path } else { &before_path },
        "Items",
        selected,
    )?;
    let page = usize::try_from(located.row.page().get())?;
    let base = page * jet3::PAGE_BYTES;
    let root = usize::try_from(located.root)? * jet3::PAGE_BYTES;
    let slot = usize::from(located.row.slot());
    let count = u16::from_le_bytes(
        before
            .get(base + 8..base + 10)
            .ok_or("slot count")?
            .try_into()?,
    );
    let free = u16::from_le_bytes(
        before
            .get(base + 2..base + 4)
            .ok_or("free bytes")?
            .try_into()?,
    );
    let stored = u32::from_le_bytes(
        before
            .get(root + 12..root + 16)
            .ok_or("table count")?
            .try_into()?,
    );
    let mut expected = before.clone();
    let mut changes = Vec::new();
    if insert {
        // Independently specified finite EXP-0060 Long/Long/Text row.
        let mut row = vec![3];
        row.extend_from_slice(&88_i32.to_le_bytes());
        row.extend_from_slice(&(-8800_i32).to_le_bytes());
        row.extend_from_slice(b"inserted");
        row.extend_from_slice(&[17, 9, 1, 7]);
        if slot != usize::from(count)
            || located.range.len() != row.len()
            || located.count != stored as usize + 1
            || count < 1
        {
            return Err("insert locator/count".into());
        }
        let packed = usize::from(
            u16::from_le_bytes(
                before
                    .get(base + 8 + 2 * usize::from(count)..base + 10 + 2 * usize::from(count))
                    .ok_or("last slot")?
                    .try_into()?,
            ) & 0x1fff,
        );
        if located.range.end != base + packed
            || usize::from(free) != packed - 10 - 2 * usize::from(count)
        {
            return Err("insert contiguous space".into());
        }
        patch(&mut expected, &mut changes, located.range.start, &row)?;
        patch(
            &mut expected,
            &mut changes,
            base + 10 + 2 * slot,
            &u16::try_from(located.range.start - base)?.to_le_bytes(),
        )?;
        patch(
            &mut expected,
            &mut changes,
            base + 8,
            &(count + 1).to_le_bytes(),
        )?;
        patch(
            &mut expected,
            &mut changes,
            base + 2,
            &free
                .checked_sub(u16::try_from(row.len() + 2)?)
                .ok_or("free bytes")?
                .to_le_bytes(),
        )?;
        patch(
            &mut expected,
            &mut changes,
            root + 12,
            &(stored + 1).to_le_bytes(),
        )?;
    } else {
        if slot + 1 != usize::from(count) || count < 2 || located.count != stored as usize {
            return Err("delete tail/count".into());
        }
        if usize::from(free) != located.range.start - base - 10 - 2 * usize::from(count) {
            return Err("delete contiguous space".into());
        }
        patch(
            &mut expected,
            &mut changes,
            base + 10 + 2 * slot,
            &(0xc000 | u16::try_from(located.range.end - base)?).to_le_bytes(),
        )?;
        patch(
            &mut expected,
            &mut changes,
            base + 2,
            &free
                .checked_add(u16::try_from(located.range.len())?)
                .ok_or("free bytes")?
                .to_le_bytes(),
        )?;
        patch(
            &mut expected,
            &mut changes,
            root + 12,
            &(stored - 1).to_le_bytes(),
        )?;
    }
    if expected != after {
        return Err("row mutation changed unplanned bytes".into());
    }
    Ok(
        json!({"scenario_id":id,"request":request,"root":located.root,"page":page,"slot":slot,"row_offset":located.range.start,"row_length":located.range.len(),"patches":changes,"before_sha256":crate::sha256_hex(&before),"after_sha256":crate::sha256_hex(&after),"preserved":true}),
    )
}
/// Generates through public APIs or independently verifies retained images.
pub fn row_update_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    let case = scenario(id)?;
    let request = &case["request"];
    if request.get("kind").is_none() {
        return crate::update_fixture(command, id, directory);
    }
    if command == "generate" {
        fs::create_dir(directory.join("before"))?;
        fs::create_dir(directory.join("after"))?;
        let before = directory.join("before/database.mdb");
        let after = directory.join("after/database.mdb");
        generate(&before)?;
        fs::copy(&before, &after)?;
        if request["kind"] == "insert" {
            insert_row(
                &after,
                b"Items",
                &[
                    RowValue::Long(88),
                    RowValue::Long(-8800),
                    RowValue::Text(b"inserted"),
                ],
                &mut budget(),
            )?;
        } else {
            let row = locate(&before, "Items", 3)?.row;
            delete_row(
                &after,
                RowDelete {
                    table: b"Items",
                    row,
                },
                &mut budget(),
            )?;
        }
    } else if command != "verify" {
        return Err("expected generate or verify".into());
    }
    println!("{}", verify(id, request, directory)?);
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    #[test]
    fn public_row_mutations_and_independent_byte_checks() -> Result<()> {
        for id in ["DAO-UPDATE-INSERT-ROW", "DAO-UPDATE-DELETE-TAIL"] {
            let directory = tempfile::tempdir()?;
            row_update_fixture("generate", id, directory.path())?;
            let case = scenario(id)?;
            let receipt = verify(id, &case["request"], directory.path())?;
            assert_eq!(receipt["slot"], if id.ends_with("TAIL") { 2 } else { 3 });
            let path = directory.path().join("after/database.mdb");
            let original = fs::read(&path)?;
            for offset in [1538, original.len() - 1] {
                let mut bad = original.clone();
                bad[offset] ^= 1;
                fs::write(&path, bad)?;
                assert!(verify(id, &case["request"], directory.path()).is_err());
            }
            fs::write(&path, original)?;
        }
        Ok(())
    }
    #[test]
    fn unique_reader_row_binding_is_required() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("before.mdb");
        generate(&path)?;
        let located = locate(&path, "Items", 3)?;
        let mut bytes = fs::read(&path)?;
        let raw = bytes[located.range.clone()].to_vec();
        let start = located.row.page().get() as usize * jet3::PAGE_BYTES + 128;
        bytes[start..start + raw.len()].copy_from_slice(&raw);
        fs::write(&path, bytes)?;
        assert!(locate(&path, "Items", 3).is_err());
        Ok(())
    }
}
