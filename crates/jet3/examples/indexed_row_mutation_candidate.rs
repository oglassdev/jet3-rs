//! Finite public row/index mutation candidates for EXP-0215.
use jet3::{
    ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget,
    ResourceLimits, RowDelete, RowLocator, RowValue, TableSpec, UpdateError,
};
use std::{env, fs, path::Path};
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn locate(path: &Path, id: i32) -> Result<(u64, RowLocator), Box<dyn std::error::Error>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut c = db.catalog(&mut b)?;
        let mut root = None;
        while let Some(r) = c.next_record()? {
            if r.name().raw_bytes() == b"Items" {
                root = r.table_definition();
            }
        }
        root.ok_or("Items")?
    };
    let table = db.table_definition(root, &mut b)?;
    let mut c = db.rows(&table, &mut b)?;
    let mut found = None;
    while let Some(r) = c.next_row()? {
        if r.field(table.columns()[0].ordinal())
            .and_then(|f| f.raw_bytes())
            == Some(id.to_le_bytes().as_slice())
            && found.replace(r.locator()).is_some()
        {
            return Err("duplicate Id".into());
        }
    }
    Ok((root.get(), found.ok_or("Id")?))
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [directory] = args.as_slice() else {
        return Err("usage: indexed_row_mutation_candidate NEW_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    let mut receipts = Vec::new();
    for (name, descending, count, insertion) in [
        ("ascending", false, 3, Some(i32::MIN)),
        ("descending", true, 3, Some(i32::MAX)),
        ("capacity", false, 199, Some(199)),
        ("deletions", false, 6, None),
    ] {
        let original = directory.join(format!("{name}-original.mdb"));
        let candidate = directory.join(format!("{name}-candidate.mdb"));
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
        ];
        let keys = [if descending {
            IndexColumnSpec::descending(0)
        } else {
            IndexColumnSpec::ascending(0)
        }];
        let indexes = [IndexSpec {
            name: b"ByKey",
            kind: if descending {
                IndexKind::Unique
            } else {
                IndexKind::Primary
            },
            fields: &keys,
        }];
        let values: Vec<_> = (0..count)
            .map(|n| [RowValue::Long(n), RowValue::Long(n + 100)])
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        jet3::create_database_with_rows(
            &original,
            &TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &indexes,
            },
            &rows,
            &mut budget(),
        )?;
        fs::copy(&original, &candidate)?;
        let root = locate(&original, 0)?.0;
        let mut actions = Vec::new();
        if let Some(id) = insertion {
            let r = jet3::insert_row(
                &candidate,
                b"Items",
                &[RowValue::Long(id), RowValue::Long(-777)],
                &mut budget(),
            )?;
            actions.push(format!(
                "{{\"kind\":\"insert\",\"page\":{},\"slot\":{}}}",
                r.page().get(),
                r.slot()
            ));
        } else {
            for id in [1, 4, 0] {
                let (_, r) = locate(&candidate, id)?;
                jet3::delete_row(
                    &candidate,
                    RowDelete {
                        table: b"Items",
                        row: r,
                    },
                    &mut budget(),
                )?;
                actions.push(format!(
                    "{{\"kind\":\"delete\",\"page\":{},\"slot\":{}}}",
                    r.page().get(),
                    r.slot()
                ));
            }
        }
        let before = fs::read(&candidate)?;
        let existing = if insertion.is_some() { 0 } else { 2 };
        let error = jet3::insert_row(
            &candidate,
            b"Items",
            &[RowValue::Long(existing), RowValue::Long(0)],
            &mut budget(),
        )
        .err()
        .ok_or("duplicate accepted")?;
        let refusal = match error {
            UpdateError::Unsupported("duplicate unique key") => "duplicate",
            UpdateError::Unsupported("full root leaf") => "capacity",
            _ => return Err("unexpected refusal".into()),
        };
        if fs::read(&candidate)? != before {
            return Err("refusal changed source".into());
        }
        receipts.push(format!("\"{name}\":{{\"root\":{root},\"actions\":[{}],\"public_refusal\":\"{refusal}\",\"refusal_preserved\":true}}",actions.join(",")));
    }
    println!("{{{}}}", receipts.join(","));
    Ok(())
}
