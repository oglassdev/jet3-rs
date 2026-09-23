//! Initial database construction shared with the wide-row fixture.
use super::*;

pub(super) fn create(path: &Path, case: &Case, model: &BTreeMap<i32, Row>) -> Result<()> {
    let index_fields = case.index_fields();
    let values = model.values().map(|r| values(r)).collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    jet3::create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    validation: jet3::TableValidation::NONE,
                    name: b"Items",
                    columns: &case.columns(),
                    indexes: &case.indexes(&index_fields),
                },
                rows: &rows,
            },
            TableRows {
                table: TableSpec {
                    validation: jet3::TableValidation::NONE,
                    name: b"Notes",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Body", ColumnType::Memo),
                    ],
                    indexes: &[],
                },
                rows: &[
                    &[RowValue::Long(7), RowValue::Memo(MEMO)],
                    &[RowValue::Long(8), RowValue::Null],
                ],
            },
        ],
        &mut budget(),
    )?;
    Ok(())
}
