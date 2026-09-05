//! Deterministic public creation/update images for EXP-0179.
use jet3::{
    ColumnSpec, ColumnType, DatabaseReader, FieldUpdate, IndexColumnSpec, IndexKind, IndexSpec,
    ResourceBudget, ResourceLimits, RowValue, TableSpec, create_database_with_rows, update_field,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [directory] = args.as_slice() else {
        return Err("usage: single_leaf_key_candidate NEW_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    let mut receipts = Vec::new();
    for (name, kind, descending, ids, selected, replacement) in [
        (
            "ascending-primary",
            IndexKind::Primary,
            false,
            vec![-10, 0, 10],
            0_i32,
            i32::MIN,
        ),
        (
            "descending-unique",
            IndexKind::Unique,
            true,
            vec![i32::MIN, 0, i32::MAX],
            0,
            i32::MAX - 1,
        ),
        (
            "full-leaf",
            IndexKind::Primary,
            false,
            (0..200).collect(),
            100,
            -1,
        ),
    ] {
        let original = directory.join(format!("{name}-original.mdb"));
        let candidate = directory.join(format!("{name}-candidate.mdb"));
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
            ColumnSpec::new(
                b"Payload",
                ColumnType::Text {
                    max_len: std::num::NonZeroU8::new(8).ok_or("text width")?,
                },
            ),
        ];
        let fields = [if descending {
            IndexColumnSpec::descending(0)
        } else {
            IndexColumnSpec::ascending(0)
        }];
        let indexes = [IndexSpec {
            name: b"ByKey",
            kind,
            fields: &fields,
        }];
        let values: Vec<_> = ids
            .iter()
            .enumerate()
            .map(|(i, id)| {
                [
                    RowValue::Long(*id),
                    RowValue::Long(i as i32 + 100),
                    RowValue::Text(b"payload"),
                ]
            })
            .collect();
        let rows: Vec<_> = values.iter().map(|v| v.as_slice()).collect();
        create_database_with_rows(
            &original,
            &TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &indexes,
            },
            &rows,
            &mut budget,
        )?;
        let mut db = DatabaseReader::open(&original, &mut budget)?;
        let root = {
            let mut c = db.catalog(&mut budget)?;
            let mut found = None;
            while let Some(r) = c.next_record()? {
                if r.name().raw_bytes() == b"Items" {
                    found = r.table_definition();
                }
            }
            found.ok_or("table absent")?
        };
        let def = db.table_definition(root, &mut budget)?;
        let column = def.columns()[0].ordinal();
        let row = {
            let mut c = db.rows(&def, &mut budget)?;
            let mut found = None;
            while let Some(r) = c.next_row()? {
                if r.field(column).and_then(|f| f.raw_bytes())
                    == Some(selected.to_le_bytes().as_slice())
                {
                    if found.is_some() {
                        return Err("ambiguous selected row".into());
                    }
                    found = Some(r.locator());
                }
            }
            found.ok_or("row absent")?
        };
        let index = def.physical_indexes()[0].root();
        drop(db);
        fs::copy(&original, &candidate)?;
        update_field(
            &candidate,
            FieldUpdate {
                table: b"Items",
                row,
                column,
                value: RowValue::Long(replacement),
            },
            &mut budget,
        )?;
        receipts.push(format!(
            "\"{name}\":{{\"root\":{},\"page\":{},\"slot\":{},\"column\":{},\"index\":{}}}",
            root.get(),
            row.page().get(),
            row.slot(),
            column.get(),
            index.get()
        ));
    }
    println!("{{{}}}", receipts.join(","));
    Ok(())
}
