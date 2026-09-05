//! Finite hosted field-update fixtures and independent byte-preservation checks.
use jet3::{
    CatalogObjectClass, ColumnPhysicalType, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate,
    ResourceBudget, ResourceLimits, RowLocator, RowValue, SliceSource, TableRows, TableSpec,
    create_database_with_table_rows, update_field,
};
use serde::Deserialize;
use serde_json::json;
use std::{fs, num::NonZeroU8, ops::Range, path::Path};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
/// Separate finite update inventory; no read/write membership is changed.
pub const UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/update-scenarios.json");
#[derive(Deserialize)]
struct Inventory {
    scenarios: Vec<Case>,
}
#[derive(Deserialize)]
struct Case {
    id: String,
    request: Request,
}
#[derive(Deserialize)]
struct Request {
    table: String,
    column: String,
    row_index: usize,
    before: i32,
    after: i32,
}

fn case(id: &str) -> Result<Case> {
    serde_json::from_str::<Inventory>(UPDATE_SCENARIOS)?
        .scenarios
        .into_iter()
        .find(|case| case.id == id)
        .ok_or_else(|| "unknown update scenario".into())
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn generate_before(id: &str, path: &Path) -> Result<()> {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
        ColumnSpec::new(
            b"Label",
            ColumnType::Text {
                max_len: NonZeroU8::new(255).ok_or("size")?,
            },
        ),
        ColumnSpec::new(
            b"Blob",
            ColumnType::Binary {
                max_len: NonZeroU8::new(4).ok_or("size")?,
            },
        ),
    ];
    let labels = (0..33)
        .map(|n| format!("row{n:03}:{}", "x".repeat(193)))
        .collect::<Vec<_>>();
    let blobs = (0..33_u8)
        .map(|n| [b'0' + n / 10, b'0' + n % 10, b'!', b'!'])
        .collect::<Vec<_>>();
    let rows = (0..33)
        .map(|n| {
            [
                RowValue::Long(n as i32),
                RowValue::Long(1000 + n as i32),
                RowValue::Text(labels[n].as_bytes()),
                RowValue::Binary(&blobs[n]),
            ]
        })
        .collect::<Vec<_>>();
    let row_slices = rows.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let memo_columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Body", ColumnType::Memo),
    ];
    let memo = vec![b'm'; 2048];
    let memo_rows = [
        vec![RowValue::Long(7), RowValue::Memo(&memo)],
        vec![RowValue::Long(8), RowValue::Null],
    ];
    let memo_slices = memo_rows.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let prefix_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let prefix_rows = [&[RowValue::Long(100)][..], &[RowValue::Long(101)][..]];
    let mut tables = Vec::new();
    if id == "DAO-UPDATE-LATER-TABLE" {
        tables.push(TableRows {
            table: TableSpec {
                name: b"Prefix",
                columns: &prefix_columns,
                indexes: &[],
            },
            rows: &prefix_rows,
        });
    }
    tables.push(TableRows {
        table: TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        },
        rows: &row_slices,
    });
    tables.push(TableRows {
        table: TableSpec {
            name: b"Notes",
            columns: &memo_columns,
            indexes: &[],
        },
        rows: &memo_slices,
    });
    create_database_with_table_rows(path, &tables, &mut budget())?;
    Ok(())
}

struct Located {
    row: RowLocator,
    column: jet3::ColumnOrdinal,
    range: Range<usize>,
}
fn locate(bytes: &[u8], request: &Request) -> Result<Located> {
    let mut resource = budget();
    let source = SliceSource::new(bytes, resource.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut resource)?;
    let mut root = None;
    {
        let mut catalog = database.catalog(&mut resource)?;
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::User
                && record.name().raw_bytes() == request.table.as_bytes()
            {
                if root.is_some() {
                    return Err("ambiguous target table".into());
                }
                root = record.table_definition();
            }
        }
    }
    let definition =
        database.table_definition(root.ok_or("target table missing")?, &mut resource)?;
    let column = definition
        .columns()
        .iter()
        .find(|column| column.name().raw_bytes() == request.column.as_bytes())
        .ok_or("target column missing")?;
    if column.physical_type() != ColumnPhysicalType::Long || column.auto_increment() {
        return Err("target is not ordinary Long".into());
    }
    let ordinal = column.ordinal();
    let mut rows = database.rows(&definition, &mut resource)?;
    let mut index = 0;
    while let Some(row) = rows.next_row()? {
        if index != request.row_index {
            index += 1;
            continue;
        }
        if row.locator() != row.storage_locator() {
            return Err("overflow target".into());
        }
        let raw = row.raw_bytes();
        let field = row
            .field(ordinal)
            .and_then(|f| f.raw_bytes())
            .ok_or("target is null")?;
        if field != request.before.to_le_bytes() {
            return Err("requested before value mismatch".into());
        }
        let relative = field
            .as_ptr()
            .addr()
            .checked_sub(raw.as_ptr().addr())
            .ok_or("field outside row")?;
        let end = relative.checked_add(field.len()).ok_or("field range")?;
        if raw.get(relative..end) != Some(field) || field.len() != 4 {
            return Err("field outside row".into());
        }
        let page_start = usize::try_from(row.storage_locator().page().get())?
            .checked_mul(jet3::PAGE_BYTES)
            .ok_or("page range")?;
        let page = bytes
            .get(page_start..page_start + jet3::PAGE_BYTES)
            .ok_or("page absent")?;
        let positions = page
            .windows(raw.len())
            .enumerate()
            .filter_map(|(n, candidate)| (candidate == raw).then_some(n))
            .collect::<Vec<_>>();
        let [start] = positions.as_slice() else {
            return Err("raw row match is not unique".into());
        };
        let start = page_start + start + relative;
        return Ok(Located {
            row: row.locator(),
            column: ordinal,
            range: start..start + 4,
        });
    }
    Err("target row absent".into())
}

