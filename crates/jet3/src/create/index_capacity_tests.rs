use crate::{
    ColumnSpec, ColumnType, DatabaseReader, IndexDirection, IndexKind, IndexSpec, PageNumber,
    RowValue, TableSpec,
    create::{api_tests::*, initial_rows_tests::*},
    create_database_with_rows,
};
use std::collections::BTreeSet;
use std::fs;

#[test]
fn thirty_two_indexes_span_maps_and_validate_the_final_unique_index() -> TestResult {
    let directory = TestDirectory::create()?;
    let column_names = (0..10)
        .map(|n| format!("C{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let columns = column_names
        .iter()
        .map(|n| ColumnSpec::new(n, ColumnType::Long))
        .collect::<Vec<_>>();
    let names = (0..32)
        .map(|n| format!("I{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let fields = (0..32)
        .map(|n| {
            if n == 31 {
                return vec![field(9, IndexDirection::Ascending)];
            }
            (0..10)
                .map(|c| {
                    field(
                        c,
                        if n & (1 << (c % 5)) == 0 {
                            IndexDirection::Ascending
                        } else {
                            IndexDirection::Descending
                        },
                    )
                })
                .collect()
        })
        .collect::<Vec<_>>();
    let indexes = names
        .iter()
        .zip(&fields)
        .map(|(name, fields)| IndexSpec {
            name,
            fields,
            kind: IndexKind::Unique,
        })
        .collect::<Vec<_>>();
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    let values = (0..64)
        .map(|n| {
            (0..10)
                .map(|c| RowValue::Long(n * 100 + c))
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut b)?;
    let definition = db.table_definition(PageNumber::new(20), &mut b)?;
    assert_eq!(definition.physical_indexes().len(), 32);
    let mut all_nodes = BTreeSet::new();
    for (n, physical) in definition.physical_indexes().iter().enumerate() {
        let row = 2 + n;
        assert_eq!(physical.usage_map().page().get(), 21 + (row / 15) as u64);
        assert_eq!(physical.usage_map().row(), (row % 15) as u8);
        let tree = db.index_tree(&definition, n as u16, &mut b)?;
        assert_eq!(tree.entries().len(), 64);
        assert_eq!(physical.fields().len(), if n == 31 { 1 } else { 10 });
        for node in tree.nodes() {
            assert!(all_nodes.insert(node.page()));
            assert!(map_bit(
                &original,
                physical.usage_map().page().get(),
                physical.usage_map().row(),
                node.page().get()
            )?);
        }
    }
    let last_root = db.index_tree(&definition, 31, &mut b)?.root().get();
    drop(db);
    let mut incoming = (0..10)
        .map(|c| RowValue::Long(10000 + c))
        .collect::<Vec<_>>();
    incoming[9] = RowValue::Long(9);
    assert!(matches!(
        crate::insert_row(directory.target(), b"Items", &incoming, &mut budget()),
        Err(crate::UpdateError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    incoming[9] = RowValue::Long(10009);
    crate::insert_row(directory.target(), b"Items", &incoming, &mut budget())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut b)?;
    let definition = db.table_definition(PageNumber::new(20), &mut b)?;
    for n in 0..32 {
        assert_eq!(db.index_tree(&definition, n, &mut b)?.entries().len(), 65);
    }
    drop(db);
    let mut corrupt = fs::read(directory.target())?;
    let page = 23 * crate::PAGE_BYTES;
    let start = u16::from_le_bytes(corrupt[page + 16..page + 18].try_into()?) as usize;
    assert_ne!(
        corrupt[page + start + 5 + last_root as usize / 8] & (1 << (last_root % 8)),
        0
    );
    corrupt[page + start + 5 + last_root as usize / 8] &= !(1 << (last_root % 8));
    fs::write(directory.target(), &corrupt)?;
    incoming[0] = RowValue::Long(20000);
    incoming[9] = RowValue::Long(20009);
    assert!(crate::insert_row(directory.target(), b"Items", &incoming, &mut budget()).is_err());
    assert_eq!(fs::read(directory.target())?, corrupt);
    Ok(())
}
