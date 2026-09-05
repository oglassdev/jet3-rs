//! Deterministic multiple-data-page candidate for a preregistered DAO run.
use jet3::{
    ColumnSpec, ColumnType, ResourceBudget, ResourceLimits, RowValue, TableSpec,
    create_database_with_rows,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or("usage: multi_page_row_candidate OUTPUT.mdb")?;
    let table = TableSpec {
        name: b"Rows",
        columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
        indexes: &[],
    };
    // 509 Long rows pack into pages of 254, 254, and 1 rows. The two
    // exhausted pages are owned but not marked available by this candidate.
    let values = (0..509)
        .map(|value| [RowValue::Long(value - 254)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &table,
        &rows,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
