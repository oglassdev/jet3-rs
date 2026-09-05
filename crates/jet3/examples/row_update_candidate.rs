//! Replace one complete row through the public API for EXP-0197.
use jet3::{
    CatalogObjectClass, DatabaseReader, ResourceBudget, ResourceLimits, RowUpdate, RowValue,
    update_row,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [source, destination, table, selected_id, profile] = args.as_slice() else {
        return Err(
            "usage: row_update_candidate SOURCE DESTINATION TABLE ORIGINAL_ID PROFILE".into(),
        );
    };
    if Path::new(destination).exists() {
        return Err("destination exists".into());
    }
    let selected_id: i32 = selected_id.parse()?;
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let mut database = DatabaseReader::open(source, &mut budget)?;
    let root = {
        let mut catalog = database.catalog(&mut budget)?;
        let mut roots = Vec::new();
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::User
                && record.name().raw_bytes() == table.as_bytes()
            {
                roots.push(record.table_definition().ok_or("not a table")?);
            }
        }
        if roots.len() != 1 {
            return Err("table not unique".into());
        }
        roots[0]
    };
    let definition = database.table_definition(root, &mut budget)?;
    let id = definition
        .columns()
        .iter()
        .find(|c| c.name().raw_bytes() == b"Id")
        .ok_or("Id absent")?
        .ordinal();
    let locator = {
        let mut rows = database.rows(&definition, &mut budget)?;
        let mut found = Vec::new();
        while let Some(row) = rows.next_row()? {
            if row.field(id).and_then(|field| field.raw_bytes())
                == Some(selected_id.to_le_bytes().as_slice())
            {
                found.push(row.locator());
            }
        }
        if found.len() != 1 {
            return Err("selected Id not unique".into());
        }
        found[0]
    };
    drop(database);
    fs::copy(source, destination)?;
    let grow = [b'g'; 60];
    let values = match profile.as_str() {
        "grow-first" => [
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Text(&grow),
            RowValue::Binary(&[1, 35, 69, 103, 137, 171, 205, 239]),
            RowValue::Boolean(false),
        ],
        "shrink-middle" => [
            RowValue::Long(2),
            RowValue::Long(-42),
            RowValue::Null,
            RowValue::Binary(&[1, 2]),
            RowValue::Boolean(true),
        ],
        "null-later" => [
            RowValue::Long(12),
            RowValue::Long(i32::MIN),
            RowValue::Text(b"restored"),
            RowValue::Binary(&[0, 255, 1, 2]),
            RowValue::Boolean(true),
        ],
        "tombstone" => [
            RowValue::Long(3),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
            RowValue::Boolean(false),
        ],
        _ => return Err("unknown profile".into()),
    };
    update_row(
        destination,
        RowUpdate {
            table: table.as_bytes(),
            row: locator,
            values: &values,
        },
        &mut budget,
    )?;
    println!(
        "{{\"root\":{},\"page\":{},\"slot\":{}}}",
        root.get(),
        locator.page().get(),
        locator.slot()
    );
    Ok(())
}
