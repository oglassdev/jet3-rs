use super::*;
use crate::{RowValue, TableRows, create_database_with_table_rows};

const COLUMNS: [ColumnSpec<'static>; 3] =
    [ID, SEQUENCE, ColumnSpec::new(b"Value", ColumnType::Long)];
const INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ZPrimary",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"ASequence",
        fields: &[field(1, IndexDirection::Descending)],
        kind: IndexKind::Ordinary,
    },
    IndexSpec {
        name: b"MValue",
        fields: &[field(2, IndexDirection::Ascending)],
        kind: IndexKind::Unique,
    },
];

#[test]
fn six_tables_preserve_independent_index_roots_and_initial_rows() -> TestResult {
    for populated in [false, true] {
        let directory = TestDirectory::create()?;
        let names = (0..6).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
        let tables = names
            .iter()
            .enumerate()
            .map(|(n, name)| TableSpec {
                name: name.as_bytes(),
                columns: &COLUMNS,
                indexes: &INDEXES[..[3, 0, 1, 2, 3, 3][n]],
            })
            .collect::<Vec<_>>();
        let values = (0..17)
            .map(|n| {
                [
                    RowValue::Long(n),
                    RowValue::Long(n % 3),
                    RowValue::Long(n - 8),
                ]
            })
            .collect::<Vec<_>>();
        let rows = values
            .iter()
            .map(<[RowValue<'_>; 3]>::as_slice)
            .collect::<Vec<_>>();
        if populated {
            let requests = tables
                .iter()
                .map(|table| TableRows {
                    table: *table,
                    rows: &rows,
                })
                .collect::<Vec<_>>();
            create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
        } else {
            create_database(directory.target(), &tables, &mut budget())?;
        }
        let mut budget = budget();
        let mut database = DatabaseReader::open(directory.target(), &mut budget)?;
        let mut root = 20;
        for (position, table) in tables.iter().enumerate() {
            let definition = database.table_definition(PageNumber::new(root), &mut budget)?;
            let mut cursor = database.rows(&definition, &mut budget)?;
            let mut count = 0;
            while cursor.next_row()?.is_some() {
                count += 1;
            }
            drop(cursor);
            assert_eq!(count, if populated { 17 } else { 0 });
            assert_eq!(definition.physical_indexes().len(), table.indexes.len());
            for (n, index) in definition.physical_indexes().iter().enumerate() {
                assert_eq!(
                    index.root(),
                    PageNumber::new(root + 2 + u64::from(position == 0) + n as u64)
                );
                assert_eq!(index.usage_map().page(), PageNumber::new(root + 1));
                assert_eq!(index.usage_map().row(), 2 + n as u8);
                let tree = database.index_tree(&definition, n as u16, &mut budget)?;
                assert_eq!(tree.entries().len(), if populated { 17 } else { 0 });
            }
            root +=
                2 + u64::from(position == 0) + table.indexes.len() as u64 + u64::from(populated);
        }
        assert_eq!(database.geometry().page_count(), root);
    }
    Ok(())
}

#[test]
fn creation_counter_overflow_is_refused_before_allocating_or_writing() -> TestResult {
    let directory = TestDirectory::create()?;
    let tables = [TableSpec {
        name: b"T",
        columns: &[ID],
        indexes: &[],
    }; 128];
    let mut budget = budget();
    assert!(matches!(
        create_database(directory.target(), &tables, &mut budget),
        Err(CreateDatabaseError::Compose(
            ComposeError::TableCountOverflow {
                count: 128,
                maximum: 127
            }
        ))
    ));
    assert_eq!(budget.allocation_bytes().get(), 0);
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn catalog_name_width_determines_the_actual_table_capacity() -> TestResult {
    for (count, padding) in [(28, 0), (15, 45)] {
        let directory = TestDirectory::create()?;
        let names = (0..=count)
            .map(|n| format!("T{n:02}{}", "x".repeat(padding)))
            .collect::<Vec<_>>();
        let tables = names
            .iter()
            .map(|name| TableSpec {
                name: name.as_bytes(),
                columns: &[ID],
                indexes: &[],
            })
            .collect::<Vec<_>>();
        create_database(directory.target(), &tables[..count], &mut budget())?;
        let before = fs::read(directory.target())?;
        assert!(matches!(
            create_database(directory.target(), &tables, &mut budget()),
            Err(CreateDatabaseError::Compose(ComposeError::Page(
                crate::PageImageError::PageFull { .. }
            )))
        ));
        assert_eq!(fs::read(directory.target())?, before);
        assert_eq!(directory.entries()?, ["created.mdb"]);
    }
    Ok(())
}
