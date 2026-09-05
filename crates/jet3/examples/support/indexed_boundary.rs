use jet3::{
    ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget,
    ResourceLimits, RowValue, TableDefinition, TableRows, TableSpec,
};
use std::{num::NonZeroU8, path::Path};
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const NAME: &[u8; 80] = &[b'x'; 80];
pub const MEMO: &[u8; 4096] = &[b'n'; 4096];
pub fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
pub fn values(id: i32) -> [RowValue<'static>; 4] {
    [
        RowValue::Long(id),
        RowValue::Text(NAME),
        if id % 2 == 0 {
            RowValue::Null
        } else {
            RowValue::Currency { scaled: -123456 }
        },
        RowValue::Boolean(id % 2 != 0),
    ]
}
pub fn create(path: &Path, count: i32) -> Result<()> {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Name",
            ColumnType::Text {
                max_len: NonZeroU8::new(80).ok_or("length")?,
            },
        ),
        ColumnSpec::new(b"Price", ColumnType::Currency),
        ColumnSpec::new(b"Active", ColumnType::Boolean),
    ];
    let indexes = [IndexSpec {
        name: b"ById",
        kind: IndexKind::Primary,
        fields: &[IndexColumnSpec::ascending(0)],
    }];
    let values: Vec<_> = (0..count).map(values).collect();
    let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
    jet3::create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    name: b"Items",
                    columns: &columns,
                    indexes: &indexes,
                },
                rows: &rows,
            },
            TableRows {
                table: TableSpec {
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
pub fn definition(path: &Path) -> Result<TableDefinition> {
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
    Ok(db.table_definition(root, &mut b)?)
}
