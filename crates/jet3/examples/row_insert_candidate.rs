//! One public insertion into a closed DAO-created copy for EXP-0169.
use jet3::{
    CatalogObjectClass, DatabaseReader, ResourceBudget, ResourceLimits, RowValue, insert_row,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [source, destination, table, id, value, payload] = args.as_slice() else {
        return Err(
            "usage: row_insert_candidate SOURCE DESTINATION TABLE ID VALUE ASCII_PAYLOAD".into(),
        );
    };
    if Path::new(destination).exists() || !payload.is_ascii() {
        return Err("existing destination or non-ASCII payload".into());
    }
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
    drop(database);
    fs::copy(source, destination)?;
    let locator = insert_row(
        destination,
        table.as_bytes(),
        &[
            RowValue::Long(id.parse()?),
            RowValue::Long(value.parse()?),
            RowValue::Text(payload.as_bytes()),
        ],
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
