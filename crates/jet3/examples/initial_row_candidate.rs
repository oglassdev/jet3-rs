//! Deterministic scalar-row candidate for a separately preregistered DAO run.
use std::num::NonZeroU8;

use jet3::{
    ColumnSpec, ColumnType, ResourceBudget, ResourceLimits, RowValue, TableSpec,
    create_database_with_rows,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or("usage: initial_row_candidate OUTPUT.mdb")?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Code",
            ColumnType::Text {
                max_len: NonZeroU8::new(8).ok_or("invalid text length")?,
            },
        ),
    ];
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &[],
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Text(b"one")],
        &[RowValue::Long(-2), RowValue::Text(b"two")],
        &[RowValue::Null, RowValue::Null],
    ];
    create_database_with_rows(
        path,
        &table,
        rows,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