fn verify(case: &Case, directory: &Path) -> Result<serde_json::Value> {
    let before = fs::read(directory.join("before/database.mdb"))?;
    let after = fs::read(directory.join("after/database.mdb"))?;
    let located = locate(&before, &case.request)?;
    if before.len() != after.len()
        || after.get(located.range.clone()) != Some(case.request.after.to_le_bytes().as_slice())
        || before[..located.range.start] != after[..located.range.start]
        || before[located.range.end..] != after[located.range.end..]
    {
        return Err(
            "update changed bytes outside the independently located requested field".into(),
        );
    }
    Ok(
        json!({"scenario_id":case.id,"table":case.request.table,"column":case.request.column,
        "row_index":case.request.row_index,"before":case.request.before,"after":case.request.after,
        "offset":located.range.start,"length":4,"page":located.row.page().get(),"slot":located.row.slot(),
        "before_sha256":crate::sha256_hex(&before),"after_sha256":crate::sha256_hex(&after),"preserved":true}),
    )
}

/// Generates or independently verifies the declared field update; never acquires DAO.
pub fn update_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    let case = case(id)?;
    if command == "generate" {
        fs::create_dir(directory.join("before"))?;
        fs::create_dir(directory.join("after"))?;
        let before_path = directory.join("before/database.mdb");
        let after_path = directory.join("after/database.mdb");
        generate_before(id, &before_path)?;
        fs::copy(&before_path, &after_path)?;
        let bytes = fs::read(&before_path)?;
        let located = locate(&bytes, &case.request)?;
        update_field(
            &after_path,
            FieldUpdate {
                table: case.request.table.as_bytes(),
                row: located.row,
                column: located.column,
                value: RowValue::Long(case.request.after),
            },
            &mut budget(),
        )?;
    } else if command != "verify" {
        return Err("expected generate or verify".into());
    }
    let result = verify(&case, directory)?;
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn generated_updates_bind_requested_rows_and_preserve_all_other_bytes() -> Result<()> {
        for case in serde_json::from_str::<Inventory>(UPDATE_SCENARIOS)?.scenarios {
            let directory = tempfile::tempdir()?;
            update_fixture("generate", &case.id, directory.path())?;
            let receipt = verify(&case, directory.path())?;
            assert_eq!(receipt["row_index"], case.request.row_index);
            let path = directory.path().join("after/database.mdb");
            let original = fs::read(&path)?;
            let mut changed = original.clone();
            let last = changed.len() - 1;
            changed[last] ^= 1;
            fs::write(&path, changed)?;
            assert!(verify(&case, directory.path()).is_err());
            fs::write(&path, original)?;
            let mut wrong = case;
            wrong.request.row_index = (wrong.request.row_index + 1) % 33;
            assert!(verify(&wrong, directory.path()).is_err());
        }
        Ok(())
    }

    #[test]
    fn raw_row_binding_rejects_duplicate_byte_matches() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let case = case("DAO-UPDATE-FIRST-FIELD")?;
        let path = directory.path().join("original.mdb");
        generate_before(&case.id, &path)?;
        let mut bytes = fs::read(path)?;
        let located = locate(&bytes, &case.request)?;
        // Duplicate the complete first row elsewhere in the same page.
        let mut resource = budget();
        let source = SliceSource::new(&bytes, resource.read_budget())?;
        let mut database = DatabaseReader::from_source(source, &mut resource)?;
        let mut catalog = database.catalog(&mut resource)?;
        let mut root = None;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == b"Items" {
                root = record.table_definition();
            }
        }
        drop(catalog);
        let definition = database.table_definition(root.ok_or("missing Items")?, &mut resource)?;
        let mut rows = database.rows(&definition, &mut resource)?;
        let raw = rows.next_row()?.ok_or("missing row")?.raw_bytes().to_vec();
        drop(rows);
        let start = located.row.page().get() as usize * jet3::PAGE_BYTES + 128;
        bytes[start..start + raw.len()].copy_from_slice(&raw);
        assert_eq!(
            locate(&bytes, &case.request)
                .err()
                .ok_or("expected ambiguity")?
                .to_string(),
            "raw row match is not unique"
        );
        Ok(())
    }
}
