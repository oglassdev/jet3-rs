//! Update a closed DAO-created copy through the public API for EXP-0163.
use jet3::{
    CatalogObjectClass, DatabaseReader, FieldUpdate, ResourceBudget, ResourceLimits, RowValue,
    update_field,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [source, destination, table, selected_id, column, replacement] = args.as_slice() else {
        return Err(
            "usage: fixed_field_update_candidate SOURCE DESTINATION TABLE ORIGINAL_ID COLUMN ARM"
                .into(),
        );
    };
    if Path::new(destination).exists() {
        return Err("destination exists".into());
    }
    let selected_id: i32 = selected_id.parse()?;
    let text = vec![
        0xe9;
        if replacement == "fixed-text-255" {
            255
        } else {
            1
        }
    ];
    let replacement = match replacement.as_str() {
        "byte" => RowValue::Byte(u8::MAX),
        "integer" => RowValue::Integer(i16::MIN),
        "long-control" => RowValue::Long(i32::MIN),
        "currency" => RowValue::Currency { scaled: i64::MIN },
        "single" => RowValue::Single(-0.0),
        "double" => RowValue::Double(-0.0),
        "date" => RowValue::DateTime { days: -1.25 },
        "guid" => RowValue::Guid([
            0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee,
            0xff,
        ]),
        "fixed-text-1" | "fixed-text-255" => RowValue::Text(&text),
        _ => return Err("unknown fixed-field arm".into()),
    };
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
    let column = definition
        .columns()
        .iter()
        .find(|c| c.name().raw_bytes() == column.as_bytes())
        .ok_or("column absent")?
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
    update_field(
        destination,
        FieldUpdate {
            table: table.as_bytes(),
            row: locator,
            column,
            value: replacement,
        },
        &mut budget,
    )?;
    println!(
        "{{\"root\":{},\"page\":{},\"slot\":{},\"column\":{}}}",
        root.get(),
        locator.page().get(),
        locator.slot(),
        column.get()
    );
    Ok(())
}
